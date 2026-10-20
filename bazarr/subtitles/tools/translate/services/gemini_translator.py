# coding=utf-8

import json
import re
import os
import json_tricks
import signal
import threading
import time
import typing
import logging

import srt
import pysubs2
import requests
import unicodedata as ud
from collections import Counter
from typing import List
from srt import Subtitle

from utilities.path_mappings import path_mappings
from subtitles.processing import ProcessSubtitlesResult
from app.jobs_queue import JobCanceled, jobs_queue
from languages.get_languages import alpha3_from_alpha2, language_from_alpha2, language_from_alpha3
from ..core.translator_utils import get_description
from ..core.translation_request import EpisodeMedia, GeminiConfig, TranslationRequest

logger = logging.getLogger(__name__)
DEFAULT_GEMINI_BATCH_SIZE = 300
DEFAULT_GEMINI_KEY_COOLDOWN_SECONDS = 60
_GEMINI_KEY_COOLDOWNS = {}
_GEMINI_KEY_COOLDOWNS_LOCK = threading.Lock()


class SubtitleObject(typing.TypedDict):
    """
    TypedDict for subtitle objects used in translation
    """
    index: str
    content: str


class GeminiTranslatorService:

    def __init__(self, request: TranslationRequest, config: GeminiConfig) -> None:
        self.request = request
        self.config = config

        self.gemini_api_key = None
        self.api_keys = []
        self.current_api_key = None
        self.current_api_index = -1
        self.current_api_number = 1
        self.backup_api_number = 2
        self.target_language = ''
        self.input_file = request.source_srt_file
        self.output_file = request.dest_srt_file
        self.start_line = 1
        self.description = ''
        self.model_name = "gemini-2.0-flash"
        self.batch_size = DEFAULT_GEMINI_BATCH_SIZE
        self.free_quota = True
        self.error_log = False
        self.interrupt_flag = False
        self.progress_file = None
        self.current_progress = 0
        self.job_id: int | None = None

    def translate(self, job_id: int | None) -> str | None:
        self.job_id = job_id
        self.api_keys = self._get_configured_api_keys()
        self.current_api_key = self._select_next_api_key()
        self.gemini_api_key = self.current_api_key
        self.target_language = language_from_alpha3(self.request.target.language.code3) or ''
        self.model_name = self.config.model
        self.batch_size = self.config.batch_size
        media = self.request.media
        self.description = get_description(media.kind,
                                           media.movie_id if not isinstance(media, EpisodeMedia) else None,
                                           media.series_id if isinstance(media, EpisodeMedia) else None)
        self.progress_file = os.path.join(os.path.dirname(self.input_file), f'.{os.path.basename(self.input_file)}.progress')
        self._check_saved_progress()
        return self._translate_with_gemini()

    @staticmethod
    def get_instruction(language: str, description: str) -> str:
        """
        Get the instruction for the translation model based on the target language.
        """
        instruction = f"""You are an assistant that translates subtitles to {language}.
    You will receive the following JSON type:

    class SubtitleObject(typing.TypedDict):
        index: str
        content: str

    Request: list[SubtitleObject]

    The 'index' key is the index of the subtitle dialog.
    The 'content' key is the dialog to be translated.

    The indices must remain the same in the response as in the request.
    Dialogs must be translated as they are without any changes.
    If a line has a comma or multiple sentences, try to keep one line to about 40-50 characters.
    """
        if description:
            instruction += "\nAdditional user instruction: '" + description + "'"
        return instruction

    def _check_saved_progress(self):
        """Check if there's a saved progress file and load it if exists"""
        if not self.progress_file or not os.path.exists(self.progress_file):
            return

        if self.start_line != 1:
            return

        try:
            with open(self.progress_file, "r") as f:
                data = json.load(f)
                saved_line = data.get("line", 1)
                input_file = data.get("input_file")

                # Verify the progress file matches our current input file
                if input_file != self.input_file:
                    jobs_queue.update_job_progress(
                        job_id=self.job_id,
                        progress_message=f"Found progress file for different subtitle: {input_file}. Ignoring saved "
                                         f"progress.")
                    return

                if saved_line > 1 and self.start_line == 1:
                    os.remove(self.output_file)
        except Exception as e:
            jobs_queue.update_job_progress(job_id=self.job_id, progress_message=f"Error reading progress file: {e}")

    def _save_progress(self, line):
        """Save current progress to temporary file"""
        if not self.progress_file:
            return

        try:
            with open(self.progress_file, "w") as f:
                json.dump({"line": line, "input_file": self.input_file}, f)
        except Exception as e:
            jobs_queue.update_job_progress(job_id=self.job_id, progress_message=f"Failed to save progress: {e}")

    def _clear_progress(self):
        """Clear the progress file on successful completion"""
        if self.progress_file and os.path.exists(self.progress_file):
            try:
                os.remove(self.progress_file)
            except Exception as e:
                jobs_queue.update_job_progress(job_id=self.job_id, progress_message=f"Failed to remove progress file: {e}")

    def handle_interrupt(self, *args):
        """Handle interrupt signal by setting interrupt flag"""
        self.interrupt_flag = True

    def setup_signal_handlers(self):
        """Set up signal handlers if in main thread"""
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self.handle_interrupt)
            return True
        return False

    def _get_batch_size(self) -> int:
        return self.config.batch_size

    def _get_configured_api_keys(self) -> List[str]:
        return list(self.config.keys)

    @staticmethod
    def _is_key_on_cooldown(api_key: str) -> bool:
        now = time.time()
        with _GEMINI_KEY_COOLDOWNS_LOCK:
            expires_at = _GEMINI_KEY_COOLDOWNS.get(api_key)
            if expires_at is None:
                return False
            if expires_at <= now:
                _GEMINI_KEY_COOLDOWNS.pop(api_key, None)
                return False
            return True

    def _select_next_api_key(self):
        if not self.api_keys:
            self.current_api_key = None
            return None

        total_keys = len(self.api_keys)
        start_index = self.current_api_index if self.current_api_index >= 0 else -1

        for offset in range(1, total_keys + 1):
            candidate_index = (start_index + offset) % total_keys
            candidate_key = self.api_keys[candidate_index]
            if not self._is_key_on_cooldown(candidate_key):
                self.current_api_index = candidate_index
                self.current_api_key = candidate_key
                return candidate_key

        self.current_api_key = None
        return None

    @staticmethod
    def _is_rate_limited_response(response) -> bool:
        if response is None:
            return False

        if response.status_code == 429:
            return True

        try:
            error_body = response.json().get("error", {})
        except Exception:
            return False

        error_status = str(error_body.get("status", "")).upper()
        error_message = str(error_body.get("message", "")).lower()
        return error_status == "RESOURCE_EXHAUSTED" or "rate limit" in error_message or "quota" in error_message

    @staticmethod
    def _get_retry_after_seconds(response) -> int:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(1, int(float(retry_after)))
                except (TypeError, ValueError):
                    pass

        return DEFAULT_GEMINI_KEY_COOLDOWN_SECONDS

    @staticmethod
    def _set_key_cooldown(api_key: str, cooldown_seconds: int):
        with _GEMINI_KEY_COOLDOWNS_LOCK:
            _GEMINI_KEY_COOLDOWNS[api_key] = time.time() + max(1, cooldown_seconds)

    def _handle_rate_limited_key(self, response):
        if not self.current_api_key:
            raise RuntimeError("All Gemini API keys are currently rate limited. Please wait before retrying.")

        cooldown_seconds = self._get_retry_after_seconds(response)
        self._set_key_cooldown(self.current_api_key, cooldown_seconds)

        rotated_key = self._select_next_api_key()
        if not rotated_key:
            raise RuntimeError("All Gemini API keys are currently rate limited. Please wait before retrying.")

        if self.job_id is not None:
            jobs_queue.update_job_progress(
                job_id=self.job_id,
                progress_message="Gemini key rate limited. Rotating to another configured key.",
            )

    def _build_generate_payload(self, batch: List[SubtitleObject]) -> dict:
        return {
            "system_instruction": {
                "parts": [
                    {
                        "text": self.get_instruction(self.target_language, self.description)
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(batch, ensure_ascii=False)
                        }
                    ]
                }
            ]
        }

    current_progress = 0

    def _process_batch(
            self,
            batch: List[SubtitleObject],  # Changed from list[SubtitleObject]
            translated_subtitle: List[Subtitle],  # Changed from list[Subtitle]
            total: int,
            retry_num=3
    ):
        """
        Process a batch of subtitles for translation with accurate progress tracking.
        Splits oversized batches on retry to handle output token limit failures.

        Args:
            batch (List[SubtitleObject]): Batch of subtitles to translate
            translated_subtitle (List[Subtitle]): List to store translated subtitles
            total (int): Total number of subtitles to translate
        """

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.current_api_key}"

        payload = json.dumps(self._build_generate_payload(batch), ensure_ascii=False)
        headers = {
            'Content-Type': 'application/json'
        }

        try:
            response = requests.request("POST", url, headers=headers, data=payload)
            response.raise_for_status()  # Raise an exception for bad status codes

            def clean_json_string(json_string):
                pattern = r'^```json\s*(.*?)\s*```$'
                cleaned_string = re.sub(pattern, r'\1', json_string, flags=re.DOTALL)
                return cleaned_string.strip()

            parts = json.loads(response.text)['candidates'][0]['content']['parts']
            result = clean_json_string(''.join(part['text'] for part in parts))

            translated_lines = json_tricks.loads(result)

            # Validate translated lines BEFORE updating progress
            if len(translated_lines) != len(batch):
                raise ValueError(
                    f"Gemini returned {len(translated_lines)} lines instead of expected {len(batch)} lines")

            # Process translated lines
            self._process_translated_lines(
                translated_lines=translated_lines,
                translated_subtitle=translated_subtitle,
                batch=batch,
            )

            # Update progress only after successful validation and processing
            chunk_size = len(translated_lines)
            self.current_progress = self.current_progress + chunk_size

            jobs_queue.update_job_progress(job_id=self.job_id, progress_value=self.current_progress)

            # Clear the batch after successful processing
            batch.clear()

            return self.current_progress

        except Exception as e:
            response = getattr(e, "response", None)
            if self._is_rate_limited_response(response):
                self._handle_rate_limited_key(response)
                if retry_num > 0:
                    return self._process_batch(batch, translated_subtitle, total, retry_num - 1)
                else:
                    jobs_queue.update_job_progress(job_id=self.job_id, progress_message=f"Translation request failed: {e}")
                    raise e

            # For non-rate-limit errors, try splitting the batch if it's large enough
            if len(batch) > 1 and retry_num > 0:
                mid = len(batch) // 2
                first_half = batch[:mid]
                second_half = batch[mid:]

                try:
                    self._process_batch(first_half, translated_subtitle, total, retry_num - 1)
                    self._process_batch(second_half, translated_subtitle, total, retry_num - 1)
                    batch.clear()
                    return self.current_progress
                except Exception as split_error:
                    jobs_queue.update_job_progress(job_id=self.job_id, progress_message=f"Translation request failed: {split_error}")
                    raise split_error
            else:
                jobs_queue.update_job_progress(job_id=self.job_id, progress_message=f"Translation request failed: {e}")
                raise e

    @staticmethod
    def _process_translated_lines(
            translated_lines: List[SubtitleObject],  # Changed from list[SubtitleObject]
            translated_subtitle: List[Subtitle],  # Changed from list[Subtitle]
            batch: List[SubtitleObject],  # Changed from list[SubtitleObject]
    ):
        """
        Process the translated lines and update the subtitle list.

        Args:
            translated_lines (List[SubtitleObject]): List of translated lines
            translated_subtitle (List[Subtitle]): List to store translated subtitles
            batch (List[SubtitleObject]): Batch of subtitles to translate
        """

        def _dominant_strong_direction(s: str) -> str:
            """
            Determine the dominant text direction (RTL or LTR) of a string.

            Args:
                s (str): Input string to analyze

            Returns:
                str: 'rtl' if right-to-left is dominant, 'ltr' otherwise
            """
            count = Counter([ud.bidirectional(c) for c in list(s)])
            rtl_count = count["R"] + count["AL"] + count["RLE"] + count["RLI"]
            ltr_count = count["L"] + count["LRE"] + count["LRI"]
            return "rtl" if rtl_count > ltr_count else "ltr"

        for line in translated_lines:
            if "content" not in line or "index" not in line:
                break
            if str(line["index"]) not in [str(x["index"]) for x in batch]:
                raise Exception("Gemini has returned different indices.")
            if _dominant_strong_direction(line["content"]) == "rtl":
                translated_subtitle[int(line["index"])].content = f"\u202b{line['content']}\u202c"
            else:
                translated_subtitle[int(line["index"])].content = line["content"]

    def _translate_with_gemini(self) -> str | None:
        if not self.api_keys:
            jobs_queue.update_job_progress(
                job_id=self.job_id,
                progress_message="Please provide at least one valid Gemini API key.",
            )
            return None

        if not self.current_api_key:
            jobs_queue.update_job_progress(
                job_id=self.job_id,
                progress_message="All Gemini API keys are currently rate limited. Please wait before retrying.",
            )
            return None

        if not self.target_language:
            jobs_queue.update_job_progress(job_id=self.job_id, progress_message="Please provide a target language.")
            return None

        if not self.input_file:
            jobs_queue.update_job_progress(job_id=self.job_id, progress_message="Please provide a subtitle file.")
            return None

        total = 0
        try:
            with open(self.input_file, "r", encoding="utf-8") as original_file:
                original_text = original_file.read()
                original_subtitle = list(srt.parse(original_text))
                if not original_subtitle:
                    jobs_queue.update_job_progress(job_id=self.job_id, progress_message='No subtitle lines to translate.')
                    return None

                # Use with statement for the output file too
                with open(self.output_file, "w", encoding="utf-8") as translated_file:
                    if len(original_subtitle) < self.batch_size:
                        self.batch_size = len(original_subtitle)

                    delay = False
                    delay_time = 30

                    i = self.start_line - 1
                    total = len(original_subtitle)
                    batch = [SubtitleObject(index=str(i), content=original_subtitle[i].content)]
                    i += 1

                    # Save initial progress
                    self._save_progress(i)

                    jobs_queue.update_job_progress(job_id=self.job_id, progress_max=total,
                                                   progress_message=self.request.source_srt_file)

                    while (i < total or len(batch) > 0) and not self.interrupt_flag:
                        if i < total and len(batch) < self.batch_size:
                            batch.append(SubtitleObject(index=str(i), content=original_subtitle[i].content))
                            i += 1
                            continue

                        try:
                            start_time = time.time()
                            self._process_batch(batch, original_subtitle, total)
                            end_time = time.time()

                            # Save progress after each batch
                            self._save_progress(i + 1)

                            if delay and (end_time - start_time < delay_time) and i < total:
                                time.sleep(delay_time - (end_time - start_time))

                        except Exception as e:
                            self._clear_progress()
                            # File will be automatically closed by the with statement
                            raise e

                    # Check if we exited the loop due to an interrupt
                    jobs_queue.update_job_progress(job_id=self.job_id, progress_value=total,
                                                   progress_message=self.request.source_srt_file)
                    if self.interrupt_flag:
                        self._clear_progress()
                        raise JobCanceled

                    # Write the final result - this happens inside the with block
                    translated_file.write(srt.compose(original_subtitle))

                    # Clear progress file on successful completion
                    self._clear_progress()
            return self.output_file

        except Exception as e:
            logger.error(f'BAZARR encountered an error translating with Gemini: {str(e)}')
            jobs_queue.update_job_progress(job_id=self.job_id, progress_value=total,
                                           progress_message=f'Gemini translation failed: {str(e)}')
            self._clear_progress()
            if self.output_file and os.path.exists(self.output_file):
                try:
                    if os.path.getsize(self.output_file) == 0:
                        os.remove(self.output_file)
                except OSError:
                    pass
            raise e
