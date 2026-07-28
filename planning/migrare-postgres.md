# Migrarea SQLite/SQLCipher → PostgreSQL

Stare: în lucru (începută 2026-07-28). Fazele și progresul lor: vezi lista de
task-uri a sesiunii (#201–#206) și commit-urile care referă acest document.

## Decizii de arhitectură (cu motivele lor)

**1. Postgres trăiește pe VPS (127.0.0.1), nu pe shared hosting.**
Verificat empiric 2026-07-28: portul 5432 al serverului shared Hostico
(188.241.222.225) e închis din exterior — bazele `gymhxjim_*` erau accesibile
doar din interiorul rețelei lor (phpPgAdmin le vedea pe `127.0.0.200`).
PostgreSQL 16 există deja pe VPS (instalat de ISPConfig), ascultă doar pe
127.0.0.1, iar bazele `etva_testare`/`etva_productie` + rolul `etva_app` sunt
create. Bazele shared rămân referință istorică / potențială țintă de backup
off-site — nu se șterg fără decizia lui Andrei.

**2. Dual-backend prin adaptor, nu rescriere hard-cutover.**
`etva/dbcompat.py` oferă un wrapper de conexiune psycopg cu semantica
`sqlite3` pe care o așteaptă tot codul existent. SQLite rămâne backend-ul
implicit (dev local + toată suita de teste actuală) până când testare, apoi
productie, sunt comutate și verificate. Rollback = ștergi variabila de mediu.
Selecție: `ETVA_DB=postgres` + `DATABASE_URL` (EnvironmentFile root-only,
`/etc/etva-{env}/db.env`, același tipar ca smtp.env — parola generată direct
pe server, nu trece prin chat/git).

**3. Izolarea per-firmă: RLS în loc de fișiere SQLCipher separate.**
Cele 7 tabele „per-firmă" (clients, client_assignments, reconciliations,
invoices_company, invoices_anaf, differences, audit_log) devin tabele
partajate cu `firm_id` + politică `izolare_firma`
(`USING/WITH CHECK firm_id = current_setting('app.firm_id')::int`, ENABLE +
FORCE). `firm_id` are DEFAULT `current_setting('app.firm_id')::int`, deci
INSERT-urile existente din `etva/*` (care nu cunosc coloana) funcționează
nemodificate. `portal/app.py::firm_conn(firm_id)` returnează pe PG un wrapper
peste aceeași conexiune care face `SET app.firm_id` la comutarea firmei —
sigur, pentru că toate cererile sunt deja serializate de `db_lock`.
Criptarea per-firmă (SQLCipher) e înlocuită de criptare la nivel de disc +
faptul că secretele individuale (chei, token-uri ANAF) rămân Fernet-wrapped
ca și acum. `firm_keys` rămâne fără FK spre `firms` — orfanii sunt
intenționați (istoric: recuperabilitatea bazelor șterse).

**4. Schema canonică e versionată: `etva/pg_schema.sql`.**
Idempotent (`CREATE TABLE IF NOT EXISTS`, `DROP POLICY IF EXISTS`+CREATE,
`ADD COLUMN IF NOT EXISTS`), aplicabil oricând cu `psql -f`. Înlocuiește
vechiul scratchpad nesalvat în git. `etva/pg.py::EXPECTED_SCHEMA` se ține în
sinc manual cu el; `verify_schema()` compară referința cu baza reală la
pornire (fail-fast pe derivă). Migrațiile `_migrate_*` rămân doar pe ramura
SQLite — pe PG schema evoluează prin acest fișier.

**5. Unicitatea clients.cui devine (firm_id, cui).**
În SQLite `UNIQUE(cui)` era per-fișier, deci implicit per-firmă; pe tabela
partajată echivalentul corect e `UNIQUE(firm_id, cui)` — două firme de
contabilitate pot avea același client.

## Semantica adaptorului (etva/dbcompat.py) — contractul exact

Scopul: codul din `portal/app.py`/`portal/db.py`/`etva/*` să ruleze
nemodificat (mai puțin sweep-ul de mai jos). Wrapper-ul:

