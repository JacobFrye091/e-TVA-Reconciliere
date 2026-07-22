"""CUI/CIF verification via ANAF's public PlatitorTvaRest v9 web service.

Endpoint confirmed live: POST https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva
The service returns HTTP 404 even on a well-formed "not found" response, so
callers must always parse the JSON body rather than trust the status code.
"""
import datetime
import json
import re
import urllib.error
import urllib.request

_ANAF_URL = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
_TIMEOUT = 8


class AnafCuiError(Exception):
    """The ANAF verification service could not be reached or returned garbage."""


def normalize_cui(raw: str) -> int:
    """Strip an optional 'RO' prefix and whitespace, return the numeric CUI.

    Raises ValueError if what's left isn't a plain number.
    """
    cleaned = re.sub(r"(?i)^\s*ro", "", raw or "").strip()
    if not cleaned.isdigit():
        raise ValueError(f"CUI invalid: {raw!r}")
    return int(cleaned)


def _fetch(numeric_cui: int, day: str) -> dict:
    payload = json.dumps([{"cui": numeric_cui, "data": day}]).encode("utf-8")
    req = urllib.request.Request(
        _ANAF_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AnafCuiError(f"Serviciul ANAF nu a putut fi contactat: {exc}") from exc
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AnafCuiError("Raspuns neasteptat de la serviciul ANAF.") from exc


def verify_cui(cui: str, on_date: datetime.date | None = None) -> dict | None:
    """Look up a CUI/CIF at ANAF.

    Returns a dict with cui/denumire/adresa/stare_inregistrare/scpTVA if
    ANAF has a record of it, or None if it does not exist there. Raises
    AnafCuiError if the service itself couldn't be reached — that's a
    connectivity problem, not proof the CUI is invalid.
    """
    numeric_cui = normalize_cui(cui)
    day = (on_date or datetime.date.today()).isoformat()
    data = _fetch(numeric_cui, day)
    found = data.get("found") or []
    if not found:
        return None
    general = found[0].get("date_generale") or {}
    scop_tva = found[0].get("inregistrare_scop_Tva") or {}
    return {
        "cui": general.get("cui", numeric_cui),
        "denumire": general.get("denumire", ""),
        "adresa": general.get("adresa", ""),
        "stare_inregistrare": general.get("stare_inregistrare", ""),
        "scpTVA": scop_tva.get("scpTVA", False),
    }
