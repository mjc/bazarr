import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from weakref import ref
from unittest.mock import Mock

import pytest
import requests

from app.database import TableEpisodes, TableMovies, TableShows
from constants import HEADERS
from radarr import info as radarr_info
from radarr import filesystem as radarr_filesystem, notify as radarr_notify, rootfolder as radarr_rootfolder
from radarr.sync import movies
from sonarr import info as sonarr_info
from sonarr import filesystem as sonarr_filesystem, notify as sonarr_notify, rootfolder as sonarr_rootfolder
from sonarr.sync import episodes, series
from tests.helpers.arr_fixtures import (episode_payload, movie_payload, series_payload,
                                      sonarr_connection, radarr_connection)
from utilities import arr
from utilities.json_types import JsonValue


ARR_ENDPOINTS = {'sonarr': (sonarr_filesystem, sonarr_notify, sonarr_rootfolder),
                 'radarr': (radarr_filesystem, radarr_notify, radarr_rootfolder)}


@pytest.fixture
def http_transport(monkeypatch):
    sessions = []
    closed_sessions = []
    responses = []
    closed_responses = []
    calls = []
    payloads = {}

    class Response(requests.Response):
        def close(self) -> None:
            closed_responses.append(id(self))
            super().close()

    class Session(requests.Session):
        def __init__(self) -> None:
            super().__init__()
            sessions.append(ref(self))

        def close(self) -> None:
            closed_sessions.append(id(self))
            super().close()

        def get(self, url, **kwargs):
            calls.append((id(self), url, kwargs, dict(self.cookies)))
            parsed = urlsplit(url)
            payload = payloads[parsed.path]
            if callable(payload):
                payload = payload(parsed)
            if isinstance(payload, Exception):
                raise payload
            response = Response()
            response.status_code = 200
            response.encoding = 'utf-8'
            response._content = json.dumps(payload).encode()
            response._content_consumed = True
            responses.append(ref(response))
            self.cookies.set('arr-response', parsed.path)
            return response

        def post(self, url, **kwargs):
            return self.get(url, **kwargs)

    monkeypatch.setattr(arr.requests, 'Session', Session)
    return SimpleNamespace(sessions=sessions, closed_sessions=closed_sessions, responses=responses,
                           closed_responses=closed_responses, calls=calls, payloads=payloads)


def test_borrowed_session_reuses_transport_and_discards_cookies(http_transport):
    http_transport.payloads['/items'] = {'Items': []}
    with arr.arr_session() as session:
        for _ in range(3):
            assert arr.request_json('http://arr/items', 17, session=session) == {'Items': []}
            assert not session.cookies
            assert not http_transport.closed_sessions
            assert all(response() is None for response in http_transport.responses)
    assert len(http_transport.sessions) == 1
    assert len(http_transport.closed_sessions) == 1
    assert len(http_transport.closed_responses) == 3
    assert all(not cookies for _, _, _, cookies in http_transport.calls)
    assert all(kwargs == {'timeout': 17, 'verify': False, 'headers': HEADERS, 'stream': True}
               for _, _, kwargs, _ in http_transport.calls)


@pytest.mark.parametrize('failure', [requests.Timeout(), requests.ConnectionError(), RuntimeError('broken')])
def test_owned_session_closes_after_request_failure(http_transport, failure):
    http_transport.payloads['/items'] = failure
    with pytest.raises(type(failure)):
        arr.request_json('http://arr/items', 30)
    assert len(http_transport.closed_sessions) == 1
    assert not http_transport.closed_responses


@pytest.mark.parametrize('failure', ['http', 'json'])
def test_response_and_owner_close_after_response_failure(http_transport, monkeypatch, failure):
    http_transport.payloads['/items'] = []
    response = requests.Response()
    response.status_code = 503 if failure == 'http' else 200
    response._content = b'not json'
    response._content_consumed = True
    close = Mock()
    monkeypatch.setattr(response, 'close', close)
    with arr.arr_session() as session:
        monkeypatch.setattr(session, 'get', lambda *args, **kwargs: response)
        error = requests.HTTPError if failure == 'http' else requests.JSONDecodeError
        with pytest.raises(error):
            arr.request_json('http://arr/items', 30, session=session)
        close.assert_called_once()
        assert not http_transport.closed_sessions
    assert len(http_transport.closed_sessions) == 1


