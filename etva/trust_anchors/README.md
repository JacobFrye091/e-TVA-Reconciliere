# Ancore de încredere pentru semnături electronice calificate

Acest director e gol în mod intenționat. `etva/digital_signature.py`
încarcă de aici certificatele rădăcină/intermediare ale autorităților de
certificare românești/UE calificate (certSIGN, DigiSign, Trans Sped,
alphaSign etc., sau lista de încredere UE/eIDAS) — fără ele, verificarea
unei semnături raportează mereu `trusted: false`, chiar dacă semnătura e
validă criptografic.

## Cum se adaugă

Pune aici fișiere `.pem`/`.crt`/`.cer` cu certificatele rădăcină reale,
obținute dintr-o sursă oficială (site-ul furnizorului de încredere, sau
lista de încredere publicată de ADR/eIDAS). Nu inventa sau presupune
niciun certificat — un certificat greșit ar putea fie respinge semnături
legitime, fie (mai rău) accepta unele nelegitime.

## De ce e gol acum

Nu există încă un certificat digital calificat real cu care să se
testeze (vezi task-ul din lista de sarcini a proiectului despre
obținerea certificatului) — codul de verificare e construit și testat
cu certificate sintetice (generate doar pentru teste), dar increderea
reală se activează abia când certificatele de aici sunt cele adevărate.
