# coding=utf-8
# fmt: off

import logging
import operator

from functools import reduce
from types import SimpleNamespace

from utilities.path_mappings import path_mappings
from subtitles.indexer.movies import store_subtitles_movie, list_missing_subtitles_movies
from radarr.history import history_log_movie
from app.notifier import send_notifications_movie
from app.get_providers import get_providers
from app.database import (get_exclusion_clause, get_audio_profile_languages, TableMovies, TableMoviesSubtitles,
                          database, update, select, get_subtitles)
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue
from app.config import settings

from ..adaptive_searching import update_failed_attempts
from ..download import generate_subtitles
from app.wanted_sql import prepare_due_missing_subtitle_language_table, supports_sqlite_wanted_search
from subtitles.serialization import parse_missing_subtitles
from .utils import get_due_missing_languages, get_language_search_items


_SQLITE_BIND_PARAMETER_BATCH_SIZE = 900
_FAILED_ATTEMPT_UPDATE_BATCH_SIZE = 300
_DUE_MOVIE_DETAILS_BATCH_SIZE = _SQLITE_BIND_PARAMETER_BATCH_SIZE


def _movie_needs_wanted_lookup_refresh(movie):
    return (
        movie.missing_subtitles is None or
        (
            not getattr(movie, "has_indexed_subtitles", True) and
            not parse_missing_subtitles(movie.missing_subtitles)
        ) or
        getattr(movie, "has_incomplete_embedded_subtitles", False)
    )


def _updated_failed_attempts(failed_attempts, languages):
    return update_failed_attempts(languages, failed_attempts)


def _iter_chunks(items, batch_size):
    for index in range(0, len(items), batch_size):
        yield items[index:index + batch_size]


def _failed_attempts_match(column, value):
    if value is None:
        return column.is_(None)

    return column == value


