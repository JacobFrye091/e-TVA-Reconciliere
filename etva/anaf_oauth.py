"""ANAF OAuth2 client for the "decont precompletat" API.

Two ANAF services are involved (per the official OAuth registration guide
and mfinante's decont-precompletat spec):
  - logincert.anaf.ro - Identity Provider: authorize/token/refresh, where
    the FIRM authenticates with its own qualified digital certificate (not
    e-TVA Reconciliere's - each firm authorizes independently, and can
    revoke access at any time without our involvement).
  - api.anaf.ro - the protected decont/ws/v1/info endpoint, called with the
    access token obtained above.

This module never sees a firm's certificate - it only handles the OAuth2
mechanics (building the authorize redirect, exchanging/refreshing tokens,
calling the protected endpoint) using e-TVA Reconciliere's own
client_id/client_secret (from the ANAF developer registration) plus
whatever per-firm token is already on hand.

The decont endpoint's exact response shape is not yet confirmed against a
real live call (that needs a real firm's certificate to authorize, and a
server reachable at the registered Callback URL) - documented behaviour is
a ZIP archive containing JSON files, one of which is the precompleted
declaration itself (matching etva.importer.anaf_p300_json's expected
CIF/AN/LUNA/RD*_VAL/RD*_TVA shape). _extract_decont_json is written
defensively against that documentation; verify and adjust once a live
response is available.
"""
import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile

_AUTHORIZE_URL = "https://logincert.anaf.ro/anaf-oauth2/v1/authorize"
_TOKEN_URL = "https://logincert.anaf.ro/anaf-oauth2/v1/token"
_DECONT_URL = "https://api.anaf.ro/decont/ws/v1/info"
_TIMEOUT = 15


class AnafOAuthError(Exception):
    """The ANAF OAuth2 service could not be reached or returned an error."""


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """URL to send the firm's browser to - they authenticate there with
    their own qualified digital certificate; ANAF then redirects back to
    redirect_uri with a one-time ?code=... (and the same ?state=...)."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "token_content_type": "jwt",
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _token_request(client_id: str, client_secret: str, body: dict) -> dict:
    body = {**body, "token_content_type": "jwt"}
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL, data=data, method="POST",
        headers={
            "Authorization": _basic_auth_header(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise AnafOAuthError(
            f"ANAF a refuzat cererea de token ({exc.code}): "
            f"{exc.read().decode('utf-8', 'replace')}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AnafOAuthError(f"Serviciul ANAF nu a putut fi contactat: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnafOAuthError("Raspuns neasteptat de la ANAF la obtinerea tokenului.") from exc


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str,
                             redirect_uri: str) -> dict:
    """First-time exchange of the one-time authorization code for an
    access_token + refresh_token pair. Returns the raw ANAF response dict
    (expected keys: access_token, refresh_token, among others)."""
    return _token_request(client_id, client_secret, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    })


def refresh_access_token(client_id: str, client_secret: str,
                         refresh_token: str) -> dict:
    """Trade a still-valid refresh_token for a new access_token - ANAF
    rotates the refresh_token too on every use, so the new one must
    replace the old in storage."""
    return _token_request(client_id, client_secret, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


def _extract_decont_json(raw: bytes) -> dict:
    """The decont endpoint returns a ZIP with one or more JSON files inside;
    pick the one that actually looks like a precompleted declaration
    (has CIF/AN/LUNA) rather than assuming a fixed filename."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".json"):
                    continue
                try:
                    candidate = json.loads(zf.read(name))
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and {"CIF", "AN", "LUNA"} <= candidate.keys():
                    return candidate
        raise AnafOAuthError(
            "Arhiva primita de la ANAF nu contine decontul precompletat asteptat.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnafOAuthError("Raspuns neasteptat de la ANAF la cererea decontului.") from exc


def fetch_decont(access_token: str, cui: str, an: int, luna: int) -> dict:
    """Call the protected decont precompletat endpoint for one firm/period.
    Returns a dict shaped for etva.importer.anaf_p300_json.parse_p300_json_data."""
    params = urllib.parse.urlencode({"cui": cui, "an": an, "luna": luna})
    req = urllib.request.Request(
        f"{_DECONT_URL}?{params}",
        headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise AnafOAuthError(
            f"ANAF a refuzat cererea de decont ({exc.code}): "
            f"{exc.read().decode('utf-8', 'replace')}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AnafOAuthError(f"Serviciul ANAF nu a putut fi contactat: {exc}") from exc
    return _extract_decont_json(raw)
