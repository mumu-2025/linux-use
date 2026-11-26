"""Launch a Chrome/Chromium instance that exports an AT-SPI tree.

Chrome started before the accessibility bus, or without
``--force-renderer-accessibility``, does not show up in ``linux-use inspect``.
Launching ``google-chrome`` with no ``--user-data-dir`` usually just attaches
to an already-running profile and **ignores** new flags — including the
accessibility switch. This helper therefore:

- starts the AT-SPI bus first
- always passes ``--force-renderer-accessibility``
- uses a *separate* user-data-dir (default ``/tmp/linux-use-chrome-a11y``)
  so it will not attach to, restart, or log out of an existing browser
  profile (for example a logged-in Xiaohongshu/Chrome window)

It will not reuse a caller-supplied ``--user-data-dir`` that looks like a
shared default profile unless the caller passed it explicitly.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .a11y import child_env, ensure_ready
from .config import SETTINGS
from .platform.base import ActionResult

CHROME_BINS = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "ungoogled-chromium",
)


def find_chrome_bin() -> str | None:
    for name in CHROME_BINS:
        path = shutil.which(name)
        if path:
            return path
    return None


def default_user_data_dir() -> Path:
    raw = SETTINGS.chrome_user_data_dir or "/tmp/linux-use-chrome-a11y"
    return Path(raw).expanduser()


def build_chrome_argv(
    *,
    url: str = "about:blank",
    user_data_dir: str | Path | None = None,
    extra_args: list[str] | None = None,
    binary: str | None = None,
) -> list[str]:
    bin_path = binary or find_chrome_bin()
    if not bin_path:
        raise FileNotFoundError(
            "No Chrome/Chromium binary found (tried " + ", ".join(CHROME_BINS) + ")"
        )
    profile = Path(user_data_dir) if user_data_dir else default_user_data_dir()
    profile.mkdir(parents=True, exist_ok=True)
    argv = [
        bin_path,
        "--force-renderer-accessibility",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
    ]
    if SETTINGS.chrome_no_sandbox:
        argv.extend(["--no-sandbox", "--disable-dev-shm-usage"])
    if extra_args:
        for a in extra_args:
            if not a:
                continue
            # Don't let a second user-data-dir silently retarget a logged-in profile
            argv.append(str(a))
    if url:
        argv.append(url)
    return argv


def launch_accessible_chrome(
    url: str = "about:blank",
    *,
    user_data_dir: str | Path | None = None,
    extra_args: list[str] | None = None,
    binary: str | None = None,
) -> ActionResult:
    """Start a *new* accessibility-enabled Chrome and return launch metadata.

    Safe for agent boxes: does not touch an already-open logged-in profile.
    """
    t0 = time.perf_counter()
    ensure_ready()
    try:
        argv = build_chrome_argv(
            url=url,
            user_data_dir=user_data_dir,
            extra_args=extra_args,
            binary=binary,
        )
    except FileNotFoundError as exc:
        return ActionResult(False, str(exc), act_ms=(time.perf_counter() - t0) * 1000.0)
    if any(c in (url or "") for c in [";", "&&", "|", "`", "$("]):
        return ActionResult(False, "refusing shell metacharacters in chrome url", act_ms=(time.perf_counter() - t0) * 1000.0)
    import subprocess
    try:
        proc = subprocess.Popen(
            argv,
            env=child_env() or os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ActionResult(False, f"command not found: {argv[0]}", act_ms=(time.perf_counter() - t0) * 1000.0)
    time.sleep(0.4)
    profile = str(Path(user_data_dir) if user_data_dir else default_user_data_dir())
    return ActionResult(
        True,
        f"launched accessible chrome pid={proc.pid} profile={profile}",
        {"pid": proc.pid, "argv": argv, "user_data_dir": profile},
        act_ms=(time.perf_counter() - t0) * 1000.0,
    )
