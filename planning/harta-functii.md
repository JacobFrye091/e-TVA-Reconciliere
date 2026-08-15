# Harta functiilor aplicatiei e-TVA Reconciliere

Document de referinta: toate functiile aplicatiei (backend Python + frontend
JS) si ordinea in care se apeleaza intre ele, organizat pe straturi si pe
fluxuri. Generat prin scanarea completa a codului la 2026-08-02 (branch
`main`/`testare`/`dev`, commit `29ec879`). Actualizeaza-l manual daca
schimbi semnificativ un flux - nu se regenereaza automat.

Actualizat manual 2026-08-04: adaugata notificarea de finalizare contract
(`invoicing.NOTIFICARE_CONTRACT_FINALIZAT_EMAIL`) si, in `/master/pipeline`
(vizibile doar pe mediul `testare`), doua rute noi de promovare a codului
direct din panoul web (§3p, §6.4), fiindca pipeline-ul local existent
(DEV/TESTARE/PROD alaturi) nu functioneaza de pe un VPS deployat.

Nota istorica: `dev`, `main` si `testare` au divergat azi (acelasi fix
aplicat separat pe checkout-uri diferite, commit-uri diferite cu continut
identic) - rezolvat prin doua merge-uri succesive (`main` -> `testare`,
apoi `testare` -> `dev`), fiecare urmat de promovarea rezultatului
unificat mai departe (vezi [[etva-capacitate-server]] pentru context
complet).

Actualizat manual 2026-08-09: adaugat modulul premium **Risc Fiscal**
(evaluare bazata pe metodologia oficiala ANAF, Anexa 2 - vezi
`etva/risc_fiscal.py`) - nivel `simplu`/`complet` per firma
(`firms.risc_fiscal_nivel`), tabela `risc_fiscal_perioade` (persistenta,
`etva/risc_fiscal_store.py`), rute noi `/api/risc-fiscal/*` (§3r), tab nou
in SPA (§2h), raport PDF (`portal/risc_fiscal_report.py`, §6.10) si un al
treilea scheduler de fundal (`portal/risk_alerts.py`, §6.11, §7) pentru
alerte automate la scor "ridicat". Importer-ul SAF-T D406 (planificat
initial ca sursa de date financiare) e AMANAT - datele financiare se
introduc manual pana exista un export D406 real de validare. In aceeasi
lucrare, fixat si un bug pre-existent de izolare intre firme 'directe' pe
Postgres in `etva/cod_mappings.py` (index unic global fara `firm_id`).

Actualizat manual 2026-08-10: adaugata **schimbarea self-service a
abonamentului** (upgrade/downgrade pentru pachetul de reconcilieri si
nivelul Risc Fiscal, cu contract nou generat automat). Rute noi in
`portal/app.py`: `POST /panou/plan/schimbare` (`schimba_plan` - inlocuieste
`salveaza_plan` ca tinta a formularului din `alege_plan.html`, dar cade
pe `salveaza_plan` cand `CONTRACTE_ACTIVE` e oprit sau firma n-are inca
`ciclu_facturare`) si `POST /panou/plan/schimbare/anuleaza`
(`anuleaza_schimbare_plan`). Un upgrade cu abonament deja platit lasa firma
sa aleaga: `timing=imediat` (plateste doar diferenta de pret, contract nou
generat si trimis la eSemneaza pe loc) sau `timing=programat` (fara plata
acum, intra in vigoare la finalul perioadei curente). Downgrade-urile si
schimbarile de ciclu sunt intotdeauna `programat`. Tabela noua
`plan_schimbari_programate` (o schimbare `in_asteptare` per firma, impus
prin index unic partial) e procesata de al patrulea scheduler de fundal,
`portal/plan_schimbari.py` (§7) - genereaza contractul abia la aplicare, nu
la cerere, ca sa nu blocheze gresit platile pe planul vechi inca valabil
(vezi docstring-ul modulului pentru motivul complet). Schema noua:
`firms.abonament_activ_pana` (perioada platita curent, NULL pentru firmele
existente - fara migrare retroactiva), `payments.tip`
(`abonament`/`diferenta_upgrade` + 3 coloane insotitoare) si
`contracts.stare='anulat'` (contract auto-generat dar anulat inainte de
semnare). `valideaza_plata` (`/master/plati/<id>/valideaza`) ramifica acum
pe `payments.tip`. Vezi planul complet in istoricul de conversatie pentru
deciziile de design (in special momentul generarii contractului pentru
schimbarile programate).

Actualizat manual 2026-08-10 (a doua interventie din aceeasi zi): modulul
Risc Fiscal primeste optiunea "sursa date financiare" (SAF-T vs. manual) in
formularul din `web/index.html`, cu SAF-T ca implicit recomandat. Alegerea
SAF-T incarca fisierul D406 (XML), il salveaza brut in
`risc_fiscal_perioade.saft_xml_original` (coloana noua) SI acum extrage
automat cei 4 indicatori financiari. Fix important de acces:
`current_identity()` (`portal/app.py`) nu mai considera `firms.risc_fiscal_nivel`
suficient pentru acces la `/api/risc-fiscal/*` - cere si o plata
`payments.tip='abonament'` deja `validata` de master (altfel o firma putea
alege nivelul in trial si folosi modulul premium nelimitat, gratuit, fara sa
fi platit vreodata). Zeci de teste din `tests/test_portal.py` au fost
adaptate sa treaca printr-un ciclu complet de plata inainte de a apela
rutele risc-fiscal (vezi helper-ul `_firma_cu_abonament_platit`/
`_client_risc_fiscal_platit`).

Actualizat manual 2026-08-10 (a treia interventie): **Pasul 4 (importer
SAF-T D406), amanat din 2026-08-09, e acum LIVRAT** - `etva/importer/saft_d406.py`,
validat empiric pe un export real (SAGA C, nu doar sintetic). Extrage
direct din `MasterFiles/GeneralLedgerAccounts`: capitaluri proprii (suma
soldurilor clasa 1), rezultat net (contul 121, tinut cumulat pe anul fiscal
de programele de contabilitate), datorii totale (clasa 4 marcata
Pasiv/Bifunctional de `AccountType` + eventual cont 519, EXCLUZAND explicit
conturile de creanta din clasa 4 - clienti 411, debitori 461 etc. - si toata
clasa 5 de trezorerie), cifra de afaceri (doar grupa 70, nu toata clasa 7).
Parsarea foloseste namespace URI XML (`mfp:anaf:dgti:d406:declaratie:v1`),
nu prefixul `nsSAFT:` din document (alte programe pot exporta cu alt prefix
sau namespace implicit). Fisierul real de test NU e in depozit (depozitul
GitHub e public, fisierul continea IBAN/telefon/email/solduri ale unei
firme reale) - `tests/test_saft_d406.py` foloseste fixture-uri sintetice
calibrate pe aceeasi structura. Limitari cunoscute, de revalidat pe alte
programe de contabilitate: presupunerea ca soldurile claselor 6/7 sunt
tinute cumulat pe an (nu resetate lunar), si contul 519 (nevalidat inca,
absent din fisierul de test).

Fix descoperit direct dintr-un raport PDF real generat in timpul testarii
(scor mic afisat langa eticheta rosie "Risc ridicat", contradictoriu la
prima vedere): `etva/risc_fiscal.py::calculeaza_scor` forta deja
`clasificare='ridicat'` cand un semnal de Sectiunea B era activ (override),
dar `scor_afisat` ramanea punctajul brut pe indicatorii 1-5 (posibil foarte
mic, daca toti indicatorii financiari erau in favoarea firmei). Acum
`scor_afisat` e fortat la 100 ori de cate ori override-ul e activ, consistent
cu eticheta - `scor_total_indicatori`/`scor_max_posibil` raman neschimbate
(punctajul brut ramane vizibil separat in raport).

