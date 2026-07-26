"""Invoice numbering + PDF rendering for e-TVA Reconciliere's own billing
(subscriptions paid by firms using the platform) - not to be confused with
the per-firm reconciliation data in etva/db.py.

The PDF here is a human-readable rendering only. Under art. 2 alin. (1)
lit. a) OUG 120/2021, a PDF is NOT a legal electronic invoice - only the
RO_CIUS/UBL XML, actually transmitted through RO e-Factura, has that
status for B2B relationships in Romania (mandatory since 2024-07-01). XML
generation/submission is a separate, not-yet-built step (see project
memory / task list) - this module intentionally only produces the PDF and
the numbered invoice record.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer)

# Identitatea legala a emitentului (VML EXPERT ADVISOR SRL) - aceleasi date
# ca in docs/confidentialitate.html si docs/termeni.html. Firma e platitoare
# de TVA (verificat live la ANAF - scpTVA=True pentru RO35070700).
FURNIZOR = {
    "nume": "VML EXPERT ADVISOR SRL",
    "cui": "RO35070700",
    "reg_com": "J2015011944405",
    "adresa": "Vânju Mare nr. 8, cod poștal 012262, Sector 1, București",
    "email": "office@ereconciliere.ro",
}

_INK = colors.HexColor("#17203a")
_ACCENT = colors.HexColor("#9c3327")
_MUTED = colors.HexColor("#5b6478")
_BORDER = colors.HexColor("#d2d7e0")


def next_invoice_number(conn, serie: str) -> int:
    """Urmatorul numar secvential, fara goluri, pentru o serie - se apeleaza
    doar sub db_lock (acelasi lock care serializeaza deja tot accesul la
    portal.db), altfel doua cereri concurente ar putea calcula acelasi
    numar."""
    row = conn.execute(
        "SELECT MAX(numar) AS ultimul FROM invoices WHERE serie=?",
        (serie,)).fetchone()
    return (row["ultimul"] or 0) + 1


def _suma(valoare: float) -> str:
    return f"{valoare:,.2f}".replace(",", " ").replace(".", ",")


def generate_pdf(invoice: dict) -> bytes:
    """invoice: un rand din tabela invoices (sau un dict cu aceleasi chei)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm)

    eyebrow = ParagraphStyle("eyebrow", fontName="Helvetica-Bold", fontSize=9,
                             textColor=_ACCENT, leading=11, spaceAfter=2)
    title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=22,
                           textColor=_INK, leading=26)
    label = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8,
                           textColor=_MUTED, leading=11)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=10,
                          textColor=_INK, leading=14)
    small = ParagraphStyle("small", fontName="Helvetica", fontSize=8.5,
                           textColor=_MUTED, leading=12)

    elems = []
    elems.append(Paragraph("e-TVA RECONCILIERE", eyebrow))
    elems.append(Paragraph("Factură", title))
    elems.append(Paragraph(
        f"Seria {invoice['serie']} Nr. {invoice['numar']}", body))
    elems.append(Spacer(1, 4 * mm))

    meta_tbl = Table([[
        Paragraph("DATA EMITERII", label), Paragraph("DATA SCADENȚEI", label),
        Paragraph("PERIOADĂ FACTURATĂ", label),
    ], [
        Paragraph(invoice["data_emiterii"][:10], body),
        Paragraph((invoice.get("data_scadentei") or "-")[:10], body),
        Paragraph(
            f"{(invoice.get('perioada_inceput') or '-')} – "
            f"{(invoice.get('perioada_sfarsit') or '-')}"
            if invoice.get("perioada_inceput") else "-", body),
    ]], colWidths=[58 * mm, 58 * mm, 58 * mm])
    meta_tbl.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
    ]))
    elems.append(meta_tbl)
    elems.append(Spacer(1, 6 * mm))

    parti_tbl = Table([[
        Paragraph("FURNIZOR", label), Paragraph("CLIENT", label),
    ], [
        Paragraph(
            f"<b>{FURNIZOR['nume']}</b><br/>CUI {FURNIZOR['cui']}<br/>"
            f"Reg. Com. {FURNIZOR['reg_com']}<br/>{FURNIZOR['adresa']}", body),
        Paragraph(
            f"<b>{invoice['firm_name']}</b><br/>CUI {invoice['firm_cui']}",
            body),
    ]], colWidths=[87 * mm, 87 * mm])
    parti_tbl.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elems.append(parti_tbl)
    elems.append(Spacer(1, 8 * mm))

    linii_data = [[
        Paragraph("DESCRIERE", label), Paragraph("VALOARE NETĂ", label),
    ], [
        Paragraph(invoice["descriere"], body),
        Paragraph(f"{_suma(invoice['valoare_neta'])} {invoice['moneda']}",
                  body),
    ]]
    linii_tbl = Table(linii_data, colWidths=[124 * mm, 50 * mm])
    linii_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, _INK),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, _BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elems.append(linii_tbl)
    elems.append(Spacer(1, 4 * mm))

    total_style = ParagraphStyle("total", fontName="Helvetica-Bold",
                                 fontSize=12, textColor=_INK, leading=15,
                                 alignment=2)
    sub_style = ParagraphStyle("sub", fontName="Helvetica", fontSize=10,
                               textColor=_INK, leading=14, alignment=2)
    totaluri_tbl = Table([
        [Paragraph("Valoare netă", sub_style),
         Paragraph(f"{_suma(invoice['valoare_neta'])} {invoice['moneda']}", sub_style)],
        [Paragraph(f"TVA ({invoice['cota_tva']:.0f}%)", sub_style),
         Paragraph(f"{_suma(invoice['valoare_tva'])} {invoice['moneda']}", sub_style)],
        [Paragraph("TOTAL DE PLATĂ", total_style),
         Paragraph(f"{_suma(invoice['valoare_totala'])} {invoice['moneda']}", total_style)],
    ], colWidths=[124 * mm, 50 * mm])
    totaluri_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 2), (-1, 2), 0.75, _INK),
        ("TOPPADDING", (0, 2), (-1, 2), 5),
    ]))
    elems.append(totaluri_tbl)
    elems.append(Spacer(1, 14 * mm))

    elems.append(Paragraph(
        "Document generat electronic, valabil fără semnătură și ștampilă, "
        "conform art. 319 alin. (29) din Legea nr. 227/2015 privind Codul "
        f"fiscal. Emis de {FURNIZOR['nume']} ({FURNIZOR['email']}).", small))

    doc.build(elems)
    return buf.getvalue()
