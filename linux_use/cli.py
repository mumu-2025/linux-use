"""Command-line interface: linux-use / python -m linux_use."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import SETTINGS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="linux-use",
        description="Desktop GUI agent (AT-SPI / UI Automation / AX). Default policy: hybrid.",
    )
    parser.add_argument("--version", action="version", version=f"linux-use {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="Dump the accessibility tree (no LLM).")
    p_ins.add_argument("--json", action="store_true", help="Machine-readable JSON")
    p_ins.add_argument("--full", action="store_true", help="Force a full rescan")
    p_ins.add_argument("--out", type=Path, help="Write output to a file")
    p_ins.add_argument("--include-hidden", action="store_true")

    p_map = sub.add_parser("map", help="Dump the classified element location map (name/role/region -> coordinates).")
    p_map.add_argument("--json", action="store_true")
    p_map.add_argument("--refresh", action="store_true", help="Force a fresh snapshot before dumping")
    p_map.add_argument("--out", type=Path)

    p_run = sub.add_parser("run", help="Run a plain-English task with an LLM.")
    p_run.add_argument("task", help="Task in plain language")
    p_run.add_argument("--max-steps", type=int, default=SETTINGS.max_steps)
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--base-url", default=None)
    p_run.add_argument("--mode", choices=["normal", "flash"], default="normal")
    p_run.add_argument("--experimental", action="store_true")

    p_act = sub.add_parser("act", help="Run one scripted action (no LLM).")
    p_act.add_argument("tool", help="click, type, shortcut, scroll, move, app, shell, wait, inspect, file, screenshot")
    p_act.add_argument("--id", dest="element_id")
    p_act.add_argument("--name")
    p_act.add_argument("--role")
    p_act.add_argument("--region")
    p_act.add_argument("--x", type=int)
    p_act.add_argument("--y", type=int)
    p_act.add_argument("--from-x", type=int)
    p_act.add_argument("--from-y", type=int)
    p_act.add_argument("--button", default="left")
    p_act.add_argument("--clicks", type=int, default=1)
    p_act.add_argument("--text")
    p_act.add_argument("--keys")
    p_act.add_argument("--direction", default="down")
    p_act.add_argument("--amount", type=int, default=3)
    p_act.add_argument("--clear", action="store_true")
    p_act.add_argument("--press-enter", action="store_true")
    p_act.add_argument("--drag", action="store_true")
    p_act.add_argument("--action")
    p_act.add_argument("--command")
    p_act.add_argument("--title")
    p_act.add_argument("--width", type=int)
    p_act.add_argument("--height", type=int)
    p_act.add_argument("--path")
    p_act.add_argument("--dest")
    p_act.add_argument("--content")
    p_act.add_argument("--force", action="store_true")
    p_act.add_argument("--seconds", type=float, default=0.4)

    p_sm = sub.add_parser("smoke", help="Controlled smoke test with latency numbers.")
    p_sm.add_argument("--keep-window", action="store_true")

    p_bench = sub.add_parser("bench", help="Apples-to-apples compare original / cache / locmap / hybrid on the same scripted task.")
    p_bench.add_argument("--runs", type=int, default=5, help="Runs per mode (default 5)")
    p_bench.add_argument("--out-dir", type=Path, default=None)

    p_sv = sub.add_parser("serve", help="JSON-RPC server for other agents.")
    p_sv.add_argument("--stdio", action="store_true", help="Read JSON-RPC from stdin")
    p_sv.add_argument("--host", default="127.0.0.1")
    p_sv.add_argument("--port", type=int, default=8765)

    p_mcp = sub.add_parser("mcp", help="MCP stdio server (inspect/map/act/run/windows) for Claude, Cursor, and other MCP clients.")

    args = parser.parse_args(argv)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "map":
        return cmd_map(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "act":
        return cmd_act(args)
    if args.cmd == "smoke":
        from .smoke import run_smoke
        return run_smoke(keep_window=args.keep_window)
    if args.cmd == "bench":
        from .bench import run_bench
        payload = run_bench(runs=max(1, args.runs), out_dir=args.out_dir)
        modes = payload["summary"]["modes"]
        failed = sum(modes[m]["failed"] for m in modes)
        return 0 if failed == 0 else 1
    if args.cmd == "serve":
        from .server import serve_stdio, serve_tcp
        if args.stdio:
            return serve_stdio()
        return serve_tcp(args.host, args.port)
    if args.cmd == "mcp":
        from .mcp_server import serve_mcp
        return serve_mcp()
    parser.print_help()
    return 2


def cmd_inspect(args) -> int:
    from .api import inspect_desktop
    snap = inspect_desktop(force=bool(args.full))
    if args.json:
        text = json.dumps(snap.to_dict(), ensure_ascii=False, indent=2)
    else:
        text = snap.observation()
        if args.include_hidden:
            text += "\n\n-- including hidden --\n" + snap.compact_text(include_hidden=True)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({snap.refresh_mode}, {snap.observe_ms:.1f} ms, {snap.node_count} nodes)")
    else:
        print(text)
    return 0


def cmd_map(args) -> int:
    from .api import get_location_map
    loc = get_location_map(refresh=bool(args.refresh))
    text = json.dumps(loc.to_dict(), ensure_ascii=False, indent=2) if args.json else loc.dump_text()
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(loc.entries)} entries, built_ms={loc.built_ms:.1f})")
    else:
        print(text)
    return 0


def cmd_run(args) -> int:
    from .agent import Agent
    from .llm import OpenAICompatLLM
    llm = OpenAICompatLLM(model=args.model, base_url=args.base_url)
    if not llm.available():
        print(
            "No API key set (LINUX_USE_API_KEY or OPENAI_API_KEY). "
            "Use `linux-use inspect` or `linux-use act` / `linux-use smoke` without an LLM.",
            file=sys.stderr,
        )
        return 2
    agent = Agent(llm=llm, mode=args.mode, max_steps=args.max_steps, experimental=args.experimental)
    result = agent.invoke(args.task)
    print("\n=== result ===")
    print(result.answer)
    print(
        f"ok={result.ok} steps={len(result.steps)} "
        f"total={result.total_ms:.0f}ms observe={result.observe_ms_total:.0f}ms "
        f"act={result.act_ms_total:.0f}ms"
    )
    return 0 if result.ok else 1


def cmd_act(args) -> int:
    from .api import act
    tool = args.tool
    kwargs = {}
    mapping = {
        "element_id": "id",
        "name": "name", "role": "role", "region": "region",
        "x": "x", "y": "y",
        "from_x": "from_x", "from_y": "from_y",
        "button": "button", "clicks": "clicks",
        "text": "text", "keys": "keys",
        "direction": "direction", "amount": "amount",
        "clear": "clear", "press_enter": "press_enter", "drag": "drag",
        "action": "action", "command": "command", "title": "title",
        "width": "width", "height": "height",
        "path": "path", "dest": "dest", "content": "content",
        "force": "force", "seconds": "seconds",
    }
    for attr, key in mapping.items():
        if not hasattr(args, attr):
            continue
        val = getattr(args, attr)
        if val is None:
            continue
        if isinstance(val, bool) and val is False and key not in {"clear", "press_enter", "drag", "force"}:
            continue
        # skip default-y flags for tools that don't use them
        kwargs[key] = val
    # Trim kwargs that the tool doesn't need by letting Toolbelt handle TypeError... 
    # we already filter by signature in call()
    result = act(tool, **kwargs)
    print(("OK " if result.ok else "FAIL ") + result.message)
    if result.act_ms:
        print(f"act_ms={result.act_ms:.1f}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
