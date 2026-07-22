"""Suggested corrected D300 values per category, or per real D300 line.
Informative only."""
from etva.d300 import D300_LINES


def _line_sort_key(line_no: str):
    return tuple(int(p) for p in line_no.replace("+", ".").split("."))


def suggest_d300_lines(result) -> list:
    flagged = {d["line_no"] for d in result.differences}
    lines = sorted(set(result.totals_company) | set(result.totals_anaf),
                   key=_line_sort_key)
    out = []
    for line_no in lines:
        c = result.totals_company.get(line_no, {"base": 0.0, "vat": 0.0})
        a = result.totals_anaf.get(line_no, {"base": 0.0, "vat": 0.0})
        dirty = line_no in flagged
        src = a if dirty else c
        out.append({"line_no": line_no, "label": D300_LINES.get(line_no, ""),
                    "company_base": c["base"], "company_vat": c["vat"],
                    "anaf_base": a["base"], "anaf_vat": a["vat"],
                    "suggested_base": src["base"],
                    "suggested_vat": src["vat"],
                    "status": "de_verificat" if dirty else "ok"})
    return out


def suggest_d300(result) -> list:
    flagged = {d["category"] for d in result.differences}
    cats = sorted(set(result.totals_company) | set(result.totals_anaf))
    out = []
    for cat in cats:
        c = result.totals_company.get(cat, {"base": 0.0, "vat": 0.0})
        a = result.totals_anaf.get(cat, {"base": 0.0, "vat": 0.0})
        dirty = cat in flagged
        src = a if dirty else c
        out.append({"category": cat,
                    "company_base": c["base"], "company_vat": c["vat"],
                    "anaf_base": a["base"], "anaf_vat": a["vat"],
                    "suggested_base": src["base"],
                    "suggested_vat": src["vat"],
                    "status": "de_verificat" if dirty else "ok"})
    return out
