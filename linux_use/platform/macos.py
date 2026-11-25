"""macOS backends (AX + CGEvent).

UNTESTED on real macOS hardware. Prefer importing via
`linux_use.platform.get_backends()`, which auto-detects the OS.

Install optional deps with: pip install 'linux-use[macos]'
The hosting process must be trusted under System Settings >
Privacy & Security > Accessibility.
"""

from __future__ import annotations

from .macos_a11y import HARDWARE_TESTED, UNTESTED_NOTE, MacAccessibility
from .macos_desktop import MacDesktop
from .macos_input import MacInput

__all__ = [
    "MacAccessibility",
    "MacDesktop",
    "MacInput",
    "HARDWARE_TESTED",
    "UNTESTED_NOTE",
]
