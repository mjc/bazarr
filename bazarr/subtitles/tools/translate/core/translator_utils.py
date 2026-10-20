# coding=utf-8

import logging
import os
import srt
import datetime

from typing import Union

from .translation_request import TranslationRequest, TranslationTarget
from subzero.language import Language
from subliminal_patch.score import MAX_SCORES
from languages.get_languages import alpha3_from_alpha2, resolve_language
from subtitles.processing import ProcessSubtitlesResult
from utilities.path_mappings import path_mappings

from app.database import TableShows, TableEpisodes, TableMovies, database, select

logger = logging.getLogger(__name__)


def validate_translation_params(video_path: str, source_srt_file: str, from_lang: str, to_lang: str) -> bool:
    """Validate translation parameters."""
    if not os.path.exists(source_srt_file):
        raise FileNotFoundError(f"Source subtitle file not found: {source_srt_file}")

    if not from_lang or not to_lang:
        raise ValueError("Source and target languages must be specified")

    return True


def resolve_translation_source(subtitles, subtitles_path, subtitles_id, language_validator=alpha3_from_alpha2):
    """
    Resolve the source language for a translate request from the subtitles list returned by
    `get_subtitles(...)`.

    A translate target is either an external file (identified by its `path`) or an embedded track
    (identified by its subtitles database `id`). For the embedded case the matched track id is
    returned so the caller can extract it to an external file before translating.

    :param language_validator: callable used to validate the resolved language code2. Defaults to
        `alpha3_from_alpha2`; injected mainly for unit testing.
    :return: tuple ``(from_language, embedded_subtitle_id)`` where ``embedded_subtitle_id`` is
        ``None`` for an external subtitle.
    :raises ValueError: with an API-friendly message if the source cannot be identified, is not a
        valid language, or the supplied id does not point to an embedded track.
    """
    if subtitles_path:
        for external_subtitles in subtitles:
            if external_subtitles['path'] == subtitles_path:
                from_language = external_subtitles['code2']
                if from_language and language_validator(from_language):
                    return from_language, None
                break
    elif subtitles_id is not None:
        for embedded_subtitles in subtitles:
            if embedded_subtitles['id'] == subtitles_id:
                if embedded_subtitles.get('embedded_track_id') is None:
                    raise ValueError('Selected subtitle is not an embedded track')
                from_language = embedded_subtitles['code2']
                if from_language and language_validator(from_language):
                    return from_language, subtitles_id
                break

    raise ValueError('Invalid source language code')


# Subtitle extensions that Bazarr can produce a translated file in. The Google and Lingarr
# translators go through pysubs2, which honours the destination path extension; the Gemini
# translator only handles SRT (see get_translation_extension below).
SUPPORTED_TRANSLATION_EXTENSIONS = ('.srt', '.ass', '.ssa', '.vtt')


def get_translation_extension(source_srt_file: str, original_format: bool, translator_type: str) -> str:
    """
    Return the extension to use for the translated subtitle file.

    When ``original_format`` is true and the source has a supported text-based extension, that
    extension is preserved so a ``.ass``/``.vtt`` source is translated into a ``.ass``/``.vtt``
    file instead of always being converted to ``.srt``. The Gemini translator reads and writes
    SRT only, so for any non-SRT source it falls back to ``.srt``.
    """
    if not original_format:
        return '.srt'

    src_ext = os.path.splitext(source_srt_file)[1].lower()
    if src_ext not in SUPPORTED_TRANSLATION_EXTENSIONS:
        return '.srt'

    if translator_type == 'gemini' and src_ext != '.srt':
        logger.info(f'Gemini translator cannot preserve the {src_ext} format; falling back to .srt '
                    f'for {source_srt_file}')
        return '.srt'

    return src_ext


def convert_language_codes(to_lang: str, forced: bool = False, hi: bool = False) -> tuple[Language, TranslationTarget]:
    """Convert and validate language codes."""
    resolved = resolve_language(to_lang)
    if resolved is None:
        raise ValueError(f'Unable to translate to {to_lang}')
    lang_obj = resolved.to_language(forced=forced, hi=hi)
    normalized_code2 = lang_obj.alpha2
    if not isinstance(normalized_code2, str):
        raise ValueError(f'Unable to translate to {to_lang}')
    return lang_obj, TranslationTarget(resolved, to_lang, normalized_code2)


def create_process_result(message: str, request: TranslationRequest, output_path: str,
                          default_score: int) -> ProcessSubtitlesResult:
    if request.media.kind == 'episode':
        prr = path_mappings.path_replace_reverse
    else:
        prr = path_mappings.path_replace_reverse_movie
    score = int((default_score / 100) * MAX_SCORES[request.media.kind])
    return ProcessSubtitlesResult(
        message=message,
        reversed_path=prr(request.video_path),
        downloaded_language_code2=request.target.requested_code2,
        downloaded_provider=None,
        score=score,
        forced=request.forced,
        subtitle_id=None,
        reversed_subtitles_path=prr(output_path),
        hearing_impaired=request.hi,
    )


