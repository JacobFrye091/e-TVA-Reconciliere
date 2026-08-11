"""Client pentru serviciul web public de bilant al ANAF (situatii financiare
anuale) - https://webservicesp.anaf.ro/bilant?an=YYYY&cui=NNN

Serviciu OFICIAL ANAF, public, fara autentificare si fara cheie de API
(listat pe anaf.ro, sectiunea "servicii web", ca "informatii din situatiile
financiare anuale ale agentilor economici"). Spre deosebire de SAF-T (care
cere fisierul de la contabil) sau de completarea manuala, aici datele vin
direct de la ANAF, doar pe baza CUI-ului.

Structura raspunsului (confirmata live 2026-08-11 pe doua firme de marimi
foarte diferite - o microintreprindere cu 1 salariat si o corporatie cu
3191 salariati - indicatorii I1..I20 au fost IDENTICI ca numerotare si
semnificatie, deci maparea de mai jos nu depinde de tipul de formular
depus):
    I1  ACTIVE IMOBILIZATE - TOTAL      I13 Cifra de afaceri neta
    I2  ACTIVE CIRCULANTE - TOTAL       I14 VENITURI TOTALE
    I3  Stocuri                         I15 CHELTUIELI TOTALE
    I4  Creante                         I16 Profit brut
    I5  Casa si conturi la banci        I17 Pierdere bruta
    I6  CHELTUIELI IN AVANS             I18 Profit net
    I7  DATORII                         I19 Pierdere neta
    I8  VENITURI IN AVANS               I20 Numar mediu de salariati
    I9  PROVIZIOANE
    I10 CAPITALURI - TOTAL
    I11 Capital subscris varsat
    I12 Patrimoniul regiei

LIMITARE IMPORTANTA, de propagat in UI si in raportul PDF: bilantul e
ANUAL si depus cu decalaj (bilantul pe 2025 apare abia in a doua jumatate
a lui 2026). Cifrele descriu situatia la 31 decembrie a anului de
referinta, NU perioada curenta - de aceea SAF-T ramane sursa recomandata
pentru actualitate, iar aici intoarcem mereu si `an`, ca apelantul sa
poata spune limpede din ce exercitiu financiar vin datele.

Un CUI inexistent sau un an pentru care nu s-a depus inca bilantul intorc
un raspuns valid cu lista de indicatori goala (`"i": []`) si denumire
goala - nu o eroare HTTP - de aceea extrage_bilant() trateaza acest caz ca
"fara date" (None), nu ca esec.
"""
import datetime
import json
import urllib.error
import urllib.request

from etva.anaf_cui import normalize_cui

_ANAF_URL = "https://webservicesp.anaf.ro/bilant"
_TIMEOUT = 8

_I_ACTIVE_IMOBILIZATE = "I1"
_I_DATORII = "I7"
_I_CAPITALURI = "I10"
_I_CIFRA_AFACERI = "I13"
_I_PROFIT_NET = "I18"
_I_PIERDERE_NETA = "I19"
_I_NUMAR_SALARIATI = "I20"


class AnafBilantError(Exception):
    """Serviciul de bilant al ANAF nu a putut fi contactat sau a raspuns
    cu ceva ce nu e JSON - o problema de conectivitate, NU dovada ca firma
    n-are bilant depus (acela e un raspuns valid, cu lista goala)."""


def _fetch(numeric_cui: int, an: int) -> dict:
    url = f"{_ANAF_URL}?an={an}&cui={numeric_cui}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AnafBilantError(
            f"Serviciul de bilant ANAF nu a putut fi contactat: {exc}") from exc
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AnafBilantError(
            "Raspuns neasteptat de la serviciul de bilant ANAF.") from exc


def _indicatori(raspuns: dict) -> dict:
    """Lista [{indicator, val_indicator, ...}] -> {"I1": 123.0, ...}."""
    valori = {}
    for intrare in raspuns.get("i") or []:
        cheie = intrare.get("indicator")
        if not cheie:
            continue
        try:
            valori[cheie] = float(intrare.get("val_indicator") or 0)
        except (TypeError, ValueError):
            valori[cheie] = 0.0
    return valori


def extrage_bilant(cui: str, an: "int | None" = None,
                   ani_inapoi: int = 2) -> "dict | None":
    """Ultimul bilant depus la ANAF pentru `cui`, sau None daca nu exista
    niciunul in intervalul cautat.

    Cauta descrescator, incepand cu `an` (implicit: anul trecut - bilantul
    anului in curs nu e depus inca) si mergand `ani_inapoi` ani in urma.
    Prima incercare care intoarce indicatori castiga. Motivul fallback-ului:
    intre 1 ianuarie si termenul de depunere (~mai), bilantul anului trecut
    inca nu e disponibil, deci trebuie sa cadem pe cel de acum doi ani in
    loc sa raportam "fara date".

    Ridica AnafBilantError daca serviciul nu poate fi contactat deloc -
    apelantul decide daca asta blocheaza evaluarea sau doar o degradeaza.
    """
    numeric_cui = normalize_cui(cui)
    an_start = an if an is not None else datetime.date.today().year - 1
    for candidat in range(an_start, an_start - ani_inapoi - 1, -1):
        raspuns = _fetch(numeric_cui, candidat)
        valori = _indicatori(raspuns)
        if not valori:
            continue

        profit_net = valori.get(_I_PROFIT_NET, 0.0)
        pierdere_neta = valori.get(_I_PIERDERE_NETA, 0.0)
        # ANAF raporteaza profitul si pierderea in doua campuri separate,
        # ambele pozitive, cel neaplicabil fiind 0 - le unificam intr-un
        # singur rezultat cu semn, cum il asteapta etva/risc_fiscal.py.
        rezultat_net = profit_net if profit_net else -pierdere_neta

        return {
            "an": raspuns.get("an", candidat),
            "denumire": raspuns.get("deni", "") or "",
            "caen": raspuns.get("caen") or None,
            "capitaluri_proprii": valori.get(_I_CAPITALURI, 0.0),
            "datorii_totale": valori.get(_I_DATORII, 0.0),
            "cifra_afaceri": valori.get(_I_CIFRA_AFACERI, 0.0),
            "rezultat_net": rezultat_net,
            "active_imobilizate": valori.get(_I_ACTIVE_IMOBILIZATE, 0.0),
            "numar_salariati": int(valori.get(_I_NUMAR_SALARIATI, 0.0)),
        }
    return None


