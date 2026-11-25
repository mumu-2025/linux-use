"""MCP stdio server so Claude, Cursor, and other MCP clients can drive the desktop.

Transport: JSON-RPC 2.0 with LSP-style Content-Length framing (MCP standard).
No extra dependency; the protocol is implemented here.

Tools: inspect, map, act, run, windows.

Start with:  linux-use mcp
(or the alias desktop-use mcp)

This is not an official Anthropic SDK server; it speaks the documented MCP
protocol (2024-11-05) over stdin/stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__
from .platform import current_os, hardware_tested

PROTOCOL_VERSION = "2024-11-05"

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "inspect",
        "description": (
            "Dump the current desktop accessibility tree (AT-SPI / UIA / AX) "
            "and timing metrics. No LLM. Prefer this to a screenshot when you "
            "need element ids, roles, names, and bounds."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "Force a full rescan instead of the incremental cache.", "default": False},
            },
        },
    },
    {
        "name": "map",
        "description": (
            "Dump the classified element location map (name/role/region -> "
            "screen coordinates). Used by the hybrid click path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "Refresh from a new snapshot first.", "default": False},
            },
        },
    },
    {
        "name": "act",
        "description": (
            "Run one scripted desktop action without an LLM. Tools: click, type, "
            "shortcut, scroll, move, app, shell, wait, inspect, file, screenshot, done. "
            "The default policy is HYBRID (AT-SPI/UIA/AX primitive first, then cache "
            "or locmap; cheap post-action verify)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "Action name, e.g. click, type, shortcut."},
                "id": {"type": "string", "description": "Cached element id (e12)."},
                "name": {"type": "string"},
                "role": {"type": "string"},
                "region": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "text": {"type": "string"},
                "keys": {"type": "string", "description": "Shortcut, e.g. ctrl+s or cmd+s."},
                "button": {"type": "string", "default": "left"},
                "clicks": {"type": "integer", "default": 1},
                "clear": {"type": "boolean"},
                "press_enter": {"type": "boolean"},
                "direction": {"type": "string"},
                "amount": {"type": "integer"},
                "command": {"type": "string"},
                "title": {"type": "string"},
                "action": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "force": {"type": "boolean"},
            },
            "required": ["tool"],
        },
    },
    {
        "name": "run",
        "description": (
            "Run a plain-English desktop task with an OpenAI-compatible LLM "
            "(LINUX_USE_API_KEY / OPENAI_API_KEY). Returns the agent's answer and step log."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to do on the desktop."},
                "max_steps": {"type": "integer", "default": 25},
                "model": {"type": "string"},
                "base_url": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "windows",
        "description": "List top-level windows (id, title, geometry, pid, active).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _result_text(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def handle_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    arguments = dict(arguments or {})
    if name == "inspect":
        from .api import inspect_desktop
        snap = inspect_desktop(force=bool(arguments.get("force")))
        header = (
            f"os={current_os()} hardware_tested={hardware_tested()} "
            f"mode={snap.refresh_mode} observe_ms={snap.observe_ms:.1f} "
            f"nodes={snap.node_count} walked={snap.walked} generation={snap.generation}\n"
        )
        return _result_text(header + snap.observation())
    if name == "map":
        from .api import get_location_map
        locmap = get_location_map(refresh=bool(arguments.get("refresh")))
        dump = locmap.dump_text() if hasattr(locmap, "dump_text") else str(locmap)
        return _result_text(dump)
    if name == "windows":
        from .platform import get_backends
        _, desktop, _ = get_backends()
        lines = []
        for w in desktop.list_windows():
            mark = "*" if w.active else " "
            lines.append(
                f"{mark} {w.wid} pid={w.pid} {w.x},{w.y} {w.width}x{w.height} {w.title}"
            )
        return _result_text("\n".join(lines) or "(no windows)")
    if name == "act":
        from .api import act
        tool = arguments.pop("tool", None)
        if not tool:
            return _result_text("act requires 'tool'", is_error=True)
        # Common alias: id -> accepted by Toolbelt as id
        if "element_id" in arguments and "id" not in arguments:
            arguments["id"] = arguments.pop("element_id")
        res = act(tool, **arguments)
        payload = {
            "ok": getattr(res, "ok", False),
            "message": getattr(res, "message", str(res)),
            "act_ms": getattr(res, "act_ms", 0),
            "data": getattr(res, "data", {}) or {},
        }
        return _result_text(json.dumps(payload, ensure_ascii=False, indent=2), is_error=not payload["ok"])
    if name == "run":
        task = arguments.get("task")
        if not task:
            return _result_text("run requires 'task'", is_error=True)
        from .agent import Agent
        from .config import SETTINGS
        from .llm import OpenAICompatLLM
        if not (SETTINGS.api_key or ""):
            return _result_text(
                "No LLM API key. Set LINUX_USE_API_KEY or OPENAI_API_KEY, "
                "or use the inspect/map/act tools without an LLM.",
                is_error=True,
            )
        model = arguments.get("model") or SETTINGS.model
        base_url = arguments.get("base_url") or SETTINGS.base_url
        llm = OpenAICompatLLM(model=model, base_url=base_url, api_key=SETTINGS.api_key)
        agent = Agent(llm=llm, max_steps=int(arguments.get("max_steps") or SETTINGS.max_steps))
        result = agent.invoke(str(task))
        payload = {
            "ok": result.ok,
            "answer": result.answer,
            "steps": [
                {
                    "index": s.index, "tool": s.tool, "result": s.result,
                    "observe_ms": s.observe_ms, "act_ms": s.act_ms,
                    "refresh_mode": s.refresh_mode,
                }
                for s in (result.steps or [])
            ],
            "total_ms": result.total_ms,
            "observe_ms_total": result.observe_ms_total,
        }
        return _result_text(json.dumps(payload, ensure_ascii=False, indent=2), is_error=not result.ok)
    return _result_text(f"unknown tool: {name}", is_error=True)


def handle_rpc(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Return a JSON-RPC response dict, or None for notifications."""
    mid = msg.get("id", None)
    method = msg.get("method") or ""
    params = msg.get("params") or {}
    is_notification = "id" not in msg

    def ok(result):
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "linux-use",
                "version": __version__,
                "os": current_os(),
                "hardwareTested": hardware_tested(),
            },
            "instructions": (
                "Desktop GUI agent. Prefer inspect/map to understand the UI, "
                "then act (hybrid policy is the default). run() needs an API key. "
                f"Current OS={current_os()}, hardware_tested={hardware_tested()}."
            ),
        })
    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOL_DEFS})
    if method == "tools/call":
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        try:
            result = handle_tool(name, arguments)
        except Exception as exc:
            result = _result_text(f"{type(exc).__name__}: {exc}", is_error=True)
        return ok(result)
    if method in {"shutdown", "quit"}:
        return ok({})
    return err(-32601, f"method not found: {method}")


