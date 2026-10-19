import importlib
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlalchemy.sql.selectable import Exists

from app.database import TableMovies, TableMoviesRootfolder, TableShows, TableShowsRootfolder, metadata
from tests.helpers.arr_fixtures import radarr_connection, sonarr_connection


@pytest.fixture(params=['sqlite', 'postgresql'])
def root_session(request):
    if request.param == 'postgresql':
        url = os.environ.get('ROOTFOLDER_TEST_POSTGRES_URL')
        if not url:
            pytest.skip('ROOTFOLDER_TEST_POSTGRES_URL is not configured')
        owner = create_engine(url)
        schema = 'rootfolder_' + uuid4().hex
        with owner.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = owner.execution_options(schema_translate_map={None: schema})
    else:
        owner = engine = create_engine('sqlite://')
        schema = None
    metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        if schema is not None:
            with owner.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        owner.dispose()


@pytest.fixture(params=['sonarr', 'radarr'])
def roots(request, root_session, monkeypatch):
    name = request.param
    module = importlib.import_module(f'{name}.rootfolder')
    media = TableShows if name == 'sonarr' else TableMovies
    root = TableShowsRootfolder if name == 'sonarr' else TableMoviesRootfolder
    connection = sonarr_connection() if name == 'sonarr' else radarr_connection()
    monkeypatch.setattr(module, 'database', root_session)
    response = Mock(spec=requests.Response)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    http = requests.Session()
    get = Mock(return_value=response)
    monkeypatch.setattr(http, 'get', get)
    statements = []
    event.listen(root_session, 'do_orm_execute', lambda state: statements.append(state.statement))

    def add_media(path: str, number: int = 1) -> None:
        value = (TableShows(sonarrSeriesId=number, title='Show', path=path) if name == 'sonarr' else
                 TableMovies(radarrId=number, tmdbId=str(number), title='Movie', path=path))
        root_session.add(value)
        root_session.flush()

    def fetch(folders):
        response.json.return_value = folders
        return getattr(module, f'get_{name}_rootfolder')(connection=connection, session=http)

    def records():
        return root_session.execute(select(root.id, root.path, root.accessible, root.error).order_by(root.id)).all()

    yield SimpleNamespace(name=name, session=root_session, media=media, root=root, add_media=add_media,
                          fetch=fetch, records=records, statements=statements, get=get)
    http.close()


@pytest.mark.parametrize('path,prefix', [
    ('/media/TV/a.mkv', '/media/TV'),
    ('/media/tv/a.mkv', '/media/TV'),
    ('/media/TV2/a.mkv', '/media/TV'),
    ('/media/TV/a.mkv', '/media/TV/'),
    ('/media/TV2/a.mkv', '/media/TV/'),
    ('/a%_/file', '/a%_'),
    ('/aXY/file', '/a%_'),
    ('/a_b/file', '/a_b'),
    ('/axb/file', '/a_b'),
    ('C:\\TV\\a.mkv', 'C:\\TV\\'),
    ('/é/こんにちは/a', '/é/こんにちは'),
    ('/é/a', '/é/'),
    ('', ''),
    ('/media/a', ''),
    ('/media/a', '/media/ab'),
])
def test_prefix_filter_matches_python_string_semantics(roots, path: str, prefix: str) -> None:
    roots.add_media(path)
    roots.fetch([{'id': 1, 'path': prefix}])
    assert [(row.id, row.path) for row in roots.records()] == ([(1, prefix)] if path.startswith(prefix) else [])


def test_empty_prefix_does_not_match_an_empty_media_table(roots) -> None:
    roots.fetch([{'id': 1, 'path': ''}])
    assert roots.records() == []


