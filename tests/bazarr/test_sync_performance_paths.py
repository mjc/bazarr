from tests.helpers.arr_fixtures import episode_payload, episode_record, movie_payload, series_payload, series_record, stub_arr_api
# -*- coding: utf-8 -*-
import datetime
import os
from unittest.mock import Mock

from sqlalchemy.sql import Executable
from sonarr.episode_snapshot import Episode

from types import SimpleNamespace

import pytest


class _Result:
    def __init__(self, first_value=None, all_value=None):
        self._first_value = first_value
        self._all_value = [] if all_value is None else all_value

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._all_value)


def test_update_one_series_fetches_and_inserts_for_standalone_call(monkeypatch):
    from sonarr.sync import series as series_sync

    execute_calls = []

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            if len(execute_calls) == 1:
                return _Result(first_value=None)
            return _Result()

    events = []
    monkeypatch.setattr(series_sync, "database", _Database())
    monkeypatch.setattr(
        series_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                serie_default_enabled=False, serie_tag_enabled=False, parse_embedded_audio_track=False,
                remove_profile_tags=[]),
            sonarr=SimpleNamespace(apikey="sonarr-key"),
        ),
    )
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", Mock(return_value=[]))
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    stub_arr_api(monkeypatch, series_sync, "get_series_from_sonarr_api", Mock(return_value=series_payload()))
    monkeypatch.setattr(
        series_sync,
        "map_series",
        lambda *args, **kwargs: {"sonarrSeriesId": 123, "path": "/series/path"},
    )
    monkeypatch.setattr(series_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(series_sync.path_mappings, "path_replace", lambda path: path)

    series_sync.update_one_series(123, action="updated")

    assert len(execute_calls) == 2
    assert events == [{"type": "series", "action": "update", "payload": 123}]


def test_update_series_runs_one_explicit_episode_sync(monkeypatch):
    from sonarr.sync import series as series_sync

    update_calls = []
    episode_sync_calls = []

    class _Database:
        def execute(self, statement: Executable, parameters: dict[str, list[int]] | None = None) -> _Result:
            return _Result(all_value=[])

    class _MockEvent:
        def is_set(self):
            return False

    class _MockJob:
        def __init__(self):
            self.cancel_event = _MockEvent()

    monkeypatch.setattr(series_sync, "database", _Database())
    monkeypatch.setattr(
        series_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(debug=False),
            sonarr=SimpleNamespace(apikey="sonarr-key", sync_only_monitored_series=False),
        ),
    )
    monkeypatch.setattr(series_sync, "check_sonarr_rootfolder", lambda: None)
    stub_arr_api(monkeypatch, series_sync, "get_series_from_sonarr_api", lambda **kwargs: [series_payload()])
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", Mock(return_value=[]))
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        series_sync,
        "_sync_series",
        lambda *args, **kwargs: update_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(series_sync, "sync_episodes", lambda series_id: episode_sync_calls.append(series_id))
    monkeypatch.setattr(
        series_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            get_job=lambda *args, **kwargs: _MockJob(),
        ),
    )

    series_sync.update_series(job_id=1)

    assert episode_sync_calls == [123]
    assert len(update_calls) == 1
    assert 'refresh_episodes' not in update_calls[0][1]


def test_update_series_returns_early_when_sonarr_api_returns_none(monkeypatch):
    from sonarr.sync import series as series_sync

    execute_calls = []
    episode_sync_calls = []
    update_one_series_calls = []
    job_name_updates = []

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            return _Result(all_value=[])

    class _MockEvent:
        def is_set(self):
            return False

    class _MockJob:
        def __init__(self):
            self.cancel_event = _MockEvent()

    monkeypatch.setattr(series_sync, "database", _Database())
    monkeypatch.setattr(
        series_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(debug=False),
            sonarr=SimpleNamespace(
                apikey="sonarr-key",
                sync_only_monitored_series=False,
            ),
        ),
    )
    monkeypatch.setattr(series_sync, "check_sonarr_rootfolder", lambda: None)
    # The API helper swallows ConnectionError/Timeout errors and returns None.
    stub_arr_api(
        monkeypatch, series_sync,
        "get_series_from_sonarr_api",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", Mock(return_value=[]))
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        series_sync,
        "update_one_series",
        lambda *args, **kwargs: update_one_series_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        series_sync,
        "sync_episodes",
        lambda series_id: episode_sync_calls.append(series_id),
    )
    monkeypatch.setattr(
        series_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: job_name_updates.append(
                (args, kwargs)
            ),
            get_job=lambda *args, **kwargs: _MockJob(),
        ),
    )

    series_sync.update_series(job_id=1)

    assert execute_calls == []
    assert episode_sync_calls == []
    assert update_one_series_calls == []
    assert job_name_updates == [((), {"job_id": 1, "new_job_name": "Synced series with Sonarr"})]


