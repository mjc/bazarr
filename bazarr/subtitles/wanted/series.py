# coding=utf-8
# fmt: off

import logging
import operator

from functools import reduce

from sqlalchemy import bindparam, case

from utilities.path_mappings import path_mappings
from subtitles.indexer.series import store_subtitles
from subtitles.indexer.series import list_missing_subtitles
from sonarr.history import history_log
from app.notifier import send_notifications
from app.get_providers import get_providers
from app.database import get_exclusion_clause, get_audio_profile_languages, TableShows, TableEpisodes, \
    TableEpisodesSubtitles, database, update, select, get_subtitles
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue
from app.config import settings

from ..adaptive_searching import get_adaptive_search_policy
from ..download import generate_subtitles
from subtitles.wanted_state import (
    get_due_missing_languages_map,
    get_due_missing_languages_for_media,
    get_missing_languages,
    record_failed_subtitle_attempts,
    record_failed_subtitle_attempts_map,
)
from .utils import get_language_search_items


_WANTED_EPISODE_DETAILS_SELECT = select(TableEpisodes.path,
                                        TableEpisodes.missing_subtitles,
                                        TableEpisodes.sonarrEpisodeId,
                                        TableEpisodes.sonarrSeriesId,
                                        TableEpisodes.audio_language,
                                        TableEpisodes.sceneName,
                                        TableEpisodes.failedAttempts,
                                        TableShows.title,
                                        TableShows.profileId,
                                        TableEpisodes.season,
                                        TableEpisodes.episode,
                                        TableEpisodes.title.label('episodeTitle'),
                                        select(TableEpisodesSubtitles.id)
                                        .where(TableEpisodesSubtitles.sonarrEpisodeId == TableEpisodes.sonarrEpisodeId)
                                        .limit(1)
                                        .exists()
                                        .label("has_indexed_subtitles"),
                                        select(TableEpisodesSubtitles.id)
                                        .where(TableEpisodesSubtitles.sonarrEpisodeId == TableEpisodes.sonarrEpisodeId)
                                        .where(TableEpisodesSubtitles.path.is_(None))
                                        .where(TableEpisodesSubtitles.embedded_track_id.is_(None))
                                        .limit(1)
                                        .exists()
                                        .label("has_incomplete_embedded_subtitles")) \
    .select_from(TableEpisodes) \
    .join(TableShows)
_WANTED_EPISODE_DETAILS_STMT = _WANTED_EPISODE_DETAILS_SELECT \
    .where(TableEpisodes.sonarrEpisodeId == bindparam("wanted_sonarr_episode_id"))
_FAILED_ATTEMPT_UPDATE_BATCH_SIZE = 5000
_TEMP_FAILED_ATTEMPT_UPDATE_MIN_SIZE = 1000
_DUE_EPISODE_DETAILS_BATCH_SIZE = 5000


def _episode_needs_wanted_lookup_refresh(episode):
    return (
        episode.missing_subtitles is None or
        not getattr(episode, "has_indexed_subtitles", True) or
        getattr(episode, "has_incomplete_embedded_subtitles", False)
    )


