"""Optional screenshot helper (not the primary observation path)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .a11y import child_env


def take_screenshot(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    env = child_env()
    if shutil.which("scrot"):
        proc = subprocess.run(["scrot", "-o", "-z", str(path)], capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or "scrot failed")
        return path
    if shutil.which("ffmpeg"):
        disp = env.get("DISPLAY", ":4")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "x11grab", "-video_size", "1920x1200",
             "-i", disp, "-frames:v", "1", str(path)],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or "ffmpeg screenshot failed")
        return path
    raise RuntimeError("No screenshot tool available (scrot or ffmpeg)")
