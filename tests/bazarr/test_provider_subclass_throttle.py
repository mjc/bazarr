import datetime
import pytest
from bazarr.app import get_providers


@pytest.mark.parametrize('provider,exception_type,description', [
    ('opensubtitlescom', get_providers.TooManyRequests, '1 minute'),
    ('addic7ed', get_providers.DownloadLimitExceeded, '3 hours'),
    ('unknown', get_providers.ServiceUnavailable, '20 minutes'),
])
def test_subclass_uses_base_exception_policy(mocker, provider, exception_type, description):
    class ProviderFailure(exception_type):
        pass

    state = {}
    mocker.patch.object(get_providers, 'tp', state)
    mocker.patch.object(get_providers, 'set_throttled_providers')
    mocker.patch.object(get_providers, 'update_throttled_provider')
    mocker.patch.object(get_providers.event_tracker, 'track_throttling')
    mocker.patch.object(get_providers, 'throttled_count', return_value=True)
    get_providers.provider_throttle(provider, ProviderFailure('limited'))
    reason, until, actual = state[provider]
    assert actual == description
    assert reason == 'ProviderFailure'
    assert until > datetime.datetime.now()
