"""Every operation, written once, without I/O.

Each function is a generator: it yields :class:`~google_docs._transport.Call`
objects and receives their results. :class:`~google_docs.DocsClient` drives them
with ``httpx.Client`` and :class:`~google_docs.AsyncDocsClient` with
``httpx.AsyncClient``, so the two clients cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Mapping, Optional, Tuple, TypeVar, Union

from ._plan import Where, plan_blocks, plan_inline
from ._transport import DOCS_API, DRIVE_API, Call, document_id
from .content import Content, Para, as_content
from .errors import DocsError, NotFoundInDocument, RevisionConflict
from .models import Body, Document, Paragraph, SectionBreak, Table

T = TypeVar("T")
Op = Generator[Call, Any, T]
ContentLike = Union[str, Content]

EXPORT_FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "application/zip",
    "epub": "application/epub+zip",
}


@dataclass(frozen=True)
class WriteResult:
    """What a write returned: the new revision and the API's per-request replies."""

    document_id: str
    revision_id: Optional[str]
    replies: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    #: For :meth:`DocsClient.fill`: how many times each placeholder was replaced.
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://docs.google.com/document/d/{self.document_id}/edit"


# --------------------------------------------------------------------------- basics


def get(ref: Any, *, tabs: bool = True) -> Op[Document]:
    params = {"includeTabsContent": "true"} if tabs else None
    data = yield Call("GET", f"{DOCS_API}documents/{document_id(ref)}", params)
    return Document.from_api(data)


def create(title: str) -> Op[Document]:
    data = yield Call("POST", f"{DOCS_API}documents", json={"title": title}, idempotent=False)
    return Document.from_api(data)


def batch_update(ref: Any, requests: List[Dict[str, Any]],
                 revision_id: Optional[str] = None) -> Op[WriteResult]:
    doc_id = document_id(ref)
    body: Dict[str, Any] = {"requests": requests}
    if revision_id:
        body["writeControl"] = {"requiredRevisionId": revision_id}
    data = yield Call("POST", f"{DOCS_API}documents/{doc_id}:batchUpdate", json=body,
                      idempotent=False)
    revision = (data.get("writeControl") or {}).get("requiredRevisionId")
    return WriteResult(data.get("documentId") or doc_id, revision, data.get("replies") or [])


def edit(ref: Any, build: Callable[[Document], List[Dict[str, Any]]], *,
         retries: int = 1) -> Op[WriteResult]:
    """Read, plan, write, guarded by the revision we read.

    If someone else edits in between, the API refuses the write (nothing is applied)
    and we start over from a fresh read."""
    attempt = 0
    while True:
        doc = yield from get(ref)
        requests = build(doc)
        if not requests:
            return WriteResult(doc.id, doc.revision_id)
        try:
            return (yield from batch_update(doc.id, requests, doc.revision_id))
        except RevisionConflict:
            if attempt >= retries:
                raise
            attempt += 1


# --------------------------------------------------------------------------- writing


def target(doc: Document, tab: Optional[str]) -> Tuple[Body, Where]:
    t = doc.tab(tab) if tab else doc.tabs[0]
    return t.body, Where(tab_id=t.id)


def append(ref: Any, content: ContentLike, *, tab: Optional[str] = None) -> Op[WriteResult]:
    blocks = as_content(content).blocks

    def build(doc: Document) -> List[Dict[str, Any]]:
        body, where = target(doc, tab)
        last = _last_paragraph(body)
        return plan_blocks(blocks, last.end - 1, where=where, open_paragraph=not last.is_empty,
                           tail_empty=True).requests

    return (yield from edit(ref, build))


def prepend(ref: Any, content: ContentLike, *, tab: Optional[str] = None) -> Op[WriteResult]:
    blocks = as_content(content).blocks

    def build(doc: Document) -> List[Dict[str, Any]]:
        body, where = target(doc, tab)
        first = next((b for b in body.content if not isinstance(b, SectionBreak)), None)
        if not isinstance(first, Paragraph):
            raise DocsError("this document starts with a table, and the Docs API cannot "
                            "insert anything before it; add an empty line above it first")
        return plan_blocks(blocks, first.start, where=where, open_paragraph=False,
                           tail_empty=first.is_empty).requests

    return (yield from edit(ref, build))


def insert_after(ref: Any, heading: str, content: ContentLike, *,
                 tab: Optional[str] = None) -> Op[WriteResult]:
    blocks = as_content(content).blocks

    def build(doc: Document) -> List[Dict[str, Any]]:
        body, where = target(doc, tab)
        head = body.heading(heading)
        return _after(body, head, blocks, where)

    return (yield from edit(ref, build))


def replace_section(ref: Any, heading: str, content: ContentLike, *,
                    tab: Optional[str] = None) -> Op[WriteResult]:
    blocks = as_content(content).blocks

    def build(doc: Document) -> List[Dict[str, Any]]:
        body, where = target(doc, tab)
        head = body.heading(heading)
        _, end = body.section(heading)
        requests: List[Dict[str, Any]] = []
        if end > head.end:
            requests.append({"deleteContentRange": {"range": where.range(head.end, end)}})
        if end > head.end and end == body.end_index - 1:
            # The section ran to the end: the document's last newline survives as an empty
            # paragraph right under the heading. Write into it so nothing is left over.
            requests += plan_blocks(blocks, head.end, where=where, tail_empty=True).requests
        elif end > head.end:
            # The next heading now starts right where the deleted section began.
            requests += plan_blocks(blocks, head.end, where=where, tail_empty=False).requests
        else:
            requests += _after(body, head, blocks, where)
        return requests

    return (yield from edit(ref, build))


