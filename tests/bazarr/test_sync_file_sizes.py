import os
from unittest.mock import Mock

import pytest

from constants import MINIMUM_VIDEO_SIZE
from utilities.arr_files import FileSizes
from utilities.path_mappings import PathMappings


def test_file_size_cache_belongs_to_one_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    stat = Mock(side_effect=[0, 100])
    monkeypatch.setattr(os.path, 'getsize', stat)
    sizes = FileSizes(lambda path: path)
    assert sizes.resolve('/movie.mkv', 0) == 0
    assert sizes.resolve('/movie.mkv', 0) == 0
    assert FileSizes(lambda path: path).resolve('/movie.mkv', 0) == 100
    assert stat.call_count == 2


def test_file_size_resolution_uses_current_path_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    mappings = PathMappings()
    mappings.path_mapping_movies = [['/movies', '/first']]
    sizes = FileSizes(mappings.path_replace_movie)
    stat = Mock(return_value=0)
    monkeypatch.setattr(os.path, 'getsize', stat)

    assert sizes.resolve('/movies/one.mkv', 0) == 0
    mappings.path_mapping_movies = [['/movies', '/second']]
    assert sizes.resolve('/movies/two.mkv', 0) == 0

    stat.assert_any_call('/first/one.mkv')
    stat.assert_any_call('/second/two.mkv')


def test_large_reported_size_does_not_probe_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    stat = Mock()
    monkeypatch.setattr(os.path, 'getsize', stat)
    assert FileSizes(lambda path: path).resolve('/movie.mkv', MINIMUM_VIDEO_SIZE + 1) == MINIMUM_VIDEO_SIZE + 1
    stat.assert_not_called()
