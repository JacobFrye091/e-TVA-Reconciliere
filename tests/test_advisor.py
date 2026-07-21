from etva.engine import reconcile
from etva.advisor import suggest_d300

def row(cui="RO1", no="F1", base=100.0, vat=19.0, cat="livrari_interne"):
    return {"partner_cui": cui, "invoice_no": no, "date": "2026-01-10",
            "base": base, "vat": vat, "category": cat}

def test_clean_category_keeps_company_values():
    r = reconcile([row()], [row()])
    s = suggest_d300(r)
    assert s == [{"category": "livrari_interne", "company_base": 100.0,
                  "company_vat": 19.0, "anaf_base": 100.0, "anaf_vat": 19.0,
                  "suggested_base": 100.0, "suggested_vat": 19.0,
                  "status": "ok"}]

def test_diff_category_suggests_anaf_values():
    r = reconcile([row(base=100.0, vat=19.0)], [row(base=150.0, vat=28.5)])
    s = suggest_d300(r)[0]
    assert s["status"] == "de_verificat"
    assert s["suggested_base"] == 150.0 and s["suggested_vat"] == 28.5

def test_category_only_at_anaf():
    r = reconcile([], [row()])
    s = suggest_d300(r)[0]
    assert s["company_base"] == 0.0 and s["suggested_base"] == 100.0
    assert s["status"] == "de_verificat"
