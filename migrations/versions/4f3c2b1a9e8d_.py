"""track successful episode subtitle indexing

Revision ID: 4f3c2b1a9e8d
Revises: 6f0b2c8d9a1e, 0124f9e278fb
Create Date: 2026-05-24 01:58:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4f3c2b1a9e8d'
down_revision = ('6f0b2c8d9a1e', '0124f9e278fb')
branch_labels = None
depends_on = None


bind = op.get_context().bind
insp = sa.inspect(bind)


def column_exists(table_name, column_name):
    columns = insp.get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade():
    with op.batch_alter_table('table_episodes', schema=None) as batch_op:
        if not column_exists('table_episodes', 'subtitles_last_indexed_episode_file_id'):
            batch_op.add_column(sa.Column('subtitles_last_indexed_episode_file_id', sa.Integer(), nullable=True))
        if not column_exists('table_episodes', 'subtitles_last_indexed_external_signature'):
            batch_op.add_column(sa.Column('subtitles_last_indexed_external_signature', sa.Text(), nullable=True))
        if not column_exists('table_episodes', 'subtitles_last_indexed_file_size'):
            batch_op.add_column(sa.Column('subtitles_last_indexed_file_size', sa.BigInteger(), nullable=True))
        if not column_exists('table_episodes', 'subtitles_last_indexed_path'):
            batch_op.add_column(sa.Column('subtitles_last_indexed_path', sa.Text(), nullable=True))


def downgrade():
    pass
