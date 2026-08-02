# Integrare FGO in procesul de creare facturi

Document de referinta: ce s-a schimbat, de ce, si ce ramane deschis.
Implementat si deployat 2026-08-02 (branch-urile `main`/`testare`/`dev`,
commit `f3cb922`).

## Context

Facturarea proprie a VML Expert Advisor catre firmele care folosesc
platforma (abonamente) folosea un lant complet "hand-rolled": numerotare
locala, XML UBL generat manual, OAuth2 + upload catre ANAF SPV scris de
mana (`etva/efactura_xml.py`, `etva/anaf_oauth.py::upload_invoice/
check_upload_status/download_response`). S-a inlocuit cu FGO (fgo.ro) -
un SaaS de facturare + automatizare e-Factura/SPV, cu API REST.

Scop: FGO acopera **atat crearea facturii, cat si trimiterea ei la SPV**.

## Ce s-a schimbat in cod

| Fisier | Schimbare |
|---|---|
| `etva/fgo.py` (nou) | Client FGO - `emite_factura`, `get_status`, `get_nomenclator`. Stdlib `urllib` (fara `requests`), un singur `FgoError`. |
| `etva/efactura_xml.py` | **Sters** - singurul apelant (`trimite_factura_anaf`) a fost eliminat. |
| `portal/app.py` | `creeaza_factura` + `valideaza_plata` apeleaza acum `_emite_factura_fgo()` (helper comun nou) in loc de INSERT local. Rutele `trimite_factura_anaf`, `verifica_stare_factura_anaf`, `descarca_raspuns_anaf` (+ echivalentele firmei: `descarca_factura_proprie_xml`, `descarca_raspuns_anaf_propriu`) **eliminate complet** - SPV e gestionat integral de FGO, nu mai exista ce sa verificam/trimitem din cod. `descarca_factura_pdf`/`descarca_factura_proprie_pdf` redirectioneaza acum catre `Factura.Link` de la FGO. Formularul de facturare cere acum si **Judetul clientului** (`ROMANIA_JUDETE`, lista hardcodata - obligatoriu pentru e-Factura). |
| `portal/db.py`, `etva/pg.py`, `etva/pg_schema.sql` | 3 coloane noi pe `invoices`: `fgo_serie`, `fgo_numar`, `fgo_link_pdf` (seria/numarul REALE atribuite de FGO, afisate firmei - `serie`/`numar` locale raman doar cheia interna de randare, neschimbate). |
| `portal/templates/master_facturi.html`, `master_plati.html`, `alege_plan.html` | Eliminate coloana "Stare ANAF" si butoanele XML/"Trimite la ANAF"/"Verifica stare"/"Raspuns ANAF". Adaugat camp Judet in ambele formulare de emitere (`/master/facturi` si `/master/plati/.../valideaza`). |
| `tests/test_fgo.py` (nou) | Teste unitare pentru `etva/fgo.py` (hash, normalizare CotaTVA, erori). |
| `tests/test_portal.py` | Actualizat masiv: mock global `fgo.emite_factura` (fixture `_mock_fgo`, autouse), sterse testele pentru rutele eliminate. |

## Descoperiri importante (nu presupuneri - verificate live)

1. **Test si productie sunt conturi FGO complet separate**, cu inregistrare
   separata: `testuat.fgo.ro/inregistrare` (fara abonament platit) vs
   `www.fgo.ro/inregistrare`. Contul folosit initial era de productie -
   apelurile catre `api-testuat.fgo.ro` cu acele credentiale esuau cu
   "Codul unic nu exista sau nu este asociat". S-a creat cont UAT separat.
2. **Trimiterea la SPV nu e un apel API** - se activeaza manual, o singura
   data, din FGO -> Setari - e-Factura (cont conectat + interval ales).
   Niciun endpoint `factura/*` nu expune status SPV.
3. **`GET /nomenclator/*`**: parametrii trebuie trimisi ca query string,
   NU ca JSON body (desi exemplul din documentatia FGO arata JSON body pe
   GET) - da 403 de la CloudFront altfel. Raspunsul are cheia `"List"`.
