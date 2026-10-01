# coding=utf-8

from datetime import datetime
from contextlib import contextmanager

from sqlalchemy import case, func, or_
from sqlalchemy.engine import Connection

from app.database import (
    TableEpisodes,
    TableFailedSubtitleAttempts,
    TableMissingSubtitleScans,
    TableMissingSubtitles,
    TableMovies,
    database,
    delete,
    insert,
    select,
)
from subtitles.adaptive_searching import get_adaptive_search_policy, get_active_search_languages

WANTED_STATE_QUERY_BATCH_SIZE = 5000


def _iter_chunks(items, batch_size=None):
    if batch_size is None:
        batch_size = WANTED_STATE_QUERY_BATCH_SIZE
    for index in range(0, len(items), batch_size):
        yield items[index:index + batch_size]


def _normalize_media_ids(media_ids, coerce_int=False):
    if isinstance(media_ids, (int, str)):
        media_ids = [media_ids]
    else:
        media_ids = list(dict.fromkeys(media_ids))

    if coerce_int:
        return list(dict.fromkeys(int(media_id) for media_id in media_ids))
    return media_ids


def get_missing_subtitle_rows(media_type, media_id, missing_subtitles):
    rows = []
    seen_languages = set()
    for language in missing_subtitles or []:
        if not isinstance(language, str):
            continue
        if language in seen_languages:
            continue
        seen_languages.add(language)
        rows.append({
            "media_type": media_type,
            "media_id": media_id,
            "language": language,
        })

    return rows


def record_failed_subtitle_attempts(media_type, media_id, languages):
    if isinstance(languages, str):
        languages = [languages]
    else:
        languages = list(dict.fromkeys(languages))

    if not languages:
        return

    record_failed_subtitle_attempts_map(media_type, {media_id: languages})


def record_failed_subtitle_attempts_map(media_type, languages_by_media_id):
    languages_by_media_id = {
        media_id: list(dict.fromkeys(languages))
        for media_id, languages in languages_by_media_id.items()
        if languages
    }
    if not languages_by_media_id:
        return {}

    media_ids = list(languages_by_media_id)
    media_table, media_id_column = {
        'movie': (TableMovies.__table__, 'radarrId'),
        'series': (TableEpisodes.__table__, 'sonarrEpisodeId'),
    }[media_type]
    for media_id_chunk in _iter_chunks(media_ids):
        with _wanted_state_transaction() as connection:
            media_ids_to_update = connection.execute(
                select(media_table.c[media_id_column])
                .where(media_table.c[media_id_column].in_(media_id_chunk))
                .order_by(media_table.c[media_id_column])
                .with_for_update()
            ).scalars().all()
            if not media_ids_to_update:
                continue

            current_timestamp = datetime.timestamp(datetime.now())
            media_ids_to_update = set(media_ids_to_update)
            existing_attempts = {media_id: {} for media_id in media_ids_to_update}
            for row in connection.execute(
                select(
                    TableFailedSubtitleAttempts.media_id,
                    TableFailedSubtitleAttempts.language,
                    TableFailedSubtitleAttempts.initial_attempt_at,
                    TableFailedSubtitleAttempts.latest_attempt_at,
                )
                .where(TableFailedSubtitleAttempts.media_type == media_type)
                .where(TableFailedSubtitleAttempts.media_id.in_(media_ids_to_update))
            ):
                existing_attempts[row.media_id][row.language] = row

            rows = []
            for media_id in media_ids_to_update:
                for language in languages_by_media_id[media_id]:
                    existing_attempt = existing_attempts[media_id].get(language)
                    rows.append({
                        "media_type": media_type,
                        "media_id": media_id,
                        "language": language,
                        "initial_attempt_at": (
                            existing_attempt.initial_attempt_at if existing_attempt else current_timestamp
                        ),
                        "latest_attempt_at": current_timestamp,
                    })

            latest_timestamp = TableFailedSubtitleAttempts.latest_attempt_at
            for row_chunk in _iter_chunks(rows):
                statement = insert(TableFailedSubtitleAttempts).values(row_chunk)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["media_type", "media_id", "language"],
                        set_={
                            "latest_attempt_at": case(
                                (latest_timestamp < current_timestamp, current_timestamp),
                                else_=latest_timestamp,
                            ),
                        },
                    )
                )

    return None


