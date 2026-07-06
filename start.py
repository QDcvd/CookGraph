"""迷你烹饪问答机器人项目：一键启动前后端"""
import argparse
import subprocess
import sys
import os
import select
import socketserver
import threading
import time
import urllib.request
from urllib.parse import urlparse

# 项目根目录
ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(ROOT, "frontend")
PORT = int(os.environ.get("PORT", "8000"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "5173"))
FRONTEND_PORT_SCAN_LIMIT = int(os.environ.get("FRONTEND_PORT_SCAN_LIMIT", "10"))

# 解析命令行参数
_parser = argparse.ArgumentParser(description="启动迷你烹饪问答机器人项目")
_parser.add_argument(
    "--adapter",
    default="agent_adapter_local_LLM_harness",
    choices=["agent_adapter", "agent_adapter_local_LLM", "agent_adapter_local_LLM_harness"],
    help="选择 Agent 适配器：默认 agent_adapter_local_LLM_harness（本地模型工具循环）",
)
_parser.add_argument(
    "--debug-llm",
    action="store_true",
    help="开启大模型调试输出：后端会打印模型每次返回的完整对象",
)
_parser.add_argument(
    "--no-llm-tunnel",
    action="store_true",
    help="禁用 .env 中配置的远端 LM Studio SSH 本地转发",
)
_args = _parser.parse_args()


def load_project_env() -> dict:
    """读取 .env 并合并到进程环境；命令行/系统环境变量优先。"""
    env = os.environ.copy()
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        return env

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    return env


def env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(env: dict, key: str, default: int) -> int:
    try:
        return int(str(env.get(key, default)).strip())
    except ValueError:
        return default


def openai_base_available(base_url: str, timeout: float = 2.0) -> bool:
    if not base_url:
        return False
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def kill_port(port: int):
    """杀掉占用指定端口的进程（跨平台）"""
    if sys.platform == "win32":
        # Windows: netstat 找 PID → taskkill
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True
        )
        pids = set()
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    pids.add(parts[-1])

        for pid in sorted(pids):
            kill_windows_process_tree(pid, f"端口 {port}")
    else:
        # Linux / WSL: lsof 找 PID → kill
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True
            )
            if result.stdout.strip():
                for pid in result.stdout.strip().split():
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                    print(f"[kill] 已杀掉端口 {port} 上的旧进程 (PID={pid})")
        except FileNotFoundError:
            pass  # lsof 没装就算了


