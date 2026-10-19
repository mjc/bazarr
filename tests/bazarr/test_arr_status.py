from unittest.mock import Mock
from types import SimpleNamespace
from dogpile.cache import region as cache_region

import pytest
import requests

from utilities import arr
from sonarr import info as sonarr
from radarr import info as radarr


@pytest.mark.parametrize('payload', [None, [], {}, {'error': 'Not Found'}, {'version': None},
                                      {'version': 4}, {'version': ''}])
def test_invalid_status_falls_back_to_v3(monkeypatch, payload):
    request = Mock(side_effect=[payload, {'version': '4.0.16.2944'}])
    monkeypatch.setattr(arr, 'request_json', request)
    assert arr.discover_arr_version('http://arr', 'key', 30) == '4.0.16.2944'
    assert request.call_count == 2
    assert '/api/system/status?' in request.call_args_list[0].args[0]
    assert '/api/v3/system/status?' in request.call_args_list[1].args[0]


@pytest.mark.parametrize('failure', [requests.HTTPError('No response'), requests.ConnectionError(),
                                      requests.Timeout(), requests.JSONDecodeError('bad', '', 0)])
def test_status_failure_falls_back_then_reports_unknown(monkeypatch, failure):
    request = Mock(side_effect=[failure, failure])
    monkeypatch.setattr(arr, 'request_json', request)
    assert arr.discover_arr_version('http://arr', 'key', 30) is None
    assert request.call_count == 2


def test_status_does_not_hide_programming_errors(monkeypatch):
    monkeypatch.setattr(arr, 'request_json', Mock(side_effect=RuntimeError('broken')))
    with pytest.raises(RuntimeError, match='broken'):
        arr.discover_arr_version('http://arr', 'key', 30)


@pytest.mark.parametrize('app', [sonarr, radarr])
def test_version_cache_is_scoped_to_connection(monkeypatch, app):
    name = 'sonarr' if app is sonarr else 'radarr'
    configuration = getattr(app.settings, name)
    monkeypatch.setattr(app.settings.general, f'use_{name}', True)
    monkeypatch.setattr(app, 'region', app.make_region().configure('dogpile.cache.memory'))
    discover = Mock(return_value='4.0.16.2944')
    monkeypatch.setattr(app, 'discover_arr_version', discover)
    service = getattr(app, f'get_{name}_info')
    assert service.version() == '4.0.16.2944'
    assert service.version() == '4.0.16.2944'
    discover.assert_called_once()
    monkeypatch.setattr(configuration, 'ip', 'different-host')
    assert service.version() == '4.0.16.2944'
    assert discover.call_count == 2
    monkeypatch.setattr(app.settings.general, f'use_{name}', False)
    assert service.version() == ''
    assert discover.call_count == 2


@pytest.mark.parametrize('version,legacy,separate', [
    ('2.0.0.5344', True, False), ('3.0.0.1', False, True),
    ('4.0.9.2420', False, True), ('4.0.9.2421', False, False),
    ('4.0.9.10000', False, False), ('4.0.10', False, False),
])
def test_sonarr_capabilities_compare_numeric_revision_once(monkeypatch, version, legacy, separate):
    get = Mock(return_value=version)
    monkeypatch.setattr(sonarr.get_sonarr_info, 'version', get)
    capabilities = sonarr.get_sonarr_info.capabilities()
    assert capabilities is not None
    assert capabilities.legacy == legacy
    assert capabilities.separate_episode_files == separate
    get.assert_called_once()


@pytest.mark.parametrize('version', ['', 'unknown', '4.0', '4.0.x.1', '4.0.0.1.2', '４.0.0'])
def test_invalid_version_has_no_capabilities(monkeypatch, version):
    monkeypatch.setattr(sonarr.get_sonarr_info, 'version', lambda: version)
    assert sonarr.get_sonarr_info.capabilities() is None


def test_legacy_semver_compatibility(monkeypatch):
    version = Mock(return_value='2.0.0.5344')
    monkeypatch.setattr(sonarr.get_sonarr_info, 'version', version)
    assert str(sonarr.get_sonarr_info.semver()) == '2.0.0-5344'
    version.assert_called_once()


@pytest.mark.parametrize('app', [sonarr, radarr])
def test_version_cache_reads_do_not_extend_expiration(monkeypatch, app):
    name = 'sonarr' if app is sonarr else 'radarr'
    monkeypatch.setattr(app.settings.general, f'use_{name}', True)
    region = app.make_region().configure('dogpile.cache.memory')
    monkeypatch.setattr(app, 'region', region)
    discover = Mock(return_value='4.0.16.2944')
    monkeypatch.setattr(app, 'discover_arr_version', discover)
    service = getattr(app, f'get_{name}_info')
    assert service.version() == '4.0.16.2944'
    set_value = Mock(wraps=region.set)
    monkeypatch.setattr(region, 'set', set_value)
    assert service.version() == '4.0.16.2944'
    set_value.assert_not_called()
    now = cache_region.time.time()
    monkeypatch.setattr(cache_region, 'time', SimpleNamespace(time=lambda: now + 61))
    assert service.version() == '4.0.16.2944'
    assert discover.call_count == 2
