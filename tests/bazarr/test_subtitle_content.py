from pathlib import Path
from types import SimpleNamespace
from collections.abc import Callable
from unittest.mock import Mock

import pysubs2
import pytest

from subliminal_patch.core import SZProviderPool, save_subtitles
from subliminal_patch.subtitle import Subtitle
from subzero.language import Language

from app.config import settings
from subtitles.content import validate_subtitle
from subtitles import pool
from subtitles.tools import mods
from subtitles import manual
from tests.helpers.profile_records import profile_record


MICRODVD = '''{1}{1}25.0
{25}{50}Héllo world
'''


class DownloadedSubtitle(Subtitle):
    provider_name = 'content_test'


def _pool_with_downloader(
    monkeypatch: pytest.MonkeyPatch, download: Callable[[Subtitle], None], multithreading: bool = False,
) -> SZProviderPool:
    monkeypatch.setattr(settings.general, 'multithreading', multithreading)
    provider_pool = pool._init_pool('movie', providers=['content_test'])
    provider_pool.initialized_providers['content_test'] = SimpleNamespace(download_subtitle=download)
    return provider_pool


@pytest.mark.parametrize('original', [False, True])
@pytest.mark.parametrize('encoding', ['utf-8', 'cp1252'])
def test_microdvd_validation_can_be_repeated_without_changing_bytes(original: bool, encoding: str) -> None:
    subtitle = Subtitle(Language('eng'), original_format=original, encoding=encoding)
    subtitle.content = MICRODVD.encode(encoding)
    assert validate_subtitle(subtitle)
    validated = subtitle.content
    assert isinstance(validated, bytes)
    assert validate_subtitle(subtitle)
    assert subtitle.content is validated
    assert 'Héllo world' in subtitle.get_modified_content().decode(encoding)


@pytest.mark.parametrize('content', [None, b'', b'not a subtitle'])
def test_missing_or_invalid_content_remains_invalid_on_repeated_validation(content: bytes | None) -> None:
    subtitle = Subtitle(Language('eng'))
    subtitle.content = content
    assert not validate_subtitle(subtitle)
    assert not validate_subtitle(subtitle)


def test_empty_content_cannot_reuse_a_previous_successful_validation() -> None:
    subtitle = Subtitle(Language('eng'))
    subtitle.content = MICRODVD.encode()
    assert validate_subtitle(subtitle)
    subtitle.content = None
    assert not validate_subtitle(subtitle)


def test_cached_vendor_conversion_is_repaired_using_its_original_encoding() -> None:
    subtitle = Subtitle(Language('eng'), original_format=True, encoding='cp1252')
    subtitle.content = MICRODVD.encode('cp1252')
    assert subtitle.is_valid()
    assert isinstance(subtitle.content, str)
    assert validate_subtitle(subtitle)
    assert 'Héllo world' in subtitle.get_modified_content().decode('cp1252')


def test_unlabelled_text_is_rejected_without_guessing_a_byte_encoding() -> None:
    subtitle = Subtitle(Language('eng'))
    subtitle.content = MICRODVD
    assert not validate_subtitle(subtitle)


@pytest.mark.parametrize('multithreading', [False, True])
@pytest.mark.parametrize('keep_encoding', [False, True])
def test_provider_download_prepares_content_before_library_normalization_and_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, multithreading: bool, keep_encoding: bool,
) -> None:
    if keep_encoding:
        monkeypatch.setenv('SZ_KEEP_ENCODING', 'True')
    else:
        monkeypatch.delenv('SZ_KEEP_ENCODING', raising=False)

    subtitle = DownloadedSubtitle(Language('eng'), original_format=True, encoding='cp1252')

    def download(subtitle: Subtitle) -> None:
        subtitle.content = MICRODVD.encode('cp1252')

    provider_pool = _pool_with_downloader(monkeypatch, download, multithreading)
    assert provider_pool.download_subtitle(subtitle)
    assert isinstance(subtitle.content, bytes)
    saved = save_subtitles(str(tmp_path / 'movie.mkv'), [subtitle], formats=(subtitle.format,))
    assert saved == [subtitle]
    output = Path(subtitle.storage_path)
    assert output.suffix == '.srt'
    encoding = 'cp1252' if keep_encoding else 'utf-8'
    assert 'Héllo world' in output.read_text(encoding=encoding)


@pytest.mark.parametrize('content', [None, b'', b'not a subtitle', MICRODVD])
def test_provider_rejects_missing_and_invalid_content(
    monkeypatch: pytest.MonkeyPatch, content: bytes | str | None,
) -> None:
    subtitle = DownloadedSubtitle(Language('eng'), original_format=True)

    def download(subtitle: Subtitle) -> None:
        subtitle.content = content

    provider_pool = _pool_with_downloader(monkeypatch, download)
    assert not provider_pool.download_subtitle(subtitle)
    assert subtitle.content is None
    assert not subtitle.is_valid()


