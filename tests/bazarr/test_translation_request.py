from dataclasses import FrozenInstanceError

import pytest
from dynaconf.utils.boxing import DynaBox

from bazarr.subtitles.tools.translate.core.translation_request import (
    EpisodeMedia, GeminiConfig, GoogleConfig, LingarrConfig, MovieMedia, load_translator_config,
    resolve_translation_media, resolve_translator_name,
)
from bazarr.subtitles.tools.translate.core.translator_utils import convert_language_codes


@pytest.mark.parametrize('value,expected', [(None, 'google_translate'), ('', 'google_translate'),
                                           ('google_translate', 'google_translate'), ('gemini', 'gemini'),
                                           ('lingarr', 'lingarr')])
def test_translator_name_has_one_canonical_default(value, expected):
    assert resolve_translator_name(value) == expected


def test_unknown_translator_fails_explicitly():
    with pytest.raises(ValueError, match='Unknown translator'):
        resolve_translator_name('unknown')


@pytest.mark.parametrize('media_type,series,episode,movie,expected', [
    ('episode', 1, 2, 99, EpisodeMedia(1, 2)), ('movie', 1, 2, 99, MovieMedia(99)),
])
def test_media_resolution_discards_irrelevant_legacy_ids(media_type, series, episode, movie, expected):
    assert resolve_translation_media(media_type, series, episode, movie) == expected


@pytest.mark.parametrize('media_type,series,episode,movie', [
    ('episode', None, 2, 99), ('episode', 1, None, 99), ('movie', 1, 2, None), ('unknown', 1, 2, 99),
])
def test_media_requires_its_own_identifiers(media_type, series, episode, movie):
    with pytest.raises(ValueError):
        resolve_translation_media(media_type, series, episode, movie)


@pytest.mark.parametrize('code2,code3,normalized', [('fr', 'fra', 'fr'), ('pb', 'pob', 'pt'), ('zt', 'zht', 'zh')])
def test_target_preserves_custom_identity_and_subtitle_flags(code2, code3, normalized):
    language, target = convert_language_codes(code2, forced=True, hi=True)
    assert (target.language.code2, target.language.code3, target.normalized_code2) == (code2, code3, normalized)
    assert language.forced and language.hi


def test_invalid_target_is_rejected_before_service_creation():
    with pytest.raises(ValueError, match='Unable to translate'):
        convert_language_codes('xx')


@pytest.mark.parametrize('batch,expected', [(450, 450), ('invalid', 300), (0, 1)])
def test_gemini_configuration_normalizes_once(batch, expected):
    section = DynaBox({'translator_type': 'gemini', 'translator_info': False, 'default_score': 50,
                       'gemini_model': 'model', 'gemini_batch_size': batch,
                       'gemini_keys': [' key-1 ', '', 'key-2', 'key-1']})
    config = load_translator_config(section)
    assert isinstance(config, GeminiConfig)
    assert config.batch_size == expected
    assert config.keys == ('key-1', 'key-2')
    section.gemini_model = 'changed'
    section.gemini_keys.append('new')
    assert config.model == 'model'
    assert config.keys == ('key-1', 'key-2')
    with pytest.raises(FrozenInstanceError):
        config.model = 'mutate'


@pytest.mark.parametrize('name,expected', [('', GoogleConfig), ('lingarr', LingarrConfig)])
def test_service_configuration_reads_only_selected_fields(name, expected):
    section = DynaBox({'translator_type': name, 'translator_info': True, 'default_score': 60,
                       'lingarr_url': 'http://lingarr', 'lingarr_token': 'token'})
    assert isinstance(load_translator_config(section), expected)
