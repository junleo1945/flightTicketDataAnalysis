"""查询量按用户隔离：query_stat / look2book_sync 增加 user_id

历史数据：有上传批次的按批次的上传人归属；其余（ERP 缓存、无人归属）记为 0，对所有人不可见。
look2book_sync 清空，之后每个用户按需从 ERP 重新拉取。

Revision ID: e5a1c8d4f3b2
Revises: c3f7a9d2e5b1
Create Date: 2026-10-13 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5a1c8d4f3b2'
down_revision = 'c3f7a9d2e5b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('query_stat', sa.Column('user_id', sa.BigInteger(), server_default='0', nullable=False))
    op.execute(
        """UPDATE query_stat q SET user_id = COALESCE(b.user_id, 0)
           FROM upload_batch b WHERE q.batch_id = b.id"""
    )
    op.create_index('ix_query_stat_user_id', 'query_stat', ['user_id'])
    op.drop_constraint('uq_query_stat_key', 'query_stat', type_='unique')
    op.create_unique_constraint(
        'uq_query_stat_key', 'query_stat',
        ['user_id', 'biz_date', 'range_type', 'period_start', 'period_end', 'partner_type', 'partner_code'],
    )

    op.execute('DELETE FROM look2book_sync')
    op.add_column('look2book_sync', sa.Column('user_id', sa.BigInteger(), nullable=False, server_default='0'))
    op.drop_constraint('look2book_sync_pkey', 'look2book_sync', type_='primary')
    op.create_primary_key('look2book_sync_pkey', 'look2book_sync', ['user_id', 'biz_date'])


def downgrade() -> None:
    op.drop_constraint('look2book_sync_pkey', 'look2book_sync', type_='primary')
    op.create_primary_key('look2book_sync_pkey', 'look2book_sync', ['biz_date'])
    op.drop_column('look2book_sync', 'user_id')
    op.execute('DELETE FROM query_stat WHERE user_id <> 0 AND batch_id IS NULL')
    op.drop_constraint('uq_query_stat_key', 'query_stat', type_='unique')
    op.create_unique_constraint(
        'uq_query_stat_key', 'query_stat',
        ['biz_date', 'range_type', 'period_start', 'period_end', 'partner_type', 'partner_code'],
    )
    op.drop_index('ix_query_stat_user_id', table_name='query_stat')
    op.drop_column('query_stat', 'user_id')
