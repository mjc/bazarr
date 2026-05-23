#!/usr/bin/env python
"""
Standalone pyperf benchmarks for wanted/adaptive hot paths.

Run this via uv so pyperf and Bazarr's Python dependencies are present:

    tmp_req=$(mktemp)
    cat requirements.txt dev-requirements.txt | sed 's/ --only-binary=Pillow//' > "$tmp_req"
    export SZ_USER_AGENT='BazarrBenchmark/1.0' BAZARR_VERSION='benchmark'
    uv run --with-requirements "$tmp_req" python \
      tests/benchmarks/benchmark_function_hotpaths.py \
      --config-dir /var/lib/bazarr \
      --scenario all-wanted-5lang \
      --benchmark adaptive.is_search_active \
      --fast
    rm -f "$tmp_req"
"""

import argparse
import ast
import gc
import importlib
import inspect
import json
import os
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.benchmarks.helpers import (  # noqa: E402
    StaticDatabase,
    default_repo_root,
    dummy_result,
    install_repo,
    load_attempt_samples,
    load_episode_rows,
    load_movie_rows,
    measure,
    restore,
    swap,
)
from tests.benchmarks.scenarios import SCENARIOS, prepared_config_dir  # noqa: E402

try:
    import pyperf
except ModuleNotFoundError as exc:
    raise SystemExit("pyperf is required; run this benchmark via uv with requirements.txt + dev-requirements.txt.") from exc


