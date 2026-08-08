import pytest

from etva.d300 import (suggest_line, classify_legend, with_mirrored_lines,
                       with_parent_rollups, expand_derived_lines,
                       valid_lines_for_direction, validate_overrides,
                       MappingSectionError, TOTAL_LINES,
                       resolve_codes, resolve_invoice_lines)


def test_confident_rate_mappings():
    assert suggest_line("vanzari", "Bunuri/servicii taxabile cu cota 21%") == "9"
    assert suggest_line("vanzari", "Bunuri/servicii taxabile cu cota 11%") == "10"
    assert suggest_line("cumparari", "Achizitii ... taxabile cu cota 21%") == "24"
    assert suggest_line("cumparari", "Achizitii ... taxabile cu cota 11%") == "25"
    assert suggest_line("cumparari", "Achizitii bunuri scutite, neimpozabile") == "29"


def test_legal_article_references_are_unambiguous():
    assert suggest_line("cumparari", "obligat la plata TVA art. 307") == "22.1"
    assert suggest_line("cumparari", "obligat la plata TVA art. 331 cu cota 21%") == "26.1"
    assert suggest_line("cumparari", "obligat la plata TVA art. 331 cu cota 11%") == "26.2"


def test_ambiguous_labels_are_left_unmapped():
    # A vague "taxare inversa" sale could be a domestic reverse-charge sale
    # (line 13) or a cross-border service export (line 3) — must not guess.
    assert suggest_line("vanzari", "Bunuri/servicii cu taxare inversa") is None
    # Cash-accounting VAT not yet due this period.
    assert suggest_line("cumparari", "cu TVA la plata cu cota 21%") is None
    assert suggest_line("cumparari", "AIC neimpozabile") is None


def test_classify_legend_sums_multiple_codes_onto_one_line():
    legend = {
        "2-3": {"label": "cota 21%", "base": 100.0, "vat": 21.0},
        "aux": {"label": "cota 21%", "base": 50.0, "vat": 10.5},
        "10": {"label": "Bunuri/servicii cu taxare inversa", "base": 5.0, "vat": 0.0},
    }
    mapped, unmapped = classify_legend("vanzari", legend)
    assert mapped["9"] == {"base": 150.0, "vat": 31.5}
    assert len(unmapped) == 1 and unmapped[0]["cod"] == "10"
    assert unmapped[0]["direction"] == "vanzari"


def test_classify_legend_override():
    legend = {"weird": {"label": "ceva neclar", "base": 10.0, "vat": 2.0}}
    mapped, unmapped = classify_legend("cumparari", legend, {"weird": "29"})
    assert mapped == {"29": {"base": 10.0, "vat": 2.0}}
    assert unmapped == []


def test_valid_lines_for_direction_excludes_totals_and_opposite_section():
    vanzari = valid_lines_for_direction("vanzari")
    cumparari = valid_lines_for_direction("cumparari")
    assert not (vanzari.keys() & TOTAL_LINES)
    assert not (cumparari.keys() & TOTAL_LINES)
    assert "9" in vanzari and "14+15" in vanzari
    assert "20" not in vanzari and "24" not in vanzari
    assert "24" in cumparari and "29" in cumparari
    assert "9" not in cumparari


def test_validate_overrides_flags_cross_section_and_unknown_line():
    errors = validate_overrides("cumparari", {"14": "14+15", "24": "24"})
    assert len(errors) == 1
    assert "14" in errors[0] and "14+15" in errors[0]
    errors = validate_overrides("vanzari", {"x": "99"})
    assert "necunoscuta" in errors[0]


def test_classify_legend_raises_on_cross_section_override():
    # Bug real reprodus: un cod din jurnalul de cumparari ("14", "AIC
    # neimpozabile") mapat pe o linie exclusiv de vanzari ("14+15") -
    # trebuie respins, nu acceptat tacit in totalul companiei.
    legend = {"14": {"label": "AIC neimpozabile", "base": 662.0, "vat": 0.0}}
    with pytest.raises(MappingSectionError) as exc:
        classify_legend("cumparari", legend, {"14": "14+15"})
    assert "14+15" in str(exc.value)


def test_classify_legend_ignores_override_for_absent_cod():
    # overrides poate fi un dict plat, partajat intre fisierul de vanzari
    # si cel de cumparari din aceeasi cerere - o intrare destinata
    # celuilalt fisier nu trebuie nici validata, nici aplicata aici.
    legend = {"9x": {"label": "cota 21%", "base": 10.0, "vat": 2.1}}
    mapped, unmapped = classify_legend(
        "vanzari", legend, {"14": "14+15"})  # "14" nu exista in acest legend
    assert mapped == {"9": {"base": 10.0, "vat": 2.1}}
    assert unmapped == []


def test_reverse_charge_mirrors_onto_collected_side():
    lines = {"26.1": {"base": 615.0, "vat": 129.15}}
    mirrored = with_mirrored_lines(lines)
    assert mirrored["12.1"] == {"base": 615.0, "vat": 129.15}
    assert mirrored["26.1"] == {"base": 615.0, "vat": 129.15}


def test_parent_line_rolls_up_from_children():
    lines = {"26.1": {"base": 615.0, "vat": 129.15}}
    rolled = with_parent_rollups(lines)
    assert rolled["26"] == {"base": 615.0, "vat": 129.15}


def test_expand_derived_lines_does_both():
    lines = {"26.1": {"base": 615.0, "vat": 129.15}}
    out = expand_derived_lines(lines)
    assert set(out) == {"26.1", "12.1", "26", "12"}
    assert out["12"] == {"base": 615.0, "vat": 129.15}


def test_resolve_codes_matches_classify_legend_mapped_codes():
    legend = {
        "2-3": {"label": "cota 21%", "base": 100.0, "vat": 21.0},
        "10": {"label": "Bunuri/servicii cu taxare inversa", "base": 5.0, "vat": 0.0},
    }
    resolved = resolve_codes("vanzari", legend)
    assert resolved == {"2-3": "9"}  # "10" ramane neclasificat, absent din rezultat


def test_resolve_codes_applies_overrides_same_as_classify_legend():
    legend = {"weird": {"label": "ceva neclar", "base": 10.0, "vat": 2.0}}
    assert resolve_codes("cumparari", legend, {"weird": "29"}) == {"weird": "29"}


def test_resolve_codes_raises_on_cross_section_override():
    legend = {"14": {"label": "AIC neimpozabile", "base": 662.0, "vat": 0.0}}
    with pytest.raises(MappingSectionError):
        resolve_codes("cumparari", legend, {"14": "14+15"})


def test_resolve_invoice_lines_direct_line_is_unchanged():
    assert resolve_invoice_lines("14+15") == ["14+15"]
    assert resolve_invoice_lines("24") == ["24"]


def test_resolve_invoice_lines_simple_parent():
    assert resolve_invoice_lines("7") == ["22", "22.1", "7", "7.1"]


def test_resolve_invoice_lines_double_derived_parent_and_mirror():
    # "5" e simultan parinte al lui "5.1" SI oglinda lui "20" - iar "5.1"
    # e la randul lui oglinda lui "20.1" - o factura reala e etichetata
    # doar "20.1", niciodata "5" sau "5.1" direct.
    assert resolve_invoice_lines("5") == ["20", "20.1", "5", "5.1"]
    assert resolve_invoice_lines("12") == ["12", "12.1", "12.2", "26", "26.1", "26.2"]
