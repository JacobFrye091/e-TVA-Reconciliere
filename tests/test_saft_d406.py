"""Teste pentru etva/importer/saft_d406.py - parserul de export SAF-T
D406. Fixture-ul de mai jos e SINTETIC (date/nume fictive) - fisierul REAL
folosit la validarea initiala a maparii conturilor a fost intentionat
exclus din depozit (depozitul GitHub e public, iar exportul real continea
IBAN/telefon/email/solduri ale unei firme reale)."""
import pytest

from etva.importer import saft_d406

_NS = "mfp:anaf:dgti:d406:declaratie:v1"


def _cont(account_id, tip, closing_debit=None, closing_credit=None):
    debit = (f"<nsSAFT:ClosingDebitBalance>{closing_debit}</nsSAFT:ClosingDebitBalance>"
            if closing_debit is not None else "")
    credit = (f"<nsSAFT:ClosingCreditBalance>{closing_credit}</nsSAFT:ClosingCreditBalance>"
             if closing_credit is not None else "")
    return (f"<nsSAFT:Account><nsSAFT:AccountID>{account_id}</nsSAFT:AccountID>"
           f"<nsSAFT:AccountType>{tip}</nsSAFT:AccountType>{debit}{credit}</nsSAFT:Account>")


def _fisier(conturi: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<nsSAFT:AuditFile xmlns:nsSAFT="{_NS}">'
        '<nsSAFT:Header/>'
        '<nsSAFT:MasterFiles><nsSAFT:GeneralLedgerAccounts>'
        f'{conturi}'
        '</nsSAFT:GeneralLedgerAccounts></nsSAFT:MasterFiles>'
        '</nsSAFT:AuditFile>'
    ).encode("utf-8")


def test_extrage_date_financiare_mapare_pe_clase_de_conturi():
    """Cazuri acoperite, calibrate pe structura confirmata pe un export
    real (SAGA C): capital (1012) + rezultat curent (121, Bifunctional) -
    cont-corectie cu sold debitor (129, Activ); datorii doar din clasa 4
    Pasiv/Bifunctional (401, 4423), EXCLUZAND explicit un cont de creanta
    (411, Activ) si un cont de trezorerie (512, clasa 5, chiar daca
    Bifunctional); cifra de afaceri doar din grupa 70 (704), nu toata
    clasa 7."""
    xml = _fisier(
        _cont("1012", "Pasiv", closing_credit=10000) +
        _cont("129", "Activ", closing_debit=500) +
        _cont("121", "Bifunctional", closing_credit=3000) +
        _cont("401", "Pasiv", closing_credit=2000) +
        _cont("411", "Activ", closing_debit=1500) +
        _cont("4423", "Pasiv", closing_credit=800) +
        _cont("512101", "Bifunctional", closing_debit=5000) +
        _cont("704", "Pasiv", closing_credit=12000) +
        _cont("607", "Activ", closing_debit=4000))
    date = saft_d406.extrage_date_financiare(xml)
    assert date["capitaluri_proprii"] == 12500.0  # 10000 - 500 + 3000
    assert date["datorii_totale"] == 2800.0        # 401 + 4423 (411 si 512 excluse)
    assert date["cifra_afaceri"] == 12000.0        # doar grupa 70
    assert date["rezultat_net"] == 3000.0          # contul 121


def test_extrage_date_financiare_sold_imobilizari_din_clasa_2():
    """sold_imobilizari (clasa 2 - imobilizari) e un reper suplimentar,
    separat de indicatorii de scor, folosit de portal/app.py sa avertizeze
    contabilul daca bifa manuala 'Lipsa bunurilor imobile sau mobile'
    (Sectiunea B) contrazice balanta reala."""
    xml = _fisier(
        _cont("2131", "Activ", closing_debit=15000) +
        _cont("2813", "Activ", closing_credit=4000))  # amortizare cumulata
    date = saft_d406.extrage_date_financiare(xml)
    assert date["sold_imobilizari"] == 11000.0  # 15000 - 4000


def test_extrage_date_financiare_cont_519_credit_bancar_intra_in_datorii():
    """Contul 519 (credite bancare pe termen scurt) trebuie inclus in
    datorii chiar daca e clasa 5 (exclusa altfel in intregime, ca sa nu
    prinda gresit conturi de trezorerie/banca)."""
    xml = _fisier(_cont("5191", "Pasiv", closing_credit=7000))
    date = saft_d406.extrage_date_financiare(xml)
    assert date["datorii_totale"] == 7000.0


def test_extrage_date_financiare_respinge_xml_invalid():
    with pytest.raises(saft_d406.SaftD406Error):
        saft_d406.extrage_date_financiare(b"nu sunt deloc un fisier xml")


def test_extrage_date_financiare_respinge_fisier_fara_conturi():
    xml = f'<nsSAFT:AuditFile xmlns:nsSAFT="{_NS}"><nsSAFT:Header/></nsSAFT:AuditFile>'.encode()
    with pytest.raises(saft_d406.SaftD406Error):
        saft_d406.extrage_date_financiare(xml)


def test_extrage_date_financiare_functioneaza_fara_prefix_namespace():
    """Alte programe de contabilitate ar putea exporta cu namespace
    implicit (fara prefix) - parserul se bazeaza pe namespace URI, nu pe
    prefixul din document, deci trebuie sa functioneze identic."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<AuditFile xmlns="{_NS}">'
        '<Header/>'
        '<MasterFiles><GeneralLedgerAccounts>'
        '<Account><AccountID>1012</AccountID><AccountType>Pasiv</AccountType>'
        '<ClosingCreditBalance>500</ClosingCreditBalance></Account>'
        '</GeneralLedgerAccounts></MasterFiles>'
        '</AuditFile>'
    ).encode("utf-8")
    date = saft_d406.extrage_date_financiare(xml)
    assert date["capitaluri_proprii"] == 500.0


def test_extrage_date_financiare_ignora_conturi_fara_sold():
    """Un cont fara nicio balanta inregistrata (ambele campuri lipsa din
    XML) nu trebuie sa strice calculul - tratat ca 0."""
    xml = _fisier(_cont("1012", "Pasiv"))
    date = saft_d406.extrage_date_financiare(xml)
    assert date["capitaluri_proprii"] == 0.0