def _read_message(inp: TextIO) -> dict[str, Any] | None:
    """Read one MCP / LSP framed message, or a raw newline-delimited JSON object."""
    # Peek first line to decide framing.
    first = inp.readline()
    if first == "":
        return None
    if first.lower().startswith("content-length:"):
        length = int(first.split(":", 1)[1].strip())
        # Read remaining headers until blank line
        while True:
            line = inp.readline()
            if line == "":
                return None
            if line in ("\r\n", "\n"):
                break
        body = inp.read(length)
        if not body:
            return None
        return json.loads(body)
    # Newline-delimited JSON (handy for tests and debugging)
    line = first.strip()
    if not line:
        return _read_message(inp)
    return json.loads(line)


def _write_message(out: TextIO, msg: dict[str, Any]) -> None:
    body = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    blob = body.encode("utf-8")
    out.write(f"Content-Length: {len(blob)}\r\n\r\n")
    out.write(body)
    out.flush()


def serve_mcp(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Block on stdin and serve MCP until EOF or shutdown."""
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    while True:
        try:
            msg = _read_message(inp)
        except Exception as exc:
            _write_message(out, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": f"parse error: {exc}"}})
            continue
        if msg is None:
            return 0
        try:
            resp = handle_rpc(msg)
        except Exception as exc:
            mid = msg.get("id") if isinstance(msg, dict) else None
            resp = {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        if resp is not None:
            _write_message(out, resp)
        if isinstance(msg, dict) and msg.get("method") in {"shutdown", "quit"}:
            return 0


def main(argv: list[str] | None = None) -> int:
    return serve_mcp()
