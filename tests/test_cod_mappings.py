import os, pytest
from etva import db, cod_mappings


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(c)
    yield c
    c.close()


def test_save_and_load_for_client(conn):
    cod_mappings.save_mapping(conn, 5, "vanzari", "17", "14+15", "sef")
    assert cod_mappings.load_for_client(conn, 5) == {("vanzari", "17"): "14+15"}


def test_upsert_overwrites_existing_row(conn):
    cod_mappings.save_mapping(conn, 5, "vanzari", "17", "1", "sef")
    cod_mappings.save_mapping(conn, 5, "vanzari", "17", "14+15", "sef2")
    rows = cod_mappings.list_for_client(conn, 5)
    assert len(rows) == 1
    assert rows[0]["line_no"] == "14+15"
    assert rows[0]["updated_by"] == "sef2"


def test_direct_firm_scope_client_id_none(conn):
    cod_mappings.save_mapping(conn, None, "vanzari", "17", "14+15", "sef")
    assert cod_mappings.load_for_client(conn, None) == {("vanzari", "17"): "14+15"}
    assert cod_mappings.load_for_client(conn, 5) == {}


def test_same_cod_different_meaning_per_direction(conn):
    # Bug real reprodus: codul "14" exista atat in jurnalul de vanzari cat
    # si in cel de cumparari, cu sensuri diferite - trebuie sa poata avea
    # mapari diferite, scopate pe directie, fara sa se calce una pe alta.
    cod_mappings.save_mapping(conn, 5, "vanzari", "14", "1", "sef")
    cod_mappings.save_mapping(conn, 5, "cumparari", "14", "29", "sef")
    loaded = cod_mappings.load_for_client(conn, 5)
    assert loaded == {("vanzari", "14"): "1", ("cumparari", "14"): "29"}


def test_client_id_none_does_not_collide_with_real_client(conn):
    # Doua indexuri unice partiale separate (client_id IS NULL vs NOT
    # NULL) - un cod/directie identice pentru firma "direct" si pentru un
    # client real trebuie sa coexiste ca randuri separate.
    cod_mappings.save_mapping(conn, None, "vanzari", "17", "14+15", "sef")
    cod_mappings.save_mapping(conn, 5, "vanzari", "17", "1", "sef")
    assert cod_mappings.load_for_client(conn, None) == {("vanzari", "17"): "14+15"}
    assert cod_mappings.load_for_client(conn, 5) == {("vanzari", "17"): "1"}


def test_save_mapping_rejects_cross_section_line(conn):
    with pytest.raises(cod_mappings.MappingError):
        cod_mappings.save_mapping(conn, 5, "cumparari", "14", "14+15", "sef")
    assert cod_mappings.load_for_client(conn, 5) == {}


def test_delete_mapping(conn):
    cod_mappings.save_mapping(conn, 5, "vanzari", "17", "14+15", "sef")
    [row] = cod_mappings.list_for_client(conn, 5)
    cod_mappings.delete_mapping(conn, row["id"])
    assert cod_mappings.list_for_client(conn, 5) == []