def test_root_filter_only_returns_exists_scalars_not_media_paths(roots) -> None:
    for number in range(1, 101):
        roots.add_media(f'/root/show-{number}', number)
    roots.statements.clear()
    roots.fetch([{'id': 1, 'path': '/root/'}, {'id': 2, 'path': '/missing/'}])
    selects = [statement for statement in roots.statements if statement.is_select]
    media_selects = [statement for statement in selects
                     if isinstance(next(iter(statement.selected_columns)), Exists)]
    assert len(media_selects) == 2
    assert all(isinstance(next(iter(statement.selected_columns)), Exists) for statement in media_selects)
    assert all(roots.media.__tablename__ in str(statement) for statement in media_selects)
    root_selects = [statement for statement in selects if statement not in media_selects]
    assert len(root_selects) == 1
    assert list(root_selects[0].selected_columns.keys()) == ['id']
    collation = '"C"' if roots.session.get_bind().dialect.name == 'postgresql' else '"BINARY"'
    assert all('COLLATE ' + collation in str(statement) for statement in media_selects)


def test_reconciliation_preserves_existing_health_fields(roots) -> None:
    roots.add_media('/new/show')
    roots.session.add_all([roots.root(id=1, path='/old/', accessible=0, error='Saved error'),
                           roots.root(id=9, path='/stale/', accessible=1, error='')])
    roots.session.flush()
    roots.fetch([{'id': 1, 'path': '/new/'}, {'id': 2, 'path': '/new/show'}])
    assert [tuple(row) for row in roots.records()] == [(1, '/new/', 0, 'Saved error'), (2, '/new/show', None, None)]


def test_duplicate_existing_ids_keep_response_order(roots) -> None:
    roots.add_media('/root/show')
    roots.session.add(roots.root(id=1, path='/old/', accessible=1, error='Saved'))
    roots.session.flush()
    roots.fetch([{'id': 1, 'path': '/root/'}, {'id': 1, 'path': '/root/show'}])
    assert [tuple(row) for row in roots.records()] == [(1, '/root/show', 1, 'Saved')]


def test_duplicate_new_ids_preserve_integrity_error_and_update_before_insert(roots) -> None:
    roots.add_media('/root/show')
    roots.session.add(roots.root(id=1, path='/old/'))
    roots.session.flush()
    roots.statements.clear()
    with pytest.raises(IntegrityError), roots.session.begin_nested():
        roots.fetch([{'id': 2, 'path': '/root/'}, {'id': 2, 'path': '/root/show'},
                     {'id': 1, 'path': '/root/'}])
    writes = [statement for statement in roots.statements if statement.is_update or statement.is_insert]
    assert writes[0].is_update
    assert sum(statement.is_insert for statement in writes) == 2


@pytest.mark.parametrize('error', [requests.ConnectionError, requests.Timeout, requests.HTTPError])
def test_http_failure_does_not_reconcile_existing_folders(roots, error) -> None:
    roots.session.add(roots.root(id=1, path='/keep/', accessible=0, error='Saved'))
    roots.session.flush()
    roots.get.side_effect = error('Unavailable')
    assert roots.fetch([]) == []
    assert [tuple(row) for row in roots.records()] == [(1, '/keep/', 0, 'Saved')]


@pytest.mark.parametrize('malformed', [None, {}, [None], [{'id': 1, 'path': None}],
                                       [{'id': True, 'path': '/root/'}], [{'id': 1, 'path': '/root\x00'}],
                                       [{'id': 1, 'path': '/root/'}, {'id': 2, 'path': '/root\x00'}]])
def test_malformed_response_preserves_existing_folder_snapshot(roots, malformed) -> None:
    roots.add_media('/root/show')
    roots.session.add(roots.root(id=9, path='/keep/', accessible=0, error='Saved'))
    roots.session.flush()
    assert roots.fetch(malformed) == []
    assert [tuple(row) for row in roots.records()] == [(9, '/keep/', 0, 'Saved')]


def test_valid_empty_response_removes_old_root_folders(roots) -> None:
    roots.session.add(roots.root(id=1, path='/old/'))
    roots.session.flush()
    roots.fetch([])
    assert roots.records() == []
