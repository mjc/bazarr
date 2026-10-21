# coding=utf-8

import logging
import gc
from collections.abc import Iterator, Sequence
from itertools import islice

from sonarr.series_snapshot import Series

from sqlalchemy import bindparam
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import RowMapping
from datetime import datetime

from app.config import settings
from sonarr.rootfolder import check_sonarr_rootfolder
from app.database import TableShows, TableLanguagesProfiles, database, insert, update, select
from utilities.arr_sync_lookups import ArrSyncLookups, default_profile_id
from utilities.arr_types import KEEP_PROFILE
from utilities.arr_sync_state import SERIES_STATE, values_match
from utilities.helper import bool_map
from utilities.path_mappings import path_mappings
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue, JobCanceled

from .episodes import sync_episodes
from .parser import map_series
from .utils import get_profile_list, get_tags, get_series_from_sonarr_api

from subtitles.wanted_state import delete_series_and_wanted_search_state

FEATURE_PREFIX = "SYNC_SERIES "
SERIES_SYNC_BATCH_SIZE = 100


def trace(message: str) -> None:
    if settings.general.debug:
        logging.debug(FEATURE_PREFIX + message)


def get_language_profiles() -> Sequence[tuple[int, str, str | None]]:
    return database.execute(
        select(TableLanguagesProfiles.profileId, TableLanguagesProfiles.name, TableLanguagesProfiles.tag)).tuples().all()


def update_series(job_id: int | None = None, wait_for_completion: bool = False) -> None:
    if not job_id:
        jobs_queue.add_job_from_function("Syncing series with Sonarr", is_progress=True,
                                         wait_for_completion=wait_for_completion, is_cancellable=True)
        return
    else:
        job = jobs_queue.get_job(job_id=job_id)
        if job is None:
            return

    # Update root folders and update their health status
    check_sonarr_rootfolder()

    # Get shows data from Sonarr
    try:
        series = get_series_from_sonarr_api(apikey_sonarr=settings.sonarr.apikey)
    except Exception as e:
        logging.exception(f"BAZARR Error trying to get series from Sonarr: {e}")
        return
    else:
        if series is None:
            logging.debug('BAZARR Sonarr series sync returned an invalid payload. Skipping sync.')
            jobs_queue.update_job_name(job_id=job_id, new_job_name="Synced series with Sonarr")
            gc.collect()
            return

        # Get current shows in DB
        stored_series: dict[int, RowMapping] = {row['sonarrSeriesId']: row for row in database.execute(
            select(TableShows.sonarrSeriesId, TableShows.monitored)).mappings()}
        monitoring_loaded = True if settings.sonarr.sync_only_monitored_series else False
        series_count = len(series)
        skipped_count = 0
        identifiers: set[int] = set()

        audio_profiles = get_profile_list()
        tagsDict = get_tags()
        if audio_profiles is None or tagsDict is None:
            return
        language_profiles = get_language_profiles()
        lookups = ArrSyncLookups(tagsDict, language_profiles, audio_profiles)

        trace(f"Starting sync for {series_count} shows")

        jobs_queue.update_job_progress(job_id=job_id, progress_max=series_count)
        if series_count and job.cancel_event.is_set():
            raise JobCanceled

        def identified_series() -> Iterator[tuple[int, Series]]:
            for position, show in enumerate(series, start=1):
                if show is None or show.id in identifiers:
                    continue
                identifiers.add(show.id)
                yield position, show

        stored_query = SERIES_STATE.where(TableShows.sonarrSeriesId.in_(bindparam('series_ids', expanding=True)))
        records = identified_series()
        while batch := list(islice(records, SERIES_SYNC_BATCH_SIZE)):
            stored_batch = {row['sonarrSeriesId']: row for row in database.execute(
                stored_query, {'series_ids': [show.id for _, show in batch]}).mappings()}
            for position, show in batch:
                if job.cancel_event.is_set():
                    raise JobCanceled

                show_id = show.id
                jobs_queue.update_job_progress(job_id=job_id, progress_value=position, progress_message=show.title)
                initial = stored_series.get(show_id)
                initial_monitored: str | None = initial['monitored'] if initial is not None else None
                if (monitoring_loaded and settings.sonarr.sync_only_monitored_series and not show.monitored
                        and bool_map.get(initial_monitored or '') is False):
                    trace(f"{position}: (Skipped Unmonitored) {show.title}")
                    skipped_count += 1
                    continue

                trace(f"{position}: (Processing) {show.title}")
                # Pop metadata so the processed row is released before the episode sync.
                # The initial row also preserves UPDATE semantics if the show was deleted meanwhile.
                _sync_series(show, lookups, stored_batch.pop(show_id, initial))
                sync_episodes(series_id=show_id)

        # Calculate series to remove from DB
        invalid_count = series_count - len(identifiers)
        removed_series = (
            () if invalid_count else stored_series.keys() - identifiers
        )
        if invalid_count:
            logging.warning('BAZARR Skipping series deletion because Sonarr returned unidentified series.')

        for series_id in removed_series:
            # Remove series from DB
            update_one_series(series_id, action='deleted')

        if settings.sonarr.sync_only_monitored_series:
            trace(f"skipped {skipped_count + invalid_count} unmonitored series out of {series_count}")

        logging.debug('BAZARR All series synced from Sonarr into database.')

    jobs_queue.update_job_name(job_id=job_id, new_job_name="Synced series with Sonarr")

    gc.collect()


