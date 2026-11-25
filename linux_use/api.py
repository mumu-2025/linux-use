"""Stable Python API for this bot and other agents."""

from __future__ import annotations

from typing import Any

from .locmap import LocationMap
from .platform import get_backends
from .tools import Toolbelt
from .tree import TreeSnapshot

_a11y = _desktop = _input = _tools = None
_locmap = LocationMap()


def _ctx():
    global _a11y, _desktop, _input, _tools
    if _tools is None:
        _a11y, _desktop, _input = get_backends()
        _a11y.ensure_ready()
        _tools = Toolbelt(_a11y, _desktop, _input, experimental=True, locmap=_locmap)
    return _a11y, _desktop, _input, _tools


def inspect_desktop(force: bool = False) -> TreeSnapshot:
    a11y, desktop, _, tools = _ctx()
    snap = a11y.snapshot(force=force)
    try:
        tools.locmap.ingest(snap, desktop.list_windows())
    except Exception:
        pass
    return snap


def act(tool: str, **kwargs) -> Any:
    """Run one scripted tool without an LLM, e.g. act('type', text='hello', id='e12')."""
    *_, tools = _ctx()
    return tools.call(tool, kwargs)


def scripted(steps: list[dict[str, Any]]) -> list[Any]:
    """Run a list of {tool, ...args} dicts. Example: [{'tool':'click','id':'e12'}]."""
    results = []
    for step in steps:
        step = dict(step)
        name = step.pop("tool")
        results.append(act(name, **step))
    return results


def get_location_map(*, refresh: bool = False):
    """Return the classified element location map, refreshing from a snapshot if needed."""
    *_, tools = _ctx()
    if refresh or not tools.locmap.entries:
        inspect_desktop(force=refresh)
    return tools.locmap
