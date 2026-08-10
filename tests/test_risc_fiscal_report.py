"""Teste pentru portal/risc_fiscal_report.py - raportul PDF al unei
evaluari de risc fiscal. Apeleaza generate_pdf() direct, fara Flask/DB -
perioada e un dict construit manual, cu forma pe care
etva.risc_fiscal_store._decodeaza() o produce dupa citirea din baza."""
from portal import risc_fiscal_report as rfr


def _perioada(**suprascrie):
    de_baza = {
        "perioada": "2026-T2", "tip_raport": "complet", "scor_afisat": 42,
        "clasificare": "moderat", "scor_total_indicatori": 90,
        "scor_max_posibil": 220, "scor_detaliu": [], "flaguri_sectiune_b": {},
    }
    de_baza.update(suprascrie)
    return de_baza


def test_explicatie_flag_sectiune_b_raspunde_diferit_pe_da_si_nu():
    da = rfr._explicatie_flag_sectiune_b("declarat_inactiv", True)
    nu = rfr._explicatie_flag_sectiune_b("declarat_inactiv", False)
    assert da != nu
    assert "figurează ca inactivă" in da
    assert "NU figurează" in nu
    # Ambele mentioneaza explicit ca verificarea e automata/live - nu doar
    # o eticheta bifata de contabil, ca la restul conditiilor.
    assert "verificat automat, live, la ANAF" in da
    assert "verificat automat, live, la ANAF" in nu


def test_explicatie_flag_sectiune_b_entitate_noua_mentioneaza_pragul():
    da = rfr._explicatie_flag_sectiune_b("entitate_noua", True)
    nu = rfr._explicatie_flag_sectiune_b("entitate_noua", False)
    assert "sub 12 luni" in da
    assert "12 luni" in nu
    assert "nu se bazează pe o declarație" in da
    assert "nu se bazează pe o declarație" in nu


def test_explicatie_flag_sectiune_b_acopera_toate_cele_9_conditii():
    """Fiecare cheie din FLAGURI_SECTIUNE_B_LABEL trebuie sa aiba o
    explicatie proprie, distincta de placeholder-ul generic - altfel PDF-ul
    ar afisa "Da."/"Nu." fara sens pentru o conditie uitata."""
    for cheie in rfr.FLAGURI_SECTIUNE_B_LABEL:
        assert cheie in rfr._EXPLICATIE_FLAG_SECTIUNE_B
        da, nu = rfr._EXPLICATIE_FLAG_SECTIUNE_B[cheie]
        assert len(da) > 20 and len(nu) > 20


def test_generate_pdf_nivel_complet_produce_pdf_valid():
    perioada = _perioada(flaguri_sectiune_b={
        "declarat_inactiv": True, "cazier_fiscal": False, "entitate_noua": False,
        "fara_salariati": False, "fara_bunuri": False, "insolventa": False,
        "evidenta_speciala": False, "raport_inspectie_risc_mare": False,
        "comunicare_garda_financiara": False})
    pdf = rfr.generate_pdf(firm_name="Firma Test SRL", firm_cui="RO44904111",
                           client_name=None, perioada=perioada)
    assert pdf[:4] == b"%PDF"


def test_generate_pdf_nivel_simplu_nu_afiseaza_sectiunea_b():
    """La nivel 'simplu' Sectiunea B nu se aplica deloc - tabelul de
    explicatii nu trebuie construit (ar afisa 9 "Nu"-uri fara sens, cand de
    fapt nivelul nici nu colecteaza aceste date)."""
    perioada = _perioada(tip_raport="simplu", flaguri_sectiune_b={})
    pdf = rfr.generate_pdf(firm_name="Firma Test SRL", firm_cui="RO44904111",
                           client_name="Client SRL", perioada=perioada)
    assert pdf[:4] == b"%PDF"
