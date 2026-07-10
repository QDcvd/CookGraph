#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复刻数据库第一个 session 的所有对话，观察新架构下的表现。
"""
import asyncio, os, sys, threading, socketserver, select, urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def load_project_env() -> dict:
    env = os.environ.copy()
    env_path = ROOT / ".env"
    if not env_path.exists(): return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in env:
            env[k] = v; os.environ[k] = v
    return env

load_project_env()
from backend.agent_adapter_local_LLM_harness import stream_search_agent

# ── SSH tunnel (same pattern as run_multiturn_dialogue_test.py) ──
def env_truthy(v): return str(v or "").strip().lower() in {"1","true","yes","on"}
def env_int(e,k,d):
    try: return int(str(e.get(k,d)).strip())
    except: return d

def openai_base_available(url, t=2.0):
    if not url: return False
    try:
        with urllib.request.urlopen(url.rstrip("/")+"/models", timeout=t) as r:
            return 200 <= r.status < 300
    except: return False

class _FwdSvr(socketserver.ThreadingTCPServer):
    daemon_threads = True; allow_reuse_address = True

def _handler(transport, rh, rp):
    class H(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                ch = transport.open_channel("direct-tcpip", (rh, rp), self.request.getpeername())
                if not ch: return
                while True:
                    r,_,_ = select.select([self.request,ch],[],[],1.0)
                    if self.request in r:
                        d = self.request.recv(65536)
                        if not d: break
                        ch.sendall(d)
                    if ch in r:
                        d = ch.recv(65536)
                        if not d: break
                        self.request.sendall(d)
            finally: ch.close()
    return H

def start_tunnel(env):
    if not env_truthy(env.get("LLM_SSH_TUNNEL")): return None
    b = env.get("LLM_BASE_URL","")
    lp = env_int(env, "LLM_LOCAL_PORT", 51234)
    lh = env.get("LLM_LOCAL_HOST","127.0.0.1")
    lb = f"http://{lh}:{lp}/v1"
    env["LLM_BASE_URL"] = lb; os.environ["LLM_BASE_URL"] = lb
    if openai_base_available(lb): return None
    rh = str(env.get("LLM_REMOTE_HOST","")).strip()
    ru = str(env.get("LLM_REMOTE_USER","")).strip()
    rpwd = str(env.get("LLM_REMOTE_PASSWORD",""))
    rbind = env.get("LLM_REMOTE_BIND_HOST","127.0.0.1")
    rp = env_int(env, "LLM_REMOTE_PORT", 1234)
    if not rh or not ru: return None
    import paramiko
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(rh, username=ru, password=rpwd or None, timeout=15,banner_timeout=15,auth_timeout=15)
    svr = _FwdSvr((lh,lp), _handler(c.get_transport(), rbind, rp))
    t = threading.Thread(target=svr.serve_forever, daemon=True); t.start()
    return svr, c

# ── 复刻对话 ──
questions = [
    "告诉我西红柿炒鸡蛋怎么做",
    "鸡蛋有多少种做法？",
    "我想吃清蒸鲈鱼",
    "锅包肉怎么做",
    "告诉我清蒸鲈鱼怎么做",
    "严格使用菜谱工具查询",
    "完了",
]

async def main():
    tunnel = start_tunnel(os.environ)
    history = []
    print(f"{'='*60}")
    print(f"  复刻第一个 session 对话（{len(questions)} 轮）")
    print(f"  LLM: {os.environ.get('LLM_MODEL','?')}")
    print(f"{'='*60}")

    for i, q in enumerate(questions):
        print(f"\n{'─'*40}")
        print(f"[第{i+1}轮] 用户: {q}")
        print(f"{'─'*40}")

        full = ""
        trace = None
        try:
            async for ev in stream_search_agent(q, history):
                if ev.get("type") == "content":
                    full += ev.get("content","")
                elif ev.get("type") == "trace":
                    trace = ev.get("rag_trace")
        except Exception as e:
            print(f"[错误] {e}")
            continue

        print(f"[回答]: {full[:500]}")
        if trace:
            calls = trace.get("tool_calls",[])
            if calls:
                for tc in calls:
                    print(f"  → tool: {tc.get('tool_name','')}({tc.get('args',{})})")
        print(f"  [{len(full)} chars]")

        history.append({"role":"user","content":q})
        history.append({"role":"assistant","content":full,"rag_trace":trace})

    if tunnel:
        tunnel[0].shutdown(); tunnel[0].server_close(); tunnel[1].close()
    print(f"\n{'='*60}  复刻完成")

asyncio.run(main())
