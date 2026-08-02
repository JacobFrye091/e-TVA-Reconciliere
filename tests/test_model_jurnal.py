"""Tests for the e-TVA 'model' journal format: the downloadable .xlsx
template offered to firms that don't export from SAGA, and its parser."""
import pandas as pd
import pytest

from etva.d300 import suggest_line
from etva.importer.model import (
    ANTET, FIRST_DATA_ROW_EXCEL, TIPURI_OPERATIUNE, build_model_template,
    parse_model_journal, NotModelFormat,
)


def _eticheta(directie, linie):
    for et, ln in TIPURI_OPERATIUNE[directie]:
        if ln == linie:
            return et
    raise KeyError(linie)


def _completeaza(ws, randuri):
    """randuri: list of (data, numar_doc, denumire, cui, baza, tva, eticheta)."""
    for i, rand in enumerate(randuri):
        for c, val in enumerate(rand):
            ws.cell(row=FIRST_DATA_ROW_EXCEL + i, column=c + 1, value=val)


def _write_saga_like(path):
    rows = [
        ["Exemplu Test SRL  c.f. RO12345678  r.c. J40/1/2026"] + [None] * 10,
        [None] * 11, [None] * 11, [None] * 11,
        [None, None, "JURNAL PENTRU VANZARI"] + [None] * 8,
        [None] * 11, [None] * 11,
        ["Nr. crt.", "Document", None, "Client/beneficiar", None, None, None,
         "Total document (inclusiv TVA)", "Baza  impozitare", "Valoare T.V.A.", "Referinta cod *)"],
        [None, "Data", "Numar", None, "Denumire", "Cod fiscal", None, None, None, None, None],
        [1, "2026-05-01", "F0001", "Client Unu SRL", None, "RO11111111", None, 1210, 1000, 210, "2-3"],
    ]
    pd.DataFrame(rows).to_excel(path, header=False, index=False, engine="openpyxl")


def test_roundtrip_vanzari(tmp_path):
    wb = build_model_template("vanzari")
    ws = wb["Jurnal"]
    _completeaza(ws, [
        ("2026-05-01", "F0001", "Client Unu SRL", "RO11111111", 1000, 210,
         _eticheta("vanzari", "9")),
        ("2026-05-05", "F0002", "Client Doi SRL", "RO22222222", 500, 0,
         _eticheta("vanzari", "1")),
        ("2026-05-10", "EXP-0001", "Foreign Client Ltd", "IE1234567X", 100, 0,
         _eticheta("vanzari", "3")),
    ])
    path = tmp_path / "model_vanzari.xlsx"
    wb.save(path)

    j = parse_model_journal(str(path))
    assert j.direction == "vanzari"
    assert len(j.entries) == 3
    assert j.entries[0]["partner_cui"] == "RO11111111"
    assert j.entries[0]["cod"] == "9"
    assert j.legend["9"] == {"label": _eticheta("vanzari", "9"),
                             "base": 1000, "vat": 210}
    assert j.legend["1"]["base"] == 500
    assert j.legend["3"]["base"] == 100


def test_roundtrip_cumparari(tmp_path):
    wb = build_model_template("cumparari")
    ws = wb["Jurnal"]
    _completeaza(ws, [
        ("2026-05-02", "FZ-100", "Furnizor Unu SRL", "RO33333333", 100, 21,
         _eticheta("cumparari", "24")),
        ("2026-05-03", "FZ-101", "EU Goods Ltd", "IE9999999X", 200, 0,
         _eticheta("cumparari", "20.1")),
        ("2026-05-04", "FZ-102", "Local Reverse SRL", "RO44444444", 300, 63,
         _eticheta("cumparari", "26.1")),
    ])
    path = tmp_path / "model_cumparari.xlsx"
    wb.save(path)

    j = parse_model_journal(str(path))
    assert j.direction == "cumparari"
    assert len(j.entries) == 3
    assert j.legend["24"] == {"label": _eticheta("cumparari", "24"),
                              "base": 100, "vat": 21}
    assert j.legend["20.1"]["base"] == 200
    assert j.legend["26.1"]["vat"] == 63


def test_sablon_structura():
    wb = build_model_template("vanzari")
    assert wb.sheetnames[0] == "Jurnal"
    assert "Instructiuni" in wb.sheetnames
    assert "Liste" in wb.sheetnames
    assert wb["Liste"].sheet_state == "hidden"

    ws = wb["Jurnal"]
    assert ws["A1"].value == "MODEL e-TVA - JURNAL DE VANZARI (v1)"
    assert [ws.cell(row=4, column=c + 1).value for c in range(len(ANTET))] == ANTET

    dvs = ws.data_validations.dataValidation
    assert len(dvs) == 1
    assert dvs[0].formula1 == "=Liste!$A$1:$A$7"
    assert "G5:G1000" in str(dvs[0].sqref)


# Etichetele din dropdown trebuie sa fie fara ambiguitate pentru
# suggest_line: fie confirma exact linia D300 asociata, fie se retrage la
# None (nemapat -> corectie manuala) — niciodata o alta linie gresita.
_ASTEPTARI_SUGGEST_LINE = {
    "vanzari": {"9": "9", "10": "10", "11": "11", "1": "1", "13": "13",
               "3": None, "14+15": None},
    "cumparari": {"24": "24", "25": "25", "20.1": None, "22.1": "22.1",
                 "26.1": "26.1", "26.2": "26.2", "29": "29"},
}


@pytest.mark.parametrize("directie", ["vanzari", "cumparari"])
def test_vocabular_suggest_line_pinuit(directie):
    asteptari = _ASTEPTARI_SUGGEST_LINE[directie]
    for eticheta, linie in TIPURI_OPERATIUNE[directie]:
        assert suggest_line(directie, eticheta) == asteptari[linie], (
            f"{directie}/{linie} ({eticheta!r}) nu se mai potriveste garda")


def test_eticheta_libera_ramane_ca_atare(tmp_path):
    wb = build_model_template("vanzari")
    ws = wb["Jurnal"]
    _completeaza(ws, [
        ("2026-05-01", "F0009", "Client Neclar SRL", "RO55555555", 400, 84,
         "Operatiune neclara, de verificat manual"),
    ])
    path = tmp_path / "model_liber.xlsx"
    wb.save(path)

    j = parse_model_journal(str(path))
    assert j.entries[0]["cod"] == "Operatiune neclara, de verificat manual"
    assert j.legend["Operatiune neclara, de verificat manual"]["base"] == 400


def test_fisier_saga_ridica_not_model_format(tmp_path):
    path = tmp_path / "saga.xlsx"
    _write_saga_like(path)
    with pytest.raises(NotModelFormat, match="MODEL e-TVA"):
        parse_model_journal(str(path))


def test_fisier_corupt_ridica_not_model_format(tmp_path):
    path = tmp_path / "corupt.xlsx"
    path.write_bytes(b"nu este un fisier excel valid")
    with pytest.raises(NotModelFormat, match="nu a putut fi citit"):
        parse_model_journal(str(path))
