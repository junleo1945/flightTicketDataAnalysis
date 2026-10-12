"""erp auth: users, auth_sessions; chat_log.user_id index

Revision ID: a7c3e91f0b2d
Revises: 6d891c99b948
Create Date: 2026-10-12 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7c3e91f0b2d'
down_revision = '6d891c99b948'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('username', sa.String(length=128), nullable=False),
    sa.Column('display_name', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('auth_sessions',
    sa.Column('sid', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('erp_sid', sa.String(length=255), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    sa.PrimaryKeyConstraint('sid')
    )
    op.create_index('auth_sessions_user_idx', 'auth_sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_chat_log_user_id'), 'chat_log', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_chat_log_user_id'), table_name='chat_log')
    op.drop_index('auth_sessions_user_idx', table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_table('users')
