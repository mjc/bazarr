# coding=utf-8
# fmt: off

import logging
import operator
import gc

from functools import reduce
from types import SimpleNamespace

from utilities.path_mappings import path_mappings
from subtitles.indexer.series import store_subtitles, list_missing_subtitles
from sonarr.history import history_log
from app.notifier import send_notifications
from app.get_providers import get_providers
from app.database import get_exclusion_clause, get_audio_profile_languages, TableShows, TableEpisodes, \
    TableEpisodesSubtitles, database, update, select, get_subtitles
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
_DUE_EPISODE_DETAILS_BATCH_SIZE = _SQLITE_BIND_PARAMETER_BATCH_SIZE


def _episode_needs_wanted_lookup_refresh(episode):
    return (
        episode.missing_subtitles is None or
        (
            not getattr(episode, "has_indexed_subtitles", True) and
            not parse_missing_subtitles(episode.missing_subtitles)
        ) or
        getattr(episode, "has_incomplete_embedded_subtitles", False)
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


def _wanted_episode(episode, providers_list, job_id=None, due_languages=None, fallback_allowed=None,
                    defer_failed_attempts=False):
    audio_language_list = get_audio_profile_languages(episode.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    if due_languages is None:
        missing_languages = parse_missing_subtitles(episode.missing_subtitles)
        languages_to_stamp = get_due_missing_languages(episode.missing_subtitles, episode.failedAttempts)
        for language in set(missing_languages) - set(languages_to_stamp):
            logging.debug(
                f"BAZARR Search is throttled by adaptive search for this episode {episode.path} and "
                f"language: {language}")
    else:
        languages_to_stamp = list(due_languages)

    if not languages_to_stamp:
        return

    if fallback_allowed is None:
        fallback_allowed = settings.general.use_whisper_fallback and settings.general.use_whisper_fallback_series

    languages = get_language_search_items(languages_to_stamp)
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
                select(TableEpisodes.missing_subtitles)
                .where(TableEpisodes.sonarrEpisodeId == episode.sonarrEpisodeId)) \
                .first()
            if not refreshed_episode:
                return

            current_missing_languages = set(parse_missing_subtitles(refreshed_episode.missing_subtitles))
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

        updated = _updated_failed_attempts(episode.failedAttempts, remaining_due_languages)
        database.execute(
            update(TableEpisodes)
            .values(failedAttempts=updated)
            .where(TableEpisodes.sonarrEpisodeId ==
                   episode.sonarrEpisodeId))


def wanted_download_subtitles(
    sonarr_episode_id,
    job_id=None,
    providers_list=None,
    episode_details=None,
    due_languages=None,
    fallback_allowed=None,
    defer_failed_attempts=False,
):
    stmt = select(TableEpisodes.path,
                  TableEpisodes.missing_subtitles,
                  TableEpisodes.sonarrEpisodeId,
                  TableEpisodes.sonarrSeriesId,
                  TableEpisodes.audio_language,
                  TableEpisodes.sceneName,
                  TableEpisodes.failedAttempts,
                  TableShows.title,
                  TableShows.profileId,
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
        .join(TableShows) \
        .where((TableEpisodes.sonarrEpisodeId == sonarr_episode_id))

    if episode_details is not None and due_languages is not None and not _episode_needs_wanted_lookup_refresh(episode_details):
        if providers_list is None:
            providers_list = get_providers()
        if providers_list:
            return _wanted_episode(
                episode_details,
                providers_list,
                job_id=job_id,
                due_languages=due_languages,
                fallback_allowed=fallback_allowed,
                defer_failed_attempts=defer_failed_attempts)
        else:
            logging.info("BAZARR All providers are throttled")
        return

    if episode_details is None:
        episode_details = database.execute(stmt).first()

    if not episode_details:
        logging.debug(f"BAZARR no episode with that sonarrId can be found in database: {sonarr_episode_id}")
        return
    if _episode_needs_wanted_lookup_refresh(episode_details):
        rebuilt_wanted_state = False
        previously_indexed_subtitles = get_subtitles(sonarr_episode_id=sonarr_episode_id)
        if not len(previously_indexed_subtitles) or \
                any([not x['embedded_track_id'] for x in previously_indexed_subtitles if not x['path']]):
            # subtitles indexing for this episode might be incomplete, we'll do it again
            store_subtitles(sonarr_episode_id)
            episode_details = database.execute(stmt).first()
            if not episode_details:
                return
            rebuilt_wanted_state = True
        if episode_details.missing_subtitles is None:
            # missing subtitles calculation for this episode is incomplete, we'll do it again
            list_missing_subtitles(epno=sonarr_episode_id)
            rebuilt_wanted_state = True
        if rebuilt_wanted_state:
            episode_details = database.execute(stmt).first()
            if not episode_details:
                return
            due_languages = None
            providers_list = None

    if providers_list is None:
        providers_list = get_providers()

    if providers_list:
        return _wanted_episode(
            episode_details,
            providers_list,
            job_id=job_id,
            due_languages=due_languages,
            fallback_allowed=fallback_allowed,
            defer_failed_attempts=defer_failed_attempts)
    else:
        logging.info("BAZARR All providers are throttled")


def _record_failed_episode_attempts(failed_attempt_languages):
    if not failed_attempt_languages:
        return

    pending_attempt_languages = dict(failed_attempt_languages)
    id_column = TableEpisodes.__table__.c.sonarrEpisodeId
    failed_attempts_column = TableEpisodes.__table__.c.failedAttempts
    for _ in range(3):
        current_attempts_by_episode = {}
        episode_ids = list(pending_attempt_languages)
        for episode_id_chunk in _iter_chunks(episode_ids, _FAILED_ATTEMPT_UPDATE_BATCH_SIZE):
            for row in database.execute(
                select(TableEpisodes.sonarrEpisodeId, TableEpisodes.failedAttempts)
                .where(TableEpisodes.sonarrEpisodeId.in_(episode_id_chunk))
            ).all():
                current_attempts_by_episode[row.sonarrEpisodeId] = row.failedAttempts

        retry_attempt_languages = {}
        for sonarr_episode_id, languages in pending_attempt_languages.items():
            current_attempts = current_attempts_by_episode.get(sonarr_episode_id)
            updated_attempts = _updated_failed_attempts(current_attempts, languages)
            result = database.execute(
                TableEpisodes.__table__.update()
                .where(id_column == sonarr_episode_id)
                .where(_failed_attempts_match(failed_attempts_column, current_attempts))
                .values(failedAttempts=updated_attempts)
            )
            if result.rowcount != 1:
                retry_attempt_languages[sonarr_episode_id] = languages

        if not retry_attempt_languages:
            return
        pending_attempt_languages = retry_attempt_languages


def wanted_search_missing_subtitles_series(job_id=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Searching for missing series subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    conditions = [(TableEpisodes.missing_subtitles.is_not(None)),
                  (TableEpisodes.missing_subtitles != '[]')]
    conditions += get_exclusion_clause('series')
    episode_rows = []

    episode_detail_select = select(TableEpisodes.sonarrSeriesId,
                                   TableEpisodes.sonarrEpisodeId,
                                   TableShows.tags,
                                   TableEpisodes.monitored,
                                   TableShows.title,
                                   TableEpisodes.season,
                                   TableEpisodes.episode,
                                   TableEpisodes.title.label('episodeTitle'),
                                   TableShows.seriesType,
                                   TableEpisodes.path,
                                   TableEpisodes.missing_subtitles,
                                   TableEpisodes.audio_language,
                                   TableEpisodes.sceneName,
                                   TableEpisodes.failedAttempts,
                                   TableShows.profileId,
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

    if supports_sqlite_wanted_search():
        due_languages = prepare_due_missing_subtitle_language_table(
            database,
            select(
                TableEpisodes.sonarrEpisodeId.label("media_id"),
                TableEpisodes.missing_subtitles,
                TableEpisodes.failedAttempts.label("failed_attempts"),
            )
            .select_from(TableEpisodes)
            .join(TableShows)
            .where(reduce(operator.and_, conditions)),
            "temp_due_episode_languages",
        )
        due_episode_ids = [
            row.media_id
            for row in database.execute(
                select(due_languages.c.media_id)
                .distinct()
                .order_by(due_languages.c.media_id)
            ).all()
        ]
        for due_episode_id_chunk in _iter_chunks(due_episode_ids, _DUE_EPISODE_DETAILS_BATCH_SIZE):
            episode_rows.extend(
                database.execute(
                    episode_detail_select
                    .add_columns(due_languages.c.language.label("due_language"))
                    .join(due_languages, due_languages.c.media_id == TableEpisodes.sonarrEpisodeId)
                    .where(TableEpisodes.sonarrEpisodeId.in_(due_episode_id_chunk))
                ).all()
            )
        episodes_by_id = {}
        for row in episode_rows:
            episode, languages = episodes_by_id.setdefault(row.sonarrEpisodeId, (
                SimpleNamespace(
                    sonarrSeriesId=row.sonarrSeriesId,
                    sonarrEpisodeId=row.sonarrEpisodeId,
                    tags=row.tags,
                    monitored=row.monitored,
                    title=row.title,
                    season=row.season,
                    episode=row.episode,
                    episodeTitle=row.episodeTitle,
                    seriesType=row.seriesType,
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
        episodes = list(episodes_by_id.values())
    else:
        episode_rows = database.execute(
            episode_detail_select
            .where(reduce(operator.and_, conditions))
            .order_by(TableEpisodes.sonarrEpisodeId)
        ).all()
        episodes = [
            (
                SimpleNamespace(
                sonarrSeriesId=row.sonarrSeriesId,
                sonarrEpisodeId=row.sonarrEpisodeId,
                tags=row.tags,
                monitored=row.monitored,
                title=row.title,
                season=row.season,
                episode=row.episode,
                episodeTitle=row.episodeTitle,
                seriesType=row.seriesType,
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
            for row in episode_rows
        ]

    count_episodes = len(episodes)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_episodes)

    if count_episodes == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')

    throttled = False
    fallback_allowed = settings.general.use_whisper_fallback and settings.general.use_whisper_fallback_series
    failed_attempt_languages = {}
    for i, (episode, due_languages) in enumerate(episodes, start=1):
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
                    fallback_allowed=fallback_allowed,
                    defer_failed_attempts=True)
            else:
                remaining_due_languages = _wanted_episode(
                    episode,
                    providers,
                    job_id=job_id,
                    due_languages=due_languages,
                    fallback_allowed=fallback_allowed,
                    defer_failed_attempts=True)
            if remaining_due_languages:
                failed_attempt_languages[episode.sonarrEpisodeId] = remaining_due_languages

            # make sure to override the progress value updated by the subtitles synchronization
            jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_max=count_episodes)
        else:
            logging.info("BAZARR All providers are throttled")
            throttled = True
            break

    _record_failed_episode_attempts(failed_attempt_languages)

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing series subtitles")
    logging.info('BAZARR Finished searching for missing Series Subtitles. Check History for more information.')

    gc.collect()
