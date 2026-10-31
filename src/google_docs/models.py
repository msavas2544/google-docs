"""A typed, read-only view of a Google Docs document.

The Docs API returns a deep tree of JSON. These classes wrap it so you can walk a
document the way you think about it (tabs, headings, paragraphs, tables, cells)
without losing anything: every object keeps the raw JSON in ``.raw``.

Indexes are the API's own: offsets in UTF-16 code units, end-exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover
    import pandas

DOCS_URL = "https://docs.google.com/document/d/{}/edit"

#: Fonts we treat as "code" when turning a document into Markdown.
MONOSPACE_FONTS = frozenset({
    "consolas", "courier", "courier new", "fira code", "fira mono", "ibm plex mono",
    "inconsolata", "jetbrains mono", "menlo", "monaco", "roboto mono", "source code pro",
    "space mono", "ubuntu mono", "cousine", "anonymous pro", "noto sans mono", "pt mono",
})

#: List glyph types that mean "numbered".
ORDERED_GLYPHS = frozenset({"DECIMAL", "ZERO_DECIMAL", "UPPER_ALPHA", "ALPHA",
                            "UPPER_ROMAN", "ROMAN"})


def utf16_len(text: str) -> int:
    """Length of ``text`` the way the Docs API counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


# --------------------------------------------------------------------------- inline


@dataclass(frozen=True)
class TextRun:
    """A stretch of text that shares one style."""

    start: int
    end: int
    text: str
    raw: Dict[str, Any] = field(repr=False, compare=False)

    @property
    def style(self) -> Dict[str, Any]:
        return dict(self.raw.get("textRun", {}).get("textStyle") or {})

    @property
    def bold(self) -> bool:
        return bool(self.style.get("bold"))

    @property
    def italic(self) -> bool:
        return bool(self.style.get("italic"))

    @property
    def underline(self) -> bool:
        return bool(self.style.get("underline"))

    @property
    def strikethrough(self) -> bool:
        return bool(self.style.get("strikethrough"))

    @property
    def link(self) -> Optional[str]:
        link = self.style.get("link") or {}
        if link.get("url"):
            return str(link["url"])
        for key in ("heading", "bookmark"):  # tabs-aware shape: {"heading": {"id", "tabId"}}
            target = link.get(key) or {}
            if target.get("id"):
                return f"#{target['id']}"
            if link.get(key + "Id"):  # legacy shape
                return f"#{link[key + 'Id']}"
        return None

    @property
    def font(self) -> Optional[str]:
        family = (self.style.get("weightedFontFamily") or {}).get("fontFamily")
        return str(family) if family else None

    @property
    def is_code(self) -> bool:
        """True when the run is set in a monospace font."""
        return bool(self.font and self.font.lower() in MONOSPACE_FONTS)


@dataclass(frozen=True)
class InlineImage:
    """An image that sits inside a paragraph."""

    start: int
    end: int
    object_id: str
    uri: Optional[str]
    title: Optional[str]
    description: Optional[str]
    width_pt: Optional[float]
    height_pt: Optional[float]
    raw: Dict[str, Any] = field(repr=False, compare=False)

    text = ""


@dataclass(frozen=True)
class Chip:
    """A smart chip: a person, a rich link, a date, or a footnote reference."""

    start: int
    end: int
    kind: str  # "person" | "rich_link" | "date" | "footnote"
    text: str
    url: Optional[str]
    raw: Dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True)
class Marker:
    """A page break, column break, horizontal rule, or an element we don't model."""

    start: int
    end: int
    kind: str  # "page_break" | "column_break" | "horizontal_rule" | "equation" | "other"
    raw: Dict[str, Any] = field(repr=False, compare=False)

    text = ""


Inline = Union[TextRun, InlineImage, Chip, Marker]


# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True)
class Paragraph:
    start: int
    end: int
    elements: Tuple[Inline, ...]
    style_name: str
    list_id: Optional[str]
    nesting_level: int
    raw: Dict[str, Any] = field(repr=False, compare=False)
    _lists: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def text(self) -> str:
        """The paragraph's text without its closing newline."""
        return "".join(e.text for e in self.elements).rstrip("\n")

    @property
    def heading_level(self) -> Optional[int]:
        """1-6 for ``HEADING_n``, 0 for ``TITLE``, ``None`` for anything else."""
        if self.style_name.startswith("HEADING_"):
            return int(self.style_name[-1])
        return 0 if self.style_name == "TITLE" else None

    @property
    def is_heading(self) -> bool:
        return self.heading_level is not None

    @property
    def heading_id(self) -> Optional[str]:
        return (self.raw.get("paragraphStyle") or {}).get("headingId")

    @property
    def is_list_item(self) -> bool:
        return self.list_id is not None

    @property
    def is_ordered(self) -> bool:
        """True for numbered list items."""
        if not self.list_id:
            return False
        levels = ((self._lists.get(self.list_id) or {}).get("listProperties") or {}).get(
            "nestingLevels") or []
        if self.nesting_level < len(levels):
            return levels[self.nesting_level].get("glyphType") in ORDERED_GLYPHS
        return False

    @property
    def is_empty(self) -> bool:
        return not self.elements or all(
            isinstance(e, TextRun) and not e.text.strip("\n") for e in self.elements)

    @property
    def content_end(self) -> int:
        """Index of the paragraph's closing newline (where appended text goes)."""
        return self.end - 1


@dataclass(frozen=True)
class Cell:
    start: int
    end: int
    content: Tuple[Block, ...]
    raw: Dict[str, Any] = field(repr=False, compare=False)

    @property
    def paragraphs(self) -> List[Paragraph]:
        return [b for b in self.content if isinstance(b, Paragraph)]

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


@dataclass(frozen=True)
class Table:
    start: int
    end: int
    rows: Tuple[Tuple[Cell, ...], ...]
    raw: Dict[str, Any] = field(repr=False, compare=False)

    @property
    def shape(self) -> Tuple[int, int]:
        return len(self.rows), max((len(r) for r in self.rows), default=0)

    @property
    def values(self) -> List[List[str]]:
        """Cell texts, row by row."""
        return [[c.text for c in row] for row in self.rows]

    def cell(self, row: int, column: int) -> Cell:
        return self.rows[row][column]

    def records(self) -> List[Dict[str, str]]:
        """Rows as dicts keyed by the first row."""
        values = self.values
        if not values:
            return []
        header = values[0]
        return [dict(zip(header, row)) for row in values[1:]]

    def to_pandas(self, header: bool = True) -> pandas.DataFrame:
        """Turn the table into a :class:`pandas.DataFrame` (pandas must be installed)."""
        import pandas as pd

        values = self.values
        if header and values:
            return pd.DataFrame(values[1:], columns=values[0])
        return pd.DataFrame(values)


@dataclass(frozen=True)
class SectionBreak:
    start: int
    end: int
    raw: Dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True)
class TableOfContents:
    start: int
    end: int
    content: Tuple[Block, ...]
    raw: Dict[str, Any] = field(repr=False, compare=False)


Block = Union[Paragraph, Table, SectionBreak, TableOfContents]


# --------------------------------------------------------------------------- body / tab


@dataclass(frozen=True)
class Body:
    """The content of one tab (or a header, footer or footnote)."""

    content: Tuple[Block, ...]
    lists: Dict[str, Any] = field(default_factory=dict, repr=False)
    inline_objects: Dict[str, Any] = field(default_factory=dict, repr=False)
    footnotes: Dict[str, Body] = field(default_factory=dict, repr=False)

    @property
    def end_index(self) -> int:
        return self.content[-1].end if self.content else 1

    @property
    def paragraphs(self) -> List[Paragraph]:
        """Top-level paragraphs (not the ones inside tables)."""
        return [b for b in self.content if isinstance(b, Paragraph)]

    def walk(self) -> Iterator[Paragraph]:
        """Every paragraph in reading order, including those inside table cells."""
        yield from _walk(self.content)

    @property
    def tables(self) -> List[Table]:
        return [b for b in self.content if isinstance(b, Table)]

    @property
    def headings(self) -> List[Paragraph]:
        return [p for p in self.paragraphs if p.is_heading]

    @property
    def text(self) -> str:
        """Plain text, one line per paragraph (table cells included)."""
        return "\n".join(p.text for p in self.walk())

    def find(self, needle: str, *, match_case: bool = True) -> List[Tuple[int, int]]:
        """``(start, end)`` index ranges of every occurrence of ``needle``.

        Matches never cross paragraph boundaries, the same rule the API's
        ``replaceAllText`` follows."""
        if not needle:
            raise ValueError("needle must not be empty")
        hits: List[Tuple[int, int]] = []
        want = needle if match_case else needle.lower()
        for p in self.walk():
            for start, text in _text_chunks(p):
                hay = text if match_case else text.lower()
                pos = hay.find(want)
                while pos != -1:
                    s = start + utf16_len(text[:pos])
                    hits.append((s, s + utf16_len(needle)))
                    pos = hay.find(want, pos + len(want))
        return hits

    def heading(self, title: str) -> Paragraph:
        """The first heading whose text equals ``title`` (case-insensitive)."""
        from .errors import NotFoundInDocument

        want = title.strip().casefold()
        for p in self.headings:
            if p.text.strip().casefold() == want:
                return p
        raise NotFoundInDocument(f"no heading {title!r}")

    def section(self, title: str) -> Tuple[int, int]:
        """Index range covered by the heading ``title`` and everything under it, up to the
        next heading of the same or a higher level."""
        head = self.heading(title)
        level = head.heading_level or 0
        end = self.end_index - 1
        seen = False
        for b in self.content:
            if b is head:
                seen = True
                continue
            if seen and isinstance(b, Paragraph) and b.heading_level is not None \
                    and b.heading_level <= level:
                end = b.start
                break
        return head.start, end

    def to_markdown(self) -> str:
        from ._markdown_out import render

        return render(self)