- traduce placeholder-ele `?` → `%s`, sărind conținutul literalelor `'...'`;
- rândurile returnate sunt dict-uri cu valorile normalizate la semantica
  SQLite: `datetime/date` → string ISO (`.isoformat()`), `bool` → `1/0`,
  `Decimal` → `float`, `memoryview` → `bytes`;
- sesiunea PG rulează cu `timezone='UTC'` ca redarea ISO a timestamptz-urilor
  să fie stabilă, indiferent de fusul serverului;
- `insert_id(conn, sql, params)` — API explicit pentru cele 10 situri
  `cur.lastrowid`: pe SQLite execute+lastrowid, pe PG `... RETURNING id`;
- `executescript`/`PRAGMA` nu există pe ramura PG (migrațiile sunt
  SQLite-only); `commit()/rollback()/close()` passthrough;
- excepțiile de integritate psycopg sunt retraduse în
  `sqlite3.IntegrityError` ca blocurile `except` existente să prindă la fel;
- la teardown-ul fiecărei cereri, pe PG se face `rollback()` dacă nu s-a
  făcut commit — altfel un simplu GET lasă o tranzacție deschisă la infinit
  pe conexiunea unică partajată.

## Sweep-ul de compatibilitate (modificări de cod, valabile pe ambele backend-uri)

- literalele boolean din SQL (`active=1`, `is_master=1`, `citit=0`, `activa=1`,
  `VALUES(...,1)` pe coloane boolean etc.) → `TRUE`/`FALSE` (SQLite ≥3.23 le
  acceptă nativ ca 1/0);
- `INSERT OR REPLACE/IGNORE` (2 situri) → `ON CONFLICT ...` (valid în ambele);
- cele 10 situri `cur.lastrowid` → `insert_id()`;
- parametrii Python `0/1` scriși în coloane boolean → `True/False`.

Capcane cunoscute, de verificat explicit la review (nu de presupus):
- comparațiile de string-uri ISO făcute în Python pe valori venite din DB
  amestecate cu `datetime.now().isoformat()` proaspăt (ex. sortarea
  timeline-ului din /master/istoric, logica de trial) — redarea timestamptz
  în UTC adaugă `+00:00` unde SQLite întorcea exact ce s-a scris;
- aritmetică `float` pe valori care pe PG ar fi `Decimal` fără normalizare;
- `PRAGMA table_info` folosit în afara migrațiilor;
- testul cu `set_trace_callback` — SQLite-only, se marchează ca atare.

## Testare

Suita rămâne implicit pe SQLite (fixture-urile actuale, neschimbate).
`ETVA_TEST_PG=1` rulează aceeași suită pe un PostgreSQL local
(instalat pe Windows-ul de dev; baza de test se recreează per sesiune de
teste). Nimic nu se promovează fără ambele rulări verzi.

## Migrarea datelor + flip (per mediu, testare întâi)

1. `psql -f etva/pg_schema.sql` pe baza mediului;
2. script one-shot: portal.db → tabelele portal; fiecare `firm_*.db`
   (decriptat cu cheia din `firm_keys` + `secret.key`) → tabelele per-firmă
   cu `firm_id`-ul respectiv; la final `setval()` pe fiecare secvență la
   `MAX(id)` (protejează de reutilizarea id-urilor — lecția bug-ului
   `_migrate_firms_autoincrement`);
3. `ETVA_DB=postgres` + `EnvironmentFile` db.env în unitatea systemd,
   restart, verificare manuală completă (login, înregistrare, reconciliere,
   master, backup);
4. fișierele SQLite rămân pe disc ca backup înghețat — nu se șterg.

Momentul e ideal: productie are ~0 firme reale, deci pasul 2 e aproape gol.

## Explicit în afara scopului (proiecte separate, nu strecurate aici)

- mai mult de 1 worker gunicorn (scheduler-ele și lock-ul sunt gândite
  single-process; deblocarea concurenței e alt proiect);
- `BYPASSRLS`/politici pe tabelele portal (masterul are nevoie de vedere
  cross-firm acolo — rămân fără RLS, ca până acum);
- retragerea SQLite din cod (rămâne backend-ul de dev/teste până după
  stabilizarea completă în producție).
