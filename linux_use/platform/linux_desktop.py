"""Window management via wmctrl/xdotool on X11."""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import Iterable

from ..a11y import child_env
from ..config import SETTINGS
from .base import ActionResult, DesktopBackend, WindowInfo


def _run(cmd: list[str], timeout: float = 8.0, env=None) -> subprocess.CompletedProcess:
    env = env or child_env()
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env
    )


class LinuxDesktop(DesktopBackend):
    def list_windows(self) -> list[WindowInfo]:
        proc = _run(["wmctrl", "-lpG"])
        windows: list[WindowInfo] = []
        active_id = self._active_wid()
        for line in (proc.stdout or "").splitlines():
            parts = line.split(None, 8)
            if len(parts) < 8:
                continue
            wid, _desk, pid, x, y, w, h, host = parts[:8]
            title = parts[8] if len(parts) > 8 else ""
            windows.append(WindowInfo(
                wid=wid,
                title=title,
                pid=int(pid) if pid.lstrip("-").isdigit() else -1,
                x=int(x), y=int(y), width=int(w), height=int(h),
                host=host,
                active=(wid.lower() == (active_id or "").lower()),
            ))
        return windows

    def _active_wid(self) -> str | None:
        proc = _run(["xdotool", "getactivewindow"])
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                return hex(int(proc.stdout.strip()))
            except ValueError:
                return proc.stdout.strip()
        return None

    def _find(self, title_or_id: str) -> WindowInfo | None:
        key = title_or_id.strip().lower()
        wins = self.list_windows()
        for w in wins:
            if w.wid.lower() == key or w.wid.lower() == key.replace("0x", ""):
                return w
        for w in wins:
            if key and key in w.title.lower():
                return w
        return None

    def focus_window(self, title_or_id: str) -> ActionResult:
        t0 = time.perf_counter()
        win = self._find(title_or_id)
        if win is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        proc = _run(["wmctrl", "-i", "-a", win.wid])
        if proc.returncode != 0:
            _run(["xdotool", "windowactivate", "--sync", str(int(win.wid, 16))])
        return ActionResult(True, f"focused {win.title}", {"wid": win.wid}, act_ms=_ms(t0))

    def launch_app(self, command: str, args: Iterable[str] | None = None) -> ActionResult:
        t0 = time.perf_counter()
        if any(c in command for c in [";", "&&", "|", "`", "$(", "\n"]):
            # simple command + args only; no shell interpolation
            return ActionResult(False, "refusing shell metacharacters in launch command", act_ms=_ms(t0))
        argv = shlex.split(command)
        if args:
            argv.extend(str(a) for a in args)
        try:
            proc = subprocess.Popen(
                argv,
                env=child_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            return ActionResult(False, f"command not found: {argv[0]}", act_ms=_ms(t0))
        # Wait briefly so the window can appear
        time.sleep(0.4)
        return ActionResult(True, f"launched pid={proc.pid}", {"pid": proc.pid, "argv": argv}, act_ms=_ms(t0))

    def resize_window(self, title_or_id: str, x: int, y: int, width: int, height: int) -> ActionResult:
        t0 = time.perf_counter()
        win = self._find(title_or_id)
        if win is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        spec = f"0,{x},{y},{width},{height}"
        proc = _run(["wmctrl", "-i", "-r", win.wid, "-e", spec])
        ok = proc.returncode == 0
        return ActionResult(ok, proc.stderr.strip() or spec, {"wid": win.wid}, act_ms=_ms(t0))

    def close_window(self, title_or_id: str) -> ActionResult:
        t0 = time.perf_counter()
        win = self._find(title_or_id)
        if win is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        proc = _run(["wmctrl", "-i", "-c", win.wid])
        return ActionResult(proc.returncode == 0, f"closed {win.title}", {"wid": win.wid}, act_ms=_ms(t0))

    def screen_size(self) -> tuple[int, int]:
        proc = _run(["xdotool", "getdisplaygeometry"])
        if proc.returncode == 0:
            parts = proc.stdout.split()
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
        return 1920, 1200


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0
