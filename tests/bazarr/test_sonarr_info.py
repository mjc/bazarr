from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests.exceptions import JSONDecodeError, RequestException

from sonarr import info


def _response(payload):
    return SimpleNamespace(json=lambda: payload)


@pytest.fixture
def sonarr_get(monkeypatch):
    monkeypatch.setattr(info.settings.general, "use_sonarr", True)
    monkeypatch.setattr(info, "url_sonarr", lambda: "http://sonarr")
    monkeypatch.setattr(info, "region", info.make_region().configure("dogpile.cache.memory"))
    get = Mock()
    monkeypatch.setattr(info.requests, "get", get)
    return get


@pytest.mark.parametrize("legacy_payload", [{}, {"error": "Not Found"}, None, [], {"version": None},
                                             {"version": 4}, {"version": ""}])
def test_invalid_legacy_status_falls_back_to_v3(sonarr_get, legacy_payload):
    sonarr_get.side_effect = [_response(legacy_payload), _response({"version": "4.0.16.2944"})]

    assert info.get_sonarr_info.version() == "4.0.16.2944"
    assert sonarr_get.call_count == 2
    assert "/api/system/status?" in sonarr_get.call_args_list[0].args[0]
    assert "/api/v3/system/status?" in sonarr_get.call_args_list[1].args[0]
    assert info.region.get("sonarr_version") == "4.0.16.2944"


def test_invalid_legacy_json_falls_back_to_v3(sonarr_get):
    sonarr_get.side_effect = [JSONDecodeError("Invalid JSON", "", 0), _response({"version": "4.0.16.2944"})]

    assert info.get_sonarr_info.version() == "4.0.16.2944"
    assert sonarr_get.call_count == 2


@pytest.mark.parametrize("payload", [None, [], {}, {"version": None}, {"version": 4}, {"version": ""}])
def test_invalid_v3_status_returns_unknown(sonarr_get, payload):
    sonarr_get.side_effect = [JSONDecodeError("Invalid JSON", "", 0), _response(payload)]

    assert info.get_sonarr_info.version() == "unknown"
    assert info.region.get("sonarr_version") == "unknown"


@pytest.mark.parametrize("error", [RequestException("Unavailable"), JSONDecodeError("Invalid JSON", "", 0)])
def test_v3_request_errors_return_unknown(sonarr_get, error):
    sonarr_get.side_effect = [JSONDecodeError("Invalid JSON", "", 0), error]

    assert info.get_sonarr_info.version() == "unknown"


def test_valid_legacy_status_is_cached(sonarr_get):
    sonarr_get.return_value = _response({"version": "2.0.0.5344"})

    assert info.get_sonarr_info.version() == "2.0.0.5344"
    assert info.get_sonarr_info.is_legacy()
    assert info.get_sonarr_info.is_deprecated()
    assert str(info.get_sonarr_info.semver()) == "2.0.0-5344"
    sonarr_get.assert_called_once()


def test_disabled_sonarr_does_not_request_status(sonarr_get, monkeypatch):
    monkeypatch.setattr(info.settings.general, "use_sonarr", False)

    assert info.get_sonarr_info.version() == ""
    sonarr_get.assert_not_called()
