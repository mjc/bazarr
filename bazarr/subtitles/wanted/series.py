# coding=utf-8
# fmt: off

import logging
import operator

from functools import reduce

from utilities.path_mappings import path_mappings
from subtitles.indexer.series import store_subtitles, list_missing_subtitles
from sonarr.history import history_log
from app.notifier import send_notifications
from app.get_providers import get_providers
from app.database import get_exclusion_clause, get_audio_profile_languages, TableShows, TableEpisodes, database, \
    update, select
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue

from ..adaptive_searching import updateFailedAttempts
from ..download import generate_subtitles
from .utils import get_due_missing_languages, get_language_search_items


def _wanted_episode(episode, providers_list, due_languages=None, job_id=None):
    audio_language_list = get_audio_profile_languages(episode.audio_language)
    if len(audio_language_list) > 0:
        audio_language = audio_language_list[0]['name']
    else:
        audio_language = 'None'

    languages_to_stamp = due_languages
    if languages_to_stamp is None:
        languages_to_stamp = get_due_missing_languages(episode.missing_subtitles, episode.failedAttempts)
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
                                     job_id=job_id):
        if result:
            found_any = True
            if isinstance(result, tuple) and len(result):
                result = result[0]
            store_subtitles(episode.path, path_mappings.path_replace(episode.path))
            history_log(1, episode.sonarrSeriesId, episode.sonarrEpisodeId, result)
            event_stream(type='series', action='update', payload=episode.sonarrSeriesId)
            event_stream(type='episode-wanted', action='delete', payload=episode.sonarrEpisodeId)
            send_notifications(episode.sonarrSeriesId, episode.sonarrEpisodeId, result.message)

    if not found_any and providers_list:
        for language in languages_to_stamp:
            updated = updateFailedAttempts(
                desired_language=language,
                attempt_string=episode.failedAttempts)
            database.execute(
                update(TableEpisodes)
                .values(failedAttempts=updated)
                .where(TableEpisodes.sonarrEpisodeId ==
                       episode.sonarrEpisodeId))


def wanted_download_subtitles(sonarr_episode_id, job_id=None, providers_list=None, episode_details=None,
                              due_languages=None):
    stmt = None

    def _get_stmt():
        nonlocal stmt
        if stmt is None:
            stmt = select(TableEpisodes.path,
                          TableEpisodes.missing_subtitles,
                          TableEpisodes.sonarrEpisodeId,
                          TableEpisodes.sonarrSeriesId,
                          TableEpisodes.audio_language,
                          TableEpisodes.sceneName,
                          TableEpisodes.failedAttempts,
                          TableShows.title,
                          TableShows.profileId,
                          TableEpisodes.subtitles) \
                .select_from(TableEpisodes) \
                .join(TableShows) \
                .where((TableEpisodes.sonarrEpisodeId == sonarr_episode_id))
        return stmt

    if episode_details is None:
        episode_details = database.execute(_get_stmt()).first()

    if not episode_details:
        logging.debug(f"BAZARR no episode with that sonarrId can be found in database: {sonarr_episode_id}")
        return
    elif episode_details.subtitles is None:
        # subtitles indexing for this episode is incomplete, we'll do it again
        store_subtitles(episode_details.path, path_mappings.path_replace(episode_details.path))
        episode_details = database.execute(_get_stmt()).first()
        due_languages = None
    elif episode_details.missing_subtitles is None:
        # missing subtitles calculation for this episode is incomplete, we'll do it again
        list_missing_subtitles(epno=sonarr_episode_id)
        episode_details = database.execute(_get_stmt()).first()
        due_languages = None

    if providers_list is None:
        providers_list = get_providers()

    if providers_list:
        _wanted_episode(episode_details, providers_list, due_languages=due_languages, job_id=job_id)
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
        select(TableEpisodes.path,
               TableEpisodes.sonarrSeriesId,
               TableEpisodes.sonarrEpisodeId,
               TableEpisodes.audio_language,
               TableEpisodes.sceneName,
               TableEpisodes.failedAttempts,
               TableShows.title,
               TableShows.profileId,
               TableEpisodes.season,
               TableEpisodes.episode,
               TableEpisodes.title.label('episodeTitle'),
               TableEpisodes.missing_subtitles,
               TableEpisodes.subtitles)
        .select_from(TableEpisodes)
        .join(TableShows)
        .where(reduce(operator.and_, conditions))) \
        .all()

    episodes_to_search = []
    for episode in episodes:
        due_languages = get_due_missing_languages(episode.missing_subtitles, episode.failedAttempts)
        if due_languages:
            episodes_to_search.append((episode, due_languages))

    count_episodes = len(episodes_to_search)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_episodes)

    if count_episodes == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')
        throttled = False
        providers = None
    else:
        providers = get_providers()
        throttled = not providers
        if throttled:
            logging.info("BAZARR All providers are throttled")

    for i, (episode, due_languages) in enumerate(episodes_to_search, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i,
                                       progress_message=f'{episode.title} - S{episode.season:02d}E{episode.episode:02d}'
                                                        f' - {episode.episodeTitle}')

        if providers:
            wanted_download_subtitles(episode.sonarrEpisodeId,
                                      job_id=job_id,
                                      providers_list=providers,
                                      episode_details=episode,
                                      due_languages=due_languages)
        else:
            break

    outcome_msg = ("All providers throttled" if throttled
                   else "Search completed")
    jobs_queue.update_job_progress(job_id=job_id, progress_message=outcome_msg)
    jobs_queue.update_job_name(job_id=job_id, new_job_name="Searched for missing series subtitles")
    logging.info('BAZARR Finished searching for missing Series Subtitles. Check History for more information.')
