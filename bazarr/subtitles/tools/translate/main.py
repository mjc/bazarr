# coding=utf-8

import logging
import os
from typing import TypeAlias

from sqlalchemy import Row
from subliminal_patch.core import get_subtitle_path

from .core.translation_request import (
    EpisodeMedia, GeminiConfig, TranslationRequest, load_translator_config, resolve_translation_media,
    resolve_translator_name,
)
from .core.translator_utils import (
    add_translator_info, convert_language_codes, create_process_result, get_translation_extension,
    validate_translation_params,
)
from .services.translator_factory import TranslatorFactory
from languages.get_languages import language_from_alpha2
from app.config import settings
from app.jobs_queue import jobs_queue
from radarr.history import history_log_movie
from sonarr.history import history_log
from utilities.helper import get_target_folder

TranslationMetadata: TypeAlias = (
    Row[tuple[str, int | None, int, int, str | None, int | None]] | Row[tuple[str, str | None, int | None]]
)


def extract_and_translate_embedded(subtitles_id: int, media_type: str, video_path: str, from_lang: str,
                                   to_lang: str, forced: bool, hi: bool, sonarr_series_id: int | None,
                                   sonarr_episode_id: int | None, radarr_id: int | None, metadata: TranslationMetadata,
                                   original_format: bool = False, job_id: int | None = None) -> None:
    # Enqueue before adding locals: the job helper binds this frame's locals to the function signature.
    if not job_id:
        jobs_queue.add_job_from_function(f'Extracting and translating from {from_lang.upper()} to '
                                         f'{to_lang.upper()}', is_progress=True)
        return

    from subtitles.embedded import extract_embedded_subtitle
    subtitles_path = extract_embedded_subtitle(subtitles_id=subtitles_id, media_type=media_type, job_id=job_id)
    if not subtitles_path:
        logging.error('Unable to extract embedded subtitle %s', subtitles_id)
        return

    translate_subtitles_file(video_path=video_path, source_srt_file=subtitles_path, from_lang=from_lang,
                             to_lang=to_lang, forced=forced, hi=hi, media_type=media_type,
                             sonarr_series_id=sonarr_series_id, sonarr_episode_id=sonarr_episode_id,
                             radarr_id=radarr_id, metadata=metadata, original_format=original_format,
                             job_id=job_id)


def translate_subtitles_file(video_path: str, source_srt_file: str, from_lang: str, to_lang: str, forced: bool,
                             hi: bool, media_type: str, sonarr_series_id: int | None, sonarr_episode_id: int | None,
                             radarr_id: int | None, metadata: TranslationMetadata, original_format: bool = False,
                             job_id: int | None = None) -> str | None:
    if not job_id:
        jobs_queue.add_job_from_function(
            f'Translating from {from_lang.upper()} to {to_lang.upper()} using '
            f'{resolve_translator_name(settings.translator.translator_type).replace("_", " ").title()}',
            is_progress=True)
        return None

    config = load_translator_config(settings.translator)
    try:
        validate_translation_params(video_path, source_srt_file, from_lang, to_lang)
        language, target = convert_language_codes(to_lang, forced, hi)
        media = resolve_translation_media(media_type, sonarr_series_id, sonarr_episode_id, radarr_id)
        extension = get_translation_extension(source_srt_file, original_format, config.name)
        alongside_video: str = get_subtitle_path(video_path, language=language, extension=extension,
                                                forced_tag=forced, hi_tag=hi)
        folder = get_target_folder(video_path)
        destination = os.path.join(folder, os.path.basename(alongside_video)) if folder else alongside_video
        request = TranslationRequest(source_srt_file, destination, video_path, from_lang, target, media, forced, hi)
        translator = TranslatorFactory.create_translator(request, config)
        output = translator.translate(job_id=job_id)
        if output is None:
            return None
        if not os.path.isfile(output):
            raise FileNotFoundError(f'Translator did not save its output: {output}')
        if config.annotate:
            label = config.model if isinstance(config, GeminiConfig) else (
                'Lingarr' if config.name == 'lingarr' else 'Google Translate')
            add_translator_info(output, f'# Subtitles translated with {label} # ')
        suffix = ' using Lingarr.' if config.name == 'lingarr' else '.'
        message = (f'{language_from_alpha2(from_lang)} subtitles translated to '
                   f'{target.language.name}{suffix}')
        result = create_process_result(message, request, output, config.default_score)
        if isinstance(media, EpisodeMedia):
            history_log(action=6, sonarr_series_id=media.series_id, sonarr_episode_id=media.episode_id, result=result)
        else:
            history_log_movie(action=6, radarr_id=media.movie_id, result=result)
        from api.subtitles.subtitles import postprocess_subtitles
        postprocess_subtitles(output, media.kind, metadata,
                             media.episode_id if isinstance(media, EpisodeMedia) else media.movie_id)
        jobs_queue.update_job_name(job_id=job_id,
                                   new_job_name=f'Translated from {from_lang.upper()} to {to_lang.upper()} using '
                                                f'{config.name.replace("_", " ").title()}')
        return output
    except (OSError, ValueError, RuntimeError):
        logging.exception('Translation failed')
        raise
