"""订单表、数据质量表按上传人隔离：增加 user_id，主键与唯一键包含 user_id

历史数据：有批次的按批次上传人归属；无归属记为 0（对所有人不可见）。

回滚：不支持。按用户隔离后同一订单号可能存在多份，无法还原为全局唯一主键。

Revision ID: f7c2a9e1d4b6
Revises: e5a1c8d4f3b2
Create Date: 2026-10-13 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'f7c2a9e1d4b6'
down_revision = 'e5a1c8d4f3b2'
branch_labels = None
depends_on = None

# 表 → 需要增加 user_id 的表（supplier_order / order_airline / order_route 通过订单号回填）
BY_BATCH = ['tm_order', 'supplier_order', 'supply_quality']
CHILDREN = ['order_airline', 'order_route']


def _fk_names(table: str) -> list[str]:
    insp = sa.inspect(op.get_bind())
    return [fk['name'] for fk in insp.get_foreign_keys(table) if fk.get('referred_table') == 'tm_order']


def _pk_name(table: str) -> str:
    insp = sa.inspect(op.get_bind())
    return insp.get_pk_constraint(table)['name']


def upgrade() -> None:
    for t in BY_BATCH + CHILDREN:
        op.add_column(t, sa.Column('user_id', sa.BigInteger(), server_default='0', nullable=False))

    # 回填：按批次的上传人归属
    for t in BY_BATCH:
        op.execute(f"UPDATE {t} x SET user_id = COALESCE(b.user_id, 0) FROM upload_batch b WHERE x.batch_id = b.id")
    # 子表跟随父订单（此时 tm_order 仍是 tm_order_no 唯一）
    for t in CHILDREN:
        op.execute(f"UPDATE {t} x SET user_id = o.user_id FROM tm_order o WHERE x.tm_order_no = o.tm_order_no")

    # 去掉指向 tm_order 的外键，再替换主键
    for t in ['supplier_order'] + CHILDREN:
        for name in _fk_names(t):
            op.drop_constraint(name, t, type_='foreignkey')

    op.drop_constraint(_pk_name('tm_order'), 'tm_order', type_='primary')
    op.create_primary_key('tm_order_pkey', 'tm_order', ['user_id', 'tm_order_no'])
    op.drop_constraint(_pk_name('supplier_order'), 'supplier_order', type_='primary')
    op.create_primary_key('supplier_order_pkey', 'supplier_order', ['user_id', 'tm_supplier_order_no'])
    op.drop_constraint(_pk_name('order_airline'), 'order_airline', type_='primary')
    op.create_primary_key('order_airline_pkey', 'order_airline', ['user_id', 'tm_order_no', 'airline_code'])
    op.drop_constraint(_pk_name('order_route'), 'order_route', type_='primary')
    op.create_primary_key('order_route_pkey', 'order_route', ['user_id', 'tm_order_no', 'route'])

    op.create_foreign_key('supplier_order_user_order_fk', 'supplier_order', 'tm_order',
                          ['user_id', 'tm_order_no'], ['user_id', 'tm_order_no'])
    op.create_foreign_key('order_airline_user_order_fk', 'order_airline', 'tm_order',
                          ['user_id', 'tm_order_no'], ['user_id', 'tm_order_no'])
    op.create_foreign_key('order_route_user_order_fk', 'order_route', 'tm_order',
                          ['user_id', 'tm_order_no'], ['user_id', 'tm_order_no'])

    for t in ['tm_order', 'supplier_order', 'order_airline', 'order_route', 'supply_quality']:
        op.create_index(f'ix_{t}_user_id', t, ['user_id'])

    op.drop_constraint('uq_supply_quality_key', 'supply_quality', type_='unique')
    op.create_unique_constraint(
        'uq_supply_quality_key', 'supply_quality',
        ['user_id', 'biz_date', 'range_type', 'period_start', 'period_end', 'airline_code', 'agent_code', 'supplier_code'],
    )


def downgrade() -> None:
    raise RuntimeError('不支持回滚：按用户隔离后同一订单号可能存在多份，无法还原全局唯一主键')
