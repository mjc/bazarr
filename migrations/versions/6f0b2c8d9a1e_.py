"""Add indexes for subtitle sync queries

Revision ID: 6f0b2c8d9a1e
Revises: 309dc062d2e4
Create Date: 2026-04-22 12:35:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6f0b2c8d9a1e'
down_revision = '309dc062d2e4'
branch_labels = None
depends_on = None

bind = op.get_context().bind
insp = sa.inspect(bind)


def index_exists(table_name, index_name):
    indexes = insp.get_indexes(table_name)
    return any(i["name"] == index_name for i in indexes)


def upgrade():
    if not index_exists('table_episodes', 'idx_table_episodes_sonarrSeriesId'):
        op.create_index('idx_table_episodes_sonarrSeriesId', 'table_episodes', ['sonarrSeriesId'])

    if not index_exists('table_episodes', 'idx_table_episodes_missing_subtitles'):
        op.create_index(
            'idx_table_episodes_missing_subtitles',
            'table_episodes',
            ['missing_subtitles'],
            sqlite_where=sa.text("missing_subtitles IS NOT NULL AND missing_subtitles != '[]'"),
        )

    if not index_exists('table_movies', 'idx_table_movies_missing_subtitles'):
        op.create_index(
            'idx_table_movies_missing_subtitles',
            'table_movies',
            ['missing_subtitles'],
            sqlite_where=sa.text("missing_subtitles IS NOT NULL AND missing_subtitles != '[]'"),
        )


def downgrade():
    if index_exists('table_movies', 'idx_table_movies_missing_subtitles'):
        op.drop_index('idx_table_movies_missing_subtitles', table_name='table_movies')

    if index_exists('table_episodes', 'idx_table_episodes_missing_subtitles'):
        op.drop_index('idx_table_episodes_missing_subtitles', table_name='table_episodes')

    if index_exists('table_episodes', 'idx_table_episodes_sonarrSeriesId'):
        op.drop_index('idx_table_episodes_sonarrSeriesId', table_name='table_episodes')
