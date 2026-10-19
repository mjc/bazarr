# coding=utf-8
import pysubs2
import srt

from collections.abc import Iterable
from datetime import timedelta
from typing import TypedDict
from flask_restx import Resource, Namespace, reqparse, fields

from app.database import is_indexed_subtitle_path
from ..utils import authenticate
from subtitles.indexer.utils import detect_encoding
from utilities.path_mappings import path_mappings


api_ns_subtitle_contents = Namespace('Subtitle Contents', description='Retrieve contents of subtitle file')

# Same formats Bazarr can translate; only these files are read from disk
SUBTITLE_EXTENSIONS = ('.srt', '.ass', '.ssa', '.vtt')


class SubtitleTime(TypedDict):
    hours: int
    minutes: int
    seconds: int
    total_seconds: int
    microseconds: int


class SubtitleCue(TypedDict):
    index: int | None
    content: str
    proprietary: str
    start: SubtitleTime
    end: SubtitleTime


class SubtitleContentsResponse(TypedDict):
    data: list[SubtitleCue]


def times_dict_from_ms(ms: int) -> SubtitleTime:
    total_seconds = ms // 1000
    return {
        'hours': total_seconds // 3600,
        'minutes': (total_seconds % 3600) // 60,
        'seconds': total_seconds % 60,
        'total_seconds': total_seconds,
        'microseconds': (ms % 1000) * 1000,
    }


def map_contents(lines: Iterable[tuple[int | None, str, str, int, int]]) -> list[SubtitleCue]:
    return [{
        'index': index,
        'content': content,
        'proprietary': proprietary,
        'start': times_dict_from_ms(start_ms),
        'end': times_dict_from_ms(end_ms),
    } for index, content, proprietary, start_ms, end_ms in lines]


def parse_srt_contents(file_content: str) -> list[SubtitleCue]:
    return map_contents(
        (int(sub.index) if sub.index is not None else None, str(sub.content), sub.proprietary,
         sub.start // timedelta(milliseconds=1), sub.end // timedelta(milliseconds=1))
        for sub in srt.parse(file_content))


def parse_ssa_contents(file_content: str) -> list[SubtitleCue]:
    events = pysubs2.SSAFile.from_string(file_content).get_text_events()
    return map_contents(
        (index, event.text, '', event.start, event.end)
        for index, event in enumerate(events, start=1))


@api_ns_subtitle_contents.route('subtitles/contents')
class SubtitleNameContents(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('subtitlePath', type=str, required=True, help='Subtitle filepath')

    time_modal = api_ns_subtitle_contents.model('time_modal', {
        'hours': fields.Integer(),
        'minutes': fields.Integer(),
        'seconds': fields.Integer(),
        'total_seconds': fields.Integer(),
        'microseconds': fields.Integer(),
    })

    get_response_model = api_ns_subtitle_contents.model('SubtitlesContentsGetResponse', {
        'index': fields.Integer(),
        'content': fields.String(),
        'proprietary': fields.String(),
        'start': fields.Nested(time_modal),
        'end': fields.Nested(time_modal),
        # 'duration': fields.Nested(time_modal),
    })

    @authenticate
    @api_ns_subtitle_contents.response(200, 'Success')
    @api_ns_subtitle_contents.response(400, 'Invalid subtitle file encoding')
    @api_ns_subtitle_contents.response(401, 'Not Authenticated')
    @api_ns_subtitle_contents.expect(get_request_parser)
    def get(self) -> SubtitleContentsResponse | tuple[str, int]:
        """Retrieve subtitle file contents"""

        args = self.get_request_parser.parse_args()
        path = args.get('subtitlePath')

        if not path or not isinstance(path, str) or not path.casefold().endswith(SUBTITLE_EXTENSIONS):
            return 'Not a subtitle file', 400

        # The UI sends the mapped (local) path, but the index stores the reversed (Sonarr/Radarr) path
        if not is_indexed_subtitle_path(path_mappings.path_replace_reverse(path),
                                        path_mappings.path_replace_reverse_movie(path)):
            return 'Subtitles file is not indexed by Bazarr', 404

        # Detect subtitles file encoding
        encoding = detect_encoding(path)
        if encoding is None:
            return 'Invalid subtitle file encoding', 400

        # Load the subtitles content
        with open(path, "r", encoding=encoding) as f:
            file_content = f.read()

        try:
            results = parse_srt_contents(file_content)
        except (srt.SRTParseError, srt.TimestampParseError):
            try:
                results = parse_ssa_contents(file_content)
            except pysubs2.exceptions.Pysubs2Error:
                return 'Unable to parse subtitle file', 400

        return {'data': results}
