"""数据质量改为接口获取：新建 data_quality_stat / data_quality_sync，删除 supply_quality 与数据质量上传批次

旧的上传数据不保留（没有分析工具使用过，口径也与接口不同）。

回滚：不支持。数据已删除，无法还原。

Revision ID: d2e8b4a7c9f1
Revises: a1d5c7e9b3f2
Create Date: 2026-10-22 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'd2e8b4a7c9f1'
down_revision = 'a1d5c7e9b3f2'
branch_labels = None
depends_on = None

COUNT_COLS = [
    'total', 'timeout_5_10', 'timeout_gt_10', 'verify_success', 'verify_fail', 'supplier_success',
    'supplier_fail', 'supplier_changed', 'unbook', 'booking_success', 'booking_fail',
]
RATE_COLS = ['booking_rate', 'booking_success_rate', 'timeout_rate']


def upgrade() -> None:
    op.create_table(
        'data_quality_stat',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stat_date', sa.Date(), nullable=False),
        sa.Column('agent_code', sa.String(length=64), nullable=False),
        sa.Column('supplier_code', sa.String(length=64), nullable=False),
        sa.Column('ticket_carrier', sa.String(length=8), nullable=False),
        *[sa.Column(c, sa.BigInteger(), server_default='0', nullable=False) for c in COUNT_COLS],
        *[sa.Column(c, sa.Numeric(10, 6), server_default='0', nullable=False) for c in RATE_COLS],
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stat_date', 'agent_code', 'supplier_code', 'ticket_carrier', name='uq_data_quality_stat_key'),
    )
    op.create_index('ix_data_quality_stat_stat_date', 'data_quality_stat', ['stat_date'])
    op.create_table(
        'data_quality_sync',
        sa.Column('stat_date', sa.Date(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('stat_date'),
    )
    # 先删明细表（外键指向 upload_batch），再删批次
    op.drop_table('supply_quality')
    op.execute("DELETE FROM upload_batch WHERE file_type = 'quality'")


def downgrade() -> None:
    raise RuntimeError('不支持回滚：supply_quality 的数据已删除')
