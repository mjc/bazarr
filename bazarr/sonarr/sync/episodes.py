# coding=utf-8

import os
import logging
import operator
import requests

from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import RowMapping
from dynaconf.utils.boxing import DynaBox
from utilities.arr import arr_session
from utilities.json_types import JsonValue
from datetime import datetime
from functools import reduce

from app.database import database, TableShows, TableEpisodes, TableMissingSubtitles, update, insert, select, \
    get_exclusion_clause
from app.config import settings
from app.settings_cache import settings_cache
from utilities.helper import bool_map, has_changed_fields
from utilities.path_mappings import path_mappings
from subtitles.indexer.series import store_subtitles, series_full_scan_subtitles
from subtitles.mass_download import episode_download_subtitles
from app.event_handler import event_stream
from sonarr.info import SonarrConnection, get_sonarr_connection
from sonarr.episode_snapshot import EpisodeSnapshot, EpisodeSyncPlan, EpisodeValues, complete_episode_snapshot
from app.jobs_queue import jobs_queue
from app.notifier import send_notifications
from subtitles.wanted_state import delete_media_and_wanted_search_state, get_due_missing_languages_map

from .parser import episodeParser
from .utils import get_episodes_from_sonarr_api, get_episodesFiles_from_sonarr_api

FEATURE_PREFIX = "SYNC_EPISODES "


def trace(message: str) -> None:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug('%s%s', FEATURE_PREFIX, message)


def _load_episode_snapshot(connection: SonarrConnection, *, series_id: int | None = None,
                           episode_id: int | None = None,
                           session: requests.Session | None = None) -> EpisodeSnapshot | None:
    payload: JsonValue = get_episodes_from_sonarr_api(apikey_sonarr=connection.apikey, series_id=series_id,
                                          episode_id=episode_id, connection=connection, session=session)
    if episode_id is not None:
        if not isinstance(payload, dict):
            return None
        if payload.get('id') != episode_id:
            return None
        payload = [payload]
    if not isinstance(payload, list):
        return None
    files: JsonValue = None
    separate = connection.capabilities.separate_episode_files
    if separate:
        if series_id is not None:
            files = get_episodesFiles_from_sonarr_api(apikey_sonarr=connection.apikey, series_id=series_id,
                                                       connection=connection, session=session)
        elif payload and isinstance(payload[0], dict) and payload[0].get('hasFile') is True:
            file_id = payload[0].get('episodeFileId')
            if type(file_id) is not int:
                return None
            file = get_episodesFiles_from_sonarr_api(apikey_sonarr=connection.apikey,
                                                      episode_file_id=file_id, connection=connection, session=session)
            files = [file] if isinstance(file, dict) else None
        else:
            files = []
    snapshot = complete_episode_snapshot(payload, files, separate_files=separate)
    if snapshot is not None and series_id is not None:
        if any(episode['seriesId'] != series_id for episode in snapshot.episodes):
            return None
    return snapshot


def _prepare_episode_sync(connection: SonarrConnection, series_id: int, general: DynaBox,
                          sync_monitored: bool, *, session: requests.Session | None = None) -> EpisodeSyncPlan | None:
    snapshot = _load_episode_snapshot(connection, series_id=series_id, session=session)
    if snapshot is None:
        return None
    # Retain the existing conservative behavior for an empty episode catalogue.
    if not snapshot.episodes:
        return None
    current: dict[int, RowMapping] = {
        row['sonarrEpisodeId']: row for row in database.execute(
            select(*(column for column in TableEpisodes.__table__.columns
                     if column.name != 'ffprobe_cache'))
            .where(TableEpisodes.__table__.c.sonarrSeriesId == series_id)).mappings()
    }
    retained_ids: set[int] = set()
    additions: list[EpisodeValues] = []
    updates: list[EpisodeValues] = []
    skipped_count = 0
    monitored_by_file = {row['episode_file_id']: row['monitored'] for row in current.values()} if sync_monitored else {}
    audio_languages: dict[int, str | None] = {}
    for episode in snapshot.episodes:
        file = episode.get('episodeFile')
        if not episode['hasFile'] or file is None:
            continue
        if sync_monitored:
            stored_monitored = monitored_by_file.get(file['id'])
            monitored = None if stored_monitored is None else bool_map.get(stored_monitored)
            if monitored is not None and monitored == episode['monitored'] and not monitored:
                retained_ids.add(episode['id'])
                skipped_count += 1
                continue
        parsed = episodeParser(episode, general=general, audio_languages=audio_languages)
        if parsed is None:
            continue
        retained_ids.add(episode['id'])
        if episode['id'] not in current:
            additions.append(parsed)
        elif has_changed_fields(current[episode['id']], parsed):
            updates.append(parsed)
    return EpisodeSyncPlan(list(current.keys() - retained_ids), additions, updates, len(snapshot.episodes), skipped_count)


