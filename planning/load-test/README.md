# Test de încărcare go2 (k6) — cum se rulează

Vezi context complet și fazele A-D în planul original (nu versionat în
repo). Acest fișier documentează doar partea tehnică, reutilizabilă:
scriptul `k6-etva.js` și pașii de pregătire/rulare.

## De ce k6, de pe ce mașină

`k6` rulează **întotdeauna de pe o mașină diferită de go2** (laptop, alt
VPS mic) — niciodată de pe serverul de producție însuși. Dacă generatorul
de trafic rulează pe aceeași mașină cu `gunicorn`/Postgres pe care le
testezi, concurează cu ele pentru CPU și rezultatele nu mai reflectă
capacitatea reală a infrastructurii, ci și overhead-ul propriu al k6.

Instalare k6 (pe mașina externă, nu pe go2):
```bash
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6
```

## Pregătire — cont sintetic dedicat testului (o singură dată)

**Nu folosi niciodată una din firmele reale pentru testul de încărcare.**
Creează un cont/firmă sintetică prin fluxul real de `/inregistrare` pe
`https://ereconciliere.ro`, exact ca la validarea migrării (vezi
`planning/migrare-postgres.md`, contul `VML EXPERT ADVISOR SRL` de pe
testare):

1. Înregistrează o firmă nouă (CUI fictiv dar valid ca format), cu un
   email pe care îl controlezi.
2. Verifică emailul (`EMAIL_VERIFICARE_OBLIGATORIE=1` e activ pe
   productie — fără verificare, `/app` redirecționează la
   `/asteapta-verificare-email` și tot testul eșuează la primul request).
3. Autentifică-te manual o dată, adaugă cel puțin un client
   (`/api/clients`) și creează cel puțin o reconciliere, ca să obții un
   `id` real de reconciliere — necesar pentru scenariul de export
   (`/api/reconciliations/<id>/export`), fluxul semnalat explicit în
   `planning/brief-optimizari-performanta.md` #1 ca fiind cel mai folosit.
4. Notează: CUI, parola, id-ul reconcilierii — acestea sunt
   `TEST_CUI`/`TEST_PASSWORD`/`TEST_RECONCILIERE_ID` de mai jos.

**Notă:** acest pas scrie date reale în baza de producție (un cont nou,
un client, o reconciliere) și trimite un email real de verificare. E o
acțiune deliberată, reversibilă (firma sintetică poate fi arhivată/ștearsă
ulterior din panoul master), dar nu una "gratuită" — de aceea nu a fost
automatizată aici.

## Rulare

```bash
export BASE_URL=https://ereconciliere.ro
export TEST_CUI=RO...           # CUI-ul firmei sintetice
export TEST_PASSWORD='...'
export TEST_RECONCILIERE_ID=1   # id-ul notat mai sus

k6 run planning/load-test/k6-etva.js
```

Smoke test rapid înainte de rampa completă (confirmă că scriptul
funcționează, fără să genereze concurență reală):
```bash
k6 run --stage 20s:2,20s:0 planning/load-test/k6-etva.js
```

## În timpul rulării — monitorizare live (pe go2, într-un terminal separat)

```bash
journalctl -u etva-productie -f
watch -n5 'sudo -u postgres psql -d etva_productie -c "select count(*) from pg_stat_activity"'
watch -n5 free -h
```

**Oprește manual (Ctrl+C pe k6) la primul semn real** de impact asupra
celor 2 firme active (erori 5xx susținute, latență explozivă) — nu
aștepta finalul rampei sau pragurile din `thresholds`, acelea sunt doar
plasa de siguranță automată, nu înlocuiesc supravegherea live.

## Ce acoperă scriptul și ce nu

Acoperă fluxul read-heavy (login → `/app` → `/api/clients` →
`/api/reconciliations/:id/export` → `/api/audit` → logout), confirmat ca
fiind traseul real din cod (`portal/app.py`, rute `@app.get`/`@app.post`,
nu `@app.route` — deci un `grep .route(` simplu le ratează pe majoritatea).

**Nu acoperă** scrierile (creare reconciliere prin `POST
/api/reconciliations`, upload fișiere) — payload-ul exact (multipart,
format fișiere) nu a fost verificat încă; de adăugat separat dacă se
dorește și acoperirea căii de scriere, nu ghicit aici.

## După test

Compară cu rularea anterioară (baseline înainte de tunare / retest după)
folosind rezumatul afișat de k6 la final (`http_req_duration`,
`http_req_failed`, metricile custom `etva_panou_duration`/`etva_export_duration`).
Documentează cifrele concrete în `planning/` (stil `restaurare-postgres.md`)
și actualizează rândul #11 din `brief-optimizari-performanta.md`.
