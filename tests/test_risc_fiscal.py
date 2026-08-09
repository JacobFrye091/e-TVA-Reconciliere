import pytest

from etva import risc_fiscal as rf


def _financiar(capitaluri_proprii=100.0, datorii_totale=50.0,
              cifra_afaceri=1000.0, rezultat_net=10.0):
    return {"capitaluri_proprii": capitaluri_proprii,
            "datorii_totale": datorii_totale,
            "cifra_afaceri": cifra_afaceri, "rezultat_net": rezultat_net}


def test_calculeaza_scor_respinge_nivel_necunoscut():
    with pytest.raises(ValueError):
        rf.calculeaza_scor("premium", _financiar())


# ---------- indicator 1: capitaluri proprii ----------

def test_capitaluri_proprii_negative_puncteaza_maxim():
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=-10.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 1)
    assert d["punctaj"] == 100


def test_capitaluri_proprii_pozitive_puncteaza_zero():
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=10.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 1)
    assert d["punctaj"] == 0


# ---------- indicator 2: grad de indatorare ----------

def test_grad_indatorare_sub_prag_zero_puncte():
    scor = rf.calculeaza_scor(
        rf.SIMPLU, _financiar(capitaluri_proprii=100.0, datorii_totale=100.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 2)
    assert d["punctaj"] == 0  # grad exact 1.0, in pragul <=1


def test_grad_indatorare_peste_prag_50_puncte():
    scor = rf.calculeaza_scor(
        rf.SIMPLU, _financiar(capitaluri_proprii=100.0, datorii_totale=150.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 2)
    assert d["punctaj"] == 50


def test_grad_indatorare_capital_propriu_negativ_50_puncte_conservator():
    scor = rf.calculeaza_scor(
        rf.SIMPLU, _financiar(capitaluri_proprii=-5.0, datorii_totale=10.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 2)
    assert d["punctaj"] == 50


# ---------- indicator 3: profitabilitate ----------

def test_profitabilitate_pierdere_70_puncte():
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar(rezultat_net=-1.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 3)
    assert d["punctaj"] == 70


def test_profitabilitate_profit_zero_puncte():
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar(rezultat_net=5.0))
    d = next(x for x in scor.detaliu if x["indicator"] == 3)
    assert d["punctaj"] == 0


# ---------- nivel simplu: doar 1-3, restul neaplicabil ----------

def test_nivel_simplu_ignora_indicatorii_4_5_chiar_daca_sunt_dati():
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar(), declaratii_nedepuse=5,
                              obligatii_restante=True, obligatii_crescute=True)
    d4 = next(x for x in scor.detaliu if x["indicator"] == 4)
    d5 = next(x for x in scor.detaliu if x["indicator"] == 5)
    assert d4["neaplicabil"] == rf.NEAPLICABIL_NIVEL
    assert d5["neaplicabil"] == rf.NEAPLICABIL_NIVEL
    assert scor.scor_max_posibil == 220


def test_nivel_simplu_scor_afisat_normalizat_pe_220():
    # capitaluri negative (100p) + indatorare peste prag (50p) + pierdere (70p) = 220/220
    scor = rf.calculeaza_scor(
        rf.SIMPLU, _financiar(capitaluri_proprii=-1.0, datorii_totale=10.0,
                              rezultat_net=-1.0))
    assert scor.scor_total_indicatori == 220
    assert scor.scor_afisat == 100
    assert scor.clasificare == "ridicat"


# ---------- indicatorii 6-8: mereu neaplicabili, niciodata insumati ----------

def test_indicatorii_6_8_mereu_neaplicabili():
    scor = rf.calculeaza_scor(rf.COMPLET, _financiar(), declaratii_nedepuse=0,
                              obligatii_restante=False)
    for n in (6, 7, 8):
        d = next(x for x in scor.detaliu if x["indicator"] == n)
        assert d["neaplicabil"] == rf.NEAPLICABIL_ANAF
    assert scor.scor_max_posibil == 370  # 6-8 nu se aduna la maxim posibil


# ---------- nivel complet: indicatorii 4-5 ----------

def test_declaratii_nedepuse_praguri():
    assert rf.calculeaza_scor(rf.COMPLET, _financiar(), declaratii_nedepuse=0,
                              obligatii_restante=False).scor_total_indicatori == 0
    scor1 = rf.calculeaza_scor(rf.COMPLET, _financiar(), declaratii_nedepuse=1,
                               obligatii_restante=False)
    d4 = next(x for x in scor1.detaliu if x["indicator"] == 4)
    assert d4["punctaj"] == 50
    scor2 = rf.calculeaza_scor(rf.COMPLET, _financiar(), declaratii_nedepuse=3,
                               obligatii_restante=False)
    d4b = next(x for x in scor2.detaliu if x["indicator"] == 4)
    assert d4b["punctaj"] == 100


def test_obligatii_restante_crescute_vs_scazute():
    scor_crescute = rf.calculeaza_scor(
        rf.COMPLET, _financiar(), declaratii_nedepuse=0,
        obligatii_restante=True, obligatii_crescute=True)
    scor_scazute = rf.calculeaza_scor(
        rf.COMPLET, _financiar(), declaratii_nedepuse=0,
        obligatii_restante=True, obligatii_crescute=False)
    d_crescute = next(x for x in scor_crescute.detaliu if x["indicator"] == 5)
    d_scazute = next(x for x in scor_scazute.detaliu if x["indicator"] == 5)
    assert d_crescute["punctaj"] == 50
    assert d_scazute["punctaj"] == 30


# ---------- valori de intrare lipsa: nu puncteaza, nu crapa ----------

def test_valoare_lipsa_nu_puncteaza_si_apare_neaplicabil():
    financiar = _financiar(capitaluri_proprii=None)
    scor = rf.calculeaza_scor(rf.SIMPLU, financiar)
    d1 = next(x for x in scor.detaliu if x["indicator"] == 1)
    assert d1["neaplicabil"] == "valoare de intrare lipsa"
    d2 = next(x for x in scor.detaliu if x["indicator"] == 2)
    assert d2["neaplicabil"] == "valoare de intrare lipsa"  # depinde si de capitaluri


# ---------- Sectiunea B: override la "ridicat" indiferent de punctaj ----------

def test_flag_sectiune_b_forteaza_clasificare_ridicat():
    # Scor foarte mic pe Sectiunea C (toate indicatoarele in favoarea firmei)
    scor = rf.calculeaza_scor(
        rf.COMPLET, _financiar(capitaluri_proprii=100.0, datorii_totale=10.0,
                               rezultat_net=10.0),
        declaratii_nedepuse=0, obligatii_restante=False,
        flaguri_sectiune_b={"declarat_inactiv": True})
    assert scor.clasificare == "ridicat"
    assert scor.override_sectiune_b is True
    assert "Declarat inactiv fiscal" in scor.flaguri_risc_mare_active


def test_fara_flaguri_sectiune_b_nu_forteaza_nimic():
    scor = rf.calculeaza_scor(
        rf.COMPLET, _financiar(capitaluri_proprii=100.0, datorii_totale=10.0,
                               rezultat_net=10.0),
        declaratii_nedepuse=0, obligatii_restante=False,
        flaguri_sectiune_b={"declarat_inactiv": False})
    assert scor.override_sectiune_b is False
    assert scor.flaguri_risc_mare_active == []


def test_flaguri_sectiune_b_ignorate_la_nivel_simplu():
    """Sectiunea B e parte din 'vectorul fiscal' manual, disponibil doar la
    nivelul 'complet' - la 'simplu' flagurile transmise sunt ignorate."""
    scor = rf.calculeaza_scor(
        rf.SIMPLU, _financiar(capitaluri_proprii=100.0, datorii_totale=10.0,
                              rezultat_net=10.0),
        flaguri_sectiune_b={"declarat_inactiv": True})
    assert scor.override_sectiune_b is False
    assert scor.clasificare == "scazut"


def test_cheie_necunoscuta_in_flaguri_sectiune_b_ignorata():
    scor = rf.calculeaza_scor(
        rf.COMPLET, _financiar(capitaluri_proprii=100.0, datorii_totale=10.0,
                               rezultat_net=10.0),
        declaratii_nedepuse=0, obligatii_restante=False,
        flaguri_sectiune_b={"nu_exista": True})
    assert scor.override_sectiune_b is False


# ---------- clasificare pe scor_afisat (fara flag de Sectiunea B) ----------

def test_clasificare_praguri_scazut_moderat_ridicat():
    assert rf._clasifica(0) == "scazut"
    assert rf._clasifica(33) == "scazut"
    assert rf._clasifica(34) == "moderat"
    assert rf._clasifica(66) == "moderat"
    assert rf._clasifica(67) == "ridicat"
    assert rf._clasifica(100) == "ridicat"
