"""The asyncio client. Same methods as :class:`DocsClient`, awaited."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TypeVar, Union

import httpx

from . import _ops
from ._ops import ContentLike, Op, WriteResult
from ._transport import USER_AGENT, Call, backoff, error, result, should_retry
from .client import CredentialsLike, as_credentials
from .content import as_content
from .errors import DocsError
from .models import Document

T = TypeVar("T")


class AsyncDocsClient:
    """Read and write Google Docs.

    >>> async with AsyncDocsClient(OAuthCredentials.load("token.json")) as docs:
    ...     doc = await docs.create("Weekly report", "# Hello")

    Every method mirrors :class:`DocsClient`; see there for details.
    """

    def __init__(self, credentials: CredentialsLike, *, http: Optional[httpx.AsyncClient] = None,
                 timeout: float = 60.0, max_retries: int = 5, backoff_base: float = 0.5
                 ) -> None:
        self.credentials = as_credentials(credentials)
        self._own = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    async def __aenter__(self) -> AsyncDocsClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._own:
            await self._http.aclose()

    # -- reading ----------------------------------------------------------------
    async def get(self, document: Any, *, tabs: bool = True) -> Document:
        """Fetch a document with all its tabs."""
        return await self._run(_ops.get(document, tabs=tabs))

    async def markdown(self, document: Any, *, tab: Optional[str] = None) -> str:
        """The document (or one tab) as Markdown."""
        doc = await self.get(document)
        return (doc.tab(tab) if tab else doc.tabs[0]).to_markdown()

    # -- creating -----------------------------------------------------------------
    async def create(self, title: str, content: Optional[ContentLike] = None) -> Document:
        """Create a document, optionally with content, and return it."""
        doc = await self._run(_ops.create(title))
        if content:
            await self.append(doc, content)
            doc = await self.get(doc)
        return doc

    async def copy(self, document: Any, title: str, *, folder_id: Optional[str] = None) -> Document:
        """Copy a document (a template, say) through Drive and return the copy."""
        return await self._run(_ops.copy(document, title, folder_id=folder_id))

    async def from_template(self, template: Any, title: str, values: Mapping[str, Any], *,
                      folder_id: Optional[str] = None) -> Document:
        """Copy ``template`` and fill its ``{{placeholders}}`` in one go."""
        doc = await self.copy(template, title, folder_id=folder_id)
        await self.fill(doc, values)
        return await self.get(doc)

    # -- writing ------------------------------------------------------------------
    async def append(self, document: Any, content: ContentLike, *,
               tab: Optional[str] = None) -> WriteResult:
        """Add content at the end of the document."""
        return await self._run(_ops.append(document, as_content(content), tab=tab))

    async def prepend(self, document: Any, content: ContentLike, *,
                tab: Optional[str] = None) -> WriteResult:
        """Add content at the very top of the document."""
        return await self._run(_ops.prepend(document, as_content(content), tab=tab))

    async def insert_after(self, document: Any, heading: str, content: ContentLike, *,
                     tab: Optional[str] = None) -> WriteResult:
        """Add content right under the heading with this text."""
        return await self._run(_ops.insert_after(document, heading, as_content(content), tab=tab))

    async def replace_section(self, document: Any, heading: str, content: ContentLike, *,
                        tab: Optional[str] = None) -> WriteResult:
        """Replace everything under a heading (up to the next heading of the same or a
        higher level) and keep the heading itself."""
        return await self._run(
            _ops.replace_section(document, heading, as_content(content), tab=tab))

    async def fill(self, document: Any, values: Mapping[str, Any], *, tab: Optional[str] = None,
             open: str = "{{", close: str = "}}") -> WriteResult:
        """Fill ``{{placeholders}}``. Plain values replace text everywhere; a
        :class:`Content` value puts rich content (lists, tables...) where the placeholder
        was. ``result.counts`` tells you how many of each were replaced."""
        return await self._run(_ops.fill(document, values, tab=tab, open=open, close=close))

    async def replace_text(self, document: Any, find: str, replace: str, *, match_case: bool = True,
                     tab: Optional[str] = None) -> int:
        """Replace every occurrence of ``find``; returns how many were replaced."""
        return await self._run(_ops.replace_text(document, find, replace, match_case=match_case,
                                           tab=tab))

    async def batch_update(self, document: Any, requests: List[Dict[str, Any]], *,
                     revision_id: Optional[str] = None) -> WriteResult:
        """Send raw Docs API requests, for anything the helpers don't cover."""
        return await self._run(_ops.batch_update(document, requests, revision_id))

    # -- drive ----------------------------------------------------------------------
    async def export(self, document: Any, format: str = "pdf",
               path: Optional[Union[str, Path]] = None) -> bytes:
        """Export as ``pdf``, ``docx``, ``odt``, ``rtf``, ``txt``, ``md``, ``html`` (zip) or
        ``epub``. With ``path`` the file is also written there."""
        data = await self._run(_ops.export(document, format))
        if path is not None:
            Path(path).write_bytes(data)
        return data

    async def trash(self, document: Any) -> None:
        """Move the document to the Drive trash."""
        await self._run(_ops.trash(document))

    # -- engine ---------------------------------------------------------------------
    async def _run(self, op: Op[T]) -> T:
        value: Any = None
        exc: Optional[BaseException] = None
        while True:
            try:
                call = op.throw(exc) if exc is not None else op.send(value)
            except StopIteration as stop:
                return stop.value  # type: ignore[no-any-return]
            value, exc = None, None
            try:
                value = await self._send(call)
            except DocsError as e:
                exc = e

    async def _send(self, call: Call) -> Any:
        refreshed = False
        attempt = 0
        while True:
            token = await self.credentials.atoken(self._http)
            headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
            try:
                resp = await self._http.request(call.method, call.url, params=call.params,
                                          json=call.json, headers=headers)
            except httpx.TransportError:
                if not call.idempotent or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(backoff(attempt, self.backoff_base, None))
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
                await asyncio.sleep(backoff(attempt, self.backoff_base, retry_after))
                attempt += 1
                continue
            raise error(resp)