def test_status_fallback_borrows_one_session_and_releases_responses(http_transport):
    http_transport.payloads.update({'/api/system/status': {'error': 'not found'},
                                    '/api/v3/system/status': {'version': '4.0.9.2421'}})
    assert arr.discover_arr_version('http://arr', 'key', 30) == '4.0.9.2421'
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert len(http_transport.calls) == 2
    assert all(response() is None for response in http_transport.responses)
    assert all(not cookies for _, _, _, cookies in http_transport.calls)


def test_borrowed_scope_failure_clears_cookies_without_closing_owner(http_transport):
    with arr.arr_session() as session:
        session.cookies.set('before-failure', 'discard')
        with pytest.raises(RuntimeError, match='broken'):
            with arr.arr_session(session):
                raise RuntimeError('broken')
        assert not session.cookies
        assert not http_transport.closed_sessions
    assert len(http_transport.closed_sessions) == 1


def test_concurrent_operations_own_different_sessions(http_transport):
    gate = Barrier(2)

    def run() -> int:
        with arr.arr_session() as session:
            gate.wait(timeout=5)
            session.cookies.set('owner', str(id(session)))
            gate.wait(timeout=5)
            assert session.cookies.get('owner') == str(id(session))
            return id(session)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run)
        second = executor.submit(run)
        assert first.result() != second.result()
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 2
    assert all(session() is None for session in http_transport.sessions)


@pytest.fixture
def configured_arr(monkeypatch, http_transport, memory_session):
    for name, info in (('sonarr', sonarr_info), ('radarr', radarr_info)):
        monkeypatch.setattr(info.settings.general, f'use_{name}', True)
        configuration = getattr(info.settings, name)
        for key, value in {'ip': 'arr', 'port': '', 'base_url': '', 'ssl': False,
                           'apikey': 'key', 'http_timeout': 30}.items():
            monkeypatch.setattr(configuration, key, value)
        monkeypatch.setattr(info, 'region', info.make_region().configure('dogpile.cache.memory'))
        rootfolder = ARR_ENDPOINTS[name][2]
        monkeypatch.setattr(rootfolder, 'database', memory_session)
    for module in (series, episodes, movies):
        monkeypatch.setattr(module, 'database', memory_session)
        monkeypatch.setattr(module, 'event_stream', Mock())
    monkeypatch.setattr(series.gc, 'collect', lambda: None)
    monkeypatch.setattr(episodes, '_is_there_missing_subtitles', lambda **kwargs: False)
    monkeypatch.setattr(movies, '_is_there_missing_subtitles', lambda *args: False)
    for name in ('serie_default_enabled', 'movie_default_enabled', 'parse_embedded_audio_track',
                 'serie_tag_enabled', 'movie_tag_enabled', 'enable_strm_support'):
        monkeypatch.setattr(series.settings.general, name, False)
    monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_series', False)
    monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_episodes', False)
    monkeypatch.setattr(movies.settings.radarr, 'sync_only_monitored_movies', False)
    job = SimpleNamespace(cancel_event=Event())
    queue = SimpleNamespace(get_job=lambda **kwargs: job, update_job_progress=Mock(), update_job_name=Mock())
    monkeypatch.setattr(series, 'jobs_queue', queue)
    monkeypatch.setattr(movies, 'jobs_queue', queue)
    http_transport.payloads.update({'/api/system/status': {'version': '3.0.0.1'},
                                    '/api/v3/rootfolder': [], '/api/v3/languageprofile': [],
                                    '/api/v3/qualityprofile': [], '/api/v3/tag': []})

    def indexing(media_id: int) -> None:
        assert all(response() is None for response in http_transport.responses)

    monkeypatch.setattr(episodes, 'store_subtitles', indexing)
    monkeypatch.setattr(movies, 'store_subtitles_movie', indexing)
    return memory_session, job


def test_bulk_series_and_nested_episode_fetches_share_one_owner(configured_arr, http_transport):
    database, _ = configured_arr
    second = series_payload(8)
    second['path'] = '/series/8'
    http_transport.payloads['/api/v3/series/'] = [series_payload(7), second]

    def episode_list(parsed) -> JsonValue:
        series_id = int(parse_qs(parsed.query)['seriesId'][0])
        payload = episode_payload(series_id, series_id * 100)
        payload['episodeFileId'] = series_id * 10
        del payload['episodeFile']
        return [payload]

    def episode_files(parsed) -> JsonValue:
        series_id = int(parse_qs(parsed.query)['seriesId'][0])
        file = episode_payload(series_id, series_id * 100)['episodeFile']
        assert file is not None
        file['id'] = series_id * 10
        file['path'] = f'/series/{series_id}/episode.mkv'
        return [file]

    http_transport.payloads.update({'/api/v3/episode': episode_list, '/api/v3/episodeFile': episode_files})
    series.update_series(job_id=1)

    assert database.get(TableShows, 7) is not None
    assert database.get(TableShows, 8) is not None
    assert database.get(TableEpisodes, 700) is not None
    assert database.get(TableEpisodes, 800) is not None
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert len(http_transport.calls) == 9
    assert len({session_id for session_id, _, _, _ in http_transport.calls}) == 1
    assert all(not cookies for _, _, _, cookies in http_transport.calls)
    assert all(session() is None for session in http_transport.sessions)


