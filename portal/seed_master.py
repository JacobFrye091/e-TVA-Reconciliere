"""One-time local seeding of the master account.

Usage: python -m portal.seed_master <username>
The password is read from stdin so it never lands in shell history or git.
"""
import os, sys, getpass, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from portal import db as pdb
from portal import security as psec
from portal.run import data_dir


def main():
    if len(sys.argv) != 2:
        print("Utilizare: python -m portal.seed_master <utilizator>")
        sys.exit(1)
    username = sys.argv[1]
    password = os.environ.get("ETVA_MASTER_PASSWORD") or getpass.getpass(
        "Parola master: ")
    conn = pdb.open_db(os.path.join(data_dir(), "portal.db"))
    if conn.execute("SELECT 1 FROM users WHERE is_master=1").fetchone():
        print("Exista deja un cont master. Nu s-a modificat nimic.")
        sys.exit(1)
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        (username, psec.hash_password(password)))
    conn.commit()
    print(f"Cont master '{username}' creat.")


if __name__ == "__main__":
    main()
