import pytest

from subzero.language import Language
from subliminal_patch.subtitle import Subtitle


@pytest.mark.parametrize("original_format", [False, True])
def test_microdvd_conversion_preserves_bytes(original_format):
    subtitle = Subtitle(Language("eng"), original_format=original_format)
    subtitle.content = b"{1}{1}25.0\n{25}{50}Hello world\n"

    assert subtitle.is_valid()
    assert isinstance(subtitle.content, bytes)
    assert b"Hello world" in subtitle.get_modified_content()


def test_missing_subtitle_content_returns_none():
    subtitle = Subtitle(Language("eng"))

    assert subtitle.text is None
    assert subtitle.get_modified_content() is None