@pytest.mark.parametrize('single', [False, True])
def test_movie_fetches_share_owner_and_release_it_before_indexing(configured_arr, http_transport, monkeypatch, single):
    database, _ = configured_arr
    movie = movie_payload()
    http_transport.payloads['/api/v3/movie/1' if single else '/api/v3/movie'] = movie if single else [movie]

    def indexing(movie_id: int) -> None:
        assert movie_id == 1
        assert len(http_transport.closed_sessions) == 1
        assert all(response() is None for response in http_transport.responses)

    monkeypatch.setattr(movies, 'store_subtitles_movie', indexing)
    if single:
        movies.update_one_movie(1, 'added', defer_search=True)
    else:
        movies.update_movies(job_id=1)
    assert database.get(TableMovies, 1) is not None
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert len({session_id for session_id, _, _, _ in http_transport.calls}) == 1


@pytest.mark.parametrize('kind', ['series', 'movie'])
def test_bulk_enqueue_creates_no_transport_or_extra_frame_locals(monkeypatch, http_transport, kind):
    module = series if kind == 'series' else movies
    function = module.update_series if kind == 'series' else module.update_movies
    captured = []

    def enqueue(*args, **kwargs) -> None:
        frame = inspect.currentframe()
        assert frame is not None and frame.f_back is not None
        caller = frame.f_back
        captured.append(inspect.signature(function).bind(**caller.f_locals).arguments)
        del caller, frame

    monkeypatch.setattr(module.jobs_queue, 'add_job_from_function', enqueue)
    function()
    assert captured == [{'job_id': None, 'wait_for_completion': False}]
    assert not http_transport.sessions


def test_canceled_series_operation_closes_owner(configured_arr, http_transport):
    _, job = configured_arr
    http_transport.payloads['/api/v3/series/'] = [series_payload(7)]
    job.cancel_event.set()
    with pytest.raises(series.JobCanceled):
        series.update_series(job_id=1)
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert all(response() is None for response in http_transport.responses)


@pytest.mark.parametrize('kind', ['episode', 'series'])
def test_standalone_sonarr_operation_owns_its_nested_fetches(configured_arr, http_transport, kind):
    database, _ = configured_arr
    database.add(TableShows(sonarrSeriesId=7, title='Old', path='/show'))
    database.flush()
    episode = episode_payload()
    file = episode['episodeFile']
    assert file is not None
    del episode['episodeFile']
    http_transport.payloads.update({'/api/v3/series/7': series_payload(7),
                                    '/api/v3/episode/101': episode,
                                    '/api/v3/episode': [episode], '/api/v3/episodeFile/11': file,
                                    '/api/v3/episodeFile': [file]})
    if kind == 'episode':
        episodes.sync_one_episode(101, defer_search=True)
    else:
        series.update_one_series(7, 'updated')
    assert database.get(TableEpisodes, 101) is not None
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert len({session_id for session_id, _, _, _ in http_transport.calls}) == 1
    assert all(response() is None for response in http_transport.responses)


@pytest.mark.parametrize('kind', ['series', 'movie'])
def test_invalid_remote_catalogue_closes_transport_without_changing_rows(configured_arr, http_transport, kind):
    database, _ = configured_arr
    row = (TableShows(sonarrSeriesId=7, title='Keep', path='/show') if kind == 'series'
           else TableMovies(radarrId=1, tmdbId='1', title='Keep', path='/movie'))
    database.add(row)
    database.flush()
    http_transport.payloads['/api/v3/series/' if kind == 'series' else '/api/v3/movie'] = None
    if kind == 'series':
        series.update_series(job_id=1)
    else:
        movies.update_movies(job_id=1)
    database.expire_all()
    assert row.title == 'Keep'
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert all(response() is None for response in http_transport.responses)


