# coding=utf-8

from flask_restx import Resource, Namespace, reqparse, fields
from api.formatting import format_directories
from api.response_types import FileBrowserEntry

from radarr.filesystem import browse_radarr_filesystem

from ..utils import authenticate

api_ns_files_radarr = Namespace('Files Browser for Radarr', description='Browse content of file system as seen by '
                                                                        'Radarr')


@api_ns_files_radarr.route('files/radarr')
class BrowseRadarrFS(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('path', type=str, default='', help='Path to browse')

    get_response_model = api_ns_files_radarr.model('RadarrFileBrowserGetResponse', {
        'name': fields.String(),
        'children': fields.Boolean(),
        'path': fields.String(),
    })

    @authenticate
    @api_ns_files_radarr.response(401, 'Not Authenticated')
    @api_ns_files_radarr.expect(get_request_parser)
    def get(self) -> list[FileBrowserEntry]:
        """List Radarr file system content"""
        args = self.get_request_parser.parse_args()
        path: str = args['path']
        try:
            result = browse_radarr_filesystem(path)
            if result is None:
                raise ValueError
        except Exception:
            return []
        return format_directories(result['directories'])
