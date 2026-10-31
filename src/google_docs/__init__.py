"""Google Docs for Python: read documents as Markdown, write them from Markdown.

>>> from google_docs import DocsClient, OAuthCredentials
>>> docs = DocsClient(OAuthCredentials.load("token.json"))
>>> doc = docs.create("Hello", "# Hello\\n\\nWritten from **Python**.")
>>> print(docs.get(doc).to_markdown())
"""

from ._ops import EXPORT_FORMATS, WriteResult
from ._version import __version__
from .async_client import AsyncDocsClient
from .auth import (
    DEFAULT_SCOPES,
    SCOPE_DOCUMENTS,
    SCOPE_DOCUMENTS_READONLY,
    SCOPE_DRIVE,
    SCOPE_DRIVE_FILE,
    Credentials,
    GoogleAuthCredentials,
    OAuthCredentials,
    OAuthFlow,
    StaticToken,
    load_client_secrets,
)
from .client import DocsClient
from .content import Content, Span, bold, code, italic, link, md, strike, underline
from .errors import (
    APIError,
    AuthError,
    DocsError,
    InvalidArgument,
    NotFound,
    NotFoundInDocument,
    PermissionDenied,
    RateLimited,
    RevisionConflict,
    ServerError,
    Unauthenticated,
)
from .models import (
    Body,
    Cell,
    Chip,
    Document,
    InlineImage,
    Paragraph,
    Tab,
    Table,
    TextRun,
    utf16_len,
)

__all__ = [
    "APIError", "AsyncDocsClient", "AuthError", "Body", "Cell", "Chip", "Content",
    "Credentials", "DEFAULT_SCOPES", "DocsClient", "DocsError", "Document", "EXPORT_FORMATS",
    "GoogleAuthCredentials", "InlineImage", "InvalidArgument", "NotFound",
    "NotFoundInDocument", "OAuthCredentials", "OAuthFlow", "Paragraph", "PermissionDenied",
    "RateLimited", "RevisionConflict", "SCOPE_DOCUMENTS", "SCOPE_DOCUMENTS_READONLY",
    "SCOPE_DRIVE", "SCOPE_DRIVE_FILE", "ServerError", "Span", "StaticToken", "Tab", "Table",
    "TextRun", "Unauthenticated", "WriteResult", "__version__", "bold", "code", "italic",
    "link", "load_client_secrets", "md", "strike", "underline", "utf16_len",
]
