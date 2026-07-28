"""Migrare one-shot a datelor SQLite/SQLCipher -> PostgreSQL.

Ruleaza pe server, ca pas al comutarii unui mediu pe Postgres (vezi
planning/migrare-postgres.md, faza 3), INAINTE ca aplicatia sa porneasca
cu ETVA_DB=postgres:

    ETVA_DATA_DIR=... DATABASE_URL=... python -m portal.migrare_pg

Ce face, in ordine, totul intr-O SINGURA tranzactie Postgres (orice
eroare lasa baza exact cum era):

1. refuza sa ruleze daca baza Postgres are deja firme/utilizatori
   (protectie la dubla rulare - nu exista "merge", doar migrare completa
   pe o baza goala);
2. copiaza tabelele portalului din portal.db pastrand id-urile exacte
   (un singur fisier-sursa, fara coliziuni), cu 0/1 -> boolean unde
   schema PG cere; la final seteaza fiecare secventa peste MAX(id) -
   aceeasi lectie ca _migrate_firms_autoincrement: un id folosit vreodata
   nu trebuie reemis;
3. pentru fiecare firma, deschide firm_<id>.db cu cheia despachetata din
   firm_keys si copiaza cele 7 tabele per-firma sub firm_id-ul ei, cu
   REMAPAREA id-urilor: fisierele separate aveau spatii de id suprapuse
   (clients id 1 exista in fiecare firma), deci id-urile vechi nu pot fi
   pastrate; referintele interne (reconciliations.client_id,
   *.reconciliation_id, client_assignments.client_id) sunt rescrise prin
   harta veche->noua. audit_log.entity_id (text istoric) NU se rescrie -
   ramane valoarea consemnata la momentul actiunii.

Fisierele SQLite nu sunt atinse - raman pe disc ca backup inghetat.
"""
import json
import os
import sqlite3
import sys

from etva import db as fdb
from etva import pg
from portal import security as psec
from portal.run import data_dir

# Coloanele care in SQLite sunt INTEGER 0/1, dar in Postgres boolean.
_COLOANE_BOOLEAN = {
    "firms": {"active", "email_verificat"},
    "users": {"is_master", "onboarding_completat", "active"},
    "user_firms": {"active"},
    "announcements": {"activ"},
    "contact_messages": {"citit"},
    "payments": {"recurent"},
    "contracts": {"semnatura_verificata"},
    "setari_tva": {"activa"},
}

# Ordinea de import a tabelelor portalului respecta dependintele FK
# (firms/users inaintea user_firms, invoices inaintea payments).
_TABELE_PORTAL = [
    "firms", "users", "user_firms", "firm_keys", "pipeline_log",
    "announcements", "contact_messages", "master_actions",
    "deletion_requests", "anaf_oauth_tokens", "invoices", "payments",
    "planuri_facturare", "contracts", "login_lockouts", "setari_tva",
]

# Tabelele portalului care au coloana id generata (primesc setval la final).
_TABELE_PORTAL_CU_ID = [
    "firms", "users", "pipeline_log", "announcements", "contact_messages",
    "master_actions", "deletion_requests", "invoices", "payments",
    "contracts", "setari_tva",
]


def _valoare(tabel: str, coloana: str, v):
    if coloana in _COLOANE_BOOLEAN.get(tabel, set()) and v is not None:
        return bool(v)
    return v


def _copiaza_tabel(sqlite_conn, pg_cur, tabel: str, coloane: list) -> int:
    randuri = sqlite_conn.execute(
        f"SELECT {', '.join(coloane)} FROM {tabel}").fetchall()
    if not randuri:
        return 0
    placeholders = ", ".join(["%s"] * len(coloane))
    sql = (f"INSERT INTO {tabel}({', '.join(coloane)}) "
           f"VALUES ({placeholders})")
    for r in randuri:
        pg_cur.execute(sql, [_valoare(tabel, c, r[c]) for c in coloane])
    return len(randuri)


