"""Catalog pure helpers: language tags and natural keys (spec 0022)."""

from voiceai.modules.catalog.static_methods import build_catalog_id, is_valid_language


def test_well_formed_language_tags_pass() -> None:
    """Primary, script, and region subtags all validate."""
    assert is_valid_language("en") is True
    assert is_valid_language("hi") is True
    assert is_valid_language("en-US") is True
    assert is_valid_language("zh-Hant-TW") is True
    assert is_valid_language("es-419") is True


def test_malformed_language_tags_fail() -> None:
    """Empty, separator, and shape violations never validate."""
    assert is_valid_language("") is False
    assert is_valid_language("en_US") is False
    assert is_valid_language("english") is False
    assert is_valid_language("e") is False
    assert is_valid_language("EN") is False


def test_catalog_id_joins_the_natural_key() -> None:
    """The key shape matches the seeder and the repository pin."""
    assert build_catalog_id("tts", "maya", "Maya 2 Native") == "tts:maya:Maya 2 Native"
