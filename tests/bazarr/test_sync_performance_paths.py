# -*- coding: utf-8 -*-
from types import SimpleNamespace
import datetime
import json

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


def _job_queue():
    return SimpleNamespace(
        add_job_from_function=lambda *args, **kwargs: None,
        update_job_progress=lambda *args, **kwargs: None,
        update_job_name=lambda *args, **kwargs: None,
    )


def test_series_wanted_search_prefilters_adaptive_search_and_reuses_providers(monkeypatch):
    from subtitles.wanted import series as wanted_series
    from subtitles.wanted import utils as wanted_utils

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
            subtitles="[]",
            missing_subtitles='["en"]',
            failedAttempts="[]",
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
            subtitles="[]",
            missing_subtitles='["fr"]',
            failedAttempts='[["fr", 1.0], ["fr", 2.0]]',
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_calls = []
    downloads = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    monkeypatch.setattr(
        wanted_utils,
        "get_active_search_languages",
        lambda desired_languages, attempt_string: [language for language in desired_languages if language == "en"],
    )
    monkeypatch.setattr(
        wanted_series,
        "wanted_download_subtitles",
        lambda episode_id, **kwargs: downloads.append((episode_id, kwargs["episode_details"], kwargs["due_languages"])),
    )

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert provider_calls == [True]
    assert downloads == [(10, rows[0], ["en"])]


def test_movie_wanted_search_prefilters_adaptive_search_and_reuses_providers(monkeypatch):
    from subtitles.wanted import movies as wanted_movies
    from subtitles.wanted import utils as wanted_utils

    rows = [
        SimpleNamespace(
            radarrId=10,
            path="/movies/due.mkv",
            title="Due Movie",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            subtitles="[]",
            missing_subtitles='["en"]',
            failedAttempts="[]",
        ),
        SimpleNamespace(
            radarrId=20,
            path="/movies/skip.mkv",
            title="Throttled Movie",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            subtitles="[]",
            missing_subtitles='["fr"]',
            failedAttempts='[["fr", 1.0], ["fr", 2.0]]',
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_calls = []
    downloads = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    monkeypatch.setattr(
        wanted_utils,
        "get_active_search_languages",
        lambda desired_languages, attempt_string: [language for language in desired_languages if language == "en"],
    )
    monkeypatch.setattr(
        wanted_movies,
        "wanted_download_subtitles_movie",
        lambda radarr_id, **kwargs: downloads.append((radarr_id, kwargs["movie"], kwargs["due_languages"])),
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert provider_calls == [True]
    assert downloads == [(10, rows[0], ["en"])]


def test_adaptive_search_throttle_skip_is_not_logged_per_item(monkeypatch, caplog):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        audio_language="eng",
        missing_subtitles='["en", "fr"]',
        failedAttempts='[["en", 1.0], ["fr", 1.0]]',
        path="/movies/movie.mkv",
        sceneName="Scene",
        title="Movie",
        profileId=1,
        radarrId=10,
    )

    monkeypatch.setattr(wanted_movies, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_movies, "get_due_missing_languages", lambda missing_subtitles, failed_attempts: [])
    monkeypatch.setattr(wanted_movies.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(wanted_movies, "generate_subtitles", lambda *args, **kwargs: iter(()))

    with caplog.at_level("DEBUG"):
        wanted_movies._wanted_movie(movie, providers_list=["provider"], job_id="job")

    assert "Search is throttled by adaptive search" not in caplog.text


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
        subtitles="[]",
    )
    wanted_calls = []

    class _Database:
        def execute(self, statement):
            pytest.fail("prefetched movie rows should not be queried again")

    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie_arg, providers_list, due_languages=None, **kwargs: wanted_calls.append(
            (movie_arg, providers_list, due_languages)
        ),
    )

    wanted_movies.wanted_download_subtitles_movie(
        movie.radarrId,
        job_id="job",
        providers_list=["provider"],
        movie=movie,
        due_languages=["en"],
    )

    assert wanted_calls == [(movie, ["provider"], ["en"])]


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
        subtitles="[]",
    )
    wanted_calls = []

    class _Database:
        def execute(self, statement):
            pytest.fail("prefetched series rows should not be queried again")

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode_arg, providers_list, due_languages=None, **kwargs: wanted_calls.append(
            (episode_arg, providers_list, due_languages)
        ),
    )

    wanted_series.wanted_download_subtitles(
        episode.sonarrEpisodeId,
        job_id="job",
        providers_list=["provider"],
        episode_details=episode,
        due_languages=["en"],
    )

    assert wanted_calls == [(episode, ["provider"], ["en"])]


