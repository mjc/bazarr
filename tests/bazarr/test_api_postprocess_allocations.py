import ast
from dataclasses import dataclass

import pytest
from flask import Flask
from sqlalchemy.orm import Session

from api import utils
from app.database import TableEpisodes, TableShows


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

    assert result['audio_language'] == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]


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
        assert isinstance(tags, list)
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
        languages = first['audio_language']
        assert isinstance(languages, list)
        language = languages[0]
        assert isinstance(language, dict)
        language['name'] = 'changed'
        second = utils.postprocess({'audio_language': "['English']"}, subtitles=[])
        assert second['audio_language'] == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]
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
    assert result['audio_language'] == []


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
