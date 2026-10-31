"""An in-memory Google Docs server for tests.

:class:`FakeDocs` speaks the same HTTP API as Google (through an ``httpx`` mock
transport) and models documents the way Docs does: a run of UTF-16 index units
where paragraphs end in newlines and tables are made of table, row and cell
markers. Inserts shift indexes, bullets eat their nesting tabs, and invalid
ranges are rejected, so code that is wrong about indexes fails here first::

    from google_docs.testing import FakeDocs

    fake = FakeDocs()
    docs = fake.client()
    doc = docs.create("Test", "# Hi\\n\\n- one\\n- two")
    assert docs.get(doc).to_markdown() == "# Hi\\n\\n- one\\n- two\\n"

It covers the requests this library sends plus a few common ones. It does not try
to model layout, headers and footers, suggestions or comments.
"""

from __future__ import annotations

import copy
import itertools
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .models import utf16_len

_ids = itertools.count(1)

BULLET_GLYPHS = ("●", "○", "■")
NUMBER_GLYPHS = ("DECIMAL", "ALPHA", "ROMAN")


class FakeError(Exception):
    def __init__(self, message: str, status: str = "INVALID_ARGUMENT", code: int = 400):
        super().__init__(message)
        self.status, self.code = status, code


# --------------------------------------------------------------------------- model


@dataclass
class U:
    """One index unit."""

    kind: str  # sb, c, nl, img, pb, tbl, row, cell, end
    ch: str = ""
    ts: Dict[str, Any] = field(default_factory=dict)  # text style
    ps: Dict[str, Any] = field(default_factory=dict)  # paragraph style (on nl only)
    bullet: Optional[Dict[str, Any]] = None  # on nl only
    data: Any = None

    @property
    def width(self) -> int:
        if self.kind == "c":
            return utf16_len(self.ch)
        return 1

    @property
    def inline(self) -> bool:
        return self.kind in ("c", "img", "pb")


@dataclass
class _Tab:
    id: str
    title: str
    units: List[U]
    lists: Dict[str, Any] = field(default_factory=dict)
    objects: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _Doc:
    id: str
    title: str
    tabs: List[_Tab]
    revision: int = 1
    trashed: bool = False

    @property
    def revision_id(self) -> str:
        return f"rev-{self.id}-{self.revision}"


def _empty_units() -> List[U]:
    return [U("sb"), U("nl", "\n", ps={"namedStyleType": "NORMAL_TEXT"})]


# --------------------------------------------------------------------------- editing


