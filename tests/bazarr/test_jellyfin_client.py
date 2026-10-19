"""Tests for the Jellyfin HTTP client.

Mock responses are validated against Jellyfin's OpenAPI spec via fake_jellyfin.
"""

import os
from unittest.mock import patch, MagicMock

import pytest
from bazarr.jellyfin.client import JellyfinClient
from fake_jellyfin import (
    FakeJellyfinClient,
    OPENAPI_PATH,
    make_movie,
    make_episode,
)


@pytest.fixture
def client():
    return JellyfinClient("http://jellyfin:8096", "test-api-key")



def test_fake_client_has_same_interface_as_real():
    """Ensure FakeJellyfinClient implements all public methods of JellyfinClient."""
    real_methods = {m for m in dir(JellyfinClient) if not m.startswith('_')}
    fake_methods = {m for m in dir(FakeJellyfinClient) if not m.startswith('_')}
    missing = real_methods - fake_methods
    assert not missing, f"FakeJellyfinClient is missing methods: {missing}"


@pytest.mark.skipif(not os.path.exists(OPENAPI_PATH), reason="jellyfin-openapi.json not found")
class TestFakeClientMatchesContract:
    """Verify that FakeJellyfinClient returns schema-valid responses."""

    def test_system_info(self):
        fake = FakeJellyfinClient()
        fake.get_system_info()  # validates internally

    def test_libraries(self):
        fake = FakeJellyfinClient()
        fake.get_libraries()

    def test_items(self):
        fake = FakeJellyfinClient()
        fake.items = [make_movie()]
        fake.get_items({})

    def test_episodes(self):
        fake = FakeJellyfinClient()
        fake.episodes = {"s1": {1: [make_episode()]}}
        fake.get_episodes("s1", 1)

    def test_empty_items(self):
        fake = FakeJellyfinClient()
        fake.get_items({})

    def test_empty_episodes(self):
        fake = FakeJellyfinClient()
        fake.get_episodes("s1", 1)



@patch.object(JellyfinClient, "post")
def test_refresh_item(mock_post, client):
    mock_post.return_value = MagicMock()
    client.refresh_item("item-123")
    mock_post.assert_called_once_with("/Items/item-123/Refresh", params={
        "metadataRefreshMode": "ValidationOnly",
        "imageRefreshMode": "None",
        "replaceAllMetadata": "false",
        "replaceAllImages": "false",
    })


@patch.object(JellyfinClient, "post")
def test_report_media_updated(mock_post, client):
    mock_post.return_value = MagicMock()
    client.report_media_updated("/media/movies/Test Movie")
    mock_post.assert_called_once_with("/Library/Media/Updated", json={
        "Updates": [{"Path": "/media/movies/Test Movie", "UpdateType": "Modified"}],
    })


@pytest.mark.parametrize('method', ['items', 'episodes', 'libraries'])
@pytest.mark.parametrize('payload', [None, {}, {'Items': None}, {'Items': {}}, 'invalid'])
def test_invalid_collection_response_is_distinct_from_empty(client, method, payload):
    with patch.object(client, 'get') as request:
        request.return_value.json.return_value = payload
        result = (client.get_items({}) if method == 'items' else
                  client.get_episodes('series', 1) if method == 'episodes' else client.get_libraries())
    assert result is None


@pytest.mark.parametrize('method', ['items', 'episodes', 'libraries'])
def test_valid_empty_collection(client, method):
    with patch.object(client, 'get') as request:
        request.return_value.json.return_value = [] if method == 'libraries' else {'Items': []}
        result = (client.get_items({}) if method == 'items' else
                  client.get_episodes('series', 1) if method == 'episodes' else client.get_libraries())
    assert result == []


def test_items_reuse_nullable_records_and_provider_mapping(client):
    providers = {'Imdb': None, 'Tmdb': '123'}
    movie = {'Id': 'movie', 'Name': None, 'Path': None, 'ProviderIds': providers}
    with patch.object(client, 'get') as request:
        request.return_value.json.return_value = {'Items': [movie]}
        items = client.get_items({})
    assert items is not None and items[0] is movie
    assert items[0]['ProviderIds'] is providers


@pytest.mark.parametrize('field,value', [('Name', None), ('Path', None), ('ProviderIds', None),
                                        ('ProviderIds', {'Imdb': None})])
def test_fake_accepts_openapi_nullable_values(field, value):
    fake = FakeJellyfinClient()
    movie = make_movie()
    movie[field] = value
    fake.items = [movie]
    assert fake.get_items({}) == [movie]


@pytest.mark.parametrize('field,value', [('Name', []), ('Path', 12), ('ProviderIds', []),
                                        ('ProviderIds', {'Imdb': 12})])
def test_fake_rejects_invalid_non_null_values(field, value):
    from jsonschema import ValidationError
    fake = FakeJellyfinClient()
    movie = make_movie()
    movie[field] = value
    fake.items = [movie]
    with pytest.raises(ValidationError):
        fake.get_items({})


def test_fake_accepts_nullable_virtual_folder_type():
    fake = FakeJellyfinClient()
    fake.libraries = [{'Name': 'Mixed', 'ItemId': 'mixed', 'CollectionType': None}]
    assert fake.get_libraries() == fake.libraries


@pytest.mark.parametrize('method', ['episodes', 'libraries'])
def test_other_collection_boundaries_reuse_valid_records(client, method):
    record = (make_episode() if method == 'episodes' else
              {'Name': 'Movies', 'ItemId': 'movies', 'CollectionType': 'movies'})
    with patch.object(client, 'get') as request:
        request.return_value.json.return_value = ({'Items': [None, record]} if method == 'episodes' else [None, record])
        result = client.get_episodes('series', 1) if method == 'episodes' else client.get_libraries()
    assert result is not None and result[0] is record


