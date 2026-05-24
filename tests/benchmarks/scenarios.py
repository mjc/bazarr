import sqlite3

from contextlib import contextmanager

from .helpers import temporary_config_tree


ALL_WANTED_LANGUAGES = '["en", "fr", "de", "es", "ru"]'
FAKE_MOVIE_ID_START = 1_000_000
FAKE_SHOW_ID_START = 2_000_000
FAKE_EPISODE_ID_START = 3_000_000
BATCH_SIZE = 1000


SCENARIOS = {
    "baseline",
    "all-wanted-5lang",
    "all-wanted-5lang-10x",
    "all-wanted-5lang-100x",
    "skewed-catalog",
}

def _apply_skewed_catalog(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE table_movies SET missing_subtitles='[]', failedAttempts='[]'")
        conn.execute("UPDATE table_episodes SET missing_subtitles='[]', failedAttempts='[]'")

        largest_show = conn.execute(
            """
            SELECT sonarrSeriesId
            FROM table_episodes
            GROUP BY sonarrSeriesId
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """
        ).fetchone()
        if largest_show is not None:
            conn.execute(
                """
                UPDATE table_episodes
                SET missing_subtitles=?, failedAttempts='[]'
                WHERE sonarrSeriesId = ?
                """,
                (ALL_WANTED_LANGUAGES, largest_show[0]),
            )

        conn.execute(
            """
            UPDATE table_movies
            SET missing_subtitles=?, failedAttempts='[]'
            WHERE radarrId IN (
                SELECT radarrId
                FROM table_movies
                ORDER BY radarrId
                LIMIT 250
            )
            """,
            (ALL_WANTED_LANGUAGES,),
        )
        conn.commit()


def _row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def _insert_row(conn, table_name, row_dict):
    columns = list(row_dict.keys())
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, [row_dict[column] for column in columns])