Verificat exhaustiv (2026-08-10, la cererea explicita a lui Andrei "aplica
exact legea in vigoare"), direct din sursa oficiala ANAF (nu din bloguri
tertiare): toti indicatorii 1-5 si cele 9 conditii din Sectiunea B din
`etva/risc_fiscal.py` corespund EXACT textului oficial "Fisa indicatorilor
de risc fiscal" (Anexa nr. 2 la procedura OPANAF 3699/2015, modificat prin
OPANAF 1232/2017 - confirmat inca in vigoare in 2026). Niciun cod modificat
in urma verificarii - implementarea era deja corecta. Separat, s-a
clarificat ca OPANAF 417/2025 (accize, destinatari inregistrati/antrepozitari)
e o reglementare COMPLET SEPARATA, fara legatura cu Anexa 2/rambursari TVA -
ramane doar informativ, neintegrat in modul (decizie in asteptare de la
Andrei: modul separat pentru accize, sau deloc).

Actualizat manual 2026-08-10 (UX): `etva/risc_fiscal_store.py::lista_perioade`
ordoneaza acum dupa `creat_la DESC` (momentul rularii), nu dupa eticheta
`perioada` - o resubmisie recenta a unei perioade "mai vechi" ca eticheta
trebuie sa apara sus. In `web/index.html`, dupa `salveazaRiscFiscal()`,
pagina deruleaza automat la cardul "Istoric evaluari" (`scrollIntoView`),
ca utilizatorul sa vada rezultatul fara sa caute manual.

Fix descoperit de Andrei (screenshot cu "risc ridicat 100/100" pe orice
perioada calculata): formularul din `web/index.html` nu reseta niciodata
bifele de Sectiunea B (`.rfFlag`) dupa `salveazaRiscFiscal()` - o bifa
lasata activa dintr-un test anterior ramanea bifata la infinit, fortand
override-ul (§ mai sus) pe toate evaluarile urmatoare, indiferent de cifre.
Confirmat direct in baza `risc_fiscal_perioade` de pe `testare`: 3 randuri
consecutive cu toate cele 9 flaguri `true`. Fix: `salveazaRiscFiscal()`
goleste acum tot formularul (bife, campuri manuale, fisier SAF-T) dupa
fiecare salvare reusita. In aceeasi interventie: coloana noua "Rulat la"
(prima in tabelul de istoric, `creat_la` formatat cu `toLocaleString`) si
tooltip pe chip-ul de clasificare care numeste explicit conditia din
Sectiunea B care a fortat "ridicat", cand e cazul (`ETICHETE_FLAGURI_SECTIUNE_B`
in JS, oglindeste `risc_fiscal.FLAGURI_SECTIUNE_B`). `risc_fiscal_store._decodeaza`
normalizeaza acum `creat_la` la text ISO 8601 indiferent de backend (Postgres
intorcea `datetime`, serializat altfel de `jsonify` decat textul simplu din
SQLite). Verificate si doua surse noi trimise de Andrei (pagina oficiala ANAF
`Anexanr2laproceduraFisaindicriscfiscal.htm` si un articol cabinetexpert.ro) -
ambele confirma metodologia deja implementata, fara nicio schimbare de cod.

Actualizat manual 2026-08-10 (a patra interventie): la cererea explicita a
lui Andrei ("nu ne incredem total in bifa utilizatorului"), 3 din cele 9
bife ale Sectiunii B nu mai depind exclusiv de contabil:
- **declarat_inactiv**: verificat LIVE la ANAF la fiecare evaluare
  (`anaf_cui.verify_cui(cui).inactiv_fiscal`, camp deja adaugat in Pasul 2
  dar niciodata cablat pana acum in ruta de risc fiscal) - rezultatul live
  suprascrie bifa manuala, indiferent in ce sens greseste ea. Daca ANAF nu
  raspunde (retea/timeout), se pastreaza bifa manuala (fallback, nu
  blocheaza evaluarea). `anaf_cui.verify_cui` a primit un camp nou,
  `data_inregistrare` (din `date_generale.data_inregistrare`, confirmat
  live din documentatia oficiala `doc_WS_V9.txt`).
- **entitate_noua**: nu se suprascrie (nu exista un prag numeric oficial de
  "nou infiintat" in Anexa 2 pe care sa-l aplicam automat), dar data reala
  de inregistrare de la ANAF e trimisa in raspuns (`verificari_automate.
  data_inregistrare_anaf`) si afisata contabilului ca reper dupa calcul.
- **fara_bunuri**: la fel, asistiv nu automat - `etva/importer/saft_d406.py`
  extrage acum si `sold_imobilizari` (suma soldurilor clasa 2, separat de
  cei 4 indicatori de scor), trimis ca `verificari_automate.
  sold_imobilizari_saft`; daca balanta arata imobilizari si totusi bifa e
  bifata, UI-ul afiseaza un avertisment explicit dupa salvare.
Celelalte 3 (cazier_fiscal, insolventa/BPI, fara_salariati/REVISAL) NU au
un API public oficial identificat - automatizarea lor ar insemna scraping
pe portaluri fara API documentat (fragil, zona gri ToS) - lasate deliberat
manuale, decizie in asteptare de la Andrei daca merita totusi investitia.

Actualizat manual 2026-08-10 (a cincea interventie): la intrebarea directa
"astea pot fi verificate dupa CUI, de ce nu faci asta" pentru grupele
B/C, s-a verificat CONCRET (nu presupus) fiecare caz, direct pe surse
oficiale ANAF/ONRC/BPI:
- **cazier_fiscal**: confirmat ca NU se poate - pentru o firma (CUI),
  cazierul fiscal se cere exclusiv prin SPV-ul PROPRIU al firmei (cu
  certificatul ei digital), nu exista o cautare publica dupa CUI ca la
  TVA. Ramane manual.
- **REVISAL (fara_salariati)**: confirmat ca NU se poate - numarul de
  salariati e o informatie protejata, fara API/pagina publica. Ramane
  manual.
- **obligatii restante** (indicator deja manual, nu Sectiunea B): ANAF
  publica trimestrial o "Lista contribuabililor cu obligatii restante",
  DAR doar peste praguri mari (100.000-500.000 RON dupa categorie) - ar
  rata sistematic restantele mici/medii ale clientilor tipici ai acestei
  aplicatii, deci NU e o sursa fiabila pentru auto-verificare. Ramane
  neschimbat (SPV, manual).
- **entitate_noua**: acum AUTOMATIZATA complet, la decizia lui Andrei -
  Anexa 2 nu are un prag oficial, dar Andrei a ales explicit "sub 12 luni"
  (`portal/app.py::RISC_FISCAL_PRAG_ENTITATE_NOUA_ZILE = 365`), aplicat pe
  `data_inregistrare` de la ANAF (acelasi apel live folosit si pentru
  declarat_inactiv) - suprascrie bifa manuala in ambele sensuri, cu
  fallback pe bifa daca data lipseste/nu poate fi interpretata. Checkbox-ul
  din UI e acum disabled, la fel ca la declarat_inactiv.
- **insolventa (BPI)**: Andrei a ales varianta "cont propriu ONRC (gratuit,
  dar fragil)" - RAMANE DE IMPLEMENTAT intr-o interventie viitoare (necesita
  cont ONRC real, creat de Andrei, plus cercetarea fluxului de autentificare
  al portal.onrc.ro pentru sectiunea BPI gratuita "persoane publicate in
  BPI" - nu e un API documentat, deci integrarea va fi pe baza de sesiune).

Actualizat manual 2026-08-11: **a treia sursa de date financiare - bilantul
depus la ANAF** (`etva/anaf_bilant.py`, modul nou). Serviciu web OFICIAL
ANAF, public, fara autentificare si fara cheie de API:
`https://webservicesp.anaf.ro/bilant?an=YYYY&cui=NNN` (listat pe anaf.ro ca
"informatii din situatiile financiare anuale"). Descoperit cautand raspuns
la intrebarea lui Andrei "de unde mai pot lua informatii public de la ANAF".

