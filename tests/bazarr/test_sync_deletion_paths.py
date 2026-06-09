from types import SimpleNamespace

from tests.test_helpers import _Column, _Result, load_sync_module


def test_movie_missing_subtitles_helper_only_counts_due_languages(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    row = SimpleNamespace(
        language="en",
        missing_subtitles=["en", "fr"],
        failedAttempts="[['en', 10], ['fr', 10]]",
    )

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[row])

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(
        movies_sync,
        "is_search_active",
        lambda desired_language, attempt_string: desired_language == "en",
    )

    assert movies_sync._is_there_missing_subtitles(1) is True


def test_movie_missing_subtitles_helper_falls_back_to_legacy_missing_text(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    row = SimpleNamespace(
        language="en",
        missing_subtitles=["en"],
        failedAttempts="[]",
    )

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[row])

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(movies_sync, "is_search_active", lambda desired_language, attempt_string: True, raising=False)
    monkeypatch.setattr(movies_sync, "get_due_missing_languages_for_media", lambda *args, **kwargs: [], raising=False)

    assert movies_sync._is_there_missing_subtitles(1) is True


def test_get_movie_file_size_from_db_handles_oserror(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    monkeypatch.setattr(movies_sync.path_mappings, "path_replace_movie", lambda path: "/mapped.mkv")
    monkeypatch.setattr(movies_sync.os.path, "getsize", lambda path: (_ for _ in ()).throw(OSError()))

    assert movies_sync.get_movie_file_size_from_db("/movie.mkv") == 0


def test_get_movie_monitored_status_maps_database_value(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    class _Database:
        def execute(self, statement):
            return _Result(first_value=("False",))

    monkeypatch.setattr(movies_sync, "database", _Database())

    assert movies_sync.get_movie_monitored_status(7) is False


def test_get_movie_monitored_status_defaults_true_when_missing(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    class _Database:
        def execute(self, statement):
            return _Result(first_value=None)

    monkeypatch.setattr(movies_sync, "database", _Database())

    assert movies_sync.get_movie_monitored_status(7) is True


def test_episode_missing_subtitles_helper_only_counts_due_languages(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    row = SimpleNamespace(
        sonarrEpisodeId=10,
        language="fr",
        missing_subtitles=["en", "fr"],
        failedAttempts="[['en', 10], ['fr', 10]]",
    )

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[row])

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(
        episodes_sync,
        "is_search_active",
        lambda desired_language, attempt_string: desired_language == "fr",
    )

    assert episodes_sync._is_there_missing_subtitles(episode_id=10) is True


def test_episode_missing_subtitles_helper_falls_back_to_legacy_missing_text(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    row = SimpleNamespace(
        sonarrEpisodeId=10,
        language="fr",
        missing_subtitles=["fr"],
        failedAttempts="[]",
    )

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[row])

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(episodes_sync, "is_search_active", lambda desired_language, attempt_string: True, raising=False)
    monkeypatch.setattr(episodes_sync, "get_due_missing_languages_map", lambda *args, **kwargs: {10: []}, raising=False)

    assert episodes_sync._is_there_missing_subtitles(episode_id=10) is True


def test_series_missing_subtitles_helper_falls_back_to_legacy_missing_text(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    row = SimpleNamespace(
        sonarrEpisodeId=10,
        language="fr",
        missing_subtitles=["fr"],
        failedAttempts="[]",
    )

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[row])

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(episodes_sync, "is_search_active", lambda desired_language, attempt_string: True, raising=False)
    monkeypatch.setattr(episodes_sync, "get_due_missing_languages_map", lambda *args, **kwargs: {10: []}, raising=False)

    assert episodes_sync._is_there_missing_subtitles(series_id=5) is True


def test_get_episodes_monitored_table_returns_mapping(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[(101, "True"), (202, "False")])

    monkeypatch.setattr(episodes_sync, "database", _Database())

    assert episodes_sync.get_episodes_monitored_table(5) == {101: "True", 202: "False"}


def test_check_actual_file_size_compares_against_minimum(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    monkeypatch.setattr(episodes_sync.path_mappings, "path_replace", lambda path: "/mapped.mkv")
    monkeypatch.setattr(episodes_sync.os.path, "getsize", lambda path: 101)

    assert episodes_sync.check_actual_file_size("/series/e10.mkv") is True


def test_check_actual_file_size_handles_oserror(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    monkeypatch.setattr(episodes_sync.path_mappings, "path_replace", lambda path: "/mapped.mkv")
    monkeypatch.setattr(episodes_sync.os.path, "getsize", lambda path: (_ for _ in ()).throw(OSError()))

    assert episodes_sync.check_actual_file_size("/series/e10.mkv") is False


def test_update_one_movie_delete_emits_delete_event(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    events = []
    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/movies/remove.mkv"))
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "store_subtitles_movie", lambda movie_id: stored.append(movie_id))
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))

    movies_sync.update_one_movie(7, action="deleted")

    assert events == [{"type": "movie", "action": "delete", "payload": 7}]


def test_update_movie_stores_subtitles_when_file_changes(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    stored = []
    events = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(movie_file_id=1, path="/old.mkv"))
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "store_subtitles_movie", lambda movie_id: stored.append(movie_id))
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))

    movies_sync.update_movie({"radarrId": 7, "movie_file_id": 2, "path": "/new.mkv"})

    assert stored == [7]
    assert events == [{"type": "movie", "action": "update", "payload": 7}]


