"""Catalog of D300 declaration lines used by ANAF's precompleted e-TVA
document, and a conservative classifier that maps a company journal's own
VAT-code legend (e.g. SAGA's "Referinta cod") onto those line numbers.

The classifier only returns a line number when the mapping is legally
unambiguous (a specific Fiscal Code article, or a plain domestic VAT rate).
Everything else returns None so the accountant confirms it manually — this
mirrors the rest of the app's "suggest, never decide" approach, and is
deliberate: several genuinely different operations (an EU B2B service export
vs. a domestic reverse-charge sale, for instance) can share the same vague
label ("taxare inversa") in accounting software while landing on different
D300 lines.
"""
import re

D300_LINES = {
    "1": "Livrari intracomunitare de bunuri, scutite conform art. 294 alin.(2) lit.a) si d)",
    "2": "Regularizari livrari intracomunitare scutite conform art. 294 alin.(2) lit.a) si d)",
    "3": "Livrari/prestari cu locul in afara Romaniei si livrari intracomunitare scutite conform art. 294 alin.(2) lit.b) si c)",
    "3.1": "Prestari de servicii intracomunitare care nu beneficiaza de scutire in statul membru in care taxa e datorata",
    "4": "Regularizari privind prestarile de servicii intracomunitare de la rd. 3.1",
    "5": "Achizitii intracomunitare de bunuri, cumparatorul obligat la plata TVA (taxare inversa)",
    "5.1": "Achizitii intracomunitare, furnizorul inregistrat in scopuri de TVA in statul membru de livrare",
    "6": "Regularizari privind achizitiile intracomunitare de bunuri de la rd. 5",
    "7": "Achizitii de bunuri (altele decat rd.5-6) si servicii, beneficiarul obligat la plata TVA (taxare inversa)",
    "7.1": "Achizitii de servicii intracomunitare, beneficiarul obligat la plata TVA (taxare inversa)",
    "8": "Regularizari privind achizitiile de servicii intracomunitare de la rd. 7.1",
    "9": "Livrari de bunuri si prestari de servicii, taxabile cu cota 21%",
    "10": "Livrari de bunuri si prestari de servicii, taxabile cu cota 11%",
    "11": "Livrari de bunuri si prestari de servicii, taxabile cu cota 9%",
    "12": "Achizitii de bunuri si servicii supuse masurilor de simplificare, beneficiarul obligat la plata TVA (taxare inversa)",
    "12.1": "Achizitii de bunuri si servicii supuse masurilor de simplificare, cota 21%",
    "12.2": "Achizitii de bunuri supuse masurilor de simplificare, cota 11%",
    "13": "Livrari de bunuri si prestari de servicii supuse masurilor de simplificare (taxare inversa)",
    "14+15": "Livrari scutite cu/fara drept de deducere, altele decat cele de la rd. 1-3",
    "16": "Regularizari taxa colectata",
    "17": "Vanzari intracomunitare de bunuri la distanta si servicii TBE catre persoane neimpozabile",
    "18": "Regularizari privind vanzarile la distanta si serviciile TBE de la rd. 17",
    "19": "TOTAL TAXA COLECTATA",
    "20": "Achizitii intracomunitare de bunuri, cumparatorul obligat la plata TVA (deductibil)",
    "20.1": "Achizitii intracomunitare, furnizor inregistrat TVA in statul membru de livrare (deductibil)",
    "21": "Regularizari privind achizitiile intracomunitare de bunuri de la rd. 20",
    "22": "Achizitii de bunuri (altele decat rd.20-21) si servicii, beneficiarul obligat la plata TVA (deductibil)",
    "22.1": "Achizitii de servicii intracomunitare, beneficiarul obligat la plata TVA (deductibil)",
    "23": "Regularizari privind achizitiile de servicii intracomunitare de la rd. 22.1",
    "24": "Achizitii de bunuri si servicii taxabile cu cota de 21%, altele decat cele de la rd.27",
    "25": "Achizitii de bunuri si servicii, taxabile cu cota de 11%",
    "26": "Achizitii de bunuri si servicii supuse masurilor de simplificare, beneficiarul obligat la plata TVA (deductibil)",
    "26.1": "Achizitii de bunuri si servicii supuse masurilor de simplificare, cota 21% (deductibil)",
    "26.2": "Achizitii de bunuri supuse masurilor de simplificare, cota 11% (deductibil)",
    "27": "Compensatia in cota forfetara pentru achizitii de produse/servicii agricole",
    "28": "Regularizari privind compensatia in cota forfetara",
    "29": "Achizitii de bunuri si servicii scutite de taxa sau neimpozabile",
    "29.1": "Achizitii de servicii intracomunitare scutite de taxa",
    "30": "TOTAL TAXA DEDUCTIBILA",
    "31": "SUB-TOTAL TAXA DEDUSA conform art. 297/298 sau 300/298 si compensatie forfetara",
    "32": "TVA efectiv restituita cumparatorilor straini",
    "33": "Regularizari taxa dedusa",
    "34": "Ajustari conform pro-rata / ajustari de taxa",
    "35": "TOTAL TAXA DEDUSA",
}

