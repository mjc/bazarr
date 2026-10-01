"""track completed missing-subtitle scans separately

Revision ID: 5bf802c7a1d3
Revises: e6cbb0f6f9b1
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '5bf802c7a1d3'
down_revision = 'e6cbb0f6f9b1'
branch_labels = None
depends_on = None

MISSING_SUBTITLE_SCANS = sa.table(
    'table_missing_subtitle_scans',
    sa.column('media_type', sa.Text),
    sa.column('media_id', sa.Integer),
)
MISSING_SUBTITLES = sa.table(
    'table_missing_subtitles',
    sa.column('media_type', sa.Text),
    sa.column('media_id', sa.Integer),
    sa.column('language', sa.Text),
)
FAILED_ATTEMPTS = sa.table(
    'table_failed_subtitle_attempts',
    sa.column('media_type', sa.Text),
    sa.column('media_id', sa.Integer),
    sa.column('language', sa.Text),
    sa.column('initial_attempt_at', sa.Float),
    sa.column('latest_attempt_at', sa.Float),
)

MEDIA_TABLES = (
    ('movie', 'table_movies', 'radarrId'),
    ('series', 'table_episodes', 'sonarrEpisodeId'),
)


def _serialize_attempt_windows(attempt_windows):
    attempts = []
    for language, (initial, latest) in attempt_windows.items():
        attempts.append([language, initial])
        if latest != initial:
            attempts.append([language, latest])
    return repr(sorted(attempts, key=lambda attempt: attempt[0]))


def upgrade():
    bind = op.get_bind()
    op.create_table(
        'table_missing_subtitle_scans',
        sa.Column('media_type', sa.Text(), nullable=False),
        sa.Column('media_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('media_type', 'media_id', name='pk_missing_subtitle_scans'),
    )

    for media_type, table_name, id_column in MEDIA_TABLES:
        result = bind.exec_driver_sql(
            f'SELECT "{id_column}" FROM {table_name} WHERE missing_subtitles IS NOT NULL'
        )
        while rows := result.fetchmany(500):
            bind.execute(sa.insert(MISSING_SUBTITLE_SCANS), [
                {'media_type': media_type, 'media_id': media_id}
                for (media_id,) in rows
            ])


def downgrade():
    bind = op.get_bind()
    for media_type, table_name, id_column_name in MEDIA_TABLES:
        media = sa.table(
            table_name,
            sa.column(id_column_name, sa.Integer),
            sa.column('missing_subtitles', sa.Text),
            sa.column('failedAttempts', sa.Text),
        )
        last_media_id = None
        while True:
            media_query = f'SELECT "{id_column_name}" FROM {table_name}'
            params = {}
            if last_media_id is not None:
                media_query += f' WHERE "{id_column_name}" > :last_media_id'
                params['last_media_id'] = last_media_id
            media_query += f' ORDER BY "{id_column_name}" LIMIT 500'
            media_ids = [row[0] for row in bind.execute(sa.text(media_query), params)]
            if not media_ids:
                break
            last_media_id = media_ids[-1]

            missing_by_id = {}
            for media_id, language in bind.execute(
                sa.select(MISSING_SUBTITLES.c.media_id, MISSING_SUBTITLES.c.language)
                .where(MISSING_SUBTITLES.c.media_type == media_type)
                .where(MISSING_SUBTITLES.c.media_id.in_(media_ids))
                .order_by(MISSING_SUBTITLES.c.media_id)
            ):
                missing_by_id.setdefault(media_id, []).append(language)

            attempts_by_id = {}
            for media_id, language, initial, latest in bind.execute(
                sa.select(
                    FAILED_ATTEMPTS.c.media_id,
                    FAILED_ATTEMPTS.c.language,
                    FAILED_ATTEMPTS.c.initial_attempt_at,
                    FAILED_ATTEMPTS.c.latest_attempt_at,
                )
                .where(FAILED_ATTEMPTS.c.media_type == media_type)
                .where(FAILED_ATTEMPTS.c.media_id.in_(media_ids))
                .order_by(FAILED_ATTEMPTS.c.media_id)
            ):
                attempts_by_id.setdefault(media_id, {})[language] = (initial, latest)

            scanned_ids = set(bind.execute(
                sa.select(MISSING_SUBTITLE_SCANS.c.media_id)
                .where(MISSING_SUBTITLE_SCANS.c.media_type == media_type)
                .where(MISSING_SUBTITLE_SCANS.c.media_id.in_(media_ids))
            ).scalars())
            bind.execute(
                sa.update(media)
                .where(media.c[id_column_name] == sa.bindparam('_media_id'))
                .values(
                    missing_subtitles=sa.bindparam('_missing_subtitles'),
                    failedAttempts=sa.bindparam('_failed_attempts'),
                ),
                [
                    {
                        '_media_id': media_id,
                        '_missing_subtitles': repr(missing_by_id.get(media_id, [])) if media_id in scanned_ids else None,
                        '_failed_attempts': _serialize_attempt_windows(attempts_by_id.get(media_id, {})),
                    }
                    for media_id in media_ids
                ],
            )

    op.drop_table('table_missing_subtitle_scans')
