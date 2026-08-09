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


def test_verify_cui_parses_inactivitate_fiscala(monkeypatch):
    monkeypatch.setattr(anaf_cui, "_fetch", lambda cui, day: {
        "found": [{
            "date_generale": {"cui": cui, "denumire": "FIRMA INACTIVA SRL",
                              "adresa": "STR. X", "stare_inregistrare": "RADIAT"},
            "inregistrare_scop_Tva": {"scpTVA": False},
            "stare_inactiv": {"dataInactivare": "2025-01-10",
                              "dataReactivare": "", "dataPublicare": "2025-01-11",
                              "dataRadiere": "", "statusInactivi": True},
        }],
        "notFound": [],
    })
    info = anaf_cui.verify_cui("12345678")
    assert info["inactiv_fiscal"] is True
    assert info["data_inactivare"] == "2025-01-10"
    assert info["data_reactivare"] == ""


def test_verify_cui_parses_tva_la_incasare(monkeypatch):
    monkeypatch.setattr(anaf_cui, "_fetch", lambda cui, day: {
        "found": [{
            "date_generale": {"cui": cui, "denumire": "FIRMA TVAI SRL",
                              "adresa": "STR. Y", "stare_inregistrare": "INREGISTRAT"},
            "inregistrare_scop_Tva": {"scpTVA": True},
            "inregistrare_RTVAI": {"dataInceputTvaInc": "2024-03-01",
                                   "dataSfarsitTvaInc": "", "statusTvaIncasare": True},
        }],
        "notFound": [],
    })
    info = anaf_cui.verify_cui("12345678")
    assert info["tva_incasare"] is True
    assert info["data_inceput_tva_incasare"] == "2024-03-01"


def test_verify_cui_parses_status_ro_efactura(monkeypatch):
    monkeypatch.setattr(anaf_cui, "_fetch", lambda cui, day: {
        "found": [{
            "date_generale": {"cui": cui, "denumire": "FIRMA EFACTURA SRL",
                              "adresa": "STR. Z", "stare_inregistrare": "INREGISTRAT",
                              "statusRO_e_Factura": True},
            "inregistrare_scop_Tva": {"scpTVA": True},
        }],
        "notFound": [],
    })
    info = anaf_cui.verify_cui("12345678")
    assert info["inregistrat_ro_efactura"] is True


def test_verify_cui_omits_optional_keys_when_absent_from_response(monkeypatch):
    """O firma fara istoric de inactivitate/TVA la incasare nu primeste
    aceste chei deloc - nu False/None inventat, absenta reala."""
    monkeypatch.setattr(anaf_cui, "_fetch", lambda cui, day: {
        "found": [{
            "date_generale": {"cui": cui, "denumire": "FIRMA SIMPLA SRL",
                              "adresa": "STR. W", "stare_inregistrare": "INREGISTRAT"},
            "inregistrare_scop_Tva": {"scpTVA": True},
        }],
        "notFound": [],
    })
    info = anaf_cui.verify_cui("12345678")
    assert "inactiv_fiscal" not in info
    assert "tva_incasare" not in info
    assert "inregistrat_ro_efactura" not in info
