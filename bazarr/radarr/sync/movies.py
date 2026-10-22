# coding=utf-8

import logging
import os
import operator
from collections.abc import Sequence
from typing import Literal, TypeAlias

from radarr.movie_snapshot import FileState, IncompleteMovie, Movie, MovieFile, MovieValues

from datetime import datetime
from functools import reduce
from sqlalchemy.engine import RowMapping

from app.config import settings
from app.database import TableMovies, TableLanguagesProfiles, TableMissingSubtitles, database, insert, update, \
    select, get_exclusion_clause
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue, JobCanceled
from app.notifier import send_notifications_movie
from utilities.arr_files import is_usable_file
from radarr.rootfolder import check_radarr_rootfolder
from subtitles.indexer.movies import store_subtitles_movie
from utilities.arr_sync_lookups import ArrSyncLookups, default_profile_id
from utilities.arr_types import KEEP_PROFILE, ProfileAssignment, WriteOutcome
from utilities.arr_sync_state import MOVIE_STATE, values_match
from utilities.helper import bool_map
from utilities.path_mappings import path_mappings
from subtitles.wanted_state import delete_media_and_wanted_search_state, get_due_missing_languages_for_media

from sqlalchemy.exc import IntegrityError
from .audio import movie_audio_language
from .parser import map_movie
from .utils import get_profile_list, get_tags, get_movies_from_radarr_api

FEATURE_PREFIX = "SYNC_MOVIES "
MovieSelection: TypeAlias = tuple[Movie, MovieFile] | FileState | Literal['preserve', 'remove']


def trace(message: str) -> None:
    if settings.general.debug:
        logging.debug(FEATURE_PREFIX + message)


def get_language_profiles() -> Sequence[tuple[int, str, str | None]]:
    return database.execute(
        select(TableLanguagesProfiles.profileId, TableLanguagesProfiles.name, TableLanguagesProfiles.tag)).tuples().all()


def _classify_movie(movie: Movie | IncompleteMovie) -> MovieSelection:
    match movie:
        case Movie(hasFile=False) | IncompleteMovie(hasFile=False):
            return 'remove'
        case Movie(movieFile=file) if file is not None:
            return movie, file
        case IncompleteMovie(hasFile=True, movieFile=file) if file is not None:
            return file
        case _:
            return 'preserve'


def _retains_movie(selection: MovieSelection, *, file_sizes: dict[str, int]) -> bool:
    match selection:
        case 'remove':
            return False
        case 'preserve':
            return True
        case (_, file) | (FileState() as file):
            return is_usable_file(file.path, file.size,
                                  path_replace=path_mappings.path_replace_movie, file_sizes=file_sizes)


def _retained_movie_ids(movies: list[Movie | IncompleteMovie], *, file_sizes: dict[str, int]) -> set[int] | None:
    identifiers: set[int] = set()
    retained_ids: set[int] = set()
    for movie in movies:
        identifier = movie.id
        if identifier is not None:
            identifiers.add(identifier)
            if _retains_movie(_classify_movie(movie), file_sizes=file_sizes):
                retained_ids.add(identifier)
    return retained_ids if len(identifiers) == len(movies) else None


def _movie_values(movie: Movie, file: MovieFile, lookups: ArrSyncLookups,
                  *, profile_id: ProfileAssignment) -> MovieValues:
    general = settings.general
    audio_language = movie_audio_language(
        movie, file, lookups, parse_embedded_audio_track=general.parse_embedded_audio_track)
    tags = lookups.tags(movie.tags)
    tag_enabled = general.movie_tag_enabled
    return map_movie(
        movie, file=file, audio_language=audio_language, tags=tags,
        profile_id=lookups.profile_id(
            tags, profile_id, enabled=tag_enabled,
            remove_tags=general.remove_profile_tags if tag_enabled else ()))

