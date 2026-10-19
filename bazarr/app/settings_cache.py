from threading import RLock
from typing import Literal, Protocol

from dynaconf.utils.boxing import DynaBox


SettingsSection = Literal['general', 'sonarr', 'radarr']


class SettingsSource(Protocol):
    def __getattr__(self, name: SettingsSection) -> DynaBox: ...


class SettingsCache:
    """Reuse section lookups while allowing saves and reloads to replace them."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._source: SettingsSource | None = None
        self._sections: dict[SettingsSection, DynaBox] = {}

    def get(self, source: SettingsSource, section: SettingsSection) -> DynaBox:
        with self._lock:
            if source is not self._source:
                self._sections.clear()
                self._source = source
            cached = self._sections.get(section)
            if cached is None:
                if section == 'general':
                    cached = source.general
                elif section == 'sonarr':
                    cached = source.sonarr
                else:
                    cached = source.radarr
                self._sections[section] = cached
            return cached

    def invalidate(self) -> None:
        # Serialize invalidation with loading, so an older in-flight lookup
        # cannot refill the cache after a save or reload has invalidated it.
        with self._lock:
            self._sections.clear()
            self._source = None


settings_cache = SettingsCache()