def add_translator_info(dest_srt_file: str, info: str) -> None:
    if os.path.splitext(dest_srt_file)[1].lower() == '.srt':
        # Load the SRT content
        with open(dest_srt_file, "r", encoding="utf-8") as f:
            srt_content = f.read()

        # Parse subtitles
        subtitles = list(srt.parse(srt_content))

        if subtitles:
            first_start = subtitles[0].start
        else:
            # If no subtitles exist, set an arbitrary end time for the info subtitle
            first_start = datetime.timedelta(seconds=5)

        # Determine the end time as the minimum of first_start and 5s
        end_time = min(first_start, datetime.timedelta(seconds=5))

        # If end time is exactly 5s, start at 1s. Otherwise, start at 0s.
        if end_time == datetime.timedelta(seconds=5):
            start_time = datetime.timedelta(seconds=1)
        else:
            start_time = datetime.timedelta(seconds=0)

        # Add the info subtitle
        new_sub = srt.Subtitle(
            index=1,  # temporary, will be reindexed
            start=start_time,
            end=end_time,
            content=info
        )
        subtitles.insert(0, new_sub)

        # Re-index and sort
        subtitles = list(srt.sort_and_reindex(subtitles))

        with open(dest_srt_file, "w", encoding="utf-8") as f:
            f.write(srt.compose(subtitles))


def get_description(media_type, radarr_id, sonarr_series_id):
    try:
        if media_type in ('movies', 'movie'):
            movie = database.execute(
                select(TableMovies.title, TableMovies.imdbId, TableMovies.year, TableMovies.overview)
                .where(TableMovies.radarrId == radarr_id)
            ).first()

            if movie:
                return (f"You will translate movie that is called {movie.title} from {movie.year} "
                        f"and it has IMDB ID = {movie.imdbId}. Its overview: {movie.overview}")
            else:
                logger.info(f"No movie found for this radarr_id: {radarr_id}")
                return ""

        else:
            series = database.execute(
                select(TableShows.title, TableShows.imdbId, TableShows.year, TableShows.overview)
                .where(TableShows.sonarrSeriesId == sonarr_series_id)
            ).first()

            if series:
                return (f"You will translate TV show that is called {series.title} from {series.year} "
                        f"and it has IMDB ID = {series.imdbId}. Its overview: {series.overview}")
            else:
                logger.info(f"No series found for this sonarr_series_id: {sonarr_series_id}")
                return ""
    except Exception:
        logger.exception("Problem with getting media info")
        return ""


def get_title(
        media_type: str,
        radarr_id: Union[int, None] = None,
        sonarr_series_id: Union[int, None] = None,
        sonarr_episode_id: Union[int, None] = None
) -> str:
    try:
        if media_type in ("movies", "movie"):
            if radarr_id is None:
                return ""

            movie_row = database.execute(
                select(TableMovies.title).where(TableMovies.radarrId == radarr_id)
            ).first()

            if movie_row is None:
                return ""

            title_attr = getattr(movie_row, "title", None)
            if title_attr is None:
                return ""

            movie_title = str(title_attr).strip()
            if movie_title == "":
                return ""

            return movie_title

        # Handle series
        if sonarr_series_id is None:
            return ""

        series_row = database.execute(
            select(TableShows.title).where(TableShows.sonarrSeriesId == sonarr_series_id)
        ).first()

        if series_row is None:
            return ""

        series_title_attr = getattr(series_row, "title", None)
        if series_title_attr is None:
            return ""

        series_title = str(series_title_attr).strip()
        if series_title == "":
            return ""

        # If episode ID is provided, get episode details and format as "Series - S##E## - Episode Title"
        if sonarr_episode_id is not None:
            episode_row = database.execute(
                select(TableEpisodes.season, TableEpisodes.episode, TableEpisodes.title)
                .where(TableEpisodes.sonarrEpisodeId == sonarr_episode_id)
            ).first()

            if episode_row is not None:
                season = getattr(episode_row, "season", None)
                episode = getattr(episode_row, "episode", None)
                episode_title = getattr(episode_row, "title", None)

                if season is not None and episode is not None:
                    season_str = f"S{season:02d}"
                    episode_str = f"E{episode:02d}"

                    full_title = f"{series_title} - {season_str}{episode_str}"

                    if episode_title and str(episode_title).strip():
                        full_title += f" - {str(episode_title).strip()}"

                    return full_title

        return series_title

    except Exception:
        logger.exception("Problem with getting title")
        return ""
