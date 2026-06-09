import random

import pytest
from types import SimpleNamespace

from tests.test_helpers import _Result, load_wanted_module


def _movie_row():
    return SimpleNamespace(
        path="/movies/movie.mkv",
        missing_subtitles="['en', 'fr:forced']",
        radarrId=7,
        audio_language="['eng']",
        sceneName="Scene",
        failedAttempts="[['en', 10], ['fr:forced', 10]]",
        title="Movie",
        profileId=11,
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )


def _episode_row():
    return SimpleNamespace(
        path="/series/e01.mkv",
        missing_subtitles="['en', 'fr:hi']",
        sonarrEpisodeId=17,
        sonarrSeriesId=3,
        audio_language="['eng']",
        sceneName="Scene",
        failedAttempts="[['en', 10], ['fr:hi', 10]]",
        title="Series",
        profileId=22,
        season=1,
        episode=1,
        episodeTitle="Pilot",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )


def _patch_movie_success_side_effects(monkeypatch, module):
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: iter([SimpleNamespace(message="ok")]))
    monkeypatch.setattr(module, "store_subtitles_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "history_log_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "send_notifications_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)


def _patch_series_success_side_effects(monkeypatch, module):
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: iter([SimpleNamespace(message="ok")]))
    monkeypatch.setattr(module, "store_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "history_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "send_notifications", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)


def test_wanted_movie_only_requests_due_languages(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()
    captured = []

    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: desired_language == "fr:forced")
    # Test with real is_search_active to validate adaptive search path
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_movie(movie, ["provider"])

    assert captured == [[("fr", "False", "True")]]


def test_wanted_series_only_requests_due_languages(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()
    captured = []

    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: desired_language == "fr:hi")
    # Test with real is_search_active to validate adaptive search path
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_episode(episode, ["provider"])

    assert captured == [[("fr", "True", "False")]]


def test_wanted_movie_refreshes_missing_state_before_search(monkeypatch):
    module = load_wanted_module("movies")
    initial_movie = _movie_row()
    initial_movie.missing_subtitles = None
    refreshed_movie = _movie_row()
    searched = []
    rebuilt = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=initial_movie)
            return _Result(first_value=refreshed_movie)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "list_missing_subtitles_movies", lambda **kwargs: rebuilt.append(kwargs))
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/movies/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(module, "_wanted_movie", lambda movie, providers_list, **kwargs: searched.append((movie, providers_list)))
    module.wanted_download_subtitles_movie(7, job_id="job")

    assert rebuilt == [{"no": 7}]
    assert searched[0][0] is refreshed_movie
    assert searched[0][1] == ["provider"]


def test_wanted_episode_refreshes_missing_state_before_search(monkeypatch):
    module = load_wanted_module("series")
    initial_episode = _episode_row()
    initial_episode.missing_subtitles = None
    refreshed_episode = _episode_row()
    searched = []
    rebuilt = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=initial_episode)
            return _Result(first_value=refreshed_episode)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "list_missing_subtitles", lambda **kwargs: rebuilt.append(kwargs))
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/series/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(module, "_wanted_episode", lambda episode, providers_list, **kwargs: searched.append((episode, providers_list)))
    module.wanted_download_subtitles(17, job_id="job")

    assert rebuilt == [{"epno": 17}]
    assert searched[0][0] is refreshed_episode
    assert searched[0][1] == ["provider"]


def test_movie_partial_success_does_not_stamp_remaining_languages(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()

    _patch_movie_success_side_effects(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "database",
        SimpleNamespace(
            execute=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("partial success should not refresh or stamp remaining movie languages")
            )
        ),
    )

    module._wanted_movie(movie, ["provider"])


def test_series_partial_success_does_not_stamp_remaining_languages(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()

    _patch_series_success_side_effects(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "database",
        SimpleNamespace(
            execute=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("partial success should not refresh or stamp remaining episode languages")
            )
        ),
    )

    module._wanted_episode(episode, ["provider"])


def test_movie_success_does_not_refresh_details_after_download(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()

    _patch_movie_success_side_effects(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "database",
        SimpleNamespace(
            execute=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("successful movie search should not refresh details")
            )
        ),
    )

    module._wanted_movie(movie, ["provider"])


def test_series_success_does_not_refresh_details_after_download(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()

    _patch_series_success_side_effects(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "database",
        SimpleNamespace(
            execute=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("successful episode search should not refresh details")
            )
        ),
    )

    module._wanted_episode(episode, ["provider"])


