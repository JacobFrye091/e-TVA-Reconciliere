# Contract eSemneaza cu trimitere controlată de master — Plan de implementare

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Master trimite contractul spre semnare (nu firma), cu doi semnatari reali în ordine
(PRESTATOR apoi BENEFICIAR, ambii `one_click_sign`), și un instantaneu XML înghețat la finalizare.

**Architecture:** Flask monolith existent (`portal/app.py`). Adaugă 2 rute noi
(`GET`/`POST /master/contracte/creeaza/<firm_id>`), 2 coloane noi pe `contracts`, extinde
`etva/esemneaza.py::create_sign_request` cu opțiuni per-recipient, unifică logica de verificare
stare (polling + webhook) într-un singur helper.

**Tech Stack:** Python 3.14, Flask, SQLite (stdlib `sqlite3`), pytest. Fără dependențe noi.

## Global Constraints

- Spec: `planning/specs/2026-07-28-contract-esemneaza-admin-review-design.md` — orice ambiguitate
  se rezolvă conform lui, nu prin presupuneri noi.
- Metoda `certificat` de semnare rămâne complet neatinsă (nicio linie de cod din ramura ei nu se
  modifică).
- Nicio schimbare la `FGO`/Netopia — rămâne manual.
- `ESEMNEAZA_API_KEY` și secretul webhook-ului se configurează manual pe VPS (nu trec prin git/chat).
- Testele rulează cu `python -m pytest -q` din `C:\Users\vasil\Downloads\e-TVA-Reconciliere\DEV`;
  suita completă trebuie verde înainte de orice promovare dev→testare→producție.
- Commit-uri frecvente, câte unul per task de mai jos.

---

### Task 1: `etva/esemneaza.py` — suport `options` per destinatar (one_click_sign)

**Files:**
- Modify: `etva/esemneaza.py:105-150` (`create_sign_request`)
- Test: `tests/test_esemneaza.py`

**Interfaces:**
- Produces: `create_sign_request(api_key, file_name, recipients, sender_name=None,
  sign_in_order=False, extract_tags=False)` — fiecare element din `recipients` poate avea acum
  cheia opțională `"options"` (listă de string-uri, ex. `["one_click_sign"]"`), transmisă mai
  departe către eSemneaza ca `recipient["options"]`.

- [ ] **Step 1: Scrie testul care eșuează**

Adaugă în `tests/test_esemneaza.py`, după `test_create_sign_request_supports_multiple_recipients_with_custom_position`:

```python
def test_create_sign_request_passes_recipient_options(monkeypatch):
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.data)
        return json.dumps({"id": "req-4"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    esemneaza.create_sign_request(
        "key-123", "contract-4.pdf",
        recipients=[
            {"email": "a@exemplu.ro", "name": "A", "options": ["one_click_sign"]},
            {"email": "b@exemplu.ro", "name": "B", "options": ["one_click_sign"]},
        ],
        extract_tags=True, sign_in_order=True)

    recipients = captured["body"]["recipients"]
    assert recipients[0]["options"] == ["one_click_sign"]
    assert recipients[1]["options"] == ["one_click_sign"]


def test_create_sign_request_omits_options_when_not_given(monkeypatch):
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.data)
        return json.dumps({"id": "req-5"}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    esemneaza.create_sign_request(
        "key-123", "contract-5.pdf",
        recipients=[{"email": "a@exemplu.ro", "name": "A", "field_page": 1}])

    assert "options" not in captured["body"]["recipients"][0]
```

- [ ] **Step 2: Rulează testul, confirmă eșecul**

Run: `cd "C:\Users\vasil\Downloads\e-TVA-Reconciliere\DEV" && python -m pytest tests/test_esemneaza.py::test_create_sign_request_passes_recipient_options -v`
Expected: FAIL — `KeyError: 'options'` sau `assert None == [...]`

- [ ] **Step 3: Implementează**

În `etva/esemneaza.py`, în interiorul buclei `for r in recipients:` din `create_sign_request`
(linia ~131-139), înlocuiește:

```python
    body_recipients = []
    for r in recipients:
        recipient = {"type": "EMAIL", "email": r["email"], "name": r["name"]}
        if not extract_tags:
            recipient["fields"] = [{
                "x": r.get("field_x", 300), "y": r.get("field_y", 60),
                "width": 180, "height": 50, "pageNum": r["field_page"],
                "type": "SIGNATURE", "required": True,
            }]
        body_recipients.append(recipient)
```

cu:

```python
    body_recipients = []
    for r in recipients:
        recipient = {"type": "EMAIL", "email": r["email"], "name": r["name"]}
        if not extract_tags:
            recipient["fields"] = [{
                "x": r.get("field_x", 300), "y": r.get("field_y", 60),
                "width": 180, "height": 50, "pageNum": r["field_page"],
                "type": "SIGNATURE", "required": True,
            }]
        if r.get("options"):
            recipient["options"] = r["options"]
        body_recipients.append(recipient)
```

- [ ] **Step 4: Rulează testele, confirmă succesul**

Run: `python -m pytest tests/test_esemneaza.py -v`
Expected: toate testele din fișier PASS (inclusiv cele 2 noi).

- [ ] **Step 5: Commit**

```bash
git add etva/esemneaza.py tests/test_esemneaza.py
git commit -m "esemneaza: support per-recipient options (one_click_sign)"
```

---

### Task 2: `portal/db.py` — coloane noi pe `contracts`

**Files:**
- Modify: `portal/db.py:156-178` (schema `contracts`), lângă `_migrate_add_esemneaza_columns`
  (linia ~483-498), și lista de migrări apelate din `open_db`
- Test: `tests/test_portal.py` (schema/migrările lui `portal/db.py` se testează aici, nu în
  `tests/test_db.py` — acela testează `etva/db.py`, modulul separat pentru bazele SQLCipher
  per-firmă, confirmat prin căutare: singurele teste care ating `esemneaza_request_id`/coloanele
  `contracts` sunt în `test_portal.py`)

**Interfaces:**
- Produces: coloanele `contracts.prestator_semnat_la` (TEXT, nullable) și
  `contracts.contract_xml_final` (BLOB, nullable), disponibile pe orice `sqlite3.Row` din
  `contracts` — folosite de Task 5.

- [ ] **Step 1: Scrie testul care eșuează**

Adaugă în `tests/test_portal.py`, oriunde în fișier (import-urile din acest proiect se fac local,
nu doar la începutul fișierului - vezi `from portal import pipeline as pl` la linia 912):

```python
def test_migrate_add_contract_prestator_semnare_adds_columns(tmp_path):
    import sqlite3
    from portal import db as pdb
    path = str(tmp_path / "old_portal.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE contracts(id INTEGER PRIMARY KEY, firm_id INTEGER, "
        "numar INTEGER, ciclu_facturare TEXT, suma REAL, "
        "beneficiar_denumire TEXT, beneficiar_cui TEXT, beneficiar_adresa TEXT, "
        "stare TEXT, creat_la TEXT, esemneaza_request_id TEXT, "
        "esemneaza_document_pdf BLOB, esemneaza_certificate_pdf BLOB)")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    cols = {r["name"] for r in reopened.execute("PRAGMA table_info(contracts)")}
    assert "prestator_semnat_la" in cols
    assert "contract_xml_final" in cols
```

- [ ] **Step 2: Rulează testul, confirmă eșecul**

