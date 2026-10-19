import ast
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module

import pytest
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from api import formatting, response_data
from api.formatting import ResponseFormatter
from api.response_types import EpisodeRow, HistoryRow, SeriesRow
from app import database as app_database
from app.database import (TableBlacklist, TableBlacklistMovie, TableEpisodes, TableHistory,
                          TableHistoryMovie, TableMovies, TableShows)
from languages.formatting import LanguageInfo
from utilities.subtitle_metadata import SubtitleFile
from utilities.text_list import parse_string_list, parse_text_tuple


@dataclass
class DisplaySettings:
    embedded_subs_show_desired: bool = False
    default_und_audio_lang: str = ''


class CountingSettings:
    def __init__(self) -> None:
        self.reads = 0
        self.display = DisplaySettings()

    @property
    def general(self) -> DisplaySettings:
        self.reads += 1
        return self.display


def unchanged_path(path: str | None) -> str | None:
    return path


def new_formatter() -> ResponseFormatter:
    return ResponseFormatter(False, None, '', unchanged_path, unchanged_path)


@pytest.fixture
def formatter() -> ResponseFormatter:
    return new_formatter()


def series_row(tags: str | None = '[]', titles: str | None = '[]', audio: str | None = None) -> SeriesRow:
    return {'sonarrSeriesId': 1, 'tags': tags, 'alternativeTitles': titles, 'audio_language': audio, 'monitored': 'True'}


def episode_row(audio: str | None = None, profile_id: int | None = None) -> EpisodeRow:
    return {'sonarrEpisodeId': 1, 'sonarrSeriesId': 1, 'audio_language': audio, 'monitored': 'True',
            'missing_subtitles': [], 'profileId': profile_id}


def subtitle(name: str, code: str, path: str | None = None) -> SubtitleFile:
    return {'id': 1, 'name': name, 'code2': code, 'code3': None, 'path': path,
            'forced': False, 'hi': False, 'file_size': None, 'embedded_track_id': None}


def reject_literal_eval(value: str) -> None:
    pytest.fail('Response formatting must not build a Python AST')


def test_response_lists_do_not_use_literal_eval(formatter: ResponseFormatter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)
    row = series_row(repr(['English', "a'b", 'line\nfeed', '\\path']), repr(['Título', 'a "title"']))

    result = formatter.series(row, episode_audio=None)

    assert result['tags'] == ['English', "a'b", 'line\nfeed', '\\path']
    assert result['alternativeTitles'] == ['Título', 'a "title"']
    assert row['tags'] == repr(['English', "a'b", 'line\nfeed', '\\path'])


@pytest.mark.parametrize('kind', ['series', 'movies'])
def test_tag_endpoints_do_not_use_literal_eval(
    kind: str, api_client: FlaskClient, api_key: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = [repr(['z', 'a', "a'b"]), repr(['a']), None, repr(['z', 'a', "a'b"])]
    for index, tags in enumerate(values, start=1):
        memory_session.add_all([
            TableShows(sonarrSeriesId=index, path=f'/shows/{index}', title='Show', tags=tags),
            TableMovies(radarrId=index, path=f'/movies/{index}', title='Movie', tmdbId=str(index), tags=tags),
        ])
    memory_session.flush()
    parsed_values: list[str] = []

    def record_parse(value: str) -> list[str]:
        parsed_values.append(value)
        return parse_string_list(value)

    monkeypatch.setattr(app_database, 'database', memory_session)
    monkeypatch.setattr(app_database, 'parse_string_list', record_parse)
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)

    response = api_client.get(f'/api/{kind}/tags', headers={'X-API-KEY': api_key})

    assert response.status_code == 200
    assert response.get_json() == {'data': [{'tag': 'a'}, {'tag': "a'b"}, {'tag': 'z'}]}
    assert len(parsed_values) == 2


