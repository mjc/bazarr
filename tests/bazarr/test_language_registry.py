from dataclasses import FrozenInstanceError

import pytest

from languages import get_languages
from languages.formatting import language_info_from_code, language_info_from_name
from languages.registry import LanguageRegistry, ResolvedLanguage


def entry(code2='en', code3='eng', name='English', code3b=None):
    return {'code2': code2, 'code3': code3, 'name': name, 'code3b': code3b}


@pytest.fixture
def registry(monkeypatch):
    registry = LanguageRegistry([
        entry(), entry('fr', 'fra', 'French', 'fre'), entry('pb', 'pob', 'Portuguese (Brazil)'),
        entry('zt', 'zht', 'Chinese Traditional'), entry('ea', 'spl', 'Spanish (Latino)'),
    ])
    monkeypatch.setattr(get_languages, '_language_registry', registry)
    return registry


def test_all_language_lookup_projections_use_the_same_records(registry):
    english = registry.from_code2('en:hi')
    assert english is registry.from_code3('eng:forced') is registry.from_name('English')
    assert get_languages.language_from_alpha2('en:hi') == 'English'
    assert get_languages.language_from_alpha3('fre') == 'French'
    assert get_languages.alpha2_from_alpha3('fre') == 'fr'
    assert get_languages.alpha2_from_language('English') == 'en'
    assert get_languages.alpha3_from_alpha2('fr:forced') == 'fra'
    assert get_languages.alpha3_from_language('French') == 'fra'


def test_duplicate_keys_and_alias_collisions_preserve_first_match():
    registry = LanguageRegistry([
        entry('aa', 'aaa', 'First', 'bbb'), entry('bb', 'bbb', 'First', 'aaa'), entry('aa', 'ccc', 'Third'),
    ])
    assert registry.from_code2('aa').name == 'First'
    assert registry.from_code3('bbb').code2 == 'aa'
    assert registry.from_code3('aaa').code2 == 'aa'
    assert registry.from_name('First').code2 == 'aa'


@pytest.mark.parametrize('field', ['code2', 'code3', 'name'])
def test_incomplete_language_metadata_is_displayable_but_not_resolved(monkeypatch, field):
    value = entry()
    value[field] = None
    registry = LanguageRegistry([value])
    monkeypatch.setattr(get_languages, '_language_registry', registry)
    assert get_languages.resolve_language('en') is None
    if field != 'code2':
        assert language_info_from_code('en:hi') == {'name': value['name'], 'code2': 'en:hi', 'code3': value['code3']}


@pytest.mark.parametrize('code', [None, '', 'xx', 'xx:hi'])
def test_unknown_language_is_not_resolved(registry, code):
    assert get_languages.resolve_language(code) is None
    if code:
        assert language_info_from_code(code) == {'code2': code, 'code3': None, 'name': None}
    assert language_info_from_name('Unknown') == {'name': 'Unknown', 'code2': None, 'code3': None}


@pytest.mark.parametrize('code,official', [('pb', 'por'), ('zt', 'zho'), ('ea', 'spa')])
def test_custom_identity_and_flags_do_not_change_resolved_metadata(registry, code, official):
    resolved = get_languages.resolve_language(code + ':hi')
    assert resolved.code2 == code
    assert resolved is registry.from_code2(code)
    first = resolved.to_language(forced=True, hi=True)
    second = resolved.to_language()
    assert first is not second
    assert first.alpha3 == official
    assert (first.forced, first.hi) == (True, True)
    assert (second.forced, second.hi) == (False, False)
    assert resolved.code2 == code


def test_registry_and_resolved_records_are_immutable(registry):
    with pytest.raises(TypeError):
        registry.by_code2['xx'] = registry.from_code2('en')
    with pytest.raises(FrozenInstanceError):
        registry.from_code2('en').name = 'Changed'


def test_replacing_registry_refreshes_all_projections(registry, monkeypatch):
    previous = get_languages.resolve_language('en')
    monkeypatch.setattr(get_languages, '_language_registry', LanguageRegistry([entry(name='Updated')]))
    assert previous.name == 'English'
    assert get_languages.resolve_language('en').name == 'Updated'
    assert language_info_from_code('en')['name'] == 'Updated'
    assert get_languages.alpha3_from_language('English') is None
    assert get_languages.alpha3_from_language('Updated') == 'eng'


def test_database_refresh_replaces_all_indexes_together(registry, memory_session, monkeypatch):
    from app.database import TableSettingsLanguages
    monkeypatch.setattr(get_languages, 'database', memory_session)
    memory_session.add_all([
        TableSettingsLanguages(code2='en', code3='eng', name='New English'),
        TableSettingsLanguages(code2='zh', code3='zho', name='Chinese'),
        TableSettingsLanguages(code2='el', code3='ell', code3b='gre', name='Modern Greek'),
    ])
    memory_session.commit()
    get_languages.refresh_language_registry()
    current = get_languages.get_language_registry()
    assert current is not registry
    assert current.from_code2('en') is current.from_code3('eng') is current.from_name('New English')
    assert current.from_name('French') is None
    assert current.from_name('Chinese Simplified').code2 == 'zh'
    assert current.from_code3('gre').name == 'Greek'


def test_database_refresh_failure_keeps_previous_registry(registry, mocker):
    database = mocker.patch.object(get_languages, 'database')
    database.execute.side_effect = [None, None, RuntimeError('read failed')]
    with pytest.raises(RuntimeError, match='read failed'):
        get_languages.refresh_language_registry()
    assert get_languages.get_language_registry() is registry
    assert get_languages.resolve_language('fr').name == 'French'


def test_nullable_arr_audio_language_preserves_absence(registry):
    assert get_languages.audio_language_from_name(None) is None
    assert get_languages.audio_language_from_name('Unknown') == 'Unknown'
    assert get_languages.audio_language_from_name('Portuguese (Brazil)') == 'Portuguese (Brazil)'
