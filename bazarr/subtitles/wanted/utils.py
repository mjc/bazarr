# coding=utf-8

from subtitles.adaptive_searching import is_search_active
from subtitles.serialization import parse_missing_subtitles, missing_subtitle_to_language_tuple


def get_due_missing_languages(missing_subtitles, failed_attempts):
    return [
        language
        for language in parse_missing_subtitles(missing_subtitles)
        if is_search_active(desired_language=language, attempt_string=failed_attempts)
    ]


def get_language_search_items(missing_languages):
    return [missing_subtitle_to_language_tuple(language) for language in missing_languages]
