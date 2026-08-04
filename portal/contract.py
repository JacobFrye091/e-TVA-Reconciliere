"""Contractul de prestari servicii dintre VML EXPERT ADVISOR SRL (prestator)
si firma abonata (beneficiar), pentru abonamentul platit la platforma
e-TVA Reconciliere.

Structura articolelor e adaptata dupa un contract de prestari servicii
standard (parti / obiect / durata / pret / obligatii / raspundere / forta
majora / litigii / clauze finale), cu o clauza de reziliere specifica
ceruta explicit: rambursul la retragere inainte de finalul perioadei
platite nu poate depasi CONTRACT_RAMBURS_MAX_PROCENT.

Nici textul si nici PDF-ul contractului nu se stocheaza - sunt fisiere
mari, inutile cand pot fi regenerate oricand din datele structurate
pastrate in contracts (beneficiar_denumire/cui/adresa, ciclu, suma,
creat_la). genereaza_text_din_rand() reconstruieste exact acelasi text de
fiecare data (data din antet vine din creat_la, nu din "azi", ca sa nu se
schimbe la fiecare vizualizare); un contract nou trebuie generat explicit
daca firma isi schimba ciclul de facturare, la fel ca inainte.
"""
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle

from etva import anaf_cui
from portal import pdf_fonts
from portal.db import (
    CONTRACT_RAMBURS_MAX_PROCENT, CONTRACT_METODA_CERTIFICAT,
    CONTRACT_METODA_ESEMNEAZA, CONTRACT_METODA_MOUSE)
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
                   suma: float, data_creare: datetime) -> str:
    """Textul integral al contractului, gata de afisat sau tiparit.
    data_creare vine din contracts.creat_la, nu din "acum" - contractul
    trebuie sa arate aceeasi data de fiecare data cand e regenerat pentru
    afisare/descarcare, nu data curenta a cererii."""
    azi = data_creare.strftime("%d.%m.%Y")
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

10.2. Prezentul contract se consideră încheiat la data la care ambele \
părți l-au semnat electronic.


