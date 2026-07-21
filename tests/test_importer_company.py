import pandas as pd, pytest
from etva.importer import company

GOOD = pd.DataFrame({
    "cui_partener": ["RO111", "RO222"],
    "nr_factura": ["F1", "F2"],
    "data": ["2026-01-10", "2026-01-15"],
    "baza": [100.0, 200.0],
    "tva": [19.0, 38.0],
    "categorie": ["livrari_interne", "achizitii_interne"],
})

def test_parse_xlsx(tmp_path):
    p = str(tmp_path / "j.xlsx")
    GOOD.to_excel(p, index=False)
    rows = company.parse_company_journal(p)
    assert rows[0] == {"partner_cui": "RO111", "invoice_no": "F1",
                       "date": "2026-01-10", "base": 100.0, "vat": 19.0,
                       "category": "livrari_interne"}

def test_parse_csv(tmp_path):
    p = str(tmp_path / "j.csv")
    GOOD.to_csv(p, index=False)
    assert len(company.parse_company_journal(p)) == 2

def test_missing_column_rejected(tmp_path):
    p = str(tmp_path / "j.csv")
    GOOD.drop(columns=["tva"]).to_csv(p, index=False)
    with pytest.raises(company.ImportError_) as e:
        company.parse_company_journal(p)
    assert "tva" in str(e.value)

def test_bad_number_rejected_entirely(tmp_path):
    bad = GOOD.astype(str)
    bad.loc[1, "baza"] = "abc"
    p = str(tmp_path / "j.csv")
    bad.to_csv(p, index=False)
    with pytest.raises(company.ImportError_) as e:
        company.parse_company_journal(p)
    assert "baza" in str(e.value) and "3" in str(e.value)  # file row number
