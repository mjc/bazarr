from pathlib import Path
from types import SimpleNamespace

import pytest

from utilities import subtitle_paths
from subtitles.tools import mods
from utilities.helper import get_target_folder


@pytest.mark.parametrize('mode', ['current', 'absolute', 'relative'])
def test_input_lookup_returns_existing_path_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    video = tmp_path / 'movie.mkv'
    custom = tmp_path / 'subs'
    custom.mkdir()
    general = subtitle_paths.settings.general
    monkeypatch.setattr(general, 'subfolder', mode)
    monkeypatch.setattr(general, 'subfolder_custom', str(custom) if mode == 'absolute' else 'subs')
    filename = 'movie.en.srt'
    assert subtitle_paths.find_external_subtitle(str(video), filename) is None
    alongside = tmp_path / filename
    alongside.write_bytes(b'alongside')
    result = subtitle_paths.find_external_subtitle(str(video), filename)
    assert result == subtitle_paths.ExternalSubtitle(str(alongside), 9)
    (custom / filename).write_bytes(b'custom')
    assert subtitle_paths.find_external_subtitle(str(video), filename) == result
    alongside.unlink()
    result = subtitle_paths.find_external_subtitle(str(video), filename)
    assert result == (None if mode == 'current' else subtitle_paths.ExternalSubtitle(str(custom / filename), 6))


@pytest.mark.parametrize('mode', ['current', 'absolute', 'relative'])
def test_output_destination_does_not_require_a_preexisting_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    general = subtitle_paths.settings.general
    monkeypatch.setattr(general, 'subfolder', mode)
    monkeypatch.setattr(general, 'subfolder_custom', str(tmp_path / 'subs') if mode == 'absolute' else 'subs')
    expected = tmp_path / 'new.en.srt' if mode == 'current' else tmp_path / 'subs' / 'new.en.srt'
    assert subtitle_paths.subtitle_destination(str(tmp_path / 'movie.mkv'), 'new.en.srt') == str(expected)
    assert not expected.exists()


def test_only_missing_inputs_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subtitle_paths.settings.general, 'subfolder', 'current')

    def denied(path: str) -> None:
        raise PermissionError(path)

    monkeypatch.setattr(subtitle_paths, 'os', SimpleNamespace(path=subtitle_paths.os.path, stat=denied))
    with pytest.raises(PermissionError):
        subtitle_paths.find_external_subtitle(str(tmp_path / 'movie.mkv'), 'movie.en.srt')


@pytest.mark.parametrize('mode', ['current', 'absolute', 'relative'])
def test_saving_uses_the_same_folder_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(subtitle_paths.settings.general, 'subfolder', mode)
    custom = str(tmp_path / 'subs') if mode == 'absolute' else 'subs'
    monkeypatch.setattr(subtitle_paths.settings.general, 'subfolder_custom', custom)
    folder = get_target_folder(str(tmp_path / 'movie.mkv'))
    assert folder == (None if mode == 'current' else str(tmp_path / 'subs'))
    if folder is not None:
        assert Path(folder).is_dir()


@pytest.mark.parametrize('in_place', [False, True])
def test_modifications_replace_content_before_removing_source(tmp_path: Path, in_place: bool) -> None:
    source = tmp_path / 'movie.en.hi.srt'
    source.write_bytes(b'original')
    source.chmod(0o640)
    destination = source if in_place else tmp_path / 'subs' / 'movie.en.srt'
    mods._write_modified_subtitle(b'modified', str(source), str(destination))
    assert destination.read_bytes() == b'modified'
    assert destination.stat().st_mode & 0o777 == 0o640
    assert source.exists() is in_place
    assert list(destination.parent.glob('.bazarr-*')) == []


def test_directory_alias_does_not_delete_replacement(tmp_path: Path) -> None:
    directory = tmp_path / 'subtitles'
    directory.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(directory, target_is_directory=True)
    source = directory / 'movie.en.srt'
    destination = alias / source.name
    source.write_bytes(b'original')
    mods._write_modified_subtitle(b'modified', str(source), str(destination))
    assert source.read_bytes() == b'modified'
    assert destination.read_bytes() == b'modified'
    assert list(directory.glob('.bazarr-*')) == []


@pytest.mark.parametrize('symbolic', [False, True])
def test_distinct_source_link_is_removed_after_replacement(tmp_path: Path, symbolic: bool) -> None:
    destination = tmp_path / 'movie.en.srt'
    destination.write_bytes(b'original')
    source = tmp_path / 'movie.en.hi.srt'
    if symbolic:
        source.symlink_to(destination)
    else:
        source.hardlink_to(destination)
    mods._write_modified_subtitle(b'modified', str(source), str(destination))
    assert not source.exists()
    assert not source.is_symlink()
    assert destination.read_bytes() == b'modified'


@pytest.mark.parametrize('in_place', [False, True])
def test_failed_replacement_keeps_original_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, in_place: bool,
) -> None:
    source = tmp_path / 'movie.en.hi.srt'
    source.write_bytes(b'original')
    destination = source if in_place else tmp_path / 'movie.en.srt'
    if not in_place:
        destination.write_bytes(b'existing')

    def fail_replace(source: str, destination: str) -> None:
        raise OSError('replacement failed')

    monkeypatch.setattr(mods.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='replacement failed'):
        mods._write_modified_subtitle(b'modified', str(source), str(destination))
    assert source.read_bytes() == b'original'
    assert destination.read_bytes() == (b'original' if in_place else b'existing')
    assert list(tmp_path.glob('.bazarr-*')) == []


@pytest.mark.parametrize('mode', ['current', 'absolute', 'relative'])
def test_remove_hi_can_save_to_a_new_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    monkeypatch.setattr(subtitle_paths.settings.general, 'subfolder', mode)
    monkeypatch.setattr(subtitle_paths.settings.general, 'subfolder_custom',
                        str(tmp_path / 'subs') if mode == 'absolute' else 'subs')
    monkeypatch.setattr(subtitle_paths.settings.general, 'single_language', False)
    monkeypatch.setattr(mods, 'alpha3_from_alpha2', lambda language: 'eng')

    class ModifiableSubtitle:
        def __init__(self, language, mods, original_format):
            self.language, self.mods = language, mods
            self.content = b''
            self.format = 'srt'

        def is_valid(self) -> bool:
            return True

        def get_modified_content(self, format: str) -> bytes:
            return b'modified'

    monkeypatch.setattr(mods, 'Subtitle', ModifiableSubtitle)
    source = tmp_path / 'movie.en.hi.srt'
    source.write_bytes(b'original')
    result = mods.subtitles_apply_mods('en', str(source), ['remove_HI'], str(tmp_path / 'movie.mkv'))
    expected = tmp_path / 'movie.en.srt' if mode == 'current' else tmp_path / 'subs' / 'movie.en.srt'
    assert result == str(expected)
    assert expected.read_bytes() == b'modified'
    assert not source.exists()
