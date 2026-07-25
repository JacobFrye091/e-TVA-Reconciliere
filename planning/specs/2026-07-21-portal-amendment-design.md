# Amendament: Portal de conturi + autentificare centralizată

Înlocuiește modelul de conturi locale din spec-ul inițial.

## Decizii

- **Portal web** (`portal/`, Flask + SQLite local, nepublicat momentan — rulează local; site-ul
  static de pe GitHub Pages rămâne vitrina publică):
  - Înregistrare **cont de firmă** (denumire, CUI, utilizator, parolă) — fără plată.
  - Autentificare web + **API JSON** pentru aplicația desktop (`POST /api/auth`).
  - **Master** (AVASILESCU) — creat printr-un script rulat local (credentialele NU apar în cod);
    vede toate firmele, le poate dezactiva.
  - Firma (rol `admin`) își creează sub-utilizatori cu rolurile existente:
    Manager / Contabil / Junior. Permisiunile per rol rămân cele din `etva.db`.
  - Fiecare firmă primește la înregistrare o **cheie de date** aleatoare (32 B) — stocată criptat
    (Fernet, secret local auto-generat, gitignored) și livrată aplicației la autentificare.
- **Aplicația desktop**: se elimină setup-ul local (parolă master, keystore, frază de recuperare,
  utilizatori locali, panou admin). La pornire: ecran de logare cu contul din portal → aplicația
  primește identitatea (rol, permisiuni) + cheia de date → deschide baza SQLCipher locală per
  firmă (`firm_<id>.db`). Alocarea clienților pe contabili rămâne locală, pe bază de username.
- **Securitate**: parolele doar ca hash Argon2id; cheia de date nu se persistă în aplicație;
  bazele de date locale rămân SQLCipher AES-256; audit-ul păstrează username-ul portalului.
- **Logo/favicon**: marcă SVG (bloc petrol + literele TVA + accent delta) în site, portal și
  favicon de browser.

## Compromisuri asumate (MVP)

- Logarea în aplicație necesită portalul accesibil (local acum, online după publicare).
- Cheia de date există în DB-ul portalului (criptată cu secretul local) — custodia migrează de la
  fraza de recuperare la portal; de re-întărit la publicare (HTTPS obligatoriu, rotație chei).
