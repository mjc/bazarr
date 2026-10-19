# coding=utf-8

import operator
from functools import reduce

from api.response_types import HistoryPageResponse, HistoryResponse, HistoryRow
from api.response_data import load_formatter, load_history_languages
from api.swaggerui import subtitles_language_model
from app.database import (TableEpisodes, TableShows, TableHistory, TableBlacklist, database, select,
                          TableEpisodesSubtitles, func)
from subtitles.upgrade import get_upgradable_episode_subtitles

from flask_restx import Resource, Namespace, reqparse, fields
from ..utils import authenticate

api_ns_episodes_history = Namespace('Episodes History', description='List episodes history events')


@api_ns_episodes_history.route('episodes/history')
class EpisodesHistory(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('start', type=int, required=False, default=0, help='Paging start integer')
    get_request_parser.add_argument('length', type=int, required=False, default=-1, help='Paging length integer')
    get_request_parser.add_argument('episodeid', type=int, required=False, help='Episode ID')

    get_language_model = api_ns_episodes_history.model('subtitles_language_model', subtitles_language_model)

    data_model = api_ns_episodes_history.model('history_episodes_data_model', {
        'seriesTitle': fields.String(),
        'monitored': fields.Boolean(),
        'episode_number': fields.String(),
        'episodeTitle': fields.String(),
        'timestamp': fields.String(),
        'subs_id': fields.String(),
        'description': fields.String(),
        'sonarrSeriesId': fields.Integer(),
        'language': fields.Nested(get_language_model),
        'score': fields.String(),
        'tags': fields.List(fields.String),
        'action': fields.Integer(),
        'subtitles_path': fields.String(),
        'sonarrEpisodeId': fields.Integer(),
        'provider': fields.String(),
        'upgradable': fields.Boolean(),
        'parsed_timestamp': fields.String(),
        'blacklisted': fields.Boolean(),
        'matches': fields.List(fields.String),
        'dont_matches': fields.List(fields.String),
    })

    get_response_model = api_ns_episodes_history.model('EpisodeHistoryGetResponse', {
        'data': fields.Nested(data_model),
        'total': fields.Integer(),
    })

    @authenticate
    @api_ns_episodes_history.response(401, 'Not Authenticated')
    @api_ns_episodes_history.expect(get_request_parser)
    def get(self) -> HistoryPageResponse:
        """List episodes history events"""
        args = self.get_request_parser.parse_args()
        start = args.get('start')
        length = args.get('length')
        episodeid = args.get('episodeid')

        blacklisted_subtitles = select(TableBlacklist.provider,
                                       TableBlacklist.subs_id) \
            .subquery()

        external_subtitles_sq = select(
            TableEpisodesSubtitles.sonarrEpisodeId,
            func.min(TableEpisodesSubtitles.path).label('path')
        ).group_by(TableEpisodesSubtitles.sonarrEpisodeId).subquery()

        query_conditions = [(TableEpisodes.title.is_not(None))]
        if episodeid:
            query_conditions.append((TableEpisodes.sonarrEpisodeId == episodeid))

        stmt = select(TableHistory.id,
                      TableShows.title.label('seriesTitle'),
                      TableEpisodes.monitored,
                      TableEpisodes.season.concat('x').concat(TableEpisodes.episode).label('episode_number'),
                      TableEpisodes.title.label('episodeTitle'),
                      TableHistory.timestamp,
                      TableHistory.subs_id,
                      TableHistory.description,
                      TableHistory.sonarrSeriesId,
                      TableEpisodes.path,
                      TableHistory.language,
                      TableHistory.score,
                      TableHistory.score_out_of,
                      TableShows.tags,
                      TableHistory.action,
                      TableHistory.video_path,
                      TableHistory.subtitles_path,
                      TableHistory.sonarrEpisodeId,
                      TableHistory.provider,
                      TableShows.seriesType,
                      TableShows.profileId,
                      TableHistory.matched,
                      TableHistory.not_matched,
                      external_subtitles_sq.c.path.label('external_subtitles'),
                      blacklisted_subtitles.c.subs_id.label('blacklisted')) \
            .select_from(TableHistory) \
            .join(TableShows, onclause=TableHistory.sonarrSeriesId == TableShows.sonarrSeriesId) \
            .join(TableEpisodes, onclause=TableHistory.sonarrEpisodeId == TableEpisodes.sonarrEpisodeId) \
            .join(external_subtitles_sq,
                  onclause=TableHistory.sonarrEpisodeId == external_subtitles_sq.c.sonarrEpisodeId, isouter=True) \
            .join(blacklisted_subtitles, onclause=TableHistory.subs_id == blacklisted_subtitles.c.subs_id,
                  isouter=True) \
            .where(reduce(operator.and_, query_conditions)) \
            .order_by(TableHistory.timestamp.desc())
        if length > 0:
            stmt = stmt.limit(length).offset(start)
        episode_rows = database.execute(stmt).all()

        upgradable_episodes_not_perfect = get_upgradable_episode_subtitles(history_id_list=[x.id for x in
                                                                                            episode_rows])

        formatter = load_formatter()
        desired_languages = load_history_languages(
            ((x.id, x.language, x.profileId) for x in episode_rows), upgradable_episodes_not_perfect)

        episode_history: list[HistoryResponse] = []
        for x in episode_rows:
            record: HistoryRow = {
                'id': x.id,
                'seriesTitle': x.seriesTitle,
                'monitored': x.monitored,
                'episode_number': x.episode_number,
                'episodeTitle': x.episodeTitle,
                'timestamp': x.timestamp,
                'subs_id': x.subs_id,
                'description': x.description,
                'sonarrSeriesId': x.sonarrSeriesId,
                'path': x.path,
                'language': x.language,
                'profileId': x.profileId,
                'score': x.score,
                'score_out_of': x.score_out_of,
                'tags': x.tags,
                'action': x.action,
                'video_path': x.video_path,
                'subtitles_path': x.subtitles_path,
                'sonarrEpisodeId': x.sonarrEpisodeId,
                'provider': x.provider,
                'matched': x.matched,
                'not_matched': x.not_matched,
                'external_subtitles': x.external_subtitles,
                'blacklisted': bool(x.blacklisted),
            }
            item = formatter.episode_history(
                record, can_upgrade=x.id in upgradable_episodes_not_perfect
                and (x.language, x.profileId) in desired_languages)

            episode_history.append(item)

        count = database.execute(
            select(func.count())
            .select_from(TableHistory)
            .join(TableEpisodes)
            .where(TableEpisodes.title.is_not(None))) \
            .scalar()

        return {'data': episode_history, 'total': count}
