from collections.abc import Iterable
from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask.testing import FlaskClient
from flask_restx import marshal, marshalling
from sqlalchemy.orm import Session
from utilities.video_analyzer import SyncReferences

from api import response_data
from api.formatting import ResponseFormatter
from api.response_types import BlacklistRow, EpisodeRow, HistoryRow, MovieRow, SeriesRow
from app import database as app_database
from languages import get_languages


def unchanged_path(path: str | None) -> str | None:
    return path


@pytest.fixture
def formatter() -> ResponseFormatter:
    return ResponseFormatter(False, None, '/bazarr', unchanged_path, unchanged_path)


def media_row() -> MovieRow:
    return {'alternativeTitles': "['Other title', None]", 'audio_language': None,
            'created_at_timestamp': datetime(2024, 1, 2, 3, 4, 5), 'tags': "['tag', None]",
            'monitored': 'True', 'missing_subtitles': ['en:forced'], 'profileId': 4,
            'radarrId': 2, 'path': '/movie.mkv', 'title': 'Movie', 'poster': '/poster.jpg'}


def history_row() -> HistoryRow:
    return {'id': 3, 'timestamp': datetime(2024, 1, 2, 3, 4, 5), 'language': None,
            'score': 0, 'score_out_of': None, 'tags': None, 'monitored': None,
            'video_path': '/video.mkv', 'path': '/video.mkv', 'subtitles_path': '/video.en.srt',
            'external_subtitles': '/video.en.srt', 'matched': None, 'not_matched': None,
            'title': 'Movie', 'radarrId': 2, 'sonarrSeriesId': 1, 'sonarrEpisodeId': 5,
            'episode_number': '1x1', 'episodeTitle': 'Episode', 'seriesTitle': 'Show', 'profileId': 4}


@pytest.mark.parametrize('kind', ['series', 'movies', 'episodes', 'episode-history', 'movie-history',
                                 'episode-blacklist', 'movie-blacklist'])
def test_formatter_returns_the_complete_wire_shape(formatter: ResponseFormatter, kind: str) -> None:
    if kind == 'series':
        row: SeriesRow = {'alternativeTitles': '[]', 'audio_language': None, 'tags': None,
                          'monitored': None, 'created_at_timestamp': datetime(2024, 1, 2, 3, 4, 5),
                          'ended': 'False', 'episodeFileCount': None, 'episodeMissingCount': None}
        result = formatter.series(row, episode_audio=None)
        model = import_module('api.series.series').Series.data_model
    elif kind == 'movies':
        result = formatter.movie(media_row(), subtitles=[])
        model = import_module('api.movies.movies').Movies.data_model
    elif kind == 'episodes':
        episode: EpisodeRow = {'audio_language': '', 'missing_subtitles': [], 'monitored': None, 'profileId': 4}
        result = formatter.episode(episode, subtitles=[])
        model = import_module('api.episodes.episodes').Episodes.get_response_model
    elif kind.endswith('history'):
        if kind.startswith('episode'):
            result = formatter.episode_history(history_row(), can_upgrade=False)
            model = import_module('api.episodes.history').EpisodesHistory.data_model
        else:
            result = formatter.movie_history(history_row(), can_upgrade=False)
            model = import_module('api.movies.history').MoviesHistory.data_model
    else:
        blacklisted: BlacklistRow = {'timestamp': datetime(2024, 1, 2, 3, 4, 5), 'language': ''}
        if kind.startswith('episode'):
            result = formatter.episode_blacklist(blacklisted)
            model = import_module('api.episodes.blacklist').EpisodesBlacklist.get_response_model
        else:
            result = formatter.movie_blacklist(blacklisted)
            model = import_module('api.movies.blacklist').MoviesBlacklist.get_response_model

    assert result == marshal(result, model)
    assert set(result) == set(model)
    assert 'profileId' not in result or kind in ('series', 'movies')
    assert 'video_path' not in result and 'external_subtitles' not in result and 'id' not in result


@pytest.mark.parametrize('value,expected', [(None, None), ('True', True), ('False', False),
                                           ('', False), ('1', True), ('0', False), ('on', True)])
