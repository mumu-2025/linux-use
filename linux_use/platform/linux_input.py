"""X11 input via xdotool (this box is Xvfb + xfwm4, not Wayland)."""

from __future__ import annotations

import subprocess
import time

from ..a11y import child_env
from .base import ActionResult, InputBackend


def _run(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=child_env())


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


BUTTONS = {"left": "1", "middle": "2", "right": "3", "1": "1", "2": "2", "3": "3"}


class LinuxInput(InputBackend):
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> ActionResult:
        t0 = time.perf_counter()
        btn = BUTTONS.get(str(button).lower(), "1")
        clicks = max(1, min(int(clicks), 3))
        cmd = ["xdotool", "mousemove", "--sync", str(int(x)), str(int(y)), "click", "--repeat", str(clicks), btn]
        proc = _run(cmd)
        ok = proc.returncode == 0
        return ActionResult(ok, proc.stderr.strip() or f"click {btn}x{clicks} at {x},{y}", act_ms=_ms(t0))

    def move(self, x: int, y: int) -> ActionResult:
        t0 = time.perf_counter()
        proc = _run(["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))])
        return ActionResult(proc.returncode == 0, f"move {x},{y}", act_ms=_ms(t0))

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> ActionResult:
        t0 = time.perf_counter()
        proc = _run([
            "xdotool",
            "mousemove", "--sync", str(int(x1)), str(int(y1)),
            "mousedown", "1",
            "mousemove", "--sync", str(int(x2)), str(int(y2)),
            "mouseup", "1",
        ])
        return ActionResult(proc.returncode == 0, f"drag {x1},{y1}->{x2},{y2}", act_ms=_ms(t0))

    def type_text(self, text: str, *, clear: bool = False, press_enter: bool = False) -> ActionResult:
        t0 = time.perf_counter()
        if clear:
            _run(["xdotool", "key", "--clearmodifiers", "ctrl+a", "BackSpace"])
        # xdotool type handles ASCII well; keep a small delay for reliability
        proc = _run(["xdotool", "type", "--clearmodifiers", "--delay", "5", "--", text])
        if press_enter:
            _run(["xdotool", "key", "Return"])
        ok = proc.returncode == 0
        msg = proc.stderr.strip() or f"typed {len(text)} chars"
        return ActionResult(ok, msg, {"n": len(text)}, act_ms=_ms(t0))

    def shortcut(self, keys: str) -> ActionResult:
        t0 = time.perf_counter()
        combo = keys.strip().replace(" ", "").replace("-", "+")
        # xdotool uses plus-separated keysyms; allow "ctrl+s" style
        proc = _run(["xdotool", "key", "--clearmodifiers", combo])
        return ActionResult(proc.returncode == 0, f"key {combo}", act_ms=_ms(t0))

    def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ActionResult:
        t0 = time.perf_counter()
        mapping = {"down": "5", "up": "4", "left": "6", "right": "7"}
        btn = mapping.get(direction.lower(), "5")
        amount = max(1, min(int(amount), 30))
        _run(["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))])
        proc = _run(["xdotool", "click", "--repeat", str(amount), btn])
        return ActionResult(proc.returncode == 0, f"scroll {direction} x{amount} at {x},{y}", act_ms=_ms(t0))

    def mouse_location(self) -> tuple[int, int]:
        proc = _run(["xdotool", "getmouselocation", "--shell"])
        vals = {}
        for line in (proc.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k] = v
        return int(vals.get("X", 0)), int(vals.get("Y", 0))