class BenchmarkSuite:
    def __init__(self, repo_root, config_dir, args):
        self.repo_root = repo_root
        self.config_dir = config_dir
        self.db_path = config_dir / "db" / "bazarr.db"
        self.args = args
        self.movie_rows = load_movie_rows(self.db_path, args.movie_limit)
        self.episode_rows = load_episode_rows(self.db_path, args.episode_limit)
        self.attempt_samples = load_attempt_samples(self.db_path, args.attempt_limit)

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

    def benchmark_names(self):
        adaptive = self._import("subtitles.adaptive_searching")
        names = [
            "parsing.missing_subtitles.literal_eval",
            "parsing.failed_attempts.literal_eval",
            "adaptive.is_search_active",
            "adaptive.updateFailedAttempts",
            "wanted.movie.hotpath",
            "wanted.series.hotpath",
            "wanted.movie.download_helper",
            "wanted.series.download_helper",
            "wanted.movie.scan_loop",
            "wanted.series.scan_loop",
        ]

        if hasattr(adaptive, "get_adaptive_search_policy"):
            names.append("adaptive.get_adaptive_search_policy")
        if "adaptive_search_policy" in inspect.signature(adaptive.is_search_active).parameters and hasattr(
            adaptive, "get_adaptive_search_policy"
        ):
            names.append("adaptive.is_search_active_cached_policy")
        if hasattr(adaptive, "get_active_search_languages"):
            names.append("adaptive.get_active_search_languages")
            if "adaptive_search_policy" in inspect.signature(adaptive.get_active_search_languages).parameters and hasattr(
                adaptive, "get_adaptive_search_policy"
            ):
                names.append("adaptive.get_active_search_languages_cached_policy")
        try:
            wanted_utils = self._import("subtitles.wanted.utils")
        except ModuleNotFoundError:
            wanted_utils = None
        if wanted_utils is not None and hasattr(wanted_utils, "get_due_missing_languages"):
            names.append("wanted.get_due_missing_languages")
        return names

    def selected_names(self):
        names = self.benchmark_names()
        if not self.args.benchmark:
            return names
        return [name for name in names if any(name.startswith(prefix) for prefix in self.args.benchmark)]

    def metadata(self):
        return {
            "scenario": self.args.scenario,
            "label": self.args.label,
            "movie_rows": str(len(self.movie_rows)),
            "episode_rows": str(len(self.episode_rows)),
            "attempt_rows": str(len(self.attempt_samples["rows"])),
            "attempt_language_pairs": str(len(self.attempt_samples["language_attempt_pairs"])),
        }

    def _adaptive_module(self):
        return self._import("subtitles.adaptive_searching")

    def _parsing_missing_subtitles_literal_eval(self):
        rows = [json_row for json_row, _ in self.attempt_samples["rows"]]
        for value in rows:
            ast.literal_eval(value)

    def _parsing_failed_attempts_literal_eval(self):
        rows = [failed_attempts for _, failed_attempts in self.attempt_samples["rows"]]
        for value in rows:
            ast.literal_eval(value)

    def _adaptive_is_search_active(self):
        adaptive = self._adaptive_module()
        for language, failed_attempts in self.attempt_samples["language_attempt_pairs"]:
            adaptive.is_search_active(language, failed_attempts)

    def _adaptive_is_search_active_cached_policy(self):
        adaptive = self._adaptive_module()
        policy = adaptive.get_adaptive_search_policy()
        for language, failed_attempts in self.attempt_samples["language_attempt_pairs"]:
            adaptive.is_search_active(language, failed_attempts, adaptive_search_policy=policy)

    def _adaptive_get_policy(self):
        adaptive = self._adaptive_module()
        adaptive.get_adaptive_search_policy()

    def _adaptive_get_active_search_languages(self):
        adaptive = self._adaptive_module()
        for desired_languages, failed_attempts in self.attempt_samples["desired_language_lists"]:
            adaptive.get_active_search_languages(desired_languages, failed_attempts)

    def _adaptive_get_active_search_languages_cached_policy(self):
        adaptive = self._adaptive_module()
        policy = adaptive.get_adaptive_search_policy()
        for desired_languages, failed_attempts in self.attempt_samples["desired_language_lists"]:
            adaptive.get_active_search_languages(
                desired_languages,
                failed_attempts,
                adaptive_search_policy=policy,
            )

    def _wanted_due_missing_languages(self):
        wanted_utils = self._import("subtitles.wanted.utils")
        for desired_languages, failed_attempts in self.attempt_samples["desired_language_lists"]:
            wanted_utils.get_due_missing_languages(str(desired_languages), failed_attempts)

    def _adaptive_update_failed_attempts(self):
        adaptive = self._adaptive_module()
        for _, failed_attempts in self.attempt_samples["rows"]:
            for language in ("en", "fr", "es:hi"):
                adaptive.updateFailedAttempts(language, failed_attempts)

    def _wanted_movie_hotpath(self):
        wanted_movies = self._import("subtitles.wanted.movies")
        originals = swap(
            wanted_movies,
            generate_subtitles=lambda *args, **kwargs: iter([dummy_result()]),
            get_audio_profile_languages=lambda *args, **kwargs: [],
            store_subtitles_movie=lambda *args, **kwargs: None,
            history_log_movie=lambda *args, **kwargs: None,
            send_notifications_movie=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
        )
        try:
            for row in self.movie_rows:
                wanted_movies._wanted_movie(row, ["provider"])
        finally:
            restore(wanted_movies, originals)

    def _wanted_series_hotpath(self):
        wanted_series = self._import("subtitles.wanted.series")
        originals = swap(
            wanted_series,
            generate_subtitles=lambda *args, **kwargs: iter([dummy_result()]),
            get_audio_profile_languages=lambda *args, **kwargs: [],
            store_subtitles=lambda *args, **kwargs: None,
            history_log=lambda *args, **kwargs: None,
            send_notifications=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
        )
        try:
            for row in self.episode_rows:
                wanted_series._wanted_episode(row, ["provider"])
        finally:
            restore(wanted_series, originals)

    def _wanted_movie_download_helper(self):
        wanted_movies = self._import("subtitles.wanted.movies")
        movie = self.movie_rows[0]
        originals = swap(
            wanted_movies,
            database=StaticDatabase(first_value=movie),
            get_subtitles=lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "/tmp/sub.srt"}],
            get_providers=lambda: ["provider"],
            generate_subtitles=lambda *args, **kwargs: iter([dummy_result()]),
            get_audio_profile_languages=lambda *args, **kwargs: [],
            store_subtitles_movie=lambda *args, **kwargs: None,
            history_log_movie=lambda *args, **kwargs: None,
            send_notifications_movie=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
            list_missing_subtitles_movies=lambda *args, **kwargs: None,
        )
        try:
            for _ in range(len(self.movie_rows)):
                wanted_movies.wanted_download_subtitles_movie(movie.radarrId, job_id="job")
        finally:
            restore(wanted_movies, originals)

    def _wanted_series_download_helper(self):
        wanted_series = self._import("subtitles.wanted.series")
        episode = self.episode_rows[0]
        originals = swap(
            wanted_series,
            database=StaticDatabase(first_value=episode),
            get_subtitles=lambda *args, **kwargs: [{"embedded_track_id": 1, "path": "/tmp/sub.srt"}],
            get_providers=lambda: ["provider"],
            generate_subtitles=lambda *args, **kwargs: iter([dummy_result()]),
            get_audio_profile_languages=lambda *args, **kwargs: [],
            store_subtitles=lambda *args, **kwargs: None,
            history_log=lambda *args, **kwargs: None,
            send_notifications=lambda *args, **kwargs: None,
            event_stream=lambda *args, **kwargs: None,
            list_missing_subtitles=lambda *args, **kwargs: None,
        )
        try:
            for _ in range(len(self.episode_rows)):
                wanted_series.wanted_download_subtitles(episode.sonarrEpisodeId, job_id="job")
        finally:
            restore(wanted_series, originals)

    def _wanted_movie_scan_loop(self):
        wanted_movies = self._import("subtitles.wanted.movies")
        originals = swap(
            wanted_movies,
            database=StaticDatabase(all_value=self.movie_rows),
            get_exclusion_clause=lambda media_type: [],
            jobs_queue=type(
                "Queue",
                (),
                {
                    "add_job_from_function": staticmethod(lambda *args, **kwargs: None),
                    "update_job_progress": staticmethod(lambda *args, **kwargs: None),
                    "update_job_name": staticmethod(lambda *args, **kwargs: None),
                },
            )(),
            get_providers=lambda: ["provider"],
            wanted_download_subtitles_movie=lambda *args, **kwargs: None,
        )
        try:
            wanted_movies.wanted_search_missing_subtitles_movies(job_id="job")
        finally:
            restore(wanted_movies, originals)

    def _wanted_series_scan_loop(self):
        wanted_series = self._import("subtitles.wanted.series")
        originals = swap(
            wanted_series,
            database=StaticDatabase(all_value=self.episode_rows),
            get_exclusion_clause=lambda media_type: [],
            jobs_queue=type(
                "Queue",
                (),
                {
                    "add_job_from_function": staticmethod(lambda *args, **kwargs: None),
                    "update_job_progress": staticmethod(lambda *args, **kwargs: None),
                    "update_job_name": staticmethod(lambda *args, **kwargs: None),
                },
            )(),
            get_providers=lambda: ["provider"],
            wanted_download_subtitles=lambda *args, **kwargs: None,
        )
        try:
            wanted_series.wanted_search_missing_subtitles_series(job_id="job")
        finally:
            restore(wanted_series, originals)

    def benchmark_map(self):
        bench_map = {
            "parsing.missing_subtitles.literal_eval": self._parsing_missing_subtitles_literal_eval,
            "parsing.failed_attempts.literal_eval": self._parsing_failed_attempts_literal_eval,
            "adaptive.is_search_active": self._adaptive_is_search_active,
            "adaptive.updateFailedAttempts": self._adaptive_update_failed_attempts,
            "wanted.movie.hotpath": self._wanted_movie_hotpath,
            "wanted.series.hotpath": self._wanted_series_hotpath,
            "wanted.movie.download_helper": self._wanted_movie_download_helper,
            "wanted.series.download_helper": self._wanted_series_download_helper,
            "wanted.movie.scan_loop": self._wanted_movie_scan_loop,
            "wanted.series.scan_loop": self._wanted_series_scan_loop,
        }
        adaptive = self._adaptive_module()
        if hasattr(adaptive, "get_adaptive_search_policy"):
            bench_map["adaptive.get_adaptive_search_policy"] = self._adaptive_get_policy
        if "adaptive_search_policy" in inspect.signature(adaptive.is_search_active).parameters and hasattr(
            adaptive, "get_adaptive_search_policy"
        ):
            bench_map["adaptive.is_search_active_cached_policy"] = self._adaptive_is_search_active_cached_policy
        if hasattr(adaptive, "get_active_search_languages"):
            bench_map["adaptive.get_active_search_languages"] = self._adaptive_get_active_search_languages
            if "adaptive_search_policy" in inspect.signature(adaptive.get_active_search_languages).parameters and hasattr(
                adaptive, "get_adaptive_search_policy"
            ):
                bench_map[
                    "adaptive.get_active_search_languages_cached_policy"
                ] = self._adaptive_get_active_search_languages_cached_policy
        try:
            wanted_utils = self._import("subtitles.wanted.utils")
        except ModuleNotFoundError:
            wanted_utils = None
        if wanted_utils is not None and hasattr(wanted_utils, "get_due_missing_languages"):
            bench_map["wanted.get_due_missing_languages"] = self._wanted_due_missing_languages
        return bench_map


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run standalone pyperf benchmarks for Bazarr wanted/adaptive hot paths.",
        epilog=(
            "Run this via uv, not plain python. Example: "
            "tmp_req=$(mktemp) && cat requirements.txt dev-requirements.txt | "
            "sed 's/ --only-binary=Pillow//' > \"$tmp_req\" && "
            "uv run --with-requirements \"$tmp_req\" python "
            "tests/benchmarks/benchmark_function_hotpaths.py --config-dir /var/lib/bazarr --fast; "
            "rm -f \"$tmp_req\""
        ),
    )
    parser.add_argument("--repo", default=os.environ.get("BAZARR_BENCHMARK_REPO", str(default_repo_root())))
    parser.add_argument("--config-dir", default=os.environ.get("BAZARR_BENCHMARK_SOURCE_CONFIG_DIR"))
    parser.add_argument("--label", default=os.environ.get("BAZARR_BENCHMARK_LABEL", "function-hotpaths"))
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=os.environ.get("BAZARR_BENCHMARK_SCENARIO", "baseline"),
    )
    parser.add_argument("--benchmark", action="append", default=[], help="Benchmark name prefix to run")
    parser.add_argument("--movie-limit", type=int, default=int(os.environ.get("BAZARR_BENCHMARK_MOVIE_LIMIT", "1000")))
    parser.add_argument(
        "--episode-limit",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_EPISODE_LIMIT", "1000")),
    )
    parser.add_argument(
        "--attempt-limit",
        type=int,
        default=int(os.environ.get("BAZARR_BENCHMARK_ATTEMPT_LIMIT", "5000")),
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
    os.environ["BAZARR_BENCHMARK_MOVIE_LIMIT"] = str(args.movie_limit)
    os.environ["BAZARR_BENCHMARK_EPISODE_LIMIT"] = str(args.episode_limit)
    os.environ["BAZARR_BENCHMARK_ATTEMPT_LIMIT"] = str(args.attempt_limit)
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
                "BAZARR_BENCHMARK_MOVIE_LIMIT",
                "BAZARR_BENCHMARK_EPISODE_LIMIT",
                "BAZARR_BENCHMARK_ATTEMPT_LIMIT",
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
