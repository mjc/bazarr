import pytest

from radarr.sync.converter import RadarrFormatAudioCodec, RadarrFormatVideoCodec


def test_unknown_mpeg_audio_details_preserve_codec():
    assert RadarrFormatAudioCodec("MPEG Audio", "unknown", "unknown", None) == "MPEG Audio"


@pytest.mark.parametrize("codec,codec_id,library", [
    ("HEVC", None, "unknown"), ("V_MPEGH/ISO/HEVC", None, "unknown"),
    ("MPEG-4 Visual", "unknown", "unknown"),
])
def test_unknown_video_details_preserve_codec(codec, codec_id, library):
    assert RadarrFormatVideoCodec(codec, codec_id, library) == codec


def test_known_codec_details_still_normalize():
    assert RadarrFormatAudioCodec("MPEG Audio", "55", None, None) == "MP3"
    assert RadarrFormatVideoCodec("HEVC", None, "x265 4.0") == "h265"


def test_missing_codec_stays_none():
    assert RadarrFormatAudioCodec(None, None, None, None) is None
    assert RadarrFormatVideoCodec(None, None, None) is None


@pytest.mark.parametrize("codec", [0, False, [], {}])
def test_legacy_non_string_codec_is_preserved_at_boundary(codec):
    assert RadarrFormatAudioCodec(codec, None, None, None) is codec
    assert RadarrFormatVideoCodec(codec, None, None) is codec


def test_specific_codec_details_keep_precedence():
    assert RadarrFormatAudioCodec("MPEG Audio", "55", "Layer 2", None) == "MP3"
    assert RadarrFormatVideoCodec("MPEG-4 Visual", "XVID", "DivX") == "XviD"
