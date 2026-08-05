# Brief: optimizări de performanță și infrastructură (2026-08-02 → 2026-08-03)

Document de referință: ce s-a făcut, ce s-a verificat concret (nu presupus),
și ce rămâne de făcut. Continuă analiza din `planning/harta-functii.md`.

---

## Ce s-a făcut

### 1. Indecși Postgres pe tabelele cu RLS

`reconciliations`, `invoices_company`, `invoices_anaf`, `differences`,
`audit_log` filtrau `firm_id` prin RLS la fiecare interogare fără niciun
index de suport (doar `clients` avea unul) — scan secvențial pe fiecare
query, inclusiv pe fluxul cel mai folosit (vizualizare/export rezultate
reconciliere). Adăugați 5 indecși (`CREATE INDEX CONCURRENTLY`, zero
downtime), în `etva/pg_schema.sql`.

**Verificat:** `EXPLAIN` confirmă index scan; toți indecșii `valid` pe
`etva_testare` și `etva_productie`.
**Commit-uri:** `5308fe3` (testare), `f5ac040` (productie), `b5f6a2d` (dev).

### 2. N+1 la `master_statistici`/`master_users` — decizie: neatins

Investigat: rolul `etva_app` nu are `BYPASSRLS` (decizie deliberată,
documentată în `planning/migrare-postgres.md`). Orice query agregat
cross-firmă pe tabele cu RLS ar necesita slăbirea izolării — cost mai mare
decât beneficiul, pentru o pagină admin cu trafic mic. Bucla per-firmă
rămâne, dar devine rapidă via indecșii de la punctul 1.

### 3. Descoperire: VPS-ul rula pe 1 vCPU / 1.9GB RAM

Verificare directă (nu presupunere) a arătat un singur nucleu CPU și
memorie critic de mică (1.8GB deja în swap). Asta a schimbat planul inițial
pentru `gunicorn --workers`.

### 4. Leader election pentru scheduler-ele de fundal (prerechizit pentru workers > 1)

Cu mai mulți workeri, fiecare proces ar porni propriile fire de fundal
(backup, remindere trial), rulându-le duplicat (ex. emailuri trimise de
două ori la boot). `_is_scheduler_leader` (file lock `fcntl.flock`,
`portal/app.py`) asigură că doar un singur proces le pornește — funcționează
identic pe SQLite și Postgres, la orice număr de workeri.

**Verificat live, de fiecare dată după fiecare schimbare de workeri:** `lsof`
pe fișierul de lock + log explicit `[scheduler] pid X: lider/nu sunt lider`.
**Commit-uri:** `569f87b`+`50be0de` (testare), `9238236` (productie), `f7427d5` (dev).

### 5. Bug găsit: leak de tranzacție în `trial_reminders.py`

`verifica_si_trimite`/`arhiveaza_firme_neplatitoare` lăsau tranzacția
implicită a `SELECT`-ului inițial deschisă când nu găseau nimic de procesat
(bucla nu ajungea niciodată la `commit()`). Descoperit **live**: a blocat
`CREATE INDEX CONCURRENTLY` pe testare și productie, cu sesiuni idle in
transaction de 5+ ore. Fix: `commit()` imediat după `fetchall()`. Test de
regresie adăugat (verifică direct `pg_stat_activity`).

### 6. VPS upgrade: go0 → go1

Confirmat cu Hostico. **Înainte → după:** 1→2 vCPU, 1.9→3.8GB RAM, 25→48GB
disc. Swap: 1.8GB folosit → 65MB. Verificat live după restart complet de
VM: toate serviciile active, HTTP 200, leader election corect.

### 7. `gunicorn --workers`: 1 → 2 → 3

- **1→2**, imediat după fix-ul de leader election (punctul 4) — pe 1 vCPU,
  beneficiu doar de izolare, nu paralelism real (corectat explicit ulterior
  când s-a descoperit punctul 3).
- **2→3**, după upgrade-ul la go1 (2 vCPU reale) — "nuclee + 1", verificat
  live pe ambele medii (procese, HTTP, leader election, lock file).

