# -*- coding: utf-8 -*-

from datetime import timedelta
from types import SimpleNamespace


_POLICY = {
    "delay": timedelta(weeks=3),
    "delta": timedelta(weeks=1),
    "delay_label": "3w",
    "delta_label": "1w",
    "now": SimpleNamespace(timestamp=lambda: 100.0),
    "initial_search_cutoff": 100.0 - timedelta(weeks=3).total_seconds(),
    "latest_search_cutoff": 100.0 - timedelta(weeks=1).total_seconds(),
}


class _Result:
    def __init__(self, all_value=None):
        self._all_value = [] if all_value is None else all_value
        self.all_called = False

    def __iter__(self):
        return iter(self._all_value)

    def all(self):
        self.all_called = True
        return self._all_value


def _job_queue():
    return SimpleNamespace(
        add_job_from_function=lambda *args, **kwargs: None,
        update_job_progress=lambda *args, **kwargs: None,
        update_job_name=lambda *args, **kwargs: None,
    )


class _Update:
    def __init__(self):
        self.values_kwargs = {}

    def values(self, **kwargs):
        self.values_kwargs = kwargs
        return self

    def where(self, *args):
        return self


def _capture_update(monkeypatch, module):
    updates = []

    def update(_table):
        statement = _Update()
        updates.append(statement)
        return statement

    monkeypatch.setattr(module, "update", update)
    return updates


def _patch_due_stream(monkeypatch, module, due_languages):
    due_languages = dict(due_languages)

    def iter_due_chunks(*args, batch_size=None, **kwargs):
        items = list(due_languages.items())
        if batch_size is None:
            batch_size = len(items) or 1
        for index in range(0, len(items), batch_size):
            yield dict(items[index:index + batch_size])

    monkeypatch.setattr(module, "count_due_missing_media", lambda *args, **kwargs: len(due_languages), raising=False)
    if hasattr(module, "_count_due_episodes"):
        monkeypatch.setattr(module, "_count_due_episodes", lambda *args, **kwargs: len(due_languages))
    if hasattr(module, "_count_due_movies"):
        monkeypatch.setattr(module, "_count_due_movies", lambda *args, **kwargs: len(due_languages))
    monkeypatch.setattr(module, "iter_due_missing_languages_maps", iter_due_chunks)


def _adaptive_policy(now_timestamp):
    return {
        "delay": timedelta(weeks=3),
        "delta": timedelta(weeks=1),
        "delay_label": "3w",
        "delta_label": "1w",
        "now": SimpleNamespace(timestamp=lambda: now_timestamp),
        "initial_search_cutoff": now_timestamp - timedelta(weeks=3).total_seconds(),
        "latest_search_cutoff": now_timestamp - timedelta(weeks=1).total_seconds(),
    }


def test_movie_detail_lookup_includes_subtitle_index_flags():
    from subtitles.wanted import movies as wanted_movies

    selected_column_names = {
        getattr(column, "name", None)
        for column in wanted_movies._WANTED_MOVIE_DETAILS_SELECT.selected_columns
    }

    assert "has_indexed_subtitles" in selected_column_names
    assert "has_incomplete_embedded_subtitles" in selected_column_names


def test_series_wanted_search_prefilters_adaptive_search_and_reuses_providers(monkeypatch):
    from subtitles.wanted import series as wanted_series

    rows = [
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=10,
            title="Due Series",
            season=1,
            episode=1,
            episodeTitle="Due",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            sonarrSeriesId=2,
            sonarrEpisodeId=20,
            title="Throttled Series",
            season=1,
            episode=2,
            episodeTitle="Skip",
            missing_subtitles="['fr']",
            failedAttempts="[['fr', 1.0], ['fr', 2.0]]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_calls = []
    searches = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_series, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    _patch_due_stream(monkeypatch, wanted_series, {10: ["en"], 20: []})
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers, **kwargs: searches.append((episode.sonarrEpisodeId, kwargs["due_languages"])),
    )

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert provider_calls == [True]
    assert searches == [(10, ["en"])]


def test_movie_wanted_search_prefilters_adaptive_search_and_reuses_providers(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    rows = [
        SimpleNamespace(
            radarrId=10,
            title="Due Movie",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            radarrId=20,
            title="Throttled Movie",
            missing_subtitles="['fr']",
            failedAttempts="[['fr', 1.0], ['fr', 2.0]]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_calls = []
    searches = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    _patch_due_stream(monkeypatch, wanted_movies, {10: ["en"], 20: []})
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie, providers, **kwargs: searches.append((movie.radarrId, kwargs["due_languages"])),
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert provider_calls == [True]
    assert searches == [(10, ["en"])]


def test_movie_wanted_search_refreshes_provider_availability(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    rows = [
        SimpleNamespace(
            radarrId=10,
            title="First Movie",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            radarrId=20,
            title="Second Movie",
            missing_subtitles="['fr']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_results = iter((["provider"], []))
    searches = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: next(provider_results))
    _patch_due_stream(monkeypatch, wanted_movies, {10: ["en"], 20: ["en"]})
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie, providers, **kwargs: searches.append((movie.radarrId, providers)),
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert searches == [(10, ["provider"])]


def test_movie_wanted_search_does_not_stamp_rows_after_providers_throttle(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    rows = [
        SimpleNamespace(
            radarrId=10,
            title="First Movie",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            radarrId=20,
            title="Second Movie",
            missing_subtitles="['fr']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_results = iter((["provider"], []))
    attempt_calls = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: next(provider_results))
    _patch_due_stream(monkeypatch, wanted_movies, {10: ["en"], 20: ["en"]})
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie, providers, **kwargs: attempt_calls.append((movie.radarrId, kwargs["due_languages"])),
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert attempt_calls == [(10, ["en"])]


def test_movie_wanted_search_batches_failed_attempt_updates(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    rows = [
        SimpleNamespace(
            radarrId=10,
            title="First Movie",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            radarrId=20,
            title="Second Movie",
            missing_subtitles="['fr']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def __init__(self):
            self.calls = []
            self.bind = SimpleNamespace(
                engine=SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
            )

        def execute(self, statement, params=None):
            self.calls.append((statement, params))
            if params is None:
                return _Result(all_value=rows)
            return _Result()

    database = _Database()
    batch_calls = []
    wanted_calls = []
    monkeypatch.setattr(wanted_movies, "database", database)
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: ["provider"])
    _patch_due_stream(monkeypatch, wanted_movies, {
        10: ["en"],
        20: ["fr"],
    })
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie, providers, **kwargs:
            wanted_calls.append((movie.radarrId, kwargs["defer_failed_attempts"])) or kwargs["due_languages"],
    )
    monkeypatch.setattr(
        wanted_movies,
        "record_failed_subtitle_attempts_map",
        lambda media_type, failures:
            batch_calls.append((media_type, failures)) or {10: "attempts-10", 20: "attempts-20"},
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert wanted_calls == [(10, True), (20, True)]
    assert batch_calls == [("movie", {10: ["en"], 20: ["fr"]})]
    assert database.calls[-1][1] is None
    assert "CASE" in str(database.calls[-1][0])


def test_movie_failed_attempt_legacy_update_uses_sqlite_temp_table(monkeypatch):
    import sqlalchemy as sa

    from subtitles.wanted import movies as wanted_movies

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    connection.exec_driver_sql(
        'CREATE TABLE table_movies ("radarrId" INTEGER PRIMARY KEY, "failedAttempts" TEXT)'
    )
    connection.exec_driver_sql(
        'INSERT INTO table_movies ("radarrId", "failedAttempts") VALUES (10, "old"), (20, "old")'
    )

    class _Database:
        bind = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))

        def connection(self):
            return connection

    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "_TEMP_FAILED_ATTEMPT_UPDATE_MIN_SIZE", 1)
    monkeypatch.setattr(
        wanted_movies,
        "record_failed_subtitle_attempts_map",
        lambda media_type, failures: {10: "attempts-10", 20: "attempts-20"},
    )

    try:
        wanted_movies._record_failed_movie_attempts({10: ["en"], 20: ["fr"]})

        assert connection.execute(
            sa.text('SELECT "radarrId", "failedAttempts" FROM table_movies ORDER BY "radarrId"')
        ).all() == [(10, "attempts-10"), (20, "attempts-20")]
    finally:
        connection.close()


def test_series_wanted_search_refreshes_provider_availability(monkeypatch):
    from subtitles.wanted import series as wanted_series

    rows = [
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=10,
            title="First Series",
            season=1,
            episode=1,
            episodeTitle="First",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=20,
            title="Second Series",
            season=1,
            episode=2,
            episodeTitle="Second",
            missing_subtitles="['fr']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_results = iter((["provider"], []))
    searches = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_series, "get_providers", lambda: next(provider_results))
    _patch_due_stream(monkeypatch, wanted_series, {10: ["en"], 20: ["en"]})
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers, **kwargs: searches.append((episode.sonarrEpisodeId, providers)),
    )

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert searches == [(10, ["provider"])]


def test_series_wanted_search_does_not_stamp_rows_after_providers_throttle(monkeypatch):
    from subtitles.wanted import series as wanted_series

    rows = [
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=10,
            title="First Series",
            season=1,
            episode=1,
            episodeTitle="First",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=20,
            title="Second Series",
            season=1,
            episode=2,
            episodeTitle="Second",
            missing_subtitles="['fr']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def execute(self, statement):
            return _Result(all_value=rows)

    provider_results = iter((["provider"], []))
    attempt_calls = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_series, "get_providers", lambda: next(provider_results))
    _patch_due_stream(monkeypatch, wanted_series, {10: ["en"], 20: ["en"]})
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers, **kwargs: attempt_calls.append((episode.sonarrEpisodeId, kwargs["due_languages"])),
    )

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert attempt_calls == [(10, ["en"])]


def test_series_wanted_search_batches_failed_attempt_updates(monkeypatch):
    from subtitles.wanted import series as wanted_series

    rows = [
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=10,
            title="First Series",
            season=1,
            episode=1,
            episodeTitle="First",
            missing_subtitles="['en']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
        SimpleNamespace(
            sonarrSeriesId=1,
            sonarrEpisodeId=20,
            title="Second Series",
            season=1,
            episode=2,
            episodeTitle="Second",
            missing_subtitles="['fr']",
            failedAttempts="[]",
            has_indexed_subtitles=True,
            has_incomplete_embedded_subtitles=False,
        ),
    ]

    class _Database:
        def __init__(self):
            self.calls = []
            self.bind = SimpleNamespace(
                engine=SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
            )

        def execute(self, statement, params=None):
            self.calls.append((statement, params))
            if params is None:
                return _Result(all_value=rows)
            return _Result()

    database = _Database()
    batch_calls = []
    wanted_calls = []
    monkeypatch.setattr(wanted_series, "database", database)
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_series, "get_providers", lambda: ["provider"])
    _patch_due_stream(monkeypatch, wanted_series, {
        10: ["en"],
        20: ["fr"],
    })
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers, **kwargs:
            wanted_calls.append((episode.sonarrEpisodeId, kwargs["defer_failed_attempts"])) or kwargs["due_languages"],
    )
    monkeypatch.setattr(
        wanted_series,
        "record_failed_subtitle_attempts_map",
        lambda media_type, failures:
            batch_calls.append((media_type, failures)) or {10: "attempts-10", 20: "attempts-20"},
    )

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert wanted_calls == [(10, True), (20, True)]
    assert batch_calls == [("series", {10: ["en"], 20: ["fr"]})]
    assert database.calls[-1][1] is None
    assert "CASE" in str(database.calls[-1][0])


def test_series_failed_attempt_legacy_update_uses_sqlite_temp_table(monkeypatch):
    import sqlalchemy as sa

    from subtitles.wanted import series as wanted_series

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    connection.exec_driver_sql(
        'CREATE TABLE table_episodes ("sonarrEpisodeId" INTEGER PRIMARY KEY, "failedAttempts" TEXT)'
    )
    connection.exec_driver_sql(
        'INSERT INTO table_episodes ("sonarrEpisodeId", "failedAttempts") VALUES (10, "old"), (20, "old")'
    )

    class _Database:
        bind = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))

        def connection(self):
            return connection

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "_TEMP_FAILED_ATTEMPT_UPDATE_MIN_SIZE", 1)
    monkeypatch.setattr(
        wanted_series,
        "record_failed_subtitle_attempts_map",
        lambda media_type, failures: {10: "attempts-10", 20: "attempts-20"},
    )

    try:
        wanted_series._record_failed_episode_attempts({10: ["en"], 20: ["fr"]})

        assert connection.execute(
            sa.text('SELECT "sonarrEpisodeId", "failedAttempts" FROM table_episodes ORDER BY "sonarrEpisodeId"')
        ).all() == [(10, "attempts-10"), (20, "attempts-20")]
    finally:
        connection.close()


