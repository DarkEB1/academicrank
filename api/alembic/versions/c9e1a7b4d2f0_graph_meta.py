"""graph_meta: persisted graph generation counter

Revision ID: c9e1a7b4d2f0
Revises: bec852712a4a
Create Date: 2026-07-29

Replaces max(graph_edges.id) as the cache-invalidation marker (spec N1: ABA and
process-locality holes). Seeded at version 1 so readers never special-case an
empty table on a migrated database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9e1a7b4d2f0'
down_revision: Union[str, Sequence[str], None] = 'bec852712a4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'graph_meta',
        sa.Column('id', sa.SmallInteger(), nullable=False),
        sa.Column('version', sa.BigInteger(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_graph_meta_singleton'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute("INSERT INTO graph_meta (id, version) VALUES (1, 1)")


def downgrade() -> None:
    op.drop_table('graph_meta')
