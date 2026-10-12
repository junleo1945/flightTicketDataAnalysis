from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert


def upsert(conn: Connection, table, rows: list[dict], index_elements: list[str]) -> None:
    """按唯一键批量写入，冲突时用新值覆盖。executemany 方式，不受参数个数限制。"""
    if not rows:
        return
    stmt = pg_insert(table)
    update_cols = [c for c in rows[0] if c not in index_elements]
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_={c: stmt.excluded[c] for c in update_cols})
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)
    conn.execute(stmt, rows)