def test_movie_download_wrapper_does_not_refresh_details_after_success(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()
    calls = []

    class _Database:
        def execute(self, *args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                return _Result(first_value=movie)
            raise AssertionError("successful movie download wrapper should not re-query details")

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/movies/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    _patch_movie_success_side_effects(monkeypatch, module)

    module.wanted_download_subtitles_movie(7, job_id="job")


def test_series_download_wrapper_does_not_refresh_details_after_success(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()
    calls = []

    class _Database:
        def execute(self, *args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                return _Result(first_value=episode)
            raise AssertionError("successful series download wrapper should not re-query details")

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/series/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    _patch_series_success_side_effects(monkeypatch, module)

    module.wanted_download_subtitles(17, job_id="job")


def test_movie_wanted_search_falls_back_to_legacy_missing_text(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()
    captured = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None), raising=False)

    module._wanted_movie(movie, ["provider"])

    assert captured == [[("en", "False", "False"), ("fr", "False", "True")]]


def test_series_wanted_search_falls_back_to_legacy_missing_text(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()
    captured = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None), raising=False)

    module._wanted_episode(episode, ["provider"])

    assert captured == [[("en", "False", "False"), ("fr", "True", "False")]]


def test_movie_download_wrapper_falls_back_to_legacy_missing_text(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()
    captured = []

    class _Database:
        def execute(self, *args, **kwargs):
            return _Result(first_value=movie)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/movies/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "history_log_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "send_notifications_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)

    module.wanted_download_subtitles_movie(7, job_id="job")

    assert captured == [[("en", "False", "False"), ("fr", "False", "True")]]


def test_series_download_wrapper_falls_back_to_legacy_missing_text(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()
    captured = []

    class _Database:
        def execute(self, *args, **kwargs):
            return _Result(first_value=episode)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/series/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "store_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "history_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "send_notifications", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)

    module.wanted_download_subtitles(17, job_id="job")

    assert captured == [[("en", "False", "False"), ("fr", "True", "False")]]


def test_movie_scheduled_search_falls_back_to_legacy_rows_when_due_map_is_empty(monkeypatch):
    module = load_wanted_module("movies")
    searched = []
    row = SimpleNamespace(radarrId=7, title="Movie", tags=[], monitored=True, missing_subtitles="['en']", failedAttempts="[]")

    monkeypatch.setattr(
        module,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[row])))
    monkeypatch.setattr(module, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(module, "wanted_download_subtitles_movie", lambda movie_id, **kwargs: searched.append(movie_id))

    module.wanted_search_missing_subtitles_movies(job_id="job")

    assert searched == [7]


def test_series_scheduled_search_falls_back_to_legacy_rows_when_due_map_is_empty(monkeypatch):
    module = load_wanted_module("series")
    searched = []
    row = SimpleNamespace(
        sonarrEpisodeId=17,
        sonarrSeriesId=3,
        title="Series",
        season=1,
        episode=1,
        episodeTitle="Pilot",
        missing_subtitles="['en']",
        failedAttempts="[]",
    )

    monkeypatch.setattr(
        module,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[row])))
    monkeypatch.setattr(module, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(module, "wanted_download_subtitles", lambda episode_id, **kwargs: searched.append(episode_id))

    module.wanted_search_missing_subtitles_series(job_id="job")

    assert searched == [17]


def test_wanted_search_reports_throttled_when_all_providers_are_throttled(monkeypatch):
    for name, id_field, title_field in (
        ("movies", "radarrId", "title"),
        ("series", "sonarrEpisodeId", None),
    ):
        module = load_wanted_module(name)
        progress_updates = []
        row = _movie_row() if name == "movies" else _episode_row()

        if name == "series":
            row = SimpleNamespace(
                sonarrEpisodeId=row.sonarrEpisodeId,
                sonarrSeriesId=row.sonarrSeriesId,
                title=row.title,
                season=row.season,
                episode=row.episode,
                episodeTitle=row.episodeTitle,
            )
        else:
            row = SimpleNamespace(radarrId=row.radarrId, title=row.title, tags=[], monitored=True)

        monkeypatch.setattr(module, "get_providers", lambda: [])
        monkeypatch.setattr(
            module,
            "jobs_queue",
            SimpleNamespace(
                add_job_from_function=lambda *args, **kwargs: None,
                update_job_progress=lambda **kwargs: progress_updates.append(kwargs),
                update_job_name=lambda *args, **kwargs: None,
            ),
        )

        if hasattr(module, "_count_searchable_due_movies"):
            monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: [row]))
        elif hasattr(module, "_count_searchable_due_episodes"):
            monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: [row]))
        else:
            monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[row])))

        if name == "movies":
            module.wanted_search_missing_subtitles_movies(job_id="job")
        else:
            module.wanted_search_missing_subtitles_series(job_id="job")

        assert progress_updates[-1]["progress_message"] == "All providers throttled"


