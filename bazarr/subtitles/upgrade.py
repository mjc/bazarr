# coding=utf-8
# fmt: off

import logging
import operator

from collections.abc import Collection
from datetime import datetime, timedelta
from functools import reduce
from sqlalchemy import Select, and_, or_

from app.config import settings
from app.language_profiles import Profile
from app.database import (get_exclusion_clause, get_audio_profile_languages, TableShows, TableEpisodes, TableMovies,
     TableHistory, TableHistoryMovie, database, select, func, load_profile_catalog, TableEpisodesSubtitles,
     TableMoviesSubtitles)
from app.jobs_queue import jobs_queue, JobCanceled
from app.get_providers import get_providers
from app.notifier import send_notifications, send_notifications_movie
from radarr.history import history_log_movie
from sonarr.history import history_log
from subliminal_patch.score import MAX_SCORES
from subtitles.indexer.movies import store_subtitles_movie
from subtitles.indexer.series import store_subtitles
from utilities.path_mappings import path_mappings
from .download import generate_subtitles
from .language_utils import format_episode_part, resolve_audio_language
from app.event_handler import event_stream


def upgrade_subtitles(wait_for_completion=False):
    use_sonarr = settings.general.use_sonarr
    use_radarr = settings.general.use_radarr

    if use_sonarr:
        upgrade_episodes_subtitles(wait_for_completion=wait_for_completion)

    if use_radarr:
        upgrade_movies_subtitles(wait_for_completion=wait_for_completion)

    logging.info('BAZARR Finished searching for Subtitles to upgrade. Check History for more information.')


def upgrade_episodes_subtitles(job_id: int | None = None, wait_for_completion: bool = False) -> None:
    if not job_id:
        jobs_queue.add_job_from_function("Trying to upgrade episodes subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion, is_cancellable=True)
        return
    else:
        job = jobs_queue.get_job(job_id=job_id)

    profiles = load_profile_catalog()
    episodes_to_upgrade = get_upgradable_episode_subtitles()
    episodes_data = [{
        'id': x.id,
        'seriesTitle': x.seriesTitle,
        'season': x.season,
        'episode': x.episode,
        'title': x.title,
        'language': x.language,
        'audio_language': x.audio_language,
        'video_path': x.video_path,
        'sceneName': x.sceneName,
        'score': x.score,
        'score_out_of': x.score_out_of,
        'sonarrEpisodeId': x.sonarrEpisodeId,
        'sonarrSeriesId': x.sonarrSeriesId,
        'subtitles_path': x.subtitles_path,
        'profileId': x.profileId,
        'original_id': episodes_to_upgrade[x.id],
    } for x in database.execute(
        select(TableHistory.id,
               TableShows.title.label('seriesTitle'),
               TableEpisodes.season,
               TableEpisodes.episode,
               TableEpisodes.title,
               TableHistory.language,
               TableEpisodes.audio_language,
               TableHistory.video_path,
               TableEpisodes.sceneName,
               TableHistory.score,
               TableHistory.score_out_of,
               TableHistory.sonarrEpisodeId,
               TableHistory.sonarrSeriesId,
               TableHistory.subtitles_path,
               TableEpisodes.path,
               TableShows.profileId,
               TableEpisodesSubtitles.path.label('external_subtitles'))
        .select_from(TableHistory)
        .join(TableShows, onclause=TableHistory.sonarrSeriesId == TableShows.sonarrSeriesId)
        .join(TableEpisodes, onclause=TableHistory.sonarrEpisodeId == TableEpisodes.sonarrEpisodeId)
        .join(TableEpisodesSubtitles, onclause=TableHistory.sonarrEpisodeId == TableEpisodesSubtitles.sonarrEpisodeId, isouter=True)
        .where(TableEpisodesSubtitles.path.is_not(None)))
    .all() if x.id in episodes_to_upgrade
    and x.video_path == x.path
    and x.subtitles_path == x.external_subtitles
    and (profile := profiles.get(x.profileId)) is not None
    and x.language in profile.upgrade_languages]

    count_episode_to_upgrade = len(episodes_data)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_episode_to_upgrade)

    if count_episode_to_upgrade == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')

    for i, episode in enumerate(episodes_data, start=1):
        if job.cancel_event.is_set():
            raise JobCanceled

        profile = load_profile_catalog().get(episode['profileId'])
        if profile is None or episode['language'] not in profile.upgrade_languages:
            continue

        providers_list = get_providers()

        season_part = format_episode_part(episode["season"])
        episode_part = format_episode_part(episode["episode"])
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i,
                                       progress_message=f'{episode["seriesTitle"]} - S{season_part}E'
                                                        f'{episode_part} - {episode["title"]}')

        if not providers_list:
            logging.info("BAZARR All providers are throttled")
            return

        language, is_forced, is_hi = parse_language_string(episode['language'])
        if not language:
            continue
        if is_hi == 'True' and language not in profile.hi_required_languages:
            is_hi = 'False'

        audio_language = resolve_audio_language(get_audio_profile_languages(episode['audio_language']), fallback=None)

        minimum_score = _upgrade_minimum_score(
            episode['score'], settings.general.minimum_score, episode['score_out_of'] or MAX_SCORES['episode']
        )

        result = list(generate_subtitles(path_mappings.path_replace(episode['video_path']),
                                         [(language, is_hi, is_forced)],
                                         audio_language,
                                         episode['sceneName'],
                                         episode['seriesTitle'],
                                         'series',
                                         episode['profileId'],
                                         forced_minimum_score=minimum_score,
                                         is_upgrade=True,
                                         previous_subtitles_to_delete=path_mappings.path_replace(
                                             episode['subtitles_path']),
                                         job_id=job_id, profile=profile))

        if result:
            if isinstance(result, list) and len(result):
                result = result[0]
            store_subtitles(episode['sonarrEpisodeId'])
            history_log(3, episode['sonarrSeriesId'], episode['sonarrEpisodeId'], result,
                        upgraded_from_id=episode['original_id'] or episode['id'])  # we use or to handle None values on initial upgrade
            if hasattr(result, 'message'):
                send_notifications(episode['sonarrSeriesId'], episode['sonarrEpisodeId'], result.message)
            event_stream(type="episode-history")
    jobs_queue.update_job_name(job_id=job_id, new_job_name='Tried to upgrade episodes subtitles')


