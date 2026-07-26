import xml.etree.ElementTree as ET

from etva import efactura_xml

_NS = {"cac": efactura_xml._NS_CAC, "cbc": efactura_xml._NS_CBC,
      "": efactura_xml._NS_INVOICE}

_FURNIZOR = {"nume": "VML EXPERT ADVISOR SRL", "cui": "RO35070700"}

_INVOICE = {
    "serie": "ETVA", "numar": 1,
    "firm_name": "Firma Exemplu SRL", "firm_cui": "RO12345678",
    "descriere": "Abonament e-TVA Reconciliere - iunie 2026",
    "data_emiterii": "2026-06-15T10:00:00",
    "data_scadentei": "2026-06-30",
    "valoare_neta": 100.0, "cota_tva": 19, "valoare_tva": 19.0,
    "valoare_totala": 119.0, "moneda": "RON",
}


def _parse(xml_bytes):
    return ET.fromstring(xml_bytes)


def test_build_invoice_xml_is_well_formed_and_declares_namespaces():
    xml_bytes = efactura_xml.build_invoice_xml(_INVOICE, _FURNIZOR)
    assert xml_bytes.startswith(b"<?xml")
    root = _parse(xml_bytes)
    assert root.tag == f"{{{efactura_xml._NS_INVOICE}}}Invoice"


def test_build_invoice_xml_has_mandatory_header_fields():
    root = _parse(efactura_xml.build_invoice_xml(_INVOICE, _FURNIZOR))
    assert root.find("cbc:ID", _NS).text == "ETVA1"
    assert root.find("cbc:IssueDate", _NS).text == "2026-06-15"
    assert root.find("cbc:InvoiceTypeCode", _NS).text == "380"
    assert root.find("cbc:DocumentCurrencyCode", _NS).text == "RON"
    assert root.find("cbc:DueDate", _NS).text == "2026-06-30"


def test_build_invoice_xml_omits_due_date_when_not_set():
    invoice = {**_INVOICE, "data_scadentei": None}
    root = _parse(efactura_xml.build_invoice_xml(invoice, _FURNIZOR))
    assert root.find("cbc:DueDate", _NS) is None


def test_build_invoice_xml_supplier_and_customer_parties():
    root = _parse(efactura_xml.build_invoice_xml(_INVOICE, _FURNIZOR))
    supplier = root.find("cac:AccountingSupplierParty/cac:Party", _NS)
    assert supplier.find(
        "cac:PartyLegalEntity/cbc:RegistrationName", _NS).text == "VML EXPERT ADVISOR SRL"
    assert supplier.find(
        "cac:PartyLegalEntity/cbc:CompanyID", _NS).text == "RO35070700"
    assert supplier.find(
        "cac:PartyTaxScheme/cbc:CompanyID", _NS).text == "RO35070700"
    assert supplier.find(
        "cac:PostalAddress/cac:Country/cbc:IdentificationCode", _NS).text == "RO"

    customer = root.find("cac:AccountingCustomerParty/cac:Party", _NS)
    assert customer.find(
        "cac:PartyLegalEntity/cbc:RegistrationName", _NS).text == "Firma Exemplu SRL"
    assert customer.find(
        "cac:PartyLegalEntity/cbc:CompanyID", _NS).text == "RO12345678"


def test_build_invoice_xml_tax_total_and_monetary_total():
    root = _parse(efactura_xml.build_invoice_xml(_INVOICE, _FURNIZOR))
    tax_total = root.find("cac:TaxTotal", _NS)
    assert tax_total.find("cbc:TaxAmount", _NS).text == "19.00"
    subtotal = tax_total.find("cac:TaxSubtotal", _NS)
    assert subtotal.find("cbc:TaxableAmount", _NS).text == "100.00"
    category = subtotal.find("cac:TaxCategory", _NS)
    assert category.find("cbc:ID", _NS).text == "S"
    assert category.find("cbc:Percent", _NS).text == "19"

    monetary = root.find("cac:LegalMonetaryTotal", _NS)
    assert monetary.find("cbc:LineExtensionAmount", _NS).text == "100.00"
    assert monetary.find("cbc:TaxExclusiveAmount", _NS).text == "100.00"
    assert monetary.find("cbc:TaxInclusiveAmount", _NS).text == "119.00"
    assert monetary.find("cbc:PayableAmount", _NS).text == "119.00"


def test_build_invoice_xml_single_line():
    root = _parse(efactura_xml.build_invoice_xml(_INVOICE, _FURNIZOR))
    line = root.find("cac:InvoiceLine", _NS)
    assert line.find("cbc:ID", _NS).text == "1"
    assert line.find("cbc:InvoicedQuantity", _NS).text == "1"
    assert line.find("cbc:LineExtensionAmount", _NS).text == "100.00"
    item = line.find("cac:Item", _NS)
    assert item.find("cbc:Name", _NS).text == _INVOICE["descriere"]
    assert item.find("cac:ClassifiedTaxCategory/cbc:ID", _NS).text == "S"
    assert item.find("cac:ClassifiedTaxCategory/cbc:Percent", _NS).text == "19"
    assert line.find("cac:Price/cbc:PriceAmount", _NS).text == "100.00"