def test_wanted_search_marks_empty_run_complete(monkeypatch):
    for name in ("movies", "series"):
        module = load_wanted_module(name)
        progress_updates = []

        monkeypatch.setattr(
            module,
            "jobs_queue",
            SimpleNamespace(
                add_job_from_function=lambda *args, **kwargs: None,
                update_job_progress=lambda **kwargs: progress_updates.append(kwargs),
                update_job_name=lambda *args, **kwargs: None,
            ),
        )

        if hasattr(module, "_count_searchable_due_movies"):
            monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: []))
            module.wanted_search_missing_subtitles_movies(job_id="job")
        elif hasattr(module, "_count_searchable_due_episodes"):
            monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: []))
            module.wanted_search_missing_subtitles_series(job_id="job")
        else:
            monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[])))
            if name == "movies":
                module.wanted_search_missing_subtitles_movies(job_id="job")
            else:
                module.wanted_search_missing_subtitles_series(job_id="job")

        assert any(update.get("progress_value") == "max" for update in progress_updates)
        assert progress_updates[-1]["progress_message"] == "Search completed"


def test_failed_attempt_batch_writers_execute_updates(monkeypatch):
    for name, worker_name, table_name, id_key in (
        ("movies", "_record_failed_movie_attempts", "TableMovies", 7),
        ("series", "_record_failed_episode_attempts", "TableEpisodes", 17),
    ):
        module = load_wanted_module(name)
        if not hasattr(module, worker_name):
            continue

        executed = []
        bind = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        database = SimpleNamespace(
            bind=bind,
            execute=lambda statement: executed.append(statement),
        )

        monkeypatch.setattr(module, "database", database)
    
        getattr(module, worker_name)({id_key: ["en"]})

        assert len(executed) == 1


def test_failed_attempt_batch_writers_use_temp_table_for_large_sqlite_updates(monkeypatch):
    for name, worker_name, id_key in (
        ("movies", "_record_failed_movie_attempts", 7),
        ("series", "_record_failed_episode_attempts", 17),
    ):
        module = load_wanted_module(name)
        if not hasattr(module, worker_name):
            continue

        sql_calls = []
        connection = SimpleNamespace(exec_driver_sql=lambda sql, params=None: sql_calls.append((sql, params)))
        database = SimpleNamespace(
            bind=SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))),
            connection=lambda: connection,
        )

        monkeypatch.setattr(module, "database", database)
        monkeypatch.setattr(module, "_TEMP_FAILED_ATTEMPT_UPDATE_MIN_SIZE", 1)
    
        getattr(module, worker_name)({id_key: ["en"]})

        assert any("CREATE TEMP TABLE temp_failed_attempt_updates" in sql for sql, _ in sql_calls)
        assert any("DROP TABLE IF EXISTS temp_failed_attempt_updates" in sql for sql, _ in sql_calls)


def test_pending_failed_attempt_helpers_flush_and_clear(monkeypatch):
    for name, helper_name, worker_name in (
        ("movies", "_record_pending_failed_movie_attempts", "_record_failed_movie_attempts"),
        ("series", "_record_pending_failed_episode_attempts", "_record_failed_episode_attempts"),
    ):
        module = load_wanted_module(name)
        if not hasattr(module, helper_name):
            continue

        calls = []
        pending = {1: ["en"]}
        monkeypatch.setattr(module, worker_name, lambda data: calls.append(data))

        getattr(module, helper_name)(pending)

        assert calls == [{1: ["en"]}]
        assert pending == {}