@pytest.mark.parametrize('kind', ['episodes', 'movies'])
def test_history_does_not_load_unused_subtitles(
    kind: str, api_client: FlaskClient, api_key: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_session.add_all([
        TableShows(sonarrSeriesId=1, path='/shows/1', title='Show', tags='[]'),
        TableMovies(radarrId=1, path='/movies/1/movie.mkv', title='Movie', tmdbId='1', tags='[]'),
    ])
    memory_session.flush()
    memory_session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, path='/shows/1/episode.mkv',
                                     title='Episode', season=1, episode=1))
    memory_session.flush()
    for index in range(1, 4):
        memory_session.add_all([
            TableHistory(id=index, action=1, description='Downloaded', sonarrSeriesId=1, sonarrEpisodeId=1,
                         language='en', matched="['series']", not_matched=None),
            TableHistoryMovie(id=index, action=1, description='Downloaded', radarrId=1,
                              language='en', matched="['title']", not_matched=None),
        ])
    memory_session.flush()
    monkeypatch.setattr(import_module(f'api.{kind}.history'), 'database', memory_session)
    monkeypatch.setattr('subtitles.upgrade.database', memory_session)

    def reject_subtitle_lookup(sonarr_episode_id: int | None = None, radarr_id: int | None = None) -> None:
        pytest.fail('History responses do not include subtitle lists')

    monkeypatch.setattr(app_database, 'get_subtitles', reject_subtitle_lookup)
    monkeypatch.setattr(app_database, 'get_subtitles_map', reject_subtitle_lookup)
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)

    response = api_client.get(f'/api/{kind}/history', headers={'X-API-KEY': api_key})

    assert response.status_code == 200
    body = response.get_json()
    assert body['total'] == 3
    assert len(body['data']) == 3
    for item in body['data']:
        assert item['matches'] == (['series'] if kind == 'episodes' else ['title'])
        assert item['dont_matches'] == []


@pytest.mark.parametrize('kind', ['episodes', 'movies'])
def test_populated_blacklist_formats_language_and_timestamp(
    kind: str, api_client: FlaskClient, api_key: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = datetime(2026, 1, 2, 3, 4, 5)
    memory_session.add_all([
        TableShows(sonarrSeriesId=1, path='/shows/1', title='Show'),
        TableMovies(radarrId=1, path='/movies/1.mkv', title='Movie', tmdbId='1'),
    ])
    memory_session.flush()
    memory_session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, path='/shows/1/episode.mkv',
                                     title='Episode', season=1, episode=2))
    memory_session.flush()
    memory_session.add_all([
        TableBlacklist(sonarr_episode_id=1, sonarr_series_id=1, provider='provider', subs_id='123',
                       language='en:forced', timestamp=timestamp),
        TableBlacklistMovie(radarr_id=1, provider='provider', subs_id='123',
                            language='en:forced', timestamp=timestamp),
    ])
    memory_session.flush()
    monkeypatch.setattr(import_module(f'api.{kind}.blacklist'), 'database', memory_session)
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)
    response = api_client.get(f'/api/{kind}/blacklist', headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    item, = response.get_json()['data']
    assert item['language'] == {'name': 'English', 'code2': 'en', 'code3': 'eng', 'forced': True, 'hi': False}
    assert item['timestamp'] == formatting.pretty.date(timestamp)
    assert item['parsed_timestamp'] == timestamp.strftime('%x %X')
    assert item['provider'] == 'provider'
    assert item['subs_id'] == '123'
    if kind == 'episodes':
        assert item['seriesTitle'] == 'Show'
        assert item['episode_number'] == '1x2'
    else:
        assert item['title'] == 'Movie'


def test_series_audio_is_loaded_before_formatting(
    formatter: ResponseFormatter, monkeypatch: pytest.MonkeyPatch, memory_session: Session,
) -> None:
    memory_session.add(TableShows(sonarrSeriesId=1, title='Show', path='/shows/show'))
    memory_session.flush()
    values = ["['English']"] * 100 + ["['French', 1]", '[None]', None]
    memory_session.add_all([
        TableEpisodes(sonarrEpisodeId=index, sonarrSeriesId=1, audio_language=value,
                      episode=index, season=1, title='Episode', path=f'/shows/show/{index}.mkv')
        for index, value in enumerate(values, start=1)
    ])
    memory_session.flush()
    monkeypatch.setattr(response_data, 'database', memory_session)
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)
    audio = response_data.load_series_audio_languages([1], formatter.audio_names)

    def reject_query() -> None:
        pytest.fail('Formatting must not load database rows')

    monkeypatch.setattr(memory_session, 'execute', reject_query)
    result = formatter.series(series_row(audio='[]'), episode_audio=audio[1])

    assert result['audio_language'] == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]


