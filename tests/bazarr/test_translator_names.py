import pytest
from subtitles.tools.translate.services.translator_factory import resolve_translator_name


@pytest.mark.parametrize('value', [None, ''])
def test_missing_translator_defaults_to_factory_google_name(value):
    assert resolve_translator_name(value) == 'google_translate'


@pytest.mark.parametrize('value', ['google_translate', 'gemini', 'lingarr'])
def test_configured_translator_is_preserved(value):
    assert resolve_translator_name(value) == value


def test_unknown_translator_has_no_resolved_name():
    assert resolve_translator_name('unknown') is None