# Update movies in DB
def update_movie(previous: RowMapping | MovieValues, updated_movie: MovieValues,
                  *, check_file_identity: bool = True) -> WriteOutcome:
    previous_file: tuple[int | None, str] = previous['movie_file_id'], previous['path']
    file_changed = previous_file != (updated_movie['movie_file_id'], updated_movie['path'])
    try:
        previous_movie_file_id, previous_movie_path = previous_file

        updated_movie['updated_at_timestamp'] = datetime.now()
        statement = update(TableMovies).values(updated_movie).where(TableMovies.radarrId == updated_movie['radarrId'])
        if check_file_identity:
            statement = statement.where(
                TableMovies.movie_file_id.is_not_distinct_from(previous_movie_file_id),
                TableMovies.path.is_not_distinct_from(previous_movie_path))
            result = database.connection().execute(statement)
            if result.rowcount == 0:
                return 'failed'
        else:
            database.execute(statement)
    except IntegrityError as e:
        logging.error(f"BAZARR cannot update movie {updated_movie['path']} because of {e}")
        return 'failed'
    if file_changed:
        logging.debug(f'BAZARR updating subtitles for movie {updated_movie["path"]}')
        store_subtitles_movie(updated_movie['radarrId'])
    else:
        logging.debug(f'BAZARR skipping subtitle update for movie {updated_movie["path"]} as path '
                      f'and movie_file_id unchanged')
    event_stream(type='movie', action='update', payload=updated_movie['radarrId'])
    return 'reindexed' if file_changed else 'updated'


# Insert new movies in DB
def add_movie(added_movie: MovieValues) -> bool:
    try:
        added_movie['created_at_timestamp'] = datetime.now()
        database.execute(insert(TableMovies).values(added_movie))
    except IntegrityError as e:
        logging.error(f"BAZARR cannot insert movie {added_movie['path']} because of {e}")
        return False
    store_subtitles_movie(added_movie['radarrId'])
    event_stream(type='movie', action='update', payload=added_movie['radarrId'])
    return True


def _delete_movie(movie_id: int, path: str) -> None:
    try:
        if not delete_media_and_wanted_search_state('movie', TableMovies, 'radarrId', [movie_id]):
            return
    except IntegrityError as e:
        logging.error(f"BAZARR cannot delete movie {path_mappings.path_replace_movie(path)} because of {e}")
        return
    event_stream(type='movie', action='delete', payload=int(movie_id))
    logging.debug(f'BAZARR deleted this movie from the database: {path_mappings.path_replace_movie(path)}')


