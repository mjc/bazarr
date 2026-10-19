# coding=utf-8

import logging
import gc
import requests
from dynaconf.utils.boxing import DynaBox
from sonarr.info import SonarrConnection, get_sonarr_connection
from sonarr.series_snapshot import SeriesSyncPlan, SeriesValues, is_series
from utilities.arr import arr_session
from utilities.arr_types import is_arr_tags

from sqlalchemy.exc import IntegrityError
from datetime import datetime

from app.config import settings
from app.settings_cache import settings_cache
from sonarr.rootfolder import check_sonarr_rootfolder
from app.database import TableShows, TableLanguagesProfiles, database, insert, update, select
from utilities.helper import bool_map, has_changed_fields
from utilities.path_mappings import path_mappings
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue, JobCanceled

from .episodes import sync_episodes
from .parser import seriesParser
from .utils import get_profile_list, get_tags, get_series_from_sonarr_api

from subtitles.wanted_state import delete_series_and_wanted_search_state

FEATURE_PREFIX = "SYNC_SERIES "


def trace(message: str) -> None:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug('%s%s', FEATURE_PREFIX, message)


def get_language_profiles():
    return database.execute(
        select(TableLanguagesProfiles.profileId, TableLanguagesProfiles.name, TableLanguagesProfiles.tag)).all()


def get_series_monitored_table():
    series_monitored = database.execute(
        select(TableShows.sonarrSeriesId, TableShows.monitored))\
        .all()
    series_dict = dict((x, y) for x, y in series_monitored)
    return series_dict


def _default_series_profile(general: DynaBox) -> int | None:
    if general.serie_default_enabled is not True:
        return None
    profile = general.serie_default_profile
    if profile is None or profile == '':
        return None
    try:
        return int(profile)
    except (ValueError, TypeError):
        return None


def _prepare_series_sync(connection: SonarrConnection, sonarr: DynaBox,
                         general: DynaBox, *, session: requests.Session | None = None) -> SeriesSyncPlan | None:
    try:
        payload = get_series_from_sonarr_api(apikey_sonarr=connection.apikey, connection=connection, session=session)
    except requests.exceptions.RequestException:
        logging.exception('BAZARR Error trying to get series from Sonarr')
        return None
    if not isinstance(payload, list):
        return None
    current = database.execute(select(TableShows.sonarrSeriesId, TableShows.monitored)).all()
    existing_ids = {row.sonarrSeriesId for row in current}
    monitored = {row.sonarrSeriesId: row.monitored for row in current}
    if not payload:
        return SeriesSyncPlan([], set(), existing_ids, True, 0, 0)
    audio_profiles = get_profile_list(connection=connection, session=session)
    tags = get_tags(connection=connection, session=session)
    if audio_profiles is None or not is_arr_tags(tags):
        return None
    language_profiles = get_language_profiles()
    retained: set[int] = set()
    updates: list[SeriesValues] = []
    complete = True
    skipped = 0
    for raw in payload:
        if not is_series(raw) or raw['id'] <= 0 or raw['id'] in retained:
            complete = False
            skipped += 1
            continue
        series_id = raw['id']
        retained.add(series_id)
        previous_monitored = monitored.get(series_id)
        was_monitored = None if previous_monitored is None else bool_map.get(previous_monitored)
        if sonarr.sync_only_monitored_series and was_monitored is False and raw['monitored'] is False:
            skipped += 1
            continue
        try:
            values = seriesParser(raw, action='update',
                                  tags_dict=tags, language_profiles=language_profiles,
                                  serie_default_profile=None, audio_profiles=audio_profiles,
                                  general=general, capabilities=connection.capabilities)
        except (ValueError, OverflowError):
            complete = False
            skipped += 1
            continue
        updates.append(values)
    return SeriesSyncPlan(updates, retained, existing_ids, complete, len(payload), skipped)


def update_series(job_id: int | None = None, wait_for_completion: bool = False) -> None:
    if not job_id:
        jobs_queue.add_job_from_function('Syncing series with Sonarr', is_progress=True,
                                         wait_for_completion=wait_for_completion, is_cancellable=True)
        return
    job = jobs_queue.get_job(job_id=job_id)
    if job is None:
        return
    with arr_session() as http:
        sonarr = settings_cache.get(settings, 'sonarr')
        general = settings_cache.get(settings, 'general')
        connection = get_sonarr_connection(sonarr, session=http)
        if connection is None:
            return
        check_sonarr_rootfolder(connection=connection, session=http)
        plan = _prepare_series_sync(connection, sonarr, general, session=http)
        if plan is None:
            logging.debug('BAZARR Sonarr series sync returned an invalid payload. Skipping sync.')
            jobs_queue.update_job_name(job_id=job_id, new_job_name='Synced series with Sonarr')
            return
        jobs_queue.update_job_progress(job_id=job_id, progress_max=plan.series_count)
        plan.updates.reverse()
        processed = 0
        while plan.updates:
            if job.cancel_event.is_set():
                raise JobCanceled
            series = plan.updates.pop()
            processed += 1
            series_id = series['sonarrSeriesId']
            jobs_queue.update_job_progress(job_id=job_id, progress_value=processed, progress_message=series['title'])
            update_one_series(series_id, action='updated', sync_episodes_after_update=False,
                              prepared_series=series, connection=connection, general=general, session=http)
            del series
            sync_episodes(series_id=series_id, connection=connection, session=http)
        if plan.complete:
            for series_id in plan.existing_ids - plan.retained_ids:
                update_one_series(series_id, action='deleted')
        else:
            logging.warning('BAZARR Skipping series deletion because Sonarr returned incomplete series.')
        if sonarr.sync_only_monitored_series:
            trace(f'skipped {plan.skipped_count} unmonitored series out of {plan.series_count}')
        jobs_queue.update_job_name(job_id=job_id, new_job_name='Synced series with Sonarr')
        logging.debug('BAZARR All series synced from Sonarr into database.')
        gc.collect()


