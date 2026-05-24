#!/usr/bin/env python
"""
Standalone pyperf benchmarks for Sonarr/Radarr/SQLite sync hot paths.

Run this via uv so pyperf and Bazarr's Python dependencies are present:

    tmp_req=$(mktemp)
    cat requirements.txt dev-requirements.txt | sed 's/ --only-binary=Pillow//' > "$tmp_req"
    export SZ_USER_AGENT='BazarrBenchmark/1.0' BAZARR_VERSION='benchmark'
    uv run --with-requirements "$tmp_req" python \
      tests/benchmarks/benchmark_sync_hotpaths.py \
      --config-dir /var/lib/bazarr \
      --benchmark sync.sqlite \
      --fast
    rm -f "$tmp_req"
"""

import argparse
import importlib
import json
import os
import shutil
import sqlite3
import sys

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.benchmarks.helpers import StaticDatabase, default_repo_root, install_repo, restore, swap  # noqa: E402
from tests.benchmarks.scenarios import SCENARIOS, prepared_config_dir  # noqa: E402

try:
    import pyperf
except ModuleNotFoundError as exc:
    raise SystemExit("pyperf is required; run this benchmark via uv with requirements.txt + dev-requirements.txt.") from exc


SYNC_INDEX_DEFINITIONS = (
    (
        "idx_table_episodes_sonarrSeriesId",
        "CREATE INDEX IF NOT EXISTS idx_table_episodes_sonarrSeriesId "
        "ON table_episodes(sonarrSeriesId)",
    ),
    (
        "idx_table_episodes_missing_subtitles",
        "CREATE INDEX IF NOT EXISTS idx_table_episodes_missing_subtitles "
        "ON table_episodes(missing_subtitles) "
        "WHERE missing_subtitles IS NOT NULL AND missing_subtitles != '[]'",
    ),
    (
        "idx_table_movies_missing_subtitles",
        "CREATE INDEX IF NOT EXISTS idx_table_movies_missing_subtitles "
        "ON table_movies(missing_subtitles) "
        "WHERE missing_subtitles IS NOT NULL AND missing_subtitles != '[]'",
    ),
)


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


class _Queue:
    @staticmethod
    def add_job_from_function(*args, **kwargs):
        return None

    @staticmethod
    def update_job_progress(*args, **kwargs):
        return None

    @staticmethod
    def update_job_name(*args, **kwargs):
        return None


class _SeriesMassDownloadDatabase:
    def __init__(self, series_row, episodes_details):
        self.series_row = series_row
        self.episodes_details = episodes_details
        self._execute_count = 0

    def execute(self, statement):
        self._execute_count += 1
        if self._execute_count == 1:
            return _Result(first_value=self.series_row)
        return _Result(all_value=self.episodes_details)


def _model(**values):
    return SimpleNamespace(
        __table__=SimpleNamespace(columns=[_Column(name) for name in values]),
        **values,
    )


def _series_row(show):
    return {
        "sonarrSeriesId": int(show["id"]),
        "title": show["title"],
        "path": show["path"],
    }


def _legacy_double_episode_sync(series_sync, shows, audio_profiles, tags_dict, language_profiles):
    for show in shows:
        series_sync.update_one_series(
            show["id"],
            action="updated",
            series_data=[show],
            audio_profiles=audio_profiles,
            tagsDict=tags_dict,
            language_profiles=language_profiles,
        )
        series_sync.sync_episodes(series_id=show["id"])


def _legacy_force_series_update(series_sync, show, fixed_now):
    existing_series = series_sync.database.execute(None).first()[0]
    series = series_sync.seriesParser(show, action="update")
    existing_values = {
        column.name: getattr(existing_series, column.name)
        for column in existing_series.__table__.columns
    }
    if series.items() <= existing_values.items():
        series["updated_at_timestamp"] = fixed_now
        series_sync.database.execute(None)


def _legacy_episode_parser_with_stat(parser_module, episode, enable_strm_support, parse_embedded_audio_track):
    parser_module.os.path.getsize(parser_module.path_mappings.path_replace(episode["episodeFile"]["path"]))
    parser_module.episodeParser(
        episode,
        enable_strm_support=enable_strm_support,
        parse_embedded_audio_track=parse_embedded_audio_track,
    )


