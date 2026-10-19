from types import SimpleNamespace

import pytest

from app import database
from languages import get_languages


def test_subtitle_payload_maps_language_and_path():
    original_languages = getattr(get_languages, "languages_dict", None)
    get_languages.languages_dict = [{"code2": "en", "code3": "eng", "code3b": None, "name": "English"}]
    subtitle = SimpleNamespace(
        id=71,
        path="/media/movie.en.srt",
        language="en",
        forced=False,
        hi=True,
        size=123,
        embedded_track_id=None,
    )

    try:
        result = database._subtitle_payload(subtitle, lambda path: path.replace("/media", "/mapped"))
    finally:
        if original_languages is None:
            del get_languages.languages_dict
        else:
            get_languages.languages_dict = original_languages

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


def test_sort_subtitles_matches_legacy_get_subtitles_order():
    subtitles = [
        {"name": "French", "forced": True},
        {"name": "English", "forced": True},
        {"name": "English", "forced": False},
    ]

    assert database._sort_subtitles(subtitles) == [
        {"name": "English", "forced": False},
        {"name": "English", "forced": True},
        {"name": "French", "forced": True},
    ]


@pytest.mark.parametrize("unknown_first", [False, True])
def test_sort_subtitles_preserves_unknown_languages(monkeypatch, unknown_first):
    monkeypatch.setattr(get_languages, "languages_dict", [
        {"code2": "en", "code3": "eng", "code3b": None, "name": "English"},
    ])
    rows = [
        SimpleNamespace(id=1, path="/movie.en.srt", language="en", forced=False,
                        hi=False, size=123, embedded_track_id=None),
        SimpleNamespace(id=2, path=None, language="und", forced=True,
                        hi=False, size=None, embedded_track_id=0),
        SimpleNamespace(id=3, path=None, language="und", forced=False,
                        hi=True, size=None, embedded_track_id=1),
    ]
    if unknown_first:
        rows.reverse()
    payloads = [database._subtitle_payload(row, lambda path: path) for row in rows]

    result = database._sort_subtitles(payloads)

    assert [subtitle["id"] for subtitle in result] == [3, 2, 1]
    assert result[0] == {
        "id": 3, "path": None, "name": None, "code2": "und", "code3": None,
        "forced": False, "hi": True, "file_size": None, "embedded_track_id": 1,
    }
