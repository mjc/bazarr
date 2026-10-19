from languages.registry import LanguageRegistry
import pytest

from app import database
from languages import get_languages
from utilities.subtitle_metadata import SubtitleFile, SubtitleOrder


def test_subtitle_payload_maps_language_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_languages, '_language_registry', LanguageRegistry([
        {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'},
    ]))
    def replace_path(path: str | None) -> str | None:
        return path.replace('/media', '/mapped') if path is not None else None

    result = database._subtitle_payload('/media/movie.en.srt', 'en', False, True, 123, None, 71, replace_path)

    assert result == {
        "id": 71,
        "path": "/mapped/movie.en.srt",
        "name": "English",
        "code2": "en",
        "code3": "eng",
        "forced": False,
        "hi": True,
        "file_size": 123,
        "embedded_track_id": None,
    }


def test_sort_subtitles_matches_legacy_get_subtitles_order() -> None:
    subtitles: list[SubtitleOrder] = [
        {"name": "French", "forced": True},
        {"name": "English", "forced": True},
        {"name": "English", "forced": False},
    ]

    assert database._sort_subtitles(subtitles) == [
        {"name": "English", "forced": False},
        {"name": "English", "forced": True},
        {"name": "French", "forced": True},
    ]


@pytest.mark.parametrize('unknown_first', [False, True])
def test_sort_subtitles_preserves_unknown_language_metadata(
    monkeypatch: pytest.MonkeyPatch, unknown_first: bool,
) -> None:
    monkeypatch.setattr(get_languages, '_language_registry', LanguageRegistry([
        {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'},
    ]))

    def unchanged_path(path: str | None) -> str | None:
        return path

    known = database._subtitle_payload('/movie.en.srt', 'en', False, False, 123, None, 1, unchanged_path)
    unknown = [
        database._subtitle_payload(None, 'und', True, False, None, 0, 2, unchanged_path),
        database._subtitle_payload(None, 'und', False, True, None, 1, 3, unchanged_path),
        database._subtitle_payload(None, 'und', False, False, 0, 2, 4, unchanged_path),
    ]
    subtitles: list[SubtitleFile] = [*unknown, known] if unknown_first else [known, *unknown]

    result = database._sort_subtitles(subtitles)

    assert [subtitle['id'] for subtitle in result] == [3, 4, 2, 1]
    assert result == [unknown[1], unknown[2], unknown[0], known]
    assert result[0] == {
        'id': 3, 'path': None, 'name': None, 'code2': 'und', 'code3': None,
        'forced': False, 'hi': True, 'file_size': None, 'embedded_track_id': 1,
    }
    assert subtitles == ([*unknown, known] if unknown_first else [known, *unknown])
