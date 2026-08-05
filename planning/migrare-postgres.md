# Migrarea SQLite/SQLCipher → PostgreSQL

Stare: **atât testare cât și productie rulează pe Postgres din 2026-07-29**
(Fazele 3 și 4 complete, verificate live, cu OK explicit al lui Andrei
pentru productie: "continua schimbarea si in productie, dai bice"). Faza 5
(backup `pg_dump`) era deja completă; **restore (2026-08-05, vezi
`planning/restaurare-postgres.md`) e acum și el complet, doar pe mediul
testare** - buton real în `/master/backup`, testat live cu date reale.
Rămâne doar decizia finală despre bazele vechi de pe shared hosting
(cPanel) - fișierele SQLite/SQLCipher pre-migrare, păstrate ca backup
înghețat, nu au fost încă șterse/arhivate definitiv. Fazele și progresul
lor: vezi lista de task-uri a sesiunii (#201–#206) și commit-urile care
referă acest document.

## Faza 3 — testare, executată și verificată (2026-07-29)

Secvență reală, în ordine: cod promovat dev→testare (426 teste, 0 eșecuri)
și deployat pe VPS (încă pe SQLite - doar deploy de cod); copie de
siguranță suplimentară a `eTVA-Portal-Testare/` pe disc
(`eTVA-Portal-Testare.pre-postgres-backup-20260729`, în plus față de
fișierele SQLite care oricum rămân neatinse); `raport_migrare` (dry-run,
read-only) rulat pe datele reale - 1 firmă (DEDEMAN SRL), 2 utilizatori,
0 avertismente, țintă goală și schema conformă; `migreaza()` rulat real -
rezultatul a coincis exact cu raportul; date verificate direct în Postgres
(id-uri păstrate, `RLS` funcțional pe `app.firm_id`); `EnvironmentFile=-/etc/etva-testare/db.env`
adăugat în unitatea systemd, `daemon-reload` + `restart` -
`ETVA_DB=postgres` confirmat în procesul rulat, jurnal curat, HTTP 200.

**Verificare funcțională reală, prin browser, pe site-ul live**
(testare.ereconciliere.ro): apel live către ANAF (`/api/anaf/denumire?cui=RO35070700`,
200 OK, denumire preluată corect) și o **înregistrare completă de cont nou**
dusă până la capăt prin formular - firma nouă (`VML EXPERT ADVISOR SRL`,
id 2), user nou (`vml-expert-advisor-srl`, id 3) au aterizat corect în
Postgres: `email_verificat=False` (boolean real, nu 0/1), `trial_expira_la`
un `timestamptz` aware calculat corect la +30 zile - dovada directă că
fix-ul de `datetime.now(timezone.utc)` din sweep-ul de compatibilitate era
real necesar, nu teoretic. Firma existentă (DEDEMAN SRL) și userii ei au
rămas intacți. Contul de test nu a fost șters - rămâne ca dovadă vie pe
mediul de testare.

**Notă operațională pentru viitor**: parola reală a rolului `etva_app` nu
e cunoscută (generată direct pe server, niciodată în chat) - orice comandă
care are nevoie de `DATABASE_URL` trebuie să o citească din
`/etc/etva-{env}/db.env` (`set -a && . /etc/etva-{env}/db.env`), nu
presupusă sau cerută utilizatorului.

## Faza 4 — productie, executată și verificată (2026-07-29)

Autorizată explicit de Andrei ("continua schimbarea si in productie, dai
bice"), imediat după Faza 3. Secvență identică cu testare: cod promovat
testare→main (426 teste, 0 eșecuri) și deployat (încă SQLite); copie de
siguranță suplimentară (`eTVA-Portal-Productie.pre-postgres-backup-20260729`);
`raport_migrare` pe datele reale - **0 firme, 1 utilizator (masterul
AVASILESCU)**, câteva rânduri de istoric admin (anunțuri/mesaje contact/
audit master), 0 avertismente, țintă goală, schema conformă; `migreaza()`
rulat real, rezultat identic cu raportul; date verificate direct (master
cu `is_master=True`/`active=True`, `setari_tva` la 21% activ); systemd
actualizat (`EnvironmentFile=-/etc/etva-productie/db.env` adăugat lângă
`smtp.env`/`esemneaza.env` deja existente), `restart` - `ETVA_DB=postgres`
confirmat, jurnal curat, HTTP 200 atât local cât și **HTTPS 200 pe
ereconciliere.ro**.

**Verificare funcțională, deliberat mai conservatoare decât pe testare**:
apel live către ANAF prin formularul real de înregistrare de pe
ereconciliere.ro (`checked:true`, denumire preluată corect) - dovadă că
serverul flip-uit servește corect trafic real prin HTTPS. **Nu s-a dus până
la capăt o înregistrare completă** (spre deosebire de testare) - decizie
deliberată, ca să nu rămână un cont de test permanent în baza de date de
producție reală; codul e identic cu cel deja verificat exhaustiv (înregistrare
completă + RLS) pe testare, deci riscul acoperit e minim.

Ambele fișiere SQLite (`eTVA-Portal-Testare`/`eTVA-Portal-Productie`) rămân
neatinse pe disc, plus cele două copii `.pre-postgres-backup-20260729` -
nimic nu a fost șters.

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

Suita implicită (`pytest -q`, ~417 teste) rămâne pe SQLite, neschimbată -
nu depinde de PostgreSQL fiind pornit pe mașina care rulează testele.

`tests/test_migrare_pg.py` (5 teste, pentru `portal/migrare_pg.py`) rulează
împotriva unui PostgreSQL 16 REAL local, cu auto-skip curat dacă acel
cluster nu e pornit - nu blochează niciodată suita principală. Setup local
(o singură dată, pe mașina de dev):
```
initdb -D <folder> -U postgres -A trust
pg_ctl -D <folder> -o "-p 54329 -c listen_addresses=127.0.0.1" start
psql -U postgres -h 127.0.0.1 -p 54329 -c "CREATE ROLE etva_app LOGIN PASSWORD 'etva_test'"
psql -U postgres -h 127.0.0.1 -p 54329 -c "CREATE DATABASE etva_template"
psql -U postgres -h 127.0.0.1 -p 54329 -d etva_template -f etva/pg_schema.sql
```
Fixture-ul `pg_dsn` clonează `etva_template` (`CREATE DATABASE ... TEMPLATE`)
într-o bază nouă per test, ștearsă la final - fiecare test pornește de la
schema curată, fără date reziduale între teste. Orice modificare la
`etva/pg_schema.sql` trebuie reaplicată manual pe `etva_template` local
(exact comanda de mai sus) ca aceste teste să reflecte schema curentă.

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