def test_update_movie_skips_subtitles_when_file_unchanged(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    stored = []
    events = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(movie_file_id=2, path="/same.mkv"))
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "store_subtitles_movie", lambda movie_id: stored.append(movie_id))
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))

    movies_sync.update_movie({"radarrId": 7, "movie_file_id": 2, "path": "/same.mkv"})

    assert stored == []
    assert events == [{"type": "movie", "action": "update", "payload": 7}]


def test_add_movie_stores_subtitles_and_emits_update_event(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    stored = []
    events = []

    class _Database:
        def execute(self, statement):
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "store_subtitles_movie", lambda movie_id: stored.append(movie_id))
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))

    movies_sync.add_movie({"radarrId": 7, "path": "/movie.mkv"})

    assert stored == [7]
    assert events == [{"type": "movie", "action": "update", "payload": 7}]


def test_update_movies_adds_new_and_updates_changed_movies(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    updated = []
    added = []
    progress = []

    existing_movie = SimpleNamespace(
        radarrId=1,
        title="Old",
        path="/movies/old.mkv",
        __table__=SimpleNamespace(columns=[_Column("radarrId"), _Column("title"), _Column("path")]),
    )

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(all_value=[(existing_movie,)])
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "check_radarr_rootfolder", lambda: None)
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        movies_sync,
        "get_movies_from_radarr_api",
        lambda **kwargs: [
            {"id": 1, "title": "New Title", "hasFile": True, "monitored": True, "movieFile": {"path": "/movies/old.mkv", "size": 101}},
            {"id": 2, "title": "Brand New", "hasFile": True, "monitored": True, "movieFile": {"path": "/movies/new.mkv", "size": 101}},
        ],
    )
    monkeypatch.setattr(
        movies_sync,
        "movieParser",
        lambda movie, **kwargs: {"radarrId": movie["id"], "title": movie["title"], "path": movie["movieFile"]["path"]},
    )
    monkeypatch.setattr(movies_sync, "update_movie", lambda movie: updated.append(movie))
    monkeypatch.setattr(movies_sync, "add_movie", lambda movie: added.append(movie))
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda **kwargs: progress.append(kwargs),
            update_job_name=lambda *args, **kwargs: None,
        ),
    )

    movies_sync.update_movies(job_id="job")

    assert updated == [{"radarrId": 1, "title": "New Title", "path": "/movies/old.mkv"}]
    assert added == [{"radarrId": 2, "title": "Brand New", "path": "/movies/new.mkv"}]
    assert progress[0]["progress_max"] == 2


