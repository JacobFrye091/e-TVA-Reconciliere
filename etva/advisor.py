"""Suggested corrected D300 values per category. Informative only."""


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
