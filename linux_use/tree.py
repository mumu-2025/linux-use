"""Accessibility tree snapshot model and compact rendering for the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable


INTERACTIVE_ROLES = {
    "push button", "button", "toggle button", "check box", "radio button",
    "menu item", "menu", "menu bar", "combo box", "text", "entry", "password text",
    "document text", "document", "edit", "paragraph", "terminal", "link",
    "tab", "page tab", "slider", "spin button", "scroll bar", "tree item",
    "list item", "cell", "heading", "tool bar", "switch",
}


@dataclass
class UIElement:
    id: str
    role: str
    name: str
    path: str
    app: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    states: list[str] = field(default_factory=list)
    text: str = ""
    actions: list[str] = field(default_factory=list)
    value: str = ""
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)

    @property
    def cx(self) -> int:
        return self.x + self.width // 2

    @property
    def cy(self) -> int:
        return self.y + self.height // 2

    @property
    def showing(self) -> bool:
        return "showing" in self.states or "visible" in self.states

    @property
    def focused(self) -> bool:
        return "focused" in self.states

    @property
    def bounds_valid(self) -> bool:
        return self.width > 1 and self.height > 1 and self.x > -10000 and self.y > -10000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TreeDelta:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.updated or self.notes)


@dataclass
class TreeSnapshot:
    elements: dict[str, UIElement] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)
    focused_id: str | None = None
    active_app: str | None = None
    generation: int = 0
    cache_hit: bool = False
    refresh_mode: str = "full"  # full | incremental | cache
    observe_ms: float = 0.0
    events: list[str] = field(default_factory=list)
    delta: TreeDelta | None = None
    truncated: bool = False
    node_count: int = 0
    walked: int = 0

    def get(self, element_id: str) -> UIElement | None:
        return self.elements.get(element_id)

    def find(self, *, role: str | None = None, name: str | None = None,
             focused: bool | None = None, app: str | None = None) -> list[UIElement]:
        out = []
        for el in self.elements.values():
            if role and el.role != role:
                continue
            if name and name.lower() not in (el.name or "").lower():
                continue
            if focused is True and not el.focused:
                continue
            if app and el.app != app:
                continue
            out.append(el)
        return out

    def compact_text(self, *, max_nodes: int = 400, include_hidden: bool = False) -> str:
        lines: list[str] = []
        shown = 0

        def walk(eid: str, indent: int) -> None:
            nonlocal shown
            if shown >= max_nodes:
                return
            el = self.elements.get(eid)
            if el is None:
                return
            containers = {
                "application", "desktop frame", "frame", "window", "dialog",
                "filler", "panel", "page tab", "page tab list", "scroll pane",
                "split pane", "layered pane", "tool bar", "status bar",
                "table", "list", "tree", "document", "section", "page",
            }
            structural = el.role in containers or bool(el.child_ids)
            hidden = (not el.showing) and (not el.focused) and (not structural)
            offscreen = not el.bounds_valid
            if hidden and not include_hidden:
                return
            # Skip closed-menu guts with garbage bounds, but keep containers
            # (page tab often has bounds -1,-1,-1,-1 while still selected).
            if offscreen and not structural and not el.focused and "selected" not in el.states:
                return
            bits = [f"[{el.id}]", el.role]
            if el.name:
                bits.append(f'"{_clip(el.name, 80)}"')
            if el.bounds_valid:
                bits.append(f"({el.x},{el.y},{el.width},{el.height})")
            interesting = [s for s in el.states if s in {
                "focused", "editable", "selected", "checked", "expanded",
                "pressed", "active", "required", "invalid",
            }]
            if interesting:
                bits.append(" ".join(interesting))
            if el.actions and el.role in INTERACTIVE_ROLES:
                bits.append("actions=" + ",".join(el.actions[:4]))
            if el.text:
                bits.append("text=" + repr(_clip(el.text, 120)))
            if el.value and el.value != el.text and el.value != el.name:
                bits.append("value=" + repr(_clip(el.value, 80)))
            lines.append("  " * indent + " ".join(bits))
            shown += 1
            for cid in el.child_ids:
                walk(cid, indent + 1)

        for rid in self.roots:
            walk(rid, 0)
        if self.truncated or shown >= max_nodes:
            lines.append(f"... truncated, showing {shown}/{self.node_count} nodes")
        return "\n".join(lines)

    def observation(self) -> str:
        header = [
            f"observe_ms={self.observe_ms:.1f} mode={self.refresh_mode} "
            f"cache_hit={self.cache_hit} generation={self.generation} "
            f"nodes={self.node_count} walked={self.walked}",
        ]
        if self.active_app:
            header.append(f"active_app={self.active_app}")
        if self.focused_id and self.focused_id in self.elements:
            el = self.elements[self.focused_id]
            label = el.name or el.text
            header.append(
                f"focused=[{el.id}] {el.role} {label!r} "
                f"({el.x},{el.y},{el.width},{el.height})"
            )
        if self.events:
            header.append("events: " + "; ".join(self.events[:12]))
        if self.delta and not self.delta.is_empty():
            d = self.delta
            header.append(
                f"delta added={len(d.added)} removed={len(d.removed)} updated={len(d.updated)}"
            )
            for note in d.notes[:8]:
                header.append("  " + note)
        body = self.compact_text()
        return "\n".join(header) + "\n" + body

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "cache_hit": self.cache_hit,
            "refresh_mode": self.refresh_mode,
            "observe_ms": self.observe_ms,
            "events": self.events,
            "focused_id": self.focused_id,
            "active_app": self.active_app,
            "node_count": self.node_count,
            "walked": self.walked,
            "truncated": self.truncated,
            "roots": self.roots,
            "elements": {k: v.to_dict() for k, v in self.elements.items()},
        }


def _clip(text: str, n: int) -> str:
    text = (text or "").replace("\n", "\\n")
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def diff_snapshots(old: TreeSnapshot | None, new: TreeSnapshot) -> TreeDelta:
    if old is None:
        return TreeDelta(added=list(new.elements), notes=["initial snapshot"])
    added, removed, updated = [], [], []
    notes = []
    old_ids, new_ids = set(old.elements), set(new.elements)
    for eid in sorted(new_ids - old_ids):
        added.append(eid)
        el = new.elements[eid]
        notes.append(f"+ [{eid}] {el.role} {el.name!r}")
    for eid in sorted(old_ids - new_ids):
        removed.append(eid)
        el = old.elements[eid]
        notes.append(f"- [{eid}] {el.role} {el.name!r}")
    for eid in sorted(old_ids & new_ids):
        a, b = old.elements[eid], new.elements[eid]
        changes = []
        if (a.x, a.y, a.width, a.height) != (b.x, b.y, b.width, b.height):
            changes.append("bounds")
        if a.text != b.text:
            changes.append(f"text {a.text!r}->{b.text!r}")
        if a.states != b.states:
            changes.append("states")
        if a.name != b.name:
            changes.append("name")
        if changes:
            updated.append(eid)
            notes.append(f"* [{eid}] " + ", ".join(changes))
    return TreeDelta(added=added, removed=removed, updated=updated, notes=notes)
