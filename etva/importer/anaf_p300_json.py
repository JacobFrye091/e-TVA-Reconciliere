"""Parse the JSON response of ANAF's "decont precompletat" web service.

Discovered from official ANAF documentation (mfinante): a GET call to
either https://webserviceapl.anaf.ro/decont/ws/v1/info (digital
certificate) or https://api.anaf.ro/decont/ws/v1/info (OAuth2) with
cui/an/luna returns a zip with two JSON files, one of them the
precompleted declaration itself, using field names like RD9_VAL/RD9_TVA
for each D300 line's precompleted base/VAT total.

This module only parses that JSON shape — it does not call the API
(that requires per-firm certificate or OAuth2 credentials, a separate,
bigger integration decision). If an accountant obtains the JSON some
other way (e.g. calling the API themselves), this lets it be uploaded
here just like the PDF/xlsx/csv formats already are.

Field names beyond the plain RD{line}_VAL/RD{line}_TVA pair (e.g.
RD10_TVA_394, RD24_TVA_EFCT_I, RD10_1_TVA_AMEF) are per-source-system
breakdowns of the same precompleted total, not separate totals — same
as the PDF's own "Surse de date" sub-rows, they're ignored here.

The official spec also uses a concatenated-digit line numbering for
some sub-lines (e.g. RD101_TVA_REGAC, RD261_TVA) that is inconsistent
in practice (RD273_TVA is documented as line 26.3, not 27.3) — those
are deliberately NOT parsed rather than guessed. Anything that looks
like a RD{n}[_{m}]_(VAL|TVA) field but isn't a line already known to
etva.d300.D300_LINES is left out rather than invented.
"""
import json
import re

from etva.d300 import D300_LINES
from etva.importer.anaf_p300 import AnafP300

_FIELD_RE = re.compile(r"^RD(\d+)(?:_(\d+))?_(VAL|TVA)$")
_COMBINED_LINE = {("14", "15"): "14+15"}


class NotAnafP300Json(Exception):
    pass


def _line_no(g1: str, g2: str | None) -> str:
    if g2 is not None:
        combined = _COMBINED_LINE.get((g1, g2))
        if combined:
            return combined
        return f"{g1}.{g2}"
    return g1


def parse_p300_json(path: str) -> AnafP300:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return parse_p300_json_data(data)


def parse_p300_json_data(data: dict) -> AnafP300:
    """Pure parsing of an already-decoded JSON object (easy to unit test)."""
    cui = data.get("CIF")
    an = data.get("AN")
    luna = data.get("LUNA")
    if cui is None or an is None or luna is None:
        raise NotAnafP300Json(
            "Fisierul JSON nu are structura unui decont precompletat ANAF "
            "(lipsesc campurile CIF/AN/LUNA).")

    lines: dict = {}
    for key, value in data.items():
        if value in (None, ""):
            continue
        m = _FIELD_RE.match(key)
        if not m:
            continue
        g1, g2, kind = m.groups()
        line_no = _line_no(g1, g2)
        if line_no not in D300_LINES:
            continue
        acc = lines.setdefault(line_no, {"base": 0.0, "vat": 0.0})
        acc["vat" if kind == "TVA" else "base"] = float(value)

    cui_str = str(cui).strip()
    company_cui = cui_str if cui_str.upper().startswith("RO") else f"RO{cui_str}"
    period = f"{int(an):04d}-{int(luna):02d}"
    return AnafP300(company_cui=company_cui, company_name=None,
                    period=period, lines=lines)
