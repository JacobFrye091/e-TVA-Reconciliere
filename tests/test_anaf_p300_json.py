import json

import pytest

from etva.importer.anaf_p300_json import (
    NotAnafP300Json, parse_p300_json, parse_p300_json_data)


def _sample(**overrides):
    data = {
        "CIF": "111", "AN": 2026, "LUNA": 6,
        "RD9_VAL": 1000.0, "RD9_TVA": 210.0,
        # source-breakdown fields for the same line 9 total - must be ignored
        "RD9_TVA_AMEF": 210.0, "RD9_VAL_EFCT": 1000.0,
        "RD12_1_VAL": 500.0, "RD12_1_TVA": 105.0,
        "RD12_1_TVA_394": 105.0,
        # combined line 14+15
        "RD14_15_VAL": 50.0,
        # ambiguous concatenated-digit field - must NOT be parsed as a line
        "RD261_TVA": 999.0,
        "RD9_1_VAL": 42.0,  # not (yet) in D300_LINES - must be ignored, not guessed
    }
    data.update(overrides)
    return data


def test_parses_plain_and_sub_lines(tmp_path):
    doc = parse_p300_json_data(_sample())
    assert doc.company_cui == "RO111"
    assert doc.period == "2026-06"
    assert doc.lines["9"] == {"base": 1000.0, "vat": 210.0}
    assert doc.lines["12.1"] == {"base": 500.0, "vat": 105.0}
    assert doc.lines["14+15"] == {"base": 50.0, "vat": 0.0}


def test_ignores_source_breakdown_suffixes(tmp_path):
    doc = parse_p300_json_data(_sample())
    # only the plain RD9_VAL/RD9_TVA pair should have contributed to line 9
    assert doc.lines["9"]["vat"] == 210.0


def test_ignores_unknown_and_ambiguous_fields(tmp_path):
    doc = parse_p300_json_data(_sample())
    assert "261" not in doc.lines
    assert "26.1" not in doc.lines  # RD261_TVA must not be guessed as 26.1
    assert "9.1" not in doc.lines   # not yet a known D300 line


def test_cui_already_prefixed_ro_is_not_doubled():
    doc = parse_p300_json_data(_sample(CIF="RO111"))
    assert doc.company_cui == "RO111"


def test_missing_identification_fields_raises():
    with pytest.raises(NotAnafP300Json):
        parse_p300_json_data({"RD9_VAL": 100})


def test_parse_p300_json_reads_a_real_file(tmp_path):
    path = tmp_path / "decont.json"
    path.write_text(json.dumps(_sample()), encoding="utf-8")
    doc = parse_p300_json(str(path))
    assert doc.lines["9"] == {"base": 1000.0, "vat": 210.0}
