# coding=utf-8

import operator

from flask_restx import Resource, Namespace, reqparse, fields, marshal
from functools import reduce

from app.database import (TableMovies, TableHistoryMovie, TableBlacklistMovie, database, select, func,
                          TableMoviesSubtitles)
from subtitles.upgrade import get_upgradable_movies_subtitles
from api.response_types import HistoryResponse, HistoryRow
from api.response_data import load_formatter, load_history_languages
from api.swaggerui import subtitles_language_model

from api.utils import authenticate

api_ns_movies_history = Namespace('Movies History', description='List movies history events')


@api_ns_movies_history.route('movies/history')
class MoviesHistory(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('start', type=int, required=False, default=0, help='Paging start integer')
    get_request_parser.add_argument('length', type=int, required=False, default=-1, help='Paging length integer')
    get_request_parser.add_argument('radarrid', type=int, required=False, help='Movie ID')

    get_language_model = api_ns_movies_history.model('subtitles_language_model', subtitles_language_model)

    data_model = api_ns_movies_history.model('history_movies_data_model', {
        'action': fields.Integer(),
        'title': fields.String(),
        'timestamp': fields.String(),
        'description': fields.String(),
        'radarrId': fields.Integer(),
        'monitored': fields.Boolean(),
        'path': fields.String(),
        'language': fields.Nested(get_language_model),
        'tags': fields.List(fields.String),
        'score': fields.String(),
        'subs_id': fields.String(),
        'provider': fields.String(),
        'subtitles_path': fields.String(),
        'upgradable': fields.Boolean(),
        'parsed_timestamp': fields.String(),
        'blacklisted': fields.Boolean(),
        'matches': fields.List(fields.String),
        'dont_matches': fields.List(fields.String),
    })

    get_response_model = api_ns_movies_history.model('MovieHistoryGetResponse', {
        'data': fields.Nested(data_model),
        'total': fields.Integer(),
    })

    @authenticate
    @api_ns_movies_history.response(401, 'Not Authenticated')
    @api_ns_movies_history.expect(get_request_parser)
    def get(self):
        """List movies history events"""
        args = self.get_request_parser.parse_args()
        start = args.get('start')
        length = args.get('length')
        radarrid = args.get('radarrid')

        blacklisted_subtitles = select(TableBlacklistMovie.provider,
                                       TableBlacklistMovie.subs_id) \
            .subquery()

        external_subtitles_sq = select(
            TableMoviesSubtitles.radarrId,
            func.min(TableMoviesSubtitles.path).label('path')
        ).group_by(TableMoviesSubtitles.radarrId).subquery()

        query_conditions = [(TableMovies.title.is_not(None))]
        if radarrid:
            query_conditions.append((TableMovies.radarrId == radarrid))

        stmt = select(TableHistoryMovie.id,
                      TableHistoryMovie.action,
                      TableMovies.title,
                      TableHistoryMovie.timestamp,
                      TableHistoryMovie.description,
                      TableHistoryMovie.radarrId,
                      TableMovies.monitored,
                      TableMovies.path,
                      TableHistoryMovie.language,
                      TableMovies.tags,
                      TableHistoryMovie.score,
                      TableHistoryMovie.score_out_of,
                      TableHistoryMovie.subs_id,
                      TableHistoryMovie.provider,
                      TableHistoryMovie.subtitles_path,
                      TableHistoryMovie.video_path,
                      TableHistoryMovie.matched,
                      TableHistoryMovie.not_matched,
                      TableMovies.profileId,
                      external_subtitles_sq.c.path.label('external_subtitles'),
                      blacklisted_subtitles.c.subs_id.label('blacklisted')) \
            .select_from(TableHistoryMovie) \
            .join(TableMovies) \
            .join(external_subtitles_sq,
                  onclause=TableHistoryMovie.radarrId == external_subtitles_sq.c.radarrId, isouter=True) \
            .join(blacklisted_subtitles, onclause=TableHistoryMovie.subs_id == blacklisted_subtitles.c.subs_id,
                  isouter=True) \
            .where(reduce(operator.and_, query_conditions)) \
            .order_by(TableHistoryMovie.timestamp.desc())
        if length > 0:
            stmt = stmt.limit(length).offset(start)
        movie_rows = database.execute(stmt).all()

        upgradable_movies_not_perfect = get_upgradable_movies_subtitles(history_id_list=[x.id for x in
                                                                                         movie_rows])

        formatter = load_formatter()
        desired_languages = load_history_languages(
            ((x.id, x.language, x.profileId) for x in movie_rows), upgradable_movies_not_perfect)

        movie_history: list[HistoryResponse] = []
        for x in movie_rows:
            record: HistoryRow = {
                'id': x.id,
                'action': x.action,
                'title': x.title,
                'timestamp': x.timestamp,
                'description': x.description,
                'radarrId': x.radarrId,
                'monitored': x.monitored,
                'path': x.path,
                'language': x.language,
                'profileId': x.profileId,
                'tags': x.tags,
                'score': x.score,
                'score_out_of': x.score_out_of,
                'subs_id': x.subs_id,
                'provider': x.provider,
                'subtitles_path': x.subtitles_path,
                'video_path': x.video_path,
                'matched': x.matched,
                'not_matched': x.not_matched,
                'external_subtitles': x.external_subtitles,
                'blacklisted': bool(x.blacklisted),
            }
            item = formatter.movie_history(
                record, can_upgrade=x.id in upgradable_movies_not_perfect
                and (x.language, x.profileId) in desired_languages)

            movie_history.append(item)

        count = database.execute(
            select(func.count())
            .select_from(TableHistoryMovie)
            .join(TableMovies)
            .where(TableMovies.title.is_not(None))) \
            .scalar()

        return marshal({'data': movie_history, 'total': count}, self.get_response_model)