def extrage_istoric(cui: str, ani: int = 3, an_start: "int | None" = None) -> list:
    """Ultimele `ani` exercitii financiare depuse, cel mai recent primul.

    Sare peste anii fara bilant depus in loc sa se opreasca la primul gol,
    fiindca o firma poate avea o intrerupere in depuneri. Intoarce o lista
    (posibil goala) - nu ridica exceptii daca un an anume nu poate fi luat,
    fiindca istoricul e un bonus analitic pentru raport, nu o conditie de
    functionare a evaluarii.
    """
    inceput = an_start if an_start is not None else datetime.date.today().year - 1
    istoric = []
    # Cauta cu cativa ani mai mult decat cere, ca o intrerupere de depunere
    # sa nu scurteze rezultatul sub `ani` exercitii disponibile.
    for candidat in range(inceput, inceput - ani - 3, -1):
        if len(istoric) >= ani:
            break
        try:
            an = extrage_bilant(cui, an=candidat, ani_inapoi=0)
        except (ValueError, AnafBilantError):
            break
        if an:
            istoric.append(an)
    return istoric


# Sub acest prag, diferentele fata de bilant nu se semnaleaza deloc: sume
# mici oscileaza natural si ar produce doar zgomot.
_PRAG_ABSOLUT_DISCREPANTA = 1000.0
# Raportul de la care o diferenta nu mai poate fi pusa pe seama evolutiei
# normale a firmei si arata mai degraba a fisier gresit sau greseala de
# tastare (ex. o suma trecuta de 1000 de ori mai mare).
_PRAG_RAPORT_DISCREPANTA = 10.0

_ETICHETE_COMPARATIE = {
    "capitaluri_proprii": "capitalurile proprii",
    "datorii_totale": "datoriile totale",
}


def _ron(valoare: float) -> str:
    """Suma in format romanesc (punct la mii): 531748.0 -> "531.748".
    Formatam numarul separat, NU cu .replace() pe fraza intreaga - altfel
    s-ar strica si virgulele gramaticale din textul mesajului."""
    return f"{valoare:,.0f}".replace(",", ".")


def compara_cu_bilant(date_financiare: dict, bilant: dict) -> list:
    """Compara cifrele introduse (SAF-T sau manual) cu ultimul bilant depus
    si intoarce o lista de avertismente in limbaj natural.

    Scopul e sa prinda fisierul gresit / firma gresita / virgula pusa aiurea,
    NU sa valideze contabilitatea: bilantul e anual si mai vechi decat
    perioada evaluata, deci diferentele mici sunt normale si asteptate.
    De aceea se compara doar pozitiile de bilant care evolueaza lent
    (capitaluri proprii, datorii) - NU cifra de afaceri sau rezultatul net,
    care sunt cumulate de la inceputul anului si deci in mod normal
    fractiuni din valoarea anuala la orice moment din cursul anului.

    Se semnaleaza doar doua situatii fara explicatie fireasca:
      - schimbarea de semn a capitalurilor proprii (pozitiv <-> negativ),
        care singura muta indicatorul 1 cu 100 de puncte;
      - o diferenta de cel putin 10x intr-un sens sau altul.
    """
    avertismente = []
    an = bilant.get("an")

    for camp, eticheta in _ETICHETE_COMPARATIE.items():
        introdus = date_financiare.get(camp)
        oficial = bilant.get(camp)
        if introdus is None or oficial is None:
            continue
        if max(abs(introdus), abs(oficial)) < _PRAG_ABSOLUT_DISCREPANTA:
            continue

        if camp == "capitaluri_proprii" and (introdus < 0) != (oficial < 0):
            avertismente.append(
                f"Ai introdus {eticheta} {_ron(introdus)} RON, dar bilanțul pe "
                f"{an} depus la ANAF arată {_ron(oficial)} RON — semn opus. "
                f"Verifică: singură, această diferență schimbă punctajul "
                f"indicatorului 1 cu 100 de puncte.")
            continue

        if abs(oficial) < _PRAG_ABSOLUT_DISCREPANTA:
            continue
        raport = abs(introdus) / abs(oficial)
        if raport >= _PRAG_RAPORT_DISCREPANTA or raport <= 1 / _PRAG_RAPORT_DISCREPANTA:
            avertismente.append(
                f"Ai introdus {eticheta} {_ron(introdus)} RON, dar bilanțul pe "
                f"{an} depus la ANAF arată {_ron(oficial)} RON. Verifică dacă "
                f"fișierul sau cifra corespund firmei și perioadei evaluate.")
    return avertismente