def update_movies(job_id: int | None = None, wait_for_completion: bool = False) -> None:
    if not job_id:
        jobs_queue.add_job_from_function("Syncing movies with Radarr", is_progress=True,
                                         wait_for_completion=wait_for_completion, is_cancellable=True)
        return
    else:
        job = jobs_queue.get_job(job_id=job_id)
        if job is None:
            return

    check_radarr_rootfolder()
    logging.debug('BAZARR Starting movie sync from Radarr.')
    apikey_radarr = settings.radarr.apikey

    movie_default_profile = (default_profile_id(settings.general.movie_default_profile)
                             if settings.general.movie_default_enabled is True else None)

    # Prevent trying to insert a movie with a non-existing languages profileId
    if (movie_default_profile is not None and not database.execute(
            select(TableLanguagesProfiles)
            .where(TableLanguagesProfiles.profileId == movie_default_profile))
            .first()):
        movie_default_profile = None

    if apikey_radarr is not None:
        # Get movies data from radarr
        movies = get_movies_from_radarr_api(apikey_radarr=apikey_radarr)
        if movies is None:
            logging.debug('BAZARR Radarr movie sync returned an invalid payload. Skipping sync.')
            jobs_queue.update_job_name(job_id=job_id, new_job_name="Synced movies with Radarr")
            return
        else:
            audio_profiles = get_profile_list()
            tagsDict = get_tags()
            if audio_profiles is None or tagsDict is None:
                return
            language_profiles = get_language_profiles()
            lookups = ArrSyncLookups(tagsDict, language_profiles, audio_profiles)

            movies_count = len(movies)
            jobs_queue.update_job_progress(job_id=job_id, progress_max=movies_count)
            # Get current movies in DB
            stored_movies: dict[int, RowMapping | MovieValues] = {
                row['radarrId']: row
                for row in database.execute(MOVIE_STATE).mappings()
            }
            file_sizes: dict[str, int] = {}
            retained_ids = _retained_movie_ids(movies, file_sizes=file_sizes)

            # Remove movies from DB that either no longer exist in Radarr or exist and Radarr says do not have a movie file
            movies_to_delete = (
                list(stored_movies.keys() - retained_ids) if retained_ids is not None else []
            )
            if retained_ids is None:
                logging.warning('BAZARR Skipping movie deletion because Radarr returned unidentified movies.')
            movies_deleted = 0
            if len(movies_to_delete):
                try:
                    deleted_movie_ids = delete_media_and_wanted_search_state(
                        'movie', TableMovies, 'radarrId', movies_to_delete,
                    )
                except IntegrityError as e:
                    logging.error(f"BAZARR cannot delete movies because of {e}")
                else:
                    movies_deleted = len(deleted_movie_ids)
                    for removed_movie in deleted_movie_ids:
                        event_stream(type='movie', action='delete', payload=removed_movie)

            # Add new movies and update movies that Radarr says have media files
            # Any new movies added to Radarr that don't have media files yet will not be added to DB
            sync_monitored = settings.radarr.sync_only_monitored_movies
            files_missing = skipped_count = 0
            movies_added = movies_updated = 0
            if movies_count and job.cancel_event.is_set():
                raise JobCanceled

            movies.reverse()
            for i in range(1, movies_count + 1):
                record = movies.pop()
                title = record.title if isinstance(record, Movie) else None
                if title is not None:
                    if job.cancel_event.is_set():
                        raise JobCanceled
                    jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_message=title)
                selection = _classify_movie(record)
                if isinstance(selection, tuple):
                    movie, file = selection
                    previous = stored_movies.get(movie.id)
                    previous_monitored: str | None = previous['monitored'] if previous is not None else None
                    if (sync_monitored and not movie.monitored and previous is not None
                            and bool_map.get(previous_monitored or '') is False):
                        trace(f"{i}: (Skipped Unmonitored) {movie.title}")
                        skipped_count += 1
                        continue
                    if _retains_movie(selection, file_sizes=file_sizes):
                        trace(f"{i}: (Processing) {movie.title}")
                        values = _movie_values(
                            movie, file, lookups,
                            profile_id=movie_default_profile if previous is None else KEEP_PROFILE)
                        movie_id = movie.id
                        del movie, file, record, selection
                        if previous is None:
                            _ = add_movie(values)
                            movies_added += 1
                        elif not values_match(previous, values) and update_movie(previous, values) != 'failed':
                            _ = values.setdefault('profileId', previous.get('profileId'))
                            stored_movies[movie_id] = values
                            movies_updated += 1
                        continue
                if record.id is not None:
                    files_missing += 1
                if title is not None:
                    trace(f"{i}: (Skipped File Missing) {title}")

            trace(f"Skipped {files_missing} file missing movies out of {movies_count}")
            if sync_monitored:
                trace(f"Skipped {skipped_count} unmonitored movies out of {movies_count}")
                trace(f"Processed {movies_count - files_missing - skipped_count} movies out of {movies_count} "
                      f"with {movies_added} added, {movies_updated} updated and {movies_deleted} deleted")
            else:
                trace(f"Processed {movies_count - files_missing} movies out of {movies_count} with {movies_added} added and "
                      f"{movies_updated} updated")

            logging.debug('BAZARR All movies synced from Radarr into database.')
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Synced movies with Radarr")


