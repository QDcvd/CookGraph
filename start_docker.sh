#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-minicooking-agent-env}"
CONTAINER_NAME="${CONTAINER_NAME:-minicooking-agent}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
APT_MIRROR="${APT_MIRROR:-mirrors.aliyun.com}"
UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
MODEL_SOURCE="${MODEL_SOURCE:-modelscope}"
MODELSCOPE_MODEL_ID="${MODELSCOPE_MODEL_ID:-AI-ModelScope/gte-large-zh}"
MODEL_REPO="${MODEL_REPO:-thenlper/gte-large-zh}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
REBUILD="${REBUILD:-0}"

log() {
  printf '\033[1;32m[docker]\033[0m %s\n' "$*"
}

die() {
  printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
MiniCookingAgent-Demo Docker 一键启动

用法：
  bash start_docker.sh [选项]

选项：
  --rebuild          强制重新构建镜像
  --image NAME      镜像名，默认 minicooking-agent-env
  --backend-port N  宿主机后端端口，默认 8000
  --frontend-port N 宿主机前端端口，默认 5173
  -h, --help        显示帮助

常用环境变量：
  MODEL_SOURCE=modelscope|huggingface
  MODELSCOPE_MODEL_ID=AI-ModelScope/gte-large-zh
  MODEL_REPO=thenlper/gte-large-zh
  APT_MIRROR=mirrors.aliyun.com
  UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
  NPM_REGISTRY=https://registry.npmmirror.com
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild)
      REBUILD=1
      shift
      ;;
    --image)
      IMAGE_NAME="${2:-}"
      [[ -n "$IMAGE_NAME" ]] || die "--image 需要镜像名"
      shift 2
      ;;
    --backend-port)
      BACKEND_PORT="${2:-}"
      [[ -n "$BACKEND_PORT" ]] || die "--backend-port 需要端口"
      shift 2
      ;;
    --frontend-port)
      FRONTEND_PORT="${2:-}"
      [[ -n "$FRONTEND_PORT" ]] || die "--frontend-port 需要端口"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1"
      ;;
  esac
done

command -v docker >/dev/null 2>&1 || die "缺少 docker 命令，请先安装 Docker Desktop 或 Docker Engine。"

image_exists() {
  docker image inspect "$IMAGE_NAME" >/dev/null 2>&1
}

if [[ "$REBUILD" == "1" ]] || ! image_exists; then
  log "构建 Docker 镜像：$IMAGE_NAME"
  log "模型来源：$MODEL_SOURCE"
  docker build \
    --build-arg "APT_MIRROR=$APT_MIRROR" \
    --build-arg "UV_INDEX_URL=$UV_INDEX_URL" \
    --build-arg "NPM_REGISTRY=$NPM_REGISTRY" \
    --build-arg "MODEL_SOURCE=$MODEL_SOURCE" \
    --build-arg "MODELSCOPE_MODEL_ID=$MODELSCOPE_MODEL_ID" \
    --build-arg "MODEL_REPO=$MODEL_REPO" \
    --build-arg "HF_ENDPOINT=$HF_ENDPOINT" \
    -t "$IMAGE_NAME" \
    "$ROOT_DIR"
else
  log "复用已有镜像：$IMAGE_NAME（如需重建：bash start_docker.sh --rebuild）"
fi

log "启动容器，挂载项目：$ROOT_DIR -> /workspace"
log "访问地址：后端 http://localhost:$BACKEND_PORT，前端 http://localhost:$FRONTEND_PORT"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

exec docker run --rm -it \
  --name "$CONTAINER_NAME" \
  -p "$BACKEND_PORT:8000" \
  -p "$FRONTEND_PORT:5173" \
  -v "$ROOT_DIR:/workspace" \
  -w /workspace \
  "$IMAGE_NAME" \
  python start.py --adapter agent_adapter_local_LLM_harness