def upgrade_movies_subtitles(job_id: int | None = None, wait_for_completion: bool = False) -> None:
    if not job_id:
        jobs_queue.add_job_from_function("Trying to upgrade movies subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion, is_cancellable=True)
        return
    else:
        job = jobs_queue.get_job(job_id=job_id)

    profiles = load_profile_catalog()
    movies_to_upgrade = get_upgradable_movies_subtitles()
    movies_data = [{
        'id': x.id,
        'title': x.title,
        'language': x.language,
        'audio_language': x.audio_language,
        'video_path': x.video_path,
        'sceneName': x.sceneName,
        'score': x.score,
        'score_out_of': x.score_out_of,
        'radarrId': x.radarrId,
        'profileId': x.profileId,
        'subtitles_path': x.subtitles_path,
        'original_id': movies_to_upgrade[x.id],
    } for x in database.execute(
        select(TableHistoryMovie.id,
               TableMovies.title,
               TableHistoryMovie.language,
               TableMovies.audio_language,
               TableHistoryMovie.video_path,
               TableMovies.sceneName,
               TableHistoryMovie.score,
               TableHistoryMovie.score_out_of,
               TableHistoryMovie.radarrId,
               TableHistoryMovie.subtitles_path,
               TableMovies.path,
               TableMovies.profileId,
               TableMoviesSubtitles.path.label('external_subtitles'))
        .select_from(TableHistoryMovie)
        .join(TableMovies, onclause=TableHistoryMovie.radarrId == TableMovies.radarrId)
        .join(TableMoviesSubtitles, onclause=TableHistoryMovie.radarrId == TableMoviesSubtitles.radarrId, isouter=True)
        .where(TableMoviesSubtitles.path.is_not(None)))
    .all() if x.id in movies_to_upgrade
    and x.video_path == x.path
    and x.subtitles_path == x.external_subtitles
    and (profile := profiles.get(x.profileId)) is not None
    and x.language in profile.upgrade_languages]

    count_movie_to_upgrade = len(movies_data)
    jobs_queue.update_job_progress(job_id=job_id, progress_max=count_movie_to_upgrade)

    if count_movie_to_upgrade == 0:
        jobs_queue.update_job_progress(job_id=job_id, progress_value='max')

    for i, movie in enumerate(movies_data, start=1):
        if job.cancel_event.is_set():
            raise JobCanceled

        profile = load_profile_catalog().get(movie['profileId'])
        if profile is None or movie['language'] not in profile.upgrade_languages:
            continue

        providers_list = get_providers()

        jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_message=movie['title'])

        if not providers_list:
            logging.info("BAZARR All providers are throttled")
            return

        language, is_forced, is_hi = parse_language_string(movie['language'])
        if not language:
            continue
        if is_hi == 'True' and language not in profile.hi_required_languages:
            is_hi = 'False'

        audio_language = resolve_audio_language(get_audio_profile_languages(movie['audio_language']), fallback=None)

        minimum_score = _upgrade_minimum_score(
            movie['score'], settings.general.minimum_score_movie, movie['score_out_of'] or MAX_SCORES['movie']
        )

        result = list(generate_subtitles(path_mappings.path_replace_movie(movie['video_path']),
                                         [(language, is_hi, is_forced)],
                                         audio_language,
                                         movie['sceneName'],
                                         movie['title'],
                                         'movie',
                                         movie['profileId'],
                                         forced_minimum_score=minimum_score,
                                         is_upgrade=True,
                                         previous_subtitles_to_delete=path_mappings.path_replace_movie(
                                             movie['subtitles_path']),
                                         job_id=job_id, profile=profile))
        if result:
            if isinstance(result, list) and len(result):
                result = result[0]
            store_subtitles_movie(movie['radarrId'])
            history_log_movie(3, movie['radarrId'], result, upgraded_from_id=movie['original_id'] or movie['id'])  # we use or to handle None values on initial upgrade
            if hasattr(result, 'message'):
                send_notifications_movie(movie['radarrId'], result.message)
            event_stream(type="movie-history")
    jobs_queue.update_job_name(job_id=job_id, new_job_name='Tried to upgrade movies subtitles')


