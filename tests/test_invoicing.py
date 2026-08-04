import xml.etree.ElementTree as ET

from portal import invoicing


def _factura(**overrides):
    row = {
        "serie": "VML", "numar": 7, "stare": "emisa",
        "data_emiterii": "2026-08-01", "data_scadentei": "2026-08-15",
        "perioada_inceput": None, "perioada_sfarsit": None,
        "descriere": "Abonament lunar", "firm_name": "Firma Test SRL",
        "firm_cui": "RO123", "moneda": "RON", "valoare_neta": 100.0,
        "cota_tva": 21.0, "valoare_tva": 21.0, "valoare_totala": 121.0,
        "fgo_serie": None, "fgo_numar": None, "fgo_link_pdf": None,
    }
    row.update(overrides)
    return row


def test_invoice_xml_contains_core_fields():
    xml_bytes = invoicing.invoice_xml(_factura())
    radacina = ET.fromstring(xml_bytes)
    assert radacina.tag == "factura"
    assert radacina.get("serie") == "VML"
    assert radacina.get("numar") == "7"
    assert radacina.find("client/nume").text == "Firma Test SRL"
    assert radacina.find("client/cui").text == "RO123"
    assert radacina.find("sume").get("moneda") == "RON"
    assert radacina.find("sume/valoare_totala").text == "121.00"
    assert radacina.find("furnizor/cui").text == invoicing.FURNIZOR["cui"]
    assert radacina.find("perioada") is None
    assert radacina.find("fgo") is None


def test_invoice_xml_includes_perioada_and_fgo_when_present():
    xml_bytes = invoicing.invoice_xml(_factura(
        perioada_inceput="2026-08-01", perioada_sfarsit="2026-08-31",
        fgo_serie="X", fgo_numar="42", fgo_link_pdf="https://fgo.ro/x.pdf"))
    radacina = ET.fromstring(xml_bytes)
    assert radacina.find("perioada/inceput").text == "2026-08-01"
    assert radacina.find("perioada/sfarsit").text == "2026-08-31"
    assert radacina.find("fgo/serie").text == "X"
    assert radacina.find("fgo/numar").text == "42"
    assert radacina.find("fgo/link_pdf").text == "https://fgo.ro/x.pdf"