def sync_episodes(series_id: int | None, defer_search: bool = False, is_signalr: bool = False,
                  *, connection: SonarrConnection | None = None, session: requests.Session | None = None) -> None:
    logging.debug(f'BAZARR Starting episodes sync from Sonarr for series ID {series_id}.')
    sonarr = settings_cache.get(settings, 'sonarr')

    if not series_id:
        return
    general = settings_cache.get(settings, 'general')
    with arr_session(session) as http:
        connection = get_sonarr_connection(sonarr, session=http) if connection is None else connection
        if connection is None:
            logging.debug('BAZARR skipping episodes sync because Sonarr version is unknown')
            return
        sync_monitored = bool(sonarr.sync_only_monitored_series and sonarr.sync_only_monitored_episodes)
        plan = _prepare_episode_sync(connection, series_id, general, sync_monitored, session=http)
    del http
    if plan is None:
        logging.debug('BAZARR skipping episodes sync because Sonarr returned an incomplete snapshot')
        return
    episodes_to_add = plan.additions
    episodes_to_update = plan.updates
    if sync_monitored and general.debug:
        series_title = database.execute(select(TableShows.title).where(TableShows.sonarrSeriesId == series_id)).scalar()
        trace(f"Skipped {plan.skipped_count} unmonitored episodes out of {plan.episode_count} for {series_title}")

    # Remove old episodes from DB
    episodes_to_delete = plan.deleted_ids
    del plan

    if len(episodes_to_delete):
        try:
            deleted_episode_ids = delete_media_and_wanted_search_state(
                'series', TableEpisodes, 'sonarrEpisodeId', episodes_to_delete,
            )
        except IntegrityError as e:
            logging.error(f"BAZARR cannot delete episodes because of {e}")
        else:
            for removed_episode in deleted_episode_ids:
                event_stream(type='episode', action='delete', payload=removed_episode)

    # Insert new episodes in DB
    if len(episodes_to_add):
        for added_episode in episodes_to_add:
            try:
                added_episode['created_at_timestamp'] = datetime.now()
                database.execute(insert(TableEpisodes).values(added_episode))
            except IntegrityError as e:
                logging.error(f"BAZARR cannot insert episodes because of {e}. We'll try to update it instead.")
                del added_episode['created_at_timestamp']
                episodes_to_update.append(added_episode)
            else:
                store_subtitles(added_episode['sonarrEpisodeId'])
                event_stream(type='episode', payload=added_episode['sonarrEpisodeId'])

    # Update existing episodes in DB
    if len(episodes_to_update):
        for updated_episode in episodes_to_update:
            try:
                previous_episode_data = database.execute(
                    select(TableEpisodes.episode_file_id, TableEpisodes.path)
                    .where(TableEpisodes.sonarrEpisodeId == updated_episode['sonarrEpisodeId'])
                ).first()
                previous_episode_id = updated_episode['sonarrEpisodeId']
                previous_episode_file_id = previous_episode_data.episode_file_id if previous_episode_data else None
                previous_episode_path = previous_episode_data.path if previous_episode_data else None
                updated_episode['updated_at_timestamp'] = datetime.now()
                database.execute(update(TableEpisodes)
                                 .values(updated_episode)
                                 .where(TableEpisodes.sonarrEpisodeId == updated_episode['sonarrEpisodeId']))
            except IntegrityError as e:
                logging.error(f"BAZARR cannot update episodes because of {e}")
            else:
                if (previous_episode_file_id != updated_episode['episode_file_id'] or
                        previous_episode_path != updated_episode['path']):
                    # Store subtitles for updated episode where path or episode_file_id changed
                    logging.debug(f'BAZARR updating subtitles for episode '
                                  f'{path_mappings.path_replace(updated_episode["path"])}')
                    store_subtitles(previous_episode_id)
                else:
                    logging.debug(f'BAZARR skipping subtitle update for episode '
                                  f'{path_mappings.path_replace(updated_episode["path"])} as path and episode_file_id '
                                  f'unchanged')
                event_stream(type='episode', action='update', payload=previous_episode_id)

    # Downloading missing subtitles
    series_data = database.execute(
        select(TableShows.title,
               TableShows.year,
               TableShows.path)
        .where(TableShows.sonarrSeriesId == series_id)
    ).first()
    if not series_data:
        pass
    else:
        if defer_search:
            logging.debug(
                f'BAZARR searching for missing subtitles is deferred until scheduled task execution for this series: '
                f'{series_data.title} ({series_data.year})')
        else:
            for episode in episodes_to_update + episodes_to_add:
                episode_title = (f'{series_data.title} - S{episode["season"]:02d}E{episode["episode"]:02d} '
                                 f'- {episode["title"]}')
                if _is_there_missing_subtitles(episode_id=episode['sonarrEpisodeId']):
                    if os.path.exists(path_mappings.path_replace(episode['path'])):
                        logging.debug(f'BAZARR downloading missing subtitles for this episode: {episode_title}')
                        jobs_queue.feed_jobs_pending_queue(job_name=f'Downloading missing subtitles for '
                                                                    f'{episode_title}',
                                                           module='subtitles.mass_download.series',
                                                           func='episode_download_subtitles',
                                                           args=[],
                                                           kwargs={'no': episode['sonarrEpisodeId']},
                                                           is_signalr=is_signalr)
                    else:
                        logging.debug(f'BAZARR cannot find this episode file yet (Sonarr may be slow to import episode '
                                      f'between disks?). Searching for missing subtitles is deferred until scheduled '
                                      f'task execution for this episode: {episode_title}')
                else:
                    if is_signalr and general.notify_if_nothing_is_missing_for_signalr_event:
                        send_notifications(series_id, episode['sonarrEpisodeId'],
                                           "There are no missing subtitles in this episode.")
                    logging.debug(f'BAZARR no missing subtitles for this episode: {episode_title}')

    logging.debug(f'BAZARR All episodes from series ID {series_id} synced from Sonarr into database.')


