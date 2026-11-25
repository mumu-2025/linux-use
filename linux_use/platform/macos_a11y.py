"""macOS Accessibility (AX) backend via pyobjc ApplicationServices.

Uses AXUIElement to walk the tree and AXObserver notifications to dirty
the incremental cache (value / focus / window create-destroy / move-resize).

UNTESTED on real macOS hardware: written and unit-tested with mocks on Linux.
Requires Accessibility permission for the hosting process.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..tree import TreeSnapshot, UIElement
from .base import AccessibilityBackend

HARDWARE_TESTED = False
UNTESTED_NOTE = (
    "macOS Accessibility (AX) backend is implemented but untested on real hardware."
)

# AXRole -> linux-use role
AX_ROLES = {
    "AXButton": "button",
    "AXCheckBox": "checkbox",
    "AXRadioButton": "radio",
    "AXPopUpButton": "combo box",
    "AXComboBox": "combo box",
    "AXTextField": "text",
    "AXTextArea": "text",
    "AXStaticText": "label",
    "AXLink": "link",
    "AXImage": "image",
    "AXList": "list",
    "AXMenu": "menu",
    "AXMenuBar": "menu bar",
    "AXMenuItem": "menu item",
    "AXMenuBarItem": "menu",
    "AXProgressIndicator": "progress bar",
    "AXSlider": "slider",
    "AXIncrementor": "spin button",
    "AXScrollBar": "scroll bar",
    "AXGroup": "group",
    "AXSplitGroup": "group",
    "AXTabGroup": "page tab list",
    "AXRadioGroup": "group",
    "AXToolbar": "tool bar",
    "AXTable": "table",
    "AXOutline": "tree",
    "AXRow": "list item",
    "AXCell": "cell",
    "AXColumn": "column",
    "AXWindow": "frame",
    "AXSheet": "frame",
    "AXDrawer": "frame",
    "AXDialog": "frame",
    "AXApplication": "application",
    "AXWebArea": "document",
    "AXHeading": "heading",
    "AXScrollArea": "scroll pane",
    "AXPopOver": "frame",
    "AXTab": "tab",
    "AXColorWell": "color well",
}


def role_for_ax(ax_role: str | None) -> str:
    if not ax_role:
        return "unknown"
    return AX_ROLES.get(ax_role, ax_role.replace("AX", "").lower() or "unknown")


class MacAccessibility(AccessibilityBackend):
    """Walk the AX tree and cache snapshots. `ax` may be injected for tests."""

    HARDWARE_TESTED = HARDWARE_TESTED

    def __init__(self, ax: Any | None = None, *, max_nodes: int = 400):
        self._ax = ax  # injected AX client
        self._started = False
        self._lock = threading.Lock()
        self._cache: TreeSnapshot | None = None
        self._elements: dict[str, UIElement] = {}
        self._objects: dict[str, Any] = {}
        self._ref_to_id: dict[str, str] = {}
        self._next_id = 1
        self._generation = 0
        self._dirty_structural = False
        self._dirty_property = False
        self._pending: list[str] = []
        self._max_nodes = max_nodes
        self._observers: list[Any] = []

    def ensure_ready(self) -> None:
        if self._started and self._ax is not None:
            return
        if self._ax is None:
            self._ax = self._create_ax()
        if not self._started:
            self._subscribe_events()
            self._started = True

    def _create_ax(self) -> Any:
        try:
            from ApplicationServices import (  # type: ignore
                AXIsProcessTrusted,
                AXIsProcessTrustedWithOptions,
                AXUIElementCreateSystemWide,
                kAXTrustedCheckOptionPrompt,
            )
        except ImportError as exc:
            raise RuntimeError(
                "macOS backend requires pyobjc ApplicationServices. "
                "Install with: pip install 'linux-use[macos]'. " + UNTESTED_NOTE
            ) from exc
        try:
            trusted = bool(AXIsProcessTrusted())
            if not trusted:
                try:
                    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
                except Exception:
                    pass
                trusted = bool(AXIsProcessTrusted())
            if not trusted:
                raise RuntimeError(
                    "This process is not trusted for Accessibility. "
                    "Enable it in System Settings > Privacy & Security > Accessibility. "
                    + UNTESTED_NOTE
                )
            return _LiveAxClient()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize AX: {exc}. " + UNTESTED_NOTE) from exc

    def _subscribe_events(self) -> None:
        ax = self._ax
        if ax is None or not hasattr(ax, "add_observer"):
            return
        backend = self

        def _cb(notification: str, _src=None) -> None:
            with backend._lock:
                backend._pending.append(str(notification))
                low = str(notification).lower()
                if any(s in low for s in ("created", "destroyed", "window")):
                    backend._dirty_structural = True
                else:
                    backend._dirty_property = True

        try:
            ax.add_observer(_cb)
        except Exception:
            with self._lock:
                self._dirty_property = True

    def invalidate(self, reason: str = "manual") -> None:
        with self._lock:
            self._dirty_structural = True
            self._pending.append(f"manual:{reason}")

    def snapshot(self, *, force: bool = False) -> TreeSnapshot:
        self.ensure_ready()
        t0 = time.perf_counter()
        with self._lock:
            pending = list(self._pending)
            self._pending = []
            structural = self._dirty_structural
            prop = self._dirty_property
            self._dirty_structural = False
            self._dirty_property = False
        if force or self._cache is None or structural:
            snap = self._full_scan()
            snap.refresh_mode = "full"
            snap.cache_hit = False
        elif prop and self._cache is not None:
            snap = self._incremental_refresh()
            snap.refresh_mode = "incremental"
            snap.cache_hit = False
        else:
            snap = self._cache
            snap = TreeSnapshot(
                elements=snap.elements, roots=snap.roots, focused_id=snap.focused_id,
                active_app=snap.active_app, generation=snap.generation, cache_hit=True,
                refresh_mode="cache", events=pending, node_count=snap.node_count, walked=0,
            )
        snap.events = pending
        snap.observe_ms = (time.perf_counter() - t0) * 1000.0
        self._cache = snap
        self._elements = snap.elements
        return snap

    def _alloc_id(self, key: str) -> str:
        if key in self._ref_to_id:
            return self._ref_to_id[key]
        eid = f"e{self._next_id}"
        self._next_id += 1
        self._ref_to_id[key] = eid
        return eid

    def _key(self, raw: Any) -> str:
        ident = getattr(raw, "identifier", None)
        if ident:
            return str(ident)
        return "p:" + str(id(raw))

    def _read_element(self, raw: Any, eid: str, parent_id: str | None, app: str) -> UIElement:
        ax = self._ax
        def get(attr, default=None):
            if ax is not None and hasattr(ax, "get_attr"):
                try:
                    v = ax.get_attr(raw, attr)
                    return default if v is None else v
                except Exception:
                    return default
            try:
                v = getattr(raw, attr, default)
                return v() if callable(v) else v
            except Exception:
                return default

        ax_role = get("AXRole") or get("role") or ""
        role = role_for_ax(str(ax_role) if ax_role else "")
        name = str(get("AXTitle") or get("AXDescription") or get("name") or "")
        pos = get("AXPosition") or get("position")
        size = get("AXSize") or get("size")
        x = y = w = h = 0
        if pos is not None:
            x = int(getattr(pos, "x", pos[0] if isinstance(pos, (tuple, list)) else 0) or 0)
            y = int(getattr(pos, "y", pos[1] if isinstance(pos, (tuple, list)) else 0) or 0)
        if size is not None:
            w = int(getattr(size, "width", size[0] if isinstance(size, (tuple, list)) else 0) or 0)
            h = int(getattr(size, "height", size[1] if isinstance(size, (tuple, list)) else 0) or 0)
        states: list[str] = []
        if get("AXEnabled", True) not in (False, 0, "false"):
            states.append("enabled")
        else:
            states.append("insensitive")
        if get("AXHidden", False) not in (True, 1, "true"):
            states.append("showing")
            states.append("visible")
        if get("AXFocused", False) in (True, 1, "true"):
            states.append("focused")
        if get("AXExpanded", False) in (True, 1, "true"):
            states.append("expanded")
        if role in {"text"}:
            states.append("editable")
        actions: list[str] = []
        acts = get("AXActions") or get("actions") or []
        try:
            acts = list(acts)
        except Exception:
            acts = []
        for a in acts:
            low = str(a).replace("AX", "").lower()
            if low in {"press", "click"}:
                actions.append("click")
            else:
                actions.append(low)
        if not actions and role in {"button", "menu", "menu item", "tab", "link", "checkbox"}:
            actions.append("click")
        text = str(get("AXValue") or get("value") or get("AXSelectedText") or "")
        return UIElement(
            id=eid, role=role, name=name, path=eid, app=app or "",
            x=x, y=y, width=w, height=h, states=states,
            text=text[:2000], actions=actions, value=text[:500],
            parent_id=parent_id, child_ids=[],
        )

    def _children(self, raw: Any) -> list[Any]:
        ax = self._ax
        if ax is not None and hasattr(ax, "get_attr"):
            try:
                ch = ax.get_attr(raw, "AXChildren") or []
                return list(ch)
            except Exception:
                return []
        ch = getattr(raw, "children", None) or getattr(raw, "AXChildren", None) or []
        try:
            return list(ch() if callable(ch) else ch)
        except Exception:
            return []

    def _full_scan(self) -> TreeSnapshot:
        ax = self._ax
        roots_raw = []
        if ax is not None and hasattr(ax, "applications"):
            roots_raw = list(ax.applications() or [])
        elif ax is not None and hasattr(ax, "system_wide"):
            sw = ax.system_wide()
            roots_raw = self._children(sw) or [sw]
        elements: dict[str, UIElement] = {}
        objects: dict[str, Any] = {}
        roots: list[str] = []
        focused_id = None
        walked = 0
        truncated = False

        def walk(raw, parent_id, app, depth):
            nonlocal walked, truncated, focused_id
            if raw is None or walked >= self._max_nodes:
                truncated = walked >= self._max_nodes
                return
            walked += 1
            eid = self._alloc_id(self._key(raw))
            # Application name
            el_app = app
            if not el_app:
                axc = self._ax
                if axc is not None and hasattr(axc, "get_attr"):
                    el_app = str(axc.get_attr(raw, "AXTitle") or axc.get_attr(raw, "AXApplication") or "")
            el = self._read_element(raw, eid, parent_id, el_app or "")
            elements[eid] = el
            objects[eid] = raw
            if parent_id is None:
                roots.append(eid)
            elif parent_id in elements:
                elements[parent_id].child_ids.append(eid)
            if el.focused:
                focused_id = eid
            for ch in self._children(raw):
                walk(ch, eid, el.app or el_app, depth + 1)

        for raw in roots_raw:
            walk(raw, None, "", 0)
        self._generation += 1
        self._objects = objects
        return TreeSnapshot(
            elements=elements, roots=roots, focused_id=focused_id,
            active_app=(elements[roots[0]].name if roots and elements[roots[0]].role == "application" else (elements[roots[0]].app if roots else None)),
            generation=self._generation, cache_hit=False, refresh_mode="full",
            truncated=truncated, node_count=len(elements), walked=walked,
        )

    def _incremental_refresh(self) -> TreeSnapshot:
        if not self._cache or not self._objects:
            return self._full_scan()
        walked = 0
        focused_id = self._cache.focused_id
        elements = dict(self._cache.elements)
        try:
            for eid, raw in list(self._objects.items()):
                walked += 1
                prev = elements.get(eid)
                el = self._read_element(raw, eid, prev.parent_id if prev else None, prev.app if prev else "")
                if prev is not None:
                    el.child_ids = list(prev.child_ids)
                    el.path = prev.path
                elements[eid] = el
                if el.focused:
                    focused_id = eid
        except Exception:
            return self._full_scan()
        self._generation += 1
        return TreeSnapshot(
            elements=elements, roots=list(self._cache.roots), focused_id=focused_id,
            active_app=self._cache.active_app, generation=self._generation,
            cache_hit=False, refresh_mode="incremental",
            node_count=len(elements), walked=walked,
        )

    def get_element(self, element_id: str) -> UIElement | None:
        raw = self._objects.get(element_id)
        if raw is None:
            return self._cache.elements.get(element_id) if self._cache else None
        prev = self._cache.elements.get(element_id) if self._cache else None
        try:
            el = self._read_element(raw, element_id, prev.parent_id if prev else None, prev.app if prev else "")
            if prev is not None:
                el.child_ids = list(prev.child_ids)
            if self._cache:
                self._cache.elements[element_id] = el
            return el
        except Exception:
            return prev

    def live_object(self, element_id: str):
        return self._objects.get(element_id)

    def grab_focus(self, element_id: str) -> bool:
        raw = self._objects.get(element_id)
        if raw is None or self._ax is None:
            return False
        try:
            if hasattr(self._ax, "set_attr"):
                self._ax.set_attr(raw, "AXFocused", True)
                return True
        except Exception:
            return False
        return False

    def do_action(self, element_id: str, action: str | None = None) -> bool:
        raw = self._objects.get(element_id)
        if raw is None or self._ax is None:
            return False
        action = (action or "click").lower()
        ax_action = {
            "click": "AXPress", "press": "AXPress", "invoke": "AXPress",
            "confirm": "AXConfirm", "cancel": "AXCancel",
            "raise": "AXRaise", "showmenu": "AXShowMenu", "show-menu": "AXShowMenu",
        }.get(action, "AX" + action[:1].upper() + action[1:] if not action.startswith("ax") else action)
        try:
            if hasattr(self._ax, "perform"):
                return bool(self._ax.perform(raw, ax_action))
        except Exception:
            return False
        return False

    def set_text(self, element_id: str, text: str) -> bool:
        raw = self._objects.get(element_id)
        if raw is None or self._ax is None:
            return False
        try:
            if hasattr(self._ax, "set_attr"):
                self._ax.set_attr(raw, "AXValue", str(text))
                return True
        except Exception:
            return False
        return False

    def peek_menu_open(self, element_id: str) -> bool:
        el = self.get_element(element_id)
        if el and "expanded" in (el.states or []):
            return True
        raw = self._objects.get(element_id)
        if raw is not None and self._ax is not None and hasattr(self._ax, "get_attr"):
            try:
                if self._ax.get_attr(raw, "AXExpanded") in (True, 1, "true"):
                    return True
            except Exception:
                pass
        if el:
            for cid in (el.child_ids or [])[:8]:
                ch = self.get_element(cid)
                if ch and ch.showing and ch.bounds_valid:
                    return True
        return False


class _LiveAxClient:
    """Real pyobjc AX client. Only constructed on a trusted macOS process."""

    def __init__(self):
        from ApplicationServices import (  # type: ignore
            AXObserverAddNotification,
            AXObserverCreate,
            AXObserverGetRunLoopSource,
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementCreateSystemWide,
            AXUIElementPerformAction,
            AXUIElementSetAttributeValue,
            AXValueGetValue,
            kAXCreatedNotification,
            kAXFocusedUIElementChangedNotification,
            kAXFocusedWindowChangedNotification,
            kAXMovedNotification,
            kAXResizedNotification,
            kAXUIElementDestroyedNotification,
            kAXValueCGPointType,
            kAXValueCGSizeType,
            kAXValueChangedNotification,
            kAXWindowCreatedNotification,
        )
        import Cocoa  # type: ignore
        from CoreFoundation import CFRunLoopAddSource, CFRunLoopGetCurrent, kCFRunLoopDefaultMode  # type: ignore

        self._AXUIElementCopyAttributeValue = AXUIElementCopyAttributeValue
        self._AXUIElementSetAttributeValue = AXUIElementSetAttributeValue
        self._AXUIElementPerformAction = AXUIElementPerformAction
        self._AXUIElementCreateApplication = AXUIElementCreateApplication
        self._system = AXUIElementCreateSystemWide()
        self._AXValueGetValue = AXValueGetValue
        self._kPoint = kAXValueCGPointType
        self._kSize = kAXValueCGSizeType
        self._Cocoa = Cocoa
        self._obs_cb = None
        self._observers = []
        self._notif = [
            kAXFocusedUIElementChangedNotification,
            kAXFocusedWindowChangedNotification,
            kAXValueChangedNotification,
            kAXUIElementDestroyedNotification,
            kAXWindowCreatedNotification,
            kAXCreatedNotification,
            kAXMovedNotification,
            kAXResizedNotification,
        ]
        self._AXObserverCreate = AXObserverCreate
        self._AXObserverAddNotification = AXObserverAddNotification
        self._AXObserverGetRunLoopSource = AXObserverGetRunLoopSource
        self._CFRunLoopAddSource = CFRunLoopAddSource
        self._CFRunLoopGetCurrent = CFRunLoopGetCurrent
        self._kCFRunLoopDefaultMode = kCFRunLoopDefaultMode

    def system_wide(self):
        return self._system

    def applications(self):
        ws = self._Cocoa.NSWorkspace.sharedWorkspace()
        apps = []
        for app in ws.runningApplications():
            try:
                pid = int(app.processIdentifier())
                if pid <= 0:
                    continue
                apps.append(self._AXUIElementCreateApplication(pid))
            except Exception:
                continue
        return apps

    def get_attr(self, element, name: str):
        err, value = self._AXUIElementCopyAttributeValue(element, name, None)
        # pyobjc may return (err, value) or just value depending on version
        if isinstance(err, tuple) or not isinstance(value, (type(None),)) and err not in (0, None):
            # Some bindings return the value as the only result; others use *err*
            pass
        if value is None and err not in (0, None) and not hasattr(err, "x"):
            # If the API returned only the value in `err`
            if err not in (0, None) and not isinstance(err, int):
                value = err
        value = self._unwrap(value)
        return value

    def _unwrap(self, value):
        if value is None:
            return None
        # AXValue CGPoint / CGSize
        try:
            ok, point = self._AXValueGetValue(value, self._kPoint, None)
            if ok or (hasattr(point, "x")):
                return point
        except Exception:
            pass
        try:
            ok, size = self._AXValueGetValue(value, self._kSize, None)
            if ok or (hasattr(size, "width")):
                return size
        except Exception:
            pass
        return value

    def set_attr(self, element, name: str, value) -> bool:
        err = self._AXUIElementSetAttributeValue(element, name, value)
        return err in (0, None, True)

    def perform(self, element, action: str) -> bool:
        err = self._AXUIElementPerformAction(element, action)
        return err in (0, None, True)

    def add_observer(self, callback) -> None:
        # Observer is per-pid; attach to running apps best-effort.
        self._obs_cb = callback

        def _py_cb(observer, element, notification, refcon):
            try:
                callback(str(notification), element)
            except Exception:
                pass

        for app in self.applications()[:16]:
            try:
                from ApplicationServices import AXUIElementGetPid  # type: ignore
                err, pid = AXUIElementGetPid(app, None)
                if not pid:
                    continue
                obs, src_err = None, None
                # AXObserverCreate(pid, cb, out)
                result = self._AXObserverCreate(int(pid), _py_cb, None)
                observer = result[1] if isinstance(result, tuple) else result
                if observer is None:
                    continue
                for notif in self._notif:
                    try:
                        self._AXObserverAddNotification(observer, app, notif, None)
                    except Exception:
                        continue
                src = self._AXObserverGetRunLoopSource(observer)
                self._CFRunLoopAddSource(self._CFRunLoopGetCurrent(), src, self._kCFRunLoopDefaultMode)
                self._observers.append(observer)
            except Exception:
                continue