def test_serialized_subtitle_helpers_accept_json_and_legacy_literals():
    from subtitles.serialization import dump_text_list, parse_missing_subtitles

    assert parse_missing_subtitles('["en", "fr:hi"]') == ["en", "fr:hi"]
    assert parse_missing_subtitles("['en', 'fr:hi']") == ["en", "fr:hi"]
    assert dump_text_list(["en"]) == '["en"]'


def test_update_failed_attempts_writes_json(monkeypatch):
    from subtitles import adaptive_searching

    fake_now = datetime.datetime(2026, 5, 23, 14, 0, 0)
    monkeypatch.setattr(
        adaptive_searching,
        "datetime",
        SimpleNamespace(now=lambda: fake_now, timestamp=datetime.datetime.timestamp),
    )

    updated = adaptive_searching.updateFailedAttempts("en", "[['fr', 1.0]]")

    assert json.loads(updated) == [["en", fake_now.timestamp()], ["fr", 1.0]]


def test_get_active_search_languages_evaluates_attempts_once(monkeypatch):
    from subtitles import adaptive_searching

    fake_now = datetime.datetime(2026, 5, 23, 14, 0, 0)
    monkeypatch.setattr(
        adaptive_searching,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                adaptive_searching=True,
                adaptive_searching_delay="3w",
                adaptive_searching_delta="1w",
            )
        ),
    )
    monkeypatch.setattr(adaptive_searching, "datetime", SimpleNamespace(now=lambda: fake_now, fromtimestamp=datetime.datetime.fromtimestamp))

    due_languages = adaptive_searching.get_active_search_languages(
        ["en", "fr", "de"],
        json.dumps([
            ["en", fake_now.timestamp() - (8 * 24 * 60 * 60)],
            ["en", fake_now.timestamp() - (2 * 24 * 60 * 60)],
            ["fr", fake_now.timestamp() - (40 * 24 * 60 * 60)],
            ["fr", fake_now.timestamp() - (2 * 24 * 60 * 60)],
        ]),
    )

    assert due_languages == ["en", "de"]


