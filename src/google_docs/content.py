"""Content you can put into a document: write Markdown, or build it in Python.

Both roads lead to the same small set of blocks, which :mod:`google_docs._plan`
turns into Docs API requests::

    from google_docs import Content, bold, link

    body = (Content()
            .heading("Q3 report")
            .paragraph("Revenue grew ", bold("18%"), ". Details in ", link("the sheet", url))
            .bullets(["North: +22%", "South: +9%"])
            .table([["Region", "Revenue"], ["North", "1.2M"]]))

    body = Content.from_markdown(open("report.md").read())
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union

# --------------------------------------------------------------------------- inline


@dataclass(frozen=True)
class Span:
    """A piece of text with one set of character styles."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    underline: bool = False
    code: bool = False
    link: Optional[str] = None

    def styled(self, **changes: Any) -> Span:
        return replace(self, **changes)


SpanLike = Union[str, Span]


def bold(text: SpanLike) -> Span:
    return _span(text).styled(bold=True)


def italic(text: SpanLike) -> Span:
    return _span(text).styled(italic=True)


def strike(text: SpanLike) -> Span:
    return _span(text).styled(strike=True)


def underline(text: SpanLike) -> Span:
    return _span(text).styled(underline=True)


def code(text: SpanLike) -> Span:
    return _span(text).styled(code=True)


def link(text: SpanLike, url: str) -> Span:
    return _span(text).styled(link=url)


def _span(x: SpanLike) -> Span:
    return x if isinstance(x, Span) else Span(str(x))


# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True)
class ListMark:
    ordered: bool = False
    level: int = 0
    checkbox: bool = False


@dataclass(frozen=True)
class Para:
    """One paragraph. ``kind`` is ``normal``, ``heading``, ``code``, ``quote`` or ``rule``."""

    spans: Tuple[Span, ...] = ()
    kind: str = "normal"
    level: int = 0  # heading level (0 = Title, 1-6); quote depth for quotes
    list: Optional[ListMark] = None

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass(frozen=True)
class TableBlock:
    rows: Tuple[Tuple[Tuple[Span, ...], ...], ...]
    header: bool = True

    @property
    def shape(self) -> Tuple[int, int]:
        return len(self.rows), max((len(r) for r in self.rows), default=0)


@dataclass(frozen=True)
class ImageBlock:
    url: str
    alt: str = ""
    width: Optional[float] = None  # points
    height: Optional[float] = None


@dataclass(frozen=True)
class PageBreak:
    pass


BlockLike = Union[Para, TableBlock, ImageBlock, PageBreak]


