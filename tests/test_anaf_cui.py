import pytest

from etva import anaf_cui


def test_normalize_cui_strips_ro_prefix_and_spaces():
    assert anaf_cui.normalize_cui(" RO 12345678".replace(" ", "")) == 12345678
    assert anaf_cui.normalize_cui("ro12345678") == 12345678
    assert anaf_cui.normalize_cui("  44904111  ") == 44904111


def test_normalize_cui_rejects_non_numeric():
    with pytest.raises(ValueError):
        anaf_cui.normalize_cui("ABC123")


def test_verify_cui_found(monkeypatch):
    monkeypatch.setattr(anaf_cui, "_fetch", lambda cui, day: {
        "found": [{
            "date_generale": {"cui": cui, "denumire": "EXEMPLU TEST SRL",
                              "adresa": "STR. EXEMPLU NR. 1",
                              "stare_inregistrare": "INREGISTRAT din data 01.01.2020"},
            "inregistrare_scop_Tva": {"scpTVA": True},
        }],
        "notFound": [],
    })
    info = anaf_cui.verify_cui("RO12345678")
    assert info == {"cui": 12345678, "denumire": "EXEMPLU TEST SRL",
                    "adresa": "STR. EXEMPLU NR. 1",
                    "stare_inregistrare": "INREGISTRAT din data 01.01.2020",
                    "scpTVA": True}


def test_verify_cui_not_found(monkeypatch):
    monkeypatch.setattr(anaf_cui, "_fetch",
                        lambda cui, day: {"found": [], "notFound": [cui]})
    assert anaf_cui.verify_cui("99999999") is None


def test_verify_cui_propagates_service_error(monkeypatch):
    def _boom(cui, day):
        raise anaf_cui.AnafCuiError("nu s-a putut conecta")
    monkeypatch.setattr(anaf_cui, "_fetch", _boom)
    with pytest.raises(anaf_cui.AnafCuiError):
        anaf_cui.verify_cui("12345678")


def test_verify_cui_rejects_invalid_format():
    with pytest.raises(ValueError):
        anaf_cui.verify_cui("nu-e-un-cui")