class BenchmarkSuite:
    def __init__(self, repo_root, config_dir, args):
        self.repo_root = repo_root
        self.config_dir = config_dir
        self.db_path = config_dir / "db" / "bazarr.db"
        self.args = args

        self._ensure_sync_indexes(self.db_path)
        self.unindexed_db_path = self._build_unindexed_db_copy()
        self.series_ids = self._load_series_ids(args.series_limit)

        self.synthetic_shows = [self._build_show(index) for index in range(args.series_loop_count)]
        self.cached_show = self._build_show(0)
        self.cached_show_row = _series_row(self.cached_show)
        self.cached_existing_series = _model(**self.cached_show_row)
        self.fixed_now = datetime(2026, 1, 1)

        self.audio_profiles = [(1, "English"), (2, "French")]
        self.tags_dict = [{"id": 1, "label": "benchmark"}]
        self.language_profiles = [(1, "English", "benchmark")]

        self.movie_samples = [self._build_movie(index) for index in range(args.movie_count)]
        self.current_movies_in_db = {
            movie["id"]: {
                "radarrId": movie["id"],
                "title": movie["title"],
                "path": movie["movieFile"]["path"],
                "profileId": 1,
            }
            for movie in self.movie_samples
        }
        self.current_movies_db_kv = [row.items() for row in self.current_movies_in_db.values()]

        self.video_path = config_dir / "db" / "benchmark-video.mkv"
        with open(self.video_path, "wb") as handle:
            handle.write(b"0" * 25000)
        self.episode_payload = self._build_episode_payload()
        self.provider_names = [
            "opensubtitlescom",
            "opensubtitles",
            "subdl",
            "podnapisi",
            "addic7ed",
            "subsource",
            "animetosho",
            "jimaku",
            "betaseries",
            "napisy24",
            "titulky",
            "titlovi",
        ]
        self.throttled_providers = {
            name: ("TooManyRequests", datetime(2030, 1, 1), "1 hour")
            for name in ("addic7ed", "subsource", "titlovi")
        }
        self.series_download_series_id = 50000
        self.series_download_series_row = SimpleNamespace(
            path="/benchmark/series",
            title="Benchmark Series Download",
        )
        self.series_download_episode_rows = [
            SimpleNamespace(
                sonarrEpisodeId=60000 + index,
                title="Benchmark Series Download",
                season=(index // 10) + 1,
                episode=(index % 10) + 1,
                episodeTitle=f"Episode {index + 1}",
                missing_subtitles='["en", "fr", "de"]',
            )
            for index in range(args.provider_loop_count)
        ]
        self.upgrade_episode_rows = [
            SimpleNamespace(
                id=70000 + index,
                seriesTitle="Benchmark Upgrade Series",
                season=(index // 10) + 1,
                episode=(index % 10) + 1,
                title=f"Episode {index + 1}",
                language="en",
                audio_language="[]",
                video_path=f"/benchmark/series/episode_{index + 1}.mkv",
                sceneName=f"benchmark.series.episode.{index + 1}",
                score=100,
                sonarrEpisodeId=71000 + index,
                sonarrSeriesId=72000,
                subtitles_path=f"/benchmark/subs/episode_{index + 1}.srt",
                path=f"/benchmark/series/episode_{index + 1}.mkv",
                profileId=1,
                external_subtitles=f"/benchmark/subs/episode_{index + 1}.srt",
            )
            for index in range(args.provider_loop_count)
        ]
        self.upgrade_movie_rows = [
            SimpleNamespace(
                id=80000 + index,
                title=f"Benchmark Upgrade Movie {index + 1}",
                language="en",
                audio_language="[]",
                video_path=f"/benchmark/movies/movie_{index + 1}.mkv",
                sceneName=f"benchmark.movie.{index + 1}",
                score=100,
                radarrId=81000 + index,
                subtitles_path=f"/benchmark/subs/movie_{index + 1}.srt",
                path=f"/benchmark/movies/movie_{index + 1}.mkv",
                profileId=1,
                external_subtitles=f"/benchmark/subs/movie_{index + 1}.srt",
            )
            for index in range(args.provider_loop_count)
        ]

    def _import(self, module_name):
        original_argv = sys.argv[:]
        try:
            sys.argv = [
                sys.argv[0],
                "-c",
                str(self.config_dir),
                "--no-update",
                "--no-signalr",
                "--no-tasks",
            ]
            return importlib.import_module(module_name)
        finally:
            sys.argv = original_argv

    def _ensure_sync_indexes(self, db_path):
        with sqlite3.connect(db_path) as conn:
            for _, sql in SYNC_INDEX_DEFINITIONS:
                conn.execute(sql)
            conn.commit()

    def _build_unindexed_db_copy(self):
        target = self.config_dir / "db" / "bazarr-no-sync-indexes.db"
        shutil.copy2(self.db_path, target)
        with sqlite3.connect(target) as conn:
            for name, _ in SYNC_INDEX_DEFINITIONS:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            conn.commit()
        return target

    def _load_series_ids(self, limit):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT sonarrSeriesId
                FROM table_episodes
                GROUP BY sonarrSeriesId
                ORDER BY COUNT(*) DESC, sonarrSeriesId
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row[0] for row in rows]

    def _build_show(self, index):
        return {
            "id": 10000 + index,
            "title": f"Benchmark Show {index + 1}",
            "path": f"/benchmark/series/{index + 1:05d}",
            "monitored": True,
        }

    def _build_movie(self, index):
        return {
            "id": index + 1,
            "title": f"Benchmark Movie {index + 1}",
            "hasFile": True,
            "monitored": True,
            "movieFile": {
                "path": f"/benchmark/movies/{index + 1:05d}.mkv",
                "size": 25000,
            },
        }

    def _build_episode_payload(self):
        return {
            "hasFile": True,
            "seriesId": self.cached_show["id"],
            "id": 20000,
            "title": "Benchmark Episode",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "monitored": True,
            "episodeFile": {
                "id": 30000,
                "path": str(self.video_path),
                "size": 25000,
                "language": {"name": "English"},
                "mediaInfo": {},
                "quality": {"quality": {"name": "HDTV-1080p"}},
            },
        }

    def benchmark_names(self):
        return [
            "sync.sqlite.series_episode_lookup.indexed",
            "sync.sqlite.series_episode_lookup.unindexed",
            "sync.sqlite.series_episode_lookup.single_select",
            "sync.sqlite.series_episode_lookup.double_select",
            "sync.sqlite.missing_subtitles_count.indexed",
            "sync.sqlite.missing_subtitles_count.unindexed",
            "sync.sonarr.update_series.single_episode_sync",
            "sync.sonarr.update_series.double_episode_sync",
            "sync.sonarr.update_one_series.cached_context",
            "sync.sonarr.update_one_series.uncached_context",
            "sync.sonarr.update_one_series.skip_unchanged",
            "sync.sonarr.update_one_series.write_unchanged_row",
            "sync.sonarr.episode_parser.cached_settings",
            "sync.sonarr.episode_parser.dynamic_settings",
            "sync.sonarr.episode_parser.filesize_fastpath",
            "sync.sonarr.episode_parser.filesize_with_stat",
            "sync.radarr.movie_compare.keyed",
            "sync.radarr.movie_compare.subset_scan",
            "providers.get_providers.repeated",
            "providers.mass_download.series_loop",
            "providers.upgrade.episodes_loop",
            "providers.upgrade.movies_loop",
        ]

    def selected_names(self):
        names = self.benchmark_names()
        if not self.args.benchmark:
            return names
        return [name for name in names if any(name.startswith(prefix) for prefix in self.args.benchmark)]

    def metadata(self):
        return {
            "scenario": self.args.scenario,
            "label": self.args.label,
            "series_ids": str(len(self.series_ids)),
            "series_loop_count": str(self.args.series_loop_count),
            "movie_count": str(self.args.movie_count),
            "sqlite_rounds": str(self.args.sqlite_rounds),
            "provider_loop_count": str(self.args.provider_loop_count),
        }

    def _provider_environment(self, providers_module):
        return swap(
            providers_module,
            provider_registry=SimpleNamespace(names=lambda: list(self.provider_names)),
            settings=SimpleNamespace(general=SimpleNamespace(enabled_providers=list(self.provider_names))),
            tp=dict(self.throttled_providers),
            set_throttled_providers=lambda data: None,
        )

    def _sqlite_series_episode_lookup(self, db_path):
        with sqlite3.connect(db_path) as conn:
            for _ in range(self.args.sqlite_rounds):
                for series_id in self.series_ids:
                    conn.execute(
                        """
                        SELECT sonarrEpisodeId, episode_file_id, path
                        FROM table_episodes
                        WHERE sonarrSeriesId = ?
                        """,
                        (series_id,),
                    ).fetchall()

    def _sqlite_series_episode_lookup_indexed(self):
        self._sqlite_series_episode_lookup(self.db_path)

    def _sqlite_series_episode_lookup_unindexed(self):
        self._sqlite_series_episode_lookup(self.unindexed_db_path)

    def _sqlite_series_episode_lookup_single_select(self):
        with sqlite3.connect(self.db_path) as conn:
            for _ in range(self.args.sqlite_rounds):
                for series_id in self.series_ids:
                    conn.execute(
                        """
                        SELECT *
                        FROM table_episodes
                        WHERE sonarrSeriesId = ?
                        """,
                        (series_id,),
                    ).fetchall()

    def _sqlite_series_episode_lookup_double_select(self):
        with sqlite3.connect(self.db_path) as conn:
            for _ in range(self.args.sqlite_rounds):
                for series_id in self.series_ids:
                    conn.execute(
                        """
                        SELECT sonarrEpisodeId
                        FROM table_episodes
                        WHERE sonarrSeriesId = ?
                        """,
                        (series_id,),
                    ).fetchall()
                    conn.execute(
                        """
                        SELECT *
                        FROM table_episodes
                        WHERE sonarrSeriesId = ?
                        """,
                        (series_id,),
                    ).fetchall()

    def _sqlite_missing_subtitles_count(self, db_path):
        with sqlite3.connect(db_path) as conn:
            for _ in range(self.args.sqlite_rounds):
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM table_episodes
                    WHERE missing_subtitles IS NOT NULL AND missing_subtitles != '[]'
                    """
                ).fetchone()
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM table_movies
                    WHERE missing_subtitles IS NOT NULL AND missing_subtitles != '[]'
                    """
                ).fetchone()

    def _sqlite_missing_subtitles_count_indexed(self):
        self._sqlite_missing_subtitles_count(self.db_path)

    def _sqlite_missing_subtitles_count_unindexed(self):
        self._sqlite_missing_subtitles_count(self.unindexed_db_path)

    def _series_update_environment(self, series_sync):
        sync_state = {"calls": 0}

        def sync_episodes(*args, **kwargs):
            sync_state["calls"] += 1

        def update_one_series(*args, **kwargs):
            if kwargs.get("sync_episodes_after_update", True) and not kwargs.get("is_signalr", False):
                sync_state["calls"] += 1

        originals = swap(
            series_sync,
            check_sonarr_rootfolder=lambda: None,
            get_series_from_sonarr_api=lambda **kwargs: list(self.synthetic_shows),
            get_profile_list=lambda: list(self.audio_profiles),
            get_tags=lambda: list(self.tags_dict),
            get_language_profiles=lambda: list(self.language_profiles),
            update_one_series=update_one_series,
            sync_episodes=sync_episodes,
            database=StaticDatabase(
                all_value=[SimpleNamespace(sonarrSeriesId=show["id"]) for show in self.synthetic_shows]
            ),
            jobs_queue=_Queue(),
            settings=SimpleNamespace(
                general=SimpleNamespace(debug=False),
                sonarr=SimpleNamespace(apikey="sonarr-key", sync_only_monitored_series=False),
            ),
        )
        return sync_state, originals

    def _sonarr_update_series_single_episode_sync(self):
        series_sync = self._import("sonarr.sync.series")
        sync_state, originals = self._series_update_environment(series_sync)
        try:
            series_sync.update_series(job_id="job")
        finally:
            restore(series_sync, originals)
        return sync_state["calls"]

    def _sonarr_update_series_double_episode_sync(self):
        series_sync = self._import("sonarr.sync.series")
        sync_state, originals = self._series_update_environment(series_sync)
        try:
            _legacy_double_episode_sync(
                series_sync,
                self.synthetic_shows,
                self.audio_profiles,
                self.tags_dict,
                self.language_profiles,
            )
        finally:
            restore(series_sync, originals)
        return sync_state["calls"]

    def _series_update_one_environment(self, series_sync):
        parser_stub = lambda show, **kwargs: dict(_series_row(show))
        originals = swap(
            series_sync,
            database=StaticDatabase(first_value=(self.cached_existing_series,)),
            seriesParser=parser_stub,
            get_profile_list=lambda: list(self.audio_profiles),
            get_tags=lambda: list(self.tags_dict),
            get_language_profiles=lambda: list(self.language_profiles),
            get_series_from_sonarr_api=lambda **kwargs: [dict(self.cached_show)],
            sync_episodes=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
            settings=SimpleNamespace(
                general=SimpleNamespace(serie_default_enabled=False),
                sonarr=SimpleNamespace(apikey="sonarr-key"),
            ),
        )
        return originals

    def _sonarr_update_one_series_cached_context(self):
        series_sync = self._import("sonarr.sync.series")
        originals = self._series_update_one_environment(series_sync)
        try:
            for _ in range(self.args.series_loop_count):
                series_sync.update_one_series(
                    self.cached_show["id"],
                    action="updated",
                    sync_episodes_after_update=False,
                    series_data=[dict(self.cached_show)],
                    audio_profiles=self.audio_profiles,
                    tagsDict=self.tags_dict,
                    language_profiles=self.language_profiles,
                )
        finally:
            restore(series_sync, originals)

    def _sonarr_update_one_series_uncached_context(self):
        series_sync = self._import("sonarr.sync.series")
        originals = self._series_update_one_environment(series_sync)
        try:
            for _ in range(self.args.series_loop_count):
                series_sync.update_one_series(
                    self.cached_show["id"],
                    action="updated",
                    sync_episodes_after_update=False,
                )
        finally:
            restore(series_sync, originals)

    def _sonarr_update_one_series_skip_unchanged(self):
        self._sonarr_update_one_series_cached_context()

    def _sonarr_update_one_series_write_unchanged_row(self):
        series_sync = self._import("sonarr.sync.series")
        originals = self._series_update_one_environment(series_sync)
        try:
            for _ in range(self.args.series_loop_count):
                _legacy_force_series_update(series_sync, dict(self.cached_show), self.fixed_now)
        finally:
            restore(series_sync, originals)

    def _episode_parser_environment(self, parser_module):
        originals = swap(
            parser_module,
            settings=SimpleNamespace(
                general=SimpleNamespace(enable_strm_support=False, parse_embedded_audio_track=False),
            ),
            audio_language_from_name=lambda name: name,
        )
        path_originals = swap(parser_module.path_mappings, path_replace=lambda path: path)
        return originals, path_originals

    def _sonarr_episode_parser_cached_settings(self):
        parser_module = self._import("sonarr.sync.parser")
        originals, path_originals = self._episode_parser_environment(parser_module)
        try:
            for _ in range(self.args.series_loop_count):
                parser_module.episodeParser(
                    self.episode_payload,
                    enable_strm_support=False,
                    parse_embedded_audio_track=False,
                )
        finally:
            restore(parser_module.path_mappings, path_originals)
            restore(parser_module, originals)

    def _sonarr_episode_parser_dynamic_settings(self):
        parser_module = self._import("sonarr.sync.parser")
        originals, path_originals = self._episode_parser_environment(parser_module)
        try:
            for _ in range(self.args.series_loop_count):
                parser_module.episodeParser(self.episode_payload)
        finally:
            restore(parser_module.path_mappings, path_originals)
            restore(parser_module, originals)

    def _sonarr_episode_parser_filesize_fastpath(self):
        parser_module = self._import("sonarr.sync.parser")
        originals, path_originals = self._episode_parser_environment(parser_module)
        try:
            for _ in range(self.args.series_loop_count):
                parser_module.episodeParser(
                    self.episode_payload,
                    enable_strm_support=False,
                    parse_embedded_audio_track=False,
                )
        finally:
            restore(parser_module.path_mappings, path_originals)
            restore(parser_module, originals)

    def _sonarr_episode_parser_filesize_with_stat(self):
        parser_module = self._import("sonarr.sync.parser")
        originals, path_originals = self._episode_parser_environment(parser_module)
        try:
            for _ in range(self.args.series_loop_count):
                _legacy_episode_parser_with_stat(
                    parser_module,
                    self.episode_payload,
                    enable_strm_support=False,
                    parse_embedded_audio_track=False,
                )
        finally:
            restore(parser_module.path_mappings, path_originals)
            restore(parser_module, originals)

    def _radarr_movie_compare_keyed(self):
        current_movies_id_db = set(self.current_movies_in_db)
        for movie in self.movie_samples:
            if movie["id"] in current_movies_id_db:
                parsed_movie = {
                    "radarrId": movie["id"],
                    "title": movie["title"],
                    "path": movie["movieFile"]["path"],
                    "profileId": 1,
                }
                parsed_movie.items() <= self.current_movies_in_db[movie["id"]].items()

    def _radarr_movie_compare_subset_scan(self):
        for movie in self.movie_samples:
            parsed_movie = {
                "radarrId": movie["id"],
                "title": movie["title"],
                "path": movie["movieFile"]["path"],
                "profileId": 1,
            }
            any(parsed_movie.items() <= row for row in self.current_movies_db_kv)

    def _providers_get_providers_repeated(self):
        providers_module = self._import("app.get_providers")
        originals = self._provider_environment(providers_module)
        try:
            for _ in range(self.args.provider_loop_count):
                providers_module.get_providers()
        finally:
            restore(providers_module, originals)

    def _providers_mass_download_series_loop(self):
        providers_module = self._import("app.get_providers")
        mass_download_series = self._import("subtitles.mass_download.series")
        provider_originals = self._provider_environment(providers_module)
        mass_download_originals = swap(
            mass_download_series,
            database=_SeriesMassDownloadDatabase(
                self.series_download_series_row,
                self.series_download_episode_rows,
            ),
            get_exclusion_clause=lambda media_type: [],
            jobs_queue=_Queue(),
            episode_download_subtitles=lambda *args, **kwargs: None,
            path_mappings=SimpleNamespace(path_replace=lambda path: path),
            os=SimpleNamespace(path=SimpleNamespace(exists=lambda path: True)),
            settings=SimpleNamespace(
                general=SimpleNamespace(
                    use_whisper_fallback=False,
                    use_whisper_fallback_series=False,
                )
            ),
        )
        try:
            mass_download_series.series_download_subtitles(self.series_download_series_id, job_id="job")
        finally:
            restore(mass_download_series, mass_download_originals)
            restore(providers_module, provider_originals)

    def _providers_upgrade_episodes_loop(self):
        providers_module = self._import("app.get_providers")
        upgrade_module = self._import("subtitles.upgrade")
        provider_originals = self._provider_environment(providers_module)
        upgrade_originals = swap(
            upgrade_module,
            database=StaticDatabase(all_value=self.upgrade_episode_rows),
            get_upgradable_episode_subtitles=lambda: {row.id: row.id for row in self.upgrade_episode_rows},
            _language_still_desired=lambda *args, **kwargs: True,
            jobs_queue=_Queue(),
            get_audio_profile_languages=lambda *args, **kwargs: [],
            generate_subtitles=lambda *args, **kwargs: [],
            store_subtitles=lambda *args, **kwargs: None,
            history_log=lambda *args, **kwargs: None,
            send_notifications=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
            _is_hi_required=lambda *args, **kwargs: False,
            path_mappings=SimpleNamespace(path_replace=lambda path: path),
        )
        try:
            upgrade_module.upgrade_episodes_subtitles(job_id="job")
        finally:
            restore(upgrade_module, upgrade_originals)
            restore(providers_module, provider_originals)

    def _providers_upgrade_movies_loop(self):
        providers_module = self._import("app.get_providers")
        upgrade_module = self._import("subtitles.upgrade")
        provider_originals = self._provider_environment(providers_module)
        upgrade_originals = swap(
            upgrade_module,
            database=StaticDatabase(all_value=self.upgrade_movie_rows),
            get_upgradable_movies_subtitles=lambda: {row.id: row.id for row in self.upgrade_movie_rows},
            _language_still_desired=lambda *args, **kwargs: True,
            jobs_queue=_Queue(),
            get_audio_profile_languages=lambda *args, **kwargs: [],
            generate_subtitles=lambda *args, **kwargs: [],
            store_subtitles_movie=lambda *args, **kwargs: None,
            history_log_movie=lambda *args, **kwargs: None,
            send_notifications_movie=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
            _is_hi_required=lambda *args, **kwargs: False,
            path_mappings=SimpleNamespace(path_replace_movie=lambda path: path),
        )
        try:
            upgrade_module.upgrade_movies_subtitles(job_id="job")
        finally:
            restore(upgrade_module, upgrade_originals)
            restore(providers_module, provider_originals)

    def benchmark_map(self):
        return {
            "sync.sqlite.series_episode_lookup.indexed": self._sqlite_series_episode_lookup_indexed,
            "sync.sqlite.series_episode_lookup.unindexed": self._sqlite_series_episode_lookup_unindexed,
            "sync.sqlite.series_episode_lookup.single_select": self._sqlite_series_episode_lookup_single_select,
            "sync.sqlite.series_episode_lookup.double_select": self._sqlite_series_episode_lookup_double_select,
            "sync.sqlite.missing_subtitles_count.indexed": self._sqlite_missing_subtitles_count_indexed,
            "sync.sqlite.missing_subtitles_count.unindexed": self._sqlite_missing_subtitles_count_unindexed,
            "sync.sonarr.update_series.single_episode_sync": self._sonarr_update_series_single_episode_sync,
            "sync.sonarr.update_series.double_episode_sync": self._sonarr_update_series_double_episode_sync,
            "sync.sonarr.update_one_series.cached_context": self._sonarr_update_one_series_cached_context,
            "sync.sonarr.update_one_series.uncached_context": self._sonarr_update_one_series_uncached_context,
            "sync.sonarr.update_one_series.skip_unchanged": self._sonarr_update_one_series_skip_unchanged,
            "sync.sonarr.update_one_series.write_unchanged_row": self._sonarr_update_one_series_write_unchanged_row,
            "sync.sonarr.episode_parser.cached_settings": self._sonarr_episode_parser_cached_settings,
            "sync.sonarr.episode_parser.dynamic_settings": self._sonarr_episode_parser_dynamic_settings,
            "sync.sonarr.episode_parser.filesize_fastpath": self._sonarr_episode_parser_filesize_fastpath,
            "sync.sonarr.episode_parser.filesize_with_stat": self._sonarr_episode_parser_filesize_with_stat,
            "sync.radarr.movie_compare.keyed": self._radarr_movie_compare_keyed,
            "sync.radarr.movie_compare.subset_scan": self._radarr_movie_compare_subset_scan,
            "providers.get_providers.repeated": self._providers_get_providers_repeated,
            "providers.mass_download.series_loop": self._providers_mass_download_series_loop,
            "providers.upgrade.episodes_loop": self._providers_upgrade_episodes_loop,
            "providers.upgrade.movies_loop": self._providers_upgrade_movies_loop,
        }


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run standalone pyperf benchmarks for Bazarr sync and SQLite hot paths.",
        epilog=(
            "Run this via uv, not plain python. Example: "
            "tmp_req=$(mktemp) && cat requirements.txt dev-requirements.txt | "
            "sed 's/ --only-binary=Pillow//' > \"$tmp_req\" && "
            "uv run --with-requirements \"$tmp_req\" python "
            "tests/benchmarks/benchmark_sync_hotpaths.py --config-dir /var/lib/bazarr --benchmark sync.sqlite --fast; "
            "rm -f \"$tmp_req\""
        ),
    )
    parser.add_argument("--repo", default=os.environ.get("BAZARR_BENCHMARK_REPO", str(default_repo_root())))
    parser.add_argument("--config-dir", default=os.environ.get("BAZARR_BENCHMARK_SOURCE_CONFIG_DIR"))
    parser.add_argument("--label", default=os.environ.get("BAZARR_BENCHMARK_LABEL", "sync-hotpaths"))
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=os.environ.get("BAZARR_BENCHMARK_SCENARIO", "baseline"),
    )
    parser.add_argument("--benchmark", action="append", default=[], help="Benchmark name prefix to run")
    parser.add_argument(
        "--series-limit",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_SERIES_LIMIT", "200")),
    )
    parser.add_argument(
        "--series-loop-count",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_SERIES_LOOP_COUNT", "1000")),
    )
    parser.add_argument(
        "--movie-count",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_MOVIE_COUNT", "3000")),
    )
    parser.add_argument(
        "--sqlite-rounds",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_SQLITE_ROUNDS", "5")),
    )
    parser.add_argument(
        "--provider-loop-count",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_PROVIDER_LOOP_COUNT", "250")),
    )
    args, remaining = parser.parse_known_args(argv)
    if not args.config_dir:
        parser.error("the following arguments are required: --config-dir")
    if not args.benchmark and os.environ.get("BAZARR_BENCHMARK_FILTERS"):
        args.benchmark = json.loads(os.environ["BAZARR_BENCHMARK_FILTERS"])
    return args, remaining


