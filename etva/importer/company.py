"""Company sales/purchases journal parser (D300/394-style columns)."""
import pandas as pd

REQUIRED = ["cui_partener", "nr_factura", "data", "baza", "tva", "categorie"]
_CANON = {"cui_partener": "partner_cui", "nr_factura": "invoice_no",
          "data": "date", "baza": "base", "tva": "vat",
          "categorie": "category"}


class ImportError_(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


def _read(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, dtype=str)
    return pd.read_excel(path, dtype=str)


def rows_from_dataframe(df: pd.DataFrame, required=REQUIRED,
                        canon=_CANON) -> list:
    errors = []
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ImportError_([f"Coloane lipsa: {', '.join(missing)}"])
    rows = []
    for idx, rec in df.iterrows():
        file_row = idx + 2  # 1-based + header row
        row = {}
        for src, dst in canon.items():
            val = rec[src]
            if pd.isna(val) or str(val).strip() == "":
                errors.append(f"Rand {file_row}: '{src}' este gol")
                continue
            if dst in ("base", "vat"):
                try:
                    row[dst] = float(str(val).replace(",", "."))
                except ValueError:
                    errors.append(
                        f"Rand {file_row}: '{src}' nu este numeric ({val})")
            else:
                row[dst] = str(val).strip()
        rows.append(row)
    if errors:
        raise ImportError_(errors)
    return rows


def parse_company_journal(path: str) -> list:
    return rows_from_dataframe(_read(path))