def test_series_preserves_boolean_count_and_timestamp_conversion(
    formatter: ResponseFormatter, value: str | None, expected: bool | None,
) -> None:
    created = datetime(2024, 1, 2, 3, 4, 5, 123456)
    row: SeriesRow = {'alternativeTitles': None, 'audio_language': None, 'tags': None, 'monitored': None,
                      'ended': value, 'created_at_timestamp': created,
                      'episodeFileCount': None, 'episodeMissingCount': 7}
    result = formatter.series(row, episode_audio=None)
    assert result['ended'] is expected
    assert result['created_at_timestamp'] == str(created)
    assert result['episodeFileCount'] == 0 and result['episodeMissingCount'] == 7
    assert result['alternativeTitles'] == [] and result['tags'] == []
    row['created_at_timestamp'] = None
    assert formatter.series(row, episode_audio=None)['created_at_timestamp'] is None


@pytest.mark.parametrize('kind', ['episodes', 'movies'])
@pytest.mark.parametrize('unknown_first', [False, True])
def test_media_responses_preserve_unknown_subtitle_languages(
    kind: str, unknown_first: bool, api_client: FlaskClient, api_key: str,
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_session.add(app_database.TableShows(sonarrSeriesId=1, path='/shows/1', title='Show'))
    memory_session.flush()
    memory_session.add_all([
        app_database.TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, path='/shows/1/episode.mkv',
                                   title='Episode', season=1, episode=1),
        app_database.TableMovies(radarrId=1, path='/movie.mkv', title='Movie', tmdbId='1'),
    ])
    memory_session.flush()
    unknown_id, known_id = (1, 2) if unknown_first else (2, 1)
    if kind == 'episodes':
        memory_session.add_all([
            app_database.TableEpisodesSubtitles(id=unknown_id, sonarrEpisodeId=1, sonarrSeriesId=1,
                language='und', forced=False, hi=True, path=None, size=None, embedded_track_id=0),
            app_database.TableEpisodesSubtitles(id=known_id, sonarrEpisodeId=1, sonarrSeriesId=1,
                language='en', forced=True, hi=False, path='/movie.en.srt', size=123, embedded_track_id=None),
        ])
    else:
        memory_session.add_all([
            app_database.TableMoviesSubtitles(id=unknown_id, radarrId=1,
                language='und', forced=False, hi=True, path=None, size=None, embedded_track_id=0),
            app_database.TableMoviesSubtitles(id=known_id, radarrId=1,
                language='en', forced=True, hi=False, path='/movie.en.srt', size=123, embedded_track_id=None),
        ])
    memory_session.flush()
    monkeypatch.setattr(get_languages, 'languages_dict', [
        {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'},
    ])
    monkeypatch.setattr(app_database, 'database', memory_session)
    monkeypatch.setattr(response_data, 'database', memory_session)
    endpoint = import_module(f'api.{kind}.{kind}')
    monkeypatch.setattr(endpoint, 'database', memory_session)

    def display_formatter(profile_ids: Iterable[int | None]) -> ResponseFormatter:
        return ResponseFormatter(False, None, '', unchanged_path, unchanged_path)

    monkeypatch.setattr(endpoint, 'load_formatter', display_formatter)

    query = '?episodeid[]=1' if kind == 'episodes' else ''
    response = api_client.get(f'/api/{kind}{query}', headers={'X-API-KEY': api_key})

    assert response.status_code == 200
    assert response.get_json()['data'][0]['subtitles'] == [
        {'id': unknown_id, 'path': None, 'name': None, 'code2': 'und', 'code3': None,
         'forced': False, 'hi': True, 'file_size': None, 'embedded_track_id': 0},
        {'id': known_id, 'path': '/movie.en.srt', 'name': 'English', 'code2': 'en', 'code3': 'eng',
         'forced': True, 'hi': False, 'file_size': 123, 'embedded_track_id': None},
    ]


@pytest.mark.parametrize('path', ['/api/series', '/api/movies', '/api/episodes?episodeid[]=1',
                                 '/api/episodes/history', '/api/movies/history',
                                 '/api/episodes/blacklist', '/api/movies/blacklist', '/api/badges',
                                 '/api/system/tasks', '/api/system/backups', '/api/system/logs',
                                 '/api/providers', '/api/providers?history=true'])
def test_formatted_endpoints_do_not_marshal_again(
    path: str, api_client: FlaskClient, api_key: str, memory_session: Session,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    modules = ['api.series.series', 'api.movies.movies', 'api.episodes.episodes', 'api.episodes.history',
               'api.movies.history', 'api.episodes.blacklist', 'api.movies.blacklist', 'api.badges.badges',
               'api.providers.providers']
    for name in modules:
        monkeypatch.setattr(import_module(name), 'database', memory_session)
    monkeypatch.setattr(app_database, 'database', memory_session)
    monkeypatch.setattr(response_data, 'database', memory_session)
    badges = import_module('api.badges.badges')
    for name in ('get_throttled_providers', 'get_health_issues', 'get_all_announcements'):
        monkeypatch.setattr(badges, name, lambda: [])
    providers = import_module('api.providers.providers')
    monkeypatch.setattr(providers, 'list_throttled_providers', lambda: [['z', None, 'now'], ['a', 'Timeout', 'later']])
    tasks = import_module('api.system.tasks')
    task = {'name': 'Sync', 'interval': 'every day', 'next_run_in': 'soon', 'next_run_time': 'soon',
            'job_id': 'sync', 'job_running': False}
    monkeypatch.setattr(tasks.scheduler, 'get_task_list', lambda: [task])
    backups = import_module('api.system.backups')
    backup = {'filename': 'backup.zip', 'date': 'Jan 02 2024', 'size': '1.0 KB', 'type': 'backup'}
    monkeypatch.setattr(backups, 'get_backup_files', lambda fullpath: [backup])
    logs = import_module('api.system.logs')
    log_file = tmp_path / 'bazarr.log'
    log_file.write_text('orphan exception|\n2024-01-02 03:04:05|INFO|component|message|\n', encoding='utf-8')
    monkeypatch.setattr(logs, 'get_log_file_path', lambda: str(log_file))
    monkeypatch.setattr(logs, 'settings', SimpleNamespace(log=SimpleNamespace(
        include_filter='', exclude_filter='', ignore_case=False, use_regex=False)))

    monkeypatch.setattr(marshalling, '_marshal', Mock(
        side_effect=AssertionError('An already formatted response was marshalled again')))
    response = api_client.get(path, headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    body = response.get_json()
    if path == '/api/system/tasks':
        assert body == {'data': [task]}
    elif path == '/api/system/backups':
        assert body == {'data': [backup]}
    elif path == '/api/system/logs':
        assert body == {'data': [
            {'timestamp': '2024-01-02 03:04:05', 'type': 'INFO', 'message': 'message', 'exception': None},
            {'timestamp': None, 'type': 'ERROR', 'message': 'See exception', 'exception': 'orphan exception'},
        ]}
    elif path == '/api/providers':
        assert body == {'data': [{'name': 'a', 'status': 'Timeout', 'retry': 'later'},
                                 {'name': 'z', 'status': 'Good', 'retry': '-'}]}
    elif path != '/api/badges':
        assert body['data'] == []


def test_provider_history_keeps_unique_names_and_excludes_manual_entries(
    api_client: FlaskClient, api_key: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.database import TableHistory, TableHistoryMovie

    for model in (TableHistory, TableHistoryMovie):
        for provider in ('z', 'a', 'z', 'manual', None):
            memory_session.add(model(action=1, description="Downloaded", language="en", provider=provider))
    memory_session.flush()
    monkeypatch.setattr(import_module('api.providers.providers'), 'database', memory_session)
    response = api_client.get('/api/providers?history=true', headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == {'data': [{'name': 'a', 'status': 'History', 'retry': '-'},
                                           {'name': 'z', 'status': 'History', 'retry': '-'}]}


@pytest.mark.parametrize('guessed,season,episode,language', [
    ({}, 0, 0, None),
    ({'season': 2, 'episode': 3, 'subtitle_language': 'en'}, 2, 3, 'en'),
    ({'season': [2, 3], 'episode': [4, 5]}, 2, 4, None),
    ({'season': [], 'episode': []}, 0, 0, None),
    ({'season': ['2'], 'episode': ['3']}, 2, 3, None),
    ({'season': [None], 'episode': [None]}, None, None, None),
    ({'season': True, 'episode': False}, 1, 0, None),
    ({'season': 'invalid', 'episode': 'invalid'}, 0, 0, None),
])
def test_filename_info_preserves_defaults_and_integer_conversion(
    guessed: dict[str, str | int | list[str | int | None] | None], season: int | None,
    episode: int | None, language: str | None, api_client: FlaskClient, api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = import_module('api.subtitles.subtitles_info')
    monkeypatch.setattr(module, 'guessit', lambda name, options: guessed)
    monkeypatch.setattr(marshalling, '_marshal', Mock(side_effect=AssertionError('Redundant marshalling')))
    response = api_client.get('/api/subtitles/info', query_string={'filenames[]': 'show.srt'},
                              headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == {'data': [{'filename': 'show.srt', 'subtitle_language': language,
                                            'season': season, 'episode': episode}]}


@pytest.mark.parametrize('prerelease,expected', [(True, True), (False, False), (None, None),
                                               ('false', False), ('true', True), (1, True), (0, False)])
def test_release_list_returns_formatted_values_without_marshalling(
    prerelease: bool | str | int | None, expected: bool | None, api_client: FlaskClient, api_key: str,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import json

    module = import_module('api.system.releases')
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'releases.txt').write_text(json.dumps([{'name': 'v1.2.3', 'date': '2024-01-02T03:04:05Z',
                                                    'body': 'Heading\n- One\n- Two', 'prerelease': prerelease}]))
    monkeypatch.setattr(module, 'args', SimpleNamespace(config_dir=str(tmp_path)))
    monkeypatch.setattr(module, 'settings', SimpleNamespace(general=SimpleNamespace(branch='development')))
    monkeypatch.setenv('BAZARR_VERSION', '1.2.3')
    monkeypatch.setattr(marshalling, '_marshal', Mock(side_effect=AssertionError('Redundant marshalling')))
    response = api_client.get('/api/system/releases', headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == {'data': [{'name': 'v1.2.3', 'date': '2024-01-02', 'body': ['One', 'Two'],
                                            'prerelease': expected, 'current': True}]}


@pytest.mark.parametrize('contents', [None, 'invalid JSON', '[]'])
def test_release_list_keeps_the_empty_cache_fallback(
    contents: str | None,
    api_client: FlaskClient, api_key: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    module = import_module('api.system.releases')
    if contents is not None:
        (tmp_path / 'config').mkdir()
        (tmp_path / 'config' / 'releases.txt').write_text(contents)
    monkeypatch.setattr(module, 'args', SimpleNamespace(config_dir=str(tmp_path)))
    response = api_client.get('/api/system/releases', headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == {'data': []}


def test_release_list_keeps_partial_cache_records_in_the_fallback(
    api_client: FlaskClient, api_key: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import json

    module = import_module('api.system.releases')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'releases.txt').write_text(json.dumps([
        {'name': 'v1.2.3', 'date': '2024-01-02T00:00:00Z', 'body': 'Heading\n- One', 'prerelease': True},
        {'name': 'v1.2.2', 'date': '2024-01-01T00:00:00Z', 'body': None, 'prerelease': False},
    ]))
    monkeypatch.setattr(module, 'args', SimpleNamespace(config_dir=str(tmp_path)))
    monkeypatch.setattr(module, 'settings', SimpleNamespace(general=SimpleNamespace(branch='development')))
    monkeypatch.setenv('BAZARR_VERSION', '1.2.3')
    response = api_client.get('/api/system/releases', headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == {'data': [
        {'name': 'v1.2.3', 'date': '2024-01-02', 'body': ['One'], 'prerelease': True, 'current': True},
        {'name': 'v1.2.2', 'date': '2024-01-01T00:00:00Z', 'body': None, 'prerelease': False, 'current': None},
    ]}


@pytest.mark.parametrize('empty', [True, False])
def test_subtitle_sync_references_return_the_same_fields_without_marshalling(
    empty: bool, api_client: FlaskClient, api_key: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = import_module('api.subtitles.subtitles')
    references: SyncReferences = {
        'audio_tracks': [] if empty else [{'stream': 'a:0', 'name': 'Audio', 'language': None}],
        'embedded_subtitles_tracks': [] if empty else [
            {'stream': 's:1', 'name': 'English', 'language': 'English',
             'forced': False, 'hearing_impaired': True}],
        'external_subtitles_tracks': [] if empty else [
            {'name': 'show.en.srt', 'path': '/show.en.srt', 'language': None,
             'forced': True, 'hearing_impaired': False}],
    }
    expected = marshal(references, module.Subtitles.get_response_model, envelope='data')
    producer = Mock(return_value=references)
    monkeypatch.setattr(module, 'subtitles_sync_references', producer)
    monkeypatch.setattr(marshalling, '_marshal', Mock(side_effect=AssertionError('Redundant marshalling')))
    response = api_client.get('/api/subtitles', query_string={'subtitlesPath': '/show.en.srt',
                                                            'sonarrEpisodeId': 7},
                              headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == expected
    producer.assert_called_once_with(subtitles_path='/show.en.srt', sonarr_episode_id=7, radarr_movie_id=None)