def find_windows_child_pids(pid: str) -> list[str]:
    """查找 Windows 子进程；用于清理 uvicorn --reload 遗留的 worker。"""
    script = f"""
$all = New-Object System.Collections.Generic.List[int]
function Add-Children([int]$ParentId) {{
  Get-CimInstance Win32_Process | Where-Object {{ $_.ParentProcessId -eq $ParentId }} | ForEach-Object {{
    $all.Add([int]$_.ProcessId)
    Add-Children ([int]$_.ProcessId)
  }}
}}
Add-Children ([int]{pid})
$all | Sort-Object -Unique
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    child_pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            child_pids.append(line)
    return child_pids


def kill_windows_process_tree(pid: str, reason: str):
    """杀掉 PID 及其子进程；即使父进程已退出，也会清理遗留子进程。"""
    if not str(pid).isdigit() or str(pid) == "0":
        return

    targets = find_windows_child_pids(str(pid))
    targets.append(str(pid))
    killed = set()
    for target in targets:
        if target in killed:
            continue
        killed.add(target)
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", target],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"[kill] 已杀掉{reason}上的旧进程 (PID={target})")


def kill_frontend_ports():
    """清理 Vite 前端常用端口，避免旧前端导致端口递增。"""
    start = FRONTEND_PORT
    end = FRONTEND_PORT + FRONTEND_PORT_SCAN_LIMIT
    print(f"[kill] 正在清理旧前端端口 {start}-{end - 1} ...")
    for port in range(start, end):
        kill_port(port)


def ensure_frontend_deps(npm: str):
    node_modules = os.path.join(FRONTEND_DIR, "node_modules")
    if os.path.isdir(node_modules):
        return
    print("未检测到 frontend/node_modules，正在安装前端依赖...")
    subprocess.check_call([npm, "install"], cwd=FRONTEND_DIR)


def ensure_backend_runtime():
    """启动前检查当前 Python 是否装了后端依赖，避免前端已启动但后端秒退。"""
    required_modules = [
        ("uvicorn", "uvicorn"),
        ("fastapi", "fastapi"),
        ("langchain", "langchain"),
        ("langchain_openai", "langchain-openai"),
        ("langgraph", "langgraph"),
        ("dotenv", "python-dotenv"),
        ("ddgs", "ddgs"),
        ("paramiko", "paramiko"),
    ]
    missing = []
    for module_name, package_name in required_modules:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)

    if not missing:
        return

    print("[error] 当前 Python 环境缺少后端依赖，后端不会启动。")
    print(f"[error] 当前解释器：{sys.executable}")
    print("[error] 缺少包：" + ", ".join(missing))
    print("")
    print("推荐方式：")
    print("  conda activate bigdog")
    print("  python start.py")
    print("")
    print("或新建本项目环境：")
    print("  bash setup.sh")
    print("  conda activate minicook")
    print("  python start.py")
    raise SystemExit(1)


def sanitize_backend_env(env: dict):
    """清理 Git Bash/Conda 里可能指向不存在文件的证书变量。"""
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = env.get(key)
        if value and not os.path.exists(value):
            env.pop(key, None)
            print(f"[env] 已移除无效证书变量 {key}={value}")


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_forward_handler(transport, remote_host: str, remote_port: int):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                channel = transport.open_channel(
                    "direct-tcpip",
                    (remote_host, remote_port),
                    self.request.getpeername(),
                )
            except Exception as e:
                print(f"[tunnel] 打开远端通道失败：{e}", flush=True)
                return

            if channel is None:
                print("[tunnel] 打开远端通道失败：SSH channel 为空", flush=True)
                return

            try:
                while True:
                    readable, _, _ = select.select([self.request, channel], [], [], 1.0)
                    if self.request in readable:
                        data = self.request.recv(65536)
                        if not data:
                            break
                        channel.sendall(data)
                    if channel in readable:
                        data = channel.recv(65536)
                        if not data:
                            break
                        self.request.sendall(data)
            finally:
                channel.close()

    return Handler


def maybe_start_llm_tunnel(env: dict):
    """远端 LM Studio 只监听 127.0.0.1 时，建立本地 127.0.0.1 → SSH → 远端 127.0.0.1 转发。"""
    if _args.no_llm_tunnel or not env_truthy(env.get("LLM_SSH_TUNNEL")):
        return None

    base_url = env.get("LLM_BASE_URL", "")
    parsed = urlparse(base_url)
    local_port = env_int(env, "LLM_LOCAL_PORT", parsed.port or 1234)
    local_host = env.get("LLM_LOCAL_HOST", "127.0.0.1")
    local_base_url = f"http://{local_host}:{local_port}/v1"
    env["LLM_BASE_URL"] = local_base_url

    if openai_base_available(local_base_url):
        print(f"[tunnel] 本地 LLM API 已可用：{local_base_url}")
        return None

    remote_host = env.get("LLM_REMOTE_HOST", "").strip()
    remote_user = env.get("LLM_REMOTE_USER", "").strip()
    remote_password = env.get("LLM_REMOTE_PASSWORD", "")
    remote_bind_host = env.get("LLM_REMOTE_BIND_HOST", "127.0.0.1")
    remote_port = env_int(env, "LLM_REMOTE_PORT", 1234)
    if not remote_host or not remote_user:
        print("[tunnel] 未配置 LLM_REMOTE_HOST/LLM_REMOTE_USER，跳过 SSH 隧道")
        return None

    try:
        import paramiko
    except ImportError:
        print("[tunnel] 缺少 paramiko，无法自动建立 SSH 隧道；请运行 pip install paramiko")
        return None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"[tunnel] 正在建立本地 {local_host}:{local_port} → {remote_host}:{remote_bind_host}:{remote_port}")
    client.connect(
        remote_host,
        username=remote_user,
        password=remote_password or None,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    server = _ForwardServer(
        (local_host, local_port),
        _make_forward_handler(client.get_transport(), remote_bind_host, remote_port),
    )
    thread = threading.Thread(target=server.serve_forever, name="llm-ssh-tunnel", daemon=True)
    thread.start()
    if openai_base_available(local_base_url, timeout=5.0):
        print(f"[tunnel] 已连接远端 LM Studio：{local_base_url}")
    else:
        print(f"[tunnel] 隧道已启动，但 {local_base_url}/models 暂未响应，请确认 LM Studio 已加载模型")
    return server, client


def stop_llm_tunnel(tunnel):
    if not tunnel:
        return
    server, client = tunnel
    print("[tunnel] 正在关闭 LLM SSH 隧道...")
    server.shutdown()
    server.server_close()
    client.close()


def main():
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    ensure_backend_runtime()
    ensure_frontend_deps(npm)

    # 先杀掉旧进程，再启动后端（传入 AGENT_ADAPTER 环境变量）
    kill_port(PORT)
    kill_frontend_ports()
    env = load_project_env()
    env["AGENT_ADAPTER"] = _args.adapter
    env["PYTHONUNBUFFERED"] = "1"
    sanitize_backend_env(env)
    tunnel = maybe_start_llm_tunnel(env)
    print(f"[adapter] 将使用适配器：{_args.adapter}")
    if _args.debug_llm:
        env["MINICOOK_LLM_DEBUG"] = "1"
        print("[debug] 已开启大模型返回值调试输出")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app",
         "--host", "0.0.0.0", "--port", str(PORT), "--reload"],
        cwd=ROOT,
        env=env,
    )
    time.sleep(1.0)
    if backend.poll() is not None:
        print(f"[error] 后端启动失败，退出码={backend.returncode}")
        print("[error] 请查看上方 Python/uvicorn 报错；通常是依赖缺失或端口/配置错误。")
        stop_llm_tunnel(tunnel)
        raise SystemExit(backend.returncode or 1)
    print(f"[OK] 后端启动 (PID={backend.pid}) → http://localhost:8000")

    # 启动前端
    frontend = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=FRONTEND_DIR,
    )
    print(f"[OK] 前端启动 (PID={frontend.pid}) → http://localhost:{FRONTEND_PORT}")

    print("\n按 Ctrl+C 停止所有服务")
    try:
        backend.wait()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
        backend.terminate()
        frontend.terminate()
        backend.wait()
        frontend.wait()
        print("已停止")
    finally:
        stop_llm_tunnel(tunnel)

if __name__ == "__main__":
    main()
