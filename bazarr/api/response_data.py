# coding=utf-8

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping

from sqlalchemy import Select

from api.formatting import ResponseFormatter
from app.config import base_url, settings
from app.database import TableEpisodes, TableMissingSubtitles, database, load_profile_catalog, select
from app.language_profiles import ProfileCatalog
from languages.custom_lang import CustomLanguage
from languages.get_languages import language_from_alpha2
from subliminal_patch import core
from subtitles.pool import get_language_equals
from subtitles.wanted_state import append_missing_language_rows
from utilities.path_mappings import path_mappings


def load_formatter(profile_ids: Iterable[int | None] = ()) -> ResponseFormatter:
    general = settings.general
    desired: dict[int, frozenset[str]] = {}
    if general.embedded_subs_show_desired:
        profiles = {profile_id for profile_id in profile_ids if profile_id}
        if profiles:
            catalog = load_profile_catalog()
            equals = core._LanguageEquals(get_language_equals())
            for profile_id in profiles:
                profile = catalog.get(profile_id)
                languages: set[core.Language] = set()
                for code in profile.desired_languages if profile is not None else ():
                    custom = CustomLanguage.from_value(code, 'alpha2')
                    languages.add(custom.subzero_language() if custom else core.Language.fromietf(code))
                codes: set[str] = set()
                expanded: set[core.Language] = equals.translate(languages)
                for language in expanded:
                    custom = CustomLanguage.from_value(language, 'language')
                    codes.add(custom.alpha2 if custom else str(language.alpha2))
                desired[profile_id] = frozenset(codes)
    return ResponseFormatter(
        show_desired_subtitles=bool(general.embedded_subs_show_desired),
        undefined_audio_language=language_from_alpha2(general.default_und_audio_lang),
        base_url=base_url,
        series_path=path_mappings.path_replace,
        movie_path=path_mappings.path_replace_movie,
        desired_languages=desired,
    )


def load_series_audio_languages(
    series_ids: Select[tuple[int]], parse: Callable[[str], tuple[str | None, ...]],
) -> dict[int, tuple[str | None, ...]]:
    names: dict[int, set[str | None]] = defaultdict(set)
    with database.execute(
        select(TableEpisodes.sonarrSeriesId, TableEpisodes.audio_language)
        .where(TableEpisodes.sonarrSeriesId.in_(series_ids.correlate(None)))
        .distinct()
    ).yield_per(500) as rows:
        for series_id, value in rows:
            names[series_id].update(parse(value or '[]'))
    return {series_id: tuple(values) for series_id, values in names.items()}


def load_missing_languages(media_type: str, media_ids: Select[tuple[int]]) -> dict[int, list[str]]:
    languages: dict[int, list[str]] = defaultdict(list)
    codes: dict[str, str] = {}
    append_missing_language_rows(
        database,
        select(TableMissingSubtitles.media_id, TableMissingSubtitles.language)
        .where(TableMissingSubtitles.media_type == media_type)
        .where(TableMissingSubtitles.media_id.in_(media_ids.correlate(None)))
        .order_by(TableMissingSubtitles.id),
        languages, codes,
    )
    return languages


def load_history_languages(
    rows: Iterable[tuple[int, str | None, int | None]], candidates: Mapping[int, int | None],
) -> set[tuple[str | None, int | None]]:
    if not candidates:
        return set()
    catalog: ProfileCatalog = load_profile_catalog()
    desired: dict[tuple[str | None, int | None], bool] = {}
    for history_id, language, profile_id in rows:
        key = language, profile_id
        if history_id in candidates and key not in desired:
            profile = catalog.get(profile_id)
            desired[key] = profile is not None and language in profile.upgrade_languages
    return {key for key, wanted in desired.items() if wanted}