def update_one_series(series_id: int, action: str, is_signalr: bool = False,
                      sync_episodes_after_update: bool = True) -> None:
    logging.debug(f'BAZARR syncing this specific series from Sonarr: {series_id}')

    # Check if there's a row in database for this series ID
    existing_series = database.execute(
        SERIES_STATE
        .where(TableShows.sonarrSeriesId == series_id))\
        .mappings().first()

    # Delete series from DB
    if action == 'deleted' and existing_series:
        delete_series_and_wanted_search_state(series_id)

        event_stream(type='series', action='delete', payload=int(series_id))
        return

    if action != 'updated':
        return
    audio_profiles = get_profile_list()
    tagsDict = get_tags()
    if audio_profiles is None or tagsDict is None:
        return
    lookups = ArrSyncLookups(tagsDict, get_language_profiles(), audio_profiles)
    try:
        records = get_series_from_sonarr_api(apikey_sonarr=settings.sonarr.apikey, sonarr_series_id=series_id)
        if not records:
            return
    except Exception:
        logging.exception(f'BAZARR cannot get series with ID {series_id} from Sonarr API.')
        return
    for series_data in records:
        _sync_series(series_data, lookups, existing_series,
                     refresh_episodes=sync_episodes_after_update and not is_signalr)


def _sync_series(series_data: Series, lookups: ArrSyncLookups, existing_series: RowMapping | None,
                 *, refresh_episodes: bool = False) -> None:
    serie_default_profile = (default_profile_id(settings.general.serie_default_profile)
                             if settings.general.serie_default_enabled is True else None)
    tags = lookups.tags(series_data.tags)
    general = settings.general
    tag_enabled = general.serie_tag_enabled
    series = map_series(
        series_data, tags=tags,
        audio_language=('[]' if general.parse_embedded_audio_track else
                        lookups.audio_language_values.get(series_data.languageProfileId, '[]')),
        profile_id=lookups.profile_id(
            tags, KEEP_PROFILE if existing_series else serie_default_profile,
            enabled=tag_enabled, remove_tags=general.remove_profile_tags if tag_enabled else ()))

    if existing_series and values_match(existing_series, series):
        if refresh_episodes:
            sync_episodes(series_id=series['sonarrSeriesId'])
        return
    try:
        if existing_series:
            series['updated_at_timestamp'] = datetime.now()
            database.execute(
                update(TableShows)
                .values(series)
                .where(TableShows.sonarrSeriesId == series['sonarrSeriesId']))
        else:
            series['created_at_timestamp'] = datetime.now()
            database.execute(insert(TableShows).values(series))
    except IntegrityError as e:
        logging.error(f"BAZARR cannot {'update' if existing_series else 'insert'} series {series['path']} because of {e}")
        return
    if existing_series and refresh_episodes:
        # A manual refresh syncs episodes; SignalR schedules them separately.
        sync_episodes(series_id=series['sonarrSeriesId'])
    event_stream(type='series', action='update', payload=series['sonarrSeriesId'])
    logging.debug(f'BAZARR synced this series into the database:{path_mappings.path_replace(series["path"])}')
