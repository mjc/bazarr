# coding=utf-8
from typing import TypedDict


class LanguageInfo(TypedDict):
    name: str | None
    code2: str | None
    code3: str | None


def language_info_from_code(code: str) -> LanguageInfo:
    from .get_languages import languages_dict

    language = next((item for item in languages_dict if item['code2'] == code[:2]), None)
    return {
        'name': language['name'] if language else None,
        'code2': code,
        'code3': language['code3'] if language else None,
    }
