# coding=utf-8

import logging
import os
from collections.abc import Mapping
from typing import TypeGuard, TypedDict

import requests

from utilities.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)


class _ItemId(TypedDict):
    Id: str


class JellyfinItem(_ItemId, total=False):
    Name: str | None
    Path: str | None
    ProviderIds: dict[str, str | None] | None


class JellyfinEpisode(_ItemId):
    IndexNumber: int


class _LibraryName(TypedDict):
    Name: str


class JellyfinLibrary(_LibraryName, total=False):
    ItemId: str | None
    CollectionType: str | None


def _is_item(payload: JsonValue) -> TypeGuard[JellyfinItem]:
    if not isinstance(payload, dict):
        return False
    item_id = payload.get('Id')
    if not isinstance(item_id, str) or not item_id:
        return False
    if any(value is not None and not isinstance(value, str)
           for value in (payload.get('Name'), payload.get('Path'))):
        return False
    provider_ids = payload.get('ProviderIds')
    return provider_ids is None or (isinstance(provider_ids, dict) and
                                   all(value is None or isinstance(value, str) for value in provider_ids.values()))


def _is_episode(payload: JsonValue) -> TypeGuard[JellyfinEpisode]:
    if not isinstance(payload, dict):
        return False
    item_id, number = payload.get('Id'), payload.get('IndexNumber')
    return isinstance(item_id, str) and bool(item_id) and type(number) is int


def _is_library(payload: JsonValue) -> TypeGuard[JellyfinLibrary]:
    if not isinstance(payload, dict) or not isinstance(payload.get('Name'), str):
        return False
    return all(value is None or isinstance(value, str)
               for value in (payload.get('ItemId'), payload.get('CollectionType')))


def _item_records(payload: JsonValue) -> list[JsonValue] | None:
    if not isinstance(payload, dict):
        return None
    items = payload.get('Items')
    return items if isinstance(items, list) else None


class JellyfinClient:
    """Thin HTTP client for the Jellyfin REST API."""

    def __init__(self, url: str, api_key: str):
        self.base_url = url.rstrip('/')
        self.session = requests.Session()
        self.session.verify = False

        bazarr_version = os.environ.get('BAZARR_VERSION', 'unknown')
        self.session.headers.update({
            'Authorization': (
                f'MediaBrowser Client="Bazarr", Device="Bazarr", '
                f'DeviceId="bazarr", Version="{bazarr_version}", '
                f'Token="{api_key}"'
            ),
            'Content-Type': 'application/json',
        })

    def _url(self, path: str) -> str:
        return f'{self.base_url}{path}'

    def get(self, path: str, params: Mapping[str, str | int] | None = None) -> requests.Response:
        response = self.session.get(self._url(path), params=params, timeout=30)
        response.raise_for_status()
        return response

    def post(self, path: str, json: JsonObject | None = None,
             params: Mapping[str, str | int] | None = None) -> requests.Response:
        response = self.session.post(self._url(path), json=json, params=params, timeout=30)
        response.raise_for_status()
        return response

    def get_system_info(self) -> JsonObject | None:
        """GET /System/Info — returns server name, version, id."""
        payload: JsonValue = self.get('/System/Info').json()
        return payload if isinstance(payload, dict) else None

    def get_libraries(self) -> list[JellyfinLibrary] | None:
        """GET /Library/VirtualFolders — returns all library folders."""
        payload: JsonValue = self.get('/Library/VirtualFolders').json()
        return [library for library in payload if _is_library(library)] if isinstance(payload, list) else None

    def get_items(self, params: Mapping[str, str | int]) -> list[JellyfinItem] | None:
        """GET /Items — search/browse items with query parameters."""
        payload: JsonValue = self.get('/Items', params=params).json()
        items = _item_records(payload)
        return [item for item in items if _is_item(item)] if items is not None else None

    def get_episodes(self, series_id: str, season: int) -> list[JellyfinEpisode] | None:
        """GET /Shows/{seriesId}/Episodes — list episodes for a season."""
        payload: JsonValue = self.get(f'/Shows/{series_id}/Episodes', params={
            'season': season,
            'fields': 'ProviderIds',
        }).json()
        items = _item_records(payload)
        return [episode for episode in items if _is_episode(episode)] if items is not None else None

    def refresh_item(self, item_id: str, metadata_refresh_mode: str = 'ValidationOnly',
                     image_refresh_mode: str = 'None') -> None:
        """POST /Items/{itemId}/Refresh — default to a lightweight subtitle refresh."""
        self.post(f'/Items/{item_id}/Refresh', params={
            'metadataRefreshMode': metadata_refresh_mode,
            'imageRefreshMode': image_refresh_mode,
            'replaceAllMetadata': 'false',
            'replaceAllImages': 'false',
        })

    def report_media_updated(self, path: str) -> None:
        """POST /Library/Media/Updated — notify Jellyfin of filesystem changes.

        Triggers filesystem change detection for the given path.
        """
        self.post('/Library/Media/Updated', json={
            'Updates': [{'Path': path, 'UpdateType': 'Modified'}],
        })

