"""Tests for the ANAF P300 PDF word-position parser, using synthetic word
data shaped like real pdfplumber output — no real PDF file needed, and
never the real confidential filings used to design this parser."""
import pytest
from etva.importer.anaf_p300 import parse_p300_rows, NotAnafP300


def _w(text, x0, top):
    return {"text": text, "x0": x0, "top": top}


def _row(*words):
    return sorted(words, key=lambda w: w["x0"])


def _header_row(top=184):
    return _row(_w("Denumire", 20, top), _w("indicatori", 66, top),
                _w("Valoare", 455, top), _w("TVA", 556, top))


def test_extracts_line_values_by_position():
    page = [
        _header_row(),
        _row(_w("9", 20, 61), _w("Livrari", 28, 61), _w("cota", 250, 61),
             _w("21%", 271, 61), _w("30.724", 458, 61), _w("6.452", 550, 61)),
    ]
    result = parse_p300_rows([page])
    assert result.lines == {"9": {"base": 30724.0, "vat": 6452.0}}


def test_wrapped_label_still_finds_values_within_block():
    page = [
        _header_row(),
        _row(_w("12.1", 20, 216), _w("Achizitii", 34, 216),
             _w("de", 71, 216)),
        _row(_w("bunuri", 20, 228), _w("supuse", 51, 228),
             _w("615", 472, 228), _w("129", 558, 228)),
    ]
    result = parse_p300_rows([page])
    assert result.lines == {"12.1": {"base": 615.0, "vat": 129.0}}


def test_blank_line_produces_no_entry():
    page = [
        _header_row(),
        _row(_w("3", 20, 310), _w("Livrari", 28, 310), _w("afara", 60, 310)),
        _row(_w("Surse", 20, 356), _w("de", 49, 356), _w("date", 63, 356)),
    ]
    result = parse_p300_rows([page])
    assert result.lines == {}


def test_extracts_header_metadata():
    page = [
        _w("Cod", 23, 119), _w("de", 44, 119), _w("identificare", 58, 119),
        _w("fiscala:", 109, 119), _w("RO", 143, 119), _w("44904111", 161, 119),
    ]
    page = [_row(*page),
            _row(_w("Denumire", 23, 131), _w(":", 69, 131), _w("EXEMPLU", 75, 131),
                 _w("TEST", 130, 131), _w("S.R.L.", 180, 131),
                 _w("Domiciliu", 230, 131)),
            _row(_w("Perioada", 220, 57), _w("de", 262, 57), _w("raportare", 276, 57),
                 _w("Luna", 320, 57), _w("5", 345, 57), _w("An", 353, 57),
                 _w("2026", 368, 57)),
            _header_row(top=184)]
    result = parse_p300_rows([page])
    assert result.company_cui == "RO44904111"
    assert result.company_name == "EXEMPLU TEST S.R.L."
    assert result.period == "2026-05"


def test_no_valoare_tva_header_raises():
    page = [_row(_w("ceva", 20, 10), _w("altceva", 60, 10))]
    with pytest.raises(NotAnafP300):
        parse_p300_rows([page])


def test_values_split_across_two_pages():
    page1 = [_header_row()]
    page2 = [
        _header_row(top=10),
        _row(_w("24", 20, 60), _w("Achizitii", 34, 60),
             _w("14.323", 458, 60), _w("3.008", 550, 60)),
    ]
    result = parse_p300_rows([page1, page2])
    assert result.lines == {"24": {"base": 14323.0, "vat": 3008.0}}
