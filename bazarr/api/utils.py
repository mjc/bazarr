# coding=utf-8

from dataclasses import dataclass, field
from functools import wraps
from typing import Literal
from flask import request, abort, has_request_context

from languages.custom_lang import CustomLanguage
from sqlalchemy import or_, and_

from subliminal_patch import core

from app.config import settings, base_url
from languages.get_languages import language_from_alpha2, alpha3_from_alpha2
from app.database import (get_audio_profile_languages, get_desired_languages, get_subtitles, database, TableEpisodes,
                          TableShows, select)
from subtitles.pool import get_language_equals
from utilities.helper import bool_map
from subtitles.serialization import parse_missing_subtitles
from utilities.path_mappings import path_mappings
from utilities.text_list import parse_text_list, parse_text_list_or_default

from api.response_types import (JsonObject, JsonValue, LanguageInfo, MediaResponse, MediaRow,
                                SubtitleLanguage)

None_Keys = ['null', 'undefined', '', None]

False_Keys = ['False', 'false', '0']


@dataclass
class _PostprocessContext:
    show_desired_subtitles: bool
    text_lists: dict[str, tuple[str | None, ...]] = field(default_factory=dict)
    audio_languages: dict[tuple[str | None, ...], list[LanguageInfo]] = field(default_factory=dict)
    desired_languages: dict[int | str, frozenset[str]] = field(default_factory=dict)
    language_equals: core._LanguageEquals | None = None

    def parse_list(self, value: str) -> tuple[str | None, ...]:
        if value not in self.text_lists:
            self.text_lists[value] = tuple(parse_text_list(value))
        return self.text_lists[value]

    def format_audio_languages(self, languages: tuple[str | None, ...]) -> list[LanguageInfo]:
        if languages not in self.audio_languages:
            self.audio_languages[languages] = [
                {'name': language['name'], 'code2': language['code2'], 'code3': language['code3']}
                for language in get_audio_profile_languages(languages)
            ]
        return [language.copy() for language in self.audio_languages[languages]]

    def desired_language_codes(self, profile_id: int | str) -> frozenset[str]:
        if profile_id not in self.desired_languages:
            if self.language_equals is None:
                self.language_equals = core._LanguageEquals(get_language_equals())
            desired_lang_objects: set[core.Language] = set()
            for lang_code in get_desired_languages(profile_id) or []:
                custom = CustomLanguage.from_value(lang_code, 'alpha2')
                if custom:
                    desired_lang_objects.add(custom.subzero_language())
                else:
                    desired_lang_objects.add(core.Language.fromietf(lang_code))
            expanded_desired_lang_codes: set[str] = set()
            expanded_languages: set[core.Language] = self.language_equals.translate(desired_lang_objects)
            for lang_obj in expanded_languages:
                custom = CustomLanguage.from_value(lang_obj, 'language')
                expanded_desired_lang_codes.add(custom.alpha2 if custom else str(lang_obj.alpha2))
            self.desired_languages[profile_id] = frozenset(expanded_desired_lang_codes)
        return self.desired_languages[profile_id]


def _postprocess_context() -> _PostprocessContext:
    context: _PostprocessContext | None = None
    if has_request_context():
        context = request.environ.get('bazarr.postprocess')
    if context is None:
        context = _PostprocessContext(bool(settings.general.embedded_subs_show_desired))
        if has_request_context():
            request.environ['bazarr.postprocess'] = context
    return context


def profile_id_type(value):
    # reqparse type for the profileid filter: a profile ID or "none" to list
    # items without a languages profile. Raises ValueError (400) otherwise.
    if value == 'none':
        return value
    return int(value)


