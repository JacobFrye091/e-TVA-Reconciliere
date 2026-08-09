from etva.engine import reconcile, reconcile_d300, find_candidate_invoices

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


def fact(base, vat):
    return {"base": base, "vat": vat}


def test_find_candidate_single_exact_match():
    rows = [fact(100.0, 21.0), fact(60.5, 0.0)]
    assert find_candidate_invoices(rows, 60.5, 0.0) == {1}


def test_find_candidate_pair_match():
    rows = [fact(30.0, 6.0), fact(30.5, 6.1)]
    assert find_candidate_invoices(rows, 60.5, 12.1) == {0, 1}


def test_find_candidate_negative_delta_never_searches():
    # ANAF has more than the company - the cause isn't among these invoices.
    rows = [fact(60.5, 0.0)]
    assert find_candidate_invoices(rows, -60.5, 0.0) == set()


def test_find_candidate_ignores_a_near_but_not_matching_invoice():
    # Cazul real din conversatie: delta 4214.96/885.48, cea mai apropiata
    # factura e la 0.10/0.36 distanta - o coincidenta, nu cauza reala
    # (cauza reala fiind mai multe facturi necuprinse de e-Factura, nu una
    # singura). Nu trebuie marcata cu incredere falsa.
    rows = [fact(360.0, 75.6), fact(4214.86, 885.12), fact(600.83, 126.17)]
    assert find_candidate_invoices(rows, 4214.96, 885.48) == set()


def test_find_candidate_requires_both_base_and_vat_close():
    # Baza se potriveste exact, dar TVA nu - nu trebuie marcata.
    rows = [fact(60.5, 99.0)]
    assert find_candidate_invoices(rows, 60.5, 0.0) == set()