def test_movie_wanted_search_uses_due_language_prefilter(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    due_row = SimpleNamespace(
        radarrId=10,
        title="Due Movie",
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )
    unknown_row = SimpleNamespace(
        radarrId=20,
        title="Unknown Movie",
        missing_subtitles="['fr']",
        failedAttempts="[['fr', 1.0], ['fr', 2.0]]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )

    class _Database:
        def __init__(self):
            self.results = iter((_Result(all_value=[due_row]), _Result(all_value=[unknown_row]), _Result()))
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return next(self.results)

    database = _Database()
    provider_calls = []
    searches = []
    monkeypatch.setattr(wanted_movies, "database", database)
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    _patch_due_stream(monkeypatch, wanted_movies, {10: ["en"], 20: []})
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie, providers, **kwargs: searches.append((movie.radarrId, kwargs["due_languages"])),
    )

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert len(database.statements) == 1
    assert searches == [(10, ["en"])]
    assert provider_calls == [True]


def test_series_wanted_search_uses_due_language_prefilter(monkeypatch):
    from subtitles.wanted import series as wanted_series

    due_row = SimpleNamespace(
        sonarrSeriesId=1,
        sonarrEpisodeId=10,
        title="Due Series",
        season=1,
        episode=1,
        episodeTitle="Due",
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )
    unknown_row = SimpleNamespace(
        sonarrSeriesId=1,
        sonarrEpisodeId=20,
        title="Unknown Series",
        season=1,
        episode=2,
        episodeTitle="Unknown",
        missing_subtitles="['fr']",
        failedAttempts="[['fr', 1.0], ['fr', 2.0]]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )

    class _Database:
        def __init__(self):
            self.results = iter((_Result(all_value=[due_row]), _Result(all_value=[unknown_row]), _Result()))
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return next(self.results)

    database = _Database()
    provider_calls = []
    searches = []
    monkeypatch.setattr(wanted_series, "database", database)
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_series, "get_providers", lambda: provider_calls.append(True) or ["provider"])
    _patch_due_stream(monkeypatch, wanted_series, {10: ["en"], 20: []})
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode, providers, **kwargs: searches.append((episode.sonarrEpisodeId, kwargs["due_languages"])),
    )

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert len(database.statements) == 1
    assert searches == [(10, ["en"])]
    assert provider_calls == [True]


def test_movie_download_refreshes_providers_after_wanted_state_rebuild(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    original_movie = SimpleNamespace(
        radarrId=10,
        title="Movie",
        missing_subtitles=None,
        failedAttempts="[]",
        has_indexed_subtitles=False,
        has_incomplete_embedded_subtitles=False,
    )
    refreshed_movie = SimpleNamespace(
        radarrId=10,
        title="Movie",
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )

    class _Database:
        def execute(self, statement, params=None):
            return SimpleNamespace(first=lambda: refreshed_movie)

    calls = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "get_subtitles", lambda **kwargs: [])
    monkeypatch.setattr(wanted_movies, "store_subtitles_movie", lambda radarr_id: calls.append(("store", radarr_id)))
    monkeypatch.setattr(wanted_movies, "list_missing_subtitles_movies", lambda no: calls.append(("missing", no)))
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: calls.append(("providers_refreshed",)) or [])
    monkeypatch.setattr(wanted_movies, "_wanted_movie", lambda *args, **kwargs: calls.append(("search",)))

    wanted_movies.wanted_download_subtitles_movie(
        original_movie.radarrId,
        movie=original_movie,
        providers_list=["stale-provider"],
    )

    assert calls == [("store", 10), ("providers_refreshed",)]


def test_series_download_refreshes_providers_after_wanted_state_rebuild(monkeypatch):
    from subtitles.wanted import series as wanted_series

    original_episode = SimpleNamespace(
        sonarrEpisodeId=10,
        missing_subtitles=None,
        failedAttempts="[]",
        has_indexed_subtitles=False,
        has_incomplete_embedded_subtitles=False,
    )
    refreshed_episode = SimpleNamespace(
        sonarrEpisodeId=10,
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )

    class _Database:
        def execute(self, statement, params=None):
            return SimpleNamespace(first=lambda: refreshed_episode)

    calls = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "get_subtitles", lambda **kwargs: [])
    monkeypatch.setattr(wanted_series, "store_subtitles", lambda sonarr_episode_id: calls.append(("store", sonarr_episode_id)))
    monkeypatch.setattr(wanted_series, "list_missing_subtitles", lambda epno: calls.append(("missing", epno)))
    monkeypatch.setattr(wanted_series, "get_providers", lambda: calls.append(("providers_refreshed",)) or [])
    monkeypatch.setattr(wanted_series, "_wanted_episode", lambda *args, **kwargs: calls.append(("search",)))

    wanted_series.wanted_download_subtitles(
        original_episode.sonarrEpisodeId,
        episode_details=original_episode,
        providers_list=["stale-provider"],
    )

    assert calls == [("store", 10), ("providers_refreshed",)]


def test_movie_download_uses_supplied_due_row_without_detail_lookup(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        radarrId=10,
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )

    calls = []
    monkeypatch.setattr(
        wanted_movies,
        "database",
        SimpleNamespace(execute=lambda *args, **kwargs: calls.append(("detail_lookup",))),
    )
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: calls.append(("policy",)) or _POLICY)
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie_arg, providers, **kwargs: calls.append((movie_arg.radarrId, providers, kwargs["due_languages"])),
    )

    wanted_movies.wanted_download_subtitles_movie(
        movie.radarrId,
        providers_list=["provider"],
        movie=movie,
        due_languages=["en"],
    )

    assert calls == [("policy",), (10, ["provider"], ["en"])]


def test_movie_download_preserves_due_languages_when_refresh_check_does_not_rebuild(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        radarrId=10,
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=False,
        has_incomplete_embedded_subtitles=False,
    )

    calls = []
    monkeypatch.setattr(
        wanted_movies,
        "database",
        SimpleNamespace(execute=lambda *args, **kwargs: calls.append(("detail_lookup",))),
    )
    monkeypatch.setattr(wanted_movies, "get_subtitles", lambda **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: calls.append(("policy",)) or _POLICY)
    monkeypatch.setattr(
        wanted_movies,
        "_wanted_movie",
        lambda movie_arg, providers, **kwargs: calls.append((movie_arg.radarrId, providers, kwargs["due_languages"])),
    )

    wanted_movies.wanted_download_subtitles_movie(
        movie.radarrId,
        providers_list=["provider"],
        movie=movie,
        due_languages=["en"],
    )

    assert calls == [("policy",), (10, ["provider"], ["en"])]


