import pytest
from dynaconf.utils.boxing import DynaBox

from radarr.movie_snapshot import MovieMediaInfo
from radarr.sync.converter import RadarrFormatAudioCodec, RadarrFormatVideoCodec
from radarr.sync.parser import movieParser
from sonarr.sync.converter import SonarrFormatAudioCodec, SonarrFormatVideoCodec
from sonarr.sync.parser import episodeParser
from tests.helpers.arr_fixtures import episode_payload, movie_payload, radarr_connection


@pytest.mark.parametrize('codec,codec_id,profile,features,expected', [
    (None, None, None, None, None),
    ('', None, None, None, ''),
    ('AC-3', None, None, None, 'AC3'),
    ('E-AC-3', None, None, None, 'EAC3'),
    ('AAC', 'A_AAC/MPEG4/LC/SBR', None, None, 'HE-AAC'),
    ('AAC', None, None, None, 'AAC'),
    (' mp3 ', None, None, None, 'MP3'),
    (' MP3 ', None, None, None, ' MP3 '),
    ('MPEG Audio', '55', None, None, 'MP3'),
    ('MPEG Audio', 'A_MPEG/L3', None, None, 'MP3'),
    ('MPEG Audio', None, 'Layer 3', None, 'MP3'),
    ('MPEG Audio', 'A_MPEG/L2', None, None, 'MP2'),
    ('MPEG Audio', None, 'Layer 2', None, 'MP2'),
    ('MPEG Audio', '55', 'Layer 2', None, 'MP3'),
    ('MPEG Audio', 'A_MPEG/L2', 'Layer 3', None, 'MP3'),
    ('MPEG Audio', None, None, None, 'MPEG Audio'),
    ('MPEG Audio', '', '', None, 'MPEG Audio'),
    ('MPEG Audio', 'unknown', 'unknown', None, 'MPEG Audio'),
    ('MLP FBA', None, None, '16-ch', 'TrueHD Atmos'),
    ('MLP FBA', None, None, None, 'TrueHD'),
    ('DTS', None, None, None, 'DTS'),
])
def test_radarr_audio_codec_preserves_format_without_matching_details(
    codec: str | None, codec_id: str | None, profile: str | None,
    features: str | None, expected: str | None,
) -> None:
    assert RadarrFormatAudioCodec(codec, codec_id, profile, features) == expected


@pytest.mark.parametrize('codec,codec_id,library,expected', [
    (None, None, None, None),
    ('', None, None, ''),
    ('x264', None, None, 'h264'),
    ('AVC', None, None, 'h264'),
    ('V.MPEG4/ISO/AVC', None, None, 'h264'),
    ('HEVC', None, 'x265 4.0', 'h265'),
    ('V_MPEGH/ISO/HEVC', None, 'x265 4.0', 'h265'),
    ('HEVC', None, None, 'HEVC'),
    ('HEVC', None, '', 'HEVC'),
    ('HEVC', None, 'unknown', 'HEVC'),
    ('V_MPEGH/ISO/HEVC', None, 'unknown', 'V_MPEGH/ISO/HEVC'),
    ('MPEG Video', '2', None, 'Mpeg2'),
    ('MPEG Video', 'V_MPEG2', None, 'Mpeg2'),
    ('MPEG Video', '1', None, 'Mpeg'),
    ('MPEG Video', None, None, 'MPEG Video'),
    ('MPEG-1 Video', None, None, 'Mpeg'),
    ('MPEG-2 Video', None, None, 'Mpeg2'),
    ('MPEG-4 Visual', 'XVID', 'other', 'XviD'),
    ('MPEG-4 Visual', 'other', 'XviD 1.3', 'XviD'),
    ('MPEG-4 Visual', 'DIV3', 'other', 'DivX'),
    ('MPEG-4 Visual', 'DIVX', 'other', 'DivX'),
    ('MPEG-4 Visual', 'DX50', 'other', 'DivX'),
    ('MPEG-4 Visual', 'other', 'DivX 6', 'DivX'),
    ('MPEG-4 Visual', 'XVID', 'DivX', 'XviD'),
    ('MPEG-4 Visual', 'DIVX', 'XviD', 'XviD'),
    ('MPEG-4 Visual', 'unknown', 'unknown', 'MPEG-4 Visual'),
    ('MPEG-4 Visual', None, 'XviD', 'MPEG-4 Visual'),
    ('MPEG-4 Visual', 'DIVX', None, 'MPEG-4 Visual'),
    ('VC-1', None, None, 'VC1'),
    ('WMV2', None, None, 'WMV'),
    ('DivX', None, None, 'DivX'),
    ('div3', None, None, 'DivX'),
    ('AV1', None, None, 'AV1'),
])
def test_radarr_video_codec_preserves_format_without_matching_details(
    codec: str | None, codec_id: str | None, library: str | None, expected: str | None,
) -> None:
    assert RadarrFormatVideoCodec(codec, codec_id, library) == expected