def main():
    args, pyperf_argv = _parse_args(sys.argv[1:])

    os.environ.setdefault("SZ_USER_AGENT", "BazarrBenchmark/1.0")
    os.environ.setdefault("BAZARR_VERSION", "benchmark")
    os.environ["BAZARR_BENCHMARK_REPO"] = args.repo
    os.environ["BAZARR_BENCHMARK_SOURCE_CONFIG_DIR"] = args.config_dir
    os.environ["BAZARR_BENCHMARK_LABEL"] = args.label
    os.environ["BAZARR_BENCHMARK_SCENARIO"] = args.scenario
    os.environ["BAZARR_BENCHMARK_SERIES_LIMIT"] = str(args.series_limit)
    os.environ["BAZARR_BENCHMARK_SERIES_LOOP_COUNT"] = str(args.series_loop_count)
    os.environ["BAZARR_BENCHMARK_MOVIE_COUNT"] = str(args.movie_count)
    os.environ["BAZARR_BENCHMARK_SQLITE_ROUNDS"] = str(args.sqlite_rounds)
    os.environ["BAZARR_BENCHMARK_PROVIDER_LOOP_COUNT"] = str(args.provider_loop_count)
    os.environ["BAZARR_BENCHMARK_FILTERS"] = json.dumps(args.benchmark)

    repo_root = Path(args.repo).resolve()
    install_repo(repo_root)

    with prepared_config_dir(args.config_dir, args.scenario) as config_dir:
        suite = BenchmarkSuite(repo_root, config_dir, args)
        inherited = ",".join(
            [
                "BAZARR_BENCHMARK_REPO",
                "BAZARR_BENCHMARK_SOURCE_CONFIG_DIR",
                "BAZARR_BENCHMARK_LABEL",
                "BAZARR_BENCHMARK_SCENARIO",
                "BAZARR_BENCHMARK_SERIES_LIMIT",
                "BAZARR_BENCHMARK_SERIES_LOOP_COUNT",
                "BAZARR_BENCHMARK_MOVIE_COUNT",
                "BAZARR_BENCHMARK_SQLITE_ROUNDS",
                "BAZARR_BENCHMARK_PROVIDER_LOOP_COUNT",
                "BAZARR_BENCHMARK_FILTERS",
                "SZ_USER_AGENT",
                "BAZARR_VERSION",
            ]
        )
        sys.argv = [sys.argv[0], "--inherit-environ", inherited, *pyperf_argv]
        runner = pyperf.Runner()
        runner.metadata.update(suite.metadata())

        bench_map = suite.benchmark_map()
        selected = suite.selected_names()
        if not selected:
            raise SystemExit("No benchmarks matched the requested filters.")

        for name in selected:
            runner.bench_func(name, bench_map[name])


if __name__ == "__main__":
    main()