def replace_text(ref: Any, find: str, replace: str, *, match_case: bool = True,
                 tab: Optional[str] = None) -> Op[int]:
    if not find:
        raise ValueError("find must not be empty")

    def build(doc: Document) -> List[Dict[str, Any]]:
        return [_replace_all(find, replace, match_case, doc, tab)]

    result = yield from edit(ref, build)
    return _occurrences(result.replies[0]) if result.replies else 0


def fill(ref: Any, values: Mapping[str, Any], *, tab: Optional[str] = None,
         open: str = "{{", close: str = "}}") -> Op[WriteResult]:
    """Replace ``{{key}}`` placeholders. Text values replace every occurrence in the
    document, headers and footers included. :class:`Content` values (rich text,
    lists, tables...) replace placeholders in the body of ``tab``."""
    plain = {k: "" if v is None else str(v) for k, v in values.items()
             if not isinstance(v, Content)}
    rich = {k: v for k, v in values.items() if isinstance(v, Content)}
    counts: Dict[str, int] = {}

    def build(doc: Document) -> List[Dict[str, Any]]:
        counts.clear()
        requests: List[Dict[str, Any]] = []
        if rich:
            body, where = target(doc, tab)
            top_level = {id(b) for b in body.content}
            edits: List[Tuple[int, int, Paragraph, Content, str]] = []
            for p in body.walk():
                for key, content in rich.items():
                    token = f"{open}{key}{close}"
                    for s, e in Body((p,)).find(token):
                        edits.append((s, e, p, content, key))
            for s, e, p, content, key in sorted(edits, key=lambda x: -x[0]):
                counts[key] = counts.get(key, 0) + 1
                whole = p.text.strip() == f"{open}{key}{close}" and id(p) in top_level
                if whole:
                    if p.end - 1 > p.start:
                        requests.append({"deleteContentRange": {
                            "range": where.range(p.start, p.end - 1)}})
                    requests += plan_blocks(content.blocks, p.start, where=where,
                                            tail_empty=True).requests
                else:
                    spans = _inline_spans(content, key)
                    requests.append({"deleteContentRange": {"range": where.range(s, e)}})
                    requests += plan_inline(spans, s, where=where).requests
        for key, value in plain.items():
            requests.append(_replace_all(f"{open}{key}{close}", value, True, doc, None))
        return requests

    result = yield from edit(ref, build)
    replies = result.replies[-len(plain):] if plain else []
    for key, reply in zip(plain, replies):
        counts[key] = _occurrences(reply)
    ordered = {key: counts.get(key, 0) for key in values}
    return WriteResult(result.document_id, result.revision_id, result.replies, ordered)


# --------------------------------------------------------------------------- drive


def copy(ref: Any, title: str, *, folder_id: Optional[str] = None) -> Op[Document]:
    body: Dict[str, Any] = {"name": title}
    if folder_id:
        body["parents"] = [folder_id]
    data = yield Call("POST", f"{DRIVE_API}files/{document_id(ref)}/copy",
                      {"supportsAllDrives": "true", "fields": "id"}, body, idempotent=False)
    return (yield from get(data["id"]))


def export(ref: Any, fmt: str) -> Op[bytes]:
    mime = EXPORT_FORMATS.get(fmt.lower().lstrip("."), fmt)
    if "/" not in mime:
        raise ValueError(f"unknown format {fmt!r}; use one of {sorted(EXPORT_FORMATS)}")
    data = yield Call("GET", f"{DRIVE_API}files/{document_id(ref)}/export",
                      {"mimeType": mime}, raw=True)
    return bytes(data)


def trash(ref: Any) -> Op[None]:
    yield Call("PATCH", f"{DRIVE_API}files/{document_id(ref)}",
               {"supportsAllDrives": "true"}, {"trashed": True})


# --------------------------------------------------------------------------- helpers


def _after(body: Body, para: Paragraph, blocks: List[Any], where: Where) -> List[Dict[str, Any]]:
    """Requests that put ``blocks`` right after ``para`` without touching ``para`` itself.

    We insert in front of the next paragraph, so ``para`` (a heading, usually) keeps its
    newline, style and heading ID. Only when a table follows, which cannot be written in
    front of, do we split off the end of ``para`` instead."""
    following = [b for b in body.content if b.start >= para.end]
    nxt = following[0] if following else None
    if isinstance(nxt, Paragraph):
        return plan_blocks(blocks, nxt.start, where=where, tail_empty=False).requests
    return plan_blocks(blocks, para.end - 1, where=where, open_paragraph=True,
                       tail_empty=True).requests


def _last_paragraph(body: Body) -> Paragraph:
    for b in reversed(body.content):
        if isinstance(b, Paragraph):
            return b
        if isinstance(b, Table):  # pragma: no cover - a body never ends with a table
            break
    raise NotFoundInDocument("document body has no paragraph to write after")


def _replace_all(find: str, replace: str, match_case: bool, doc: Document,
                 tab: Optional[str]) -> Dict[str, Any]:
    req: Dict[str, Any] = {"containsText": {"text": find, "matchCase": match_case},
                           "replaceText": replace}
    if tab:
        req["tabsCriteria"] = {"tabIds": [doc.tab(tab).id]}
    return {"replaceAllText": req}


def _occurrences(reply: Dict[str, Any]) -> int:
    return int((reply.get("replaceAllText") or {}).get("occurrencesChanged") or 0)


def _inline_spans(content: Content, key: str) -> Tuple[Any, ...]:
    paras = [b for b in content.blocks if isinstance(b, Para) and b.list is None]
    if len(paras) != len(content.blocks) or len(paras) > 1:
        raise ValueError(
            f"{{{{{key}}}}} shares its paragraph with other text or sits in a table cell, so it "
            "can only take inline content (one paragraph, no lists or tables). Put the "
            "placeholder on a line of its own in the body to insert blocks.")
    return paras[0].spans if paras else ()
