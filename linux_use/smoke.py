"""Controlled smoke test on a throwaway Mousepad window. Measures observe vs act latency."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .a11y import child_env, ensure_ready
from .config import SETTINGS
from .platform import get_backends
from .screenshot import take_screenshot
from .tools import Toolbelt

MARKER = "LINUX_USE_SMOKE_OK_20261125"
LOG_DIR = SETTINGS.log_dir
PAD = Path("/tmp/linux-use-smoke-pad.txt")
TITLE = "linux-use-smoke-pad.txt"


def _close_leftovers(desktop) -> None:
    for w in desktop.list_windows():
        if "linux-use-smoke-pad" in w.title.lower() or PAD.name in w.title.lower():
            desktop.close_window(w.wid)
            time.sleep(0.2)


def _bench_location_map(a11y, desktop, tools, text_el, results: dict) -> None:
    """Compare intent-to-click with a forced tree walk vs the classified location map."""
    import time as _time
    from .locmap import LocationMap

    # Make sure File menu is closed
    try:
        tools.shortcut("Escape")
    except Exception:
        pass
    _time.sleep(0.15)

    # --- without map: forced full snapshot + search + click ---
    t0 = _time.perf_counter()
    snap = a11y.snapshot(force=True)
    target = None
    for el in snap.elements.values():
        if el.role == "menu" and (el.name or "").strip().lower() == "file" and el.bounds_valid:
            target = el
            break
    observe_ms = snap.observe_ms
    walked = snap.walked
    click_ms = 0.0
    if target is not None:
        cres = tools.input.click(target.cx, target.cy)
        click_ms = cres.act_ms
        ok_wo = cres.ok
    else:
        ok_wo = False
    without_ms = (_time.perf_counter() - t0) * 1000.0
    try:
        tools.shortcut("Escape")
    except Exception:
        pass
    _time.sleep(0.15)

    # Ingest map from the snapshot we already have, plus current window list
    loc = tools.locmap
    loc.ingest(snap, desktop.list_windows())
    build_ms = loc.built_ms

    # --- with map: lookup + cheap validity + click cached coords (no tree walk) ---
    t1 = _time.perf_counter()
    resolved = loc.resolve(
        name="File", role="menu", a11y=a11y, desktop=desktop, refresh_if_stale=False,
    )
    click2_ms = 0.0
    ok_w = False
    if resolved.ok and resolved.x is not None:
        cres2 = tools.input.click(int(resolved.x), int(resolved.y))
        click2_ms = cres2.act_ms
        ok_w = cres2.ok
    with_ms = (_time.perf_counter() - t1) * 1000.0
    try:
        tools.shortcut("Escape")
    except Exception:
        pass
    _time.sleep(0.1)

    # Second warm hit (map already used once)
    t2 = _time.perf_counter()
    resolved2 = loc.resolve(
        name="File", role="menu", a11y=a11y, desktop=desktop, refresh_if_stale=False,
    )
    click3_ms = 0.0
    if resolved2.ok and resolved2.x is not None:
        cres3 = tools.input.click(int(resolved2.x), int(resolved2.y))
        click3_ms = cres3.act_ms
    with_warm_ms = (_time.perf_counter() - t2) * 1000.0
    try:
        tools.shortcut("Escape")
    except Exception:
        pass

    results["location_map"] = {
        "without_map_intent_ms": round(without_ms, 2),
        "without_map_observe_ms": round(observe_ms, 2),
        "without_map_click_ms": round(click_ms, 2),
        "without_map_walked": walked,
        "without_map_ok": ok_wo,
        "map_build_ms": round(build_ms, 2),
        "map_entries": len(loc.entries),
        "with_map_intent_ms": round(with_ms, 2),
        "with_map_lookup_ms": round(resolved.lookup_ms, 2),
        "with_map_valid_ms": round(resolved.valid_check_ms, 2),
        "with_map_click_ms": round(click_ms if False else click2_ms, 2),
        "with_map_walked": resolved.walked,
        "with_map_source": resolved.source,
        "with_map_ok": ok_w,
        "with_map_warm_intent_ms": round(with_warm_ms, 2),
        "with_map_warm_lookup_ms": round(resolved2.lookup_ms, 2),
        "with_map_warm_valid_ms": round(resolved2.valid_check_ms, 2),
        "with_map_warm_click_ms": round(click3_ms, 2),
        "with_map_warm_source": resolved2.source,
        "speedup_vs_full": (round(without_ms / with_ms, 2) if with_ms else None),
        "speedup_warm_vs_full": (round(without_ms / with_warm_ms, 2) if with_warm_ms else None),
        "locate_without_ms": round(without_ms - click_ms, 2),
        "locate_with_ms": round(with_ms - click2_ms, 2),
        "locate_warm_ms": round(with_warm_ms - click3_ms, 2),
        "target": "menu File",
    }
    lm = results["location_map"]
    if lm["locate_with_ms"]:
        lm["locate_speedup"] = round(lm["locate_without_ms"] / lm["locate_with_ms"], 2)
    if lm["locate_warm_ms"]:
        lm["locate_warm_speedup"] = round(lm["locate_without_ms"] / lm["locate_warm_ms"], 2)
    results["steps"].append({
        "name": "map_click_without_tree_walk",
        "ok": ok_wo,
        "act_ms": round(without_ms, 2),
        "msg": f"full-scan+click intent={without_ms:.1f}ms observe={observe_ms:.1f}ms walked={walked}",
    })
    results["steps"].append({
        "name": "map_click_with_location_map",
        "ok": ok_w and resolved.source == "map",
        "act_ms": round(with_ms, 2),
        "msg": (
            f"map {resolved.source} intent={with_ms:.1f}ms lookup={resolved.lookup_ms:.2f}ms "
            f"valid={resolved.valid_check_ms:.2f}ms walked={resolved.walked} "
            f"warm={with_warm_ms:.1f}ms speedup={results['location_map']['speedup_vs_full']}x"
        ),
    })


def run_smoke(keep_window: bool = False) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    results: dict = {"steps": [], "ok": False}
    ensure_ready()
    a11y, desktop, inp = get_backends()
    a11y.ensure_ready()
    tools = Toolbelt(a11y, desktop, inp, experimental=True)

    _close_leftovers(desktop)
    time.sleep(0.4)
    # Drain teardown events so they do not pollute the first snapshot.
    a11y.snapshot(force=False)
    PAD.write_text("", encoding="utf-8")

    before = LOG_DIR / "01-before.png"
    try:
        take_screenshot(before)
        results["screenshot_before"] = str(before)
    except Exception as exc:
        results["screenshot_before_error"] = str(exc)

    launch = desktop.launch_app("mousepad", [str(PAD)])
    results["steps"].append({"name": "launch_mousepad", "ok": launch.ok, "act_ms": launch.act_ms, "msg": launch.message})
    # Give the window and AT-SPI registration a moment
    deadline = time.time() + 6
    appeared = False
    while time.time() < deadline:
        wins = [w for w in desktop.list_windows() if "Mousepad" in w.title or PAD.name in w.title]
        if wins:
            appeared = True
            break
        time.sleep(0.15)
    a11y.invalidate("smoke-launch")
    # Wait until Mousepad actually published an editable text node (not just a window).
    ready = None
    deadline = time.time() + 15
    while time.time() < deadline:
        snap = a11y.snapshot(force=True)
        for el in snap.elements.values():
            if el.role == "text" and "editable" in el.states and el.bounds_valid:
                ready = snap
                break
        if ready:
            break
        time.sleep(0.2)
    if ready is None:
        results["error"] = "mousepad window appeared but no editable text node in AT-SPI tree"
        (LOG_DIR / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(results, indent=2, ensure_ascii=False))
        if not keep_window:
            _close_leftovers(desktop)
        return 1

    # Cold / forced full scan (tree is up; this is the measured full walk)
    t_cold = a11y.snapshot(force=True)
    results["steps"].append({
        "name": "observe_cold_full",
        "ok": t_cold.node_count > 0,
        "observe_ms": round(t_cold.observe_ms, 2),
        "mode": t_cold.refresh_mode,
        "cache_hit": t_cold.cache_hit,
        "nodes": t_cold.node_count,
        "walked": t_cold.walked,
        "focused": t_cold.focused_id,
        "active_app": t_cold.active_app,
    })
    (LOG_DIR / "tree-cold.txt").write_text(t_cold.observation(), encoding="utf-8")

    # Immediate second observe: should be a cache hit
    t_warm = a11y.snapshot(force=False)
    results["steps"].append({
        "name": "observe_warm_cache",
        "ok": t_warm.observe_ms < 20 or t_warm.cache_hit or t_warm.refresh_mode in ("cache", "incremental"),
        "observe_ms": round(t_warm.observe_ms, 2),
        "mode": t_warm.refresh_mode,
        "cache_hit": t_warm.cache_hit,
        "nodes": t_warm.node_count,
        "walked": t_warm.walked,
        "events": t_warm.events,
    })
    (LOG_DIR / "tree-warm.txt").write_text(t_warm.observation(), encoding="utf-8")

    text_el = None
    for el in t_cold.elements.values():
        if el.role == "text" and "editable" in el.states and el.bounds_valid:
            text_el = el
            if el.focused:
                break
    if text_el is None:
        results["error"] = "no editable text element found in Mousepad tree"
        (LOG_DIR / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(results, indent=2, ensure_ascii=False))
        if not keep_window:
            _close_leftovers(desktop)
        return 1

    results["text_element"] = {
        "id": text_el.id, "role": text_el.role, "bounds": [text_el.x, text_el.y, text_el.width, text_el.height],
        "states": text_el.states, "path": text_el.path,
    }

    desktop.focus_window(TITLE)
    click_res = tools.click(id=text_el.id)
    results["steps"].append({
        "name": "click_text_element",
        "ok": click_res.ok,
        "act_ms": round(click_res.act_ms, 2),
        "msg": click_res.message,
    })

    type_res = tools.type_text(MARKER, id=text_el.id, clear=True)
    results["steps"].append({
        "name": "type_marker",
        "ok": type_res.ok,
        "act_ms": round(type_res.act_ms, 2),
        "msg": type_res.message,
    })

    # Observation after typing: expect incremental (text-changed) or cheap refresh, not a needless full walk of hidden menus
    time.sleep(0.05)
    t_after = a11y.snapshot(force=False)
    results["steps"].append({
        "name": "observe_after_type",
        "ok": True,
        "observe_ms": round(t_after.observe_ms, 2),
        "mode": t_after.refresh_mode,
        "cache_hit": t_after.cache_hit,
        "nodes": t_after.node_count,
        "walked": t_after.walked,
        "events": t_after.events,
        "text_now": (t_after.get(text_el.id).text if t_after.get(text_el.id) else None),
        "delta_notes": (t_after.delta.notes[:8] if t_after.delta else []),
    })
    (LOG_DIR / "tree-after-type.txt").write_text(t_after.observation(), encoding="utf-8")

    save_res = tools.shortcut("ctrl+s")
    results["steps"].append({
        "name": "shortcut_ctrl_s",
        "ok": save_res.ok,
        "act_ms": round(save_res.act_ms, 2),
        "msg": save_res.message,
    })
    time.sleep(0.25)
    file_text = PAD.read_text(encoding="utf-8") if PAD.exists() else ""
    file_ok = MARKER in file_text
    # If AT-SPI set_text didn't persist, the buffer may still have it; try one fallback via xdotool if file empty
    if not file_ok:
        tools.click(id=text_el.id)
        tools.type_text(MARKER, clear=True)
        tools.shortcut("ctrl+s")
        time.sleep(0.3)
        file_text = PAD.read_text(encoding="utf-8") if PAD.exists() else ""
        file_ok = MARKER in file_text
        results["steps"].append({
            "name": "retry_type_and_save",
            "ok": file_ok,
            "file_text": file_text[:200],
        })

    results["file_contains_marker"] = file_ok
    results["file_text"] = file_text[:200]
    results["steps"].append({"name": "verify_file", "ok": file_ok, "msg": file_text[:200]})

    after = LOG_DIR / "02-after.png"
    try:
        take_screenshot(after)
        results["screenshot_after"] = str(after)
    except Exception as exc:
        results["screenshot_after_error"] = str(exc)

    # One more cache-hit observe after UI settled
    t_final = a11y.snapshot(force=False)
    results["steps"].append({
        "name": "observe_final",
        "ok": True,
        "observe_ms": round(t_final.observe_ms, 2),
        "mode": t_final.refresh_mode,
        "cache_hit": t_final.cache_hit,
        "walked": t_final.walked,
        "events": t_final.events,
    })

    _bench_location_map(a11y, desktop, tools, text_el, results)

    if not keep_window:
        close = desktop.close_window(TITLE)
        # If mousepad asks to save, try Enter / Escape carefully only on that dialog
        time.sleep(0.3)
        still = [w for w in desktop.list_windows() if "Mousepad" in w.title or "Save" in w.title]
        if still:
            # dismiss save dialog if any, then close
            inp.shortcut("Escape")
            time.sleep(0.15)
            desktop.close_window(TITLE)
        results["steps"].append({"name": "close_mousepad", "ok": close.ok, "msg": close.message})

    observe_times = [s["observe_ms"] for s in results["steps"] if "observe_ms" in s]
    act_times = [s["act_ms"] for s in results["steps"] if "act_ms" in s]
    results["summary"] = {
        "window_appeared": appeared,
        "file_ok": file_ok,
        "observe_ms": observe_times,
        "act_ms": act_times,
        "cold_observe_ms": results["steps"][1]["observe_ms"] if len(results["steps"]) > 1 else None,
        "warm_observe_ms": results["steps"][2]["observe_ms"] if len(results["steps"]) > 2 else None,
    }
    results["ok"] = bool(appeared and file_ok and t_cold.node_count > 0)

    (LOG_DIR / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    _print_report(results)
    return 0 if results["ok"] else 1


def _print_report(results: dict) -> None:
    print("=== linux-use smoke test ===")
    for s in results["steps"]:
        flag = "OK" if s.get("ok", True) else "FAIL"
        extra = []
        if "observe_ms" in s:
            extra.append(f"observe={s['observe_ms']:.1f}ms mode={s.get('mode')} walked={s.get('walked')}")
        if "act_ms" in s:
            extra.append(f"act={s['act_ms']:.1f}ms")
        if s.get("msg"):
            extra.append(str(s["msg"])[:120])
        print(f"  [{flag}] {s['name']}" + ("  " + "  ".join(extra) if extra else ""))
    print(f"file_contains_marker={results.get('file_contains_marker')}  ok={results.get('ok')}")
    print(f"logs: {LOG_DIR}")
