# -*- coding: utf-8 -*-
from datetime import datetime as _datetime
import sqlite3
from types import SimpleNamespace
import time

import pytest


class _Result:
    def __init__(self, first_value=None, all_value=None):
        self._first_value = first_value
        self._all_value = [] if all_value is None else all_value

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value


class _Column:
    def __init__(self, name):
        self.name = name


def _model(**values):
    return SimpleNamespace(
        __table__=SimpleNamespace(columns=[_Column(name) for name in values]),
        **values,
    )


def _job_queue():
    return SimpleNamespace(
        add_job_from_function=lambda *args, **kwargs: None,
        update_job_progress=lambda *args, **kwargs: None,
        update_job_name=lambda *args, **kwargs: None,
    )


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
            general=SimpleNamespace(serie_default_enabled=False),
            sonarr=SimpleNamespace(apikey="sonarr-key"),
        ),
    )
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", lambda: {})
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(series_sync, "get_series_from_sonarr_api", lambda **kwargs: [{"id": 123}])
    monkeypatch.setattr(
        series_sync,
        "seriesParser",
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
        def execute(self, statement):
            return _Result(all_value=[])

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
    monkeypatch.setattr(series_sync, "get_series_from_sonarr_api", lambda **kwargs: [{"id": 123, "title": "Series"}])
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", lambda: {})
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        series_sync,
        "update_one_series",
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
        ),
    )

    series_sync.update_series(job_id="job")

    assert episode_sync_calls == [123]
    assert len(update_calls) == 1
    assert update_calls[0][1]["sync_episodes_after_update"] is False


def test_unchanged_series_skips_update_but_manual_call_syncs_episodes(monkeypatch):
    from sonarr.sync import series as series_sync

    existing_series = _model(sonarrSeriesId=123, path="/series/path")
    execute_calls = []
    episode_sync_calls = []

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            return _Result(first_value=(existing_series,))

    monkeypatch.setattr(series_sync, "database", _Database())
    monkeypatch.setattr(
        series_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(serie_default_enabled=False),
            sonarr=SimpleNamespace(apikey="sonarr-key"),
        ),
    )
    monkeypatch.setattr(series_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(series_sync, "get_tags", lambda: {})
    monkeypatch.setattr(series_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        series_sync,
        "seriesParser",
        lambda *args, **kwargs: {"sonarrSeriesId": 123, "path": "/series/path"},
    )
    monkeypatch.setattr(series_sync, "sync_episodes", lambda series_id: episode_sync_calls.append(series_id))

    series_sync.update_one_series(
        123,
        action="updated",
        series_data=[{"id": 123}],
        sync_episodes_after_update=True,
    )

    assert len(execute_calls) == 1
    assert episode_sync_calls == [123]


def test_episode_parser_uses_reported_size_before_filesystem_stat(monkeypatch):
    from constants import MINIMUM_VIDEO_SIZE
    from sonarr.sync import parser

    monkeypatch.setattr(
        parser.os.path,
        "getsize",
        lambda path: pytest.fail("episodeParser should not stat files when Sonarr size is already valid"),
    )
    monkeypatch.setattr(parser, "audio_language_from_name", lambda name: name)

    parsed = parser.episodeParser(
        {
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
        },
        enable_strm_support=False,
        parse_embedded_audio_track=False,
    )

    assert parsed["sonarrEpisodeId"] == 456
    assert parsed["file_size"] == MINIMUM_VIDEO_SIZE + 1


def test_update_movies_compares_against_matching_radarr_id(monkeypatch):
    from constants import MINIMUM_VIDEO_SIZE
    from radarr.sync import movies as movies_sync

    movie_rows = [
        (_model(radarrId=1, title="Movie 1", path="/movies/one.mkv"),),
        (_model(radarrId=2, title="Old Movie 2", path="/movies/two.mkv"),),
    ]
    updated_movies = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=movie_rows)

    def _movie_parser(movie, **kwargs):
        return {
            "radarrId": movie["id"],
            "title": movie["title"],
            "path": movie["movieFile"]["path"],
        }

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(
        movies_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(movie_default_enabled=False, enable_strm_support=False, debug=False),
            radarr=SimpleNamespace(apikey="radarr-key", sync_only_monitored_movies=False),
        ),
    )
    monkeypatch.setattr(movies_sync, "check_radarr_rootfolder", lambda: None)
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(movies_sync, "movieParser", _movie_parser)
    monkeypatch.setattr(movies_sync, "update_movie", lambda movie: updated_movies.append(movie))
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: None)
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(
        movies_sync,
        "get_movies_from_radarr_api",
        lambda apikey_radarr: [
            {
                "id": 1,
                "title": "Movie 1",
                "hasFile": True,
                "monitored": True,
                "movieFile": {"path": "/movies/one.mkv", "size": MINIMUM_VIDEO_SIZE + 1},
            },
            {
                "id": 2,
                "title": "New Movie 2",
                "hasFile": True,
                "monitored": True,
                "movieFile": {"path": "/movies/two.mkv", "size": MINIMUM_VIDEO_SIZE + 1},
            },
        ],
    )

    movies_sync.update_movies(job_id="job")

    assert updated_movies == [{"radarrId": 2, "title": "New Movie 2", "path": "/movies/two.mkv"}]