def add_list_query_args(parser):
    parser.add_argument('sort_by', type=str, required=False, default='title',
                        help='Column to sort by')
    parser.add_argument('sort_order', type=str, required=False, default='asc', choices=('asc', 'desc'),
                        help='Sort direction')
    parser.add_argument('monitored', type=str, required=False, choices=('true', 'false'),
                        help='Filter by monitored status')
    parser.add_argument('profileid', type=profile_id_type, required=False,
                        help='Filter by languages profile ID or "none"')
    parser.add_argument('missing', type=str, required=False, choices=('true', 'false'),
                        help='Filter by missing subtitles')
    parser.add_argument('ended', type=str, required=False, choices=('true', 'false'),
                        help='Filter by series ended status')
    parser.add_argument('audio_language', type=str, required=False,
                        help='Filter by audio language name')
    parser.add_argument('tags[]', type=str, action='append', required=False, default=[],
                        help='Tags to filter by')


def profile_filter_clause(column, value):
    if value == 'none':
        return column.is_(None)
    return column == value


def monitored_filter_clause(column, value):
    # monitored is stored as the string 'True'/'False', not a boolean
    return column == str(bool_map[value])


def ended_filter_clause(column, value):
    # ended is stored as the string 'True'/'False', not a boolean
    return column == str(bool_map[value])


def tags_filter_clause(column, tags):
    # Tags are stored as a stringified list (e.g. "['tag1', 'tag2']"), match
    # any of the requested tags using the same pattern as get_exclusion_clause.
    return or_(*[column.contains(f"'{tag}'") for tag in tags])


def audio_language_filter_clause(column, language):
    # Audio languages are stored as a stringified list of language names
    # (e.g. "['English', 'French']").
    return column.contains(f"'{language}'")


def series_audio_language_filter_clause(language):
    # Series with an empty audio_language display the audio languages
    # aggregated from their episodes (see postprocess), so the filter must
    # match both the series value and the episodes values to stay consistent
    # with what the table shows.
    pattern = f"'{language}'"
    return or_(
        TableShows.audio_language.contains(pattern),
        and_(
            or_(TableShows.audio_language == '[]', TableShows.audio_language.is_(None)),
            TableShows.sonarrSeriesId.in_(
                select(TableEpisodes.sonarrSeriesId)
                .where(TableEpisodes.audio_language.contains(pattern))
            ),
        ),
    )


def apply_sort(stmt, sort_columns, default_column, sort_by, sort_order):
    # sort_columns is a whitelist of allowed sort columns; unknown sort_by
    # values fall back to the default column.
    sort_column = sort_columns.get(sort_by, default_column)
    return stmt.order_by(sort_column.asc() if sort_order == 'asc' else sort_column.desc())
def normalize_flag_token(value: JsonValue) -> Literal['True', 'False']:
    return 'True' if isinstance(value, str) and value.strip().lower() == 'true' else 'False'


def authenticate(actual_method):
    @wraps(actual_method)
    def wrapper(*args, **kwargs):
        apikey_settings = settings.auth.apikey
        apikey_get = request.args.get('apikey')
        apikey_post = request.form.get('apikey')
        apikey_header = None
        if 'X-API-KEY' in request.headers:
            apikey_header = request.headers['X-API-KEY']

        # An unset configured key must never match a request without a key
        if apikey_settings and apikey_settings in [apikey_get, apikey_post, apikey_header]:
            return actual_method(*args, **kwargs)

        return abort(401)

    return wrapper


def get_series_audio_languages(series_ids: list[int]) -> dict[int, list[str | None]]:
    languages: dict[int, set[str | None]] = {series_id: set() for series_id in series_ids}
    parsed_values: dict[str | None, list[str | None]] = {}
    for offset in range(0, len(series_ids), 900):
        rows = database.execute(
            select(TableEpisodes.sonarrSeriesId, TableEpisodes.audio_language)
            .where(TableEpisodes.sonarrSeriesId.in_(series_ids[offset:offset + 900]))
            .distinct()
        )
        for series_id, value in rows:
            if value not in parsed_values:
                parsed_values[value] = parse_text_list_or_default(value)
            languages[series_id].update(parsed_values[value])
    return {series_id: list(names) for series_id, names in languages.items()}


