"""Platform backends. Auto-detect OS; Linux is hardware-tested.

Windows (UI Automation + SendInput) and macOS (AX + CGEvent) are real
code paths, but they have not been run on real hardware. Same hybrid
policy (cache vs locmap, AT-SPI/UIA/AX primitives) sits on top of all three.
"""

from __future__ import annotations

import sys
from .base import AccessibilityBackend, ActionResult, DesktopBackend, InputBackend, WindowInfo

HARDWARE_TESTED_OS = frozenset({"linux"})


def current_os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def hardware_tested(name: str | None = None) -> bool:
    return (name or current_os()) in HARDWARE_TESTED_OS


def get_backends():
    """Return (a11y, desktop, input) for the current OS."""
    name = current_os()
    if name == "linux":
        from .linux_a11y import LinuxAccessibility
        from .linux_desktop import LinuxDesktop
        from .linux_input import LinuxInput
        return LinuxAccessibility(), LinuxDesktop(), LinuxInput()
    if name == "windows":
        from .windows import WindowsAccessibility, WindowsDesktop, WindowsInput
        return WindowsAccessibility(), WindowsDesktop(), WindowsInput()
    if name == "macos":
        from .macos import MacAccessibility, MacDesktop, MacInput
        return MacAccessibility(), MacDesktop(), MacInput()
    raise RuntimeError(f"Unsupported platform: {name}")


__all__ = [
    "AccessibilityBackend",
    "ActionResult",
    "DesktopBackend",
    "InputBackend",
    "WindowInfo",
    "current_os",
    "get_backends",
    "hardware_tested",
    "HARDWARE_TESTED_OS",
]