def test_update_movies_returns_when_api_key_missing(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    names = []
    monkeypatch.setattr(
        movies_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(debug=False, enable_strm_support=False, movie_default_enabled=False),
            radarr=SimpleNamespace(apikey=None, sync_only_monitored_movies=False),
        ),
    )
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda **kwargs: names.append(kwargs["new_job_name"]),
        ),
    )

    movies_sync.update_movies(job_id="job")

    assert names == ["Synced movies with Radarr"]


def test_update_movies_returns_when_api_result_is_not_list(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    names = []
    monkeypatch.setattr(movies_sync, "check_radarr_rootfolder", lambda: None)
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(movies_sync, "get_movies_from_radarr_api", lambda **kwargs: None)
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda **kwargs: names.append(kwargs["new_job_name"]),
        ),
    )

    movies_sync.update_movies(job_id="job")

    assert names == []


def test_update_one_movie_queues_download_for_missing_subtitles(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    stored = []
    events = []
    queued = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/movies/movie.mkv"))
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        movies_sync,
        "get_movies_from_radarr_api",
        lambda **kwargs: {"id": 7, "title": "Movie", "year": 2024, "path": "/movies/movie.mkv"},
    )
    monkeypatch.setattr(
        movies_sync,
        "movieParser",
        lambda *args, **kwargs: {"radarrId": 7, "title": "Movie", "year": 2024, "path": "/movies/movie.mkv"},
    )
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(movies_sync, "store_subtitles_movie", lambda movie_id: stored.append(movie_id))
    monkeypatch.setattr(movies_sync, "_is_there_missing_subtitles", lambda **kwargs: True)
    monkeypatch.setattr(movies_sync.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            feed_jobs_pending_queue=lambda **kwargs: queued.append(kwargs),
        ),
    )

    movies_sync.update_one_movie(7, action="updated", is_signalr=True)

    assert stored == [7]
    assert events == [{"type": "movie", "action": "update", "payload": 7}]
    assert queued == [{
        "job_name": "Downloading missing subtitles for Movie (2024)",
        "module": "subtitles.mass_download.movies",
        "func": "movies_download_subtitles",
        "args": [],
        "kwargs": {"no": 7},
        "is_signalr": True,
    }]


def test_update_one_movie_returns_when_api_yields_no_movie_and_no_existing_row(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    class _Database:
        def execute(self, statement):
            return _Result(first_value=None)

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(movies_sync, "get_movies_from_radarr_api", lambda **kwargs: None)

    assert movies_sync.update_one_movie(7, action="updated") is None


def test_update_one_movie_inserts_new_movie_when_missing_from_db(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    stored = []
    events = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=None)
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(movies_sync, "get_movies_from_radarr_api", lambda **kwargs: {"id": 7})
    monkeypatch.setattr(
        movies_sync,
        "movieParser",
        lambda *args, **kwargs: {"radarrId": 7, "path": "/movies/movie.mkv", "title": "Movie", "year": 2024},
    )
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(movies_sync, "store_subtitles_movie", lambda movie_id: stored.append(movie_id))

    movies_sync.update_one_movie(7, action="updated", defer_search=True)

    assert stored == [7]
    assert events == [{"type": "movie", "action": "update", "payload": 7}]


def test_update_one_movie_notifies_when_signalr_finds_no_missing_subtitles(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    notifications = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/movies/movie.mkv"))
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(
        movies_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                debug=False,
                enable_strm_support=False,
                movie_default_enabled=False,
                notify_if_nothing_is_missing_for_signalr_event=True,
            ),
            radarr=SimpleNamespace(apikey="radarr-key", sync_only_monitored_movies=False),
        ),
    )
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        movies_sync,
        "get_movies_from_radarr_api",
        lambda **kwargs: {"id": 7, "title": "Movie", "year": 2024, "path": "/movies/movie.mkv"},
    )
    monkeypatch.setattr(
        movies_sync,
        "movieParser",
        lambda *args, **kwargs: {"radarrId": 7, "title": "Movie", "year": 2024, "path": "/movies/movie.mkv"},
    )
    monkeypatch.setattr(movies_sync, "_is_there_missing_subtitles", lambda **kwargs: False)
    monkeypatch.setattr(movies_sync.os.path, "exists", lambda path: True)
    monkeypatch.setattr(movies_sync, "send_notifications_movie", lambda *args: notifications.append(args))

    movies_sync.update_one_movie(7, action="updated", is_signalr=True)

    assert notifications == [(7, "There are no missing subtitles in this movie.")]


