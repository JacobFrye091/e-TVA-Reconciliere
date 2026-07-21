"""Run the portal locally: python -m portal.run (port 8990)."""
import os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from portal.app import create_app


def data_dir() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "eTVA-Portal")
    os.makedirs(d, exist_ok=True)
    return d


if __name__ == "__main__":
    create_app(data_dir()).run(host="127.0.0.1", port=8990)
