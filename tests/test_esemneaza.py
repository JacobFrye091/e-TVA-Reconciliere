import json

import pytest

from etva import esemneaza


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
    monkeypatch.setattr(esemneaza.urllib.request, "urlopen", _fake_urlopen)


def test_upload_document_sends_multipart_and_returns_filename(monkeypatch):
    captured = {}

    def handler(req):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["content_type"] = req.get_header("Content-type")
        captured["body"] = req.data
        return json.dumps({"fileName": "contract-1__abc.pdf"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = esemneaza.upload_document("key-123", b"%PDF-fake-bytes", "contract-1.pdf")

    assert result == "contract-1__abc.pdf"
    assert captured["url"] == "https://app.esemneaza.ro/api/v1/files"
    assert captured["auth"] == "Bearer key-123"
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="contract-1.pdf"' in captured["body"]
    assert b"%PDF-fake-bytes" in captured["body"]


def test_upload_document_raises_if_no_filename_returned(monkeypatch):
    _install_fake_urlopen(monkeypatch, lambda req: json.dumps({}).encode())
    with pytest.raises(esemneaza.EsemneazaError):
        esemneaza.upload_document("key-123", b"data", "x.pdf")


def test_create_sign_request_sends_expected_body(monkeypatch):
    captured = {}

    def handler(req):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        return json.dumps({"id": "req-1", "status": "IN_PROGRESS"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = esemneaza.create_sign_request(
        "key-123", "contract-1__abc.pdf",
        recipients=[{"email": "admin@exemplu.ro", "name": "Firma Exemplu SRL",
                    "field_page": 2}],
        sender_name="e-TVA Reconciliere")

    assert result == {"id": "req-1", "status": "IN_PROGRESS"}
    assert captured["url"] == "https://app.esemneaza.ro/api/v1/requests"
    assert captured["auth"] == "Bearer key-123"
    body = captured["body"]
    assert body["fileName"] == "contract-1__abc.pdf"
    assert body["senderName"] == "e-TVA Reconciliere"
    assert body["signInOrder"] is False
    recipient = body["recipients"][0]
    assert recipient["type"] == "EMAIL"
    assert recipient["email"] == "admin@exemplu.ro"
    assert recipient["name"] == "Firma Exemplu SRL"
    field = recipient["fields"][0]
    assert field["type"] == "SIGNATURE"
    assert field["pageNum"] == 2
    assert field["required"] is True


def test_create_sign_request_supports_multiple_recipients_with_custom_position(monkeypatch):
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.data)
        return json.dumps({"id": "req-2"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    esemneaza.create_sign_request(
        "key-123", "contract-2.pdf",
        recipients=[
            {"email": "a@exemplu.ro", "name": "A", "field_page": 1,
             "field_x": 60, "field_y": 40},
            {"email": "b@exemplu.ro", "name": "B", "field_page": 1},
        ],
        sign_in_order=True)

    recipients = captured["body"]["recipients"]
    assert len(recipients) == 2
    assert recipients[0]["fields"][0]["x"] == 60
    assert recipients[0]["fields"][0]["y"] == 40
    assert recipients[1]["fields"][0]["x"] == 300
    assert captured["body"]["signInOrder"] is True


def test_get_sign_request_returns_parsed_status(monkeypatch):
    def handler(req):
        assert req.full_url == "https://app.esemneaza.ro/api/v1/requests/req-1"
        return json.dumps({
            "id": "req-1", "status": "IN_PROGRESS",
            "recipients": [{"sigStatus": "APPLIED"}],
        }).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = esemneaza.get_sign_request("key-123", "req-1")
    assert result["recipients"][0]["sigStatus"] == esemneaza.SIGSTATUS_APPLIED


def test_cancel_sign_request_posts_to_cancel_endpoint(monkeypatch):
    def handler(req):
        assert req.full_url == "https://app.esemneaza.ro/api/v1/requests/req-1/cancel"
        assert req.get_method() == "POST"
        return json.dumps({"success": True}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = esemneaza.cancel_sign_request("key-123", "req-1")
    assert result == {"success": True}


def test_get_completed_document_url(monkeypatch):
    def handler(req):
        assert req.full_url == (
            "https://app.esemneaza.ro/api/v1/requests/req-1/completed_download_url")
        return json.dumps({"requestId": "req-1", "docUrl": "https://x/doc"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = esemneaza.get_completed_document_url("key-123", "req-1")
    assert result["docUrl"] == "https://x/doc"


def test_get_certificate_download_url(monkeypatch):
    def handler(req):
        assert req.full_url == (
            "https://app.esemneaza.ro/api/v1/requests/req-1/download_certificate_url")
        return json.dumps({"requestId": "req-1", "certificateUrl": "https://x/cert"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    result = esemneaza.get_certificate_download_url("key-123", "req-1")
    assert result["certificateUrl"] == "https://x/cert"


def test_fetch_url_bytes_returns_raw_body(monkeypatch):
    _install_fake_urlopen(monkeypatch, lambda req: b"raw-pdf-bytes")
    assert esemneaza.fetch_url_bytes("https://x/doc") == b"raw-pdf-bytes"


def test_http_error_raises_esemneaza_error(monkeypatch):
    import urllib.error

    def handler(req):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", None,
            __import__("io").BytesIO(b'{"message":"Missing authorization header"}'))

    _install_fake_urlopen(monkeypatch, handler)
    with pytest.raises(esemneaza.EsemneazaError, match="401"):
        esemneaza.get_sign_request("bad-key", "req-1")
