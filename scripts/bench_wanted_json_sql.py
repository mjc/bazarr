#!/usr/bin/env python3
"""Benchmark JSON wanted-search query shapes against a SQLite Bazarr DB.

This compares the original single CTE/detail query with the temp-table shape:
expand due languages once, index the temporary rows, then fetch details in
bounded ID chunks.
"""

import argparse
import sqlite3
import statistics
import time


def _duration_to_seconds(value):
    amount = int(value[:-1])
    if value.endswith("d"):
        return amount * 24 * 60 * 60
    if value.endswith("w"):
        return amount * 7 * 24 * 60 * 60
    raise ValueError(f"Unsupported duration: {value}")


def _python_repr_array_to_json_sql(column):
    normalized = f"replace(replace(coalesce({column}, '[]'), '''', '\"'), 'None', 'null')"
    return f"CASE WHEN json_valid({normalized}) THEN {normalized} ELSE '[]' END"


def _due_cte_sql(media, due_name="due_languages"):
    id_column = "radarrId" if media == "movies" else "sonarrEpisodeId"
    table = "table_movies" if media == "movies" else "table_episodes"
    source = f"{table} AS media"
    if media == "series":
        source += " JOIN table_shows ON table_shows.sonarrSeriesId = media.sonarrSeriesId"
    missing_json = _python_repr_array_to_json_sql("candidate_media.missing_subtitles")
    attempts_json = _python_repr_array_to_json_sql("candidate_media.failed_attempts")

    return f"""
WITH
candidate_media AS MATERIALIZED (
    SELECT
        media.{id_column} AS media_id,
        media.missing_subtitles AS missing_subtitles,
        media.failedAttempts AS failed_attempts
    FROM {source}
    WHERE media.missing_subtitles IS NOT NULL
      AND media.missing_subtitles != '[]'
),
missing_languages AS MATERIALIZED (
    SELECT
        candidate_media.media_id AS media_id,
        missing_values.value AS language
    FROM candidate_media
    JOIN json_each({missing_json}) AS missing_values
    WHERE missing_values.value IS NOT NULL
),
attempt_windows AS MATERIALIZED (
    SELECT
        candidate_media.media_id AS media_id,
        json_extract(attempt_values.value, '$[0]') AS language,
        min(CAST(json_extract(attempt_values.value, '$[1]') AS FLOAT)) AS initial_attempt_at,
        max(CAST(json_extract(attempt_values.value, '$[1]') AS FLOAT)) AS latest_attempt_at
    FROM candidate_media
    JOIN json_each({attempts_json}) AS attempt_values
    WHERE json_extract(attempt_values.value, '$[0]') IS NOT NULL
      AND CAST(json_extract(attempt_values.value, '$[1]') AS FLOAT) IS NOT NULL
    GROUP BY candidate_media.media_id, json_extract(attempt_values.value, '$[0]')
),
{due_name} AS (
    SELECT missing_languages.media_id, missing_languages.language
    FROM missing_languages
    LEFT JOIN attempt_windows
      ON attempt_windows.media_id = missing_languages.media_id
     AND attempt_windows.language = missing_languages.language
    WHERE attempt_windows.language IS NULL
       OR attempt_windows.initial_attempt_at + :delay_seconds > CAST(strftime('%s', 'now') AS FLOAT)
       OR attempt_windows.latest_attempt_at + :delta_seconds <= CAST(strftime('%s', 'now') AS FLOAT)
)
"""


