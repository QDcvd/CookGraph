"""Shared OpenAI-compatible LLM endpoint readiness helpers."""

from __future__ import annotations

import atexit
import os
import select
import socketserver
import threading
import urllib.request
import warnings
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_TUNNEL = None
_LOCK = threading.Lock()


def env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    try:
        return int(str(os.getenv(key, default)).strip())
    except ValueError:
        return default


def openai_base_available(base_url: str, timeout: float = 2.0) -> bool:
    if not base_url:
        return False
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


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
            except Exception:
                return
            if channel is None:
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
                try:
                    channel.close()
                except Exception:
                    pass

    return Handler


def ensure_llm_endpoint(base_url: str | None = None, *, force_retry: bool = False) -> str:
    """Return a usable base URL when possible, starting the configured SSH tunnel once."""
    configured = str(base_url or os.getenv("LLM_BASE_URL") or "http://127.0.0.1:51234/v1").rstrip("/")
    if not force_retry and openai_base_available(configured):
        return configured

    if not env_truthy(os.getenv("LLM_SSH_TUNNEL")):
        return configured

    parsed = urlparse(configured)
    local_port = env_int("LLM_LOCAL_PORT", parsed.port or 51234)
    local_host = os.getenv("LLM_LOCAL_HOST", "127.0.0.1")
    local_base_url = f"http://{local_host}:{local_port}/v1"
    os.environ["LLM_BASE_URL"] = local_base_url

    if not force_retry and openai_base_available(local_base_url):
        return local_base_url

    global _TUNNEL
    with _LOCK:
        if _TUNNEL is not None:
            return local_base_url
        _TUNNEL = _start_tunnel(local_host, local_port)
    return local_base_url


def _start_tunnel(local_host: str, local_port: int):
    remote_host = str(os.getenv("LLM_REMOTE_HOST") or "").strip()
    remote_user = str(os.getenv("LLM_REMOTE_USER") or "").strip()
    remote_password = str(os.getenv("LLM_REMOTE_PASSWORD") or "")
    remote_bind_host = os.getenv("LLM_REMOTE_BIND_HOST", "127.0.0.1")
    remote_port = env_int("LLM_REMOTE_PORT", 1234)
    if not remote_host or not remote_user:
        return None
    try:
        warnings.filterwarnings("ignore", message="Blowfish has been deprecated.*")
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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
        return server, client
    except Exception:
        return None


def _close_tunnel() -> None:
    global _TUNNEL
    if not _TUNNEL:
        return
    server, client = _TUNNEL
    try:
        server.shutdown()
        server.server_close()
        client.close()
    finally:
        _TUNNEL = None


atexit.register(_close_tunnel)