def test_series_download_uses_supplied_due_row_without_detail_lookup(monkeypatch):
    from subtitles.wanted import series as wanted_series

    episode = SimpleNamespace(
        sonarrEpisodeId=10,
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=True,
        has_incomplete_embedded_subtitles=False,
    )

    calls = []
    monkeypatch.setattr(
        wanted_series,
        "database",
        SimpleNamespace(execute=lambda *args, **kwargs: calls.append(("detail_lookup",))),
    )
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: calls.append(("policy",)) or _POLICY)
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode_arg, providers, **kwargs: calls.append((episode_arg.sonarrEpisodeId, providers, kwargs["due_languages"])),
    )

    wanted_series.wanted_download_subtitles(
        episode.sonarrEpisodeId,
        providers_list=["provider"],
        episode_details=episode,
        due_languages=["en"],
    )

    assert calls == [("policy",), (10, ["provider"], ["en"])]


def test_series_download_preserves_due_languages_when_refresh_check_does_not_rebuild(monkeypatch):
    from subtitles.wanted import series as wanted_series

    episode = SimpleNamespace(
        sonarrEpisodeId=10,
        missing_subtitles="['en']",
        failedAttempts="[]",
        has_indexed_subtitles=False,
        has_incomplete_embedded_subtitles=False,
    )

    calls = []
    monkeypatch.setattr(
        wanted_series,
        "database",
        SimpleNamespace(execute=lambda *args, **kwargs: calls.append(("detail_lookup",))),
    )
    monkeypatch.setattr(wanted_series, "get_subtitles", lambda **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: calls.append(("policy",)) or _POLICY)
    monkeypatch.setattr(
        wanted_series,
        "_wanted_episode",
        lambda episode_arg, providers, **kwargs: calls.append((episode_arg.sonarrEpisodeId, providers, kwargs["due_languages"])),
    )

    wanted_series.wanted_download_subtitles(
        episode.sonarrEpisodeId,
        providers_list=["provider"],
        episode_details=episode,
        due_languages=["en"],
    )

    assert calls == [("policy",), (10, ["provider"], ["en"])]


def test_movie_missing_subtitle_indexer_reuses_one_adaptive_policy_snapshot(monkeypatch):
    from subtitles.indexer import movies as movie_indexer

    rows = [
        SimpleNamespace(radarrId=10, profileId=1, audio_language="eng", missing_subtitles="[]"),
        SimpleNamespace(radarrId=20, profileId=1, audio_language="eng", missing_subtitles="[]"),
    ]

    class _Database:
        def __init__(self):
            self.selected = False
            self.statements = []
            self.select_result = _Result(all_value=rows)

        def execute(self, statement):
            self.statements.append(statement)
            if not self.selected:
                self.selected = True
                return self.select_result
            return _Result()

    policy = {"policy": "snapshot"}
    audio_calls = []
    refresh_calls = []
    database = _Database()
    monkeypatch.setattr(movie_indexer, "database", database)
    monkeypatch.setattr(movie_indexer, "settings", SimpleNamespace(general=SimpleNamespace(use_embedded_subs=True)))
    monkeypatch.setattr(movie_indexer, "get_adaptive_search_policy", lambda: policy)
    monkeypatch.setattr(
        movie_indexer,
        "get_audio_profile_languages",
        lambda audio_language: audio_calls.append(audio_language) or [{"code2": "en"}],
    )
    monkeypatch.setattr(movie_indexer, "get_profiles_list", lambda profile_id: {
        "items": [
            {"language": "en", "forced": "False", "hi": "False", "audio_exclude": "True", "audio_only_include": "False"},
            {"language": "fr", "forced": "False", "hi": "False", "audio_exclude": "False", "audio_only_include": "False"},
        ],
    })
    monkeypatch.setattr(movie_indexer, "get_subtitles", lambda **kwargs: [])
    monkeypatch.setattr(movie_indexer, "get_profile_cutoff", lambda profile_id: [])
    monkeypatch.setattr(movie_indexer, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        movie_indexer,
        "refresh_wanted_search_state",
        lambda media_type, media_id, missing, **kwargs:
            refresh_calls.append((media_type, media_id, missing, kwargs)),
    )

    movie_indexer.list_missing_subtitles_movies()

    assert database.select_result.all_called is False
    assert audio_calls == ["eng", "eng"]
    assert len(database.statements) == 3
    assert refresh_calls == [
        ("movie", 10, "['fr']", {"adaptive_search_policy": policy, "refresh_failed_attempts": False}),
        ("movie", 20, "['fr']", {"adaptive_search_policy": policy, "refresh_failed_attempts": False}),
    ]


def test_series_missing_subtitle_indexer_reuses_one_adaptive_policy_snapshot(monkeypatch):
    from subtitles.indexer import series as series_indexer

    rows = [
        SimpleNamespace(sonarrSeriesId=1, sonarrEpisodeId=10, profileId=1, audio_language="eng", missing_subtitles="[]"),
        SimpleNamespace(sonarrSeriesId=1, sonarrEpisodeId=20, profileId=1, audio_language="eng", missing_subtitles="[]"),
    ]

    class _Database:
        def __init__(self):
            self.selected = False
            self.statements = []
            self.select_result = _Result(all_value=rows)

        def execute(self, statement):
            self.statements.append(statement)
            if not self.selected:
                self.selected = True
                return self.select_result
            return _Result()

    policy = {"policy": "snapshot"}
    audio_calls = []
    refresh_calls = []
    database = _Database()
    monkeypatch.setattr(series_indexer, "database", database)
    monkeypatch.setattr(series_indexer, "settings", SimpleNamespace(general=SimpleNamespace(use_embedded_subs=True)))
    monkeypatch.setattr(series_indexer, "get_adaptive_search_policy", lambda: policy)
    monkeypatch.setattr(
        series_indexer,
        "get_audio_profile_languages",
        lambda audio_language: audio_calls.append(audio_language) or [{"code2": "en"}],
    )
    monkeypatch.setattr(series_indexer, "get_profiles_list", lambda profile_id: {
        "items": [
            {"language": "en", "forced": "False", "hi": "False", "audio_exclude": "True", "audio_only_include": "False"},
            {"language": "fr", "forced": "False", "hi": "False", "audio_exclude": "False", "audio_only_include": "False"},
        ],
    })
    monkeypatch.setattr(series_indexer, "get_subtitles", lambda **kwargs: [])
    monkeypatch.setattr(series_indexer, "get_profile_cutoff", lambda profile_id: [])
    monkeypatch.setattr(series_indexer, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        series_indexer,
        "refresh_wanted_search_state",
        lambda media_type, media_id, missing, **kwargs:
            refresh_calls.append((media_type, media_id, missing, kwargs)),
    )

    series_indexer.list_missing_subtitles()

    assert database.select_result.all_called is False
    assert audio_calls == ["eng", "eng"]
    assert len(database.statements) == 3
    assert refresh_calls == [
        ("series", 10, "['fr']", {"adaptive_search_policy": policy, "refresh_failed_attempts": False}),
        ("series", 20, "['fr']", {"adaptive_search_policy": policy, "refresh_failed_attempts": False}),
    ]


