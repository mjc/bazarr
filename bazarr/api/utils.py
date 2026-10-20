# coding=utf-8

from functools import wraps
from typing import Literal
from flask import request, abort
from sqlalchemy import or_, and_

from app.config import settings
from app.database import TableEpisodes, TableShows, select
from utilities.helper import bool_map
from api.response_types import JsonObject, JsonValue

None_Keys = ['null', 'undefined', '', None]
False_Keys = ['False', 'false', '0']


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
    # aggregated from their episodes (see series response formatting), so the filter must
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