def test_unchanged_series_skips_update_but_manual_call_syncs_episodes(monkeypatch):
    from sonarr.sync import series as series_sync

    from sonarr.sync.parser import map_series

    record = series_record(series_payload())
    existing_series = map_series(record, audio_language='[]', tags=[])
    execute_calls = []
    episode_sync_calls = []

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            return _Result(first_value=existing_series)

    monkeypatch.setattr(series_sync, "database", _Database())
    monkeypatch.setattr(
        series_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                serie_default_enabled=False, serie_tag_enabled=False, parse_embedded_audio_track=False,
                remove_profile_tags=[]),
            sonarr=SimpleNamespace(apikey="sonarr-key"),
        ),
    )
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", Mock(return_value=[]))
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(series_sync, 'get_series_from_sonarr_api', Mock(return_value=[record]))
    monkeypatch.setattr(series_sync, "sync_episodes", lambda series_id: episode_sync_calls.append(series_id))

    series_sync.update_one_series(
        123,
        action="updated",
        sync_episodes_after_update=True,
    )

    assert len(execute_calls) == 1
    assert episode_sync_calls == [123]


def test_episode_parser_uses_reported_size_before_filesystem_stat(monkeypatch):
    from constants import MINIMUM_VIDEO_SIZE
    from sonarr.sync import audio as audio_resolver
    from sonarr.sync import parser

    monkeypatch.setattr(
        os.path,
        "getsize",
        lambda path: pytest.fail("map_episode should not stat files when Sonarr size is already valid"),
    )
    monkeypatch.setattr(audio_resolver, "audio_language_from_name", lambda name: name)

    record = episode_record({
            "hasFile": True,
            "seriesId": 123,
            "id": 456,
            "title": "Episode",
            "seasonNumber": 1,
            "episodeNumber": 2,
            "monitored": True,
            "episodeFile": {
                "id": 789,
                "path": "/series/path/episode.mkv",
                "size": MINIMUM_VIDEO_SIZE + 1,
                "language": {"name": "English"},
                "mediaInfo": {},
                "quality": {"quality": {"name": "HDTV-1080p"}},
            },
    })
    assert record.episodeFile is not None
    parsed = parser.map_episode(record, record.episodeFile, audio_language=audio_resolver.episode_audio_language(
        record, record.episodeFile, False, series_languages={}))


    assert parsed is not None
    assert parsed["sonarrEpisodeId"] == 456
    assert parsed["file_size"] == MINIMUM_VIDEO_SIZE + 1


