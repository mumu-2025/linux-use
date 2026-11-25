"""Windows window manager backend via user32 / kernel32 (ctypes).

UNTESTED on real Windows hardware: written and unit-tested with mocks on Linux.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any, Callable, Iterable

from .base import ActionResult, DesktopBackend, WindowInfo

HARDWARE_TESTED = False
UNTESTED_NOTE = "Windows desktop backend is implemented but untested on real hardware."

# Win32 constants
SW_RESTORE = 9
WM_CLOSE = 0x0010
GW_OWNER = 4
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class WindowsDesktop(DesktopBackend):
    """Enumerate and control top-level windows with the Win32 API.

    `api` may be injected for tests. It should provide the user32/kernel32
    functions used below (EnumWindows, GetWindowRect, ...).
    """

    HARDWARE_TESTED = HARDWARE_TESTED

    def __init__(self, api: Any | None = None, popen: Callable[..., Any] | None = None):
        self._api = api
        self._popen = popen or subprocess.Popen

    def _winapi(self) -> Any:
        if self._api is not None:
            return self._api
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes
            self._api = _LiveWinAPI(ctypes, wintypes)
            return self._api
        raise RuntimeError(
            "Windows desktop API is not available on this OS. " + UNTESTED_NOTE
        )

    def list_windows(self) -> list[WindowInfo]:
        api = self._winapi()
        return list(api.enum_windows())

    def _find(self, title_or_id: str) -> WindowInfo | None:
        want = (title_or_id or "").strip()
        if not want:
            return None
        wins = self.list_windows()
        for w in wins:
            if w.wid == want or w.wid == want.lower():
                return w
        low = want.casefold()
        for w in wins:
            if (w.title or "").casefold() == low:
                return w
        for w in wins:
            if low in (w.title or "").casefold():
                return w
        return None

    def focus_window(self, title_or_id: str) -> ActionResult:
        t0 = time.perf_counter()
        w = self._find(title_or_id)
        if w is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        try:
            self._winapi().set_foreground(w.wid)
            return ActionResult(True, f"focused {w.wid} {w.title!r}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"focus failed: {exc}", act_ms=_ms(t0))

    def launch_app(self, command: str, args: Iterable[str] | None = None) -> ActionResult:
        t0 = time.perf_counter()
        cmd = [command, *([str(a) for a in args] if args else [])]
        try:
            proc = self._popen(cmd)
            pid = getattr(proc, "pid", None)
            return ActionResult(True, f"launched {' '.join(cmd)} pid={pid}", {"pid": pid}, act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"launch failed: {exc}", act_ms=_ms(t0))

    def resize_window(self, title_or_id: str, x: int, y: int, width: int, height: int) -> ActionResult:
        t0 = time.perf_counter()
        w = self._find(title_or_id)
        if w is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        try:
            self._winapi().move_window(w.wid, int(x), int(y), int(width), int(height))
            return ActionResult(True, f"moved {w.wid} to {x},{y} {width}x{height}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"resize failed: {exc}", act_ms=_ms(t0))

    def close_window(self, title_or_id: str) -> ActionResult:
        t0 = time.perf_counter()
        w = self._find(title_or_id)
        if w is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        try:
            self._winapi().close_window(w.wid)
            return ActionResult(True, f"closed {w.wid} {w.title!r}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"close failed: {exc}", act_ms=_ms(t0))

    def screen_size(self) -> tuple[int, int]:
        api = self._winapi()
        return api.screen_size()


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


class _LiveWinAPI:
    """Thin ctypes wrapper; only constructed on Windows."""

    def __init__(self, ctypes, wintypes):
        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def enum_windows(self) -> list[WindowInfo]:
        ctypes, wintypes = self.ctypes, self.wintypes
        user32 = self.user32
        out: list[WindowInfo] = []
        fg = int(user32.GetForegroundWindow() or 0)

        def _cb(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, GW_OWNER):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if not title:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append(WindowInfo(
                wid=str(int(hwnd)),
                title=title,
                pid=int(pid.value),
                x=int(rect.left), y=int(rect.top),
                width=int(rect.right - rect.left),
                height=int(rect.bottom - rect.top),
                host="windows",
                active=(int(hwnd) == fg),
            ))
            return True

        user32.EnumWindows(self.EnumWindowsProc(_cb), 0)
        return out

    def set_foreground(self, wid: str) -> None:
        hwnd = int(wid, 0) if str(wid).startswith("0x") else int(wid)
        self.user32.ShowWindow(hwnd, SW_RESTORE)
        self.user32.SetForegroundWindow(hwnd)

    def move_window(self, wid: str, x: int, y: int, width: int, height: int) -> None:
        hwnd = int(wid, 0) if str(wid).startswith("0x") else int(wid)
        # bRepaint = True
        self.user32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)

    def close_window(self, wid: str) -> None:
        hwnd = int(wid, 0) if str(wid).startswith("0x") else int(wid)
        self.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def screen_size(self) -> tuple[int, int]:
        w = int(self.user32.GetSystemMetrics(SM_CXSCREEN) or 0)
        h = int(self.user32.GetSystemMetrics(SM_CYSCREEN) or 0)
        return w, h
