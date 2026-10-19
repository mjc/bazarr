# coding=utf-8

import logging
import os
import operator

from datetime import datetime
from typing import Literal
from utilities.video_analyzer import embedded_audio_reader
from dynaconf.utils.boxing import DynaBox
from radarr.info import RadarrConnection, get_radarr_connection
from radarr.movie_snapshot import MovieChange, MovieSyncPlan, MovieValues, is_movie, movie_file_size
from utilities.arr_types import is_arr_tags
from utilities.json_types import JsonValue
from functools import reduce

from app.config import settings
from app.database import TableMovies, TableLanguagesProfiles, TableMissingSubtitles, database, insert, update, \
    select, get_exclusion_clause
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue, JobCanceled
from app.notifier import send_notifications_movie
from constants import MINIMUM_VIDEO_SIZE
from radarr.rootfolder import check_radarr_rootfolder
from subtitles.indexer.movies import store_subtitles_movie
from utilities.helper import bool_map, has_changed_fields
from utilities.path_mappings import path_mappings
from subtitles.wanted_state import delete_media_and_wanted_search_state, get_due_missing_languages_for_media

from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import RowMapping
from .parser import movieParser
from .utils import get_profile_list, get_tags, get_movies_from_radarr_api

FEATURE_PREFIX = "SYNC_MOVIES "


def trace(message: str) -> None:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug('%s%s', FEATURE_PREFIX, message)


def get_language_profiles():
    return database.execute(
        select(TableLanguagesProfiles.profileId, TableLanguagesProfiles.name, TableLanguagesProfiles.tag)).all()


def get_movie_file_size_from_db(movie_path):
    try:
        bazarr_file_size = os.path.getsize(path_mappings.path_replace_movie(movie_path))
    except OSError:
        bazarr_file_size = 0
    return bazarr_file_size


def _has_usable_movie_file(movie: JsonValue, *, general: DynaBox | None = None) -> bool | None:
    if not isinstance(movie, dict) or not isinstance(movie.get('hasFile'), bool):
        return None
    if not movie['hasFile']:
        return False
    file = movie.get('movieFile')
    if not isinstance(file, dict):
        return None
    path, file_id = file.get('path'), file.get('id')
    size = movie_file_size(file.get('size'))
    if not isinstance(path, str) or not path.strip() or type(file_id) is not int or file_id <= 0 or size is None:
        return None
    if size > MINIMUM_VIDEO_SIZE or get_movie_file_size_from_db(path) > MINIMUM_VIDEO_SIZE:
        return True
    general_settings = settings.general if general is None else general
    return bool(general_settings.enable_strm_support and path.lower().endswith('.strm'))


def _resolve_movie_audio(change: MovieChange, general: DynaBox) -> None:
    if general.parse_embedded_audio_track:
        values = change.values
        values['audio_language'] = str(embedded_audio_reader(
            path_mappings.path_replace_movie(values['path']), file_size=values['file_size'],
            movie_file_id=values['movie_file_id'], use_cache=True))


