from etva.engine import (reconcile, reconcile_d300, find_candidate_invoices,
                         find_closest_invoices)

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


def test_find_candidate_triple_match():
    # Nicio factura singura si nicio pereche nu explica diferenta, dar
    # un triplet da - trebuie gasit si evidentiat.
    rows = [fact(1000.0, 210.0), fact(20.0, 4.2), fact(30.0, 6.3), fact(50.0, 10.5)]
    assert find_candidate_invoices(rows, 100.0, 21.0) == {1, 2, 3}


def test_find_candidate_quadruple_match():
    rows = [fact(10.0, 2.1), fact(20.0, 4.2), fact(30.0, 6.3), fact(40.0, 8.4),
            fact(9999.0, 2099.79)]
    assert find_candidate_invoices(rows, 100.0, 21.0) == {0, 1, 2, 3}


def test_find_candidate_stops_at_max_size_four():
    # 5 facturi mici care insumeaza exact diferenta - dincolo de
    # _CANDIDATE_MAX_SIZE (4) cautarea se opreste intentionat, userul
    # ramane cu mesajul de fallback in loc de o evidentiere pe 5 randuri.
    rows = [fact(10.0, 2.1), fact(20.0, 4.2), fact(30.0, 6.3), fact(40.0, 8.4),
            fact(50.0, 10.5)]
    assert find_candidate_invoices(rows, 150.0, 31.5) == set()


def test_find_candidate_prefers_smallest_subset():
    # O factura singura explica exact diferenta - desi o alta pereche
    # din randuri insumeaza acelasi total, castiga subsetul mai mic.
    rows = [fact(100.0, 21.0), fact(40.0, 8.4), fact(60.0, 12.6)]
    assert find_candidate_invoices(rows, 100.0, 21.0) == {0}


def test_find_candidate_ambiguous_subsets_return_nothing():
    # Doua perechi diferite (sume diferite de facturi) insumeaza amandoua
    # diferenta - nu exista temei sa alegem una, deci nu se marcheaza nimic.
    rows = [fact(40.0, 8.4), fact(60.0, 12.6), fact(30.0, 6.3), fact(70.0, 14.7)]
    assert find_candidate_invoices(rows, 100.0, 21.0) == set()


def test_find_candidate_identical_invoices_pick_deterministically():
    # Trei facturi identice - oricare doua explica diferenta, dar nu e o
    # ambiguitate reala (aceeasi suma), deci se alege prima pereche.
    rows = [fact(50.0, 10.5), fact(50.0, 10.5), fact(50.0, 10.5)]
    assert find_candidate_invoices(rows, 100.0, 21.0) == {0, 1}


def test_find_candidate_ignores_zero_amount_rows():
    # Un rand 0.00/0.00 nu trebuie sa apara in rezultat si nu trebuie sa
    # impiedice gasirea perechii reale.
    rows = [fact(0.0, 0.0), fact(30.0, 6.0), fact(30.5, 6.1)]
    assert find_candidate_invoices(rows, 60.5, 12.1) == {1, 2}


def test_find_candidate_single_found_beyond_pair_cap():
    # Chiar cu ~400 de facturi (peste plafonul de combinatii pentru
    # perechi), o singura factura care se potriveste exact tot trebuie
    # gasita - cautarea pe k=1 nu depinde de plafon in acest interval.
    filler = [fact(1000.0 + i, 210.0 + i * 0.21) for i in range(400)]
    rows = filler + [fact(77.5, 16.28)]
    assert find_candidate_invoices(rows, 77.5, 16.28) == {400}


def test_find_candidate_triple_not_searched_beyond_combo_budget():
    # Aceleasi ~400 facturi de umplutura, plus 3 facturi mici care
    # insumeaza exact diferenta - dar la n~400 chiar si nivelul perechilor
    # depaseste plafonul de combinatii, deci cautarea se opreste inainte
    # sa ajunga la triplete. Cade pe fallback, nu ramane blocata.
    filler = [fact(1000.0 + i, 210.0 + i * 0.21) for i in range(400)]
    rows = filler + [fact(10.0, 2.1), fact(20.0, 4.2), fact(30.0, 6.3)]
    assert find_candidate_invoices(rows, 60.0, 12.6) == set()


def test_find_closest_picks_the_clear_single_winner():
    # Cazul real din conversatie: nicio factura sau combinatie nu explica
    # exact diferenta (find_candidate_invoices ramane la set() - vezi
    # test_find_candidate_ignores_a_near_but_not_matching_invoice), dar
    # factura de la 4214.86/885.12 e vizibil mai aproape decat restul.
    rows = [fact(360.0, 75.6), fact(4214.86, 885.12), fact(600.83, 126.17)]
    assert find_closest_invoices(rows, 4214.96, 885.48) == {1}


def test_find_closest_picks_a_pair_when_no_single_invoice_is_close():
    # Nicio factura singura nu e aproape de delta, dar o pereche e clar
    # mai aproape decat orice alta combinatie - trebuie evidentiate
    # ambele, nu doar una.
    rows = [fact(40.0, 8.4), fact(60.3, 12.7), fact(500.0, 0.0)]
    assert find_closest_invoices(rows, 100.0, 21.0) == {0, 1}


def test_find_closest_returns_nothing_when_two_are_similarly_close():
    # Doua facturi la distanta similara de delta (si nicio combinatie mai
    # mare suficient de aproape) - nu exista un raspuns clar, deci nu se
    # ghiceste nimic.
    rows = [fact(100.4, 21.0), fact(99.7, 21.0), fact(500.0, 0.0)]
    assert find_closest_invoices(rows, 100.0, 21.0) == set()


def test_find_closest_returns_nothing_when_nothing_is_close_enough():
    rows = [fact(500.0, 0.0), fact(10.0, 0.0)]
    assert find_closest_invoices(rows, 100.0, 21.0) == set()


def test_find_closest_negative_delta_never_searches():
    rows = [fact(100.4, 21.0)]
    assert find_closest_invoices(rows, -100.0, -21.0) == set()


def test_find_closest_ignores_zero_amount_rows():
    # Fara filtrare, randul 0.00/0.00 ar fi si el "apropiat" de un delta
    # foarte mic, creand o ambiguitate falsa care ar ascunde singurul
    # raspuns real.
    rows = [fact(0.0, 0.0), fact(0.32, 0.05)]
    assert find_closest_invoices(rows, 0.3, 0.05) == {1}
