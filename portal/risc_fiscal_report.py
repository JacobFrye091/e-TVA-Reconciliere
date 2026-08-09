"""Raport PDF pentru o evaluare de risc fiscal (etva/risc_fiscal.py +
etva/risc_fiscal_store.py) - aceleasi conventii vizuale ca portal/invoicing.py
(reportlab + portal/pdf_fonts).

Disclaimer-ul de nonechivalenta cu clasificarea oficiala ANAF e OBLIGATORIU
pe fiecare raport (vezi etva/risc_fiscal.py, docstring modul) - scorul de
aici e calculat pe indicatorii 1-5 din Anexa 2, normalizat 0-100 cu etichete
proprii, nu pragul/clasificarea oficiala ANAF ("risc mic/mediu/mare",
definita pe toti cei 8 indicatori).
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer)

from portal import pdf_fonts

_INK = colors.HexColor("#17203a")
_ACCENT = colors.HexColor("#9c3327")
_MUTED = colors.HexColor("#5b6478")
_BORDER = colors.HexColor("#d2d7e0")

_CULOARE_CLASIFICARE = {
    "scazut": colors.HexColor("#1c7a4d"),
    "moderat": colors.HexColor("#b8860b"),
    "ridicat": colors.HexColor("#b32424"),
}

_ETICHETA_NIVEL = {"simplu": "Simplu", "complet": "Complet"}
_ETICHETA_CLASIFICARE = {"scazut": "Risc scăzut", "moderat": "Risc moderat",
                         "ridicat": "Risc ridicat"}

FLAGURI_SECTIUNE_B_LABEL = {
    "cazier_fiscal": "Înscrieri în cazierul fiscal",
    "entitate_noua": "Entitate nou înființată",
    "fara_salariati": "Absența salariaților",
    "fara_bunuri": "Lipsa bunurilor imobile sau mobile",
    "insolventa": "Procedură de insolvență deschisă",
    "evidenta_speciala": "Înregistrare în evidență specială",
    "declarat_inactiv": "Declarat inactiv fiscal",
    "raport_inspectie_risc_mare": "Raport de risc fiscal mare de la inspecția fiscală",
    "comunicare_garda_financiara": "Comunicare de risc fiscal mare de la Garda Financiară",
}


def generate_pdf(*, firm_name: str, firm_cui: str, client_name: "str | None",
                 perioada: dict) -> bytes:
    """perioada: un rand din etva.risc_fiscal_store (dict cu scor_afisat/
    clasificare/detaliu/etc, deja decodat din JSON)."""
    pdf_fonts.asigura_fonturi()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm)

    eyebrow = ParagraphStyle("eyebrow", fontName=pdf_fonts.BOLD, fontSize=9,
                             textColor=_ACCENT, leading=11, spaceAfter=2)
    title = ParagraphStyle("title", fontName=pdf_fonts.BOLD, fontSize=22,
                           textColor=_INK, leading=26)
    label = ParagraphStyle("label", fontName=pdf_fonts.BOLD, fontSize=8,
                           textColor=_MUTED, leading=11)
    body = ParagraphStyle("body", fontName=pdf_fonts.REGULAR, fontSize=10,
                          textColor=_INK, leading=14)
    small = ParagraphStyle("small", fontName=pdf_fonts.REGULAR, fontSize=8.5,
                           textColor=_MUTED, leading=12)
    scor_style = ParagraphStyle(
        "scor", fontName=pdf_fonts.BOLD, fontSize=28, leading=32,
        textColor=_CULOARE_CLASIFICARE.get(perioada["clasificare"], _INK))

    elems = []
    elems.append(Paragraph("E-TVA RECONCILIERE", eyebrow))
    elems.append(Paragraph("Raport de risc fiscal", title))
    elems.append(Paragraph(
        f"Evaluare {_ETICHETA_NIVEL.get(perioada['tip_raport'], perioada['tip_raport'])} "
        f"— perioada {perioada['perioada']}", body))
    elems.append(Spacer(1, 4 * mm))

    parti_tbl = Table([[
        Paragraph("FIRMĂ DE CONTABILITATE", label), Paragraph("EVALUAT", label),
    ], [
        Paragraph(f"<b>{firm_name}</b><br/>CUI {firm_cui}", body),
        Paragraph(f"<b>{client_name or firm_name}</b>", body),
    ]], colWidths=[87 * mm, 87 * mm])
    parti_tbl.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elems.append(parti_tbl)
    elems.append(Spacer(1, 8 * mm))

    scor_tbl = Table([[
        Paragraph(str(perioada["scor_afisat"]), scor_style),
        Paragraph(
            f"<b>{_ETICHETA_CLASIFICARE.get(perioada['clasificare'], perioada['clasificare'])}</b>"
            f"<br/>{perioada['scor_total_indicatori'] if perioada.get('scor_total_indicatori') is not None else '-'} "
            f"din {perioada.get('scor_max_posibil', '-')} puncte pe indicatorii calculați", body),
    ]], colWidths=[35 * mm, 139 * mm])
    scor_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elems.append(scor_tbl)
    elems.append(Spacer(1, 6 * mm))

    if perioada.get("flaguri_sectiune_b"):
        active = [FLAGURI_SECTIUNE_B_LABEL.get(k, k)
                 for k, v in perioada["flaguri_sectiune_b"].items() if v]
        if active:
            elems.append(Paragraph(
                "<b>Risc mare (Secțiunea B, metodologie ANAF):</b> "
                + ", ".join(active), body))
            elems.append(Spacer(1, 4 * mm))

    detaliu_rows = [[Paragraph("INDICATOR", label), Paragraph("VALOARE", label),
                     Paragraph("PUNCTAJ", label)]]
    for d in perioada.get("scor_detaliu") or []:
        if "neaplicabil" in d:
            detaliu_rows.append([
                Paragraph(f"{d['indicator']}. {d['nume']}", body),
                Paragraph(f"<i>{d['neaplicabil']}</i>", small), Paragraph("—", body)])
        else:
            detaliu_rows.append([
                Paragraph(f"{d['indicator']}. {d['nume']}", body),
                Paragraph(str(d.get("valoare", "-")), body),
                Paragraph(str(d.get("punctaj", "-")), body)])
    detaliu_tbl = Table(detaliu_rows, colWidths=[104 * mm, 40 * mm, 30 * mm])
    detaliu_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, _INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elems.append(detaliu_tbl)
    elems.append(Spacer(1, 10 * mm))

    elems.append(Paragraph(
        "<b>Notă privind metodologia:</b> scorul de mai sus e calculat pe "
        "indicatorii 1-5 din Anexa 2 („Fișa indicatorilor de risc fiscal”), "
        "parte din Procedura ANAF de stabilire a gradului de risc fiscal la "
        "rambursările de TVA (Ordin ANAF 17.12.2015). Indicatorii 6-8 din "
        "aceeași fișă (istoric rambursări TVA soluționate) sunt date interne "
        "ANAF, imposibil de reprodus extern, și NU sunt incluși. Scorul și "
        "clasificarea de mai sus sunt <b>proprii, normalizate 0-100</b> — "
        "NU sunt echivalente cu pragul/clasificarea oficială ANAF "
        "(„risc mic/mediu/ridicat”, prag ≥60 puncte), care se aplică doar "
        "sumei complete a tuturor celor 8 indicatori.", small))

    doc.build(elems)
    return buf.getvalue()