def refresh_wanted_search_state(media_type, media_id, missing_subtitles, failed_attempts=None,
                                refresh_failed_attempts=True):
    database.execute(
        delete(TableMissingSubtitles)
        .where(TableMissingSubtitles.media_type == media_type)
        .where(TableMissingSubtitles.media_id == media_id)
    )

    rows = get_missing_subtitle_rows(
        media_type,
        media_id,
        missing_subtitles,
    )
    if rows:
        database.execute(insert(TableMissingSubtitles), rows)
    database.execute(
        insert(TableMissingSubtitleScans)
        .values(media_type=media_type, media_id=media_id)
        .on_conflict_do_nothing(index_elements=['media_type', 'media_id'])
    )
    if refresh_failed_attempts:
        database.execute(
            delete(TableFailedSubtitleAttempts)
            .where(TableFailedSubtitleAttempts.media_type == media_type)
            .where(TableFailedSubtitleAttempts.media_id == media_id)
        )
        attempts_by_language = {}
        for language, timestamp in failed_attempts or []:
            initial, latest = attempts_by_language.get(language, (timestamp, timestamp))
            attempts_by_language[language] = min(initial, timestamp), max(latest, timestamp)
        rows = [
            {
                "media_type": media_type,
                "media_id": media_id,
                "language": language,
                "initial_attempt_at": initial,
                "latest_attempt_at": latest,
            }
            for language, (initial, latest) in attempts_by_language.items()
        ]
        if rows:
            database.execute(insert(TableFailedSubtitleAttempts), rows)


@contextmanager
def _wanted_state_transaction():
    bind = database.get_bind()
    if isinstance(bind, Connection):
        # Respect an existing transaction (for example a caller's unit of work).
        with bind.begin_nested():
            yield bind
        return

    # Runtime sessions use AUTOCOMMIT. Give these related writes their own real
    # transaction, without changing the isolation of the caller's connection.
    with bind.connect().execution_options(isolation_level=bind.dialect.default_isolation_level) as connection:
        with connection.begin():
            if connection.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            yield connection


def store_missing_subtitles(table, id_column_name, media_type, media_id, missing_subtitles):
    """Save normalized missing-language rows and mark the media as scanned."""
    id_column = table.c[id_column_name]
    rows = get_missing_subtitle_rows(media_type, media_id, missing_subtitles)
    with _wanted_state_transaction() as connection:
        media = connection.execute(
            select(id_column).where(id_column == media_id).with_for_update()
        ).first()
        if media is None:
            return

        missing_filter = (
            (TableMissingSubtitles.media_type == media_type) &
            (TableMissingSubtitles.media_id == media_id)
        )
        current_languages = connection.execute(
            select(TableMissingSubtitles.language).where(missing_filter).order_by(TableMissingSubtitles.id)
        ).scalars().all()
        if current_languages != [row['language'] for row in rows]:
            connection.execute(delete(TableMissingSubtitles).where(missing_filter))
            if rows:
                connection.execute(insert(TableMissingSubtitles), rows)

        scan = connection.execute(
            select(TableMissingSubtitleScans.media_id)
            .where(TableMissingSubtitleScans.media_type == media_type)
            .where(TableMissingSubtitleScans.media_id == media_id)
        ).first()
        if scan is None:
            connection.execute(
                insert(TableMissingSubtitleScans)
                .values(media_type=media_type, media_id=media_id)
            )


def get_missing_languages(media_type, media_id):
    languages = [
        row.language
        for row in database.execute(
            select(TableMissingSubtitles.language)
            .where(TableMissingSubtitles.media_type == media_type)
            .where(TableMissingSubtitles.media_id == media_id)
            .order_by(TableMissingSubtitles.id)
        )
    ]
    if languages:
        return languages

    return []


def needs_missing_subtitle_scan(media_type, media_id):
    return database.execute(
        select(TableMissingSubtitleScans.media_id)
        .where(TableMissingSubtitleScans.media_type == media_type)
        .where(TableMissingSubtitleScans.media_id == media_id)
    ).first() is None


def get_missing_languages_map(media_type, media_ids):
    media_ids = _normalize_media_ids(media_ids)
    missing_languages = {media_id: [] for media_id in media_ids}
    if not media_ids:
        return missing_languages

    for media_id_chunk in _iter_chunks(media_ids):
        for row in database.execute(
            select(TableMissingSubtitles.media_id, TableMissingSubtitles.language)
            .where(TableMissingSubtitles.media_type == media_type)
            .where(TableMissingSubtitles.media_id.in_(media_id_chunk))
            .order_by(TableMissingSubtitles.id)
        ):
            missing_languages[row.media_id].append(row.language)

    return missing_languages


def delete_wanted_search_state(media_type, media_ids):
    media_ids = _normalize_media_ids(media_ids, coerce_int=True)

    for media_id_chunk in _iter_chunks(media_ids):
        if not media_id_chunk:
            continue
        database.execute(
            delete(TableMissingSubtitles)
            .where(TableMissingSubtitles.media_type == media_type)
            .where(TableMissingSubtitles.media_id.in_(media_id_chunk))
        )
        database.execute(
            delete(TableMissingSubtitleScans)
            .where(TableMissingSubtitleScans.media_type == media_type)
            .where(TableMissingSubtitleScans.media_id.in_(media_id_chunk))
        )
        database.execute(
            delete(TableFailedSubtitleAttempts)
            .where(TableFailedSubtitleAttempts.media_type == media_type)
            .where(TableFailedSubtitleAttempts.media_id.in_(media_id_chunk))
        )


