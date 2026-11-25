
"""Windows backends with injected mocks (no COM, no SendInput, runs on Linux)."""

from types import SimpleNamespace

from linux_use.platform.base import WindowInfo
from linux_use.platform.windows_a11y import PATTERN_INVOKE, PATTERN_VALUE, WindowsAccessibility
from linux_use.platform.windows_desktop import WindowsDesktop
from linux_use.platform.windows_input import WindowsInput, MOUSEEVENTF_LEFTDOWN, KEYEVENTF_UNICODE


class Rect(SimpleNamespace):
    pass


class InvokePat:
    def __init__(self):
        self.invoked = 0

    def Invoke(self):
        self.invoked += 1


class ValuePat:
    def __init__(self, value=""):
        self.CurrentValue = value
        self.written = None

    def SetValue(self, text):
        self.written = text
        self.CurrentValue = text


class FakeEl:
    def __init__(self, name, ctype, rect, children=None, patterns=None,
                 enabled=True, offscreen=False, focus=False, runtime="1-1", pid=9):
        self.CurrentName = name
        self.CurrentControlType = ctype
        self.CurrentBoundingRectangle = rect
        self.CurrentIsEnabled = enabled
        self.CurrentIsOffscreen = offscreen
        self.CurrentHasKeyboardFocus = focus
        self.CurrentProcessId = pid
        self._children = children or []
        self._patterns = patterns or {}
        self._runtime = runtime
        self.focused_calls = 0

    def GetRuntimeId(self):
        return [int(x) for x in self._runtime.split("-")]

    def GetCurrentPattern(self, pid):
        return self._patterns.get(pid)

    def GetChildren(self):
        return list(self._children)

    def SetFocus(self):
        self.CurrentHasKeyboardFocus = True
        self.focused_calls += 1
        return True


class FakeUIA:
    def __init__(self, root):
        self._root = root
        self.focus_handlers = []
        self.structure_handlers = []

    def GetRootElement(self):
        return self._root

    def AddFocusChangedEventHandler(self, _cache, handler):
        self.focus_handlers.append(handler)

    def AddStructureChangedEventHandler(self, _root, _scope, _cache, handler):
        self.structure_handlers.append(handler)


def _tree():
    invoke = InvokePat()
    value = ValuePat("")
    btn = FakeEl("OK", 50000, Rect(left=10, top=10, right=90, bottom=40),
                 patterns={PATTERN_INVOKE: invoke}, runtime="1-10")
    edit = FakeEl("body", 50004, Rect(left=10, top=50, right=200, bottom=80),
                 patterns={PATTERN_VALUE: value}, runtime="1-11")
    root = FakeEl("Notepad", 50032, Rect(left=0, top=0, right=400, bottom=300),
                  children=[btn, edit], runtime="1")
    return root, btn, edit, invoke, value


def test_windows_snapshot_and_roles():
    root, btn, edit, invoke, value = _tree()
    a11y = WindowsAccessibility(uia=FakeUIA(root))
    snap = a11y.snapshot(force=True)
    assert snap.refresh_mode == "full"
    assert snap.node_count >= 3
    roles = {el.role for el in snap.elements.values()}
    names = {el.name for el in snap.elements.values()}
    assert "button" in roles
    assert "text" in roles
    assert "OK" in names
    # second snapshot with no events is a cache hit
    snap2 = a11y.snapshot()
    assert snap2.cache_hit is True
    assert snap2.refresh_mode == "cache"
    assert snap2.walked == 0


def test_windows_do_action_set_text_focus_and_events():
    root, btn, edit, invoke, value = _tree()
    uia = FakeUIA(root)
    a11y = WindowsAccessibility(uia=uia)
    snap = a11y.snapshot(force=True)
    ok_id = next(e.id for e in snap.elements.values() if e.name == "OK")
    text_id = next(e.id for e in snap.elements.values() if e.role == "text")
    assert a11y.do_action(ok_id, "click") is True
    assert invoke.invoked == 1
    assert a11y.set_text(text_id, "hello") is True
    assert value.written == "hello"
    assert a11y.grab_focus(text_id) is True
    el = a11y.get_element(text_id)
    assert el.focused
    # Fire a focus event and expect incremental, not a silent cache hit
    assert uia.focus_handlers, "event subscription should register a focus handler"
    uia.focus_handlers[0].HandleFocusChangedEvent(edit)
    snap3 = a11y.snapshot()
    assert snap3.cache_hit is False
    assert snap3.refresh_mode in {"incremental", "full"}


def test_windows_desktop_mock():
    wins = [WindowInfo(wid="42", title="Notes", pid=7, x=1, y=2, width=100, height=50, active=True)]

    class API:
        def __init__(self):
            self.focused = None
            self.moved = None
            self.closed = None

        def enum_windows(self):
            return list(wins)

        def set_foreground(self, wid):
            self.focused = wid

        def move_window(self, wid, x, y, w, h):
            self.moved = (wid, x, y, w, h)

        def close_window(self, wid):
            self.closed = wid

        def screen_size(self):
            return (1920, 1080)

    launched = {}

    def popen(cmd):
        launched["cmd"] = cmd
        return SimpleNamespace(pid=99)

    d = WindowsDesktop(api=API(), popen=popen)
    listed = d.list_windows()
    assert listed[0].title == "Notes"
    assert d.focus_window("Notes").ok
    assert d.resize_window("42", 0, 0, 200, 100).ok
    assert d.close_window("Notes").ok
    assert d.launch_app("notepad.exe", ["a.txt"]).ok
    assert launched["cmd"][0] == "notepad.exe"
    assert d.screen_size() == (1920, 1080)


def test_windows_input_sendinput_events():
    class Sender:
        def __init__(self):
            self.events = []

        def send(self, evs):
            self.events.extend(evs)

        def cursor(self):
            return (11, 22)

        def screen_size(self):
            return (1920, 1080)

    s = Sender()
    inp = WindowsInput(sender=s)
    assert inp.click(100, 200).ok
    kinds = [e["kind"] for e in s.events]
    assert "mouse" in kinds
    assert any(e.get("flags") == MOUSEEVENTF_LEFTDOWN for e in s.events if e["kind"] == "mouse")
    s.events.clear()
    assert inp.type_text("A").ok
    assert any(e["kind"] == "unicode" and e["char"] == "A" for e in s.events)
    s.events.clear()
    assert inp.shortcut("ctrl+s").ok
    vks = [e["vk"] for e in s.events if e["kind"] == "key"]
    assert 0x11 in vks and ord("S") in vks
    assert inp.mouse_location() == (11, 22)
    assert inp.scroll(10, 10, "down", 3).ok
    assert inp.drag(1, 1, 5, 5).ok


def test_windows_backend_refuses_live_api_on_linux():
    d = WindowsDesktop()
    try:
        d.list_windows()
        raise AssertionError("should not talk to real Win32 on Linux")
    except RuntimeError as exc:
        assert "untested" in str(exc).lower() or "not available" in str(exc).lower()
