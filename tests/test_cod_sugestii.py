"""Stratul 2 de clasificare (etva/cod_sugestii.py).

Testele pornesc deliberat de la etichete pe care suggest_line() le lasa
neclasificate - acolo si numai acolo ruleaza motorul in aplicatie (vezi
portal/app.py::sugestii_cod_mapare).
"""
import pytest

from etva.cod_sugestii import (MEDIE, RIDICATA, SCAZUTA, cota_dedusa,
                               citeste_semnale, reguli_invalide, sugereaza,
                               sugereaza_lot)
from etva.d300 import TOTAL_LINES, suggest_line, valid_lines_for_direction

# (directie, eticheta, baza, tva, linie asteptata, incredere asteptata)
CAZURI = [
    # --- Vanzari ---
    ("vanzari", "Livrari intracomunitare de bunuri", 5000, 0, "1", RIDICATA),
    ("vanzari", "Export de bunuri catre tari terte", 2000, 0, "3", RIDICATA),
    ("vanzari", "Vanzari la distanta catre persoane fizice UE", 800, 0,
     "17", RIDICATA),
    ("vanzari", "Livrari scutite fara drept de deducere", 300, 0,
     "14+15", RIDICATA),
    ("vanzari", "Livrari cu taxare inversa", 1500, 0, "13", MEDIE),
    ("vanzari", "Livrari supuse masurilor de simplificare", 900, 0,
     "13", RIDICATA),
    ("vanzari", "Regularizari livrari intracomunitare de bunuri", -400, 0,
     "2", RIDICATA),
    ("vanzari", "Prestari de servicii intracomunitare", 1200, 0, "3", SCAZUTA),
    # Cota dedusa aritmetic, cand eticheta nu o scrie.
    ("vanzari", "Operatiuni interne diverse", 1000, 210, "9", RIDICATA),
    ("vanzari", "Operatiuni interne diverse", 1000, 110, "10", RIDICATA),
    ("vanzari", "Operatiuni interne diverse", 1000, 90, "11", RIDICATA),
    # --- Cumparari ---
    ("cumparari", "Achizitii intracomunitare de servicii, taxare inversa",
     1000, 0, "22.1", RIDICATA),
    ("cumparari", "Achizitii intracomunitare de bunuri", 3000, 0,
     "20.1", RIDICATA),
    ("cumparari", "Prestari de servicii intracomunitare primite", 700, 0,
     "22.1", MEDIE),
    ("cumparari", "Regularizari achizitii intracomunitare de bunuri", -200, 0,
     "21", MEDIE),
    ("cumparari", "Achizitii diverse", 1000, 210, "24", RIDICATA),
    ("cumparari", "Achizitii diverse", 1000, 110, "25", RIDICATA),
    ("cumparari", "Achizitii cu beneficiarul obligat la plata TVA", 500, 0,
     "22", SCAZUTA),
    ("cumparari", "AIC neimpozabile", 400, 0, "29", SCAZUTA),
]

# Etichete pe care platforma trebuie sa REFUZE sa le mapeze (line_no None).
CAZURI_REFUZ = [
    ("cumparari", "cu TVA la plata cu cota 21%", 100, 21),
    ("cumparari", "Achizitii cu TVA neexigibil", 100, 0),
    ("vanzari", "Operatiuni diverse", 1000, 190),   # cota 19%, fara linie
    ("cumparari", "Achizitii diverse", 1000, 90),   # 9% deductibil - lipsa
    ("vanzari", "Cod fara nicio informatie", 1000, 0),
    ("vanzari", "Cod fara sume si fara indicii", 0, 0),
    ("cumparari", "Regularizari diverse de taxa", -100, -21),
]


def test_regulile_nu_propun_linii_din_sectiunea_opusa():
    assert reguli_invalide() == []


@pytest.mark.parametrize("directie,eticheta,baza,tva,linie,incredere", CAZURI)
def test_sugestii_asteptate(directie, eticheta, baza, tva, linie, incredere):
    s = sugereaza(directie, "X", eticheta, baza, tva)
    assert s["line_no"] == linie
    assert s["incredere"] == incredere
    assert s["motiv"]