Run: `python -m pytest tests/test_portal.py::test_migrate_add_contract_prestator_semnare_adds_columns -v`
Expected: FAIL — `AssertionError` (coloana nu există încă)

- [ ] **Step 3: Implementează schema pentru instalări noi**

În `portal/db.py`, în interiorul `CREATE TABLE IF NOT EXISTS contracts(...)` (linia ~156-178),
înlocuiește linia finală:

```python
  esemneaza_request_id TEXT,
  esemneaza_document_pdf BLOB,
  esemneaza_certificate_pdf BLOB);
```

cu:

```python
  esemneaza_request_id TEXT,
  esemneaza_document_pdf BLOB,
  esemneaza_certificate_pdf BLOB,
  prestator_semnat_la TEXT,
  contract_xml_final BLOB);
```

- [ ] **Step 4: Implementează migrarea pentru baze existente**

În `portal/db.py`, imediat după funcția `_migrate_add_esemneaza_columns` (linia ~498), adaugă:

```python
def _migrate_add_contract_prestator_semnare(conn: sqlite3.Connection) -> None:
    """Adauga urmarirea separata a semnaturii PRESTATORULUI (recipient 1 la
    eSemneaza, ordine impusa prin signInOrder) si instantaneul XML inghetat
    la finalizare - vezi planning/specs/2026-07-28-contract-esemneaza-admin-
    review-design.md."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "contracts" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(contracts)")}
    if "prestator_semnat_la" in cols:
        return
    conn.execute("ALTER TABLE contracts ADD COLUMN prestator_semnat_la TEXT;")
    conn.execute("ALTER TABLE contracts ADD COLUMN contract_xml_final BLOB;")
    conn.commit()
```

Apoi, în `open_db` (linia ~689-699), găsește apelul existent `_migrate_add_esemneaza_columns(conn)`
(dacă e acolo — altfel apelurile de migrare sunt grupate lângă `_migrate_legacy_users`/
`_migrate_add_firm_tip`/`_migrate_add_onboarding_flag`) și adaugă imediat după el:

```python
    _migrate_add_contract_prestator_semnare(conn)
```

- [ ] **Step 5: Rulează testele, confirmă succesul**

Run: `python -m pytest tests/test_portal.py::test_migrate_add_contract_prestator_semnare_adds_columns -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add portal/db.py tests/test_portal.py
git commit -m "db: add contracts.prestator_semnat_la and contract_xml_final"
```

---

### Task 3: `portal/contract.py` — text/PDF pentru semnătură reală dublă

**Files:**
- Modify: `portal/contract.py:77-165` (`genereaza_text`), `portal/contract.py:242-314`
  (`genereaza_pdf`)
- Create: `tests/test_contract.py` (nu există încă în repo - fișier nou, lângă `tests/test_esemneaza.py`)
- Modify: `tests/test_portal.py:3157-3175` (`test_genereaza_pdf_embeds_esemneaza_signature_tag` -
  test existent, verifică deja `{{s:1}}` via `pdfplumber`; trebuie extins pentru al doilea tag,
  nu duplicat într-un test nou și mai slab)

**Interfaces:**
- Consumes: `pdfplumber` (deja o dependință a proiectului - `requirements.txt`, folosită deja în
  `tests/test_portal.py:3161` pentru exact acest scop).
- Produces: `genereaza_text(...)` nu mai include propoziția "PRESTATORUL a semnat electronic...";
  `genereaza_pdf(..., tag_semnatura_esemneaza=True)` inserează `{{s:1}}` lângă PRESTATOR și
  `{{s:2}}` lângă BENEFICIAR (nu doar `{{s:1}}` lângă BENEFICIAR, ca acum).

- [ ] **Step 1: Scrie testul care eșuează pentru text**

Creează `tests/test_contract.py`:

```python
from datetime import datetime, timezone

from portal import contract


def test_genereaza_text_does_not_assert_prestator_signature():
    beneficiar = {"denumire": "Firma Test SRL", "cui": "RO123", "adresa": "Str. Test 1"}
    text = contract.genereaza_text(
        1, beneficiar, "lunar", 100.0, datetime(2026, 7, 28, tzinfo=timezone.utc))
    assert "a semnat electronic" not in text
    assert "PRESTATOR: VML EXPERT ADVISOR SRL" in text
```

- [ ] **Step 2: Actualizează testul existent pentru al doilea tag**

În `tests/test_portal.py`, funcția `test_genereaza_pdf_embeds_esemneaza_signature_tag`
(linia ~3157-3175) verifică azi doar `{{s:1}}` (lângă BENEFICIAR, în forma veche). Înlocuiește
integral funcția cu:

```python
def test_genereaza_pdf_embeds_esemneaza_signature_tag(app):
    """Confirmat empiric impotriva eSemneaza real (2026-07-27): un PDF cu
    "{{s:1}}" in text, incarcat cu extractTags=True, produce singur un camp
    de semnatura cu pozitie corecta - nu mai trebuie ghicite coordonate.
    Acum ambele parti semneaza real (vezi planning/specs/2026-07-28-
    contract-esemneaza-admin-review-design.md), deci ambele tag-uri trebuie
    prezente: {{s:1}} langa PRESTATOR, {{s:2}} langa BENEFICIAR."""
    import pdfplumber
    from portal import contract as contract_mod
    from datetime import datetime, timezone
    beneficiar = {"denumire": "Firma Test SRL", "cui": "RO999",
                 "adresa": "Adresa test"}
    continut = contract_mod.genereaza_text(
        1, beneficiar, "lunar", 59.0, datetime.now(timezone.utc))
    pdf_bytes = contract_mod.genereaza_pdf(continut, tag_semnatura_esemneaza=True)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = pdf.pages[-1].extract_text()
    assert "{{s:1}}" in text
    assert "{{s:2}}" in text
    pdf_fara_tag = contract_mod.genereaza_pdf(continut)
    with pdfplumber.open(io.BytesIO(pdf_fara_tag)) as pdf:
        text_fara_tag = pdf.pages[-1].extract_text()
    assert "{{s:1}}" not in text_fara_tag
    assert "{{s:2}}" not in text_fara_tag
```

(`import io` există deja la linia 989 din `tests/test_portal.py`, deci `io.BytesIO` e deja
disponibil ca nume global în fișier - nu adăuga alt import.)

- [ ] **Step 3: Rulează testele, confirmă eșecul**

Run: `python -m pytest tests/test_contract.py tests/test_portal.py::test_genereaza_pdf_embeds_esemneaza_signature_tag -v`
Expected: `test_genereaza_text_does_not_assert_prestator_signature` FAIL (textul conține încă
"a semnat electronic"); `test_genereaza_pdf_embeds_esemneaza_signature_tag` FAIL la
`assert "{{s:2}}" in text` (al doilea tag nu există încă).

- [ ] **Step 4: Implementează în `genereaza_text`**

În `portal/contract.py`, în funcția `genereaza_text` (linia ~154-164), înlocuiește:

```python
10.2. PRESTATORUL a semnat electronic prezentul contract la data emiterii \
lui, {azi}. Contractul se consideră încheiat la data la care BENEFICIARUL \
îl semnează.


PRESTATOR: {FURNIZOR['nume']} (semnat electronic la {azi})     BENEFICIAR: {beneficiar_anaf['denumire']}
"""
```

cu:

