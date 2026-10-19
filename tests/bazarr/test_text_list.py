import logging

import pytest

from utilities.text_list import parse_string_list, parse_text_list, parse_text_list_or_default, parse_text_tuple


def test_parse_text_list_accepts_none_only_as_a_complete_token():
    assert parse_text_list("[None, 'en']") == [None, "en"]

    with pytest.raises(ValueError):
        parse_text_list("[Nonexistent]")


def test_parse_text_list_or_default_logs_invalid_legacy_values(caplog):
    caplog.set_level(logging.DEBUG, logger="utilities.text_list")

    assert parse_text_list_or_default("[True]", default=["en"]) == ["en"]
    assert "Could not parse text list '[True]'" in caplog.text
    assert "invalid text-list syntax" in caplog.text


def test_parse_text_list_round_trips_python_repr_escapes():
    values = ["line\nbreak", "tab\tvalue", "\x01", "separator\u2028", r"literal\backslash"]

    assert parse_text_list(repr(values)) == values
    assert parse_text_list(r"['\u2603', '\U0001f40d', '\x50']") == ["☃", "🐍", "P"]


@pytest.mark.parametrize('values', [[], ['series', 'year'], ["'quoted'", '☃', r'back\slash', 'line\nbreak']])
def test_parse_string_list_round_trips_strings_without_ast(values: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_ast(value: str) -> None:
        pytest.fail('Text lists must not be parsed as Python code')

    monkeypatch.setattr('ast.literal_eval', reject_ast)
    monkeypatch.setattr('ast.parse', reject_ast)

    assert parse_string_list(repr(values)) == values


@pytest.mark.parametrize('value', ['[None]', '[True]', '[1]', "['tag', None]", '[str(1)]', 'invalid'])
def test_parse_string_list_rejects_non_string_entries(value: str) -> None:
    with pytest.raises(ValueError):
        parse_string_list(value)


def test_text_decoder_round_trips_control_characters_and_unicode() -> None:
    values = [chr(value) for value in range(256)] + ['🐍', 'Ω', "single' double\"", r'back\slash', None]
    assert parse_text_tuple(repr(values)) == tuple(values)
    assert parse_text_list(repr(values)) == values


def test_text_decoder_rejects_a_malformed_tail_without_returning_partial_values() -> None:
    with pytest.raises(ValueError):
        parse_text_tuple("['French', 1]")


def test_existing_sequence_adapters_keep_their_ownership_contract() -> None:
    values = ['en', None]
    assert parse_text_list(values) is values
    assert parse_text_list(tuple(values)) == values
