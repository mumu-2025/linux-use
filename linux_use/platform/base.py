"""OS-agnostic interfaces for Linux (AT-SPI), Windows (UIA), and macOS (AX)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class WindowInfo:
    wid: str
    title: str
    pid: int
    x: int
    y: int
    width: int
    height: int
    host: str = ""
    active: bool = False


@dataclass
class ActionResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    act_ms: float = 0.0


class AccessibilityBackend(ABC):
    """Read the GUI via the OS accessibility API (AT-SPI / UIA / AX)."""

    @abstractmethod
    def ensure_ready(self) -> None: ...

    @abstractmethod
    def snapshot(self, *, force: bool = False) -> Any: ...

    @abstractmethod
    def get_element(self, element_id: str) -> Any: ...

    @abstractmethod
    def invalidate(self, reason: str = "manual") -> None: ...

    # Optional primitives used by the hybrid policy. Backends should override.
    def grab_focus(self, element_id: str) -> bool:
        return False

    def do_action(self, element_id: str, action: str | None = None) -> bool:
        return False

    def set_text(self, element_id: str, text: str) -> bool:
        return False

    def live_object(self, element_id: str):
        return None

    def peek_menu_open(self, element_id: str) -> bool:
        """Cheap check that a menu/button's popup is showing. No full tree walk."""
        el = self.get_element(element_id)
        return bool(el and "expanded" in (el.states or []))


class DesktopBackend(ABC):
    @abstractmethod
    def list_windows(self) -> list[WindowInfo]: ...

    @abstractmethod
    def focus_window(self, title_or_id: str) -> ActionResult: ...

    @abstractmethod
    def launch_app(self, command: str, args: Iterable[str] | None = None) -> ActionResult: ...

    @abstractmethod
    def resize_window(self, title_or_id: str, x: int, y: int, width: int, height: int) -> ActionResult: ...

    @abstractmethod
    def close_window(self, title_or_id: str) -> ActionResult: ...

    @abstractmethod
    def screen_size(self) -> tuple[int, int]: ...


class InputBackend(ABC):
    @abstractmethod
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> ActionResult: ...

    @abstractmethod
    def move(self, x: int, y: int) -> ActionResult: ...

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> ActionResult: ...

    @abstractmethod
    def type_text(self, text: str, *, clear: bool = False, press_enter: bool = False) -> ActionResult: ...

    @abstractmethod
    def shortcut(self, keys: str) -> ActionResult: ...

    @abstractmethod
    def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ActionResult: ...

    @abstractmethod
    def mouse_location(self) -> tuple[int, int]: ...