### 8. Bug găsit: același tipar de leak, în `portal/db.py`

În timpul verificării de la punctul 7, descoperit că `_migrate_seed_pachet_reconcilieri`,
`_migrate_seed_planuri_facturare`, `_migrate_seed_cota_tva` (rulează la
fiecare pornire, inclusiv pe Postgres) au exact același bug — `SELECT
COUNT(*)` apoi `return` fără `commit()` dacă tabela nu e goală. Confirmat
live: după restart cu workers=3, workerii non-lider aveau fiecare câte o
sesiune idle in transaction. Fix identic (commit imediat după count).
Migrațiile SQLite-only (`_migrate_add_*`) nu rulează pe Postgres și nu
reproduc problema — lăsate neatinse, intenționat.

**Commit-uri:** `c30efa9` (testare), `b87defb` (productie), `9cd6fe2` (dev).

### 9. Brief separat: certificat digital + locuri netratate

`planning/certificat-digital-si-locuri-netratate.md` — task-ul de obținere
a certificatului digital calificat (necesar pentru testarea live a
OAuth2-ului ANAF și a verificării semnăturilor de contract), plus o trecere
prin cod pentru alte zone marcate ca neterminate (plăți, contracte,
reconciliere bancară FGO, restore Postgres).

### Verificare finală, pe toate 3 branch-uri

`dev`, `testare`, `main` au conținut identic pentru toate schimbările de mai
sus (confirmat prin diff înainte de fiecare push). Nimic necomis, nimic
nepush-uit.

---

## Ce mai e de făcut

| # | Item | Tip | Blocaj |
|---|---|---|---|
| 1 | Certificat digital calificat real | Administrativ | Fără el, OAuth2 ANAF (decont) și verificarea semnăturilor rămân netestate live |
| ~~2~~ | ~~Restore Postgres testat (Faza 5)~~ | Tehnic | **FĂCUT 2026-08-05** — buton real în `/master/backup` (doar mediul testare), testat live cu date reale (round-trip complet), vezi `planning/restaurare-postgres.md` |
| ~~3~~ | ~~Monitorizare automată swap/load~~ | Tehnic | **FĂCUT 2026-08-03** — `etva-monitorizeaza-resurse.timer`, confirmat activ live |
| 4 | Integrare Netopia/FGO pentru plăți | Business | `PLATA_ACTIVA=0` — amânat până există cont Netopia configurat |
| 5 | Reactivare contracte (semnătură electronică) | Business | `CONTRACTE_ACTIVE=0` — depinde parțial și de #1 |
| 6 | Reconciliere bancară automată (FGO/PSD2) | Business | Discutat, neimplementat — depinde de #4 |
| 7 | API live ANAF pentru modul clasic (factură-cu-factură) | Extern | Așteaptă ca ANAF să publice formatul oficial — în afara controlului echipei |
| 8 | Separare testare/productie pe infrastructură proprie | Arhitectural | **FĂCUT 2026-08-04** — go2 (VPS separat, 4 vCPU/8GB) dedicat exclusiv productiei, cutover complet |
| 9 | Reconsiderare `--workers` peste 3 | Tehnic | Doar dacă monitorizarea reală (acum activă) arată nevoie — nu de făcut preventiv |
| 10 | Tunare Postgres (`shared_buffers`/`work_mem`) | Tehnic | **Parțial** — făcut pe testare 2026-08-04 (256MB/8MB, valori conservatoare, serverul mai găzduiește mail/DNS/FTP); rămâne de făcut pe go2 (productie), la un moment ales de Andrei — restart Postgres = întrerupere reală |
| 11 | Test de încărcare real (locust/k6) | Tehnic | Neînceput — singurul mod să se confirme ferm câți utilizatori concurenți ține infrastructura actuală |

Rămase fără nicio dependență externă, deci acționabile oricând: **#1**
(certificat), **#10** (tunare go2) și **#11** (test de încărcare) — restul
așteaptă fie o decizie de business (Netopia), fie ANAF, fie date reale de
trafic care încă nu există (2 firme active, 9MB bază de date).
