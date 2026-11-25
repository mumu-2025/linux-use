"""Classified on-screen element location map.

Stores role/name/region -> cached screen coordinates so a later click can
use the point directly, without walking the accessibility tree.

Entries are invalidated from AT-SPI events or a cheap window-geometry check.
If the check fails, the caller should fall back to an incremental snapshot.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .tree import TreeSnapshot, UIElement


# Canonical classes used for lookup (role aliases fold into these).
ROLE_CLASS = {
    "push button": "button",
    "button": "button",
    "toggle button": "button",
    "tool bar button": "button",
    "text": "text",
    "entry": "text",
    "password text": "text",
    "document text": "text",
    "document": "text",
    "edit": "text",
    "paragraph": "text",
    "terminal": "text",
    "text box": "text",
    "link": "link",
    "menu item": "menu_item",
    "menu": "menu",
    "menu bar": "menu_bar",
    "page tab": "tab",
    "tab": "tab",
    "list item": "list_item",
    "tree item": "list_item",
    "check box": "checkbox",
    "radio button": "radio",
    "combo box": "combo",
    "slider": "slider",
    "spin button": "spin",
    "heading": "heading",
    "label": "label",
    "accelerator label": "label",
    "image": "image",
    "scroll bar": "scroll_bar",
    "frame": "frame",
    "window": "frame",
    "dialog": "dialog",
    "alert": "dialog",
}

INTERACTIVE_CLASSES = {
    "button", "text", "link", "menu_item", "menu", "tab", "list_item",
    "checkbox", "radio", "combo", "slider", "spin", "heading",
}


def classify_role(role: str) -> str:
    role = (role or "").strip().lower()
    return ROLE_CLASS.get(role, role or "unknown")


def _name_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


@dataclass
class LocEntry:
    id: str
    klass: str
    role: str
    name: str
    region: str
    app: str
    x: int
    y: int
    width: int
    height: int
    cx: int
    cy: int
    window_geom: tuple[int, int, int, int] | None = None
    wid: str | None = None
    showing: bool = True
    generation: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.window_geom is not None:
            d["window_geom"] = list(self.window_geom)
        return d


@dataclass
class ResolveResult:
    ok: bool
    entry: LocEntry | None
    x: int | None
    y: int | None
    source: str  # map | refreshed | miss
    message: str
    intent_ms: float
    valid_check_ms: float = 0.0
    lookup_ms: float = 0.0
    walked: int = 0


@dataclass
class LocationMap:
    """Fast name/role/region -> coordinates, kept in sync with AT-SPI snapshots."""

    entries: dict[str, LocEntry] = field(default_factory=dict)
    generation: int = 0
    built_ms: float = 0.0
    dirty_ids: set[str] = field(default_factory=set)
    dirty_regions: set[str] = field(default_factory=set)
    structural_dirty: bool = False
    last_events: list[str] = field(default_factory=list)

    def clear(self) -> None:
        self.entries.clear()
        self.dirty_ids.clear()
        self.dirty_regions.clear()
        self.structural_dirty = False
        self.last_events = []
        self.generation = 0

    def ingest(self, snap: TreeSnapshot, windows: Iterable[Any] | None = None) -> None:
        t0 = time.perf_counter()
        win_by_title: list[tuple[str, Any]] = []
        for w in windows or []:
            title = getattr(w, "title", "") or ""
            geom = (
                int(getattr(w, "x", 0)),
                int(getattr(w, "y", 0)),
                int(getattr(w, "width", 0)),
                int(getattr(w, "height", 0)),
            )
            win_by_title.append((title, (getattr(w, "wid", None), geom)))

        def _match_window(region: str):
            if not region:
                return None, None
            key = region.casefold()
            for title, (wid, geom) in win_by_title:
                if key and (key in title.casefold() or title.casefold() in key):
                    return wid, geom
            return None, None

        # Region = nearest ancestor frame/window/dialog/application name
        region_of: dict[str, str] = {}

        def region_for(el: UIElement) -> str:
            if el.id in region_of:
                return region_of[el.id]
            if el.role in {"frame", "window", "dialog", "alert", "application"} and el.name:
                region_of[el.id] = el.name
                return el.name
            if el.parent_id and el.parent_id in snap.elements:
                r = region_for(snap.elements[el.parent_id])
                region_of[el.id] = r
                return r
            region_of[el.id] = el.app or ""
            return region_of[el.id]

        new: dict[str, LocEntry] = {}
        for el in snap.elements.values():
            klass = classify_role(el.role)
            # Keep interactive widgets plus named/showing ones with valid bounds.
            if not el.bounds_valid:
                continue
            if klass not in INTERACTIVE_CLASSES and not el.name and not el.focused:
                continue
            if not el.showing and not el.focused:
                continue
            region = region_for(el)
            wid, geom = _match_window(region)
            new[el.id] = LocEntry(
                id=el.id,
                klass=klass,
                role=el.role,
                name=(el.name or "").strip(),
                region=region,
                app=el.app or "",
                x=el.x, y=el.y, width=el.width, height=el.height,
                cx=el.cx, cy=el.cy,
                window_geom=geom,
                wid=wid,
                showing=el.showing or el.focused,
                generation=snap.generation,
            )
        self.entries = new
        self.generation = snap.generation
        self.dirty_ids.clear()
        self.dirty_regions.clear()
        self.structural_dirty = False
        self.last_events = list(snap.events or [])
        self.built_ms = (time.perf_counter() - t0) * 1000.0

    def note_events(self, events: Iterable[str], *, structural: bool = False,
                    apps: Iterable[str] | None = None, ids: Iterable[str] | None = None) -> None:
        evs = [e for e in events if e]
        if evs:
            self.last_events = list(evs)[:16]
        if structural:
            self.structural_dirty = True
        if apps:
            self.dirty_regions.update(a for a in apps if a)
        if ids:
            self.dirty_ids.update(i for i in ids if i)
        # Parse coarse event labels for bounds/children/window hints
        for e in evs:
            low = e.lower()
            if "children-changed" in low or "window:create" in low or "window:destroy" in low:
                self.structural_dirty = True
            if "bounds-changed" in low or "scroll" in low:
                # Unknown id: mark all in mentioned app later; keep structural false
                pass

    def invalidate_region(self, region: str) -> None:
        if region:
            self.dirty_regions.add(region)

    def lookup(
        self,
        *,
        name: str | None = None,
        role: str | None = None,
        region: str | None = None,
        element_id: str | None = None,
    ) -> list[LocEntry]:
        if element_id:
            el = self.entries.get(element_id)
            return [el] if el else []
        want_name = _name_key(name) if name else ""
        want_class = classify_role(role) if role else ""
        want_region = (region or "").casefold()
        hits: list[LocEntry] = []
        exact: list[LocEntry] = []
        for el in self.entries.values():
            if want_class and el.klass != want_class and el.role != (role or "").lower():
                continue
            if want_region and want_region not in (el.region or "").casefold() and want_region not in (el.app or "").casefold():
                continue
            if want_name:
                key = _name_key(el.name)
                if key == want_name:
                    exact.append(el)
                    continue
                if want_name not in key:
                    continue
            hits.append(el)
        return exact or hits

    def cheap_valid(self, entry: LocEntry, desktop) -> tuple[bool, float, str]:
        """Window-geometry check only — no accessibility tree walk."""
        t0 = time.perf_counter()
        if self.structural_dirty:
            return False, (time.perf_counter() - t0) * 1000.0, "structural-dirty"
        if entry.id in self.dirty_ids:
            return False, (time.perf_counter() - t0) * 1000.0, "id-dirty"
        if entry.region and any(
            r.casefold() in entry.region.casefold() or entry.region.casefold() in r.casefold()
            for r in self.dirty_regions
        ):
            return False, (time.perf_counter() - t0) * 1000.0, "region-dirty"
        if entry.window_geom is None and not entry.wid:
            # Nothing to compare; treat as usable if still showing when recorded.
            ms = (time.perf_counter() - t0) * 1000.0
            return bool(entry.showing), ms, "no-window-geom"
        try:
            wins = desktop.list_windows()
        except Exception as exc:
            return False, (time.perf_counter() - t0) * 1000.0, f"wmctrl-failed:{exc}"
        match = None
        if entry.wid:
            for w in wins:
                if w.wid == entry.wid:
                    match = w
                    break
        if match is None and entry.region:
            key = entry.region.casefold()
            for w in wins:
                if key in w.title.casefold() or w.title.casefold() in key:
                    match = w
                    break
        if match is None:
            return False, (time.perf_counter() - t0) * 1000.0, "window-missing"
        geom = (int(match.x), int(match.y), int(match.width), int(match.height))
        if entry.window_geom and geom != entry.window_geom:
            return False, (time.perf_counter() - t0) * 1000.0, f"geom-changed {entry.window_geom}->{geom}"
        ms = (time.perf_counter() - t0) * 1000.0
        return True, ms, "ok"

    def resolve(
        self,
        *,
        name: str | None = None,
        role: str | None = None,
        region: str | None = None,
        element_id: str | None = None,
        a11y=None,
        desktop=None,
        refresh_if_stale: bool = True,
    ) -> ResolveResult:
        t0 = time.perf_counter()
        t_lookup = time.perf_counter()
        hits = self.lookup(name=name, role=role, region=region, element_id=element_id)
        lookup_ms = (time.perf_counter() - t_lookup) * 1000.0
        valid_ms = 0.0

        def _pick(cands: list[LocEntry]) -> LocEntry | None:
            if not cands:
                return None
            if len(cands) == 1:
                return cands[0]
            # Prefer focused / showing / on-screen
            cands = sorted(cands, key=lambda e: (not e.showing, e.klass != "button", -e.width * e.height))
            return cands[0]

        entry = _pick(hits)
        if entry and desktop is not None:
            ok, valid_ms, why = self.cheap_valid(entry, desktop)
            if ok:
                return ResolveResult(
                    True, entry, entry.cx, entry.cy, "map",
                    f"map hit [{entry.id}] {entry.klass} {entry.name!r} at {entry.cx},{entry.cy} ({why})",
                    intent_ms=(time.perf_counter() - t0) * 1000.0,
                    valid_check_ms=valid_ms,
                    lookup_ms=lookup_ms,
                    walked=0,
                )
            stale_reason = why
        else:
            stale_reason = "miss" if not entry else "no-desktop"

        walked = 0
        if refresh_if_stale and a11y is not None:
            snap = a11y.snapshot(force=False)
            walked = snap.walked
            self.ingest(snap, desktop.list_windows() if desktop is not None else None)
            hits = self.lookup(name=name, role=role, region=region, element_id=element_id)
            entry = _pick(hits)
            if entry:
                return ResolveResult(
                    True, entry, entry.cx, entry.cy, "refreshed",
                    f"refreshed [{entry.id}] {entry.klass} {entry.name!r} at {entry.cx},{entry.cy} (was {stale_reason})",
                    intent_ms=(time.perf_counter() - t0) * 1000.0,
                    valid_check_ms=valid_ms,
                    lookup_ms=lookup_ms,
                    walked=walked,
                )
        label = name or element_id or role or "?"
        return ResolveResult(
            False, entry, None, None, "miss",
            f"no mapped element for {label!r} ({stale_reason})",
            intent_ms=(time.perf_counter() - t0) * 1000.0,
            valid_check_ms=valid_ms,
            lookup_ms=lookup_ms,
            walked=walked,
        )

    def dump_text(self) -> str:
        lines = [
            f"location map  entries={len(self.entries)} generation={self.generation} "
            f"built_ms={self.built_ms:.1f} structural_dirty={self.structural_dirty}"
        ]
        if self.last_events:
            lines.append("events: " + "; ".join(self.last_events[:8]))
        # Group by class then region
        grouped: dict[str, list[LocEntry]] = {}
        for el in self.entries.values():
            grouped.setdefault(el.klass, []).append(el)
        for klass in sorted(grouped):
            lines.append(f"[{klass}]")
            items = sorted(grouped[klass], key=lambda e: (e.region, e.name, e.id))
            for el in items:
                name = el.name or "—"
                lines.append(
                    f"  {el.id:>5}  {name!r:30}  region={el.region!r}  "
                    f"({el.cx},{el.cy})  bounds=({el.x},{el.y},{el.width},{el.height})"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "built_ms": self.built_ms,
            "count": len(self.entries),
            "structural_dirty": self.structural_dirty,
            "entries": [e.to_dict() for e in self.entries.values()],
        }