@pytest.mark.parametrize("directie,eticheta,baza,tva", CAZURI_REFUZ)
def test_refuzuri_motivate(directie, eticheta, baza, tva):
    s = sugereaza(directie, "X", eticheta, baza, tva)
    assert s["line_no"] is None
    assert s["incredere"] is None
    # Un refuz fara explicatie ar fi la fel de inutil ca o sugestie gresita.
    assert len(s["motiv"]) > 40


@pytest.mark.parametrize("directie,eticheta,baza,tva",
                        [(d, e, b, v) for d, e, b, v, _, _ in CAZURI]
                        + CAZURI_REFUZ)
def test_motorul_ruleaza_doar_pe_coduri_neclasificate(directie, eticheta,
                                                     baza, tva):
    # Daca stratul 1 ar clasifica deja eticheta, cazul nu ar ajunge
    # niciodata la stratul 2 - testul ar masura altceva decat crede.
    assert suggest_line(directie, eticheta) is None


@pytest.mark.parametrize("directie,eticheta,baza,tva",
                        [(d, e, b, v) for d, e, b, v, _, _ in CAZURI]
                        + CAZURI_REFUZ)
def test_invariant_linii_valide(directie, eticheta, baza, tva):
    valide = valid_lines_for_direction(directie)
    s = sugereaza(directie, "X", eticheta, baza, tva)
    tinte = [s["line_no"]] if s["line_no"] else []
    tinte += [a["line_no"] for a in s["alternative"]]
    for linie in tinte:
        assert linie in valide
        assert linie not in TOTAL_LINES


def test_tva_la_incasare_nu_primeste_niciodata_sugestie():
    # Refuzul lui suggest_line pentru TVA neexigibila e deliberat (taxa nu e
    # datorata in perioada) - stratul 2 nu are voie sa il "repare".
    for eticheta in ("cu TVA la plata cu cota 21%",
                     "Achizitii cu TVA neexigibil, cota 11%",
                     "Facturi cu TVA la incasare"):
        assert sugereaza("cumparari", "X", eticheta, 1000, 210)["line_no"] is None


def test_beneficiar_obligat_la_plata_nu_e_confundat_cu_tva_la_incasare():
    # "obligat la plata TVA" e formularea D300 pentru taxare inversa;
    # suggest_line o inghite prin testul lui pentru "la plata", motiv
    # pentru care astfel de coduri ajung neclasificate aici.
    semnale = citeste_semnale(
        "cumparari", "Achizitii cu beneficiarul obligat la plata TVA", 500, 0)
    assert semnale.taxare_inversa is True
    assert semnale.la_incasare is False


def test_cota_dedusa_lipeste_de_cotele_cunoscute():
    assert cota_dedusa(1000, 210) == 21.0
    assert cota_dedusa(1000, 209.5) == 21.0      # rotunjiri per factura
    assert cota_dedusa(1000, 110) == 11.0
    assert cota_dedusa(1000, 0) == 0.0
    assert cota_dedusa(0, 0) is None             # fara semnal aritmetic
    assert cota_dedusa(-100, -21) is None
    assert cota_dedusa(1000, 155) == 15.5        # cota necunoscuta, raportata


def test_alternativele_sunt_explicate():
    s = sugereaza("cumparari", "X", "Prestari de servicii intracomunitare "
                                    "scutite", 1000, 0)
    assert s["incredere"] == SCAZUTA
    assert s["line_no"] == "29.1"
    assert [a["line_no"] for a in s["alternative"]] == ["22.1"]
    assert all(a["motiv"] and a["line_label"] for a in s["alternative"])


def test_sugereaza_lot_accepta_forma_unmapped():
    # Exact forma pe care o produce classify_legend() in lista `unmapped`.
    unmapped = [{"cod": "17", "label": "Livrari intracomunitare de bunuri",
                 "base": 100.0, "vat": 0.0, "direction": "vanzari"}]
    (s,) = sugereaza_lot(unmapped)
    assert s["cod"] == "17" and s["line_no"] == "1"
