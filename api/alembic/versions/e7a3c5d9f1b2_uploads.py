"""uploads, upload_references, trust_sources, works.source, work_local_id_seq

Revision ID: e7a3c5d9f1b2
Revises: c9e1a7b4d2f0
Create Date: 2026-07-30

Data model for the PDF-upload trust-seeding feature (spec 2026-07-29). The
schema follows the spec's model plus the columns its own UI/error-handling
sections require storage for: per-entry strength, self-citation label,
"couldn't check" marker, and the upload's own (editable) title + draft-time
resolution -- recorded in DECISIONS D9.

work_local_id_seq allocates 'L'||nextval() local ids for works that exist only
because a user uploaded them. Ids are NEVER reused: the engine has no
transactional memory, so a reused id would inherit phantom edges from an
abandoned confirm (spec B6).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7a3c5d9f1b2'
down_revision: Union[str, Sequence[str], None] = 'c9e1a7b4d2f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('works', sa.Column(
        'source', sa.Text(), server_default='openalex', nullable=False))

    op.execute("CREATE SEQUENCE IF NOT EXISTS work_local_id_seq")

    op.create_table(
        'uploads',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('profile_id', sa.String(length=40), nullable=False),
        sa.Column('filename', sa.Text(), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('resolved_openalex_id', sa.String(length=24), nullable=True),
        sa.Column('resolved_work_id', sa.String(length=24), nullable=True),
        sa.Column('work_id', sa.String(length=24), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False,
                  server_default='draft'),
        sa.Column('n_parsed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('n_matched', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('n_added', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('n_unresolved', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['work_id'], ['works.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('profile_id', 'content_hash', name='uq_upload_dedupe'),
    )
    op.create_index(op.f('ix_uploads_profile_id'), 'uploads', ['profile_id'])

    op.create_table(
        'upload_references',
        sa.Column('upload_id', sa.String(length=40), nullable=False),
        sa.Column('idx', sa.Integer(), nullable=False),
        sa.Column('raw', sa.Text(), nullable=False),
        sa.Column('parsed_title', sa.Text(), nullable=True),
        sa.Column('parsed_doi', sa.Text(), nullable=True),
        sa.Column('parsed_year', sa.SmallInteger(), nullable=True),
        sa.Column('resolved_openalex_id', sa.String(length=24), nullable=True),
        sa.Column('work_id', sa.String(length=24), nullable=True),
        sa.Column('match_method', sa.String(length=16), nullable=False,
                  server_default='none'),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0'),
        sa.Column('decision', sa.String(length=16), nullable=False,
                  server_default='pending'),
        sa.Column('strength', sa.SmallInteger(), nullable=False, server_default='3'),
        sa.Column('is_self_citation', sa.Boolean(), nullable=False,
                  server_default='false'),
        sa.Column('couldnt_check', sa.Boolean(), nullable=False,
                  server_default='false'),
        sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['work_id'], ['works.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('upload_id', 'idx'),
    )

    op.create_table(
        'trust_sources',
        sa.Column('profile_id', sa.String(length=40), nullable=False),
        sa.Column('work_id', sa.String(length=24), nullable=False),
        sa.Column('upload_id', sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ['profile_id', 'work_id'],
            ['trust.profile_id', 'trust.work_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('profile_id', 'work_id', 'upload_id'),
    )
    op.create_index(op.f('ix_trust_sources_upload_id'), 'trust_sources',
                    ['upload_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_trust_sources_upload_id'), table_name='trust_sources')
    op.drop_table('trust_sources')
    op.drop_table('upload_references')
    op.drop_index(op.f('ix_uploads_profile_id'), table_name='uploads')
    op.drop_table('uploads')
    op.execute("DROP SEQUENCE IF EXISTS work_local_id_seq")
    op.drop_column('works', 'source')
