import io
from datetime import datetime, timezone

import pdfplumber

from portal import contract


def test_genereaza_text_does_not_assert_prestator_signature():
    beneficiar = {"denumire": "Firma Test SRL", "cui": "RO123", "adresa": "Str. Test 1"}
    text = contract.genereaza_text(
        1, beneficiar, "lunar", 100.0, datetime(2026, 7, 28, tzinfo=timezone.utc))
    assert "a semnat electronic" not in text
    assert "PRESTATOR: VML EXPERT ADVISOR SRL" in text


def _pozitii_semnatura(denumire_beneficiar: str) -> tuple[float, float]:
    """(top PRESTATOR, top BENEFICIAR) ale etichetelor 'Semnatura ...' din
    ultima pagina a PDF-ului, pentru un beneficiar cu denumirea data."""
    beneficiar = {"denumire": denumire_beneficiar, "cui": "RO123", "adresa": "Str. Test 1"}
    text = contract.genereaza_text(
        1, beneficiar, "lunar", 100.0, datetime(2026, 7, 28, tzinfo=timezone.utc))
    pdf_bytes = contract.genereaza_pdf(text, tag_semnatura_esemneaza=True)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        cuvinte = pdf.pages[-1].extract_words()
    # "PRESTATOR:"/"BENEFICIAR:" apar de doua ori - o data in randul cu
    # denumirile (sus), o data in randul "Semnatura PRESTATOR/BENEFICIAR:"
    # (cel de interes aici) - cel din urma e mai jos pe pagina (top mai mare).
    top_prestator = max(w["top"] for w in cuvinte
                        if w["text"] == "PRESTATOR:" and w["x0"] < 200)
    top_beneficiar = max(w["top"] for w in cuvinte
                         if w["text"] == "BENEFICIAR:" and w["x0"] > 200)
    return top_prestator, top_beneficiar


def test_genereaza_pdf_semnatura_esemneaza_la_aceeasi_inaltime_indiferent_de_lungime_nume():
    """Etichetele (si tag-urile invizibile {{s:1}}/{{s:2}} pe care le
    detecteaza eSemneaza, vezi extract_tags in etva/esemneaza.py) trebuie sa
    ajunga la aceeasi inaltime sub PRESTATOR/BENEFICIAR indiferent cat de
    lunga e denumirea vreuneia dintre parti - altfel eSemneaza plaseaza
    stampila de semnatura in locuri diferite (stampila BENEFICIARULUI se
    putea suprapune peste eticheta, cu un nume scurt)."""
    scurt_prestator, scurt_beneficiar = _pozitii_semnatura("SRL Scurt")
    assert scurt_prestator == scurt_beneficiar

    lung_prestator, lung_beneficiar = _pozitii_semnatura(
        "SOCIETATEA COMERCIALA CU O DENUMIRE FOARTE LUNGA CARE SE INTINDE "
        "PE MAI MULTE LINII IN TABEL SRL")
    assert lung_prestator == lung_beneficiar
    # Denumirea lunga se intinde pe mai multe linii - randul de sub ea
    # trebuie impins mai jos, nu ramas la aceeasi inaltime ca la un nume scurt.
    assert lung_prestator > scurt_prestator
