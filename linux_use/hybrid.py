"""Hybrid policy: pick cache vs locmap (and AT-SPI primitives) per action.

Rules come from the 2026-11-25 three-mode benchmark on this box:

- In-memory lookup on a fresh cached tree is ~0.02 ms; a locmap window-geometry
  check is ~5–12 ms. So if the last snapshot is still trustworthy, prefer cache.
- Locmap wins when we can skip a tree walk entirely (no fresh snapshot, static
  chrome like buttons/menus/tabs, geometry unchanged, no invalidation).
- Dynamic widgets (text, lists, links, scroll areas) stay on the incremental
  cache path; after scroll/resize/navigation, refresh first.
- AT-SPI do_action / set_text / grab_focus beat coordinate clicks when they
  work (Don't Save via do_action was 0.58 ms vs ~105 ms for xdotool).
- After each action, do a cheap targeted verify. On mismatch: incremental
  refresh + retry once; full rescan only as a last resort.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .locmap import LocationMap, classify_role
from .platform.base import ActionResult
from .tree import TreeSnapshot, UIElement

STATIC_CLASSES = {
    "button", "menu", "tab", "checkbox", "radio", "menu_bar", "toolbar",
}
DYNAMIC_CLASSES = {
    "text", "list_item", "link", "document", "paragraph", "entry", "edit",
    "scroll_bar", "terminal",
}


@dataclass
class PathLog:
    step: str
    path: str
    detail: str = ""
    ok: bool = True
    ms: float = 0.0
    walked: int = 0


@dataclass
class HybridDecision:
    path: str
    x: int | None = None
    y: int | None = None
    element_id: str | None = None
    use_atspi: bool = False
    atspi_action: str | None = None
    reason: str = ""
    locate_ms: float = 0.0
    observe_ms: float = 0.0
    walked: int = 0
    snap: TreeSnapshot | None = None


class HybridRouter:
    """Default action router for linux-use."""

    def __init__(self, a11y, desktop, input_backend, locmap: LocationMap | None = None):
        self.a11y = a11y
        self.desktop = desktop
        self.input = input_backend
        self.locmap = locmap if locmap is not None else LocationMap()
        self.last_snap: TreeSnapshot | None = None
        self.logs: list[PathLog] = []

    def log(self, step: str, path: str, detail: str = "", ok: bool = True,
            ms: float = 0.0, walked: int = 0) -> None:
        self.logs.append(PathLog(step, path, detail, ok, ms, walked))

    def consume_logs(self) -> list[dict[str, Any]]:
        out = [p.__dict__ for p in self.logs]
        self.logs.clear()
        return out

    # ------------------------------------------------------------------ observe
    def observe(self, *, force: bool = False, reason: str = "observe") -> tuple[TreeSnapshot, float, int]:
        snap = self.a11y.snapshot(force=force)
        try:
            self.locmap.ingest(snap, self.desktop.list_windows())
        except Exception:
            pass
        self.last_snap = snap
        self.log(reason, "full" if force or snap.refresh_mode == "full" else snap.refresh_mode,
                 f"nodes={snap.node_count}", True, snap.observe_ms, snap.walked)
        return snap, snap.observe_ms, snap.walked

    def _cache_fresh(self) -> bool:
        if self.last_snap is None:
            return False
        if self.locmap.structural_dirty:
            return False
        if self.locmap.dirty_regions or self.locmap.dirty_ids:
            return False
        return True

    def _find_in_snap(self, snap: TreeSnapshot, *, name: str | None, role: str | None,
                      element_id: str | None) -> UIElement | None:
        if element_id and element_id in snap.elements:
            return snap.elements[element_id]
        want_role = (role or "").strip().lower()
        want_class = classify_role(role) if role else ""
        want_name = " ".join((name or "").split()).casefold()
        exact = []
        loose = []
        for el in snap.elements.values():
            if want_role and el.role != want_role and classify_role(el.role) != want_class:
                continue
            if want_name:
                key = " ".join((el.name or "").split()).casefold()
                if key == want_name:
                    exact.append(el)
                    continue
                if want_name not in key:
                    continue
            loose.append(el)
        cands = exact or loose
        if not cands:
            return None
        cands.sort(key=lambda e: (not e.focused, not e.showing, not e.bounds_valid))
        return cands[0]

    # ------------------------------------------------------------------ decide + act
    def decide(self, *, name: str | None = None, role: str | None = None,
               element_id: str | None = None, region: str | None = None) -> HybridDecision:
        t0 = time.perf_counter()
        klass = classify_role(role or "")
        if not klass and element_id and self.last_snap and element_id in self.last_snap.elements:
            klass = classify_role(self.last_snap.elements[element_id].role)
        static = klass in STATIC_CLASSES
        dynamic = klass in DYNAMIC_CLASSES or klass == "text"

        # 1. Fresh cached tree: in-memory locate (faster than locmap+wmctrl).
        if self._cache_fresh() and self.last_snap is not None:
            el = self._find_in_snap(self.last_snap, name=name, role=role, element_id=element_id)
            if el is not None:
                use_atspi = bool(el.actions) or dynamic or el.role in {"text", "entry", "edit"}
                return HybridDecision(
                    path="cache",
                    x=el.cx, y=el.cy, element_id=el.id,
                    use_atspi=use_atspi,
                    atspi_action="click" if el.actions else None,
                    reason="fresh cached tree",
                    locate_ms=(time.perf_counter() - t0) * 1000.0,
                    snap=self.last_snap,
                )

        # 2. Static chrome + locmap hit + geometry still valid: no tree walk.
        if static or (not dynamic and name):
            hits = self.locmap.lookup(name=name, role=role, region=region, element_id=element_id)
            if hits:
                entry = hits[0]
                ok, vms, why = self.locmap.cheap_valid(entry, self.desktop)
                if ok:
                    return HybridDecision(
                        path="locmap",
                        x=entry.cx, y=entry.cy, element_id=entry.id,
                        use_atspi=True,
                        atspi_action="click",
                        reason=f"locmap valid ({why})",
                        locate_ms=(time.perf_counter() - t0) * 1000.0,
                    )
                else:
                    self.locmap.note_events([why], ids=[entry.id], apps=[entry.region])

        # 3. Incremental refresh, then cache locate.
        snap, oms, walked = self.observe(force=False, reason="hybrid-refresh")
        el = self._find_in_snap(snap, name=name, role=role, element_id=element_id)
        if el is not None:
            return HybridDecision(
                path="cache-refresh",
                x=el.cx, y=el.cy, element_id=el.id,
                use_atspi=True,
                atspi_action="click" if el.actions else None,
                reason="incremental refresh",
                locate_ms=(time.perf_counter() - t0) * 1000.0,
                observe_ms=oms,
                walked=walked,
                snap=snap,
            )

        # 4. Last resort: full rescan.
        snap, oms2, walked2 = self.observe(force=True, reason="hybrid-full")
        el = self._find_in_snap(snap, name=name, role=role, element_id=element_id)
        if el is not None:
            return HybridDecision(
                path="full",
                x=el.cx, y=el.cy, element_id=el.id,
                use_atspi=True,
                atspi_action="click" if el.actions else None,
                reason="full rescan",
                locate_ms=(time.perf_counter() - t0) * 1000.0,
                observe_ms=oms + oms2,
                walked=walked + walked2,
                snap=snap,
            )
        return HybridDecision(
            path="miss",
            reason="target not found",
            locate_ms=(time.perf_counter() - t0) * 1000.0,
            observe_ms=oms + oms2,
            walked=walked + walked2,
        )

    def click(self, *, name: str | None = None, role: str | None = None,
              element_id: str | None = None, region: str | None = None,
              button: str = "left", clicks: int = 1,
              expect: str | None = None) -> ActionResult:
        t0 = time.perf_counter()
        dec = self.decide(name=name, role=role, element_id=element_id, region=region)
        if dec.path == "miss" or (dec.x is None and not dec.element_id):
            self.log("click", "miss", dec.reason, False, dec.locate_ms, dec.walked)
            return ActionResult(False, f"hybrid miss: {dec.reason}", {
                "path": "miss", "locate_ms": dec.locate_ms, "walked": dec.walked,
            }, act_ms=(time.perf_counter() - t0) * 1000.0)
        t_act = time.perf_counter()
        res = self._perform_click(dec, button, clicks)
        act_only = (time.perf_counter() - t_act) * 1000.0
        ok = res.ok
        verify_ms = 0.0
        if ok and expect:
            vok, verify_ms, detail = self.verify(expect, element_id=dec.element_id, role=role)
            if not vok:
                # Retry once after incremental refresh.
                _, oms, w = self.observe(force=False, reason="verify-retry")
                dec2 = self.decide(name=name, role=role, element_id=element_id, region=region)
                t_act = time.perf_counter()
                res = self._perform_click(dec2, button, clicks)
                act_only += (time.perf_counter() - t_act) * 1000.0
                vok, verify_ms, detail = self.verify(expect, element_id=dec2.element_id, role=role)
                extra_obs = oms + dec2.observe_ms
                extra_walked = w + dec2.walked
                if not vok:
                    _, oms2, w2 = self.observe(force=True, reason="verify-full")
                    dec3 = self.decide(name=name, role=role, element_id=element_id, region=region)
                    t_act = time.perf_counter()
                    res = self._perform_click(dec3, button, clicks)
                    act_only += (time.perf_counter() - t_act) * 1000.0
                    vok, verify_ms, detail = self.verify(expect, element_id=dec3.element_id, role=role)
                    extra_obs += oms2 + dec3.observe_ms
                    extra_walked += w2 + dec3.walked
                    dec = dec3
                    path = "retry-full"
                else:
                    dec = dec2
                    path = "retry-incremental"
                ok = res.ok and vok
                self.log("click", path, detail, ok, (time.perf_counter() - t0) * 1000.0, dec.walked + extra_walked)
                res.data = {**(res.data or {}), "path": path, "decision": dec.path,
                            "locate_ms": dec.locate_ms, "observe_ms": dec.observe_ms + extra_obs,
                            "verify_ms": verify_ms, "act_ms": act_only,
                            "walked": dec.walked + extra_walked, "id": dec.element_id}
                res.message = f"hybrid {path} via {dec.path}: {res.message}; verify={detail}"
                return res
        self.log("click", dec.path, dec.reason, ok, (time.perf_counter() - t0) * 1000.0, dec.walked)
        res.data = {**(res.data or {}), "path": dec.path, "locate_ms": dec.locate_ms,
                    "observe_ms": dec.observe_ms, "verify_ms": verify_ms,
                    "act_ms": act_only, "walked": dec.walked, "id": dec.element_id}
        res.message = f"hybrid {dec.path}: {res.message}"
        return res

    def _perform_click(self, dec: HybridDecision, button: str, clicks: int) -> ActionResult:
        # Prefer AT-SPI on the live element.
        if dec.use_atspi and dec.element_id and button == "left" and clicks == 1:
            if dec.atspi_action and self.a11y.do_action(dec.element_id, dec.atspi_action):
                return ActionResult(True, f"AT-SPI {dec.atspi_action} [{dec.element_id}]", {"via": "do_action"})
            if self.a11y.do_action(dec.element_id, "click"):
                return ActionResult(True, f"AT-SPI click [{dec.element_id}]", {"via": "do_action"})
            if self.a11y.grab_focus(dec.element_id):
                # Focus-only is enough for a text field we will type into next;
                # still click if this is clearly a button-like widget that needs it.
                klass = classify_role((self.last_snap.elements.get(dec.element_id).role
                                       if self.last_snap and dec.element_id in (self.last_snap.elements or {})
                                       else "") or "")
                if klass in {"text", "entry", "edit"} or klass in DYNAMIC_CLASSES:
                    return ActionResult(True, f"AT-SPI grab_focus [{dec.element_id}]", {"via": "grab_focus"})
        if dec.x is not None and dec.y is not None:
            return self.input.click(int(dec.x), int(dec.y), button=button, clicks=clicks)
        return ActionResult(False, "no click primitive available")

    def type_text(self, text: str, *, element_id: str | None = None,
                  name: str | None = None, role: str | None = "text",
                  clear: bool = True, press_enter: bool = False) -> ActionResult:
        t0 = time.perf_counter()
        dec = None
        eid = element_id
        if not eid:
            dec = self.decide(name=name, role=role, element_id=element_id)
            eid = dec.element_id
            if eid is None:
                return ActionResult(False, "hybrid type: no text target",
                                    {"path": "miss"}, act_ms=(time.perf_counter() - t0) * 1000.0)
        if eid:
            self.a11y.grab_focus(eid)
            if self.a11y.set_text(eid, text):
                if press_enter:
                    self.input.shortcut("Return")
                ok, vms, detail = self.verify("text:" + text, element_id=eid)
                path = "atspi"
                if not ok:
                    self.observe(force=False, reason="type-verify-retry")
                    if self.a11y.set_text(eid, text):
                        ok, vms, detail = self.verify("text:" + text, element_id=eid)
                        path = "retry-incremental"
                self.log("type", path, detail, ok, (time.perf_counter() - t0) * 1000.0,
                         dec.walked if dec else 0)
                wall = (time.perf_counter() - t0) * 1000.0
                return ActionResult(ok, f"hybrid {path} set_text [{eid}] ({len(text)} chars); {detail}", {
                    "path": path, "id": eid, "verify_ms": vms,
                    "locate_ms": dec.locate_ms if dec else 0.0,
                    "observe_ms": dec.observe_ms if dec else 0.0,
                    "act_ms": max(0.0, wall - vms - (dec.locate_ms if dec else 0.0) - (dec.observe_ms if dec else 0.0)),
                    "walked": dec.walked if dec else 0,
                }, act_ms=wall)
        res = self.input.type_text(text, clear=clear, press_enter=press_enter)
        self.log("type", "xdotool", res.message, res.ok, res.act_ms)
        return res

    def shortcut(self, keys: str, *, expect: str | None = None,
                 element_id: str | None = None) -> ActionResult:
        t0 = time.perf_counter()
        res = self.input.shortcut(keys)
        act_only = res.act_ms
        verify_ms = 0.0
        observe_ms = 0.0
        walked = 0
        path = "shortcut"
        if res.ok and expect:
            ok, verify_ms, detail = self.verify(expect, element_id=element_id)
            if not ok:
                _, oms, walked = self.observe(force=False, reason="shortcut-verify")
                observe_ms += oms
                res = self.input.shortcut(keys)
                act_only += res.act_ms
                ok, verify_ms, detail = self.verify(expect, element_id=element_id)
                path = "retry-incremental"
                if not ok:
                    _, oms2, w2 = self.observe(force=True, reason="shortcut-full")
                    observe_ms += oms2
                    walked += w2
                    res = self.input.shortcut(keys)
                    act_only += res.act_ms
                    ok, verify_ms, detail = self.verify(expect, element_id=element_id)
                    path = "retry-full"
                res.ok = bool(res.ok and ok)
                res.message = f"hybrid {path}: {res.message}; {detail}"
            else:
                res.message = f"hybrid shortcut {keys}; {detail}"
        self.log("shortcut", path, keys, res.ok, (time.perf_counter() - t0) * 1000.0, walked)
        res.data = {**(res.data or {}), "path": path, "verify_ms": verify_ms,
                    "observe_ms": observe_ms, "locate_ms": 0.0, "act_ms": act_only,
                    "walked": walked, "id": element_id}
        return res

    def verify(self, expect: str, *, element_id: str | None = None,
               role: str | None = None) -> tuple[bool, float, str]:
        """Cheap targeted check. No full tree walk."""
        t0 = time.perf_counter()
        try:
            if expect.startswith("text:"):
                want = expect[5:]
                got = ""
                if element_id:
                    el = self.a11y.get_element(element_id)
                    if el is not None:
                        got = el.text or ""
                ok = want in got
                return ok, (time.perf_counter() - t0) * 1000.0, f"text ok={ok} got={got[:80]!r}"
            if expect == "focused":
                if not element_id:
                    return False, (time.perf_counter() - t0) * 1000.0, "no id"
                el = self.a11y.get_element(element_id)
                ok = bool(el and el.focused)
                return ok, (time.perf_counter() - t0) * 1000.0, f"focused={ok}"
            if expect == "showing":
                if not element_id:
                    return False, (time.perf_counter() - t0) * 1000.0, "no id"
                el = self.a11y.get_element(element_id)
                ok = bool(el and (el.showing or el.bounds_valid))
                return ok, (time.perf_counter() - t0) * 1000.0, f"showing={ok}"
            if expect == "menu-open":
                ok = self._menu_open(element_id)
                return ok, (time.perf_counter() - t0) * 1000.0, f"menu-open={ok}"
            if expect == "menu-closed":
                ok = not self._menu_open(element_id)
                return ok, (time.perf_counter() - t0) * 1000.0, f"menu-closed={ok}"
            if expect.startswith("file:"):
                from pathlib import Path
                path = Path(expect[5:])
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                # caller passes "file:/path::MARKER" ?
                return bool(text), (time.perf_counter() - t0) * 1000.0, f"file n={len(text)}"
            if expect.startswith("filecontains:"):
                # filecontains:/path/to/file::MARKER
                rest = expect[len("filecontains:"):]
                if "::" in rest:
                    fpath, marker = rest.split("::", 1)
                else:
                    fpath, marker = rest, ""
                from pathlib import Path
                p = Path(fpath)
                text = p.read_text(encoding="utf-8") if p.exists() else ""
                ok = marker in text if marker else bool(text)
                return ok, (time.perf_counter() - t0) * 1000.0, f"filecontains ok={ok}"
        except Exception as exc:
            return False, (time.perf_counter() - t0) * 1000.0, f"verify error: {exc}"
        return True, (time.perf_counter() - t0) * 1000.0, "no-op"

    def _menu_open(self, element_id: str | None) -> bool:
        if not element_id:
            return False
        peek = getattr(self.a11y, "peek_menu_open", None)
        if callable(peek):
            try:
                return bool(peek(element_id))
            except Exception:
                pass
        el = self.a11y.get_element(element_id)
        return bool(el and "expanded" in (el.states or []))
