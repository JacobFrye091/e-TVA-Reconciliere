"""Dev-only: run the app server headless on a fixed port (no pywebview).

Uses a throwaway data dir under the system temp folder so the real
%APPDATA% installation is never touched.
"""
import os, sys, pathlib, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from werkzeug.serving import make_server
from etva.main import AppHolder


def main():
    d = os.path.join(tempfile.gettempdir(), "etva-dev")
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    print(f"Dev data dir: {d}")
    make_server("127.0.0.1", 5123, AppHolder(d)).serve_forever()


if __name__ == "__main__":
    main()