@pytest.mark.parametrize('codec,expected', [
    (None, None), ('', ''), ('AC-3', 'AC3'), ('E-AC-3', 'EAC3'),
    ('MPEG Audio', 'MP3'), ('AAC', 'AAC'), ('DTS', 'DTS'),
])
def test_sonarr_audio_codec(codec: str | None, expected: str | None) -> None:
    assert SonarrFormatAudioCodec(codec) == expected


@pytest.mark.parametrize('codec,expected', [
    (None, None), ('', ''), ('x264', 'h264'), ('AVC', 'h264'),
    ('V.MPEG4/ISO/AVC', 'V.MPEG4/ISO/AVC'), ('x265', 'h265'), ('HEVC', 'h265'),
    ('XviD 1.3', 'XviD'), ('DivX 6', 'DivX'), ('MPEG-1 Video', 'Mpeg'),
    ('MPEG-2 Video', 'Mpeg2'), ('MPEG-4 Video', 'Mpeg4'), ('VC-1', 'VC1'),
    ('video VP6', 'VP6'), ('video VP7', 'VP7'), ('video VP8', 'VP8'), ('video VP9', 'VP9'),
    ('WMV2', 'WMV2'), ('div3', 'div3'), ('AV1', 'AV1'),
])
def test_sonarr_video_codec(codec: str | None, expected: str | None) -> None:
    assert SonarrFormatVideoCodec(codec) == expected


@pytest.mark.parametrize('version', ['0.2.0.1', '5.0.0.1'])
@pytest.mark.parametrize('video,audio', [
    ('HEVC', 'MPEG Audio'), ('V_MPEGH/ISO/HEVC', 'MPEG Audio'),
    ('MPEG-4 Visual', 'MPEG Audio'), (None, None),
])
def test_movie_parser_retains_codecs_for_legacy_and_current_media_info(
    version: str, video: str | None, audio: str | None,
) -> None:
    movie = movie_payload()
    media: MovieMediaInfo = {'videoCodecID': 'unknown', 'videoCodecLibrary': 'unknown',
                            'audioCodecID': 'unknown', 'audioProfile': 'unknown'}
    capabilities = radarr_connection(version).capabilities
    if capabilities.legacy:
        media['videoFormat'] = video
        media['audioFormat'] = audio
    else:
        media['videoCodec'] = video
        media['audioCodec'] = audio
    movie['movieFile']['mediaInfo'] = media
    general = DynaBox({'parse_embedded_audio_track': False, 'movie_tag_enabled': False})

    parsed = movieParser(movie, 'update', [], [], None, [], general=general, capabilities=capabilities)

    assert parsed is not None
    assert parsed['video_codec'] == video
    assert parsed['audio_codec'] == audio


@pytest.mark.parametrize('video,audio,expected_video,expected_audio', [
    ('AVC', 'AC-3', 'h264', 'AC3'), ('HEVC', 'MPEG Audio', 'h265', 'MP3'),
    ('AV1', 'unknown', 'AV1', 'unknown'), (None, None, None, None),
])
def test_episode_parser_normalizes_its_validated_codec_fields(
    video: str | None, audio: str | None, expected_video: str | None, expected_audio: str | None,
) -> None:
    episode = episode_payload()
    file = episode['episodeFile']
    assert file is not None
    file['mediaInfo'] = {'videoCodec': video, 'audioCodec': audio}
    general = DynaBox({'parse_embedded_audio_track': False, 'enable_strm_support': False})

    parsed = episodeParser(episode, general=general)

    assert parsed is not None
    assert parsed['video_codec'] == expected_video
    assert parsed['audio_codec'] == expected_audio