PRESTATOR: {FURNIZOR['nume']}     BENEFICIAR: {beneficiar_anaf['denumire']}
"""


def genereaza_text_din_rand(row) -> str:
    """Reconstruieste textul contractului din datele stocate (un rand din
    contracts) - foloseste asta oriunde e nevoie de textul contractului
    dupa creare (afisare, descarcare PDF), in loc de a-l citi dintr-o
    coloana; nu mai exista una."""
    beneficiar = {
        "denumire": row["beneficiar_denumire"],
        "cui": row["beneficiar_cui"],
        "adresa": row["beneficiar_adresa"],
    }
    data_creare = datetime.fromisoformat(row["creat_la"])
    return genereaza_text(row["numar"], beneficiar, row["ciclu_facturare"],
                          row["suma"], data_creare)


def date_contract_xml(row) -> bytes:
    """Datele inghetate ale contractului, ca XML - format portabil din care
    se poate regenera oricand textul/PDF-ul printr-un alt template, fara sa
    depinda de un fisier stocat. Aceleasi conventii ca celelalte exporturi
    XML din aplicatie (vezi portal/app.py:_istoric_la_xml)."""
    radacina = ET.Element("contract", numar=str(row["numar"]))
    ET.SubElement(radacina, "data_creare").text = row["creat_la"]
    ET.SubElement(radacina, "stare").text = row["stare"]
    ET.SubElement(radacina, "ciclu_facturare").text = row["ciclu_facturare"]
    ET.SubElement(radacina, "suma_ron").text = f"{row['suma']:.2f}"

    prestator = ET.SubElement(radacina, "prestator")
    ET.SubElement(prestator, "nume").text = FURNIZOR["nume"]
    ET.SubElement(prestator, "cui").text = FURNIZOR["cui"]
    ET.SubElement(prestator, "reg_com").text = FURNIZOR["reg_com"]
    ET.SubElement(prestator, "adresa").text = FURNIZOR["adresa"]

    beneficiar = ET.SubElement(radacina, "beneficiar")
    ET.SubElement(beneficiar, "denumire").text = row["beneficiar_denumire"]
    ET.SubElement(beneficiar, "cui").text = row["beneficiar_cui"]
    ET.SubElement(beneficiar, "adresa").text = row["beneficiar_adresa"]

    semnatura = ET.SubElement(radacina, "semnatura")
    ET.SubElement(semnatura, "metoda").text = row["metoda_semnatura"] or ""
    ET.SubElement(semnatura, "verificata").text = (
        "true" if row["semnatura_verificata"] else "false")
    ET.SubElement(semnatura, "semnat_la").text = row["semnat_la"] or ""
    if row["semnatura_detalii"]:
        ET.SubElement(semnatura, "detalii").text = row["semnatura_detalii"]

    if row["reziliat_la"]:
        reziliere = ET.SubElement(radacina, "reziliere")
        ET.SubElement(reziliere, "solicitata_la").text = (
            row["reziliere_solicitata_la"] or "")
        ET.SubElement(reziliere, "finalizata_la").text = row["reziliat_la"]
        ET.SubElement(reziliere, "de").text = row["reziliat_de"] or ""
        ET.SubElement(reziliere, "ramburs_procent").text = (
            f"{row['ramburs_procent']:.2f}"
            if row["ramburs_procent"] is not None else "")

    return ET.tostring(radacina, encoding="utf-8", xml_declaration=True)


def pdf_final(contract) -> bytes:
    """PDF-ul contractului. Pentru eSemneaza, documentul semnat e un
    artefact real primit de la un tert - servit exact cum a fost primit,
    NU regenerat (spre deosebire de celelalte metode, unde nu exista
    niciun fisier original de pastrat). Pentru semnatura cu mouse-ul (
    metoda veche, pastrata doar pentru contracte semnate inainte de
    eSemneaza) re-embedam PNG-ul desenat; pentru semnatura cu certificat
    nu mai exista fisierul original incarcat, asa ca atasam in schimb
    rezultatul verificarii facute la momentul semnarii."""
    if (contract["metoda_semnatura"] == CONTRACT_METODA_ESEMNEAZA
            and contract["esemneaza_document_pdf"]):
        return bytes(contract["esemneaza_document_pdf"])
    continut = genereaza_text_din_rand(contract)
    if contract["metoda_semnatura"] == CONTRACT_METODA_MOUSE:
        semnatura_img = (bytes(contract["semnatura_mouse_img"])
                         if contract["semnatura_mouse_img"] else None)
        return genereaza_pdf(continut, semnatura_img=semnatura_img)
    if contract["metoda_semnatura"] == CONTRACT_METODA_CERTIFICAT:
        detalii = json.loads(contract["semnatura_detalii"] or "{}")
        nota = nota_verificare_certificat(detalii, contract["semnat_la"])
        return genereaza_pdf(continut, nota_semnatura=nota)
    return genereaza_pdf(continut)


def nota_verificare_certificat(semnatura_detalii: dict, semnat_la: str) -> str:
    """Text care inlocuieste, in PDF-ul regenerat, fisierul semnat original
    incarcat de beneficiar - acela nu mai e pastrat pe server (fisier mare,
    inutil de stocat), asa ca aratam explicit rezultatul verificarii facute
    la momentul semnarii, nu pretindem ca returnam fisierul brut."""
    semnatar = semnatura_detalii.get("semnatar") or "necunoscut"
    incredere = "confirmat" if semnatura_detalii.get("trusted") else "neconfirmat"
    data_afisata = semnat_la[:10] if semnat_la else "-"
    return (
        f"Document semnat cu certificat digital calificat de {semnatar} la "
        f"{data_afisata}. Lanțul de încredere al certificatului: {incredere}. "
        f"Fișierul PDF original încărcat de beneficiar nu este păstrat pe "
        f"server - acesta este un document regenerat din datele "
        f"contractului, cu rezultatul verificării semnăturii de mai sus.")


def genereaza_pdf(continut: str, semnatura_img: bytes | None = None,
                  nota_semnatura: str | None = None,
                  tag_semnatura_esemneaza: bool = False) -> bytes:
    """PDF-ul contractului, generat la cerere din text - textul in sine nu
    se stocheaza (vezi docstring-ul modulului). Pentru semnatura cu
    mouse-ul, semnatura_img e PNG-ul desenat, stampilat la final. Pentru
    semnatura cu certificat, nu mai exista PDF-ul original incarcat de
    beneficiar - nota_semnatura (vezi nota_verificare_certificat) descrie
    in schimb rezultatul verificarii facute la momentul semnarii.

    tag_semnatura_esemneaza=True adauga doua tag-uri invizibile (font alb):
    `{{s:1}}` langa PRESTATOR si `{{s:2}}` langa BENEFICIAR - eSemneaza le
    detecteaza automat cu extractTags=True (vezi etva/esemneaza.py) si
    genereaza campurile de semnatura, cu pozitia calculata din locul
    tag-urilor in text, cate unul pentru fiecare semnatar in ordine
    (signInOrder=True: semnatarul 1 = PRESTATOR, semnatarul 2 = BENEFICIAR)."""
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
        elif bloc.startswith("PRESTATOR:"):
            # Randul final PRESTATOR/BENEFICIAR - doua coloane reale
            # (un Paragraph obisnuit reflow-eaza spatiile multiple folosite
            # pentru aliniere si arata urat, in loc sa pastreze cele doua
            # blocuri separate vizual).
            stanga, dreapta = re.split(r"\s{2,}", bloc, maxsplit=1)
            if tag_semnatura_esemneaza:
                stanga += ' <font color="white">{{s:1}}</font>'
                dreapta += ' <font color="white">{{s:2}}</font>'
            latime_coloana = (doc.width) / 2
            elems.append(Spacer(1, 4 * mm))
            elems.append(Table(
                [[Paragraph(stanga, body), Paragraph(dreapta, body)]],
                colWidths=[latime_coloana, latime_coloana],
                style=TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ])))
        elif len(prima_linie) < 60 and prima_linie.isupper():
            elems.append(Paragraph(bloc.replace("\n", " "), articol))
        else:
            elems.append(Paragraph(bloc.replace("\n", " "), body))
    if semnatura_img:
        elems.append(Spacer(1, 4 * mm))
        elems.append(Paragraph("Semnătură BENEFICIAR (desenată electronic):", body))
        elems.append(Image(io.BytesIO(semnatura_img), width=60 * mm, height=25 * mm))
    if nota_semnatura:
        elems.append(Spacer(1, 4 * mm))
        elems.append(Paragraph(nota_semnatura, body))

    doc.build(elems)
    return buf.getvalue()
