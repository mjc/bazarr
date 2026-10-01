import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from flask import Flask

from app import database as db


frozen_migration = importlib.import_module("migrations.versions.e6cbb0f6f9b1_")
storage_migration = importlib.import_module("migrations.versions.5bf802c7a1d3_")


def test_normal_startup_resolves_one_head_after_development():
    engine = create_engine('sqlite://')
    try:
        with engine.connect() as connection, Operations.context(MigrationContext.configure(connection)):
            scripts = ScriptDirectory('migrations')
            assert scripts.get_heads() == [storage_migration.revision]
            revisions = scripts._upgrade_revs('head', '537e9b4d10e3')
            assert [step.revision.revision for step in revisions] == [frozen_migration.revision,
                                                                      storage_migration.revision]
    finally:
        engine.dispose()


def test_normal_startup_upgrades_development_database(monkeypatch, tmp_path):
    url = f'sqlite:///{tmp_path / "startup.sqlite"}'
    engine = create_engine(url, isolation_level='AUTOCOMMIT')
    db.metadata.create_all(engine)
    db.TableMissingSubtitleScans.__table__.drop(engine)
    db.TableMissingSubtitles.__table__.drop(engine)
    db.TableFailedSubtitleAttempts.__table__.drop(engine)
    try:
        with Session(engine) as session:
            monkeypatch.setattr(db, 'engine', engine)
            monkeypatch.setattr(db, 'url', url)
            monkeypatch.setattr(db, 'database', session)
            session.execute(db.text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            session.execute(db.text("INSERT INTO alembic_version VALUES ('537e9b4d10e3')"))
            session.execute(db.insert(db.TableMovies).values(
                radarrId=7, path='/movie.mkv', title='Movie', tmdbId='7',
                missing_subtitles="['en', 'en', 'fr:hi']", failedAttempts="[['en', 10], ['en', 20]]",
            ))
            db.migrate_db(Flask(__name__))
            assert session.execute(db.text('SELECT version_num FROM alembic_version')).scalar_one() == storage_migration.revision
            assert session.execute(db.select(db.TableMissingSubtitles.language).order_by(db.TableMissingSubtitles.id)).scalars().all() == ['en', 'fr:hi']
            attempt = session.execute(db.select(db.TableFailedSubtitleAttempts)).scalar_one()
            assert (attempt.language, attempt.initial_attempt_at, attempt.latest_attempt_at) == ('en', 10, 20)
            assert session.execute(db.select(db.TableMissingSubtitleScans.media_id)).scalars().all() == [7]
            # Another startup must leave the data and version intact.
            db.migrate_db(Flask(__name__))
            assert session.execute(db.select(db.func.count()).select_from(db.TableMissingSubtitles)).scalar_one() == 2
    finally:
        engine.dispose()


def test_scan_marker_downgrade_restores_legacy_wanted_state(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "downgrade.sqlite"}')
    db.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(db.insert(db.TableMovies).values(
                radarrId=7, path='/movie.mkv', title='Movie', tmdbId='7',
                missing_subtitles=None, failedAttempts=None,
            ))
            connection.execute(db.insert(db.TableMissingSubtitles), [
                {'media_type': 'movie', 'media_id': 7, 'language': 'en'},
                {'media_type': 'movie', 'media_id': 7, 'language': 'fr:hi'},
            ])
            connection.execute(db.insert(db.TableFailedSubtitleAttempts).values(
                media_type='movie', media_id=7, language='en',
                initial_attempt_at=10.0, latest_attempt_at=20.0,
            ))
            connection.execute(db.insert(db.TableMissingSubtitleScans).values(
                media_type='movie', media_id=7,
            ))
            with Operations.context(MigrationContext.configure(connection)):
                storage_migration.downgrade()

        with engine.connect() as connection:
            movie = connection.execute(db.select(
                db.TableMovies.missing_subtitles, db.TableMovies.failedAttempts,
            ).where(db.TableMovies.radarrId == 7)).one()
            assert movie.missing_subtitles == "['en', 'fr:hi']"
            assert movie.failedAttempts == "[['en', 10.0], ['en', 20.0]]"
            assert not connection.execute(db.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='table_missing_subtitle_scans'"
            )).first()
    finally:
        engine.dispose()


def test_migration_appends_missing_rows_using_frozen_legacy_parser(monkeypatch):
    rows = []

    def fail_runtime_parser(*_args, **_kwargs):
        pytest.fail("migration must not use the runtime text-list parser")

    monkeypatch.setattr(frozen_migration, "parse_text_list_or_default", fail_runtime_parser, raising=False)

    frozen_migration._append_missing_rows("movie", 7, "['en', None, 'fr:hi', 'en']", rows)

    assert rows == [("movie", 7, "en"), ("movie", 7, "fr:hi")]


def test_migration_appends_attempt_rows_using_frozen_legacy_parser(monkeypatch):
    rows = []

    def fail_runtime_parser(*_args, **_kwargs):
        pytest.fail("migration must not use the runtime attempt parser")

    monkeypatch.setattr(frozen_migration, "get_attempt_windows", fail_runtime_parser, raising=False)

    frozen_migration._append_attempt_rows("series", 17, "[['en', 1], ['en', 3], ['fr', 2]]", rows)

    assert rows == [
        ("series", 17, "en", 1.0, 3.0),
        ("series", 17, "fr", 2.0, 2.0),
    ]


def test_migration_frozen_parsers_ignore_malformed_and_non_finite_values():
    missing_rows = []
    frozen_migration._append_missing_rows("movie", 7, "['en', invalid]", missing_rows)
    assert missing_rows == []

    attempt_rows = []
    frozen_migration._append_attempt_rows("movie", 7, "[['en', inf]]", attempt_rows)
    assert attempt_rows == []
