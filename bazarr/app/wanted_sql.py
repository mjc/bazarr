# coding=utf-8

import os
import sqlite3

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text, and_, case, cast, func, insert, literal, or_, select, text, true

from app.config import settings


_VALID_IDENTIFIER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _quote_identifier(identifier):
    if (
        not identifier or
        identifier[0].isdigit() or
        not all(char in _VALID_IDENTIFIER_CHARS for char in identifier)
    ):
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")

    return f'"{identifier}"'


def _duration_to_seconds(value):
    try:
        amount = int(value[:-1])
    except (TypeError, ValueError):
        return None

    if value.endswith('d'):
        return amount * 24 * 60 * 60
    if value.endswith('w'):
        return amount * 7 * 24 * 60 * 60

    return None


def supports_sqlite_wanted_search():
    postgres_enabled = os.getenv("POSTGRES_ENABLED")
    if postgres_enabled is not None:
        return postgres_enabled.lower() != 'true'

    return not settings.postgresql.enabled


def _serialized_list_has_values(column):
    return and_(
        column.is_not(None),
        column.like('[%]'),
        or_(column.like("%'%"), column.like('%"%')),
        column != '[]',
        column != '[None]',
        column != '[null]',
    )


def python_repr_array_to_json(column):
    normalized = func.replace(func.coalesce(column, '[]'), "'", '"')
    normalized = func.replace(normalized, 'None', 'null')
    return case(
        (func.json_valid(normalized), normalized),
        else_='[]',
    )


def has_wanted_subtitle(missing_subtitles_column):
    if not supports_sqlite_wanted_search():
        return _serialized_list_has_values(missing_subtitles_column)

    missing = (
        func.json_each(python_repr_array_to_json(missing_subtitles_column))
        .table_valued("value")
        .alias("missing")
    )

    return (
        select(literal(1))
        .select_from(missing)
        .where(missing.c.value.is_not(None))
        .exists()
    )


def _attempts_for_language(attempts_json, name):
    attempts = func.json_each(attempts_json).table_valued("value").alias(name)
    attempt_language = func.json_extract(attempts.c.value, '$[0]')
    attempt_timestamp = cast(func.json_extract(attempts.c.value, '$[1]'), Float)
    return attempts, attempt_language, attempt_timestamp


def _materialized_cte(statement, name):
    cte = statement.cte(name)
    if sqlite3.sqlite_version_info >= (3, 35, 0):
        return cte.prefix_with("MATERIALIZED")

    return cte


def due_missing_subtitle_languages(candidate_select):
    if not supports_sqlite_wanted_search():
        raise NotImplementedError("SQL wanted search JSON expansion is only supported by SQLite")

    candidate = _materialized_cte(candidate_select, "candidate_media")
    missing_json = python_repr_array_to_json(candidate.c.missing_subtitles)
    attempts_json = python_repr_array_to_json(candidate.c.failed_attempts)

    missing_values = (
        func.json_each(missing_json)
        .table_valued("value")
        .alias("missing_values")
    )
    missing = _materialized_cte(
        select(
            candidate.c.media_id,
            missing_values.c.value.label("language"),
        )
        .select_from(candidate)
        .join(missing_values, true())
        .where(missing_values.c.value.is_not(None)),
        "missing_languages",
    )

    attempt_values = (
        func.json_each(attempts_json)
        .table_valued("value")
        .alias("attempt_values")
    )
    attempt_language = func.json_extract(attempt_values.c.value, '$[0]')
    attempt_timestamp = cast(func.json_extract(attempt_values.c.value, '$[1]'), Float)
    attempts = _materialized_cte(
        select(
            candidate.c.media_id,
            attempt_language.label("language"),
            func.min(attempt_timestamp).label("initial_attempt_at"),
            func.max(attempt_timestamp).label("latest_attempt_at"),
        )
        .select_from(candidate)
        .join(attempt_values, true())
        .where(and_(attempt_language.is_not(None), attempt_timestamp.is_not(None)))
        .group_by(candidate.c.media_id, attempt_language),
        "attempt_windows",
    )

    if not settings.general.adaptive_searching:
        return select(missing.c.media_id, missing.c.language)

    delay_seconds = _duration_to_seconds(settings.general.adaptive_searching_delay)
    delta_seconds = _duration_to_seconds(settings.general.adaptive_searching_delta)
    if delay_seconds is None or delta_seconds is None:
        return select(missing.c.media_id, missing.c.language)

    now_timestamp = cast(func.strftime('%s', 'now'), Float)
    return (
        select(missing.c.media_id, missing.c.language)
        .select_from(
            missing.outerjoin(
                attempts,
                and_(
                    attempts.c.media_id == missing.c.media_id,
                    attempts.c.language == missing.c.language,
                ),
            )
        )
        .where(or_(
            attempts.c.language.is_(None),
            attempts.c.initial_attempt_at + delay_seconds > now_timestamp,
            attempts.c.latest_attempt_at + delta_seconds <= now_timestamp,
        ))
    )


