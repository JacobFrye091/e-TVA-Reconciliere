"""Export local, pe firma, al documentelor pastrate in Postgres - facturi
(XML propriu + PDF) si contracte semnate (PDF) - pentru arhivarea lor pe
OneDrive alaturi de backup-ul zilnic (vezi /usr/local/sbin/etva-backup-pg.sh,
in afara acestui repo).

Ruleaza independent de Flask (nu are nevoie de create_app - nu porneste
scheduler-e, nu deschide conexiuni SQLCipher per-firma) - o singura
conexiune Postgres, prin etva.pg, la fel ca alte scripturi/migrari.

Regenereaza tot la fiecare rulare (nu incremental) - simplu si fara risc de
desincronizare; rclone (apelat separat, din scriptul bash) nu re-incarca
fisierele neschimbate.

XML-ul de aici NU e documentul oficial e-Factura (UBL) validat de ANAF -
vezi portal/invoicing.py::invoice_xml pentru motiv (FGO nu il expune prin
API, doar printr-un buton manual per-factura in dashboard-ul lor).

Usage: python -m portal.export_date_firme <director_iesire>
"""
import re
import sys
import urllib.error
import urllib.request

from etva import dbcompat, pg
from portal import contract as contract_mod
from portal import invoicing

_TIMEOUT_FETCH_PDF = 20


def _nume_folder_firma(firma: dict) -> str:
    """Nume de folder lizibil, dar unic (CUI e UNIQUE in firms) - caractere
    interzise in nume de fisier/folder (Windows/OneDrive) inlocuite cu '_'."""
    nume = re.sub(r'[\\/:*?"<>|]', "_", firma["name"]).strip()
    return f"{nume} ({firma['cui']})"


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=_TIMEOUT_FETCH_PDF) as resp:
        return resp.read()


def _exporta_factura(conn, factura: dict, dir_facturi) -> None:
    nume_fisier = f"factura-{factura['serie']}-{factura['numar']}"
    (dir_facturi / f"{nume_fisier}.xml").write_bytes(
        invoicing.invoice_xml(factura))
    if factura["fgo_link_pdf"]:
        pdf_bytes = _fetch_bytes(factura["fgo_link_pdf"])
    else:
        pdf_bytes = invoicing.generate_pdf(dict(factura))
    (dir_facturi / f"{nume_fisier}.pdf").write_bytes(pdf_bytes)


def _exporta_contract(contract: dict, dir_contracte) -> None:
    nume_fisier = f"contract-{contract['numar']}"
    (dir_contracte / f"{nume_fisier}.pdf").write_bytes(
        contract_mod.pdf_final(contract))
    if contract["esemneaza_certificate_pdf"]:
        (dir_contracte / f"{nume_fisier}-certificat.pdf").write_bytes(
            bytes(contract["esemneaza_certificate_pdf"]))


def exporta(conn, director_iesire) -> dict:
    """Exporta toate firmele active (active=TRUE, nearhivate) in
    director_iesire/<firma>/{facturi,contracte}/... . Intoarce un rezumat
    {firme, facturi, contracte, erori: [str]} - o eroare la o singura
    factura/contract nu opreste restul exportului (backup-ul de baza al
    bazei de date, mult mai important, ruleaza oricum separat)."""
    rezumat = {"firme": 0, "facturi": 0, "contracte": 0, "erori": []}
    firme = conn.execute(
        "SELECT * FROM firms WHERE active=TRUE AND arhivata_la IS NULL "
        "ORDER BY name").fetchall()
    for firma in firme:
        dir_firma = director_iesire / _nume_folder_firma(firma)
        dir_facturi = dir_firma / "facturi"
        dir_contracte = dir_firma / "contracte"

        facturi = conn.execute(
            "SELECT * FROM invoices WHERE firm_id=? ORDER BY numar",
            (firma["id"],)).fetchall()
        contracte = conn.execute(
            "SELECT * FROM contracts WHERE firm_id=? AND stare='semnat' "
            "ORDER BY numar", (firma["id"],)).fetchall()
        if not facturi and not contracte:
            continue

        rezumat["firme"] += 1
        if facturi:
            dir_facturi.mkdir(parents=True, exist_ok=True)
        if contracte:
            dir_contracte.mkdir(parents=True, exist_ok=True)

        for factura in facturi:
            try:
                _exporta_factura(conn, factura, dir_facturi)
                rezumat["facturi"] += 1
            except (urllib.error.URLError, OSError) as e:
                rezumat["erori"].append(
                    f"factura {factura['serie']}-{factura['numar']} "
                    f"({firma['name']}): {e}")
        for contract in contracte:
            try:
                _exporta_contract(contract, dir_contracte)
                rezumat["contracte"] += 1
            except OSError as e:
                rezumat["erori"].append(
                    f"contract {contract['numar']} ({firma['name']}): {e}")
    return rezumat


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m portal.export_date_firme <director_iesire>",
              file=sys.stderr)
        sys.exit(2)
    import pathlib
    director_iesire = pathlib.Path(sys.argv[1])
    director_iesire.mkdir(parents=True, exist_ok=True)

    conn = dbcompat.connect(pg.dsn_from_env())
    try:
        rezumat = exporta(conn, director_iesire)
    finally:
        conn.close()

    print(f"firme: {rezumat['firme']}, facturi: {rezumat['facturi']}, "
         f"contracte: {rezumat['contracte']}, erori: {len(rezumat['erori'])}")
    for eroare in rezumat["erori"]:
        print(f"  - {eroare}", file=sys.stderr)


if __name__ == "__main__":
    main()