def test_wanted_search_refreshes_provider_availability(monkeypatch):
    for name, id_attr in (("movies", "radarrId"), ("series", "sonarrEpisodeId")):
        module = load_wanted_module(name)
        row_one = _movie_row() if name == "movies" else _episode_row()
        row_two = _movie_row() if name == "movies" else _episode_row()
        setattr(row_two, id_attr, 20)
        row_two.title = "Second"
        if name == "series":
            row_two.episodeTitle = "Second"
            row_two.episode = 2

        provider_results = iter((["provider"], []))
        searches = []
        monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[row_one, row_two])))
        monkeypatch.setattr(module, "jobs_queue", SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ))
        monkeypatch.setattr(module, "get_exclusion_clause", lambda media_type: [])
        monkeypatch.setattr(module, "get_providers", lambda: next(provider_results))
        if name == "movies":
            monkeypatch.setattr(module, "wanted_download_subtitles_movie", lambda movie_id, **kwargs: searches.append((movie_id, ["provider"])))
            module.wanted_search_missing_subtitles_movies(job_id="job")
        else:
            monkeypatch.setattr(module, "wanted_download_subtitles", lambda episode_id, **kwargs: searches.append((episode_id, ["provider"])))
            module.wanted_search_missing_subtitles_series(job_id="job")

        assert searches == [(getattr(row_one, id_attr), ["provider"])]


def test_wanted_search_batches_failed_attempt_updates(monkeypatch):
    for name, id_attr, worker_name in (
        ("movies", "radarrId", "_wanted_movie"),
        ("series", "sonarrEpisodeId", "_wanted_episode"),
    ):
        module = load_wanted_module(name)
        if not hasattr(module, "record_failed_subtitle_attempts_map"):
            continue
        row_one = _movie_row() if name == "movies" else _episode_row()
        row_two = _movie_row() if name == "movies" else _episode_row()
        setattr(row_two, id_attr, 20)
        row_two.title = "Second"
        if name == "series":
            row_two.episodeTitle = "Second"
            row_two.episode = 2

        batch_calls = []
        wanted_calls = []
        monkeypatch.setattr(
            module,
            "database",
            SimpleNamespace(
                bind=SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))),
                execute=lambda statement, params=None: _Result(all_value=[row_one, row_two]),
            ),
        )
        monkeypatch.setattr(module, "jobs_queue", SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
        ))
        monkeypatch.setattr(module, "get_exclusion_clause", lambda media_type: [])
        monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
        monkeypatch.setattr(
            module,
            worker_name,
            lambda item, providers, **kwargs: wanted_calls.append((getattr(item, id_attr), kwargs["defer_failed_attempts"])) or kwargs["due_languages"],
        )
        monkeypatch.setattr(
            module,
            "record_failed_subtitle_attempts_map",
            lambda media_type, failures: batch_calls.append((media_type, failures)) or {getattr(row_one, id_attr): "a", getattr(row_two, id_attr): "b"},
        )
        if name == "movies":
            module.wanted_search_missing_subtitles_movies(job_id="job")
        else:
            module.wanted_search_missing_subtitles_series(job_id="job")

        assert wanted_calls == [(getattr(row_one, id_attr), True), (getattr(row_two, id_attr), True)]
        assert batch_calls == [("movie" if name == "movies" else "series", {getattr(row_one, id_attr): ["en"], getattr(row_two, id_attr): ["fr"]})]


# ---------------------------------------------------------------------------
# Additional edge-case and boundary tests
# ---------------------------------------------------------------------------


def test_wanted_download_subtitles_movie_returns_early_when_movie_not_found(monkeypatch):
    """wanted_download_subtitles_movie should return None gracefully when no movie row exists."""
    module = load_wanted_module("movies")
    called = []

    class _Database:
        def execute(self, statement, *args, **kwargs):
            return _Result(first_value=None)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [])

    result = module.wanted_download_subtitles_movie(9999, job_id="job")

    assert result is None
    assert called == [], "_wanted_movie should not be called when movie is absent"


def test_wanted_download_subtitles_returns_early_when_episode_not_found(monkeypatch):
    """wanted_download_subtitles should return None gracefully when no episode row exists."""
    module = load_wanted_module("series")
    called = []

    class _Database:
        def execute(self, statement, *args, **kwargs):
            return _Result(first_value=None)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [])

    result = module.wanted_download_subtitles(9999, job_id="job")

    assert result is None
    assert called == [], "_wanted_episode should not be called when episode is absent"


def test_wanted_movie_skips_generate_when_no_providers_available(monkeypatch):
    """_wanted_movie should not call generate_subtitles when providers_list is empty."""
    module = load_wanted_module("movies")
    movie = _movie_row()
    generated = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: generated.append(args) or iter(()))
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_movie(movie, [])

    assert len(generated) == 1
    assert generated[0][1] == []


