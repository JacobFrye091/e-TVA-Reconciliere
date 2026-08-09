"""PostgreSQL connectivity - fundatia migrarii de la SQLite/SQLCipher
(vezi planning/migrare-postgres.md).

Bazele reale ale aplicatiei (etva_testare/etva_productie) ruleaza pe
PostgreSQL 16 chiar pe VPS, pe 127.0.0.1 - cele de pe gazduirea shared
cPanel (gymhxjim_*) s-au dovedit inaccesibile din exterior (portul 5432
inchis) si raman doar referinta istorica de schema. Sursa de adevar a
schemei e acum versionata in repo: etva/pg_schema.sql (idempotent,
aplicabil cu psql -f), iar EXPECTED_SCHEMA de mai jos trebuie tinut in
sinc cu el - verify_schema() compara acea referinta cu baza reala.

Modelul de securitate (confirmat cu utilizatorul): izolarea intre firme se
face prin Row-Level Security (politica 'izolare_firma', filtrand dupa
current_setting('app.firm_id')) in locul unui fisier SQLCipher separat per
firma; criptarea la repaus ramane la nivel de disc (LUKS), nu per-tabel;
secretele individuale (cheia firmei, token-urile OAuth ANAF) raman
criptate cu Fernet inainte sa intre in baza de date, exact ca acum.
"""
import os

import psycopg
from psycopg.rows import dict_row


class PgError(Exception):
    """Postgres nu a putut fi contactat sau schema nu corespunde."""