def update_movie(change: MovieChange, *, general: DynaBox,
                 sync_monitored: bool = False) -> Literal['updated', 'skipped', 'unchanged', 'failed']:
    updated_movie = change.values
    parse_audio = bool(general.parse_embedded_audio_track)
    needs_monitored_guard = sync_monitored and updated_movie['monitored'] == 'False'
    previous_movie_data = None
    if needs_monitored_guard:
        previous_movie_data = database.execute(
            select(TableMovies.movie_file_id, TableMovies.path, TableMovies.monitored)
            .where(TableMovies.radarrId == updated_movie['radarrId'])).first()
        if previous_movie_data is not None and bool_map.get(previous_movie_data.monitored) is False:
            return 'skipped'
    if parse_audio:
        _resolve_movie_audio(change, general)
    if parse_audio or not needs_monitored_guard:
        previous_movie_data = database.execute(
            select(TableMovies.movie_file_id, TableMovies.path, TableMovies.monitored)
            .where(TableMovies.radarrId == updated_movie['radarrId'])).first()
    monitored_changed = previous_movie_data is not None and previous_movie_data.monitored != updated_movie['monitored']
    previous_movie_file_id = previous_movie_data.movie_file_id if previous_movie_data else None
    previous_movie_path = previous_movie_data.path if previous_movie_data else None
    if not change.metadata_changed and not monitored_changed and updated_movie['audio_language'] == change.previous_audio_language:
        return 'unchanged'
    try:
        updated_movie['updated_at_timestamp'] = datetime.now()
        database.execute(update(TableMovies).values(updated_movie)
                         .where(TableMovies.radarrId == updated_movie['radarrId']))
    except IntegrityError as error:
        logging.error('BAZARR cannot update movie %s because of %s', updated_movie['path'], error)
        return 'failed'
    if previous_movie_file_id != updated_movie['movie_file_id'] or previous_movie_path != updated_movie['path']:
        store_subtitles_movie(updated_movie['radarrId'])
    event_stream(type='movie', action='update', payload=updated_movie['radarrId'])
    return 'updated'


# Insert new movies in DB
def add_movie(added_movie: MovieValues) -> None:
    try:
        added_movie['created_at_timestamp'] = datetime.now()
        database.execute(
            insert(TableMovies)
            .values(added_movie))
    except IntegrityError as e:
        logging.error(f"BAZARR cannot insert movie {added_movie['path']} because of {e}")
    else:
        store_subtitles_movie(added_movie['radarrId'])
        event_stream(type='movie', action='update', payload=int(added_movie['radarrId']))


def _default_movie_profile(general: DynaBox) -> int | None:
    if general.movie_default_enabled is not True:
        return None
    configured = general.movie_default_profile
    if configured is None or configured == '':
        return None
    try:
        profile = int(configured)
    except (TypeError, ValueError):
        return None
    if database.execute(select(TableLanguagesProfiles.profileId)
                         .where(TableLanguagesProfiles.profileId == profile)).first() is None:
        return None
    return profile


def _prepare_movie_sync(connection: RadarrConnection, radarr: DynaBox,
                        general: DynaBox) -> MovieSyncPlan | None:
    payload = get_movies_from_radarr_api(apikey_radarr=connection.apikey, connection=connection)
    if not isinstance(payload, list):
        return None
    current: dict[int, RowMapping] = {
        row['radarrId']: row for row in database.execute(
            select(*(column for column in TableMovies.__table__.columns
                     if column.name != 'ffprobe_cache'))).mappings()
    }
    if not payload:
        return MovieSyncPlan([], list(current), 0, 0)
    audio_profiles = get_profile_list(connection=connection)
    tags = get_tags(connection=connection)
    if audio_profiles is None or not is_arr_tags(tags):
        return None
    language_profiles = get_language_profiles()
    default_profile = _default_movie_profile(general)
    retained: set[int] = set()
    seen: set[int] = set()
    complete = True
    changes: list[MovieChange] = []
    missing = 0
    for position, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            complete = False
            continue
        movie_id = raw.get('id')
        if type(movie_id) is not int or movie_id <= 0 or movie_id in seen:
            complete = False
            continue
        seen.add(movie_id)
        usable = _has_usable_movie_file(raw, general=general)
        if usable is not False:
            retained.add(movie_id)
        if usable is not True or not is_movie(raw):
            missing += 1
            continue
        previous = current.get(movie_id)
        values = movieParser(raw, action='update', tags_dict=tags, language_profiles=language_profiles,
                             movie_default_profile=None, audio_profiles=audio_profiles,
                             general=general, capabilities=connection.capabilities, parse_embedded_audio_track=False)
        if values is None:
            continue
        if previous is None:
            if 'profileId' not in values:
                values['profileId'] = default_profile
            changes.append(MovieChange(values, False, position, True, None))
        else:
            if general.parse_embedded_audio_track:
                changed = any(previous[key] != value for key, value in values.items() if key != 'audio_language')
            else:
                changed = has_changed_fields(previous, values)
            needs_monitored_check = radarr.sync_only_monitored_movies and values['monitored'] == 'False'
            if changed or general.parse_embedded_audio_track or needs_monitored_check:
                changes.append(MovieChange(values, True, position, changed, previous['audio_language']))
    deleted = list(current.keys() - retained) if complete else []
    if not complete:
        logging.warning('BAZARR Skipping movie deletion because Radarr returned unidentified movies.')
    return MovieSyncPlan(changes, deleted, len(payload), missing)