def test_generate_subtitles_rechecks_missing_languages_only_after_save(monkeypatch):
    from subtitles import download
    from subtitles import pool as subtitles_pool

    class _Language:
        def __init__(self, basename):
            self.hi = False
            self.forced = False
            self.basename = basename

    class _Subtitle:
        def __init__(self):
            self.format = "srt"
            self.matches = {"hash"}
            self.storage_path = "/tmp/subtitle.srt"
            self.provider_name = "provider"
            self.uploader = "uploader"
            self.release_info = "release"
            self.score = 100
            self.id = "subtitle-id"
            self.language = SimpleNamespace(hi=False, forced=False)

    class _Pool:
        providers = ["provider"]

    class _Video:
        def __init__(self):
            self.original_path = "/video.mkv"

    check_calls = []
    processed = SimpleNamespace(message="ok", matches={"hash"})
    en_language = _Language("en")
    fr_language = _Language("fr")
    video = _Video()

    monkeypatch.setattr(download, "_get_pool", lambda media_type, profile_id: _Pool())
    monkeypatch.setattr(subtitles_pool, "_update_pool", lambda media_type, profile_id: False)
    monkeypatch.setattr(download, "_get_language_obj", lambda languages: [en_language, fr_language])
    monkeypatch.setattr(download, "_set_forced_providers", lambda **kwargs: None)
    monkeypatch.setattr(download, "get_profiles_list", lambda profile_id: {"originalFormat": False})
    monkeypatch.setattr(download, "get_video", lambda *args, **kwargs: video)
    monkeypatch.setattr(download, "_get_scores", lambda *args, **kwargs: (0, 100, {}))
    monkeypatch.setattr(download, "get_array_from", lambda value: [])
    monkeypatch.setattr(download, "download_best_subtitles", lambda **kwargs: {video: [_Subtitle()]})
    monkeypatch.setattr(download, "get_target_folder", lambda path: None)
    monkeypatch.setattr(download, "save_subtitles", lambda *args, **kwargs: [_Subtitle()])
    monkeypatch.setattr(download, "process_subtitle", lambda **kwargs: processed)
    monkeypatch.setattr(download.subliminal, "region", SimpleNamespace(backend=SimpleNamespace(sync=lambda: None)))
    monkeypatch.setattr(
        download,
        "check_missing_languages",
        lambda path, media_type: check_calls.append((path, media_type)) or {en_language},
    )

    results = list(
        download.generate_subtitles(
            "/video.mkv",
            [("en", "False", "False"), ("fr", "False", "False")],
            "None",
            "Scene",
            "Title",
            "movie",
            1,
            check_if_still_required=True,
            job_id="job",
        )
    )

    assert results == [processed]
    assert check_calls == [("/video.mkv", "movie"), ("/video.mkv", "movie")]


def test_wanted_movie_search_emits_one_progress_update_per_item(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    rows = [
        SimpleNamespace(
            radarrId=10,
            path="/movies/due.mkv",
            title="Due Movie",
            audio_language="eng",
            sceneName="Scene",
            profileId=1,
            subtitles="[]",
            missing_subtitles='["en"]',
            failedAttempts="[]",
        ),
    ]

    progress_updates = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(
        wanted_movies,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: progress_updates.append(kwargs),
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(wanted_movies, "get_due_missing_languages", lambda missing_subtitles, failed_attempts: ["en"])
    monkeypatch.setattr(wanted_movies, "wanted_download_subtitles_movie", lambda *args, **kwargs: None)

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    per_item_updates = [
        update for update in progress_updates
        if update.get("progress_value") == 1 and update.get("progress_message") == "Due Movie"
    ]
    assert len(per_item_updates) == 1


def test_wanted_series_search_emits_one_progress_update_per_item(monkeypatch):
    from subtitles.wanted import series as wanted_series

    rows = [
        SimpleNamespace(
            path="/series/episode.mkv",
            sonarrSeriesId=1,
            sonarrEpisodeId=10,
            audio_language="eng",
            sceneName="Scene",
            failedAttempts="[]",
            title="Series",
            profileId=1,
            season=1,
            episode=2,
            episodeTitle="Episode",
            missing_subtitles='["en"]',
            subtitles="[]",
        ),
    ]

    progress_updates = []

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(
        wanted_series,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: progress_updates.append(kwargs),
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(wanted_series, "get_due_missing_languages", lambda missing_subtitles, failed_attempts: ["en"])
    monkeypatch.setattr(wanted_series, "wanted_download_subtitles", lambda *args, **kwargs: None)

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    per_item_updates = [
        update for update in progress_updates
        if update.get("progress_value") == 1 and update.get("progress_message") == "Series - S01E02 - Episode"
    ]
    assert len(per_item_updates) == 1


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


def test_wanted_search_indexes_are_declared_on_models():
    from app.database import TableEpisodes, TableMovies

    episode_indexes = {index.name for index in TableEpisodes.__table__.indexes}
    movie_indexes = {index.name for index in TableMovies.__table__.indexes}

    assert "idx_table_episodes_sonarrSeriesId" in episode_indexes
    assert "idx_table_episodes_missing_subtitles" in episode_indexes
    assert "idx_table_movies_missing_subtitles" in movie_indexes