def _detail_sql(media, id_filter_sql=""):
    if media == "movies":
        return f"""
SELECT
    media.radarrId,
    media.path,
    media.missing_subtitles,
    media.audio_language,
    media.sceneName,
    media.failedAttempts,
    media.title,
    media.profileId,
    EXISTS (
        SELECT 1 FROM table_movies_subtitles
        WHERE table_movies_subtitles.radarrId = media.radarrId
        LIMIT 1
    ) AS has_indexed_subtitles,
    EXISTS (
        SELECT 1 FROM table_movies_subtitles
        WHERE table_movies_subtitles.radarrId = media.radarrId
          AND table_movies_subtitles.path IS NULL
          AND table_movies_subtitles.embedded_track_id IS NULL
        LIMIT 1
    ) AS has_incomplete_embedded_subtitles,
    due_languages.language
FROM table_movies AS media
JOIN due_languages ON due_languages.media_id = media.radarrId
{id_filter_sql}
"""

    return f"""
SELECT
    media.sonarrSeriesId,
    media.sonarrEpisodeId,
    table_shows.title,
    media.season,
    media.episode,
    media.title AS episodeTitle,
    table_shows.seriesType,
    media.path,
    media.missing_subtitles,
    media.audio_language,
    media.sceneName,
    media.failedAttempts,
    table_shows.profileId,
    EXISTS (
        SELECT 1 FROM table_episodes_subtitles
        WHERE table_episodes_subtitles.sonarrEpisodeId = media.sonarrEpisodeId
        LIMIT 1
    ) AS has_indexed_subtitles,
    EXISTS (
        SELECT 1 FROM table_episodes_subtitles
        WHERE table_episodes_subtitles.sonarrEpisodeId = media.sonarrEpisodeId
          AND table_episodes_subtitles.path IS NULL
          AND table_episodes_subtitles.embedded_track_id IS NULL
        LIMIT 1
    ) AS has_incomplete_embedded_subtitles,
    due_languages.language
FROM table_episodes AS media
JOIN table_shows ON table_shows.sonarrSeriesId = media.sonarrSeriesId
JOIN due_languages ON due_languages.media_id = media.sonarrEpisodeId
{id_filter_sql}
"""


def bench_single_cte(connection, media, params):
    rows = connection.execute(_due_cte_sql(media) + _detail_sql(media), params).fetchall()
    return len(rows), len({row[0] if media == "movies" else row[1] for row in rows})


def bench_temp_table(connection, media, params, chunk_size):
    connection.execute("DROP TABLE IF EXISTS due_languages")
    connection.execute("CREATE TEMPORARY TABLE due_languages (media_id INTEGER NOT NULL, language TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO due_languages (media_id, language) "
        + _due_cte_sql(media, due_name="due_language_rows")
        + "SELECT media_id, language FROM due_language_rows",
        params,
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_due_languages_media_id ON due_languages (media_id)")

    due_ids = [row[0] for row in connection.execute("SELECT DISTINCT media_id FROM due_languages ORDER BY media_id")]
    row_count = 0
    for index in range(0, len(due_ids), chunk_size):
        chunk = due_ids[index:index + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        id_column = "media.radarrId" if media == "movies" else "media.sonarrEpisodeId"
        rows = connection.execute(
            _detail_sql(media, f"WHERE {id_column} IN ({placeholders})"),
            chunk,
        ).fetchall()
        row_count += len(rows)
    return row_count, len(due_ids)


def time_run(fn):
    start = time.perf_counter()
    row_count, media_count = fn()
    return (time.perf_counter() - start) * 1000, row_count, media_count


def summarize(samples):
    timings = [sample[0] for sample in samples]
    return statistics.median(timings), min(timings), max(timings), samples[-1][1], samples[-1][2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--chunk-size", type=int, default=5000)
    parser.add_argument("--delay", default="3w")
    parser.add_argument("--delta", default="1w")
    args = parser.parse_args()

    if args.runs <= 0:
        parser.error("--runs must be a positive integer")
    if args.warmups < 0:
        parser.error("--warmups cannot be negative")
    if args.chunk_size <= 0:
        parser.error("--chunk-size must be a positive integer")

    params = {
        "delay_seconds": _duration_to_seconds(args.delay),
        "delta_seconds": _duration_to_seconds(args.delta),
    }
    connection = sqlite3.connect(args.db)
    try:
        for media in ("series", "movies"):
            print(f"\n{media}")
            for label, fn in (
                ("single_cte", lambda media=media: bench_single_cte(connection, media, params)),
                ("temp_table", lambda media=media: bench_temp_table(connection, media, params, args.chunk_size)),
            ):
                for _ in range(args.warmups):
                    fn()
                samples = [time_run(fn) for _ in range(args.runs)]
                median_ms, min_ms, max_ms, rows, unique = summarize(samples)
                print(
                    f"{label}: median={median_ms:.3f}ms min={min_ms:.3f}ms max={max_ms:.3f}ms "
                    f"rows={rows} unique_media={unique}"
                )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
