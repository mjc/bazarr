import logging
from unittest.mock import Mock

import pytest
import requests

from sonarr.sync import utils


@pytest.fixture
def sonarr_get(monkeypatch):
    monkeypatch.setattr(utils, "url_api_sonarr", lambda: "http://sonarr/api/v3/")
    monkeypatch.setattr(utils.settings.sonarr, "http_timeout", 30)
    get = Mock()
    monkeypatch.setattr(utils.requests, "get", get)
    return get


@pytest.mark.parametrize("failure_stage", ["request", "status"])
def test_series_http_error_without_response_is_logged(sonarr_get, caplog, failure_stage):
    error = requests.exceptions.HTTPError("No response attached")
    if failure_stage == "request":
        sonarr_get.side_effect = error
    else:
        sonarr_get.return_value = Mock(raise_for_status=Mock(side_effect=error))

    with caplog.at_level(logging.ERROR):
        assert utils.get_series_from_sonarr_api("test") is None

    assert "Http error." in caplog.text
    assert "No response attached" in caplog.text


@pytest.mark.parametrize("status_code", [401, 404, 500])
def test_series_http_error_with_status_still_propagates(sonarr_get, status_code):
    response = requests.Response()
    response.status_code = status_code
    response.url = "http://sonarr/api/v3/series/7"
    sonarr_get.return_value = response

    with pytest.raises(requests.exceptions.HTTPError):
        utils.get_series_from_sonarr_api("test", sonarr_series_id=7)


@pytest.mark.parametrize("error_type", [requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                                       requests.exceptions.RequestException])
def test_series_transport_errors_still_return_none(sonarr_get, error_type):
    sonarr_get.side_effect = error_type("Unavailable")

    assert utils.get_series_from_sonarr_api("test") is None
