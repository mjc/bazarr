# coding=utf-8

from dataclasses import dataclass, field
from functools import wraps
from typing import TypeAlias
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

JsonValue: TypeAlias = str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue']
JsonObject: TypeAlias = dict[str, JsonValue]

None_Keys = ['null', 'undefined', '', None]

False_Keys = ['False', 'false', '0']


@dataclass
class _PostprocessContext:
    show_desired_subtitles: bool
    text_lists: dict[str, tuple[str | None, ...]] = field(default_factory=dict)
    audio_languages: dict[tuple[str | None, ...], list[JsonObject]] = field(default_factory=dict)
    desired_languages: dict[int | str, frozenset[str]] = field(default_factory=dict)
    language_equals: core._LanguageEquals | None = None

    def parse_list(self, value: JsonValue) -> list[str | None]:
        if not isinstance(value, str):
            raise ValueError('Expected a serialized text list')
        if value not in self.text_lists:
            parsed: list[str | None] = []
            for entry in parse_text_list(value):
                if entry is not None and not isinstance(entry, str):
                    raise ValueError('Expected strings or None in text list')
                parsed.append(entry)
            self.text_lists[value] = tuple(parsed)
        return list(self.text_lists[value])

    def format_audio_languages(self, value: JsonValue) -> list[JsonValue]:
        languages: list[str | None]
        if isinstance(value, str):
            try:
                languages = self.parse_list(value)
            except ValueError:
                languages = []
        elif isinstance(value, list):
            languages = []
            for language in value:
                if language is not None and not isinstance(language, str):
                    raise ValueError('Expected audio language names')
                languages.append(language)
        else:
            raise ValueError('Expected audio language names')
        key = tuple(languages)
        if key not in self.audio_languages:
            self.audio_languages[key] = [
                {'name': language['name'], 'code2': language['code2'], 'code3': language['code3']}
                for language in get_audio_profile_languages(languages)
            ]
        return [dict(language) for language in self.audio_languages[key]]

    def desired_language_codes(self, profile_id: JsonValue) -> frozenset[str]:
        if not isinstance(profile_id, (int, str)):
            raise ValueError('Expected a language profile ID')
        if profile_id not in self.desired_languages:
            if self.language_equals is None:
                self.language_equals = core._LanguageEquals(get_language_equals())
            desired_lang_objects = set()
            for lang_code in get_desired_languages(profile_id) or []:
                custom = CustomLanguage.from_value(lang_code, 'alpha2')
                if custom:
                    desired_lang_objects.add(custom.subzero_language())
                else:
                    desired_lang_objects.add(core.Language.fromietf(lang_code))
            expanded_desired_lang_codes: set[str] = set()
            for lang_obj in self.language_equals.translate(desired_lang_objects):
                if isinstance(lang_obj, core.Language):
                    custom = CustomLanguage.from_value(lang_obj, 'language')
                    if custom:
                        expanded_desired_lang_codes.add(custom.alpha2)
                    else:
                        code2 = lang_obj.alpha2
                        if not isinstance(code2, str):
                            raise ValueError('Expected an alpha-2 language code')
                        expanded_desired_lang_codes.add(code2)
                else:
                    expanded_desired_lang_codes.add(str(lang_obj))
            self.desired_languages[profile_id] = frozenset(expanded_desired_lang_codes)
        return self.desired_languages[profile_id]


