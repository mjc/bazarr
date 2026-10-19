# coding=utf-8

from collections.abc import Callable, Iterable, Mapping

from api.formatting import ResponseFormatter
from app.config import base_url, settings
from app.database import SUBTITLE_MAP_QUERY_BATCH_SIZE, TableEpisodes, database, get_desired_languages, select
from languages.custom_lang import CustomLanguage
from languages.get_languages import language_from_alpha2
from subliminal_patch import core
from subtitles.pool import get_language_equals
from subtitles.upgrade import _language_still_desired
from utilities.path_mappings import path_mappings


def load_formatter(profile_ids: Iterable[int | None] = ()) -> ResponseFormatter:
    general = settings.general
    desired: dict[int, frozenset[str]] = {}
    if general.embedded_subs_show_desired:
        profiles = {profile_id for profile_id in profile_ids if profile_id}
        if profiles:
            equals = core._LanguageEquals(get_language_equals())
            for profile_id in profiles:
                languages: set[core.Language] = set()
                for code in get_desired_languages(profile_id) or []:
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
    series_ids: Iterable[int], parse: Callable[[str], tuple[str | None, ...]],
) -> dict[int, tuple[str | None, ...]]:
    ids = list(dict.fromkeys(series_ids))
    names: dict[int, set[str | None]] = {series_id: set() for series_id in ids}
    for offset in range(0, len(ids), SUBTITLE_MAP_QUERY_BATCH_SIZE):
        rows = database.execute(
            select(TableEpisodes.sonarrSeriesId, TableEpisodes.audio_language)
            .where(TableEpisodes.sonarrSeriesId.in_(ids[offset:offset + SUBTITLE_MAP_QUERY_BATCH_SIZE]))
            .distinct()
        )
        for series_id, value in rows:
            names[series_id].update(parse(value or '[]'))
    return {series_id: tuple(values) for series_id, values in names.items()}


def load_history_languages(
    rows: Iterable[tuple[int, str | None, int | None]], candidates: Mapping[int, int | None],
) -> set[tuple[str | None, int | None]]:
    desired: dict[tuple[str | None, int | None], bool] = {}
    for history_id, language, profile_id in rows:
        key = language, profile_id
        if history_id in candidates and key not in desired:
            desired[key] = _language_still_desired(language, profile_id)
    return {key for key, wanted in desired.items() if wanted}
