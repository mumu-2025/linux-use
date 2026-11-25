"""Platform backends. Linux is implemented; Windows/macOS are interface stubs."""

from __future__ import annotations

import sys
from .base import AccessibilityBackend, DesktopBackend, InputBackend


def current_os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


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
