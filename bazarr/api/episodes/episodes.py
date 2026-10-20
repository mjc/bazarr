# coding=utf-8

from flask_restx import Resource, Namespace, reqparse, fields

from app.database import TableEpisodes, TableShows, database, select, get_subtitles_for_query
from api.swaggerui import subtitles_model, subtitles_language_model, audio_language_model

from ..utils import authenticate
from api.response_types import EpisodesResponse
from api.response_data import load_formatter, load_missing_languages

api_ns_episodes = Namespace('Episodes', description='List episodes metadata for specific series or episodes.')


@api_ns_episodes.route('episodes')
class Episodes(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('seriesid[]', type=int, action='append', required=False, default=[],
                                    help='Series IDs to list episodes for')
    get_request_parser.add_argument('episodeid[]', type=int, action='append', required=False, default=[],
                                    help='Episodes ID to list')

    get_subtitles_model = api_ns_episodes.model('subtitles_model', subtitles_model)
    get_subtitles_language_model = api_ns_episodes.model('subtitles_language_model', subtitles_language_model)
    get_audio_language_model = api_ns_episodes.model('audio_language_model', audio_language_model)

    get_response_model = api_ns_episodes.model('EpisodeGetResponse', {
        'audio_language': fields.Nested(get_audio_language_model),
        'episode': fields.Integer(),
        'missing_subtitles': fields.Nested(get_subtitles_language_model),
        'monitored': fields.Boolean(),
        'path': fields.String(),
        'season': fields.Integer(),
        'sonarrEpisodeId': fields.Integer(),
        'sonarrSeriesId': fields.Integer(),
        'subtitles': fields.Nested(get_subtitles_model),
        'title': fields.String(),
        'sceneName': fields.String(),
    })

    @authenticate
    @api_ns_episodes.expect(get_request_parser)
    @api_ns_episodes.response(200, 'Success')
    @api_ns_episodes.response(401, 'Not Authenticated')
    @api_ns_episodes.response(404, 'Series or Episode ID not provided')
    def get(self) -> EpisodesResponse | tuple[str, int]:
        """List episodes metadata for specific series or episodes"""
        args = self.get_request_parser.parse_args()
        seriesId = args.get('seriesid[]')
        episodeId = args.get('episodeid[]')

        stmt = select(
                TableEpisodes.audio_language,
                TableEpisodes.episode,
                TableEpisodes.monitored,
                TableEpisodes.path,
                TableEpisodes.season,
                TableEpisodes.sonarrEpisodeId,
                TableEpisodes.sonarrSeriesId,
                TableEpisodes.title,
                TableEpisodes.sceneName,
                TableShows.profileId
            ).join(TableShows)

        if episodeId:
            stmt = stmt.where(TableEpisodes.sonarrEpisodeId.in_(episodeId))
        elif seriesId:
            stmt = stmt.where(TableEpisodes.sonarrSeriesId.in_(seriesId)) \
                .order_by(TableEpisodes.season.desc(), TableEpisodes.episode.desc())
        else:
            return "Series or Episode ID not provided", 404

        stmt_query = database.execute(stmt).all()
        media_ids = stmt.with_only_columns(TableEpisodes.sonarrEpisodeId, maintain_column_froms=True).order_by(None)
        missing_languages = load_missing_languages('series', media_ids) if stmt_query else {}
        subtitles_map = get_subtitles_for_query('series', media_ids) if stmt_query else {}
        formatter = load_formatter(x.profileId for x in stmt_query)

        return {'data': [formatter.episode({
                'audio_language': x.audio_language,
                'episode': x.episode,
                'missing_subtitles': missing_languages.get(x.sonarrEpisodeId, ()),
                'monitored': x.monitored,
                'path': x.path,
                'season': x.season,
                'sonarrEpisodeId': x.sonarrEpisodeId,
                'sonarrSeriesId': x.sonarrSeriesId,
                'title': x.title,
                'sceneName': x.sceneName,
                'profileId': x.profileId,  # required to filter desired subtitles when settings.general.embedded_subs_show_desired
                }, subtitles=subtitles_map.get(x.sonarrEpisodeId, ())) for x in stmt_query]}
