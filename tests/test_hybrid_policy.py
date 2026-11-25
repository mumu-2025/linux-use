
"""Hybrid decision rules, no live AT-SPI required."""

from linux_use.hybrid import HybridRouter, STATIC_CLASSES, DYNAMIC_CLASSES
from linux_use.locmap import LocationMap, LocEntry
from linux_use.tree import TreeSnapshot, UIElement
from linux_use.platform.base import ActionResult, WindowInfo


def _el(eid, role, name, **kw):
    states = kw.pop("states", ["showing", "visible", "enabled"])
    return UIElement(
        id=eid, role=role, name=name, path=eid, app=kw.pop("app", "Pad"),
        x=kw.pop("x", 10), y=kw.pop("y", 10),
        width=kw.pop("width", 80), height=kw.pop("height", 20),
        states=states, actions=kw.pop("actions", []), text=kw.pop("text", ""),
    )


class FakeA11y:
    def __init__(self, snap):
        self._snap = snap
        self.invalidated = []
        self.actions = []

    def snapshot(self, *, force=False):
        self._snap.refresh_mode = "full" if force else "incremental"
        self._snap.cache_hit = False
        self._snap.observe_ms = 1.0
        self._snap.walked = 3 if force else 1
        return self._snap

    def get_element(self, element_id):
        return self._snap.elements.get(element_id)

    def live_object(self, element_id):
        return None

    def do_action(self, element_id, action=None):
        self.actions.append(("do_action", element_id, action))
        return True

    def grab_focus(self, element_id):
        self.actions.append(("grab_focus", element_id))
        return True

    def set_text(self, element_id, text):
        el = self.get_element(element_id)
        if el is None:
            return False
        el.text = text
        self.actions.append(("set_text", element_id, text))
        return True

    def peek_menu_open(self, element_id):
        el = self.get_element(element_id)
        return bool(el and "expanded" in (el.states or []))

    def invalidate(self, reason="manual"):
        self.invalidated.append(reason)


class FakeDesktop:
    def __init__(self, geom=("1", 0, 0, 800, 600, "Pad")):
        self.geom = geom
        self.listed = 0

    def list_windows(self):
        self.listed += 1
        wid, x, y, w, h, title = self.geom
        return [WindowInfo(wid=wid, title=title, pid=1, x=x, y=y, width=w, height=h, active=True)]


class FakeInput:
    def __init__(self):
        self.calls = []

    def click(self, x, y, button="left", clicks=1):
        self.calls.append(("click", x, y, button, clicks))
        return ActionResult(True, f"clicked {x},{y}", act_ms=1.0)

    def type_text(self, text, *, clear=False, press_enter=False):
        self.calls.append(("type", text))
        return ActionResult(True, "typed", act_ms=1.0)

    def shortcut(self, keys):
        self.calls.append(("shortcut", keys))
        return ActionResult(True, f"shortcut {keys}", act_ms=1.0)


def _router(fresh=True):
    text = _el("e1", "text", "body", states=["showing", "visible", "editable", "enabled"], actions=[])
    file_m = _el("e2", "menu", "File", x=20, y=2, width=40, height=16, actions=["click"])
    snap = TreeSnapshot(elements={"e1": text, "e2": file_m}, roots=["e2", "e1"],
                        generation=1, refresh_mode="full", node_count=2, walked=2)
    a11y = FakeA11y(snap)
    desk = FakeDesktop()
    loc = LocationMap()
    loc.ingest(snap, desk.list_windows())
    inp = FakeInput()
    router = HybridRouter(a11y, desk, inp, loc)
    if fresh:
        router.last_snap = snap
    return router, a11y, desk, loc, inp, snap


def test_static_and_dynamic_class_sets():
    assert "menu" in STATIC_CLASSES
    assert "button" in STATIC_CLASSES
    assert "menu_item" not in STATIC_CLASSES  # ephemeral; geom check is not enough
    assert "text" in DYNAMIC_CLASSES


def test_fresh_cache_is_preferred_over_locmap():
    router, a11y, desk, loc, inp, snap = _router(fresh=True)
    listed_before = desk.listed
    dec = router.decide(name="File", role="menu")
    assert dec.path == "cache"
    assert dec.element_id == "e2"
    assert dec.use_atspi is True
    # In-memory locate must not call list_windows (that would be the locmap geom check)
    assert desk.listed == listed_before


def test_locmap_used_when_cache_is_cold_and_static_and_valid():
    router, a11y, desk, loc, inp, snap = _router(fresh=True)
    router.last_snap = None  # no fresh tree, but locmap still populated
    dec = router.decide(name="File", role="menu")
    assert dec.path == "locmap"
    assert dec.element_id == "e2"
    assert dec.x is not None


def test_dynamic_or_unknown_falls_back_to_refresh_when_cold():
    router, a11y, desk, loc, inp, snap = _router(fresh=True)
    router.last_snap = None
    loc.entries.clear()  # unknown target
    dec = router.decide(name="Missing", role="button")
    assert dec.path in {"cache-refresh", "full", "miss"}


def test_click_prefers_atspi_do_action():
    router, a11y, desk, loc, inp, snap = _router(fresh=True)
    res = router.click(name="File", role="menu")
    assert res.ok
    assert res.data["path"] == "cache"
    assert ("do_action", "e2", "click") in a11y.actions
    assert inp.calls == []  # no coordinate click


def test_type_prefers_set_text_and_verifies():
    router, a11y, desk, loc, inp, snap = _router(fresh=True)
    res = router.type_text("HELLO", element_id="e1")
    assert res.ok
    assert res.data["path"] == "atspi"
    assert any(c[0] == "set_text" for c in a11y.actions)
    assert "HELLO" in (a11y.get_element("e1").text or "")


def test_logs_path_for_each_step():
    router, a11y, desk, loc, inp, snap = _router(fresh=True)
    router.click(name="File", role="menu")
    logs = router.consume_logs()
    assert logs
    assert any(lg["step"] == "click" and lg["path"] == "cache" for lg in logs)