def test_movie_missing_subtitle_indexer_skips_unchanged_rows(monkeypatch):
    from subtitles.indexer import movies as movie_indexer

    rows = [
        SimpleNamespace(radarrId=10, profileId=1, audio_language="eng", missing_subtitles="['en']"),
    ]

    class _Database:
        def __init__(self):
            self.selected = False
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            if not self.selected:
                self.selected = True
                return _Result(all_value=rows)
            return _Result()

    refresh_calls = []
    database = _Database()
    monkeypatch.setattr(movie_indexer, "database", database)
    monkeypatch.setattr(movie_indexer, "settings", SimpleNamespace(general=SimpleNamespace(use_embedded_subs=True)))
    monkeypatch.setattr(movie_indexer, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(movie_indexer, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(movie_indexer, "get_profiles_list", lambda profile_id: {
        "items": [{"language": "en", "forced": "False", "hi": "False", "audio_exclude": "False", "audio_only_include": "False"}],
    })
    monkeypatch.setattr(movie_indexer, "get_subtitles", lambda **kwargs: [])
    monkeypatch.setattr(movie_indexer, "get_profile_cutoff", lambda profile_id: [])
    monkeypatch.setattr(movie_indexer, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(movie_indexer, "refresh_wanted_search_state", lambda *args, **kwargs: refresh_calls.append(args))

    movie_indexer.list_missing_subtitles_movies()

    assert len(database.statements) == 1
    assert refresh_calls == []


def test_series_missing_subtitle_indexer_skips_unchanged_rows(monkeypatch):
    from subtitles.indexer import series as series_indexer

    rows = [
        SimpleNamespace(sonarrSeriesId=1, sonarrEpisodeId=10, profileId=1, audio_language="eng", missing_subtitles="['en']"),
    ]

    class _Database:
        def __init__(self):
            self.selected = False
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            if not self.selected:
                self.selected = True
                return _Result(all_value=rows)
            return _Result()

    refresh_calls = []
    database = _Database()
    monkeypatch.setattr(series_indexer, "database", database)
    monkeypatch.setattr(series_indexer, "settings", SimpleNamespace(general=SimpleNamespace(use_embedded_subs=True)))
    monkeypatch.setattr(series_indexer, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(series_indexer, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(series_indexer, "get_profiles_list", lambda profile_id: {
        "items": [{"language": "en", "forced": "False", "hi": "False", "audio_exclude": "False", "audio_only_include": "False"}],
    })
    monkeypatch.setattr(series_indexer, "get_subtitles", lambda **kwargs: [])
    monkeypatch.setattr(series_indexer, "get_profile_cutoff", lambda profile_id: [])
    monkeypatch.setattr(series_indexer, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(series_indexer, "refresh_wanted_search_state", lambda *args, **kwargs: refresh_calls.append(args))

    series_indexer.list_missing_subtitles()

    assert len(database.statements) == 1
    assert refresh_calls == []


def test_movie_wanted_search_stamps_remaining_languages_after_partial_success(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr', 'de']",
        failedAttempts="[]",
        path="/movies/movie.mkv",
        sceneName="Scene",
        title="Movie",
        profileId=1,
        radarrId=10,
    )
    refreshed_movie = SimpleNamespace(
        radarrId=10,
        missing_subtitles="['fr', 'de']",
        failedAttempts="[['en', 1.0]]",
    )

    class _Database:
        def execute(self, statement, params=None):
            if hasattr(statement, "values_kwargs"):
                return _Result()
            return SimpleNamespace(first=lambda: refreshed_movie)

    calls = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_movies.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(wanted_movies, "generate_subtitles", lambda *args, **kwargs: iter((SimpleNamespace(message="ok"),)))
    monkeypatch.setattr(wanted_movies, "store_subtitles_movie", lambda radarr_id: calls.append(("store", radarr_id)))
    monkeypatch.setattr(wanted_movies, "history_log_movie", lambda *args: None)
    monkeypatch.setattr(wanted_movies, "send_notifications_movie", lambda *args: None)
    monkeypatch.setattr(wanted_movies, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        wanted_movies,
        "record_failed_subtitle_attempts",
        lambda media_type, media_id, languages:
            calls.append(("attempts", media_type, media_id, languages)) or "updated",
    )
    monkeypatch.setattr(wanted_movies, "get_missing_languages", lambda media_type, media_id: ["fr", "de"])

    wanted_movies._wanted_movie(
        movie,
        providers_list=["provider"],
        due_languages=["en", "fr"],
        adaptive_search_policy=_POLICY,
    )

    assert ("store", 10) in calls
    assert ("attempts", "movie", 10, ["fr"]) in calls


def test_movie_no_result_stamps_due_languages_without_refreshing_missing_languages(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr']",
        failedAttempts="[]",
        path="/movies/movie.mkv",
        sceneName="Scene",
        title="Movie",
        profileId=1,
        radarrId=10,
    )

    class _Database:
        def execute(self, statement, params=None):
            if hasattr(statement, "values_kwargs"):
                return _Result()
            raise AssertionError("no-result searches should not refresh movie details")

    attempt_calls = []
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "update", lambda table: _Update())
    monkeypatch.setattr(wanted_movies, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_movies.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(wanted_movies, "generate_subtitles", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(wanted_movies, "store_subtitles_movie", lambda radarr_id: None)
    monkeypatch.setattr(wanted_movies, "get_missing_languages", lambda *args: (_ for _ in ()).throw(
        AssertionError("no-result searches should not refresh missing languages")
    ))
    monkeypatch.setattr(
        wanted_movies,
        "record_failed_subtitle_attempts",
        lambda media_type, media_id, languages:
            attempt_calls.append((media_type, media_id, languages)) or "updated",
    )

    wanted_movies._wanted_movie(
        movie,
        providers_list=["provider"],
        due_languages=["en", "fr"],
        adaptive_search_policy=_POLICY,
    )

    assert attempt_calls == [("movie", 10, ["en", "fr"])]


def test_movie_partial_success_records_still_missing_languages(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    now_timestamp = 2000000.0
    old_attempt = now_timestamp - timedelta(weeks=4).total_seconds()
    fr_latest = now_timestamp - 3600
    de_latest = now_timestamp - 86400
    movie = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr', 'de']",
        failedAttempts="[]",
        path="/movies/movie.mkv",
        sceneName="Scene",
        title="Movie",
        profileId=1,
        radarrId=10,
    )
    refreshed_movie = SimpleNamespace(
        radarrId=10,
        missing_subtitles="['fr', 'de']",
        failedAttempts=f"[['en', {old_attempt}], ['de', {old_attempt}], ['de', {de_latest}]]",
    )
    updated_attempts = (
        f"[['de', {old_attempt}], ['de', {de_latest}], "
        f"['fr', {old_attempt}], ['fr', {fr_latest}]]"
    )

    class _Database:
        def execute(self, statement, params=None):
            if hasattr(statement, "values_kwargs"):
                return _Result()
            return SimpleNamespace(first=lambda: refreshed_movie)

    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_movies.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(wanted_movies, "generate_subtitles", lambda *args, **kwargs: iter((SimpleNamespace(message="ok"),)))
    monkeypatch.setattr(wanted_movies, "store_subtitles_movie", lambda radarr_id: None)
    monkeypatch.setattr(wanted_movies, "history_log_movie", lambda *args: None)
    monkeypatch.setattr(wanted_movies, "send_notifications_movie", lambda *args: None)
    monkeypatch.setattr(wanted_movies, "event_stream", lambda *args, **kwargs: None)
    attempt_calls = []
    monkeypatch.setattr(
        wanted_movies,
        "record_failed_subtitle_attempts",
        lambda media_type, media_id, languages:
            attempt_calls.append((media_type, media_id, languages)) or updated_attempts,
    )
    monkeypatch.setattr(wanted_movies, "get_missing_languages", lambda media_type, media_id: ["fr", "de"])

    policy = _adaptive_policy(now_timestamp)
    wanted_movies._wanted_movie(
        movie,
        providers_list=["provider"],
        due_languages=["en", "fr"],
        adaptive_search_policy=policy,
    )

    assert attempt_calls == [("movie", 10, ["fr"])]


def test_series_wanted_search_stamps_remaining_languages_after_partial_success(monkeypatch):
    from subtitles.wanted import series as wanted_series

    episode = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr', 'de']",
        failedAttempts="[]",
        path="/series/episode.mkv",
        sceneName="Scene",
        title="Series",
        profileId=1,
        sonarrSeriesId=1,
        sonarrEpisodeId=10,
    )
    refreshed_episode = SimpleNamespace(
        sonarrEpisodeId=10,
        missing_subtitles="['fr', 'de']",
        failedAttempts="[['en', 1.0]]",
    )

    class _Database:
        def execute(self, statement, params=None):
            if hasattr(statement, "values_kwargs"):
                return _Result()
            return SimpleNamespace(first=lambda: refreshed_episode)

    calls = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_series.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(wanted_series, "generate_subtitles", lambda *args, **kwargs: iter((SimpleNamespace(message="ok"),)))
    monkeypatch.setattr(wanted_series, "store_subtitles", lambda sonarr_episode_id: calls.append(("store", sonarr_episode_id)))
    monkeypatch.setattr(wanted_series, "history_log", lambda *args: None)
    monkeypatch.setattr(wanted_series, "send_notifications", lambda *args: None)
    monkeypatch.setattr(wanted_series, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        wanted_series,
        "record_failed_subtitle_attempts",
        lambda media_type, media_id, languages:
            calls.append(("attempts", media_type, media_id, languages)) or "updated",
    )
    monkeypatch.setattr(wanted_series, "get_missing_languages", lambda media_type, media_id: ["fr", "de"])

    wanted_series._wanted_episode(
        episode,
        providers_list=["provider"],
        due_languages=["en", "fr"],
        adaptive_search_policy=_POLICY,
    )

    assert ("store", 10) in calls
    assert ("attempts", "series", 10, ["fr"]) in calls


def test_series_no_result_stamps_due_languages_without_refreshing_missing_languages(monkeypatch):
    from subtitles.wanted import series as wanted_series

    episode = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr']",
        failedAttempts="[]",
        path="/series/episode.mkv",
        sceneName="Scene",
        title="Series",
        profileId=1,
        sonarrSeriesId=1,
        sonarrEpisodeId=10,
    )

    class _Database:
        def execute(self, statement, params=None):
            if hasattr(statement, "values_kwargs"):
                return _Result()
            raise AssertionError("no-result searches should not refresh episode details")

    attempt_calls = []
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "update", lambda table: _Update())
    monkeypatch.setattr(wanted_series, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_series.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(wanted_series, "generate_subtitles", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(wanted_series, "store_subtitles", lambda sonarr_episode_id: None)
    monkeypatch.setattr(wanted_series, "get_missing_languages", lambda *args: (_ for _ in ()).throw(
        AssertionError("no-result searches should not refresh missing languages")
    ))
    monkeypatch.setattr(
        wanted_series,
        "record_failed_subtitle_attempts",
        lambda media_type, media_id, languages:
            attempt_calls.append((media_type, media_id, languages)) or "updated",
    )

    wanted_series._wanted_episode(
        episode,
        providers_list=["provider"],
        due_languages=["en", "fr"],
        adaptive_search_policy=_POLICY,
    )

    assert attempt_calls == [("series", 10, ["en", "fr"])]


def test_series_partial_success_records_still_missing_languages(monkeypatch):
    from subtitles.wanted import series as wanted_series

    now_timestamp = 2000000.0
    old_attempt = now_timestamp - timedelta(weeks=4).total_seconds()
    fr_latest = now_timestamp - 3600
    de_latest = now_timestamp - 86400
    episode = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr', 'de']",
        failedAttempts="[]",
        path="/series/episode.mkv",
        sceneName="Scene",
        title="Series",
        profileId=1,
        sonarrSeriesId=1,
        sonarrEpisodeId=10,
    )
    refreshed_episode = SimpleNamespace(
        sonarrEpisodeId=10,
        missing_subtitles="['fr', 'de']",
        failedAttempts=f"[['en', {old_attempt}], ['de', {old_attempt}], ['de', {de_latest}]]",
    )
    updated_attempts = (
        f"[['de', {old_attempt}], ['de', {de_latest}], "
        f"['fr', {old_attempt}], ['fr', {fr_latest}]]"
    )

    class _Database:
        def execute(self, statement, params=None):
            if hasattr(statement, "values_kwargs"):
                return _Result()
            return SimpleNamespace(first=lambda: refreshed_episode)

    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_series.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(wanted_series, "generate_subtitles", lambda *args, **kwargs: iter((SimpleNamespace(message="ok"),)))
    monkeypatch.setattr(wanted_series, "store_subtitles", lambda sonarr_episode_id: None)
    monkeypatch.setattr(wanted_series, "history_log", lambda *args: None)
    monkeypatch.setattr(wanted_series, "send_notifications", lambda *args: None)
    monkeypatch.setattr(wanted_series, "event_stream", lambda *args, **kwargs: None)
    attempt_calls = []
    monkeypatch.setattr(
        wanted_series,
        "record_failed_subtitle_attempts",
        lambda media_type, media_id, languages:
            attempt_calls.append((media_type, media_id, languages)) or updated_attempts,
    )
    monkeypatch.setattr(wanted_series, "get_missing_languages", lambda media_type, media_id: ["fr", "de"])

    policy = _adaptive_policy(now_timestamp)
    wanted_series._wanted_episode(
        episode,
        providers_list=["provider"],
        due_languages=["en", "fr"],
        adaptive_search_policy=policy,
    )

    assert attempt_calls == [("series", 10, ["fr"])]


def test_adaptive_search_throttle_skip_is_not_logged_per_item(monkeypatch, caplog):
    from subtitles.wanted import movies as wanted_movies

    movie = SimpleNamespace(
        audio_language="eng",
        missing_subtitles="['en', 'fr']",
        failedAttempts="[['en', 1.0], ['fr', 1.0]]",
        path="/movies/movie.mkv",
        sceneName="Scene",
        title="Movie",
        profileId=1,
        radarrId=10,
    )

    monkeypatch.setattr(wanted_movies, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(wanted_movies, "get_due_missing_languages_for_media", lambda *args, **kwargs: [])
    monkeypatch.setattr(wanted_movies.path_mappings, "path_replace_movie", lambda path: path)
    searches = []
    monkeypatch.setattr(wanted_movies, "generate_subtitles", lambda *args, **kwargs: searches.append(True) or iter(()))

    with caplog.at_level("DEBUG"):
        wanted_movies._wanted_movie(movie, providers_list=["provider"], job_id="job")

    assert searches == []
    assert "Search is throttled by adaptive search" not in caplog.text


def test_generate_subtitles_rechecks_missing_languages_before_each_attempt(monkeypatch):
    from subtitles import download
    from subtitles import pool as subtitle_pool

    class _Video:
        original_path = "/movies/movie.mkv"

    class _Pool:
        providers = ["provider"]

    class _Language:
        hi = False
        forced = False

        def __init__(self, basename):
            self.basename = basename

        def __eq__(self, other):
            return isinstance(other, _Language) and self.basename == other.basename

        def __hash__(self):
            return hash(self.basename)

        def __repr__(self):
            return self.basename

    downloaded_languages = []
    missing_language_snapshots = iter((
        [_Language("en"), _Language("fr")],
        [_Language("en")],
    ))

    monkeypatch.setattr(subtitle_pool, "_update_pool", lambda *args, **kwargs: False)
    monkeypatch.setattr(download, "_get_pool", lambda *args, **kwargs: _Pool())
    monkeypatch.setattr(download, "_get_language_obj", lambda languages: [_Language(language[0]) for language in languages])
    monkeypatch.setattr(download, "get_profiles_list", lambda profile_id: {"originalFormat": False})
    monkeypatch.setattr(download, "_set_forced_providers", lambda *args, **kwargs: None)
    monkeypatch.setattr(download, "_get_scores", lambda *args, **kwargs: (0, 100, {}))
    monkeypatch.setattr(download, "get_array_from", lambda value: [])
    monkeypatch.setattr(download, "get_video", lambda *args, **kwargs: _Video())
    monkeypatch.setattr(
        download,
        "check_missing_languages",
        lambda *args, **kwargs: next(missing_language_snapshots),
    )

    def _download_best_subtitles(*args, **kwargs):
        downloaded_languages.extend(kwargs["languages"])
        return {}

    monkeypatch.setattr(download, "download_best_subtitles", _download_best_subtitles)
    monkeypatch.setattr(
        download,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                utf8_encode=False,
                minimum_score=0,
                minimum_score_movie=0,
                subzero_mods="",
            ),
        ),
    )

    list(download.generate_subtitles(
        "/movies/movie.mkv",
        [("en", "False", "False"), ("fr", "False", "False")],
        "None",
        "Scene",
        "Movie",
        "movie",
        1,
        check_if_still_required=True,
    ))

    assert [language.basename for language in downloaded_languages] == ["en"]


def test_check_missing_languages_reads_normalized_movie_rows(monkeypatch):
    from subtitles import download

    class _ScalarResult:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self._values

    execute_calls = []

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            if len(execute_calls) == 1:
                return SimpleNamespace(first=lambda: SimpleNamespace(radarrId=30))
            return _ScalarResult(["en", "fr:hi", "de:forced"])

    monkeypatch.setattr(download, "database", _Database())
    monkeypatch.setattr(download.path_mappings, "path_replace_reverse_movie", lambda path: "/db/movie.mkv")
    monkeypatch.setattr(download, "_get_language_obj", lambda languages: languages)

    assert download.check_missing_languages("/mapped/movie.mkv", "movie") == [
        ("en", "False", "False"),
        ("fr", "True", "False"),
        ("de", "False", "True"),
    ]
    assert len(execute_calls) == 2


def test_movie_mass_download_uses_normalized_missing_languages(monkeypatch):
    from subtitles.mass_download import movies as mass_movies

    movie = SimpleNamespace(
        path="/movies/movie.mkv",
        missing_subtitles="['legacy-would-be-wrong']",
        audio_language="eng",
        radarrId=30,
        sceneName="Scene",
        title="Movie",
        year="2024",
        tags="[]",
        monitored="True",
        profileId=1,
    )
    generated_calls = []

    monkeypatch.setattr(mass_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(mass_movies, "database", SimpleNamespace(execute=lambda statement: SimpleNamespace(first=lambda: movie)))
    monkeypatch.setattr(mass_movies, "get_subtitles", lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "movie.en.srt"}])
    monkeypatch.setattr(mass_movies, "get_missing_languages", lambda media_type, media_id: ["en", "fr:forced"])
    monkeypatch.setattr(mass_movies.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(mass_movies.os.path, "exists", lambda path: True)
    monkeypatch.setattr(mass_movies, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(mass_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(mass_movies, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(
        mass_movies,
        "generate_subtitles",
        lambda path, languages, *args, **kwargs: generated_calls.append((path, languages)) or iter(()),
    )

    mass_movies.movies_download_subtitles(30, job_id="job")

    assert generated_calls == [
        (
            "/movies/movie.mkv",
            [("en", "False", "False"), ("fr", "False", "True")],
        )
    ]


def test_serialization_accepts_repr_and_native_missing_subtitle_values():
    from subtitles.serialization import (
        missing_subtitle_to_language_tuple,
        parse_missing_subtitles,
    )

    assert parse_missing_subtitles("['en', None, 'fr:forced']") == ["en", "fr:forced"]
    assert parse_missing_subtitles('["en", "fr:hi"]') == ["en", "fr:hi"]
    assert parse_missing_subtitles(("en", None, "fr:hi")) == ["en", "fr:hi"]
    assert missing_subtitle_to_language_tuple("fr:forced") == ("fr", "False", "True")
    assert missing_subtitle_to_language_tuple("fr:hi") == ("fr", "True", "False")


def test_postprocess_accepts_native_missing_subtitle_lists(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "api_utils_under_test",
        "bazarr/api/utils.py",
    )
    api_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_utils)

    monkeypatch.setattr(
        api_utils,
        "settings",
        SimpleNamespace(general=SimpleNamespace(embedded_subs_show_desired=False)),
    )
    monkeypatch.setattr(api_utils, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(api_utils, "get_subtitles", lambda *args, **kwargs: [])
    monkeypatch.setattr(api_utils, "language_from_alpha2", lambda language: language.upper())
    monkeypatch.setattr(api_utils, "alpha3_from_alpha2", lambda language: f"{language}g")
    monkeypatch.setattr(api_utils.path_mappings, "path_replace_movie", lambda path: path)

    processed = api_utils.postprocess({
        "radarrId": 30,
        "missing_subtitles": ["en", "fr:forced", "de:hi"],
        "tags": "[]",
        "path": "/movies/movie.mkv",
    })

    assert processed["missing_subtitles"] == [
        {"name": "EN", "code2": "en", "code3": "eng", "forced": False, "hi": False},
        {"name": "FR", "code2": "fr", "code3": "frg", "forced": True, "hi": False},
        {"name": "DE", "code2": "de", "code3": "deg", "forced": False, "hi": True},
    ]


def test_audio_language_parser_uses_fast_repr_list_parser():
    from app.database import _parse_audio_languages_text

    assert _parse_audio_languages_text("['English', None, 'Japanese']") == ["English", None, "Japanese"]
    assert _parse_audio_languages_text('["English", "French"]') == ["English", "French"]
    assert _parse_audio_languages_text("not a list") == []
    assert _parse_audio_languages_text("[") == []


def test_get_array_from_uses_fast_repr_list_parser():
    from app.config import get_array_from

    assert get_array_from("['OCRFix', None, 'common']") == ["OCRFix", None, "common"]
    assert get_array_from('["OCRFix", "common"]') == ["OCRFix", "common"]
    assert get_array_from("OCRFix,common") == ["OCRFix", "common"]
    assert get_array_from("OCRFix") == ["OCRFix"]
    assert get_array_from("[") == []
    assert get_array_from("") == []


def test_series_sync_batches_due_language_lookup(monkeypatch):
    from sonarr.sync import episodes as sync_episodes

    due_language_map_calls = []

    monkeypatch.setattr(sync_episodes, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(
        sync_episodes,
        "get_due_missing_languages_map",
        lambda media_type, media_ids: due_language_map_calls.append((media_type, media_ids)) or {10: ["fr"]},
    )
    monkeypatch.setattr(
        sync_episodes,
        "database",
        SimpleNamespace(
            execute=lambda statement: _Result(
                [
                    SimpleNamespace(language="en", sonarrEpisodeId=10),
                    SimpleNamespace(language="fr", sonarrEpisodeId=10),
                ]
            )
        ),
    )

    assert sync_episodes._is_there_missing_subtitles(episode_id=10) is True
    assert due_language_map_calls == [("series", [10])]


def test_series_mass_download_passes_batched_missing_languages_to_episode_download(monkeypatch):
    from subtitles.mass_download import series as mass_series

    execute_calls = []
    download_calls = []

    class _Database:
        def execute(self, statement):
            execute_calls.append(statement)
            if len(execute_calls) == 1:
                return SimpleNamespace(first=lambda: SimpleNamespace(path="/shows/series", title="Series"))
            return _Result([
                SimpleNamespace(sonarrEpisodeId=10, title="Series", season=1, episode=1, episodeTitle="One"),
                SimpleNamespace(sonarrEpisodeId=20, title="Series", season=1, episode=2, episodeTitle="Two"),
            ])

    monkeypatch.setattr(mass_series, "database", _Database())
    monkeypatch.setattr(mass_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(mass_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(mass_series, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(mass_series, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(mass_series.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        mass_series,
        "settings",
        SimpleNamespace(general=SimpleNamespace(use_whisper_fallback=False, use_whisper_fallback_series=False)),
    )
    monkeypatch.setattr(
        mass_series,
        "get_missing_languages_map",
        lambda media_type, media_ids: {10: ["en"], 20: ["fr"]},
    )
    monkeypatch.setattr(
        mass_series,
        "get_missing_languages",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("per-episode lookup should not run")),
    )
    monkeypatch.setattr(
        mass_series,
        "episode_download_subtitles",
        lambda **kwargs: download_calls.append((kwargs["no"], kwargs["missing_languages"])),
    )

    mass_series.series_download_subtitles(1, job_id="job")

    assert download_calls == [(10, ["en"]), (20, ["fr"])]


def test_episode_mass_download_refreshes_missing_languages_after_reindex(monkeypatch):
    from subtitles.mass_download import series as mass_series

    episode_row = SimpleNamespace(
        path="/shows/series/episode.mkv",
        missing_subtitles=None,
        monitored="True",
        sonarrEpisodeId=10,
        sceneName="Scene",
        title="Series",
        sonarrSeriesId=1,
        audio_language="eng",
        seriesType="standard",
        episodeTitle="One",
        season=1,
        episode=1,
        profileId=1,
    )
    subtitle_rows = []
    generate_calls = []
    missing_language_calls = []

    class _Database:
        def execute(self, statement):
            return SimpleNamespace(first=lambda: episode_row)

    monkeypatch.setattr(mass_series, "database", _Database())
    monkeypatch.setattr(mass_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(mass_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(mass_series, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(mass_series, "get_audio_profile_languages", lambda audio_language: [])
    monkeypatch.setattr(mass_series.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        mass_series,
        "settings",
        SimpleNamespace(general=SimpleNamespace(use_whisper_fallback=False, use_whisper_fallback_series=False)),
    )
    monkeypatch.setattr(mass_series, "get_subtitles", lambda *args, **kwargs: [])
    monkeypatch.setattr(mass_series, "store_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(mass_series, "list_missing_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mass_series,
        "get_missing_languages",
        lambda *args, **kwargs: missing_language_calls.append(args) or ["fresh"],
    )
    monkeypatch.setattr(mass_series, "missing_subtitle_to_language_tuple", lambda language: ("lang", language))
    monkeypatch.setattr(
        mass_series,
        "generate_subtitles",
        lambda *args, **kwargs: generate_calls.append(args[1]) or subtitle_rows,
    )

    mass_series.episode_download_subtitles(10, job_id="job", missing_languages=["stale"])

    assert missing_language_calls == [("series", 10)]
    assert generate_calls == [[("lang", "fresh")]]


def test_scheduled_series_detail_lookup_batches_due_episode_ids(monkeypatch):
    from subtitles.wanted import series as wanted_series

    detail_queries = []
    rows = [
        SimpleNamespace(sonarrEpisodeId=10, title="Series", season=1, episode=1, episodeTitle="One"),
        SimpleNamespace(sonarrEpisodeId=20, title="Series", season=1, episode=2, episodeTitle="Two"),
    ]

    class _Database:
        def execute(self, statement):
            detail_queries.append(statement)
            return _Result([rows[len(detail_queries) - 1]])

    monkeypatch.setattr(wanted_series, "_DUE_EPISODE_DETAILS_BATCH_SIZE", 1)
    _patch_due_stream(monkeypatch, wanted_series, {10: ["en"], 20: ["fr"]})
    monkeypatch.setattr(wanted_series, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_series, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_series, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(wanted_series, "_episode_needs_wanted_lookup_refresh", lambda episode: False)
    monkeypatch.setattr(wanted_series, "_wanted_episode", lambda *args, **kwargs: None)
    monkeypatch.setattr(wanted_series, "database", _Database())
    monkeypatch.setattr(wanted_series, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_series, "settings", SimpleNamespace(general=SimpleNamespace(use_whisper_fallback=False)))

    wanted_series.wanted_search_missing_subtitles_series(job_id="job")

    assert len(detail_queries) == 2


def test_scheduled_movie_detail_lookup_batches_due_movie_ids(monkeypatch):
    from subtitles.wanted import movies as wanted_movies

    detail_queries = []
    rows = [
        SimpleNamespace(radarrId=10, title="One"),
        SimpleNamespace(radarrId=20, title="Two"),
    ]

    class _Database:
        def execute(self, statement):
            detail_queries.append(statement)
            return _Result([rows[len(detail_queries) - 1]])

    monkeypatch.setattr(wanted_movies, "_DUE_MOVIE_DETAILS_BATCH_SIZE", 1)
    _patch_due_stream(monkeypatch, wanted_movies, {10: ["en"], 20: ["fr"]})
    monkeypatch.setattr(wanted_movies, "get_exclusion_clause", lambda media_type: [])
    monkeypatch.setattr(wanted_movies, "get_adaptive_search_policy", lambda: _POLICY)
    monkeypatch.setattr(wanted_movies, "get_providers", lambda: ["provider"])
    monkeypatch.setattr(wanted_movies, "_movie_needs_wanted_lookup_refresh", lambda movie: False)
    monkeypatch.setattr(wanted_movies, "_wanted_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(wanted_movies, "database", _Database())
    monkeypatch.setattr(wanted_movies, "jobs_queue", _job_queue())
    monkeypatch.setattr(wanted_movies, "settings", SimpleNamespace(general=SimpleNamespace(use_whisper_fallback=False)))

    wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")

    assert len(detail_queries) == 2


def test_record_failed_attempts_chunks_normalized_upserts(monkeypatch):
    import sqlalchemy as sa

    from app.database import TableFailedSubtitleAttempts
    from subtitles import wanted_state

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    TableFailedSubtitleAttempts.__table__.create(connection)

    class _Database:
        def __init__(self):
            self.insert_calls = 0

        def execute(self, statement):
            if getattr(statement, "is_insert", False):
                self.insert_calls += 1
            return connection.execute(statement)

    database = _Database()
    monkeypatch.setattr(wanted_state, "database", database)
    monkeypatch.setattr(wanted_state, "WANTED_STATE_QUERY_BATCH_SIZE", 1)

    try:
        assert set(
            wanted_state.record_failed_subtitle_attempts_map(
                "series",
                {10: ["en"], 20: ["fr"]},
            )
        ) == {10, 20}
        assert database.insert_calls == 2
        assert connection.execute(sa.text("SELECT COUNT(*) FROM table_failed_subtitle_attempts")).scalar() == 2
    finally:
        connection.close()


def test_delete_wanted_search_state_removes_normalized_rows(monkeypatch):
    import sqlalchemy as sa

    from app.database import TableFailedSubtitleAttempts, TableMissingSubtitles
    from subtitles import wanted_state

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    TableMissingSubtitles.__table__.create(connection)
    TableFailedSubtitleAttempts.__table__.create(connection)
    connection.execute(
        sa.insert(TableMissingSubtitles),
        [
            {"media_type": "series", "media_id": 10, "language": "en"},
            {"media_type": "series", "media_id": 20, "language": "fr"},
        ],
    )
    connection.execute(
        sa.insert(TableFailedSubtitleAttempts),
        [
            {
                "media_type": "series",
                "media_id": 10,
                "language": "en",
                "initial_attempt_at": 1.0,
                "latest_attempt_at": 2.0,
            },
            {
                "media_type": "series",
                "media_id": 20,
                "language": "fr",
                "initial_attempt_at": 1.0,
                "latest_attempt_at": 2.0,
            },
        ],
    )

    monkeypatch.setattr(wanted_state, "database", connection)

    try:
        wanted_state.delete_wanted_search_state("series", "10")

        assert connection.execute(
            sa.text("SELECT media_id FROM table_missing_subtitles ORDER BY media_id")
        ).all() == [(20,)]
        assert connection.execute(
            sa.text("SELECT media_id FROM table_failed_subtitle_attempts ORDER BY media_id")
        ).all() == [(20,)]
    finally:
        connection.close()


def test_update_one_series_delete_branch_cleans_wanted_state(monkeypatch):
    import sqlalchemy as sa

    from app.database import (
        TableEpisodes,
        TableFailedSubtitleAttempts,
        TableLanguagesProfiles,
        TableMissingSubtitles,
        TableShows,
    )
    from sonarr.sync import series as series_sync
    from subtitles import wanted_state

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    TableLanguagesProfiles.__table__.create(connection)
    TableShows.__table__.create(connection)
    TableEpisodes.__table__.create(connection)
    TableMissingSubtitles.__table__.create(connection)
    TableFailedSubtitleAttempts.__table__.create(connection)
    connection.execute(
        sa.insert(TableShows),
        [
            {"sonarrSeriesId": 10, "path": "/series/10", "title": "Series 10"},
        ],
    )
    connection.execute(
        sa.insert(TableEpisodes),
        [
            {"sonarrEpisodeId": 101, "sonarrSeriesId": 10, "episode": 1, "season": 1, "path": "/episode/101", "title": "Ep 101"},
            {"sonarrEpisodeId": 102, "sonarrSeriesId": 10, "episode": 2, "season": 1, "path": "/episode/102", "title": "Ep 102"},
        ],
    )
    connection.execute(
        sa.insert(TableMissingSubtitles),
        [
            {"media_type": "series", "media_id": 101, "language": "en"},
            {"media_type": "series", "media_id": 102, "language": "fr"},
        ],
    )
    connection.execute(
        sa.insert(TableFailedSubtitleAttempts),
        [
            {
                "media_type": "series",
                "media_id": 101,
                "language": "en",
                "initial_attempt_at": 1.0,
                "latest_attempt_at": 2.0,
            },
            {
                "media_type": "series",
                "media_id": 102,
                "language": "fr",
                "initial_attempt_at": 1.0,
                "latest_attempt_at": 2.0,
            },
        ],
    )

    monkeypatch.setattr(series_sync, "database", connection)
    monkeypatch.setattr(series_sync, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(wanted_state, "database", connection)

    try:
        series_sync.update_one_series(10, action="deleted")

        assert connection.execute(
            sa.text("SELECT sonarrSeriesId FROM table_shows")
        ).all() == []
        assert connection.execute(
            sa.text("SELECT media_id FROM table_missing_subtitles ORDER BY media_id")
        ).all() == []
        assert connection.execute(
            sa.text("SELECT media_id FROM table_failed_subtitle_attempts ORDER BY media_id")
        ).all() == []
    finally:
        connection.close()


def test_refresh_wanted_search_state_replaces_missing_rows_and_optionally_failed_attempts(monkeypatch):
    import sqlalchemy as sa

    from app.database import TableFailedSubtitleAttempts, TableMissingSubtitles
    from subtitles import wanted_state

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    TableMissingSubtitles.__table__.create(connection)
    TableFailedSubtitleAttempts.__table__.create(connection)
    connection.execute(
        sa.insert(TableMissingSubtitles),
        [
            {"media_type": "movie", "media_id": 30, "language": "en"},
            {"media_type": "movie", "media_id": 30, "language": "fr"},
        ],
    )
    connection.execute(
        sa.insert(TableFailedSubtitleAttempts),
        [
            {
                "media_type": "movie",
                "media_id": 30,
                "language": "en",
                "initial_attempt_at": 1.0,
                "latest_attempt_at": 2.0,
            },
        ],
    )

    monkeypatch.setattr(wanted_state, "database", connection)

    try:
        wanted_state.refresh_wanted_search_state(
            "movie",
            30,
            "['de']",
            refresh_failed_attempts=False,
        )

        assert connection.execute(
            sa.text("SELECT language FROM table_missing_subtitles ORDER BY language")
        ).all() == [("de",)]
        assert connection.execute(
            sa.text("SELECT language, initial_attempt_at, latest_attempt_at FROM table_failed_subtitle_attempts")
        ).all() == [("en", 1.0, 2.0)]

        wanted_state.refresh_wanted_search_state(
            "movie",
            30,
            "[]",
            failed_attempts="[['fr', 3.0], ['fr', 5.0]]",
        )

        assert connection.execute(sa.text("SELECT language FROM table_missing_subtitles")).all() == []
        assert connection.execute(
            sa.text("SELECT language, initial_attempt_at, latest_attempt_at FROM table_failed_subtitle_attempts")
        ).all() == [("fr", 3.0, 5.0)]
    finally:
        connection.close()


def test_adaptive_search_handles_fast_repr_attempt_format():
    from datetime import datetime, timedelta

    from subtitles.adaptive_searching import get_active_search_languages

    now = datetime.now()
    latest_attempt = now.timestamp() - 3600
    old_attempt = now.timestamp() - timedelta(weeks=4).total_seconds()
    policy = {
        "delay": timedelta(weeks=3),
        "delta": timedelta(weeks=1),
        "delay_label": "3w",
        "delta_label": "1w",
        "now": now,
        "initial_search_cutoff": now.timestamp() - timedelta(weeks=3).total_seconds(),
        "latest_search_cutoff": now.timestamp() - timedelta(weeks=1).total_seconds(),
    }

    assert get_active_search_languages(
        ["en", "fr", "de"],
        f"[['en', {old_attempt}], ['en', {latest_attempt}], ['fr', {old_attempt}]]",
        adaptive_search_policy=policy,
    ) == ["fr", "de"]


def test_adaptive_search_handles_double_quoted_attempt_format():
    from datetime import datetime, timedelta

    from subtitles.adaptive_searching import get_active_search_languages

    now = datetime.now()
    latest_attempt = now.timestamp() - 3600
    old_attempt = now.timestamp() - timedelta(weeks=4).total_seconds()
    policy = {
        "delay": timedelta(weeks=3),
        "delta": timedelta(weeks=1),
        "delay_label": "3w",
        "delta_label": "1w",
        "now": now,
        "initial_search_cutoff": now.timestamp() - timedelta(weeks=3).total_seconds(),
        "latest_search_cutoff": now.timestamp() - timedelta(weeks=1).total_seconds(),
    }

    assert get_active_search_languages(
        ["en", "fr", "de"],
        f'[["en", {old_attempt}], ["en", {latest_attempt}], ["fr", {old_attempt}]]',
        adaptive_search_policy=policy,
    ) == ["fr", "de"]

def test_adaptive_policy_cache_refreshes_when_settings_change(monkeypatch):
    from subtitles import adaptive_searching

    monkeypatch.setattr(
        adaptive_searching,
        "_adaptive_policy_cache",
        {
            "expires_at": 0.0,
            "settings_key": None,
            "policy_components": None,
        },
    )
    monkeypatch.setattr(
        adaptive_searching,
        "settings",
        SimpleNamespace(
            general=SimpleNamespace(
                adaptive_searching=True,
                adaptive_searching_delay="3w",
                adaptive_searching_delta="1w",
            ),
        ),
    )

    first_policy = adaptive_searching.get_adaptive_search_policy()
    adaptive_searching.settings.general.adaptive_searching_delay = "4w"
    second_policy = adaptive_searching.get_adaptive_search_policy()

    assert first_policy["delay_label"] == "3w"
    assert second_policy["delay_label"] == "4w"
    assert adaptive_searching.get_adaptive_search_policy_key(second_policy) == "4w|1w"


def test_adaptive_search_treats_malformed_attempt_entries_as_active():
    from subtitles.adaptive_searching import get_active_search_languages

    assert get_active_search_languages(
        ["en"],
        "[[['nested'], 1.0]]",
        adaptive_search_policy=_POLICY,
    ) == ["en"]


def test_update_failed_attempts_skips_malformed_attempt_entries():
    from subtitles.adaptive_searching import update_failed_attempts

    updated = update_failed_attempts(["en"], "[[['nested'], 1.0], ['fr', 'bad']]")

    assert "nested" not in updated
    assert "fr" not in updated
    assert "en" in updated


def test_update_failed_attempts_skips_non_finite_attempt_entries():
    from subtitles.adaptive_searching import update_failed_attempts

    updated = update_failed_attempts(["en"], "[['fr', 'inf']]")

    assert "fr" not in updated
    assert "en" in updated


def test_update_failed_attempts_preserves_attempts_for_empty_update_set():
    from subtitles.adaptive_searching import update_failed_attempts

    updated = update_failed_attempts([], "[['fr', 1.0]]")

    assert updated == "[['fr', 1.0]]"


def test_missing_subtitle_rows_are_normalized_and_deduplicated():
    from subtitles.wanted_state import get_missing_subtitle_rows

    assert get_missing_subtitle_rows('movie', 10, "['en', 'ru', 'en']") == [
        {
            "media_type": "movie",
            "media_id": 10,
            "language": "en",
        },
        {
            "media_type": "movie",
            "media_id": 10,
            "language": "ru",
        },
    ]


def test_failed_subtitle_attempt_rows_are_normalized_by_language():
    from subtitles.wanted_state import get_failed_subtitle_attempt_rows

    assert get_failed_subtitle_attempt_rows(
        'movie',
        10,
        "[['en', 3.0], ['fr', 2.0], ['en', 5.0], ['fr', 1.0]]",
    ) == [
        {
            "media_type": "movie",
            "media_id": 10,
            "language": "en",
            "initial_attempt_at": 3.0,
            "latest_attempt_at": 5.0,
        },
        {
            "media_type": "movie",
            "media_id": 10,
            "language": "fr",
            "initial_attempt_at": 1.0,
            "latest_attempt_at": 2.0,
        },
    ]


def test_record_failed_subtitle_attempts_upserts_without_reselecting(monkeypatch):
    import sqlalchemy as sa

    from app.database import TableFailedSubtitleAttempts
    from subtitles import wanted_state

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    TableFailedSubtitleAttempts.__table__.create(connection)
    connection.execute(
        TableFailedSubtitleAttempts.__table__.insert(),
        [
            {
                "media_type": "series",
                "media_id": 10,
                "language": "en",
                "initial_attempt_at": 1.0,
                "latest_attempt_at": 2.0,
            },
            {
                "media_type": "series",
                "media_id": 10,
                "language": "fr",
                "initial_attempt_at": 3.0,
                "latest_attempt_at": 4.0,
            },
        ],
    )

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement, params=None):
            self.calls += 1
            if params is None:
                return connection.execute(statement)
            return connection.execute(statement, params)

    class _DateTime:
        @staticmethod
        def now():
            return "now"

        @staticmethod
        def timestamp(value):
            assert value == "now"
            return 10.0

    database = _Database()
    monkeypatch.setattr(wanted_state, "database", database)
    monkeypatch.setattr(wanted_state, "datetime", _DateTime)

    try:
        assert wanted_state.record_failed_subtitle_attempts("series", 10, ["en", "de"]) == (
            "[['de', 10.0], ['en', 1.0], ['en', 10.0], ['fr', 3.0], ['fr', 4.0]]"
        )
        assert database.calls == 2
        assert connection.execute(
            sa.select(
                TableFailedSubtitleAttempts.language,
                TableFailedSubtitleAttempts.initial_attempt_at,
                TableFailedSubtitleAttempts.latest_attempt_at,
            )
            .order_by(TableFailedSubtitleAttempts.language)
        ).all() == [
            ("de", 10.0, 10.0),
            ("en", 1.0, 10.0),
            ("fr", 3.0, 4.0),
        ]
    finally:
        connection.close()


def test_due_missing_languages_map_filters_in_one_query(monkeypatch):
    import sqlalchemy as sa

    from app.database import TableFailedSubtitleAttempts, TableMissingSubtitles
    from subtitles import wanted_state

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    TableMissingSubtitles.__table__.create(connection)
    TableFailedSubtitleAttempts.__table__.create(connection)
    connection.execute(
        TableMissingSubtitles.__table__.insert(),
        [
            {"media_type": "series", "media_id": 10, "language": "en"},
            {"media_type": "series", "media_id": 10, "language": "fr"},
            {"media_type": "series", "media_id": 20, "language": "de"},
            {"media_type": "series", "media_id": 30, "language": "es"},
        ],
    )
    connection.execute(
        TableFailedSubtitleAttempts.__table__.insert(),
        [
            {
                "media_type": "series",
                "media_id": 10,
                "language": "en",
                "initial_attempt_at": _POLICY["initial_search_cutoff"] + 1,
                "latest_attempt_at": _POLICY["latest_search_cutoff"] + 1,
            },
            {
                "media_type": "series",
                "media_id": 10,
                "language": "fr",
                "initial_attempt_at": _POLICY["initial_search_cutoff"] - 1,
                "latest_attempt_at": _POLICY["latest_search_cutoff"] + 1,
            },
            {
                "media_type": "series",
                "media_id": 20,
                "language": "de",
                "initial_attempt_at": _POLICY["initial_search_cutoff"] - 1,
                "latest_attempt_at": _POLICY["latest_search_cutoff"] - 1,
            },
        ],
    )

    class _Database:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            return connection.execute(statement)

    database = _Database()
    monkeypatch.setattr(wanted_state, "database", database)

    try:
        assert wanted_state.get_due_missing_languages_map(
            "series",
            [10, 20, 30],
            adaptive_search_policy=_POLICY,
        ) == {
            10: ["en"],
            20: ["de"],
            30: ["es"],
        }
        assert database.calls == 1
    finally:
        connection.close()


def test_wanted_state_migration_backfills_and_downgrades_sqlite(monkeypatch):
    import importlib.util

    import sqlalchemy as sa

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    metadata = sa.MetaData()
    sa.Table(
        "table_episodes",
        metadata,
        sa.Column("sonarrEpisodeId", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    sa.Table(
        "table_movies",
        metadata,
        sa.Column("radarrId", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(connection)
    normalized_missing = sa.Table(
        "table_missing_subtitles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("media_type", sa.Text),
        sa.Column("media_id", sa.Integer),
        sa.Column("language", sa.Text),
    )
    normalized_failed = sa.Table(
        "table_failed_subtitle_attempts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("media_type", sa.Text),
        sa.Column("media_id", sa.Integer),
        sa.Column("language", sa.Text),
        sa.Column("initial_attempt_at", sa.Float),
        sa.Column("latest_attempt_at", sa.Float),
    )
    normalized_missing.create(connection)
    normalized_failed.create(connection)
    connection.execute(
        normalized_missing.insert(),
        [
            {"media_type": "series", "media_id": 10, "language": "old"},
            {"media_type": "movie", "media_id": 30, "language": "old"},
        ],
    )
    connection.execute(
        normalized_failed.insert(),
        [
            {
                "media_type": "series",
                "media_id": 10,
                "language": "old",
                "initial_attempt_at": 0.0,
                "latest_attempt_at": 0.0,
            },
            {
                "media_type": "movie",
                "media_id": 30,
                "language": "old",
                "initial_attempt_at": 0.0,
                "latest_attempt_at": 0.0,
            },
        ],
    )
    connection.execute(
        metadata.tables["table_episodes"].insert(),
        [
            {
                "sonarrEpisodeId": 10,
                "missing_subtitles": '["en", "fr", "en"]',
                "failedAttempts": "[['en', 3.0], ['en', 5.0], ['fr', 2.0]]",
            },
            {
                "sonarrEpisodeId": 20,
                "missing_subtitles": "[]",
                "failedAttempts": "[]",
            },
        {
            "sonarrEpisodeId": 21,
            "missing_subtitles": '["es"]',
            "failedAttempts": '[["es", 7.0]]',
        },
    ],
    )
    connection.execute(
        metadata.tables["table_movies"].insert(),
        [
            {
                "radarrId": 30,
                "missing_subtitles": "['de']",
                "failedAttempts": "[['de', 4.0], ['de', 1.0]]",
            },
        ],
    )

    class _MigrationOp:
        def get_context(self):
            return SimpleNamespace(bind=connection)

        def create_table(self, table_name, *elements):
            sa.Table(table_name, sa.MetaData(), *elements).create(connection)

        def create_index(self, index_name, table_name, columns, unique=False):
            table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
            sa.Index(index_name, *(table.c[column] for column in columns), unique=unique).create(connection)

        def drop_index(self, index_name, table_name=None):
            connection.exec_driver_sql(f'DROP INDEX "{index_name}"')

        def drop_table(self, table_name):
            sa.Table(table_name, sa.MetaData(), autoload_with=connection).drop(connection)

    spec = importlib.util.spec_from_file_location(
        "wanted_state_migration",
        "migrations/versions/e6cbb0f6f9b1_.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = _MigrationOp()

    try:
        migration.upgrade()

        missing_rows = connection.execute(
            sa.text(
                "SELECT media_type, media_id, language "
                "FROM table_missing_subtitles "
                "ORDER BY media_type, media_id, language"
            )
        ).all()
        attempt_rows = connection.execute(
            sa.text(
                "SELECT media_type, media_id, language, initial_attempt_at, latest_attempt_at "
                "FROM table_failed_subtitle_attempts "
                "ORDER BY media_type, media_id, language"
            )
        ).all()

        assert missing_rows == [
            ("movie", 30, "de"),
            ("series", 10, "en"),
            ("series", 10, "fr"),
            ("series", 21, "es"),
        ]
        assert attempt_rows == [
            ("movie", 30, "de", 1.0, 4.0),
            ("series", 10, "en", 3.0, 5.0),
            ("series", 10, "fr", 2.0, 2.0),
            ("series", 21, "es", 7.0, 7.0),
        ]

        migration.downgrade()

        remaining_tables = set(sa.inspect(connection).get_table_names())
        assert "table_missing_subtitles" not in remaining_tables
        assert "table_failed_subtitle_attempts" not in remaining_tables
        assert {"table_episodes", "table_movies"}.issubset(remaining_tables)
    finally:
        connection.close()


def test_wanted_state_migration_does_not_use_literal_eval():
    migration_text = open("migrations/versions/e6cbb0f6f9b1_.py", encoding="utf-8").read()

    assert "literal_eval" not in migration_text


def test_wanted_state_migration_generic_insert_helpers_use_sqlalchemy():
    import importlib.util

    import sqlalchemy as sa

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    metadata = sa.MetaData()
    sa.Table(
        "table_missing_subtitles",
        metadata,
        sa.Column("media_type", sa.Text),
        sa.Column("media_id", sa.Integer),
        sa.Column("language", sa.Text),
    )
    sa.Table(
        "table_failed_subtitle_attempts",
        metadata,
        sa.Column("media_type", sa.Text),
        sa.Column("media_id", sa.Integer),
        sa.Column("language", sa.Text),
        sa.Column("initial_attempt_at", sa.Float),
        sa.Column("latest_attempt_at", sa.Float),
    )
    metadata.create_all(connection)

    spec = importlib.util.spec_from_file_location(
        "wanted_state_migration",
        "migrations/versions/e6cbb0f6f9b1_.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    try:
        migration._insert_missing_subtitles(connection, [("series", 10, "en")])
        migration._insert_failed_attempts(connection, [("series", 10, "en", 1.0, 2.0)])

        assert connection.execute(
            sa.text("SELECT media_type, media_id, language FROM table_missing_subtitles")
        ).all() == [("series", 10, "en")]
        assert connection.execute(
            sa.text(
                "SELECT media_type, media_id, language, initial_attempt_at, latest_attempt_at "
                "FROM table_failed_subtitle_attempts"
            )
        ).all() == [("series", 10, "en", 1.0, 2.0)]
    finally:
        connection.close()