```python
10.2. Prezentul contract se consideră încheiat la data la care ambele \
părți l-au semnat electronic.


PRESTATOR: {FURNIZOR['nume']}     BENEFICIAR: {beneficiar_anaf['denumire']}
"""
```

- [ ] **Step 5: Implementează în `genereaza_pdf`**

În `portal/contract.py`, în funcția `genereaza_pdf`, în ramura `elif bloc.startswith("PRESTATOR:")`
(linia ~283-300), înlocuiește:

```python
            stanga, dreapta = re.split(r"\s{2,}", bloc, maxsplit=1)
            if tag_semnatura_esemneaza:
                dreapta += ' <font color="white">{{s:1}}</font>'
```

cu:

```python
            stanga, dreapta = re.split(r"\s{2,}", bloc, maxsplit=1)
            if tag_semnatura_esemneaza:
                stanga += ' <font color="white">{{s:1}}</font>'
                dreapta += ' <font color="white">{{s:2}}</font>'
```

Actualizează și docstring-ul parametrului `tag_semnatura_esemneaza` (menționează azi doar
`{{s:1}}`/BENEFICIAR) ca să reflecte ambele tag-uri - înlocuiește paragraful care începe cu
`tag_semnatura_esemneaza=True adauga tag-ul` cu:

```python
    tag_semnatura_esemneaza=True adauga doua tag-uri invizibile (font alb):
    `{{s:1}}` langa PRESTATOR si `{{s:2}}` langa BENEFICIAR - eSemneaza le
    detecteaza automat cu extractTags=True (vezi etva/esemneaza.py) si
    genereaza campurile de semnatura, cu pozitia calculata din locul
    tag-urilor in text, cate unul pentru fiecare semnatar in ordine
    (signInOrder=True: semnatarul 1 = PRESTATOR, semnatarul 2 = BENEFICIAR).
```

- [ ] **Step 6: Rulează testele, confirmă succesul**

Run: `python -m pytest tests/test_contract.py tests/test_portal.py::test_genereaza_pdf_embeds_esemneaza_signature_tag -v`
Expected: toate PASS.

- [ ] **Step 7: Rulează suita completă (verificare de regresie pe generarea textului)**

Run: `python -m pytest -q`
Expected: toate PASS (verifică niciun test existent nu depindea de textul vechi).

- [ ] **Step 8: Commit**

```bash
git add portal/contract.py tests/test_contract.py tests/test_portal.py
git commit -m "contract: real dual signature (drop textual prestator assertion, tag both signers)"
```

---

### Task 4: `portal/app.py` + `portal/db.py` — creare/trimitere controlată de master

**Files:**
- Modify: `portal/app.py` (adaugă rute noi lângă `master_contracte`, linia ~2336-2348; șterge
  `_genereaza_contract`, linia ~918-943)
- Create: `portal/templates/master_contract_creeaza.html`
- Modify: `portal/templates/master_plati.html:45-53` (link nou)
- Test: `tests/test_portal.py`

**Interfaces:**
- Consumes: `contract_mod.date_beneficiar(cui)` (existent), `contract_mod.next_contract_number(conn)`
  (existent), `contract_mod.genereaza_text_din_rand(contract)` (existent),
  `contract_mod.genereaza_pdf(text, tag_semnatura_esemneaza=True)` (Task 3),
  `esemneaza.upload_document`/`create_sign_request` (Task 1), `_calculeaza_suma_plata(firm, ciclu)`
  (existent în `app.py`), `FURNIZOR` din `portal/invoicing.py` (existent: `nume`, `email`).
- Produces: rutele `GET`/`POST /master/contracte/creeaza/<int:firm_id>`, endpoint Flask
  `creeaza_contract_master`/`trimite_contract_master`.

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă în `tests/test_portal.py`, lângă `test_master_contracte_requires_master` (dacă există) sau
lângă alte teste de `/master/contracte`:

```python
def _creeaza_master(app):
    conn = app.portal_conn
    if conn.execute("SELECT 1 FROM users WHERE is_master=1").fetchone() is None:
        conn.execute(
            "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
            ("master-test", psec.hash_password("ParolaMaster123!")))
        conn.commit()
    master = app.test_client()
    master.post("/autentificare",
               data={"cui": "master-test", "password": "ParolaMaster123!"})
    return master


def test_creeaza_contract_master_requires_master(app):
    c = app.test_client()
    inregistreaza(c, cui="RO301")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO301'").fetchone()["id"]
    r = c.get(f"/master/contracte/creeaza/{firm_id}", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_creeaza_contract_master_prefills_from_anaf(app):
    c = app.test_client()
    inregistreaza(c, cui="RO302")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO302'").fetchone()["id"]
    r = master.get(f"/master/contracte/creeaza/{firm_id}")
    assert b"Firma Test" in r.data  # din _mock_anaf_cui (denumire="Firma Test")


def test_trimite_contract_master_creeaza_si_trimite(app):
    c = app.test_client()
    inregistreaza(c, cui="RO303")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO303'").fetchone()["id"]
    r = master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"}, follow_redirects=False)
    assert r.status_code == 302 and "/master/contracte" in r.headers["Location"]
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract is not None
    assert contract["stare"] == "in_asteptare"
    assert contract["metoda_semnatura"] == "esemneaza"
    assert contract["esemneaza_request_id"] == "fake-request-id"
    assert contract["numar"] >= 1


def test_trimite_contract_master_blocks_second_pending_contract(app):
    c = app.test_client()
    inregistreaza(c, cui="RO304")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO304'").fetchone()["id"]
    data = {"denumire": "Firma Test SRL", "adresa": "Str. Test 1",
            "ciclu": "lunar", "suma": "100.00"}
    master.post(f"/master/contracte/creeaza/{firm_id}", data=data)
    r = master.post(f"/master/contracte/creeaza/{firm_id}", data=data,
                    follow_redirects=True)
    assert "deja un contract".encode() in r.data
    contracte = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]
    assert contracte == 1
```

`_mock_esemneaza` (fixture autouse existentă) trebuie actualizată **înainte** ca acest test să
poată trece cu `create_sign_request` acceptând acum mai mulți destinatari - vezi Task 6, Step 1,
care actualizează fixture-ul; rulează acest task și pe cel de Task 6 împreună dacă testele de mai
sus eșuează din cauza fixture-ului vechi (mockuiește `create_sign_request` cu semnătura veche,
care oricum acceptă `recipients` generic - nu ar trebui să pice din acest motiv, dar
`get_sign_request` mockuit vechi întoarce un singur `recipients[0]` fără `order`, folosit abia în
Task 5).

- [ ] **Step 2: Rulează testele, confirmă eșecul**

Run: `python -m pytest tests/test_portal.py::test_creeaza_contract_master_requires_master tests/test_portal.py::test_trimite_contract_master_creeaza_si_trimite -v`
Expected: FAIL — `404 Not Found` (ruta nu există încă).

- [ ] **Step 3: Șterge `_genereaza_contract` (devine neutilizată de Task 6, dar o scoatem acum ca
  să nu rămână cod mort nefolosit; Task 6 elimină și apelul ei din `vezi_contract`)**

Nu șterge încă - `_genereaza_contract` e apelată din `vezi_contract` (Task 6). Sari acest pas aici,
revino la el în Task 6, Step 3.

- [ ] **Step 4: Implementează rutele noi**

În `portal/app.py`, imediat după ruta `master_contracte` (imediat înainte de
`descarca_contract_pdf_master`, linia ~2350), adaugă:

```python
    @app.get("/master/contracte/creeaza/<int:firm_id>")
    def creeaza_contract_master(firm_id):
        if not CONTRACTE_ACTIVE:
            return redirect(url_for("master"))
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        firm = conn.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if firm is None:
            return redirect(url_for("master_contracte", eroare="Firma nu a fost gasita."))
        ultimul = conn.execute(
            "SELECT * FROM contracts WHERE firm_id=? ORDER BY id DESC LIMIT 1",
            (firm_id,)).fetchone()
        if (ultimul is not None and ultimul["stare"] == pdb.CONTRACT_STARE_IN_ASTEPTARE
                and ultimul["esemneaza_request_id"]):
            return redirect(url_for(
                "master_contracte",
                eroare="Firma are deja un contract in asteptare - reziliaza-l intai."))
        denumire_sugerata = adresa_sugerata = ""
        eroare_anaf = None
        try:
            beneficiar = contract_mod.date_beneficiar(firm["cui"])
            denumire_sugerata = beneficiar["denumire"]
            adresa_sugerata = beneficiar["adresa"]
        except contract_mod.ContractError as e:
            eroare_anaf = str(e)
        suma_sugerata = (_calculeaza_suma_plata(firm, firm["ciclu_facturare"])
                        if firm["ciclu_facturare"] else None)
        return render_template(
            "master_contract_creeaza.html", user=user, firm=firm,
            denumire_sugerata=denumire_sugerata, adresa_sugerata=adresa_sugerata,
            suma_sugerata=suma_sugerata, eroare_anaf=eroare_anaf,
            cicluri=pdb.CICLURI_FACTURARE,
            eroare=request.args.get("eroare"))

    @app.post("/master/contracte/creeaza/<int:firm_id>")
    def trimite_contract_master(firm_id):
        if not CONTRACTE_ACTIVE:
            return redirect(url_for("master"))
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        if not ESEMNEAZA_API_KEY:
            return redirect(url_for(
                "creeaza_contract_master", firm_id=firm_id,
                eroare="Semnarea electronica nu este configurata inca pe acest server."))
        firm = conn.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if firm is None:
            return redirect(url_for("master_contracte", eroare="Firma nu a fost gasita."))
        ultimul = conn.execute(
            "SELECT * FROM contracts WHERE firm_id=? ORDER BY id DESC LIMIT 1",
            (firm_id,)).fetchone()
        if (ultimul is not None and ultimul["stare"] == pdb.CONTRACT_STARE_IN_ASTEPTARE
                and ultimul["esemneaza_request_id"]):
            return redirect(url_for(
                "master_contracte",
                eroare="Firma are deja un contract in asteptare - reziliaza-l intai."))
        denumire = request.form.get("denumire", "").strip()
        adresa = request.form.get("adresa", "").strip()
        ciclu = request.form.get("ciclu", "")
        try:
            suma = float(request.form.get("suma", ""))
        except ValueError:
            suma = None
        if (not denumire or not adresa or ciclu not in pdb.CICLURI_FACTURARE
                or suma is None or suma <= 0):
            return redirect(url_for(
                "creeaza_contract_master", firm_id=firm_id,
                eroare="Completeaza toate campurile cu valori valide."))
        admin = conn.execute(
            "SELECT u.email FROM user_firms uf JOIN users u ON u.id = uf.user_id "
            "WHERE uf.firm_id=? AND uf.role='admin' AND u.email IS NOT NULL "
            "LIMIT 1", (firm_id,)).fetchone()
        if admin is None or not admin["email"]:
            return redirect(url_for(
                "creeaza_contract_master", firm_id=firm_id,
                eroare="Adminul firmei nu are o adresa de email inregistrata."))
        numar = contract_mod.next_contract_number(conn)
        acum = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO contracts(firm_id, numar, ciclu_facturare, suma, "
            "beneficiar_denumire, beneficiar_cui, beneficiar_adresa, stare, "
            "creat_la) VALUES(?,?,?,?,?,?,?,?,?)",
            (firm_id, numar, ciclu, suma, denumire, firm["cui"], adresa,
             pdb.CONTRACT_STARE_IN_ASTEPTARE, acum))
        contract_id = cur.lastrowid
        conn.commit()
        contract = conn.execute("SELECT * FROM contracts WHERE id=?",
                                (contract_id,)).fetchone()
        continut = contract_mod.genereaza_text_din_rand(contract)
        pdf_bytes = contract_mod.genereaza_pdf(continut, tag_semnatura_esemneaza=True)
        try:
            file_name = esemneaza.upload_document(
                ESEMNEAZA_API_KEY, pdf_bytes, f"contract-{numar}.pdf")
            rezultat = esemneaza.create_sign_request(
                ESEMNEAZA_API_KEY, file_name,
                recipients=[
                    {"email": invoicing.FURNIZOR["email"], "name": invoicing.FURNIZOR["nume"],
                     "options": ["one_click_sign"]},
                    {"email": admin["email"], "name": firm["name"],
                     "options": ["one_click_sign"]},
                ],
                sender_name="e-TVA Reconciliere", extract_tags=True,
                sign_in_order=True)
        except esemneaza.EsemneazaError as e:
            conn.execute("DELETE FROM contracts WHERE id=?", (contract_id,))
            conn.commit()
            return redirect(url_for(
                "creeaza_contract_master", firm_id=firm_id,
                eroare=f"Nu am putut trimite contractul spre semnare: {e}"))
        conn.execute(
            "UPDATE contracts SET metoda_semnatura=?, esemneaza_request_id=? "
            "WHERE id=?",
            (pdb.CONTRACT_METODA_ESEMNEAZA, rezultat.get("id"), contract_id))
        conn.commit()
        _log_master_action(
            user, "contract.trimis_spre_semnare",
            f"{firm['name']} - contract nr. {numar}")
        return redirect(url_for(
            "master_contracte",
            mesaj=f"Contractul nr. {numar} a fost trimis spre semnare."))
```

`FURNIZOR` e deja importat în `app.py`? Verifică — dacă nu, adaugă lângă celelalte importuri din
`portal.invoicing` (caută `from portal.invoicing import FURNIZOR` sau `from portal import
invoicing`; dacă doar modulul e importat ca `invoicing`, folosește `invoicing.FURNIZOR["email"]`/
`invoicing.FURNIZOR["nume"]` mai sus în loc de `FURNIZOR["email"]`/`FURNIZOR["nume"]`).

- [ ] **Step 5: Creează template-ul**

Creează `portal/templates/master_contract_creeaza.html`:

