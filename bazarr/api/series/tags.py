# coding=utf-8

from flask_restx import Resource, Namespace, fields

from api.response_types import ListResponse
from api.marshalling import marshal_rows

from app.database import TableShows, get_media_tags

from ..utils import authenticate

api_ns_series_tags = Namespace('Series Tags', description='List tags assigned to series')


@api_ns_series_tags.route('series/tags')
class SeriesTags(Resource):
    get_response_model = api_ns_series_tags.model('SeriesTagsGetResponse', {
        'tag': fields.String(),
    })

    @authenticate
    @api_ns_series_tags.response(200, 'Success')
    @api_ns_series_tags.response(401, 'Not Authenticated')
    def get(self) -> ListResponse:
        """List all distinct tags assigned to any series"""
        return {'data': marshal_rows(({'tag': tag} for tag in get_media_tags(TableShows.tags)),
                                     self.get_response_model)}
