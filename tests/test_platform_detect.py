
from linux_use.platform import current_os, hardware_tested, get_backends, HARDWARE_TESTED_OS


def test_linux_is_detected_and_tested():
    assert current_os() == "linux"
    assert hardware_tested() is True
    assert "linux" in HARDWARE_TESTED_OS
    assert "windows" not in HARDWARE_TESTED_OS
    assert "macos" not in HARDWARE_TESTED_OS


def test_get_backends_on_linux():
    a11y, desktop, inp = get_backends()
    assert a11y.__class__.__name__ == "LinuxAccessibility"
    assert desktop.__class__.__name__ == "LinuxDesktop"
    assert inp.__class__.__name__ == "LinuxInput"


def test_windows_macos_importable_and_marked_untested():
    from linux_use.platform.windows import (
        HARDWARE_TESTED as W, WindowsAccessibility, WindowsDesktop, WindowsInput,
    )
    from linux_use.platform.macos import (
        HARDWARE_TESTED as M, MacAccessibility, MacDesktop, MacInput,
    )
    assert W is False
    assert M is False
    assert WindowsAccessibility(uia=object()).HARDWARE_TESTED is False
    assert MacAccessibility(ax=object()).HARDWARE_TESTED is False
    # Classes exist and inherit the shared interface
    assert hasattr(WindowsAccessibility, "snapshot")
    assert hasattr(MacAccessibility, "do_action")
    assert hasattr(WindowsInput, "click")
    assert hasattr(MacInput, "shortcut")
    assert hasattr(WindowsDesktop, "list_windows")
    assert hasattr(MacDesktop, "launch_app")
