"""Reconciliation engine: invoice-level matching + category totals, and
D300-line-level matching against ANAF's real precompleted e-TVA totals
(which carry no invoice detail at all — see etva/importer/anaf_p300.py)."""
from dataclasses import dataclass, field
from collections import defaultdict
from itertools import combinations
from math import comb

from etva.d300 import D300_LINES


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


def reconcile_d300(company_lines: dict, anaf_lines: dict,
                   tolerance: float = 1.0) -> ReconcileResult:
    """Compare company totals per D300 line against ANAF's precompleted
    lines. `company_lines`/`anaf_lines` are {line_no: {"base", "vat"}}."""
    totals_company = {k: {"base": round(v["base"], 2), "vat": round(v["vat"], 2)}
                      for k, v in company_lines.items()}
    totals_anaf = {k: {"base": round(v["base"], 2), "vat": round(v["vat"], 2)}
                   for k, v in anaf_lines.items()}
    result = ReconcileResult(totals_company=totals_company, totals_anaf=totals_anaf)

    def diff(dtype, line_no, c, a):
        result.differences.append({
            "diff_type": dtype, "line_no": line_no,
            "label": D300_LINES.get(line_no, ""),
            "company": c, "anaf": a,
            "delta_base": round((c["base"] if c else 0) - (a["base"] if a else 0), 2),
            "delta_vat": round((c["vat"] if c else 0) - (a["vat"] if a else 0), 2),
        })

    for line_no, c in totals_company.items():
        a = totals_anaf.get(line_no)
        if a is None:
            diff("lipsa_in_anaf", line_no, c, None)
        elif abs(c["base"] - a["base"]) > tolerance or abs(c["vat"] - a["vat"]) > tolerance:
            diff("suma_diferita", line_no, c, a)
    for line_no, a in totals_anaf.items():
        if line_no not in totals_company:
            diff("lipsa_la_companie", line_no, None, a)
    return result


# Marimea maxima a subsetului cautat. Dincolo de atat, la numarul tipic de
# facturi pe o linie (10-30), potrivirile intamplatoare devin suficient de
# frecvente incat o evidentiere "cu incredere" ar induce in eroare mai des
# decat ar ajuta - fallback-ul onest existent ("poate fi vorba de mai multe
# facturi mici adunate") ramane raspunsul corect peste acest prag.
_CANDIDATE_MAX_SIZE = 4

# Numarul maxim de combinatii C(n, k) incercate la un nivel k, ales sa
# reproduca exact plafonul vechi n<=300 pentru perechi: C(300, 2) = 44_850,
# C(301, 2) = 45_150.
_CANDIDATE_COMBO_BUDGET = 45_000


def find_candidate_invoices(rows: list, delta_base: float, delta_vat: float,
                            tolerance: float = 0.05) -> set:
    """Indices into `rows` (each with "base"/"vat") whose sum most likely
    explains a line-level difference - the smallest subset (a single
    invoice, then a pair, then a triple... up to _CANDIDATE_MAX_SIZE)
    matched tightly on BOTH base and vat (not base alone) to avoid
    flagging innocent invoices by coincidence: on real data, a company's
    invoices can land within a leu or two of the delta purely by chance
    while having nothing to do with the actual cause (e.g. ANAF's
    e-Factura source simply not covering several unrelated invoices) -
    requiring both values to agree filters that out.

    Only searches when delta_base > 0 (the company's total exceeds
    ANAF's, i.e. extra/duplicate/misclassified invoices could plausibly
    be among these) - for delta_base <= 0 (ANAF has more than the
    company) the cause isn't among the company's own listed invoices at
    all, so nothing is searched.

    Searches increasing subset sizes and stops at the first size with a
    match: a single invoice is a more plausible, more actionable
    explanation than a triple that happens to sum the same, so a smaller
    match always wins over a larger one regardless of what larger sizes
    might also contain. Sizes beyond _CANDIDATE_MAX_SIZE are never
    tried - past that size, coincidental matches at typical per-line
    invoice counts become common enough to be untrustworthy (see the
    module's test suite / design notes for the measured rates), so the
    wider fallback message a caller shows on an empty result is more
    honest than a confident-looking highlight of many rows.

    Within the winning size, more than one DISTINCT set of amounts
    matching the delta is genuine ambiguity - there's no basis to prefer
    one over another, so nothing is flagged (same philosophy as a
    near-but-not-quite match never being flagged). The exception is
    interchangeable invoices, e.g. three identical 500-lei invoices where
    any two explain a 1000-lei delta: those aren't different
    explanations, so the first one found (in the same order `rows`
    arrived in) is returned instead of discarding a genuine match over a
    coincidence of identical amounts.

    Rows whose base and vat are both ~0 are excluded before searching -
    otherwise such a row could tag onto a match to fabricate a second,
    differently-valued "match" purely by coincidence and trip the
    ambiguity rule above for no real reason.

    Search cost is bounded by a combination budget rather than a raw row
    count: size k is only tried while C(n, k) <= _CANDIDATE_COMBO_BUDGET,
    where n excludes the zero rows above. Once a size is too expensive,
    larger sizes are skipped too (C(n, k) only grows from there for
    k <= n/2) and whatever was already found - possibly nothing - is
    returned silently, the same way a very long line silently fell back
    to single-only matching before this function searched beyond pairs.

    An empty result means "no confident match found", not "no invoices
    exist" - callers should say so explicitly rather than falling back to
    a looser, less trustworthy guess.
    """
    if delta_base <= 0:
        return set()
    idx = [i for i, r in enumerate(rows)
           if abs(r["base"]) > tolerance or abs(r["vat"]) > tolerance]
    n = len(idx)
    for k in range(1, _CANDIDATE_MAX_SIZE + 1):
        if k > n or comb(n, k) > _CANDIDATE_COMBO_BUDGET:
            break
        best, best_sig = None, None
        for combo in combinations(idx, k):
            base_sum = sum(rows[i]["base"] for i in combo)
            if abs(base_sum - delta_base) > tolerance:
                continue
            vat_sum = sum(rows[i]["vat"] for i in combo)
            if abs(vat_sum - delta_vat) > tolerance:
                continue
            sig = tuple(sorted((rows[i]["base"], rows[i]["vat"]) for i in combo))
            if best is None:
                best, best_sig = combo, sig
            elif sig != best_sig:
                return set()
        if best is not None:
            return set(best)
    return set()
