from flask.testing import FlaskClient
from pytest import MonkeyPatch
from pytest_mock import MockerFixture
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from api import formatting, response_data
from app.database import TableEpisodes, TableShows


def selected_series(*series_ids: int) -> Select[tuple[int]]:
    return select(TableShows.sonarrSeriesId).where(TableShows.sonarrSeriesId.in_(series_ids))


def add_series(session: Session, series_id: int, audio_language: str | None = '[]') -> None:
    session.add(TableShows(sonarrSeriesId=series_id, path=f'/series/{series_id}',
                          title=f'Series {series_id}', sortTitle=f'Series {series_id}',
                          audio_language=audio_language))
    session.flush()


def add_episode(session: Session, episode_id: int, series_id: int, languages: str | None) -> None:
    session.add(TableEpisodes(sonarrEpisodeId=episode_id, sonarrSeriesId=series_id,
                             season=1, episode=episode_id, title='Episode',
                             path=f'/series/{series_id}/{episode_id}.mkv', audio_language=languages))


def test_repeated_episode_languages_are_parsed_once(
    memory_session: Session, monkeypatch: MonkeyPatch, mocker: MockerFixture,
) -> None:
    monkeypatch.setattr(response_data, 'database', memory_session)
    for series_id in (1, 2):
        add_series(memory_session, series_id)
    for episode_id in range(1, 1001):
        add_episode(memory_session, episode_id, episode_id % 2 + 1,
                    "['English']" if episode_id % 3 else "['English', 'French']")
    memory_session.commit()
    parse = mocker.spy(formatting, 'parse_text_tuple')
    execute = mocker.spy(memory_session, 'execute')

    result = response_data.load_series_audio_languages(selected_series(1, 2), response_data.load_formatter().audio_names)

    assert {series_id: set(names) for series_id, names in result.items()} == {
        1: {'English', 'French'}, 2: {'English', 'French'},
    }
    assert parse.call_count == 2
    assert execute.call_count == 1


def test_empty_selection_returns_no_languages(memory_session: Session, monkeypatch: MonkeyPatch,
                                             mocker: MockerFixture) -> None:
    monkeypatch.setattr(response_data, 'database', memory_session)
    execute = mocker.spy(memory_session, 'execute')

    assert response_data.load_series_audio_languages(selected_series(), response_data.load_formatter().audio_names) == {}
    assert execute.call_count == 1


def test_invalid_and_empty_languages(memory_session: Session, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(response_data, 'database', memory_session)
    for series_id in (1, 2):
        add_series(memory_session, series_id)
    for episode_id, languages in enumerate((None, '[]', '[', "['English', None, '']"), 1):
        add_episode(memory_session, episode_id, 1, languages)
    memory_session.commit()

    result = response_data.load_series_audio_languages(selected_series(1, 2), response_data.load_formatter().audio_names)

    assert set(result[1]) == {'English', None, ''}
    assert 2 not in result


def test_large_series_selection_uses_one_query(memory_session: Session, monkeypatch: MonkeyPatch,
                                             mocker: MockerFixture) -> None:
    monkeypatch.setattr(response_data, 'database', memory_session)
    memory_session.add_all([
        TableShows(sonarrSeriesId=series_id, title='Show', path=f'/series/{series_id}')
        for series_id in range(1, 902)
    ])
    memory_session.flush()
    add_episode(memory_session, 1, 901, "['French']")
    memory_session.commit()
    execute = mocker.spy(memory_session, 'execute')

    result = response_data.load_series_audio_languages(select(TableShows.sonarrSeriesId),
                                                       response_data.load_formatter().audio_names)

    assert result[901] == ('French',)
    assert 1 not in result
    assert execute.call_count == 1


def test_next_request_observes_episode_updates(memory_session: Session, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(response_data, 'database', memory_session)
    add_series(memory_session, 1)
    add_episode(memory_session, 1, 1, "['English']")
    memory_session.commit()
    assert response_data.load_series_audio_languages(selected_series(1), response_data.load_formatter().audio_names) == {
        1: ('English',),
    }

    episode = memory_session.get(TableEpisodes, 1)
    assert episode is not None
    episode.audio_language = "['French']"
    memory_session.commit()

    assert response_data.load_series_audio_languages(selected_series(1), response_data.load_formatter().audio_names) == {
        1: ('French',),
    }


def test_series_list_loads_only_returned_fallbacks(
    api_client: FlaskClient, api_key: str, memory_session: Session,
    monkeypatch: MonkeyPatch, mocker: MockerFixture,
) -> None:
    from api.series import series

    monkeypatch.setattr(series, 'database', memory_session)
    monkeypatch.setattr(response_data, 'database', memory_session)
    for series_id, language in ((1, '[]'), (2, "['Dutch']"), (3, '[]')):
        add_series(memory_session, series_id, language)
        add_episode(memory_session, series_id, series_id, "['English']")
    memory_session.commit()
    aggregate = mocker.spy(series, 'load_series_audio_languages')
    parse = mocker.spy(formatting, 'parse_text_tuple')

    response = api_client.get('/api/series?length=2', headers={'X-API-KEY': api_key})

    assert response.status_code == 200
    assert response.json is not None
    assert response.json['total'] == 3
    assert [row['audio_language'][0]['name'] for row in response.json['data']] == ['English', 'Dutch']
    aggregate.assert_called_once()
    assert aggregate.spy_return == {1: ('English',)}
    assert parse.call_args_list.count(mocker.call("['English']")) == 1


def test_single_series_fallback_keeps_undefined_language_default(
    memory_session: Session, monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(response_data, 'database', memory_session)
    monkeypatch.setattr(response_data.settings.general, 'default_und_audio_lang', 'en')
    add_series(memory_session, 1)
    add_episode(memory_session, 1, 1, '[None]')
    memory_session.commit()

    formatter = response_data.load_formatter()
    languages = response_data.load_series_audio_languages(selected_series(1), formatter.audio_names)
    result = formatter.series(
        {'sonarrSeriesId': 1, 'audio_language': '[]', 'tags': '[]', 'alternativeTitles': '[]', 'monitored': 'True'},
        episode_audio=languages[1],
    )

    assert result['audio_language'] == [{'name': 'English', 'code2': 'en', 'code3': 'eng'}]
