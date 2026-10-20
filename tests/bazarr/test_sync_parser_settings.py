from importlib import import_module

import pytest
from dynaconf.utils.boxing import DynaBox

from app.settings_cache import settings_cache
from tests.helpers.arr_fixtures import movie_record, series_record


@pytest.mark.parametrize('kind', ['series', 'movie'])
def test_sync_parser_reuses_supplied_settings_and_reads_current_settings_when_called_alone(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Settings:
        reads = 0
        section = DynaBox({'parse_embedded_audio_track': False, 'serie_tag_enabled': True,
                           'movie_tag_enabled': True, 'remove_profile_tags': []})

        @property
        def general(self) -> DynaBox:
            self.reads += 1
            return self.section

    settings = Settings()
    if kind == 'series':
        module = import_module('sonarr.sync.parser')
        parse = module.seriesParser
        monkeypatch.setattr(module.get_sonarr_info, 'is_legacy', lambda: False)
        payload = {'title': 'Show', 'path': '/show', 'id': 1, 'tvdbId': 2, 'sortTitle': 'Show',
                   'year': 2024, 'seriesType': 'standard', 'monitored': True, 'images': [],
                   'alternateTitles': [], 'tags': [10]}
    else:
        module = import_module('radarr.sync.parser')
        parse = module.movieParser
        monkeypatch.setattr(module.get_radarr_info, 'is_legacy', lambda: False)
        payload = {'title': 'Movie', 'id': 1, 'tmdbId': 2, 'sortTitle': 'Movie', 'year': 2024,
                   'monitored': True, 'hasFile': True, 'tags': [10],
                   'movieFile': {'path': '/movie', 'id': 3, 'size': 100000000,
                                 'quality': {'quality': {'name': 'WEB-1080p'}}, 'languages': []}}
    payload = series_record(payload) if kind == 'series' else movie_record(payload)
    monkeypatch.setattr(module, 'settings', settings)
    arguments = (payload, 'insert', [{'id': 10, 'label': 'keep'}], [(7, 'English', 'keep')], 99, [])

    expected = parse(*arguments)
    assert settings.reads == 1
    assert expected['profileId'] == 7
    assert parse(*arguments, general=settings.section) == expected
    assert settings.reads == 1
    assert parse(*arguments) == expected
    assert settings.reads == 1

    settings.section = DynaBox({'parse_embedded_audio_track': False, 'serie_tag_enabled': False,
                                'movie_tag_enabled': False, 'remove_profile_tags': []})
    settings_cache.invalidate()
    assert parse(*arguments)['profileId'] == 99
    assert settings.reads == 2
