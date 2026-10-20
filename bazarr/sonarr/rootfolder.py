# coding=utf-8

import os
import requests
import logging

from sqlalchemy import func

from app.config import settings
from app.database import TableShowsRootfolder, TableShows, database, insert, update, delete, select
from utilities.path_mappings import path_mappings
from sonarr.info import SonarrConnection, url_api_sonarr
from utilities.arr import arr_session, decode_arr_response
from utilities.json_types import JsonValue
from constants import HEADERS


def get_sonarr_rootfolder(*, connection: SonarrConnection | None = None,
                          session: requests.Session | None = None) -> list[JsonValue] | None:
    sonarr_rootfolder: list[tuple[int, str]] = []

    # Get root folder data from Sonarr
    with arr_session(session) as http:
        api_url = url_api_sonarr(session=http) if connection is None else connection.api_url
        apikey_sonarr = settings.sonarr.apikey if connection is None else connection.apikey
        timeout = int(settings.sonarr.http_timeout) if connection is None else connection.timeout
        url_sonarr_api_rootfolder = f"{api_url}rootfolder?apikey={apikey_sonarr}"

        try:
            with http.get(url_sonarr_api_rootfolder, timeout=timeout, verify=False,
                          headers=HEADERS, stream=True) as response:
                folders = decode_arr_response(response)
        except requests.exceptions.JSONDecodeError:
            raise
        except requests.exceptions.ConnectionError:
            logging.exception("BAZARR Error trying to get rootfolder from Sonarr. Connection Error.")
            return []
        except requests.exceptions.Timeout:
            logging.exception("BAZARR Error trying to get rootfolder from Sonarr. Timeout Error.")
            return []
        except requests.exceptions.RequestException:
            logging.exception("BAZARR Error trying to get rootfolder from Sonarr.")
            return []
        else:
            del response

    if not isinstance(folders, list):
        return []
    collation = 'C' if database.get_bind().dialect.name == 'postgresql' else 'BINARY'
    for folder in folders:
        if not isinstance(folder, dict):
            return []
        root_id, root_path = folder.get('id'), folder.get('path')
        if type(root_id) is not int or not isinstance(root_path, str) or '\x00' in root_path:
            return []
        has_media = database.execute(select(
            select(1).select_from(TableShows)
            .where(func.substr(TableShows.path, 1, len(root_path)).collate(collation) == root_path)
            .exists())).scalar_one()
        if has_media:
            sonarr_rootfolder.append((root_id, root_path))
    del folders
    existing_ids = set(database.execute(select(TableShowsRootfolder.id)).scalars())
    desired_ids = {root_id for root_id, _ in sonarr_rootfolder}
    for root_id in existing_ids - desired_ids:
        database.execute(delete(TableShowsRootfolder).where(TableShowsRootfolder.id == root_id))
    # Preserve response order and the existing update-before-insert behavior,
    # including duplicate IDs in the source response.
    for root_id, root_path in sonarr_rootfolder:
        if root_id in existing_ids:
            database.execute(update(TableShowsRootfolder).values(path=root_path)
                             .where(TableShowsRootfolder.id == root_id))
    for root_id, root_path in sonarr_rootfolder:
        if root_id not in existing_ids:
            database.execute(insert(TableShowsRootfolder).values(id=root_id, path=root_path))


def check_sonarr_rootfolder(*, connection: SonarrConnection | None = None,
                            session: requests.Session | None = None) -> None:
    get_sonarr_rootfolder(connection=connection, session=session)
    rootfolder = database.execute(
        select(TableShowsRootfolder.id, TableShowsRootfolder.path))\
        .all()
    for item in rootfolder:
        root_path = item.path
        if not root_path.endswith(('/', '\\')):
            if root_path.startswith('/'):
                root_path += '/'
            else:
                root_path += '\\'
        if not os.path.isdir(path_mappings.path_replace(root_path)):
            database.execute(
                update(TableShowsRootfolder)
                .values(accessible=0, error='This Sonarr root directory does not seem to be accessible by Bazarr. '
                                            'Please check path mapping or if directory/drive is online.')
                .where(TableShowsRootfolder.id == item.id))
        else:
            # Try os.access() first (fast, no disk I/O)
            # Fall back to write test only if os.access() fails (e.g., NFS mounts)
            mapped_path = path_mappings.path_replace(root_path)
            if os.access(mapped_path, os.W_OK):
                # Path is writable according to os.access()
                database.execute(
                    update(TableShowsRootfolder)
                    .values(accessible=1, error='')
                    .where(TableShowsRootfolder.id == item.id))
            else:
                # os.access() failed, try actual write test (needed for NFS)
                try:
                    test_file = os.path.join(mapped_path, '.bazarr_write_test')
                    with open(test_file, 'w') as f:
                        f.write('test')
                    os.remove(test_file)
                except Exception as e:
                    database.execute(
                        update(TableShowsRootfolder)
                        .values(accessible=0, error=f"There's an issue with this Sonarr root directory: {repr(e)}")
                        .where(TableShowsRootfolder.id == item.id))
                else:
                    database.execute(
                        update(TableShowsRootfolder)
                        .values(accessible=1, error='')
                        .where(TableShowsRootfolder.id == item.id))