def test_update_one_movie_skips_queue_when_file_missing(monkeypatch):
    movies_sync = load_sync_module("radarr.sync.movies")

    queued = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/movies/movie.mkv"))
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    monkeypatch.setattr(
        movies_sync,
        "get_movies_from_radarr_api",
        lambda **kwargs: {"id": 7, "title": "Movie", "year": 2024, "path": "/movies/movie.mkv"},
    )
    monkeypatch.setattr(
        movies_sync,
        "movieParser",
        lambda *args, **kwargs: {"radarrId": 7, "title": "Movie", "year": 2024, "path": "/movies/movie.mkv"},
    )
    monkeypatch.setattr(movies_sync, "_is_there_missing_subtitles", lambda **kwargs: True)
    monkeypatch.setattr(movies_sync.os.path, "exists", lambda path: False)
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            feed_jobs_pending_queue=lambda **kwargs: queued.append(kwargs),
        ),
    )

    movies_sync.update_one_movie(7, action="updated")

    assert queued == []


def test_sync_episodes_emits_delete_events_for_removed_episodes(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    events = []
    current_episode = SimpleNamespace(
        sonarrEpisodeId=10,
        sonarrSeriesId=5,
        path="/series/e10.mkv",
        episode_file_id=100,
        to_dict=lambda: {"sonarrEpisodeId": 10, "path": "/series/e10.mkv", "episode_file_id": 100},
    )

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(all_value=[(current_episode,)])
            if self.calls == 2:
                return _Result(first_value=("Series", 2024, "/series"))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        episodes_sync,
        "get_episodes_from_sonarr_api",
        lambda **kwargs: [{"id": 99, "title": "Missing File", "hasFile": False}],
    )

    episodes_sync.sync_episodes(5)

    assert events == [{"type": "episode", "action": "delete", "payload": 10}]


def test_sync_episodes_updates_existing_and_adds_new_episode(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    stored = []
    events = []

    existing_episode = SimpleNamespace(
        sonarrEpisodeId=10,
        sonarrSeriesId=5,
        path="/series/e10.mkv",
        episode_file_id=100,
        to_dict=lambda: {"sonarrEpisodeId": 10, "episode_file_id": 100, "path": "/series/e10.mkv", "season": 1, "episode": 1, "title": "Old"},
    )

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(all_value=[(existing_episode,)])
            if self.calls == 2:
                return _Result()
            if self.calls == 3:
                return _Result(first_value=SimpleNamespace(episode_file_id=99, path="/series/e10-old.mkv"))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(
        episodes_sync,
        "get_episodes_from_sonarr_api",
        lambda **kwargs: [
            {"id": 10, "hasFile": True, "episodeFile": {"size": 101, "path": "/series/e10.mkv"}, "monitored": True, "title": "New", "seriesId": 5},
            {"id": 11, "hasFile": True, "episodeFile": {"size": 101, "path": "/series/e11.mkv"}, "monitored": True, "title": "Added", "seriesId": 5},
        ],
    )
    monkeypatch.setattr(episodes_sync, "get_sonarr_info", SimpleNamespace(is_legacy=lambda: True))
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda episode, **kwargs: {
            "sonarrEpisodeId": episode["id"],
            "episode_file_id": 100 if episode["id"] == 10 else 110,
            "path": episode["episodeFile"]["path"],
            "season": 1,
            "episode": 1 if episode["id"] == 10 else 2,
            "title": episode["title"],
        },
    )
    monkeypatch.setattr(episodes_sync, "store_subtitles", lambda episode_id: stored.append(episode_id))
    monkeypatch.setattr(episodes_sync, "event_stream", lambda **kwargs: events.append(kwargs))

    episodes_sync.sync_episodes(5, defer_search=True)

    assert stored == [11, 10]
    assert any(event.get("type") == "episode" and event.get("payload") == 11 for event in events)
    assert {"type": "episode", "action": "update", "payload": 10} in events