@pytest.mark.parametrize('info', [sonarr_info, radarr_info])
def test_next_operation_reads_new_settings_without_mutating_old_connection(configured_arr, http_transport, monkeypatch, info):
    name = 'sonarr' if info is sonarr_info else 'radarr'
    connect = getattr(info, f'get_{name}_connection')
    with arr.arr_session() as session:
        before = connect(session=session)
    configuration = getattr(info.settings, name)
    monkeypatch.setattr(configuration, 'ip', 'other-arr')
    monkeypatch.setattr(configuration, 'apikey', 'new-key')
    with arr.arr_session() as session:
        after = connect(session=session)
    assert before is not None and after is not None
    assert before.api_url == 'http://arr/api/v3/' and before.apikey == 'key'
    assert after.api_url == 'http://other-arr/api/v3/' and after.apikey == 'new-key'
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 2
    assert len(http_transport.calls) == 2


@pytest.mark.parametrize('kind', ['series', 'movie'])
def test_unknown_version_releases_owner_before_any_catalogue_fetch(configured_arr, http_transport, kind):
    http_transport.payloads.update({'/api/system/status': None, '/api/v3/system/status': None})
    if kind == 'series':
        series.update_series(job_id=1)
    else:
        movies.update_movies(job_id=1)
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    assert [urlsplit(url).path for _, url, _, _ in http_transport.calls] == [
        '/api/system/status', '/api/v3/system/status']


@pytest.mark.parametrize('app', ['sonarr', 'radarr'])
def test_filesystem_borrows_transport_and_releases_response(configured_arr, http_transport, app):
    module = ARR_ENDPOINTS[app][0]
    http_transport.payloads['/api/v3/filesystem'] = {'directories': []}
    with arr.arr_session() as session:
        assert getattr(module, f'browse_{app}_filesystem')('/media', session=session) == {'directories': []}
        assert not http_transport.closed_sessions
        assert all(response() is None for response in http_transport.responses)
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    _, url, _, _ = http_transport.calls[-1]
    assert parse_qs(urlsplit(url).query)['path'] == ['/media']


@pytest.mark.parametrize('app', ['sonarr', 'radarr'])
def test_rootfolder_preserves_existing_http_status_policy(configured_arr, http_transport, monkeypatch, app):
    module = ARR_ENDPOINTS[app][2]
    connection = sonarr_connection() if app == 'sonarr' else radarr_connection()
    response = requests.Response()
    response.status_code = 503
    response._content = b'[]'
    response._content_consumed = True
    with arr.arr_session() as session:
        monkeypatch.setattr(session, 'get', lambda *args, **kwargs: response)
        assert getattr(module, f'get_{app}_rootfolder')(connection=connection, session=session) is None
        assert not http_transport.closed_sessions
    assert len(http_transport.closed_sessions) == 1


@pytest.mark.parametrize('app', ['sonarr', 'radarr'])
def test_notify_borrows_transport_and_preserves_command_shape(configured_arr, http_transport, app):
    module = ARR_ENDPOINTS[app][1]
    http_transport.payloads['/api/v3/command'] = {}
    with arr.arr_session() as session:
        getattr(module, f'notify_{app}')(7, session=session)
        assert not http_transport.closed_sessions
    assert len(http_transport.sessions) == len(http_transport.closed_sessions) == 1
    _, _, kwargs, _ = http_transport.calls[-1]
    assert kwargs['json'] == ({'name': 'RescanSeries', 'seriesId': 7} if app == 'sonarr'
                              else {'name': 'RescanMovie', 'movieId': 7})


@pytest.mark.parametrize('service', ['sonarr', 'radarr'])
@pytest.mark.parametrize('kind', ['filesystem', 'rootfolder'])
def test_arr_readers_preserve_json_failure_and_close_response(
    configured_arr, http_transport, monkeypatch: pytest.MonkeyPatch, service: str, kind: str,
) -> None:
    module = ARR_ENDPOINTS[service][0 if kind == 'filesystem' else 2]
    monkeypatch.setattr(module, f'url_api_{service}', lambda **kwargs: 'http://arr/api/v3/')
    response = requests.Response()
    response.status_code = 200
    response.encoding = 'utf-8'
    response._content = b'not json'
    response._content_consumed = True
    close = Mock()
    monkeypatch.setattr(response, 'close', close)
    with arr.arr_session() as session:
        monkeypatch.setattr(session, 'get', lambda *args, **kwargs: response)
        with pytest.raises(requests.JSONDecodeError):
            if kind == 'filesystem':
                getattr(module, f'browse_{service}_filesystem')('/media', session=session)
            else:
                connection = sonarr_connection() if service == 'sonarr' else radarr_connection()
                getattr(module, f'get_{service}_rootfolder')(connection=connection, session=session)
        close.assert_called_once()
        assert not http_transport.closed_sessions
