"""Windows UI Automation accessibility backend.

Implemented against the COM UI Automation API via comtypes (optional:
`pip install 'linux-use[windows]'`). Event handlers subscribe to focus,
structure, and bounding-rectangle changes to dirty the incremental cache
the same way the Linux AT-SPI backend does.

UNTESTED on real Windows hardware: this module was written and unit-tested
with mocks on Linux. Do not treat it as a verified Windows port.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..tree import TreeSnapshot, UIElement
from .base import AccessibilityBackend

HARDWARE_TESTED = False
UNTESTED_NOTE = (
    "Windows UI Automation backend is implemented but untested on real hardware."
)

# UIA Control Type IDs -> linux-use role names (aligned with AT-SPI-ish labels)
CONTROL_TYPE_ROLES = {
    50000: "button",
    50001: "calendar",
    50002: "checkbox",
    50003: "combo box",
    50004: "text",       # Edit
    50005: "link",
    50006: "image",
    50007: "list item",
    50008: "list",
    50009: "menu",
    50010: "menu bar",
    50011: "menu item",
    50012: "progress bar",
    50013: "radio",
    50014: "scroll bar",
    50015: "slider",
    50016: "spin button",
    50017: "status bar",
    50018: "page tab list",
    50019: "tab",        # TabItem
    50020: "label",      # Text
    50021: "tool bar",
    50022: "tool tip",
    50023: "tree",
    50024: "tree item",
    50025: "custom",
    50026: "group",
    50027: "thumb",
    50028: "table",
    50029: "cell",
    50030: "document",
    50031: "split button",
    50032: "frame",      # Window
    50033: "filler",     # Pane
    50034: "header",
    50035: "header item",
    50036: "table",
    50037: "title bar",
    50038: "separator",
}

# Pattern IDs
PATTERN_INVOKE = 10000
PATTERN_VALUE = 10002
PATTERN_EXPAND = 10005
PATTERN_WINDOW = 10009
PATTERN_TEXT = 10014
PATTERN_TOGGLE = 10015
PATTERN_LEGACY = 10018


def role_for_control_type(control_type: int | None) -> str:
    if not control_type:
        return "unknown"
    return CONTROL_TYPE_ROLES.get(int(control_type), f"uia-{control_type}")


class WindowsAccessibility(AccessibilityBackend):
    """Walk the UIA tree, cache snapshots, invalidate on UIA events.

    `uia` may be injected (tests). On a real Windows host, `ensure_ready`
    creates a `CUIAutomation` COM object via comtypes.
    """

    HARDWARE_TESTED = HARDWARE_TESTED

    def __init__(self, uia: Any | None = None, *, max_nodes: int = 400):
        self._uia = uia
        self._started = False
        self._lock = threading.Lock()
        self._cache: TreeSnapshot | None = None
        self._elements: dict[str, UIElement] = {}
        self._objects: dict[str, Any] = {}
        self._runtime_to_id: dict[str, str] = {}
        self._next_id = 1
        self._generation = 0
        self._dirty_structural = False
        self._dirty_property = False
        self._pending: list[str] = []
        self._max_nodes = max_nodes
        self._handlers: list[Any] = []

    def ensure_ready(self) -> None:
        if self._started and self._uia is not None:
            return
        if self._uia is None:
            self._uia = self._create_uia()
        if not self._started:
            self._subscribe_events()
            self._started = True

    def _create_uia(self) -> Any:
        try:
            import comtypes  # noqa: F401
            import comtypes.client
        except ImportError as exc:
            raise RuntimeError(
                "Windows backend requires comtypes. "
                "Install with: pip install 'linux-use[windows]'. " + UNTESTED_NOTE
            ) from exc
        try:
            import comtypes.client
            # CUIAutomation CLSID
            uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=None,
            )
            return uia
        except Exception as exc:
            raise RuntimeError(
                f"Failed to create IUIAutomation COM object: {exc}. " + UNTESTED_NOTE
            ) from exc

    def _subscribe_events(self) -> None:
        """Register focus / structure / bounds handlers when the COM API is present."""
        uia = self._uia
        if uia is None or not hasattr(uia, "AddFocusChangedEventHandler"):
            return
        backend = self

        def _mark(kind: str) -> None:
            with backend._lock:
                backend._pending.append(kind)
                if kind.startswith("structure") or kind.startswith("window"):
                    backend._dirty_structural = True
                else:
                    backend._dirty_property = True

        # Handlers are constructed as simple callables if the injected UIA
        # supports a Pythonic API; real comtypes interfaces are wrapped below.
        try:
            class _Focus:
                def HandleFocusChangedEvent(self, sender):
                    _mark("focus-changed")

            class _Structure:
                def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
                    _mark("structure-changed")

            class _Property:
                def HandlePropertyChangedEvent(self, sender, propertyId, newValue):
                    _mark(f"property-changed:{propertyId}")

            focus_h, struct_h, prop_h = _Focus(), _Structure(), _Property()
            uia.AddFocusChangedEventHandler(None, focus_h)
            root = uia.GetRootElement() if hasattr(uia, "GetRootElement") else None
            if root is not None and hasattr(uia, "AddStructureChangedEventHandler"):
                uia.AddStructureChangedEventHandler(root, 1, None, struct_h)  # 1=Subtree
            if root is not None and hasattr(uia, "AddPropertyChangedEventHandler"):
                # 30001 Name, 30013 IsOffscreen, 30001? 30043 BoundingRectangle
                props = getattr(uia, "IntSafeArray", lambda xs: xs)([30001, 30013, 30043])
                try:
                    uia.AddPropertyChangedEventHandler(root, 1, None, prop_h, props)
                except Exception:
                    pass
            self._handlers.extend([focus_h, struct_h, prop_h])
        except Exception:
            # Event subscription is best-effort; snapshot still works without it.
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

    def _root_element(self) -> Any:
        if self._uia is None:
            return None
        if hasattr(self._uia, "GetRootElement"):
            return self._uia.GetRootElement()
        return getattr(self._uia, "root", None)

    def _runtime_key(self, raw: Any) -> str:
        getter = getattr(raw, "GetRuntimeId", None) or getattr(raw, "CurrentRuntimeId", None)
        try:
            rid = getter() if callable(getter) else getter
            if rid:
                if isinstance(rid, (list, tuple)):
                    return "r:" + "-".join(str(x) for x in rid)
                return "r:" + str(rid)
        except Exception:
            pass
        # Fallback identity: id() is stable for the duration of the COM proxy.
        return "p:" + str(id(raw))

    def _alloc_id(self, key: str) -> str:
        if key in self._runtime_to_id:
            return self._runtime_to_id[key]
        eid = f"e{self._next_id}"
        self._next_id += 1
        self._runtime_to_id[key] = eid
        return eid

    def _read_element(self, raw: Any, eid: str, parent_id: str | None, app: str) -> UIElement:
        def _attr(names, default=""):
            for n in names:
                try:
                    v = getattr(raw, n)
                    if callable(v):
                        v = v()
                    if v is not None and v != "":
                        return v
                except Exception:
                    continue
            return default

        name = str(_attr(("CurrentName", "Name", "name"), "") or "")
        ctype = _attr(("CurrentControlType", "ControlType", "control_type"), 0)
        try:
            ctype = int(ctype or 0)
        except Exception:
            ctype = 0
        role = role_for_control_type(ctype)
        rect = _attr(("CurrentBoundingRectangle", "BoundingRectangle", "bounding_rectangle"), None)
        x = y = w = h = 0
        if rect is not None:
            try:
                # comtypes RECT, tuple, or object with left/top/width/height
                if isinstance(rect, (tuple, list)) and len(rect) >= 4:
                    x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
                    # Some APIs return left, top, right, bottom
                    if w > 10000 or (hasattr(rect, "right") is False and w > x and h > y and w > 200):
                        pass
                    if w >= x and h >= y and w > 50 and not hasattr(rect, "width"):
                        # likely l,t,r,b
                        w, h = int(rect[2]) - x, int(rect[3]) - y
                else:
                    left = int(getattr(rect, "left", getattr(rect, "Left", 0)) or 0)
                    top = int(getattr(rect, "top", getattr(rect, "Top", 0)) or 0)
                    if hasattr(rect, "width") or hasattr(rect, "Width"):
                        x, y = left, top
                        w = int(getattr(rect, "width", getattr(rect, "Width", 0)) or 0)
                        h = int(getattr(rect, "height", getattr(rect, "Height", 0)) or 0)
                    else:
                        right = int(getattr(rect, "right", getattr(rect, "Right", left)) or left)
                        bottom = int(getattr(rect, "bottom", getattr(rect, "Bottom", top)) or top)
                        x, y, w, h = left, top, right - left, bottom - top
            except Exception:
                x = y = w = h = 0
        states: list[str] = []
        if _truth(_attr(("CurrentIsEnabled", "IsEnabled", "is_enabled"), True)):
            states.append("enabled")
        else:
            states.append("sensitive" if False else "insensitive")
        if not _truth(_attr(("CurrentIsOffscreen", "IsOffscreen", "is_offscreen"), False)):
            states.append("showing")
            states.append("visible")
        if _truth(_attr(("CurrentHasKeyboardFocus", "HasKeyboardFocus", "has_keyboard_focus"), False)):
            states.append("focused")
        if role in {"text", "document", "edit"}:
            states.append("editable")
        expand = self._pattern(raw, PATTERN_EXPAND)
        if expand is not None:
            state = _attr_on(expand, ("CurrentExpandCollapseState", "ExpandCollapseState"), 0)
            # 0=collapsed, 1=expanded, 2=partially, 3=leaf
            try:
                if int(state) == 1:
                    states.append("expanded")
            except Exception:
                pass
        actions: list[str] = []
        if self._pattern(raw, PATTERN_INVOKE) is not None:
            actions.append("click")
        if expand is not None:
            actions.append("expand")
        text = ""
        value = ""
        vp = self._pattern(raw, PATTERN_VALUE)
        if vp is not None:
            value = str(_attr_on(vp, ("CurrentValue", "Value"), "") or "")
            text = value
        tp = self._pattern(raw, PATTERN_TEXT)
        if tp is not None and not text:
            try:
                rng = tp.DocumentRange if not callable(getattr(tp, "DocumentRange", None)) else tp.DocumentRange()
                if rng is not None and hasattr(rng, "GetText"):
                    text = str(rng.GetText(-1) or "")
            except Exception:
                pass
        if role in {"text", "edit"} and not text:
            text = value
        return UIElement(
            id=eid, role=role, name=name, path=eid, app=app or "",
            x=int(x), y=int(y), width=int(w), height=int(h),
            states=states, text=text[:2000], actions=actions, value=str(value)[:500],
            parent_id=parent_id, child_ids=[],
        )

    def _pattern(self, raw: Any, pattern_id: int) -> Any:
        getter = getattr(raw, "GetCurrentPattern", None) or getattr(raw, "GetPattern", None)
        if not callable(getter):
            patterns = getattr(raw, "patterns", None)
            if isinstance(patterns, dict):
                return patterns.get(pattern_id)
            return None
        try:
            return getter(pattern_id)
        except Exception:
            return None

    def _children(self, raw: Any) -> list[Any]:
        for name in ("GetCachedChildren", "GetChildren", "children"):
            attr = getattr(raw, name, None)
            try:
                if callable(attr):
                    val = attr()
                else:
                    val = attr
                if val is None:
                    continue
                return list(val)
            except Exception:
                continue
        # UIA FindAll / raw view walker
        try:
            if self._uia is not None and hasattr(self._uia, "RawViewWalker"):
                walker = self._uia.RawViewWalker
                child = walker.GetFirstChildElement(raw)
                out = []
                while child is not None:
                    out.append(child)
                    child = walker.GetNextSiblingElement(child)
                return out
        except Exception:
            pass
        return []

    def _full_scan(self) -> TreeSnapshot:
        root = self._root_element()
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
            key = self._runtime_key(raw)
            eid = self._alloc_id(key)
            proc = ""
            try:
                proc = str(getattr(raw, "CurrentProcessId", getattr(raw, "ProcessId", "")) or "")
            except Exception:
                proc = ""
            el_app = app or proc
            el = self._read_element(raw, eid, parent_id, el_app)
            elements[eid] = el
            objects[eid] = raw
            if parent_id is None:
                roots.append(eid)
            elif parent_id in elements:
                elements[parent_id].child_ids.append(eid)
            if el.focused:
                focused_id = eid
            for ch in self._children(raw):
                walk(ch, eid, el_app or el.app, depth + 1)

        if root is not None:
            walk(root, None, "", 0)
        self._generation += 1
        self._objects = objects
        snap = TreeSnapshot(
            elements=elements, roots=roots, focused_id=focused_id,
            active_app=(elements[roots[0]].app if roots else None),
            generation=self._generation, cache_hit=False, refresh_mode="full",
            truncated=truncated, node_count=len(elements), walked=walked,
        )
        return snap

    def _incremental_refresh(self) -> TreeSnapshot:
        # Re-read cached objects in place; fall back to a full scan if a proxy died.
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
            if self._cache:
                return self._cache.elements.get(element_id)
            return None
        prev = (self._cache.elements.get(element_id) if self._cache else None)
        try:
            el = self._read_element(raw, element_id, prev.parent_id if prev else None, prev.app if prev else "")
            if prev is not None:
                el.child_ids = list(prev.child_ids)
            if self._cache:
                self._cache.elements[element_id] = el
            self._elements[element_id] = el
            return el
        except Exception:
            return prev

    def live_object(self, element_id: str):
        return self._objects.get(element_id)

    def grab_focus(self, element_id: str) -> bool:
        raw = self._objects.get(element_id)
        if raw is None:
            return False
        try:
            if hasattr(raw, "SetFocus"):
                raw.SetFocus()
                return True
        except Exception:
            return False
        return False

    def do_action(self, element_id: str, action: str | None = None) -> bool:
        raw = self._objects.get(element_id)
        if raw is None:
            return False
        action = (action or "click").lower()
        try:
            if action in {"click", "invoke", "press"}:
                pat = self._pattern(raw, PATTERN_INVOKE)
                if pat is not None and hasattr(pat, "Invoke"):
                    pat.Invoke()
                    return True
            if action in {"expand", "open"}:
                pat = self._pattern(raw, PATTERN_EXPAND)
                if pat is not None and hasattr(pat, "Expand"):
                    pat.Expand()
                    return True
            if action in {"collapse", "close"}:
                pat = self._pattern(raw, PATTERN_EXPAND)
                if pat is not None and hasattr(pat, "Collapse"):
                    pat.Collapse()
                    return True
            if action in {"toggle"}:
                pat = self._pattern(raw, PATTERN_TOGGLE)
                if pat is not None and hasattr(pat, "Toggle"):
                    pat.Toggle()
                    return True
        except Exception:
            return False
        return False

    def set_text(self, element_id: str, text: str) -> bool:
        raw = self._objects.get(element_id)
        if raw is None:
            return False
        try:
            pat = self._pattern(raw, PATTERN_VALUE)
            if pat is not None and hasattr(pat, "SetValue"):
                pat.SetValue(str(text))
                return True
            # Text pattern range
            tp = self._pattern(raw, PATTERN_TEXT)
            if tp is not None:
                rng = getattr(tp, "DocumentRange", None)
                rng = rng() if callable(rng) else rng
                if rng is not None and hasattr(rng, "GetText"):
                    # Some UIA clients expose IUIAutomationValuePattern only.
                    pass
        except Exception:
            return False
        return False

    def peek_menu_open(self, element_id: str) -> bool:
        el = self.get_element(element_id)
        if el and "expanded" in (el.states or []):
            return True
        raw = self._objects.get(element_id)
        if raw is None:
            return False
        pat = self._pattern(raw, PATTERN_EXPAND)
        if pat is not None:
            state = _attr_on(pat, ("CurrentExpandCollapseState", "ExpandCollapseState"), 0)
            try:
                return int(state) == 1
            except Exception:
                return False
        # Showing child with valid bounds
        if el:
            for cid in (el.child_ids or [])[:8]:
                ch = self.get_element(cid)
                if ch and ch.showing and ch.bounds_valid:
                    return True
        return False


def _truth(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v in (0, "0", None, "", "False", "false"):
        return False
    return bool(v)


def _attr_on(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    for n in names:
        try:
            v = getattr(obj, n)
            if callable(v):
                v = v()
            if v is not None:
                return v
        except Exception:
            continue
    return default