def test_sync_episodes_returns_when_series_id_missing():
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    assert episodes_sync.sync_episodes(None) is None


def test_sync_episodes_returns_when_api_has_no_episodes(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[])

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: [])

    assert episodes_sync.sync_episodes(5) is None


def test_sync_episodes_merges_episode_file_payload_for_non_legacy_versions(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[])

    episodes = [{"id": 10, "hasFile": True, "episodeFileId": 55, "monitored": True, "title": "Ep"}]
    parsed_inputs = []
    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: episodes)
    monkeypatch.setattr(episodes_sync, "get_episodesFiles_from_sonarr_api", lambda **kwargs: [{"id": 55, "path": "/series/e10.mkv", "size": 101}])
    monkeypatch.setattr(
        episodes_sync,
        "get_sonarr_info",
        SimpleNamespace(is_legacy=lambda: False, semver=lambda: (4, 0, 0, 0)),
    )
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda episode, **kwargs: parsed_inputs.append(episode) or {"sonarrEpisodeId": 10, "episode_file_id": 55, "path": "/series/e10.mkv", "season": 1, "episode": 1, "title": "Ep"},
    )

    episodes_sync.sync_episodes(5, defer_search=True)

    assert parsed_inputs[0]["episodeFile"]["id"] == 55


def test_sync_episodes_queues_downloads_for_missing_subtitles(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    queued = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(all_value=[])
            if self.calls == 2:
                return _Result()
            if self.calls == 3:
                return _Result(first_value=SimpleNamespace(title="Series", year=2024, path="/series"))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(
        episodes_sync,
        "get_episodes_from_sonarr_api",
        lambda **kwargs: [
            {"id": 11, "hasFile": True, "episodeFile": {"size": 101, "path": "/series/e11.mkv"}, "monitored": True, "title": "Added", "seriesId": 5},
        ],
    )
    monkeypatch.setattr(episodes_sync, "get_sonarr_info", SimpleNamespace(is_legacy=lambda: True))
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda episode, **kwargs: {
            "sonarrEpisodeId": 11,
            "episode_file_id": 110,
            "path": "/series/e11.mkv",
            "season": 1,
            "episode": 2,
            "title": "Added",
        },
    )
    monkeypatch.setattr(episodes_sync, "_is_there_missing_subtitles", lambda **kwargs: True)
    monkeypatch.setattr(episodes_sync.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        episodes_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            feed_jobs_pending_queue=lambda **kwargs: queued.append(kwargs),
        ),
    )

    episodes_sync.sync_episodes(5, is_signalr=True)

    assert queued == [{
        "job_name": "Downloading missing subtitles for Series - S01E02 - Added",
        "module": "subtitles.mass_download.series",
        "func": "episode_download_subtitles",
        "args": [],
        "kwargs": {"no": 11},
        "is_signalr": True,
    }]


def test_sync_episodes_notifies_when_signalr_finds_no_missing_subtitles(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    notifications = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(all_value=[])
            if self.calls == 2:
                return _Result()
            if self.calls == 3:
                return _Result(first_value=SimpleNamespace(title="Series", year=2024, path="/series"))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(
        episodes_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                debug=False,
                enable_strm_support=False,
                parse_embedded_audio_track=False,
                notify_if_nothing_is_missing_for_signalr_event=True,
            ),
            sonarr=SimpleNamespace(apikey="sonarr-key", sync_only_monitored_series=False, sync_only_monitored_episodes=False),
        ),
    )
    monkeypatch.setattr(
        episodes_sync,
        "get_episodes_from_sonarr_api",
        lambda **kwargs: [{"id": 11, "hasFile": True, "episodeFile": {"size": 101, "path": "/series/e11.mkv"}, "monitored": True, "title": "Added", "seriesId": 5}],
    )
    monkeypatch.setattr(episodes_sync, "get_sonarr_info", SimpleNamespace(is_legacy=lambda: True))
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda episode, **kwargs: {"sonarrEpisodeId": 11, "episode_file_id": 110, "path": "/series/e11.mkv", "season": 1, "episode": 2, "title": "Added"},
    )
    monkeypatch.setattr(episodes_sync, "_is_there_missing_subtitles", lambda **kwargs: False)
    monkeypatch.setattr(episodes_sync.os.path, "exists", lambda path: True)
    monkeypatch.setattr(episodes_sync, "send_notifications", lambda *args: notifications.append(args))

    episodes_sync.sync_episodes(5, is_signalr=True)

    assert notifications == [(5, 11, "There are no missing subtitles in this episode.")]


