import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings(adaptive_searching):
    return SimpleNamespace(
        general=SimpleNamespace(
            adaptive_searching=adaptive_searching,
            adaptive_searching_delay="3w",
            adaptive_searching_delta="1w",
        ),
        postgresql=SimpleNamespace(enabled=False),
    )


def _load_wanted_sql(adaptive_searching):
    previous_app_module = sys.modules.get('app')
    previous_config_module = sys.modules.get('app.config')
    app_module = types.ModuleType('app')
    config_module = types.ModuleType('app.config')
    config_module.settings = _settings(adaptive_searching)
    sys.modules['app'] = app_module
    sys.modules['app.config'] = config_module

    try:
        spec = importlib.util.spec_from_file_location(
            'wanted_sql_test_module',
            _REPO_ROOT / 'bazarr/app/wanted_sql.py',
        )
        wanted_sql = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wanted_sql)
        return wanted_sql
    finally:
        if previous_app_module is None:
            sys.modules.pop('app', None)
        else:
            sys.modules['app'] = previous_app_module

        if previous_config_module is None:
            sys.modules.pop('app.config', None)
        else:
            sys.modules['app.config'] = previous_config_module


def _wanted_rows(connection, media):
    wanted_sql = _load_wanted_sql(adaptive_searching=True)

    return connection.execute(
        sa.select(media.c.id)
        .where(wanted_sql.has_due_missing_subtitle(media.c.missing_subtitles, media.c.failedAttempts))
        .order_by(media.c.id)
    ).scalars().all()


def test_python_repr_array_to_json_converts_missing_subtitle_text():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)

    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        assert connection.execute(
            sa.select(wanted_sql.python_repr_array_to_json(sa.literal("['en', None, 'fr:hi']")))
        ).scalar_one() == '["en", null, "fr:hi"]'
        assert connection.execute(
            sa.select(wanted_sql.python_repr_array_to_json(sa.literal("not-json")))
        ).scalar_one() == '[]'


def test_wanted_subtitle_sql_finds_real_missing_languages():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            media.insert(),
            [
                {"id": 1, "missing_subtitles": "['en']"},
                {"id": 2, "missing_subtitles": '["fr:hi"]'},
                {"id": 3, "missing_subtitles": "[]"},
                {"id": 4, "missing_subtitles": None},
                {"id": 5, "missing_subtitles": "[None]"},
                {"id": 6, "missing_subtitles": "not-json"},
            ],
        )

        assert connection.execute(
            sa.select(media.c.id)
            .where(wanted_sql.has_wanted_subtitle(media.c.missing_subtitles))
            .order_by(media.c.id)
        ).scalars().all() == [1, 2]


def test_due_missing_subtitle_sql_finds_non_empty_missing_lists():
    wanted_sql = _load_wanted_sql(adaptive_searching=False)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            media.insert(),
            [
                {"id": 1, "missing_subtitles": "['en']", "failedAttempts": "[]"},
                {"id": 2, "missing_subtitles": "['fr:forced']", "failedAttempts": None},
                {"id": 3, "missing_subtitles": "[]", "failedAttempts": "[]"},
                {"id": 4, "missing_subtitles": None, "failedAttempts": "[]"},
                {"id": 5, "missing_subtitles": "[None]", "failedAttempts": "[]"},
                {"id": 6, "missing_subtitles": "not-json", "failedAttempts": "[]"},
            ],
        )

        assert connection.execute(
            sa.select(media.c.id)
            .where(wanted_sql.has_due_missing_subtitle(media.c.missing_subtitles, media.c.failedAttempts))
            .order_by(media.c.id)
        ).scalars().all() == [1, 2]


def test_due_missing_subtitle_sql_applies_adaptive_search():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)
    now = time.time()
    old = now - 4 * 7 * 24 * 60 * 60
    recent = now - 60 * 60
    stale = now - 8 * 24 * 60 * 60

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            media.insert(),
            [
                {"id": 1, "missing_subtitles": "['en']", "failedAttempts": "[]"},
                {
                    "id": 2,
                    "missing_subtitles": "['fr']",
                    "failedAttempts": f"[['fr', {old}], ['fr', {recent}]]",
                },
                {
                    "id": 3,
                    "missing_subtitles": "['de']",
                    "failedAttempts": f"[['de', {old}], ['de', {stale}]]",
                },
                {
                    "id": 4,
                    "missing_subtitles": "['es', 'pt']",
                    "failedAttempts": f"[['es', {old}], ['es', {recent}]]",
                },
                {"id": 5, "missing_subtitles": "[]", "failedAttempts": "[]"},
                {"id": 6, "missing_subtitles": "['it']", "failedAttempts": "not-json"},
                {"id": 7, "missing_subtitles": "['nl']", "failedAttempts": "[['nl', None]]"},
                {"id": 8, "missing_subtitles": "['sv']", "failedAttempts": "[['sv']]"},
            ],
        )

        assert _wanted_rows(connection, media) == [1, 3, 4, 6, 7, 8]


def test_due_missing_subtitle_sql_ignores_attempts_when_adaptive_search_is_disabled():
    wanted_sql = _load_wanted_sql(adaptive_searching=False)
    now = time.time()

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            media.insert(),
            [
                {
                    "id": 1,
                    "missing_subtitles": "['en']",
                    "failedAttempts": f"[['en', {now}]]",
                },
                {"id": 2, "missing_subtitles": "[]", "failedAttempts": "[]"},
            ],
        )

        assert connection.execute(
            sa.select(media.c.id)
            .where(wanted_sql.has_due_missing_subtitle(media.c.missing_subtitles, media.c.failedAttempts))
        ).scalars().all() == [1]