```html
{% extends "base.html" %}
{% block titlu %}Trimite contract — e-TVA Reconciliere{% endblock %}
{% block continut %}
<main class="ingust">
  <div class="card" style="display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap">
    <h2>Trimite contract spre semnare — {{ firm['name'] }}</h2>
    <a class="btn" style="background:transparent;color:var(--accent);border:1px solid var(--border)"
       href="/master/contracte">&larr; Inapoi la contracte</a>
  </div>
  {% if eroare %}
  <div class="card"><p class="eroare" style="margin:0">{{ eroare }}</p></div>
  {% endif %}
  {% if eroare_anaf %}
  <div class="card"><p class="sub" style="color:var(--warn);margin:0">ANAF nu a raspuns
    ({{ eroare_anaf }}) - completeaza campurile manual.</p></div>
  {% endif %}

  <div class="card">
    <p class="sub">Vei semna tu primul (recipient 1, o singura apasare), apoi beneficiarul
      primeste automat mailul de la eSemneaza.ro (recipient 2, numele ii vine precompletat).</p>
    <form method="post" action="/master/contracte/creeaza/{{ firm['id'] }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <label>Denumire beneficiar
        <input type="text" name="denumire" value="{{ denumire_sugerata }}" required></label>
      <label>CUI beneficiar
        <input type="text" value="{{ firm['cui'] }}" disabled></label>
      <label>Adresa beneficiar
        <input type="text" name="adresa" value="{{ adresa_sugerata }}" required></label>
      <label>Ciclu de facturare
        <select name="ciclu" required>
          {% for c in cicluri %}
          <option value="{{ c }}" {% if firm['ciclu_facturare'] == c %}selected{% endif %}>
            {{ {'lunar': 'Lunar', '6luni': 'La 6 luni', 'an': 'Anual'}[c] }}
          </option>
          {% endfor %}
        </select></label>
      <label>Suma (fara TVA, RON)
        <input type="number" name="suma" step="0.01" min="0.01"
               value="{{ suma_sugerata if suma_sugerata is not none else '' }}" required></label>
      <button class="btn-lat" type="submit" style="margin-top:12px">Trimite spre semnare</button>
    </form>
  </div>
</main>
{% endblock %}
```

- [ ] **Step 6: Adaugă link-ul din `master_plati.html`**

În `portal/templates/master_plati.html`, în celula finală a tabelului (linia ~45-54), înlocuiește:

```html
        <td style="text-align:right">
          {% if p['stare'] != 'validata' %}
          <form class="inline" method="post" action="/master/plati/{{ p['id'] }}/valideaza">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button class="mic" type="submit">Validează încasarea</button>
          </form>
          {% elif p['invoice_id'] %}
          <a class="mic" href="/master/facturi/{{ p['invoice_id'] }}/pdf">Factura (PDF)</a>
          {% endif %}
        </td>
```

cu:

```html
        <td style="text-align:right">
          {% if p['stare'] != 'validata' %}
          <form class="inline" method="post" action="/master/plati/{{ p['id'] }}/valideaza">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button class="mic" type="submit">Validează încasarea</button>
          </form>
          {% else %}
          {% if p['invoice_id'] %}
          <a class="mic" href="/master/facturi/{{ p['invoice_id'] }}/pdf">Factura (PDF)</a>
          {% endif %}
          <a class="mic" href="/master/contracte/creeaza/{{ p['firm_id'] }}">Trimite contract</a>
          {% endif %}
        </td>
```

- [ ] **Step 7: Rulează testele, confirmă succesul**

Run: `python -m pytest tests/test_portal.py -k "contract_master" -v`
Expected: `test_creeaza_contract_master_requires_master` PASS;
`test_creeaza_contract_master_prefills_from_anaf`,
`test_trimite_contract_master_creeaza_si_trimite`,
`test_trimite_contract_master_blocks_second_pending_contract` — pot încă eșua dacă fixture-ul
`_mock_esemneaza` nu e actualizat (vezi Task 6, Step 1) - dacă eșuează din alt motiv decât
`get_sign_request`, investighează înainte de a continua.

- [ ] **Step 8: Commit**

```bash
git add portal/app.py portal/templates/master_contract_creeaza.html portal/templates/master_plati.html tests/test_portal.py
git commit -m "app: master-controlled contract creation and sending to eSemneaza"
```

---

### Task 5: `portal/app.py` — unifică verificarea stării (2 semnatari, XML înghețat)

**Files:**
- Modify: `portal/app.py:945-996` (`_finalizeaza_contract_esemneaza`,
  `_verifica_finalizare_esemneaza`), `portal/app.py:1172-1206` (webhook)
- Test: `tests/test_portal.py`

**Interfaces:**
- Consumes: `esemneaza.get_sign_request(api_key, request_id)` (existent, întoarce
  `{"recipients": [{"order": int, "sigStatus": str}, ...]}`), `contract_mod.date_contract_xml(row)`
  (existent).
- Produces: `_actualizeaza_stare_esemneaza(contract)` — înlocuiește
  `_verifica_finalizare_esemneaza`; apelată din `vezi_contract` (Task 6) și din webhook.

- [ ] **Step 1: Actualizează fixture-ul `_mock_esemneaza` (necesar înainte ca testele de mai jos
  să poată trece)**

În `tests/test_portal.py`, fixture-ul `_mock_esemneaza` (linia ~39-60) — găsește linia:

```python
    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "status": "COMPLETED",
        "recipients": [{"sigStatus": esemneaza.SIGSTATUS_APPLIED}]})
```

înlocuiește-o cu:

```python
    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "status": "COMPLETED",
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_APPLIED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_APPLIED}]})
```

- [ ] **Step 2: Scrie testele care eșuează**

Adaugă în `tests/test_portal.py`:

```python
def test_actualizeaza_stare_esemneaza_marks_prestator_then_completes(app, monkeypatch):
    import portal.app as app_module
    c = app.test_client()
    inregistreaza(c, cui="RO305")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO305'").fetchone()["id"]

    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_APPLIED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_PENDING}]})
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})

    c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["prestator_semnat_la"] is not None
    assert contract["stare"] == "in_asteptare"
    assert contract["contract_xml_final"] is None

    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_APPLIED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_APPLIED}]})
    c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["stare"] == "semnat"
    assert contract["contract_xml_final"] is not None
    assert b"<contract " in bytes(contract["contract_xml_final"])[:200]


def test_actualizeaza_stare_esemneaza_handles_rejection(app, monkeypatch):
    c = app.test_client()
    inregistreaza(c, cui="RO306")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO306'").fetchone()["id"]
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})

    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_REJECTED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_PENDING}]})
    c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["esemneaza_request_id"] is None
    assert contract["stare"] == "in_asteptare"
```

- [ ] **Step 3: Rulează testele, confirmă eșecul**

Run: `python -m pytest tests/test_portal.py::test_actualizeaza_stare_esemneaza_marks_prestator_then_completes -v`
Expected: FAIL — `AttributeError` sau `assert None is not None` (coloana/logica nu există încă).

- [ ] **Step 4: Implementează**

În `portal/app.py`, înlocuiește integral funcțiile `_finalizeaza_contract_esemneaza` (linia
~945-968) și `_verifica_finalizare_esemneaza` (linia ~970-996) cu:

```python
    def _finalizeaza_contract_esemneaza(contract, request_id: str):
        """Marcheaza contractul complet semnat (ambii semnatari), pastreaza
        documentul final + certificatul primite de la eSemneaza, si ingheata
        un instantaneu XML - vezi planning/specs/2026-07-28-contract-
        esemneaza-admin-review-design.md."""
        doc = esemneaza.get_completed_document_url(ESEMNEAZA_API_KEY, request_id)
        pdf_bytes = esemneaza.fetch_url_bytes(doc["docUrl"])
        cert_bytes = None
        try:
            cert = esemneaza.get_certificate_download_url(ESEMNEAZA_API_KEY, request_id)
            cert_bytes = esemneaza.fetch_url_bytes(cert["certificateUrl"])
        except esemneaza.EsemneazaError:
            pass
        acum = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE contracts SET stare=?, semnatura_verificata=1, "
            "semnatura_detalii=?, semnat_la=?, esemneaza_document_pdf=?, "
            "esemneaza_certificate_pdf=? WHERE id=?",
            (pdb.CONTRACT_STARE_SEMNAT,
             json.dumps({"metoda": "esemneaza", "request_id": request_id}),
             acum, pdf_bytes, cert_bytes, contract["id"]))
        conn.commit()
        rand_final = conn.execute("SELECT * FROM contracts WHERE id=?",
                                  (contract["id"],)).fetchone()
        xml_final = contract_mod.date_contract_xml(rand_final)
        conn.execute("UPDATE contracts SET contract_xml_final=? WHERE id=?",
                    (xml_final, contract["id"]))
        conn.commit()

    def _actualizeaza_stare_esemneaza(contract):
        """Interogheaza starea reala la eSemneaza (sursa de adevar - vezi
        get_sign_request, NU continutul webhook-ului, a carui forma exacta
        nu e documentata) si actualizeaza contractul. Recipientii se
        identifica dupa `order` (1=PRESTATOR, 2=BENEFICIAR, impus prin
        signInOrder la creare - vezi trimite_contract_master). Apelata atat
        din polling (vezi vezi_contract) cat si din webhook."""
        if (not ESEMNEAZA_API_KEY or not contract
                or contract["stare"] != pdb.CONTRACT_STARE_IN_ASTEPTARE
                or not contract["esemneaza_request_id"]):
            return contract
        try:
            stare = esemneaza.get_sign_request(
                ESEMNEAZA_API_KEY, contract["esemneaza_request_id"])
        except esemneaza.EsemneazaError:
            return contract
        recipienti = stare.get("recipients") or []
        prestator = next((r for r in recipienti if r.get("order") == 1), None)
        beneficiar = next((r for r in recipienti if r.get("order") == 2), None)
        respins = (
            (prestator and prestator.get("sigStatus") == esemneaza.SIGSTATUS_REJECTED)
            or (beneficiar and beneficiar.get("sigStatus") == esemneaza.SIGSTATUS_REJECTED))
        if respins:
            conn.execute(
                "UPDATE contracts SET esemneaza_request_id=NULL WHERE id=?",
                (contract["id"],))
            conn.commit()
            return conn.execute("SELECT * FROM contracts WHERE id=?",
                                (contract["id"],)).fetchone()
        prestator_semnat = (
            prestator and prestator.get("sigStatus") == esemneaza.SIGSTATUS_APPLIED)
        if prestator_semnat and not contract["prestator_semnat_la"]:
            conn.execute(
                "UPDATE contracts SET prestator_semnat_la=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), contract["id"]))
            conn.commit()
        beneficiar_semnat = (
            beneficiar and beneficiar.get("sigStatus") == esemneaza.SIGSTATUS_APPLIED)
        if prestator_semnat and beneficiar_semnat:
            _finalizeaza_contract_esemneaza(contract, contract["esemneaza_request_id"])
        return conn.execute("SELECT * FROM contracts WHERE id=?",
                            (contract["id"],)).fetchone()
```

Apoi, în ruta webhook (`webhook_esemneaza`, linia ~1172-1206), înlocuiește tot corpul de la
`payload = request.get_json(silent=True) or {}` până la finalul funcției cu:

```python
        payload = request.get_json(silent=True) or {}
        request_id = (payload.get("requestId") or payload.get("id")
                     or payload.get("request_id"))
        if not request_id:
            return jsonify({"ok": True})
        contract = conn.execute(
            "SELECT * FROM contracts WHERE esemneaza_request_id=?",
            (request_id,)).fetchone()
        if contract is not None:
            _actualizeaza_stare_esemneaza(contract)
        return jsonify({"ok": True})
```

(Șterge liniile care extrăgeau `eveniment` și blocul `if eveniment in (...)` — nu mai e nevoie,
webhook-ul acum doar declanșează o re-interogare reală, nu interpretează payload-ul.)

- [ ] **Step 5: Rulează testele, confirmă succesul**

Run: `python -m pytest tests/test_portal.py -k "actualizeaza_stare_esemneaza or contract_master or webhook" -v`
Expected: toate PASS.

- [ ] **Step 6: Rulează suita completă**

Run: `python -m pytest -q`
Expected: toate PASS (verifică niciun test vechi nu se baza pe `_verifica_finalizare_esemneaza`
ca nume de funcție, sau pe câmpul `recipients[0]` fără `order`).

- [ ] **Step 7: Commit**

```bash
git add portal/app.py tests/test_portal.py
git commit -m "app: track both signers by order, freeze XML on completion, simplify webhook"
```

---

### Task 6: Firma devine doar spectator

**Files:**
- Modify: `portal/app.py:918-943` (șterge `_genereaza_contract`), `portal/app.py:998-1020`
  (`vezi_contract`), `portal/app.py:1075-1170` (`semneaza_contract` — elimină ramura eSemneaza)
- Modify: `portal/templates/contract_semneaza.html`
- Test: `tests/test_portal.py`

**Interfaces:**
- Consumes: `_contract_curent(firm_id)` (existent), `_actualizeaza_stare_esemneaza(contract)`
  (Task 5).
- Produces: `vezi_contract` nu mai creează contracte; `POST /panou/contract/semneaza` respinge
  `metoda=esemneaza` cu eroare explicită (ramura `certificat` neschimbată).

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă în `tests/test_portal.py`:

```python
def test_vezi_contract_shows_in_pregatire_when_none_sent(app):
    c = app.test_client()
    inregistreaza(c, cui="RO307")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    r = c.get("/panou/contract")
    assert "pregătire".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts").fetchone()["n"] == 0


def test_vezi_contract_waits_for_prestator_before_showing_email_message(app, monkeypatch):
    c = app.test_client()
    inregistreaza(c, cui="RO308")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO308'").fetchone()["id"]
    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_PENDING},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_PENDING}]})
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})
    r = c.get("/panou/contract")
    assert "finalizarea din partea noastră".encode() in r.data


def test_semneaza_contract_rejects_esemneaza_method_from_firm(app):
    c = app.test_client()
    inregistreaza(c, cui="RO309")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO309'").fetchone()["id"]
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})
    r = c.post("/panou/contract/semneaza", data={"metoda": "esemneaza"},
              follow_redirects=True)
    assert "metoda de semnatura valida".encode() in r.data
```

- [ ] **Step 2: Rulează testele, confirmă eșecul**

Run: `python -m pytest tests/test_portal.py::test_vezi_contract_shows_in_pregatire_when_none_sent -v`
Expected: FAIL (azi `vezi_contract` ar genera automat un contract, deci lista nu e goală).

- [ ] **Step 3: Șterge `_genereaza_contract` și actualizează `vezi_contract`**

În `portal/app.py`, șterge integral funcția `_genereaza_contract` (linia ~918-943).

Înlocuiește ruta `vezi_contract` (linia ~998-1020):

```python
    @app.get("/panou/contract")
    def vezi_contract():
        if not CONTRACTE_ACTIVE:
            return redirect(url_for("panou"))
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        firm = conn.execute("SELECT * FROM firms WHERE id=?",
                            (active_firm_id,)).fetchone()
        if firm is None or not firm["ciclu_facturare"]:
            return redirect(url_for(
                "alege_plan", eroare="Alege intai un ciclu de facturare."))
        contract, eroare_generare = _genereaza_contract(firm)
        contract = _verifica_finalizare_esemneaza(contract)
        continut = (contract_mod.genereaza_text_din_rand(contract)
                   if contract is not None else None)
        return render_template(
            "contract_semneaza.html", user=user, firm=firm, contract=contract,
            continut=continut,
            eroare=eroare_generare or request.args.get("eroare"),
            mesaj=request.args.get("mesaj"))
```

