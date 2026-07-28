from datetime import datetime, timezone

from portal import contract


def test_genereaza_text_does_not_assert_prestator_signature():
    beneficiar = {"denumire": "Firma Test SRL", "cui": "RO123", "adresa": "Str. Test 1"}
    text = contract.genereaza_text(
        1, beneficiar, "lunar", 100.0, datetime(2026, 7, 28, tzinfo=timezone.utc))
    assert "a semnat electronic" not in text
    assert "PRESTATOR: VML EXPERT ADVISOR SRL" in text
