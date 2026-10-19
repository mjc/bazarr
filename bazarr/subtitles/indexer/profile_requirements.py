# coding=utf-8

from collections.abc import Iterable, Sequence

from subliminal_patch import core

from app.language_profiles import Profile, ProfileItem, SubtitleRequirement
from languages.custom_lang import CustomLanguage
from utilities.subtitle_metadata import SubtitleFile

ANY_CUTOFF = 65535


def _desired(items: Iterable[ProfileItem], audio: set[str | None]) -> list[SubtitleRequirement]:
    return [item.requirement for item in items
            if not (item.audio_exclude and item.language in audio)
            and not (item.audio_only_include and item.language not in audio)]


def _actual(subtitles: Sequence[SubtitleFile], use_embedded_subs: bool) -> list[SubtitleRequirement]:
    return [SubtitleRequirement(subtitle['code2'], subtitle['forced'], 'required' if subtitle['hi'] else 'prefer')
            for subtitle in subtitles if subtitle['code2'] and (use_embedded_subs or subtitle['path'])]


def _expanded(actual: list[SubtitleRequirement], lang_equals: core._LanguageEquals) -> set[SubtitleRequirement]:
    languages: set[core.Language | str] = set()
    for subtitle in actual:
        custom = CustomLanguage.from_value(subtitle.language, 'alpha2')
        try:
            language = custom.subzero_language() if custom else core.Language.fromietf(subtitle.language)
        except (ValueError, KeyError):
            languages.add(subtitle.language)
            continue
        language.forced = subtitle.forced
        language.hi = subtitle.hi == 'required'
        languages.add(language)
    expanded: set[SubtitleRequirement] = set()
    for language in lang_equals.check_set(languages):
        if isinstance(language, core.Language):
            custom = CustomLanguage.from_value(language, 'language')
            code = custom.alpha2 if custom else language.alpha2
            if isinstance(code, str):
                expanded.add(SubtitleRequirement(code, bool(language.forced), 'required' if language.hi else 'prefer'))
    return expanded


def _cutoff_met(profile: Profile, audio: set[str | None], actual: set[SubtitleRequirement],
                expanded: set[SubtitleRequirement]) -> bool:
    for item in profile.cutoff_items:
        if item.audio_only_include and item.language not in audio:
            continue
        if item.audio_exclude and item.language in audio:
            return True
        if item.requirement in actual or item.requirement in expanded:
            return True
        if not item.forced and item.hi == 'prefer' and item.language not in profile.non_hi_languages:
            if SubtitleRequirement(item.language, False, 'required') in actual:
                return True
        if item.hi == 'excluded' and SubtitleRequirement(item.language, item.forced, 'prefer') in actual:
            return True
    return False


def _unsatisfied(desired: Iterable[SubtitleRequirement], actual: list[SubtitleRequirement],
                  actual_set: set[SubtitleRequirement], expanded: set[SubtitleRequirement],
                  non_hi: frozenset[str]) -> list[SubtitleRequirement]:
    missing = [item for item in desired if item not in actual_set and item not in expanded]
    for item in actual:
        remove = None
        if item.hi == 'required' and not item.forced and item.language not in non_hi:
            remove = SubtitleRequirement(item.language, False, 'prefer')
        elif item.hi == 'prefer' and not item.forced and item.language in non_hi:
            remove = SubtitleRequirement(item.language, False, 'excluded')
        if remove is not None and remove in missing:
            missing.remove(remove)
    return missing


def _format(items: Iterable[SubtitleRequirement]) -> list[str]:
    return [item.language + (':forced' if item.forced else ':hi' if item.hi == 'required' else '') for item in items]


def calculate_missing_subtitles(profile: Profile, subtitles: Sequence[SubtitleFile],
                                audio_language_codes: set[str | None], lang_equals: core._LanguageEquals,
                                *, use_embedded_subs: bool) -> list[str]:
    actual = _actual(subtitles, use_embedded_subs)
    actual_set = set(actual)
    expanded = _expanded(actual, lang_equals)
    if profile.groups is None:
        if _cutoff_met(profile, audio_language_codes, actual_set, expanded):
            return []
        return _format(_unsatisfied(_desired(profile.items, audio_language_codes), actual, actual_set,
                                    expanded, profile.non_hi_languages))

    expanded.update(actual_set)
    for item in actual_set:
        if item.forced:
            continue
        if item.hi == 'required' and item.language not in profile.non_hi_languages:
            expanded.add(SubtitleRequirement(item.language, False, 'prefer'))
        elif item.hi == 'prefer' and item.language in profile.non_hi_languages:
            expanded.add(SubtitleRequirement(item.language, False, 'excluded'))
    satisfied_groups: set[int] = set()
    missing: list[SubtitleRequirement] = []
    missing_keys: set[SubtitleRequirement] = set()
    for group in profile.groups:
        desired = _desired(group.items, audio_language_codes)
        if not desired:
            satisfied_groups.add(group.group_id)
            continue
        unsatisfied = [item for item in desired if item not in expanded]
        satisfied = len(unsatisfied) < len(desired) if group.operator == 'OR' else not unsatisfied
        if satisfied:
            satisfied_groups.add(group.group_id)
        else:
            for item in unsatisfied:
                if item not in missing_keys:
                    missing_keys.add(item)
                    missing.append(item)
    if profile.cutoff and satisfied_groups and (profile.cutoff == ANY_CUTOFF or profile.cutoff in satisfied_groups):
        return []
    return _format(missing)
