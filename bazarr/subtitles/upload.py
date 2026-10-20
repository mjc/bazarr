# coding=utf-8

import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from subliminal_patch.core import save_subtitles
from subliminal_patch.subtitle import Subtitle
from subtitles.content import validate_subtitle
from subliminal_patch.score import MAX_SCORES

from languages.get_languages import resolve_language
from languages.formatting import LanguageInfo
from app.config import settings, get_array_from
from utilities.helper import get_target_folder, force_unicode
from utilities.post_processing import pp_replace, set_chmod
from utilities.path_mappings import path_mappings
from radarr.history import history_log_movie
from radarr.notify import notify_radarr
from sonarr.history import history_log
from sonarr.notify import notify_sonarr
from app.database import (TableEpisodes, TableMovies, TableShows, get_profile, get_audio_profile_languages,
                          database, select)
from app.jobs_queue import jobs_queue
from app.event_handler import event_stream
from app.notifier import send_notifications, send_notifications_movie
from subtitles.indexer.series import store_subtitles
from subtitles.indexer.movies import store_subtitles_movie
from subtitles.processing import ProcessSubtitlesResult
from plex.operations import plex_set_movie_added_date_now, plex_set_episode_added_date_now, plex_refresh_item
from jellyfin.operations import jellyfin_refresh_item

from .sync import sync_subtitles
from .post_processing import postprocessing


class UploadedSubtitle(Protocol):
    def getvalue(self) -> bytes: ...


class AudioLanguagePayload(TypedDict):
    name: str
    code2: str
    code3: str


@dataclass(frozen=True, slots=True)
class EpisodeUploadMetadata:
    sonarrSeriesId: int
    sonarrEpisodeId: int
    season: int
    episode: int
    profileId: int | None
    imdbId: str | None
    tvdbId: int | None


@dataclass(frozen=True, slots=True)
class MovieUploadMetadata:
    radarrId: int
    profileId: int | None
    imdbId: str | None
    tmdbId: str


def _load_metadata(media_type: Literal['series', 'movie'], episode_id: int | None,
                   movie_id: int | None) -> EpisodeUploadMetadata | MovieUploadMetadata | None:
    if media_type == 'series':
        row = database.execute(select(
            TableEpisodes.sonarrSeriesId, TableEpisodes.sonarrEpisodeId, TableEpisodes.season,
            TableEpisodes.episode, TableShows.profileId, TableShows.imdbId, TableShows.tvdbId,
        ).select_from(TableEpisodes).join(TableShows)
         .where(TableEpisodes.sonarrEpisodeId == episode_id)).first()
        return EpisodeUploadMetadata(*row) if row is not None else None
    row = database.execute(select(TableMovies.radarrId, TableMovies.profileId, TableMovies.imdbId, TableMovies.tmdbId)
                           .where(TableMovies.radarrId == movie_id)).first()
    return MovieUploadMetadata(*row) if row is not None else None


def _get_audio_language_payload(audio_languages: Sequence[LanguageInfo] | None) -> AudioLanguagePayload:
    first = audio_languages[0] if audio_languages else None
    return {'name': first['name'] or '' if first else '',
            'code2': first['code2'] or '' if first else '',
            'code3': first['code3'] or '' if first else ''}


