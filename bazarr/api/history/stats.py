# coding=utf-8

import datetime
from dataclasses import dataclass
from typing import TypedDict

from sqlalchemy import Date, Select, func
from flask_restx import Resource, Namespace, reqparse, fields

from app.database import TableHistory, TableHistoryMovie, database, select

from ..utils import authenticate

api_ns_history_stats = Namespace('History Statistics', description='Get history statistics')


class HistoryCount(TypedDict):
    date: str
    count: int


class HistoryStatsResponse(TypedDict):
    series: list[HistoryCount]
    movies: list[HistoryCount]


WINDOW_DAYS = {'week': 6, 'month': 30, 'trimester': 90, 'year': 364}


@dataclass(frozen=True)
class HistoryQuery:
    start: datetime.datetime
    end: datetime.datetime
    action: str
    provider: str
    language: str

    def statement(self, table: type[TableHistory] | type[TableHistoryMovie]) -> Select[tuple[datetime.date, int]]:
        day = func.date(table.timestamp, type_=Date)
        statement = select(day, func.count()).where(table.timestamp.between(self.start, self.end))
        statement = statement.where(table.action.in_([1, 2, 3]) if self.action == 'All'
                                    else table.action == self.action)
        if self.provider != 'All':
            statement = statement.where(table.provider == self.provider)
        if self.language != 'All':
            statement = statement.where(table.language == self.language)
        return statement.group_by(day)

    def counts(self, table: type[TableHistory] | type[TableHistoryMovie]) -> dict[datetime.date, int]:
        return {day: count for day, count in database.execute(self.statement(table))}

    def response(self) -> HistoryStatsResponse:
        series_counts = self.counts(TableHistory)
        movie_counts = self.counts(TableHistoryMovie)
        series: list[HistoryCount] = []
        movies: list[HistoryCount] = []
        first_day = self.start.date()
        for offset in range((self.end.date() - first_day).days + 1):
            day = first_day + datetime.timedelta(days=offset)
            label = day.isoformat()
            series.append({'date': label, 'count': series_counts.get(day, 0)})
            movies.append({'date': label, 'count': movie_counts.get(day, 0)})
        return {'series': series, 'movies': movies}


@api_ns_history_stats.route('history/stats')
class HistoryStats(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('timeFrame', type=str, default='month', choices=tuple(WINDOW_DAYS),
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
        now = datetime.datetime.now()
        query = HistoryQuery(
            start=now - datetime.timedelta(days=WINDOW_DAYS[args['timeFrame']]), end=now,
            action=args['action'], provider=args['provider'], language=args['language'],
        )
        return query.response()