def test_series_requires_preloaded_episode_audio(formatter: ResponseFormatter) -> None:
    with pytest.raises(ValueError, match='loaded before formatting'):
        formatter.series(series_row(audio='[]'), episode_audio=None)


def test_settings_are_read_once_when_creating_a_formatter(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = CountingSettings()
    monkeypatch.setattr(response_data, 'settings', settings)
    formatter = response_data.load_formatter()
    subtitles = [subtitle('English', 'en')]
    for _ in range(100):
        assert formatter.episode(episode_row(), subtitles=subtitles)['subtitles'] == subtitles
    assert settings.reads == 1

    settings.display.embedded_subs_show_desired = True
    next_formatter = response_data.load_formatter()
    assert next_formatter.episode(episode_row(), subtitles=subtitles)['subtitles'] == []
    assert formatter.episode(episode_row(), subtitles=subtitles)['subtitles'] == subtitles
    assert settings.reads == 2


@pytest.mark.parametrize('show_desired', [False, True])
def test_subtitle_outputs_do_not_share_dictionaries(show_desired: bool) -> None:
    formatter = ResponseFormatter(show_desired, None, '', unchanged_path, unchanged_path,
                                  {1: frozenset({'en'})})
    loaded = [subtitle('English', 'en'), subtitle('French', 'fr', '/show/french.srt')]
    first = formatter.episode(episode_row(profile_id=1), subtitles=loaded)['subtitles']
    second = formatter.episode(episode_row(profile_id=1), subtitles=loaded)['subtitles']
    first[0]['path'] = '/changed.srt'
    first[1]['name'] = 'changed'
    assert loaded[0]['path'] is None
    assert loaded[1]['name'] == 'French'
    assert second == loaded
    assert formatter.episode(episode_row(profile_id=1), subtitles=loaded)['subtitles'] == loaded


def test_tag_parses_are_reused_with_fresh_output_lists(
    formatter: ResponseFormatter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def record_parse(value: str) -> tuple[str | None, ...]:
        calls.append(value)
        return parse_text_tuple(value)

    monkeypatch.setattr(formatting, 'parse_text_tuple', record_parse)
    row = series_row(tags="['shared']")
    first = formatter.series(row, episode_audio=None)
    first['tags'].append('changed')
    assert formatter.series(row, episode_audio=None)['tags'] == ['shared']
    assert calls == ["['shared']"]
    assert new_formatter().series(row, episode_audio=None)['tags'] == ['shared']
    assert calls == ["['shared']", "['shared']"]


def test_alternate_titles_are_not_retained_in_the_cache(formatter: ResponseFormatter) -> None:
    for index in range(100):
        row = series_row(titles=repr([f'Title {index}']))
        assert formatter.series(row, episode_audio=None)['alternativeTitles'] == [f'Title {index}']
    assert set(formatter._text_lists) == {'[]'}


def test_audio_formatting_is_reused_without_sharing_output(
    formatter: ResponseFormatter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, ...]] = []

    def format_audio(names: Sequence[str | None], undefined_name: str | None) -> list[LanguageInfo]:
        calls.append(tuple(names))
        return [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]

    monkeypatch.setattr(formatting, 'format_audio_languages', format_audio)
    row = episode_row("['English']")
    first = formatter.episode(row, subtitles=[])['audio_language']
    assert isinstance(first, list)
    first[0]['name'] = 'changed'
    assert formatter.episode(row, subtitles=[])['audio_language'] == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]
    assert calls == [('English',)]
    new_formatter().episode(row, subtitles=[])
    assert calls == [('English',), ('English',)]


@pytest.mark.parametrize('value', ['invalid', '[', '[1]', 'None', "['English', 1]"])
def test_invalid_audio_languages_keep_the_empty_default(formatter: ResponseFormatter, value: str) -> None:
    assert formatter.episode(episode_row(value), subtitles=[])['audio_language'] == []


@pytest.mark.parametrize('value', [None, ''])
def test_empty_audio_values_keep_their_response_shape(formatter: ResponseFormatter, value: str | None) -> None:
    assert formatter.episode(episode_row(value), subtitles=[])['audio_language'] == value


