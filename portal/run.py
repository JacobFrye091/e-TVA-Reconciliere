"""Run the portal locally: python -m portal.run (port 8990 by default).

ETVA_PORT and ETVA_DATA_DIR let each environment (dev/testare/productie)
use its own port and data folder without touching this file per-environment.
"""
import os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from portal.app import create_app


def data_dir() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, os.environ.get("ETVA_DATA_DIR", "eTVA-Portal"))
    os.makedirs(d, exist_ok=True)
    return d


if __name__ == "__main__":
    port = int(os.environ.get("ETVA_PORT", "8990"))
    create_app(data_dir()).run(host="127.0.0.1", port=port)