Ce aduce, confirmat live pe doua firme de marimi extreme (o
microintreprindere cu 1 salariat si o corporatie cu ~2900 salariati -
numerotarea I1..I20 a fost IDENTICA, deci maparea nu depinde de tipul de
formular depus):
- **Indicatorii 1-3 fara niciun efort**: I10 capitaluri, I7 datorii, I13
  cifra de afaceri, I18/I19 profit/pierdere (unificate intr-un rezultat cu
  semn). `sursa_date='bilant_anaf'` - a treia optiune, alaturi de SAF-T si
  manual. Coloana `sursa_date` e `TEXT` fara `CHECK` in toate cele 3 locuri
  de schema, deci valoarea noua NU a cerut nicio migrare.
- **Precompletarea a doua bife de Sectiunea B**: I20 (numar mediu salariati)
  -> `fara_salariati`, I1 (active imobilizate) -> `fara_bunuri`, printr-o
  ruta noua `GET /api/risc-fiscal/bilant`.

DECIZIE DE DESIGN, luata explicit impreuna cu Andrei: aceste doua bife se
**precompleteaza**, NU se suprascriu server-side ca `declarat_inactiv` /
`entitate_noua`. Motivul: acelea se verifica in timp real, pe cand bilantul
e ANUAL si decalat (descrie 31 decembrie al ultimului exercitiu depus), deci
o firma care a angajat luna trecuta ar aparea gresit "fara salariati". UI-ul
precompleteaza, arata anul de referinta si cere confirmarea contabilului.
Din acelasi motiv, raportul PDF afiseaza acum explicit sursa datelor
(`_eticheta_sursa`), cu mentiunea "31 decembrie" pentru bilant.

Securitate: la `sursa_date='bilant_anaf'` cifrele se iau EXCLUSIV
server-side dupa CUI; orice valoare din formular e ignorata, ca un client sa
nu poata trimite cifre inventate si sa le vada apoi in raport prezentate
drept date oficiale ANAF (vezi testul
`test_salveaza_risc_fiscal_sursa_bilant_ignora_cifrele_din_formular`).

Bilantul e tratat ca o comoditate, nu ca o dependenta: `_bilant_anaf()` din
`portal/app.py` inghite `AnafBilantError` si intoarce None, deci o
defectiune la ANAF nu blocheaza o evaluare care se poate face si din SAF-T
sau manual. CUI inexistent / an nedepus intorc HTTP 200 cu `"i": []`, deci
"fara date" nu e o eroare - `extrage_bilant()` cauta implicit anul trecut si
cade automat pe anul dinainte (necesar intre 1 ianuarie si termenul de
depunere din mai).

Verificat si respins ca surse de automatizare, in aceeasi cautare:
"Lista contribuabililor fara obligatii restante" (aplicatie JSF cu
ViewState, prea fragila de automatizat) si lista trimestriala de restantieri
(publica doar sumele mari, 100.000-500.000 RON dupa categorie, deci ar rata
sistematic clientii tipici ai aplicatiei).

Actualizat manual 2026-08-15: **firele de fundal se reconecteaza la
Postgres** (`portal/app.py::_ReqScopedConn._fallback_viu`).

Bug gasit din intamplare, verificand log-urile dupa un deploy: conexiunea
de rezerva (cea folosita in afara unui request HTTP) nu se reconecta
niciodata. Odata cazuta, ramanea moarta pana la repornirea procesului, iar
TOATE firele de fundal esuau tacut la fiecare tick - backup, remindere
trial, alerte de risc fiscal, schimbari de plan programate. Masurat pe
testare: `OperationalError: the connection is closed` la fiecare 30 de
minute, din 18:08 pana la 23:38 (5 ore si jumatate), reparat abia de un
restart intamplator. Postgres NU repornise in acel interval.

De ce a trecut neobservat atat: cererile HTTP nu sunt afectate deloc - ele
iau conexiuni din pool, care se reface singur - deci aplicatia parea
perfect sanatoasa din exterior.

Fix: `_current()` verifica `raw.closed` inainte sa intoarca conexiunea de
rezerva si o reface daca a murit, cu o linie de log cand se intampla.
Reconectarea e proactiva doar DUPA ce psycopg a marcat conexiunea inchisa
(adica din al doilea tick dupa cadere) - deliberat NU se reia automat
comanda esuata: pe un `commit`, de exemplu, nu se poate sti daca
tranzactia a ajuns pe server inainte sa cada legatura, iar o reluare oarba
ar putea aplica de doua ori sau raporta succes fals. Deci: un singur tick
pierdut, apoi auto-vindecare, in loc de blocare pana la restart.

Testele (`test_conexiunea_de_rezerva_*`, doar Postgres) au fost verificate
ca prind chiar bug-ul: cu comportamentul vechi injectat, esueaza cu exact
`OperationalError: the connection is closed`.

