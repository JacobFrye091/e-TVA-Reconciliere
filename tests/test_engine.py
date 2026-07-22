from etva.engine import reconcile, reconcile_d300

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


# ---------- D300 line-level reconciliation (real ANAF e-TVA format) ----------

def test_d300_lines_match_no_diffs():
    r = reconcile_d300({"9": {"base": 100.0, "vat": 21.0}},
                       {"9": {"base": 100.0, "vat": 21.0}})
    assert r.differences == []


def test_d300_line_missing_in_anaf():
    r = reconcile_d300({"22.1": {"base": 120.0, "vat": 25.2}}, {})
    d = r.differences[0]
    assert d["diff_type"] == "lipsa_in_anaf"
    assert d["line_no"] == "22.1"
    assert d["anaf"] is None


def test_d300_line_missing_at_company():
    r = reconcile_d300({}, {"29": {"base": 1193.0, "vat": 0.0}})
    d = r.differences[0]
    assert d["diff_type"] == "lipsa_la_companie"
    assert d["company"] is None


def test_d300_line_amount_differs_beyond_tolerance():
    r = reconcile_d300({"24": {"base": 14323.46, "vat": 3007.94}},
                       {"24": {"base": 14000.0, "vat": 3007.94}})
    assert r.differences[0]["diff_type"] == "suma_diferita"


def test_d300_no_duplicate_concept_at_line_level():
    # Lines are unique by construction — reconcile_d300 never emits "duplicat".
    r = reconcile_d300({"9": {"base": 1.0, "vat": 0.0}},
                       {"9": {"base": 1.0, "vat": 0.0}})
    assert all(d["diff_type"] != "duplicat" for d in r.differences)