# Schema asteptata, transcrisa din etva_postgres_schema.sql (referinta
# mentinuta manual pe masura ce se adauga coloane noi - vezi task-urile
# #120, #127, #167). verify_schema() compara aceasta lista cu ce gaseste
# efectiv in baza de date, ca sa detectam rapid orice discrepanta inainte
# sa construim vreun cod pe ea.
EXPECTED_SCHEMA = {
    "firms": {
        "id": "integer", "name": "text", "cui": "text", "tip": "text",
        "active": "boolean", "email_verificat": "boolean",
        "email_verificare_token": "text",
        "creat_la": "timestamp with time zone",
        "trial_expira_la": "timestamp with time zone",
        "ciclu_facturare": "text", "trial_reminder_ultim_prag": "integer",
        "arhivata_la": "timestamp with time zone",
        "reconcilieri_lunare_estimate": "integer",
        "risc_fiscal_nivel": "text",
    },
    "users": {
        "id": "integer", "username": "text", "pw_hash": "text",
        "email": "text", "is_master": "boolean",
        "onboarding_completat": "boolean", "active": "boolean",
    },
    "user_firms": {
        "user_id": "integer", "firm_id": "integer", "role": "text",
        "active": "boolean",
    },
    "firm_keys": {
        "firm_id": "integer", "wrapped_key": "bytea",
    },
    "pipeline_log": {
        "id": "integer", "source_env": "text", "target_env": "text",
        "commit_hash": "text", "promoted_by": "text",
        "promoted_at": "timestamp with time zone",
    },
    "announcements": {
        "id": "integer", "mesaj": "text", "tip": "text",
        "incepe_la": "timestamp with time zone",
        "se_termina_la": "timestamp with time zone", "creat_de": "text",
        "creat_la": "timestamp with time zone", "activ": "boolean",
    },
    "contact_messages": {
        "id": "integer", "nume": "text", "email": "text", "tip": "text",
        "mesaj": "text", "trimis_de": "text", "firma": "text",
        "creat_la": "timestamp with time zone", "citit": "boolean",
    },
    "master_actions": {
        "id": "integer", "actiune": "text", "detalii": "text",
        "creat_de": "text", "creat_la": "timestamp with time zone",
    },
    "deletion_requests": {
        "id": "integer", "user_id": "integer", "username": "text",
        "firm_id": "integer", "firm_name": "text",
        "creat_la": "timestamp with time zone",
        "termen_la": "timestamp with time zone", "stare": "text",
        "procesat_la": "timestamp with time zone", "procesat_de": "text",
    },
    "login_lockouts": {
        "identificator": "text", "incercari": "integer",
        "ultima_incercare": "timestamp with time zone",
        "blocat_pana": "timestamp with time zone",
    },
    "anaf_oauth_tokens": {
        "firm_id": "integer", "wrapped_access_token": "bytea",
        "wrapped_refresh_token": "bytea",
        "obtinut_la": "timestamp with time zone",
        "expira_la": "timestamp with time zone", "autorizat_de": "text",
    },
    "invoices": {
        "id": "integer", "serie": "text", "numar": "integer",
        "firm_id": "integer", "firm_name": "text", "firm_cui": "text",
        "descriere": "text",
        "perioada_inceput": "timestamp with time zone",
        "perioada_sfarsit": "timestamp with time zone",
        "data_emiterii": "timestamp with time zone",
        "data_scadentei": "timestamp with time zone",
        "valoare_neta": "numeric", "cota_tva": "numeric",
        "valoare_tva": "numeric", "valoare_totala": "numeric",
        "moneda": "text", "stare": "text", "creat_de": "text",
        "creat_la": "timestamp with time zone",
        "anaf_index_incarcare": "text", "anaf_stare": "text",
        "anaf_id_descarcare": "text", "anaf_raspuns": "bytea",
        "anaf_trimis_la": "timestamp with time zone",
        "fgo_serie": "text", "fgo_numar": "text", "fgo_link_pdf": "text",
    },
    "payments": {
        "id": "integer", "firm_id": "integer", "ciclu_facturare": "text",
        "suma": "numeric", "moneda": "text", "recurent": "boolean",
        "stare": "text", "creat_la": "timestamp with time zone",
        "validat_de": "text", "validat_la": "timestamp with time zone",
        "invoice_id": "integer",
    },
    "planuri_facturare": {
        "tip": "text", "ciclu_facturare": "text",
        "pret_lunar_ron": "numeric", "actualizat_de": "text",
        "actualizat_la": "timestamp with time zone",
    },
    "setari_tva": {
        "id": "integer", "cota_procent": "numeric", "activa": "boolean",
        "actualizat_de": "text",
        "actualizat_la": "timestamp with time zone",
    },
    "pachete_reconcilieri": {
        "id": "integer", "reconcilieri_incluse": "integer",
        "marime_pachet": "integer", "pret_pachet_lunar_ron": "numeric",
        "actualizat_de": "text",
        "actualizat_la": "timestamp with time zone",
    },
    "nomenclator_module": {
        "modul": "text", "pret_lunar_ron": "numeric",
        "actualizat_de": "text",
        "actualizat_la": "timestamp with time zone",
    },
    "contracts": {
        "id": "integer", "firm_id": "integer", "numar": "integer",
        "ciclu_facturare": "text", "suma": "numeric",
        "beneficiar_denumire": "text", "beneficiar_cui": "text",
        "beneficiar_adresa": "text", "stare": "text",
        "creat_la": "timestamp with time zone", "metoda_semnatura": "text",
        "semnatura_mouse_img": "bytea",
        "semnatura_verificata": "boolean", "semnatura_detalii": "text",
        "semnat_la": "timestamp with time zone",
        "reziliere_solicitata_la": "timestamp with time zone",
        "reziliat_la": "timestamp with time zone", "reziliat_de": "text",
        "ramburs_procent": "numeric", "esemneaza_request_id": "text",
        "esemneaza_document_pdf": "bytea",
        "esemneaza_certificate_pdf": "bytea",
        "prestator_semnat_la": "timestamp with time zone",
        "contract_xml_final": "bytea",
    },
    "clients": {
        "id": "integer", "firm_id": "integer", "cui": "text",
        "name": "text", "gdpr_confirmat": "boolean",
        "gdpr_confirmat_de": "text",
        "gdpr_confirmat_la": "timestamp with time zone",
    },
    "client_assignments": {
        "firm_id": "integer", "username": "text", "client_id": "integer",
    },
    "reconciliations": {
        "id": "integer", "firm_id": "integer", "client_id": "integer",
        "period": "text", "created_at": "timestamp with time zone",
        "created_by": "text",
    },
    "invoices_company": {
        "id": "integer", "firm_id": "integer",
        "reconciliation_id": "integer", "partner_cui": "text",
        "invoice_no": "text", "date": "text", "base": "numeric",
        "vat": "numeric", "category": "text",
    },
    "invoices_anaf": {
        "id": "integer", "firm_id": "integer",
        "reconciliation_id": "integer", "partner_cui": "text",
        "invoice_no": "text", "date": "text", "base": "numeric",
        "vat": "numeric", "category": "text",
    },
    "differences": {
        "id": "integer", "firm_id": "integer",
        "reconciliation_id": "integer", "diff_type": "text",
        "details": "text",
    },
    "audit_log": {
        "id": "integer", "firm_id": "integer", "user_id": "text",
        "action": "text", "entity": "text", "entity_id": "text",
        "ts": "timestamp with time zone",
    },
    "cod_mappings": {
        "id": "integer", "firm_id": "integer", "client_id": "integer",
        "direction": "text", "cod": "text", "line_no": "text",
        "updated_at": "timestamp with time zone", "updated_by": "text",
    },
    "risc_fiscal_perioade": {
        "id": "integer", "firm_id": "integer", "client_id": "integer",
        "perioada": "text", "tip_raport": "text", "sursa_date": "text",
        "capitaluri_proprii": "numeric", "datorii_totale": "numeric",
        "cifra_afaceri": "numeric", "rezultat_net": "numeric",
        "declaratii_nedepuse_manual": "integer",
        "obligatii_restante_manual": "boolean",
        "obligatii_crescute_manual": "boolean",
        "flaguri_sectiune_b": "text",
        "scor_total_indicatori": "integer", "scor_max_posibil": "integer",
        "scor_afisat": "integer", "clasificare": "text",
        "scor_detaliu": "text", "creat_de": "text",
        "creat_la": "timestamp with time zone",
    },
    "risc_fiscal_alerte": {
        "id": "integer", "firm_id": "integer", "perioada_id": "integer",
        "semnatura": "text", "trimis_la": "timestamp with time zone",
    },
}