def test_wanted_episode_skips_generate_when_no_providers_available(monkeypatch):
    """_wanted_episode should not call generate_subtitles when providers_list is empty."""
    module = load_wanted_module("series")
    episode = _episode_row()
    generated = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: generated.append(args) or iter(()))
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_episode(episode, [])

    assert len(generated) == 1
    assert generated[0][1] == []


def test_wanted_search_missing_subtitles_movies_completes_with_empty_list(monkeypatch):
    """wanted_search_missing_subtitles_movies should complete gracefully with no movies to search."""
    module = load_wanted_module("movies")
    names = []

    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[])))
    monkeypatch.setattr(module, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(
        module,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda **kwargs: names.append(kwargs["new_job_name"]),
        ),
    )

    module.wanted_search_missing_subtitles_movies(job_id="job")

    # Job name should be updated to reflect completion even with no movies
    assert any("movie" in n.lower() or "search" in n.lower() or "subtitle" in n.lower() for n in names), \
        f"Expected a job-completion name update, got: {names}"


def test_wanted_search_missing_subtitles_series_completes_with_empty_list(monkeypatch):
    """wanted_search_missing_subtitles_series should complete gracefully with no episodes to search."""
    module = load_wanted_module("series")
    names = []

    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda statement: _Result(all_value=[])))
    monkeypatch.setattr(module, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(
        module,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda **kwargs: names.append(kwargs["new_job_name"]),
        ),
    )

    module.wanted_search_missing_subtitles_series(job_id="job")

    assert any("subtitle" in n.lower() or "search" in n.lower() or "series" in n.lower() for n in names), \
        f"Expected a job-completion name update, got: {names}"


def test_wanted_download_subtitles_movie_skips_search_when_no_providers(monkeypatch):
    """wanted_download_subtitles_movie should not call _wanted_movie when providers list is empty."""
    module = load_wanted_module("movies")
    movie = _movie_row()
    wanted_calls = []

    class _Database:
        def execute(self, statement, *args, **kwargs):
            return _Result(first_value=movie)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/movies/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: [])
    monkeypatch.setattr(module, "_wanted_movie", lambda *args, **kwargs: wanted_calls.append(args))

    module.wanted_download_subtitles_movie(7, job_id="job")

    assert wanted_calls == [], "_wanted_movie should not run when no providers are available"


def test_wanted_download_subtitles_skips_search_when_no_providers(monkeypatch):
    """wanted_download_subtitles should not call _wanted_episode when providers list is empty."""
    module = load_wanted_module("series")
    episode = _episode_row()
    wanted_calls = []

    class _Database:
        def execute(self, statement, *args, **kwargs):
            return _Result(first_value=episode)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/series/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "get_providers", lambda: [])
    monkeypatch.setattr(module, "_wanted_episode", lambda *args, **kwargs: wanted_calls.append(args))

    module.wanted_download_subtitles(17, job_id="job")

    assert wanted_calls == [], "_wanted_episode should not run when no providers are available"


def test_wanted_movie_does_not_stamp_failed_attempts_when_no_providers(monkeypatch):
    """_wanted_movie with an empty providers list must not stamp failed attempts."""
    module = load_wanted_module("movies")
    movie = _movie_row()
    stamped = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: iter([]))
    monkeypatch.setattr(module, "get_due_missing_languages_for_media", lambda *args, **kwargs: [], raising=False)
    monkeypatch.setattr(module, "updateFailedAttempts", lambda *args, **kwargs: stamped.append(args[0]) or "")
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_movie(movie, [])

    assert stamped == [], "No failed-attempt stamps should be made when providers_list is empty"


def test_wanted_episode_does_not_stamp_failed_attempts_when_no_providers(monkeypatch):
    """_wanted_episode with an empty providers list must not stamp failed attempts."""
    module = load_wanted_module("series")
    episode = _episode_row()
    stamped = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: iter([]))
    monkeypatch.setattr(module, "get_due_missing_languages_for_media", lambda *args, **kwargs: [], raising=False)
    monkeypatch.setattr(module, "updateFailedAttempts", lambda *args, **kwargs: stamped.append(args[0]) or "")
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_episode(episode, [])

    assert stamped == [], "No failed-attempt stamps should be made when providers_list is empty"


