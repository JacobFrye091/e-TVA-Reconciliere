"""ANAF e-TVA data source. The official format is not yet published, so the
file-based implementation uses a configurable column mapping. A future live
API connector implements the same interface."""
from abc import ABC, abstractmethod
import pandas as pd
from etva.importer.company import rows_from_dataframe, REQUIRED

DEFAULT_MAPPING = {c: c for c in REQUIRED}


class AnafDataSource(ABC):
    @abstractmethod
    def get_etva_data(self, cui: str, period: str) -> list:
        ...


class FileAnafDataSource(AnafDataSource):
    def __init__(self, path: str, column_mapping: dict = None):
        self.path = path
        self.mapping = column_mapping or DEFAULT_MAPPING

    def get_etva_data(self, cui: str, period: str) -> list:
        if self.path.lower().endswith(".csv"):
            df = pd.read_csv(self.path, dtype=str)
        else:
            df = pd.read_excel(self.path, dtype=str)
        # Rename actual file columns to canonical Romanian names first.
        rename = {actual: canon for canon, actual in self.mapping.items()}
        df = df.rename(columns=rename)
        return rows_from_dataframe(df)
