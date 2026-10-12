#!/usr/bin/env bash
# 部署脚本：拉取 GHCR 上的镜像并启动服务，不在服务器上构建。
#
# 用法：
#   ./deploy.sh             部署 latest 标签
#   ./deploy.sh <sha>       部署指定 commit（用于回滚），标签即 commit 的完整或短 SHA
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "缺少 .env，请先执行：cp .env.example .env 并填写配置" >&2
  exit 1
fi

# 读取 .env（只支持 KEY=VALUE 形式，不要在值里放空格）
set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ -n "${1:-}" ]; then
  export IMAGE_TAG="$1"
fi
export IMAGE_TAG="${IMAGE_TAG:-latest}"
export IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io/junleo1945}"

echo "==> 部署镜像标签：${IMAGE_TAG}（仓库：${IMAGE_REGISTRY}）"

if [ -n "${GHCR_USER:-}" ] && [ -n "${GHCR_TOKEN:-}" ]; then
  echo "==> 登录 GHCR"
  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin
fi

echo "==> 拉取镜像"
docker compose pull backend frontend

echo "==> 启动服务（使用已拉取的镜像，不再构建）"
docker compose up -d --no-build db backend frontend

echo "==> 等待服务就绪"
url="http://localhost:${WEB_PORT:-80}/api/ready"
for _ in $(seq 1 45); do
  if curl -fsS "$url" >/dev/null 2>&1; then
    echo "✅ 部署完成：http://localhost:${WEB_PORT:-80}/"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "❌ 服务未在 90 秒内就绪，请查看日志：docker compose logs --tail=100 backend" >&2
docker compose ps
exit 1
