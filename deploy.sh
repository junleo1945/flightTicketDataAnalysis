#!/usr/bin/env bash
# 部署脚本：只使用 docker 命令，不依赖 docker compose 插件。
#
# 用法：
#   ./deploy.sh              拉取并启动 latest 版本
#   ./deploy.sh <tag>        启动指定版本（如 commit SHA，用于回滚）
#   ./deploy.sh stop         停止并删除三个容器（数据卷保留）
#   SKIP_PULL=1 ./deploy.sh  不拉取镜像，直接使用本机已有的镜像（离线导入场景）
set -euo pipefail
cd "$(dirname "$0")"

NET=fa-net
DB=fa-db
BE=fa-backend
FE=fa-frontend
PG_VOL=fa-pgdata
UP_VOL=fa-uploads

if [ "${1:-}" = "stop" ]; then
  docker rm -f "$FE" "$BE" "$DB" >/dev/null 2>&1 || true
  echo "✅ 已停止服务（数据卷 $PG_VOL、$UP_VOL 保留）"
  exit 0
fi

if [ ! -f .env ]; then
  echo "缺少 .env，请先执行：cp .env.example .env 并填写配置" >&2
  exit 1
fi

# 读取 .env；其中的代理变量为空时会覆盖本机 shell 里的代理设置
set -a
# shellcheck disable=SC1091
. ./.env
set +a

TAG="${1:-${IMAGE_TAG:-latest}}"
REGISTRY="${IMAGE_REGISTRY:-ghcr.io/junleo1945}"
WEB_PORT="${WEB_PORT:-9092}"
DB_PORT="${DB_PORT:-9095}"
POSTGRES_DB="${POSTGRES_DB:-flight_analysis}"
POSTGRES_USER="${POSTGRES_USER:-flight}"
: "${POSTGRES_PASSWORD:?请在 .env 中设置 POSTGRES_PASSWORD}"
BACKEND_IMAGE="$REGISTRY/flightticketdataanalysis-backend:$TAG"
FRONTEND_IMAGE="$REGISTRY/flightticketdataanalysis-frontend:$TAG"

echo "==> 版本：$TAG（仓库：$REGISTRY）"

if [ "${SKIP_PULL:-0}" != "1" ]; then
  if [ -n "${GHCR_USER:-}" ] && [ -n "${GHCR_TOKEN:-}" ]; then
    echo "==> 登录 GHCR"
    echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin
  fi
  echo "==> 拉取镜像"
  docker pull postgres:16
  docker pull "$BACKEND_IMAGE"
  docker pull "$FRONTEND_IMAGE"
else
  echo "==> SKIP_PULL=1：使用本机已有镜像"
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

# 先删除旧容器（数据卷不动），再重新创建，保证配置与 .env 一致
docker rm -f "$FE" "$BE" "$DB" >/dev/null 2>&1 || true

echo "==> 启动数据库"
docker run -d --name "$DB" --network "$NET" --network-alias db --restart unless-stopped \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -v "$PG_VOL:/var/lib/postgresql/data" \
  -p "$DB_PORT:5432" \
  postgres:16 >/dev/null

echo "==> 等待数据库就绪"
for _ in $(seq 1 30); do
  if docker exec "$DB" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then break; fi
  sleep 2
done
docker exec "$DB" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null

echo "==> 启动后端（启动时自动执行数据库迁移）"
docker run -d --name "$BE" --network "$NET" --network-alias backend --restart unless-stopped \
  --env-file .env \
  -e DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}" \
  -e UPLOAD_DIR=/data/uploads \
  -e HTTP_PROXY="${HTTP_PROXY:-}" \
  -e HTTPS_PROXY="${HTTPS_PROXY:-}" \
  -e NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,db,backend}" \
  -v "$UP_VOL:/data/uploads" \
  "$BACKEND_IMAGE" >/dev/null

echo "==> 启动前端"
docker run -d --name "$FE" --network "$NET" --restart unless-stopped \
  -p "$WEB_PORT:80" \
  "$FRONTEND_IMAGE" >/dev/null

echo "==> 等待服务就绪"
url="http://localhost:${WEB_PORT}/api/ready"
for _ in $(seq 1 45); do
  if curl -fsS "$url" >/dev/null 2>&1; then
    echo "✅ 部署完成"
    echo "   页面：http://localhost:${WEB_PORT}/"
    echo "   数据库：localhost:${DB_PORT}（用户 ${POSTGRES_USER}，库 ${POSTGRES_DB}）"
    docker ps --filter "network=$NET" --format '   {{.Names}}\t{{.Status}}\t{{.Ports}}'
    exit 0
  fi
  sleep 2
done

echo "❌ 服务未在 90 秒内就绪，请查看日志：docker logs --tail=100 $BE" >&2
exit 1
