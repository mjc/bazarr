import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Select, create_engine, event, func, select
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.schema import CreateSchema, DropSchema

from api.formatting import ResponseFormatter
from app import database as db


@pytest.fixture(params=['sqlite', 'postgresql'])
def history_session(request: pytest.FixtureRequest) -> Iterator[Session]:
    if request.param == 'postgresql':
        url = os.environ.get('HISTORY_SIDECAR_TEST_POSTGRES_URL')
        if not url:
            pytest.skip('HISTORY_SIDECAR_TEST_POSTGRES_URL is not configured')
        if not db.postgresql:
            pytest.fail('PostgreSQL checks require POSTGRES_ENABLED=true before application imports')
        owner = create_engine(url, isolation_level='AUTOCOMMIT')
        schema = 'history_sidecars_' + uuid4().hex
        with owner.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = owner.execution_options(schema_translate_map={None: schema})
    else:
        owner = engine = create_engine('sqlite://')
        schema = None
    try:
        db.metadata.create_all(engine)
        with Session(engine) as session:
            try:
                yield session
            finally:
                session.rollback()
    finally:
        if schema is not None:
            with owner.connect() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        owner.dispose()


def unchanged_path(path: str) -> str:
    return path


@pytest.mark.parametrize('media', ['episodes', 'movies'])
@pytest.mark.parametrize('length,start', [(1, 0), (2, 1), (100, 0), (100, 100), (-1, 0), (0, 1)])
@pytest.mark.parametrize('filtered', [False, True])
def test_history_matches_grouped_sidecars(
    media: str, length: int, start: int, filtered: bool, history_session: Session,
    api_client: FlaskClient, api_key: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = media == 'episodes'
    history = db.TableHistory if episode else db.TableHistoryMovie
    sidecar = db.TableEpisodesSubtitles if episode else db.TableMoviesSubtitles
    media_key = 'sonarrEpisodeId' if episode else 'radarrId'
    parent_key = getattr(history, media_key)
    child_key = getattr(sidecar, media_key)
    endpoint = import_module(f'api.{media}.history')
    if episode:
        history_session.add(db.TableShows(sonarrSeriesId=1, title='Show', path='/show', tags='[]'))
        history_session.flush()
    for media_id in range(1, 5):
        if episode:
            history_session.add(db.TableEpisodes(sonarrEpisodeId=media_id, sonarrSeriesId=1,
                title=f'Episode {media_id}', path=f'/video-{media_id}.mkv', season=1, episode=media_id))
        else:
            history_session.add(db.TableMovies(radarrId=media_id, tmdbId=str(media_id),
                title=f'Movie {media_id}', path=f'/video-{media_id}.mkv', tags='[]'))
    history_session.flush()
    for media_id, paths in [(1, ['/z.srt', '/a.srt', None]), (2, [None]), (3, []), (4, ['', '/last.srt'])]:
        for track, path in enumerate(paths):
            values = {media_key: media_id, 'language': 'en', 'hi': False, 'forced': False,
                      'path': path, 'embedded_track_id': track if path is None else None}
            if episode:
                values['sonarrSeriesId'] = 1
            history_session.add(sidecar(**values))
    for number, media_id in enumerate([1, 2, 3, 4, 1]):
        values = {media_key: media_id, 'id': number + 1, 'action': 1, 'language': 'en',
                  'video_path': f'/video-{media_id}.mkv', 'subtitles_path': '/a.srt',
                  'timestamp': datetime(2026, 1, 1) + timedelta(days=number), 'score': 100,
                  'score_out_of': 120, 'description': str(number), 'subs_id': 'same', 'provider': 'first'}
        if episode:
            values['sonarrSeriesId'] = 1
        history_session.add(history(**values))
    blacklist = db.TableBlacklist if episode else db.TableBlacklistMovie
    for provider in ('first', 'second'):
        values = {'provider': provider, 'subs_id': 'same',
                  'sonarr_episode_id' if episode else 'radarr_id': 1}
        if episode:
            values['sonarr_series_id'] = 1
        history_session.add(blacklist(**values))
    history_session.flush()
    monkeypatch.setattr(endpoint, 'database', history_session)
    upgrade_name = 'get_upgradable_episode_subtitles' if episode else 'get_upgradable_movies_subtitles'
    monkeypatch.setattr(endpoint, upgrade_name, lambda history_id_list: {value: None for value in history_id_list})
    monkeypatch.setattr(endpoint, 'load_history_languages', lambda rows, candidates: {('en', None)})
    monkeypatch.setattr(endpoint, 'load_formatter', lambda: ResponseFormatter(
        False, None, '', unchanged_path, unchanged_path))
    parameters = {'length': length, 'start': start}
    if filtered:
        parameters['episodeid' if episode else 'radarrid'] = 2
    headers = {'X-API-KEY': api_key}
    captured = []

    def record(state: ORMExecuteState) -> None:
        if isinstance(state.statement, Select) and 'external_subtitles' in state.statement.selected_columns.keys():
            captured.append(state.statement)

    def legacy_grouping(state: ORMExecuteState) -> None:
        statement = state.statement
        if not isinstance(statement, Select) or 'external_subtitles' not in statement.selected_columns.keys():
            return
        grouped = select(child_key.label('media_id'), func.min(sidecar.path).label('path')).group_by(child_key).subquery()
        columns = [grouped.c.path.label('external_subtitles') if column.key == 'external_subtitles' else column
                   for column in statement.selected_columns]
        state.statement = statement.with_only_columns(*columns).outerjoin(grouped, parent_key == grouped.c.media_id)

    event.listen(history_session, 'do_orm_execute', record)
    try:
        actual = api_client.get(f'/api/{media}/history', query_string=parameters, headers=headers)
    finally:
        event.remove(history_session, 'do_orm_execute', record)
    event.listen(history_session, 'do_orm_execute', legacy_grouping)
    try:
        expected = api_client.get(f'/api/{media}/history', query_string=parameters, headers=headers)
    finally:
        event.remove(history_session, 'do_orm_execute', legacy_grouping)
    assert actual.status_code == expected.status_code == 200
    assert actual.get_json() == expected.get_json()
    assert len(captured) == 1
    sql = str(captured[0])
    assert 'GROUP BY' not in sql
    assert 'SELECT min(' in sql
    rows = history_session.execute(captured[0]).all()
    minimum_paths = {1: '/a.srt', 2: None, 3: None, 4: ''}
    assert all(row.external_subtitles == minimum_paths[getattr(row, media_key)] for row in rows)
