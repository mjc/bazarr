import logging
import os
from collections.abc import Iterable
from typing_extensions import TypedDict

import pysubs2
import requests
from deep_translator.exceptions import RequestError, TooManyRequests
from retry.api import retry

from app.jobs_queue import jobs_queue
from utilities.json_types import JsonValue
from ..core.translation_request import EpisodeMedia, LingarrConfig, TranslationRequest
from ..core.translator_utils import get_title

logger = logging.getLogger(__name__)
_LANGUAGE_CODES = {'zh': 'zh-CN', 'zt': 'zh-TW', 'pb': 'pt-BR'}


class TranslationLine(TypedDict, closed=True):
    position: int
    line: str


class TranslationPayload(TypedDict, closed=True):
    arrMediaId: int
    title: str
    sourceLanguage: str
    targetLanguage: str
    mediaType: str
    lines: list[TranslationLine]
    sourceSubtitlePath: str
    translatedSubtitlePath: str


def parse_translation_batch(payload: JsonValue) -> dict[int, str] | None:
    if not isinstance(payload, list):
        return None
    translations: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            return None
        position = item.get('position')
        text = item.get('line')
        if not isinstance(position, int) or isinstance(position, bool) or not isinstance(text, str):
            return None
        translations[position] = text
    return translations


class LingarrTranslatorService:
    def __init__(self, request: TranslationRequest, config: LingarrConfig) -> None:
        self.request = request
        self.config = config

    def translate(self, job_id: int | None = None) -> str:
        try:
            jobs_queue.update_job_progress(job_id=job_id, progress_max=1,
                                           progress_message=self.request.source_srt_file)
            subs = pysubs2.load(self.request.source_srt_file, encoding='utf-8')
            if subs:
                translations = self._translate_content(event.plaintext for event in subs)
                for position, event in enumerate(subs):
                    translated = translations.get(position)
                    if translated:
                        event.text = translated
            subs.save(self.request.dest_srt_file)
            jobs_queue.update_job_progress(job_id=job_id, progress_value='max')
            return self.request.dest_srt_file
        except (OSError, RequestError, TooManyRequests, requests.exceptions.RequestException) as error:
            logger.error('Lingarr translation failed for %s: %s', self.request.source_srt_file, error)
            jobs_queue.update_job_progress(job_id=job_id, progress_message=f'Lingarr translation failed: {error}')
            raise

    def _build_payload(self, lines: Iterable[str]) -> TranslationPayload:
        media = self.request.media
        if isinstance(media, EpisodeMedia):
            title = get_title('episode', sonarr_series_id=media.series_id, sonarr_episode_id=media.episode_id)
            api_type, media_id = 'Episode', media.series_id
        else:
            title = get_title('movie', radarr_id=media.movie_id)
            api_type, media_id = 'Movie', media.movie_id
        basename = os.path.basename(self.request.source_srt_file) or 'subtitle.srt'
        display_title = f'{title} · {basename}' if title.strip() else basename
        payload: TranslationPayload = {
            'arrMediaId': media_id,
            'title': display_title,
            'sourceLanguage': _LANGUAGE_CODES.get(self.request.from_lang, self.request.from_lang),
            'targetLanguage': _LANGUAGE_CODES.get(self.request.target.language.code2, self.request.target.language.code2),
            'mediaType': api_type,
            'lines': [{'position': position, 'line': text} for position, text in enumerate(lines) if text.strip()],
            'sourceSubtitlePath': self.request.source_srt_file,
            'translatedSubtitlePath': self.request.dest_srt_file,
        }
        return payload

    def _translate_content(self, lines: Iterable[str]) -> dict[int, str]:
        payload = self._build_payload(lines)
        response = self._post_content(payload)
        if response.status_code != 200:
            raise RequestError(f'Lingarr API error: {response.status_code}')
        try:
            decoded: JsonValue = response.json()
        except ValueError as error:
            raise RequestError('Lingarr returned invalid JSON') from error
        translations = parse_translation_batch(decoded)
        if translations is None:
            raise RequestError('Lingarr returned invalid translation data')
        return translations

    @retry(exceptions=(TooManyRequests, RequestError, requests.exceptions.RequestException), tries=3, delay=1,
           backoff=2, jitter=(0, 1))
    def _post_content(self, payload: TranslationPayload) -> requests.Response:
        headers = {'Content-Type': 'application/json'}
        if self.config.token:
            headers['X-Api-Key'] = self.config.token
        response = requests.post(f'{self.config.url}/api/translate/content', json=payload, headers=headers,
                                 timeout=1920)
        if response.status_code == 401:
            raise RequestError('Authentication failed: Invalid or missing API key')
        if response.status_code == 429:
            raise TooManyRequests('Rate limit exceeded')
        if response.status_code >= 500:
            raise RequestError(f'Server error: {response.status_code}')
        return response
