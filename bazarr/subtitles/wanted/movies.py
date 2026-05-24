# coding=utf-8
# fmt: off

import logging
import operator

from functools import reduce

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

from ..adaptive_searching import get_adaptive_search_policy, update_failed_attempts
from ..download import generate_subtitles
from .utils import get_due_missing_languages


def _movie_needs_wanted_lookup_refresh(movie):
    return (
        movie.missing_subtitles is None or
        not getattr(movie, "has_indexed_subtitles", True) or
        getattr(movie, "has_incomplete_embedded_subtitles", False)
    )


def _wanted_movie(movie, providers_list, job_id=None, adaptive_search_policy=None):
    audio_language_list = get_audio_profile_languages(movie.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    due_missing_languages = get_due_missing_languages(
        movie.missing_subtitles,
        movie.failedAttempts,
        adaptive_search_policy=adaptive_search_policy,
    )
    if not due_missing_languages:
        return

    languages = []
    for language in due_missing_languages:
        hi_ = "True" if language.endswith(':hi') else "False"
        forced_ = "True" if language.endswith(':forced') else "False"
        languages.append((language.split(":")[0], hi_, forced_))

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
                                     fallback_allowed=settings.general.use_whisper_fallback):

        if result:
            found_any = True
            if isinstance(result, tuple) and len(result):
                result = result[0]
            store_subtitles_movie(movie.radarrId)
            history_log_movie(1, movie.radarrId, result)
            send_notifications_movie(movie.radarrId, result.message)
            event_stream(type='movie-wanted', action='delete', payload=movie.radarrId)

    if not found_any and providers_list:
        database.execute(
            update(TableMovies)
            .values(failedAttempts=update_failed_attempts(due_missing_languages, movie.failedAttempts))
            .where(TableMovies.radarrId == movie.radarrId))


def wanted_download_subtitles_movie(radarr_id, job_id=None, providers_list=None, adaptive_search_policy=None):
    stmt = select(TableMovies.path,
                  TableMovies.missing_subtitles,
                  TableMovies.radarrId,
                  TableMovies.audio_language,
                  TableMovies.sceneName,
                  TableMovies.failedAttempts,
                  TableMovies.title,
                  TableMovies.profileId) \
        .where(TableMovies.radarrId == radarr_id)
    movie = database.execute(stmt).first()

    previously_indexed_subtitles = get_subtitles(radarr_id=radarr_id)

    if not movie:
        logging.debug(f"BAZARR no movie with that radarrId can be found in database: {radarr_id}")
        return
    elif not len(previously_indexed_subtitles) or \
            any([not x['embedded_track_id'] for x in previously_indexed_subtitles if not x['path']]):
        # subtitles indexing for this movie might be incomplete, we'll do it again
        store_subtitles_movie(radarr_id)
        movie = database.execute(stmt).first()
    elif movie.missing_subtitles is None:
        # missing subtitles calculation for this movie is incomplete, we'll do it again
        list_missing_subtitles_movies(no=radarr_id)
        movie = database.execute(stmt).first()

    if providers_list is None:
        providers_list = get_providers()
    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    if providers_list:
        _wanted_movie(
            movie,
            providers_list,
            job_id=job_id,
            adaptive_search_policy=adaptive_search_policy,
        )
    else:
        logging.info("BAZARR All providers are throttled")


def wanted_search_missing_subtitles_movies(job_id=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Searching for missing movies subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    conditions = [(TableMovies.missing_subtitles.is_not(None)),
                  (TableMovies.missing_subtitles != '[]')]
    conditions += get_exclusion_clause('movie')
    movies = database.execute(
        select(TableMovies.radarrId,
               TableMovies.audio_language,
               TableMovies.failedAttempts,
               TableMovies.missing_subtitles,
               TableMovies.path,
               TableMovies.profileId,
               TableMovies.sceneName,
               TableMovies.tags,
               TableMovies.monitored,
               TableMovies.title,
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
        .where(reduce(operator.and_, conditions))) \
        .all()

    count_movies = len(movies)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_movies)

    if count_movies == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')

    adaptive_search_policy = get_adaptive_search_policy()
    throttled = False
    for i, movie in enumerate(movies, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_message=movie.title)

        providers = get_providers()
        if providers:
            if _movie_needs_wanted_lookup_refresh(movie):
                wanted_download_subtitles_movie(
                    movie.radarrId,
                    job_id=job_id,
                    providers_list=providers,
                    adaptive_search_policy=adaptive_search_policy,
                )
            else:
                _wanted_movie(
                    movie,
                    providers,
                    job_id=job_id,
                    adaptive_search_policy=adaptive_search_policy,
                )

            # make sure to override the progress value updated by the subtitles synchronization
            jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_max=count_movies)
        else:
            logging.info("BAZARR All providers are throttled")
            throttled = True
            break

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing movies subtitles")
    logging.info('BAZARR Finished searching for missing Movies Subtitles. Check History for more information.')
