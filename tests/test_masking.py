import re

from src.masking import mask_value


def test_mask_shows_first_character_and_short_hash():
    masked = mask_value("Sydney Harbour Traders")
    assert re.fullmatch(r"S\*\*\*#[0-9a-f]{4}", masked)
    assert "Sydney" not in masked


def test_mask_is_stable_within_a_run_and_differs_by_value():
    assert mask_value("Sydney Harbour Traders") == mask_value("Sydney Harbour Traders")
    assert mask_value("Sydney Harbour Traders") != mask_value("SYDNEY HARBOUR TRADERS")


def test_short_values_hide_first_character():
    # For 1-2 character values the first character would give most of it away.
    assert re.fullmatch(r"\*\*\*#[0-9a-f]{4}", mask_value("AB"))


def test_null_and_empty_string_stay_distinguishable():
    # Not personal data, and NULL vs '' is exactly what an operator must see.
    assert mask_value(None) == "NULL"
    assert mask_value("") == "''"
