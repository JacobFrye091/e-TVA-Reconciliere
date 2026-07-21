"""Dev-only: run portal (8990) + app server (5123) headless, no pywebview.

Uses throwaway data dirs under the system temp folder so the real
%APPDATA% installations are never touched.
"""
import os, sys, pathlib, tempfile, threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from werkzeug.serving import make_server
from portal.app import create_app as create_portal
from etva.main import AppHolder


def main():
    portal_dir = os.path.join(tempfile.gettempdir(), "etva-dev-portal")
    app_dir = os.path.join(tempfile.gettempdir(), "etva-dev")
    os.makedirs(os.path.join(app_dir, "uploads"), exist_ok=True)
    os.makedirs(portal_dir, exist_ok=True)
    print(f"Dev data dirs: {portal_dir} | {app_dir}")

    portal_srv = make_server("127.0.0.1", 8990, create_portal(portal_dir),
                             threaded=True)
    threading.Thread(target=portal_srv.serve_forever, daemon=True).start()
    make_server("127.0.0.1", 5123,
                AppHolder(app_dir, "http://127.0.0.1:8990"),
                threaded=True).serve_forever()


if __name__ == "__main__":
    main()