4. **`Continut[i][CotaTVA]` trimis ca float intreg (21.0) e RESPINS** de
   FGO ("nu exista in nomenclator") - trebuie `21` (int). Normalizat in
   `etva/fgo.py::emite_factura`.
5. **`Client[Judet]`** obligatoriu pentru clienti din Romania (cerinta
   e-Factura) - firmele nu au acest camp stocat, de-aia s-a adaugat in
   formular (nu in schema `firms`, ca sa nu extindem scopul).
6. Domeniul apelant trebuie whitelist-uit explicit in FGO -> Setari API ->
   "Domenii autorizate" - contul de productie permite doar 1 site (plan
   curent), deci domeniul de test si cel de productie nu pot coexista pe
   acelasi cont.

## Configurare per mediu

Acelasi tipar ca `esemneaza.env`/`db.env` - fisier `.env` separat per
mediu, legat in systemd prin `EnvironmentFile=-`:

| | Testare | Productie |
|---|---|---|
| Fisier | `/etc/etva-testare/fgo.env` | `/etc/etva-productie/fgo.env` |
| `FGO_MEDIU` | `test` | `productie` |
| `FGO_PLATFORMA_URL` | `https://testare.ereconciliere.ro` | `https://ereconciliere.ro` |
| Cont FGO | UAT (`testuat.fgo.ro`), fara abonament | live (`www.fgo.ro`), `GO_eFactura` |
| Auto-SPV | activat manual in cont | activat manual in cont (1 zi de la emitere) |

`FGO_COD_UNIC=35070700` si `FGO_SERIE=VML` identice pe ambele medii (CUI-ul
VML, seria "Facturi" deja existenta in FGO).

## Schema DB - aplicata pe toate cele 3 baze Postgres

`fgo_serie`/`fgo_numar`/`fgo_link_pdf` adaugate (ALTER TABLE aditiv,
idempotent) pe: `etva_testare`, `etva_productie`, si `etva_template`
(sablonul clusterului local de test, port 54329, folosit de
`ETVA_TEST_PG=1`).

## Testare

- 437 teste (SQLite) + 275-284 teste (Postgres real, `ETVA_TEST_PG=1`) -
  toate trec.
- Validat live, cu credentiale reale, impotriva `api-testuat.fgo.ro`:
  `factura/emitere`, `nomenclator/judet|tara|tva|tipfactura`.
- Testat end-to-end prin UI-ul real pe `testare.ereconciliere.ro`.

## Deploy

Commit `f3cb922` pe toate cele 3 branch-uri (`main`, `testare`, `dev`),
push-uite pe GitHub. Servicii `etva-testare` si `etva-productie` repornite
cu codul nou.

**Nota de proces**: implementarea a fost facuta initial din greseala in
checkout-ul de productie (`/opt/etva-productie/app`, branch `main`) in loc
de cel de testare - corectat prin mutarea muncii pe branch separat
(`fgo-integration`) inainte sa ajunga vreodata pe `main` real, apoi
promovata corect prin `testare` -> `dev`/`main`.

## Ce ramane deschis

- **Plata**: `creeaza_cerere_plata()` (`/panou/plata`) nu proceseaza inca
  nicio plata reala - doar inregistreaza o "cerere" (`payments.stare=
  in_asteptare`), pe care masterul o valideaza manual dupa ce confirma
  incasarea pe alta cale (TODO explicit in cod). Integrarea Netopia
  Payments (prin FGO, `LinkPlata`) ar automatiza asta - **amanata pana
  exista un cont Netopia configurat**.
- Optional, pentru mai tarziu: callback-ul FGO de incasare (webhook, vezi
  FGO -> Setari API) sau integrarea bancara PSD2
  (`fgo.ro/procesare-extrase`) pentru reconciliere automata a platilor cu
  extrasul bancar real - discutate, neimplementate.
- `CodArticol` fix (`"ABONAMENT"`) evita duplicarea articolelor in
  catalogul FGO, dar merita verificat periodic in cont ca ipoteza tine.