def _wanted_movie(movie, providers_list, job_id=None, due_languages=None, fallback_allowed=None,
                  defer_failed_attempts=False):
    audio_language_list = get_audio_profile_languages(movie.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    if due_languages is None:
        missing_languages = parse_missing_subtitles(movie.missing_subtitles)
        languages_to_stamp = get_due_missing_languages(movie.missing_subtitles, movie.failedAttempts)
        for language in set(missing_languages) - set(languages_to_stamp):
            logging.info(f"BAZARR Search is throttled by adaptive search for this movie {movie.path} and "
                         f"language: {language}")
    else:
        languages_to_stamp = list(due_languages)

    if not languages_to_stamp:
        return

    if fallback_allowed is None:
        fallback_allowed = settings.general.use_whisper_fallback

    languages = get_language_search_items(languages_to_stamp)
    found_any = False
    for result in generate_subtitles(path_mappings.path_replace_movie(movie.path),
                                     languages,
                                     audio_language,
                                     str(movie.sceneName),
                                     movie.title,
                                     'movie',
                                     movie.profileId,
                                     check_if_still_required=True,
                                     job_id=job_id,
                                     fallback_allowed=fallback_allowed):

        if result:
            found_any = True
            if isinstance(result, tuple) and len(result):
                result = result[0]
            store_subtitles_movie(movie.radarrId)
            history_log_movie(1, movie.radarrId, result)
            send_notifications_movie(movie.radarrId, result.message)
            event_stream(type='movie-wanted', action='delete', payload=movie.radarrId)

    if providers_list:
        if found_any:
            refreshed_movie = database.execute(
                select(TableMovies.missing_subtitles)
                .where(TableMovies.radarrId == movie.radarrId)) \
                .first()
            if not refreshed_movie:
                return

            current_missing_languages = set(parse_missing_subtitles(refreshed_movie.missing_subtitles))
            remaining_due_languages = [
                language for language in languages_to_stamp
                if language in current_missing_languages
            ]
        else:
            remaining_due_languages = languages_to_stamp
        if not remaining_due_languages:
            return

        if defer_failed_attempts:
            return remaining_due_languages

        updated = _updated_failed_attempts(movie.failedAttempts, remaining_due_languages)
        database.execute(
            update(TableMovies)
            .values(failedAttempts=updated)
            .where(TableMovies.radarrId == movie.radarrId))


def wanted_download_subtitles_movie(
    radarr_id,
    job_id=None,
    providers_list=None,
    movie=None,
    due_languages=None,
    fallback_allowed=None,
    defer_failed_attempts=False,
):
    stmt = select(TableMovies.path,
                  TableMovies.missing_subtitles,
                  TableMovies.radarrId,
                  TableMovies.audio_language,
                  TableMovies.sceneName,
                  TableMovies.failedAttempts,
                  TableMovies.title,
                  TableMovies.profileId,
                  select(TableMoviesSubtitles.id)
                  .where(TableMoviesSubtitles.radarrId == TableMovies.radarrId)
                  .limit(1)
                  .exists()
                  .label("has_indexed_subtitles"),
                  select(TableMoviesSubtitles.id)
                  .where(TableMoviesSubtitles.radarrId == TableMovies.radarrId)
                  .where(TableMoviesSubtitles.path.is_(None))
                  .where(TableMoviesSubtitles.embedded_track_id.is_(None))
                  .limit(1)
                  .exists()
                  .label("has_incomplete_embedded_subtitles")) \
        .where(TableMovies.radarrId == radarr_id)

    if movie is not None and due_languages is not None and not _movie_needs_wanted_lookup_refresh(movie):
        if providers_list is None:
            providers_list = get_providers()
        if providers_list:
            return _wanted_movie(
                movie,
                providers_list,
                job_id=job_id,
                due_languages=due_languages,
                fallback_allowed=fallback_allowed,
                defer_failed_attempts=defer_failed_attempts)
        else:
            logging.info("BAZARR All providers are throttled")
        return

    if movie is None:
        movie = database.execute(stmt).first()

    if not movie:
        logging.debug(f"BAZARR no movie with that radarrId can be found in database: {radarr_id}")
        return
    if _movie_needs_wanted_lookup_refresh(movie):
        rebuilt_wanted_state = False
        previously_indexed_subtitles = get_subtitles(radarr_id=radarr_id)
        if not len(previously_indexed_subtitles) or \
                any([not x['embedded_track_id'] for x in previously_indexed_subtitles if not x['path']]):
            # subtitles indexing for this movie might be incomplete, we'll do it again
            store_subtitles_movie(radarr_id)
            movie = database.execute(stmt).first()
            if not movie:
                return
            rebuilt_wanted_state = True
        if movie.missing_subtitles is None:
            # missing subtitles calculation for this movie is incomplete, we'll do it again
            list_missing_subtitles_movies(no=radarr_id)
            rebuilt_wanted_state = True
        if rebuilt_wanted_state:
            movie = database.execute(stmt).first()
            if not movie:
                return
            due_languages = None
            providers_list = None

    if providers_list is None:
        providers_list = get_providers()

    if providers_list:
        return _wanted_movie(
            movie,
            providers_list,
            job_id=job_id,
            due_languages=due_languages,
            fallback_allowed=fallback_allowed,
            defer_failed_attempts=defer_failed_attempts)
    else:
        logging.info("BAZARR All providers are throttled")


def _record_failed_movie_attempts(failed_attempt_languages):
    if not failed_attempt_languages:
        return

    pending_attempt_languages = dict(failed_attempt_languages)
    id_column = TableMovies.__table__.c.radarrId
    failed_attempts_column = TableMovies.__table__.c.failedAttempts
    for _ in range(3):
        current_attempts_by_movie = {}
        movie_ids = list(pending_attempt_languages)
        for movie_id_chunk in _iter_chunks(movie_ids, _FAILED_ATTEMPT_UPDATE_BATCH_SIZE):
            for row in database.execute(
                select(TableMovies.radarrId, TableMovies.failedAttempts)
                .where(TableMovies.radarrId.in_(movie_id_chunk))
            ).all():
                current_attempts_by_movie[row.radarrId] = row.failedAttempts

        retry_attempt_languages = {}
        for radarr_id, languages in pending_attempt_languages.items():
            current_attempts = current_attempts_by_movie.get(radarr_id)
            updated_attempts = _updated_failed_attempts(current_attempts, languages)
            result = database.execute(
                TableMovies.__table__.update()
                .where(id_column == radarr_id)
                .where(_failed_attempts_match(failed_attempts_column, current_attempts))
                .values(failedAttempts=updated_attempts)
            )
            if result.rowcount != 1:
                retry_attempt_languages[radarr_id] = languages

        if not retry_attempt_languages:
            return
        pending_attempt_languages = retry_attempt_languages


def wanted_search_missing_subtitles_movies(job_id=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Searching for missing movies subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    conditions = [(TableMovies.missing_subtitles.is_not(None)),
                  (TableMovies.missing_subtitles != '[]')]
    conditions += get_exclusion_clause('movie')
    movie_rows = []

    movie_detail_select = select(TableMovies.radarrId,
                                 TableMovies.tags,
                                 TableMovies.monitored,
                                 TableMovies.title,
                                 TableMovies.path,
                                 TableMovies.missing_subtitles,
                                 TableMovies.audio_language,
                                 TableMovies.sceneName,
                                 TableMovies.failedAttempts,
                                 TableMovies.profileId,
                                 select(TableMoviesSubtitles.id)
                                 .where(TableMoviesSubtitles.radarrId == TableMovies.radarrId)
                                 .limit(1)
                                 .exists()
                                 .label("has_indexed_subtitles"),
                                 select(TableMoviesSubtitles.id)
                                 .where(TableMoviesSubtitles.radarrId == TableMovies.radarrId)
                                 .where(TableMoviesSubtitles.path.is_(None))
                                 .where(TableMoviesSubtitles.embedded_track_id.is_(None))
                                 .limit(1)
                                 .exists()
                                 .label("has_incomplete_embedded_subtitles"))

    if supports_sqlite_wanted_search():
        due_languages = prepare_due_missing_subtitle_language_table(
            database,
            select(
                TableMovies.radarrId.label("media_id"),
                TableMovies.missing_subtitles,
                TableMovies.failedAttempts.label("failed_attempts"),
            )
            .select_from(TableMovies)
            .where(reduce(operator.and_, conditions)),
            "temp_due_movie_languages",
        )
        due_movie_ids = [
            row.media_id
            for row in database.execute(
                select(due_languages.c.media_id)
                .distinct()
                .order_by(due_languages.c.media_id)
            ).all()
        ]
        for due_movie_id_chunk in _iter_chunks(due_movie_ids, _DUE_MOVIE_DETAILS_BATCH_SIZE):
            movie_rows.extend(
                database.execute(
                    movie_detail_select
                    .add_columns(due_languages.c.language.label("due_language"))
                    .join(due_languages, due_languages.c.media_id == TableMovies.radarrId)
                    .where(TableMovies.radarrId.in_(due_movie_id_chunk))
                ).all()
            )
        movies_by_id = {}
        for row in movie_rows:
            movie, languages = movies_by_id.setdefault(row.radarrId, (
                SimpleNamespace(
                    radarrId=row.radarrId,
                    tags=row.tags,
                    monitored=row.monitored,
                    title=row.title,
                    path=row.path,
                    missing_subtitles=row.missing_subtitles,
                    audio_language=row.audio_language,
                    sceneName=row.sceneName,
                    failedAttempts=row.failedAttempts,
                    profileId=row.profileId,
                    has_indexed_subtitles=row.has_indexed_subtitles,
                    has_incomplete_embedded_subtitles=row.has_incomplete_embedded_subtitles,
                ),
                [],
            ))
            languages.append(row.due_language)
        movies = list(movies_by_id.values())
    else:
        movie_rows = database.execute(
            movie_detail_select
            .where(reduce(operator.and_, conditions))
            .order_by(TableMovies.radarrId)
        ).all()
        movies = [
            (
                SimpleNamespace(
                radarrId=row.radarrId,
                tags=row.tags,
                monitored=row.monitored,
                title=row.title,
                path=row.path,
                missing_subtitles=row.missing_subtitles,
                audio_language=row.audio_language,
                sceneName=row.sceneName,
                failedAttempts=row.failedAttempts,
                profileId=row.profileId,
                has_indexed_subtitles=row.has_indexed_subtitles,
                has_incomplete_embedded_subtitles=row.has_incomplete_embedded_subtitles,
            ),
                None,
            )
            for row in movie_rows
        ]

    count_movies = len(movies)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_movies)

    if count_movies == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')

    throttled = False
    fallback_allowed = settings.general.use_whisper_fallback
    failed_attempt_languages = {}
    for i, (movie, due_languages) in enumerate(movies, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_message=movie.title)

        providers = get_providers()
        if providers:
            if _movie_needs_wanted_lookup_refresh(movie):
                remaining_due_languages = wanted_download_subtitles_movie(
                    movie.radarrId,
                    job_id=job_id,
                    providers_list=providers,
                    movie=movie,
                    due_languages=due_languages,
                    fallback_allowed=fallback_allowed,
                    defer_failed_attempts=True)
            else:
                remaining_due_languages = _wanted_movie(
                    movie,
                    providers,
                    job_id=job_id,
                    due_languages=due_languages,
                    fallback_allowed=fallback_allowed,
                    defer_failed_attempts=True)
            if remaining_due_languages:
                failed_attempt_languages[movie.radarrId] = remaining_due_languages

            # make sure to override the progress value updated by the subtitles synchronization
            jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_max=count_movies)
        else:
            logging.info("BAZARR All providers are throttled")
            throttled = True
            break

    _record_failed_movie_attempts(failed_attempt_languages)

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing movies subtitles")
    logging.info('BAZARR Finished searching for missing Movies Subtitles. Check History for more information.')
