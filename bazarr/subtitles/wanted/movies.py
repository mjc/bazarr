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
from app.database import get_exclusion_clause, get_audio_profile_languages, TableMovies, database, update, select
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue

from ..adaptive_searching import updateFailedAttempts
from ..download import generate_subtitles
from .utils import get_due_missing_languages, get_language_search_items


def _wanted_movie(movie, providers_list, due_languages=None, job_id=None):
    audio_language_list = get_audio_profile_languages(movie.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    languages_to_stamp = due_languages
    if languages_to_stamp is None:
        languages_to_stamp = get_due_missing_languages(movie.missing_subtitles, movie.failedAttempts)
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
                                     job_id=job_id):

        if result:
            found_any = True
            if isinstance(result, tuple) and len(result):
                result = result[0]
            store_subtitles_movie(movie.path, path_mappings.path_replace_movie(movie.path))
            history_log_movie(1, movie.radarrId, result)
            event_stream(type='movie-wanted', action='delete', payload=movie.radarrId)
            send_notifications_movie(movie.radarrId, result.message)

    if not found_any and providers_list:
        for language in languages_to_stamp:
            updated = updateFailedAttempts(
                desired_language=language,
                attempt_string=movie.failedAttempts)
            database.execute(
                update(TableMovies)
                .values(failedAttempts=updated)
                .where(TableMovies.radarrId == movie.radarrId))


def wanted_download_subtitles_movie(radarr_id, job_id=None, providers_list=None, movie=None, due_languages=None):
    stmt = None

    def _get_stmt():
        nonlocal stmt
        if stmt is None:
            stmt = select(TableMovies.path,
                          TableMovies.missing_subtitles,
                          TableMovies.radarrId,
                          TableMovies.audio_language,
                          TableMovies.sceneName,
                          TableMovies.failedAttempts,
                          TableMovies.title,
                          TableMovies.profileId,
                          TableMovies.subtitles) \
                .where(TableMovies.radarrId == radarr_id)
        return stmt

    if movie is None:
        movie = database.execute(_get_stmt()).first()

    if not movie:
        logging.debug(f"BAZARR no movie with that radarrId can be found in database: {radarr_id}")
        return
    elif movie.subtitles is None:
        # subtitles indexing for this movie is incomplete, we'll do it again
        store_subtitles_movie(movie.path, path_mappings.path_replace_movie(movie.path))
        movie = database.execute(_get_stmt()).first()
        due_languages = None
    elif movie.missing_subtitles is None:
        # missing subtitles calculation for this movie is incomplete, we'll do it again
        list_missing_subtitles_movies(no=radarr_id)
        movie = database.execute(_get_stmt()).first()
        due_languages = None

    if providers_list is None:
        providers_list = get_providers()

    if providers_list:
        _wanted_movie(movie, providers_list, due_languages=due_languages, job_id=job_id)
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
               TableMovies.path,
               TableMovies.title,
               TableMovies.missing_subtitles,
               TableMovies.failedAttempts,
               TableMovies.audio_language,
               TableMovies.sceneName,
               TableMovies.profileId,
               TableMovies.subtitles)
        .where(reduce(operator.and_, conditions))) \
        .all()

    movies_to_search = []
    for movie in movies:
        due_languages = get_due_missing_languages(movie.missing_subtitles, movie.failedAttempts)
        if due_languages:
            movies_to_search.append((movie, due_languages))

    count_movies = len(movies_to_search)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_movies)

    if count_movies == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')
        throttled = False
        providers = None
    else:
        providers = get_providers()
        throttled = not providers
        if throttled:
            logging.info("BAZARR All providers are throttled")

    for i, (movie, due_languages) in enumerate(movies_to_search, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_message=movie.title)

        if providers:
            wanted_download_subtitles_movie(movie.radarrId,
                                            job_id=job_id,
                                            providers_list=providers,
                                            movie=movie,
                                            due_languages=due_languages)
        else:
            break

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing movies subtitles")
    logging.info('BAZARR Finished searching for missing Movies Subtitles. Check History for more information.')
