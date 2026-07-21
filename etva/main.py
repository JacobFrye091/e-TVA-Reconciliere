"""Desktop entry point: local Flask in a background thread + pywebview window.

Flow: create_setup_app serves the unlock/setup wizard; once the DB is open,
the main app's routes are registered on the same server via a swap.
"""
import os, socket, threading
import webview
from werkzeug.serving import make_server
from etva.server import create_app, create_setup_app


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
    """Dispatches WSGI calls to setup app until unlocked, then to main app."""
    def __init__(self, app_dir):
        self.main_app = None
        self.setup_app = create_setup_app(app_dir, self._on_ready)
        self.app_dir = app_dir

    def _on_ready(self, conn):
        self.main_app = create_app(conn, os.path.join(self.app_dir, "uploads"))

    def __call__(self, environ, start_response):
        if self.main_app is not None and not environ["PATH_INFO"].startswith("/api/setup"):
            return self.main_app(environ, start_response)
        return self.setup_app(environ, start_response)


def main():
    app_dir = _app_dir()
    holder = AppHolder(app_dir)
    port = _free_port()
    server = make_server("127.0.0.1", port, holder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webview.create_window("e-TVA Reconciliere",
                          f"http://127.0.0.1:{port}/",
                          width=1200, height=800)
    webview.start()
    server.shutdown()


if __name__ == "__main__":
    main()
