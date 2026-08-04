# Harta functiilor aplicatiei e-TVA Reconciliere

Document de referinta: toate functiile aplicatiei (backend Python + frontend
JS) si ordinea in care se apeleaza intre ele, organizat pe straturi si pe
fluxuri. Generat prin scanarea completa a codului la 2026-08-02 (branch
`main`/`testare`/`dev`, commit `29ec879`). Actualizeaza-l manual daca
schimbi semnificativ un flux - nu se regenereaza automat.

Actualizat manual 2026-08-04 pe `main` (productie, go2): adaugata
notificarea de finalizare contract (`invoicing.NOTIFICARE_CONTRACT_FINALIZAT_EMAIL`,
commit `4e02684`). Nota: `testare` are in plus doua rute noi in panoul
`/master/pipeline` (promovare cod catre productie direct din VPS) care
inca nu exista pe `main` - vezi harta din branch-ul `testare` pentru ele.

Notatie: `->` inseamna "apeleaza". Functiile cu prefix `_` sunt helper-e
private (nu sunt rute/API public). `(extern)` = apelata doar din afara
modulului ei, fara sa apeleze nimic notabil intern.

## Cuprins

1. [Secventa de pornire](#1-secventa-de-pornire)
2. [Frontend (SPA) - web/index.html](#2-frontend-spa---webindexhtml)
3. [Backend - rute Flask (portal/app.py)](#3-backend---rute-flask-portalapppy)
4. [Helper-e interne din create_app (portal/app.py)](#4-helper-e-interne-din-create_app-portalapppy)
5. [Pachetul etva/ (motor de business logic)](#5-pachetul-etva---motor-de-business-logic)
6. [Module suport portal/ (fara app.py)](#6-module-suport-portal---fara-apppy)
7. [Fire de fundal (scheduler-e)](#7-fire-de-fundal-scheduler-e)
8. [Fluxuri end-to-end cheie](#8-fluxuri-end-to-end-cheie)

---

## 1. Secventa de pornire

### 1a. `gunicorn` -> `portal/wsgi.py` (productie - asa ruleaza :8990/:8991)

| # | Actiune |
|---|---|
| 1 | gunicorn importa modulul `portal.wsgi` |
| 2 | `from portal.app import create_app`, `from portal.run import data_dir` |
| 3 | la nivel de modul: `app = create_app(data_dir(), enable_backup_scheduler=True, enable_trial_reminder_scheduler=True)` - se executa o singura data, la incarcare |
| 4 | `app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)` - ca `url_for(_external=True)` sa produca `https://` cand Apache seteaza `X-Forwarded-Proto` |

**Constrangere critica**: gunicorn MUST rula cu exact 1 worker process (threads sunt ok). Cele 2 fire de fundal + conexiunea SQLite/SQLCipher + `db_lock` sunt in-process; 2 workeri = conexiuni/lock-uri neserializate = coruptie posibila.

### 1b. `python -m portal.run` (dezvoltare fara gunicorn)

Acelasi lant, dar prin `run.py`: `data_dir()` -> `create_app(data_dir(), enable_backup_scheduler=True, enable_trial_reminder_scheduler=True)` -> `.run(host="127.0.0.1", port=ETVA_PORT)`.

### 1c. `data_dir()` (in `run.py`, reutilizata de `wsgi.py`, `seed_master.py`, `migrare_pg.py`)

Citeste env `APPDATA` (fallback `~`) + env `ETVA_DATA_DIR` (default `"eTVA-Portal"`) -> `os.makedirs(d, exist_ok=True)` -> intoarce calea.

Pe testare/productie: `HOME=/opt/etva-{testare,productie}` + `ETVA_DATA_DIR=eTVA-Portal-{Testare,Productie}` (setate in unit-ul systemd) -> `data_dir()` = `/opt/etva-{testare,productie}/eTVA-Portal-{Testare,Productie}`.

### 1d. `create_app(data_dir, enable_backup_scheduler, enable_trial_reminder_scheduler)` (portal/app.py:149-3381)

| # | Linie | Actiune |
|---|-------|---------|
| 1 | 151 | `os.makedirs(data_dir, exist_ok=True)` |
| 2 | 152-155 | `os.makedirs` pentru `firms_dir` si `upload_dir` |
| 3 | 156 | `conn = pdb.open_db(data_dir/portal.db)` - conexiunea unica la baza portalului (vezi §6.1 pentru toate migrarile rulate aici) |
| 4 | 157 | `secret = psec.load_secret(data_dir/secret.key)` - cheia de criptare a datelor firmelor |
| 5 | 159 | `app = Flask(__name__)` |
| 6 | 163 | `app.secret_key = psec.load_secret(data_dir/flask_secret.key)` (persistenta, nu regenerata la fiecare pornire) |
| 7 | 164-173 | config sesiune: cookie 365 zile, `HttpOnly`, `SameSite=Lax`, `Secure` din env |
| 8 | 174 | `csrf = CSRFProtect(app)` |
| 9 | 176 | `firm_conns = {}` - cache de conexiuni per firma |
| 10 | 186 | `db_lock = threading.RLock()` |
| 11 | 188-191 | `@app.before_request _acquire_db_lock` - achizitioneaza `db_lock` la fiecare cerere |
| 12 | 193-209 | `@app.teardown_request _release_db_lock` - elibereaza lock-ul (+ `conn.rollback()` daca backend=postgres) |
| 13 | 211-212 | **daca** `enable_backup_scheduler`: `backup_mod.start_scheduler(data_dir, db_lock)` - porneste thread-ul de backup |
| 14 | 214-306 | definesc closures: `firm_conn`, `current_user`, `list_user_firms`, `_firma_testare_master`, `current_identity` |
| 15 | 308-314 | `@app.context_processor _inject_pachet_reconcilieri` - injecteaza `pachet_reconcilieri` in toate template-urile |
| 16 | 316-335 | `_log_master_action` + decoratorul `require(perm)` (folosit de toate rutele `/api/*`) |
| 17 | 337-3370 | **inregistrarea tuturor rutelor** (vezi §3) |
| 18 | 3372-3375 | **daca** `enable_trial_reminder_scheduler`: `remind_mod.start_scheduler(conn, db_lock, _trimite_email)` - porneste thread-ul de remindere trial (dupa ce `_trimite_email` exista deja) |
| 19 | 3377-3380 | expune `app.portal_conn`, `app.firm_conn`, `app.portal_secret`, `app.get_valid_anaf_access_token` (folosite de teste/seeding) |
| 20 | 3381 | `return app` |

### 1e. Alte puncte de intrare (scripturi CLI, nu servesc trafic HTTP)

- `portal/devserver.py::main()` - server dev pe portul 5123, date temporare, **fara** scheduler-e (`enable_*=False` implicit).
- `portal/seed_master.py::main()` - `data_dir()` -> `pdb.open_db` -> daca exista deja un `is_master=TRUE`, iese neschimbat -> altfel `psec.hash_password` -> INSERT user master -> commit.
- `portal/migrare_pg.py::main()` - vezi §6.9 (migrare SQLite -> Postgres, o singura data per mediu).

---

## 2. Frontend (SPA) - web/index.html

Un singur `<script>` (linia 430-957). Login/inregistrare/panou cont sunt
**in afara** SPA-ului (template-uri Jinja server-side separate,
`portal/templates/*.html`) - SPA-ul incepe abia dupa autentificare, servit
la `GET /app`.

### 2a. Incarcare initiala

```
window.addEventListener('DOMContentLoaded', ...)
  -> fetch('/api/me')
     -> daca ok: intraInAplicatie(ident)
     -> altfel: window.location = '/autentificare'

intraInAplicatie(ident):
  1. seteaza permisiuni, userCurent, firmaDirecta, anafAutorizat din ident
  2. ajusteaza vizibilitatea elementelor DOM dupa permisiuni (butoane admin/audit/export)
  3. await incarcaClienti()
  4. navigheaza('dashboard')
  5. daca !ident.onboarding_completat: setTimeout(arataIntrebareGhid, 500)
  6. verificaAnunt()
  7. setInterval(verificaAnunt, 5*60*1000)
```

### 2b. Helper-e transversale

- `api(url, opts)` -> daca metoda != GET/HEAD: `await _obtineCsrfToken()` -> adauga header `X-CSRFToken` -> `fetch(url, opts)` -> daca 401: redirect `/autentificare`.
- `_obtineCsrfToken()` -> `fetch('/api/csrf-token')` (o singura data, cache in `_csrfToken` - SPA-ul e servit static via `send_file`, nu poate primi tokenul in HTML ca template-urile Jinja).
- `arata(id)` / `navigheaza(id)` - comutare intre "straturi" (auth vs shell) si intre view-uri.

### 2c. Flux: reconciliere noua

```
ruleazaReconciliere():
  1. valideaza ca exista fisiere jurnal (fisierVanzari/fisierCumparari)
  2. construieste FormData: client_id (daca !firmaDirecta), period,
     format_jurnal() [citeste radio-ul SAGA/model bifat], company_file(s),
     anaf_sursa/anaf_file, anaf_mapping?, cod_mapping?
  3. api('/api/reconciliations', POST)  -> backend: new_reconciliation (§3)
  4. daca ok: afiseazaRezultate(body) -> navigheaza('rezultate')

comutaFormatJurnal() / formatJurnal() - comutare panou model vizibil,
  apelat onchange pe radio-urile SAGA/model.
comutaSursaAnaf() - comutare vizibilitate camp fisier ANAF cand e bifat
  "preia automat".
```

### 2d. Flux: rezultate -> reia alta verificare / export

```
afiseazaRezultate(b) - populeaza tabelele de rezultate (ramura d300_lines
  vs. invoices, dupa b.mode).

reiaAltaVerificare() - goleste fisierVanzari/Cumparari/Anaf + perioada +
  eroare, navigheaza('dashboard'), scrollTo top. NU atinge radio-ul
  format_jurnal/panoul modelului - alegerea SAGA/model ramane intentionat.

exporta() -> window.location = `/api/reconciliations/${reconCurent}/export`
  (navigare directa, nu fetch - backend: export_report §3).
```

### 2e. Flux: clienti / alocare / audit

```
incarcaClienti() -> api('/api/clients') GET -> populeaza tabel + selClient
  + alocClient.
adaugaClient() -> valideaza checkbox GDPR -> api('/api/clients', POST) ->
  daca ok: incarcaClienti().
aloca() -> valideaza username -> api('/api/assignments', POST).
incarcaAudit() -> api('/api/audit') GET -> populeaza tabel (apelat manual
  din onclick-ul butonului de navigare "Audit", nu la incarcarea paginii).
verificaAnunt() -> api('/api/anunt-activ') -> arata/ascunde banner (la
  intrare + la fiecare 5 minute).
```

### 2f. Flux: tur ghidat

```
arataIntrebareGhid() (modal initial, doar daca !onboarding_completat)
  -> onclick "Da": incepeTurul()
  -> onclick "Nu": marcheazaGhidTerminat()

incepeTurul():
  1. turPasi = pasiiTurului()   [construieste lista de pasi din
     permisiuni/firmaDirecta/navRezultate - include pasul "Formatul
     jurnalului" (#campFormatJurnal) intre "Perioada" si "Jurnalele firmei"]
  2. construiesteSuprapunereTur()  [creeaza elementele overlay in DOM]
  3. aratapPasulTur()

aratapPasulTur():
  - gaseste primul pas vizibil (elementVizibilTur - el.offsetParent!==null),
    sarind peste cele nevizibile (ex. #btnExport daca userul n-are
    permisiunea de export)
  - navigheaza(pas.view) daca acel view nu e deja activ
  - pozitioneaza overlay-ul (#turSpot/#turTip) pe elementul tinta

urmatorulPasTur() / pasulAnteriorTur() -> incrementeaza/decrementeaza
  turIndex -> aratapPasulTur().
opresteTurul() -> distrugeSuprapunereTur() -> marcheazaGhidTerminat()
  [api POST /api/onboarding/completat] -> navigheaza('dashboard').
```

### 2g. Logout

```
logout() -> api('/api/logout', POST) -> window.location = '/'.
```

---

## 3. Backend - rute Flask (portal/app.py)

Fisier de 3381 linii; aproape tot traieste in `create_app()`. Tabelul de
mai jos pastreaza ordinea din fisier (care respecta grupurile de
comentarii de sectiune existente in cod).

### 3a. Public pages

| Metoda | Path | Handler | Lant de apeluri |
|---|---|---|---|
| GET | `/`, `/index.html` | `landing` | `send_file(docs/index.html)` |
| GET | `/favicon.svg` | `favicon` | `send_file` |
| GET | `/ghid.html` | `ghid` | `send_file` |
| GET | `/termeni.html` | `termeni` | `send_file` |
| GET | `/confidentialitate.html` | `confidentialitate` | `send_file` |
| GET | `/cookie-uri.html` | `cookie_uri` | `send_file` |
| GET | `/contact.html` | `contact_page` | `send_file` |
| GET | `/api/anaf/denumire` | `anaf_denumire` | `_anaf_lookup(cui)` -> `anaf_cui.verify_cui` -> `jsonify` |

### 3b. ANAF OAuth2 (decontul precompletat)

| Metoda | Path | Handler | Lant de apeluri |
|---|---|---|---|
| GET | `/panou/anaf/autorizare` | `anaf_oauth_autorizare` | `current_user()` -> `_role_in_firm` -> genereaza `state` -> `redirect(anaf_oauth.build_authorize_url(...))` |
| GET | `/api/anaf/callback` | `anaf_oauth_callback` | valideaza `code`/`state` din sesiune -> `anaf_oauth.exchange_code_for_tokens(...)` -> `current_user()` -> `_store_anaf_tokens(...)` -> `redirect(panou)` |

### 3c. Inregistrare & autentificare

| Metoda | Path | Handler |
|---|---|---|
| GET/POST | `/inregistrare` | `register` |
| GET | `/verifica-email/<token>` | `verifica_email` |
| GET | `/asteapta-verificare-email` | `asteapta_verificare_email` |
| POST | `/retrimite-verificare-email` | `retrimite_verificare_email` |
| GET/POST | `/autentificare` | `login` |
| GET | `/iesire` | `logout_page` |

```
register (POST):
  validari campuri -> validare email regex -> validare accept_termeni ->
  validare lungime parola -> _parse_reconcilieri_estimate(f, tip) ->
  verifica CUI unic -> _verify_cui_or_error(cui)
    [-> _anaf_lookup -> anaf_cui.verify_cui]
  -> _unique_username(_slugify(name)) -> psec.hash_password(password) ->
  INSERT users -> _create_firm(...) ->
  daca token (firma noua neverificata): _trimite_email_verificare(...)
    [-> url_for -> _trimite_email]
  -> seteaza sesiune -> redirect(aplicatie)

verifica_email:
  cauta firma dupa token -> UPDATE firms SET email_verificat=TRUE ->
  cauta admin firmei -> daca are email: _trimite_email(...) (confirmare
  client) -> _trimite_email(CONTACT_EMAIL_TO, ...) (notificare interna) ->
  redirect(login)

login (POST):
  _login_blocat(identificator) -> cauta user master -> daca nu, cauta
  colegi firmei dupa CUI + psec.verify_password pe fiecare -> daca gasit:
  _reseteaza_login_esuat, altfel _inregistreaza_login_esuat -> seteaza
  sesiune -> daca master: redirect(master); altfel list_user_firms ->
  current_identity() -> audit.log(..., "login") -> redirect(panou sau
  aplicatie)
```

### 3d. The product (SPA)

| Metoda | Path | Handler | Lant de apeluri |
|---|---|---|---|
| GET | `/app` | `aplicatie` | `current_user()` -> verifica firma arhivata -> `current_identity()` -> daca `EMAIL_VERIFICARE_OBLIGATORIE` si neverificat: `redirect(asteapta_verificare_email)` -> altfel `send_file(_SPA)` |

### 3e. Firm account pages

| Metoda | Path | Handler |
|---|---|---|
| GET | `/panou` | `panou` |
| POST | `/panou/firme` | `add_firm` |
| GET | `/panou/plan` | `alege_plan` |
| POST | `/panou/plan` | `salveaza_plan` |
| POST | `/panou/plata` | `creeaza_cerere_plata` |

```
panou: current_user() -> list_user_firms -> alege firma activa -> membri
  (user_firms/users) -> deletion_requests -> anaf_oauth_tokens ->
  _zile_trial_ramase -> contracts -> render_template(..., anunt=_anunt_activ())

add_firm: current_user() -> verifica ca nu are deja firma "direct" ->
  valideaza name/cui/tip -> _parse_reconcilieri_estimate -> verifica CUI
  unic -> _verify_cui_or_error -> _create_firm(..., email_verificat=True)
  -> seteaza active_firm_id -> redirect(panou)
```

### 3f. Contract de prestari servicii

| Metoda | Path | Handler |
|---|---|---|
| GET | `/panou/contract` | `vezi_contract` |
| GET | `/panou/contract/pdf` | `descarca_contract_pdf` |
| GET | `/panou/contract/xml` | `descarca_contract_xml` |
| GET | `/panou/contract/certificat` | `descarca_certificat_esemneaza` |
| POST | `/panou/contract/semneaza` | `semneaza_contract` |
| POST | `/api/esemneaza/webhook` (csrf exempt) | `webhook_esemneaza` |
| POST | `/panou/contract/reziliaza` | `reziliaza_contract` |
| POST | `/panou/comutare-firma` | `switch_firm` |
| POST | `/panou/utilizatori` | `add_member` |
| POST | `/panou/utilizatori/<username>/dezactivare` | `deactivate_member` |
| POST | `/panou/cerere-stergere` | `cerere_stergere` |

```
vezi_contract: _contract_curent(firm_id) -> _actualizeaza_stare_esemneaza
  [-> esemneaza.get_sign_request -> daca ambii au semnat:
   _finalizeaza_contract_esemneaza -> esemneaza.get_completed_document_url,
   esemneaza.fetch_url_bytes, esemneaza.get_certificate_download_url,
   contract_mod.date_contract_xml]
  -> contract_mod.genereaza_text_din_rand(contract) -> render_template

semneaza_contract: _contract_curent -> daca metoda=CERTIFICAT: citeste
  fisier -> digital_signature.verifica_semnatura_pdf(pdf_bytes) -> daca
  invalid: eroare -> UPDATE contracts -> audit.log(...) ->
  redirect(vezi_contract)
```

### 3g. Master (statistici, useri, istoric)

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master` | `master` |
| POST | `/master/firma/<int:firm_id>/comutare` | `toggle_firm` |
| GET | `/master/statistici` | `master_statistici` |
| GET | `/master/utilizatori` | `master_users` |
| GET | `/master/utilizatori/<int:user_id>/istoric` | `master_user_history` |
| GET | `/master/utilizatori/<int:user_id>/istoric.xml` | `master_user_history_xml` |
| GET | `/master/firme/<int:firm_id>/istoric.xml` | `master_firma_istoric_xml` |
| GET | `/master/istoric` | `master_istoric_propriu` |
| GET | `/master/istoric.xml` | `master_istoric_propriu_xml` |

```
master: firms (cu n_users) -> contact_messages (necitite) ->
  deletion_requests (in asteptare/intarziate) -> citeste
  BACKUP_ONEDRIVE_STATUS -> render_template(versiune=pipeline.
  running_vs_current(), mediu=pipeline.own_environment())

master_statistici: firms -> pdb.get_preturi -> per firma activa cu ciclu:
  firm_conn -> count clients -> calcul MRR -> payments (sumate) ->
  _donut_segments x2 -> _bar_pct -> render_template
```

### 3h. Master: anunturi

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/anunturi` | `master_anunturi` |
| POST | `/master/anunturi` | `creeaza_anunt` |
| POST | `/master/anunturi/<int:id>/dezactivare` | `dezactiveaza_anunt` |
| GET | `/api/anunt-activ` | `anunt_activ_api` |

### 3i. Formular de contact

| Metoda | Path | Handler |
|---|---|---|
| POST | `/api/contact` (csrf exempt) | `trimite_contact` |
| GET | `/master/mesaje` | `master_mesaje` |
| POST | `/master/mesaje/<int:id>/citit` | `marcheaza_mesaj_citit` |

### 3j. Master: cereri de stergere a datelor (GDPR)

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/cereri-stergere` | `master_cereri_stergere` |
| POST | `/master/cereri-stergere/<int:id>/finalizare` | `finalizeaza_cerere_stergere` |
| POST | `/master/cereri-stergere/<int:id>/anulare` | `anuleaza_cerere_stergere` |

```
finalizeaza_cerere_stergere: cerere -> psec.hash_password(token aleator)
  -> UPDATE users (anonimizare username+parola) -> UPDATE user_firms
  SET active=FALSE -> UPDATE deletion_requests -> _log_master_action
  (jurnalul de audit ramane neschimbat - pastrat permanent)
```

### 3k. Master: facturare (VML Expert Advisor -> firme)

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/facturi` | `master_facturi` |
| POST | `/master/facturi` | `creeaza_factura` |
| GET | `/master/facturi/<int:id>/pdf` | `descarca_factura_pdf` |
| GET | `/master/facturi/<int:id>/xml` | `descarca_factura_xml` |
| POST | `/master/facturi/<int:id>/trimite-anaf` | `trimite_factura_anaf` |
| POST | `/master/facturi/<int:id>/verifica-stare` | `verifica_stare_factura_anaf` |
| GET | `/master/facturi/<int:id>/raspuns-anaf` | `descarca_raspuns_anaf` |

```
creeaza_factura: firms -> invoicing.next_invoice_number(conn,
  pdb.FACTURA_SERIE) -> calcul TVA/total -> INSERT invoices ->
  _log_master_action

trimite_factura_anaf: invoices -> _vml_firm_id() ->
  get_valid_anaf_access_token [-> anaf_oauth.refresh_access_token daca
  expirat] -> efactura_xml.build_invoice_xml(...) ->
  anaf_cui.normalize_cui(...) -> anaf_oauth.upload_invoice(...) ->
  UPDATE invoices -> _log_master_action

verifica_stare_factura_anaf: anaf_oauth.check_upload_status(...) -> daca
  gata: anaf_oauth.download_response(...) -> UPDATE invoices
```

### 3l. Firma: facturile proprii (doar vizualizare)

| Metoda | Path | Handler |
|---|---|---|
| GET | `/panou/factura/<int:id>/pdf` | `descarca_factura_proprie_pdf` |
| GET | `/panou/factura/<int:id>/xml` | `descarca_factura_proprie_xml` |
| GET | `/panou/factura/<int:id>/raspuns-anaf` | `descarca_raspuns_anaf_propriu` |

### 3m. Master: backup date

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/backup` | `master_backup` |
| POST | `/master/backup/creeaza` | `creeaza_backup` |
| GET | `/master/backup/<nume>/descarca` | `descarca_backup` |
| POST | `/master/backup/restaureaza` | `restaureaza_backup` |

```
restaureaza_backup: verifica mediu != productie -> verifica backend !=
  postgres -> verifica confirm=="da" -> backup_mod.validate_backup_zip
  -> backup_mod.create_backup + prune_old_backups (snapshot de siguranta
  inainte de restore) -> _log_master_action -> inchide conn + toate
  firm_conns -> backup_mod.restore_backup(data_dir, fisier)
```

### 3n. Master: remindere expirare trial

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/remindere-trial` | `master_remindere_trial` |
| POST | `/master/remindere-trial/trimite` | `trimite_remindere_trial` |
| POST | `/master/remindere-trial/arhiveaza` | `arhiveaza_firme_trial` |

Aceleasi functii ca in scheduler-ul de fundal (§7) - idempotente, deci
rularea manuala in aceeasi zi cu thread-ul nu retrimite/re-arhiveaza dublu.

### 3o. Master: validare incasari, contracte, nomenclator preturi

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/plati` | `master_plati` |
| POST | `/master/plati/<int:id>/valideaza` | `valideaza_plata` |
| GET | `/master/contracte` | `master_contracte` |
| GET | `/master/contracte/creeaza/<int:firm_id>` | `creeaza_contract_master` |
| POST | `/master/contracte/creeaza/<int:firm_id>` | `trimite_contract_master` |
| GET | `/master/contracte/<int:id>/pdf` | `descarca_contract_pdf_master` |
| GET | `/master/contracte/<int:id>/xml` | `descarca_contract_xml_master` |
| GET | `/master/contracte/<int:id>/certificat` | `descarca_certificat_esemneaza_master` |
| POST | `/master/contracte/<int:id>/reziliaza` | `finalizeaza_reziliere_contract` |
| GET | `/master/nomenclator` | `master_nomenclator` |
| POST | `/master/nomenclator` | `salveaza_nomenclator` |
| POST | `/master/nomenclator/pachete` | `salveaza_pachet_reconcilieri` |
| POST | `/master/nomenclator/tva` | `salveaza_cota_tva` |
| POST | `/master/nomenclator/tva/<int:id>/activeaza` | `activeaza_cota_tva` |

```
trimite_contract_master: firms -> ultimul contract -> valideaza form ->
  cauta admin cu email -> contract_mod.next_contract_number(conn) ->
  INSERT contracts -> contract_mod.genereaza_text_din_rand(contract) ->
  contract_mod.genereaza_pdf(..., tag_semnatura_esemneaza=True) ->
  esemneaza.upload_document(...) -> esemneaza.create_sign_request(...,
  recipients=[FURNIZOR, admin], sign_in_order=True) -> daca esueaza:
  DELETE contracts + eroare -> UPDATE contracts (metoda, request_id) ->
  _log_master_action -> redirect(master_contracte)
```

### 3p. Master: pipeline dev/testare/productie

| Metoda | Path | Handler |
|---|---|---|
| GET | `/master/pipeline` | `pipeline_dashboard` |
| POST | `/master/pipeline/promoveaza` | `promote_environment` |
| POST | `/master/server/restart` | `restart_server` |
| POST | `/master/backup-onedrive` | `master_backup_onedrive` |

```
pipeline_dashboard: pipeline.local_pipeline_available() -> daca True:
  per env pipeline.branch_info(env) -> per promotie posibila
  pipeline.ahead_count + pipeline.can_promote -> render_template(istoric=
  pipeline.history(conn))

promote_environment: pipeline.promote(source, target) [poate arunca
  PipelineError] -> pipeline.log_promotion(...) -> redirect
```

### 3q. Product API - session-based (folosit de SPA)

| Metoda | Path | Handler |
|---|---|---|
| GET | `/api/csrf-token` | `csrf_token_pentru_spa` |
| GET | `/api/me` | `me` |
| POST | `/api/onboarding/completat` | `onboarding_completat` |
| POST | `/api/logout` | `logout_api` |
| GET | `/api/clients` | `list_clients` |
| POST | `/api/clients` | `add_client` |
| DELETE | `/api/clients/<int:cid>` | `del_client` |
| POST | `/api/assignments` | `assign_client` |
| GET | `/api/sabloane/jurnal/<directie>` | `descarca_sablon_jurnal` |
| POST | `/api/reconciliations` | `new_reconciliation` |
| GET | `/api/reconciliations/<int:rid>` | `get_reconciliation` |
| GET | `/api/reconciliations/<int:rid>/export` | `export_report` |
| GET | `/api/audit` | `audit_view` |

Toate trec prin `require(perm=None)`: `current_identity()` -> 401 daca
None -> verifica `perm in ident["permissions"]` -> 403 daca nu -> apeleaza
handler-ul cu `ident` ca prim argument.

```
descarca_sablon_jurnal: require() -> valideaza directie (404 daca
  invalida) -> build_model_template(directie)  [etva/importer/model.py]
  -> Response (xlsx)

new_reconciliation (cel mai complex handler):
  require("reconciliere.creare") -> firm_conn(firm_id) ->
  1. daca firma nu e "direct": client_id obligatoriu din form
  2. period, format_jurnal (implicit "saga"), company_files (obligatoriu)
  3. sursa decont ANAF:
     - anaf_sursa=="auto": get_valid_anaf_access_token -> parseaza
       period -> anaf_oauth.fetch_decont(...) -> parse_p300_json_data(...)
     - altfel (upload): .json -> parse_p300_json(...); .pdf ->
       parse_p300_pdf(...)  [ambele in etva/importer/]
  4. daca anaf_doc is not None (mod D300 pe linii):
     - per fisier: _save_upload(f) ->
       - format_jurnal=="model": parse_model_journal(saved_path)
         [etva/importer/model.py] + overrides = identitate pe coduri
         D300_LINES + cod_mapping
       - altfel: parse_saga_journal(saved_path) [etva/importer/saga.py]
         + overrides = cod_mapping
     - classify_legend(direction, legend, overrides)  [etva/d300.py]
     - expand_derived_lines(company_lines)  [etva/d300.py]
     - _persist_lines(...) -> audit.log(..., "reconciliere.creare") ->
       jsonify(_result_payload_lines(...))
         [-> reconcile_d300(...) [etva/engine.py],
          suggest_d300_lines(...) [etva/advisor.py]]
  5. altfel daca format_jurnal=="model" (fara decont ANAF): eroare -
     modelul e-TVA merge doar cu decont ANAF (nu cu fisier ANAF xlsx/csv)
  6. altfel (mod clasic factura-cu-factura): parse_company_journal(...)
     [etva/importer/company.py] -> FileAnafDataSource(...).
     get_etva_data(...) [etva/importer/anaf.py] -> _persist(...) ->
     audit.log(...) -> jsonify(_result_payload(...))
       [-> reconcile(...) [etva/engine.py], suggest_d300(...)
        [etva/advisor.py]]

export_report: require("rapoarte.export") -> firm_conn -> reconciliations
  JOIN clients -> _reconciliation_mode(fc, rid) ->
  - "d300_lines": _load_lines x2 -> reconcile_d300 -> suggest_d300_lines
    -> export_mod.write_report_lines(...)  [etva/export.py]
  - "invoices": _load_rows x2 -> reconcile -> suggest_d300 ->
    export_mod.write_report(...)  [etva/export.py]
  -> audit.log(..., "raport.export") -> send_file(...)
```

---

## 4. Helper-e interne din create_app (portal/app.py)

Functii private (nu sunt rute), definite in interiorul `create_app()` si
folosite de mai multe handlere:

| Functie | Ce face |
|---|---|
| `_avatar_color(username)` | Culoare din paleta fixa, dupa suma codurilor de caractere |
| `_bar_pct(value, maximum)` | Procent rotunjit pentru bare grafice |
| `_donut_segments(counts)` | `dasharray`/`dashoffset` pentru grafic donut SVG |
| `_acquire_db_lock` / `_release_db_lock` | before/teardown request - serializare acces DB |
| `firm_conn(firm_id)` | Deschide/cacheaza conexiunea criptata a firmei |
| `current_user()` | Userul din `session["user_id"]` |
| `list_user_firms(user_id)` | Firmele active ale userului, cu rol |
| `_firma_testare_master()` | Creeaza/returneaza firma interna de test a masterului |
| `current_identity()` | Identitatea completa (rol/permisiuni/firma) pentru sesiune |
| `_log_master_action(user, actiune, detalii=None)` | INSERT `master_actions` |
| `require(perm=None)` | Decorator rute API: identitate + permisiune + injecteaza `ident` |
| `_anaf_lookup(cui)` / `_verify_cui_or_error(cui)` | Wrapper peste `anaf_cui.verify_cui`, traduce exceptii in mesaje |
| `_store_anaf_tokens(firm_id, tokens, username)` | Cripteaza + upsert tokenii OAuth ANAF |
| `get_valid_anaf_access_token(firm_id)` | Access token valid, refresh automat |
| `_zile_trial_ramase(trial_expira_la)` | Zile ramase pana la expirare |
| `_luni_pentru_ciclu(ciclu)` / `_pachete_extra_lunare(firm)` / `_calculeaza_suma_plata(firm, ciclu)` / `_suma_cu_tva(suma)` | Calcule de facturare/abonament |
| `_slugify(text)` / `_unique_username(desired)` | Normalizare username din nume firma |
| `_create_firm(...)` | Creeaza firma + `user_firms` + `firm_keys`; token verificare daca e cazul |
| `_parse_reconcilieri_estimate(form, tip)` | Valideaza estimarea lunara (firme "direct") |
| `_login_blocat` / `_inregistreaza_login_esuat` / `_reseteaza_login_esuat` | Lockout dupa esecuri repetate de login |
| `_role_in_firm(user_id, firm_id)` | Rolul userului in firma |
| `_contract_curent(firm_id)` | Cel mai recent contract al firmei |
| `_regenereaza_pdf_contract(contract)` | Reconstruieste PDF-ul contractului dupa metoda de semnatura |
| `_finalizeaza_contract_esemneaza` / `_actualizeaza_stare_esemneaza` | Polling + finalizare semnare eSemneaza.ro; `_finalizeaza_contract_esemneaza` trimite si un `_trimite_email` catre `invoicing.NOTIFICARE_CONTRACT_FINALIZAT_EMAIL` cand ambele parti au semnat |
| `_istoric_utilizator` / `_istoric_la_xml` / `_istoric_master` | Agregare istoric audit pentru afisare/export XML |
| `_anunt_activ()` | Anuntul activ curent (fereastra de timp) |
| `_trimite_email` / `_trimite_email_contact` / `_trimite_email_verificare` | SMTP; no-op daca `SMTP_HOST` nelipsit |
| `_suma_scurta(valoare)` | Formatare suma 2 zecimale |
| `_vml_firm_id()` / `_factura_proprie(factura_id, active_firm_id)` | Scopare facturi la firma emitenta/proprie |
| `_save_upload(f)` | Salveaza fisierul incarcat pe disc cu prefix aleator |
| `_persist` / `_result_payload` | Salvare + payload reconciliere "factura-cu-factura" |
| `_persist_lines` / `_result_payload_lines` | Salvare + payload reconciliere "linii D300" |
| `_load_rows` / `_load_lines` / `_reconciliation_mode` | Citire rezultate reconciliere existenta |

---

## 5. Pachetul etva/ - motor de business logic

### 5a. etva/d300.py (catalog linii D300 + clasificator)

| Functie | Apeluri |
|---|---|
| `with_mirrored_lines(lines)` | sintetizeaza partea "colectata" pt. taxare inversa - nu apeleaza altceva |
| `with_parent_rollups(lines)` | calculeaza linia parinte ca suma sub-liniilor - nu apeleaza altceva |
| `expand_derived_lines(lines)` | `-> with_mirrored_lines -> with_parent_rollups` (ordine fixa) |
| `suggest_line(direction, label)` | `-> _norm(label)` apoi reguli text (art. 307/331/294, cota %, cuvinte cheie) |
| `classify_legend(direction, legend, overrides)` | per cod: `overrides.get(cod)` sau `suggest_line(...)` |

### 5b. etva/engine.py (reconciliere)

```
reconcile(company_rows, anaf_rows) -> _totals(company_rows), _totals(anaf_rows)
  -> _group(company_rows), _group(anaf_rows) -> diff(...) intern

reconcile_d300(company_lines, anaf_lines) -> diff(...) intern (foloseste
  D300_LINES din d300.py) - NU foloseste _totals/_group (specifice
  invoice-level)
```

### 5c. etva/advisor.py (sugestii)

```
suggest_d300_lines(result) -> _line_sort_key (sortare linii tip "14+15"/"5.1")
suggest_d300(result) -> sorteaza categoriile ca stringuri (fara _line_sort_key)
```

### 5d. etva/importer/saga.py (parser SAGA)

```
parse_saga_journal(path):
  1. _read_raw(path)
  2. _detect_direction(df)     -> _norm, _cell
  3. _company_identity(df)     -> _cell
  4. _find_header_row(df)      -> _norm, _cell
  5. _find_columns(df, header_row) -> _norm, _cell
  6. bucla intrari: _cell (per coloana), _date_str, _num
  7. bucla legenda (dupa stop_row): _cell, _num
```

### 5e. etva/importer/model.py (parser + generator model e-TVA)

Reutilizeaza helper-ele SAGA direct (`_norm`, `_num`, `_cell`, `_date_str`,
`SagaJournal` - importate din `.saga`, nu redefinite):

```
parse_model_journal(path):
  1. _read_raw(path)                          [definitie locala, proprie]
  2. _detect_direction(df)                    -> _norm/_cell (din saga)
  3. _find_header_row(df)                     -> _norm/_cell (din saga)
  4. _find_columns(df, header_row)             -> _norm/_cell (din saga)
  5. bucla randuri: _cell, _num, _date_str, _norm (din saga, pt. a mapa
     eticheta pe linia D300 din TIPURI_OPERATIUNE)
  6. construieste SagaJournal(...) (din saga)

build_model_template(directie) - independenta, nu apeleaza nimic din
  restul modulului; genereaza direct Workbook-ul openpyxl.
```

### 5f. etva/importer/anaf_p300.py (parser PDF decont real)

```
parse_p300_pdf(path) -> _group_lines(words) [per pagina] ->
  parse_p300_rows(pages_rows) -> _find_columns(rows) [per pagina] ->
  _to_number(text) [per cifra gasita]
```

### 5g. etva/importer/anaf_p300_json.py (parser JSON decont)

```
parse_p300_json(path) -> json.load -> parse_p300_json_data(data) ->
  _line_no(g1, g2) [per camp RD{n}[_{m}]_(VAL|TVA)], foloseste D300_LINES
```

### 5h. etva/importer/company.py & anaf.py (formate generice)

```
parse_company_journal(path) -> _read(path) -> rows_from_dataframe(df)
FileAnafDataSource.get_etva_data(cui, period) -> pd.read_csv/read_excel
  -> df.rename(...) -> rows_from_dataframe(df)   [convergenta pe aceeasi
     functie, cai diferite de citire]
```

### 5i. etva/anaf_cui.py (verificare CUI la ANAF)

```
verify_cui(cui, on_date=None) -> normalize_cui(cui) -> _fetch(numeric_cui, day)
```

### 5j. etva/anaf_oauth.py (OAuth2 ANAF + RO e-Factura)

```
exchange_code_for_tokens / refresh_access_token -> _token_request ->
  _basic_auth_header
fetch_decont(access_token, cui, an, luna) -> _extract_decont_json(raw)
  (rezultatul e trecut la parse_p300_json_data)
upload_invoice / check_upload_status -> _parse_header_response(raw)
download_response -> nimic intern (HTTP GET direct)
build_authorize_url(...) -> (extern, doar redirect)
```

### 5k. etva/efactura_xml.py (generare XML UBL factura)

```
build_invoice_xml(invoice, furnizor) -> _party("AccountingSupplierParty", ...)
  -> _party("AccountingCustomerParty", ...) -> (fiecare) _cac, _cbc
```

### 5l. etva/esemneaza.py (client eSemneaza.ro)

```
upload_document / create_sign_request / get_sign_request /
cancel_sign_request / get_completed_document_url /
get_certificate_download_url
  -> _auth_headers(api_key) -> _call(req) -> _json(raw)
fetch_url_bytes(url) -> doar _call(req) (fara auth header, fara _json -
  returneaza bytes brut)
```

### 5m. etva/digital_signature.py (verificare semnatura PDF)

```
verifica_semnatura_pdf(pdf_bytes) -> _incarca_ancore_incredere() ->
  pyhanko.sign.validation.validate_pdf_signature (biblioteca externa)
```

### 5n. etva/audit.py, etva/export.py, etva/pg.py

Fara apeluri interne intre propriile functii - seturi de functii-frunza:
- `audit.py`: `log(conn, ...)`, `entries(conn, ...)`.
- `export.py`: `write_report(...)`, `write_report_lines(...)` (independente).
- `pg.py`: `dsn_from_env()`, `connect(dsn)`, `verify_schema(conn)` (apelate secvential din `migrare_pg.py`, nu una din alta).

### 5o. etva/clients.py

`create_client` -> `dbcompat.insert_id`. `assign`, `visible_clients`, `delete_client` -> doar `conn.execute` direct.

### 5p. etva/db.py (schema SQLCipher per-firma)

```
init_schema(conn) -> conn.executescript(_SCHEMA) ->
  _migrate_reconciliations_nullable_client(conn) ->
  _migrate_add_clients_gdpr(conn) -> conn.commit()
```
(`open_db` deschide fisierul; `init_schema` se apeleaza separat, dupa, din `portal/app.py`.)

### 5q. etva/dbcompat.py (adaptor Postgres, cand `ETVA_DB=postgres`)

```
FirmScopedConnection.execute(sql, params) ->
  ConnCompat._seteaza_firma(firm_id)  [SET app.firm_id, fara cache -
    "rollback-ul anula scope-ul", vezi commit istoric] ->
  ConnCompat.execute(sql, params) -> _tradu(sql) [daca exista params] ->
  CursorCompat(cur) -> fetchone/fetchall -> _rand(row) ->
  _normalizeaza(valoare) [per coloana]

insert_id(conn, sql, params) -> RETURNING id (Postgres) sau .lastrowid (sqlite3)
```

**Observatii structurale**: `etva/d300.py` e un hub - importat de
`advisor.py`, `engine.py` (doar `D300_LINES`) si
`importer/anaf_p300_json.py` (doar `D300_LINES`), dar `suggest_line`/
`classify_legend` sunt apelate exclusiv din `portal/app.py`.
`importer/model.py` traverseaza intotdeauna `importer/saga.py` (helper-e
reutilizate), chiar si cand utilizatorul foloseste modelul e-TVA, nu SAGA.

---

## 6. Module suport portal/ - fara app.py

### 6.1 portal/db.py (838 linii) - schema portalului + migrari

`open_db(path)` ruleaza, in ordine, la fiecare pornire (idempotent - fiecare migrare isi verifica singura precondita inainte sa actioneze):

```
sqlite3.connect
-> _migrate_legacy_users            (users.firm_id/role vechi -> user_firms)
-> _migrate_add_firm_tip            (firms.tip)
-> _migrate_add_onboarding_flag     (users.onboarding_completat)
-> _migrate_setari_tva_istoric      (setari_tva: rand fix -> istoric+activa)
-> conn.executescript(_SCHEMA)
-> _migrate_firms_autoincrement     (firms cu AUTOINCREMENT)
-> _migrate_add_efactura_columns    (invoices.anaf_*)
-> _migrate_add_users_email
-> _migrate_add_firms_verificare_trial   (email_verificat, trial_expira_la, ...)
-> _migrate_add_firms_trial_reminder     (trial_reminder_ultim_prag)
-> _migrate_add_firms_arhivare           (arhivata_la)
-> _migrate_add_firms_reconcilieri_estimate
-> _migrate_seed_planuri_facturare       (seed daca gol)
-> _migrate_contracts_fara_pdf           (recreeaza contracts fara blob-uri)
-> _migrate_add_contracts_esemneaza
-> _migrate_add_contract_prestator_semnare
-> _migrate_seed_cota_tva                (seed daca gol)
-> _migrate_seed_pachet_reconcilieri     (seed daca gol)
-> conn.commit()
```

Pe Postgres: `_open_db_postgres()` -> `dbcompat.connect(dsn)` ->
`pg.verify_schema(...)` (ridica `RuntimeError` daca schema difera - NU
creeaza schema, doar verifica) -> seed-urile idempotente (planuri,
cota TVA, pachet reconcilieri).

Functii publice business (apelate din `app.py`): `get_pachet_reconcilieri`,
`set_pachet_reconcilieri`, `get_cota_tva`, `listeaza_cote_tva`,
`set_cota_tva`, `activeaza_cota_tva`, `get_preturi`, `set_pret`.

### 6.2 portal/security.py (Argon2 + Fernet)

`hash_password` / `verify_password` (Argon2id). `load_secret` (citeste sau
genereaza cheie Fernet). `wrap_key`/`unwrap_key` (criptare cheie de date a
firmei cu Fernet) - folosite la `_create_firm`/`firm_conn`/tokenii ANAF
OAuth/`migrare_pg.py`.

### 6.3 portal/pdf_fonts.py

`asigura_fonturi()` - inregistreaza fonturi Noto Sans in reportlab
(diacritice RO), idempotent prin flag de modul. Prim apel in
`invoicing.py::generate_pdf` si `contract.py::genereaza_pdf`.

### 6.4 portal/pipeline.py (promovare dev->testare->productie prin git)

La import: `STARTED_AT = _capture_started_commit()` (o singura data).
Restul, toate la cerere din `/master/pipeline*`:

```
promote(source_env, target_env):
  _repo_paths() -> _is_clean -> can_promote
    [-> subprocess git merge-base --is-ancestor]
  -> subprocess git merge --ff-only -> _git(rev-parse --short HEAD)
  -> subprocess git push origin

log_promotion(conn, source, target, commit, username) -> INSERT
  pipeline_log -> commit
```

`request_server_restart(data_dir)` - scrie fisier trigger `restart.trigger`
pe care un unit systemd extern il asteapta pentru restart efectiv (nu
reporneste procesul singur).

### 6.5 portal/invoicing.py

`next_invoice_number(conn, serie)` (sub `db_lock`). `generate_pdf(invoice)`
-> `pdf_fonts.asigura_fonturi()` -> `SimpleDocTemplate`/`Table`/`Paragraph`
(reportlab) -> `_suma()`. Constanta `FURNIZOR` reutilizata de `contract.py`.

Constanta `NOTIFICARE_CONTRACT_FINALIZAT_EMAIL` (adaugata 2026-08-04) -
destinatarul emailului trimis de `_finalizeaza_contract_esemneaza`
(`portal/app.py`) cand un contract e semnat de ambele parti prin
eSemneaza - separata intentionat de `FURNIZOR['email']` (folosit pentru
cererea initiala de semnatura), ca sa nu depinda de cine verifica inboxul
de office in momentul respectiv. **De verificat**: valoarea curenta pare
o adresa personala, nu una de firma - posibil ramasa dintr-un test.

### 6.6 portal/contract.py

```
Creare + descarcare contract (ordine tipica vazuta in app.py):
date_beneficiar(cui) [-> anaf_cui.verify_cui, ridica ContractError la eroare]
-> next_contract_number(conn)
-> INSERT contracts (in app.py)
-> genereaza_text_din_rand(row) [-> genereaza_text(...)]
-> genereaza_pdf(continut, ...) sau date_contract_xml(row)
```

`nota_verificare_certificat` - text explicativ care inlocuieste fisierul
original semnat (nepastrat).

### 6.7 portal/backup.py

| Functie | Context de apel |
|---|---|
| `create_backup(data_dir)` | LA CERERE (`/master/backup/creeaza`) **si** din scheduler |
| `validate_backup_zip(fisier)` | LA CERERE, inainte de restore |
| `restore_backup(data_dir, fisier)` | LA CERERE (`/master/backup/restaureaza`) |
| `list_backups(data_dir)` | LA CERERE (afisare panou) + intern (`_seconds_until_due`) |
| `prune_old_backups(data_dir, keep=20)` | LA CERERE (dupa creare manuala) **si** din scheduler |
| `backup_path(data_dir, nume)` | LA CERERE (descarcare) |
| `_seconds_until_due(data_dir)` | intern, doar din `start_scheduler` |
| `start_scheduler(data_dir, lock)` | **punct de intrare thread** - vezi §7 |

### 6.8 portal/trial_reminders.py

| Functie | Context de apel |
|---|---|
| `zile_ramase_trial(trial_expira_la)` | LA CERERE (listare panou) + intern |
| `verifica_si_trimite(conn, trimite_email_fn)` | THREAD **si** LA CERERE (`/master/remindere-trial/trimite`) |
| `arhiveaza_firme_neplatitoare(conn)` | THREAD **si** LA CERERE (`/master/remindere-trial/arhiveaza`) |
| `start_scheduler(conn, lock, trimite_email_fn)` | **punct de intrare thread** - vezi §7 |

### 6.9 portal/migrare_pg.py (324 linii) - script CLI, o singura data per mediu

```
main(): citeste DATABASE_URL (obligatoriu) -> data_dir() ->
  --dry-run: raport_migrare(...) + print JSON
  altfel:    migreaza(data_dir_path, dsn) + print rezumat

migreaza(data_dir_path, dsn):
  1. sqlite3.connect(portal.db)
  2. psec.load_secret(secret.key)
  3. psycopg.connect(dsn, row_factory=dict_row)
  4. in tranzactie Postgres unica (fara commit intermediar):
     a. daca firms SAU users are deja randuri -> SystemExit (nimic modificat)
     b. DELETE FROM planuri_facturare, DELETE FROM setari_tva (curata seed-uri)
     c. per tabel din _TABELE_PORTAL (ordine care respecta FK):
        _copiaza_tabel(...)
     d. per firma (ordonate dupa id): daca lipseste firm_keys/fisier ->
        sarita; altfel fdb.open_db(cale, psec.unwrap_key(...)) ->
        _migreaza_firma(cur, fid, fc) -> fc.close()
     e. per tabel cu id: SELECT setval(...) (reseteaza secventele peste MAX(id))
     f. pgconn.commit() -> return rezumat
  5. except Exception: pgconn.rollback() -> raise
  6. finally: pgconn.close(), sconn.close()  (SQLite-ul original NU e
     NICIODATA atins/modificat, indiferent de succes/esec)

_migreaza_firma(pg_cur, firm_id, fc) - ordine interna:
  1. set_config('app.firm_id', firm_id)   [izolare RLS]
  2. clients -> harta_clienti
  3. client_assignments (sare orfanele)
  4. reconciliations -> harta_rec
  5. invoices_company, invoices_anaf (folosesc harta_rec, sar orfanele)
  6. differences (foloseste harta_rec)
  7. audit_log (fara remapare - istoric neschimbat)
```

`raport_migrare` (dry-run, strict read-only): acelasi parcurs de citire,
fara nicio scriere - sigur de rulat direct pe testare/productie ca
pre-verificare.

---

## 7. Fire de fundal (scheduler-e)

Ambele pornite din `create_app()` (§1d), primesc **acelasi** `db_lock`
(`threading.RLock()`) - request-urile HTTP si thread-urile de fundal se
serializeaza reciproc pe aceeasi conexiune.

| Modul | Pornire | Bucla (`_loop`, functie interna) | Interval |
|---|---|---|---|
| `backup.py` | `start_scheduler(data_dir, lock)` | `sleep(_seconds_until_due(...))` -> `with lock: create_backup(data_dir)` -> `prune_old_backups(data_dir)` -> la exceptie: `traceback.print_exc()` + `sleep(3600)` | 3 zile (calculat de la ultimul backup **de pe disc**, nu de la pornirea procesului); retry 1h |
| `trial_reminders.py` | `start_scheduler(conn, lock, trimite_email_fn)` | verifica **imediat** la pornire (fara sleep initial): `with lock: verifica_si_trimite(...)` -> `arhiveaza_firme_neplatitoare(...)` -> `sleep(6*3600)`; la exceptie: `traceback.print_exc()` + `sleep(1800)` | 6 ore; retry 30 min |

`pipeline.py`, `contract.py`, `invoicing.py`, `migrare_pg.py`,
`seed_master.py` - **fara scheduler**, totul strict la cerere (rute HTTP)
sau script CLI separat.

---

## 8. Fluxuri end-to-end cheie

### 8a. Inregistrare firma noua (frontend nu e implicat - pagina server-side)

```
GET /inregistrare (Jinja) -> completare formular ->
POST /inregistrare
  -> register() [portal/app.py]
     -> validari campuri
     -> _verify_cui_or_error -> anaf_cui.verify_cui   [apel real la ANAF]
     -> psec.hash_password -> INSERT users
     -> _create_firm -> psec.wrap_key (cheia de date a firmei)
     -> _trimite_email_verificare -> _trimite_email    [SMTP real]
     -> redirect /app
GET /app -> aplicatie() -> daca EMAIL_VERIFICARE_OBLIGATORIE si
  neverificat: redirect /asteapta-verificare-email
[click link din email] GET /verifica-email/<token> -> verifica_email()
  -> UPDATE firms SET email_verificat=TRUE -> redirect /autentificare
```

### 8b. Reconciliere cu modelul e-TVA (fluxul nou, format_jurnal=model)

```
Frontend: alege radio "Alt program" -> comutaFormatJurnal() (arata panou)
  -> click "Descarca modelul de vanzari/cumparari"
    -> GET /api/sabloane/jurnal/<directie> -> descarca_sablon_jurnal()
       -> build_model_template(directie) [etva/importer/model.py]
  -> utilizatorul completeaza xlsx-ul (dropdown Tip operatiune) ->
     incarca fisierul(ele) + decont ANAF (PDF/JSON) -> click
     "Ruleaza reconcilierea"
    -> ruleazaReconciliere() -> FormData cu format_jurnal='model' ->
       POST /api/reconciliations
      -> new_reconciliation() [portal/app.py, vezi §3q pentru detaliu]
         -> parse_model_journal (per fisier) [etva/importer/model.py]
         -> classify_legend + expand_derived_lines [etva/d300.py]
         -> reconcile_d300 [etva/engine.py]
         -> suggest_d300_lines [etva/advisor.py]
      -> jsonify(rezultat)
  -> afiseazaRezultate(body) -> navigheaza('rezultate')
```

### 8c. Migrare SQLite -> Postgres (o singura data, per mediu - testare a rulat deja)

```
[pre-check, oricand, fara risc] python -m portal.migrare_pg --dry-run
  -> raport_migrare(...) -> print JSON (gata_de_migrare: true/false)

[migrarea efectiva, o singura data] python -m portal.migrare_pg
  -> migreaza(...) [vezi §6.9 pentru ordinea exacta pe tabele/firme]
  -> [manual, separat] editeaza /etc/etva-{mediu}/db.env:
     ETVA_DB=postgres, DATABASE_URL=...
  -> systemctl restart etva-{mediu}.service
  -> la urmatoarea pornire: create_app() -> pdb.open_db() ->
     dbcompat.backend()=="postgres" -> _open_db_postgres() ->
     pg.verify_schema(...)
```

### 8d. Backup automat (fond, fara interactiune umana)

```
create_app(enable_backup_scheduler=True)
  -> backup_mod.start_scheduler(data_dir, db_lock)
     -> thread daemon: _loop()
        -> sleep(_seconds_until_due(data_dir))   [~3 zile de la ultimul backup]
        -> with db_lock:
             create_backup(data_dir)     [zip peste tot data_dir, exclus backups/]
           prune_old_backups(data_dir)   [pastreaza cele mai recente 20]
        -> (bucla la infinit; la eroare: log + retry in 1h)
```