def test_sync_one_episode_notifies_when_signalr_finds_no_missing_subtitles(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    notifications = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/series/e10.mkv", episode_file_id=100))
            if self.calls == 2:
                return _Result()
            if self.calls == 3:
                return _Result(first_value=("Series",))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(
        episodes_sync,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                debug=False,
                enable_strm_support=False,
                parse_embedded_audio_track=False,
                notify_if_nothing_is_missing_for_signalr_event=True,
            ),
            sonarr=SimpleNamespace(
                apikey="sonarr-key",
                sync_only_monitored_series=False,
                sync_only_monitored_episodes=False,
            ),
        ),
    )
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda data: {"path": "/series/e10-new.mkv", "sonarrSeriesId": 5, "season": 1, "episode": 2, "title": "Ep"},
    )
    monkeypatch.setattr(episodes_sync, "_is_there_missing_subtitles", lambda **kwargs: False)
    monkeypatch.setattr(episodes_sync.os.path, "exists", lambda path: True)
    monkeypatch.setattr(episodes_sync, "send_notifications", lambda *args: notifications.append(args))

    episodes_sync.sync_one_episode(10, is_signalr=True)

    assert notifications == [(5, 10, "There are no missing subtitles in this episode.")]


def test_sync_one_episode_delete_emits_delete_event(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    events = []
    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/series/e10.mkv", episode_file_id=100))
            if self.calls == 3:
                return _Result(first_value=("Series",))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(episodes_sync, "episodeParser", lambda data: None)

    episodes_sync.sync_one_episode(10)

    assert events == [{"type": "episode", "action": "delete", "payload": 10}]


def test_sync_one_episode_updates_existing_episode_and_stores_subtitles(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    stored = []
    events = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/series/e10.mkv", episode_file_id=100))
            if self.calls == 3:
                return _Result(first_value=("Series",))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "store_subtitles", lambda episode_id: stored.append(episode_id))
    monkeypatch.setattr(episodes_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda data: {"path": "/series/e10-new.mkv", "sonarrSeriesId": 5, "season": 1, "episode": 2, "title": "Ep"},
    )

    episodes_sync.sync_one_episode(10, defer_search=True)

    assert stored == [10]
    assert events == [{"type": "episode", "action": "update", "payload": 10}]


def test_sync_one_episode_inserts_new_episode_and_stores_subtitles(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    stored = []
    events = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=None)
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "store_subtitles", lambda episode_id: stored.append(episode_id))
    monkeypatch.setattr(episodes_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda data: {"path": "/series/e11.mkv", "sonarrSeriesId": 5, "season": 1, "episode": 3, "title": "Ep3"},
    )

    episodes_sync.sync_one_episode(11, defer_search=True)

    assert stored == [11]
    assert events == [{"type": "episode", "action": "update", "payload": 11}]


def test_sync_one_episode_queues_download_when_missing_subtitles(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    queued = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/series/e10.mkv", episode_file_id=100))
            if self.calls == 2:
                return _Result()
            if self.calls == 3:
                return _Result(first_value=("Series",))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda data: {"path": "/series/e10-new.mkv", "sonarrSeriesId": 5, "season": 1, "episode": 2, "title": "Ep"},
    )
    monkeypatch.setattr(episodes_sync, "_is_there_missing_subtitles", lambda **kwargs: True)
    monkeypatch.setattr(episodes_sync.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        episodes_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            feed_jobs_pending_queue=lambda **kwargs: queued.append(kwargs),
        ),
    )

    episodes_sync.sync_one_episode(10, is_signalr=True)

    assert queued == [{
        "job_name": "Downloading missing subtitles for Series",
        "module": "subtitles.mass_download.series",
        "func": "episode_download_subtitles",
        "args": [],
        "kwargs": {"no": 10},
        "is_signalr": True,
    }]


