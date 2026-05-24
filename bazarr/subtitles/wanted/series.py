# coding=utf-8
# fmt: off

import logging
import operator
import gc

from functools import reduce

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

from ..adaptive_searching import get_adaptive_search_policy, update_failed_attempts
from ..download import generate_subtitles
from .utils import get_due_missing_languages


def _episode_needs_wanted_lookup_refresh(episode):
    return (
        episode.missing_subtitles is None or
        not getattr(episode, "has_indexed_subtitles", True) or
        getattr(episode, "has_incomplete_embedded_subtitles", False)
    )


def _wanted_episode(episode, providers_list, job_id=None, adaptive_search_policy=None):
    audio_language_list = get_audio_profile_languages(episode.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    due_missing_languages = get_due_missing_languages(
        episode.missing_subtitles,
        episode.failedAttempts,
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
    for result in generate_subtitles(path_mappings.path_replace(episode.path),
                                     languages,
                                     audio_language,
                                     str(episode.sceneName),
                                     episode.title,
                                     'series',
                                     episode.profileId,
                                     check_if_still_required=True,
                                     job_id=job_id,
                                     fallback_allowed=settings.general.use_whisper_fallback):
        if result:
            found_any = True
            if isinstance(result, tuple) and len(result):
                result = result[0]
            store_subtitles(episode.sonarrEpisodeId)
            history_log(1, episode.sonarrSeriesId, episode.sonarrEpisodeId, result)
            send_notifications(episode.sonarrSeriesId, episode.sonarrEpisodeId, result.message)
            event_stream(type='series', action='update', payload=episode.sonarrSeriesId)
            event_stream(type='episode-wanted', action='delete', payload=episode.sonarrEpisodeId)

    if not found_any and providers_list:
        database.execute(
            update(TableEpisodes)
            .values(failedAttempts=update_failed_attempts(due_missing_languages, episode.failedAttempts))
            .where(TableEpisodes.sonarrEpisodeId == episode.sonarrEpisodeId))


def wanted_download_subtitles(sonarr_episode_id, job_id=None, providers_list=None, adaptive_search_policy=None):
    stmt = select(TableEpisodes.path,
                  TableEpisodes.missing_subtitles,
                  TableEpisodes.sonarrEpisodeId,
                  TableEpisodes.sonarrSeriesId,
                  TableEpisodes.audio_language,
                  TableEpisodes.sceneName,
                  TableEpisodes.failedAttempts,
                  TableShows.title,
                  TableShows.profileId) \
        .select_from(TableEpisodes) \
        .join(TableShows) \
        .where((TableEpisodes.sonarrEpisodeId == sonarr_episode_id))
    episode_details = database.execute(stmt).first()

    previously_indexed_subtitles = get_subtitles(sonarr_episode_id=sonarr_episode_id)

    if not episode_details:
        logging.debug(f"BAZARR no episode with that sonarrId can be found in database: {sonarr_episode_id}")
        return
    elif not len(previously_indexed_subtitles) or \
            any([not x['embedded_track_id'] for x in previously_indexed_subtitles if not x['path']]):
        # subtitles indexing for this episode might be incomplete, we'll do it again
        store_subtitles(sonarr_episode_id)
        episode_details = database.execute(stmt).first()
    elif episode_details.missing_subtitles is None:
        # missing subtitles calculation for this episode is incomplete, we'll do it again
        list_missing_subtitles(epno=sonarr_episode_id)
        episode_details = database.execute(stmt).first()

    if providers_list is None:
        providers_list = get_providers()
    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    if providers_list:
        _wanted_episode(
            episode_details,
            providers_list,
            job_id=job_id,
            adaptive_search_policy=adaptive_search_policy,
        )
    else:
        logging.info("BAZARR All providers are throttled")


def wanted_search_missing_subtitles_series(job_id=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Searching for missing series subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    conditions = [(TableEpisodes.missing_subtitles.is_not(None)),
                  (TableEpisodes.missing_subtitles != '[]')]
    conditions += get_exclusion_clause('series')
    episodes = database.execute(
        select(TableEpisodes.sonarrSeriesId,
               TableEpisodes.sonarrEpisodeId,
               TableEpisodes.audio_language,
               TableEpisodes.failedAttempts,
               TableEpisodes.missing_subtitles,
               TableEpisodes.path,
               TableEpisodes.sceneName,
               TableShows.tags,
               TableEpisodes.monitored,
               TableShows.title,
               TableShows.profileId,
               TableEpisodes.season,
               TableEpisodes.episode,
               TableEpisodes.title.label('episodeTitle'),
               TableShows.seriesType,
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
               .label("has_incomplete_embedded_subtitles"))
        .select_from(TableEpisodes)
        .join(TableShows)
        .where(reduce(operator.and_, conditions))) \
        .all()

    count_episodes = len(episodes)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_episodes)

    if count_episodes == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')

    adaptive_search_policy = get_adaptive_search_policy()
    throttled = False
    for i, episode in enumerate(episodes, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i,
                                       progress_message=f'{episode.title} - S{episode.season:02d}E{episode.episode:02d}'
                                                         f' - {episode.episodeTitle}')

        providers = get_providers()
        if providers:
            if _episode_needs_wanted_lookup_refresh(episode):
                wanted_download_subtitles(
                    episode.sonarrEpisodeId,
                    job_id=job_id,
                    providers_list=providers,
                    adaptive_search_policy=adaptive_search_policy,
                )
            else:
                _wanted_episode(
                    episode,
                    providers,
                    job_id=job_id,
                    adaptive_search_policy=adaptive_search_policy,
                )

            # make sure to override the progress value updated by the subtitles synchronization
            jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_max=count_episodes)
        else:
            logging.info("BAZARR All providers are throttled")
            throttled = True
            break

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing series subtitles")
    logging.info('BAZARR Finished searching for missing Series Subtitles. Check History for more information.')

    gc.collect()
