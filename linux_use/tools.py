"""Agent tools. Actions use cached element ids when possible (no tree rescan)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import SAFE_FILE_ROOTS, SETTINGS
from .platform.base import ActionResult
from .screenshot import take_screenshot

SHELL_DENY = (
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "shutdown", "reboot",
    "halt", "poweroff", "chmod -R 777 /",
)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., ActionResult]
    experimental: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Toolbelt:
    def __init__(self, a11y, desktop, input_backend, experimental: bool = False):
        self.a11y = a11y
        self.desktop = desktop
        self.input = input_backend
        self.experimental = experimental
        self.last_observe_ms = 0.0
        self._tools = self._build()

    def _build(self) -> dict[str, ToolSpec]:
        props = {
            "click": ToolSpec(
                "click",
                "Click a cached element id or raw screen coordinates. Prefer id.",
                _object({
                    "id": _str("Element id from the tree, e.g. e12"),
                    "x": _int("Screen X if no id"),
                    "y": _int("Screen Y if no id"),
                    "button": _str("left, right, or middle", default="left"),
                    "clicks": _int("1=single, 2=double", default=1),
                }),
                self.click,
            ),
            "type": ToolSpec(
                "type",
                "Type text. If id is a text field, write via AT-SPI (faster). Otherwise type at the focused widget.",
                _object({
                    "text": _str("Text to type", required=True),
                    "id": _str("Optional element id to focus or set"),
                    "clear": _bool("Clear existing text first", default=False),
                    "press_enter": _bool("Press Enter afterwards", default=False),
                }, required=["text"]),
                self.type_text,
            ),
            "shortcut": ToolSpec(
                "shortcut",
                "Press a keyboard shortcut such as ctrl+s or alt+F4.",
                _object({"keys": _str("Plus-separated keys, e.g. ctrl+s", required=True)}, required=["keys"]),
                self.shortcut,
            ),
            "scroll": ToolSpec(
                "scroll",
                "Scroll at an element or coordinates.",
                _object({
                    "id": _str("Element id"),
                    "x": _int("X"),
                    "y": _int("Y"),
                    "direction": _str("up, down, left, right", default="down"),
                    "amount": _int("Wheel ticks", default=3),
                }),
                self.scroll,
            ),
            "move": ToolSpec(
                "move",
                "Move the mouse, or drag from one point/element to another.",
                _object({
                    "x": _int("Destination X"),
                    "y": _int("Destination Y"),
                    "id": _str("Destination element id"),
                    "from_x": _int("Drag start X"),
                    "from_y": _int("Drag start Y"),
                    "from_id": _str("Drag start element id"),
                    "drag": _bool("If true, drag instead of hover", default=False),
                }),
                self.move,
            ),
            "app": ToolSpec(
                "app",
                "Launch, focus, resize, or close an application window.",
                _object({
                    "action": _str("launch, focus, resize, close, list", required=True),
                    "command": _str("Command to launch, e.g. mousepad"),
                    "title": _str("Window title substring or window id"),
                    "x": _int("Resize X"),
                    "y": _int("Resize Y"),
                    "width": _int("Resize width"),
                    "height": _int("Resize height"),
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Launch arguments"},
                }, required=["action"]),
                self.app,
            ),
            "shell": ToolSpec(
                "shell",
                "Run a shell command and return stdout/stderr. Use for short, safe commands.",
                _object({
                    "command": _str("Command line", required=True),
                    "timeout": _int("Seconds", default=20),
                }, required=["command"]),
                self.shell,
            ),
            "wait": ToolSpec(
                "wait",
                "Pause for a short time so the UI can settle.",
                _object({"seconds": {"type": "number", "description": "Seconds to wait", "default": 0.4}}),
                self.wait,
            ),
            "inspect": ToolSpec(
                "inspect",
                "Force a (possibly incremental) accessibility-tree refresh and return it.",
                _object({"force": _bool("Full rescan, ignoring the cache", default=False)}),
                self.inspect,
            ),
            "done": ToolSpec(
                "done",
                "Finish the task and return the final answer.",
                _object({"answer": _str("Result to show the user", required=True)}, required=["answer"]),
                self.done,
            ),
            "file": ToolSpec(
                "file",
                "Read, write, list, copy, move, or delete files under the home directory, /tmp, the current directory, and optional LINUX_USE_FILE_ROOTS.",
                _object({
                    "action": _str("read, write, list, exists, copy, move, delete", required=True),
                    "path": _str("Target path", required=True),
                    "dest": _str("Destination for copy/move"),
                    "content": _str("Content for write"),
                    "append": _bool("Append instead of overwrite", default=False),
                }, required=["action", "path"]),
                self.file,
                experimental=True,
            ),
            "screenshot": ToolSpec(
                "screenshot",
                "Save a PNG screenshot. Optional; the tree is the primary observation.",
                _object({"path": _str("Output PNG path")}),
                self.screenshot,
                experimental=True,
            ),
        }
        return props

    def schemas(self) -> list[dict[str, Any]]:
        tools = []
        for spec in self._tools.values():
            if spec.experimental and not self.experimental:
                continue
            tools.append(spec.openai_schema())
        return tools

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ActionResult:
        spec = self._tools.get(name)
        if spec is None:
            return ActionResult(False, f"unknown tool: {name}")
        if spec.experimental and not self.experimental:
            return ActionResult(False, f"tool {name} is experimental; enable experimental=True")
        arguments = arguments or {}
        try:
            return spec.handler(**{k: v for k, v in arguments.items() if k in _handler_args(spec.handler)})
        except TypeError as exc:
            return ActionResult(False, f"bad arguments for {name}: {exc}")
        except Exception as exc:
            return ActionResult(False, f"{name} failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ tools
    def click(self, id: str | None = None, x: int | None = None, y: int | None = None,
              button: str = "left", clicks: int = 1) -> ActionResult:
        t0 = time.perf_counter()
        if id:
            el = self.a11y.get_element(id)
            if el is None:
                return ActionResult(False, f"unknown element {id}", act_ms=_ms(t0))
            # Prefer AT-SPI action or grab_focus + click at cached bounds
            acted = False
            if button == "left" and clicks == 1:
                if self.a11y.do_action(id, "click"):
                    acted = True
                else:
                    self.a11y.grab_focus(id)
            if not acted:
                if not el.bounds_valid and (x is None or y is None):
                    # try refreshed live bounds
                    el = self.a11y.get_element(id) or el
                cx, cy = (x if x is not None else el.cx), (y if y is not None else el.cy)
                if el.bounds_valid or x is not None:
                    res = self.input.click(cx, cy, button=button, clicks=clicks)
                    res.message = f"clicked [{id}] {el.role} {el.name!r} at {cx},{cy}; {res.message}"
                    return res
                return ActionResult(False, f"element {id} has no valid bounds", act_ms=_ms(t0))
            return ActionResult(True, f"AT-SPI click [{id}] {el.role} {el.name!r}", act_ms=_ms(t0))
        if x is None or y is None:
            return ActionResult(False, "click requires id or x,y", act_ms=_ms(t0))
        return self.input.click(int(x), int(y), button=button, clicks=clicks)

    def type_text(self, text: str, id: str | None = None, clear: bool = False,
                  press_enter: bool = False) -> ActionResult:
        t0 = time.perf_counter()
        if id:
            el = self.a11y.get_element(id)
            if el is None:
                return ActionResult(False, f"unknown element {id}", act_ms=_ms(t0))
            self.a11y.grab_focus(id)
            if clear:
                if self.a11y.set_text(id, text):
                    if press_enter:
                        self.input.shortcut("Return")
                    return ActionResult(True, f"set_text [{id}] ({len(text)} chars)", act_ms=_ms(t0))
            elif not (el.text or "").strip() and self.a11y.set_text(id, text):
                if press_enter:
                    self.input.shortcut("Return")
                return ActionResult(True, f"set_text [{id}] ({len(text)} chars)", act_ms=_ms(t0))
            if el.bounds_valid:
                self.input.click(el.cx, el.cy)
        return self.input.type_text(text, clear=clear, press_enter=press_enter)

    def shortcut(self, keys: str) -> ActionResult:
        return self.input.shortcut(keys)

    def scroll(self, id: str | None = None, x: int | None = None, y: int | None = None,
               direction: str = "down", amount: int = 3) -> ActionResult:
        if id:
            el = self.a11y.get_element(id)
            if el is None:
                return ActionResult(False, f"unknown element {id}")
            x, y = el.cx, el.cy
        if x is None or y is None:
            mx, my = self.input.mouse_location()
            x, y = mx, my
        return self.input.scroll(int(x), int(y), direction=direction, amount=amount)

    def move(self, x: int | None = None, y: int | None = None, id: str | None = None,
             from_x: int | None = None, from_y: int | None = None, from_id: str | None = None,
             drag: bool = False) -> ActionResult:
        if id:
            el = self.a11y.get_element(id)
            if el is None:
                return ActionResult(False, f"unknown element {id}")
            x, y = el.cx, el.cy
        if x is None or y is None:
            return ActionResult(False, "move requires id or x,y")
        if drag:
            if from_id:
                el0 = self.a11y.get_element(from_id)
                if el0 is None:
                    return ActionResult(False, f"unknown element {from_id}")
                from_x, from_y = el0.cx, el0.cy
            if from_x is None or from_y is None:
                from_x, from_y = self.input.mouse_location()
            return self.input.drag(int(from_x), int(from_y), int(x), int(y))
        return self.input.move(int(x), int(y))

    def app(self, action: str, command: str | None = None, title: str | None = None,
            x: int | None = None, y: int | None = None, width: int | None = None,
            height: int | None = None, args: list[str] | None = None) -> ActionResult:
        action = (action or "").lower()
        if action == "list":
            wins = self.desktop.list_windows()
            text = "\n".join(
                f"{w.wid} pid={w.pid} {w.x},{w.y} {w.width}x{w.height} {'*' if w.active else ' '} {w.title}"
                for w in wins
            )
            return ActionResult(True, text or "(no windows)", {"windows": [w.__dict__ for w in wins]})
        if action == "launch":
            if not command:
                return ActionResult(False, "launch requires command")
            res = self.desktop.launch_app(command, args)
            self.a11y.invalidate("app-launch")
            return res
        if not title:
            return ActionResult(False, f"{action} requires title")
        if action == "focus":
            return self.desktop.focus_window(title)
        if action == "close":
            res = self.desktop.close_window(title)
            self.a11y.invalidate("app-close")
            return res
        if action == "resize":
            if None in (x, y, width, height):
                return ActionResult(False, "resize requires x,y,width,height")
            return self.desktop.resize_window(title, int(x), int(y), int(width), int(height))
        return ActionResult(False, f"unknown app action: {action}")

    def shell(self, command: str, timeout: int = 20) -> ActionResult:
        t0 = time.perf_counter()
        if not SETTINGS.allow_shell:
            return ActionResult(False, "shell is disabled", act_ms=_ms(t0))
        low = command.strip().lower()
        for banned in SHELL_DENY:
            if banned in low:
                return ActionResult(False, f"refusing dangerous command ({banned})", act_ms=_ms(t0))
        from .a11y import child_env
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=max(1, min(int(timeout), 120)), cwd=os.getcwd(),
            env=child_env(),
        )
        out = (proc.stdout or "")[-4000:]
        err = (proc.stderr or "")[-2000:]
        msg = f"exit {proc.returncode}\n{out}"
        if err:
            msg += f"\n[stderr]\n{err}"
        return ActionResult(proc.returncode == 0, msg, {"exit": proc.returncode}, act_ms=_ms(t0))

    def wait(self, seconds: float = 0.4) -> ActionResult:
        t0 = time.perf_counter()
        time.sleep(max(0.0, min(float(seconds), 15.0)))
        return ActionResult(True, f"waited {seconds}s", act_ms=_ms(t0))

    def inspect(self, force: bool = False) -> ActionResult:
        snap = self.a11y.snapshot(force=bool(force))
        self.last_observe_ms = snap.observe_ms
        return ActionResult(True, snap.observation(), {"observe_ms": snap.observe_ms, "mode": snap.refresh_mode})

    def done(self, answer: str) -> ActionResult:
        return ActionResult(True, answer, {"done": True, "answer": answer})

    def file(self, action: str, path: str, dest: str | None = None,
             content: str | None = None, append: bool = False) -> ActionResult:
        t0 = time.perf_counter()
        target = _safe_path(path)
        if target is None:
            return ActionResult(False, f"path not allowed: {path}", act_ms=_ms(t0))
        action = action.lower()
        if action == "exists":
            return ActionResult(True, str(target.exists()), {"exists": target.exists()}, act_ms=_ms(t0))
        if action == "list":
            if not target.exists():
                return ActionResult(False, "not found", act_ms=_ms(t0))
            if target.is_file():
                return ActionResult(True, str(target), act_ms=_ms(t0))
            names = sorted(p.name for p in target.iterdir())[:200]
            return ActionResult(True, "\n".join(names), act_ms=_ms(t0))
        if action == "read":
            if not target.is_file():
                return ActionResult(False, "not a file", act_ms=_ms(t0))
            data = target.read_text(encoding="utf-8", errors="replace")
            return ActionResult(True, data[:8000], {"n": len(data)}, act_ms=_ms(t0))
        if action == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with target.open("a", encoding="utf-8") as f:
                    f.write(content or "")
            else:
                target.write_text(content or "", encoding="utf-8")
            return ActionResult(True, f"wrote {target}", act_ms=_ms(t0))
        if action in {"copy", "move", "delete"}:
            if action == "delete":
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                return ActionResult(True, f"deleted {target}", act_ms=_ms(t0))
            dest_p = _safe_path(dest or "")
            if dest_p is None:
                return ActionResult(False, f"dest not allowed: {dest}", act_ms=_ms(t0))
            dest_p.parent.mkdir(parents=True, exist_ok=True)
            if action == "copy":
                if target.is_dir():
                    shutil.copytree(target, dest_p)
                else:
                    shutil.copy2(target, dest_p)
            else:
                shutil.move(str(target), str(dest_p))
            return ActionResult(True, f"{action} {target} -> {dest_p}", act_ms=_ms(t0))
        return ActionResult(False, f"unknown file action: {action}", act_ms=_ms(t0))

    def screenshot(self, path: str | None = None) -> ActionResult:
        t0 = time.perf_counter()
        dest = Path(path or (SETTINGS.log_dir / f"shot-{int(time.time())}.png"))
        try:
            take_screenshot(dest)
        except Exception as exc:
            return ActionResult(False, str(exc), act_ms=_ms(t0))
        return ActionResult(True, str(dest), {"path": str(dest)}, act_ms=_ms(t0))


def _safe_path(path: str) -> Path | None:
    if not path:
        return None
    if SETTINGS.allow_all_files:
        return Path(path).expanduser().resolve()
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve()
    except Exception:
        resolved = raw
    for root in SAFE_FILE_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    return None


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def _str(desc: str, required: bool = False, default=None):
    d = {"type": "string", "description": desc}
    if default is not None:
        d["default"] = default
    return d


def _int(desc: str, default=None):
    d = {"type": "integer", "description": desc}
    if default is not None:
        d["default"] = default
    return d


def _bool(desc: str, default=None):
    d = {"type": "boolean", "description": desc}
    if default is not None:
        d["default"] = default
    return d


def _object(props: dict, required: list[str] | None = None):
    return {"type": "object", "properties": props, "required": required or []}


def _handler_args(fn) -> set[str]:
    import inspect
    return set(inspect.signature(fn).parameters)
