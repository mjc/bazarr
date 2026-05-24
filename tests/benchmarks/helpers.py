import ast
import gc
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


def default_repo_root():
    return Path(__file__).resolve().parents[2]


def install_repo(repo_root):
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "bazarr"))
    sys.path.insert(0, str(repo_root / "libs"))
    sys.path.insert(0, str(repo_root / "custom_libs"))


def configure_bazarr_args(config_dir):
    sys.argv = [
        sys.argv[0],
        "-c",
        str(config_dir),
        "--no-update",
        "--no-signalr",
        "--no-tasks",
    ]


def measure(fn, loops=5):
    gc.collect()
    samples = []
    for _ in range(loops):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "samples_ms": samples,
        "median_ms": statistics.median(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
    }


def swap(module, **replacements):
    originals = {}
    for name, value in replacements.items():
        if hasattr(module, name):
            originals[name] = getattr(module, name)
            setattr(module, name, value)
    return originals


def restore(module, originals):
    for name, value in originals.items():
        setattr(module, name, value)


def parse_list_text(value):
    parsed = ast.literal_eval(value)
    return parsed if isinstance(parsed, list) else []


def load_movie_rows(db_path, limit):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT path,
                   missing_subtitles,
                   radarrId,
                   audio_language,
                   sceneName,
                   COALESCE(failedAttempts, '[]'),
                   title,
                   profileId,
                   COALESCE(subtitles, '[]')
            FROM table_movies
            WHERE missing_subtitles IS NOT NULL AND missing_subtitles != '[]'
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        SimpleNamespace(
            path=row[0],
            missing_subtitles=row[1],
            radarrId=row[2],
            audio_language=row[3],
            sceneName=row[4],
            failedAttempts=row[5],
            title=row[6],
            profileId=row[7],
            subtitles=row[8],
        )
        for row in rows
    ]


def load_episode_rows(db_path, limit):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.path,
                   e.missing_subtitles,
                   e.sonarrEpisodeId,
                   e.sonarrSeriesId,
                   e.audio_language,
                   e.sceneName,
                   COALESCE(e.failedAttempts, '[]'),
                   s.title,
                   s.profileId,
                   e.season,
                   e.episode,
                   e.title,
                   COALESCE(e.subtitles, '[]')
            FROM table_episodes e
            JOIN table_shows s ON s.sonarrSeriesId = e.sonarrSeriesId
            WHERE e.missing_subtitles IS NOT NULL AND e.missing_subtitles != '[]'
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        SimpleNamespace(
            path=row[0],
            missing_subtitles=row[1],
            sonarrEpisodeId=row[2],
            sonarrSeriesId=row[3],
            audio_language=row[4],
            sceneName=row[5],
            failedAttempts=row[6],
            title=row[7],
            profileId=row[8],
            season=row[9],
            episode=row[10],
            episodeTitle=row[11],
            subtitles=row[12],
        )
        for row in rows
    ]


def load_attempt_samples(db_path, limit):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT missing_subtitles, COALESCE(failedAttempts, '[]')
            FROM table_episodes
            WHERE missing_subtitles IS NOT NULL AND missing_subtitles != '[]'
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    language_attempt_pairs = []
    desired_language_lists = []
    for missing_subtitles, failed_attempts in rows:
        desired_languages = parse_list_text(missing_subtitles)
        desired_language_lists.append((desired_languages, failed_attempts))
        for language in desired_languages:
            language_attempt_pairs.append((language, failed_attempts))

    return {
        "rows": rows,
        "desired_language_lists": desired_language_lists,
        "language_attempt_pairs": language_attempt_pairs,
    }


def dummy_result():
    return SimpleNamespace(message="ok")


class StaticResult:
    def __init__(self, first_value=None, all_value=None):
        self._first_value = first_value
        self._all_value = [] if all_value is None else all_value

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value


class StaticDatabase:
    def __init__(self, first_value=None, all_value=None):
        self.first_value = first_value
        self.all_value = all_value

    def execute(self, statement):
        return StaticResult(first_value=self.first_value, all_value=self.all_value)


def ensure_benchmark_db_schema(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS table_episodes_subtitles (
                id INTEGER PRIMARY KEY,
                language TEXT,
                hi BOOLEAN,
                forced BOOLEAN,
                path TEXT,
                size BIGINT,
                embedded_track_id INTEGER,
                sonarrEpisodeId INTEGER,
                sonarrSeriesId INTEGER
            );
            CREATE TABLE IF NOT EXISTS table_movies_subtitles (
                id INTEGER PRIMARY KEY,
                language TEXT,
                hi BOOLEAN,
                forced BOOLEAN,
                path TEXT,
                size BIGINT,
                embedded_track_id INTEGER,
                radarrId INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_table_episodes_subtitles_episode
                ON table_episodes_subtitles(sonarrEpisodeId);
            CREATE INDEX IF NOT EXISTS idx_table_movies_subtitles_movie
                ON table_movies_subtitles(radarrId);
            """
        )
        conn.commit()


def copy_config_tree(source_config_dir, target_dir):
    source = Path(source_config_dir).resolve()
    target = Path(target_dir).resolve()
    (target / "db").mkdir(parents=True, exist_ok=True)
    (target / "config").mkdir(parents=True, exist_ok=True)

    db_source = source / "db" / "bazarr.db"
    if not db_source.exists():
        raise FileNotFoundError(f"Missing database at {db_source}")
    db_target = target / "db" / "bazarr.db"
    with sqlite3.connect(db_source) as source_db, sqlite3.connect(db_target) as target_db:
        source_db.backup(target_db)
        target_db.execute("PRAGMA journal_mode=DELETE")
        target_db.commit()
    ensure_benchmark_db_schema(db_target)

    config_candidates = (
        source / "config" / "config.yaml",
        source / "config.yaml",
    )
    config_source = next((candidate for candidate in config_candidates if candidate.exists()), None)
    if config_source is None:
        raise FileNotFoundError(f"Missing config.yaml under {source}")
    shutil.copy2(config_source, target / "config.yaml")
    shutil.copy2(config_source, target / "config" / "config.yaml")


@contextmanager
def temporary_config_tree(source_config_dir):
    with tempfile.TemporaryDirectory(prefix="bazarr-bench-") as tmp_dir:
        copy_config_tree(source_config_dir, tmp_dir)
        yield Path(tmp_dir)
