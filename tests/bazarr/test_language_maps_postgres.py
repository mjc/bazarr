import os
from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from api import response_data
from app import database as app_database
from app.database import TableEpisodes, TableFailedSubtitleAttempts, TableMissingSubtitles, TableShows, metadata
from subtitles import wanted_state
from utilities.text_list import parse_text_tuple


@pytest.fixture
def postgres_language_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    url = os.environ.get('LANGUAGE_MAP_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('LANGUAGE_MAP_TEST_POSTGRES_URL is not configured')
    if not app_database.postgresql:
        pytest.fail('Run PostgreSQL language-map checks with POSTGRES_ENABLED=true before application imports')
    owner = create_engine(url, isolation_level='AUTOCOMMIT', poolclass=NullPool)
    schema = 'language_maps_' + uuid4().hex
    try:
        with owner.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = owner.execution_options(schema_translate_map={None: schema})
        metadata.create_all(engine)
        with Session(engine) as session:
            monkeypatch.setattr(response_data, 'database', session)
            monkeypatch.setattr(wanted_state, 'database', session)
            monkeypatch.setattr(wanted_state, 'get_adaptive_search_policy', lambda: None)
            yield session
    finally:
        with owner.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        owner.dispose()


@pytest.mark.parametrize('mode', ['api', 'missing', 'due-filtered', 'due-full', 'iterator'])
def test_missing_language_loaders_work_with_postgres_autocommit(
    postgres_language_session: Session, mode: str,
) -> None:
    session = postgres_language_session
    codes = [f'lang-{index:04d}' for index in range(600)]
    session.execute(insert(TableMissingSubtitles), [
        {'media_type': 'series', 'media_id': media_id, 'language': code}
        for media_id in (1, 2) for code in codes
    ])
    session.execute(insert(TableFailedSubtitleAttempts), [
        {'media_type': 'series', 'media_id': 1, 'language': codes[0],
         'initial_attempt_at': 0, 'latest_attempt_at': 20},
    ])
    policy = {'delay': timedelta(days=7), 'delta': timedelta(days=1),
              'initial_search_cutoff': 100.0, 'latest_search_cutoff': 10.0}
    if mode == 'api':
        actual = response_data.load_missing_languages('series', select(TableMissingSubtitles.media_id).distinct())
    elif mode == 'missing':
        actual = wanted_state.get_missing_languages_map('series', [2, 1, 3, 1])
    elif mode == 'due-filtered':
        actual = wanted_state.get_due_missing_languages_map('series', [2, 1, 3, 1], policy)
    elif mode == 'due-full':
        actual = wanted_state.get_due_missing_languages_map('series', adaptive_search_policy=policy)
    else:
        actual = {media_id: languages for batch in wanted_state.iter_due_missing_languages_maps(
            'series', adaptive_search_policy=policy, batch_size=1) for media_id, languages in batch.items()}
    due = mode in ('due-filtered', 'due-full', 'iterator')
    expected = {1: codes[1:] if due else codes, 2: codes}
    if mode in ('missing', 'due-filtered'):
        expected[3] = []
        assert list(actual) == [2, 1, 3]
    assert actual == expected
    assert actual[1][actual[1].index(codes[-1])] is actual[2][-1]


def test_audio_language_loader_works_with_postgres_autocommit(postgres_language_session: Session) -> None:
    session = postgres_language_session
    session.execute(insert(TableShows), [{'sonarrSeriesId': 1, 'title': 'Show', 'path': '/show'}])
    session.execute(insert(TableEpisodes), [
        {'sonarrEpisodeId': index + 1, 'sonarrSeriesId': 1, 'title': 'Episode',
         'path': f'/show/{index}.mkv', 'season': 1, 'episode': index + 1,
         'audio_language': str([f'language-{index}'])}
        for index in range(600)
    ])
    actual = response_data.load_series_audio_languages(select(TableShows.sonarrSeriesId), parse_text_tuple)
    assert set(actual[1]) == {f'language-{index}' for index in range(600)}


def test_language_collection_preserves_autoflush_controls_on_postgres_autocommit(
    postgres_language_session: Session,
) -> None:
    session = postgres_language_session
    session.add(TableMissingSubtitles(media_type='series', media_id=1, language='en'))
    with session.no_autoflush:
        assert wanted_state.get_missing_languages_map('series', [1]) == {1: []}
    assert wanted_state.get_missing_languages_map('series', [1]) == {1: ['en']}


@pytest.mark.parametrize('media_type', ['series', 'movie'])
def test_subtitle_maps_work_with_postgres_autocommit(
    postgres_language_session: Session, media_type: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = postgres_language_session
    monkeypatch.setattr(app_database, 'database', session)
    episode = media_type == 'series'
    if episode:
        session.add(TableShows(sonarrSeriesId=1, title='Show', path='/show'))
        session.flush()
        session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, title='Episode',
                                  path='/show/1.mkv', season=1, episode=1))
        media_ids = select(TableEpisodes.sonarrEpisodeId)
    else:
        session.add(app_database.TableMovies(radarrId=1, title='Movie', path='/movie', tmdbId='1'))
        media_ids = select(app_database.TableMovies.radarrId)
    session.flush()
    sidecar = app_database.TableEpisodesSubtitles if episode else app_database.TableMoviesSubtitles
    for number in range(600):
        values: dict[str, str | bool | int] = {
            'language': 'en', 'path': f'/video-{number}.srt', 'forced': False, 'hi': False,
        }
        values.update({'sonarrEpisodeId': 1, 'sonarrSeriesId': 1} if episode else {'radarrId': 1})
        session.add(sidecar(**values))
    session.flush()
    by_ids = app_database.get_subtitles_map(media_type, [1])
    by_query = app_database.get_subtitles_for_query(media_type, media_ids)
    assert len(by_ids[1]) == 600
    assert by_query == by_ids
