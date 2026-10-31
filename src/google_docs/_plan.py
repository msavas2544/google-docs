"""Turn :class:`~google_docs.content.Content` into one list of Docs API requests.

This is the part that makes the library worth having. The Docs API edits by
absolute index, every insert shifts everything after it, tables need their cell
positions worked out by hand, and bullets eat the tabs you used to nest them.
The planner keeps all of that in one place, so a whole Markdown document becomes a
single atomic ``batchUpdate``.

How it stays correct:

* Every insert happens at a cursor that only moves forward, so a range computed
  for earlier content is still valid when later content goes in.
* Styles are applied after every insert, on ranges that are final by then.
* ``createParagraphBullets`` removes the leading tabs that set nesting, which moves
  text. Bullets therefore go last, from the bottom of the document up.

Nothing here does I/O; the clients feed it a :class:`~google_docs.models.Body` and
send what it returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .content import ImageBlock, PageBreak, Para, Span, TableBlock
from .models import utf16_len

CODE_FONT = "Roboto Mono"
LINK_COLOR = {"red": 0.067, "green": 0.333, "blue": 0.8}  # Docs' own link blue, #1155CC
CODE_BG = {"red": 0.953, "green": 0.953, "blue": 0.953}
RULE_COLOR = {"red": 0.8, "green": 0.8, "blue": 0.8}
QUOTE_COLOR = {"red": 0.8, "green": 0.8, "blue": 0.8}

PARAGRAPH_RESET = ("namedStyleType,alignment,indentStart,indentFirstLine,borderLeft,"
                   "borderBottom,shading,spaceAbove,spaceBelow")
TEXT_RESET = ("bold,italic,underline,strikethrough,smallCaps,link,weightedFontFamily,"
              "backgroundColor,foregroundColor,baselineOffset,fontSize")

BULLETS = "BULLET_DISC_CIRCLE_SQUARE"
NUMBERS = "NUMBERED_DECIMAL_ALPHA_ROMAN"
CHECKBOX = "BULLET_CHECKBOX"

# Control characters, and Private Use Area characters that insertText silently drops.
_CONTROL = re.compile("[\x00-\x08\x0c-\x1f\x7f\ue000-\uf8ff]")


@dataclass
class Where:
    """Which part of the document a plan writes into."""

    tab_id: Optional[str] = None
    segment_id: Optional[str] = None

    def location(self, index: int) -> Dict[str, Any]:
        loc: Dict[str, Any] = {"index": index}
        if self.segment_id:
            loc["segmentId"] = self.segment_id
        if self.tab_id:
            loc["tabId"] = self.tab_id
        return loc

    def range(self, start: int, end: int) -> Dict[str, Any]:
        rng: Dict[str, Any] = {"startIndex": start, "endIndex": end}
        if self.segment_id:
            rng["segmentId"] = self.segment_id
        if self.tab_id:
            rng["tabId"] = self.tab_id
        return rng


@dataclass
class Plan:
    requests: List[Dict[str, Any]] = field(default_factory=list)
    #: How many index units the document grows by.
    added: int = 0


@dataclass
class _ParaSpot:
    para: Para
    start: int  # index of the paragraph's first character (after any nesting tabs)
    tabs: int  # leading tabs that set the list nesting level
    length: int  # characters after the tabs, newline excluded

    @property
    def end(self) -> int:  # one past the closing newline
        return self.start + self.length + 1

    @property
    def first(self) -> int:  # first index of the paragraph including the tabs
        return self.start - self.tabs


def clean(text: str) -> str:
    """Make text safe for ``insertText``: line breaks inside a paragraph become soft breaks
    (``\\v``, what Shift+Enter makes in Docs); other control characters are dropped."""
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\v"))


def plan_blocks(blocks: Sequence[Any], index: int, *, where: Optional[Where] = None,
                open_paragraph: bool = False, tail_empty: bool = True) -> Plan:
    """Plan the insertion of ``blocks`` at ``index``.

    ``open_paragraph``: the text just before ``index`` belongs to a paragraph that the
    new content must not join (we start with a newline that closes it).

    ``tail_empty``: what follows ``index`` in its paragraph is only the closing newline.
    Our last paragraph then reuses that newline instead of leaving a blank line behind.
    """
    w = where or Where()
    inserts: List[Dict[str, Any]] = []
    styles: List[Dict[str, Any]] = []
    spots: List[Optional[_ParaSpot]] = []  # None breaks a run of list items
    cursor = index
    is_open = open_paragraph
    own_start: Optional[int] = None
    buf: List[str] = []
    buf_at = cursor

    def flush() -> None:
        nonlocal buf
        if buf:
            inserts.append({"insertText": {"text": "".join(buf), "location": w.location(buf_at)}})
            buf = []

    def mark_own(i: int) -> None:
        nonlocal own_start
        if own_start is None:
            own_start = i

    for block in blocks:
        if isinstance(block, Para):
            if not buf:
                buf_at = cursor
            if is_open:
                buf.append("\n")
                cursor += 1
            tabs = block.list.level if block.list else 0
            text = clean(block.text)
            buf.append("\t" * tabs + text)
            spot = _ParaSpot(block, cursor + tabs, tabs, utf16_len(text))
            mark_own(spot.first)
            spots.append(spot)
            cursor += tabs + spot.length
            is_open = True
        elif isinstance(block, TableBlock):
            flush()
            spots.append(None)
            if not is_open:
                mark_own(cursor)  # insertTable's newline makes an empty paragraph here
            cursor = _plan_table(block, cursor, w, inserts, styles)
            mark_own(cursor - _table_size(block))
            is_open = False
        elif isinstance(block, ImageBlock):
            flush()
            spots.append(None)
            if is_open:
                inserts.append({"insertText": {"text": "\n", "location": w.location(cursor)}})
                cursor += 1
            mark_own(cursor)
            req: Dict[str, Any] = {"uri": block.url, "location": w.location(cursor)}
            size = {k: {"magnitude": v, "unit": "PT"}
                    for k, v in (("width", block.width), ("height", block.height)) if v}
            if size:
                req["objectSize"] = size
            inserts.append({"insertInlineImage": req})
            cursor += 1
            is_open = True
        elif isinstance(block, PageBreak):
            flush()
            spots.append(None)
            inserts.append({"insertPageBreak": {"location": w.location(cursor)}})
            cursor += 2  # the break and the newline that follows it
            is_open = False
        else:  # pragma: no cover - guarded by Content's types
            raise TypeError(f"cannot insert {type(block).__name__}")
    flush()

    end = cursor
    if tail_empty:
        # The rest of the paragraph we wrote into is just its newline: either it now closes
        # our last paragraph, or (after a table) it is an empty paragraph we leave behind.
        # Either way it must lose whatever style it had.
        end = cursor + 1
    elif is_open:
        inserts.append({"insertText": {"text": "\n", "location": w.location(cursor)}})
        cursor += 1
        end = cursor

    plan = Plan(added=cursor - index)
    if own_start is None or end <= own_start:
        plan.requests = inserts
        return plan

    # Start from a clean slate: inserted text inherits the style of its neighbours.
    resets: List[Dict[str, Any]] = [
        {"updateParagraphStyle": {"range": w.range(own_start, end),
                                  "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                  "fields": PARAGRAPH_RESET}},
        {"updateTextStyle": {"range": w.range(own_start, end), "textStyle": {},
                             "fields": TEXT_RESET}},
        {"deleteParagraphBullets": {"range": w.range(own_start, end)}},
    ]
    for maybe in spots:
        if maybe is not None:
            styles.extend(_paragraph_styles(maybe, w))
            styles.extend(span_styles(maybe.para.spans, maybe.start, w,
                                      in_code=maybe.para.kind == "code"))
    bullets = _bullet_requests(spots, w)
    plan.requests = inserts + resets + styles + bullets
    plan.added -= sum(s.tabs for s in spots if s is not None and s.para.list)
    return plan


def plan_inline(spans: Sequence[Span], index: int, *, where: Optional[Where] = None) -> Plan:
    """Insert styled text inside an existing paragraph (no new paragraphs)."""
    w = where or Where()
    text = clean("".join(s.text for s in spans)).replace("\v", " ")
    n = utf16_len(text)
    if not n:
        return Plan()
    flat = tuple(s.styled(text=clean(s.text).replace("\v", " ")) for s in spans)
    reqs: List[Dict[str, Any]] = [
        {"insertText": {"text": text, "location": w.location(index)}},
        {"updateTextStyle": {"range": w.range(index, index + n), "textStyle": {},
                             "fields": TEXT_RESET}},
    ]
    reqs.extend(span_styles(flat, index, w))
    return Plan(reqs, n)


# --------------------------------------------------------------------------- tables


def _table_size(t: TableBlock) -> int:
    rows, cols = t.shape
    text = sum(utf16_len(clean(_cell_text(c))) for r in t.rows for c in r)
    # table start + rows + (cell + its paragraph) per cell + table end
    return 2 + rows * (1 + 2 * cols) + text


def _cell_text(cell: Tuple[Span, ...]) -> str:
    return "".join(s.text for s in cell)


def _plan_table(t: TableBlock, cursor: int, w: Where, inserts: List[Dict[str, Any]],
                styles: List[Dict[str, Any]]) -> int:
    """Insert a filled table at ``cursor``; return the index just past it.

    ``insertTable`` puts a newline at the cursor and the table right after it. An empty
    R x C table starting at T has the paragraph of cell (r, c) at
    ``T + 3 + r*(2C + 1) + 2c``: one unit for the table, one per row, and two per cell
    (the cell itself and its empty paragraph), plus one unit closing the table.
    Measured against the live API: a 3x2 table inserted at 126 spans 127-144, its rows
    end at 143 and its cell paragraphs are at 130, 132, 135, 137, 140 and 142.
    We fill cells last-to-first so each insert leaves the positions of the cells
    before it untouched.
    """
    rows, cols = t.shape
    table_start = cursor + 1
    inserts.append({"insertTable": {"rows": rows, "columns": cols,
                                    "location": w.location(cursor)}})
    empty = [[table_start + 3 + r * (2 * cols + 1) + 2 * c for c in range(cols)]
             for r in range(rows)]
    texts = [[clean(_cell_text(t.rows[r][c])) for c in range(cols)] for r in range(rows)]
    for r in reversed(range(rows)):
        for c in reversed(range(cols)):
            if texts[r][c]:
                inserts.append({"insertText": {"text": texts[r][c],
                                               "location": w.location(empty[r][c])}})
    shift = 0
    for r in range(rows):
        for c in range(cols):
            at = empty[r][c] + shift
            spans = t.rows[r][c]
            if t.header and r == 0:
                spans = tuple(s.styled(bold=True) for s in spans)
            styles.extend(span_styles(spans, at, w))
            shift += utf16_len(texts[r][c])
    return table_start + _table_size(t)


# --------------------------------------------------------------------------- styles


def _rgb(c: Dict[str, float]) -> Dict[str, Any]:
    return {"color": {"rgbColor": c}}


def _pt(v: float) -> Dict[str, Any]:
    return {"magnitude": v, "unit": "PT"}


def _paragraph_styles(spot: _ParaSpot, w: Where) -> List[Dict[str, Any]]:
    p = spot.para
    style: Dict[str, Any]
    if p.kind == "heading":
        style = {"namedStyleType": "TITLE" if p.level == 0 else f"HEADING_{p.level}"}
    elif p.kind == "code":
        style = {"shading": {"backgroundColor": _rgb(CODE_BG)},
                 "spaceAbove": _pt(0), "spaceBelow": _pt(0)}
    elif p.kind == "quote":
        indent = 18.0 * max(1, p.level)
        style = {"indentStart": _pt(indent), "indentFirstLine": _pt(indent),
                 "borderLeft": {"color": _rgb(QUOTE_COLOR), "width": _pt(3),
                                "padding": _pt(9), "dashStyle": "SOLID"}}
    elif p.kind == "rule":
        style = {"borderBottom": {"color": _rgb(RULE_COLOR), "width": _pt(1),
                                  "padding": _pt(0), "dashStyle": "SOLID"}}
    else:
        return []
    return [{"updateParagraphStyle": {"range": w.range(spot.first, spot.end),
                                      "paragraphStyle": style, "fields": ",".join(style)}}]


def span_styles(spans: Sequence[Span], start: int, w: Where, *,
                in_code: bool = False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    at = start
    for s in spans:
        n = utf16_len(clean(s.text))
        style: Dict[str, Any] = {}
        if s.bold:
            style["bold"] = True
        if s.italic:
            style["italic"] = True
        if s.strike:
            style["strikethrough"] = True
        if s.underline:
            style["underline"] = True
        if s.code or in_code:
            style["weightedFontFamily"] = {"fontFamily": CODE_FONT}
            if not in_code:
                style["backgroundColor"] = _rgb(CODE_BG)
        if s.link and not s.link.startswith("#"):  # "#slug" has no Docs heading ID to point at
            style["link"] = {"url": s.link}
            style["foregroundColor"] = _rgb(LINK_COLOR)
            style["underline"] = True
        if style and n:
            out.append({"updateTextStyle": {"range": w.range(at, at + n), "textStyle": style,
                                            "fields": ",".join(style)}})
        at += n
    return out


def _bullet_requests(spots: List[Optional[_ParaSpot]], w: Where) -> List[Dict[str, Any]]:
    runs: List[List[_ParaSpot]] = []
    current: List[_ParaSpot] = []
    for spot in spots:
        mark = spot.para.list if spot is not None else None
        if mark is None:
            if current:
                runs.append(current)
            current = []
            continue
        assert spot is not None
        if current and mark.level == 0 and _kind(mark) != _kind(current[0].para.list):
            runs.append(current)
            current = []
        current.append(spot)
    if current:
        runs.append(current)
    out: List[Dict[str, Any]] = []
    for run in reversed(runs):  # bottom-up: removing tabs only moves text below
        mark = run[0].para.list
        preset = CHECKBOX if mark and mark.checkbox else NUMBERS if mark and mark.ordered \
            else BULLETS
        out.append({"createParagraphBullets": {"range": w.range(run[0].first, run[-1].end),
                                               "bulletPreset": preset}})
    return out


def _kind(mark: Any) -> Tuple[bool, bool]:
    return bool(mark.ordered), bool(mark.checkbox)
