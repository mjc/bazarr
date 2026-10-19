import pytest
from subtitles.indexer import utils


@pytest.mark.parametrize('mode', ['absolute', 'relative'])
def test_unresolved_external_path_returns_none(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(utils.settings.general, 'subfolder', mode)
    monkeypatch.setattr(utils.settings.general, 'subfolder_custom', str(tmp_path / 'missing'))
    assert utils.get_external_subtitle_file(str(tmp_path / 'video.mkv'), 'video.en.srt') is None


def test_disappeared_subtitle_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(utils.settings.general, 'subfolder', 'current')
    assert utils.get_external_subtitle_file(str(tmp_path / 'video.mkv'), 'gone.en.srt') is None


def test_existing_subtitle_keeps_path_and_size(monkeypatch, tmp_path):
    monkeypatch.setattr(utils.settings.general, 'subfolder', 'current')
    subtitle = tmp_path / 'video.en.srt'
    subtitle.write_bytes(b'hello')
    result = utils.get_external_subtitle_file(str(tmp_path / 'video.mkv'), subtitle.name)
    assert result == utils.ExternalSubtitleFile(str(subtitle), 5)


@pytest.mark.parametrize('media_type', ['movie', 'episode'])
def test_indexer_skips_unresolved_subtitle_and_finishes(mocker, tmp_path, media_type):
    from types import SimpleNamespace
    from subzero.language import Language
    from subtitles.indexer import movies, series

    module = movies if media_type == 'movie' else series
    video = tmp_path / 'video.mkv'
    video.touch()
    mocker.patch.object(module, 'database').execute.return_value.first.return_value = SimpleNamespace(
        path=str(video), radarrId=1, sonarrSeriesId=1, movie_file_id=1, episode_file_id=1, file_size=0)
    mocker.patch.object(module.settings.general, 'use_embedded_subs', False)
    mocker.patch.object(module.settings.general, 'subfolder', 'absolute')
    mocker.patch.object(module.settings.general, 'subfolder_custom', str(tmp_path / 'missing'))
    mocker.patch.object(module, 'get_subtitle_destination_folder', return_value=None)
    mocker.patch.object(module, 'get_subtitles', return_value=[])
    mocker.patch.object(module, 'get_language_set', return_value=set())
    mocker.patch.object(module, 'search_external_subtitles', return_value={'missing.en.srt': Language('eng')})
    mocker.patch.object(module, 'guess_external_subtitles', side_effect=lambda folder, subs, previous: subs)
    name = 'list_missing_subtitles_movies' if media_type == 'movie' else 'list_missing_subtitles'
    finish = mocker.patch.object(module, name)
    index = module.store_subtitles_movie if media_type == 'movie' else module.store_subtitles
    index(1)
    finish.assert_called_once()
