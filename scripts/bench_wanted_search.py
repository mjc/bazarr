#!/usr/bin/env python3
"""Benchmark Bazarr wanted-search scheduling paths against copied SQLite DBs.

This intentionally stubs provider/network/filesystem subtitle download work while
leaving the scheduled wanted-search entrypoints, candidate filtering, detail
queries, adaptive due checks, and failed-attempt writes in play.
"""

import argparse
import gc
import importlib.util
import os
import shutil
import statistics
import sys
import time


def reset_modules():
    for name in list(sys.modules):
        if (
            name == "app"
            or name.startswith("app.")
            or name == "subtitles"
            or name.startswith("subtitles.")
            or name == "utilities"
            or name.startswith("utilities.")
            or name == "sonarr"
            or name.startswith("sonarr.")
            or name == "radarr"
            or name.startswith("radarr.")
        ):
            sys.modules.pop(name, None)


def prepare_config(source_db, config_dir):
    if os.path.exists(config_dir):
        shutil.rmtree(config_dir)
    os.makedirs(os.path.join(config_dir, "db"))
    os.makedirs(os.path.join(config_dir, "config"))
    shutil.copy2(source_db, os.path.join(config_dir, "db", "bazarr.db"))


def migrate_normalized(repo_dir, config_dir):
    import sqlalchemy as sa

    db_path = os.path.join(config_dir, "db", "bazarr.db")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    migration_path = os.path.join(repo_dir, "migrations", "versions", "e6cbb0f6f9b1_.py")
    spec = importlib.util.spec_from_file_location("wanted_state_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS table_missing_subtitles (
                id INTEGER PRIMARY KEY,
                media_type TEXT NOT NULL,
                media_id INTEGER NOT NULL,
                language TEXT NOT NULL,
                CONSTRAINT uc_missing_subtitles_language UNIQUE (media_type, media_id, language)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS table_failed_subtitle_attempts (
                id INTEGER PRIMARY KEY,
                media_type TEXT NOT NULL,
                media_id INTEGER NOT NULL,
                language TEXT NOT NULL,
                initial_attempt_at FLOAT NOT NULL,
                latest_attempt_at FLOAT NOT NULL,
                CONSTRAINT uc_failed_subtitle_attempts_language UNIQUE (media_type, media_id, language)
            )
            """
        )
        migration._backfill_media_state_sqlite(conn, "series", "sonarrEpisodeId", "table_episodes")
        migration._backfill_media_state_sqlite(conn, "movie", "radarrId", "table_movies")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_missing_subtitles_media "
            "ON table_missing_subtitles (media_type, media_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_failed_subtitle_attempts_media "
            "ON table_failed_subtitle_attempts (media_type, media_id)"
        )
    engine.dispose()


class JobsQueue:
    def __init__(self):
        self.progress_updates = 0

    def add_job_from_function(self, *args, **kwargs):
        return None

    def update_job_progress(self, *args, **kwargs):
        self.progress_updates += 1

    def update_job_name(self, *args, **kwargs):
        return None


def install_repo(repo_dir, config_dir):
    reset_modules()
    for path in (
        os.path.join(repo_dir, "libs"),
        os.path.join(repo_dir, "bazarr"),
        os.path.join(repo_dir, "custom_libs"),
    ):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    sys.argv = ["bench_wanted_search.py", "-c", config_dir, "--no-update"]


def count_due_languages(wanted, media_type, media_id, missing_subtitles, failed_attempts, due_languages):
    if due_languages is not None:
        return len(due_languages)
    if hasattr(wanted, "get_due_missing_languages_for_media"):
        return len(wanted.get_due_missing_languages_for_media(media_type, media_id))
    return sum(
        1
        for language in wanted.ast.literal_eval(missing_subtitles)
        if wanted.is_search_active(
            desired_language=language,
            attempt_string=failed_attempts,
        )
    )


def import_and_patch(media_type, counters, mode):
    if mode == "download-planning":
        from subtitles import download
        from subtitles import pool as subtitle_pool

        class Pool:
            providers = ["provider"]

        class Video:
            original_path = "/tmp/bazarr-bench-video.mkv"

        class Language:
            def __init__(self, basename, hi=False, forced=False):
                self.basename = basename
                self.hi = hi
                self.forced = forced

            def __eq__(self, other):
                return (
                    isinstance(other, Language) and
                    self.basename == other.basename and
                    self.hi == other.hi and
                    self.forced == other.forced
                )

            def __hash__(self):
                return hash((self.basename, self.hi, self.forced))

        def language_objects(languages):
            return {
                Language(language[0], language[1] == "True", language[2] == "True")
                for language in languages
            }

        subtitle_pool._update_pool = lambda *args, **kwargs: False
        download._get_pool = lambda *args, **kwargs: Pool()
        download._get_language_obj = language_objects
        download.check_missing_languages = lambda *args, **kwargs: {
            Language("en"),
            Language("fr"),
            Language("de"),
            Language("es"),
            Language("pt"),
            Language("en", hi=True),
            Language("fr", hi=True),
            Language("en", forced=True),
            Language("fr", forced=True),
        }
        download.get_video = lambda *args, **kwargs: Video()
        download._set_forced_providers = lambda *args, **kwargs: None
        download._get_scores = lambda *args, **kwargs: (0, 100, {})
        download.download_best_subtitles = lambda *args, **kwargs: {}

    if media_type == "series":
        from subtitles.wanted import series as wanted

        def wanted_download_subtitles(sonarr_episode_id, *args, **kwargs):
            counters["candidates"] += 1
            counters["ids"].append(sonarr_episode_id)

        def wanted_episode(episode, *args, **kwargs):
            counters["candidates"] += 1
            counters["ids"].append(episode.sonarrEpisodeId)
            counters["active_languages"] += count_due_languages(
                wanted,
                "series",
                episode.sonarrEpisodeId,
                episode.missing_subtitles,
                episode.failedAttempts,
                kwargs.get("due_languages"),
            )

        if mode == "mutating":
            original_wanted_episode = wanted._wanted_episode

            def counted_original_wanted_episode(episode, *args, **kwargs):
                counters["candidates"] += 1
                counters["ids"].append(episode.sonarrEpisodeId)
                counters["active_languages"] += count_due_languages(
                    wanted,
                    "series",
                    episode.sonarrEpisodeId,
                    episode.missing_subtitles,
                    episode.failedAttempts,
                    kwargs.get("due_languages"),
                )
                return original_wanted_episode(episode, *args, **kwargs)

            wanted._wanted_episode = counted_original_wanted_episode
            wanted.get_subtitles = lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}]
            wanted.generate_subtitles = lambda *args, **kwargs: iter(())
        elif mode == "download-planning":
            original_wanted_episode = wanted._wanted_episode

            def counted_original_wanted_episode(episode, *args, **kwargs):
                counters["candidates"] += 1
                counters["ids"].append(episode.sonarrEpisodeId)
                counters["active_languages"] += count_due_languages(
                    wanted,
                    "series",
                    episode.sonarrEpisodeId,
                    episode.missing_subtitles,
                    episode.failedAttempts,
                    kwargs.get("due_languages"),
                )
                original_wanted_episode(episode, *args, **kwargs)
                return None

            wanted._wanted_episode = counted_original_wanted_episode
            wanted.get_subtitles = lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}]
        elif mode == "scheduled":
            wanted.wanted_download_subtitles = wanted_download_subtitles
            wanted._wanted_episode = wanted_episode
        else:
            wanted.get_subtitles = lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}]
            wanted._wanted_episode = wanted_episode
        entrypoint = wanted.wanted_search_missing_subtitles_series
    else:
        from subtitles.wanted import movies as wanted

        def wanted_download_subtitles_movie(radarr_id, *args, **kwargs):
            counters["candidates"] += 1
            counters["ids"].append(radarr_id)

        def wanted_movie(movie, *args, **kwargs):
            counters["candidates"] += 1
            counters["ids"].append(movie.radarrId)
            counters["active_languages"] += count_due_languages(
                wanted,
                "movie",
                movie.radarrId,
                movie.missing_subtitles,
                movie.failedAttempts,
                kwargs.get("due_languages"),
            )

        if mode == "mutating":
            original_wanted_movie = wanted._wanted_movie

            def counted_original_wanted_movie(movie, *args, **kwargs):
                counters["candidates"] += 1
                counters["ids"].append(movie.radarrId)
                counters["active_languages"] += count_due_languages(
                    wanted,
                    "movie",
                    movie.radarrId,
                    movie.missing_subtitles,
                    movie.failedAttempts,
                    kwargs.get("due_languages"),
                )
                return original_wanted_movie(movie, *args, **kwargs)

            wanted._wanted_movie = counted_original_wanted_movie
            wanted.get_subtitles = lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}]
            wanted.generate_subtitles = lambda *args, **kwargs: iter(())
        elif mode == "download-planning":
            original_wanted_movie = wanted._wanted_movie

            def counted_original_wanted_movie(movie, *args, **kwargs):
                counters["candidates"] += 1
                counters["ids"].append(movie.radarrId)
                counters["active_languages"] += count_due_languages(
                    wanted,
                    "movie",
                    movie.radarrId,
                    movie.missing_subtitles,
                    movie.failedAttempts,
                    kwargs.get("due_languages"),
                )
                original_wanted_movie(movie, *args, **kwargs)
                return None

            wanted._wanted_movie = counted_original_wanted_movie
            wanted.get_subtitles = lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}]
        elif mode == "scheduled":
            wanted.wanted_download_subtitles_movie = wanted_download_subtitles_movie
            wanted._wanted_movie = wanted_movie
        else:
            wanted.get_subtitles = lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "existing.srt"}]
            wanted._wanted_movie = wanted_movie
        entrypoint = wanted.wanted_search_missing_subtitles_movies

    jobs_queue = JobsQueue()
    wanted.jobs_queue = jobs_queue
    wanted.get_providers = lambda: ["provider"]
    wanted.get_audio_profile_languages = lambda audio_language: []
    return entrypoint, jobs_queue


def run_once(repo_dir, config_dir, media_type, mode):
    counters = {"candidates": 0, "ids": [], "active_languages": 0}
    install_repo(repo_dir, config_dir)
    entrypoint, jobs_queue = import_and_patch(media_type, counters, mode)
    gc.collect()
    start = time.perf_counter()
    entrypoint(job_id=f"bench-{media_type}")
    elapsed_ms = (time.perf_counter() - start) * 1000
    from app.database import close_database

    close_database()
    return {
        "elapsed_ms": elapsed_ms,
        "candidates": counters["candidates"],
        "unique_candidates": len(set(counters["ids"])),
        "active_languages": counters["active_languages"],
        "progress_updates": jobs_queue.progress_updates,
    }


def summarize(samples):
    elapsed = [sample["elapsed_ms"] for sample in samples]
    return {
        "median_ms": statistics.median(elapsed),
        "min_ms": min(elapsed),
        "max_ms": max(elapsed),
        "candidates": samples[-1]["candidates"],
        "unique_candidates": samples[-1]["unique_candidates"],
        "active_languages": samples[-1]["active_languages"],
        "progress_updates": samples[-1]["progress_updates"],
    }


def parse_variant(value):
    parts = value.split(":", 2)
    if len(parts) != 3 or parts[2] not in {"migrate", "nomigrate"}:
        raise argparse.ArgumentTypeError("expected name:/path/to/repo:migrate|nomigrate")
    return parts[0], parts[1], parts[2] == "migrate"


def main():
    os.environ.setdefault("SZ_USER_AGENT", "BazarrBench/1.0")
    os.environ.setdefault("BAZARR_VERSION", "bench")

    parser = argparse.ArgumentParser(
        description="Benchmark wanted-search e2e CPU/DB paths against throwaway SQLite DB copies.",
    )
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--variant", action="append", required=True, type=parse_variant)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument(
        "--mode",
        choices=("scheduled", "planning", "mutating", "download-planning"),
        default="mutating",
        help=(
            "scheduled: candidate loop only; planning: wanted planning without writes; "
            "mutating: no-results wanted search with failed-attempt writes; "
            "download-planning: wanted search through generate_subtitles without download/write work"
        ),
    )
    parser.add_argument("--fresh-each-run", action="store_true")
    args = parser.parse_args()

    for name, repo_dir, needs_migration in args.variant:
        config_dir = f"/tmp/bazarr-bench-config-{name}"

        def reset_config():
            prepare_config(args.source_db, config_dir)
            if needs_migration:
                start = time.perf_counter()
                migrate_normalized(repo_dir, config_dir)
                return (time.perf_counter() - start) * 1000
            return None

        migration_ms = reset_config()

        print(f"\n{name}")
        if migration_ms is not None:
            print(f"migration_ms={migration_ms:.3f}")
        for media_type in ("series", "movies"):
            for _ in range(args.warmups):
                if args.fresh_each_run:
                    reset_config()
                run_once(repo_dir, config_dir, media_type, args.mode)
            samples = []
            migration_samples = []
            for _ in range(args.runs):
                if args.fresh_each_run:
                    sample_migration_ms = reset_config()
                    if sample_migration_ms is not None:
                        migration_samples.append(sample_migration_ms)
                samples.append(run_once(repo_dir, config_dir, media_type, args.mode))
            summary = summarize(samples)
            migration_suffix = ""
            if migration_samples:
                migration_suffix = f" migration_median={statistics.median(migration_samples):.3f}ms"
            print(
                f"{media_type}: median={summary['median_ms']:.3f}ms "
                f"min={summary['min_ms']:.3f}ms max={summary['max_ms']:.3f}ms "
                f"candidates={summary['candidates']} unique={summary['unique_candidates']} "
                f"active_languages={summary['active_languages']} "
                f"progress_updates={summary['progress_updates']}"
                f"{migration_suffix}"
            )


if __name__ == "__main__":
    main()