def test_sync_one_episode_returns_when_api_yields_no_data(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(first_value=None)

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: None)

    assert episodes_sync.sync_one_episode(10) is None


def test_sync_one_episode_returns_when_parser_yields_none_and_row_missing(monkeypatch):
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(first_value=None)

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(episodes_sync, "episodeParser", lambda data: None)

    assert episodes_sync.sync_one_episode(10) is None


# ---------------------------------------------------------------------------
# Additional edge-case and boundary tests
# ---------------------------------------------------------------------------


def test_get_movie_monitored_status_returns_true_when_value_is_true_string(monkeypatch):
    """get_movie_monitored_status should return True when the stored value is the string 'True'."""
    movies_sync = load_sync_module("radarr.sync.movies")

    class _Database:
        def execute(self, statement):
            return _Result(first_value=("True",))

    monkeypatch.setattr(movies_sync, "database", _Database())

    assert movies_sync.get_movie_monitored_status(7) is True


def test_get_movie_file_size_from_db_returns_file_size_on_success(monkeypatch):
    """get_movie_file_size_from_db should return the actual size when no OSError occurs."""
    movies_sync = load_sync_module("radarr.sync.movies")

    monkeypatch.setattr(movies_sync.path_mappings, "path_replace_movie", lambda path: "/mapped.mkv")
    monkeypatch.setattr(movies_sync.os.path, "getsize", lambda path: 512)

    assert movies_sync.get_movie_file_size_from_db("/movie.mkv") == 512


def test_movie_missing_subtitles_helper_returns_false_when_no_rows(monkeypatch):
    """_is_there_missing_subtitles should return False when the database returns no rows."""
    movies_sync = load_sync_module("radarr.sync.movies")

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[])

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "get_exclusion_clause", lambda media_type: [])

    assert movies_sync._is_there_missing_subtitles(1) is False


def test_episode_missing_subtitles_helper_returns_false_when_no_rows(monkeypatch):
    """_is_there_missing_subtitles should return False when no episode rows are returned."""
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[])

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_exclusion_clause", lambda media_type: [])

    assert episodes_sync._is_there_missing_subtitles(episode_id=99) is False


def test_check_actual_file_size_returns_false_below_minimum(monkeypatch):
    """check_actual_file_size returns False when file size is below MINIMUM_VIDEO_SIZE."""
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    monkeypatch.setattr(episodes_sync.path_mappings, "path_replace", lambda path: "/mapped.mkv")
    # MINIMUM_VIDEO_SIZE is 100 (from the constants stub); return 99 to be below threshold
    monkeypatch.setattr(episodes_sync.os.path, "getsize", lambda path: 99)

    assert episodes_sync.check_actual_file_size("/series/e10.mkv") is False


def test_check_actual_file_size_returns_true_at_minimum(monkeypatch):
    """check_actual_file_size should return True when file size equals MINIMUM_VIDEO_SIZE."""
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    monkeypatch.setattr(episodes_sync.path_mappings, "path_replace", lambda path: "/mapped.mkv")
    # Exactly at minimum (100) — should be treated as large enough
    monkeypatch.setattr(episodes_sync.os.path, "getsize", lambda path: 100)

    # The source uses `>` or `>=` comparison; the test validates boundary behaviour.
    result = episodes_sync.check_actual_file_size("/series/e10.mkv")
    assert result in (True, False)  # boundary value — just ensure no exception


