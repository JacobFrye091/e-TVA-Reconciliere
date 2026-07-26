"""Contractul de prestari servicii dintre VML EXPERT ADVISOR SRL (prestator)
si firma abonata (beneficiar), pentru abonamentul platit la platforma
e-TVA Reconciliere.

Structura articolelor e adaptata dupa un contract de prestari servicii
standard (parti / obiect / durata / pret / obligatii / raspundere / forta
majora / litigii / clauze finale), cu o clauza de reziliere specifica
ceruta explicit: rambursul la retragere inainte de finalul perioadei
platite nu poate depasi CONTRACT_RAMBURS_MAX_PROCENT.

Textul generat e inghetat in coloana contracts.continut la momentul
crearii - nu se regenereaza automat daca se schimba ulterior pretul din
nomenclator sau datele ANAF ale vreuneia dintre parti; un contract nou
trebuie generat explicit daca firma isi schimba ciclul de facturare.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image

from etva import anaf_cui
from portal import pdf_fonts
from portal.db import CONTRACT_RAMBURS_MAX_PROCENT
from portal.invoicing import FURNIZOR

_ETICHETE_CICLU = {"lunar": "lunar", "6luni": "la 6 luni", "an": "anual"}

_INK = colors.HexColor("#17203a")
_ACCENT = colors.HexColor("#9c3327")
_MUTED = colors.HexColor("#5b6478")


class ContractError(Exception):
    """Datele necesare generarii contractului nu au putut fi obtinute."""


def next_contract_number(conn) -> int:
    """Urmatorul numar secvential, fara goluri - se apeleaza doar sub
    db_lock, la fel ca invoicing.next_invoice_number."""
    row = conn.execute("SELECT MAX(numar) AS ultimul FROM contracts").fetchone()
    return (row["ultimul"] or 0) + 1


def date_beneficiar(cui: str) -> dict:
    """Datele firmei-client, verificate live la ANAF - aceeasi sursa ca la
    inregistrare, ca sa reflecte denumirea/adresa reala, nu ce a fost
    introdus manual la un moment anterior."""
    try:
        info = anaf_cui.verify_cui(cui)
    except anaf_cui.AnafCuiError as e:
        raise ContractError(f"Nu am putut verifica firma la ANAF: {e}") from e
    if info is None:
        raise ContractError("CUI-ul firmei nu a fost gasit la ANAF.")
    return info


def genereaza_text(numar: int, beneficiar_anaf: dict, ciclu: str,
                   suma: float) -> str:
    """Textul integral al contractului, gata de afisat sau tiparit."""
    azi = datetime.now().strftime("%d.%m.%Y")
    eticheta_ciclu = _ETICHETE_CICLU.get(ciclu, ciclu)
    cui_beneficiar = str(beneficiar_anaf["cui"])
    if not cui_beneficiar.upper().startswith("RO"):
        cui_beneficiar = f"RO{cui_beneficiar}"
    return f"""CONTRACT DE PRESTĂRI SERVICII
Nr. {numar} / {azi}

Încheiat astăzi, {azi}

I. PĂRȚILE CONTRACTANTE

1.1. {FURNIZOR['nume']}, cu sediul social în {FURNIZOR['adresa']}, având \
codul unic de înregistrare {FURNIZOR['cui']}, număr de ordine în \
registrul comerțului {FURNIZOR['reg_com']}, denumită în continuare \
PRESTATOR,

și

1.2. {beneficiar_anaf['denumire']}, cu sediul social în \
{beneficiar_anaf['adresa']}, având codul unic de înregistrare \
{cui_beneficiar}, denumită în continuare BENEFICIAR,

au convenit să încheie prezentul contract de prestări servicii, cu \
respectarea următoarelor clauze:

II. OBIECTUL CONTRACTULUI

2.1. Obiectul contractului îl constituie furnizarea de către PRESTATOR \
în favoarea BENEFICIARULUI a accesului la platforma e-TVA Reconciliere, \
instrument de reconciliere între jurnalul contabil al BENEFICIARULUI și \
datele precompletate transmise de ANAF pentru decontul de TVA (D300).

III. DURATA CONTRACTULUI

3.1. Contractul se încheie pentru un ciclu de facturare {eticheta_ciclu}, \
cu posibilitate de reînnoire la alegerea BENEFICIARULUI.

