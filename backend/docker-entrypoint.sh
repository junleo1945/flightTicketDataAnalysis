#!/bin/sh
set -e

# 数据库迁移可通过 RUN_MIGRATIONS_ON_START 关闭，迁移到 K8s 等平台时可改为 init job 执行
if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  alembic upgrade head
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
