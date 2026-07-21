import pandas as pd, pytest
from etva.importer import anaf, company

def test_file_source_with_mapping(tmp_path):
    df = pd.DataFrame({
        "CIF": ["RO111"], "Numar": ["F1"], "Data doc": ["2026-01-10"],
        "Baza impozabila": ["100"], "TVA": ["19"], "Tip": ["livrari_interne"],
    })
    p = str(tmp_path / "anaf.csv")
    df.to_csv(p, index=False)
    mapping = {"cui_partener": "CIF", "nr_factura": "Numar",
               "data": "Data doc", "baza": "Baza impozabila",
               "tva": "TVA", "categorie": "Tip"}
    src = anaf.FileAnafDataSource(p, mapping)
    rows = src.get_etva_data("RO999", "2026-01")
    assert rows == [{"partner_cui": "RO111", "invoice_no": "F1",
                     "date": "2026-01-10", "base": 100.0, "vat": 19.0,
                     "category": "livrari_interne"}]

def test_is_abstract():
    with pytest.raises(TypeError):
        anaf.AnafDataSource()

def test_bad_mapping_rejected(tmp_path):
    df = pd.DataFrame({"X": ["1"]})
    p = str(tmp_path / "anaf.csv")
    df.to_csv(p, index=False)
    src = anaf.FileAnafDataSource(p, anaf.DEFAULT_MAPPING)
    with pytest.raises(company.ImportError_):
        src.get_etva_data("RO999", "2026-01")
