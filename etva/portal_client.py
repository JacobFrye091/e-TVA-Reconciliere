"""HTTP client for the account portal's /api/auth endpoint (stdlib only)."""
import json
import urllib.error
import urllib.request

DEFAULT_PORTAL_URL = "http://127.0.0.1:8990"


class PortalError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def authenticate(portal_url: str, username: str, password: str) -> dict:
    req = urllib.request.Request(
        portal_url.rstrip("/") + "/api/auth",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e)["error"]
        except Exception:
            msg = "Autentificare esuata."
        raise PortalError(msg, e.code)
    except urllib.error.URLError:
        raise PortalError(
            "Portalul de conturi nu poate fi contactat. "
            "Porneste portalul si incearca din nou.", 502)
