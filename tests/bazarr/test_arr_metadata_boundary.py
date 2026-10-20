import json
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import TableMovies, TableShows
from radarr.sync import movies, utils as radarr
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
    info = radarr.get_radarr_info if kind == 'radarr' else sonarr.get_sonarr_info
    monkeypatch.setattr(info, 'is_legacy', lambda: legacy)
    monkeypatch.setattr(info, 'version', lambda: '3.0.0')
    monkeypatch.setattr(module, f'url_api_{kind}', lambda: 'http://example.invalid/api/v3/')
    language: JsonValue = 'English' if legacy or kind == 'sonarr' else {'name': 'English'}
    key = 'name' if kind == 'sonarr' and not legacy else 'language'
    rows: list[JsonValue] = [{key: language, 'id': identifier} for identifier in (1, 1.0, True, '1')]
    rows += [{key: language, 'id': [1]}, {key: 123, 'id': 2}, {key: language}, None]
    response = make_response(json.dumps(rows).encode())
    monkeypatch.setattr(module.requests, 'get', lambda *args, **kwargs: response)

    profiles = module.get_profile_list()
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
    monkeypatch.setattr(module.requests, 'get', lambda *args, **kwargs: response)
    assert module.get_tags() == (wire if wire in ([], [{'id': 1, 'label': 'English'}]) else None)
    assert response.raw.closed


@pytest.mark.parametrize('kind', ['radarr', 'sonarr'])
def test_unavailable_tags_do_not_overwrite_existing_rows(kind: str, memory_session: Session,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    if kind == 'radarr':
        configure_movies(monkeypatch, memory_session, 1)
        monkeypatch.setattr(movies, 'get_tags', lambda: None)
        movies.update_movies(job_id=1)
        assert memory_session.execute(select(TableMovies.title)).scalar_one() == 'Movie'
    else:
        memory_session.add(TableShows(sonarrSeriesId=123, title='Existing', path='/series/path'))
        memory_session.commit()
        monkeypatch.setattr(series, 'database', memory_session)
        monkeypatch.setattr(series, 'check_sonarr_rootfolder', lambda: None)
        monkeypatch.setattr(series, 'get_series_from_sonarr_api', lambda **kwargs: [series_record(series_payload())])
        monkeypatch.setattr(series, 'get_tags', lambda: None)
        monkeypatch.setattr(series, 'get_profile_list', lambda: [])
        monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_series', False)
        monkeypatch.setattr(series, 'jobs_queue', SimpleNamespace(
            get_job=lambda **kwargs: SimpleNamespace(cancel_event=Event())))
        series.update_series(job_id=1)
        assert memory_session.execute(select(TableShows.title)).scalar_one() == 'Existing'
