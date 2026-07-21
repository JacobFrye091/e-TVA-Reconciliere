from etva.engine import reconcile

def row(cui="RO1", no="F1", base=100.0, vat=19.0, cat="livrari_interne"):
    return {"partner_cui": cui, "invoice_no": no, "date": "2026-01-10",
            "base": base, "vat": vat, "category": cat}

def test_perfect_match_no_diffs():
    r = reconcile([row()], [row()])
    assert r.differences == []
    assert r.totals_company["livrari_interne"] == {"base": 100.0, "vat": 19.0}

def test_tolerance_swallows_rounding():
    r = reconcile([row(base=100.0)], [row(base=100.9)], tolerance=1.0)
    assert r.differences == []

def test_amount_difference():
    r = reconcile([row(base=100.0)], [row(base=150.0)])
    d = r.differences[0]
    assert d["diff_type"] == "suma_diferita"
    assert d["delta_base"] == -50.0

def test_missing_in_anaf():
    r = reconcile([row()], [])
    assert r.differences[0]["diff_type"] == "lipsa_in_anaf"
    assert r.differences[0]["anaf"] is None

def test_missing_at_company():
    r = reconcile([], [row()])
    assert r.differences[0]["diff_type"] == "lipsa_la_companie"
    assert r.differences[0]["company"] is None

def test_duplicate_flagged_and_summed():
    r = reconcile([row(base=50.0, vat=9.5), row(base=50.0, vat=9.5)],
                  [row(base=100.0)])
    types = sorted(d["diff_type"] for d in r.differences)
    assert types == ["duplicat"]  # sums match, only the duplicate flag remains

def test_totals_by_category():
    r = reconcile([row(cat="livrari_interne"),
                   row(no="F2", cat="achizitii_interne", base=200.0, vat=38.0)],
                  [])
    assert r.totals_company["achizitii_interne"]["base"] == 200.0