def get_failed_attempt_pairs(media_type, media_id):
    attempts = []
    for row in database.execute(
        select(
            TableFailedSubtitleAttempts.language,
            TableFailedSubtitleAttempts.initial_attempt_at,
            TableFailedSubtitleAttempts.latest_attempt_at,
        )
        .where(TableFailedSubtitleAttempts.media_type == media_type)
        .where(TableFailedSubtitleAttempts.media_id == media_id)
    ):
        attempts.append([row.language, row.initial_attempt_at])
        if row.latest_attempt_at != row.initial_attempt_at:
            attempts.append([row.language, row.latest_attempt_at])

    return attempts


def get_due_missing_languages_for_media(media_type, media_id, adaptive_search_policy=None):
    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    return get_active_search_languages(
        get_missing_languages(media_type, media_id),
        get_failed_attempt_pairs(media_type, media_id),
        adaptive_search_policy=adaptive_search_policy,
    )


def due_missing_languages_statement(media_type, adaptive_search_policy):
    statement = (
        select(TableMissingSubtitles.media_id, TableMissingSubtitles.language)
        .where(TableMissingSubtitles.media_type == media_type)
    )

    if adaptive_search_policy is None:
        return statement

    initial_search_cutoff = adaptive_search_policy["initial_search_cutoff"]
    latest_search_cutoff = adaptive_search_policy["latest_search_cutoff"]

    return (
        statement
        .outerjoin(
            TableFailedSubtitleAttempts,
            (TableFailedSubtitleAttempts.media_type == TableMissingSubtitles.media_type) &
            (TableFailedSubtitleAttempts.media_id == TableMissingSubtitles.media_id) &
            (TableFailedSubtitleAttempts.language == TableMissingSubtitles.language),
        )
        .where(or_(
            TableFailedSubtitleAttempts.id.is_(None),
            TableFailedSubtitleAttempts.initial_attempt_at > initial_search_cutoff,
            TableFailedSubtitleAttempts.latest_attempt_at <= latest_search_cutoff,
        ))
    )


def count_due_missing_media(media_type, adaptive_search_policy=None):
    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    return database.execute(
        due_missing_languages_statement(media_type, adaptive_search_policy)
        .with_only_columns(func.count(func.distinct(TableMissingSubtitles.media_id)))
        .order_by(None)
    ).scalar() or 0


def iter_due_missing_languages_maps(media_type, adaptive_search_policy=None, batch_size=None):
    if batch_size is None:
        batch_size = WANTED_STATE_QUERY_BATCH_SIZE
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    statement = due_missing_languages_statement(media_type, adaptive_search_policy)
    last_media_id = None
    while True:
        # Keyset pagination bounds ORM buffering and tolerates searches deleting
        # earlier rows between batches. Fetch all languages for each selected ID.
        ids_statement = (
            statement.with_only_columns(TableMissingSubtitles.media_id)
            .distinct().order_by(TableMissingSubtitles.media_id).limit(batch_size)
        )
        if last_media_id is not None:
            ids_statement = ids_statement.where(TableMissingSubtitles.media_id > last_media_id)
        media_ids = database.execute(ids_statement).scalars().all()
        if not media_ids:
            return
        due_languages = {}
        for row in database.execute(
            statement.where(TableMissingSubtitles.media_id.in_(media_ids))
            .order_by(TableMissingSubtitles.media_id, TableMissingSubtitles.id)
        ):
            due_languages.setdefault(row.media_id, []).append(row.language)
        last_media_id = media_ids[-1]
        yield due_languages


def get_due_missing_languages_map(media_type, media_ids=None, adaptive_search_policy=None):
    has_media_filter = media_ids is not None
    if has_media_filter:
        media_ids = list(dict.fromkeys(media_ids))
        due_languages = {media_id: [] for media_id in media_ids}
        if not media_ids:
            return due_languages
    else:
        due_languages = {}

    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    if adaptive_search_policy is None:
        if has_media_filter:
            return get_missing_languages_map(media_type, media_ids)

        for row in database.execute(
            select(TableMissingSubtitles.media_id, TableMissingSubtitles.language)
            .where(TableMissingSubtitles.media_type == media_type)
            .order_by(TableMissingSubtitles.id)
        ):
            due_languages.setdefault(row.media_id, []).append(row.language)
        return due_languages

    statement = (
        due_missing_languages_statement(media_type, adaptive_search_policy)
        .order_by(TableMissingSubtitles.id)
    )
    if has_media_filter:
        for media_id_chunk in _iter_chunks(media_ids):
            for row in database.execute(
                statement.where(TableMissingSubtitles.media_id.in_(media_id_chunk))
            ):
                due_languages.setdefault(row.media_id, []).append(row.language)
    else:
        for row in database.execute(statement):
            due_languages.setdefault(row.media_id, []).append(row.language)

    return due_languages