@dataclass(frozen=True)
class Tab:
    id: Optional[str]
    title: str
    index: int
    parent_id: Optional[str]
    level: int
    body: Body
    raw: Dict[str, Any] = field(repr=False, compare=False)

    @property
    def text(self) -> str:
        return self.body.text

    def to_markdown(self) -> str:
        return self.body.to_markdown()


@dataclass(frozen=True)
class Document:
    """A snapshot of a document. Re-read it with :meth:`DocsClient.get` after editing."""

    id: str
    title: str
    revision_id: Optional[str]
    tabs: Tuple[Tab, ...]
    raw: Dict[str, Any] = field(repr=False, compare=False)

    @property
    def url(self) -> str:
        return DOCS_URL.format(self.id)

    # Everything below reads the first tab, which is what most documents have.
    @property
    def body(self) -> Body:
        return self.tabs[0].body

    @property
    def text(self) -> str:
        return self.body.text

    @property
    def paragraphs(self) -> List[Paragraph]:
        return self.body.paragraphs

    @property
    def headings(self) -> List[Paragraph]:
        return self.body.headings

    @property
    def tables(self) -> List[Table]:
        return self.body.tables

    def find(self, needle: str, *, match_case: bool = True) -> List[Tuple[int, int]]:
        return self.body.find(needle, match_case=match_case)

    def to_markdown(self) -> str:
        return self.body.to_markdown()

    def tab(self, title_or_id: str) -> Tab:
        """Find a tab by title (case-insensitive) or ID."""
        from .errors import NotFoundInDocument

        for t in self.tabs:
            if t.id == title_or_id:
                return t
        for t in self.tabs:
            if t.title.casefold() == title_or_id.casefold():
                return t
        raise NotFoundInDocument(f"no tab {title_or_id!r}; tabs: {[t.title for t in self.tabs]}")

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Document:
        tabs: List[Tab] = []
        if data.get("tabs"):
            _collect_tabs(data["tabs"], None, 0, tabs)
        else:  # fetched without includeTabsContent: the legacy single-body shape
            tabs.append(Tab(id=None, title=data.get("title", ""), index=0, parent_id=None,
                            level=0, body=_body(data, data.get("body") or {}), raw={}))
        if not tabs:
            tabs.append(Tab(None, "", 0, None, 0, Body(()), {}))
        return cls(id=data.get("documentId", ""), title=data.get("title", ""),
                   revision_id=data.get("revisionId"), tabs=tuple(tabs), raw=data)


# --------------------------------------------------------------------------- parsing


def _collect_tabs(raw_tabs: List[Dict[str, Any]], parent: Optional[str], level: int,
                  out: List[Tab]) -> None:
    for t in raw_tabs:
        props = t.get("tabProperties") or {}
        doc_tab = t.get("documentTab") or {}
        out.append(Tab(id=props.get("tabId"), title=props.get("title", ""),
                       index=int(props.get("index", 0)), parent_id=parent, level=level,
                       body=_body(doc_tab, doc_tab.get("body") or {}), raw=t))
        _collect_tabs(t.get("childTabs") or [], props.get("tabId"), level + 1, out)


def _body(container: Dict[str, Any], body: Dict[str, Any]) -> Body:
    lists = container.get("lists") or {}
    objects = container.get("inlineObjects") or {}
    footnotes = {
        fid: Body(_blocks(f.get("content") or [], lists, objects), lists, objects)
        for fid, f in (container.get("footnotes") or {}).items()
    }
    return Body(_blocks(body.get("content") or [], lists, objects), lists, objects, footnotes)


