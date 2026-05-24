# coding=utf-8

import ast
import logging

from subtitles.adaptive_searching import get_active_search_languages


def parse_missing_subtitles(missing_subtitles):
    if isinstance(missing_subtitles, list):
        return missing_subtitles

    try:
        parsed = ast.literal_eval(missing_subtitles)
    except (SyntaxError, ValueError, TypeError):
        logging.debug("Wanted subtitles: missing_subtitles is malformed, no language will be searched.")
        return []

    if not isinstance(parsed, list):
        logging.debug("Wanted subtitles: missing_subtitles is malformed, no language will be searched.")
        return []

    return parsed


def get_due_missing_languages(missing_subtitles, failed_attempts, adaptive_search_policy=None):
    desired_languages = parse_missing_subtitles(missing_subtitles)
    if not desired_languages:
        return []

    return get_active_search_languages(
        desired_languages,
        failed_attempts,
        adaptive_search_policy=adaptive_search_policy,
    )
