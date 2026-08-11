"""Teste pentru etva/anaf_bilant.py - clientul serviciului web public de
bilant al ANAF. Serviciul real nu e atins niciodata: `_fetch` e mockuit,
cu raspunsuri calibrate pe forma confirmata live (vezi docstring-ul
modulului pentru structura completa I1..I20)."""
import pytest

from etva import anaf_bilant


def _raspuns(an=2025, deni="EXEMPLU TEST SRL", **indicatori):
    """Construieste un raspuns ANAF cu indicatorii dati (ex: I10=1000)."""
    return {
        "an": an, "cui": 12345678, "deni": deni, "caen": 6920,
        "den_caen": "Activitati de contabilitate",
        "i": [{"indicator": k, "val_indicator": v, "val_den_indicator": k}
              for k, v in indicatori.items()],
    }


def _gol(an=2025):
    """Raspunsul real pentru CUI inexistent / an fara bilant depus."""
    return {"an": an, "cui": 12345678, "deni": "", "caen": 0,
            "den_caen": "", "i": []}


def test_extrage_bilant_mapeaza_indicatorii(monkeypatch):
    monkeypatch.setattr(anaf_bilant, "_fetch", lambda cui, an: _raspuns(
        I1=126754, I7=32749, I10=531748, I13=461057, I18=293631, I19=0, I20=1))
    b = anaf_bilant.extrage_bilant("RO12345678", an=2025)
    assert b["an"] == 2025
    assert b["denumire"] == "EXEMPLU TEST SRL"
    assert b["capitaluri_proprii"] == 531748.0
    assert b["datorii_totale"] == 32749.0
    assert b["cifra_afaceri"] == 461057.0
    assert b["rezultat_net"] == 293631.0
    assert b["active_imobilizate"] == 126754.0
    assert b["numar_salariati"] == 1


def test_extrage_bilant_pierderea_devine_rezultat_negativ(monkeypatch):
    """ANAF raporteaza profitul (I18) si pierderea (I19) in doua campuri
    separate, ambele POZITIVE - etva/risc_fiscal.py asteapta un singur
    rezultat cu semn, deci pierderea trebuie sa iasa negativa."""
    monkeypatch.setattr(anaf_bilant, "_fetch", lambda cui, an: _raspuns(
        I10=1000, I18=0, I19=370680444))
    b = anaf_bilant.extrage_bilant("12345678", an=2025)
    assert b["rezultat_net"] == -370680444.0


def test_extrage_bilant_rezultat_zero_cand_nici_profit_nici_pierdere(monkeypatch):
    monkeypatch.setattr(anaf_bilant, "_fetch", lambda cui, an: _raspuns(
        I10=1000, I18=0, I19=0))
    b = anaf_bilant.extrage_bilant("12345678", an=2025)
    assert b["rezultat_net"] == 0.0


def test_extrage_bilant_none_cand_nu_exista_date(monkeypatch):
    """CUI inexistent / firma fara bilant depus: ANAF intoarce 200 cu lista
    goala, NU o eroare - trebuie tratat ca 'fara date', nu ca esec."""
    monkeypatch.setattr(anaf_bilant, "_fetch", lambda cui, an: _gol(an))
    assert anaf_bilant.extrage_bilant("12345678", an=2025) is None


def test_extrage_bilant_cade_pe_anul_anterior_daca_nu_s_a_depus_inca(monkeypatch):
    """Intre 1 ianuarie si termenul de depunere, bilantul anului trecut nu
    e inca disponibil - trebuie sa cada automat pe cel de acum doi ani."""
    ceruti = []

    def _fals(cui, an):
        ceruti.append(an)
        if an == 2025:
            return _gol(an)
        return _raspuns(an=an, I10=99)

    monkeypatch.setattr(anaf_bilant, "_fetch", _fals)
    b = anaf_bilant.extrage_bilant("12345678", an=2025)
    assert ceruti == [2025, 2024]
    assert b["an"] == 2024
    assert b["capitaluri_proprii"] == 99.0


def test_extrage_bilant_se_opreste_dupa_ani_inapoi(monkeypatch):
    ceruti = []

    def _fals(cui, an):
        ceruti.append(an)
        return _gol(an)

    monkeypatch.setattr(anaf_bilant, "_fetch", _fals)
    assert anaf_bilant.extrage_bilant("12345678", an=2025, ani_inapoi=2) is None
    assert ceruti == [2025, 2024, 2023]


def test_extrage_bilant_implicit_porneste_de_la_anul_trecut(monkeypatch):
    """Bilantul anului IN CURS nu are cum sa fie depus - cautarea implicita
    trebuie sa porneasca de la anul trecut."""
    import datetime
    ceruti = []

    def _fals(cui, an):
        ceruti.append(an)
        return _raspuns(an=an, I10=1)

    monkeypatch.setattr(anaf_bilant, "_fetch", _fals)
    anaf_bilant.extrage_bilant("12345678")
    assert ceruti == [datetime.date.today().year - 1]


def test_extrage_bilant_indicator_lipsa_devine_zero(monkeypatch):
    """Un formular de bilant care nu contine un indicator (ex. fara
    salariati raportati) nu trebuie sa crape calculul."""
    monkeypatch.setattr(anaf_bilant, "_fetch", lambda cui, an: _raspuns(I10=500))
    b = anaf_bilant.extrage_bilant("12345678", an=2025)
    assert b["capitaluri_proprii"] == 500.0
    assert b["numar_salariati"] == 0
    assert b["active_imobilizate"] == 0.0


def test_extrage_bilant_respinge_cui_invalid():
    with pytest.raises(ValueError):
        anaf_bilant.extrage_bilant("nu-e-un-cui")