@dataclass
class Content:
    """An ordered list of blocks with a fluent builder API."""

    blocks: List[BlockLike] = field(default_factory=list)

    # -- builders -------------------------------------------------------------
    def heading(self, text: Union[SpanLike, Sequence[SpanLike]], level: int = 1) -> Content:
        """``level`` 1-6, or 0 for the document Title style."""
        if not 0 <= level <= 6:
            raise ValueError("heading level must be 0 (title) to 6")
        return self._add(Para(_spans(text), "heading", level))

    def title(self, text: SpanLike) -> Content:
        return self.heading(text, 0)

    def paragraph(self, *parts: SpanLike) -> Content:
        return self._add(Para(_spans(parts)))

    text = paragraph

    def bullets(self, items: Iterable[Union[SpanLike, Sequence[SpanLike], Tuple[int, Any]]],
                *, ordered: bool = False, checkbox: bool = False) -> Content:
        """A list. Nest an item by passing ``(level, text)``: ``(1, "child")``."""
        for item in items:
            level = 0
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int):
                level, item = item
            self._add(Para(_spans(item), list=ListMark(ordered, level, checkbox)))
        return self

    def numbered(self, items: Iterable[Any]) -> Content:
        return self.bullets(items, ordered=True)

    def checklist(self, items: Iterable[Any]) -> Content:
        return self.bullets(items, checkbox=True)

    def table(self, rows: Sequence[Sequence[Any]], *, header: bool = True) -> Content:
        """A table from a list of rows. Cells may be text, numbers, spans or lists of spans.

        A pandas DataFrame works too: ``table([df.columns, *df.values])``."""
        width = max((len(r) for r in rows), default=0)
        if not rows or not width:
            raise ValueError("a table needs at least one row and one column")
        norm = tuple(
            tuple(_spans(_cell(c)) for c in list(r) + [""] * (width - len(r))) for r in rows)
        return self._add(TableBlock(norm, header))

    def image(self, url: str, *, alt: str = "", width: Optional[float] = None,
              height: Optional[float] = None) -> Content:
        """An image from a public URL (PNG, JPEG or GIF; Docs fetches it once).
        ``width``/``height`` are in points; give one and Docs keeps the aspect ratio."""
        return self._add(ImageBlock(url, alt, width, height))

    def code(self, source: str) -> Content:
        for line in source.rstrip("\n").split("\n"):
            self._add(Para((Span(line, code=True),) if line else (), "code"))
        return self

    def quote(self, *parts: SpanLike) -> Content:
        return self._add(Para(_spans(parts), "quote", 1))

    def rule(self) -> Content:
        return self._add(Para((), "rule"))

    def page_break(self) -> Content:
        return self._add(PageBreak())

    def markdown(self, source: str) -> Content:
        self.blocks.extend(parse_markdown(source).blocks)
        return self

    def extend(self, other: Content) -> Content:
        self.blocks.extend(other.blocks)
        return self

    # -- misc -----------------------------------------------------------------
    @classmethod
    def from_markdown(cls, source: str) -> Content:
        return parse_markdown(source)

    def _add(self, block: BlockLike) -> Content:
        self.blocks.append(block)
        return self

    def __bool__(self) -> bool:
        return bool(self.blocks)

    def __len__(self) -> int:
        return len(self.blocks)


def md(source: str) -> Content:
    """Mark a string as Markdown, e.g. for rich :meth:`DocsClient.fill` values."""
    return parse_markdown(source)


def as_content(value: Union[str, Content]) -> Content:
    """Strings are read as Markdown."""
    return value if isinstance(value, Content) else parse_markdown(value)


def _spans(x: Any) -> Tuple[Span, ...]:
    if isinstance(x, (str, Span)):
        return (_span(x),)
    return tuple(_span(s) for s in x)


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, Span, list, tuple)):
        return value
    if isinstance(value, float) and value != value:  # NaN from pandas
        return ""
    return str(value)


# --------------------------------------------------------------------------- markdown

_TASK = re.compile(r"^\[([ xX])\]\s+")