cu:

```python
    @app.get("/panou/contract")
    def vezi_contract():
        if not CONTRACTE_ACTIVE:
            return redirect(url_for("panou"))
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        contract = _contract_curent(active_firm_id)
        contract = _actualizeaza_stare_esemneaza(contract)
        continut = (contract_mod.genereaza_text_din_rand(contract)
                   if contract is not None else None)
        return render_template(
            "contract_semneaza.html", user=user, contract=contract,
            continut=continut,
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))
```

- [ ] **Step 4: Elimină ramura eSemneaza din `semneaza_contract`**

În `portal/app.py`, în funcția `semneaza_contract` (linia ~1075-1170), înlocuiește:

```python
        metoda = request.form.get("metoda")
        acum = datetime.now(timezone.utc).isoformat()

        if metoda == pdb.CONTRACT_METODA_ESEMNEAZA:
            if not ESEMNEAZA_API_KEY:
                return redirect(url_for(
                    "vezi_contract",
                    eroare="Semnarea electronica nu este configurata inca pe acest server."))
            admin = conn.execute(
                "SELECT u.email FROM user_firms uf JOIN users u ON u.id = uf.user_id "
                "WHERE uf.firm_id=? AND uf.role='admin' AND u.email IS NOT NULL "
                "LIMIT 1", (active_firm_id,)).fetchone()
            if admin is None or not admin["email"]:
                return redirect(url_for(
                    "vezi_contract",
                    eroare="Adminul firmei nu are o adresa de email inregistrata "
                          "- necesara pentru trimiterea contractului spre semnare."))
            firm = conn.execute("SELECT * FROM firms WHERE id=?",
                                (active_firm_id,)).fetchone()
            continut = contract_mod.genereaza_text_din_rand(contract)
            # tag_semnatura_esemneaza=True adauga "{{s:1}}" invizibil dupa
            # BENEFICIAR - eSemneaza il detecteaza singur (extract_tags=True
            # mai jos) si calculeaza pozitia reala a campului, fara sa
            # ghicim noi coordonate fixe.
            pdf_bytes = contract_mod.genereaza_pdf(
                continut, tag_semnatura_esemneaza=True)
            try:
                file_name = esemneaza.upload_document(
                    ESEMNEAZA_API_KEY, pdf_bytes, f"contract-{contract['numar']}.pdf")
                # Semnatura PRESTATORULUI (VML) e deja inclusa in textul
                # contractului la generare (vezi contract.genereaza_text) -
                # doar BENEFICIARUL semneaza efectiv prin eSemneaza.
                rezultat = esemneaza.create_sign_request(
                    ESEMNEAZA_API_KEY, file_name,
                    recipients=[{"email": admin["email"], "name": firm["name"]}],
                    sender_name="e-TVA Reconciliere", extract_tags=True)
            except esemneaza.EsemneazaError as e:
                return redirect(url_for(
                    "vezi_contract",
                    eroare=f"Nu am putut trimite contractul spre semnare: {e}"))
            conn.execute(
                "UPDATE contracts SET metoda_semnatura=?, esemneaza_request_id=? "
                "WHERE id=?",
                (pdb.CONTRACT_METODA_ESEMNEAZA, rezultat.get("id"), contract["id"]))
            conn.commit()
            audit_fc = firm_conn(active_firm_id)
            audit.log(audit_fc, user["username"], "contract.trimis_spre_semnare",
                      "contract", str(contract["id"]))
            return redirect(url_for(
                "vezi_contract",
                mesaj=f"Am trimis contractul spre semnare la {admin['email']} - "
                     f"verifica emailul primit de la eSemneaza.ro."))
        elif metoda == pdb.CONTRACT_METODA_CERTIFICAT:
```

cu:

```python
        metoda = request.form.get("metoda")
        acum = datetime.now(timezone.utc).isoformat()

        if metoda == pdb.CONTRACT_METODA_CERTIFICAT:
```

(Ramura `elif`/restul funcției de la `fisier = request.files.get(...)` în jos rămâne
neschimbată — devine primul și singurul `if`.)

- [ ] **Step 5: Actualizează `contract_semneaza.html`**

Înlocuiește tot conținutul din `{% if contract %}` (linia 27) până la `{% endif %}` final (linia
97) din `portal/templates/contract_semneaza.html` cu:

```html
  {% if contract is none %}
  <div class="card">
    <p class="sub">Contractul tău e în pregătire — vei fi anunțat când e gata de semnat.</p>
  </div>
  {% else %}
  <div class="card">
    <p class="sub">Contract nr. <b>{{ contract['numar'] }}</b>, ciclu de facturare
      <b>{{ {'lunar': 'lunar', '6luni': 'la 6 luni', 'an': 'anual'}[contract['ciclu_facturare']] }}</b>,
      {{ '%.2f'|format(contract['suma']) }} RON.
      {% if contract['stare'] == 'semnat' %}
      <a href="/panou/contract/pdf" target="_blank">Descarcă PDF</a> ·
      <a href="/panou/contract/xml">Descarcă XML</a>
      {% endif %}</p>
    <div class="contract-text">{{ continut }}</div>
  </div>

  {% if contract['stare'] == 'in_asteptare' and not contract['prestator_semnat_la'] %}
  <div class="card">
    <p class="sub" style="color:var(--warn)">Contractul așteaptă finalizarea din partea
      noastră. Pagina verifică automat starea de fiecare dată când o reîncarci.</p>
  </div>
  {% elif contract['stare'] == 'in_asteptare' and contract['esemneaza_request_id'] %}
  <div class="card">
    <p class="sub" style="color:var(--warn)">Contractul a fost trimis spre semnare prin
      eSemneaza.ro — verifică emailul primit de la <b>eSemneaza.ro</b> și semnează acolo.
      Pagina verifică automat starea de fiecare dată când o reîncarci.</p>
    <a class="btn" style="background:transparent;color:var(--accent);border:1px solid var(--border)"
       href="/panou/contract">Verifică starea acum</a>
  </div>
  {% elif contract['stare'] == 'in_asteptare' %}
  <div class="card">
    <p class="sub" style="color:var(--warn)">Cererea anterioară a fost respinsă sau anulată —
      echipa noastră va retrimite contractul.</p>
  </div>
  {% elif contract['stare'] == 'semnat' %}
  <div class="card">
    <p class="sub" style="color:var(--ok)">Contract semnat la {{ contract['semnat_la'][:10] }}
      (eSemneaza.ro).
      {% if contract['esemneaza_certificate_pdf'] %}
      <a href="/panou/contract/certificat" target="_blank">Descarcă certificatul de semnătură</a>
      {% endif %}</p>
    <form method="post" action="/panou/contract/reziliaza"
          onsubmit="return confirm('Sigur vrei să soliciți rezilierea contractului?');">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button class="secundar" type="submit">Solicită rezilierea</button>
    </form>
    <p class="nota">Dacă te retragi înainte de finalul perioadei de facturare deja plătite,
      rambursul nu va depăși 50% din suma achitată pentru acel ciclu.</p>
  </div>
  {% elif contract['stare'] == 'reziliere_solicitata' %}
  <div class="card"><p class="sub" style="color:var(--warn)">Cererea ta de reziliere e în curs
    de procesare.</p></div>
  {% elif contract['stare'] == 'reziliat' %}
  <div class="card"><p class="sub">Acest contract a fost reziliat
    {% if contract['ramburs_procent'] is not none %}, cu un ramburs de {{ contract['ramburs_procent']|round(0, 'floor')|int }}%{% endif %}.</p></div>
  {% endif %}
  {% endif %}
```

