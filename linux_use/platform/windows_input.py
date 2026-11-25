"""Windows input backend via SendInput (ctypes). pywinauto is not required.

UNTESTED on real Windows hardware: written and unit-tested with mocks on Linux.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable

from .base import ActionResult, InputBackend

HARDWARE_TESTED = False
UNTESTED_NOTE = "Windows input backend is implemented but untested on real hardware."

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WHEEL_DELTA = 120

VK = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


class WindowsInput(InputBackend):
    """Send mouse and keyboard events with SendInput.

    `sender` may be injected for tests. It must implement
    `send(inputs)`, `cursor()`, and `screen_size()`.
    """

    HARDWARE_TESTED = HARDWARE_TESTED

    def __init__(self, sender: Any | None = None):
        self._sender = sender

    def _api(self) -> Any:
        if self._sender is not None:
            return self._sender
        if sys.platform.startswith("win"):
            self._sender = _LiveSendInput()
            return self._sender
        raise RuntimeError(
            "Windows SendInput is not available on this OS. " + UNTESTED_NOTE
        )

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> ActionResult:
        t0 = time.perf_counter()
        button = (button or "left").lower()
        down, up = {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
        try:
            api = self._api()
            events = [self._mouse_move(api, x, y)]
            for _ in range(max(1, int(clicks))):
                events.append({"kind": "mouse", "flags": down, "x": x, "y": y, "data": 0})
                events.append({"kind": "mouse", "flags": up, "x": x, "y": y, "data": 0})
            api.send(events)
            return ActionResult(True, f"clicked {button} {clicks}x at {x},{y}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"click failed: {exc}", act_ms=_ms(t0))

    def move(self, x: int, y: int) -> ActionResult:
        t0 = time.perf_counter()
        try:
            api = self._api()
            api.send([self._mouse_move(api, x, y)])
            return ActionResult(True, f"moved to {x},{y}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"move failed: {exc}", act_ms=_ms(t0))

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> ActionResult:
        t0 = time.perf_counter()
        try:
            api = self._api()
            api.send([
                self._mouse_move(api, x1, y1),
                {"kind": "mouse", "flags": MOUSEEVENTF_LEFTDOWN, "x": x1, "y": y1, "data": 0},
                self._mouse_move(api, x2, y2),
                {"kind": "mouse", "flags": MOUSEEVENTF_LEFTUP, "x": x2, "y": y2, "data": 0},
            ])
            return ActionResult(True, f"dragged {x1},{y1} -> {x2},{y2}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"drag failed: {exc}", act_ms=_ms(t0))

    def type_text(self, text: str, *, clear: bool = False, press_enter: bool = False) -> ActionResult:
        t0 = time.perf_counter()
        try:
            api = self._api()
            events: list[dict] = []
            if clear:
                events.extend(self._keys("ctrl+a"))
                events.extend(self._keys("backspace"))
            for ch in text or "":
                events.append({"kind": "unicode", "char": ch, "up": False})
                events.append({"kind": "unicode", "char": ch, "up": True})
            if press_enter:
                events.extend(self._keys("Return"))
            api.send(events)
            return ActionResult(True, f"typed {len(text or '')} chars", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"type failed: {exc}", act_ms=_ms(t0))

    def shortcut(self, keys: str) -> ActionResult:
        t0 = time.perf_counter()
        try:
            api = self._api()
            api.send(self._keys(keys))
            return ActionResult(True, f"shortcut {keys}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"shortcut failed: {exc}", act_ms=_ms(t0))

    def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ActionResult:
        t0 = time.perf_counter()
        direction = (direction or "down").lower()
        delta = int(amount or 3) * WHEEL_DELTA
        if direction in {"down", "right"}:
            delta = -delta
        try:
            api = self._api()
            api.send([
                self._mouse_move(api, x, y),
                {"kind": "mouse", "flags": MOUSEEVENTF_WHEEL, "x": x, "y": y, "data": delta},
            ])
            return ActionResult(True, f"scrolled {direction} {amount} at {x},{y}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"scroll failed: {exc}", act_ms=_ms(t0))

    def mouse_location(self) -> tuple[int, int]:
        return self._api().cursor()

    def _mouse_move(self, api, x: int, y: int) -> dict:
        return {"kind": "mouse", "flags": MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                "x": int(x), "y": int(y), "data": 0}

    def _keys(self, keys: str) -> list[dict]:
        parts = [p.strip().lower() for p in (keys or "").replace("-", "+").split("+") if p.strip()]
        vks: list[int] = []
        for p in parts:
            if p in VK:
                vks.append(VK[p])
            elif len(p) == 1:
                ch = p.upper()
                vks.append(ord(ch))
            else:
                raise ValueError(f"unknown key: {p}")
        events: list[dict] = []
        for vk in vks:
            events.append({"kind": "key", "vk": vk, "up": False})
        for vk in reversed(vks):
            events.append({"kind": "key", "vk": vk, "up": True})
        return events


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


class _LiveSendInput:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.INPUT, self.MOUSEINPUT, self.KEYBDINPUT = _build_input_structs(ctypes, wintypes)

    def screen_size(self) -> tuple[int, int]:
        # Virtual desktop so absolute coords cover all monitors
        x = int(self.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or self.user32.GetSystemMetrics(0) or 1)
        y = int(self.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or self.user32.GetSystemMetrics(1) or 1)
        return max(x, 1), max(y, 1)

    def cursor(self) -> tuple[int, int]:
        pt = self.wintypes.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(pt))
        return int(pt.x), int(pt.y)

    def send(self, events: list[dict]) -> None:
        ctypes = self.ctypes
        sw, sh = self.screen_size()
        arr_type = self.INPUT * max(len(events), 1)
        arr = arr_type()
        for i, ev in enumerate(events):
            kind = ev.get("kind")
            if kind == "mouse":
                x, y = int(ev["x"]), int(ev["y"])
                ax = int(x * 65535 / max(sw - 1, 1))
                ay = int(y * 65535 / max(sh - 1, 1))
                arr[i].type = INPUT_MOUSE
                arr[i].union.mi.dx = ax
                arr[i].union.mi.dy = ay
                arr[i].union.mi.mouseData = int(ev.get("data") or 0)
                flags = int(ev.get("flags") or 0)
                if flags & MOUSEEVENTF_WHEEL:
                    # wheel should not force absolute move bits
                    arr[i].union.mi.dwFlags = flags
                else:
                    arr[i].union.mi.dwFlags = flags
            elif kind == "key":
                arr[i].type = INPUT_KEYBOARD
                arr[i].union.ki.wVk = int(ev["vk"])
                arr[i].union.ki.dwFlags = KEYEVENTF_KEYUP if ev.get("up") else 0
            elif kind == "unicode":
                arr[i].type = INPUT_KEYBOARD
                arr[i].union.ki.wVk = 0
                arr[i].union.ki.wScan = ord(ev["char"])
                flags = KEYEVENTF_UNICODE
                if ev.get("up"):
                    flags |= KEYEVENTF_KEYUP
                arr[i].union.ki.dwFlags = flags
        n = len(events)
        if n:
            ok = self.user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(self.INPUT))
            if ok != n:
                raise OSError(f"SendInput injected {ok}/{n} events")


# Imported by _LiveSendInput only; also used to size SM_* in input module
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def _build_input_structs(ctypes, wintypes):
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        )

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        )

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = (
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        )

    class _UNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _UNION))

    return INPUT, MOUSEINPUT, KEYBDINPUT