def get_queries_condition_parameters():
    days_to_upgrade_subs = settings.general.days_to_upgrade_subs
    minimum_timestamp = (datetime.now() - timedelta(days=int(days_to_upgrade_subs)))

    if settings.general.upgrade_manual:
        query_actions = [1, 2, 3, 4, 6]
    else:
        query_actions = [1, 3]

    return [minimum_timestamp, query_actions]


def parse_language_string(language_string):
    if not isinstance(language_string, str):
        return ['', "False", "False"]

    language_string = language_string.strip()
    if not language_string:
        return ['', "False", "False"]

    if language_string.endswith('forced'):
        language = language_string.split(':')[0]
        is_forced = "True"
        is_hi = "False"
    elif language_string.endswith('hi'):
        language = language_string.split(':')[0]
        is_forced = "False"
        is_hi = "True"
    else:
        language = language_string.split(':')[0]
        is_forced = "False"
        is_hi = "False"

    return [language, is_forced, is_hi]


def _upgrade_minimum_score(score, minimum_score, score_out_of):
    try:
        return max(int(score), int(round(minimum_score * score_out_of / 100, 0)))
    except (TypeError, ValueError):
        return None


def _load_upgradable_ids(
    candidates: Select[tuple[int, int | None, datetime]], history_id_list: Collection[int] | None,
) -> dict[int, int | None]:
    rows = candidates.cte('upgrade_candidates')
    # Upgrades outside the requested page still exclude their original events.
    upgraded_ids = select(rows.c.upgradedFromId).where(rows.c.upgradedFromId.is_not(None))
    statement = select(rows.c.id, rows.c.upgradedFromId).where(rows.c.id.not_in(upgraded_ids))
    remaining_ids: set[int] | None = None
    if history_id_list:
        if len(history_id_list) <= 500:
            statement = statement.where(rows.c.id.in_(history_id_list))
        else:
            # Unlimited history pages can exceed SQLite's bound-parameter limit.
            # Stream the two ID columns and keep only requested IDs in that case.
            remaining_ids = set(history_id_list)
    statement = statement.order_by(rows.c.timestamp.desc())
    return {history_id: parent_id or None for history_id, parent_id in database.execute(statement)
            if remaining_ids is None or history_id in remaining_ids}