def test_update_movies_compares_against_matching_radarr_id(monkeypatch):
    from constants import MINIMUM_VIDEO_SIZE
    from radarr.sync import movies as movies_sync
    from radarr.sync.parser import map_movie
    from radarr.movie_snapshot import Movie
    from radarr.sync.utils import MOVIE_DECODER

    movie_rows = []
    for identifier, title, path in [(1, 'Movie 1', '/movies/one.mkv'), (2, 'Old Movie 2', '/movies/two.mkv')]:
        record = MOVIE_DECODER.complete_from_value(
            movie_payload(movie_id=identifier, title=title, path=path, file_id=identifier + 100,
                          size=MINIMUM_VIDEO_SIZE + 1))
        assert isinstance(record, Movie)
        record.normalize(legacy=False)
        assert record.movieFile is not None
        movie_rows.append(map_movie(record, record.movieFile, audio_language='[]', tags=[], profile_id=None))
    updated_movies = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=movie_rows)

    class _MockEvent:
        def is_set(self):
            return False

    class _MockJob:
        def __init__(self):
            self.cancel_event = _MockEvent()

    def _movie_parser(movie, **kwargs):
        return map_movie(movie, **kwargs)

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(
        movies_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                movie_default_enabled=False, movie_tag_enabled=False, parse_embedded_audio_track=False,
                remove_profile_tags=[], debug=False),
            radarr=SimpleNamespace(apikey="radarr-key", sync_only_monitored_movies=False),
        ),
    )
    monkeypatch.setattr(movies_sync, "check_radarr_rootfolder", lambda: None)
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", Mock(return_value=[]))
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(movies_sync, "map_movie", _movie_parser)
    monkeypatch.setattr(movies_sync, 'movie_audio_language', lambda *args, **kwargs: "[]")
    monkeypatch.setattr(movies_sync, "update_movie", lambda previous_file, movie: updated_movies.append(movie) or True)
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: None)
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            get_job=lambda *args, **kwargs: _MockJob(),
        ),
    )
    stub_arr_api(
        monkeypatch, movies_sync,
        "get_movies_from_radarr_api",
        lambda apikey_radarr: [
            movie_payload(movie_id=1, title='Movie 1', path='/movies/one.mkv', file_id=101, size=MINIMUM_VIDEO_SIZE + 1),
            movie_payload(movie_id=2, title='New Movie 2', path='/movies/two.mkv', file_id=102, size=MINIMUM_VIDEO_SIZE + 1),
        ],
    )

    movies_sync.update_movies(job_id=1)

    assert len(updated_movies) == 1
    assert updated_movies[0]['radarrId'] == 2
    assert updated_movies[0]['title'] == 'New Movie 2'
    assert updated_movies[0]['path'] == '/movies/two.mkv'


def test_get_providers_expired_throttle_cleanup_is_idempotent(monkeypatch):
    from app import get_providers as providers

    provider = "opensubtitlescom"
    providers.tp.clear()
    providers.tp[provider] = ("TooManyRequests", datetime.datetime.now(), "1 minute")

    removed_once = {"done": False}

    class _RacingThrottle(dict):
        def __delitem__(self, key):
            if not removed_once["done"]:
                removed_once["done"] = True
                super().__delitem__(key)
                raise KeyError(key)
            super().__delitem__(key)

    racing_tp = _RacingThrottle(providers.tp)
    monkeypatch.setattr(providers, "tp", racing_tp)
    monkeypatch.setattr(providers.provider_registry, "names", lambda: [provider])
    monkeypatch.setattr(providers, "settings", SimpleNamespace(general=SimpleNamespace(enabled_providers=[provider])))
    monkeypatch.setattr(providers, "set_throttled_providers", lambda data: None)

    assert providers.get_providers() == [provider]


@pytest.mark.parametrize('existing', [False, True])
def test_incomplete_episode_snapshot_skips_writes_and_keeps_existing_id(
    existing: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sonarr.sync import episodes as episode_sync

    reads: list[Executable] = []
    parsed: list[Episode] = []
    episode = episode_record(episode_payload())
    from sonarr.sync.parser import map_episode
    assert episode.episodeFile is not None
    row = map_episode(episode, episode.episodeFile, audio_language='[]')

    class Database:
        def execute(self, statement: Executable) -> _Result:
            assert str(statement).startswith('SELECT')
            reads.append(statement)
            return _Result(all_value=[row] if existing and len(reads) == 1 else [])

    monkeypatch.setattr(episode_sync, 'database', Database())
    monkeypatch.setattr(episode_sync, 'settings', SimpleNamespace(
        sonarr=SimpleNamespace(apikey='key', sync_only_monitored_series=False),
        general=SimpleNamespace(parse_embedded_audio_track=False),
    ))
    monkeypatch.setattr(episode_sync, 'get_episodes_from_sonarr_api', Mock(return_value=None))
    monkeypatch.setattr(episode_sync, 'map_episode', Mock(side_effect=pytest.fail.Exception('Snapshot rejected')))
    monkeypatch.setattr(episode_sync, 'delete_media_and_wanted_search_state',
                        Mock(side_effect=pytest.fail.Exception('A rejected parse must not delete an existing episode')))

    episode_sync.sync_episodes(7)

    assert parsed == []
    assert len(reads) == 1
