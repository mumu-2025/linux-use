"""Windows backends (UI Automation + SendInput).

UNTESTED on real Windows hardware. Prefer importing via
`linux_use.platform.get_backends()`, which auto-detects the OS.

Install optional deps with: pip install 'linux-use[windows]'
"""

from __future__ import annotations

from .windows_a11y import HARDWARE_TESTED, UNTESTED_NOTE, WindowsAccessibility
from .windows_desktop import WindowsDesktop
from .windows_input import WindowsInput

__all__ = [
    "WindowsAccessibility",
    "WindowsDesktop",
    "WindowsInput",
    "HARDWARE_TESTED",
    "UNTESTED_NOTE",
]