def _blocks(content: List[Dict[str, Any]], lists: Dict[str, Any],
            objects: Dict[str, Any]) -> Tuple[Block, ...]:
    out: List[Block] = []
    for el in content:
        start, end = int(el.get("startIndex", 0)), int(el.get("endIndex", 0))
        if "paragraph" in el:
            out.append(_paragraph(el, start, end, lists, objects))
        elif "table" in el:
            rows = []
            for row in el["table"].get("tableRows") or []:
                rows.append(tuple(
                    Cell(int(c.get("startIndex", 0)), int(c.get("endIndex", 0)),
                         _blocks(c.get("content") or [], lists, objects), c)
                    for c in row.get("tableCells") or []))
            out.append(Table(start, end, tuple(rows), el))
        elif "tableOfContents" in el:
            toc = el["tableOfContents"].get("content") or []
            out.append(TableOfContents(start, end, _blocks(toc, lists, objects), el))
        elif "sectionBreak" in el:
            out.append(SectionBreak(start, end, el))
    return tuple(out)


def _paragraph(el: Dict[str, Any], start: int, end: int, lists: Dict[str, Any],
               objects: Dict[str, Any]) -> Paragraph:
    para = el["paragraph"]
    style = para.get("paragraphStyle") or {}
    bullet = para.get("bullet") or {}
    elements: List[Inline] = []
    for pe in para.get("elements") or []:
        s, e = int(pe.get("startIndex", 0)), int(pe.get("endIndex", 0))
        if "textRun" in pe:
            elements.append(TextRun(s, e, pe["textRun"].get("content", ""), pe))
        elif "inlineObjectElement" in pe:
            oid = pe["inlineObjectElement"].get("inlineObjectId", "")
            emb = ((objects.get(oid) or {}).get("inlineObjectProperties") or {}).get(
                "embeddedObject") or {}
            size = emb.get("size") or {}
            elements.append(InlineImage(
                s, e, oid, (emb.get("imageProperties") or {}).get("contentUri"),
                emb.get("title"), emb.get("description"),
                (size.get("width") or {}).get("magnitude"),
                (size.get("height") or {}).get("magnitude"), pe))
        elif "person" in pe:
            props = pe["person"].get("personProperties") or {}
            email = props.get("email")
            elements.append(Chip(s, e, "person", props.get("name") or email or "",
                                 f"mailto:{email}" if email else None, pe))
        elif "richLink" in pe:
            props = pe["richLink"].get("richLinkProperties") or {}
            elements.append(Chip(s, e, "rich_link", props.get("title") or props.get("uri", ""),
                                 props.get("uri"), pe))
        elif "dateElement" in pe:
            props = pe["dateElement"].get("dateElementProperties") or {}
            elements.append(Chip(s, e, "date", props.get("displayText", ""), None, pe))
        elif "footnoteReference" in pe:
            ref = pe["footnoteReference"]
            elements.append(Chip(s, e, "footnote", str(ref.get("footnoteNumber", "")),
                                 ref.get("footnoteId"), pe))
        else:
            kind = next((k for k in ("pageBreak", "columnBreak", "horizontalRule", "equation")
                         if k in pe), "other")
            elements.append(Marker(s, e, _snake(kind), pe))
    return Paragraph(start, end, tuple(elements), style.get("namedStyleType", "NORMAL_TEXT"),
                     bullet.get("listId"), int(bullet.get("nestingLevel", 0)), para, lists)


def _walk(blocks: Tuple[Block, ...]) -> Iterator[Paragraph]:
    for b in blocks:
        if isinstance(b, Paragraph):
            yield b
        elif isinstance(b, Table):
            for row in b.rows:
                for cell in row:
                    yield from _walk(cell.content)


def _text_chunks(p: Paragraph) -> Iterator[Tuple[int, str]]:
    """Runs of adjacent text inside a paragraph, with the index where each starts."""
    start: Optional[int] = None
    buf: List[str] = []
    for e in p.elements:
        if isinstance(e, TextRun):
            if start is None:
                start = e.start
            buf.append(e.text)
        elif start is not None:
            yield start, "".join(buf)
            start, buf = None, []
    if start is not None:
        yield start, "".join(buf)


def _snake(name: str) -> str:
    return "".join("_" + c.lower() if c.isupper() else c for c in name)
