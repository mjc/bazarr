# coding=utf-8

from subtitles.language_utils import parse_language_token


def missing_subtitle_to_language_tuple(language):
    parsed = parse_language_token(language)
    if parsed is None:
        return (language, "False", "False")

    _, language_tuple = parsed
    return language_tuple
