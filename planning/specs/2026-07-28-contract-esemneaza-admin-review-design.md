# Contract eSemneaza: trimitere controlată de master, dublă semnătură reală

Înlocuiește fluxul actual (firma își generează și trimite singură contractul spre semnare) cu
unul controlat integral de master, după validarea manuală a plății. Adaugă semnătură reală și
pentru PRESTATOR (VML), nu doar text, și un instantaneu XML înghețat la finalizare.

## Motivație

- Master nu are azi nicio cale să corecteze datele beneficiarului (denumire/adresă vin automat
  de la ANAF) sau prețul/ciclul înainte ca ele să ajungă la eSemneaza.
- Semnătura PRESTATORULUI e azi doar text ("a semnat electronic la data X"), nu o semnătură
  reală verificabilă.
- `ESEMNEAZA_API_KEY` nu a fost niciodată configurată pe VPS (nici testare, nici producție) -
  funcția a stat dezactivată (`CONTRACTE_ACTIVE=0`) de la introducere.
- Webhook-ul eSemneaza (`/api/esemneaza/webhook`) există în cod dar n-a putut fi folosit
  niciodată - serverul n-a avut adresă publică HTTPS până acum.

## Decizii

- **Flux nou**: master validează plata → link nou "Trimite contract spre semnare" către
  `GET/POST /master/contracte/creeaza/<firm_id>` → formular cu denumire/CUI/adresă (pre-completate
  din ANAF, editabile) și ciclu/preț (pre-completate din calculul curent, editabile) → "Trimite
  spre semnare" creează rândul `contracts` (număr unic, dată curentă) ȘI cheamă imediat
  `esemneaza.create_sign_request` cu doi destinatari.
- **Dacă ANAF nu răspunde** la deschiderea formularului, câmpurile rămân goale - master le
  completează manual, fără să blocheze fluxul.
- **O firmă nu poate avea două contracte active simultan**: dacă firma are deja un contract în
  starea `in_asteptare` (trimis, nesemnat încă de ambele părți), formularul de creare refuză un
  contract nou - trebuie reziliat/anulat cel existent înainte. Dacă ultimul contract al firmei e
  `semnat` sau `reziliat`, master poate crea unul nou (reînnoire, schimbare de preț/ciclu).
- **Doi semnatari reali, în ordine** (`signInOrder=true`, ambii cu opțiunea `one_click_sign`):
  1. PRESTATOR (master/VML) - semnează primul.
  2. BENEFICIAR (firma client) - primește automat mailul de semnat de la eSemneaza abia după ce
     PRESTATORUL a semnat; numele îi vine precompletat (one_click_sign).
- **Firma devine doar spectator**: `/panou/contract` (GET) nu mai generează nimic - arată "în
  pregătire" (nimic trimis încă), "așteaptă finalizarea din partea noastră" (master n-a semnat
  încă) sau starea reală după ce master semnează. Ruta `POST /panou/contract/semneaza` dispare
  pentru metoda eSemneaza (rămâne neatinsă pentru contracte vechi semnate prin `certificat`).
- **Webhook activat**: se configurează în panoul eSemneaza URL-ul
  `https://ereconciliere.ro/api/esemneaza/webhook`, evenimentele `REQUEST_COMPLETED`,
  `RECIPIENT_SIGNED`, `RECIPIENT_REJECTED`, plus un header secret (`X-Webhook-Secret`).
  Ruta de webhook verifică întâi header-ul; la `RECIPIENT_SIGNED` identifică semnatarul după
  câmpul `order` din răspuns (1=prestator, 2=beneficiar). **Polling-ul existent (la fiecare
  vizualizare a paginii) rămâne activ ca plasă de siguranță** - forma exactă a payload-ului de
  webhook nu e documentată oficial de eSemneaza.
- **Instantaneu XML înghețat**: coloană nouă `contracts.contract_xml_final` (BLOB), populată o
  singură dată când contractul devine complet semnat - același tipar ca
  `esemneaza_document_pdf`/`esemneaza_certificate_pdf`, care deja fac asta pentru PDF/certificat.
  Butonul de download XML existent (`/master/contracte/<id>/xml`) servește acest instantaneu
  când există, nu mai regenerează din datele curente ale rândului.
- **Cheie API și secret webhook**: se pun direct ca variabile de mediu în unit-urile systemd de pe
  VPS (productie, opțional testare pentru testare manuală) - nu tranzitează promovarea de cod.

## Schema (`contracts`)

Coloane noi, adăugate la tabela existentă (fără tabelă nouă):

- `prestator_semnat_la TEXT` - data/ora semnăturii PRESTATORULUI (recipient 1); `semnat_la`
  (existentă) își păstrează sensul de "contract complet semnat de ambele părți".
- `contract_xml_final BLOB` - instantaneul XML înghețat la finalizare.

Starea `in_asteptare` capătă sens unic: "cererea a fost trimisă la eSemneaza, se așteaptă
semnături" (nu mai există cazul "draft, netrimis încă" - rândul nu există până master nu trimite).

## Testare

- Fixture `_mock_esemneaza` actualizat pentru doi destinatari cu `signInOrder`.
- Acces la formularul de creare: doar master.
- Secvență completă: PRESTATOR semnează → `prestator_semnat_la` completat, stare rămâne
  `in_asteptare` → BENEFICIAR semnează → stare `semnat`, `contract_xml_final` populat.
- Refuz la oricare etapă (prestator sau beneficiar).
- Webhook: header secret corect vs. greșit; actualizare stare pe `RECIPIENT_SIGNED`/
  `RECIPIENT_REJECTED`.
- Firma nu mai poate genera/trimite contractul prin ruta veche.
- Suită completă verde înainte de promovare dev→testare→producție.

## În afara scopului

- Metoda `certificat` (semnătură cu certificat digital calificat) rămâne neatinsă - nu e parte
  din acest flux nou, doar `esemneaza` e afectată.
- Nicio schimbare la FGO/Netopia - rămâne manual, cum s-a confirmat separat.
