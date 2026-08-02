# Eliminarea blocajului global de request (Postgres)

Document de referinta: ce s-a schimbat, de ce, si ce ramane deschis.
Implementat 2026-08-02 pe `testare` (Postgres, `ETVA_DB=postgres`).

## Context

`gunicorn` rula cu `--workers 1`, iar fiecare cerere HTTP achizitiona un
`threading.RLock` global in `before_request`, eliberat abia in
`teardown_request`. Motivul istoric documentat in cod era corect
(SQLite/SQLCipher nu suporta scriere concurenta pe aceeasi conexiune),
dar cauza reala era alta: exista o singura conexiune fizica Postgres
pentru tot procesul (`conn = pdb.open_db(...)`), iar izolarea per-firma
pe Postgres se face prin RLS + `SET app.firm_id` pe **aceeasi**
conexiune (`dbcompat.firm_scope`) - explicit documentat ca "sigur doar
pentru ca db_lock serializeaza toate cererile".

Consecinta: un apel catre FGO (`_emite_factura_fgo`, timeout documentat
15s) sau un backup manual tineau lock-ul pe toata durata lor - orice
alta cerere, de la orice firma, era complet blocata cat timp dura.

## Ce s-a schimbat in cod

| Fisier | Schimbare |
|---|---|
| `requirements.txt` | + `psycopg_pool>=3.2` |
| `etva/dbcompat.py` | `make_pool(dsn)` - pool Postgres (`psycopg_pool.ConnectionPool`), configurat identic cu `connect()` (dict rows, fus UTC). Comentariul din `FirmScopedConnection` actualizat: siguranta nu mai vine din db_lock, ci din exclusivitatea per-conexiune a pool-ului. |
| `portal/app.py` (nou) | `_ReqScopedConn` - proxy care inlocuieste `conn` pe backend Postgres: rezolva dinamic catre `g.db_conn` (conexiunea din pool a cererii curente) sau, in afara unui context de request (teste, scheduler-ele de fundal), catre o conexiune de rezerva dedicata. Mosteneste `dbcompat.ConnCompat` doar ca `isinstance` checks (ex. `dbcompat.insert_id`) sa ramana corecte. **Niciun call-site `conn.execute(...)` din restul fisierului nu s-a schimbat.** |
| `portal/app.py` (`before_request`/`teardown_request`) | Ramificate pe backend: SQLite - neschimbat (tot lock-ul global de dinainte). Postgres - checkout/checkin dintr-un pool, fara niciun lock global. |
| `portal/app.py` (`creeaza_backup`) | `db_lock` achizitionat explicit in jurul zip-ului, nu mai mostenit implicit din lock-ul global de request (care a disparut pe calea Postgres). |
| `portal/app.py` (`valideaza_plata`) | Claim atomic (`UPDATE payments SET stare='in_procesare' WHERE stare='in_asteptare' RETURNING *`) inainte de apelul FGO, cu revenire la `in_asteptare` la orice eroare - vezi "Descoperiri" mai jos. |
| `portal/db.py` | Constanta noua `PLATA_IN_PROCESARE = "in_procesare"`. |
| `portal/templates/master_plati.html` | Chip separat "In procesare"; formularul de validare ascuns cat timp plata e in aceasta stare. |
| `tests/test_portal.py` | Teardown-ul care inchide `app.portal_conn` inchide acum si `app.db_pool`. Test nou: `test_postgres_pool_serves_concurrent_checkouts_without_blocking`. |

## Descoperiri importante

1. **Fara lock global, dubla validare a unei plati devine posibila.**
   Doi masteri (sau un dublu-click) ar fi putut trece amandoi de o
   simpla verificare `SELECT ... WHERE stare != validata` inainte sa
   apuce vreunul sa scrie, emitand factura FGO de doua ori pentru
   aceeasi incasare - lock-ul global preveni asta ca efect secundar,
   fara sa fie scopul lui declarat. Rezolvat cu claim atomic pe
   `payments.stare`.
2. **`etva/dbcompat.py::insert_id` alege ramura Postgres prin
   `isinstance(conn, (ConnCompat, FirmScopedConnection))`** - proxy-ul
   initial (fara mostenire) picica acest test si cadea pe ramura SQLite
   (`cur.lastrowid`), care arunca `NotImplementedError` pe Postgres.
   Descoperit prin suita de teste (`ETVA_TEST_PG=1`), care a picat 181
   de teste la prima incercare - motivul exact pentru care schimbarea
   asta trebuie verificata cu suita reala, nu doar citita.
3. Pe Postgres, `portal.db`/`firms/firm_*.db` de pe disc sunt fisiere
   moarte (`open_db()` ignora complet `path` pe aceasta ramura) -
   protectia `db_lock` din backup ramane relevanta doar pentru
   `uploads/`, chei etc., nu pentru date live.

## Testare

- 441 teste SQLite (implicit) - neschimbate, cale de cod neatinsa.
- 276 teste Postgres (`ETVA_TEST_PG=1`, inclusiv testul nou de
  concurenta) - toate trec.
- `test_postgres_pool_serves_concurrent_checkouts_without_blocking`
  dovedeste direct mecanismul: o conexiune tinuta ocupata 0.6s de un
  thread nu mai intarzie o a doua conexiune ceruta de alt thread
  (< 0.3s asteptare).

## Ce ramane deschis

- **`gunicorn`**: codul suporta acum concurenta reala, dar serviciul
  inca ruleaza `--workers 1` fara threaduri - pool-ul nu ajuta cu
  nimic pana `/etc/systemd/system/etva-testare.service` nu trece pe
  `--worker-class gthread --workers 1 --threads 8` (sau echivalent) si
  serviciul nu e repornit. Schimbare separata, in afara repo-ului
  (fisier root-only), cu restart confirmat explicit.
- **Verificare live**: dupa restart, de confirmat pe
  `testare.ereconciliere.ro` ca o emitere de factura FGO nu mai
  blocheaza o a doua cerere simultana.
- **Promovare pe productie**: abia dupa ce `testare` ruleaza stabil,
  prin fluxul existent (cherry-pick), cu restart separat pentru
  `etva-productie.service`, confirmat explicit.
- `psycopg_pool` a fost instalat manual in venv-ul de testare pentru a
  putea rula suita de teste - la promovare, trebuie instalat si in
  venv-ul de productie (`pip install -r requirements.txt`).
