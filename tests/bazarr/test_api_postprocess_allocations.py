import ast
from dataclasses import dataclass
from importlib import import_module

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from api import utils
from app import database as app_database
from app.database import TableEpisodes, TableHistory, TableHistoryMovie, TableMovies, TableShows
from utilities.text_list import parse_string_list


@dataclass
class DisplaySettings:
    embedded_subs_show_desired: bool = False


class CountingSettings:
    def __init__(self) -> None:
        self.reads = 0
        self.display = DisplaySettings()

    @property
    def general(self) -> DisplaySettings:
        self.reads += 1
        return self.display


def reject_literal_eval(value: str) -> None:
    raise AssertionError('Response formatting must not build a Python AST')


def test_response_lists_do_not_use_literal_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, 'settings', CountingSettings())
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)

    result = utils.postprocess({
        'tags': repr(['English', "a'b", 'line\nfeed', '\\path']),
        'alternativeTitles': repr(['Título', 'a "title"']),
    }, subtitles=[])

    assert result['tags'] == ['English', "a'b", 'line\nfeed', '\\path']
    assert result['alternativeTitles'] == ['Título', 'a "title"']


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

    monkeypatch.setattr(utils, 'get_subtitles', reject_subtitle_lookup)
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)

    response = api_client.get(f'/api/{kind}/history', headers={'X-API-KEY': api_key})

    assert response.status_code == 200
    body = response.get_json()
    assert body['total'] == 3
    assert len(body['data']) == 3
    for item in body['data']:
        assert item['matches'] == (['series'] if kind == 'episodes' else ['title'])
        assert item['dont_matches'] == []


def test_series_audio_does_not_use_literal_eval(
    monkeypatch: pytest.MonkeyPatch, memory_session: Session,
) -> None:
    memory_session.add(TableShows(sonarrSeriesId=1, title='Show', path='/shows/show'))
    memory_session.flush()
    memory_session.add_all([
        TableEpisodes(sonarrEpisodeId=index, sonarrSeriesId=1, audio_language="['English']",
                      episode=index, season=1, title='Episode', path=f'/shows/show/{index}.mkv')
        for index in range(1, 101)
    ])
    memory_session.flush()
    monkeypatch.setattr(utils, 'database', memory_session)
    monkeypatch.setattr(utils, 'settings', CountingSettings())
    monkeypatch.setattr(ast, 'literal_eval', reject_literal_eval)

    result = utils.postprocess({'sonarrSeriesId': 1, 'audio_language': '[]'}, subtitles=[])

    assert result.get('audio_language') == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]


def test_display_setting_is_read_once_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = CountingSettings()
    monkeypatch.setattr(utils, 'settings', settings)
    app = Flask(__name__)

    with app.test_request_context():
        for _ in range(100):
            utils.postprocess({}, subtitles=[])
    assert settings.reads == 1

    settings.display.embedded_subs_show_desired = True
    subtitles = [{'name': 'English', 'code2': 'en', 'path': None}]
    with app.test_request_context():
        assert utils.postprocess({}, subtitles=subtitles)['subtitles'] == []
    assert settings.reads == 2


def test_request_lists_are_parsed_once_and_cannot_share_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(utils, 'settings', CountingSettings())
    calls: list[str] = []
    from utilities.text_list import parse_text_list

    def record_parse(value: str) -> list[str | None]:
        calls.append(value)
        return parse_text_list(value)

    monkeypatch.setattr(utils, 'parse_text_list', record_parse)
    app = Flask(__name__)
    with app.test_request_context():
        first = utils.postprocess({'tags': "['shared']"}, subtitles=[])
        tags = first['tags']
        tags.append('changed')
        assert utils.postprocess({'tags': "['shared']"}, subtitles=[])['tags'] == ['shared']
    assert calls == ["['shared']"]
    with app.test_request_context():
        assert utils.postprocess({'tags': "['shared']"}, subtitles=[])['tags'] == ['shared']
    assert calls == ["['shared']", "['shared']"]


def test_audio_formatting_is_reused_without_sharing_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, 'settings', CountingSettings())
    calls: list[tuple[str | None, ...]] = []

    def format_audio(languages: tuple[str | None, ...]) -> list[dict[str, str | None]]:
        calls.append(languages)
        return [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]

    monkeypatch.setattr(utils, 'get_audio_profile_languages', format_audio)
    app = Flask(__name__)
    with app.test_request_context():
        first = utils.postprocess({'audio_language': "['English']"}, subtitles=[])
        languages = first.get('audio_language')
        assert isinstance(languages, list)
        language = languages[0]
        assert isinstance(language, dict)
        language['name'] = 'changed'
        second = utils.postprocess({'audio_language': "['English']"}, subtitles=[])
        assert second.get('audio_language') == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]
    assert calls == [('English',)]
    with app.test_request_context():
        utils.postprocess({'audio_language': "['English']"}, subtitles=[])
    assert calls == [('English',), ('English',)]


def test_calls_without_a_request_do_not_keep_display_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = CountingSettings()
    monkeypatch.setattr(utils, 'settings', settings)
    subtitles = [{'name': 'English', 'code2': 'en', 'path': None}]
    assert utils.postprocess({}, subtitles=subtitles)['subtitles'] == subtitles
    settings.display.embedded_subs_show_desired = True
    assert utils.postprocess({}, subtitles=subtitles)['subtitles'] == []
    assert settings.reads == 2


@pytest.mark.parametrize('audio_language', ['invalid', '[', '[1]', 'None'])
def test_invalid_audio_languages_keep_the_empty_default(
    monkeypatch: pytest.MonkeyPatch, audio_language: str,
) -> None:
    monkeypatch.setattr(utils, 'settings', CountingSettings())
    result = utils.postprocess({'audio_language': audio_language}, subtitles=[])
    assert result.get('audio_language') == []


def test_desired_languages_are_reused_per_profile_and_refresh_between_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = CountingSettings()
    settings.display.embedded_subs_show_desired = True
    monkeypatch.setattr(utils, 'settings', settings)
    profiles = {1: ['en'], 2: ['fr']}
    calls: list[int | str] = []
    equals_calls: list[bool] = []

    def desired_languages(profile_id: int | str) -> list[str]:
        calls.append(profile_id)
        assert isinstance(profile_id, int)
        return profiles[profile_id]

    def language_equals() -> list[tuple[utils.core.Language, utils.core.Language]]:
        equals_calls.append(True)
        return []

    monkeypatch.setattr(utils, 'get_desired_languages', desired_languages)
    monkeypatch.setattr(utils, 'get_language_equals', language_equals)
    subtitles: list[utils.JsonObject] = [
        {'name': 'English', 'code2': 'en', 'path': None},
        {'name': 'French', 'code2': 'fr', 'path': None},
        {'name': 'German', 'code2': 'de', 'path': '/external.de.srt'},
    ]
    app = Flask(__name__)
    with app.test_request_context():
        for _ in range(2):
            assert utils.postprocess({'profileId': 1}, subtitles=subtitles)['subtitles'] == subtitles[::2]
        assert utils.postprocess({'profileId': 2}, subtitles=subtitles)['subtitles'] == subtitles[1:]
    assert calls == [1, 2]
    assert equals_calls == [True]
    profiles[1] = ['fr']
    with app.test_request_context():
        assert utils.postprocess({'profileId': 1}, subtitles=subtitles)['subtitles'] == subtitles[1:]
    assert calls == [1, 2, 1]
    assert equals_calls == [True, True]
