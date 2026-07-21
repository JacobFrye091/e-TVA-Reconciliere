"""Desktop entry point: local Flask in a background thread + pywebview window.

The window starts on the login gate; after portal authentication succeeds
the same server switches to the main app for that identity. Logout resets
back to the gate.
"""
import os, socket, threading
import webview
from werkzeug.serving import make_server
from etva.server import create_app, create_gate_app
from etva.portal_client import DEFAULT_PORTAL_URL


def _app_dir() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "eTVA-Reconciliere")
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    return d


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AppHolder:
    """Dispatches WSGI calls to the gate until login, then to the main app."""

    def __init__(self, app_dir, portal_url):
        self.app_dir = app_dir
        self.main_app = None
        self.gate_app = create_gate_app(app_dir, portal_url, self._on_ready)

    def _on_ready(self, conn, identity):
        self.main_app = create_app(
            conn, os.path.join(self.app_dir, "uploads"), identity,
            on_logout=self.reset)

    def reset(self):
        self.main_app = None

    def __call__(self, environ, start_response):
        app = self.main_app if self.main_app is not None else self.gate_app
        return app(environ, start_response)


def main():
    app_dir = _app_dir()
    portal_url = os.environ.get("ETVA_PORTAL_URL", DEFAULT_PORTAL_URL)
    holder = AppHolder(app_dir, portal_url)
    port = _free_port()
    server = make_server("127.0.0.1", port, holder, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webview.create_window("e-TVA Reconciliere",
                          f"http://127.0.0.1:{port}/",
                          width=1200, height=800)
    webview.start()
    server.shutdown()


if __name__ == "__main__":
    main()