def sync_one_episode(episode_id: int, defer_search: bool = False, is_signalr: bool = False) -> None:
    logging.debug(f'BAZARR syncing this specific episode from Sonarr: {episode_id}')

    # Check if there's a row in database for this episode ID
    existing_episode = database.execute(
        select(TableEpisodes.path, TableEpisodes.episode_file_id)
        .where(TableEpisodes.sonarrEpisodeId == episode_id)) \
        .first()

    with arr_session() as http:
        connection = get_sonarr_connection(session=http)
        if connection is None:
            return
        snapshot = _load_episode_snapshot(connection, episode_id=episode_id, session=http)
    del http
    if snapshot is None or not snapshot.episodes:
        logging.debug('BAZARR skipping episode sync because Sonarr returned an incomplete snapshot')
        return
    episode = episodeParser(snapshot.episodes[0], general=settings_cache.get(settings, 'general'))
    del snapshot

    if episode is None:
        if existing_episode is None:
            return
        try:
            deleted_episode_ids = delete_media_and_wanted_search_state(
                'series', TableEpisodes, 'sonarrEpisodeId', [episode_id],
            )
        except IntegrityError as e:
            logging.error(f"BAZARR cannot delete episode {existing_episode.path} because of {e}")
        else:
            if deleted_episode_ids:
                event_stream(type='episode', action='delete', payload=int(episode_id))
                logging.debug(
                    f'BAZARR deleted this episode from the database:{path_mappings.path_replace(existing_episode.path)}')
        return

    # Update existing episodes in DB
    if existing_episode is not None:
        try:
            episode['updated_at_timestamp'] = datetime.now()
            database.execute(
                update(TableEpisodes)
                .values(episode)
                .where(TableEpisodes.sonarrEpisodeId == episode_id))
        except IntegrityError as e:
            logging.error(f"BAZARR cannot update episode {episode['path']} because of {e}")
        else:
            if (existing_episode.episode_file_id != episode['episode_file_id'] or
                    existing_episode.path != episode['path']):
                # Store subtitles for updated episode where path or episode_file_id changed
                logging.debug(f'BAZARR updating subtitles for episode {path_mappings.path_replace(episode["path"])}')
                store_subtitles(episode_id)
            else:
                logging.debug(f'BAZARR skipping subtitle update for episode '
                              f'{path_mappings.path_replace(episode["path"])} as path and episode_file_id unchanged')
                defer_search = True

            event_stream(type='episode', action='update', payload=int(episode_id))
            logging.debug(
                f'BAZARR updated this episode into the database:{path_mappings.path_replace(episode["path"])}')

    # Insert new episodes in DB
    else:
        try:
            episode['created_at_timestamp'] = datetime.now()
            database.execute(
                insert(TableEpisodes)
                .values(episode))
        except IntegrityError as e:
            logging.error(f"BAZARR cannot insert episode {episode['path']} because of {e}")
        else:
            store_subtitles(episode_id)
            event_stream(type='episode', action='update', payload=int(episode_id))
            logging.debug(
                f'BAZARR inserted this episode into the database:{path_mappings.path_replace(episode["path"])}')

    # Downloading missing subtitles
    if defer_search:
        logging.debug(
            f'BAZARR searching for missing subtitles is deferred until scheduled task execution for this episode: '
            f'{path_mappings.path_replace(episode["path"])}')
    else:
        series_title = database.execute(
            select(TableShows.title)
            .where(TableShows.sonarrSeriesId == episode["sonarrSeriesId"])
        ).scalar()
        episode_full_title = (f'{series_title} - S{episode["season"]:02d}E{episode["episode"]:02d} - '
                              f'{episode["title"]}')

        if os.path.exists(path_mappings.path_replace(episode["path"])):
            logging.debug(f'BAZARR downloading missing subtitles for this episode: {episode_full_title}')
            if _is_there_missing_subtitles(episode_id=episode_id):
                jobs_queue.feed_jobs_pending_queue(job_name=f'Downloading missing subtitles for {series_title}',
                                                   module='subtitles.mass_download.series',
                                                   func='episode_download_subtitles',
                                                   args=[],
                                                   kwargs={'no': episode_id},
                                                   is_signalr=is_signalr)
            else:
                if is_signalr and settings_cache.get(settings, 'general').notify_if_nothing_is_missing_for_signalr_event:
                    send_notifications(episode["sonarrSeriesId"], episode_id,
                                       "There are no missing subtitles in this episode.")
                logging.debug(f'BAZARR no missing subtitles for this episode: {episode_full_title}')
        else:
            logging.debug(f'BAZARR cannot find this file yet (Sonarr may be slow to import episode between disks?). '
                          f'Searching for missing subtitles is deferred until scheduled task execution for this episode'
                          f': {episode_full_title}')