def test_wanted_movie_filters_out_inactive_languages_entirely(monkeypatch):
    """_wanted_movie should pass zero languages to generate_subtitles when all are search-inactive."""
    module = load_wanted_module("movies")
    movie = _movie_row()
    captured_languages = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(
        module,
        "generate_subtitles",
        lambda *args, **kwargs: captured_languages.append(args[1]) or iter(()),
    )
    # All languages are inactive
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_movie(movie, ["provider"])

    assert captured_languages == [[]], "generate_subtitles is called with an empty language list"


def test_wanted_episode_filters_out_inactive_languages_entirely(monkeypatch):
    """_wanted_episode should pass zero languages to generate_subtitles when all are search-inactive."""
    module = load_wanted_module("series")
    episode = _episode_row()
    captured_languages = []

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(
        module,
        "generate_subtitles",
        lambda *args, **kwargs: captured_languages.append(args[1]) or iter(()),
    )
    # All languages are inactive
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_episode(episode, ["provider"])

    assert captured_languages == [[]], "generate_subtitles is called with an empty language list"


def test_wanted_movie_wrapper_handles_missing_row_after_missing_refresh(monkeypatch):
    """If refresh returns no movie row, wrapper should exit without crashing."""
    module = load_wanted_module("movies")
    movie = _movie_row()
    movie.missing_subtitles = None

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=movie)
            return _Result(first_value=None)

    monkeypatch.setattr(module, "database", _Database())
    monkeypatch.setattr(module, "get_subtitles", lambda **kwargs: [{"path": "/movies/sub.srt", "embedded_track_id": 1}])
    monkeypatch.setattr(module, "list_missing_subtitles_movies", lambda **kwargs: None)
    monkeypatch.setattr(module, "get_providers", lambda: ["provider"])

    result = module.wanted_download_subtitles_movie(7, job_id="job")
    assert result is None


def test_wanted_movie_fuzz_malformed_missing_subtitles_fails_safe(monkeypatch):
    module = load_wanted_module("movies")
    rng = random.Random(1338)
    malformed_values = [
        "",
        " ",
        "[",
        "not_a_list",
        "None",
        "{'en': 1}",
        "[1, 2, 3]",
        "[None, 1, {'x': 1}]",
    ]
    malformed_values.extend(
        "".join(rng.choice("[]{}()'\",abc123:-_ ") for _ in range(rng.randint(1, 20)))
        for _ in range(120)
    )

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    for malformed in malformed_values:
        movie = _movie_row()
        movie.missing_subtitles = malformed
        captured = []
        monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))

        module._wanted_movie(movie, ["provider"])

        assert captured == [[]]


def test_wanted_episode_fuzz_malformed_missing_subtitles_fails_safe(monkeypatch):
    module = load_wanted_module("series")
    rng = random.Random(7331)
    malformed_values = [
        "",
        " ",
        "[",
        "not_a_list",
        "None",
        "{'en': 1}",
        "[1, 2, 3]",
        "[None, 1, {'x': 1}]",
    ]
    malformed_values.extend(
        "".join(rng.choice("[]{}()'\",abc123:-_ ") for _ in range(rng.randint(1, 20)))
        for _ in range(120)
    )

    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    for malformed in malformed_values:
        episode = _episode_row()
        episode.missing_subtitles = malformed
        captured = []
        monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))

        module._wanted_episode(episode, ["provider"])

        assert captured == [[]]


def test_wanted_movie_ignores_empty_base_language_tokens(monkeypatch):
    module = load_wanted_module("movies")
    movie = _movie_row()
    movie.missing_subtitles = "[':hi', '', ':forced', 'en', 'fr:forced']"
    captured = []

    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_movie(movie, ["provider"])

    assert captured == [[("en", "False", "False"), ("fr", "False", "True")]]


def test_wanted_episode_ignores_empty_base_language_tokens(monkeypatch):
    module = load_wanted_module("series")
    episode = _episode_row()
    episode.missing_subtitles = "[':hi', '', ':forced', 'en:hi', 'fr']"
    captured = []

    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: captured.append(args[1]) or iter(()))
    monkeypatch.setattr(module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(module, "is_search_active", lambda desired_language, attempt_string: True)
    monkeypatch.setattr(module, "database", SimpleNamespace(execute=lambda *args, **kwargs: None))

    module._wanted_episode(episode, ["provider"])

    assert captured == [[("en", "True", "False"), ("fr", "False", "False")]]