# Lines that are computed totals/sub-totals rather than individual
# operations — never the target of an automatic per-code mapping.
TOTAL_LINES = {"19", "30", "31", "32", "33", "34", "35"}

# For reverse-charge operations, the buyer both "collects" (self-charges)
# and deducts the same VAT, so ANAF's own form mirrors the identical amount
# onto both sections (its footnotes say so explicitly, e.g. "Se inscriu
# aceleasi informatii precompletate la rd.12" under line 26). A purchases
# journal only ever records the deductible side, so the collected-side
# counterpart has to be synthesised to compare like with like.
MIRROR_TO_COLLECTED = {
    "20": "5", "20.1": "5.1", "21": "6",
    "22": "7", "22.1": "7.1", "23": "8",
    "26": "12", "26.1": "12.1", "26.2": "12.2",
}


def with_mirrored_lines(lines: dict) -> dict:
    out = dict(lines)
    for deductible, collected in MIRROR_TO_COLLECTED.items():
        if deductible in lines and collected not in out:
            out[collected] = dict(lines[deductible])
    return out


# A parent line's value is the sum of its own sub-lines (ANAF prefills both
# — e.g. line 26 always equals 26.1 + 26.2 in the real form). A journal
# only ever produces the sub-line, so the parent is derived here rather
# than compared as if it were an independent, possibly-missing figure.
PARENT_CHILDREN = {
    "5": ["5.1"], "7": ["7.1"], "12": ["12.1", "12.2"],
    "20": ["20.1"], "22": ["22.1"], "26": ["26.1", "26.2"],
}


def with_parent_rollups(lines: dict) -> dict:
    out = dict(lines)
    for parent, children in PARENT_CHILDREN.items():
        present = [c for c in children if c in lines]
        if present and parent not in out:
            out[parent] = {
                "base": round(sum(lines[c]["base"] for c in present), 2),
                "vat": round(sum(lines[c]["vat"] for c in present), 2),
            }
    return out


def expand_derived_lines(lines: dict) -> dict:
    """Apply both the reverse-charge mirror and the parent/sub-line rollup."""
    return with_parent_rollups(with_mirrored_lines(lines))


def _norm(text: str) -> str:
    text = text.lower()
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ş", "s"),
                 ("ț", "t"), ("ţ", "t")):
        text = text.replace(a, b)
    return text


def suggest_line(direction: str, label: str) -> str | None:
    """Best-effort D300 line for a company journal's own VAT-code label.

    `direction` is "vanzari" or "cumparari". Returns None when the label
    doesn't unambiguously identify a single line — callers should treat
    that as "needs manual mapping", not "no VAT impact".
    """
    t = _norm(label)
    has_rate = lambda pct: re.search(rf"cota\s*{pct}\s*%", t) is not None

    if "art. 307" in t or "art.307" in t:
        return "22.1"
    if "art. 331" in t or "art.331" in t:
        if has_rate(21):
            return "26.1"
        if has_rate(11):
            return "26.2"
        return "26"
    if "art. 294" in t or "art.294" in t:
        return "1"

    simplificare = "simplific" in t
    taxare_inversa = "taxare invers" in t or "tax. invers" in t
    scutit = "scutit" in t or "neimpozabil" in t
    intracomunitar = "intracomunitar" in t or t.strip() == "aic" or "aic " in t

    if direction == "vanzari":
        if simplificare and not intracomunitar:
            if has_rate(21):
                return "13"
            return None
        if scutit or intracomunitar or taxare_inversa:
            # Could be line 3 (export of services/goods, EU or non-EU) or a
            # genuine domestic reverse-charge sale — not safe to guess.
            return None
        if has_rate(21):
            return "9"
        if has_rate(11):
            return "10"
        if has_rate(9):
            return "11"
        return None

    if direction == "cumparari":
        if "la plata" in t or "neexigibil" in t or "incasare" in t:
            # Cash-accounting VAT not yet due — not comparable this period.
            return None
        if simplificare or (taxare_inversa and not intracomunitar):
            if has_rate(21):
                return "26.1"
            if has_rate(11):
                return "26.2"
            return "26"
        if intracomunitar and scutit:
            return None
        if scutit:
            return "29"
        if has_rate(21):
            return "24"
        if has_rate(11):
            return "25"
        return None

    return None


def classify_legend(direction: str, legend: dict, overrides: dict | None = None):
    """Group a journal's VAT-code legend onto D300 lines.

    `overrides` (cod -> line_no) lets the accountant correct or supply a
    mapping the automatic classifier left unmapped. Returns
    (mapped: {line_no: {base, vat}}, unmapped: [{cod, label, base, vat}]).
    """
    overrides = overrides or {}
    mapped: dict = {}
    unmapped: list = []
    for cod, info in legend.items():
        line_no = overrides.get(cod) or suggest_line(direction, info["label"])
        if line_no is None:
            unmapped.append({"cod": cod, "label": info["label"],
                             "base": info["base"], "vat": info["vat"]})
            continue
        acc = mapped.setdefault(line_no, {"base": 0.0, "vat": 0.0})
        acc["base"] += info["base"]
        acc["vat"] += info["vat"]
    return mapped, unmapped