def test_desired_languages_are_loaded_per_profile_and_refresh_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = CountingSettings()
    settings.display.embedded_subs_show_desired = True
    monkeypatch.setattr(response_data, 'settings', settings)
    profiles = {1: ['en'], 2: ['fr']}
    calls: list[int] = []
    equals_calls: list[bool] = []

    def desired_languages(profile_id: int) -> list[str]:
        calls.append(profile_id)
        return profiles[profile_id]

    def language_equals() -> list[tuple[response_data.core.Language, response_data.core.Language]]:
        equals_calls.append(True)
        return []

    monkeypatch.setattr(response_data, 'get_desired_languages', desired_languages)
    monkeypatch.setattr(response_data, 'get_language_equals', language_equals)
    subtitles = [subtitle('English', 'en'), subtitle('French', 'fr'), subtitle('German', 'de', '/external.de.srt')]
    formatter = response_data.load_formatter([1, 1, 2, None])
    for _ in range(2):
        assert formatter.episode(episode_row(profile_id=1), subtitles=subtitles)['subtitles'] == subtitles[::2]
    assert formatter.episode(episode_row(profile_id=2), subtitles=subtitles)['subtitles'] == subtitles[1:]
    assert sorted(calls) == [1, 2]
    assert equals_calls == [True]
    profiles[1] = ['fr']
    next_formatter = response_data.load_formatter([1])
    assert next_formatter.episode(episode_row(profile_id=1), subtitles=subtitles)['subtitles'] == subtitles[1:]
    assert calls[-1] == 1
    assert equals_calls == [True, True]


def test_language_templates_are_reused_without_sharing_output(formatter: ResponseFormatter) -> None:
    first = formatter.missing_languages(['en'])[0]
    first['name'] = 'changed'
    assert formatter.missing_languages(['en'])[0]['name'] == 'English'
    language = formatter.language('en')
    assert language is not None and language != ''
    assert language['name'] == 'English'
    assert len(formatter._subtitle_languages) == 1


def test_language_flags_keep_each_response_contract(formatter: ResponseFormatter) -> None:
    language = formatter.language('en:hi:forced')
    assert language is not None and language != ''
    assert language['forced'] is True and language['hi'] is False
    missing = formatter.missing_languages(['en:hi:forced'])[0]
    assert missing['forced'] is False and missing['hi'] is True
    assert formatter.language('None') is None
    assert formatter.language(None) is None
    assert formatter.language('') == ''


@pytest.mark.parametrize('names', ["['French', None, None]", ['French', None, None], ('French', None, None)])
def test_audio_adapter_preserves_duplicate_undefined_languages(
    names: str | list[str | None] | tuple[str | None, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = CountingSettings()
    settings.display.default_und_audio_lang = 'en'
    monkeypatch.setattr(app_database, 'settings', settings)
    assert app_database.get_audio_profile_languages(names) == [
        {'name': 'French', 'code2': 'fr', 'code3': 'fra'},
        {'name': 'English', 'code2': 'en', 'code3': 'eng'},
        {'name': 'English', 'code2': 'en', 'code3': 'eng'},
    ]


@pytest.mark.parametrize('score,maximum,expected', [(180, 360, '50.0%'), (0, None, '0'), (None, None, None)])
def test_shared_history_formatting(
    formatter: ResponseFormatter, score: int | None, maximum: int | None, expected: str | None,
) -> None:
    timestamp = datetime(2024, 1, 2, 3, 4, 5)
    row: HistoryRow = {
        'id': 1, 'timestamp': timestamp, 'language': 'en:forced', 'score': score, 'score_out_of': maximum,
        'tags': "['tag']", 'monitored': 'True', 'video_path': '/video.mkv', 'path': '/video.mkv',
        'subtitles_path': '/video.en.srt', 'external_subtitles': '/video.en.srt',
        'matched': "['series']", 'not_matched': "['year']", 'title': 'Movie',
    }
    for method in (formatter.episode_history, formatter.movie_history):
        result = method(row, can_upgrade=True)
        assert result['score'] == expected
        assert result['parsed_timestamp'] == timestamp.strftime('%x %X')
        assert result['matches'] == ['series']
        assert result['dont_matches'] == ['year']
        assert result['upgradable'] is True
        assert 'profileId' not in result and 'path' not in result
        assert method(row, can_upgrade=False)['upgradable'] is False
        changed = row.copy()
        changed['external_subtitles'] = '/different.srt'
        assert method(changed, can_upgrade=True)['upgradable'] is False