def get_upgradable_episode_subtitles(history_id_list: Collection[int] | None = None) -> dict[int, int | None]:
    if not settings.general.upgrade_subs:
        # return an empty set of rows
        logging.debug("Subtitles upgrade is disabled so we wont go further.")
        return {}

    minimum_timestamp, query_actions = get_queries_condition_parameters()
    logging.debug(f"Minimum timestamp used for subtitles upgrade: {minimum_timestamp}")
    logging.debug(f"These actions are considered for subtitles upgrade: {query_actions}")

    logging.debug("Determining upgradable episode subtitles")
    last_since_delete_subquery = (
        select(TableHistory.video_path,
               TableHistory.language,
               func.max(TableHistory.id).label('last_delete_id'))
        .group_by(TableHistory.video_path, TableHistory.language)
        .where(TableHistory.action == 0)
        .subquery()
    )

    max_id_timestamp = select(TableHistory.video_path,
                              TableHistory.language,
                              func.max(TableHistory.timestamp).label('timestamp')) \
        .outerjoin(last_since_delete_subquery, onclause=and_(
            TableHistory.video_path == last_since_delete_subquery.c.video_path,
            TableHistory.language == last_since_delete_subquery.c.language)) \
        .group_by(TableHistory.video_path, TableHistory.language) \
        .where(and_(TableHistory.action.in_(query_actions),
                    or_(last_since_delete_subquery.c.last_delete_id.is_(None),
                        TableHistory.id > last_since_delete_subquery.c.last_delete_id))) \
        .distinct() \
        .subquery()

    upgradable_episodes_conditions = [(TableHistory.action.in_(query_actions)),
                                      (TableHistory.timestamp > minimum_timestamp),
                                      or_(and_(TableHistory.score.is_(None), TableHistory.action == 6),
                                      (TableHistory.score < TableHistory.score_out_of - 3))]
    upgradable_episodes_conditions += get_exclusion_clause('series')
    candidates = (
        select(TableHistory.id,
               TableHistory.upgradedFromId,
               TableHistory.timestamp)
        .select_from(TableHistory)
        .join(TableShows, onclause=TableHistory.sonarrSeriesId == TableShows.sonarrSeriesId)
        .join(TableEpisodes, onclause=TableHistory.sonarrEpisodeId == TableEpisodes.sonarrEpisodeId)
        .join(max_id_timestamp, onclause=and_(TableHistory.video_path == max_id_timestamp.c.video_path,
                                              TableHistory.language == max_id_timestamp.c.language,
                                              max_id_timestamp.c.timestamp == TableHistory.timestamp))
        .where(reduce(operator.and_, upgradable_episodes_conditions)))
    upgradable_episode_subtitles = _load_upgradable_ids(candidates, history_id_list)

    logging.debug(f"We've found {len(upgradable_episode_subtitles)} episode subtitles IDs to be upgradable")
    return upgradable_episode_subtitles


def get_upgradable_movies_subtitles(history_id_list: Collection[int] | None = None) -> dict[int, int | None]:
    if not settings.general.upgrade_subs:
        # return an empty set of rows
        logging.debug("Subtitles upgrade is disabled so we won't go further.")
        return {}

    minimum_timestamp, query_actions = get_queries_condition_parameters()
    logging.debug(f"Minimum timestamp used for subtitles upgrade: {minimum_timestamp}")
    logging.debug(f"These actions are considered for subtitles upgrade: {query_actions}")

    logging.debug("Determining upgradable movie subtitles")
    last_since_delete_subquery = (
        select(TableHistoryMovie.video_path,
               TableHistoryMovie.language,
               func.max(TableHistoryMovie.id).label('last_delete_id'))
        .group_by(TableHistoryMovie.video_path, TableHistoryMovie.language)
        .where(TableHistoryMovie.action == 0)
        .subquery()
    )

    max_id_timestamp = select(TableHistoryMovie.video_path,
                              TableHistoryMovie.language,
                              func.max(TableHistoryMovie.timestamp).label('timestamp')) \
        .outerjoin(last_since_delete_subquery, onclause=and_(
            TableHistoryMovie.video_path == last_since_delete_subquery.c.video_path,
            TableHistoryMovie.language == last_since_delete_subquery.c.language)) \
        .group_by(TableHistoryMovie.video_path, TableHistoryMovie.language) \
        .where(and_(TableHistoryMovie.action.in_(query_actions),
                    or_(last_since_delete_subquery.c.last_delete_id.is_(None),
                        TableHistoryMovie.id > last_since_delete_subquery.c.last_delete_id))) \
        .distinct() \
        .subquery()

    upgradable_movies_conditions = [(TableHistoryMovie.action.in_(query_actions)),
                                    (TableHistoryMovie.timestamp > minimum_timestamp),
                                    or_(and_(TableHistoryMovie.score.is_(None), TableHistoryMovie.action == 6),
                                    (TableHistoryMovie.score < TableHistoryMovie.score_out_of - 3))]
    upgradable_movies_conditions += get_exclusion_clause('movie')
    candidates = (
        select(TableHistoryMovie.id,
               TableHistoryMovie.upgradedFromId,
               TableHistoryMovie.timestamp)
        .select_from(TableHistoryMovie)
        .join(TableMovies, onclause=TableHistoryMovie.radarrId == TableMovies.radarrId)
        .join(max_id_timestamp, onclause=and_(TableHistoryMovie.video_path == max_id_timestamp.c.video_path,
                                              TableHistoryMovie.language == max_id_timestamp.c.language,
                                              max_id_timestamp.c.timestamp == TableHistoryMovie.timestamp))
        .where(reduce(operator.and_, upgradable_movies_conditions)))
    upgradable_movie_subtitles = _load_upgradable_ids(candidates, history_id_list)

    logging.debug(f"We've found {len(upgradable_movie_subtitles)} movie subtitles IDs to be upgradable")
    return upgradable_movie_subtitles
