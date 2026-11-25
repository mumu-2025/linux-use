"""Minimal JSON-RPC 2.0 interface so other agents can drive linux-use.

Methods:
  inspect({force?: bool}) -> snapshot observation + metrics
  act({tool, ...args}) -> ActionResult
  run({task, max_steps?, mode?}) -> AgentResult (requires API key)
  windows() -> window list
  map({refresh?: bool}) -> classified location map
"""

from __future__ import annotations

import json
import socket
import sys
from typing import Any


def _dispatch(method: str, params: dict[str, Any] | None) -> Any:
    params = params or {}
    if method in {"inspect", "snapshot"}:
        from .api import inspect_desktop
        snap = inspect_desktop(force=bool(params.get("force")))
        return {
            "observation": snap.observation(),
            "observe_ms": snap.observe_ms,
            "refresh_mode": snap.refresh_mode,
            "cache_hit": snap.cache_hit,
            "generation": snap.generation,
            "node_count": snap.node_count,
            "focused_id": snap.focused_id,
            "active_app": snap.active_app,
        }
    if method == "act":
        from .api import act
        params = dict(params)
        tool = params.pop("tool", None)
        if not tool:
            raise ValueError("act requires 'tool'")
        result = act(tool, **params)
        return {"ok": result.ok, "message": result.message, "data": result.data, "act_ms": result.act_ms}
    if method in {"map", "location_map"}:
        from .api import get_location_map
        loc = get_location_map(refresh=bool(params.get("refresh")))
        return loc.to_dict()
    if method == "windows":
        from .api import _ctx
        _, desktop, __, ___ = _ctx()
        return [w.__dict__ for w in desktop.list_windows()]
    if method == "run":
        from .agent import Agent
        from .llm import OpenAICompatLLM
        llm = OpenAICompatLLM()
        if not llm.available():
            raise RuntimeError("No API key for run()")
        agent = Agent(llm=llm, log_to_console=False, max_steps=int(params.get("max_steps") or 25),
                      mode=params.get("mode") or "normal")
        result = agent.invoke(params["task"])
        return {
            "ok": result.ok,
            "answer": result.answer,
            "total_ms": result.total_ms,
            "observe_ms_total": result.observe_ms_total,
            "act_ms_total": result.act_ms_total,
            "steps": [s.__dict__ for s in result.steps],
        }
    if method in {"ping", "health"}:
        return {"ok": True, "name": "linux-use"}
    raise ValueError(f"unknown method: {method}")


def handle_rpc(request: dict[str, Any]) -> dict[str, Any]:
    rid = request.get("id")
    try:
        if request.get("jsonrpc") not in (None, "2.0"):
            raise ValueError("only jsonrpc 2.0 is supported")
        result = _dispatch(request["method"], request.get("params") or {})
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"},
        }


def serve_stdio() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            print(json.dumps(resp, ensure_ascii=False), flush=True)
            continue
        print(json.dumps(handle_rpc(req), ensure_ascii=False), flush=True)
    return 0


def serve_tcp(host: str, port: int) -> int:
    """Line-delimited JSON-RPC over a localhost TCP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(4)
    print(f"linux-use JSON-RPC listening on {host}:{port}", flush=True)
    while True:
        conn, addr = sock.accept()
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line.decode("utf-8"))
                        resp = handle_rpc(req)
                    except Exception as exc:
                        resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
                    conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0
