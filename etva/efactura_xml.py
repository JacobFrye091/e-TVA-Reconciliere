"""Build a UBL 2.1 / RO_CIUS invoice XML for RO e-Factura submission.

Scope: type-380 (standard commercial) invoices, single line, VAT category
"S" (standard/reduced taxable) - the shape e-TVA Reconciliere's own
subscription invoices actually need (see portal/invoicing.py). Credit
notes (381), corrections (384), self-billing (389) and the informational
type (751) are deliberately not covered.

Built from ANAF's own "Ghid cod facturi_final v2.9.pdf" (business terms
BT-1/2/3/5/31/47/48/146/151/152, BG-25 line group) and the EN 16931-1
semantic model, cross-checked against the RO_CIUS field descriptions in
"Ghid_RO_eFactura.pdf". NOT yet validated against ANAF's actual
Schematron/XSD artifacts (not reachable from this environment) - treat
as a well-researched best effort, not a guarantee. Validate for real
against the sandbox (api.anaf.ro/test/FCTEL) or ANAF's own free XML
validator at mfinante.gov.ro before ever using this for a real
production submission.

Customer postal address is limited to the country code (RO) - the firms
table doesn't collect a full street address today, which EN 16931
recommends but does not always strictly require. Flag for later if a
real submission gets rejected over this.
"""
import xml.etree.ElementTree as ET

_NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

ET.register_namespace("", _NS_INVOICE)
ET.register_namespace("cac", _NS_CAC)
ET.register_namespace("cbc", _NS_CBC)

_CUSTOMIZATION_ID = (
    "urn:cen.eu:en16931:2017#compliant#"
    "urn:efactura.mfinante.ro:CIUS-RO:1.0.1")


def _cac(tag):
    return f"{{{_NS_CAC}}}{tag}"


def _cbc(tag):
    return f"{{{_NS_CBC}}}{tag}"


def _party(parent_tag: str, root, nume: str, cui: str, tara: str = "RO"):
    party_wrap = ET.SubElement(root, _cac(parent_tag))
    party = ET.SubElement(party_wrap, _cac("Party"))

    addr = ET.SubElement(party, _cac("PostalAddress"))
    country = ET.SubElement(addr, _cac("Country"))
    ET.SubElement(country, _cbc("IdentificationCode")).text = tara

    tax_scheme = ET.SubElement(party, _cac("PartyTaxScheme"))
    ET.SubElement(tax_scheme, _cbc("CompanyID")).text = cui
    scheme = ET.SubElement(tax_scheme, _cac("TaxScheme"))
    ET.SubElement(scheme, _cbc("ID")).text = "VAT"

    legal_entity = ET.SubElement(party, _cac("PartyLegalEntity"))
    ET.SubElement(legal_entity, _cbc("RegistrationName")).text = nume
    ET.SubElement(legal_entity, _cbc("CompanyID")).text = cui
    return party_wrap


def build_invoice_xml(invoice: dict, furnizor: dict) -> bytes:
    """invoice: a row from the `invoices` table (or an equivalent dict).
    furnizor: portal.invoicing.FURNIZOR (or an equivalent dict with
    nume/cui)."""
    root = ET.Element(f"{{{_NS_INVOICE}}}Invoice")
    ET.SubElement(root, _cbc("CustomizationID")).text = _CUSTOMIZATION_ID
    ET.SubElement(root, _cbc("ID")).text = f"{invoice['serie']}{invoice['numar']}"  # BT-1
    ET.SubElement(root, _cbc("IssueDate")).text = invoice["data_emiterii"][:10]  # BT-2
    ET.SubElement(root, _cbc("InvoiceTypeCode")).text = "380"  # BT-3
    if invoice.get("data_scadentei"):
        ET.SubElement(root, _cbc("DueDate")).text = invoice["data_scadentei"][:10]  # BT-9
    moneda = invoice.get("moneda") or "RON"
    ET.SubElement(root, _cbc("DocumentCurrencyCode")).text = moneda  # BT-5

    _party("AccountingSupplierParty", root, furnizor["nume"], furnizor["cui"])  # BT-27/BT-31
    _party("AccountingCustomerParty", root, invoice["firm_name"], invoice["firm_cui"])  # BT-44/BT-48

    neta = round(float(invoice["valoare_neta"]), 2)
    tva = round(float(invoice["valoare_tva"]), 2)
    totala = round(float(invoice["valoare_totala"]), 2)
    cota = invoice.get("cota_tva", 19)

    tax_total = ET.SubElement(root, _cac("TaxTotal"))
    amt = ET.SubElement(tax_total, _cbc("TaxAmount"))
    amt.set("currencyID", moneda)
    amt.text = f"{tva:.2f}"
    subtotal = ET.SubElement(tax_total, _cac("TaxSubtotal"))
    taxable = ET.SubElement(subtotal, _cbc("TaxableAmount"))
    taxable.set("currencyID", moneda)
    taxable.text = f"{neta:.2f}"
    sub_amt = ET.SubElement(subtotal, _cbc("TaxAmount"))
    sub_amt.set("currencyID", moneda)
    sub_amt.text = f"{tva:.2f}"
    category = ET.SubElement(subtotal, _cac("TaxCategory"))
    ET.SubElement(category, _cbc("ID")).text = "S"  # BT-151
    ET.SubElement(category, _cbc("Percent")).text = f"{cota:.0f}"  # BT-152
    cat_scheme = ET.SubElement(category, _cac("TaxScheme"))
    ET.SubElement(cat_scheme, _cbc("ID")).text = "VAT"

    monetary = ET.SubElement(root, _cac("LegalMonetaryTotal"))
    for tag, val in (
        ("LineExtensionAmount", neta), ("TaxExclusiveAmount", neta),
        ("TaxInclusiveAmount", totala), ("PayableAmount", totala),
    ):
        el = ET.SubElement(monetary, _cbc(tag))
        el.set("currencyID", moneda)
        el.text = f"{val:.2f}"

    line = ET.SubElement(root, _cac("InvoiceLine"))
    ET.SubElement(line, _cbc("ID")).text = "1"
    qty = ET.SubElement(line, _cbc("InvoicedQuantity"))
    qty.set("unitCode", "C62")  # UN/ECE Rec 20: "one" - generic unit for a service line
    qty.text = "1"
    line_amt = ET.SubElement(line, _cbc("LineExtensionAmount"))
    line_amt.set("currencyID", moneda)
    line_amt.text = f"{neta:.2f}"
    item = ET.SubElement(line, _cac("Item"))
    ET.SubElement(item, _cbc("Name")).text = invoice["descriere"]
    item_cat = ET.SubElement(item, _cac("ClassifiedTaxCategory"))
    ET.SubElement(item_cat, _cbc("ID")).text = "S"  # BT-151 (line-level)
    ET.SubElement(item_cat, _cbc("Percent")).text = f"{cota:.0f}"  # BT-152 (line-level)
    item_scheme = ET.SubElement(item_cat, _cac("TaxScheme"))
    ET.SubElement(item_scheme, _cbc("ID")).text = "VAT"
    price = ET.SubElement(line, _cac("Price"))
    price_amt = ET.SubElement(price, _cbc("PriceAmount"))  # BT-146 - always positive
    price_amt.set("currencyID", moneda)
    price_amt.text = f"{neta:.2f}"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
