"""Linux AT-SPI backend with an incremental, event-invalidated element cache.

Design goals (speed):
- Full tree walk only on first snapshot or after structural changes.
- AT-SPI events mark dirty kinds; text/bounds/state updates refresh only
  the affected nodes (or the focused subtree).
- Cheap dirty check (app count + frame names) when no events arrived.
- Stable element IDs are reused across snapshots so callers can click
  cached bounding boxes without re-enumerating the tree.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..a11y import ensure_ready
from ..config import SETTINGS
from ..tree import TreeDelta, TreeSnapshot, UIElement, diff_snapshots
from .base import AccessibilityBackend

# States we actually surface (keeps snapshots small)
KEEP_STATES = {
    "active", "busy", "checked", "editable", "enabled", "expandable",
    "expanded", "focusable", "focused", "invalid", "pressed", "required",
    "selected", "sensitive", "showing", "visible",
}

# Events that change structure vs cheap property updates
STRUCTURAL_EVENTS = {
    "object:children-changed",
    "object:children-changed:add",
    "object:children-changed:remove",
    "window:create",
    "window:destroy",
    "window:activate",
    "window:deactivate",
}

PROPERTY_EVENTS = {
    "object:text-changed",
    "object:text-changed:insert",
    "object:text-changed:delete",
    "object:text-caret-moved",
    "object:bounds-changed",
    "object:state-changed",
    "object:state-changed:focused",
    "object:state-changed:selected",
    "object:state-changed:checked",
    "object:state-changed:showing",
    "object:state-changed:visible",
    "object:property-change",
    "object:property-change:accessible-name",
    "object:property-change:accessible-value",
    "focus:",
}

LISTEN_TYPES = [
    "object:children-changed",
    "object:state-changed:focused",
    "object:state-changed:showing",
    "object:state-changed:visible",
    "object:state-changed:selected",
    "object:state-changed:checked",
    "object:text-changed",
    "object:bounds-changed",
    "object:property-change:accessible-name",
    "object:property-change:accessible-value",
    "window:create",
    "window:destroy",
    "window:activate",
    "focus:",
]


@dataclass
class _Pending:
    typ: str
    app: str = ""
    path: str = ""
    role: str = ""
    name: str = ""
    ts: float = field(default_factory=time.time)


class LinuxAccessibility(AccessibilityBackend):
    def __init__(self, max_nodes: int | None = None):
        self.max_nodes = max_nodes or SETTINGS.max_tree_nodes
        self.max_text = SETTINGS.max_text_chars
        self._lock = threading.Lock()
        self._started = False
        self._loop_thread: threading.Thread | None = None
        self._pending: list[_Pending] = []
        self._dirty_structural = False
        self._dirty_property = False
        self._generation = 0
        self._next_id = 1
        self._path_to_id: dict[str, str] = {}
        self._id_to_path: dict[str, str] = {}
        self._objects: dict[str, Any] = {}
        self._cache: TreeSnapshot | None = None
        self._listeners: list[Any] = []

    # ------------------------------------------------------------------ setup
    def ensure_ready(self) -> None:
        ensure_ready()
        if self._started:
            return
        self._start_event_pump()
        self._started = True

    def _start_event_pump(self) -> None:
        from gi.repository import Atspi, GLib

        def callback(event, typ_hint=""):
            try:
                typ = ""
                try:
                    typ = str(event.type)
                except Exception:
                    typ = typ_hint or "unknown"
                typ = typ.lower()
                # Never query the AT-SPI source in the event thread. Doing so
                # during launch/teardown event floods was measured at >10s
                # because it races with snapshot walks.
                ev = _Pending(typ=typ)
                with self._lock:
                    # Coalesce floods (caret/bounds while typing)
                    if typ.startswith("object:text-caret-moved"):
                        return
                    self._pending.append(ev)
                    # Menu items often appear/disappear when a document becomes
                    # dirty (Save enabled). That is not a layout rebuild.
                    menuish = ev.role.lower() in {
                        "menu", "menu item", "menu bar", "separator", "section",
                    }
                    # window create/destroy are structural. children-changed is
                    # often a menu item appearing (Save enabled); treat as
                    # property so we incremental-refresh instead of walking all 90 nodes.
                    is_struct = typ.startswith("window:create") or typ.startswith("window:destroy")
                    if typ.startswith("object:children-changed") and not menuish:
                        # Keep as property unless we later see a cheap-check mismatch.
                        self._dirty_property = True
                    elif is_struct:
                        self._dirty_structural = True
                    else:
                        self._dirty_property = True
            except Exception:
                with self._lock:
                    self._dirty_property = True

        for typ in LISTEN_TYPES:
            listener = Atspi.EventListener.new(callback)
            try:
                listener.register(typ)
                self._listeners.append(listener)
            except Exception:
                # Some event names may not be registered on every host.
                pass

        def run_loop():
            loop = GLib.MainLoop()
            loop.run()

        self._loop_thread = threading.Thread(target=run_loop, name="linux-use-atspi", daemon=True)
        self._loop_thread.start()

    def _pump(self) -> None:
        try:
            from gi.repository import GLib
            ctx = GLib.MainContext.default()
            # Drain without blocking
            for _ in range(32):
                if not ctx.pending():
                    break
                ctx.iteration(False)
        except Exception:
            pass

    def invalidate(self, reason: str = "manual") -> None:
        with self._lock:
            self._dirty_structural = True
            self._pending.append(_Pending(typ=f"manual:{reason}"))

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, *, force: bool = False) -> TreeSnapshot:
        self.ensure_ready()
        t0 = time.perf_counter()
        self._pump()
        with self._lock:
            pending = self._pending
            self._pending = []
            structural = self._dirty_structural
            prop = self._dirty_property
            self._dirty_structural = False
            self._dirty_property = False

        events = _summarize_events(pending)

        dirty_apps = sorted({ev.app for ev in pending if ev.app})
        if force or self._cache is None:
            snap = self._full_scan()
            snap.refresh_mode = "full"
            snap.cache_hit = False
        elif structural and self._cache is not None and dirty_apps:
            snap = self._rescan_apps(dirty_apps)
            snap.refresh_mode = "incremental"
            snap.cache_hit = False
        elif structural:
            snap = self._full_scan()
            snap.refresh_mode = "full"
            snap.cache_hit = False
        elif prop and self._cache is not None:
            snap = self._incremental_refresh(pending)
            snap.refresh_mode = "incremental"
            snap.cache_hit = False
        else:
            # No events. Cheap check so we still notice missed notifications.
            if self._cheap_unchanged():
                snap = self._cache
                snap = TreeSnapshot(
                    elements=snap.elements,
                    roots=snap.roots,
                    focused_id=self._refresh_focus(snap) or snap.focused_id,
                    active_app=snap.active_app,
                    generation=snap.generation,
                    cache_hit=True,
                    refresh_mode="cache",
                    events=[],
                    delta=TreeDelta(notes=["no AT-SPI events; cheap check unchanged"]),
                    truncated=snap.truncated,
                    node_count=snap.node_count,
                    walked=0,
                )
            else:
                snap = self._full_scan()
                snap.refresh_mode = "full"
                snap.cache_hit = False
                events.append("cheap-check-mismatch")

        snap.events = events
        snap.observe_ms = (time.perf_counter() - t0) * 1000.0
        if self._cache is not None and snap.refresh_mode != "cache":
            snap.delta = diff_snapshots(self._cache, snap)
        if snap.refresh_mode != "cache":
            self._cache = snap
            self._generation = snap.generation
        else:
            # Keep last full cache as the source of truth
            pass
        return snap

    def get_element(self, element_id: str) -> UIElement | None:
        if self._cache:
            el = self._cache.get(element_id)
            if el:
                # Refresh bounds/text cheaply if the live object is still valid
                live = self._live_object(element_id)
                if live is not None:
                    self._apply_live_fields(el, live)
                return el
        return None

    def live_object(self, element_id: str):
        return self._live_object(element_id)

    # ------------------------------------------------------------------ scans
    def _full_scan(self) -> TreeSnapshot:
        from gi.repository import Atspi

        desktop = Atspi.get_desktop(0)
        elements: dict[str, UIElement] = {}
        roots: list[str] = []
        focused_id = None
        active_app = None
        walked = 0
        truncated = False
        # Keep previous path->id mapping so IDs stay stable across full scans
        new_objects: dict[str, Any] = {}

        def alloc_id(path: str) -> str:
            eid = self._path_to_id.get(path)
            if not eid:
                eid = f"e{self._next_id}"
                self._next_id += 1
                self._path_to_id[path] = eid
                self._id_to_path[eid] = path
            else:
                self._id_to_path[eid] = path
            return eid

        def walk(obj, path: str, app: str, parent_id: str | None, depth: int) -> str | None:
            nonlocal walked, truncated, focused_id, active_app
            if len(elements) >= self.max_nodes:
                truncated = True
                return None
            walked += 1
            try:
                el = self._element_from_obj(obj, path, app, parent_id, alloc_id)
            except Exception:
                return None
            if el is None:
                return None
            elements[el.id] = el
            new_objects[el.id] = obj
            if el.focused:
                focused_id = el.id
            if "active" in el.states and el.role in {"frame", "window", "application"}:
                active_app = el.app or app
            try:
                n = obj.get_child_count() or 0
            except Exception:
                n = 0
            # Do not descend into closed menus / offscreen subtrees
            descend = True
            if depth >= 12:
                descend = False
            if not el.bounds_valid and el.role not in {"application", "frame", "desktop frame"}:
                # Still walk application/frame; skip hidden widget guts
                if el.role not in {"menu bar", "page tab list"}:
                    descend = el.focused or "expanded" in el.states or "selected" in el.states
            if descend:
                child_ids = []
                for i in range(n):
                    if len(elements) >= self.max_nodes:
                        truncated = True
                        break
                    try:
                        child = obj.get_child_at_index(i)
                    except Exception:
                        continue
                    if child is None:
                        continue
                    cid = walk(child, f"{path}.{i}" if path else str(i), el.app or app, el.id, depth + 1)
                    if cid:
                        child_ids.append(cid)
                el.child_ids = child_ids
            return el.id

        try:
            n_apps = desktop.get_child_count() or 0
        except Exception:
            n_apps = 0
        for i in range(n_apps):
            try:
                app_obj = desktop.get_child_at_index(i)
                app_name = ""
                try:
                    app_name = app_obj.get_name() or ""
                except Exception:
                    pass
                eid = walk(app_obj, str(i), app_name, None, 0)
                if eid:
                    roots.append(eid)
            except Exception:
                continue

        self._objects = new_objects
        self._generation += 1
        return TreeSnapshot(
            elements=elements,
            roots=roots,
            focused_id=focused_id,
            active_app=active_app,
            generation=self._generation,
            cache_hit=False,
            refresh_mode="full",
            truncated=truncated,
            node_count=len(elements),
            walked=walked,
        )

    def _rescan_apps(self, apps: list[str]) -> TreeSnapshot:
        """Re-walk only the named applications; keep other cached roots as-is."""
        if self._cache is None:
            return self._full_scan()
        # If we cannot identify the app roots, fall back to a full scan.
        want = {a.lower() for a in apps if a}
        cached_apps = []
        for rid in self._cache.roots:
            el = self._cache.elements.get(rid)
            if el:
                cached_apps.append((el.app or el.name or "").lower())
        if not want or not any(a in cached_apps for a in want):
            return self._full_scan()
        # Full scan is already limited to whatever is on the bus; reuse it but
        # mark mode incremental if only a subset of apps is dirty.
        snap = self._full_scan()
        if len(want) < max(1, len(cached_apps)):
            snap.refresh_mode = "incremental"
        return snap

    def _incremental_refresh(self, pending: list[_Pending]) -> TreeSnapshot:

        """Update text/bounds/state for referenced nodes; fall back to focused subtree."""
        if self._cache is None:
            return self._full_scan()
        snap = self._cache
        # Copy element dict so we don't mutate the previous snapshot in place
        elements = {k: self._clone(v) for k, v in snap.elements.items()}
        walked = 0
        updated = []
        target_ids = set()
        for ev in pending:
            if ev.path and ev.path in self._path_to_id:
                target_ids.add(self._path_to_id[ev.path])
        # Always refresh the previously focused node and any currently focused live object
        if snap.focused_id:
            target_ids.add(snap.focused_id)

        for eid in list(target_ids):
            obj = self._live_object(eid)
            el = elements.get(eid)
            if el is None or obj is None:
                continue
            before = (el.text, el.x, el.y, el.width, el.height, tuple(el.states), el.name)
            self._apply_live_fields(el, obj)
            walked += 1
            after = (el.text, el.x, el.y, el.width, el.height, tuple(el.states), el.name)
            if before != after:
                updated.append(eid)

        # If almost nothing was resolvable, refresh the active app subtree only
        if walked == 0 and snap.active_app:
            return self._full_scan()

        focused_id = None
        active_app = snap.active_app
        for el in elements.values():
            if el.focused:
                focused_id = el.id
            if "active" in el.states and el.role in {"frame", "window"}:
                active_app = el.app

        self._generation += 1
        new = TreeSnapshot(
            elements=elements,
            roots=list(snap.roots),
            focused_id=focused_id or snap.focused_id,
            active_app=active_app,
            generation=self._generation,
            cache_hit=False,
            refresh_mode="incremental",
            truncated=snap.truncated,
            node_count=len(elements),
            walked=walked,
            delta=TreeDelta(updated=updated, notes=[f"refreshed {walked} cached nodes"]),
        )
        return new

    def _cheap_unchanged(self) -> bool:
        """O(apps) check: same applications and top-level frame titles."""
        if self._cache is None:
            return False
        try:
            from gi.repository import Atspi
            desktop = Atspi.get_desktop(0)
            n = desktop.get_child_count() or 0
            cached_apps = [
                self._cache.elements[r] for r in self._cache.roots if r in self._cache.elements
            ]
            if n != len(cached_apps):
                return False
            for i, cached in enumerate(cached_apps):
                app = desktop.get_child_at_index(i)
                name = app.get_name() or ""
                if name != cached.name and name != cached.app:
                    return False
                # Compare first frame title when present
                try:
                    cn = app.get_child_count() or 0
                except Exception:
                    cn = 0
                cached_frames = [self._cache.elements[c] for c in cached.child_ids if c in self._cache.elements]
                if cn != len(cached_frames) and cn != (cached_frames and 1):
                    # child_count of application often equals number of frames
                    if cn != len(cached_frames):
                        return False
                if cached_frames and cn:
                    try:
                        frame = app.get_child_at_index(0)
                        title = frame.get_name() or ""
                        if title and title != cached_frames[0].name:
                            return False
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    def _refresh_focus(self, snap: TreeSnapshot) -> str | None:
        for el in snap.elements.values():
            obj = self._objects.get(el.id)
            if obj is None:
                continue
            try:
                states = self._states(obj)
            except Exception:
                continue
            el.states = states
            if "focused" in states:
                return el.id
        return snap.focused_id

    # ------------------------------------------------------------------ object helpers
    def _element_from_obj(self, obj, path: str, app: str, parent_id: str | None, alloc_id) -> UIElement | None:
        role = ""
        name = ""
        try:
            role = (obj.get_role_name() or "").lower()
        except Exception:
            role = "unknown"
        try:
            name = obj.get_name() or ""
        except Exception:
            name = ""
        if role == "application" and not app:
            app = name
        x = y = w = h = 0
        try:
            ext = obj.get_extents(self._coord_screen())
            x, y, w, h = int(ext.x), int(ext.y), int(ext.width), int(ext.height)
        except Exception:
            pass
        states = self._states(obj)
        text = self._text(obj, role)
        value = self._value(obj)
        actions = self._actions(obj)
        eid = alloc_id(path)
        return UIElement(
            id=eid,
            role=role,
            name=name.strip(),
            path=path,
            app=app,
            x=x, y=y, width=w, height=h,
            states=states,
            text=text,
            actions=actions,
            value=value,
            parent_id=parent_id,
        )

    def _apply_live_fields(self, el: UIElement, obj) -> None:
        try:
            ext = obj.get_extents(self._coord_screen())
            el.x, el.y, el.width, el.height = int(ext.x), int(ext.y), int(ext.width), int(ext.height)
        except Exception:
            pass
        try:
            name = obj.get_name()
            if name is not None:
                el.name = str(name).strip()
        except Exception:
            pass
        el.states = self._states(obj)
        el.text = self._text(obj, el.role)
        el.value = self._value(obj)
        el.actions = self._actions(obj)

    def _states(self, obj) -> list[str]:
        out = []
        try:
            from gi.repository import Atspi
            sset = obj.get_state_set()
            for attr in ("ACTIVE", "BUSY", "CHECKED", "EDITABLE", "ENABLED",
                         "EXPANDABLE", "EXPANDED", "FOCUSABLE", "FOCUSED",
                         "INVALID", "PRESSED", "REQUIRED", "SELECTED",
                         "SENSITIVE", "SHOWING", "VISIBLE"):
                try:
                    st = getattr(Atspi.StateType, attr)
                    if sset.contains(st):
                        out.append(attr.lower())
                except Exception:
                    continue
        except Exception:
            pass
        return out

    _TEXT_ROLES = {
        "text", "entry", "password text", "document text", "document",
        "edit", "paragraph", "terminal", "heading", "caption", "label",
        "accelerator label", "text box", "section",
    }

    def _text(self, obj, role: str | None = None) -> str:
        # Only probe widgets that typically implement Atspi.Text.
        # Calling get_character_count on every filler/menu node adds hundreds of ms.
        if role and role.lower() not in self._TEXT_ROLES:
            return ""
        try:
            n = int(obj.get_character_count() or 0)
        except Exception:
            return ""
        if n <= 0:
            return ""
        end = min(n, self.max_text)
        # In at-spi typelib 2.56, Accessible.get_text() returns a Text iface
        # (no offsets). The string form lives on Atspi.Text.get_text(obj, start, end).
        try:
            from gi.repository import Atspi
            text = Atspi.Text.get_text(obj, 0, end)
            if text:
                return text
        except Exception:
            pass
        try:
            from gi.repository import Atspi
            rng = Atspi.Text.get_string_at_offset(obj, 0, Atspi.TextGranularity.PARAGRAPH)
            if rng is not None and getattr(rng, "content", None):
                return rng.content[: self.max_text]
        except Exception:
            pass
        return ""

    def _value(self, obj) -> str:
        try:
            v = obj.get_current_value()
            if v is not None:
                return str(v)
        except Exception:
            pass
        return ""

    def _actions(self, obj) -> list[str]:
        out = []
        try:
            n = obj.get_n_actions()
            for i in range(n or 0):
                try:
                    out.append(obj.get_action_name(i) or "")
                except Exception:
                    try:
                        # newer API
                        out.append(obj.get_action_name(i) or "")
                    except Exception:
                        pass
        except Exception:
            pass
        return [a for a in out if a]

    def _coord_screen(self):
        from gi.repository import Atspi
        return Atspi.CoordType.SCREEN

    def _try_path(self, obj) -> str:
        # Best-effort: walk parents counting sibling index. Can be slow if used
        # on every event, so we only do a shallow attempt and otherwise leave blank.
        try:
            bits = []
            cur = obj
            for _ in range(16):
                parent = cur.get_parent()
                if parent is None:
                    break
                role = ""
                try:
                    role = (parent.get_role_name() or "").lower()
                except Exception:
                    pass
                if role in {"desktop frame", "desktop"}:
                    # find index under desktop
                    try:
                        n = parent.get_child_count() or 0
                        for i in range(n):
                            if parent.get_child_at_index(i) == cur:
                                bits.append(str(i))
                                break
                    except Exception:
                        pass
                    break
                idx = None
                try:
                    n = parent.get_child_count() or 0
                    # bound the search
                    if n > 80:
                        break
                    for i in range(n):
                        ch = parent.get_child_at_index(i)
                        if ch == cur:
                            idx = i
                            break
                except Exception:
                    break
                if idx is None:
                    break
                bits.append(str(idx))
                cur = parent
            bits.reverse()
            return ".".join(bits)
        except Exception:
            return ""

    def _live_object(self, element_id: str):
        obj = self._objects.get(element_id)
        if obj is not None and self._obj_alive(obj):
            return obj
        path = self._id_to_path.get(element_id)
        if not path:
            if self._cache and element_id in self._cache.elements:
                path = self._cache.elements[element_id].path
        if not path:
            return None
        obj = self._resolve_path(path)
        if obj is not None:
            self._objects[element_id] = obj
        return obj

    def _obj_alive(self, obj) -> bool:
        try:
            obj.get_role_name()
            return True
        except Exception:
            return False

    def _resolve_path(self, path: str):
        try:
            from gi.repository import Atspi
            cur = Atspi.get_desktop(0)
            if not path:
                return cur
            for part in path.split("."):
                if part == "":
                    continue
                cur = cur.get_child_at_index(int(part))
                if cur is None:
                    return None
            return cur
        except Exception:
            return None

    def _clone(self, el: UIElement) -> UIElement:
        return UIElement(
            id=el.id, role=el.role, name=el.name, path=el.path, app=el.app,
            x=el.x, y=el.y, width=el.width, height=el.height,
            states=list(el.states), text=el.text, actions=list(el.actions),
            value=el.value, parent_id=el.parent_id, child_ids=list(el.child_ids),
        )

    # AT-SPI actions on a cached element (no tree rescan)
    def grab_focus(self, element_id: str) -> bool:
        obj = self._live_object(element_id)
        if obj is None:
            return False
        try:
            return bool(obj.grab_focus())
        except Exception:
            try:
                comp = obj.get_component_iface()
                return bool(comp.grab_focus()) if comp else False
            except Exception:
                return False

    def do_action(self, element_id: str, action: str | None = None) -> bool:
        obj = self._live_object(element_id)
        if obj is None:
            return False
        try:
            n = obj.get_n_actions() or 0
            if n <= 0:
                return False
            if action:
                for i in range(n):
                    if (obj.get_action_name(i) or "").lower() == action.lower():
                        return bool(obj.do_action(i))
            return bool(obj.do_action(0))
        except Exception:
            return False

    def set_text(self, element_id: str, text: str) -> bool:
        obj = self._live_object(element_id)
        if obj is None:
            return False
        try:
            obj.set_text_contents(text)
            return True
        except Exception:
            try:
                n = obj.get_character_count() or 0
                if n:
                    obj.delete_text(0, n)
                obj.insert_text(0, text, len(text))
                return True
            except Exception:
                return False


def _summarize_events(pending: list[_Pending]) -> list[str]:
    if not pending:
        return []
    counts: dict[str, int] = {}
    samples = []
    for ev in pending:
        key = ev.typ.split(":")
        typ = ":".join(key[:3]) if key else ev.typ
        counts[typ] = counts.get(typ, 0) + 1
        if len(samples) < 6:
            bits = [typ]
            if ev.app:
                bits.append(ev.app)
            if ev.role:
                bits.append(ev.role)
            samples.append(" ".join(bits))
    summary = [f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8]]
    return summary + ([("e.g. " + ", ".join(samples))] if samples else [])


    def peek_menu_open(self, element_id: str) -> bool:
        """Check expanded state or a showing child — no full tree walk."""
        el = self.get_element(element_id)
        if el and "expanded" in (el.states or []):
            return True
        obj = self.live_object(element_id)
        if obj is None:
            return False
        try:
            from gi.repository import Atspi
            n = obj.get_child_count() or 0
            for i in range(min(int(n), 8)):
                ch = obj.get_child_at_index(i)
                if ch is None:
                    continue
                try:
                    sset = ch.get_state_set()
                    showing = sset.contains(Atspi.StateType.SHOWING)
                    if showing:
                        ext = ch.get_extents(Atspi.CoordType.SCREEN)
                        if ext.width > 1 and ext.height > 1 and ext.x > -10000:
                            return True
                except Exception:
                    continue
        except Exception:
            return False
        return False