def update_one_movie(movie_id: int, action: str, defer_search: bool = False, is_signalr: bool = False) -> None:
    logging.debug(f'BAZARR syncing this specific movie from Radarr: {movie_id}')

    # Check if there's a row in the database for this movie ID
    existing_movie = database.execute(
        select(TableMovies.path, TableMovies.movie_file_id)
        .where(TableMovies.radarrId == movie_id))\
        .mappings().first()
    previous_path: str = existing_movie['path'] if existing_movie is not None else ''

    # Remove movie from DB
    if action == 'deleted':
        if existing_movie:
            _delete_movie(movie_id, previous_path)
        return

    movie_default_profile = (default_profile_id(settings.general.movie_default_profile)
                             if settings.general.movie_default_enabled is True else None)

    audio_profiles = get_profile_list()
    tagsDict = get_tags()
    if audio_profiles is None or tagsDict is None:
        return
    language_profiles = get_language_profiles()
    lookups = ArrSyncLookups(tagsDict, language_profiles, audio_profiles)

    try:
        # Get movie data from radarr api
        records = get_movies_from_radarr_api(apikey_radarr=settings.radarr.apikey, radarr_id=movie_id)
        if not records:
            return
        movie: MovieValues | None = None
        for movie_data in records:
            selection = _classify_movie(movie_data)
            if _retains_movie(selection, file_sizes={}):
                match selection:
                    case (record, file):
                        if action == 'updated':
                            movie = _movie_values(record, file, lookups,
                                                   profile_id=KEEP_PROFILE if existing_movie else movie_default_profile)
                    case _:
                        return
    except Exception:
        logging.exception('BAZARR cannot get movie returned by SignalR feed from Radarr API.')
        return

    if movie is None:
        if existing_movie:
            _delete_movie(movie_id, previous_path)
        return

    if existing_movie:
        outcome = update_movie(existing_movie, movie, check_file_identity=False)
        defer_search = defer_search or outcome == 'updated'
        if outcome != 'failed':
            logging.debug(
                f'BAZARR updated this movie into the database:{path_mappings.path_replace_movie(movie["path"])}')
    elif add_movie(movie):
        logging.debug(f'BAZARR inserted this movie into the database:{path_mappings.path_replace_movie(movie["path"])}')

    # Downloading missing subtitles
    if defer_search:
        logging.debug(
            f'BAZARR searching for missing subtitles is deferred until scheduled task execution for this movie: '
            f'{path_mappings.path_replace_movie(movie["path"])}')
    else:
        if os.path.exists(path_mappings.path_replace_movie(movie["path"])):
            logging.debug(f'BAZARR downloading missing subtitles for this movie: {movie["title"]} ({movie["year"]})')
            if _is_there_missing_subtitles(radarr_id=movie_id):
                jobs_queue.feed_jobs_pending_queue(job_name=f'Downloading missing subtitles for {movie["title"]} '
                                                            f'({movie["year"]})',
                                                   module='subtitles.mass_download.movies',
                                                   func='movies_download_subtitles',
                                                   args=[],
                                                   kwargs={'no': movie_id},
                                                   is_signalr=is_signalr)
            else:
                if is_signalr and settings.general.notify_if_nothing_is_missing_for_signalr_event:
                    send_notifications_movie(movie_id, "There are no missing subtitles in this movie.")
                logging.debug(f'BAZARR no missing subtitles for this movie: {movie["title"]} ({movie["year"]})')
        else:
            logging.debug(f'BAZARR cannot find this file yet (Radarr may be slow to import movie between disks?). '
                          f'Searching for missing subtitles is deferred until scheduled task execution for this movie: '
                          f'{movie["title"]} ({movie["year"]})')


def _is_there_missing_subtitles(radarr_id: int) -> bool:
    """
    Determines if there are any missing subtitles for a specified movie in the database.

    This function checks a movie identified by its radarr_id within the database to
    see if there are subtitles flagged as missing. It also considers the search activity
    status for the missing subtitles to determine whether there are active missing episodes.

    :param radarr_id: The ID of the movie in the Radarr system.
    :type radarr_id: int

    :return: A boolean indicating whether there are any active missing subtitles
             for the specified movie.
    :rtype: bool
    """
    movies_conditions = [(TableMovies.radarrId == radarr_id),
                         (TableMissingSubtitles.media_type == 'movie'),
                         (TableMissingSubtitles.media_id == TableMovies.radarrId)]
    if not radarr_id:
        return False
    movies_conditions += get_exclusion_clause('movie')
    missing_movies = database.execute(
        select(TableMissingSubtitles.language)
        .select_from(TableMovies)
        .join(TableMissingSubtitles, TableMissingSubtitles.media_id == TableMovies.radarrId)
        .where(reduce(operator.and_, movies_conditions))) \
        .all()
    due_languages = set(get_due_missing_languages_for_media('movie', radarr_id))
    for missing_movie in missing_movies:
        if missing_movie.language in due_languages:
            return True
    return False
