"""Runtime configuration from environment. No secrets are hardcoded."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _file_roots() -> tuple[Path, ...]:
    roots = [Path.home(), Path("/tmp"), Path.cwd()]
    extra = env("LINUX_USE_FILE_ROOTS")
    if extra:
        roots.extend(Path(p) for p in extra.split(os.pathsep) if p)
    # Optional workspace dir if present (some agent boxes use /workspace)
    ws = Path("/workspace")
    if ws.exists():
        roots.append(ws)
    # Deduplicate while preserving order
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return tuple(out)


SAFE_FILE_ROOTS = _file_roots()


@dataclass
class Settings:
    display: str = field(default_factory=lambda: env("DISPLAY") or ":0")
    dbus_session: str | None = field(default_factory=lambda: env("DBUS_SESSION_BUS_ADDRESS"))
    xdg_runtime_dir: str | None = field(default_factory=lambda: env("XDG_RUNTIME_DIR"))
    api_key: str | None = field(
        default_factory=lambda: env("LINUX_USE_API_KEY") or env("OPENAI_API_KEY")
    )
    base_url: str = field(
        default_factory=lambda: env("LINUX_USE_BASE_URL")
        or env("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model: str = field(
        default_factory=lambda: env("LINUX_USE_MODEL") or env("OPENAI_MODEL") or "gpt-4o-mini"
    )
    max_steps: int = 25
    max_tree_nodes: int = 400
    max_text_chars: int = 240
    allow_shell: bool = True
    allow_all_files: bool = False
    log_dir: Path = field(
        default_factory=lambda: Path(env("LINUX_USE_LOG_DIR") or "smoke-logs")
    )


SETTINGS = Settings()