def _insert_rows(conn, table_name, row_dicts):
    row_dicts = list(row_dicts)
    if not row_dicts:
        return
    columns = list(row_dicts[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, [[row[column] for column in columns] for row in row_dicts])


def _fake_movie_row(template, index):
    row = _row_to_dict(template)
    row["radarrId"] = FAKE_MOVIE_ID_START + index
    row["title"] = f"Benchmark Movie {index + 1}"
    row["sortTitle"] = row["title"]
    row["sceneName"] = f"benchmark.movie.{index + 1}"
    row["path"] = f"/benchmark/movies/movie_{index + 1:05d}.mkv"
    row["missing_subtitles"] = ALL_WANTED_LANGUAGES
    row["failedAttempts"] = "[]"
    row["subtitles"] = "[]"
    row["monitored"] = "True"
    if "tmdbId" in row:
        row["tmdbId"] = FAKE_MOVIE_ID_START + index
    if "imdbId" in row:
        row["imdbId"] = f"tt{FAKE_MOVIE_ID_START + index}"
    if "movie_file_id" in row:
        row["movie_file_id"] = FAKE_MOVIE_ID_START + index
    return row


def _fake_show_row(template, index):
    row = _row_to_dict(template)
    row["sonarrSeriesId"] = FAKE_SHOW_ID_START + index
    row["title"] = f"Benchmark Show {index + 1}"
    row["sortTitle"] = row["title"]
    row["path"] = f"/benchmark/shows/show_{index + 1:05d}"
    row["tags"] = "[]"
    row["monitored"] = "True"
    if "tvdbId" in row:
        row["tvdbId"] = FAKE_SHOW_ID_START + index
    if "imdbId" in row:
        row["imdbId"] = f"tt{FAKE_SHOW_ID_START + index}"
    return row


def _fake_episode_row(template, episode_index, show_index, episode_number_in_show):
    row = _row_to_dict(template)
    season = (episode_number_in_show // 10) + 1
    episode = (episode_number_in_show % 10) + 1
    row["sonarrEpisodeId"] = FAKE_EPISODE_ID_START + episode_index
    row["sonarrSeriesId"] = FAKE_SHOW_ID_START + show_index
    row["title"] = f"Episode {episode_number_in_show + 1}"
    row["sceneName"] = f"benchmark.show.{show_index + 1}.s{season:02d}e{episode:02d}"
    row["path"] = (
        f"/benchmark/shows/show_{show_index + 1:05d}/"
        f"Season {season:02d}/S{season:02d}E{episode:02d}.mkv"
    )
    row["season"] = season
    row["episode"] = episode
    row["missing_subtitles"] = ALL_WANTED_LANGUAGES
    row["failedAttempts"] = "[]"
    row["subtitles"] = "[]"
    row["monitored"] = "True"
    if "episode_file_id" in row:
        row["episode_file_id"] = FAKE_EPISODE_ID_START + episode_index
    return row


def _apply_fake_all_wanted_multilang(db_path, scale=1):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")

        movie_count = conn.execute("SELECT COUNT(*) FROM table_movies").fetchone()[0] * scale
        show_count = conn.execute("SELECT COUNT(*) FROM table_shows").fetchone()[0] * scale
        episode_count = conn.execute("SELECT COUNT(*) FROM table_episodes").fetchone()[0] * scale

        movie_template = conn.execute("SELECT * FROM table_movies LIMIT 1").fetchone()
        show_template = conn.execute("SELECT * FROM table_shows LIMIT 1").fetchone()
        episode_template = conn.execute("SELECT * FROM table_episodes LIMIT 1").fetchone()
        if movie_template is None or show_template is None or episode_template is None:
            raise RuntimeError("Cannot build fake benchmark catalog without source movie/show/episode rows")

        conn.execute("DELETE FROM table_episodes")
        conn.execute("DELETE FROM table_shows")
        conn.execute("DELETE FROM table_movies")

        movie_batch = []
        for index in range(movie_count):
            movie_batch.append(_fake_movie_row(movie_template, index))
            if len(movie_batch) >= BATCH_SIZE:
                _insert_rows(conn, "table_movies", movie_batch)
                movie_batch.clear()
        _insert_rows(conn, "table_movies", movie_batch)

        show_batch = []
        for index in range(show_count):
            show_batch.append(_fake_show_row(show_template, index))
            if len(show_batch) >= BATCH_SIZE:
                _insert_rows(conn, "table_shows", show_batch)
                show_batch.clear()
        _insert_rows(conn, "table_shows", show_batch)

        if show_count == 0:
            conn.commit()
            return

        episodes_per_show = [episode_count // show_count] * show_count
        for index in range(episode_count % show_count):
            episodes_per_show[index] += 1

        episode_index = 0
        episode_batch = []
        for show_index, count in enumerate(episodes_per_show):
            for episode_number_in_show in range(count):
                episode_batch.append(
                    _fake_episode_row(episode_template, episode_index, show_index, episode_number_in_show)
                )
                if len(episode_batch) >= BATCH_SIZE:
                    _insert_rows(conn, "table_episodes", episode_batch)
                    episode_batch.clear()
                episode_index += 1
        _insert_rows(conn, "table_episodes", episode_batch)

        conn.commit()


@contextmanager
def prepared_config_dir(source_config_dir, scenario):
    if scenario not in SCENARIOS:
        raise ValueError(f"Unsupported scenario: {scenario}")

    with temporary_config_tree(source_config_dir) as config_dir:
        db_path = config_dir / "db" / "bazarr.db"
        if scenario == "all-wanted-5lang":
            _apply_fake_all_wanted_multilang(db_path)
        elif scenario == "all-wanted-5lang-10x":
            _apply_fake_all_wanted_multilang(db_path, scale=10)
        elif scenario == "all-wanted-5lang-100x":
            _apply_fake_all_wanted_multilang(db_path, scale=100)
        elif scenario == "skewed-catalog":
            _apply_skewed_catalog(db_path)
        yield config_dir
