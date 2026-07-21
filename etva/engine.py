"""Reconciliation engine: invoice-level matching + category totals."""
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class ReconcileResult:
    totals_company: dict = field(default_factory=dict)
    totals_anaf: dict = field(default_factory=dict)
    differences: list = field(default_factory=list)


def _totals(rows) -> dict:
    out = defaultdict(lambda: {"base": 0.0, "vat": 0.0})
    for r in rows:
        out[r["category"]]["base"] += r["base"]
        out[r["category"]]["vat"] += r["vat"]
    return {k: {"base": round(v["base"], 2), "vat": round(v["vat"], 2)}
            for k, v in out.items()}


def _group(rows) -> dict:
    grouped = {}
    for r in rows:
        key = (r["partner_cui"], r["invoice_no"])
        g = grouped.setdefault(key, {"base": 0.0, "vat": 0.0, "count": 0,
                                     "category": r["category"]})
        g["base"] += r["base"]
        g["vat"] += r["vat"]
        g["count"] += 1
    return grouped


def reconcile(company_rows, anaf_rows, tolerance: float = 1.0) -> ReconcileResult:
    result = ReconcileResult(totals_company=_totals(company_rows),
                             totals_anaf=_totals(anaf_rows))
    comp, anaf = _group(company_rows), _group(anaf_rows)

    def diff(dtype, key, c, a):
        result.differences.append({
            "diff_type": dtype, "partner_cui": key[0], "invoice_no": key[1],
            "category": (c or a)["category"],
            "company": {"base": c["base"], "vat": c["vat"]} if c else None,
            "anaf": {"base": a["base"], "vat": a["vat"]} if a else None,
            "delta_base": round((c["base"] if c else 0) - (a["base"] if a else 0), 2),
            "delta_vat": round((c["vat"] if c else 0) - (a["vat"] if a else 0), 2),
        })

    for key, g in comp.items():
        if g["count"] > 1:
            diff("duplicat", key, g, anaf.get(key))
    for key, g in anaf.items():
        if g["count"] > 1 and comp.get(key, {}).get("count", 0) <= 1:
            diff("duplicat", key, comp.get(key), g)

    for key, c in comp.items():
        a = anaf.get(key)
        if a is None:
            diff("lipsa_in_anaf", key, c, None)
        elif (abs(c["base"] - a["base"]) > tolerance
              or abs(c["vat"] - a["vat"]) > tolerance):
            diff("suma_diferita", key, c, a)
    for key, a in anaf.items():
        if key not in comp:
            diff("lipsa_la_companie", key, None, a)
    return result
