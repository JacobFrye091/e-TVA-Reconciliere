"""Dev-only: run the platform headless on port 5123 with throwaway data."""
import os, sys, pathlib, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from werkzeug.serving import make_server
from portal.app import create_app


def main():
    data_dir = os.path.join(tempfile.gettempdir(), "etva-dev-platform")
    os.makedirs(data_dir, exist_ok=True)
    print(f"Dev data dir: {data_dir}")
    make_server("127.0.0.1", 5123, create_app(data_dir),
                threaded=True).serve_forever()


if __name__ == "__main__":
    main()
