"""Parser for the real ANAF "Decont precompletat RO e-TVA" PDF.

Unlike an invoice-level export, this document has no per-invoice detail at
all — it's ANAF's own precompleted D300 declaration, with one Valoare/TVA
pair per declaration line (9, 24, 25, ...), sourced from e-Factura / e-Casa
de marcat / SAF-T etc. Reconciliation against it can only happen at that
same line-level granularity.

Numbers are extracted by word *position*: each line's "Valoare" and "TVA"
figures sit in fixed columns, anchored here to the x-coordinate of the
"Valoare"/"TVA" column headers on that page rather than a hardcoded offset,
so small layout shifts between ANAF PDF versions don't silently break it.
"""
from dataclasses import dataclass, field
import re
import pdfplumber

_LINE_RE = re.compile(r"^\d{1,2}(\.\d{1,2})?$|^\d{1,2}\+\d{1,2}$")
_NUM_RE = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$")
_LEFT_MARGIN = 30


class NotAnafP300(Exception):
    pass


@dataclass
class AnafP300:
    company_cui: str | None
    company_name: str | None
    period: str | None
    lines: dict = field(default_factory=dict)  # line_no -> {base, vat}


def _to_number(text: str) -> float:
    if "," in text:
        return float(text.replace(".", "").replace(",", "."))
    return float(text.replace(".", ""))


def _group_lines(words):
    """Group a page's words into visual lines by rounded 'top', each
    sorted left to right — mirrors how the form's rows are laid out."""
    lines = {}
    for w in words:
        key = round(w["top"])
        lines.setdefault(key, []).append(w)
    return [sorted(lines[top], key=lambda w: w["x0"]) for top in sorted(lines)]


def _find_columns(rows):
    for row in rows:
        by_text = {w["text"].rstrip(":"): w["x0"] for w in row}
        if "Valoare" in by_text and "TVA" in by_text:
            return by_text["Valoare"], by_text["TVA"]
    return None


def parse_p300_pdf(path: str) -> AnafP300:
    with pdfplumber.open(path) as pdf:
        pages_rows = [_group_lines(page.extract_words()) for page in pdf.pages]
    return parse_p300_rows(pages_rows)


def parse_p300_rows(pages_rows: list) -> AnafP300:
    """Core parsing logic, decoupled from the PDF library so it can be
    exercised with synthetic word data in tests. `pages_rows` is one list
    of rows per page (each row a list of {"text","x0","top"} dicts sorted
    by x0) — exactly what `_group_lines` produces for a real page."""
    valoare_x = tva_x = None
    for rows in pages_rows:
        cols = _find_columns(rows)
        if cols:
            valoare_x, tva_x = cols

    if valoare_x is None:
        raise NotAnafP300(
            "Nu s-a gasit coloana Valoare/TVA — nu pare un decont RO e-TVA.")

    company_cui = company_name = period = None
    lines_out: dict = {}

    for rows in pages_rows:
        flat_text = " ".join(w["text"] for row in rows for w in row)
        if company_cui is None:
            m = re.search(r"identificare fiscal\S*:?\s*RO\s*(\d{2,10})", flat_text)
            if m:
                company_cui = f"RO{m.group(1)}"
        if company_name is None:
            m = re.search(r"Denumire\s*:\s*(.+?)\s+Domiciliu", flat_text)
            if m:
                company_name = m.group(1).strip()
        if period is None:
            m = re.search(r"Luna\s+(\d{1,2})\s+An\s+(\d{4})", flat_text)
            if m:
                period = f"{int(m.group(2))}-{int(m.group(1)):02d}"

        blocks = []
        for row in rows:
            first = row[0]
            if first["x0"] < _LEFT_MARGIN and _LINE_RE.match(first["text"]):
                blocks.append([row])
            elif blocks:
                blocks[-1].append(row)

        for block in blocks:
            line_no = block[0][0]["text"]
            base = vat = None
            for row in block:
                for w in row:
                    if not _NUM_RE.match(w["text"]):
                        continue
                    d_base = abs(w["x0"] - valoare_x)
                    d_vat = abs(w["x0"] - tva_x)
                    if d_base <= 60 and d_base < d_vat and base is None:
                        base = _to_number(w["text"])
                    elif d_vat <= 60 and d_vat <= d_base and vat is None:
                        vat = _to_number(w["text"])
            if base is not None or vat is not None:
                lines_out[line_no] = {"base": base or 0.0, "vat": vat or 0.0}

    return AnafP300(company_cui=company_cui, company_name=company_name,
                    period=period, lines=lines_out)
