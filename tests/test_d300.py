from etva.d300 import (suggest_line, classify_legend, with_mirrored_lines,
                       with_parent_rollups, expand_derived_lines)


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


def test_classify_legend_override():
    legend = {"weird": {"label": "ceva neclar", "base": 10.0, "vat": 2.0}}
    mapped, unmapped = classify_legend("cumparari", legend, {"weird": "29"})
    assert mapped == {"29": {"base": 10.0, "vat": 2.0}}
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
