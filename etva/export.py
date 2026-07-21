"""Excel report: summary with suggestions + detailed differences."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

_RED = PatternFill("solid", start_color="FFC7CE")
_BOLD = Font(bold=True)

_SUMAR_HEADER = ["Categorie", "Baza firma", "TVA firma", "Baza ANAF",
                 "TVA ANAF", "Baza sugerata", "TVA sugerata", "Status"]
_DIFF_HEADER = ["Tip diferenta", "CUI partener", "Nr factura", "Categorie",
                "Baza firma", "TVA firma", "Baza ANAF", "TVA ANAF",
                "Delta baza", "Delta TVA"]


def write_report(result, suggestions, path, client_name, period) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sumar"
    ws["A1"] = f"Client: {client_name}"
    ws["A2"] = f"Perioada: {period}"
    ws["A1"].font = ws["A2"].font = _BOLD
    ws.append([])
    ws.append(_SUMAR_HEADER)
    for cell in ws[4]:
        cell.font = _BOLD
    for s in suggestions:
        ws.append([s["category"], s["company_base"], s["company_vat"],
                   s["anaf_base"], s["anaf_vat"], s["suggested_base"],
                   s["suggested_vat"], s["status"]])
        if s["status"] == "de_verificat":
            for cell in ws[ws.max_row]:
                cell.fill = _RED

    wd = wb.create_sheet("Diferente")
    wd.append(_DIFF_HEADER)
    for cell in wd[1]:
        cell.font = _BOLD
    for d in result.differences:
        c, a = d["company"], d["anaf"]
        wd.append([d["diff_type"], d["partner_cui"], d["invoice_no"],
                   d["category"],
                   c["base"] if c else "", c["vat"] if c else "",
                   a["base"] if a else "", a["vat"] if a else "",
                   d["delta_base"], d["delta_vat"]])
    wb.save(path)
