# coding=utf-8

from dataclasses import dataclass
from typing import TypedDict

import pycountry

from subzero.language import Language

from .custom_lang import CustomLanguage
from .formatting import language_info_from_code
from app.database import TableSettingsLanguages, database, insert, update, select


class LanguageEntry(TypedDict):
    code2: str | None
    code3: str | None
    code3b: str | None
    name: str | None


@dataclass(frozen=True)
class ResolvedLanguage:
    code2: str
    code3: str
    name: str


languages_dict: list[LanguageEntry] = []


def load_language_in_db():
    # Get languages list in langs tuple
    langs = [{'code3': lang.alpha_3, 'code2': lang.alpha_2, 'name': lang.name, 'enabled': 0}
             for lang in pycountry.languages
             if hasattr(lang, 'alpha_2')]

    # Insert standard languages in database table
    database.execute(
        insert(TableSettingsLanguages)
        .values(langs)
        .on_conflict_do_nothing())

    # Update standard languages with code3b if available
    langs = [{'code3b': lang.bibliographic, 'code3': lang.alpha_3}
             for lang in pycountry.languages
             if hasattr(lang, 'alpha_2') and hasattr(lang, 'bibliographic')]

    # Update languages in database table
    database.execute(
        update(TableSettingsLanguages), langs)

    # Insert custom languages in database table
    CustomLanguage.register(TableSettingsLanguages)

    # Create languages dictionary for faster conversion than calling database
    create_languages_dict()


def create_languages_dict():
    global languages_dict
    # replace chinese by chinese simplified
    database.execute(
        update(TableSettingsLanguages)
        .values(name='Chinese Simplified')
        .where(TableSettingsLanguages.code3 == 'zho'))

    # replace Modern Greek by Greek to match Sonarr and Radarr languages
    database.execute(
        update(TableSettingsLanguages)
        .values(name='Greek')
        .where(TableSettingsLanguages.code3 == 'ell'))

    languages_dict = [{
        'code3': x.code3,
        'code2': x.code2,
        'name': x.name,
        'code3b': x.code3b,
    } for x in database.execute(
        select(TableSettingsLanguages.code3, TableSettingsLanguages.code2, TableSettingsLanguages.name,
               TableSettingsLanguages.code3b))
        .all()]


def audio_language_from_name(lang):
    lang_map = {
        'Chinese': 'zh',
        'Portuguese (Brazil)': 'pb',
        'Spanish (Latino)': 'ea',
    }

    alpha2_code = lang_map.get(lang, None)

    if alpha2_code is None:
        return lang

    return language_from_alpha2(alpha2_code)


def language_from_alpha2(lang: str) -> str | None:
    return next((item['name'] for item in languages_dict if item['code2'] == lang[:2]), None)


def language_from_alpha3(lang: str) -> str | None:
    return next((item['name'] for item in languages_dict if lang[:3] in [item['code3'], item['code3b']]), None)


def alpha2_from_alpha3(lang: str) -> str | None:
    return next((item['code2'] for item in languages_dict if lang[:3] in [item['code3'], item['code3b']]), None)


def alpha2_from_language(lang: str) -> str | None:
    return next((item['code2'] for item in languages_dict if item['name'] == lang), None)


def alpha3_from_alpha2(lang: str) -> str | None:
    return next((item['code3'] for item in languages_dict if item['code2'] == lang[:2]), None)


def alpha3_from_language(lang: str) -> str | None:
    return next((item['code3'] for item in languages_dict if item['name'] == lang), None)


def resolve_language(code2: str | None) -> ResolvedLanguage | None:
    if not code2:
        return None
    language = language_info_from_code(code2)
    if language['name'] is None or language['code3'] is None:
        return None
    return ResolvedLanguage(code2=code2[:2], code3=language['code3'], name=language['name'])


def get_language_set():
    languages = database.execute(
        select(TableSettingsLanguages.code3)
        .where(TableSettingsLanguages.enabled == 1))\
        .all()

    language_set = set()

    for lang in languages:
        custom = CustomLanguage.from_value(lang.code3, "alpha3")
        if custom is None:
            language_set.add(Language(lang.code3))
        else:
            language_set.add(custom.subzero_language())

    return language_set
