"""数据授权表 data_grant：owner 的数据可被授权给其他用户只读查看

回滚：直接删表即可，不影响已有数据。

Revision ID: a1d5c7e9b3f2
Revises: f7c2a9e1d4b6
Create Date: 2026-10-13 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1d5c7e9b3f2'
down_revision = 'f7c2a9e1d4b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'data_grant',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('grantee_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['grantee_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_user_id', 'grantee_user_id', name='uq_data_grant_owner_grantee'),
    )
    op.create_index(op.f('ix_data_grant_owner_user_id'), 'data_grant', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_data_grant_grantee_user_id'), 'data_grant', ['grantee_user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_data_grant_grantee_user_id'), table_name='data_grant')
    op.drop_index(op.f('ix_data_grant_owner_user_id'), table_name='data_grant')
    op.drop_table('data_grant')
