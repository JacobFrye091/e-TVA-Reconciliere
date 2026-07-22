"""Parser for the real "Jurnal pentru vanzari / cumparari" export produced by
SAGA accounting software: an invoice-level ledger followed by a legend that
groups those invoices under the software's own VAT codes ("Referinta cod"),
with SAGA's own declared base/VAT totals per code.

The header spans two rows and the exact column count differs between the
sales and purchases export (purchases has extra "operatiuni exigibile /
neexigibile" columns for cash-accounting VAT), so columns are located by
searching header text rather than by fixed position.
"""
from dataclasses import dataclass, field
import re
import pandas as pd


class NotSagaFormat(Exception):
    pass


@dataclass
class SagaJournal:
    direction: str  # "vanzari" | "cumparari"
    company_name: str | None
    company_cui: str | None
    entries: list = field(default_factory=list)   # per-invoice rows
    legend: dict = field(default_factory=dict)     # cod -> {label, base, vat}


def _norm(s) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ş", "s"),
                 ("ț", "t"), ("ţ", "t")):
        s = s.replace(a, b)
    return s


def _num(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _cell(df, r, c):
    if r >= len(df) or c is None or c >= df.shape[1]:
        return None
    v = df.iat[r, c]
    return None if (isinstance(v, float) and pd.isna(v)) else v


def _read_raw(path: str) -> pd.DataFrame:
    engine = "xlrd" if path.lower().endswith(".xls") else None
    return pd.read_excel(path, header=None, engine=engine)


def _find_header_row(df: pd.DataFrame) -> int:
    for r in range(min(20, len(df))):
        for c in range(df.shape[1]):
            if _norm(_cell(df, r, c)) == "nr. crt.":
                return r
    raise NotSagaFormat("Nu s-a gasit antetul 'Nr. crt.' — nu pare un jurnal SAGA.")


def _find_columns(df: pd.DataFrame, header_row: int) -> dict:
    sub_row = header_row + 1
    wanted = {
        "date": ("data",),
        "doc_no": ("numar",),
        "partner_name": ("denumire",),
        "partner_cui": ("cod fiscal",),
        "base": ("baza",),
        "vat": ("valoare t.v.a.",),
        "cod": ("referinta cod",),
    }
    found = {}
    for c in range(df.shape[1]):
        combined = _norm(_cell(df, header_row, c)) + " " + _norm(_cell(df, sub_row, c))
        for field_name, keywords in wanted.items():
            if field_name in found:
                continue
            if any(kw in combined for kw in keywords):
                found[field_name] = c
    missing = [f for f in ("date", "doc_no", "partner_cui", "base", "vat", "cod")
               if f not in found]
    if missing:
        raise NotSagaFormat(f"Coloane lipsa in jurnalul SAGA: {', '.join(missing)}")
    return found


def _detect_direction(df: pd.DataFrame) -> str:
    for r in range(min(10, len(df))):
        for c in range(df.shape[1]):
            t = _norm(_cell(df, r, c))
            if "jurnal pentru vanzari" in t:
                return "vanzari"
            if "jurnal pentru cumparari" in t:
                return "cumparari"
    raise NotSagaFormat("Nu s-a gasit titlul 'JURNAL PENTRU VANZARI/CUMPARARI'.")


def _company_identity(df: pd.DataFrame):
    text = " ".join(str(_cell(df, r, 0) or "") for r in range(min(3, len(df))))
    m = re.search(r"\bRO\s?(\d{2,10})\b", text)
    cui = f"RO{m.group(1)}" if m else None
    name = text.split("  ")[0].strip() or None
    return name, cui


def _date_str(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    return str(val).strip()[:10]


def parse_saga_journal(path: str) -> SagaJournal:
    df = _read_raw(path)
    direction = _detect_direction(df)
    company_name, company_cui = _company_identity(df)
    header_row = _find_header_row(df)
    cols = _find_columns(df, header_row)

    data_start = header_row + 2
    stop_row = len(df)
    for r in range(data_start, len(df)):
        if "intocmit" in _norm(_cell(df, r, 1)) or "intocmit" in _norm(_cell(df, r, 0)):
            stop_row = r
            break

    entries = []
    for r in range(data_start, stop_row):
        cui = _cell(df, r, cols["partner_cui"])
        doc_no = _cell(df, r, cols["doc_no"])
        base = _cell(df, r, cols["base"])
        vat = _cell(df, r, cols["vat"])
        cod = _cell(df, r, cols["cod"])
        if cui is None and base is None and vat is None and cod is None:
            continue  # blank separator row, or a stray document reference
            # with no financial data (e.g. a voided document number kept
            # only for numbering continuity)
        entries.append({
            "date": _date_str(_cell(df, r, cols["date"])),
            "doc_no": str(doc_no or "").strip(),
            "partner_name": str(_cell(df, r, cols["partner_name"]) or "").strip(),
            "partner_cui": str(cui or "").strip().upper(),
            "base": _num(base),
            "vat": _num(vat),
            "cod": str(cod or "").strip(),
        })

    legend = {}
    legend_header = None
    for r in range(stop_row, len(df)):
        if _norm(_cell(df, r, 0)) == "referinta cod *)":
            legend_header = r
            break
    if legend_header is not None:
        for r in range(legend_header + 1, len(df)):
            cod = _cell(df, r, 0)
            label = _cell(df, r, 1)
            if cod is None or label is None:
                continue
            cod = str(cod).strip()
            if not re.match(r"^\d", cod):
                continue
            legend[cod] = {
                "label": str(label).strip(),
                "base": _num(_cell(df, r, cols["base"])),
                "vat": _num(_cell(df, r, cols["vat"])),
            }

    return SagaJournal(direction=direction, company_name=company_name,
                       company_cui=company_cui, entries=entries, legend=legend)
