# coding=utf-8

import os
import logging
import operator
from collections.abc import Collection, Sequence

from sonarr.episode_snapshot import Episode, EpisodeValues, episodes_with_files

from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import RowMapping
from datetime import datetime
from functools import reduce
from itertools import chain

from utilities.arr_files import FileSizes, is_usable_file
from utilities.arr_types import WriteOutcome
from utilities.arr_sync_state import EPISODE_STATE, values_match
from app.database import database, TableShows, TableEpisodes, TableMissingSubtitles, update, insert, select, \
    get_exclusion_clause
from app.config import settings
from utilities.helper import bool_map
from utilities.path_mappings import path_mappings
from subtitles.indexer.series import store_subtitles
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue
from app.notifier import send_notifications
from subtitles.wanted_state import delete_media_and_wanted_search_state, get_due_missing_languages_map

from .audio import episode_audio_language
from .parser import map_episode
from .utils import get_episodes_from_sonarr_api


FEATURE_PREFIX = "SYNC_EPISODES "


def trace(message: str) -> None:
    if settings.general.debug:
        logging.debug(FEATURE_PREFIX + message)


def _add_episode(episode: EpisodeValues) -> bool:
    try:
        episode['created_at_timestamp'] = datetime.now()
        database.execute(insert(TableEpisodes).values(episode))
    except IntegrityError as e:
        logging.error(f"BAZARR cannot insert episode {episode['path']} because of {e}")
        del episode['created_at_timestamp']
        return False
    store_subtitles(episode['sonarrEpisodeId'])
    event_stream(type='episode', action='update', payload=episode['sonarrEpisodeId'])
    return True


def _update_episode(episode: EpisodeValues, previous: RowMapping | None,
                    *, check_file_identity: bool = True) -> WriteOutcome:
    try:
        if previous is None:
            # An insert collided with a row created during this sync.
            previous = database.execute(select(TableEpisodes.episode_file_id, TableEpisodes.path)
                                        .where(TableEpisodes.sonarrEpisodeId == episode['sonarrEpisodeId'])).mappings().first()
        previous_file: tuple[int | None, str | None] = (
            (previous['episode_file_id'], previous['path']) if previous is not None else (None, None))
        file_changed = previous_file != (episode['episode_file_id'], episode['path'])
        episode['updated_at_timestamp'] = datetime.now()
        statement = update(TableEpisodes).values(episode).where(TableEpisodes.sonarrEpisodeId == episode['sonarrEpisodeId'])
        if check_file_identity:
            statement = statement.where(TableEpisodes.episode_file_id.is_not_distinct_from(previous_file[0]),
                                        TableEpisodes.path.is_not_distinct_from(previous_file[1]))
            result = database.connection().execute(statement)
            if result.rowcount == 0:
                return 'failed'
        else:
            database.execute(statement)
    except IntegrityError as e:
        logging.error(f"BAZARR cannot update episode {episode['path']} because of {e}")
        return 'failed'
    if file_changed:
        logging.debug(f'BAZARR updating subtitles for episode {path_mappings.path_replace(episode["path"])}')
        store_subtitles(episode['sonarrEpisodeId'])
    else:
        logging.debug(f'BAZARR skipping subtitle update for episode {path_mappings.path_replace(episode["path"])} '
                      f'as path and episode_file_id unchanged')
    event_stream(type='episode', action='update', payload=episode['sonarrEpisodeId'])
    return 'reindexed' if file_changed else 'updated'