def test_series_full_scan_skips_clean_unchanged_episode(monkeypatch):
    from subtitles.indexer import series as series_indexer

    signature = 'sig'
    episodes = [
        SimpleNamespace(
            path='/series/episode.mkv',
            episode_file_id=123,
            file_size=456,
            missing_subtitles='[]',
            subtitles_last_indexed_episode_file_id=123,
            subtitles_last_indexed_external_signature=signature,
            subtitles_last_indexed_file_size=456,
            subtitles_last_indexed_path='/series/episode.mkv',
            title='Series',
            season=1,
            episode=1,
            episodeTitle='Pilot',
            sonarrEpisodeId=101,
            has_indexed_subtitles=True,
        ),
        SimpleNamespace(
            path='/series/missing.mkv',
            episode_file_id=124,
            file_size=457,
            missing_subtitles='["en"]',
            subtitles_last_indexed_episode_file_id=124,
            subtitles_last_indexed_external_signature=signature,
            subtitles_last_indexed_file_size=457,
            subtitles_last_indexed_path='/series/missing.mkv',
            title='Series',
            season=1,
            episode=2,
            episodeTitle='Episode 2',
            sonarrEpisodeId=102,
            has_indexed_subtitles=True,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=episodes)

    monkeypatch.setattr(series_indexer, "database", _Database())
    monkeypatch.setattr(
        series_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(series_indexer.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(series_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(series_indexer, "_get_subtitles_scan_signature", lambda paths: signature)
    monkeypatch.setattr(series_indexer, "get_language_set", lambda: {"en"})
    monkeypatch.setattr(
        series_indexer,
        "store_subtitles",
        lambda episode_id, **kwargs: store_calls.append((episode_id, kwargs)),
    )
    monkeypatch.setattr(series_indexer.gc, "collect", lambda: None)

    series_indexer.series_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(102, {"use_cache": True, "item": episodes[1], "languages": {"en"}})]


def test_series_full_scan_does_not_skip_when_signature_changes(monkeypatch):
    from subtitles.indexer import series as series_indexer

    episodes = [
        SimpleNamespace(
            path='/series/episode.mkv',
            episode_file_id=123,
            file_size=456,
            missing_subtitles='[]',
            subtitles_last_indexed_episode_file_id=123,
            subtitles_last_indexed_external_signature='old-signature',
            subtitles_last_indexed_file_size=456,
            subtitles_last_indexed_path='/series/episode.mkv',
            title='Series',
            season=1,
            episode=1,
            episodeTitle='Pilot',
            sonarrEpisodeId=101,
            has_indexed_subtitles=True,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=episodes)

    monkeypatch.setattr(series_indexer, "database", _Database())
    monkeypatch.setattr(
        series_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(series_indexer.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(series_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(series_indexer, "_get_subtitles_scan_signature", lambda paths: 'new-signature')
    monkeypatch.setattr(series_indexer, "get_language_set", lambda: {"en"})
    monkeypatch.setattr(
        series_indexer,
        "store_subtitles",
        lambda episode_id, **kwargs: store_calls.append((episode_id, kwargs)),
    )
    monkeypatch.setattr(series_indexer.gc, "collect", lambda: None)

    series_indexer.series_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(101, {"use_cache": True, "item": episodes[0], "languages": {"en"}})]


def test_series_full_scan_does_not_skip_without_indexed_subtitles(monkeypatch):
    from subtitles.indexer import series as series_indexer

    episodes = [
        SimpleNamespace(
            path='/series/episode.mkv',
            episode_file_id=123,
            file_size=456,
            missing_subtitles='[]',
            subtitles_last_indexed_episode_file_id=123,
            subtitles_last_indexed_external_signature='sig',
            subtitles_last_indexed_file_size=456,
            subtitles_last_indexed_path='/series/episode.mkv',
            title='Series',
            season=1,
            episode=1,
            episodeTitle='Pilot',
            sonarrEpisodeId=101,
            has_indexed_subtitles=False,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=episodes)

    monkeypatch.setattr(series_indexer, "database", _Database())
    monkeypatch.setattr(
        series_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(series_indexer.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(series_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(series_indexer, "_get_subtitles_scan_signature", lambda paths: 'sig')
    monkeypatch.setattr(series_indexer, "get_language_set", lambda: {"en"})
    monkeypatch.setattr(
        series_indexer,
        "store_subtitles",
        lambda episode_id, **kwargs: store_calls.append((episode_id, kwargs)),
    )
    monkeypatch.setattr(series_indexer.gc, "collect", lambda: None)

    series_indexer.series_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(101, {"use_cache": True, "item": episodes[0], "languages": {"en"}})]


def test_series_full_scan_reuses_language_set_for_each_episode(monkeypatch):
    from subtitles.indexer import series as series_indexer

    language_set = object()
    episodes = [
        SimpleNamespace(
            path='/series/one.mkv',
            sonarrSeriesId=1,
            episode_file_id=123,
            file_size=456,
            profileId=1,
            audio_language='[]',
            missing_subtitles='["en"]',
            subtitles_last_indexed_episode_file_id=123,
            subtitles_last_indexed_external_signature='sig',
            subtitles_last_indexed_file_size=456,
            subtitles_last_indexed_path='/series/one.mkv',
            title='Series',
            season=1,
            episode=1,
            episodeTitle='One',
            sonarrEpisodeId=101,
            has_indexed_subtitles=True,
        ),
        SimpleNamespace(
            path='/series/two.mkv',
            sonarrSeriesId=1,
            episode_file_id=124,
            file_size=457,
            profileId=1,
            audio_language='[]',
            missing_subtitles='["fr"]',
            subtitles_last_indexed_episode_file_id=124,
            subtitles_last_indexed_external_signature='sig',
            subtitles_last_indexed_file_size=457,
            subtitles_last_indexed_path='/series/two.mkv',
            title='Series',
            season=1,
            episode=2,
            episodeTitle='Two',
            sonarrEpisodeId=102,
            has_indexed_subtitles=True,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=episodes)

    monkeypatch.setattr(series_indexer, "database", _Database())
    monkeypatch.setattr(
        series_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(series_indexer, "get_language_set", lambda: language_set)
    monkeypatch.setattr(series_indexer.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(series_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        series_indexer,
        "store_subtitles",
        lambda episode_id, **kwargs: store_calls.append((episode_id, kwargs["languages"])),
    )
    monkeypatch.setattr(series_indexer.gc, "collect", lambda: None)

    series_indexer.series_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(101, language_set), (102, language_set)]
    assert store_calls[0][1] is language_set
    assert store_calls[1][1] is language_set


def test_store_subtitles_uses_cached_languages_and_updates_missing_subtitles_inline(monkeypatch):
    from subtitles.indexer import series as series_indexer

    item = SimpleNamespace(
        sonarrSeriesId=1,
        path='/series/episode.mkv',
        episode_file_id=123,
        file_size=456,
        profileId=1,
        audio_language='[]',
    )
    captured_missing = []
    events = []

    class _Database:
        def __init__(self):
            self.calls = []

        def execute(self, statement):
            self.calls.append(statement)
            return _Result()

    database = _Database()

    monkeypatch.setattr(series_indexer, "database", database)
    monkeypatch.setattr(
        series_indexer,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                use_embedded_subs=False,
                single_language=False,
                ignore_pgs_subs=False,
                ignore_vobsub_subs=False,
                ignore_ass_subs=False,
                subfolder="current",
                subfolder_custom="",
            )
        ),
    )
    monkeypatch.setattr(series_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(series_indexer.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(series_indexer.os, "stat", lambda path: SimpleNamespace(st_size=11, st_mtime_ns=1))
    monkeypatch.setattr(series_indexer.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(series_indexer.path_mappings, "path_replace_reverse", lambda path: path)
    monkeypatch.setattr(series_indexer, "_get_indexed_subtitles", lambda episode_id: [])
    monkeypatch.setattr(
        series_indexer,
        "search_external_subtitles",
        lambda *args, **kwargs: {
            "episode.en.srt": SimpleNamespace(alpha3="eng", basename="en", forced=False, hi=False)
        },
    )
    monkeypatch.setattr(series_indexer, "guess_external_subtitles", lambda dest, subtitles, excluded: subtitles)
    monkeypatch.setattr(series_indexer, "get_external_subtitles_path", lambda mapped_path, subtitle: f"/series/{subtitle}")
    monkeypatch.setattr(series_indexer.CustomLanguage, "found_external", lambda *args: None)
    monkeypatch.setattr(series_indexer, "alpha2_from_alpha3", lambda code: "en")
    monkeypatch.setattr(series_indexer, "get_language_set", lambda: pytest.fail("store_subtitles should reuse passed languages"))
    def _capture_missing(profile_id, audio_language, indexed_subtitles):
        captured_missing.append(indexed_subtitles)
        return '["fr"]'

    monkeypatch.setattr(series_indexer, "_get_missing_subtitles_text", _capture_missing)
    monkeypatch.setattr(series_indexer, "_get_subtitles_scan_signature", lambda scan_paths: "sig")
    monkeypatch.setattr(
        series_indexer,
        "list_missing_subtitles",
        lambda *args, **kwargs: pytest.fail("store_subtitles should update missing subtitles inline"),
    )
    monkeypatch.setattr(series_indexer, "event_stream", lambda **kwargs: events.append(kwargs))

    series_indexer.store_subtitles(101, item=item, languages={"cached"})

    assert captured_missing[0][0]["language"] == "en"
    assert events == [
        {"type": "episode", "payload": 101},
        {"type": "episode-wanted", "action": "update", "payload": 101},
        {"type": "badges"},
    ]
    assert len(database.calls) == 2


def test_movies_full_scan_skips_clean_unchanged_movie(monkeypatch):
    from subtitles.indexer import movies as movies_indexer

    movies = [
        SimpleNamespace(
            path='/movies/clean.mkv',
            file_size=100,
            missing_subtitles='[]',
            movie_file_id=50,
            radarrId=1,
            subtitles_last_indexed_external_signature='sig',
            subtitles_last_indexed_file_size=100,
            subtitles_last_indexed_movie_file_id=50,
            subtitles_last_indexed_path='/movies/clean.mkv',
            title='Clean Movie',
            has_indexed_subtitles=True,
        ),
        SimpleNamespace(
            path='/movies/dirty.mkv',
            file_size=101,
            missing_subtitles='["en"]',
            movie_file_id=51,
            radarrId=2,
            subtitles_last_indexed_external_signature='sig',
            subtitles_last_indexed_file_size=101,
            subtitles_last_indexed_movie_file_id=51,
            subtitles_last_indexed_path='/movies/dirty.mkv',
            title='Dirty Movie',
            has_indexed_subtitles=True,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=movies)

    monkeypatch.setattr(movies_indexer, "database", _Database())
    monkeypatch.setattr(
        movies_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(movies_indexer.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(movies_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(movies_indexer, "_get_movie_subtitles_scan_signature", lambda paths: 'sig')
    monkeypatch.setattr(
        movies_indexer,
        "store_subtitles_movie",
        lambda radarr_id, **kwargs: store_calls.append((radarr_id, kwargs)),
    )
    monkeypatch.setattr(movies_indexer.gc, "collect", lambda: None)

    movies_indexer.movies_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(2, {"use_cache": True, "item": movies[1]})]


def test_movies_full_scan_does_not_skip_without_indexed_subtitles(monkeypatch):
    from subtitles.indexer import movies as movies_indexer

    movies = [
        SimpleNamespace(
            path='/movies/clean.mkv',
            file_size=100,
            missing_subtitles='[]',
            movie_file_id=50,
            radarrId=1,
            subtitles_last_indexed_external_signature='sig',
            subtitles_last_indexed_file_size=100,
            subtitles_last_indexed_movie_file_id=50,
            subtitles_last_indexed_path='/movies/clean.mkv',
            title='Clean Movie',
            has_indexed_subtitles=False,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=movies)

    monkeypatch.setattr(movies_indexer, "database", _Database())
    monkeypatch.setattr(
        movies_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(movies_indexer.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(movies_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(movies_indexer, "_get_movie_subtitles_scan_signature", lambda paths: 'sig')
    monkeypatch.setattr(
        movies_indexer,
        "store_subtitles_movie",
        lambda radarr_id, **kwargs: store_calls.append((radarr_id, kwargs)),
    )
    monkeypatch.setattr(movies_indexer.gc, "collect", lambda: None)

    movies_indexer.movies_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(1, {"use_cache": True, "item": movies[0]})]


def test_movies_full_scan_does_not_skip_when_signature_changes(monkeypatch):
    from subtitles.indexer import movies as movies_indexer

    movies = [
        SimpleNamespace(
            path='/movies/clean.mkv',
            file_size=100,
            missing_subtitles='[]',
            movie_file_id=50,
            radarrId=1,
            subtitles_last_indexed_external_signature='old-sig',
            subtitles_last_indexed_file_size=100,
            subtitles_last_indexed_movie_file_id=50,
            subtitles_last_indexed_path='/movies/clean.mkv',
            title='Clean Movie',
            has_indexed_subtitles=True,
        ),
    ]
    store_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=movies)

    monkeypatch.setattr(movies_indexer, "database", _Database())
    monkeypatch.setattr(
        movies_indexer,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(movies_indexer.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(movies_indexer.os.path, "exists", lambda path: True)
    monkeypatch.setattr(movies_indexer, "_get_movie_subtitles_scan_signature", lambda paths: 'new-sig')
    monkeypatch.setattr(
        movies_indexer,
        "store_subtitles_movie",
        lambda radarr_id, **kwargs: store_calls.append((radarr_id, kwargs)),
    )
    monkeypatch.setattr(movies_indexer.gc, "collect", lambda: None)

    movies_indexer.movies_full_scan_subtitles(job_id="job", use_cache=True)

    assert store_calls == [(1, {"use_cache": True, "item": movies[0]})]


def test_get_active_search_languages_parses_attempts_once_for_multiple_languages(monkeypatch):
    from subtitles import adaptive_searching as adaptive

    now = time.time()
    attempt_string = str([
        ['en', now - (30 * 24 * 3600)],
        ['en', now - (8 * 24 * 3600)],
        ['fr', now - (30 * 24 * 3600)],
        ['fr', now - (1 * 24 * 3600)],
    ])
    literal_eval_calls = []
    original_literal_eval = adaptive.ast.literal_eval

    monkeypatch.setattr(
        adaptive,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                adaptive_searching=True,
                adaptive_searching_delay='3w',
                adaptive_searching_delta='1w',
            )
        ),
    )
    monkeypatch.setattr(
        adaptive.ast,
        "literal_eval",
        lambda value: literal_eval_calls.append(value) or original_literal_eval(value),
    )

    active_languages = adaptive.get_active_search_languages(['en', 'fr'], attempt_string)

    assert active_languages == ['en']
    assert literal_eval_calls == [attempt_string]


def test_update_failed_attempts_batches_multiple_languages(monkeypatch):
    from subtitles import adaptive_searching as adaptive

    class _FakeDatetime:
        @staticmethod
        def now():
            return _datetime.fromtimestamp(1000)

        @staticmethod
        def timestamp(value):
            return _datetime.timestamp(value)

        @staticmethod
        def fromtimestamp(value):
            return _datetime.fromtimestamp(value)

    monkeypatch.setattr(adaptive, "datetime", _FakeDatetime)

    updated = adaptive.update_failed_attempts(
        ['en', 'fr'],
        str([['en', 100], ['en', 200], ['fr', 300], ['es', 50]]),
    )

    assert updated == str([
        ['en', 100],
        ['en', 1000.0],
        ['es', 50],
        ['fr', 300],
        ['fr', 1000.0],
    ])


def test_get_adaptive_search_policy_reuses_cached_timedeltas(monkeypatch):
    from subtitles import adaptive_searching as adaptive

    original_get_adaptive_timedelta = adaptive._get_adaptive_timedelta
    adaptive._get_cached_policy_components.cache_clear()
    timedelta_calls = []

    monkeypatch.setattr(
        adaptive,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                adaptive_searching=True,
                adaptive_searching_delay='3w',
                adaptive_searching_delta='1w',
            )
        ),
    )
    monkeypatch.setattr(
        adaptive,
        "_get_adaptive_timedelta",
        lambda name, value: timedelta_calls.append((name, value)) or original_get_adaptive_timedelta(name, value),
    )

    first_policy = adaptive.get_adaptive_search_policy()
    second_policy = adaptive.get_adaptive_search_policy()

    assert first_policy["delay"] == second_policy["delay"]
    assert first_policy["delta"] == second_policy["delta"]
    assert len(timedelta_calls) == 2


def test_get_adaptive_search_policy_avoids_reloading_settings_within_ttl(monkeypatch):
    from subtitles import adaptive_searching as adaptive

    class _General:
        def __init__(self):
            self.calls = 0

        @property
        def adaptive_searching(self):
            self.calls += 1
            return True

        @property
        def adaptive_searching_delay(self):
            self.calls += 1
            return '3w'

        @property
        def adaptive_searching_delta(self):
            self.calls += 1
            return '1w'

    general = _General()
    adaptive._get_cached_policy_components.cache_clear()
    adaptive._adaptive_policy_cache["expires_at"] = 0.0
    adaptive._adaptive_policy_cache["policy_components"] = None
    monotonic_values = iter([100.0, 100.0, 100.5, 100.5])

    monkeypatch.setattr(adaptive, "settings", SimpleNamespace(general=general))
    monkeypatch.setattr(adaptive, "monotonic", lambda: next(monotonic_values))

    first_policy = adaptive.get_adaptive_search_policy()
    second_policy = adaptive.get_adaptive_search_policy()

    assert first_policy["delay"] == second_policy["delay"]
    assert first_policy["delta"] == second_policy["delta"]
    assert general.calls == 3


def test_wanted_episode_updates_failed_attempts_once_for_all_due_languages(monkeypatch):
    from subtitles.wanted import series as wanted_series

    execute_calls = []
    update_calls = []
    generated_languages = []
    episode = SimpleNamespace(
        audio_language='eng',
        failedAttempts='[]',
        missing_subtitles='["en", "fr"]',
        path='/series/episode.mkv',
        profileId=1,
        sceneName='scene',
        sonarrEpisodeId=101,
        sonarrSeriesId=202,
        title='Series',
    )

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            return _Result()

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "get_audio_profile_languages", lambda *args, **kwargs: [])
    monkeypatch.setattr(wanted_series, "get_due_missing_languages", lambda *args, **kwargs: ['en', 'fr'])
    monkeypatch.setattr(
        wanted_series,
        "generate_subtitles",
        lambda path, languages, *args, **kwargs: generated_languages.append(languages) or iter(()),
    )
    monkeypatch.setattr(
        wanted_series,
        "update_failed_attempts",
        lambda languages, attempts: update_calls.append((languages, attempts)) or 'updated-attempts',
    )
    monkeypatch.setattr(wanted_series.path_mappings, "path_replace", lambda path: path)

    wanted_series._wanted_episode(episode, ['provider'], adaptive_search_policy='policy')

    assert generated_languages == [[('en', 'False', 'False'), ('fr', 'False', 'False')]]
    assert update_calls == [(['en', 'fr'], '[]')]
    assert len(execute_calls) == 1


def test_wanted_download_subtitles_uses_passed_provider_and_policy(monkeypatch):
    from subtitles.wanted import series as wanted_series

    wanted_calls = []
    episode = SimpleNamespace(
        path='/series/episode.mkv',
        missing_subtitles='["en"]',
        sonarrEpisodeId=101,
        sonarrSeriesId=202,
        audio_language='eng',
        sceneName='scene',
        failedAttempts='[]',
        title='Series',
        profileId=1,
    )

    class _Database:
        def execute(self, statement):
            return _Result(first_value=episode)

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "get_subtitles", lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "/tmp/sub.srt"}])
    monkeypatch.setattr(wanted_series, "get_providers", lambda: pytest.fail("wanted_download_subtitles should reuse passed providers"))
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode_details, providers_list, **kwargs: wanted_calls.append((episode_details, providers_list, kwargs)),
    )

    wanted_series.wanted_download_subtitles(
        101,
        job_id='job',
        providers_list=['provider'],
        adaptive_search_policy='policy',
    )

    assert wanted_calls == [
        (episode, ['provider'], {'due_languages': None, 'job_id': 'job', 'adaptive_search_policy': 'policy'})
    ]


def test_wanted_series_scan_uses_bulk_episode_row_when_already_complete(monkeypatch):
    from subtitles.wanted import series as wanted_series

    download_calls = []
    wanted_calls = []
    episodes = [
        SimpleNamespace(
            audio_language='eng',
            episode=1,
            episodeTitle='Pilot',
            failedAttempts='[]',
            has_incomplete_embedded_subtitles=False,
            has_indexed_subtitles=True,
            missing_subtitles='["en"]',
            monitored='True',
            path='/series/episode.mkv',
            profileId=1,
            sceneName='scene',
            season=1,
            seriesType='standard',
            sonarrEpisodeId=101,
            sonarrSeriesId=202,
            tags='[]',
            title='Series',
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=episodes)

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_providers", lambda: ['provider'])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: 'policy')
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers_list, **kwargs: wanted_calls.append((episode, providers_list, kwargs)),
    )
    monkeypatch.setattr(
        wanted_series,
        "wanted_download_subtitles",
        lambda *args, **kwargs: download_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(wanted_series.gc, "collect", lambda: None)

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert download_calls == []
    assert wanted_calls == [
        (episodes[0], ['provider'], {'due_languages': ['en'], 'job_id': 'job', 'adaptive_search_policy': 'policy'})
    ]


def test_series_wanted_search_prefilters_adaptive_search_and_reuses_providers(monkeypatch):
    from subtitles.wanted import series as wanted_series

    policy = object()
    rows = [
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=10,
            tags=[],
            monitored="True",
            title="Due Series",
            season=1,
            episode=1,
            episodeTitle="Due",
            seriesType="standard",
            path="/series/due.mkv",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            missing_subtitles='["en"]',
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            sonarrSeriesId=2,
            sonarrEpisodeId=20,
            tags=[],
            monitored="True",
            title="Throttled Series",
            season=1,
            episode=2,
            episodeTitle="Skip",
            seriesType="standard",
            path="/series/skip.mkv",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            missing_subtitles='["fr"]',
            failedAttempts='[["fr", 1.0], ["fr", 2.0]]',
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]
    provider_calls = []
    wanted_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: policy)
    monkeypatch.setattr(
        wanted_series,
        "get_due_missing_languages",
        lambda missing_subtitles, failed_attempts, adaptive_search_policy=None: ["en"]
        if missing_subtitles == '["en"]'
        else [],
    )
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers_list, **kwargs: wanted_calls.append((episode.sonarrEpisodeId, providers_list, kwargs)),
    )
    monkeypatch.setattr(
        wanted_series,
        "wanted_download_subtitles",
        lambda *args, **kwargs: pytest.fail("complete rows should use _wanted_episode directly"),
    )
    monkeypatch.setattr(wanted_series.gc, "collect", lambda: None)

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert provider_calls == [True]
    assert wanted_calls == [
        (10, ["provider"], {"due_languages": ["en"], "job_id": "job", "adaptive_search_policy": policy})
    ]


def test_movie_wanted_search_prefilters_adaptive_search_and_reuses_providers(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    policy = object()
    rows = [
        SimpleNamespace(
            radarrId=10,
            path="/movies/due.mkv",
            title="Due Movie",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            missing_subtitles='["en"]',
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            radarrId=20,
            path="/movies/skip.mkv",
            title="Throttled Movie",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            missing_subtitles='["fr"]',
            failedAttempts='[["fr", 1.0], ["fr", 2.0]]',
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]
    provider_calls = []
    wanted_calls = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: policy)
    monkeypatch.setattr(
        wanted_movies,
        "get_due_missing_languages",
        lambda missing_subtitles, failed_attempts, adaptive_search_policy=None: ["en"]
        if missing_subtitles == '["en"]'
        else [],
    )
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie, providers_list, **kwargs: wanted_calls.append((movie.radarrId, providers_list, kwargs)),
    )
    monkeypatch.setattr(
        wanted_movies,
        "wanted_download_subtitles_movie",
        lambda *args, **kwargs: pytest.fail("complete rows should use _wanted_movie directly"),
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert provider_calls == [True]
    assert wanted_calls == [
        (10, ["provider"], {"due_languages": ["en"], "job_id": "job", "adaptive_search_policy": policy})
    ]


def test_wanted_download_movie_reuses_prefetched_row_and_due_languages(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        path="/movies/movie.mkv",
        missing_subtitles='["en"]',
        radarrId=10,
        audio_language="eng",
        sceneName="Scene",
        failedAttempts="[]",
        title="Movie",
        profileId=1,
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )
    wanted_calls = []

    class _Database:
        def execute(self, statement):
            pytest.fail("prefetched movie rows should not be queried again")

    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie_arg, providers_list, **kwargs: wanted_calls.append((movie_arg, providers_list, kwargs)),
    )

    wanted_movies.wanted_download_subtitles_movie(
        movie.radarrId,
        job_id="job",
        providers_list=["provider"],
        movie=movie,
        due_languages=["en"],
        adaptive_search_policy="policy",
    )

    assert wanted_calls == [
        (movie, ["provider"], {"due_languages": ["en"], "job_id": "job", "adaptive_search_policy": "policy"})
    ]


def test_wanted_download_series_reuses_prefetched_row_and_due_languages(monkeypatch):
    from subtitles.wanted import series as wanted_series

    episode = SimpleNamespace(
        path="/series/episode.mkv",
        missing_subtitles='["en"]',
        sonarrEpisodeId=10,
        sonarrSeriesId=20,
        audio_language="eng",
        sceneName="Scene",
        failedAttempts="[]",
        title="Series",
        profileId=1,
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )
    wanted_calls = []

    class _Database:
        def execute(self, statement):
            pytest.fail("prefetched series rows should not be queried again")

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode_arg, providers_list, **kwargs: wanted_calls.append((episode_arg, providers_list, kwargs)),
    )

    wanted_series.wanted_download_subtitles(
        episode.sonarrEpisodeId,
        job_id="job",
        providers_list=["provider"],
        episode_details=episode,
        due_languages=["en"],
        adaptive_search_policy="policy",
    )

    assert wanted_calls == [
        (episode, ["provider"], {"due_languages": ["en"], "job_id": "job", "adaptive_search_policy": "policy"})
    ]


def test_copy_config_tree_creates_missing_subtitle_tables(tmp_path):
    from tests.benchmarks.helpers import copy_config_tree

    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "db").mkdir(parents=True)
    (source / "config").mkdir(parents=True)
    (source / "config" / "config.yaml").write_text("general: {}\n", encoding="utf-8")

    with sqlite3.connect(source / "db" / "bazarr.db") as conn:
        conn.executescript(
            """
            CREATE TABLE table_movies (radarrId INTEGER PRIMARY KEY);
            CREATE TABLE table_shows (sonarrSeriesId INTEGER PRIMARY KEY);
            CREATE TABLE table_episodes (sonarrEpisodeId INTEGER PRIMARY KEY, sonarrSeriesId INTEGER);
            """
        )
        conn.commit()

    copy_config_tree(source, target)

    with sqlite3.connect(target / "db" / "bazarr.db") as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert "table_episodes_subtitles" in tables
    assert "table_movies_subtitles" in tables
