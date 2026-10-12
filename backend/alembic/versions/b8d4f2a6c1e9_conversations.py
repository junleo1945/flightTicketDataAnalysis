"""conversations: 会话表；chat_log 关联会话并保存完整 trace

Revision ID: b8d4f2a6c1e9
Revises: a7c3e91f0b2d
Create Date: 2026-10-12 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b8d4f2a6c1e9'
down_revision = 'a7c3e91f0b2d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('pinned', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)

    op.add_column('chat_log', sa.Column('conversation_id', sa.Integer(), nullable=True))
    op.add_column('chat_log', sa.Column('trace', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_foreign_key('chat_log_conversation_id_fkey', 'chat_log', 'conversations', ['conversation_id'], ['id'], ondelete='CASCADE')
    op.create_index(op.f('ix_chat_log_conversation_id'), 'chat_log', ['conversation_id'], unique=False)
    op.alter_column('chat_log', 'created_at',
                    type_=sa.DateTime(timezone=True),
                    existing_type=sa.DateTime(),
                    postgresql_using="created_at AT TIME ZONE 'UTC'")


def downgrade() -> None:
    op.alter_column('chat_log', 'created_at',
                    type_=sa.DateTime(),
                    existing_type=sa.DateTime(timezone=True),
                    postgresql_using="created_at AT TIME ZONE 'UTC'")
    op.drop_index(op.f('ix_chat_log_conversation_id'), table_name='chat_log')
    op.drop_constraint('chat_log_conversation_id_fkey', 'chat_log', type_='foreignkey')
    op.drop_column('chat_log', 'trace')
    op.drop_column('chat_log', 'conversation_id')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_table('conversations')
