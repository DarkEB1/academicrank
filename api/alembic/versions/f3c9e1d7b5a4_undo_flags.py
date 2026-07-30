"""undo provenance flags: trust_sources.created_trust, upload_references.created_citation

Revision ID: f3c9e1d7b5a4
Revises: e7a3c5d9f1b2
Create Date: 2026-07-30

The undo survivorship rule (spec B5-method) needs to know two things the Phase-2
schema could not record: whether THIS upload's confirm created the trust row (a
hand-added row must survive the upload's undo even when no other source
remains), and whether the confirm created the citation row (a corpus paper
uploaded by its author may already carry some of its citations -- deleting
those on undo would destroy corpus data).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3c9e1d7b5a4'
down_revision: Union[str, Sequence[str], None] = 'e7a3c5d9f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trust_sources', sa.Column(
        'created_trust', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('upload_references', sa.Column(
        'created_citation', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('upload_references', 'created_citation')
    op.drop_column('trust_sources', 'created_trust')
