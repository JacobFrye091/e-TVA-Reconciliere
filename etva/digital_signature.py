"""Verificarea unei semnaturi electronice calificate incorporate intr-un
PDF (PAdES), pentru documentele pe care un utilizator le semneaza cu
propriul certificat calificat si le incarca pe platforma (ex. contractul
de prestari servicii - vezi portal/contract.py).

Foloseste pyhanko, aceeasi tehnologie pe care se bazeaza si SPV/ANAF
pentru documentele lor semnate. Doua lucruri diferite se verifica aici,
separat:

  - integritatea criptografica: documentul nu a fost modificat de la
    semnare, iar semnatura corespunde matematic certificatului incorporat
    - se verifica intotdeauna, indiferent de ce certificat a fost folosit.
  - increderea (trusted): certificatul semnatarului urca pana la o
    autoritate de certificare cunoscuta si de incredere (certSIGN,
    DigiSign, Trans Sped etc., sau lista de incredere UE/eIDAS) - se
    verifica DOAR daca exista certificate radacina reale in
    TRUST_ANCHORS_DIR. Acel director e gol acum - nu exista inca un
    certificat calificat real cu care sa se testeze (vezi task-ul din
    lista de sarcini despre obtinerea certificatului). Pana atunci,
    verificarea raporteaza mereu trusted=False, cu un mesaj explicit -
    NU se presupune increderea doar pentru ca semnatura e valida
    criptografic.
"""
import io
import os

from asn1crypto import pem, x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import validation
from pyhanko_certvalidator import ValidationContext

TRUST_ANCHORS_DIR = os.path.join(os.path.dirname(__file__), "trust_anchors")


class SignatureVerificationError(Exception):
    """Fisierul nu e un PDF citibil sau nu contine nicio semnatura."""


def _incarca_ancore_incredere(director: "str | None" = None) -> list:
    # director=None (nu TRUST_ANCHORS_DIR ca valoare implicita a
    # parametrului) inseamna ca modulul citeste TRUST_ANCHORS_DIR abia la
    # apel, nu o data la definirea functiei - altfel monkeypatch-ul de
    # test asupra modulului n-ar avea niciun efect (parametrii impliciti
    # se evalueaza o singura data, la import).
    director = TRUST_ANCHORS_DIR if director is None else director
    if not os.path.isdir(director):
        return []
    ancore = []
    for nume in sorted(os.listdir(director)):
        if not nume.lower().endswith((".pem", ".crt", ".cer")):
            continue
        with open(os.path.join(director, nume), "rb") as f:
            continut = f.read()
        if pem.detect(continut):
            _, _, der = pem.unarmor(continut)
        else:
            der = continut
        ancore.append(x509.Certificate.load(der))
    return ancore


def verifica_semnatura_pdf(pdf_bytes: bytes) -> dict:
    """Verifica prima semnatura electronica incorporata intr-un PDF.

    Intoarce un dict cu:
      valid: semnatura e valida criptografic si documentul nu a fost
        modificat de la semnare
      trusted: certificatul semnatarului urca pana la o ancora de
        incredere reala configurata - False mereu daca nu exista niciuna
      semnatar / certificat_emitent: subiectul, respectiv emitentul
        certificatului (nume complet din certificat)
      valabil_de_la / valabil_pana_la: valabilitatea certificatului (ISO)
      eroare: motivul, cand valid sau trusted sunt False

    Ridica SignatureVerificationError daca fisierul nu poate fi citit ca
    PDF sau nu contine nicio semnatura incorporata.
    """
    try:
        reader = PdfFileReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise SignatureVerificationError(
            f"Fisierul nu poate fi citit ca PDF: {exc}") from exc
    semnaturi = list(reader.embedded_regular_signatures)
    if not semnaturi:
        raise SignatureVerificationError(
            "PDF-ul nu contine nicio semnatura electronica incorporata.")

    sig = semnaturi[0]
    ancore = _incarca_ancore_incredere()
    # Constructia unui lant de incredere are nevoie de cel putin o ancora
    # ca sa nu esueze cu PathBuildingError - cand nu exista inca ancore
    # reale configurate, folosim certificatul semnatarului insusi doar ca
    # sa putem obtine rezultatul integritatii criptografice (intact/valid);
    # campul "trusted" real e fortat pe False mai jos, indiferent ce ar
    # spune status.trusted intr-un astfel de context artificial.
    context = ValidationContext(trust_roots=ancore or [sig.signer_cert])
    status = validation.validate_pdf_signature(sig, signer_validation_context=context)
    cert = status.signing_cert

    rezultat = {
        "valid": bool(status.intact and status.valid),
        "trusted": bool(ancore) and bool(status.trusted),
        "semnatar": cert.subject.human_friendly if cert is not None else None,
        "certificat_emitent": cert.issuer.human_friendly if cert is not None else None,
        "valabil_de_la": cert.not_valid_before.isoformat() if cert is not None else None,
        "valabil_pana_la": cert.not_valid_after.isoformat() if cert is not None else None,
        "eroare": None,
    }
    if not rezultat["valid"]:
        rezultat["eroare"] = "Semnătura nu este validă sau documentul a fost modificat după semnare."
    elif not ancore:
        rezultat["eroare"] = (
            "Nicio ancoră de încredere configurată încă - s-a verificat "
            "doar integritatea criptografică a semnăturii, nu identitatea "
            "reală a semnatarului.")
    elif not rezultat["trusted"]:
        rezultat["eroare"] = "Certificatul nu urcă până la o autoritate de încredere cunoscută."
    return rezultat
