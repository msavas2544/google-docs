"""The synchronous client."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TypeVar, Union

import httpx

from . import _ops
from ._ops import ContentLike, Op, WriteResult
from ._transport import USER_AGENT, Call, backoff, error, result, should_retry
from .auth import Credentials, GoogleAuthCredentials, StaticToken
from .content import as_content
from .errors import DocsError
from .models import Document

T = TypeVar("T")
CredentialsLike = Union[Credentials, str, Any]


def as_credentials(creds: CredentialsLike) -> Credentials:
    """Accept our credentials, a raw access token, or a ``google.auth`` credentials object."""
    if isinstance(creds, Credentials):
        return creds
    if isinstance(creds, str):
        return StaticToken(creds)
    if hasattr(creds, "refresh") and hasattr(creds, "token"):
        return GoogleAuthCredentials(creds)
    raise TypeError(f"unsupported credentials: {type(creds).__name__}")


class DocsClient:
    """Read and write Google Docs.

    >>> docs = DocsClient(OAuthCredentials.load("token.json"))
    >>> doc = docs.create("Weekly report", "# Hello\\n\\nWritten from **Python**.")
    >>> print(docs.get(doc).to_markdown())

    Anywhere a document is expected you can pass its ID, its URL, or a
    :class:`Document`. ``content`` is Markdown or a :class:`Content`.
    """

    def __init__(self, credentials: CredentialsLike, *, http: Optional[httpx.Client] = None,
                 timeout: float = 60.0, max_retries: int = 5, backoff_base: float = 0.5
                 ) -> None:
        self.credentials = as_credentials(credentials)
        self._own = http is None
        self._http = http or httpx.Client(timeout=timeout)
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def __enter__(self) -> DocsClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._own:
            self._http.close()

    # -- reading ----------------------------------------------------------------
    def get(self, document: Any, *, tabs: bool = True) -> Document:
        """Fetch a document with all its tabs."""
        return self._run(_ops.get(document, tabs=tabs))

    def markdown(self, document: Any, *, tab: Optional[str] = None) -> str:
        """The document (or one tab) as Markdown."""
        doc = self.get(document)
        return (doc.tab(tab) if tab else doc.tabs[0]).to_markdown()

    # -- creating -----------------------------------------------------------------
    def create(self, title: str, content: Optional[ContentLike] = None) -> Document:
        """Create a document, optionally with content, and return it."""
        doc = self._run(_ops.create(title))
        if content:
            self.append(doc, content)
            doc = self.get(doc)
        return doc

    def copy(self, document: Any, title: str, *, folder_id: Optional[str] = None) -> Document:
        """Copy a document (a template, say) through Drive and return the copy."""
        return self._run(_ops.copy(document, title, folder_id=folder_id))

    def from_template(self, template: Any, title: str, values: Mapping[str, Any], *,
                      folder_id: Optional[str] = None) -> Document:
        """Copy ``template`` and fill its ``{{placeholders}}`` in one go."""
        doc = self.copy(template, title, folder_id=folder_id)
        self.fill(doc, values)
        return self.get(doc)

    # -- writing ------------------------------------------------------------------
    def append(self, document: Any, content: ContentLike, *,
               tab: Optional[str] = None) -> WriteResult:
        """Add content at the end of the document."""
        return self._run(_ops.append(document, as_content(content), tab=tab))

    def prepend(self, document: Any, content: ContentLike, *,
                tab: Optional[str] = None) -> WriteResult:
        """Add content at the very top of the document."""
        return self._run(_ops.prepend(document, as_content(content), tab=tab))

    def insert_after(self, document: Any, heading: str, content: ContentLike, *,
                     tab: Optional[str] = None) -> WriteResult:
        """Add content right under the heading with this text."""
        return self._run(_ops.insert_after(document, heading, as_content(content), tab=tab))

    def replace_section(self, document: Any, heading: str, content: ContentLike, *,
                        tab: Optional[str] = None) -> WriteResult:
        """Replace everything under a heading (up to the next heading of the same or a
        higher level) and keep the heading itself."""
        return self._run(_ops.replace_section(document, heading, as_content(content), tab=tab))

    def fill(self, document: Any, values: Mapping[str, Any], *, tab: Optional[str] = None,
             open: str = "{{", close: str = "}}") -> WriteResult:
        """Fill ``{{placeholders}}``. Plain values replace text everywhere; a
        :class:`Content` value puts rich content (lists, tables...) where the placeholder
        was. ``result.counts`` tells you how many of each were replaced."""
        return self._run(_ops.fill(document, values, tab=tab, open=open, close=close))

    def replace_text(self, document: Any, find: str, replace: str, *, match_case: bool = True,
                     tab: Optional[str] = None) -> int:
        """Replace every occurrence of ``find``; returns how many were replaced."""
        return self._run(_ops.replace_text(document, find, replace, match_case=match_case,
                                           tab=tab))

    def batch_update(self, document: Any, requests: List[Dict[str, Any]], *,
                     revision_id: Optional[str] = None) -> WriteResult:
        """Send raw Docs API requests, for anything the helpers don't cover."""
        return self._run(_ops.batch_update(document, requests, revision_id))

    # -- drive ----------------------------------------------------------------------
    def export(self, document: Any, format: str = "pdf",
               path: Optional[Union[str, Path]] = None) -> bytes:
        """Export as ``pdf``, ``docx``, ``odt``, ``rtf``, ``txt``, ``md``, ``html`` (zip) or
        ``epub``. With ``path`` the file is also written there."""
        data = self._run(_ops.export(document, format))
        if path is not None:
            Path(path).write_bytes(data)
        return data

    def trash(self, document: Any) -> None:
        """Move the document to the Drive trash."""
        self._run(_ops.trash(document))

    # -- engine ---------------------------------------------------------------------
    def _run(self, op: Op[T]) -> T:
        value: Any = None
        exc: Optional[BaseException] = None
        while True:
            try:
                call = op.throw(exc) if exc is not None else op.send(value)
            except StopIteration as stop:
                return stop.value  # type: ignore[no-any-return]
            value, exc = None, None
            try:
                value = self._send(call)
            except DocsError as e:
                exc = e

    def _send(self, call: Call) -> Any:
        refreshed = False
        attempt = 0
        while True:
            token = self.credentials.token(self._http)
            headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
            try:
                resp = self._http.request(call.method, call.url, params=call.params,
                                          json=call.json, headers=headers)
            except httpx.TransportError:
                if not call.idempotent or attempt >= self.max_retries:
                    raise
                time.sleep(backoff(attempt, self.backoff_base, None))
                attempt += 1
                continue
            if resp.status_code == 401 and not refreshed:
                self.credentials.invalidate(token)
                refreshed = True
                continue
            if resp.is_success:
                return result(call, resp)
            if should_retry(call, resp.status_code, attempt, self.max_retries):
                retry_after = resp.headers.get("Retry-After")
                time.sleep(backoff(attempt, self.backoff_base, retry_after))
                attempt += 1
                continue
            raise error(resp)
