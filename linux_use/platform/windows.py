"""Windows backend stub. Not implemented on this Linux box."""

from __future__ import annotations

from typing import Any, Iterable
from .base import AccessibilityBackend, ActionResult, DesktopBackend, InputBackend, WindowInfo


class _Stub:
    def _fail(self, *args, **kwargs):
        raise NotImplementedError(
            "Windows backend is a stub. Implement via UI Automation (UIA) later."
        )


class WindowsAccessibility(_Stub, AccessibilityBackend):
    ensure_ready = _Stub._fail
    snapshot = _Stub._fail
    get_element = _Stub._fail
    invalidate = _Stub._fail


class WindowsDesktop(_Stub, DesktopBackend):
    list_windows = _Stub._fail
    focus_window = _Stub._fail
    launch_app = _Stub._fail
    resize_window = _Stub._fail
    close_window = _Stub._fail
    screen_size = _Stub._fail


class WindowsInput(_Stub, InputBackend):
    click = _Stub._fail
    move = _Stub._fail
    drag = _Stub._fail
    type_text = _Stub._fail
    shortcut = _Stub._fail
    scroll = _Stub._fail
    mouse_location = _Stub._fail