def postprocess(item: MediaRow, subtitles: list[JsonObject] | None = None) -> MediaResponse:
    context = _postprocess_context()
    path_replace = path_mappings.path_replace_movie if item.get('radarrId') else path_mappings.path_replace
    profile_id = item.get('profileId')
    if profile_id in None_Keys:
        profile_id = None

    if subtitles is None:
        subtitles = get_subtitles(sonarr_episode_id=item.get('sonarrEpisodeId'), radarr_id=item.get('radarrId'))
    if context.show_desired_subtitles:
        if profile_id:
            desired_codes = context.desired_language_codes(profile_id)
            subtitles = [subtitle for subtitle in subtitles
                         if subtitle.get('code2') in desired_codes or subtitle.get('path')]
        else:
            subtitles = [subtitle for subtitle in subtitles if subtitle.get('path')]

    missing_subtitles: list[SubtitleLanguage] = []
    for subs in parse_missing_subtitles(item.get('missing_subtitles') or []):
        language = subs.split(':')
        missing_subtitles.append({
            'name': language_from_alpha2(language[0]),
            'code2': language[0],
            'code3': alpha3_from_alpha2(language[0]),
            'forced': len(language) > 1 and language[1] == 'forced',
            'hi': len(language) > 1 and language[1] == 'hi',
        })

    alternative_titles = item.get('alternativeTitles')
    tags = item.get('tags')
    result: MediaResponse = {
        **item,
        'alternativeTitles': list(context.parse_list(alternative_titles)) if alternative_titles else [],
        'tags': list(context.parse_list(tags)) if tags is not None else [],
        'subtitles': sorted(subtitles, key=lambda subtitle: (subtitle.get('name', ''), subtitle.get('forced', False))),
        'missing_subtitles': missing_subtitles,
        'monitored': item.get('monitored') == 'True',
        'hearing_impaired': item.get('hearing_impaired') == 'True',
    }
    if result.get('ffprobe_cache'):
        del result['ffprobe_cache']
    if 'profileId' in item:
        result['profileId'] = profile_id

    series_id = item.get('sonarrSeriesId')
    audio_language = item.get('audio_language')
    if audio_language:
        if series_id and not item.get('sonarrEpisodeId') and audio_language == '[]':
            audio_languages = tuple(get_series_audio_languages([series_id])[series_id])
        else:
            try:
                audio_languages = context.parse_list(audio_language)
            except ValueError:
                audio_languages = ()
        result['audio_language'] = context.format_audio_languages(audio_languages)

    subtitle_language = item.get('language')
    if subtitle_language:
        if subtitle_language == 'None':
            result['language'] = None
        else:
            language = subtitle_language.split(':')
            result['language'] = {
                'name': language_from_alpha2(language[0]),
                'code2': language[0],
                'code3': alpha3_from_alpha2(language[0]),
                'forced': subtitle_language.endswith(':forced'),
                'hi': subtitle_language.endswith(':hi'),
            }

    path = item.get('path')
    if path:
        result['path'] = path_replace(path)
    video_path = item.get('video_path')
    if video_path:
        result['video_path'] = path_replace(video_path)
    subtitles_path = item.get('subtitles_path')
    if subtitles_path:
        result['subtitles_path'] = path_replace(subtitles_path)
    external_subtitles = item.get('external_subtitles')
    if external_subtitles:
        result['external_subtitles'] = path_replace(external_subtitles)

    media_type = 'movies' if item.get('radarrId') else 'series'
    poster = item.get('poster')
    if poster is not None:
        result['poster'] = f"{base_url}/images/{media_type}{poster}" if poster else None
    fanart = item.get('fanart')
    if fanart is not None:
        result['fanart'] = f"{base_url}/images/{media_type}{fanart}" if fanart else None

    return result
