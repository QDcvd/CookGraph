#!/usr/bin/env bash
set -Eeuo pipefail

# MiniCookingAgent-Demo 一键部署脚本（uv 版）
#
# 用法：
#   bash deploy_uv.sh
#   bash deploy_uv.sh --with-model
#   bash deploy_uv.sh --with-model --start
#
# 可覆盖的镜像变量：
#   UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
#   NPM_REGISTRY=https://registry.npmmirror.com
#   HF_ENDPOINT=https://hf-mirror.com

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
MODEL_REPO="${MODEL_REPO:-thenlper/gte-large-zh}"
MODEL_DIR="${MODEL_DIR:-$ROOT_DIR/models/gte-large-zh}"
UV_BIN="${UV_BIN:-}"

WITH_MODEL=0
START_AFTER=0
SKIP_FRONTEND=0
SKIP_BACKEND=0

log() {
  printf '\033[1;32m[deploy]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
MiniCookingAgent-Demo 一键部署脚本（uv 版）

用法：
  bash deploy_uv.sh [选项]

选项：
  --with-model       同时下载 gte-large-zh embedding 模型到 models/gte-large-zh
  --start            部署完成后执行 python start.py 启动前后端
  --skip-frontend    跳过前端 npm ci
  --skip-backend     跳过后端 uv pip install
  -h, --help         显示帮助

常用镜像变量：
  UV_INDEX_URL       Python 包镜像，默认 https://mirrors.aliyun.com/pypi/simple
  NPM_REGISTRY       npm 镜像，默认 https://registry.npmmirror.com
  HF_ENDPOINT        HuggingFace 镜像，默认 https://hf-mirror.com
  PYTHON_VERSION     uv 创建虚拟环境用的 Python 版本，默认 3.10

示例：
  bash deploy_uv.sh --with-model
  UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash deploy_uv.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-model)
      WITH_MODEL=1
      shift
      ;;
    --start)
      START_AFTER=1
      shift
      ;;
    --skip-frontend)
      SKIP_FRONTEND=1
      shift
      ;;
    --skip-backend)
      SKIP_BACKEND=1
      shift
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

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"
}

install_uv_if_missing() {
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return
  fi

  log "未检测到 uv，正在安装 uv..."
  if command -v pipx >/dev/null 2>&1; then
    pipx install uv
  elif command -v python >/dev/null 2>&1; then
    python -m pip install -U uv -i "$UV_INDEX_URL" --trusted-host "$(echo "$UV_INDEX_URL" | sed -E 's#https?://([^/]+)/?.*#\1#')"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m pip install -U uv -i "$UV_INDEX_URL" --trusted-host "$(echo "$UV_INDEX_URL" | sed -E 's#https?://([^/]+)/?.*#\1#')"
  else
    die "没有 python/pipx，无法自动安装 uv。请先安装 Python 3.10+。"
  fi

  resolve_uv_bin
}

resolve_uv_bin() {
  if [[ -n "$UV_BIN" && -x "$UV_BIN" ]]; then
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return
  fi
  local candidate
  for candidate in \
    "$HOME/.local/bin/uv" \
    "$HOME/AppData/Roaming/Python/Python310/Scripts/uv.exe" \
    "$HOME/AppData/Roaming/Python/Python311/Scripts/uv.exe" \
    "$HOME/AppData/Roaming/Python/Python312/Scripts/uv.exe" \
    "$HOME/AppData/Roaming/uv/uv.exe"; do
    if [[ -x "$candidate" ]]; then
      UV_BIN="$candidate"
      return
    fi
  done
  die "uv 已尝试安装，但当前 shell 找不到 uv。请重开 Git Bash，或设置 UV_BIN=/path/to/uv 后重试。"
}

venv_python() {
  if [[ -x "$VENV_DIR/Scripts/python.exe" ]]; then
    printf '%s\n' "$VENV_DIR/Scripts/python.exe"
  elif [[ -x "$VENV_DIR/bin/python" ]]; then
    printf '%s\n' "$VENV_DIR/bin/python"
  else
    printf '%s\n' "$VENV_DIR/Scripts/python.exe"
  fi
}

venv_python_for_shell() {
  local py
  py="$(venv_python)"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -u "$py"
  else
    printf '%s\n' "$py"
  fi
}

path_for_python_literal() {
  local value="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$value"
  else
    printf '%s\n' "$value"
  fi
}

setup_env_file() {
  if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    log "已从 .env.example 创建 .env，请按需修改 LLM 地址和 SSH 配置。"
  fi
}

