# coding=utf-8

import logging
import re
import pysubs2

from random import randint

from subliminal_patch.providers.utils import FIRST_THOUSAND_OR_SO_USER_AGENTS as AGENT_LIST

from retry.api import retry
from ..core.translation_request import TranslationRequest
from deep_translator import GoogleTranslator
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.jobs_queue import jobs_queue
from deep_translator.exceptions import TooManyRequests, RequestError, TranslationNotFound

logger = logging.getLogger(__name__)


class GoogleTranslatorService:

    def __init__(self, request: TranslationRequest) -> None:
        self.request = request
        codes = {'he': 'iw', 'zh': 'zh-CN', 'zt': 'zh-TW', 'pb': 'pt'}
        self.target_language = codes.get(request.target.language.code2, request.target.normalized_code2)

    def translate(self, job_id: int | None) -> str:
        try:
            subs = pysubs2.load(self.request.source_srt_file, encoding='utf-8')
            subs.remove_miscellaneous_events()
            line_count = len(subs)
            jobs_queue.update_job_progress(job_id=job_id, progress_max=line_count,
                                           progress_message=self.request.source_srt_file)

            def translate_line(event: pysubs2.SSAEvent) -> str:
                text = event.plaintext
                try:
                    return self._translate_text(text, job_id)
                except TranslationNotFound:
                    logger.debug('Unable to translate line %s', text)
                    return text

            failures = 0
            first_error: Exception | None = None
            logger.debug('Sending %s blocks to Google Translate', line_count)
            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(translate_line, event) for event in subs]
                for completed, future in enumerate(as_completed(futures), start=1):
                    try:
                        future.result()
                    except Exception as error:
                        logger.error('Error in translation task: %s', error)
                        failures += 1
                        if first_error is None:
                            first_error = error
                    jobs_queue.update_job_progress(job_id=job_id, progress_value=completed)

            if first_error is not None:
                message = (f'Google translation failed for {failures}/{line_count} line(s) '
                           f'({first_error.__class__.__name__}: {first_error}). '
                           f'Subtitle was not saved to avoid leaving partially-translated content.')
                jobs_queue.update_job_progress(job_id=job_id, progress_message=message)
                raise RequestError(message)

            for event, future in zip(subs, futures):
                translated = future.result()
                if translated:
                    event.plaintext = translated
            del futures

            subs.save(self.request.dest_srt_file)

            return self.request.dest_srt_file

        except Exception as e:
            logger.error(f'BAZARR encountered an error during translation: {str(e)}')
            jobs_queue.update_job_progress(job_id=job_id,
                                           progress_message=f'Google translation failed: {str(e)}')
            raise

    @retry(exceptions=(TooManyRequests, RequestError), tries=6, delay=1, backoff=2, jitter=(0, 1))
    def _translate_text(self, text: str, job_id: int | None) -> str:
        try:
            translated_text = GoogleTranslator(
                source='auto',
                target=self.target_language,
                user_agent=AGENT_LIST[randint(0, len(AGENT_LIST) - 1)]
            ).translate(text=text)

            # The free Google endpoint occasionally answers 200 OK with an error/captcha HTML page
            # instead of a real translation. deep_translator then returns that error text (e.g.
            # "Error 500", "Too Many Requests", raw HTML) as the translation, which used to get
            # written straight into the subtitle. Detect and reject those so they are retried and,
            # if they persist, surfaced as a failure instead of being baked into the output.
            if translated_text is None or self._looks_like_error_response(translated_text):
                raise RequestError(f'Google Translate returned an invalid response: {translated_text!r}')

            return translated_text
        except (TooManyRequests, RequestError) as e:
            logger.error(f'Google Translate API error after retries: {str(e)}')
            jobs_queue.update_job_progress(job_id=job_id,
                                           progress_message=f'Google Translate API error: {str(e)}')
            raise
        except Exception as e:
            logger.error(f'Unexpected error in Google translation: {str(e)}')
            jobs_queue.update_job_progress(job_id=job_id,
                                           progress_message=f'Translation error: {str(e)}')
            raise

    @staticmethod
    def _looks_like_error_response(text: str) -> bool:
        """Heuristic guard against Google serving an error/captcha page as a translation."""
        if not text:
            return False
        # Raw HTML leaking through (error pages, captcha interstitials).
        if re.search(r'<\s*(html|body|div|br|p|DOCTYPE)[^>]*>', text, re.IGNORECASE):
            return True
        lowered = text.lower()
        error_markers = (
            'too many requests', 'server error', 'internal server error',
            'service unavailable', 'bad gateway', 'temporarily unavailable',
            'captcha', 'unusual traffic', 'rate limit',
        )
        if any(marker in lowered for marker in error_markers):
            return True
        # "Error 500", "HTTP 429", standalone "500"/"429" style leftovers.
        if re.search(r'\b(error|http)[ _-]?\d{3}\b', lowered):
            return True
        return False
