# coding=utf-8

import pycountry

from subzero.language import Language

from .custom_lang import CustomLanguage
from .registry import LanguageRegistry, ResolvedLanguage
from app.database import TableSettingsLanguages, database, insert, update, select


_language_registry = LanguageRegistry(())
_AUDIO_LANGUAGE_CODES = {'Chinese': 'zh', 'Portuguese (Brazil)': 'pb', 'Spanish (Latino)': 'ea'}


def get_language_registry() -> LanguageRegistry:
    return _language_registry


def resolve_language(code2: str | None) -> ResolvedLanguage | None:
    return _language_registry.resolve(code2)


def load_language_in_db() -> None:
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

    # Replace all lookup indexes together after loading the language table.
    refresh_language_registry()


def refresh_language_registry() -> None:
    global _language_registry
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

    registry = LanguageRegistry({
        'code3': x.code3,
        'code2': x.code2,
        'name': x.name,
        'code3b': x.code3b,
    } for x in database.execute(
        select(TableSettingsLanguages.code3, TableSettingsLanguages.code2, TableSettingsLanguages.name,
               TableSettingsLanguages.code3b)))
    _language_registry = registry


def audio_language_from_name(lang: str | None) -> str | None:
    if lang is None:
        return None
    alpha2_code = _AUDIO_LANGUAGE_CODES.get(lang)
    return language_from_alpha2(alpha2_code) if alpha2_code is not None else lang


def language_from_alpha2(lang: str) -> str | None:
    language = _language_registry.from_code2(lang)
    return language.name if language is not None else None


def language_from_alpha3(lang: str) -> str | None:
    language = _language_registry.from_code3(lang)
    return language.name if language is not None else None


def alpha2_from_alpha3(lang: str) -> str | None:
    language = _language_registry.from_code3(lang)
    return language.code2 if language is not None else None


def alpha2_from_language(lang: str) -> str | None:
    language = _language_registry.from_name(lang)
    return language.code2 if language is not None else None


def alpha3_from_alpha2(lang: str) -> str | None:
    language = _language_registry.from_code2(lang)
    return language.code3 if language is not None else None


def alpha3_from_language(lang: str) -> str | None:
    language = _language_registry.from_name(lang)
    return language.code3 if language is not None else None


def get_language_set() -> set[Language]:
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
