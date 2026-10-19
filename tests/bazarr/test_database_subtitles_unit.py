from languages.registry import LanguageRegistry
from collections.abc import Iterator
from types import SimpleNamespace, TracebackType
from unittest.mock import Mock
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


@pytest.mark.parametrize('media_type', ['series', 'movie'])
@pytest.mark.parametrize('use_query', [False, True])
@pytest.mark.parametrize('payload_error', [False, True])
def test_subtitle_map_bounds_buffering_and_closes_results(
    media_type: str, use_query: bool, payload_error: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Rows:
        closed = False

        def yield_per(self, size: int) -> 'Rows':
            assert size == 500
            return self

        def __enter__(self) -> 'Rows':
            return self

        def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                     traceback: TracebackType | None) -> None:
            self.closed = True

        def __iter__(self) -> Iterator[tuple[int, str, str, bool, bool, int, None, int]]:
            for number in range(501):
                yield 1, f'/video-{number}.srt', 'en', False, True, 100, None, number

    rows = Rows()
    execute = Mock(return_value=rows)
    monkeypatch.setattr(database, 'database', SimpleNamespace(execute=execute))
    monkeypatch.setattr(get_languages, '_language_registry', LanguageRegistry([
        {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'},
    ]))
    if payload_error:
        monkeypatch.setattr(database, '_subtitle_payload', Mock(side_effect=ValueError('payload failure')))

    def load() -> dict[int, list[SubtitleFile]]:
        if use_query:
            table, column = ((database.TableEpisodes, database.TableEpisodes.sonarrEpisodeId)
                             if media_type == 'series' else (database.TableMovies, database.TableMovies.radarrId))
            return database.get_subtitles_for_query(media_type, database.select(column).select_from(table))
        return database.get_subtitles_map(media_type, [1])

    if payload_error:
        with pytest.raises(ValueError, match='payload failure'):
            load()
    else:
        result = load()
        assert len(result[1]) == 501
        assert [item['id'] for item in result[1]] == list(range(501))
        assert result[1][0]['name'] == 'English'
        assert result[1][0]['hi'] is True
    assert rows.closed
    options = execute.call_args.args[0].get_execution_options()
    assert 'yield_per' not in options and 'stream_results' not in options