def parse_markdown(source: str) -> Content:
    """Parse CommonMark plus GitHub tables, strikethrough and task lists."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    tokens = md.parse(source)
    return _Builder().run(tokens)


class _Builder:
    def __init__(self) -> None:
        self.out = Content()
        self.lists: List[bool] = []  # stack of "ordered?" for open lists
        self.quote = 0
        self.item_first_para = False
        self.heading: Optional[int] = None
        self.table: Optional[List[List[Tuple[Span, ...]]]] = None
        self.row: Optional[List[Tuple[Span, ...]]] = None

    def run(self, tokens: List[Any]) -> Content:
        for tok in tokens:
            getattr(self, "_" + tok.type, self._ignore)(tok)
        return self.out

    def _ignore(self, tok: Any) -> None:
        pass

    # lists ------------------------------------------------------------------
    def _bullet_list_open(self, tok: Any) -> None:
        self.lists.append(False)

    def _ordered_list_open(self, tok: Any) -> None:
        self.lists.append(True)

    def _bullet_list_close(self, tok: Any) -> None:
        self.lists.pop()

    _ordered_list_close = _bullet_list_close

    def _list_item_open(self, tok: Any) -> None:
        self.item_first_para = True

    # containers -------------------------------------------------------------
    def _blockquote_open(self, tok: Any) -> None:
        self.quote += 1

    def _blockquote_close(self, tok: Any) -> None:
        self.quote -= 1

    def _heading_open(self, tok: Any) -> None:
        self.heading = int(tok.tag[1])

    def _heading_close(self, tok: Any) -> None:
        self.heading = None

    def _hr(self, tok: Any) -> None:
        self.out._add(Para((), "rule"))

    def _fence(self, tok: Any) -> None:
        self.out.code(tok.content)

    _code_block = _fence

    def _html_block(self, tok: Any) -> None:
        if tok.content.strip():
            self.out._add(Para((Span(tok.content.strip()),)))

    # tables -----------------------------------------------------------------
    def _table_open(self, tok: Any) -> None:
        self.table = []

    def _tr_open(self, tok: Any) -> None:
        self.row = []

    def _tr_close(self, tok: Any) -> None:
        assert self.table is not None and self.row is not None
        self.table.append(self.row)
        self.row = None

    def _table_close(self, tok: Any) -> None:
        assert self.table is not None
        if self.table:
            self.out.table(self.table)  # type: ignore[arg-type]
        self.table = None

    # inline -----------------------------------------------------------------
    def _inline(self, tok: Any) -> None:
        spans = _inline_spans(tok.children or [])
        if self.row is not None:
            self.row.append(spans)
            return
        if self.heading is not None:
            self.out._add(Para(spans, "heading", self.heading))
            return
        if self.lists:
            checkbox = False
            if self.item_first_para and spans and not spans[0].code:
                m = _TASK.match(spans[0].text)
                if m:
                    checkbox = True
                    spans = (spans[0].styled(text=spans[0].text[m.end():]),) + spans[1:]
            if not self.item_first_para and self.out.blocks:
                prev = self.out.blocks[-1]
                if isinstance(prev, Para) and prev.list is not None:
                    # A second paragraph inside the same item: keep it in the item.
                    self.out.blocks[-1] = replace(prev, spans=prev.spans + (Span("\v"),) + spans)
                    return
            self.item_first_para = False
            mark = ListMark(self.lists[-1], len(self.lists) - 1, checkbox)
            self.out._add(Para(spans, list=mark))
            return
        image = _lone_image(tok.children or [])
        if image is not None:
            self.out._add(image)
            return
        if self.quote:
            self.out._add(Para(spans, "quote", self.quote))
            return
        self.out._add(Para(spans))


def _lone_image(children: List[Any]) -> Optional[ImageBlock]:
    real = [c for c in children if not (c.type == "text" and not c.content.strip())]
    if len(real) == 1 and real[0].type == "image":
        img = real[0]
        alt = "".join(c.content for c in (img.children or []))
        return ImageBlock(str(img.attrs.get("src", "")), alt)
    return None


def _inline_spans(children: List[Any]) -> Tuple[Span, ...]:
    out: List[Span] = []
    style = {"bold": 0, "italic": 0, "strike": 0}
    links: List[str] = []

    def push(text: str, **extra: Any) -> None:
        if not text:
            return
        s = Span(text, bold=style["bold"] > 0, italic=style["italic"] > 0,
                 strike=style["strike"] > 0, link=links[-1] if links else None, **extra)
        if out and out[-1].styled(text="") == s.styled(text=""):
            out[-1] = out[-1].styled(text=out[-1].text + text)
        else:
            out.append(s)

    for c in children:
        t = c.type
        if t == "text":
            push(c.content)
        elif t == "code_inline":
            push(c.content, code=True)
        elif t == "softbreak":
            push(" ")
        elif t == "hardbreak":
            push("\v")
        elif t in ("strong_open", "strong_close"):
            style["bold"] += 1 if t.endswith("open") else -1
        elif t in ("em_open", "em_close"):
            style["italic"] += 1 if t.endswith("open") else -1
        elif t in ("s_open", "s_close"):
            style["strike"] += 1 if t.endswith("open") else -1
        elif t == "link_open":
            links.append(str(c.attrs.get("href", "")))
        elif t == "link_close":
            if links:
                links.pop()
        elif t == "image":
            alt = "".join(x.content for x in (c.children or [])) or "image"
            links.append(str(c.attrs.get("src", "")))
            push(alt)
            links.pop()
        elif t == "html_inline":
            push(c.content)
    return tuple(out)