class _Editor:
    """Applies batchUpdate requests to one document."""

    def __init__(self, doc: _Doc) -> None:
        self.doc = doc

    # -- addressing ---------------------------------------------------------------
    def tab(self, tab_id: Optional[str]) -> _Tab:
        if not tab_id:
            return self.doc.tabs[0]
        for t in self.doc.tabs:
            if t.id == tab_id:
                return t
        raise FakeError(f"Tab with ID {tab_id} not found.")

    @staticmethod
    def starts(units: List[U]) -> List[int]:
        out, i = [], 0
        for u in units:
            out.append(i)
            i += u.width
        out.append(i)
        return out

    def pos(self, units: List[U], index: int) -> int:
        """List position of the unit that starts at ``index``."""
        starts = self.starts(units)
        for i, s in enumerate(starts):
            if s == index:
                return i
            if s > index:
                break
        raise FakeError(f"Index {index} must not split a character or be out of bounds.")

    def location(self, req: Dict[str, Any]) -> Tuple[_Tab, int]:
        if "endOfSegmentLocation" in req:
            loc = req["endOfSegmentLocation"]
            t = self.tab(loc.get("tabId"))
            self._segment(loc)
            return t, len(t.units) - 1
        loc = req.get("location")
        if not loc:
            raise FakeError("location or endOfSegmentLocation is required")
        self._segment(loc)
        t = self.tab(loc.get("tabId"))
        i = self.pos(t.units, int(loc.get("index", 0)))
        self.check_insert(t.units, i)
        return t, i

    def range(self, req: Dict[str, Any]) -> Tuple[_Tab, int, int]:
        r = req.get("range") or {}
        self._segment(r)
        t = self.tab(r.get("tabId"))
        start, end = int(r.get("startIndex", 0)), int(r.get("endIndex", 0))
        if not 0 <= start < end:
            raise FakeError(f"Invalid range {start}-{end}: the range must not be empty.")
        total = self.starts(t.units)[-1]
        if end > total:
            raise FakeError(f"Index {end - 1} must be less than the end index of the referenced "
                            f"segment, {total}.")
        return t, self.pos(t.units, start), self.pos(t.units, end)

    @staticmethod
    def _segment(loc: Dict[str, Any]) -> None:
        if loc.get("segmentId"):
            raise FakeError("FakeDocs only models document bodies, not headers/footers/footnotes")

    @staticmethod
    def check_insert(units: List[U], i: int) -> None:
        if i == 0 or i >= len(units) or not (units[i].inline or units[i].kind == "nl"):
            raise FakeError("The insertion index must be inside the bounds of an existing "
                            "paragraph. You can still create new paragraphs by inserting "
                            "newlines.")

    @staticmethod
    def para_nl(units: List[U], i: int) -> int:
        """Position of the newline that ends the paragraph containing position ``i``."""
        j = i
        while j < len(units) and units[j].kind != "nl":
            if not units[j].inline:
                raise FakeError("position is not inside a paragraph")
            j += 1
        if j == len(units):
            raise FakeError("position is not inside a paragraph")
        return j

    # -- requests -----------------------------------------------------------------
    def apply(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if len(req) != 1:
            raise FakeError("each request must have exactly one kind")
        (kind, body), = req.items()
        handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = getattr(
            self, "_" + kind, None)
        if handler is None:
            raise FakeError(f"FakeDocs does not support {kind}")
        return handler(body)

    def _insertText(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i = self.location(r)
        # Like Docs: control and Private Use Area characters are silently dropped.
        text = re.sub("[\x00-\x08\x0c-\x1f\ue000-\uf8ff]", "", r.get("text", ""))
        if not text:
            return {}
        units = t.units
        prev = units[i - 1] if units[i - 1].inline else None
        ts = dict((prev or units[i]).ts) if (prev or units[i].inline) else {}
        nl = units[self.para_nl(units, i)]
        new: List[U] = []
        for ch in _chars(text):
            if ch == "\n":
                new.append(U("nl", "\n", dict(ts), dict(nl.ps), copy.deepcopy(nl.bullet)))
            else:
                new.append(U("c", ch, dict(ts)))
        units[i:i] = new
        return {}

    def _deleteContentRange(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i, j = self.range(r)
        units = t.units
        if j >= len(units) and units[-1].kind == "nl":
            raise FakeError("Invalid deletion range. Cannot delete the requested range: it "
                            "includes the newline at the end of the segment.")
        depth = 0
        for u in units[i:j]:
            if u.kind == "tbl":
                depth += 1
            elif u.kind == "end":
                depth -= 1
            elif u.kind in ("row", "cell") and depth == 0:
                raise FakeError("Invalid deletion range: it covers part of a table.")
            if depth < 0:
                raise FakeError("Invalid deletion range: it covers part of a table.")
        if depth:
            raise FakeError("Invalid deletion range: it covers part of a table.")
        if j < len(units) and units[j].kind in ("cell", "row", "end") and \
                units[j - 1].kind == "nl":
            raise FakeError("Invalid deletion range: the last newline of a table cell cannot "
                            "be deleted.")
        if j < len(units) and units[j].kind == "tbl" and units[j - 1].kind == "nl":
            raise FakeError("Invalid deletion range: the newline before a table cannot be "
                            "deleted.")
        del units[i:j]
        return {}

    def _replaceAllText(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        crit = r.get("containsText") or {}
        needle = crit.get("text") or ""
        if not needle:
            raise FakeError("containsText.text must not be empty")
        match_case = bool(crit.get("matchCase"))
        repl = r.get("replaceText", "")
        tab_ids = (r.get("tabsCriteria") or {}).get("tabIds")
        count = 0
        for t in self.doc.tabs:
            if tab_ids and t.id not in tab_ids:
                continue
            count += _replace_in(t.units, needle, repl, match_case)
        return {"replaceAllText": {"occurrencesChanged": count}}

    def _updateTextStyle(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i, j = self.range(r)
        fields = _fields(r)
        style = r.get("textStyle") or {}
        for u in t.units[i:j]:
            if u.inline or u.kind == "nl":
                _patch(u.ts, style, fields)
        return {}

    def _updateParagraphStyle(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i, j = self.range(r)
        fields = _fields(r)
        style = r.get("paragraphStyle") or {}
        for k in self._nls(t.units, i, j):
            _patch(t.units[k].ps, style, fields)
        return {}

    def _createParagraphBullets(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i, j = self.range(r)
        preset = r.get("bulletPreset", "BULLET_DISC_CIRCLE_SQUARE")
        list_id = f"kix.list{next(_ids)}"
        t.lists[list_id] = {"listProperties": {"nestingLevels": _levels(preset)}}
        nls = self._nls(t.units, i, j)
        # Remove nesting tabs from the bottom up so earlier positions stay valid.
        for k in reversed(nls):
            start = k
            while start > 0 and t.units[start - 1].inline:
                start -= 1
            tabs = 0
            while t.units[start + tabs].kind == "c" and t.units[start + tabs].ch == "\t":
                tabs += 1
            nl = t.units[k]
            nl.bullet = {"listId": list_id, "nestingLevel": tabs} if tabs else \
                {"listId": list_id}
            del t.units[start:start + tabs]
        return {}

    def _deleteParagraphBullets(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i, j = self.range(r)
        for k in self._nls(t.units, i, j):
            t.units[k].bullet = None
        return {}

    def _insertTable(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i = self.location(r)
        rows, cols = int(r.get("rows", 0)), int(r.get("columns", 0))
        if rows < 1 or cols < 1:
            raise FakeError("rows and columns must be at least 1")
        nl = t.units[self.para_nl(t.units, i)]
        new = [U("nl", "\n", {}, dict(nl.ps), copy.deepcopy(nl.bullet)),
               U("tbl", data=(rows, cols))]
        for _ in range(rows):
            new.append(U("row"))
            for _ in range(cols):
                new += [U("cell"), U("nl", "\n", ps={"namedStyleType": "NORMAL_TEXT"})]
        new.append(U("end"))
        t.units[i:i] = new
        return {}

    def _insertInlineImage(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i = self.location(r)
        uri = r.get("uri", "")
        if not re.match(r"^https?://", uri) or len(uri) > 2048:
            raise FakeError("Invalid requests[].insertInlineImage: the URL must be public "
                            "http(s) and under 2 kB.")
        oid = f"kix.img{next(_ids)}"
        emb: Dict[str, Any] = {"imageProperties": {"contentUri": uri}}
        if r.get("objectSize"):
            emb["size"] = r["objectSize"]
        t.objects[oid] = {"objectId": oid, "inlineObjectProperties": {"embeddedObject": emb}}
        t.units.insert(i, U("img", data=oid))
        return {"insertInlineImage": {"objectId": oid}}

    def _insertPageBreak(self, r: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N802
        t, i = self.location(r)
        if any(u.kind == "tbl" for u in t.units[:i]) and _inside_table(t.units, i):
            raise FakeError("Page breaks cannot be inserted inside a table.")
        nl = t.units[self.para_nl(t.units, i)]
        t.units[i:i] = [U("pb"), U("nl", "\n", {}, dict(nl.ps), copy.deepcopy(nl.bullet))]
        return {}

    # -- helpers --------------------------------------------------------------------
    def _nls(self, units: List[U], i: int, j: int) -> List[int]:
        """Newlines of the paragraphs that overlap positions [i, j)."""
        out = [k for k in range(i, j) if units[k].kind == "nl"]
        k = j - 1
        if k >= 0 and units[k].kind != "nl":  # range ends mid-paragraph
            while k < len(units) and units[k].inline:
                k += 1
            if k < len(units) and units[k].kind == "nl" and k not in out:
                out.append(k)
        return out


def _inside_table(units: List[U], i: int) -> bool:
    depth = 0
    for u in units[:i]:
        depth += u.kind == "tbl"
        depth -= u.kind == "end"
    return depth > 0


def _chars(text: str) -> List[str]:
    """Split into code points (an astral character is one unit of width 2)."""
    return list(text)


def _fields(r: Dict[str, Any]) -> Optional[List[str]]:
    f = r.get("fields")
    if not f:
        raise FakeError("fields is required")
    return None if f.strip() == "*" else [x.strip().split(".")[0] for x in f.split(",")]


def _patch(target: Dict[str, Any], style: Dict[str, Any], fields: Optional[List[str]]) -> None:
    if fields is None:
        target.clear()
        target.update(copy.deepcopy(style))
        return
    for f in fields:
        if f in style:
            target[f] = copy.deepcopy(style[f])
        else:
            target.pop(f, None)
            if f == "namedStyleType":
                target[f] = "NORMAL_TEXT"


def _levels(preset: str) -> List[Dict[str, Any]]:
    out = []
    for n in range(9):
        if preset.startswith("NUMBERED"):
            out.append({"glyphType": NUMBER_GLYPHS[n % 3], "glyphFormat": f"%{n}."})
        elif "CHECKBOX" in preset:
            out.append({"glyphType": "GLYPH_TYPE_UNSPECIFIED"})
        else:
            out.append({"glyphSymbol": BULLET_GLYPHS[n % 3]})
    return out


def _replace_in(units: List[U], needle: str, repl: str, match_case: bool) -> int:
    count = 0
    i = 0
    want = needle if match_case else needle.lower()
    while i < len(units):
        if units[i].kind != "c":
            i += 1
            continue
        j = i
        while j < len(units) and units[j].kind == "c":
            j += 1
        text = "".join(u.ch for u in units[i:j])
        hay = text if match_case else text.lower()
        hits = []
        p = hay.find(want)
        while p != -1:
            hits.append(p)
            p = hay.find(want, p + len(want))
        for p in reversed(hits):
            # character offsets map 1:1 onto the c units of this run
            ts = dict(units[i + p].ts)
            units[i + p:i + p + len(needle)] = [U("c", ch, dict(ts)) for ch in repl]
            count += 1
        j = i + len(text) + sum(len(repl) - len(needle) for _ in hits)
        i = j + 1
    return count


# --------------------------------------------------------------------------- reading


def _serialize(doc: _Doc, with_tabs: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {"documentId": doc.id, "title": doc.title,
                           "revisionId": doc.revision_id}
    tabs: List[Dict[str, Any]] = []
    for n, t in enumerate(doc.tabs):
        content = _content(t.units)
        tabs.append({"tabProperties": {"tabId": t.id, "title": t.title, "index": n},
                     "documentTab": {"body": {"content": content}, "lists": copy.deepcopy(t.lists),
                                     "inlineObjects": copy.deepcopy(t.objects)}})
    if with_tabs:
        out["tabs"] = tabs
    else:
        out.update(tabs[0]["documentTab"])
    return out


def _content(units: List[U]) -> List[Dict[str, Any]]:
    starts = _Editor.starts(units)
    pos = 0
    out, pos = _blocks(units, starts, pos, stop=lambda u: False)
    return out


def _blocks(units: List[U], starts: List[int], pos: int,
            stop: Callable[[U], bool]) -> Tuple[List[Dict[str, Any]], int]:
    out: List[Dict[str, Any]] = []
    while pos < len(units) and not stop(units[pos]):
        u = units[pos]
        if u.kind == "sb":
            out.append({"endIndex": 1, "sectionBreak": {"sectionStyle": {
                "sectionType": "CONTINUOUS"}}})
            pos += 1
        elif u.kind == "tbl":
            out_table, pos = _table(units, starts, pos)
            out.append(out_table)
        else:
            para, pos = _paragraph(units, starts, pos)
            out.append(para)
    return out, pos


def _paragraph(units: List[U], starts: List[int], pos: int) -> Tuple[Dict[str, Any], int]:
    begin = pos
    elements: List[Dict[str, Any]] = []
    while True:
        u = units[pos]
        s, e = starts[pos], starts[pos] + u.width
        if u.kind in ("c", "nl"):
            last = elements[-1] if elements else None
            if last and "textRun" in last and last["textRun"].get("textStyle", {}) == u.ts:
                last["textRun"]["content"] += u.ch
                last["endIndex"] = e
            else:
                elements.append({"startIndex": s, "endIndex": e,
                                 "textRun": {"content": u.ch, "textStyle": dict(u.ts)}})
        elif u.kind == "img":
            elements.append({"startIndex": s, "endIndex": e, "inlineObjectElement": {
                "inlineObjectId": u.data, "textStyle": dict(u.ts)}})
        elif u.kind == "pb":
            elements.append({"startIndex": s, "endIndex": e, "pageBreak": {"textStyle": {}}})
        else:
            raise AssertionError(f"unexpected {u.kind} inside a paragraph")
        pos += 1
        if u.kind == "nl":
            break
    para: Dict[str, Any] = {"elements": elements,
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT", **u.ps}}
    if u.bullet:
        para["bullet"] = dict(u.bullet)
    return {"startIndex": starts[begin], "endIndex": starts[pos], "paragraph": para}, pos


def _table(units: List[U], starts: List[int], pos: int) -> Tuple[Dict[str, Any], int]:
    begin = pos
    rows, cols = units[pos].data
    pos += 1
    table_rows = []
    for _ in range(rows):
        row_start = pos
        pos += 1  # row marker
        cells = []
        for _ in range(cols):
            cell_start = pos
            pos += 1  # cell marker
            content, pos = _blocks(units, starts, pos,
                                   stop=lambda u: u.kind in ("cell", "row", "end"))
            cells.append({"startIndex": starts[cell_start], "endIndex": starts[pos],
                          "content": content})
        table_rows.append({"startIndex": starts[row_start], "endIndex": starts[pos],
                           "tableCells": cells})
    assert units[pos].kind == "end"
    pos += 1
    return {"startIndex": starts[begin], "endIndex": starts[pos],
            "table": {"rows": rows, "columns": cols, "tableRows": table_rows}}, pos


# --------------------------------------------------------------------------- server


class FakeDocs:
    """In-memory Docs + Drive server. Use :meth:`client` or :meth:`transport`."""

    def __init__(self) -> None:
        self.documents: Dict[str, _Doc] = {}
        self.requests: List[httpx.Request] = []
        self._failures: List[Tuple[int, Any, Dict[str, str]]] = []

    # -- setup --------------------------------------------------------------------
    def add_document(self, title: str = "Untitled document",
                     tabs: Tuple[str, ...] = ("Tab 1",)) -> str:
        """Create a document directly (no HTTP) and return its ID."""
        doc_id = f"fake{next(_ids):06d}" + "x" * 30
        self.documents[doc_id] = _Doc(doc_id, title, [
            _Tab(f"t.{n}" if n else "t.0", name, _empty_units()) for n, name in enumerate(tabs)])
        return doc_id

    def fail_next(self, status: int, *, times: int = 1, body: Any = None,
                  headers: Optional[Dict[str, str]] = None) -> None:
        """Answer the next ``times`` requests with ``status``."""
        for _ in range(times):
            self._failures.append((status, body, headers or {}))

    def edit_elsewhere(self, document_id: str) -> None:
        """Pretend another user edited the document (bumps the revision)."""
        self.documents[document_id].revision += 1

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def async_transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self, **kwargs: Any) -> Any:
        from .client import DocsClient

        kwargs.setdefault("backoff_base", 0)
        return DocsClient("fake-token", http=httpx.Client(transport=self.transport()), **kwargs)

    def async_client(self, **kwargs: Any) -> Any:
        from .async_client import AsyncDocsClient

        kwargs.setdefault("backoff_base", 0)
        return AsyncDocsClient("fake-token",
                               http=httpx.AsyncClient(transport=self.transport()), **kwargs)

    # -- HTTP ---------------------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._failures:
            status, body, headers = self._failures.pop(0)
            return httpx.Response(status, json=body or _err(status, "injected failure",
                                                            "UNAVAILABLE"), headers=headers)
        try:
            return self._route(request)
        except FakeError as e:
            return httpx.Response(e.code, json=_err(e.code, str(e), e.status))

    def _route(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = json.loads(req.content) if req.content else {}
        m = re.fullmatch(r"/v1/documents(?:/([^/:]+))?(:batchUpdate)?", path)
        if m and req.url.host == "docs.googleapis.com":
            doc_id, batch = m.groups()
            if doc_id is None and req.method == "POST":
                new = self._doc(self.add_document(body.get("title") or "Untitled document"))
                return httpx.Response(200, json=_serialize(new, with_tabs=False))
            doc = self._doc(doc_id or "")
            if batch and req.method == "POST":
                return httpx.Response(200, json=self._batch(doc, body))
            if req.method == "GET":
                tabs = req.url.params.get("includeTabsContent") == "true"
                return httpx.Response(200, json=_serialize(doc, tabs))
        m = re.fullmatch(r"/drive/v3/files/([^/]+)(/copy|/export)?", path)
        if m and req.url.host == "www.googleapis.com":
            doc = self._doc(m.group(1))
            if m.group(2) == "/copy":
                new_id = self.add_document(body.get("name") or f"Copy of {doc.title}")
                self.documents[new_id].tabs = copy.deepcopy(doc.tabs)
                return httpx.Response(200, json={"id": new_id})
            if m.group(2) == "/export":
                return self._export(doc, req.url.params.get("mimeType", ""))
            if req.method == "PATCH":
                doc.trashed = bool(body.get("trashed"))
                return httpx.Response(200, json={"id": doc.id, "trashed": doc.trashed})
        raise FakeError(f"FakeDocs has no route for {req.method} {path}", "NOT_FOUND", 404)

    def _doc(self, doc_id: str) -> _Doc:
        doc = self.documents.get(doc_id)
        if doc is None or doc.trashed:
            raise FakeError("Requested entity was not found.", "NOT_FOUND", 404)
        return doc

    def _batch(self, doc: _Doc, body: Dict[str, Any]) -> Dict[str, Any]:
        wc = body.get("writeControl") or {}
        required = wc.get("requiredRevisionId")
        if required and required != doc.revision_id:
            raise FakeError(f"The required revision ID '{required}' does not match the latest "
                            f"revision '{doc.revision_id}'.")
        work = copy.deepcopy(doc)
        editor = _Editor(work)
        replies = []
        for n, req in enumerate(body.get("requests") or []):
            try:
                replies.append(editor.apply(req))
            except FakeError as e:
                raise FakeError(f"Invalid requests[{n}].{next(iter(req), '?')}: {e}") from None
        work.revision += 1
        self.documents[doc.id] = work
        return {"documentId": doc.id, "replies": replies,
                "writeControl": {"requiredRevisionId": work.revision_id}}

    def _export(self, doc: _Doc, mime: str) -> httpx.Response:
        from .models import Document

        parsed = Document.from_api(_serialize(doc, with_tabs=True))
        if mime == "text/plain":
            return httpx.Response(200, content=parsed.text.encode(), headers={
                "Content-Type": mime})
        if mime == "text/markdown":
            return httpx.Response(200, content=parsed.to_markdown().encode())
        head = b"%PDF-1.4\n%fake\n" if mime == "application/pdf" else b"%fake " + mime.encode()
        return httpx.Response(200, content=head + b"\n" + parsed.text.encode(),
                              headers={"Content-Type": mime})


def _err(code: int, message: str, status: str) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "status": status}}
