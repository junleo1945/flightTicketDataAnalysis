"""查订比接口缓存：query_stat 支持无批次与来源标记；新增 look2book_sync

Revision ID: c3f7a9d2e5b1
Revises: b8d4f2a6c1e9
Create Date: 2026-10-12 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3f7a9d2e5b1'
down_revision = 'b8d4f2a6c1e9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('query_stat', 'batch_id', existing_type=sa.Integer(), nullable=True)
    op.add_column('query_stat', sa.Column('source', sa.String(length=16), server_default='excel', nullable=False))
    op.create_table('look2book_sync',
    sa.Column('biz_date', sa.Date(), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('biz_date')
    )


def downgrade() -> None:
    op.drop_table('look2book_sync')
    op.drop_column('query_stat', 'source')
    op.alter_column('query_stat', 'batch_id', existing_type=sa.Integer(), nullable=False)
