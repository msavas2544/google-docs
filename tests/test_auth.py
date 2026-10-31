import json
import time
import urllib.parse

import httpx
import pytest

from google_docs import (
    DEFAULT_SCOPES,
    AuthError,
    OAuthCredentials,
    OAuthFlow,
    load_client_secrets,
)


def test_pkce_url_and_exchange():
    flow = OAuthFlow("cid", "sec", DEFAULT_SCOPES, redirect_uri="https://app/cb")
    req = flow.authorization_url()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.url).query))
    assert q["scope"] == " ".join(DEFAULT_SCOPES) and q["code_challenge_method"] == "S256"

    def token(r):
        form = dict(urllib.parse.parse_qsl(r.content.decode()))
        assert form["code_verifier"] == req.code_verifier
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r",
                                         "expires_in": 3600})

    creds = flow.exchange_code("c", req.code_verifier,
                               http=httpx.Client(transport=httpx.MockTransport(token)))
    assert creds.access_token == "a" and not creds.expired


def test_refresh_and_persist(tmp_path):
    p = tmp_path / "t.json"
    OAuthCredentials(access_token="old", refresh_token="r", client_id="c",
                     expires_at=time.time() - 1).save(p)
    creds = OAuthCredentials.load(p)
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"access_token": "new", "expires_in": 3600})))
    assert creds.token(http) == "new"
    assert json.loads(p.read_text())["access_token"] == "new"


def test_refresh_failure_is_an_auth_error():
    creds = OAuthCredentials(refresh_token="r", client_id="c")
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": "invalid_grant"})))
    with pytest.raises(AuthError, match="invalid_grant"):
        creds.token(http)


def test_client_secrets_file(tmp_path):
    p = tmp_path / "client_secret_x.json"
    p.write_text(json.dumps({"installed": {"client_id": "id.apps", "client_secret": "s"}}))
    assert load_client_secrets(p) == ("id.apps", "s")