def _is_there_missing_subtitles(series_id: int | None = None, episode_id: int | None = None) -> bool:
    """
    Determines whether there are missing subtitles for a given series or episode.

    This function checks if there are missing subtitles based on the given
    series ID or episode ID. If both `series_id` and `episode_id` are provided,
    or if neither is provided, the function returns False. Otherwise, it evaluates
    the specified conditions to determine if subtitles are missing for the
    requested series or episode.

    :param series_id: The ID of the series to check for missing subtitles.
        Optional, defaults to None.
    :param episode_id: The ID of the episode to check for missing subtitles.
        Optional, defaults to None.
    :return: Boolean indicating whether there are missing subtitles (`True`)
        or not (`False`).
    :rtype: bool
    """
    episodes_conditions = [(TableMissingSubtitles.media_type == 'series'),
                           (TableMissingSubtitles.media_id == TableEpisodes.sonarrEpisodeId)]
    if all([series_id, episode_id]) or not any([series_id, episode_id]):
        return False
    elif series_id:
        episodes_conditions.append(TableEpisodes.sonarrSeriesId == series_id)
    elif episode_id:
        episodes_conditions.append(TableEpisodes.sonarrEpisodeId == episode_id)
    episodes_conditions += get_exclusion_clause('series')
    missing_episodes = database.execute(
        select(TableMissingSubtitles.language, TableEpisodes.sonarrEpisodeId)
        .select_from(TableEpisodes)
        .join(TableShows)
        .join(TableMissingSubtitles, TableMissingSubtitles.media_id == TableEpisodes.sonarrEpisodeId)
        .where(reduce(operator.and_, episodes_conditions))) \
        .all()
    due_languages_by_episode = get_due_missing_languages_map(
        'series',
        list(dict.fromkeys(missing_episode.sonarrEpisodeId for missing_episode in missing_episodes)),
    )
    for missing_episode in missing_episodes:
        due_languages = due_languages_by_episode.get(missing_episode.sonarrEpisodeId, [])
        if missing_episode.language in due_languages:
            return True
    return False