def sync_episodes(series_id: int | None = None, defer_search: bool = False, is_signalr: bool = False,
                  *, episode_ids: Sequence[int] | None = None) -> None:
    selected = episode_ids is not None
    apikey_sonarr = settings.sonarr.apikey
    if not series_id and not episode_ids:
        return
    statement = (
        select(TableEpisodes.sonarrEpisodeId, TableEpisodes.episode_file_id, TableEpisodes.path)
        .where(TableEpisodes.sonarrEpisodeId.in_(episode_ids)) if episode_ids is not None else
        EPISODE_STATE.where(TableEpisodes.sonarrSeriesId == series_id))
    stored_episodes: dict[int, RowMapping] = {row['sonarrEpisodeId']: row for row in database.execute(statement).mappings()}
    parse_embedded_audio_track = not selected and settings.general.parse_embedded_audio_track is True
    episodes: list[Episode] | None
    if episode_ids is None:
        episodes = get_episodes_from_sonarr_api(apikey_sonarr=apikey_sonarr, series_id=series_id)
    else:
        episodes = []
        for identifier in dict.fromkeys(episode_ids):
            try:
                response = get_episodes_from_sonarr_api(apikey_sonarr=apikey_sonarr, episode_id=identifier)
            except Exception:
                logging.exception('BAZARR cannot get episode returned by SignalR feed from Sonarr API.')
                continue
            if response:
                episodes.extend(response)
            del response
    if not episodes:
        return

    audio_languages: dict[int, str] = {}
    file_sizes = FileSizes(path_mappings.path_replace)
    scope = (stored_episodes.keys() & {episode.id for episode in episodes}) if selected else stored_episodes.keys()
    current_episodes: set[int] = set()
    episodes_to_update: list[EpisodeValues] = []
    episodes_to_add: list[EpisodeValues] = []
    sync_monitored = (not selected and settings.sonarr.sync_only_monitored_series is True
                      and settings.sonarr.sync_only_monitored_episodes is True)
    episodes_monitored: dict[int | None, str | None] = (
        {row['episode_file_id']: row['monitored'] for row in stored_episodes.values()} if sync_monitored else {})
    skipped_count = 0
    for record, file in episodes_with_files(episodes):
        if sync_monitored and not record.monitored:
            monitored = episodes_monitored.get(file.id)
            if monitored is not None and bool_map.get(monitored) is False:
                current_episodes.add(record.id)
                skipped_count += 1
                continue
        try:
            if not is_usable_file(file.path, file_sizes.resolve(file.path, file.size)):
                continue
            parse_audio = settings.general.parse_embedded_audio_track is True if selected else parse_embedded_audio_track
            values = map_episode(record, file, audio_language=episode_audio_language(
                record, file, parse_audio,
                series_languages=audio_languages))
        except Exception:
            if not selected:
                raise
            logging.exception('BAZARR cannot get episode returned by SignalR feed from Sonarr API.')
            current_episodes.add(record.id)
            continue
        current_episodes.add(record.id)
        previous = stored_episodes.get(record.id)
        if previous is None:
            episodes_to_add.append(values)
        elif selected or not values_match(previous, values):
            episodes_to_update.append(values)

    if sync_monitored and settings.general.debug:
        series_title = database.execute(select(TableShows.title).where(TableShows.sonarrSeriesId == series_id)).scalar()
        trace(f"Skipped {skipped_count} unmonitored episodes out of {len(episodes)} for {series_title}")
    del episodes, audio_languages, file_sizes
    record = file = None
    episodes_to_delete = list(scope - current_episodes)
    if episodes_to_delete:
        try:
            removed: list[int] = delete_media_and_wanted_search_state('series', TableEpisodes, 'sonarrEpisodeId', episodes_to_delete)
        except IntegrityError as error:
            logging.error(f"BAZARR cannot delete episodes because of {error}")
        else:
            for identifier in removed:
                event_stream(type='episode', action='delete', payload=identifier)

    for values in episodes_to_add:
        if not _add_episode(values) and not selected:
            episodes_to_update.append(values)
    deferred: set[int] = set()
    written: list[EpisodeValues] = []
    for values in episodes_to_update:
        outcome = _update_episode(values, stored_episodes.get(values['sonarrEpisodeId']), check_file_identity=not selected)
        if selected and outcome == 'updated':
            deferred.add(values['sonarrEpisodeId'])
        if selected or outcome != 'failed':
            written.append(values)
    episodes_to_update = written
    if selected and defer_search:
        deferred.update(values['sonarrEpisodeId'] for values in chain(episodes_to_update, episodes_to_add))
    for values in chain(episodes_to_update, episodes_to_add):
        if values['sonarrEpisodeId'] in deferred:
            logging.debug('BAZARR searching for missing subtitles is deferred for episode: %s',
                          path_mappings.path_replace(values['path']))
    if selected and all(values['sonarrEpisodeId'] in deferred for values in chain(episodes_to_update, episodes_to_add)):
        return

    series_ids = ({values['sonarrSeriesId'] for values in chain(episodes_to_update, episodes_to_add)
                   if values['sonarrEpisodeId'] not in deferred} if selected else {series_id})
    shows = {row['sonarrSeriesId']: row for row in database.execute(
        select(TableShows.sonarrSeriesId, TableShows.title, TableShows.year)
        .where(TableShows.sonarrSeriesId.in_(series_ids))).mappings()}
    if not selected:
        show = shows.get(series_id)
        if show is None:
            return
        if defer_search:
            logging.debug(f'BAZARR searching for missing subtitles is deferred for series: '
                          f'{show["title"]} ({show["year"]})')
            return
    if not episodes_to_update and not episodes_to_add:
        return
    existing_paths = {values['sonarrEpisodeId']: os.path.exists(path_mappings.path_replace(values['path']))
                      for values in chain(episodes_to_update, episodes_to_add)
                      if values['sonarrEpisodeId'] not in deferred} if selected else {}
    due = (_get_due_missing_episode_ids(episode_ids={identifier for identifier, exists in existing_paths.items() if exists})
           if selected else _get_due_missing_episode_ids(series_id=series_id))
    for values in chain(episodes_to_update, episodes_to_add):
        identifier = values['sonarrEpisodeId']
        if identifier in deferred:
            continue
        show = shows.get(values['sonarrSeriesId'])
        title: str | None = show['title'] if show is not None else None
        episode_title = (f'{title} - S{values["season"]:02d}E{values["episode"]:02d} - {values["title"]}')
        exists = existing_paths[identifier] if selected else (
            os.path.exists(path_mappings.path_replace(values['path'])) if identifier in due else True)
        if not exists:
            logging.debug('BAZARR cannot find this episode file yet; searching is deferred: %s', episode_title)
        elif identifier in due:
            jobs_queue.feed_jobs_pending_queue(job_name=f'Downloading missing subtitles for {title if selected else episode_title}',
                                               module='subtitles.mass_download.series', func='episode_download_subtitles',
                                               args=[], kwargs={'no': identifier}, is_signalr=is_signalr)
        else:
            if is_signalr and settings.general.notify_if_nothing_is_missing_for_signalr_event:
                send_notifications(values['sonarrSeriesId'], identifier, "There are no missing subtitles in this episode.")
            logging.debug('BAZARR no missing subtitles for this episode: %s', episode_title)


def _get_due_missing_episode_ids(series_id: int | None = None,
                                  *, episode_ids: Collection[int] | None = None) -> set[int]:
    episodes_conditions = [(TableMissingSubtitles.media_type == 'series'),
                           (TableMissingSubtitles.media_id == TableEpisodes.sonarrEpisodeId)]
    if episode_ids is not None:
        if not episode_ids or series_id is not None:
            return set()
        episodes_conditions.append(TableEpisodes.sonarrEpisodeId.in_(episode_ids))
    elif series_id:
        episodes_conditions.append(TableEpisodes.sonarrSeriesId == series_id)
    else:
        return set()
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
    return {episode.sonarrEpisodeId for episode in missing_episodes
            if episode.language in due_languages_by_episode.get(episode.sonarrEpisodeId, [])}
