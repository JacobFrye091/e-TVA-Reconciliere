"""FGO (fgo.ro) client - facturare proprie catre firmele care folosesc
platforma (vezi portal/app.py::creeaza_factura). Inlocuieste vechiul lant
efactura_xml.build_invoice_xml + anaf_oauth.upload_invoice/check_upload_status
- vezi planning/plans pentru context.

Trimiterea la SPV/e-Factura NU e un apel API separat: se activeaza o
singura data, manual, din contul FGO (Setari - e-Factura), unde se
configureaza si intervalul dupa emitere. Niciun endpoint factura/* nu
expune un camp de stare SPV - get_status de mai jos e despre INCASARI
(plati), nu despre livrarea la SPV. Codul asta nu poate si nu trebuie sa
verifice starea SPV.

Mirrors etva/esemneaza.py's conventions: stdlib urllib only (fara
dependinta requests in acest proiect), un singur Error, functiile intorc
dict-uri parsate din JSON, ridica FgoError la esec de transport/HTTP sau
la Success=false.
"""
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

_BASE_URLS = {
    "test": "https://api-testuat.fgo.ro/v1",
    "productie": "https://api.fgo.ro/v1",
}
# Documentatia FGO: timpul maxim intre primirea cererii si formarea
# raspunsului la emitere e tot 15s server-side - peste asta FGO insusi
# raspunde cu eroare de timeout (cod intern [1-7], de tratat ca retry).
_TIMEOUT = 15


class FgoError(Exception):
    """FGO nu a putut fi contactat sau a raspuns cu Success=false."""


def _base_url(mediu: str) -> str:
    try:
        return _BASE_URLS[mediu]
    except KeyError:
        raise FgoError(
            f"Mediu FGO necunoscut: {mediu!r} (asteptat 'test' sau 'productie')")


def _hash_emitere(cod_unic: str, cheie_privata: str, client_denumire: str) -> str:
    raw = f"{cod_unic}{cheie_privata}{client_denumire}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest().upper()


def _hash_numar_factura(cod_unic: str, cheie_privata: str, numar_factura: str) -> str:
    """Anulare, Stornare, Print, Stergere, GetStatus, Incasare,
    StergereIncasare, AWB, ListFacturiAsociate - foloseste NUMARUL facturii
    asa cum l-a intors Emitere (ex. "001"), fara serie."""
    raw = f"{cod_unic}{cheie_privata}{numar_factura}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest().upper()


