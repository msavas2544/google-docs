"""HTTP plumbing shared by the sync and async clients."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from ._version import __version__
from .errors import from_response

DOCS_API = "https://docs.googleapis.com/v1/"
DRIVE_API = "https://www.googleapis.com/drive/v3/"
USER_AGENT = f"google-docs-python/{__version__}"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

_DOC_URL = re.compile(r"/document/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)")
_DOC_ID = re.compile(r"^[a-zA-Z0-9_-]{10,}$")


@dataclass
class Call:
    """One HTTP request, described without sending it."""

    method: str
    url: str
    params: Optional[Dict[str, Any]] = None
    json: Any = None
    raw: bool = False  # return bytes instead of parsed JSON
    idempotent: bool = True


def document_id(ref: Any) -> str:
    """Accept a document ID, a docs.google.com URL, or anything with an ``.id``."""
    if hasattr(ref, "id") and isinstance(ref.id, str):
        return ref.id
    s = str(ref).strip()
    m = _DOC_URL.search(s)
    if m:
        return m.group(1)
    if _DOC_ID.match(s):
        return s
    raise ValueError(f"not a Google Docs document ID or URL: {ref!r}")


def backoff(attempt: int, base: float, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    return min(base * (2 ** attempt), 30.0) * (0.5 + random.random() / 2)


def should_retry(call: Call, status: int, attempt: int, max_retries: int) -> bool:
    if attempt >= max_retries or status not in RETRY_STATUSES:
        return False
    # batchUpdate is atomic: a 429/503 means nothing was applied, so it is safe to resend.
    return call.idempotent or status in (429, 503)


def error(resp: httpx.Response) -> Exception:
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    return from_response(resp.status_code, body)


def result(call: Call, resp: httpx.Response) -> Any:
    if call.raw:
        return resp.content
    if not resp.content:
        return {}
    return resp.json()