(Am eliminat integral fostul card "Semnează contractul" cu cele două metode — firma nu mai alege
nimic; certificatul rămâne disponibil doar prin ruta `POST /panou/contract/semneaza` însăși,
neatinsă, pentru orice acces direct rămas dintr-un flux vechi.)

- [ ] **Step 6: Rulează testele, confirmă succesul**

Run: `python -m pytest tests/test_portal.py -k "vezi_contract or semneaza_contract_rejects" -v`
Expected: toate PASS.

- [ ] **Step 7: Rulează suita completă**

Run: `python -m pytest -q`
Expected: toate PASS. Dacă teste vechi (dinainte de acest plan) apelau `_semneaza_contract_esemneaza`
și pică, mergi la Task 7 — acel helper trebuie actualizat înainte ca ele să treacă din nou.

- [ ] **Step 8: Commit**

```bash
git add portal/app.py portal/templates/contract_semneaza.html tests/test_portal.py
git commit -m "app: firm side becomes read-only, remove firm-triggered esemneaza signing"
```

---

### Task 7: Actualizează helper-ul de test folosit de restul suitei

**Files:**
- Modify: `tests/test_portal.py:2614-2624` (`_semneaza_contract_esemneaza`)

**Interfaces:**
- Consumes: `_creeaza_master(app)` (Task 4, Step 1).
- Produces: `_semneaza_contract_esemneaza(app, c)` — semnătură nouă (primește și `app`), folosită
  de zeci de teste existente pentru plăți/facturi care au nevoie doar de un contract semnat, nu
  testează ele însele fluxul de contract.

- [ ] **Step 1: Actualizează helper-ul**

În `tests/test_portal.py`, înlocuiește:

```python
def _semneaza_contract_esemneaza(c):
    """Genereaza (daca nu exista deja) si semneaza contractul curent al
    firmei active a clientului dat, prin fluxul eSemneaza.ro - modulul
    etva.esemneaza e mockuit implicit (vezi fixture-ul autouse
    _mock_esemneaza) sa raporteze semnatura ca aplicata la prima verificare,
    suficient cat sa treaca poarta din creeaza_cerere_plata fara sa depinda
    de serviciul real (vezi tests/test_esemneaza.py pentru testele modulului
    insusi)."""
    c.get("/panou/contract")
    c.post("\panou\contract\semneaza", data={"metoda": "esemneaza"})
    return c.get("/panou/contract")
```

cu:

```python
def _semneaza_contract_esemneaza(app, c):
    """Master creeaza si trimite contractul (fluxul nou, controlat de
    master - vezi planning/specs/2026-07-28-contract-esemneaza-admin-review-
    design.md), apoi verifica starea din partea firmei. `c` ramane
    autentificat ca firma - se foloseste un client separat, autentificat ca
    master, pentru pasul de trimitere. Modulul etva.esemneaza e mockuit
    implicit (vezi fixture-ul autouse _mock_esemneaza) sa raporteze ambii
    semnatari ca APPLIED la prima verificare, suficient cat sa treaca poarta
    din creeaza_cerere_plata fara sa depinda de serviciul real (vezi
    tests/test_esemneaza.py pentru testele modulului insusi)."""
    with c.session_transaction() as sess:
        firm_id = sess["active_firm_id"]
    master = _creeaza_master(app)
    ciclu = app.portal_conn.execute(
        "SELECT ciclu_facturare FROM firms WHERE id=?", (firm_id,)).fetchone()["ciclu_facturare"]
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Adresa Test",
        "ciclu": ciclu, "suma": "100.00"})
    return c.get("/panou/contract")
```

- [ ] **Step 2: Actualizează toate apelurile existente**

Caută fiecare apel `_semneaza_contract_esemneaza(c)` din `tests/test_portal.py` (sunt în jur de
10-12, în teste de plăți/facturi/statistici) și înlocuiește-l cu
`_semneaza_contract_esemneaza(app, c)` — toate aceste teste au deja parametrul `app` disponibil
în semnătura funcției de test (fixture standard).

Run căutare: `grep -n "_semneaza_contract_esemneaza(c)" tests/test_portal.py`

- [ ] **Step 3: Rulează suita completă**

Run: `python -m pytest -q`
Expected: toate PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_portal.py
git commit -m "tests: update _semneaza_contract_esemneaza helper for master-controlled flow"
```

---

### Task 8: Promovare și desfășurare

**Files:** niciunul (doar git/deploy)

- [ ] **Step 1: Rulează suita completă o ultimă dată**

Run: `cd "C:\Users\vasil\Downloads\e-TVA-Reconciliere\DEV" && python -m pytest -q`
Expected: toate PASS, 0 failed.

- [ ] **Step 2: Promovează dev → testare → main**

```bash
cd "C:\Users\vasil\Downloads\e-TVA-Reconciliere\TESTARE" && git merge --ff-only dev
cd "C:\Users\vasil\Downloads\e-TVA-Reconciliere\PROD" && git merge --ff-only testare
git push origin dev:dev testare:testare main:main
```

- [ ] **Step 3: Desfășoară pe VPS (testare, apoi producție)**

```bash
ssh root@89.39.7.44 "su -s /bin/bash -c 'cd /opt/etva-testare/app && git pull' etva-testare && systemctl restart etva-testare"
ssh root@89.39.7.44 "su -s /bin/bash -c 'cd /opt/etva-productie/app && git pull' etva-productie && systemctl restart etva-productie"
```

Expected: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8990/` și portul 8991
întorc 200 pe server.

- [ ] **Step 4: Configurează manual pe VPS — cheia API și secretul webhook (NU trec prin git)**

```bash
ssh root@89.39.7.44
sed -i '/Environment=SESSION_COOKIE_SECURE=1/a Environment=ESEMNEAZA_API_KEY=<CHEIA_REALA>\nEnvironment=ESEMNEAZA_WEBHOOK_SECRET=<UN_SECRET_ALES_DE_TINE>' /etc/systemd/system/etva-productie.service
systemctl daemon-reload
systemctl restart etva-productie
```

Andrei rulează acest pas manual, cu cheia reală generată în panoul eSemneaza (Task de dinainte de
plan) — nu se pune în chat sau în commit.

- [ ] **Step 5: Configurează webhook-ul în panoul eSemneaza (manual, în browser)**

URL Webhook: `https://ereconciliere.ro/api/esemneaza/webhook`
Evenimente active: `REQUEST_COMPLETED`, `RECIPIENT_SIGNED`, `RECIPIENT_REJECTED`
Nume header: `X-Webhook-Secret` (implicit) — valoare = exact `ESEMNEAZA_WEBHOOK_SECRET` de mai sus.

- [ ] **Step 6: Verificare finală end-to-end (manual, prin `/master/plati` real, cu `CONTRACTE_ACTIVE=1`)**

`CONTRACTE_ACTIVE` rămâne 0 până Andrei decide explicit să-l activeze - nu face parte din acest
plan de implementare.
