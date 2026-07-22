"""Tests for the SAGA journal parser, using a synthetic workbook shaped
like a real "Jurnal pentru vanzari/cumparari" export — fictitious company
and invoice data only."""
import pandas as pd
import pytest
from etva.importer.saga import parse_saga_journal, NotSagaFormat


def _write_vanzari(path):
    rows = [
        ["Exemplu Test SRL  c.f. RO12345678  r.c. J40/1/2026", None, None, None, None, None, None, None, None, None, None],
        [None] * 11,
        [None] * 11,
        [None] * 11,
        [None, None, "JURNAL PENTRU VANZARI", None, None, None, None, None, None, None, None],
        [None, None, None, None, "2026-05-01", "--", "2026-05-31", None, None, None, None],
        [None] * 11,
        ["Nr. crt.", "Document", None, "Client/beneficiar", None, None, None,
         "Total document (inclusiv TVA)", "Baza  impozitare", "Valoare T.V.A.", "Referinta cod *)"],
        [None, "Data", "Numar", None, "Denumire", "Cod fiscal", None, None, None, None, None],
        [1, "2026-05-01", "F0001", "Client Unu SRL", None, "RO11111111", None, 1210, 1000, 210, "2-3"],
        [2, "2026-05-05", "F0002", "Client Doi SRL", None, "RO22222222", None, 605, 500, 105, "2-3"],
        [None] * 11,
        [3, "2026-05-10", "EXP-0001", "Foreign Client Ltd", None, "IE1234567X", None, 100, 100, 0, "10"],
        [None, "Intocmit", None, "Verificat", None, None, "Total", 1915, 1600, 315, None],
        [None] * 11,
        ["Referinta cod *)", None, None, None, None, None, None, "Total document (inclusiv TVA)", None, "Baza  impozitare", "Valoare T.V.A."],
        [None, None, None, None, "Referinta", None, None, None, None, None, None],
        [None] * 11,
        ["2-3", "Bunuri/servicii taxabile cu cota 21%", None, None, None, None, None, 1815, 1500, 315, None],
        ["10", "Bunuri/servicii cu taxare inversa", None, None, None, None, None, 100, 100, 0, None],
        [None] * 11,
        ["Pagina 1/1  SAGA C", None, None, None, None, None, None, None, None, None, None],
    ]
    pd.DataFrame(rows).to_excel(path, header=False, index=False, engine="openpyxl")


def _write_cumparari(path):
    rows = [
        ["Exemplu Test SRL  c.f. RO12345678  r.c. J40/1/2026", None, None, None, None, None, None, None, None, None, None, None, None, None, None],
        [None] * 15,
        [None] * 15,
        [None] * 15,
        [None, None, None, None, "JURNAL PENTRU CUMPARARI", None, None, None, None, None, None, None, None, None, None],
        [None, None, None, None, None, None, "2026-05-01", "--", "2026-05-31", None, None, None, None, None, None],
        [None] * 15,
        ["Nr. crt.", "Document", None, "Furnizor/prestator", None, None,
         "Total document (inclusiv TVA)", "Baza  impozitare", "Valoare T.V.A.", "Total platit",
         "Operatiuni exigibile", None, "Operatiuni neexigibile", None, "Referinta cod *)"],
        [None, "Data", "Numar", "Denumire", "Cod fiscal", None, None, None, None,
         "(inclusiv TVA)", "Baza imp.", "T.V.A.", "Baza imp.", "T.V.A.", None],
        [1, "2026-05-02", "FZ-100", "Furnizor Unu SRL", "RO33333333", None,
         121, 100, 21, 0, 0, 0, 0, 0, "4-5"],
        [None] * 15,
        [2, "2026-05-03", "ART307-1", "EU Services Ltd", "IE9999999X", None,
         12.1, 10, 2.1, 0, 0, 0, 0, 0, "15-16"],
        [None, "Intocmit", None, "Verificat", None, "Total", 133.1, 110, 23.1, 0, 0, 0, 0, 0, None],
        [None] * 15,
        ["Referinta cod *)", None, None, None, None, None,
         "Total (inclusiv TVA)", "Baza  impozitare", None, "Total platit",
         "Operatiuni exigibile", None, "Operatiuni neexigibile", None, None],
        [None, None, None, None, "Referinta", None, None, None, "Valoare TVA", None, None, None, None, None, None],
        [None, None, None, None, None, None, None, None, None, "(inclusiv TVA)", "Baza imp.", "T.V.A.", "Baza imp.", "T.V.A.", None],
        ["4-5", "Achizitii de bunuri/servicii din tara taxabile cu cota 11%", None, None, None, None,
         121, 100, 21, 0, 0, 0, 0, 0, None],
        ["15-16", "Cumparari pt.care cumparatorul este obligat la plata TVA art. 307", None, None, None, None,
         12.1, 10, 2.1, 0, 0, 0, 0, 0, None],
    ]
    pd.DataFrame(rows).to_excel(path, header=False, index=False, engine="openpyxl")


def test_parses_vanzari_entries_and_legend(tmp_path):
    path = tmp_path / "vanzari.xlsx"
    _write_vanzari(path)
    j = parse_saga_journal(str(path))
    assert j.direction == "vanzari"
    assert j.company_cui == "RO12345678"
    assert len(j.entries) == 3
    assert j.entries[0]["partner_cui"] == "RO11111111"
    assert j.entries[0]["base"] == 1000
    assert j.entries[0]["vat"] == 210
    assert j.entries[0]["cod"] == "2-3"
    assert j.legend["2-3"] == {"label": "Bunuri/servicii taxabile cu cota 21%",
                               "base": 1500, "vat": 315}
    assert j.legend["10"]["base"] == 100


def test_parses_cumparari_entries_and_legend(tmp_path):
    path = tmp_path / "cumparari.xlsx"
    _write_cumparari(path)
    j = parse_saga_journal(str(path))
    assert j.direction == "cumparari"
    assert len(j.entries) == 2
    assert j.entries[1]["cod"] == "15-16"
    assert j.entries[1]["base"] == 10
    assert j.entries[1]["vat"] == 2.1
    assert j.legend["4-5"]["base"] == 100
    assert j.legend["15-16"]["label"].endswith("art. 307")


def test_non_saga_file_raises(tmp_path):
    path = tmp_path / "plain.xlsx"
    pd.DataFrame({"cui_partener": ["RO1"], "nr_factura": ["F1"]}).to_excel(
        path, index=False, engine="openpyxl")
    with pytest.raises(NotSagaFormat):
        parse_saga_journal(str(path))
