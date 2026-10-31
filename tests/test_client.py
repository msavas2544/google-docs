import httpx
import pytest

from google_docs import (
    AsyncDocsClient,
    DocsClient,
    NotFound,
    PermissionDenied,
    RateLimited,
    StaticToken,
)
from google_docs._transport import document_id
from google_docs.testing import FakeDocs


def test_document_id_from_anything():
    raw = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert document_id(raw) == raw
    assert document_id(f"https://docs.google.com/document/d/{raw}/edit?tab=t.0#heading=h") == raw
    assert document_id(f"https://docs.google.com/document/u/1/d/{raw}/") == raw
    with pytest.raises(ValueError):
        document_id("https://example.com/nope")


def test_retries_503_then_succeeds(fake, docs):
    doc = docs.create("Doc")
    fake.fail_next(503, times=2)
    assert docs.get(doc).id == doc.id
    assert len(fake.requests) == 4  # create, 503, 503, ok


def test_rate_limit_respects_retry_after_and_gives_up(fake):
    with fake.client(max_retries=1) as docs:
        doc = docs.create("Doc")
        fake.fail_next(429, times=2, headers={"Retry-After": "0"})
        with pytest.raises(RateLimited):
            docs.get(doc)


def test_batch_update_is_not_blindly_retried_on_500(fake, docs):
    doc = docs.create("Doc")
    fake.fail_next(500)
    with pytest.raises(Exception, match="500"):
        docs.batch_update(doc, [{"insertText": {"text": "x", "location": {"index": 1}}}])
    assert docs.get(doc).text == ""


def test_401_refreshes_once():
    calls = []

    class Creds(StaticToken):
        def invalidate(self, failed_token=None):
            calls.append(failed_token)
            self.access_token = "fresh"

    def handler(request):
        if request.headers["Authorization"] == "Bearer stale":
            return httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED",
                                                       "message": "expired"}})
        return httpx.Response(200, json={"documentId": "d" * 20, "title": "ok"})

    client = DocsClient(Creds("stale"), http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.get("d" * 20).title == "ok"
    assert calls == ["stale"]


def test_errors_are_typed(fake, docs):
    with pytest.raises(NotFound):
        docs.get("x" * 25)
    fake.fail_next(403, body={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                        "message": "The caller does not have permission"}})
    with pytest.raises(PermissionDenied, match="permission"):
        docs.get("x" * 25)


def test_invalid_raw_request_is_reported(docs):
    doc = docs.create("Doc")
    with pytest.raises(Exception, match="inside the bounds of an existing paragraph"):
        docs.batch_update(doc, [{"insertText": {"text": "x", "location": {"index": 0}}}])


def test_copy_from_template_and_export(tmp_path, docs):
    template = docs.create("Template", "Hello {{name}}!")
    doc = docs.from_template(template, "For Ada", {"name": "Ada"})
    assert doc.title == "For Ada" and doc.text == "Hello Ada!"
    assert docs.get(template).text == "Hello {{name}}!"
    out = tmp_path / "x.txt"
    assert docs.export(doc, "txt", out) == b"Hello Ada!"
    assert out.read_bytes() == b"Hello Ada!"
    assert docs.export(doc, "md") == b"Hello Ada!\n"
    with pytest.raises(ValueError):
        docs.export(doc, "bmp")


def test_trash(docs):
    doc = docs.create("Doc")
    docs.trash(doc)
    with pytest.raises(NotFound):
        docs.get(doc)


def test_accepts_google_auth_style_credentials():
    class GoogleCreds:
        token, valid = "g-token", True

        def refresh(self, request):  # pragma: no cover - never called while valid
            raise AssertionError

    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"documentId": "d" * 20})

    DocsClient(GoogleCreds(), http=httpx.Client(transport=httpx.MockTransport(handler))).get(
        "d" * 20)
    assert seen == ["Bearer g-token"]


async def test_async_client_matches_sync():
    fake = FakeDocs()
    async with fake.async_client() as docs:
        assert isinstance(docs, AsyncDocsClient)
        doc = await docs.create("Async", "# Hi\n\n- a\n- b")
        await docs.append(doc, "| x |\n|---|\n| 1 |")
        await docs.insert_after(doc, "Hi", "Intro")
        await docs.replace_section(doc, "Hi", "Replaced")
        result = await docs.fill(doc, {"nothing": "x"})
        assert result.counts == {"nothing": 0}
        assert await docs.replace_text(doc, "Replaced", "Done") == 1
        assert await docs.markdown(doc) == "# Hi\n\nDone\n"
        fake.fail_next(503)
        assert (await docs.get(doc)).title == "Async"
        assert await docs.export(doc, "txt") == b"Hi\nDone"