def update_movies(job_id: int | None = None, wait_for_completion: bool = False) -> None:
    if not job_id:
        jobs_queue.add_job_from_function('Syncing movies with Radarr', is_progress=True,
                                         wait_for_completion=wait_for_completion, is_cancellable=True)
        return
    job = jobs_queue.get_job(job_id=job_id)
    if job is None:
        return
    radarr = settings.radarr
    general = settings.general
    connection = get_radarr_connection(radarr)
    if connection is None:
        return
    check_radarr_rootfolder()
    plan = _prepare_movie_sync(connection, radarr, general)
    if plan is None:
        logging.debug('BAZARR Radarr movie sync returned an invalid payload. Skipping sync.')
        jobs_queue.update_job_name(job_id=job_id, new_job_name='Synced movies with Radarr')
        return
    jobs_queue.update_job_progress(job_id=job_id, progress_max=plan.movie_count)
    if plan.deleted_ids:
        try:
            deleted = delete_media_and_wanted_search_state('movie', TableMovies, 'radarrId', plan.deleted_ids)
        except IntegrityError as error:
            logging.error('BAZARR cannot delete movies because of %s', error)
        else:
            for movie_id in deleted:
                event_stream(type='movie', action='delete', payload=movie_id)
    plan.changes.reverse()
    added = updated = skipped = 0
    while plan.changes:
        if job.cancel_event.is_set():
            raise JobCanceled
        change = plan.changes.pop()
        jobs_queue.update_job_progress(job_id=job_id, progress_value=change.position,
                                       progress_message=change.values['title'])
        if not change.existing:
            if radarr.sync_only_monitored_movies and change.values['monitored'] == 'False':
                monitored = database.execute(select(TableMovies.monitored)
                                             .where(TableMovies.radarrId == change.values['radarrId'])).scalar()
                if monitored is not None and bool_map.get(monitored) is False:
                    skipped += 1
                    continue
            _resolve_movie_audio(change, general)
            add_movie(change.values)
            added += 1
        else:
            result = update_movie(change, general=general, sync_monitored=radarr.sync_only_monitored_movies)
            if result == 'updated':
                updated += 1
            elif result == 'skipped':
                skipped += 1
        del change
    trace(f'Skipped {plan.missing_count} file missing movies out of {plan.movie_count}')
    if radarr.sync_only_monitored_movies:
        trace(f'Skipped {skipped} unmonitored movies out of {plan.movie_count}')
    trace(f'Processed {plan.movie_count - plan.missing_count - skipped} movies out of {plan.movie_count} '
          f'with {added} added and {updated} updated')
    jobs_queue.update_job_name(job_id=job_id, new_job_name='Synced movies with Radarr')
    logging.debug('BAZARR All movies synced from Radarr into database.')


