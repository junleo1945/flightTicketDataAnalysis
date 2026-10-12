#!/usr/bin/env bash
# 本地开发一键启动：容器内运行后端（热重载）、数据库、假 ERP；前端用本机 Vite（热更新）。
#
#   scripts/dev.sh          启动（首次会构建后端镜像，之后直接复用）
#   scripts/dev.sh build    修改了 backend/pyproject.toml 后重新构建后端镜像
#   scripts/dev.sh down     停止所有开发容器（数据库数据保留）
#
# 前端在前台运行，Ctrl+C 只停止前端，容器仍在后台运行；需要停止时执行 scripts/dev.sh down
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

case "${1:-up}" in
  down)
    "${COMPOSE[@]}" down
    exit 0
    ;;
  build)
    "${COMPOSE[@]}" build backend
    exit 0
    ;;
  up) ;;
  *)
    echo "用法：scripts/dev.sh [build|down]" >&2
    exit 1
    ;;
esac

if [ ! -f .env ]; then
  echo "缺少 .env，请先执行：cp .env.example .env 并填写配置" >&2
  exit 1
fi

if [ ! -d frontend/node_modules ]; then
  echo "安装前端依赖..."
  (cd frontend && npm install)
fi

echo "启动容器（数据库 / 后端 / 假 ERP）..."
"${COMPOSE[@]}" up -d

echo "等待后端就绪..."
ready=false
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  echo "后端未在 60 秒内就绪，请查看日志：" >&2
  echo "  docker compose -f docker-compose.yml -f docker-compose.dev.yml logs backend" >&2
  exit 1
fi

echo ""
echo "后端：http://127.0.0.1:8000    ERP 地址取自 .env 的 ERP_BASE_URL"
echo "前端：http://127.0.0.1:5173    （Ctrl+C 只停前端，停止容器请执行 scripts/dev.sh down）"
echo ""

cd frontend
exec npm run dev -- --host 127.0.0.1
