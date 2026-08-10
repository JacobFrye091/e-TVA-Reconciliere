"""Parser al fisierului XML SAF-T (D406), OPANAF 1783/2021 - extrage
indicatorii financiari 1-3 din etva/risc_fiscal.py (capitaluri proprii,
datorii totale, cifra de afaceri) plus rezultatul net, direct din sectiunea
MasterFiles/GeneralLedgerAccounts a exportului.

Validat empiric pe un export real D406 (SAGA C, firma VML EXPERT
CONSULTING SRL, iunie 2026) - nu doar pe documentatia oficiala ANAF
(schema.xsd/406.html, static.anaf.ro/static/10/Anaf/Declaratii_R/406.html,
care e doar un index de descarcare, fara detalii structurale in text).

Mapare folosita (aproximare, nu un calcul fiscal oficial):
  - Capitaluri proprii = suma soldurilor (credit - debit) conturilor din
    clasa 1 (capital, rezerve, rezultat reportat, rezultat exercitiu,
    repartizarea profitului) - un cont-corectie cu sold debitor (ex. 129
    "Repartizarea profitului") se scade automat prin semnul soldului lui.
  - Rezultat net = soldul contului 121 "Profit si pierdere" (credit -
    debit). Confirmat pe fisierul real: acest cont e tinut CUMULAT pe anul
    fiscal curent (soldul initial de la 1 ianuarie, nu zero), deci e cel
    mai direct indicator posibil - nu mai reconstruim diferenta clasa 7 -
    clasa 6, care ar fi supusa acelorasi ambiguitati de perioada.
  - Datorii totale = suma soldurilor creditoare nete ale conturilor din
    clasa 4 marcate Pasiv/Bifunctional de programul de contabilitate
    (obligatii fata de terti: furnizori, personal, bugetul statului,
    asigurari sociale, TVA de plata) + eventualele credite bancare pe
    termen scurt (cont 519, daca exista). Conturile de clasa 4 cu natura
    de creanta (clienti 411, debitori 461, avansuri 425/4093, TVA
    deductibila 4426, cheltuieli inregistrate in avans 471), marcate Activ
    de programul de contabilitate, sunt EXCLUSE explicit - altfel ar
    scadea gresit totalul de datorii doar pentru ca firma are si creante.
    Clasa 5 (trezorerie/banca/casa) e exclusa in intregime, chiar daca
    unele conturi bancare apar "Bifunctional" in export - sunt lichiditati
    (activ), nu datorii, chiar daca soldul lor curent e debitor.
  - Cifra de afaceri = suma soldurilor creditoare ale conturilor din grupa
    70 (venituri din vanzarea productiei/marfurilor/prestari servicii) -
    NU toata clasa 7 (care include si venituri financiare/exceptionale,
    ce nu intra in cifra de afaceri neta conform Legii Contabilitatii).

Limitari cunoscute, de verificat pe viitoare fisiere reale (alte programe
de contabilitate decat SAGA, sau exporturi anuale in loc de lunare):
  - Perioada acoperita de export (Header/SelectionCriteria) poate fi
    lunara sau anuala - in fisierul de test (lunar), soldurile claselor
    6/7 par tinute CUMULAT de la inceputul anului fiscal (nu resetate
    lunar), deci "cifra de afaceri" extrasa e cea cumulata de la 1
    ianuarie pana la data exportului, nu doar a lunii declarate. De
    confirmat ca alte programe (WinMentor etc.) se comporta identic.
  - Contul 519 (credite bancare pe termen scurt) nu a aparut in fisierul
    de test - inclus doar ca regula, nevalidat empiric inca.
  - Varianta SAF-T redusa pentru micro-entitati (daca difera structural de
    cea completa analizata aici) nu a fost inca verificata pe un fisier
    real.
"""
import xml.etree.ElementTree as ET


class SaftD406Error(Exception):
    """Fisierul nu a putut fi parsat ca export SAF-T D406 valid."""


def _namespace(root: ET.Element) -> str:
    return root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""


def _numar(cont: ET.Element, ns: dict, tag: str) -> float:
    el = cont.find(f"n:{tag}", ns)
    if el is None or not el.text or not el.text.strip():
        return 0.0
    try:
        return float(el.text.strip())
    except ValueError:
        return 0.0


def extrage_date_financiare(xml_bytes: bytes) -> dict:
    """Extrage {"capitaluri_proprii", "datorii_totale", "cifra_afaceri",
    "rezultat_net", "sold_imobilizari"} direct din
    MasterFiles/GeneralLedgerAccounts a unui export SAF-T D406. Ridica
    SaftD406Error daca fisierul nu e XML valid sau nu are sectiunea
    asteptata - apelantul decide mesajul de eroare afisat contabilului.

    sold_imobilizari (suma soldurilor debitoare nete ale conturilor din
    clasa 2 - imobilizari corporale/necorporale/in curs) NU alimenteaza
    niciun indicator de scor - e doar un reper folosit de portal/app.py ca
    sa nu se bazeze orbeste pe bifa manuala "Lipsa bunurilor imobile sau
    mobile" (Sectiunea B): daca balanta arata clar imobilizari inregistrate,
    contabilul e avertizat sa reverifice bifa, in loc sa fie doar crezut pe
    cuvant."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise SaftD406Error(f"Fisierul nu e XML valid: {e}") from e
    ns = {"n": _namespace(root)}
    conturi = root.findall(".//n:GeneralLedgerAccounts/n:Account", ns)
    if not conturi:
        raise SaftD406Error(
            "Fisierul nu contine sectiunea MasterFiles/GeneralLedgerAccounts "
            "asteptata intr-un export SAF-T D406.")

    capitaluri_proprii = 0.0
    datorii_totale = 0.0
    cifra_afaceri = 0.0
    rezultat_net = 0.0
    sold_imobilizari = 0.0

    for cont in conturi:
        id_el = cont.find("n:AccountID", ns)
        if id_el is None or not id_el.text or not id_el.text.strip():
            continue
        cont_id = id_el.text.strip()
        tip_el = cont.find("n:AccountType", ns)
        tip = tip_el.text.strip() if tip_el is not None and tip_el.text else ""
        credit = _numar(cont, ns, "ClosingCreditBalance")
        debit = _numar(cont, ns, "ClosingDebitBalance")
        sold_creditor_net = credit - debit

        clasa = cont_id[0]
        if clasa == "1":
            capitaluri_proprii += sold_creditor_net
        if cont_id == "121":
            rezultat_net = sold_creditor_net
        if (clasa == "4" and tip in ("Pasiv", "Bifunctional")) or cont_id.startswith("519"):
            datorii_totale += sold_creditor_net
        if cont_id.startswith("70"):
            cifra_afaceri += credit
        if clasa == "2":
            sold_imobilizari += -sold_creditor_net

    return {
        "capitaluri_proprii": round(capitaluri_proprii, 2),
        "datorii_totale": round(datorii_totale, 2),
        "cifra_afaceri": round(cifra_afaceri, 2),
        "rezultat_net": round(rezultat_net, 2),
        "sold_imobilizari": round(sold_imobilizari, 2),
    }
