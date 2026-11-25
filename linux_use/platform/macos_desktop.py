"""macOS window list / focus / launch via Cocoa + CGWindowList.

UNTESTED on real macOS hardware: written and unit-tested with mocks on Linux.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any, Callable, Iterable

from .base import ActionResult, DesktopBackend, WindowInfo

HARDWARE_TESTED = False
UNTESTED_NOTE = "macOS desktop backend is implemented but untested on real hardware."


class MacDesktop(DesktopBackend):
    """`api` may be injected (tests). Live path uses Quartz + AppKit."""

    HARDWARE_TESTED = HARDWARE_TESTED

    def __init__(self, api: Any | None = None, popen: Callable[..., Any] | None = None):
        self._api = api
        self._popen = popen or subprocess.Popen

    def _live(self) -> Any:
        if self._api is not None:
            return self._api
        if sys.platform == "darwin":
            self._api = _LiveMacDesktop()
            return self._api
        raise RuntimeError(
            "macOS desktop API is not available on this OS. " + UNTESTED_NOTE
        )

    def list_windows(self) -> list[WindowInfo]:
        return list(self._live().enum_windows())

    def _find(self, title_or_id: str) -> WindowInfo | None:
        want = (title_or_id or "").strip()
        if not want:
            return None
        wins = self.list_windows()
        for w in wins:
            if w.wid == want:
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
            self._live().activate(w)
            return ActionResult(True, f"focused {w.wid} {w.title!r}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"focus failed: {exc}", act_ms=_ms(t0))

    def launch_app(self, command: str, args: Iterable[str] | None = None) -> ActionResult:
        t0 = time.perf_counter()
        # Prefer NSWorkspace when the command looks like an app name; else Popen.
        try:
            if self._api is not None and hasattr(self._api, "launch"):
                pid = self._api.launch(command, list(args or []))
                return ActionResult(True, f"launched {command} pid={pid}", {"pid": pid}, act_ms=_ms(t0))
            if sys.platform == "darwin" and not str(command).startswith("/"):
                # Try Workspace by localized name, fall back to PATH.
                try:
                    pid = self._live().launch(command, list(args or []))
                    return ActionResult(True, f"launched {command} pid={pid}", {"pid": pid}, act_ms=_ms(t0))
                except Exception:
                    pass
            cmd = [command, *([str(a) for a in args] if args else [])]
            proc = self._popen(cmd)
            return ActionResult(True, f"launched {' '.join(cmd)} pid={proc.pid}", {"pid": proc.pid}, act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"launch failed: {exc}", act_ms=_ms(t0))

    def resize_window(self, title_or_id: str, x: int, y: int, width: int, height: int) -> ActionResult:
        t0 = time.perf_counter()
        w = self._find(title_or_id)
        if w is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        try:
            self._live().set_bounds(w, int(x), int(y), int(width), int(height))
            return ActionResult(True, f"moved {w.wid} to {x},{y} {width}x{height}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"resize failed: {exc}", act_ms=_ms(t0))

    def close_window(self, title_or_id: str) -> ActionResult:
        t0 = time.perf_counter()
        w = self._find(title_or_id)
        if w is None:
            return ActionResult(False, f"window not found: {title_or_id}", act_ms=_ms(t0))
        try:
            self._live().close(w)
            return ActionResult(True, f"closed {w.wid} {w.title!r}", act_ms=_ms(t0))
        except Exception as exc:
            return ActionResult(False, f"close failed: {exc}", act_ms=_ms(t0))

    def screen_size(self) -> tuple[int, int]:
        return self._live().screen_size()


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


class _LiveMacDesktop:
    def __init__(self):
        from Quartz import (  # type: ignore
            CGDisplayBounds,
            CGMainDisplayID,
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionOnScreenOnly,
        )
        from Cocoa import NSRunningApplication, NSWorkspace  # type: ignore
        self._CGWindowListCopyWindowInfo = CGWindowListCopyWindowInfo
        self._opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        self._kCGNullWindowID = kCGNullWindowID
        self._CGDisplayBounds = CGDisplayBounds
        self._CGMainDisplayID = CGMainDisplayID
        self._NSWorkspace = NSWorkspace
        self._NSRunningApplication = NSRunningApplication

    def enum_windows(self) -> list[WindowInfo]:
        info = self._CGWindowListCopyWindowInfo(self._opts, self._kCGNullWindowID) or []
        out: list[WindowInfo] = []
        front_pid = None
        try:
            app = self._NSWorkspace.sharedWorkspace().frontmostApplication()
            front_pid = int(app.processIdentifier()) if app else None
        except Exception:
            front_pid = None
        for item in info:
            try:
                title = str(item.get("kCGWindowName") or item.get("kCGWindowOwnerName") or "")
                if not title:
                    continue
                bounds = item.get("kCGWindowBounds") or {}
                x = int(bounds.get("X", 0) or 0)
                y = int(bounds.get("Y", 0) or 0)
                w = int(bounds.get("Width", 0) or 0)
                h = int(bounds.get("Height", 0) or 0)
                pid = int(item.get("kCGWindowOwnerPID") or 0)
                wid = str(int(item.get("kCGWindowNumber") or 0))
                layer = int(item.get("kCGWindowLayer") or 0)
                if layer != 0 and not title:
                    continue
                out.append(WindowInfo(
                    wid=wid, title=title, pid=pid,
                    x=x, y=y, width=w, height=h,
                    host="macos", active=(pid == front_pid),
                ))
            except Exception:
                continue
        return out

    def activate(self, w: WindowInfo) -> None:
        app = self._NSRunningApplication.runningApplicationWithProcessIdentifier_(int(w.pid))
        if app is None:
            raise RuntimeError(f"no running application for pid {w.pid}")
        # NSApplicationActivateIgnoringOtherApps = 1 << 1
        app.activateWithOptions_(1 << 1)

    def launch(self, command: str, args: list[str]) -> int | None:
        ws = self._NSWorkspace.sharedWorkspace()
        ok = ws.launchApplication_(command)
        if not ok:
            raise RuntimeError(f"NSWorkspace could not launch {command!r}")
        # Best-effort pid of the app we just started
        for app in ws.runningApplications():
            try:
                name = str(app.localizedName() or "")
                if name.casefold() == command.casefold() or command.casefold() in name.casefold():
                    return int(app.processIdentifier())
            except Exception:
                continue
        return None

    def set_bounds(self, w: WindowInfo, x: int, y: int, width: int, height: int) -> None:
        # Use AX position/size on the focused window of that pid.
        from ApplicationServices import (  # type: ignore
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetAttributeValue,
            AXValueCreate,
            kAXPositionAttribute,
            kAXSizeAttribute,
            kAXWindowsAttribute,
            kAXValueCGPointType,
            kAXValueCGSizeType,
        )
        app = AXUIElementCreateApplication(int(w.pid))
        err, windows = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, None)
        if not windows:
            raise RuntimeError("no AX windows for pid")
        # Pick the window whose title matches when possible
        target = windows[0]
        for win in windows:
            try:
                _, title = AXUIElementCopyAttributeValue(win, "AXTitle", None)
                if title and str(title) in (w.title or ""):
                    target = win
                    break
            except Exception:
                continue
        # CGPoint / CGSize via AXValueCreate
        try:
            from Quartz import CGPoint, CGSize  # type: ignore
            pos = AXValueCreate(kAXValueCGPointType, CGPoint(x, y))
            size = AXValueCreate(kAXValueCGSizeType, CGSize(width, height))
        except Exception:
            pos, size = (x, y), (width, height)
        AXUIElementSetAttributeValue(target, kAXPositionAttribute, pos)
        AXUIElementSetAttributeValue(target, kAXSizeAttribute, size)

    def close(self, w: WindowInfo) -> None:
        from ApplicationServices import (  # type: ignore
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementPerformAction,
            kAXCloseButtonAttribute,
            kAXPressAction,
            kAXWindowsAttribute,
        )
        app = AXUIElementCreateApplication(int(w.pid))
        err, windows = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, None)
        if not windows:
            raise RuntimeError("no AX windows for pid")
        target = windows[0]
        err, btn = AXUIElementCopyAttributeValue(target, kAXCloseButtonAttribute, None)
        if btn is None:
            raise RuntimeError("no AXCloseButton")
        AXUIElementPerformAction(btn, kAXPressAction)

    def screen_size(self) -> tuple[int, int]:
        b = self._CGDisplayBounds(self._CGMainDisplayID())
        # CGRect: origin + size
        try:
            return int(b.size.width), int(b.size.height)
        except Exception:
            return int(b[2]), int(b[3]) if isinstance(b, (tuple, list)) else (0, 0)
