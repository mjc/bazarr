# coding=utf-8

import ast

from subtitles.adaptive_searching import is_search_active


def get_due_missing_languages(missing_subtitles, failed_attempts):
    return [
        language
        for language in ast.literal_eval(missing_subtitles)
        if is_search_active(desired_language=language, attempt_string=failed_attempts)
    ]