def test_due_missing_subtitle_media_ids_uses_set_based_attempt_windows():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)
    now = time.time()
    old = now - 4 * 7 * 24 * 60 * 60
    recent = now - 60 * 60
    stale = now - 8 * 24 * 60 * 60

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            media.insert(),
            [
                {"id": 1, "missing_subtitles": "['en']", "failedAttempts": "[]"},
                {
                    "id": 2,
                    "missing_subtitles": "['fr']",
                    "failedAttempts": f"[['fr', {old}], ['fr', {recent}]]",
                },
                {
                    "id": 3,
                    "missing_subtitles": "['de']",
                    "failedAttempts": f"[['de', {old}], ['de', {stale}]]",
                },
            ],
        )

        candidate_select = sa.select(
            media.c.id.label("media_id"),
            media.c.missing_subtitles,
            media.c.failedAttempts.label("failed_attempts"),
        ).where(media.c.missing_subtitles != "[]")
        assert connection.execute(
            wanted_sql.due_missing_subtitle_media_ids(candidate_select)
        ).scalars().all() == [1, 3]


def test_prepare_due_missing_subtitle_language_table_materializes_indexed_temp_rows():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)
    now = time.time()
    old = now - 4 * 7 * 24 * 60 * 60
    recent = now - 60 * 60
    stale = now - 8 * 24 * 60 * 60

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            media.insert(),
            [
                {"id": 1, "missing_subtitles": "['en']", "failedAttempts": "[]"},
                {
                    "id": 2,
                    "missing_subtitles": "['fr']",
                    "failedAttempts": f"[['fr', {old}], ['fr', {recent}]]",
                },
                {
                    "id": 3,
                    "missing_subtitles": "['de', 'es']",
                    "failedAttempts": f"[['de', {old}], ['de', {stale}]]",
                },
            ],
        )

        candidate_select = sa.select(
            media.c.id.label("media_id"),
            media.c.missing_subtitles,
            media.c.failedAttempts.label("failed_attempts"),
        ).where(media.c.missing_subtitles != "[]")
        temp_table = wanted_sql.prepare_due_missing_subtitle_language_table(
            connection,
            candidate_select,
            "temp_test_due_languages",
        )

        assert connection.execute(
            sa.select(temp_table.c.media_id, temp_table.c.language)
            .order_by(temp_table.c.media_id, temp_table.c.language)
        ).all() == [(1, "en"), (3, "de"), (3, "es")]
        assert connection.execute(
            sa.text("SELECT name FROM sqlite_temp_master WHERE type = 'index' AND name = :name"),
            {"name": "ix_temp_test_due_languages_media_id"},
        ).scalar_one() == "ix_temp_test_due_languages_media_id"


def test_prepare_due_missing_subtitle_language_table_rejects_unsafe_names():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        candidate_select = sa.select(
            sa.literal(1).label("media_id"),
            sa.literal("['en']").label("missing_subtitles"),
            sa.literal("[]").label("failed_attempts"),
        )

        try:
            wanted_sql.prepare_due_missing_subtitle_language_table(
                connection,
                candidate_select,
                "temp_due_languages; DROP TABLE media",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe SQL identifier should have been rejected")


def test_wanted_predicates_use_safe_fallback_when_postgresql_is_enabled():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)
    previous_postgres_env = os.environ.get("POSTGRES_ENABLED")
    os.environ["POSTGRES_ENABLED"] = "true"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    media = sa.Table(
        "media",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("missing_subtitles", sa.Text),
        sa.Column("failedAttempts", sa.Text),
    )
    metadata.create_all(engine)

    try:
        with engine.begin() as connection:
            connection.execute(
                media.insert(),
                [
                    {"id": 1, "missing_subtitles": "['en']", "failedAttempts": "[['en', 1]]"},
                    {"id": 2, "missing_subtitles": "[]", "failedAttempts": "[]"},
                    {"id": 3, "missing_subtitles": "not-json", "failedAttempts": "[]"},
                    {"id": 4, "missing_subtitles": "[None]", "failedAttempts": "[]"},
                ],
            )

            assert connection.execute(
                sa.select(media.c.id)
                .where(wanted_sql.has_wanted_subtitle(media.c.missing_subtitles))
                .order_by(media.c.id)
            ).scalars().all() == [1]
            assert connection.execute(
                sa.select(media.c.id)
                .where(wanted_sql.has_due_missing_subtitle(media.c.missing_subtitles, media.c.failedAttempts))
                .order_by(media.c.id)
            ).scalars().all() == [1]

            try:
                wanted_sql.prepare_due_missing_subtitle_language_table(
                    connection,
                    sa.select(
                        media.c.id.label("media_id"),
                        media.c.missing_subtitles,
                        media.c.failedAttempts.label("failed_attempts"),
                    ),
                    "temp_due_languages",
                )
            except NotImplementedError:
                pass
            else:
                raise AssertionError("PostgreSQL JSON temp-table path should have been rejected")
    finally:
        if previous_postgres_env is None:
            os.environ.pop("POSTGRES_ENABLED", None)
        else:
            os.environ["POSTGRES_ENABLED"] = previous_postgres_env


def test_materialized_cte_is_omitted_for_older_sqlite():
    wanted_sql = _load_wanted_sql(adaptive_searching=True)
    previous_version_info = wanted_sql.sqlite3.sqlite_version_info
    wanted_sql.sqlite3.sqlite_version_info = (3, 34, 0)

    try:
        statement = wanted_sql._materialized_cte(sa.select(sa.literal(1).label("value")), "old_sqlite_cte")
        compiled = str(statement.select().compile(compile_kwargs={"literal_binds": True}))
        assert "MATERIALIZED" not in compiled
    finally:
        wanted_sql.sqlite3.sqlite_version_info = previous_version_info
