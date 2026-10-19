# coding=utf-8

import datetime
import operator
import itertools
from collections.abc import Iterable
from typing import TypedDict

from dateutil import rrule
from flask_restx import Resource, Namespace, reqparse, fields
from functools import reduce

from app.database import TableHistory, TableHistoryMovie, database, select

from ..utils import authenticate

api_ns_history_stats = Namespace('History Statistics', description='Get history statistics')


class HistoryCount(TypedDict):
    date: str
    count: int


class HistoryStatsResponse(TypedDict):
    series: list[HistoryCount]
    movies: list[HistoryCount]


def _history_counts(timestamps: Iterable[datetime.datetime]) -> list[HistoryCount]:
    return [
        {'date': date, 'count': sum(1 for _ in group)}
        for date, group in itertools.groupby(timestamps, key=lambda timestamp: timestamp.strftime('%Y-%m-%d'))
    ]


@api_ns_history_stats.route('history/stats')
class HistoryStats(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('timeFrame', type=str, default='month',
                                    help='Timeframe to get stats for. Must be in ["week", "month", "trimester", '
                                         '"year"]')
    get_request_parser.add_argument('action', type=str, default='All', help='Action type to filter for.')
    get_request_parser.add_argument('provider', type=str, default='All', help='Provider name to filter for.')
    get_request_parser.add_argument('language', type=str, default='All', help='Language name to filter for')

    series_data_model = api_ns_history_stats.model('history_series_stats_data_model', {
        'date': fields.String(),
        'count': fields.Integer(),
    })

    movies_data_model = api_ns_history_stats.model('history_movies_stats_data_model', {
        'date': fields.String(),
        'count': fields.Integer(),
    })

    get_response_model = api_ns_history_stats.model('HistoryStatsGetResponse', {
        'series': fields.Nested(series_data_model),
        'movies': fields.Nested(movies_data_model),
    })

    @authenticate
    @api_ns_history_stats.response(401, 'Not Authenticated')
    @api_ns_history_stats.expect(get_request_parser)
    def get(self) -> HistoryStatsResponse:
        """Get history statistics"""
        args = self.get_request_parser.parse_args()
        timeframe = args.get('timeFrame')
        action = args.get('action')
        provider = args.get('provider')
        language = args.get('language')

        # timeframe must be in ['week', 'month', 'trimester', 'year']
        if timeframe == 'year':
            delay = 364 * 24 * 60 * 60
        elif timeframe == 'trimester':
            delay = 90 * 24 * 60 * 60
        elif timeframe == 'month':
            delay = 30 * 24 * 60 * 60
        elif timeframe == 'week':
            delay = 6 * 24 * 60 * 60

        now = datetime.datetime.now()
        past = now - datetime.timedelta(seconds=delay)

        history_where_clauses = [(TableHistory.timestamp.between(past, now))]
        history_where_clauses_movie = [(TableHistoryMovie.timestamp.between(past, now))]

        if action != 'All':
            history_where_clauses.append((TableHistory.action == action))
            history_where_clauses_movie.append((TableHistoryMovie.action == action))
        else:
            history_where_clauses.append((TableHistory.action.in_([1, 2, 3])))
            history_where_clauses_movie.append((TableHistoryMovie.action.in_([1, 2, 3])))

        if provider != 'All':
            history_where_clauses.append((TableHistory.provider == provider))
            history_where_clauses_movie.append((TableHistoryMovie.provider == provider))

        if language != 'All':
            history_where_clauses.append((TableHistory.language == language))
            history_where_clauses_movie.append((TableHistoryMovie.language == language))

        history_where_clause = reduce(operator.and_, history_where_clauses)
        history_where_clause_movie = reduce(operator.and_, history_where_clauses_movie)

        data_series = _history_counts(
            row.timestamp for row in database.execute(
                select(TableHistory.timestamp, TableHistory.id)
                .where(history_where_clause)))
        data_movies = _history_counts(
            row.timestamp for row in database.execute(
                select(TableHistoryMovie.timestamp, TableHistoryMovie.id)
                .where(history_where_clause_movie)))

        series_dates = {entry['date'] for entry in data_series}
        movie_dates = {entry['date'] for entry in data_movies}
        for dt in rrule.rrule(rrule.DAILY,
                              dtstart=datetime.datetime.now() - datetime.timedelta(seconds=delay),
                              until=datetime.datetime.now()):
            date = dt.strftime('%Y-%m-%d')
            if date not in series_dates:
                data_series.append({'date': date, 'count': 0})
            if date not in movie_dates:
                data_movies.append({'date': date, 'count': 0})

        data_series.sort(key=lambda entry: entry['date'])
        data_movies.sort(key=lambda entry: entry['date'])

        return {'series': data_series, 'movies': data_movies}
