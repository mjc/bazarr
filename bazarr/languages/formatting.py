# coding=utf-8

from collections.abc import Sequence
from typing import TypedDict


class LanguageInfo(TypedDict):
    name: str | None
    code2: str | None
    code3: str | None


class SubtitleLanguage(LanguageInfo):
    forced: bool
    hi: bool


def language_info_from_name(name: str) -> LanguageInfo:
    from .get_languages import languages_dict

    language = next((item for item in languages_dict if item['name'] == name), None)
    return {
        'name': name,
        'code2': (language['code2'] or None) if language else None,
        'code3': (language['code3'] or None) if language else None,
    }


def language_info_from_code(code: str) -> LanguageInfo:
    from .get_languages import languages_dict

    language = next((item for item in languages_dict if item['code2'] == code[:2]), None)
    return {
        'name': language['name'] if language else None,
        'code2': code,
        'code3': language['code3'] if language else None,
    }


def format_audio_languages(names: Sequence[str | None], undefined_name: str | None) -> list[LanguageInfo]:
    languages: list[LanguageInfo] = []
    for name in names:
        display_name = name or undefined_name
        if display_name:
            languages.append(language_info_from_name(display_name))
    return languages


def subtitle_language(code: str, *, forced: bool, hi: bool) -> SubtitleLanguage:
    return {**language_info_from_code(code), 'forced': forced, 'hi': hi}
