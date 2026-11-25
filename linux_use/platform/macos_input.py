"""macOS input backend via Quartz CGEvent (mouse + keyboard).

UNTESTED on real macOS hardware: written and unit-tested with mocks on Linux.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from .base import ActionResult, InputBackend

HARDWARE_TESTED = False
UNTESTED_NOTE = "macOS input backend is implemented but untested on real hardware."

# Quartz event types (numeric, so mocks and live code share them)
Q_NULL = 0
Q_LEFT_DOWN = 1
Q_LEFT_UP = 2
Q_RIGHT_DOWN = 3
Q_RIGHT_UP = 4
Q_MOUSE_MOVED = 5
Q_LEFT_DRAGGED = 6
Q_RIGHT_DRAGGED = 7
Q_KEY_DOWN = 10
Q_KEY_UP = 11
Q_SCROLL = 22
Q_OTHER_DOWN = 25  # middle
Q_OTHER_UP = 26

BTN_LEFT, BTN_RIGHT, BTN_CENTER = 0, 1, 2

# Virtual key codes (ANSI US). Enough for shortcuts used by the agent.
VK = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12,
    "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19,
    "7": 0x1A, "8": 0x1C, "0": 0x1D, "o": 0x1F, "u": 0x20, "i": 0x22,
    "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
    "return": 0x24, "enter": 0x24, "tab": 0x30, "space": 0x31,
    "delete": 0x33, "backspace": 0x33, "escape": 0x35, "esc": 0x35,
    "cmd": 0x37, "command": 0x37, "shift": 0x38, "capslock": 0x39,
    "option": 0x3A, "alt": 0x3A, "ctrl": 0x3B, "control": 0x3B,
    "right": 0x7C, "left": 0x7B, "down": 0x7D, "up": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
}

# Modifier flags (CGEventFlag)
FLAG_SHIFT = 0x020000
FLAG_CTRL = 0x040000
FLAG_ALT = 0x080000
FLAG_CMD = 0x100000

MOD_KEYS = {"shift": FLAG_SHIFT, "ctrl": FLAG_CTRL, "control": FLAG_CTRL,
            "alt": FLAG_ALT, "option": FLAG_ALT, "cmd": FLAG_CMD, "command": FLAG_CMD}


class MacInput(InputBackend):
    """`poster` may be injected for tests. Live path posts CGEvents to hid tap."""

    HARDWARE_TESTED = HARDWARE_TESTED

    def __init__(self, poster: Any | None = None):
        self._poster = poster

    def _api(self) -> Any:
        if self._poster is not None:
            return self._poster
        if sys.platform == "darwin":
            self._poster = _LiveCGEvent()
            return self._poster
        raise RuntimeError(
            "macOS CGEvent is not available on this OS. " + UNTESTED_NOTE
        )

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> ActionResult:
        t0 = time.perf_counter()
        button = (button or "left").lower()
        down, up, btn = {
            "left": (Q_LEFT_DOWN, Q_LEFT_UP, BTN_LEFT),
            "right": (Q_RIGHT_DOWN, Q_RIGHT_UP, BTN_RIGHT),
            "middle": (Q_OTHER_DOWN, Q_OTHER_UP, BTN_CENTER),
        }.get(button, (Q_LEFT_DOWN, Q_LEFT_UP, BTN_LEFT))
        try:
            api = self._api()
            events = [{"kind": "mouse", "etype": Q_MOUSE_MOVED, "x": x, "y": y, "button": btn, "click": 0}]
            n = max(1, int(clicks))
            for i in range(n):
                events.append({"kind": "mouse", "etype": down, "x": x, "y": y, "button": btn, "click": i + 1})
                events.append({"kind": "mouse", "etype": up, "x": x, "y": y, "button": btn, "click": i + 1})
            api.post(events)
            return ActionResult(True, f"clicked {button} {n}x at {x},{y}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"click failed: {exc}", act_ms=_ms(t0))

    def move(self, x: int, y: int) -> ActionResult:
        t0 = time.perf_counter()
        try:
            self._api().post([{"kind": "mouse", "etype": Q_MOUSE_MOVED, "x": x, "y": y, "button": BTN_LEFT, "click": 0}])
            return ActionResult(True, f"moved to {x},{y}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"move failed: {exc}", act_ms=_ms(t0))

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> ActionResult:
        t0 = time.perf_counter()
        try:
            self._api().post([
                {"kind": "mouse", "etype": Q_MOUSE_MOVED, "x": x1, "y": y1, "button": BTN_LEFT, "click": 0},
                {"kind": "mouse", "etype": Q_LEFT_DOWN, "x": x1, "y": y1, "button": BTN_LEFT, "click": 1},
                {"kind": "mouse", "etype": Q_LEFT_DRAGGED, "x": x2, "y": y2, "button": BTN_LEFT, "click": 1},
                {"kind": "mouse", "etype": Q_LEFT_UP, "x": x2, "y": y2, "button": BTN_LEFT, "click": 1},
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
                events.extend(self._keys("cmd+a"))
                events.extend(self._keys("backspace"))
            for ch in text or "":
                events.append({"kind": "unicode", "char": ch, "up": False})
                events.append({"kind": "unicode", "char": ch, "up": True})
            if press_enter:
                events.extend(self._keys("Return"))
            api.post(events)
            return ActionResult(True, f"typed {len(text or '')} chars", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"type failed: {exc}", act_ms=_ms(t0))

    def shortcut(self, keys: str) -> ActionResult:
        t0 = time.perf_counter()
        try:
            # On macOS, treat leading ctrl as cmd for common "save" style shortcuts
            # only when the caller wrote ctrl+; we still honour an explicit cmd+.
            self._api().post(self._keys(keys))
            return ActionResult(True, f"shortcut {keys}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"shortcut failed: {exc}", act_ms=_ms(t0))

    def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ActionResult:
        t0 = time.perf_counter()
        direction = (direction or "down").lower()
        amt = int(amount or 3)
        wheel = amt if direction in {"up", "left"} else -amt
        horiz = direction in {"left", "right"}
        try:
            self._api().post([
                {"kind": "mouse", "etype": Q_MOUSE_MOVED, "x": x, "y": y, "button": BTN_LEFT, "click": 0},
                {"kind": "scroll", "x": x, "y": y, "wheel": 0 if horiz else wheel, "wheel_x": wheel if horiz else 0},
            ])
            return ActionResult(True, f"scrolled {direction} {amt} at {x},{y}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"scroll failed: {exc}", act_ms=_ms(t0))

    def mouse_location(self) -> tuple[int, int]:
        return self._api().cursor()

    def _keys(self, keys: str) -> list[dict]:
        parts = [p.strip().lower() for p in (keys or "").replace("-", "+").split("+") if p.strip()]
        flags = 0
        main: list[int] = []
        for p in parts:
            if p in MOD_KEYS:
                flags |= MOD_KEYS[p]
                main.append(VK[p])
            elif p in VK:
                main.append(VK[p])
            elif len(p) == 1 and p in VK:
                main.append(VK[p])
            else:
                raise ValueError(f"unknown key: {p}")
        events: list[dict] = []
        for vk in main:
            events.append({"kind": "key", "vk": vk, "up": False, "flags": flags})
        for vk in reversed(main):
            events.append({"kind": "key", "vk": vk, "up": True, "flags": flags})
        return events


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


class _LiveCGEvent:
    def __init__(self):
        from Quartz import (  # type: ignore
            CGEventCreate,
            CGEventCreateKeyboardEvent,
            CGEventCreateMouseEvent,
            CGEventCreateScrollWheelEvent,
            CGEventGetLocation,
            CGEventKeyboardSetUnicodeString,
            CGEventPost,
            CGEventSetFlags,
            CGEventSetIntegerValueField,
            CGEventSourceCreate,
            kCGEventSourceStateHIDSystemState,
            kCGHIDEventTap,
            kCGMouseEventClickState,
            kCGScrollEventUnitLine,
        )
        self._CGEventCreate = CGEventCreate
        self._CGEventCreateKeyboardEvent = CGEventCreateKeyboardEvent
        self._CGEventCreateMouseEvent = CGEventCreateMouseEvent
        self._CGEventCreateScrollWheelEvent = CGEventCreateScrollWheelEvent
        self._CGEventGetLocation = CGEventGetLocation
        self._CGEventKeyboardSetUnicodeString = CGEventKeyboardSetUnicodeString
        self._CGEventPost = CGEventPost
        self._CGEventSetFlags = CGEventSetFlags
        self._CGEventSetIntegerValueField = CGEventSetIntegerValueField
        self._src = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
        self._tap = kCGHIDEventTap
        self._click_field = kCGMouseEventClickState
        self._unit_line = kCGScrollEventUnitLine

    def cursor(self) -> tuple[int, int]:
        ev = self._CGEventCreate(None)
        loc = self._CGEventGetLocation(ev)
        return int(loc.x), int(loc.y)

    def post(self, events: list[dict]) -> None:
        for ev in events:
            kind = ev.get("kind")
            if kind == "mouse":
                e = self._CGEventCreateMouseEvent(
                    self._src, int(ev["etype"]), (float(ev["x"]), float(ev["y"])), int(ev.get("button") or 0),
                )
                if ev.get("click"):
                    self._CGEventSetIntegerValueField(e, self._click_field, int(ev["click"]))
                self._CGEventPost(self._tap, e)
            elif kind == "scroll":
                e = self._CGEventCreateScrollWheelEvent(
                    self._src, self._unit_line, 2, int(ev.get("wheel") or 0), int(ev.get("wheel_x") or 0),
                )
                self._CGEventPost(self._tap, e)
            elif kind == "key":
                e = self._CGEventCreateKeyboardEvent(self._src, int(ev["vk"]), not ev.get("up"))
                if ev.get("flags"):
                    self._CGEventSetFlags(e, int(ev["flags"]))
                self._CGEventPost(self._tap, e)
            elif kind == "unicode":
                ch = ev["char"]
                e = self._CGEventCreateKeyboardEvent(self._src, 0, not ev.get("up"))
                try:
                    self._CGEventKeyboardSetUnicodeString(e, len(ch), ch)
                except Exception:
                    pass
                self._CGEventPost(self._tap, e)