def dsn_from_env(var: str = "DATABASE_URL") -> str:
    """Citeste adresa de conectare Postgres dintr-o variabila de mediu -
    niciodata dintr-un fisier sau argument hardcodat (acelasi motiv ca la
    ESEMNEAZA_API_KEY: nu trebuie sa ajunga niciodata scrisa in cod sau in
    git)."""
    dsn = os.environ.get(var)
    if not dsn:
        raise PgError(f"Variabila de mediu {var} nu este setata.")
    return dsn


def connect(dsn: str):
    """Deschide o conexiune Postgres - analog cu etva.db.open_db, dar fara
    nicio cheie de criptare (criptarea la repaus e la nivel de disc, nu
    per-conexiune ca la SQLCipher)."""
    try:
        return psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raise PgError(f"Nu m-am putut conecta la Postgres: {exc}") from exc


def verify_schema(conn) -> "list[str]":
    """Compara schema reala din baza conectata cu EXPECTED_SCHEMA si
    intoarce o lista de discrepante (tabel/coloana lipsa sau cu tip
    diferit) - lista goala inseamna schema identica cu referinta."""
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public'")
    gasit: "dict[str, dict[str, str]]" = {}
    for rand in cur.fetchall():
        gasit.setdefault(rand["table_name"], {})[rand["column_name"]] = rand["data_type"]

    probleme = []
    for tabel, coloane in EXPECTED_SCHEMA.items():
        if tabel not in gasit:
            probleme.append(f"Lipseste tabelul '{tabel}'.")
            continue
        for coloana, tip_asteptat in coloane.items():
            tip_gasit = gasit[tabel].get(coloana)
            if tip_gasit is None:
                probleme.append(f"Lipseste coloana '{tabel}.{coloana}'.")
            elif tip_gasit != tip_asteptat:
                probleme.append(
                    f"Tip diferit la '{tabel}.{coloana}': asteptat "
                    f"'{tip_asteptat}', gasit '{tip_gasit}'.")
    return probleme
