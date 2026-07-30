"""Weighted search vector: title=A, abstract=B (KNOWN_ISSUES #13).

The old vector concatenated title and abstract unweighted, so ts_rank treated a
title hit and an abstract hit alike and long abstracts diluted the signal -- an
exact title query frequently did not come back first. setweight fixes the ranking
side; the GIN index (ix_works_tsv) is expression-independent and needs no rebuild.

scripts/load_db.py builds the same weighted vector for future corpus loads; this
migration rewrites the rows that already exist.

Revision ID: a9d4f0c1b3e5
Revises: f3c9e1d7b5a4
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9d4f0c1b3e5'
down_revision: Union[str, Sequence[str], None] = 'f3c9e1d7b5a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE works SET tsv = "
        "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(abstract, '')), 'B')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE works SET tsv = to_tsvector('english', "
        "coalesce(title, '') || ' ' || coalesce(abstract, ''))"
    )