Actualizat manual 2026-08-15: **facturile cu probleme se vad imediat**, la
cererea lui Andrei ("utilizatorul trebuie sa stie imediat care sunt
facturile cu probleme").

Context masurat inainte de a decide: in productie, TOATE cele 16
reconcilieri reale sunt in modul `d300_lines` - modul pe categorii e
practic nefolosit. In modul pe categorii diferentele erau deja per factura
(deci intrebarea era deja rezolvata acolo), dar in `d300_lines` utilizatorul
vedea doar LINII cu delta si trebuia sa apese "Vezi facturile" pe fiecare,
una cate una, ca sa descopere care factura e de vina. Informatia exista
(heuristica din `etva/engine.py`), dar era ascunsa in spatele a N click-uri.

Ce s-a adaugat:
- `portal/app.py::_facturi_liniei` - extras din ruta de drill-down, ca sa
  fie refolosit; `_facturi_suspecte(fc, rid)` aduna candidatii din TOATE
  liniile cu diferente intr-o singura trecere.
- Ruta noua `GET /api/reconciliations/<id>/facturi-suspecte`, plus cardul
  "Facturi de verificat" si contorul din capul paginii (`web/index.html`),
  incarcate automat dupa rezultate.
- Foaia "Facturi de verificat" in exportul Excel, asezata PRIMA
  (`etva/export.py::write_report_lines`, argumente noi optionale deci
  apelantii vechi primesc acelasi fisier ca inainte).

Decizia care conteaza cel mai mult aici - `linii_neelucidate`: heuristica
NU cauta cand delta e negativa (daca ANAF are mai mult decat firma,
vinovatul nu poate fi printre facturile firmei - lipseste una). Fara
tratare explicita, o astfel de linie ar aparea tacut ca "nimic de
verificat", desi are o problema reala. De aceea liniile fara candidat sunt
raportate separat, cu motivul lor (`_motiv_linie`): lipsa in jurnal, lipsa
la ANAF, fara potrivire, sau necautat (peste plafon).

Plafonul `MAX_LINII_CAUTATE_AUTOMAT = 12` e o plasa de siguranta, nu o
optimizare: masurat pe cazul cel mai prost (delta care nu se potriveste cu
nimic), o linie costa ~0.06s la 50-200 de facturi si SCADE sub 0.01s peste
500, fiindca plafonul de combinatii din engine taie devreme.

NU s-a facut lista completa a tuturor facturilor cu cele problematice
colorate (varianta ceruta literal): la sute-mii de facturi pe perioada ar
muta aceeasi frustrare in alta parte. Ramane disponibila daca Andrei o cere
explicit.

STARE ACTUALA A MODULULUI (2026-08-11, decizie Andrei): **RISC FISCAL E
MARCAT "IN DEZVOLTARE"** - nu e gata pentru utilizatori. Marcajul e pus in
TOATE locurile unde modulul se prezinta sau produce iesiri vizibile:
- `docs/index.html` (pagina publica): cardul de prezentare, cardul de pret
  (unde se spune explicit ca nu poate fi contractat si ca preturile sunt
  orientative) si intrarea de FAQ.
- `web/index.html` (SPA): eticheta pe titlul tab-ului + caseta de avertisment.
- `portal/templates/alege_plan.html`: avertisment inainte de alegerea
  nivelului (locul unde firma il contracteaza efectiv).
- `portal/templates/master_nomenclator.html`: avertisment la configurarea
  preturilor.
- `portal/risc_fiscal_report.py`: banner in capul raportului PDF
  (`TEXT_IN_DEZVOLTARE` + `_caseta_in_dezvoltare`). Obligatoriu fiindca
  PDF-ul circula independent de aplicatie - se salveaza, se trimite pe
  email, ajunge in dosar - deci cititorul poate sa nu fi vazut niciodata
  eticheta din interfata.
- `portal/risk_alerts.py`: prefix "[IN DEZVOLTARE]" in SUBIECTUL emailului
  de alerta + avertisment in corp. Alerta pleaca automat, fara ca cineva
  s-o citeasca inainte, deci stadiul trebuie vizibil din inbox.
La lansare, cauta sirul "IN DEZVOLTARE" / "în dezvoltare" in aceste fisiere
ca sa le scoti pe toate odata.

ATENTIE la promovarea in productie: productia NU ruleaza pe acest VPS
(cutover 2026-08-04 catre 92.114.3.68 - vezi [[etva-capacitate-server]]).
Pe acest server, `etva-productie.service` e inactiv si dezactivat, iar
checkout-ul din /opt/etva-productie/app e vechi (nu contine deloc modulul).
Site-ul public ereconciliere.ro, servit de celalalt server, PREZINTA INCA
modulul ca disponibil si vandabil - marcajele de mai sus ajung acolo doar
la o promovare explicita in productie.

Actualizat manual 2026-08-11 (a doua interventie): **verificare incrucisata
+ istoric multi-anual**, ambele pe acelasi serviciu de bilant.

`anaf_bilant.compara_cu_bilant(date_financiare, bilant)` confrunta cifrele
venite de la contabil (SAF-T sau manual) cu ultimul bilant depus. Nu
valideaza contabilitatea - prinde doar fisierul gresit / firma gresita /
virgula pusa aiurea. Doua decizii importante de calibrare, ca avertismentul
sa nu devina zgomot ignorat:
- se compara DOAR pozitiile de bilant care evolueaza lent (capitaluri
  proprii, datorii). Cifra de afaceri si rezultatul net sunt EXCLUSE
  deliberat: sunt cumulate de la inceputul anului, deci in mod normal
  fractiuni din valoarea anuala la orice moment din cursul anului - o
  comparatie directa ar da alarme false la fiecare evaluare.
- se semnaleaza doar schimbarea de semn a capitalurilor proprii (singura
  care muta indicatorul 1 cu 100 de puncte) si diferentele de cel putin
  10x; sub 1000 RON nu se semnaleaza nimic.

`anaf_bilant.extrage_istoric(cui, ani=3)` aduce ultimele 3 exercitii depuse
(sare peste anii fara depunere in loc sa se opreasca la primul gol) si
alimenteaza un tabel de evolutie in raportul PDF - context comercial real
(capitaluri/datorii/cifra/rezultat/salariati, an de an).

Schema noua: `risc_fiscal_perioade.bilant_istoric` (TEXT/JSON, cele 3 locuri
obisnuite + migrare SQLite `_migrate_add_risc_fiscal_bilant_istoric`).
Istoricul se STOCHEAZA la evaluare, NU se ia live la generarea PDF-ului:
un raport de risc fiscal redescarcat peste un an trebuie sa arate ce se stia
la momentul evaluarii, nu date noi (reproductibilitate de document de
audit). Ca efect secundar, generarea PDF-ului ramane complet offline.

Optimizare: istoricul se ia O SINGURA DATA pe evaluare si serveste toate
cele trei scopuri (sursa de date cand `sursa_date='bilant_anaf'` - primul
element E bilantul folosit; verificarea incrucisata; tabelul din PDF).
Masurat live: ~0.4s pentru 3 ani.

Actualizat manual 2026-08-10 (a sasea interventie): la cererea lui Andrei
("daca sunt mai multe facturi diferenta... ar trebui subliniate liniile cu
facturile problema"), `find_candidate_invoices` (`etva/engine.py`, §5b)
cauta acum subseturi de pana la 4 facturi (nu doar 1 sau o pereche) a caror
suma explica o diferenta pe linie D300, marcand `candidat=true` pe toate
facturile din subsetul gasit - endpoint-ul (`get_reconciliation_facturi`,
§3q) si frontend-ul (`toggleFacturiLinie`, §2d/tabelul de diferente) erau
deja generice pentru orice numar de facturi candidat, deci n-au avut nevoie
de nicio schimbare. Cautarea se opreste la primul subset de marime minima
gasit (o factura bate o pereche, o pereche bate un triplet etc.) si refuza
sa marcheze ceva daca la aceeasi marime exista doua subseturi cu sume
diferite care se potrivesc amandoua (ambiguitate reala) - cu exceptia
facturilor cu sume identice intre ele, unde nu e ambiguitate, ci aceeasi
explicatie. Plafonat la marimea 4 (dincolo de atat, potrivirile
intamplatoare devin prea frecvente la un numar tipic de facturi pe linie)
si la un buget de combinatii `C(n,k) <= 45_000` per marime (reproduce exact
vechiul plafon `n<=300` de la perechi) - peste plafon, cautarea se opreste
silentios la marimea anterioara, la fel ca inainte cand o linie avea peste
300 de facturi.

Actualizat manual 2026-08-10 (a saptea interventie): testand pe un caz
real (delta 4214.96/885.48, nicio combinatie de facturi nu explica exact
diferenta, dar o factura era la 0.10/0.36 lei distanta), Andrei a cerut un
al doilea nivel, mai slab, de sugestie. Functie noua `find_closest_invoices`
(`etva/engine.py`, §5b) - apelata de `get_reconciliation_facturi` (§3q)
DOAR cand `find_candidate_invoices` n-a gasit nicio potrivire exacta -
cauta acelasi fel de subseturi (marime 1-4, acelasi plafon de combinatii),
dar dupa cea mai mica DISTANTA combinata fata de delta (nu potrivire
exacta), acceptand un raspuns doar daca cel mai apropiat subset e sub un
prag relativ la marimea diferentei (1%, intre 0.50 si 50 lei) SI de cel
putin 2x mai aproape decat a doua cea mai apropiata suma de aceeasi
marime - altfel nu exista un raspuns clar si nu se ghiceste. Raspunsul
`/api/reconciliations/<id>/facturi` are acum si campul `aproximativ` per
factura (langa `candidat`, existent). Frontend (`toggleFacturiLinie`,
§2d): randurile `aproximativ` primesc un chenar galben PUNCTAT
(`tr.factura-apropiata`, `outline: dashed`) si chipul "Cea mai apropiata",
distinct de chenarul rosu SOLID + chipul "Posibila cauza" de la o
potrivire confirmata - aceeasi interventie a eliminat si nota explicativa
afisata deasupra listei de facturi (nu mai era necesara odata ce
evidentierea vorbeste de la sine).

Incident 2026-08-10: promovarea de mai sus in productie a scos site-ul din
functiune (503, ~30 min) - NU din cauza schimbarilor de azi (D300), ci
pentru ca `main` era ramas mult in urma: modulul Risc Fiscal (2026-08-09)
si schimbarea self-service a abonamentului (2026-08-10) au ajuns pe
dev/testare cu schema Postgres aferenta niciodata migrata pe productie
(go2). Butonul de promovare a impins tot codul acumulat pe `main` +
restart pe go2 dintr-o data; gunicorn a intrat in bucla de crash la
pornire (`RuntimeError` din `etva/pg.py::verify_schema` - coloane/tabele
lipsa: `firms.risc_fiscal_nivel`, `firms.abonament_activ_pana`,
`payments.tip`, `payments.reconcilieri_lunare_estimate_nou`,
`payments.risc_fiscal_nivel_nou`, `payments.contract_id`, tabelele
`nomenclator_module`/`plan_schimbari_programate`/`risc_fiscal_perioade`/
`risc_fiscal_alerte`). **Gotcha de retinut**: scriptul
`etva-promoveaza-productie.sh` raporteaza "ok" doar pe baza codului de
iesire al `systemctl restart` - NU verifica daca serviciul chiar a ramas
sus - deci un crash-loop imediat dupa restart nu e detectat automat de
pipeline (de investigat: un `sleep`+`systemctl is-active` dupa restart, in
script). Remediere: rollback rapid pe go2 la ultimul commit bun
(`git reset --hard 44679c0` + restart, fara sa atinga DB) cat s-a
diagnosticat cauza, apoi backup (`pg_dump -Fc`, verificat cu
`pg_restore --list`) + aplicarea manuala a `etva/pg_schema.sql` (idempotent,
`psql -v ON_ERROR_STOP=1 -d etva_productie < pg_schema.sql` - **fara
`-1`**: fisierul are `CREATE INDEX CONCURRENTLY` la final, incompatibil cu
o singura tranzactie) direct pe go2, apoi re-deploy la `cbd42f6`. **Gotcha
de retinut #2**: `sudo -u postgres pg_dump/psql -f <fisier>` esueaza cu
"Permission denied" daca fisierul e in `/root` (mod 700, userul
`postgres` nu poate traversa directorul) - solutie: redirectare
shell (`< fisier` / `> fisier`), nu `-f`/`-f`, ca fisierul sa fie deschis
de shell-ul root, nu de procesul `postgres`. Concluzie pe termen lung:
promovarile mari (schema noua) ar trebui migrate pe productie **inainte**
sau **odata cu** codul, nu descoperite abia la crash - de discutat daca
`promote_to_productie` ar trebui sa verifice `verify_schema()` pe go2
inainte de push, ca sa blocheze promovarea cu un mesaj clar in loc sa
scoata site-ul din functiune.

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
| 3 | la nivel de modul: `app = create_app(data_dir(), enable_backup_scheduler=True, enable_trial_reminder_scheduler=True, enable_risk_alerts_scheduler=True)` - se executa o singura data, la incarcare |
| 4 | `app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)` - ca `url_for(_external=True)` sa produca `https://` cand Apache seteaza `X-Forwarded-Proto` |

**Constrangere critica**: gunicorn MUST rula cu exact 1 worker process (threads sunt ok). Cele 3 fire de fundal + conexiunea SQLite/SQLCipher + `db_lock` sunt in-process; 2 workeri = conexiuni/lock-uri neserializate = coruptie posibila.

### 1b. `python -m portal.run` (dezvoltare fara gunicorn)

Acelasi lant, dar prin `run.py`: `data_dir()` -> `create_app(data_dir(), enable_backup_scheduler=True, enable_trial_reminder_scheduler=True, enable_risk_alerts_scheduler=True)` -> `.run(host="127.0.0.1", port=ETVA_PORT)`.

### 1c. `data_dir()` (in `run.py`, reutilizata de `wsgi.py`, `seed_master.py`, `migrare_pg.py`)

Citeste env `APPDATA` (fallback `~`) + env `ETVA_DATA_DIR` (default `"eTVA-Portal"`) -> `os.makedirs(d, exist_ok=True)` -> intoarce calea.

Pe testare/productie: `HOME=/opt/etva-{testare,productie}` + `ETVA_DATA_DIR=eTVA-Portal-{Testare,Productie}` (setate in unit-ul systemd) -> `data_dir()` = `/opt/etva-{testare,productie}/eTVA-Portal-{Testare,Productie}`.

### 1d. `create_app(data_dir, enable_backup_scheduler, enable_trial_reminder_scheduler, enable_risk_alerts_scheduler)` (portal/app.py)

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
| 18 | ~4040 | **daca** `enable_trial_reminder_scheduler`: `remind_mod.start_scheduler(conn, db_lock, _trimite_email)` - porneste thread-ul de remindere trial (dupa ce `_trimite_email` exista deja) |
| 18b | ~4047 | **daca** `enable_risk_alerts_scheduler`: `risk_alerts_mod.start_scheduler(conn, firm_conn, db_lock, _trimite_email)` - primeste si closure-ul `firm_conn`, spre deosebire de `remind_mod` (are nevoie de baza per-firma pentru `risc_fiscal_perioade`, nu doar de `firms`) |
| 19 | ~4053 | expune `app.portal_conn`, `app.firm_conn`, `app.portal_secret`, `app.get_valid_anaf_access_token` (folosite de teste/seeding) |
| 20 | final | `return app` |

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

### 2h. Flux: risc fiscal (modul premium)

```
intraInAplicatie(ident) - adaugat:
  riscFiscalNivel = ident.risc_fiscal_nivel
  navRiscFiscal.style.display = riscFiscalNivel ? '' : 'none'
  rfCompletFields.style.display = riscFiscalNivel === 'complet' ? '' : 'none'
  campRfClient.style.display = firmaDirecta ? 'none' : ''

incarcaClienti() - extins sa populeze si #rfClient (aceleasi optiuni ca
  selClient/alocClient), daca elementul exista in DOM.

click nav "Risc fiscal" -> navigheaza('riscFiscal') + incarcaIstoricRiscFiscal()

incarcaIstoricRiscFiscal():
  1. clientId = campRfClient vizibil ? rfClient.value : '' (firma directa)
  2. api('/api/risc-fiscal/istoric' + qs) GET -> backend: istoric_risc_fiscal (§3r)
  3. populeaza #tabelRiscFiscal (perioada/nivel/scor/clasificare + link PDF
     per rand catre /api/risc-fiscal/perioada/<perioada>/pdf)

salveazaRiscFiscal():
  1. valideaza client selectat (firme de contabilitate)
  2. construieste FormData: client_id?, perioada, capitaluri_proprii,
     datorii_totale, cifra_afaceri, rezultat_net + (doar nivel 'complet')
     declaratii_nedepuse, obligatii_restante, obligatii_crescute,
     flag_<cheie> per checkbox .rfFlag bifat (cele 9 din Sectiunea B ANAF)
  3. api('/api/risc-fiscal/perioada', POST) -> backend: salveaza_risc_fiscal_perioada (§3r)
  4. daca ok: incarcaIstoricRiscFiscal()
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
| POST | `/master/firma/<int:firm_id>/risc-fiscal/nivel` | `seteaza_risc_fiscal_nivel` |
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
| POST | `/master/backup/postgres/restaureaza` | `restaureaza_backup_postgres` |

```
restaureaza_backup: verifica mediu != productie -> verifica backend !=
  postgres -> verifica confirm=="da" -> backup_mod.validate_backup_zip
  -> backup_mod.create_backup + prune_old_backups (snapshot de siguranta
  inainte de restore) -> _log_master_action -> inchide conn + toate
  firm_conns -> backup_mod.restore_backup(data_dir, fisier)

restaureaza_backup_postgres (adaugat 2026-08-04, doar mediul testare):
  verifica mediu != productie -> verifica mediu == testare -> verifica
  backend == postgres -> confirmare == backup_pg.nume_baza(DATABASE_URL)
  -> sursa 'local:<data>' (validata fata de backup_pg.list_local_backups,
  citit din manifest) SAU 'upload' (backup_pg.save_uploaded_dump, verifica
  doar magic number gzip - continutul real e validat de scriptul root) ->
  _log_master_action (inainte de trigger - vezi comentariu din cod) ->
  backup_pg.request_restore(data_dir, sursa) [scrie trigger cu 2 linii:
  moment + sursa] -> raspuns HTML simplu (NU redirect - serverul se
  opreste imediat, un redirect ar lovi conexiunea inchisa la GET-ul
  urmator). Executia reala e in afara procesului Flask, ca la
  pull_testare/promote_to_productie (§3p): unitate systemd .path
  root-owned + /usr/local/sbin/etva-restore-pg.sh - vezi
  planning/restaurare-postgres.md pentru mecanismul complet (creeaza baza
  temporara OWNER etva_app, incarca dump-ul cu psql ca postgres, 4
  verificari de sanitate, swap prin ALTER DATABASE ... RENAME intr-o
  tranzactie, verifica reconectarea etva_app prin TCP, repornire serviciu
  garantata printr-un trap pe EXIT). Baza veche NU se sterge automat -
  ramane `<baza>_prev_<moment>`.

master_backup (GET) - cand backend==postgres, paseaza in plus
  restaurare_pg (dict): baza, backups (backup_pg.list_local_backups),
  manifest_la, stare (pipeline.read_status pe RESTORE_STATUS_NAME),
  probleme_schema (etva.pg.verify_schema(conn.raw), None daca verificarea
  insasi esueaza - nu trebuie sa rupa panoul).
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
| POST | `/master/nomenclator/risc-fiscal` | `salveaza_preturi_risc_fiscal` |
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
| POST | `/master/pipeline/testare/actualizeaza` | `pull_testare` |
| POST | `/master/pipeline/productie/promoveaza` | `promote_to_productie` |
| POST | `/master/server/restart` | `restart_server` |
| POST | `/master/backup-onedrive` | `master_backup_onedrive` |

```
pipeline_dashboard: pipeline.local_pipeline_available() -> daca True:
  per env pipeline.branch_info(env) -> per promotie posibila
  pipeline.ahead_count + pipeline.can_promote -> render_template(istoric=
  pipeline.history(conn))
  daca False (VPS deployat) si mediu=='testare': citeste in plus
  pipeline.read_status pt. PULL_TESTARE_STATUS_NAME si
  PROMOTE_PRODUCTIE_STATUS_NAME, afisate in cele doua carduri noi

promote_environment: pipeline.promote(source, target) [poate arunca
  PipelineError] -> pipeline.log_promotion(...) -> redirect

pull_testare (doar pe mediul testare): verifica pipeline.own_environment()
  -> pipeline.request_testare_pull(data_dir) [scrie trigger file] ->
  redirect. Executia reala e in afara procesului Flask - vezi
  /usr/local/sbin/etva-testare-pull.sh (unitate systemd .path root-owned,
  aplicatia n-are credentiale git). CORECTAT 2026-08-04: fluxul e acum
  dev -> testare (branch-ul dev e punctul de intrare al dezvoltarii, nu
  doar un pull pe branch-ul testare direct): verifica checkout curat ->
  git fetch origin dev -> git merge --ff-only
  origin/dev (checkout-ul local, pe branch testare, avanseaza la varful
  lui dev) -> git push origin testare (branch-ul testare de pe GitHub
  reflecta aceeasi stare) -> pip install -> systemctl restart
  etva-testare -> scrie status.

promote_to_productie (doar pe mediul testare): verifica
  pipeline.own_environment() -> pipeline.request_promote_to_productie(data_dir)
  [scrie trigger file] -> redirect. Executia reala:
  /usr/local/sbin/etva-promoveaza-productie.sh (root-owned): verifica
  checkout curat -> verifica fast-forward fata de origin/main (foloseste
  FETCH_HEAD, nu origin/main - checkout-ul testare urmareste doar
  branch-ul lui) -> git push origin HEAD:main -> SSH pe go2 (productie):
  git pull --ff-only origin main + pip install + systemctl restart
  etva-productie -> scrie status. NU aplica schimbari de schema DB -
  ramane pas manual, separat (decizie explicita).
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

`me` (`/api/me`) - extins cu `risc_fiscal_nivel` (`ident.get(...)`, None
daca modulul nu e activat pentru firma) - SPA-ul il foloseste ca sa
arate/ascunda tab-ul nou (§2h). Masterul primeste mereu `'complet'` din
`current_identity()` (§4), ca sa poata testa fluxul complet fara sa
activeze plata modulul.

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

### 3r. Risc Fiscal (modul premium)

| Metoda | Path | Handler | Permisiune |
|---|---|---|---|
| POST | `/api/risc-fiscal/perioada` | `salveaza_risc_fiscal_perioada` | `reconciliere.creare` |
| GET | `/api/risc-fiscal/istoric` | `istoric_risc_fiscal` | (niciuna, doar `require()`) |
| GET | `/api/risc-fiscal/perioada/<perioada>/pdf` | `risc_fiscal_pdf` | `rapoarte.export` |

Gating pe `firms.risc_fiscal_nivel` (verificat manual in fiecare handler,
NU o permisiune noua din `etva/db.py::PERMISSIONS` - vezi comentariul din
cod) - `reconciliere.creare`/`rapoarte.export` sunt reutilizate pt.
apropierea semantica (creezi o evaluare / exporti un raport), ca sa nu
umfle catalogul de permisiuni pt. un add-on comercial separat.

```
_client_id_din_request(ident) [helper local, vezi §4] -> firma directa:
  (None, None); altfel: client_id din form/query, eroare daca lipseste.

salveaza_risc_fiscal_perioada:
  require("reconciliere.creare") -> verifica ident.risc_fiscal_nivel (403
  daca None) -> _client_id_din_request -> valideaza perioada -> parseaza
  date_financiare (capitaluri_proprii/datorii_totale/cifra_afaceri/
  rezultat_net) -> daca nivel=='complet': parseaza declaratii_nedepuse/
  obligatii_restante/obligatii_crescute + flag_<cheie> per
  etva.risc_fiscal.FLAGURI_SECTIUNE_B -> etva.risc_fiscal.calculeaza_scor(...)
  -> firm_conn(firm_id) -> etva.risc_fiscal_store.salveaza_perioada(...)
  [upsert pe (client_id, perioada) - vezi §5r] -> audit.log(...,
  "risc_fiscal.evaluare") -> jsonify(scor + detaliu)

istoric_risc_fiscal: require() -> verifica nivel activat ->
  _client_id_din_request -> firm_conn -> etva.risc_fiscal_store.
  lista_perioade(fc, client_id) -> jsonify (lista, cea mai recenta
  perioada prima)

risc_fiscal_pdf: require("rapoarte.export") -> verifica nivel activat ->
  _client_id_din_request -> firm_conn -> etva.risc_fiscal_store.
  obtine_perioada(...) -> 404 daca lipseste -> daca client_id: SELECT
  name/cui din clients (pt. antetul raportului) -> portal.risc_fiscal_report.
  generate_pdf(...)  [§6.10] -> audit.log(..., "risc_fiscal.export_pdf")
  -> Response(pdf_bytes, mimetype="application/pdf")
```

Rutele master conexe (§3g, §3o): `seteaza_risc_fiscal_nivel` (forteaza
`firms.risc_fiscal_nivel`, tipar identic `toggle_firm`) si
`salveaza_preturi_risc_fiscal` (3 campuri per nivel din `nomenclator_module`
- `pret_lunar_ron`/`rapoarte_incluse`/`pret_raport_extra_ron` - tipar
similar `salveaza_pachet_reconcilieri`, dar cu 2x3 campuri in loc de 3).

**Facturare cu prag inclus (decizie Andrei, 2026-08-10)**: abonamentul
lunar (200 RON simplu / 350 RON complet) include 5 rapoarte/luna; peste
prag, fiecare raport suplimentar generat in luna calendaristica curenta se
factureaza la `pret_raport_extra_ron` (50/100 RON) - vezi
`_rapoarte_risc_fiscal_luna_curenta`/`_cost_modul_risc_fiscal` in §4. Un
"raport" = o evaluare distincta per (client, perioada) - resubmisia
aceleiasi perioade (upsert, §5s) nu se numara a doua oara.

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
| `_luni_pentru_ciclu(ciclu)` / `_pachete_extra_lunare(firm)` / `_rapoarte_risc_fiscal_luna_curenta(fc)` / `_cost_modul_risc_fiscal(firm)` / `_calculeaza_suma_plata(firm, ciclu)` / `_suma_cu_tva(suma)` | Calcule de facturare/abonament - `_rapoarte_risc_fiscal_luna_curenta` numara randurile `risc_fiscal_perioade` (orice client) create/actualizate in luna curenta; `_cost_modul_risc_fiscal` = abonamentul din `nomenclator_module` + (rapoarte peste prag) x `pret_raport_extra_ron`, 0.0 daca `risc_fiscal_nivel` e None |
| `_client_id_din_request(ident)` | Firma directa -> `(None, None)`; altfel client_id din form/query, eroare daca lipseste - folosit de rutele §3r |
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

find_candidate_invoices(rows, delta_base, delta_vat) -> nu apeleaza
  altceva - cauta cel mai mic subset de facturi (marime 1-4) a caror suma
  explica deltul unei linii, apelata din get_reconciliation_facturi (§3q)
  ca sa marcheze randurile "candidat" in raspunsul "Vezi facturile"

find_closest_invoices(rows, delta_base, delta_vat) -> nu apeleaza altceva
  - ghicit mai slab, apelata din get_reconciliation_facturi (§3q) DOAR
  cand find_candidate_invoices n-a gasit nimic, ca sa marcheze randurile
  "aproximativ" (subsetul cu cea mai mica distanta fata de delta, daca e
  clar mai aproape decat alternativele)
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

Extins 2026-08-09: dict-ul returnat mai poate avea (doar daca raspunsul
ANAF le contine efectiv - confirmat din documentatia oficiala v9,
`doc_WS_V9.txt`, nu ghicit) `inactiv_fiscal`/`data_inactivare`/
`data_reactivare` (din `stare_inactiv`), `tva_incasare`/
`data_inceput_tva_incasare`/`data_sfarsit_tva_incasare` (din
`inregistrare_RTVAI`) si `inregistrat_ro_efactura` (din
`date_generale.statusRO_e_Factura`) - fara schimbare de semnatura,
backward-compatible. Folosit de modulul Risc Fiscal (§5r) pentru flag-ul
automat "declarat inactiv" din Sectiunea B ANAF.

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

### 5r. etva/risc_fiscal.py (scoring, pur - fara I/O)

Implementeaza indicatorii 1-5 din Anexa 2 ANAF ("Fisa indicatorilor de
risc fiscal") - indicatorii 6-8 (istoric rambursari TVA, date interne
ANAF) NU sunt implementati, apar mereu ca "neaplicabil" in `detaliu`.
Pragul/etichetele oficiale ANAF ("risc mic/mediu/mare", prag >=60 puncte
pe toti cei 8 indicatori) NU sunt folosite - `clasificare` e o eticheta
PROPRIE ("scazut"/"moderat"/"ridicat") pe un scor normalizat 0-100.

```
calculeaza_scor(nivel, date_financiare, declaratii_nedepuse=None,
                obligatii_restante=None, obligatii_crescute=None,
                flaguri_sectiune_b=None) -> ScorRiscFiscal:
  1. valideaza nivel in NIVELURI (altfel ValueError)
  2. _indicator_capitaluri_proprii, _indicator_grad_indatorare,
     _indicator_profitabilitate (mereu, din date_financiare)
  3. daca nivel=='complet': _indicator_declaratii_nedepuse,
     _indicator_obligatii_restante (altfel marcate "necesita nivelul complet")
  4. indicatorii 6-8: mereu marcati "date interne ANAF" (niciodata insumati)
  5. flaguri_sectiune_b (doar la 'complet'): daca vreun flag e True ->
     override_sectiune_b=True -> clasificare FORTATA 'ridicat' (fidel
     metodologiei oficiale - Sectiunea B e un override categoric,
     independent de cati indicatori din Sectiunea C sunt implementati)
  6. altfel: _clasifica(scor_afisat) pe praguri proprii (<34/34-66/>66)
```

Constanta `FLAGURI_SECTIUNE_B` (9 chei -> etichete) - reutilizata de
`portal/app.py` (parsare form `flag_<cheie>`) si `web/index.html`
(checkbox-urile hardcodate din tab-ul Risc Fiscal, §2h).

### 5s. etva/risc_fiscal_store.py (persistenta - upsert pe tabela per-firma)

```
salveaza_perioada(conn, client_id, perioada, sursa_date, date_financiare,
                  scor, ..., username) -> int (id-ul randului):
  INSERT ... ON CONFLICT (client_id, perioada) DO UPDATE SET ... [ramura
  client_id NOT NULL] SAU ON CONFLICT (perioada) [SQLite] / (firm_id,
  perioada) [Postgres, dbcompat.backend()] WHERE client_id IS NULL DO
  UPDATE [ramura firma directa - branching pe backend, la fel ca
  etva/cod_mappings.py::save_mapping si pentru exact acelasi motiv: doua
  firme directe pot folosi aceeasi `perioada`] -> SELECT id (portabil pe
  ambele backend-uri, evita sa se bazeze pe lastrowid/RETURNING pe conflict)

lista_perioade(conn, client_id) -> toate perioadele unui client (sau ale
  scope-ului 'direct'), cea mai recenta prima - foloseste _decodeaza
  (json.loads pe flaguri_sectiune_b/scor_detaliu, stocate `text`, NU
  `jsonb` - acelasi tipar ca differences.details)

perioade_cu_risc_ridicat(conn) -> toate perioadele (orice client) cu
  clasificare='ridicat', JOIN clients pt. nume/CUI - folosit exclusiv de
  portal/risk_alerts.py (§6.11), scaneaza firma intreaga dintr-o data

obtine_perioada(conn, client_id, perioada) -> un rand, sau None
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

**Adaugat 2026-08-04** (doar pe `testare` - productia s-a mutat pe un VPS
separat, go2, nu mai e un worktree local; vezi §3p pentru rutele care le
folosesc):

```
request_testare_pull(data_dir) - scrie fisier trigger PULL_TESTARE_TRIGGER_NAME
request_promote_to_productie(data_dir) - scrie fisier trigger PROMOTE_PRODUCTIE_TRIGGER_NAME
read_status(data_dir, filename) -> parseaza "stare|moment ISO|mesaj",
  scris de scripturile root-owned de mai sus (acelasi format ca
  backup-onedrive.status) - None daca fisierul nu exista/e corupt
```

Ambele trigger-e sunt vazute de unitati systemd `.path` root-owned
(aplicatia ruleaza fara privilegii, fara credentiale git) - vezi
scripturile `/usr/local/sbin/etva-testare-pull.sh` si
`/usr/local/sbin/etva-promoveaza-productie.sh`, nu doar cod Python.

### 6.5 portal/invoicing.py

`next_invoice_number(conn, serie)` (sub `db_lock`). `generate_pdf(invoice)`
-> `pdf_fonts.asigura_fonturi()` -> `SimpleDocTemplate`/`Table`/`Paragraph`
(reportlab) -> `_suma()`. Constanta `FURNIZOR` reutilizata de `contract.py`.

Constanta `NOTIFICARE_CONTRACT_FINALIZAT_EMAIL` (adaugata 2026-08-04,
corectata tot atunci) - destinatarul emailului trimis de
`_finalizeaza_contract_esemneaza` (`portal/app.py`) cand un contract e
semnat de ambele parti prin eSemneaza. E o constanta separata de
`FURNIZOR['email']` (folosit pentru cererea initiala de semnatura), dar
azi ambele au aceeasi valoare (`office@ereconciliere.ro`) - separarea
structurala ramane utila daca se decide vreodata o adresa diferita pentru
notificarea de finalizare.

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

### 6.7b portal/backup_pg.py (adaugat 2026-08-04)

Modul separat de `portal/backup.py` (acela e exclusiv SQLite/zip -
extinderea lui ar fi contrazis propriul docstring). Structural sora lui
`portal/pipeline.py`, nu a lui `backup.py`: acelasi mecanism
trigger-file + unitate systemd `.path` root-owned + fisier de stare
`stare|moment|mesaj` (citit cu `pipeline.read_status`, nu duplicat aici).

| Functie | Context de apel |
|---|---|
| `nume_baza(dsn)` | LA CERERE (`master_backup`, ruta de restore) - si fraza de confirmare afisata/verificata |
| `list_local_backups(data_dir)` | LA CERERE - populeaza selectorul din panou, citeste manifestul |
| `manifest_updated_at(data_dir)` | LA CERERE - afiseaza prospetimea listei |
| `save_uploaded_dump(data_dir, fisier)` | LA CERERE, sursa 'upload' - verifica doar magic number gzip, salveaza sub nume FIX (niciodata `fisier.filename`) |
| `request_restore(data_dir, sursa)` | LA CERERE - scrie trigger-ul (2 linii: moment ISO + sursa) |
| `sterge_incarcare(data_dir)` | LA CERERE, pe orice cale de refuz **si** de scriptul root la final, indiferent de rezultat |

Manifestul (`backup-pg.manifest`, root:root 0644) e scris de
`/usr/local/sbin/etva-backup-pg.sh` (in afara repo-ului) dupa fiecare
backup reusit - aplicatia nu are acces la `/root/backup-pg` (0700 root,
dumpuri necriptate), deci nu poate lista backup-urile direct. Executia
reala a restaurarii: `/usr/local/sbin/etva-restore-pg.sh` (nou, in afara
repo-ului) - mecanism complet documentat in
`planning/restaurare-postgres.md`.

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

### 6.10 portal/risc_fiscal_report.py

`generate_pdf(*, firm_name, firm_cui, client_name, perioada)` -> aceleasi
conventii vizuale ca `invoicing.py::generate_pdf` (reportlab,
`pdf_fonts.asigura_fonturi()`, `SimpleDocTemplate`/`Table`/`Paragraph`).
Include OBLIGATORIU (ultimul paragraf) disclaimer-ul de nonechivalenta cu
clasificarea oficiala ANAF - vezi `etva/risc_fiscal.py` pentru motiv.

### 6.11 portal/risk_alerts.py

Al treilea scheduler de fundal (dupa `backup.py`/`trial_reminders.py`) -
vezi §7 pentru tabelul comparativ. Diferenta structurala: are nevoie si de
`firm_conn` (nu doar de conexiunea portalului), pentru ca
`risc_fiscal_perioade` traieste in baza per-firma.

| Functie | Context de apel |
|---|---|
| `_semnatura(perioada)` | intern - `clasificare\|scor_afisat\|flaguri_active_sortate`, idempotenta alertarii |
| `_continut_email(client_nume, perioada)` | intern |
| `_alerteaza_firma(fc, firma_nume, email, trimite_email_fn)` | intern, per firma |
| `verifica_si_alerteaza(portal_conn, firm_conn_fn, trimite_email_fn)` | THREAD **si** apelabil direct (nicio ruta manuala inca, spre deosebire de remindere-trial - vezi planul modulului) |
| `start_scheduler(portal_conn, firm_conn_fn, lock, trimite_email_fn)` | **punct de intrare thread** - vezi §7 |

```
verifica_si_alerteaza:
  firms WHERE active AND risc_fiscal_nivel IS NOT NULL AND arhivata_la IS NULL
  -> per firma: _email_admin_firma(portal_conn, firm_id)  [reutilizat direct
     din portal.trial_reminders, nu duplicat] -> daca fara email: skip
  -> firm_conn_fn(firm_id) -> _alerteaza_firma:
       etva.risc_fiscal_store.perioade_cu_risc_ridicat(fc) -> per perioada:
       _semnatura(perioada) -> daca deja in risc_fiscal_alerte cu aceeasi
       semnatura: skip -> altfel: trimite_email_fn(...) -> INSERT
       risc_fiscal_alerte
```

---

## 7. Fire de fundal (scheduler-e)

Toate trei pornite din `create_app()` (§1d), primesc **acelasi** `db_lock`
(`threading.RLock()`) - request-urile HTTP si thread-urile de fundal se
serializeaza reciproc pe aceeasi conexiune.

| Modul | Pornire | Bucla (`_loop`, functie interna) | Interval |
|---|---|---|---|
| `backup.py` | `start_scheduler(data_dir, lock)` | `sleep(_seconds_until_due(...))` -> `with lock: create_backup(data_dir)` -> `prune_old_backups(data_dir)` -> la exceptie: `traceback.print_exc()` + `sleep(3600)` | 3 zile (calculat de la ultimul backup **de pe disc**, nu de la pornirea procesului); retry 1h |
| `trial_reminders.py` | `start_scheduler(conn, lock, trimite_email_fn)` | verifica **imediat** la pornire (fara sleep initial): `with lock: verifica_si_trimite(...)` -> `arhiveaza_firme_neplatitoare(...)` -> `sleep(6*3600)`; la exceptie: `traceback.print_exc()` + `sleep(1800)` | 6 ore; retry 30 min |
| `risk_alerts.py` | `start_scheduler(portal_conn, firm_conn_fn, lock, trimite_email_fn)` | verifica **imediat** la pornire: `with lock: verifica_si_alerteaza(...)` -> `sleep(6*3600)`; la exceptie: `traceback.print_exc()` + `sleep(1800)` | 6 ore; retry 30 min - acelasi tipar/interval ca `trial_reminders.py` |

`pipeline.py`, `contract.py`, `invoicing.py`, `migrare_pg.py`,
`seed_master.py`, `risc_fiscal_report.py` - **fara scheduler**, totul
strict la cerere (rute HTTP) sau script CLI separat.

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

### 8e. Evaluare de risc fiscal + alerta automata (modul premium)

```
Firma alege un nivel: POST /panou/plan (camp risc_fiscal_nivel) ->
  salveaza_plan() -> UPDATE firms SET risc_fiscal_nivel=... [portal/app.py]
  (sau masterul forteaza din /master, seteaza_risc_fiscal_nivel §3g)

Contabilul completeaza formularul (§2h) -> POST /api/risc-fiscal/perioada
  -> salveaza_risc_fiscal_perioada [§3r]
     -> etva.risc_fiscal.calculeaza_scor(...)  [§5r - pur, indicatorii 1-5
        ANAF + override Sectiunea B]
     -> etva.risc_fiscal_store.salveaza_perioada(...)  [§5s - upsert]
     -> jsonify(scor) -> frontend: incarcaIstoricRiscFiscal()

Facturare lunara (daca nivelul e platit): valideaza_plata [master] ->
  _cost_modul_risc_fiscal(firm) [§4] -> linie separata pe factura FGO
  (CodArticol RISC_FISCAL_SIMPLU/COMPLET) -> _emite_factura_fgo(...,
  linii_extra=[...])

Alertare automata (fond, fara interactiune umana):
create_app(enable_risk_alerts_scheduler=True)
  -> risk_alerts_mod.start_scheduler(conn, firm_conn, db_lock, _trimite_email)
     -> thread daemon: _loop() -> verifica imediat, apoi la 6h
        -> with db_lock: verifica_si_alerteaza(portal_conn, firm_conn_fn, ...)
           [§6.11] -> per firma activa cu nivel setat -> per perioada
           'ridicat' nealertata cu semnatura curenta -> email adminului
           firmei -> INSERT risc_fiscal_alerte

Descarcare raport: GET /api/risc-fiscal/perioada/<perioada>/pdf ->
  risc_fiscal_pdf [§3r] -> portal.risc_fiscal_report.generate_pdf [§6.10]
  -> PDF cu disclaimer de nonechivalenta cu clasificarea oficiala ANAF
```
