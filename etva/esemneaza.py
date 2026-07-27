"""eSemneaza.ro client - electronic signature provider used for the service
contract between VML and each subscribing firm, replacing the old
mouse-drawn "simple electronic signature" (legally weak - explicit feedback
was that letting a user sign with whatever they draw isn't acceptable, see
portal/app.py::semneaza_contract).

Mirrors etva/anaf_oauth.py's conventions: stdlib urllib only (no requests
dependency in this project), one Error class, functions return parsed
dicts, raise EsemneazaError on transport/HTTP failure.

Everything below was verified against the real live API (2026-07-27, using
a real account) rather than guessed, because the reference docs
(esemneaza.stoplight.io) do not document two things anywhere:
  - The auth header format - confirmed empirically:
    `Authorization: Bearer <api_key>` (a request with no header returns
    401 "Missing authorization header"; the same request with this header
    returns 200).
  - The webhook payload shape - genuinely still unknown, no Webhooks
    reference page exists in the docs at all. portal/app.py's webhook
    route is written defensively against this (tries a couple of
    plausible key names, never crashes on an unexpected shape) - the
    primary, load-bearing path is polling get_sign_request from
    portal/app.py::_verifica_finalizare_esemneaza on every contract page
    view, not the webhook.

Also confirmed empirically: for EMAIL recipients, the create/retrieve
responses' `signUrl` field is always an empty string - eSemneaza sends its
own notification email directly to the recipient with the signing link, it
is not something our app can read back and redirect to. A recipient
without at least one SIGNATURE field placed on the document also never
gets a usable link at all, so create_sign_request always requires a page
number to place one.
"""
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid

_BASE_URL = "https://app.esemneaza.ro/api/v1"
_TIMEOUT = 20

# Valorile documentate pentru recipients[].sigStatus (vezi Retrieve a Sign
# Request) - APPLIED inseamna semnat efectiv, REJECTED inseamna refuzat.
SIGSTATUS_PENDING = "PENDING"
SIGSTATUS_RECEIVED = "RECEIVED"
SIGSTATUS_APPLIED = "APPLIED"
SIGSTATUS_REJECTED = "REJECTED"


class EsemneazaError(Exception):
    """eSemneaza.ro nu a putut fi contactat sau a raspuns cu o eroare."""


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _call(req: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise EsemneazaError(
            f"eSemneaza a refuzat cererea ({exc.code}): "
            f"{exc.read().decode('utf-8', 'replace')}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EsemneazaError(f"Serviciul eSemneaza nu a putut fi contactat: {exc}") from exc


def _json(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EsemneazaError("Raspuns neasteptat de la eSemneaza.") from exc


def upload_document(api_key: str, pdf_bytes: bytes, filename: str) -> str:
    """Incarca documentul PDF (max 15MB per docs) si intoarce fileName-ul
    unic ce trebuie folosit imediat dupa la create_sign_request - fiecare
    fileName poate fi folosit o singura data intr-o cerere de semnare
    (confirmat empiric: a doua incercare de a-l refolosi da HTTP 400 "File
    has already been used for another request")."""
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(filename)[0] or "application/pdf"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + pdf_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{_BASE_URL}/files", data=body, method="POST",
        headers={**_auth_headers(api_key),
                "Content-Type": f"multipart/form-data; boundary={boundary}"})
    result = _json(_call(req))
    file_name = result.get("fileName")
    if not file_name:
        raise EsemneazaError("eSemneaza nu a returnat un fileName dupa incarcare.")
    return file_name


def create_sign_request(api_key: str, file_name: str, recipients: "list[dict]",
                        sender_name: "str | None" = None,
                        sign_in_order: bool = False) -> dict:
    """Creeaza cererea de semnare. `recipients` e o lista (desi contractul
    acestei platforme are in practica un singur semnatar real prin
    eSemneaza - BENEFICIARUL/firma client; semnatura PRESTATORULUI (VML) e
    deja inclusa ca text in contract la generare, vezi
    contract.genereaza_text, nu trece prin eSemneaza) - lasata generica in
    caz ca apare vreodata nevoia unui al doilea semnatar real.

    Fiecare element din `recipients` e un dict cu email/name/field_page si
    optional field_x/field_y (implicit colt jos-dreapta). Un camp de
    semnatura e obligatoriu ca destinatarul sa primeasca un link functional
    (confirmat empiric: fara niciun camp, sigUrl ramane gol si nu se
    intampla nimic util)."""
    body_recipients = []
    for r in recipients:
        body_recipients.append({
            "type": "EMAIL",
            "email": r["email"],
            "name": r["name"],
            "fields": [{
                "x": r.get("field_x", 300), "y": r.get("field_y", 60),
                "width": 180, "height": 50, "pageNum": r["field_page"],
                "type": "SIGNATURE", "required": True,
            }],
        })
    body = {"fileName": file_name, "recipients": body_recipients,
           "signInOrder": sign_in_order}
    if sender_name:
        body["senderName"] = sender_name
    req = urllib.request.Request(
        f"{_BASE_URL}/requests", data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={**_auth_headers(api_key), "Content-Type": "application/json"})
    return _json(_call(req))


def get_sign_request(api_key: str, request_id: str) -> dict:
    """Starea curenta a cererii - recipients[0]['sigStatus'] e sursa de
    adevar pentru a sti daca s-a semnat (APPLIED) sau s-a refuzat
    (REJECTED). Apelat manual la fiecare vizualizare a paginii de contract
    (vezi portal/app.py::_verifica_finalizare_esemneaza) cat timp serverul
    nu are inca o adresa publica pentru webhook."""
    req = urllib.request.Request(
        f"{_BASE_URL}/requests/{request_id}", headers=_auth_headers(api_key))
    return _json(_call(req))


def cancel_sign_request(api_key: str, request_id: str) -> dict:
    req = urllib.request.Request(
        f"{_BASE_URL}/requests/{request_id}/cancel", data=b"", method="POST",
        headers={**_auth_headers(api_key), "Content-Type": "application/json"})
    return _json(_call(req))


def get_completed_document_url(api_key: str, request_id: str) -> dict:
    """{'requestId', 'docUrl'} - docUrl e valabil doar 2 ore de la generare
    (per docs), asa ca trebuie descarcat imediat (vezi fetch_url_bytes), nu
    pastrat ca link. 403 daca cererea nu s-a incheiat inca."""
    req = urllib.request.Request(
        f"{_BASE_URL}/requests/{request_id}/completed_download_url",
        headers=_auth_headers(api_key))
    return _json(_call(req))


def get_certificate_download_url(api_key: str, request_id: str) -> dict:
    """{'requestId', 'certificateUrl'} - valabil doar 15 minute de la
    generare (per docs) - la fel, descarca imediat."""
    req = urllib.request.Request(
        f"{_BASE_URL}/requests/{request_id}/download_certificate_url",
        headers=_auth_headers(api_key))
    return _json(_call(req))


def fetch_url_bytes(url: str) -> bytes:
    """Descarca continutul unui docUrl/certificateUrl temporar - acestea nu
    cer header-ul de autorizare (sunt deja semnate/tokenizate de eSemneaza
    in link-ul insusi)."""
    req = urllib.request.Request(url)
    return _call(req)