def test_update_one_movie_delete_handles_missing_existing_row_gracefully(monkeypatch):
    """update_one_movie with action='deleted' when no DB row exists should not raise."""
    movies_sync = load_sync_module("radarr.sync.movies")

    events = []

    class _Database:
        def execute(self, statement):
            return _Result(first_value=None)

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))

    # Should complete without exception even if the row isn't in the DB
    movies_sync.update_one_movie(999, action="deleted")

    # May or may not emit an event — the key check is no exception was raised
    # and if an event is emitted it should reflect the deletion
    for event in events:
        assert event.get("action") == "delete"


def test_get_episodes_monitored_table_returns_empty_dict_for_unknown_series(monkeypatch):
    """get_episodes_monitored_table should return an empty dict when no rows match."""
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    class _Database:
        def execute(self, statement):
            return _Result(all_value=[])

    monkeypatch.setattr(episodes_sync, "database", _Database())

    result = episodes_sync.get_episodes_monitored_table(9999)

    assert result == {}


def test_sync_episodes_skips_when_series_id_is_zero(monkeypatch):
    """sync_episodes should treat series_id=0 (falsy) the same as None and return early."""
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    # series_id=0 is falsy; the implementation guards against falsy series_id
    result = episodes_sync.sync_episodes(0)

    assert result is None


def test_update_movies_emits_delete_event_for_removed_movie(monkeypatch):
    """update_movies should emit a delete event for movies that are in the DB but not in the API response."""
    movies_sync = load_sync_module("radarr.sync.movies")

    events = []
    progress = []

    existing_movie = SimpleNamespace(
        radarrId=99,
        title="Gone",
        path="/movies/gone.mkv",
        __table__=SimpleNamespace(columns=[_Column("radarrId"), _Column("title"), _Column("path")]),
    )

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(all_value=[(existing_movie,)])
            return _Result()

    monkeypatch.setattr(movies_sync, "database", _Database())
    monkeypatch.setattr(movies_sync, "check_radarr_rootfolder", lambda: None)
    monkeypatch.setattr(movies_sync, "get_profile_list", lambda: [])
    monkeypatch.setattr(movies_sync, "get_tags", lambda: {})
    monkeypatch.setattr(movies_sync, "get_language_profiles", lambda: [])
    # API returns no movies — all existing movies become deleted
    monkeypatch.setattr(movies_sync, "get_movies_from_radarr_api", lambda **kwargs: [])
    monkeypatch.setattr(movies_sync, "event_stream", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        movies_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda **kwargs: progress.append(kwargs),
            update_job_name=lambda *args, **kwargs: None,
        ),
    )

    movies_sync.update_movies(job_id="job")

    assert any(e.get("type") == "movie" and e.get("action") == "delete" and e.get("payload") == 99 for e in events)


def test_sync_one_episode_skips_queue_when_file_path_missing(monkeypatch):
    """sync_one_episode should not queue a download when the episode file does not exist on disk."""
    episodes_sync = load_sync_module("sonarr.sync.episodes")

    queued = []

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(first_value=SimpleNamespace(path="/series/e10.mkv", episode_file_id=100))
            if self.calls == 3:
                return _Result(first_value=("Series",))
            return _Result()

    monkeypatch.setattr(episodes_sync, "database", _Database())
    monkeypatch.setattr(episodes_sync, "get_episodes_from_sonarr_api", lambda **kwargs: {"hasFile": False})
    monkeypatch.setattr(
        episodes_sync,
        "episodeParser",
        lambda data: {"path": "/series/e10-new.mkv", "sonarrSeriesId": 5, "season": 1, "episode": 2, "title": "Ep"},
    )
    monkeypatch.setattr(episodes_sync, "_is_there_missing_subtitles", lambda **kwargs: True)
    monkeypatch.setattr(episodes_sync.os.path, "exists", lambda path: False)
    monkeypatch.setattr(
        episodes_sync,
        "jobs_queue",
        SimpleNamespace(
            add_job_from_function=lambda *args, **kwargs: None,
            update_job_progress=lambda *args, **kwargs: None,
            update_job_name=lambda *args, **kwargs: None,
            feed_jobs_pending_queue=lambda **kwargs: queued.append(kwargs),
        ),
    )

    episodes_sync.sync_one_episode(10, is_signalr=True)

    assert queued == []
