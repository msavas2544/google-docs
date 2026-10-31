"""Exceptions raised by google-docs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class DocsError(Exception):
    """Base class for every error raised by this library."""


class AuthError(DocsError):
    """OAuth token acquisition or refresh failed."""


class APIError(DocsError):
    """The API answered with a non-2xx response."""

    def __init__(self, message: str, *, http_status: int, status: Optional[str] = None,
                 details: Optional[List[Dict[str, Any]]] = None, response_body: Any = None
                 ) -> None:
        super().__init__(f"{http_status} {status or ''}: {message}".replace("  ", " "))
        self.message = message
        self.http_status = http_status
        self.status = status
        self.details = details or []
        self.response_body = response_body


class InvalidArgument(APIError):
    """The request was rejected, usually because an index or range is out of bounds."""


class RevisionConflict(InvalidArgument):
    """Someone else changed the document between our read and our write.

    High-level helpers catch this, re-read the document and try once more, so you
    only see it when the document keeps changing under you."""


class Unauthenticated(APIError):
    pass


class PermissionDenied(APIError):
    """Missing scope, or the account cannot open this document."""


class NotFound(APIError):
    """No document with that ID (or it is not shared with this account)."""


class RateLimited(APIError):
    """Quota exceeded. Requests are retried with backoff before this is raised."""


class ServerError(APIError):
    pass


class NotFoundInDocument(DocsError, LookupError):
    """A placeholder, heading or tab you referred to is not in the document."""


_BY_STATUS = {
    "INVALID_ARGUMENT": InvalidArgument,
    "UNAUTHENTICATED": Unauthenticated,
    "PERMISSION_DENIED": PermissionDenied,
    "NOT_FOUND": NotFound,
    "RESOURCE_EXHAUSTED": RateLimited,
}
_BY_HTTP = {400: InvalidArgument, 401: Unauthenticated, 403: PermissionDenied, 404: NotFound,
            429: RateLimited}


def from_response(http_status: int, body: Any) -> APIError:
    status = message = None
    details: List[Dict[str, Any]] = []
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        status, message, details = err.get("status"), err.get("message"), err.get("details") or []
    if message is None:
        message = body if isinstance(body, str) and body else "request failed"
    cls = _BY_STATUS.get(status or "") or _BY_HTTP.get(http_status)
    if cls is None:
        cls = ServerError if http_status >= 500 else APIError
    if cls is InvalidArgument and "revision" in str(message).lower():
        cls = RevisionConflict
    return cls(str(message), http_status=http_status, status=status, details=details,
               response_body=body)
