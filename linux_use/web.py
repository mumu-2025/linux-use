"""Helpers for web-toolkit widgets (Chrome/Firefox/etc.) vs native GTK/Qt fields."""

from __future__ import annotations

from typing import Any

WEB_APP_MARKERS = (
    "chrome", "chromium", "firefox", "mozilla", "msedge", "edge",
    "brave", "opera", "vivaldi", "webkit", "epiphany", "gnome-web",
)

OMNIBOX_NAME_MARKERS = (
    "address and search bar",
    "address bar",
    "omnibox",
    "search or enter address",
    "search or enter website",
)

TEXT_ROLES = {
    "entry", "edit", "text", "combo box", "search", "password text",
    "password", "text box", "autocomplete",
}

ROLE_ALIASES = {
    "entry": TEXT_ROLES,
    "edit": TEXT_ROLES,
    "text": TEXT_ROLES,
    "combo box": TEXT_ROLES,
    "search": TEXT_ROLES,
    "button": {"button", "push button", "toggle button"},
    "page tab": {"page tab", "tab"},
    "link": {"link"},
    "menu": {"menu", "menu item"},
    "heading": {"heading"},
}

# AT-SPI roles whose descendants are webpage guts. Walking them is what made
# a "full" inspect take 13s on a Bing tab (2026-11-26). The document node
# itself is kept (title is useful); children are skipped unless include_web.
WEB_DOCUMENT_ROLES = {"document web"}


def _norm(s: str | None) -> str:
    return " ".join((s or "").split()).casefold()


def is_omnibox(el: Any) -> bool:
    if el is None:
        return False
    name = _norm(getattr(el, "name", "") or "")
    return any(m in name for m in OMNIBOX_NAME_MARKERS)


def is_web_app(app: str | None) -> bool:
    key = (app or "").casefold()
    return any(m in key for m in WEB_APP_MARKERS)


def is_web_toolkit_field(el: Any) -> bool:
    """True for Chrome/Firefox (etc.) text fields where AT-SPI set_text is unreliable.

    Native GTK/Qt editors (Mousepad, gedit, …) must stay on the set_text path.
    """
    if el is None:
        return False
    if is_omnibox(el):
        return True
    role = (getattr(el, "role", "") or "").casefold()
    if role not in TEXT_ROLES:
        return False
    return is_web_app(getattr(el, "app", "") or "")


def roles_match(have: str | None, want: str | None) -> bool:
    if not want:
        return True
    have_n = (have or "").strip().casefold()
    want_n = want.strip().casefold()
    if have_n == want_n:
        return True
    aliases = ROLE_ALIASES.get(want_n) or ROLE_ALIASES.get(have_n)
    if aliases and have_n in aliases and want_n in aliases:
        return True
    return False


def name_matches(el: Any, want_name: str) -> tuple[bool, bool]:
    """Return (is_match, is_exact) against name, text, or value."""
    want = _norm(want_name)
    if not want:
        return True, False
    fields = [
        _norm(getattr(el, "name", "") or ""),
        _norm(getattr(el, "text", "") or ""),
        _norm(getattr(el, "value", "") or ""),
    ]
    if want in fields:
        return True, True
    if any(want in f for f in fields if f):
        return True, False
    return False, False
