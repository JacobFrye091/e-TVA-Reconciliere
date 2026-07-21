from openpyxl import load_workbook
from etva.engine import reconcile
from etva.advisor import suggest_d300
from etva import export

def row(cui="RO1", no="F1", base=100.0, vat=19.0, cat="livrari_interne"):
    return {"partner_cui": cui, "invoice_no": no, "date": "2026-01-10",
            "base": base, "vat": vat, "category": cat}

def test_report_structure(tmp_path):
    r = reconcile([row()], [row(base=200.0)])
    p = str(tmp_path / "raport.xlsx")
    export.write_report(r, suggest_d300(r), p, "Firma SRL", "2026-01")
    wb = load_workbook(p)
    assert wb.sheetnames == ["Sumar", "Diferente"]
    sumar = wb["Sumar"]
    assert sumar["A1"].value == "Client: Firma SRL"
    assert sumar["A2"].value == "Perioada: 2026-01"
    diffs = wb["Diferente"]
    assert diffs["A1"].value == "Tip diferenta"
    assert diffs["A2"].value == "suma_diferita"

def test_flagged_row_is_red(tmp_path):
    r = reconcile([row()], [row(base=200.0)])
    p = str(tmp_path / "raport.xlsx")
    export.write_report(r, suggest_d300(r), p, "F", "2026-01")
    sumar = load_workbook(p)["Sumar"]
    # data starts at row 5 (title, period, blank, header)
    assert sumar.cell(row=5, column=1).fill.start_color.rgb == "00FFC7CE"
