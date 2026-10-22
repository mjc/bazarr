import json
from unittest.mock import Mock
from threading import Event
from types import SimpleNamespace

import pytest
import requests
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import TableMovies, TableShows
from radarr.info import get_radarr_info
from radarr.movie_snapshot import MovieMediaInfo
from radarr.sync import movies, utils as radarr
from sonarr.info import get_sonarr_info
from sonarr.series_snapshot import Series
from sonarr.sync import series, utils as sonarr
from tests.bazarr.test_arr_typed_decoding import make_response
from tests.bazarr.test_sync_lookups import configure_movies
from tests.helpers.arr_fixtures import series_payload, series_record
from utilities.arr_sync_lookups import ArrSyncLookups
from utilities.json_types import JsonValue


@pytest.mark.parametrize('kind', ['radarr', 'sonarr'])
@pytest.mark.parametrize('legacy', [False, True])
def test_profile_boundary_filters_invalid_rows_before_indexing(kind: str, legacy: bool,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    module = radarr if kind == 'radarr' else sonarr
    info = get_radarr_info if kind == 'radarr' else get_sonarr_info
    monkeypatch.setattr(info, 'is_legacy', lambda: legacy)
    monkeypatch.setattr(info, 'version', lambda: '3.0.0')
    monkeypatch.setattr(module, f'url_api_{kind}', lambda: 'http://example.invalid/api/v3/')
    language: JsonValue = 'English' if legacy or kind == 'sonarr' else {'name': 'English'}
    key = 'name' if kind == 'sonarr' and not legacy else 'language'
    rows: list[JsonValue] = [{key: language, 'id': identifier} for identifier in (1, 1.0, True, '1')]
    rows.extend([{key: language, 'id': [1]}, {key: 123, 'id': 2}, {key: language}, None])
    response = make_response(json.dumps(rows).encode())
    monkeypatch.setattr(requests, 'get', Mock(return_value=response))

    profiles = module.get_profile_list()
    assert profiles is not None
    assert profiles == [(1, 'English'), (1.0, 'English'), (True, 'English'), ('1', 'English')]
    assert ArrSyncLookups([], [], profiles).audio_languages(1) == ['English'] * 3
    assert response.raw.closed


@pytest.mark.parametrize('kind', ['radarr', 'sonarr'])
@pytest.mark.parametrize('wire', [[], [{'id': 1, 'label': 'English'}], [{'id': True, 'label': 'bad'}],
                                  [{'id': 1, 'label': None}], {'error': 'unavailable'}, None])
def test_tag_boundary_checks_shape(kind: str, wire: JsonValue, monkeypatch: pytest.MonkeyPatch) -> None:
    module = radarr if kind == 'radarr' else sonarr
    monkeypatch.setattr(module, f'url_api_{kind}', lambda: 'http://example.invalid/api/v3/')
    response = make_response(json.dumps(wire).encode())
    monkeypatch.setattr(requests, 'get', Mock(return_value=response))
    assert module.get_tags() == (wire if wire in ([], [{'id': 1, 'label': 'English'}]) else None)
    assert response.raw.closed


@pytest.mark.parametrize('kind', ['radarr', 'sonarr'])
@pytest.mark.parametrize('metadata', ['tags', 'profiles'])
@pytest.mark.parametrize('mode', ['bulk', 'single'])
def test_unavailable_metadata_does_not_overwrite_existing_rows(kind: str, metadata: str, mode: str, memory_session: Session,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    if kind == 'radarr':
        _ = configure_movies(monkeypatch, memory_session, 1)
        monkeypatch.setattr(movies, 'get_tags' if metadata == 'tags' else 'get_profile_list', lambda: None)
        if mode == 'bulk':
            movies.update_movies(job_id=1)
        else:
            movies.update_one_movie(1, 'updated', defer_search=True)
        assert memory_session.execute(select(TableMovies.title)).scalar_one() == 'Movie'
    else:
        memory_session.add(TableShows(sonarrSeriesId=123, title='Existing', path='/series/path'))
        memory_session.commit()
        monkeypatch.setattr(series, 'database', memory_session)
        monkeypatch.setattr(series, 'check_sonarr_rootfolder', lambda: None)
        monkeypatch.setattr(series, 'get_series_from_sonarr_api', Mock(return_value=[series_record(series_payload())]))
        monkeypatch.setattr(series, 'get_tags', Mock(return_value=None if metadata == 'tags' else []))
        monkeypatch.setattr(series, 'get_profile_list', Mock(return_value=None if metadata == 'profiles' else []))
        monkeypatch.setattr(series, 'get_language_profiles', Mock(return_value=[]))
        monkeypatch.setattr(series, 'event_stream', Mock(return_value=None))
        monkeypatch.setattr(series, 'sync_episodes', Mock(return_value=None))
        monkeypatch.setattr(get_sonarr_info, 'is_legacy', lambda: False)
        monkeypatch.setattr('app.config.settings.general.serie_default_enabled', False)
        monkeypatch.setattr('app.config.settings.sonarr.sync_only_monitored_series', False)
        monkeypatch.setattr(series, 'jobs_queue', SimpleNamespace(
            get_job=Mock(return_value=SimpleNamespace(cancel_event=Event())),
            update_job_progress=Mock(return_value=None), update_job_name=Mock(return_value=None)))
        if mode == 'bulk':
            series.update_series(job_id=1)
        else:
            series.update_one_series(123, 'updated', sync_episodes_after_update=False)
        assert memory_session.execute(select(TableShows.title)).scalar_one() == 'Existing'


@pytest.mark.parametrize('stored', ['True', 'False', None, 'unknown'])
def test_series_monitored_state_is_normalized_before_filtering(stored: str | None, memory_session: Session,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    memory_session.add(TableShows(sonarrSeriesId=123, title='Existing', path='/series/path'))
    memory_session.flush()
    _ = memory_session.execute(update(TableShows).values(monitored=stored))
    memory_session.commit()
    record = series_record(series_payload())
    record.monitored = False
    updated: list[int] = []
    monkeypatch.setattr(series, 'database', memory_session)
    monkeypatch.setattr(series, 'check_sonarr_rootfolder', lambda: None)
    monkeypatch.setattr(series, 'get_series_from_sonarr_api', Mock(return_value=[record]))
    monkeypatch.setattr(series, 'get_tags', Mock(return_value=[]))
    monkeypatch.setattr(series, 'get_profile_list', Mock(return_value=[]))
    monkeypatch.setattr(series, 'get_language_profiles', Mock(return_value=[]))

    def record_update(identifier: int, **_kwargs: str | bool | Series | ArrSyncLookups) -> None:
        updated.append(identifier)

    monkeypatch.setattr(series, 'update_one_series', record_update)
    monkeypatch.setattr(series, 'sync_episodes', Mock(return_value=None))
    monkeypatch.setattr('app.config.settings.sonarr.sync_only_monitored_series', True)
    monkeypatch.setattr(series, 'jobs_queue', SimpleNamespace(
        get_job=Mock(return_value=SimpleNamespace(cancel_event=Event())),
        update_job_progress=Mock(return_value=None),
        update_job_name=Mock(return_value=None)))
    series.update_series(job_id=1)
    assert updated == ([] if stored == 'False' else [123])
    assert memory_session.execute(select(TableShows.sonarrSeriesId)).scalars().all() == [123]


def test_unavailable_profiles_abort_before_processing_any_movie(memory_session: Session,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    records = configure_movies(monkeypatch, memory_session, 2)
    assert records[0].movieFile is not None
    records[0].movieFile.mediaInfo = MovieMediaInfo(audioLanguages='English')
    monkeypatch.setattr(get_radarr_info, 'is_legacy', lambda: True)
    monkeypatch.setattr(movies, 'get_profile_list', lambda: None)

    movies.update_movies(job_id=1)

    assert memory_session.execute(select(TableMovies.title).order_by(TableMovies.radarrId)).scalars().all() == [
        'Movie', 'Movie']
