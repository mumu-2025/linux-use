"""Stable Python API for this bot and other agents."""

from __future__ import annotations

from typing import Any

from .platform import get_backends
from .tools import Toolbelt
from .tree import TreeSnapshot

_a11y = _desktop = _input = _tools = None


def _ctx():
    global _a11y, _desktop, _input, _tools
    if _tools is None:
        _a11y, _desktop, _input = get_backends()
        _a11y.ensure_ready()
        _tools = Toolbelt(_a11y, _desktop, _input, experimental=True)
    return _a11y, _desktop, _input, _tools


def inspect_desktop(force: bool = False) -> TreeSnapshot:
    a11y, *_ = _ctx()
    return a11y.snapshot(force=force)


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