setup_backend() {
  if [[ "$SKIP_BACKEND" == "1" ]]; then
    log "跳过后端依赖安装。"
    return
  fi

  install_uv_if_missing
  resolve_uv_bin

  log "创建/复用 uv 虚拟环境：$VENV_DIR"
  "$UV_BIN" venv "$VENV_DIR" --python "$PYTHON_VERSION"

  local install_args=(
    pip install
    --python "$(venv_python)"
    --index-url "$UV_INDEX_URL"
    --upgrade
    -r "$ROOT_DIR/requirements.txt"
  )
  if [[ -n "$UV_EXTRA_INDEX_URL" ]]; then
    install_args+=(--extra-index-url "$UV_EXTRA_INDEX_URL")
  fi

  log "安装后端依赖（uv，并发解析/下载，镜像：$UV_INDEX_URL）"
  "$UV_BIN" "${install_args[@]}"
}

setup_frontend() {
  if [[ "$SKIP_FRONTEND" == "1" ]]; then
    log "跳过前端依赖安装。"
    return
  fi

  need_cmd node
  need_cmd npm

  log "配置 npm 镜像：$NPM_REGISTRY"
  npm config set registry "$NPM_REGISTRY" >/dev/null

  log "安装前端依赖（npm ci）"
  (
    cd "$FRONTEND_DIR"
    npm ci --registry "$NPM_REGISTRY" --prefer-offline --no-audit
  )
}

download_model() {
  if [[ "$WITH_MODEL" != "1" ]]; then
    return
  fi

  local py
  local model_dir_for_python
  py="$(venv_python_for_shell)"
  model_dir_for_python="$(path_for_python_literal "$MODEL_DIR")"
  mkdir -p "$(dirname "$MODEL_DIR")"

  log "下载 embedding 模型：$MODEL_REPO -> $MODEL_DIR"
  HF_ENDPOINT="$HF_ENDPOINT" "$py" - <<PY
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="$MODEL_REPO",
    local_dir=r"$model_dir_for_python",
    local_dir_use_symlinks=False,
    resume_download=True,
)
print("model ready:", r"$model_dir_for_python")
PY
}

verify_install() {
  local py
  py="$(venv_python_for_shell)"

  log "验证后端关键依赖..."
  "$py" - <<'PY'
import importlib.util

mods = [
    "fastapi",
    "uvicorn",
    "langchain_openai",
    "langgraph",
    "ddgs",
    "paramiko",
    "networkx",
    "openpyxl",
    "sentence_transformers",
    "torch",
    "sklearn",
]

missing = [name for name in mods if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("missing modules: " + ", ".join(missing))
print("backend deps ok")
PY

  if [[ "$WITH_MODEL" == "1" ]]; then
    [[ -f "$MODEL_DIR/config.json" ]] || die "模型目录缺少 config.json：$MODEL_DIR"
    [[ -f "$MODEL_DIR/modules.json" ]] || die "模型目录缺少 modules.json：$MODEL_DIR"
  elif [[ ! -f "$MODEL_DIR/config.json" ]]; then
    warn "未检测到 gte-large-zh 模型。需要向量召回时运行：bash deploy_uv.sh --with-model"
  fi
}

run_parallel_installs() {
  local backend_log="$ROOT_DIR/.deploy-backend.log"
  local frontend_log="$ROOT_DIR/.deploy-frontend.log"
  rm -f "$backend_log" "$frontend_log"

  log "开始并行安装后端和前端依赖..."

  (
    set -Eeuo pipefail
    setup_backend
  ) >"$backend_log" 2>&1 &
  local backend_pid=$!

  (
    set -Eeuo pipefail
    setup_frontend
  ) >"$frontend_log" 2>&1 &
  local frontend_pid=$!

  local failed=0
  if ! wait "$backend_pid"; then
    failed=1
    warn "后端依赖安装失败，日志如下："
    cat "$backend_log" >&2 || true
  fi

  if ! wait "$frontend_pid"; then
    failed=1
    warn "前端依赖安装失败，日志如下："
    cat "$frontend_log" >&2 || true
  fi

  if [[ "$failed" != "0" ]]; then
    die "依赖安装失败。完整日志：$backend_log / $frontend_log"
  fi

  log "依赖安装完成。日志：$backend_log / $frontend_log"
}

start_app() {
  local py
  py="$(venv_python_for_shell)"
  log "启动项目：$py start.py --adapter agent_adapter_local_LLM_harness"
  exec "$py" "$ROOT_DIR/start.py" --adapter agent_adapter_local_LLM_harness
}

main() {
  cd "$ROOT_DIR"
  setup_env_file
  run_parallel_installs
  download_model
  verify_install

  log "部署完成。"
  log "激活环境：source .venv/Scripts/activate  # Git Bash on Windows"
  log "启动项目：.venv/Scripts/python.exe start.py --adapter agent_adapter_local_LLM_harness"

  if [[ "$START_AFTER" == "1" ]]; then
    start_app
  fi
}

main "$@"