def due_missing_subtitle_media_ids(candidate_select):
    due_languages = due_missing_subtitle_languages(candidate_select).subquery()
    return select(due_languages.c.media_id).distinct()


def prepare_due_missing_subtitle_language_table(database, candidate_select, table_name):
    if not supports_sqlite_wanted_search():
        raise NotImplementedError("SQL wanted search temp tables are only supported by SQLite")

    quoted_table_name = _quote_identifier(table_name)
    index_name = f"ix_{table_name}_media_id"
    quoted_index_name = _quote_identifier(index_name)

    database.execute(text(f"DROP TABLE IF EXISTS {quoted_table_name}"))
    database.execute(
        text(f"CREATE TEMPORARY TABLE {quoted_table_name} (media_id INTEGER NOT NULL, language TEXT NOT NULL)")
    )

    due_languages = due_missing_subtitle_languages(candidate_select)
    temp_table = Table(
        table_name,
        MetaData(),
        Column("media_id", Integer, nullable=False),
        Column("language", Text, nullable=False),
        prefixes=["TEMPORARY"],
    )
    database.execute(
        insert(temp_table)
        .from_select(
            ["media_id", "language"],
            due_languages,
        )
    )
    database.execute(
        text(f"CREATE INDEX IF NOT EXISTS {quoted_index_name} ON {quoted_table_name} (media_id)")
    )
    return temp_table


def has_due_missing_subtitle(missing_subtitles_column, failed_attempts_column):
    if not settings.general.adaptive_searching or not supports_sqlite_wanted_search():
        return has_wanted_subtitle(missing_subtitles_column)

    missing = (
        func.json_each(python_repr_array_to_json(missing_subtitles_column))
        .table_valued("value")
        .alias("missing")
    )
    attempts_json = python_repr_array_to_json(failed_attempts_column)

    matching_attempts, matching_language, matching_timestamp = _attempts_for_language(
        attempts_json,
        "matching_attempts",
    )
    due_conditions = [
        ~select(literal(1))
        .select_from(matching_attempts)
        .where(and_(matching_language == missing.c.value, matching_timestamp.is_not(None)))
        .exists(),
    ]

    now_timestamp = cast(func.strftime('%s', 'now'), Float)
    delay_seconds = _duration_to_seconds(settings.general.adaptive_searching_delay)
    delta_seconds = _duration_to_seconds(settings.general.adaptive_searching_delta)

    if delay_seconds is None or delta_seconds is None:
        return has_wanted_subtitle(missing_subtitles_column)

    initial_attempts, initial_language, initial_timestamp = _attempts_for_language(
        attempts_json,
        "initial_attempts",
    )
    latest_attempts, latest_language, latest_timestamp = _attempts_for_language(
        attempts_json,
        "latest_attempts",
    )
    initial_attempt = (
        select(func.min(initial_timestamp))
        .select_from(initial_attempts)
        .where(and_(initial_language == missing.c.value, initial_timestamp.is_not(None)))
        .scalar_subquery()
    )
    latest_attempt = (
        select(func.max(latest_timestamp))
        .select_from(latest_attempts)
        .where(and_(latest_language == missing.c.value, latest_timestamp.is_not(None)))
        .scalar_subquery()
    )
    due_conditions.extend([
        initial_attempt + delay_seconds > now_timestamp,
        latest_attempt + delta_seconds <= now_timestamp,
    ])

    return (
        select(literal(1))
        .select_from(missing)
        .where(and_(missing.c.value.is_not(None), or_(*due_conditions)))
        .exists()
    )
