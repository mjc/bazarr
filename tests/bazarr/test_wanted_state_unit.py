import os
from sqlalchemy import insert, select

os.environ.setdefault("SZ_USER_AGENT", "pytest")

from subtitles import wanted_state


def test_empty_missing_language_result_is_marked_as_scanned(bind_wanted_state, transactional_session,
                                                            wanted_search_tables):
    del bind_wanted_state
    wanted_state.refresh_wanted_search_state("movie", 7, [])
    scans = transactional_session.execute(
        select(wanted_search_tables.missing_subtitle_scans.c.media_type,
               wanted_search_tables.missing_subtitle_scans.c.media_id)
    ).all()
    assert scans == [("movie", 7)]
    assert not wanted_state.needs_missing_subtitle_scan("movie", 7)


def test_unscanned_media_is_distinct_from_scanned_empty_result(bind_wanted_state):
    del bind_wanted_state
    assert wanted_state.needs_missing_subtitle_scan("movie", 7)


def test_delete_wanted_search_state_accepts_single_media_id(bind_wanted_state, transactional_session,
                                                           wanted_search_tables):
    del bind_wanted_state
    transactional_session.execute(insert(wanted_search_tables.missing_subtitles), [
        {"media_type": "movie", "media_id": 7, "language": "en"},
        {"media_type": "movie", "media_id": 8, "language": "fr"},
    ])
    transactional_session.execute(insert(wanted_search_tables.failed_subtitle_attempts), [
        {"media_type": "movie", "media_id": 7, "language": "en", "initial_attempt_at": 1.0, "latest_attempt_at": 2.0},
        {"media_type": "movie", "media_id": 8, "language": "fr", "initial_attempt_at": 1.0, "latest_attempt_at": 2.0},
    ])

    wanted_state.delete_wanted_search_state("movie", "7")

    missing_rows = transactional_session.execute(
        select(wanted_search_tables.missing_subtitles.c.media_id)
        .order_by(wanted_search_tables.missing_subtitles.c.media_id)
    ).scalars().all()
    failed_rows = transactional_session.execute(
        select(wanted_search_tables.failed_subtitle_attempts.c.media_id)
        .order_by(wanted_search_tables.failed_subtitle_attempts.c.media_id)
    ).scalars().all()
    assert missing_rows == [8]
    assert failed_rows == [8]


def test_delete_wanted_search_state_deduplicates_media_ids(bind_wanted_state, transactional_session,
                                                          wanted_search_tables):
    del bind_wanted_state
    transactional_session.execute(insert(wanted_search_tables.missing_subtitles), [
        {"media_type": "series", "media_id": 17, "language": "en"},
        {"media_type": "series", "media_id": 18, "language": "fr"},
    ])
    transactional_session.execute(insert(wanted_search_tables.failed_subtitle_attempts), [
        {"media_type": "series", "media_id": 17, "language": "en", "initial_attempt_at": 1.0, "latest_attempt_at": 2.0},
        {"media_type": "series", "media_id": 18, "language": "fr", "initial_attempt_at": 1.0, "latest_attempt_at": 2.0},
    ])

    wanted_state.delete_wanted_search_state("series", [17, "17", 17])

    missing_rows = transactional_session.execute(
        select(wanted_search_tables.missing_subtitles.c.media_id)
        .order_by(wanted_search_tables.missing_subtitles.c.media_id)
    ).scalars().all()
    failed_rows = transactional_session.execute(
        select(wanted_search_tables.failed_subtitle_attempts.c.media_id)
        .order_by(wanted_search_tables.failed_subtitle_attempts.c.media_id)
    ).scalars().all()
    assert missing_rows == [18]
    assert failed_rows == [18]
