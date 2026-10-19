from pathlib import Path

from subliminal_patch.core import save_subtitles
from subliminal_patch.subtitle import Subtitle
from subzero.language import Language
from subtitles.upload_format import configure_upload_format


def test_upload_format_preserves_hearing_impaired_detection(tmp_path):
    subtitle = Subtitle(Language("eng"), original_format=True)
    subtitle.content = b"1\n00:00:00,000 --> 00:00:01,000\n[DOOR SLAMS]\n"
    configure_upload_format(subtitle, "subtitle.srt")

    saved = save_subtitles(str(tmp_path / "movie.mkv"), [subtitle], formats=("srt",))

    assert subtitle.format == "srt"
    assert subtitle.language.hi is True
    assert Path(saved[0].storage_path).name == "movie.en.hi.srt"


def test_unknown_upload_extension_keeps_current_format():
    subtitle = Subtitle(Language("eng"), original_format=True)
    subtitle.format = "ass"

    assert configure_upload_format(subtitle, "subtitle.unknown") is None
    assert subtitle.format == "ass"