def test_provider_can_validate_microdvd_before_the_post_download_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    subtitle = DownloadedSubtitle(Language('eng'), original_format=True, encoding='cp1252')

    def download(subtitle: Subtitle) -> None:
        subtitle.content = MICRODVD.encode('cp1252')
        assert subtitle.is_valid()
        assert isinstance(subtitle.content, str)

    provider_pool = _pool_with_downloader(monkeypatch, download)
    assert provider_pool.download_subtitle(subtitle)
    assert isinstance(subtitle.content, bytes)
    assert 'Héllo world' in subtitle.text


def test_failed_manual_download_does_not_save_or_record_previous_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    subtitle = DownloadedSubtitle(Language('eng'), original_format=True, encoding='cp1252')
    subtitle.content = MICRODVD.encode('cp1252')
    assert validate_subtitle(subtitle)
    subtitle.normalize()
    old_content = subtitle.content

    def fail_download(subtitle: Subtitle) -> None:
        raise RuntimeError('The new provider download failed')

    provider_pool = _pool_with_downloader(monkeypatch, fail_download)
    provider_pool.throttle_callback = Mock()
    monkeypatch.setattr(manual, 'get_profile', lambda profile_id: profile_record())
    monkeypatch.setattr(manual.subtitle_cache, 'get', lambda cache_id: subtitle)
    monkeypatch.setattr(manual, 'get_video', lambda *args, **kwargs: SimpleNamespace(original_path=str(tmp_path / 'movie.mkv')))
    monkeypatch.setattr(manual, '_get_pool', lambda *args: provider_pool)
    monkeypatch.setattr(pool, '_update_pool', lambda *args: True)
    save = Mock()
    process = Mock()
    monkeypatch.setattr(manual, 'save_subtitles', save)
    monkeypatch.setattr(manual, 'process_subtitle', process)

    result = manual.manual_download_subtitle(str(tmp_path / 'movie.mkv'), [], 'False', 'False', 'cached',
                                            'content_test', None, 'Movie', 'movie', True, 44)

    assert result == 'Error downloading Subtitles'
    assert subtitle.content is old_content
    save.assert_not_called()
    process.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('format_', ['ass', 'srt'])
def test_provider_can_reuse_its_existing_normalized_content(
    monkeypatch: pytest.MonkeyPatch, format_: str,
) -> None:
    subtitle = DownloadedSubtitle(Language('eng'), original_format=True, encoding='cp1252')
    document = pysubs2.SSAFile()
    document.events.append(pysubs2.SSAEvent(start=1000, end=2000, text='Héllo world'))
    subtitle.content = document.to_string(format_).encode('cp1252')
    assert validate_subtitle(subtitle)
    subtitle.normalize()
    previous_content = subtitle.content

    def reuse_download(subtitle: Subtitle) -> None:
        assert subtitle.content is not None

    provider_pool = _pool_with_downloader(monkeypatch, reuse_download)
    assert provider_pool.download_subtitle(subtitle)
    assert subtitle.content == previous_content
    assert subtitle.encoding == 'cp1252'
    assert subtitle.get_encoding() == 'utf-8'
    assert subtitle.format == format_
    assert 'Héllo world' in subtitle.text


@pytest.mark.parametrize('original', [False, True])
@pytest.mark.parametrize('format_', ['ass', 'ssa', 'vtt', 'srt'])
def test_other_formats_preserve_validation_and_save_behavior(tmp_path: Path, original: bool, format_: str) -> None:
    document = pysubs2.SSAFile()
    document.events.append(pysubs2.SSAEvent(start=1000, end=2000, text='Hello'))
    subtitle = Subtitle(Language('eng'), original_format=original)
    subtitle.content = document.to_string(format_).encode('utf-8')
    assert validate_subtitle(subtitle)
    expected_format = format_ if original else 'srt'
    assert subtitle.format == expected_format
    saved = save_subtitles(str(tmp_path / 'movie.mkv'), [subtitle], formats=(subtitle.format,))
    assert saved == [subtitle]
    assert Path(subtitle.storage_path).suffix == '.' + expected_format
    assert pysubs2.load(subtitle.storage_path).events[0].text == 'Hello'


@pytest.mark.parametrize('remove_hi', [False, True])
def test_microdvd_modification_uses_byte_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remove_hi: bool,
) -> None:
    monkeypatch.setattr(mods, 'alpha3_from_alpha2', lambda language: 'eng')
    monkeypatch.setattr(mods.settings.general, 'subfolder', 'current')
    monkeypatch.setattr(mods.settings.general, 'single_language', False)
    source = tmp_path / 'movie.en.hi.sub'
    source.write_bytes(MICRODVD.encode())
    result = mods.subtitles_apply_mods('en', str(source), ['remove_HI'] if remove_hi else [],
                                     str(tmp_path / 'movie.mkv'))
    assert result is not None
    assert 'Héllo world' in Path(result).read_text()


@pytest.mark.parametrize('content', [b'', b'not a subtitle'])
def test_invalid_modification_leaves_the_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes,
) -> None:
    monkeypatch.setattr(mods, 'alpha3_from_alpha2', lambda language: 'eng')
    source = tmp_path / 'movie.en.srt'
    source.write_bytes(content)
    assert mods.subtitles_apply_mods('en', str(source), [], str(tmp_path / 'movie.mkv')) is None
    assert source.read_bytes() == content