def _migreaza_firma(pg_cur, firm_id: int, fc) -> dict:
    """Copiaza cele 7 tabele ale unei firme sub firm_id, remapand id-urile.
    Intoarce statistici per tabel."""
    pg_cur.execute("SELECT set_config('app.firm_id', %s, false)", (str(firm_id),))
    stats = {}

    harta_clienti = {}
    for r in fc.execute("SELECT id, cui, name FROM clients ORDER BY id"):
        pg_cur.execute(
            "INSERT INTO clients(firm_id, cui, name) VALUES (%s, %s, %s) "
            "RETURNING id", (firm_id, r["cui"], r["name"]))
        harta_clienti[r["id"]] = pg_cur.fetchone()["id"]
    stats["clients"] = len(harta_clienti)

    n = 0
    for r in fc.execute("SELECT username, client_id FROM client_assignments"):
        if r["client_id"] not in harta_clienti:
            continue  # alocare orfana (client sters candva) - fara corespondent
        pg_cur.execute(
            "INSERT INTO client_assignments(firm_id, username, client_id) "
            "VALUES (%s, %s, %s)",
            (firm_id, r["username"], harta_clienti[r["client_id"]]))
        n += 1
    stats["client_assignments"] = n

    harta_rec = {}
    for r in fc.execute("SELECT id, client_id, period, created_at, created_by "
                        "FROM reconciliations ORDER BY id"):
        client_nou = (harta_clienti.get(r["client_id"])
                      if r["client_id"] is not None else None)
        pg_cur.execute(
            "INSERT INTO reconciliations(firm_id, client_id, period, "
            "created_at, created_by) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (firm_id, client_nou, r["period"], r["created_at"], r["created_by"]))
        harta_rec[r["id"]] = pg_cur.fetchone()["id"]
    stats["reconciliations"] = len(harta_rec)

    for tabel in ("invoices_company", "invoices_anaf"):
        n = 0
        for r in fc.execute(f"SELECT reconciliation_id, partner_cui, invoice_no, "
                            f"date, base, vat, category FROM {tabel}"):
            if r["reconciliation_id"] not in harta_rec:
                continue
            pg_cur.execute(
                f"INSERT INTO {tabel}(firm_id, reconciliation_id, partner_cui, "
                f"invoice_no, date, base, vat, category) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (firm_id, harta_rec[r["reconciliation_id"]], r["partner_cui"],
                 r["invoice_no"], r["date"], r["base"], r["vat"], r["category"]))
            n += 1
        stats[tabel] = n

    n = 0
    for r in fc.execute("SELECT reconciliation_id, diff_type, details FROM differences"):
        if r["reconciliation_id"] not in harta_rec:
            continue
        pg_cur.execute(
            "INSERT INTO differences(firm_id, reconciliation_id, diff_type, details) "
            "VALUES (%s, %s, %s, %s)",
            (firm_id, harta_rec[r["reconciliation_id"]], r["diff_type"], r["details"]))
        n += 1
    stats["differences"] = n

    n = 0
    for r in fc.execute("SELECT user_id, action, entity, entity_id, ts FROM audit_log "
                        "ORDER BY id"):
        pg_cur.execute(
            "INSERT INTO audit_log(firm_id, user_id, action, entity, entity_id, ts) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (firm_id, r["user_id"], r["action"], r["entity"], r["entity_id"], r["ts"]))
        n += 1
    stats["audit_log"] = n
    return stats


def raport_migrare(data_dir_path: str, dsn: str) -> dict:
    """Verificare STRICT read-only, inainte de a rula vreodata migreaza()
    pe date reale: numara ce s-ar migra si semnaleaza orice ar bloca sau
    ar fi neasteptat. Nu deschide nicio tranzactie de scriere pe Postgres -
    doar SELECT-uri, atat pe SQLite cat si pe baza tinta. Gandit sa fie
    sigur de rulat chiar si direct impotriva unui mediu real (testare/
    productie), ca pas de pre-verificare separat de migrarea efectiva."""
    sconn = sqlite3.connect(os.path.join(data_dir_path, "portal.db"))
    sconn.row_factory = sqlite3.Row
    secret = psec.load_secret(os.path.join(data_dir_path, "secret.key"))

    raport = {"portal": {}, "firme": {}, "postgres": {}, "avertismente": []}
    try:
        for tabel in _TABELE_PORTAL:
            raport["portal"][tabel] = sconn.execute(
                f"SELECT COUNT(*) AS n FROM {tabel}").fetchone()["n"]

        firme = sconn.execute("SELECT id, name, cui FROM firms ORDER BY id").fetchall()
        for f in firme:
            fid = f["id"]
            eticheta = f"{fid} ({f['name']}, {f['cui']})"
            cale = os.path.join(data_dir_path, "firms", f"firm_{fid}.db")
            rand_cheie = sconn.execute(
                "SELECT wrapped_key FROM firm_keys WHERE firm_id=?", (fid,)).fetchone()
            if rand_cheie is None or not os.path.exists(cale):
                raport["firme"][eticheta] = "fara baza de date proprie - ar fi sarita"
                continue
            fc = fdb.open_db(cale, psec.unwrap_key(secret, rand_cheie["wrapped_key"]))
            try:
                alocari_orfane = fc.execute(
                    "SELECT COUNT(*) AS n FROM client_assignments "
                    "WHERE client_id NOT IN (SELECT id FROM clients)").fetchone()["n"]
                raport["firme"][eticheta] = {
                    "clients": fc.execute(
                        "SELECT COUNT(*) AS n FROM clients").fetchone()["n"],
                    "client_assignments": fc.execute(
                        "SELECT COUNT(*) AS n FROM client_assignments").fetchone()["n"],
                    "reconciliations": fc.execute(
                        "SELECT COUNT(*) AS n FROM reconciliations").fetchone()["n"],
                    "invoices_company": fc.execute(
                        "SELECT COUNT(*) AS n FROM invoices_company").fetchone()["n"],
                    "invoices_anaf": fc.execute(
                        "SELECT COUNT(*) AS n FROM invoices_anaf").fetchone()["n"],
                    "differences": fc.execute(
                        "SELECT COUNT(*) AS n FROM differences").fetchone()["n"],
                    "audit_log": fc.execute(
                        "SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"],
                }
                if alocari_orfane:
                    raport["avertismente"].append(
                        f"Firma {eticheta}: {alocari_orfane} alocari "
                        "client_assignments orfane (client sters) - vor fi "
                        "sarite la migrare, ca de obicei.")
            finally:
                fc.close()
    finally:
        sconn.close()

    import psycopg
    from psycopg.rows import dict_row
    try:
        # dict_row - pg.verify_schema() indexeaza rezultatele dupa nume de
        # coloana, nu pozitional.
        pgconn = psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raport["postgres"]["eroare_conectare"] = str(exc)
        return raport
    try:
        cur = pgconn.cursor()
        probleme_schema = pg.verify_schema(pgconn)
        raport["postgres"]["schema_ok"] = not probleme_schema
        if probleme_schema:
            raport["postgres"]["probleme_schema"] = probleme_schema
        n_firme = cur.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"]
        n_useri = cur.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        raport["postgres"]["are_deja_date"] = bool(n_firme or n_useri)
        raport["postgres"]["gata_de_migrare"] = (
            not probleme_schema and not n_firme and not n_useri)
    finally:
        pgconn.close()

    return raport


def migreaza(data_dir_path: str, dsn: str) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    sconn = sqlite3.connect(os.path.join(data_dir_path, "portal.db"))
    sconn.row_factory = sqlite3.Row
    secret = psec.load_secret(os.path.join(data_dir_path, "secret.key"))

    pgconn = psycopg.connect(dsn, row_factory=dict_row)
    try:
        cur = pgconn.cursor()
        for tabel in ("firms", "users"):
            if cur.execute(f"SELECT COUNT(*) AS n FROM {tabel}").fetchone()["n"]:
                raise SystemExit(
                    f"Baza Postgres are deja randuri in '{tabel}' - migrarea "
                    "e one-shot, pe o baza goala. Nimic nu a fost modificat.")
        # Seed-urile (planuri_facturare/setari_tva) pot exista deja daca
        # aplicatia a pornit vreodata pe aceasta baza - valorile REALE,
        # eventual editate de master, vin din SQLite, deci le inlocuim.
        cur.execute("DELETE FROM planuri_facturare")
        cur.execute("DELETE FROM setari_tva")

        rezumat = {}
        for tabel in _TABELE_PORTAL:
            coloane = list(pg.EXPECTED_SCHEMA[tabel].keys())
            rezumat[tabel] = _copiaza_tabel(sconn, cur, tabel, coloane)

        firme = sconn.execute("SELECT id FROM firms ORDER BY id").fetchall()
        rezumat["firme"] = {}
        for f in firme:
            fid = f["id"]
            cale = os.path.join(data_dir_path, "firms", f"firm_{fid}.db")
            rand_cheie = sconn.execute(
                "SELECT wrapped_key FROM firm_keys WHERE firm_id=?", (fid,)).fetchone()
            if rand_cheie is None or not os.path.exists(cale):
                rezumat["firme"][fid] = "fara baza de date proprie - sarita"
                continue
            fc = fdb.open_db(cale, psec.unwrap_key(secret, rand_cheie["wrapped_key"]))
            try:
                rezumat["firme"][fid] = _migreaza_firma(cur, fid, fc)
            finally:
                fc.close()

        for tabel in _TABELE_PORTAL_CU_ID:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{tabel}', 'id'), "
                f"GREATEST(COALESCE((SELECT MAX(id) FROM {tabel}), 0), 1))")
        pgconn.commit()
        return rezumat
    except Exception:
        pgconn.rollback()
        raise
    finally:
        pgconn.close()
        sconn.close()


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("Seteaza DATABASE_URL (postgresql://...).")
        sys.exit(1)
    d = data_dir()
    if "--dry-run" in sys.argv[1:]:
        print(f"Verificare read-only pentru {d} (nimic nu se scrie)...")
        raport = json.dumps(raport_migrare(d, dsn), indent=2, ensure_ascii=False)
        print(raport)
        return
    print(f"Migrez {d} -> Postgres...")
    rezumat = migreaza(d, dsn)
    for tabel, n in rezumat.items():
        print(f"  {tabel}: {n}")
    print("Migrare completa - fisierele SQLite raman neatinse, ca backup.")


if __name__ == "__main__":
    main()
