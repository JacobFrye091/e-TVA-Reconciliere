import io
import json
import zipfile
from urllib.error import HTTPError, URLError

import pytest

from etva import anaf_oauth


def test_build_authorize_url_has_expected_params():
    url = anaf_oauth.build_authorize_url(
        "client-123", "https://ereconciliere.ro/api/anaf/callback", "firma-9")
    assert url.startswith("https://logincert.anaf.ro/anaf-oauth2/v1/authorize?")
    assert "response_type=code" in url
    assert "client_id=client-123" in url
    assert "token_content_type=jwt" in url
    assert "state=firma-9" in url
    assert "redirect_uri=https%3A%2F%2Fereconciliere.ro%2Fapi%2Fanaf%2Fcallback" in url


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _install_fake_urlopen(monkeypatch, handler):
    """handler(request) -> bytes, or raises HTTPError/URLError."""
    def _fake_urlopen(req, timeout=None):
        return _FakeResponse(handler(req))
    monkeypatch.setattr(anaf_oauth.urllib.request, "urlopen", _fake_urlopen)


def test_exchange_code_for_tokens_sends_basic_auth_and_grant_type(monkeypatch):
    captured = {}

    def handler(req):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = req.data.decode("utf-8")
        return json.dumps({"access_token": "AAA", "refresh_token": "BBB"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = anaf_oauth.exchange_code_for_tokens(
        "client-123", "secret-xyz", "one-time-code",
        "https://ereconciliere.ro/api/anaf/callback")

    assert result == {"access_token": "AAA", "refresh_token": "BBB"}
    assert captured["url"] == anaf_oauth._TOKEN_URL
    assert captured["auth"].startswith("Basic ")
    assert "grant_type=authorization_code" in captured["body"]
    assert "code=one-time-code" in captured["body"]
    assert "token_content_type=jwt" in captured["body"]


def test_refresh_access_token_sends_refresh_grant(monkeypatch):
    captured = {}

    def handler(req):
        captured["body"] = req.data.decode("utf-8")
        return json.dumps({"access_token": "NEW", "refresh_token": "NEW2"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = anaf_oauth.refresh_access_token("client-123", "secret-xyz", "old-refresh")

    assert result["access_token"] == "NEW"
    assert "grant_type=refresh_token" in captured["body"]
    assert "refresh_token=old-refresh" in captured["body"]


def test_token_request_raises_on_http_error(monkeypatch):
    def handler(req):
        raise HTTPError(anaf_oauth._TOKEN_URL, 400, "Bad Request",
                        hdrs=None, fp=io.BytesIO(b'{"error":"invalid_grant"}'))

    monkeypatch.setattr(anaf_oauth.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(handler(req)))
    with pytest.raises(anaf_oauth.AnafOAuthError):
        anaf_oauth.exchange_code_for_tokens(
            "c", "s", "bad-code", "https://ereconciliere.ro/api/anaf/callback")


def test_token_request_raises_on_connection_error(monkeypatch):
    def _fake_urlopen(req, timeout=None):
        raise URLError("no route to host")
    monkeypatch.setattr(anaf_oauth.urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(anaf_oauth.AnafOAuthError):
        anaf_oauth.refresh_access_token("c", "s", "r")


def _zip_of(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_decont_json_from_plain_json():
    raw = json.dumps({"CIF": "12345678", "AN": 2026, "LUNA": 6}).encode()
    assert anaf_oauth._extract_decont_json(raw) == {
        "CIF": "12345678", "AN": 2026, "LUNA": 6}


def test_extract_decont_json_from_zip_picks_the_declaration_file():
    raw = _zip_of({
        "meta.json": json.dumps({"stare": "ok"}),
        "decont.json": json.dumps({"CIF": "12345678", "AN": 2026, "LUNA": 6,
                                   "RD9_VAL": 100.0}),
    })
    result = anaf_oauth._extract_decont_json(raw)
    assert result["CIF"] == "12345678" and result["RD9_VAL"] == 100.0


def test_extract_decont_json_rejects_zip_without_declaration():
    raw = _zip_of({"meta.json": json.dumps({"stare": "ok"})})
    with pytest.raises(anaf_oauth.AnafOAuthError):
        anaf_oauth._extract_decont_json(raw)


def test_extract_decont_json_rejects_garbage():
    with pytest.raises(anaf_oauth.AnafOAuthError):
        anaf_oauth._extract_decont_json(b"not json, not a zip")


def test_fetch_decont_sends_bearer_token_and_parses_zip(monkeypatch):
    captured = {}

    def handler(req):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _zip_of({"decont.json": json.dumps(
            {"CIF": "12345678", "AN": 2026, "LUNA": 6, "RD9_VAL": 50.0})})

    _install_fake_urlopen(monkeypatch, handler)
    result = anaf_oauth.fetch_decont("token-abc", "RO12345678", 2026, 6)

    assert result["RD9_VAL"] == 50.0
    assert captured["auth"] == "Bearer token-abc"
    assert captured["url"].startswith(anaf_oauth._DECONT_URL)
    assert "cui=RO12345678" in captured["url"]
    assert "an=2026" in captured["url"]
    assert "luna=6" in captured["url"]


def test_fetch_decont_raises_on_http_error(monkeypatch):
    def _fake_urlopen(req, timeout=None):
        raise HTTPError(anaf_oauth._DECONT_URL, 403, "Forbidden",
                        hdrs=None, fp=io.BytesIO(b"acces neautorizat"))
    monkeypatch.setattr(anaf_oauth.urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(anaf_oauth.AnafOAuthError):
        anaf_oauth.fetch_decont("token-abc", "RO12345678", 2026, 6)