def update_one_movie(movie_id: int | None, action: str, defer_search: bool = False, is_signalr: bool = False) -> None:
    if movie_id is None or movie_id <= 0:
        return
    logging.debug(f'BAZARR syncing this specific movie from Radarr: {movie_id}')

    # Check if there's a row in the database for this movie ID
    existing_movie = database.execute(
        select(TableMovies.path, TableMovies.movie_file_id)
        .where(TableMovies.radarrId == movie_id))\
        .first()

    # Remove movie from DB
    if action == 'deleted':
        if existing_movie:
            try:
                deleted_movie_ids = delete_media_and_wanted_search_state(
                    'movie', TableMovies, 'radarrId', [movie_id],
                )
            except IntegrityError as e:
                logging.error(f"BAZARR cannot delete movie {path_mappings.path_replace_movie(existing_movie.path)} "
                              f"because of {e}")
            else:
                if deleted_movie_ids:
                    event_stream(type='movie', action='delete', payload=int(movie_id))
                    logging.debug(
                        f'BAZARR deleted this movie from the database: '
                        f'{path_mappings.path_replace_movie(existing_movie.path)}')
        return

    if action not in ('updated', 'added'):
        return
    general = settings.general
    connection = get_radarr_connection()
    if connection is None:
        return
    movie_data = get_movies_from_radarr_api(apikey_radarr=connection.apikey, radarr_id=movie_id,
                                           connection=connection)
    if not isinstance(movie_data, dict) or type(movie_data.get('id')) is not int or movie_data['id'] != movie_id:
        return
    usable = _has_usable_movie_file(movie_data, general=general)
    if usable is None:
        return
    movie = None
    if usable:
        if not is_movie(movie_data):
            return
        audio_profiles = get_profile_list(connection=connection)
        tags = get_tags(connection=connection)
        if audio_profiles is None or not is_arr_tags(tags):
            return
        movie = movieParser(movie_data, action='update' if existing_movie else 'insert', tags_dict=tags,
                            language_profiles=get_language_profiles(), movie_default_profile=_default_movie_profile(general),
                            audio_profiles=audio_profiles, general=general, capabilities=connection.capabilities)
        if movie is None:
            return
    del movie_data

    if movie is None:
        if existing_movie is None:
            return
        try:
            deleted_movie_ids = delete_media_and_wanted_search_state(
                'movie', TableMovies, 'radarrId', [movie_id],
            )
        except IntegrityError as e:
            logging.error(f"BAZARR cannot delete movie {path_mappings.path_replace_movie(existing_movie.path)} because "
                          f"of {e}")
        else:
            if deleted_movie_ids:
                event_stream(type='movie', action='delete', payload=int(movie_id))
                logging.debug(
                    f'BAZARR deleted this movie from the database:{path_mappings.path_replace_movie(existing_movie.path)}')
        return

    # Update existing movie in DB
    if existing_movie is not None:
        try:
            movie['updated_at_timestamp'] = datetime.now()
            database.execute(
                update(TableMovies)
                .values(movie)
                .where(TableMovies.radarrId == movie['radarrId']))
        except IntegrityError as e:
            logging.error(f"BAZARR cannot update movie {path_mappings.path_replace_movie(movie['path'])} because "
                          f"of {e}")
        else:
            if (existing_movie.movie_file_id != movie['movie_file_id'] or
                    existing_movie.path != movie['path']):
                # Store subtitles for updated movie where path or movie_file_id changed
                logging.debug(f'BAZARR updating subtitles for movie {path_mappings.path_replace_movie(movie["path"])}')
                store_subtitles_movie(movie_id)
            else:
                logging.debug(f'BAZARR skipping subtitle update for movie '
                              f'{path_mappings.path_replace_movie(movie["path"])} as path and movie_file_id unchanged')
                defer_search = True

            event_stream(type='movie', action='update', payload=int(movie_id))
            logging.debug(
                f'BAZARR updated this movie into the database:{path_mappings.path_replace_movie(movie["path"])}')

    # Insert new movie in DB
    else:
        try:
            movie['created_at_timestamp'] = datetime.now()
            database.execute(
                insert(TableMovies)
                .values(movie))
        except IntegrityError as e:
            logging.error(f"BAZARR cannot insert movie {path_mappings.path_replace_movie(movie['path'])} because "
                          f"of {e}")
        else:
            store_subtitles_movie(movie_id)
            event_stream(type='movie', action='update', payload=int(movie_id))
            logging.debug(
                f'BAZARR inserted this movie into the database:{path_mappings.path_replace_movie(movie["path"])}')

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
                if is_signalr and general.notify_if_nothing_is_missing_for_signalr_event:
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
