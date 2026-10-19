import pytest


def test_invalid_history_timeframe_returns_bad_request(api_client, api_key):
    response = api_client.get('/api/history/stats?timeFrame=day', headers={'X-API-KEY': api_key})
    assert response.status_code == 400


@pytest.mark.parametrize('timeframe', ['week', 'month', 'trimester', 'year'])
def test_valid_history_timeframe_remains_accepted(api_client, api_key, timeframe):
    response = api_client.get('/api/history/stats', query_string={'timeFrame': timeframe},
                              headers={'X-API-KEY': api_key})
    assert response.status_code == 200
