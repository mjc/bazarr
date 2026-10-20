# coding=utf-8

import os
import requests
import logging

from sqlalchemy import func

from app.config import settings
from utilities.path_mappings import path_mappings
from app.database import TableMoviesRootfolder, TableMovies, database, delete, update, insert, select
from radarr.info import RadarrConnection, url_api_radarr
from utilities.arr import arr_session, decode_arr_response
from utilities.json_types import JsonValue
from constants import HEADERS


def get_radarr_rootfolder(*, connection: RadarrConnection | None = None,
                          session: requests.Session | None = None) -> list[JsonValue] | None:
    radarr_rootfolder: list[tuple[int, str]] = []

    # Get root folder data from Radarr
    with arr_session(session) as http:
        api_url = url_api_radarr(session=http) if connection is None else connection.api_url
        apikey_radarr = settings.radarr.apikey if connection is None else connection.apikey
        timeout = int(settings.radarr.http_timeout) if connection is None else connection.timeout
        url_radarr_api_rootfolder = f"{api_url}rootfolder?apikey={apikey_radarr}"

        try:
            with http.get(url_radarr_api_rootfolder, timeout=timeout, verify=False,
                          headers=HEADERS, stream=True) as response:
                folders = decode_arr_response(response)
        except requests.exceptions.JSONDecodeError:
            raise
        except requests.exceptions.ConnectionError:
            logging.exception("BAZARR Error trying to get rootfolder from Radarr. Connection Error.")
            return []
        except requests.exceptions.Timeout:
            logging.exception("BAZARR Error trying to get rootfolder from Radarr. Timeout Error.")
            return []
        except requests.exceptions.RequestException:
            logging.exception("BAZARR Error trying to get rootfolder from Radarr.")
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
            select(1).select_from(TableMovies)
            .where(func.substr(TableMovies.path, 1, len(root_path)).collate(collation) == root_path)
            .exists())).scalar_one()
        if has_media:
            radarr_rootfolder.append((root_id, root_path))
    del folders
    existing_ids = set(database.execute(select(TableMoviesRootfolder.id)).scalars())
    desired_ids = {root_id for root_id, _ in radarr_rootfolder}
    for root_id in existing_ids - desired_ids:
        database.execute(delete(TableMoviesRootfolder).where(TableMoviesRootfolder.id == root_id))
    # Preserve response order and the existing update-before-insert behavior,
    # including duplicate IDs in the source response.
    for root_id, root_path in radarr_rootfolder:
        if root_id in existing_ids:
            database.execute(update(TableMoviesRootfolder).values(path=root_path)
                             .where(TableMoviesRootfolder.id == root_id))
    for root_id, root_path in radarr_rootfolder:
        if root_id not in existing_ids:
            database.execute(insert(TableMoviesRootfolder).values(id=root_id, path=root_path))


def check_radarr_rootfolder(*, connection: RadarrConnection | None = None,
                            session: requests.Session | None = None) -> None:
    get_radarr_rootfolder(connection=connection, session=session)
    rootfolder = database.execute(
        select(TableMoviesRootfolder.id, TableMoviesRootfolder.path))\
        .all()
    for item in rootfolder:
        root_path = item.path
        if not root_path.endswith(('/', '\\')):
            if root_path.startswith('/'):
                root_path += '/'
            else:
                root_path += '\\'
        if not os.path.isdir(path_mappings.path_replace_movie(root_path)):
            database.execute(
                update(TableMoviesRootfolder)
                .values(accessible=0, error='This Radarr root directory does not seem to be accessible by Bazarr. '
                                            'Please check path mapping or if directory/drive is online.')
                .where(TableMoviesRootfolder.id == item.id))
        else:
            # Try os.access() first (fast, no disk I/O)
            # Fall back to write test only if os.access() fails (e.g., NFS mounts)
            mapped_path = path_mappings.path_replace_movie(root_path)
            if os.access(mapped_path, os.W_OK):
                # Path is writable according to os.access()
                database.execute(
                    update(TableMoviesRootfolder)
                    .values(accessible=1, error='')
                    .where(TableMoviesRootfolder.id == item.id))
            else:
                # os.access() failed, try actual write test (needed for NFS)
                try:
                    test_file = os.path.join(mapped_path, '.bazarr_write_test')
                    with open(test_file, 'w') as f:
                        f.write('test')
                    os.remove(test_file)
                except Exception as e:
                    database.execute(
                        update(TableMoviesRootfolder)
                        .values(accessible=0, error=f"There's an issue with this Radarr root directory: {repr(e)}")
                        .where(TableMoviesRootfolder.id == item.id))
                else:
                    database.execute(
                        update(TableMoviesRootfolder)
                        .values(accessible=1, error='')
                        .where(TableMoviesRootfolder.id == item.id))
