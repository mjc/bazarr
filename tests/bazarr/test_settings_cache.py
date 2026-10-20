from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dynaconf import Dynaconf
from dynaconf.utils.boxing import DynaBox
from dynaconf.validator import ValidationError

from app import config
from app.settings_cache import SettingsCache, settings_cache


class Source:
    def __init__(self) -> None:
        self.section = DynaBox({'value': 'old', 'subzero_mods': ''})
        self.reads = 0
        self.sonarr = DynaBox()
        self.radarr = DynaBox()

    @property
    def general(self) -> DynaBox:
        self.reads += 1
        return self.section

    def as_dict(self) -> dict[str, str]:
        return {'config': 'current'}


def test_cache_reuses_sections_and_observes_in_place_edits() -> None:
    source = Source()
    cache = SettingsCache()
    first = cache.get(source, 'general')
    source.section.value = 'edited'
    assert cache.get(source, 'general') is first
    assert first.value == 'edited'
    assert source.reads == 1
    assert cache.get(source, 'sonarr') is source.sonarr
    assert cache.get(source, 'radarr') is source.radarr


def test_invalidation_refreshes_replaced_sections() -> None:
    source = Source()
    cache = SettingsCache()
    first = cache.get(source, 'general')
    source.section = DynaBox({'value': 'reloaded'})
    cache.invalidate()
    assert cache.get(source, 'general') is source.section
    assert cache.get(source, 'general') is not first
    assert source.reads == 2


def test_dynaconf_section_replacement_is_visible_after_invalidation() -> None:
    source = Dynaconf(settings_files=[], general={'parse_embedded_audio_track': False})
    cache = SettingsCache()
    previous = cache.get(source, 'general')
    assert previous.parse_embedded_audio_track is False
    source.set('GENERAL', {'parse_embedded_audio_track': True}, merge=False)
    cache.invalidate()
    current = cache.get(source, 'general')
    assert current is not previous
    assert current.parse_embedded_audio_track is True


def test_replacing_settings_source_cannot_reuse_old_sections() -> None:
    first, second = Source(), Source()
    cache = SettingsCache()
    assert cache.get(first, 'general') is first.section
    assert cache.get(second, 'general') is second.section
    assert second.reads == 1


def test_invalidation_cannot_be_undone_by_an_in_flight_load() -> None:
    started, release, invalidating = Event(), Event(), Event()

    class BlockedSource(Source):
        @property
        def general(self) -> DynaBox:
            previous = self.section
            started.set()
            assert release.wait(5)
            return previous

    source = BlockedSource()
    cache = SettingsCache()
    results: list[DynaBox] = []
    loader = Thread(target=lambda: results.append(cache.get(source, 'general')))

    def invalidate() -> None:
        invalidating.set()
        cache.invalidate()

    invalidator = Thread(target=invalidate)
    loader.start()
    try:
        assert started.wait(5)
        source.section = DynaBox({'value': 'reloaded'})
        invalidator.start()
        assert invalidating.wait(5)
    finally:
        release.set()
        loader.join(5)
        if invalidator.ident is not None:
            invalidator.join(5)
    assert not loader.is_alive() and not invalidator.is_alive()
    assert results[0] is not source.section
    assert cache.get(source, 'general') is source.section


@pytest.mark.parametrize('outcome', ['unchanged', 'saved', 'write_error', 'move_error', 'comparison_error'])
def test_write_config_invalidates_settings_even_when_no_file_is_written(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    source = Source()
    monkeypatch.setattr(config, 'settings', source)
    old = settings_cache.get(source, 'general')
    source.section = DynaBox({'value': 'changed'})
    saved = source.as_dict() if outcome == 'unchanged' else {'config': 'previous'}
    snapshot = Mock(return_value=SimpleNamespace(as_dict=lambda: saved),
                    side_effect=OSError('comparison failed') if outcome == 'comparison_error' else None)
    monkeypatch.setattr(config, 'Dynaconf', snapshot)
    monkeypatch.setattr(config, 'write', Mock(side_effect=OSError('write failed') if outcome == 'write_error' else None))
    monkeypatch.setattr(config, 'move', Mock(side_effect=OSError('move failed') if outcome == 'move_error' else None))
    if outcome == 'comparison_error':
        with pytest.raises(OSError, match='comparison failed'):
            config.write_config()
    else:
        config.write_config()
    assert settings_cache.get(source, 'general') is source.section
    assert settings_cache.get(source, 'general') is not old


@pytest.mark.parametrize('reload_fails', [False, True])
def test_failed_validation_invalidates_after_settings_reload(monkeypatch: pytest.MonkeyPatch, reload_fails: bool) -> None:
    class ReloadingSource(Source):
        validators = SimpleNamespace(validate=Mock(side_effect=ValidationError('invalid settings')))

        def reload(self) -> None:
            self.section = DynaBox({'value': 'restored', 'subzero_mods': ''})
            if reload_fails:
                raise OSError('reload failed')

    source = ReloadingSource()
    monkeypatch.setattr(config, 'settings', source)
    old = settings_cache.get(source, 'general')
    with pytest.raises(OSError if reload_fails else ValidationError):
        config.save_settings(())
    assert settings_cache.get(source, 'general') is source.section
    assert settings_cache.get(source, 'general') is not old
    assert source.section.value == 'restored'
