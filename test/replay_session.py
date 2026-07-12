#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复刻数据库最新 session 的多轮用户对话，观察当前 agent 真实表现。
"""
import argparse, asyncio, json, os, sqlite3, sys, threading, socketserver, select, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_DB_PATH = ROOT / "data" / "memory.sqlite3"
DEFAULT_OUTPUT_PATH = ROOT / "test" / ".artifacts" / "latest_session_replay_result.json"

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

def kill_port(port):
    """杀掉占用指定端口的进程。"""
    import subprocess
    result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.strip().split()
            pid = parts[-1]
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)

def start_tunnel(env):
    if not env_truthy(env.get("LLM_SSH_TUNNEL")): return None
    b = env.get("LLM_BASE_URL","")
    lp = env_int(env, "LLM_LOCAL_PORT", 51234)
    lh = env.get("LLM_LOCAL_HOST","127.0.0.1")
    lb = f"http://{lh}:{lp}/v1"
    env["LLM_BASE_URL"] = lb; os.environ["LLM_BASE_URL"] = lb
    # 清理残留端口，避免前一次 TIME_WAIT 误判为"API 可用"
    kill_port(lp)
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

# ── 从 SQLite 读取最新 session ──
def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def latest_session_id(db_path: Path) -> str:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, title, updated_at
            FROM chat_sessions
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError(f"没有找到 active session：{db_path}")
    return str(row["id"])

def load_session_messages(db_path: Path, session_id: str) -> tuple[dict, list[dict]]:
    with _connect(db_path) as conn:
        session = conn.execute(
            "SELECT id, title, updated_at FROM chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise RuntimeError(f"session 不存在：{session_id}")
        rows = conn.execute(
            """
            SELECT role, content, rag_trace_json, created_at
            FROM chat_messages
            WHERE session_id = ? AND deleted_at IS NULL
            ORDER BY created_at
            """,
            (session_id,),
        ).fetchall()
    messages = []
    for row in rows:
        trace = None
        if row["rag_trace_json"]:
            try:
                trace = json.loads(row["rag_trace_json"])
            except json.JSONDecodeError:
                trace = None
        messages.append({
            "role": str(row["role"]),
            "content": str(row["content"] or ""),
            "rag_trace": trace,
            "created_at": str(row["created_at"] or ""),
        })
    return dict(session), messages

def extract_user_questions(messages: list[dict]) -> list[str]:
    return [
        item["content"]
        for item in messages
        if item.get("role") in {"human", "user"} and str(item.get("content") or "").strip()
    ]

def trace_tool_summary(trace: dict | None) -> list[dict]:
    if not isinstance(trace, dict):
        return []
    result = []
    for call in trace.get("tool_calls", []) or []:
        result.append({
            "tool_name": call.get("tool_name"),
            "args": call.get("args"),
            "preview": call.get("output_preview") or call.get("content", "")[:500],
        })
    return result

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="复刻 SQLite 最新 session 的多轮用户对话。")
    parser.add_argument("--session-id", help="指定 session_id；默认取最新 active session")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="JSON 结果输出路径")
    parser.add_argument("--max-turns", type=int, default=0, help="最多复放多少轮用户消息；0 表示全部")
    return parser.parse_args()

async def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    session_id = args.session_id or latest_session_id(db_path)
    session, stored_messages = load_session_messages(db_path, session_id)
    questions = extract_user_questions(stored_messages)
    if args.max_turns > 0:
        questions = questions[: args.max_turns]

    tunnel = start_tunnel(os.environ)
    history = []
    print(f"{'='*60}")
    print(f"  复刻最新 session 多轮用户对话")
    print(f"  session_id: {session_id}")
    print(f"  title: {session.get('title')}")
    print(f"  updated_at: {session.get('updated_at')}")
    print(f"  用户轮次: {len(questions)}")
    print(f"  LLM: {os.environ.get('LLM_MODEL','?')}")
    print(f"{'='*60}")

    replay_records = {
        "session_id": session_id,
        "session_title": session.get("title"),
        "source_updated_at": session.get("updated_at"),
        "replayed_at": datetime.now().isoformat(),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "turns": [],
    }

    try:
        for i, q in enumerate(questions):
            print(f"\n{'─'*40}")
            print(f"[第{i+1}轮] 用户: {q}")
            print(f"{'─'*40}")

            full = ""
            trace = None
            raw_events = []
            try:
                async for ev in stream_search_agent(q, history):
                    raw_events.append(ev)
                    if ev.get("type") == "content":
                        full += ev.get("content","")
                    elif ev.get("type") == "trace":
                        trace = ev.get("rag_trace")
            except Exception as e:
                print(f"[错误] {e}")
                continue

            print(f"[回答]: {full[:500]}")
            tool_summary = trace_tool_summary(trace)
            for tc in tool_summary:
                print(f"  → tool: {tc.get('tool_name','')}({tc.get('args',{})})")
            print(f"  [{len(full)} chars]")

            replay_records["turns"].append({
                "turn": i + 1,
                "user": q,
                "assistant": full,
                "tool_calls": tool_summary,
                "trace": trace,
                "raw_event_types": [ev.get("type") for ev in raw_events],
            })
            history.append({"role":"user","content":q})
            history.append({"role":"assistant","content":full,"rag_trace":trace})
    finally:
        if tunnel:
            tunnel[0].shutdown(); tunnel[0].server_close(); tunnel[1].close()

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(replay_records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[输出] {output_path}")
    print(f"\n{'='*60}  复刻完成")

if __name__ == "__main__":
    asyncio.run(main())
