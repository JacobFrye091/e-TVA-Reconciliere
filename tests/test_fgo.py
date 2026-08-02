import hashlib
import json

import pytest

from etva import fgo


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _install_fake_urlopen(monkeypatch, handler):
    """handler(request) -> bytes, or raises HTTPError/URLError."""
    def _fake_urlopen(req, timeout=None):
        return _FakeResponse(handler(req))
    monkeypatch.setattr(fgo.urllib.request, "urlopen", _fake_urlopen)


def test_emite_factura_sends_correct_hash_and_url(monkeypatch):
    captured = {}

    def handler(req):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return json.dumps({
            "Success": True, "Message": "",
            "Factura": {"Numar": "0001", "Serie": "VML",
                       "Link": "https://fgo.ro/x", "LinkPlata": None},
        }).encode()

    _install_fake_urlopen(monkeypatch, handler)
    factura = fgo.emite_factura(
        "35070700", "cheie-secreta", "https://appultau.com", "test",
        serie="VML", valuta="RON", tip_factura="Factura",
        client={"Denumire": "Ionescu Popescu", "Tara": "RO", "Judet": "Bucuresti",
               "Tip": "PF"},
        continut=[{"Denumire": "Servicii", "NrProduse": 1, "UM": "BUC",
                  "CotaTVA": 21, "PretUnitar": 100.0}])

    assert captured["url"] == "https://api-testuat.fgo.ro/v1/factura/emitere"
    hash_asteptat = hashlib.sha1(
        "35070700cheie-secretaIonescu Popescu".encode()).hexdigest().upper()
    assert captured["body"]["Hash"] == hash_asteptat
    assert captured["body"]["CodUnic"] == "35070700"
    assert factura == {"Numar": "0001", "Serie": "VML",
                       "Link": "https://fgo.ro/x", "LinkPlata": None}


def test_emite_factura_normalizes_whole_number_cota_tva_to_int(monkeypatch):
    """Regresie: FGO respinge CotaTVA=21.0 (float) cu "nu exista in
    nomenclator", desi 21 (int) e valid - vezi docstring-ul emite_factura,
    confirmat empiric 2026-08-02 impotriva contului real de test."""
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.data)
        return json.dumps({
            "Success": True, "Message": "",
            "Factura": {"Numar": "0002", "Serie": "VML", "Link": "x",
                       "LinkPlata": None},
        }).encode()

    _install_fake_urlopen(monkeypatch, handler)
    fgo.emite_factura(
        "35070700", "cheie", "https://appultau.com", "test",
        serie="VML", valuta="RON", tip_factura="Factura",
        client={"Denumire": "Test SRL", "Tara": "RO", "Judet": "Bucuresti",
               "Tip": "PJ"},
        continut=[{"Denumire": "Abonament", "NrProduse": 1, "UM": "BUC",
                  "CotaTVA": 21.0, "PretUnitar": 100.0},
                 {"Denumire": "Discount", "NrProduse": 1, "UM": "BUC",
                  "CotaTVA": 6.1, "PretUnitar": 5.0}])

    linii = captured["body"]["Continut"]
    assert linii[0]["CotaTVA"] == 21
    assert isinstance(linii[0]["CotaTVA"], int)
    # cota fractionara ramane neschimbata - nu e parte din bug-ul confirmat.
    assert linii[1]["CotaTVA"] == 6.1


def test_emite_factura_raises_on_success_false(monkeypatch):
    def handler(req):
        return json.dumps({"Success": False,
                           "Message": "Codul unic nu exista sau nu este asociat."}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    with pytest.raises(fgo.FgoError, match="Codul unic nu exista"):
        fgo.emite_factura(
            "35070700", "cheie", "https://appultau.com", "test",
            serie="VML", valuta="RON", tip_factura="Factura",
            client={"Denumire": "X", "Tara": "RO", "Judet": "Bucuresti", "Tip": "PJ"},
            continut=[{"Denumire": "Y", "NrProduse": 1, "UM": "BUC",
                      "CotaTVA": 21, "PretUnitar": 1.0}])


def test_get_nomenclator_sends_query_string_not_json_body(monkeypatch):
    """Regresie: trimiterea parametrilor ca JSON body pe GET (ca in
    exemplul din skill-ul fgo) pica cu 403 de la CloudFront - trebuie
    query string. Vezi docstring-ul get_nomenclator."""
    captured = {}

    def handler(req):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return json.dumps({"Success": True,
                           "List": [{"Nume": "Bucuresti", "Cod": "B"}]}).encode()

    _install_fake_urlopen(monkeypatch, handler)
    rezultat = fgo.get_nomenclator("35070700", "cheie", "https://appultau.com",
                                   "test", "judet")

    assert captured["data"] is None
    assert "CodUnic=35070700" in captured["url"]
    assert rezultat == [{"Nume": "Bucuresti", "Cod": "B"}]