def _call(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise FgoError(
            f"FGO a refuzat cererea ({exc.code}): "
            f"{exc.read().decode('utf-8', 'replace')}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FgoError(f"Serviciul FGO nu a putut fi contactat: {exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FgoError("Raspuns neasteptat de la FGO (nu e JSON).") from exc
    if not result.get("Success"):
        raise FgoError(result.get("Message") or
                       "FGO a raspuns cu Success=false, fara mesaj.")
    return result


def _post(mediu: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{_base_url(mediu)}{path}", data=json.dumps(body).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    return _call(req)


def _hash_fara_date(cod_unic: str, cheie_privata: str) -> str:
    """Articole (list/get/getlist/articolemodificate/gestiune) si
    nomenclatoare - fara alte date in hash."""
    return hashlib.sha1(f"{cod_unic}{cheie_privata}".encode("utf-8")).hexdigest().upper()


def get_nomenclator(cod_unic: str, cheie_privata: str, platforma_url: str, mediu: str,
                    tip: str, judet: "str | None" = None) -> "list[dict]":
    """tip: tara/judet/tva/banca/tipincasare/tipfactura/tipclient/localitati
    (localitati cere `judet`=codul judetului, ex. "AG"). Foloseste-le ca sa
    validezi/traduci valori inainte de emitere - nu inventa o valoare
    pentru TipFactura/CotaTVA/Judet.

    CONFIRMAT EMPIRIC (2026-08-02, cont test real): desi exemplul Python
    din skill-ul fgo trimite parametrii ca JSON body si pe GET (ca la
    POST), asta pica cu 403 de la CloudFront ("Bad request") - trebuie
    query string. Raspunsul are cheia "List" (nu "Date"/"Items" cum ar
    sugera alte API-uri), elemente {"Nume": ..., "Cod": ...}, fara
    diacritice (ex. "Arges", "Bucuresti")."""
    params = {"CodUnic": cod_unic, "Hash": _hash_fara_date(cod_unic, cheie_privata),
             "PlatformaUrl": platforma_url}
    if judet:
        params["judet"] = judet
    url = f"{_base_url(mediu)}/nomenclator/{tip}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    result = _call(req)
    return result.get("List", [])


def emite_factura(cod_unic: str, cheie_privata: str, platforma_url: str, mediu: str, *,
                  serie: str, valuta: str, tip_factura: str, client: dict,
                  continut: "list[dict]", numar: "str | None" = None,
                  data_emitere: "str | None" = None,
                  data_scadenta: "str | None" = None,
                  text: "str | None" = None) -> dict:
    """client: dict cu cheile Denumire/CodUnic/Tara/Judet/Tip/... (vezi
    references/api-invoices.md din skill-ul fgo). continut: lista de
    dict-uri Denumire/CodArticol/NrProduse/UM/CotaTVA/PretUnitar - trimite
    intotdeauna acelasi CodArticol fix (ex. "ABONAMENT") pentru linia de
    abonament, altfel contul FGO (setat pe "se creeaza articol nou" la
    denumire diferita) umple catalogul cu un articol nou la fiecare
    factura cu descriere diferita.

    Intoarce direct result["Factura"]: {"Numar", "Serie", "Link",
    "LinkPlata"} - astea sunt seria/numarul REALE atribuite de FGO (poate
    genera Numar automat daca lipseste) - persista-le pe alea in
    `invoices`, nu o numerotare locala separata.

    CONFIRMAT EMPIRIC (2026-08-02): `Continut[i][CotaTVA]` trimis ca float
    intreg (ex. 21.0, cum il produce Python dintr-un form field parsat cu
    type=float) e RESPINS de FGO cu "CotaTVA ... nu a fost transmisa sau
    valoarea 21,0 nu exista in nomenclator" - trebuie trimis ca 21 (int).
    Normalizat mai jos, ca apelantul sa nu trebuiasca sa stie de asta."""
    continut_normalizat = []
    for linie in continut:
        linie = dict(linie)
        cota = linie.get("CotaTVA")
        if isinstance(cota, float) and cota.is_integer():
            linie["CotaTVA"] = int(cota)
        continut_normalizat.append(linie)
    body = {
        "CodUnic": cod_unic,
        "Hash": _hash_emitere(cod_unic, cheie_privata, client["Denumire"]),
        "PlatformaUrl": platforma_url,
        "Serie": serie,
        "Valuta": valuta,
        "TipFactura": tip_factura,
        "Client": client,
        "Continut": continut_normalizat,
    }
    if numar:
        body["Numar"] = numar
    if data_emitere:
        body["DataEmitere"] = data_emitere
    if data_scadenta:
        body["DataScadenta"] = data_scadenta
    if text:
        body["Text"] = text
    result = _post(mediu, "/factura/emitere", body)
    return result["Factura"]


def get_status(cod_unic: str, cheie_privata: str, platforma_url: str, mediu: str,
               serie: str, numar: str) -> dict:
    """Stare de INCASARE (Valoare/ValoareAchitata/Incasari) - NU stare SPV,
    vezi docstring-ul modulului."""
    body = {
        "CodUnic": cod_unic,
        "Hash": _hash_numar_factura(cod_unic, cheie_privata, numar),
        "PlatformaUrl": platforma_url,
        "Serie": serie,
        "Numar": numar,
    }
    result = _post(mediu, "/factura/getstatus", body)
    return result["Factura"]
