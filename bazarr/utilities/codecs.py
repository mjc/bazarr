# coding=utf-8

_AUDIO_ALIASES = {'AC-3': 'AC3', 'E-AC-3': 'EAC3'}
_VIDEO_ALIASES = {'x264': 'h264', 'AVC': 'h264', 'MPEG-1 Video': 'Mpeg',
                  'MPEG-2 Video': 'Mpeg2', 'VC-1': 'VC1'}


def sonarr_audio_codec(codec: str | None) -> str | None:
    if codec is None:
        return None
    if codec == 'MPEG Audio':
        return 'MP3'
    return _AUDIO_ALIASES.get(codec, codec)


def sonarr_video_codec(codec: str | None) -> str | None:
    if codec is None:
        return None
    alias = _VIDEO_ALIASES.get(codec)
    if alias is not None:
        return alias
    if codec in ('x265', 'HEVC'):
        return 'h265'
    if codec.startswith('XviD'):
        return 'XviD'
    if codec.startswith('DivX'):
        return 'DivX'
    if codec == 'MPEG-4 Video':
        return 'Mpeg4'
    if codec.endswith('VP6'):
        return 'VP6'
    if codec.endswith('VP7'):
        return 'VP7'
    if codec.endswith('VP8'):
        return 'VP8'
    if codec.endswith('VP9'):
        return 'VP9'
    return codec


def radarr_audio_codec(codec: str | None, codec_id: str | None, profile: str | None,
                       additional_features: str | None) -> str | None:
    if codec is None:
        return None
    alias = _AUDIO_ALIASES.get(codec)
    if alias is not None:
        return alias
    if codec == 'AAC' and codec_id == 'A_AAC/MPEG4/LC/SBR':
        return 'HE-AAC'
    if codec.strip() == 'mp3':
        return 'MP3'
    if codec == 'MPEG Audio':
        if codec_id in ('55', 'A_MPEG/L3') or profile == 'Layer 3':
            return 'MP3'
        if codec_id == 'A_MPEG/L2' or profile == 'Layer 2':
            return 'MP2'
    if codec == 'MLP FBA':
        return 'TrueHD Atmos' if additional_features == '16-ch' else 'TrueHD'
    return codec


def radarr_video_codec(codec: str | None, codec_id: str | None, library: str | None) -> str | None:
    if codec is None:
        return None
    alias = _VIDEO_ALIASES.get(codec)
    if alias is not None:
        return alias
    if codec == 'V.MPEG4/ISO/AVC':
        return 'h264'
    if codec in ('HEVC', 'V_MPEGH/ISO/HEVC') and library and library.startswith('x265'):
        return 'h265'
    if codec == 'MPEG Video' and codec_id:
        return 'Mpeg2' if codec_id in ('2', 'V_MPEG2') else 'Mpeg'
    if codec == 'MPEG-4 Visual' and codec_id and library:
        if codec_id.endswith('XVID') or library.startswith('XviD'):
            return 'XviD'
        if codec_id.endswith(('DIV3', 'DIVX', 'DX50')) or library.startswith('DivX'):
            return 'DivX'
    if codec == 'WMV2':
        return 'WMV'
    if codec in ('DivX', 'div3'):
        return 'DivX'
    return codec
