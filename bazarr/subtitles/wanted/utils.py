# coding=utf-8

from subtitles.serialization import missing_subtitle_to_language_tuple


def get_language_search_items(missing_languages):
    return [missing_subtitle_to_language_tuple(language) for language in missing_languages]
