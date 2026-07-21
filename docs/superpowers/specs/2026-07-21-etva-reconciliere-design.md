# e-TVA-Reconciliere — Design

## Context și scop

ANAF pregătește e-TVA — o declarație TVA precompletată de fisc, pe baza datelor din e-Factura,
SAF-T, casa de marcat etc. — pentru implementare în 2025-2026. Companiile vor trebui să-și
reconcilieze propriile evidențe cu ce raportează ANAF. Nu există încă un tool dedicat pentru asta.

Acest document descrie designul unei **aplicații desktop** care permite unei firme de contabilitate
(cu mai mulți clienți și mai mulți angajați) să compare jurnalul propriu de vânzări/cumpărări
(format D300/394) cu datele e-TVA de la ANAF, să identifice diferențele și să primească sugestii de
corecție pentru declarația D300.

Format ANAF: la momentul acestui design, ANAF nu a publicat specificația oficială a fișierului/API-ului
e-TVA. Aplicația este proiectată astfel încât sursa de date ANAF să fie înlocuibilă fără a afecta
restul sistemului (vezi „Import Manager" mai jos).

## Arhitectură generală

Aplicație desktop Python, cu 5 straturi:

1. **Import Manager** — parsoare pentru jurnalul companiei și pentru datele ANAF
2. **Reconciliation Engine** — potrivire și agregare
3. **Correction Advisor** — sugestii de valori corectate pentru D300
4. **Auth, Permissions & Audit** — autentificare, roluri configurabile, jurnal de audit, criptare
5. **GUI** — pywebview (fereastră nativă) + backend Flask local + frontend HTML/CSS/JS

Ambalare finală: `.exe` unic (PyInstaller) pentru Windows.

### Flux principal

Login → alegere client (din cei alocați userului) → import fișier jurnal companie + sursă e-TVA →
Reconciliation Engine potrivește și agregă → dashboard cu totaluri pe categorii (diferențe
evidențiate) → drill-down pe categoriile cu diferențe → listă facturi individuale problematice →
Correction Advisor sugerează valori corectate → export raport (Excel) cu diferențe + sugestii.

## Componente

### Import Manager

- **Jurnal companie**: parser Excel/CSV cu coloane fixe, stil Declarația 300/394 (CUI partener,
  număr factură, dată, bază, TVA, categorie: livrări interne / achiziții / UE / import-export).
- **Sursă e-TVA (ANAF)**: expusă printr-o interfață stabilă, indiferent de implementare:

  ```python
  class AnafDataSource:
      def get_etva_data(self, cui: str, perioada: str) -> EtvaDataset:
          ...
  ```

  - **Implementare curentă (v1)**: import manual de fișier (Excel/CSV/XML) descărcat de user din
    SPV, cu mapare de coloane configurabilă din UI (pentru că formatul oficial nu există încă).
  - **Implementare viitoare**: conector API live (autentificare cu certificat digital, HTTPS),
    construit ca a doua implementare a aceleiași interfețe — nu necesită schimbări în
    Reconciliation Engine, GUI sau restul sistemului.

- **Validare la import**: coloane obligatorii lipsă sau valori ne-numerice → import respins integral
  (nu se salvează date parțiale), cu mesaj care indică exact linia/coloana problematică.

### Reconciliation Engine

- Potrivire per factură: cheie = CUI partener + număr factură.
- Toleranță de rotunjire configurabilă (implicit ±1 leu) — diferențe sub prag nu sunt semnalate ca
  eroare.
- Duplicate (aceeași factură apare de două ori într-o sursă) — marcate explicit ca „duplicat", nu
  ignorate silențios.
- Agregare pe categorie și perioadă (totaluri bază + TVA).
- Clasificare diferențe: `lipsa_in_anaf`, `lipsa_la_companie`, `suma_diferita`, `duplicat`.
- Drill-down: din orice categorie/total cu diferență, se poate naviga la lista facturilor
  individuale care o compun.

### Correction Advisor

- Pornind de la diferențele clasificate, calculează valoarea sugerată pentru fiecare rând relevant
  din D300 (bază și TVA corectate per categorie).
- Sugestiile sunt informative — nu se depun automat nicăieri, userul decide.

### Auth, Permissions & Audit

- **Autentificare**: login (username + parolă, hash Argon2). Prima pornire = setup wizard care
  creează contul Admin inițial și parola master de criptare.
- **Roluri predefinite** (editabile de Admin): Admin, Manager, Contabil, Junior — vezi tabelul din
  discuția de design pentru permisiunile fiecăruia. Admin poate crea roluri noi și atribui
  permisiuni granulare dintr-o listă fixă de coduri (`clienti.creare`, `clienti.editare`,
  `clienti.stergere`, `reconciliere.creare`, `reconciliere.editare`, `reconciliere.stergere`,
  `rapoarte.export`, `useri.gestionare`, `audit.vizualizare`).
- **Alocare clienți**: fiecare Contabil/Junior vede și lucrează doar la clienții alocați lui explicit
  (Admin și Manager văd toți clienții firmei).
- **Admin Panel**: CRUD useri (dezactivare = soft-delete, nu ștergere fizică — păstrează
  trasabilitatea în audit log), CRUD roluri/permisiuni, alocare clienți pe useri.
- **Criptare**: baza SQLite criptată integral cu SQLCipher. Cheia se derivă (Argon2/PBKDF2) din
  parola master setată la prima instalare. La creare, aplicația generează automat un **cod de
  recuperare** (frază de 24 cuvinte, generată o singură dată și afișată o singură dată) pe care
  Admin trebuie să-l salveze/tipărească în afara aplicației. Fără parola master și fără codul de
  recuperare, datele sunt irecuperabile — este o proprietate a criptării reale, nu un bug.
- **Vizualizare/export pentru audit**: userii cu permisiunile potrivite pot vizualiza/exporta datele
  decriptate oricând din aplicație (pentru audit extern, ex. inspecție ANAF). Exportul cere
  confirmare explicită, pentru că fișierul exportat iese necriptat pe disc.
- **Jurnal de audit**: tabel append-only, needitabil din UI (inclusiv de Admin) — înregistrează
  user, acțiune, entitate afectată, timestamp. Vizibil doar celor cu `audit.vizualizare`.

### GUI

pywebview + backend Flask local + frontend HTML/CSS/JS. Ecrane: Login → (Admin Panel | Dashboard
client) → Import → Rezultate (totaluri cu diferențe evidențiate + drill-down facturi) → Export.

## Model de date (SQLite/SQLCipher)

- `users` (id, username, password_hash, activ)
- `roles` (id, nume)
- `permissions` (cod, descriere)
- `role_permissions` (role_id, permission_cod)
- `user_roles` (user_id, role_id)
- `clients` (id, cui, nume)
- `client_assignments` (user_id, client_id)
- `reconciliations` (id, client_id, perioada, data_creare, creat_de)
- `invoices_company` (reconciliation_id, cui_partener, nr_factura, data, baza, tva, categorie)
- `invoices_anaf` (reconciliation_id, cui_partener, nr_factura, data, baza, tva, categorie)
- `differences` (reconciliation_id, tip_diferenta, detalii)
- `audit_log` (append-only: user_id, actiune, entitate, entitate_id, timestamp)

## Gestionarea erorilor

- Fișier malformat la import → import respins integral, mesaj cu linia/coloana exactă.
- Potrivire ambiguă/duplicat → marcat explicit în rezultate.
- Parolă master uitată → recuperare doar prin codul de recuperare; altfel acces blocat definitiv
  (mesaj clar către user, fără speranțe false de „resetare").
- Corupere bază de date → verificare integritate SQLCipher la pornire; dacă eșuează, aplicația nu
  pornește și indică problema + locația unui eventual backup.

## Testare

- **Unit tests** (pytest) pentru Reconciliation Engine: potrivire exactă, sumă diferită, lipsă în
  ANAF, lipsă la companie, duplicat, toleranță de rotunjire.
- **Integration tests**: flux complet import → reconciliere → export, cu fișiere de test realiste.
- **Auth tests**: verificare că permisiunile blochează corect accesul (ex. un Junior nu poate
  exporta chiar dacă accesează direct ruta internă).

## Roadmap / ce rămâne pentru mai târziu

Singura piesă amânată explicit este **implementarea live** a `AnafDataSource` (autentificare cu
certificat digital + apeluri API către ANAF) — imposibil de construit corect azi, pentru că ANAF nu
a publicat specificația e-TVA. Tot restul (motor de reconciliere, GUI, multi-utilizator, roluri
configurabile, criptare, audit, Admin Panel) face parte din prima implementare.