def _wanted_episode(episode, providers_list, due_languages=None, job_id=None, adaptive_search_policy=None,
                    fallback_allowed=None, defer_failed_attempts=False):
    audio_language_list = get_audio_profile_languages(episode.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    due_missing_languages = due_languages
    if due_missing_languages is None:
        due_missing_languages = get_due_missing_languages_for_media(
            'series',
            episode.sonarrEpisodeId,
            adaptive_search_policy=adaptive_search_policy,
        )
    if not due_missing_languages:
        return

    if fallback_allowed is None:
        fallback_allowed = settings.general.use_whisper_fallback

    languages = get_language_search_items(due_missing_languages)

    found_any = False
    for result in generate_subtitles(path_mappings.path_replace(episode.path),
                                     languages,
                                     audio_language,
                                     str(episode.sceneName),
                                     episode.title,
                                     'series',
                                     episode.profileId,
                                     check_if_still_required=True,
                                     job_id=job_id,
                                     fallback_allowed=fallback_allowed):
        if result:
            found_any = True
            if isinstance(result, tuple) and len(result):
                result = result[0]
            store_subtitles(episode.sonarrEpisodeId)
            history_log(1, episode.sonarrSeriesId, episode.sonarrEpisodeId, result)
            send_notifications(episode.sonarrSeriesId, episode.sonarrEpisodeId, result.message)
            event_stream(type='series', action='update', payload=episode.sonarrSeriesId)
            event_stream(type='episode-wanted', action='delete', payload=episode.sonarrEpisodeId)

    if providers_list:
        if found_any:
            refreshed_episode = database.execute(
                _WANTED_EPISODE_DETAILS_STMT,
                {"wanted_sonarr_episode_id": episode.sonarrEpisodeId},
            ).first()
            if not refreshed_episode:
                return

            current_missing_languages = set(get_missing_languages('series', episode.sonarrEpisodeId))
            remaining_due_languages = [
                language for language in due_missing_languages
                if language in current_missing_languages
            ]
        else:
            remaining_due_languages = due_missing_languages
        if not remaining_due_languages:
            return

        if defer_failed_attempts:
            return remaining_due_languages

        updated_attempts = record_failed_subtitle_attempts('series', episode.sonarrEpisodeId, remaining_due_languages)
        database.execute(
            update(TableEpisodes)
            .values(failedAttempts=updated_attempts)
            .where(TableEpisodes.sonarrEpisodeId == episode.sonarrEpisodeId))


def wanted_download_subtitles(
    sonarr_episode_id,
    job_id=None,
    providers_list=None,
    episode_details=None,
    due_languages=None,
    adaptive_search_policy=None,
    fallback_allowed=None,
    defer_failed_attempts=False,
):
    stmt_params = {"wanted_sonarr_episode_id": sonarr_episode_id}

    if episode_details is not None and due_languages is not None and not _episode_needs_wanted_lookup_refresh(episode_details):
        if providers_list is None:
            providers_list = get_providers()
        if adaptive_search_policy is None:
            adaptive_search_policy = get_adaptive_search_policy()
        if providers_list:
            return _wanted_episode(
                episode_details,
                providers_list,
                due_languages=due_languages,
                job_id=job_id,
                adaptive_search_policy=adaptive_search_policy,
                fallback_allowed=fallback_allowed,
                defer_failed_attempts=defer_failed_attempts,
            )
        else:
            logging.info("BAZARR All providers are throttled")
        return

    if episode_details is None:
        episode_details = database.execute(_WANTED_EPISODE_DETAILS_STMT, stmt_params).first()

    if not episode_details:
        logging.debug(f"BAZARR no episode with that sonarrId can be found in database: {sonarr_episode_id}")
        return
    if _episode_needs_wanted_lookup_refresh(episode_details):
        rebuilt_wanted_state = False
        previously_indexed_subtitles = get_subtitles(sonarr_episode_id=sonarr_episode_id)
        if not len(previously_indexed_subtitles) or \
                any(not x['embedded_track_id'] for x in previously_indexed_subtitles if not x['path']):
            # subtitles indexing for this episode might be incomplete, we'll do it again
            store_subtitles(sonarr_episode_id)
            episode_details = database.execute(_WANTED_EPISODE_DETAILS_STMT, stmt_params).first()
            if not episode_details:
                return
            rebuilt_wanted_state = True
        if episode_details.missing_subtitles is None:
            # missing subtitles calculation for this episode is incomplete, we'll do it again
            list_missing_subtitles(epno=sonarr_episode_id)
            rebuilt_wanted_state = True
        if rebuilt_wanted_state:
            episode_details = database.execute(_WANTED_EPISODE_DETAILS_STMT, stmt_params).first()
            if not episode_details:
                return
            due_languages = None
            providers_list = None

    if providers_list is None:
        providers_list = get_providers()
    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    if providers_list:
        return _wanted_episode(
            episode_details,
            providers_list,
            due_languages=due_languages,
            job_id=job_id,
            adaptive_search_policy=adaptive_search_policy,
            fallback_allowed=fallback_allowed,
            defer_failed_attempts=defer_failed_attempts,
        )
    else:
        logging.info("BAZARR All providers are throttled")


def _record_failed_episode_attempts(failed_attempt_languages):
    updated_attempts_by_episode = record_failed_subtitle_attempts_map(
        'series',
        failed_attempt_languages,
    )
    if not updated_attempts_by_episode:
        return

    update_items = list(updated_attempts_by_episode.items())
    if (
        len(update_items) >= _TEMP_FAILED_ATTEMPT_UPDATE_MIN_SIZE and
        database.bind.engine.dialect.name == 'sqlite'
    ):
        connection = database.connection()
        connection.exec_driver_sql('DROP TABLE IF EXISTS temp_failed_attempt_updates')
        connection.exec_driver_sql(
            'CREATE TEMP TABLE temp_failed_attempt_updates '
            '(media_id INTEGER PRIMARY KEY, failedAttempts TEXT NOT NULL)'
        )
        try:
            for index in range(0, len(update_items), _FAILED_ATTEMPT_UPDATE_BATCH_SIZE):
                connection.exec_driver_sql(
                    'INSERT INTO temp_failed_attempt_updates (media_id, failedAttempts) VALUES (?, ?)',
                    update_items[index:index + _FAILED_ATTEMPT_UPDATE_BATCH_SIZE],
                )
            connection.exec_driver_sql(
                'UPDATE table_episodes '
                'SET "failedAttempts" = ('
                'SELECT failedAttempts FROM temp_failed_attempt_updates '
                'WHERE media_id = table_episodes."sonarrEpisodeId") '
                'WHERE "sonarrEpisodeId" IN (SELECT media_id FROM temp_failed_attempt_updates)'
            )
        finally:
            connection.exec_driver_sql('DROP TABLE IF EXISTS temp_failed_attempt_updates')
        return

    id_column = TableEpisodes.__table__.c.sonarrEpisodeId
    for index in range(0, len(update_items), _FAILED_ATTEMPT_UPDATE_BATCH_SIZE):
        chunk = dict(update_items[index:index + _FAILED_ATTEMPT_UPDATE_BATCH_SIZE])
        database.execute(
            TableEpisodes.__table__.update()
            .where(id_column.in_(chunk))
            .values(failedAttempts=case(chunk, value=id_column))
        )


def wanted_search_missing_subtitles_series(job_id=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Searching for missing series subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    adaptive_search_policy = get_adaptive_search_policy()
    due_languages_by_episode = get_due_missing_languages_map(
        'series',
        adaptive_search_policy=adaptive_search_policy,
    )
    due_episode_ids = list(due_languages_by_episode)
    episodes = []
    if due_episode_ids:
        exclusion_clause = get_exclusion_clause('series')
        for index in range(0, len(due_episode_ids), _DUE_EPISODE_DETAILS_BATCH_SIZE):
            due_episode_id_chunk = due_episode_ids[index:index + _DUE_EPISODE_DETAILS_BATCH_SIZE]
            base_conditions = [TableEpisodes.sonarrEpisodeId.in_(due_episode_id_chunk)]
            base_conditions += exclusion_clause
            episodes.extend(
                database.execute(
                    _WANTED_EPISODE_DETAILS_SELECT
                    .where(reduce(operator.and_, base_conditions))
                ).all()
            )

    episodes_to_search = []
    fallback_allowed = settings.general.use_whisper_fallback
    for episode in episodes:
        due_languages = due_languages_by_episode[episode.sonarrEpisodeId]
        if due_languages:
            episodes_to_search.append((episode, due_languages))

    count_episodes = len(episodes_to_search)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_episodes)

    if count_episodes == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')
        throttled = False
    else:
        throttled = False

    failed_attempt_languages = {}
    for i, (episode, due_languages) in enumerate(episodes_to_search, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i,
                                       progress_message=f'{episode.title} - S{episode.season:02d}E{episode.episode:02d}'
                                                         f' - {episode.episodeTitle}')

        providers = get_providers()
        if providers:
            if _episode_needs_wanted_lookup_refresh(episode):
                remaining_due_languages = wanted_download_subtitles(
                    episode.sonarrEpisodeId,
                    job_id=job_id,
                    providers_list=providers,
                    episode_details=episode,
                    due_languages=due_languages,
                    adaptive_search_policy=adaptive_search_policy,
                    fallback_allowed=fallback_allowed,
                    defer_failed_attempts=True,
                )
                if remaining_due_languages:
                    failed_attempt_languages[episode.sonarrEpisodeId] = remaining_due_languages
            else:
                remaining_due_languages = _wanted_episode(
                    episode,
                    providers,
                    due_languages=due_languages,
                    job_id=job_id,
                    adaptive_search_policy=adaptive_search_policy,
                    fallback_allowed=fallback_allowed,
                    defer_failed_attempts=True,
                )
                if remaining_due_languages:
                    failed_attempt_languages[episode.sonarrEpisodeId] = remaining_due_languages

            # make sure to override the progress value updated by the subtitles synchronization
            jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_max=count_episodes)
        else:
            logging.info("BAZARR All providers are throttled")
            throttled = True
            break

    if failed_attempt_languages:
        _record_failed_episode_attempts(failed_attempt_languages)

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing series subtitles")
    logging.info('BAZARR Finished searching for missing Series Subtitles. Check History for more information.')
