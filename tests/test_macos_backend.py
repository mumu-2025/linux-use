
"""macOS backends with injected mocks (no pyobjc, runs on Linux)."""

from types import SimpleNamespace

from linux_use.platform.base import WindowInfo
from linux_use.platform.macos_a11y import MacAccessibility
from linux_use.platform.macos_desktop import MacDesktop
from linux_use.platform.macos_input import MacInput, Q_LEFT_DOWN, FLAG_CMD


class Pt(SimpleNamespace):
    pass


class Sz(SimpleNamespace):
    pass


class FakeAxEl:
    def __init__(self, ident, role, title, pos, size, children=None, **attrs):
        self.identifier = ident
        self._attrs = {
            "AXRole": role,
            "AXTitle": title,
            "AXPosition": Pt(x=pos[0], y=pos[1]),
            "AXSize": Sz(width=size[0], height=size[1]),
            "AXEnabled": True,
            "AXHidden": False,
            "AXFocused": False,
            "AXExpanded": False,
            "AXValue": "",
            "AXActions": ["AXPress"],
            "AXChildren": children or [],
        }
        self._attrs.update(attrs)


class FakeAx:
    def __init__(self, apps):
        self._apps = apps
        self.observer = None
        self.performed = []
        self.sets = []

    def applications(self):
        return list(self._apps)

    def get_attr(self, el, name):
        return el._attrs.get(name)

    def set_attr(self, el, name, value):
        el._attrs[name] = value
        self.sets.append((el.identifier, name, value))
        return True

    def perform(self, el, action):
        self.performed.append((el.identifier, action))
        return True

    def add_observer(self, cb):
        self.observer = cb


def _tree():
    btn = FakeAxEl("btn", "AXButton", "OK", (10, 10), (80, 24))
    edit = FakeAxEl("edit", "AXTextField", "body", (10, 40), (200, 24), AXValue="")
    app = FakeAxEl("app", "AXApplication", "TextEdit", (0, 0), (400, 300), children=[btn, edit])
    return app, btn, edit


def test_macos_snapshot_and_roles():
    app, btn, edit = _tree()
    ax = FakeAx([app])
    a11y = MacAccessibility(ax=ax)
    snap = a11y.snapshot(force=True)
    assert snap.refresh_mode == "full"
    roles = {el.role for el in snap.elements.values()}
    names = {el.name for el in snap.elements.values()}
    assert "button" in roles
    assert "text" in roles
    assert "OK" in names
    snap2 = a11y.snapshot()
    assert snap2.cache_hit is True


def test_macos_actions_and_observer_dirty():
    app, btn, edit = _tree()
    ax = FakeAx([app])
    a11y = MacAccessibility(ax=ax)
    snap = a11y.snapshot(force=True)
    ok_id = next(e.id for e in snap.elements.values() if e.name == "OK")
    text_id = next(e.id for e in snap.elements.values() if e.role == "text")
    assert a11y.do_action(ok_id, "click") is True
    assert ax.performed and ax.performed[0][1] == "AXPress"
    assert a11y.set_text(text_id, "hi") is True
    assert any(s[1] == "AXValue" and s[2] == "hi" for s in ax.sets)
    assert a11y.grab_focus(text_id) is True
    assert ax.observer is not None
    ax.observer("AXFocusedUIElementChanged")
    snap3 = a11y.snapshot()
    assert snap3.cache_hit is False
    assert snap3.refresh_mode in {"incremental", "full"}


def test_macos_desktop_mock():
    wins = [WindowInfo(wid="9", title="TextEdit", pid=3, x=0, y=0, width=200, height=100, active=True)]

    class API:
        def __init__(self):
            self.ops = []

        def enum_windows(self):
            return list(wins)

        def activate(self, w):
            self.ops.append(("focus", w.wid))

        def launch(self, command, args):
            self.ops.append(("launch", command, args))
            return 123

        def set_bounds(self, w, x, y, width, height):
            self.ops.append(("bounds", w.wid, x, y, width, height))

        def close(self, w):
            self.ops.append(("close", w.wid))

        def screen_size(self):
            return (1440, 900)

    d = MacDesktop(api=API())
    assert d.list_windows()[0].title == "TextEdit"
    assert d.focus_window("TextEdit").ok
    assert d.resize_window("9", 1, 2, 3, 4).ok
    assert d.close_window("TextEdit").ok
    assert d.launch_app("TextEdit").ok
    assert d.screen_size() == (1440, 900)


def test_macos_input_cgevent_events():
    class Poster:
        def __init__(self):
            self.events = []

        def post(self, evs):
            self.events.extend(evs)

        def cursor(self):
            return (4, 5)

    p = Poster()
    inp = MacInput(poster=p)
    assert inp.click(8, 9).ok
    assert any(e.get("etype") == Q_LEFT_DOWN for e in p.events)
    p.events.clear()
    assert inp.shortcut("cmd+s").ok
    assert any(e.get("kind") == "key" and (e.get("flags") or 0) & FLAG_CMD for e in p.events)
    p.events.clear()
    assert inp.type_text("Z").ok
    assert any(e.get("kind") == "unicode" and e.get("char") == "Z" for e in p.events)
    assert inp.mouse_location() == (4, 5)
    assert inp.scroll(1, 1, "up", 2).ok
    assert inp.drag(1, 1, 2, 2).ok


def test_macos_backend_refuses_live_api_on_linux():
    d = MacDesktop()
    try:
        d.list_windows()
        raise AssertionError("should not talk to real Cocoa on Linux")
    except RuntimeError as exc:
        assert "untested" in str(exc).lower() or "not available" in str(exc).lower()
