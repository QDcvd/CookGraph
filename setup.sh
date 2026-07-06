#!/bin/bash
# MiniCookingAgent-Demo — 一键安装脚本（conda 独立环境 + 并行安装）
# 使用方法: bash setup.sh
#
# 注意事项：
#   - 需要 Python >= 3.11（asyncio.timeout() 要求）
#   - Windows 建议用 Git Bash 或 Anaconda Prompt 运行
#   - 如果前端端口 3000 被系统保留（WSL/Hyper-V 常见），
#     脚本会自动检查 vite.config.ts 中的端口是否可用
#   - 国内用户可取消注释下方的镜像加速

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
ENV_NAME="minicook"
PYTHON_VERSION="3.11"

# ─── 镜像加速（国内用户取消注释） ───
# npm config set registry https://registry.npmmirror.com
# pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

echo "========================================"
echo "  迷你烹饪问答机器人环境安装"
echo "========================================"
echo "  Python : $PYTHON_VERSION（asyncio.timeout 需要 ≥3.11）"
echo "  环境名 : $ENV_NAME"
echo ""

# ────────── 前置检查 ──────────

# 1) conda 是否可用
if ! command -v conda &> /dev/null; then
    echo "❌ 找不到 conda 命令"
    echo "   请确认 Anaconda/Miniconda 已安装且 PATH 配置正确"
    echo "   Windows Git Bash 用户：改用 Anaconda Prompt 运行此脚本"
    exit 1
fi

# 2) Windows 端口排除检查（仅提示，不阻断）
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    VITE_CONFIG="$FRONTEND_DIR/vite.config.ts"
    CURRENT_PORT=$(grep -oP 'port: \K\d+' "$VITE_CONFIG" 2>/dev/null || echo "5173")
    echo "⚠️  Windows 检测："
    echo "   前端端口设为 $CURRENT_PORT"
    echo "   如遇 EACCES 错误，可能是 Hyper-V/WSL 保留了该端口"
    echo "   请运行: netsh int ipv4 show excludedportrange protocol=tcp"
    echo "   然后在 vite.config.ts 中换一个未被排除的端口"
    echo ""
fi

# 3) conda 环境是否已存在
ENV_EXISTS=false
if conda env list | grep -q "^$ENV_NAME "; then
    ENV_EXISTS=true
    echo "⚠️  conda 环境 '$ENV_NAME' 已存在"
    echo "   如需重建: conda remove -n $ENV_NAME --all -y && bash setup.sh"
    echo "   继续使用已有环境..."
else
    echo "  → 环境 '$ENV_NAME' 不存在，将自动创建"
fi
echo ""

# ────────── Phase 1: 并行安装 ──────────

echo "[1/2] 并行安装开始..."
echo "  ├─ ⚡ 后台: conda 创建环境 $ENV_NAME (Python $PYTHON_VERSION)"
echo "  └─ ⚡ 后台: frontend npm install"
echo ""

# 后台任务 A：conda 创建环境（仅当不存在时）
(
    if $ENV_EXISTS; then
        echo "[conda] ⏭️  环境已存在，跳过创建"
    else
        echo "[conda] 开始创建环境 $ENV_NAME ..."
        conda create -n "$ENV_NAME" python="$PYTHON_VERSION" -y
        echo "[conda] ✅ 环境 $ENV_NAME 创建完成"
    fi
) &
CONDA_PID=$!

# 后台任务 B：npm install
(
    if [ -d "$FRONTEND_DIR" ]; then
        echo "[npm] 开始安装前端依赖..."
        cd "$FRONTEND_DIR"
        npm install
        echo "[npm] ✅ 前端依赖安装完成"
    else
        echo "[npm] ⚠️ 前端目录不存在: $FRONTEND_DIR"
    fi
) &
NPM_PID=$!

# 等待两个后台任务
wait $CONDA_PID
echo "[conda] ✅ 环境已就绪"

wait $NPM_PID
echo "[npm] ✅ 前端依赖已就绪"

# ────────── Phase 2: pip 安装 Python 包 ──────────

echo ""
echo "[2/2] 在 conda 环境 $ENV_NAME 中安装 Python 依赖..."

# 激活 conda 环境
eval "$(conda shell.bash hook)"
conda activate "$ENV_NAME"

echo "  → 安装后端 Python 包（fastapi / langchain / langgraph ...）"
echo ""

pip install \
    "fastapi[standard]" \
    uvicorn \
    langchain \
    langchain-openai \
    langgraph \
    python-dotenv \
    ddgs \
    paramiko \
    networkx

# pip 安装成功 → 显示完成信息
echo ""
echo "========================================"
echo "  ✅ 安装全部完成！"
echo "========================================"
echo ""
echo "启动方式（任选其一）："
echo ""
echo "  1) 一键启动（后端 + 前端）"
echo "     conda activate $ENV_NAME"
echo "     python start.py"
echo "     → 后端: http://localhost:8000"
echo "     → 前端: http://localhost:5173"
echo ""
echo "  2) 分终端启动（开发模式）"
echo "     终端 A:"
echo "       conda activate $ENV_NAME"
echo "       uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload"
echo ""
echo "     终端 B:"
echo "       cd frontend && npm run dev"
echo ""
echo "  3) 使用本地模型"
echo "     conda activate $ENV_NAME"
echo "     python start.py --adapter agent_adapter_local_LLM_harness"
echo ""
echo "  配置文件: 编辑 .env 填入 API Key 或本地模型地址"
echo "  环境删除: conda remove -n $ENV_NAME --all -y"
echo ""
