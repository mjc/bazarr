# coding=utf-8

from flask_restx import Resource, Namespace, fields

from app.database import TableMovies, get_media_tags
from api.response_types import TagsResponse

from ..utils import authenticate

api_ns_movies_tags = Namespace('Movies Tags', description='List tags assigned to movies')


@api_ns_movies_tags.route('movies/tags')
class MoviesTags(Resource):
    get_response_model = api_ns_movies_tags.model('MoviesTagsGetResponse', {
        'tag': fields.String(),
    })

    @authenticate
    @api_ns_movies_tags.response(200, 'Success')
    @api_ns_movies_tags.response(401, 'Not Authenticated')
    def get(self) -> TagsResponse:
        """List all distinct tags assigned to any movie"""
        return {'data': [{'tag': tag} for tag in get_media_tags(TableMovies.tags)]}