def _postprocess_context() -> _PostprocessContext:
    if has_request_context():
        context = request.environ.get('bazarr.postprocess')
        if isinstance(context, _PostprocessContext):
            return context
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
def normalize_flag_token(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return "True"
        if normalized == "false":
            return "False"
    return "False"


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


def postprocess(item: JsonObject, subtitles: list[JsonObject] | None = None) -> JsonObject:
    context = _postprocess_context()
    # Remove ffprobe_cache
    if item.get('radarrId'):
        path_replace = path_mappings.path_replace_movie
    else:
        path_replace = path_mappings.path_replace
    if item.get('ffprobe_cache'):
        del item['ffprobe_cache']

    # Parse audio language
    if item.get('audio_language'):
        if item.get('sonarrSeriesId') and not item.get('sonarrEpisodeId') and item['audio_language'] == '[]':
            languages = get_series_audio_languages([item['sonarrSeriesId']])
            item['audio_language'] = context.format_audio_languages(languages[item['sonarrSeriesId']])
        else:
            item['audio_language'] = context.format_audio_languages(item['audio_language'])

    # Make sure profileId is a valid None value but only if it exists in item
    if item.get('profileId', False) in None_Keys:
        item['profileId'] = None

    # Parse alternate titles
    if item.get('alternativeTitles'):
        item['alternativeTitles'] = [value for value in context.parse_list(item['alternativeTitles'])]
    else:
        item['alternativeTitles'] = []

    # Add subtitles
    if subtitles is None:
        episode_id = item.get('sonarrEpisodeId')
        movie_id = item.get('radarrId')
        subtitles = get_subtitles(sonarr_episode_id=episode_id if isinstance(episode_id, int) else None,
                                  radarr_id=movie_id if isinstance(movie_id, int) else None)

    if context.show_desired_subtitles:
        if item.get('profileId'):
            expanded_desired_lang_codes = context.desired_language_codes(item['profileId'])

            # Filter subtitles: keep if code2 is in expanded desired languages or if it has a path (external)
            subtitles = [x for x in subtitles
                         if isinstance(x, dict) and
                         (x.get('code2') in expanded_desired_lang_codes or x.get('path'))]
        else:
            subtitles = [x for x in subtitles if isinstance(x, dict) and x.get('path')]

    subtitles = sorted(subtitles, key=lambda x: (x.get('name', ''), x.get('forced', False)))
    item['subtitles'] = [subtitle for subtitle in subtitles]

    # Parse missing subtitles
    if item.get('missing_subtitles'):
        missing_subtitles: list[JsonValue] = []
        for subs in parse_missing_subtitles(item['missing_subtitles']):
            language = subs.split(':')
            missing_subtitles.append({"name": language_from_alpha2(language[0]),
                                      "code2": language[0],
                                      "code3": alpha3_from_alpha2(language[0]),
                                      "forced": len(language) > 1 and language[1] == 'forced',
                                      "hi": len(language) > 1 and language[1] == 'hi'})
        item['missing_subtitles'] = missing_subtitles
    else:
        item['missing_subtitles'] = []

    # Parse tags
    if item.get('tags') is not None:
        item['tags'] = [value for value in context.parse_list(item.get('tags', '[]'))]
    else:
        item['tags'] = []
    if item.get('monitored'):
        item['monitored'] = item.get('monitored') == 'True'
    else:
        item['monitored'] = False
    if item.get('hearing_impaired'):
        item['hearing_impaired'] = item.get('hearing_impaired') == 'True'
    else:
        item['hearing_impaired'] = False

    subtitle_language = item.get('language')
    if isinstance(subtitle_language, str) and subtitle_language:
        if subtitle_language == 'None':
            item['language'] = None
        else:
            splitted_language = subtitle_language.split(':')
            item['language'] = {
                "name": language_from_alpha2(splitted_language[0]),
                "code2": splitted_language[0],
                "code3": alpha3_from_alpha2(splitted_language[0]),
                "forced": subtitle_language.endswith(':forced'),
                "hi": subtitle_language.endswith(':hi'),
            }

    if item.get('path'):
        item['path'] = path_replace(item['path'])

    if item.get('video_path'):
        # Provide mapped video path for history
        item['video_path'] = path_replace(item['video_path'])

    if item.get('subtitles_path'):
        # Provide mapped subtitles path
        item['subtitles_path'] = path_replace(item['subtitles_path'])

    if item.get('external_subtitles'):
        # Provide mapped external subtitles paths for history
        item['external_subtitles'] = path_replace(item['external_subtitles'])

    # map poster and fanart to server proxy
    if item.get('poster') is not None:
        poster = item['poster']
        item['poster'] = f"{base_url}/images/{'movies' if item.get('radarrId') else 'series'}{poster}" if poster else None

    if item.get('fanart') is not None:
        fanart = item['fanart']
        item['fanart'] = f"{base_url}/images/{'movies' if item.get('radarrId') else 'series'}{fanart}" if fanart else None

    return item