def test_extrage_bilant_propaga_eroarea_de_serviciu(monkeypatch):
    def _boom(cui, an):
        raise anaf_bilant.AnafBilantError("nu s-a putut conecta")
    monkeypatch.setattr(anaf_bilant, "_fetch", _boom)
    with pytest.raises(anaf_bilant.AnafBilantError):
        anaf_bilant.extrage_bilant("12345678", an=2025)


# ---------- istoric multi-anual ----------

def test_extrage_istoric_intoarce_anii_cel_mai_recent_primul(monkeypatch):
    monkeypatch.setattr(anaf_bilant, "_fetch",
                        lambda cui, an: _raspuns(an=an, I10=an))
    istoric = anaf_bilant.extrage_istoric("12345678", ani=3, an_start=2025)
    assert [x["an"] for x in istoric] == [2025, 2024, 2023]


def test_extrage_istoric_sare_peste_anii_fara_depunere(monkeypatch):
    """O intrerupere in depuneri nu trebuie sa scurteze istoricul - se sare
    peste anul lipsa si se merge mai in urma."""
    def _fals(cui, an):
        return _gol(an) if an == 2024 else _raspuns(an=an, I10=an)
    monkeypatch.setattr(anaf_bilant, "_fetch", _fals)
    istoric = anaf_bilant.extrage_istoric("12345678", ani=3, an_start=2025)
    assert [x["an"] for x in istoric] == [2025, 2023, 2022]


def test_extrage_istoric_lista_goala_cand_nu_exista_nimic(monkeypatch):
    monkeypatch.setattr(anaf_bilant, "_fetch", lambda cui, an: _gol(an))
    assert anaf_bilant.extrage_istoric("12345678", ani=3, an_start=2025) == []


def test_extrage_istoric_nu_propaga_eroarea_de_serviciu(monkeypatch):
    """Istoricul e un bonus pentru raport, nu o conditie de functionare -
    daca ANAF pica, intoarce ce a apucat, nu arunca."""
    def _boom(cui, an):
        raise anaf_bilant.AnafBilantError("boom")
    monkeypatch.setattr(anaf_bilant, "_fetch", _boom)
    assert anaf_bilant.extrage_istoric("12345678", ani=3, an_start=2025) == []


# ---------- verificare incrucisata ----------

_BILANT = {"an": 2025, "capitaluri_proprii": 531748.0, "datorii_totale": 32749.0}


def test_compara_cu_bilant_tace_cand_cifrele_se_potrivesc():
    assert anaf_bilant.compara_cu_bilant(
        {"capitaluri_proprii": 531748.0, "datorii_totale": 32749.0}, _BILANT) == []


def test_compara_cu_bilant_tace_la_variatie_normala_de_business():
    """Bilantul e de anul trecut, perioada evaluata e alta - o crestere de
    20% e fireasca si NU trebuie semnalata, altfel avertismentul devine
    zgomot pe care contabilul il ignora."""
    assert anaf_bilant.compara_cu_bilant(
        {"capitaluri_proprii": 640000.0, "datorii_totale": 39000.0}, _BILANT) == []


def test_compara_cu_bilant_semnaleaza_schimbarea_de_semn_a_capitalurilor():
    """Singurul caz care muta indicatorul 1 cu 100 de puncte."""
    mesaje = anaf_bilant.compara_cu_bilant({"capitaluri_proprii": -5000.0}, _BILANT)
    assert len(mesaje) == 1
    assert "semn opus" in mesaje[0]
    assert "100 de puncte" in mesaje[0]


def test_compara_cu_bilant_semnaleaza_ordinul_de_marime():
    """Tipar clasic de greseala: suma trecuta de 1000 de ori mai mare."""
    mesaje = anaf_bilant.compara_cu_bilant({"datorii_totale": 32749000.0}, _BILANT)
    assert len(mesaje) == 1
    assert "32.749.000" in mesaje[0] and "32.749" in mesaje[0]


def test_compara_cu_bilant_ignora_sumele_mici():
    """Sub prag, oscilatiile sunt normale si nu merita semnalate."""
    assert anaf_bilant.compara_cu_bilant(
        {"capitaluri_proprii": 50.0}, {"an": 2025, "capitaluri_proprii": 10.0}) == []


def test_compara_cu_bilant_nu_compara_cifra_de_afaceri_si_rezultatul():
    """Sunt cumulate de la inceputul anului, deci in mod normal fractiuni
    din valoarea anuala - o comparatie directa ar da alarme false la
    fiecare evaluare facuta in cursul anului."""
    mesaje = anaf_bilant.compara_cu_bilant(
        {"cifra_afaceri": 1000.0, "rezultat_net": -50000.0},
        {"an": 2025, "cifra_afaceri": 461057.0, "rezultat_net": 293631.0})
    assert mesaje == []


def test_compara_cu_bilant_ignora_campurile_lipsa():
    assert anaf_bilant.compara_cu_bilant({"capitaluri_proprii": None}, _BILANT) == []
    assert anaf_bilant.compara_cu_bilant({}, _BILANT) == []


def test_compara_cu_bilant_pastreaza_virgulele_gramaticale():
    """Formatarea sumelor in stil romanesc (punct la mii) nu trebuie sa
    strice punctuatia frazei - numarul se formateaza separat, nu prin
    replace pe tot mesajul."""
    mesaj = anaf_bilant.compara_cu_bilant({"capitaluri_proprii": -5000.0}, _BILANT)[0]
    assert "RON, dar bilanțul" in mesaj
    assert "Verifică: singură, această diferență" in mesaj
