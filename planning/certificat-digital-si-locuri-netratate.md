# Certificat digital calificat + locuri ramase netratate in dezvoltare

Document de referinta: task-ul de obtinere a certificatului digital
calificat (cerut explicit de Andrei, 2026-08-03) plus o trecere prin
`planning/harta-functii.md` cautand tot ce e marcat in cod/documentatie ca
neterminat, netestat live sau oprit deliberat. Fiecare punct de mai jos e
verificat direct in cod la data scrierii (nu presupus) - referintele
`fisier:linie` sunt citate exact.

## 1. Task: certificat digital calificat real, pentru testare

**Ce lipseste:** un certificat digital calificat real (certSIGN, DigiSign,
Trans Sped, alphaSign etc.), pe care Andrei sa-l foloseasca pentru
autentificare la ANAF. Fara el, doua fluxuri distincte raman verificate
doar teoretic, niciodata live:

1. **OAuth2 "decont precompletat"** - `etva/anaf_oauth.py:18-25`: "The
   decont endpoint's exact response shape is not yet confirmed against a
   real live call (that needs a real firm's certificate to authorize...)."
   Ruta care porneste fluxul: `/panou/anaf/autorizare`
   (`portal/app.py:584-589`) - admin-ul firmei se autentifica la
   `logincert.anaf.ro` cu certificatul lui; `_extract_decont_json` e scris
   defensiv dupa documentatia ANAF, dar parsarea reala a raspunsului
   (arhiva ZIP cu JSON-uri) nu a fost niciodata vazuta.
2. **Verificarea semnaturii digitale pe contracte** -
   `etva/digital_signature.py:17,91` + `etva/trust_anchors/README.md`:
   directorul de ancore de incredere e gol intentionat ("Nu există încă un
   certificat digital calificat real cu care să se testeze") - fara
   certificatele radacina reale acolo, `verifica_semnatura_pdf` raporteaza
   mereu `trusted: false`, chiar daca semnatura e valida criptografic.
   (Relevant doar daca/cand `CONTRACTE_ACTIVE=1`, vezi §2.3 mai jos.)

**Actiune:** obtinerea certificatului e un task administrativ (nu de cod) -
inregistrare la un furnizor CA acreditat, verificare KYC. Odata obtinut,
pasii tehnici sunt deja scrisi si asteapta doar validare live:
`/panou/anaf/autorizare` pentru #1, si populare `etva/trust_anchors/` cu
certificatele radacina oficiale ale CA-ului ales pentru #2.

---

## 2. Alte locuri ramase goale sau netratate (din trecerea prin harta)

### 2.1 Reconciliere "clasica" (factura-cu-factura) - sursa ANAF e mereu fisier, niciodata API live

`etva/importer/anaf.py:1-3`: "The official format is not yet published, so
the file-based implementation uses a configurable column mapping. A future
live API connector implements the same interface." Exista o singura
implementare a `AnafDataSource` (`FileAnafDataSource`) - modul clasic de
reconciliere cere intotdeauna upload manual de fisier pentru partea ANAF.
Doar modul D300-linii (cu decont OAuth2, vezi §1.1) are varianta de
auto-preluare, si aia inca neverificata live.

### 2.2 Plati - nicio integrare reala de procesare, totul manual

`portal/app.py:114-120`, TODO explicit la `portal/app.py:1191` (in
`creeaza_cerere_plata`): optiunea de plata e **complet dezactivata**
(`PLATA_ACTIVA=0`, confirmat in unit-urile systemd de pe testare si
productie - nesetata nicaieri, deci ramane pe valoarea implicita). Codul
exista intact, dar `/panou/plata` doar inregistreaza o "cerere"
(`payments.stare=in_asteptare`) pe care masterul o valideaza **manual**
dupa ce confirma incasarea altfel. Integrarea Netopia Payments (prin FGO,
`LinkPlata`) ar automatiza asta - amanata explicit pana exista cont
Netopia configurat (`planning/integrare-fgo.md:99-104`).

### 2.3 Contracte - semnatura electronica dezactivata complet (decizie de business)

`portal/app.py:106-112`: `CONTRACTE_ACTIVE=0` pe ambele medii (confirmat).
Cod complet functional (rute, `contract.py`, `esemneaza.py`) dar pus pe
pauza - "firma nu mai e obligata sa semneze nimic ca sa trimita o cerere
de plata". Se reactiveaza fara nicio schimbare de cod, doar
`CONTRACTE_ACTIVE=1` in unit-ul systemd - dar depinde si de §1.2
(certificatul) daca se foloseste metoda de semnare prin certificat, nu
doar eSemneaza.ro.

### 2.4 FGO - reconciliere bancara automata, discutata dar neimplementata

`planning/integrare-fgo.md:105-108`: callback-ul FGO de incasare (webhook)
si integrarea bancara PSD2 (`fgo.ro/procesare-extrase`) pentru
reconcilierea automata a platilor cu extrasul bancar real - "discutate,
neimplementate". Depinde oricum de §2.2 (plata activa) ca sa aiba sens.

### 2.5 Backup/restore Postgres - backup-ul real functioneaza, restore-ul nu exista

Verificat direct pe server: `etva-backup-pg.timer` (in afara repo-ului,
`/usr/local/sbin/etva-backup-pg.sh`) ruleaza real, nocturn, `pg_dump` pe
`etva_testare`+`etva_productie`, criptat GPG, urcat pe OneDrive (remote
configurat, fisiere reale prezente, retentie 14 zile local / 60 zile
cloud) - partea asta e in regula. **Ce lipseste:** nicio unealta sau
procedura de restore, nici testata, nici scrisa. In aplicatie,
`/master/backup/restaureaza` refuza explicit orice incercare pe backend
Postgres (`portal/app.py`, cf. `planning/harta-functii.md` §3m) - zip-ul
din `portal/backup.py` mai acopera doar `uploads/`+chei pe Postgres, nu
date live (`portal/pg_schema.sql`... vezi `planning/concurenta-postgres.md`
punctul 3 din "Descoperiri importante"). Documentat explicit ca "Faza 5,
inca deschisa" in `planning/migrare-postgres.md:3-8`.

### 2.6 (rezolvat azi, mentionat pentru context) Leak de tranzactie in trial_reminders

Nu mai e un gol - fixat si verificat live pe ambele medii in aceasta
sesiune (`portal/trial_reminders.py`, commit `569f87b`/`9238236`). Il
mentionez aici doar ca sa nu redescopere cineva acelasi lucru cautand prin
cod pe viitor.

---

## Prioritizare sugerata

| # | Item | Blocheaza pe altceva? |
|---|---|---|
| 1 | Certificat digital calificat | Blocheaza §1.1, §1.2, indirect §2.3 |
| 2 | Restore Postgres testat (Faza 5) | Risc de date, independent de rest |
| 3 | Integrare Netopia/FGO plati | Blocheaza §2.2, §2.4, §2.3 (partial) |
| 4 | API live ANAF pt. modul clasic | Asteapta publicare format oficial ANAF - in afara controlului nostru |

Certificatul (1) si restore-ul testat (2) sunt singurele doua fara nicio
dependenta externa in afara controlului echipei - restul asteapta fie
decizii de business (Netopia), fie ANAF sa publice ceva.