def manual_upload_subtitle(
    path: str, language: str | None, forced: bool, hi: bool, media_type: Literal['series', 'movie'],
    subtitle: UploadedSubtitle, filename: str | None,
    audio_language: str | list[str | None] | tuple[str | None, ...] | None,
    job_id: int | None = None, sonarrSeriesId: int | None = None,
    sonarrEpisodeId: int | None = None, radarrId: int | None = None,
) -> int | tuple[str, int] | None:
    if not job_id:
        return jobs_queue.add_job_from_function(f'Uploading {filename}', is_progress=False)

    resolved = resolve_language(language)
    if resolved is None:
        logging.error('BAZARR subtitle upload language is unknown or incomplete: %s', language)
        return None
    metadata = _load_metadata(media_type, sonarrEpisodeId, radarrId)
    if metadata is None:
        return None
    profile = get_profile(metadata.profileId)
    use_original_format = profile.original_format if profile is not None else False
    general = settings.general
    chmod = int(general.chmod, 8) if not sys.platform.startswith('win') and general.chmod_enabled else None
    logging.debug('BAZARR Manually uploading subtitles: %s', filename)

    sub = Subtitle(resolved.to_language(forced=forced, hi=hi), mods=get_array_from(general.subzero_mods),
                   original_format=use_original_format)
    sub.content = subtitle.getvalue()
    if not validate_subtitle(sub):
        logging.error('BAZARR Invalid subtitle file: %s', filename)
        return None
    if general.utf8_encode:
        sub.set_encoding('utf-8')
    # Validation discovers the content's format; the filename may be wrong.
    formats = (sub.format,) if use_original_format else ('srt',)
    try:
        saved = save_subtitles(path, [sub], single=general.single_language, tags=None,
                               directory=get_target_folder(path), chmod=chmod, formats=formats,
                               path_decoder=force_unicode)
    except (OSError, UnicodeError, ValueError):
        logging.exception('BAZARR Error saving subtitles for %s', path)
        return None
    if not saved or not isinstance(subtitle_path := saved[0].storage_path, str) or not subtitle_path:
        logging.error('BAZARR Error saving subtitles for %s', path)
        return None

    modifier_string = ' HI' if hi else ' forced' if forced else ''
    modifier_code = ':hi' if hi else ':forced' if forced else ''
    uploaded_name = resolved.name + modifier_string
    uploaded_code2 = resolved.code2 + modifier_code
    uploaded_code3 = resolved.code3 + modifier_code
    if general.use_postprocessing:
        audio = _get_audio_language_payload(get_audio_profile_languages(audio_language))
        args = pp_replace(general.postprocessing_cmd, path, subtitle_path, uploaded_name, uploaded_code2,
                          uploaded_code3, audio['name'], audio['code2'], audio['code3'], 100, '1', 'manual',
                          'user', 'unknown', metadata.sonarrSeriesId if isinstance(metadata, EpisodeUploadMetadata) else None,
                          metadata.sonarrEpisodeId if isinstance(metadata, EpisodeUploadMetadata) else metadata.radarrId)
        postprocessing(args, path)
        set_chmod(subtitles_path=subtitle_path)

    if isinstance(metadata, EpisodeUploadMetadata):
        sync_subtitles(video_path=path, srt_path=subtitle_path, srt_lang=uploaded_code2, percent_score=100,
                       sonarr_series_id=metadata.sonarrSeriesId, forced=forced, hi=hi,
                       sonarr_episode_id=metadata.sonarrEpisodeId, job_id=job_id)
        reversed_path = path_mappings.path_replace_reverse(path)
        reversed_subtitles_path = path_mappings.path_replace_reverse(subtitle_path)
        notify_sonarr(metadata.sonarrSeriesId)
        event_stream(type='series', action='update', payload=metadata.sonarrSeriesId)
        event_stream(type='episode-wanted', action='delete', payload=metadata.sonarrEpisodeId)
    else:
        sync_subtitles(video_path=path, srt_path=subtitle_path, srt_lang=uploaded_code2, percent_score=100,
                       radarr_id=metadata.radarrId, forced=forced, hi=hi, job_id=job_id)
        reversed_path = path_mappings.path_replace_reverse_movie(path)
        reversed_subtitles_path = path_mappings.path_replace_reverse_movie(subtitle_path)
        notify_radarr(metadata.radarrId)
        event_stream(type='movie', action='update', payload=metadata.radarrId)
        event_stream(type='movie-wanted', action='delete', payload=metadata.radarrId)

    result = ProcessSubtitlesResult(message=f'{uploaded_name} Subtitles manually uploaded.', reversed_path=reversed_path,
                                    downloaded_language_code2=uploaded_code2, downloaded_provider=None, score=None,
                                    forced=None, subtitle_id=None, reversed_subtitles_path=reversed_subtitles_path,
                                    hearing_impaired=None)
    if isinstance(metadata, EpisodeUploadMetadata):
        store_subtitles(metadata.sonarrEpisodeId)
        history_log(4, metadata.sonarrSeriesId, metadata.sonarrEpisodeId, result,
                    fake_provider='manual', fake_score=MAX_SCORES['episode'])
        if not general.dont_notify_manual_actions:
            send_notifications(metadata.sonarrSeriesId, metadata.sonarrEpisodeId, result.message)
        if general.use_plex:
            plex = settings.plex
            if plex.update_series_library:
                plex_refresh_item(metadata.imdbId, is_movie=False, season=metadata.season, episode=metadata.episode)
            if plex.set_episode_added:
                plex_set_episode_added_date_now(metadata)
        if general.use_jellyfin and settings.jellyfin.update_series_library:
            jellyfin_refresh_item(metadata.imdbId, is_movie=False, season=metadata.season, episode=metadata.episode,
                                  tvdb_id=metadata.tvdbId)
    else:
        store_subtitles_movie(metadata.radarrId)
        history_log_movie(4, metadata.radarrId, result, fake_provider='manual', fake_score=MAX_SCORES['movie'])
        if not general.dont_notify_manual_actions:
            send_notifications_movie(metadata.radarrId, result.message)
        if general.use_plex:
            plex = settings.plex
            if plex.update_movie_library:
                plex_refresh_item(metadata.imdbId, is_movie=True)
            if plex.set_movie_added:
                plex_set_movie_added_date_now(metadata)
        if general.use_jellyfin and settings.jellyfin.update_movie_library:
            jellyfin_refresh_item(metadata.imdbId, is_movie=True, tmdb_id=metadata.tmdbId)
    return '', 204