def update_one_series(series_id: int | None, action: str, is_signalr: bool = False,
                      sync_episodes_after_update: bool = True, series_data=None,
                      audio_profiles=None, tagsDict=None, language_profiles=None,
                      *, prepared_series: SeriesValues | None = None,
                      connection: SonarrConnection | None = None, general: DynaBox | None = None,
                      session: requests.Session | None = None) -> None:
    if series_id is None or series_id <= 0:
        return
    logging.debug(f'BAZARR syncing this specific series from Sonarr: {series_id}')

    # Check if there's a row in database for this series ID
    existing_series = database.execute(
        select(*TableShows.__table__.columns)
        .where(TableShows.__table__.c.sonarrSeriesId == series_id)).mappings().first()

    # Delete series from DB
    if action == 'deleted':
        if existing_series:
            delete_series_and_wanted_search_state(series_id)
            event_stream(type='series', action='delete', payload=series_id)
        return

    with arr_session(session) as http:
        general_settings = settings_cache.get(settings, 'general') if general is None else general
        connection = get_sonarr_connection(session=http) if connection is None else connection
        if connection is None:
            return
        if prepared_series is None:
            if audio_profiles is None:
                audio_profiles = get_profile_list(connection=connection, session=http)
            if tagsDict is None:
                tagsDict = get_tags(connection=connection, session=http)
            if language_profiles is None:
                language_profiles = get_language_profiles()
            if audio_profiles is None or not is_arr_tags(tagsDict):
                return
            if series_data is None:
                try:
                    series_data = get_series_from_sonarr_api(apikey_sonarr=connection.apikey,
                                                             sonarr_series_id=series_id, connection=connection, session=http)
                except requests.exceptions.RequestException:
                    logging.exception('BAZARR cannot get series with ID %s from Sonarr API.', series_id)
                    return
            if not isinstance(series_data, list) or len(series_data) != 1:
                return
            raw_series = series_data[0]
            if not is_series(raw_series) or raw_series['id'] != series_id:
                return
            try:
                prepared_series = seriesParser(raw_series, action='update' if existing_series else 'insert',
                                               tags_dict=tagsDict, language_profiles=language_profiles,
                                               serie_default_profile=_default_series_profile(general_settings),
                                               audio_profiles=audio_profiles, general=general_settings,
                                               capabilities=connection.capabilities)
            except (ValueError, OverflowError):
                return
        series = prepared_series

        if action == 'updated' and existing_series:
            # Update existing series in DB
            if not has_changed_fields(existing_series, series):
                if sync_episodes_after_update and not is_signalr:
                    sync_episodes(series_id=series_id, connection=connection, session=http)
                return

            try:
                series['updated_at_timestamp'] = datetime.now()
                database.execute(
                    update(TableShows)
                    .values(series)
                    .where(TableShows.sonarrSeriesId == series['sonarrSeriesId']))
            except IntegrityError as e:
                logging.error(f"BAZARR cannot update series {series['path']} because of {e}")
            else:
                if sync_episodes_after_update and not is_signalr:
                    # Sonarr emit two SignalR events when episodes must be refreshed.
                    # The one that gets there doesn't include the episodeChanged flag.
                    # The episodes are synced only when this function is called from the
                    # frontend sync button in the episodes' page.
                    sync_episodes(series_id=series_id, connection=connection, session=http)
                event_stream(type='series', action='update', payload=int(series_id))
                logging.debug(
                    f'BAZARR updated this series into the database:{path_mappings.path_replace(series["path"])}')
        elif action == 'updated' and not existing_series:
            # The default profile applies only when inserting, including after preparation.
            if 'profileId' not in series:
                series['profileId'] = _default_series_profile(general_settings)
            try:
                series['created_at_timestamp'] = datetime.now()
                database.execute(
                    insert(TableShows)
                    .values(series))
            except IntegrityError as e:
                logging.error(f"BAZARR cannot insert series {series['path']} because of {e}")
            else:
                event_stream(type='series', action='update', payload=int(series_id))
                logging.debug(
                    f'BAZARR inserted this series into the database:{path_mappings.path_replace(series["path"])}')
