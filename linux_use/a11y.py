"""Start and enable the AT-SPI accessibility bus on this Linux session."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .config import SETTINGS

_LAUNCHER = "/usr/libexec/at-spi-bus-launcher"
_READY = False


def _guess_dbus_address() -> str | None:
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return os.environ["DBUS_SESSION_BUS_ADDRESS"]
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    cand = Path(runtime) / "bus"
    if cand.exists():
        return f"unix:path={cand}"
    # Existence-checked fallbacks (including some agent-box layouts)
    n = _display_num()
    for p in (
        Path(f"/run/user/{os.getuid()}/bus"),
        Path(f"/tmp/xdg-runtime-box-{n}/bus"),
        Path("/tmp/xdg-runtime-box/bus"),
        Path(f"/tmp/xdg-runtime-{n}/bus"),
    ):
        if p.exists():
            return f"unix:path={p}"
    return None


def _display_num() -> str:
    d = (os.environ.get("DISPLAY") or SETTINGS.display or ":0").lstrip(":")
    return d.split(".")[0]


def apply_session_env() -> dict[str, str]:
    """Make sure DISPLAY, D-Bus and GTK accessibility flags are set for this process."""
    os.environ.setdefault("DISPLAY", SETTINGS.display)
    dbus = SETTINGS.dbus_session or _guess_dbus_address()
    if dbus:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = dbus
    if not os.environ.get("XDG_RUNTIME_DIR"):
        # Prefer the box-N dir that actually has the session bus
        n = _display_num()
        for cand in (
            f"/run/user/{os.getuid()}",
            f"/tmp/xdg-runtime-box-{n}",
            "/tmp/xdg-runtime-box",
            f"/tmp/xdg-runtime-{n}",
        ):
            if Path(cand).exists():
                os.environ["XDG_RUNTIME_DIR"] = cand
                break
    os.environ.setdefault("GTK_A11Y", "atspi")
    # atk-bridge lets GTK3 apps publish their tree
    modules = os.environ.get("GTK_MODULES", "")
    if "atk-bridge" not in modules:
        os.environ["GTK_MODULES"] = (modules + ":atk-bridge").strip(":")
    os.environ.setdefault("QT_ACCESSIBILITY", "1")
    os.environ["NO_AT_BRIDGE"] = "0"
    return dict(os.environ)


def _session_bus():
    import dbus
    apply_session_env()
    return dbus.SessionBus()


def a11y_bus_address() -> str | None:
    try:
        bus = _session_bus()
        obj = bus.get_object("org.a11y.Bus", "/org/a11y/bus")
        iface = dbus_iface(obj, "org.a11y.Bus")
        return str(iface.GetAddress())
    except Exception:
        return None


def dbus_iface(obj, name):
    import dbus
    return dbus.Interface(obj, name)


def _enable_status() -> None:
    try:
        import dbus
        bus = _session_bus()
        obj = bus.get_object("org.a11y.Bus", "/org/a11y/bus")
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        props.Set("org.a11y.Status", "IsEnabled", dbus.Boolean(True, variant_level=1))
        props.Set("org.a11y.Status", "ScreenReaderEnabled", dbus.Boolean(True, variant_level=1))
    except Exception:
        pass


def _launcher_running() -> bool:
    try:
        out = subprocess.check_output(["pgrep", "-f", "at-spi-bus-launcher"], text=True)
        return bool(out.strip())
    except subprocess.CalledProcessError:
        return False


def ensure_ready(timeout: float = 5.0) -> str:
    """Idempotently start at-spi and return the a11y bus address."""
    global _READY
    apply_session_env()
    addr = a11y_bus_address()
    if not addr and not _launcher_running() and Path(_LAUNCHER).exists():
        subprocess.Popen(
            [_LAUNCHER, "--launch-immediately"],
            env=apply_session_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            addr = a11y_bus_address()
            if addr:
                break
            time.sleep(0.1)
    if not addr:
        raise RuntimeError(
            "AT-SPI accessibility bus is not available. "
            "Install at-spi2-core and make sure a D-Bus session is running."
        )
    # libatspi/dbind otherwise invents a socket under XDG_RUNTIME_DIR and misses the real bus
    os.environ["AT_SPI_BUS_ADDRESS"] = addr
    _enable_status()
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize libatspi: {exc}") from exc
    _READY = True
    return addr


def child_env() -> dict[str, str]:
    """Environment for apps we launch so they export an accessibility tree."""
    env = apply_session_env()
    try:
        addr = a11y_bus_address()
        if addr:
            env["AT_SPI_BUS_ADDRESS"] = addr
    except Exception:
        pass
    return env