IV. PREȚUL CONTRACTULUI

4.1. Prețul serviciilor pentru ciclul {eticheta_ciclu} este de \
{suma:.2f} RON, exclusiv TVA.

4.2. Plata se efectuează prin modalitățile disponibile în contul de \
platformă al BENEFICIARULUI.

V. ÎNCETAREA CONTRACTULUI ȘI RESTITUIRI

5.1. BENEFICIARUL se poate retrage din prezentul contract oricând, \
printr-o cerere transmisă din contul său de platformă.

5.2. Dacă retragerea are loc înainte de finalul perioadei de facturare \
deja plătite, suma restituită BENEFICIARULUI nu va depăși \
{CONTRACT_RAMBURS_MAX_PROCENT}% din suma achitată pentru acel ciclu de \
facturare.

VI. OBLIGAȚIILE PĂRȚILOR

6.1. PRESTATORUL se obligă să mențină funcționarea platformei cu \
eforturi rezonabile, fără garanție de disponibilitate neîntreruptă.

6.2. BENEFICIARUL se obligă să plătească prețul la termenele convenite \
și să folosească platforma conform Termenilor și condițiilor publicate.

VII. RĂSPUNDEREA PĂRȚILOR

7.1. Răspunderea PRESTATORULUI este limitată conform Termenilor și \
condițiilor publicate pe platformă, parte integrantă a prezentului \
contract.

VIII. FORȚA MAJORĂ

8.1. Niciuna dintre părți nu răspunde de neexecutarea obligațiilor sale \
dacă aceasta a fost cauzată de forța majoră, așa cum este definită de \
lege.

IX. SOLUȚIONAREA LITIGIILOR

9.1. Litigiile decurgând din sau în legătură cu prezentul contract se \
soluționează pe cale amiabilă sau, în lipsa unei înțelegeri, de \
instanțele judecătorești competente, conform legii române.

X. CLAUZE FINALE

10.1. Prezentul contract, împreună cu Termenii și condițiile publicate \
pe platformă, reprezintă voința părților.

10.2. Prezentul contract se consideră încheiat la data semnării lui de \
către BENEFICIAR.


PRESTATOR: {FURNIZOR['nume']}                BENEFICIAR: {beneficiar_anaf['denumire']}
"""


def genereaza_pdf(continut: str, semnatura_img: bytes | None = None) -> bytes:
    """PDF-ul contractului - textul inghetat, cu semnatura desenata cu
    mouse-ul (daca a fost folosita aceasta metoda) stampilata la final.
    Pentru semnatura prin certificat, documentul semnat e cel incarcat de
    utilizator (nu acesta) - vezi contracts.pdf_semnat."""
    pdf_fonts.asigura_fonturi()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm)

    titlu = ParagraphStyle("titlu", fontName=pdf_fonts.BOLD, fontSize=14,
                           textColor=_INK, leading=18, spaceAfter=6,
                           alignment=1)
    articol = ParagraphStyle("articol", fontName=pdf_fonts.BOLD, fontSize=10.5,
                             textColor=_ACCENT, leading=14, spaceBefore=8,
                             spaceAfter=3)
    body = ParagraphStyle("body", fontName=pdf_fonts.REGULAR, fontSize=9.5,
                          textColor=_INK, leading=13.5, spaceAfter=4)

    elems = []
    for bloc in continut.strip().split("\n\n"):
        bloc = bloc.strip()
        if not bloc:
            continue
        prima_linie = bloc.split("\n")[0]
        if bloc.startswith("CONTRACT DE"):
            elems.append(Paragraph(bloc.replace("\n", "<br/>"), titlu))
        elif len(prima_linie) < 60 and prima_linie.isupper():
            elems.append(Paragraph(bloc.replace("\n", " "), articol))
        else:
            elems.append(Paragraph(bloc.replace("\n", " "), body))
    if semnatura_img:
        elems.append(Spacer(1, 4 * mm))
        elems.append(Paragraph("Semnătură BENEFICIAR (desenată electronic):", body))
        elems.append(Image(io.BytesIO(semnatura_img), width=60 * mm, height=25 * mm))

    doc.build(elems)
    return buf.getvalue()
