"""Apples-to-apples benchmark: same scripted Mousepad task under four modes.

No LLM is involved. Modes:

- original: Windows-Use style. Full AT-SPI rescan before every action and after
  every action; locate by walking the fresh tree. No element cache, no locmap.
- cache: event-driven incremental tree cache; locate by walking/looking up the
  last cached snapshot (no coordinate map).
- locmap: cache plus classified location map; click cached coordinates after a
  cheap window-geometry check (fallback to incremental refresh if the check fails).
- hybrid: default product policy. Per action, choose AT-SPI, cached tree, or
  locmap; cheap targeted verify after each action (incremental retry once, full
  rescan last). Logs the path taken at every step.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .a11y import ensure_ready
from .config import SETTINGS
from .locmap import LocationMap
from .platform import get_backends
from .tree import TreeSnapshot, UIElement

MARKER = "LINUX_USE_BENCH_OK_20261125"
PAD = Path("/tmp/linux-use-bench-pad.txt")
TITLE_HINT = "linux-use-bench-pad.txt"
MODES = ("original", "cache", "locmap", "hybrid")

# 5 rounds, rotated so each mode appears equally in each position.
ROTATIONS = [
    ("original", "cache", "locmap", "hybrid"),
    ("cache", "locmap", "hybrid", "original"),
    ("locmap", "hybrid", "original", "cache"),
    ("hybrid", "original", "cache", "locmap"),
    ("original", "locmap", "cache", "hybrid"),
]


@dataclass
class RunResult:
    mode: str
    index: int
    ok: bool
    total_ms: float
    observe_ms: float
    locate_ms: float
    act_ms: float
    walked: int
    file_ok: bool
    error: str = ""
    steps: list[dict] = field(default_factory=list)


def _close_leftovers(desktop, input_backend=None) -> None:
    for w in desktop.list_windows():
        title = w.title.lower()
        if "linux-use-bench-pad" in title or "linux-use-smoke-pad" in title or "mousepad" in title:
            desktop.close_window(w.wid)
            time.sleep(0.25)
    # Dismiss a leftover Save dialog if one appears
    still = [
        w for w in desktop.list_windows()
        if "mousepad" in w.title.lower() or "save" in w.title.lower() or "question" in w.title.lower()
    ]
    if still and input_backend is not None:
        # Prefer Don't Save if a dialog is up; Escape then close again
        input_backend.shortcut("Escape")
        time.sleep(0.15)
        for w in desktop.list_windows():
            if "mousepad" in w.title.lower() or TITLE_HINT in w.title.lower():
                desktop.close_window(w.wid)
                time.sleep(0.2)


def _wait_ready(a11y, desktop, timeout: float = 15.0) -> TreeSnapshot | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        wins = [w for w in desktop.list_windows() if TITLE_HINT in w.title or "Mousepad" in w.title]
        snap = a11y.snapshot(force=True)
        last = snap
        if wins and any(
            el.role == "text" and "editable" in el.states and el.bounds_valid
            for el in snap.elements.values()
        ):
            return snap
        time.sleep(0.15)
    return last


def _observe(mode: str, a11y, desktop, locmap: LocationMap | None) -> tuple[TreeSnapshot, float, int]:
    force = mode == "original"
    snap = a11y.snapshot(force=force)
    if mode == "locmap" and locmap is not None:
        locmap.ingest(snap, desktop.list_windows())
    return snap, snap.observe_ms, snap.walked


def _find_text(snap: TreeSnapshot) -> UIElement | None:
    found = None
    for el in snap.elements.values():
        if el.role == "text" and "editable" in el.states and el.bounds_valid:
            found = el
            if el.focused:
                return el
    return found


def _find_named(snap: TreeSnapshot, *, name: str, role: str) -> UIElement | None:
    want = name.strip().casefold()
    for el in snap.elements.values():
        if el.role == role and (el.name or "").strip().casefold() == want and el.bounds_valid:
            return el
    for el in snap.elements.values():
        if el.role == role and want in (el.name or "").strip().casefold() and el.bounds_valid:
            return el
    return None


def _locate(
    mode: str,
    *,
    kind: str,
    snap: TreeSnapshot | None,
    locmap: LocationMap | None,
    a11y,
    desktop,
) -> tuple[int | None, int | None, str | None, float, int, str]:
    """Return (x, y, element_id, locate_ms, walked, source). Does not click."""
    t0 = time.perf_counter()
    extra_walked = 0
    if mode == "locmap" and locmap is not None:
        if kind == "text":
            hits = [e for e in locmap.lookup(role="text") if e.showing]
            eid = hits[0].id if hits else None
            res = locmap.resolve(
                role="text", element_id=eid, a11y=a11y, desktop=desktop, refresh_if_stale=True,
            )
        else:
            res = locmap.resolve(
                name="File", role="menu", a11y=a11y, desktop=desktop, refresh_if_stale=True,
            )
        ms = (time.perf_counter() - t0) * 1000.0
        if res.ok:
            return res.x, res.y, (res.entry.id if res.entry else None), ms, res.walked, res.source
        return None, None, None, ms, res.walked, res.message

    # original / cache: walk the provided snapshot (caller must have observed)
    if snap is None:
        return None, None, None, (time.perf_counter() - t0) * 1000.0, 0, "no-snapshot"
    el = _find_text(snap) if kind == "text" else _find_named(snap, name="File", role="menu")
    ms = (time.perf_counter() - t0) * 1000.0
    if el is None:
        return None, None, None, ms, extra_walked, "not-found"
    return el.cx, el.cy, el.id, ms, extra_walked, "tree"



def _acc_hybrid(res, *, observe_ms, locate_ms, act_ms, walked):
    data = res.data or {}
    return (
        observe_ms + float(data.get("observe_ms") or 0) + float(data.get("verify_ms") or 0),
        locate_ms + float(data.get("locate_ms") or 0),
        act_ms + float(data.get("act_ms") or res.act_ms or 0),
        walked + int(data.get("walked") or 0),
    )


def _run_hybrid(index: int, a11y, desktop, inp) -> RunResult:
    """Same Mousepad task, executed with the product HybridRouter."""
    from .hybrid import HybridRouter

    locmap = LocationMap()
    router = HybridRouter(a11y, desktop, inp, locmap)
    steps: list[dict] = []
    observe_ms = locate_ms = act_ms = 0.0
    walked = 0
    file_ok = False
    error = ""
    text_id = None
    file_id = None

    def rec(name: str, **kw):
        steps.append({"name": name, **kw})

    try:
        a11y.invalidate(f"bench-hybrid-{index}")
        t0 = time.perf_counter()

        snap, oms, w = router.observe(force=True, reason="observe_initial")
        observe_ms += oms
        walked += w
        rec("observe_initial", observe_ms=round(oms, 2), walked=w, mode_refresh=snap.refresh_mode, path="full")

        cres = router.click(role="text", expect="showing")
        observe_ms, locate_ms, act_ms, walked = _acc_hybrid(
            cres, observe_ms=observe_ms, locate_ms=locate_ms, act_ms=act_ms, walked=walked
        )
        text_id = (cres.data or {}).get("id")
        rec(
            "click_text", ok=cres.ok, path=(cres.data or {}).get("path"),
            locate_ms=round(float((cres.data or {}).get("locate_ms") or 0), 2),
            act_ms=round(float((cres.data or {}).get("act_ms") or cres.act_ms or 0), 2),
            verify_ms=round(float((cres.data or {}).get("verify_ms") or 0), 2),
            source=(cres.data or {}).get("path"), id=text_id, message=cres.message,
        )
        if not cres.ok:
            error = f"click_text: {cres.message}"
            total = (time.perf_counter() - t0) * 1000.0
            return RunResult("hybrid", index, False, total, observe_ms, locate_ms, act_ms, walked, False, error, steps)

        tres = router.type_text(MARKER, element_id=text_id, clear=True)
        observe_ms, locate_ms, act_ms, walked = _acc_hybrid(
            tres, observe_ms=observe_ms, locate_ms=locate_ms, act_ms=act_ms, walked=walked
        )
        rec(
            "type_marker", ok=tres.ok, path=(tres.data or {}).get("path"),
            act_ms=round(float((tres.data or {}).get("act_ms") or tres.act_ms or 0), 2),
            verify_ms=round(float((tres.data or {}).get("verify_ms") or 0), 2),
            via=(tres.data or {}).get("path"), message=tres.message,
        )
        if not tres.ok:
            error = f"type_marker: {tres.message}"
            total = (time.perf_counter() - t0) * 1000.0
            return RunResult("hybrid", index, False, total, observe_ms, locate_ms, act_ms, walked, False, error, steps)

        fres = router.click(name="File", role="menu", expect="menu-open")
        observe_ms, locate_ms, act_ms, walked = _acc_hybrid(
            fres, observe_ms=observe_ms, locate_ms=locate_ms, act_ms=act_ms, walked=walked
        )
        file_id = (fres.data or {}).get("id")
        rec(
            "click_file_menu", ok=fres.ok, path=(fres.data or {}).get("path"),
            locate_ms=round(float((fres.data or {}).get("locate_ms") or 0), 2),
            act_ms=round(float((fres.data or {}).get("act_ms") or fres.act_ms or 0), 2),
            verify_ms=round(float((fres.data or {}).get("verify_ms") or 0), 2),
            source=(fres.data or {}).get("path"), id=file_id, message=fres.message,
        )
        if not fres.ok:
            error = f"click_file_menu: {fres.message}"
            total = (time.perf_counter() - t0) * 1000.0
            return RunResult("hybrid", index, False, total, observe_ms, locate_ms, act_ms, walked, False, error, steps)

        eres = router.shortcut("Escape", expect="menu-closed", element_id=file_id)
        observe_ms, locate_ms, act_ms, walked = _acc_hybrid(
            eres, observe_ms=observe_ms, locate_ms=locate_ms, act_ms=act_ms, walked=walked
        )
        rec(
            "escape_menu", ok=eres.ok, path=(eres.data or {}).get("path"),
            act_ms=round(float((eres.data or {}).get("act_ms") or eres.act_ms or 0), 2),
            verify_ms=round(float((eres.data or {}).get("verify_ms") or 0), 2),
            message=eres.message,
        )
        if not eres.ok:
            error = f"escape_menu: {eres.message}"

        sres = router.shortcut("ctrl+s")
        observe_ms, locate_ms, act_ms, walked = _acc_hybrid(
            sres, observe_ms=observe_ms, locate_ms=locate_ms, act_ms=act_ms, walked=walked
        )
        rec(
            "shortcut_ctrl_s", ok=sres.ok, path=(sres.data or {}).get("path"),
            act_ms=round(float((sres.data or {}).get("act_ms") or sres.act_ms or 0), 2),
            message=sres.message,
        )

        vok, vms, detail = router.verify(f"filecontains:{PAD}::{MARKER}")
        observe_ms += vms
        file_text = PAD.read_text(encoding="utf-8") if PAD.exists() else ""
        file_ok = MARKER in file_text and vok
        rec(
            "verify_file", ok=file_ok, text=file_text[:80], verify_ms=round(vms, 2), detail=detail,
            paths=[
                {"step": lg.step, "path": lg.path, "ok": lg.ok, "detail": lg.detail,
                 "ms": round(lg.ms, 2), "walked": lg.walked}
                for lg in router.logs
            ],
        )
        if not file_ok:
            error = error or f"marker missing in {PAD}: {file_text!r}"

        total = (time.perf_counter() - t0) * 1000.0
        return RunResult(
            "hybrid", index, bool(file_ok and not error), total,
            observe_ms, locate_ms, act_ms, walked, file_ok, error, steps,
        )
    except Exception as exc:
        return RunResult(
            "hybrid", index, False, 0.0, observe_ms, locate_ms, act_ms, walked,
            False, f"{type(exc).__name__}: {exc}", steps,
        )


def _run_once(mode: str, index: int, a11y, desktop, inp) -> RunResult:
    if mode == "hybrid":
        return _run_hybrid(index, a11y, desktop, inp)
    locmap = LocationMap() if mode == "locmap" else None
    steps: list[dict] = []
    observe_ms = locate_ms = act_ms = 0.0
    walked = 0
    snap: TreeSnapshot | None = None
    text_id: str | None = None
    file_ok = False
    error = ""

    def rec(name: str, **kw):
        steps.append({"name": name, **kw})

    def do_observe(tag: str) -> bool:
        nonlocal snap, observe_ms, walked
        snap, oms, w = _observe(mode, a11y, desktop, locmap)
        observe_ms += oms
        walked += w
        rec(tag, observe_ms=round(oms, 2), walked=w, mode_refresh=snap.refresh_mode)
        return True

    def do_click(kind: str, tag: str) -> bool:
        nonlocal snap, locate_ms, act_ms, walked, text_id, error
        # original: fresh full tree immediately before the action
        if mode == "original":
            do_observe(f"pre_{tag}")
        x, y, eid, lms, w, src = _locate(
            mode, kind=kind, snap=snap, locmap=locmap, a11y=a11y, desktop=desktop,
        )
        locate_ms += lms
        walked += w
        if x is None or y is None:
            error = f"{tag}: {src}"
            rec(tag, ok=False, locate_ms=round(lms, 2), source=src)
            return False
        if kind == "text" and eid:
            text_id = eid
        cres = inp.click(int(x), int(y))
        act_ms += cres.act_ms
        rec(tag, ok=cres.ok, locate_ms=round(lms, 2), act_ms=round(cres.act_ms, 2),
            source=src, walked=w, at=f"{x},{y}")
        if not cres.ok:
            error = f"{tag}: {cres.message}"
            return False
        return True

    try:
        a11y.invalidate(f"bench-{mode}-{index}")
        t0 = time.perf_counter()

        # 1. Initial observation (how this mode first sees the UI)
        do_observe("observe_initial")

        # 2. Click the text area
        if not do_click("text", "click_text"):
            total = (time.perf_counter() - t0) * 1000.0
            return RunResult(mode, index, False, total, observe_ms, locate_ms, act_ms, walked, False, error, steps)
        do_observe("observe_after_click_text")

        # 3. Type the marker (same AT-SPI set_text / typist path for all modes)
        if mode == "original":
            do_observe("pre_type")
        t_act = time.perf_counter()
        typed = False
        if text_id and hasattr(a11y, "set_text") and a11y.set_text(text_id, MARKER):
            typed = True
            ams = (time.perf_counter() - t_act) * 1000.0
            act_ms += ams
            rec("type_marker", ok=True, act_ms=round(ams, 2), via="set_text")
        else:
            cres = inp.type_text(MARKER, clear=True)
            act_ms += cres.act_ms
            typed = cres.ok
            rec("type_marker", ok=cres.ok, act_ms=round(cres.act_ms, 2), via="xdotool")
            if not cres.ok:
                error = cres.message
        if not typed:
            total = (time.perf_counter() - t0) * 1000.0
            return RunResult(mode, index, False, total, observe_ms, locate_ms, act_ms, walked, False, error, steps)
        do_observe("observe_after_type")

        # 4. Open the File menu
        if not do_click("file", "click_file_menu"):
            total = (time.perf_counter() - t0) * 1000.0
            return RunResult(mode, index, False, total, observe_ms, locate_ms, act_ms, walked, False, error, steps)
        do_observe("observe_after_file_menu")

        # 5. Close the menu with Escape
        if mode == "original":
            do_observe("pre_escape")
        eres = inp.shortcut("Escape")
        act_ms += eres.act_ms
        rec("escape_menu", ok=eres.ok, act_ms=round(eres.act_ms, 2))
        do_observe("observe_after_escape")

        # 6. Save with ctrl+s
        if mode == "original":
            do_observe("pre_save")
        sres = inp.shortcut("ctrl+s")
        act_ms += sres.act_ms
        rec("shortcut_ctrl_s", ok=sres.ok, act_ms=round(sres.act_ms, 2))
        do_observe("observe_after_save")

        file_text = PAD.read_text(encoding="utf-8") if PAD.exists() else ""
        file_ok = MARKER in file_text
        rec("verify_file", ok=file_ok, text=file_text[:80])
        if not file_ok:
            error = f"marker missing in {PAD}: {file_text!r}"

        total = (time.perf_counter() - t0) * 1000.0
        return RunResult(
            mode, index, file_ok and not error, total,
            observe_ms, locate_ms, act_ms, walked, file_ok, error, steps,
        )
    except Exception as exc:
        return RunResult(
            mode, index, False, 0.0, observe_ms, locate_ms, act_ms, walked,
            False, f"{type(exc).__name__}: {exc}", steps,
        )


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(statistics.fmean(values), 2),
    }


def _speedup(base: float, other: float) -> float | None:
    if not other:
        return None
    return round(base / other, 2)


def run_bench(runs: int = 5, out_dir: Path | None = None) -> dict:
    """Run the same scripted task under all four modes, `runs` times each."""
    out_dir = Path(out_dir or SETTINGS.log_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_ready()
    a11y, desktop, inp = get_backends()
    a11y.ensure_ready()

    schedule: list[tuple[str, int]] = []
    rotations = ROTATIONS * ((runs + len(ROTATIONS) - 1) // len(ROTATIONS))
    counters = {m: 0 for m in MODES}
    for rot in rotations:
        for mode in rot:
            if counters[mode] >= runs:
                continue
            counters[mode] += 1
            schedule.append((mode, counters[mode]))
        if all(counters[m] >= runs for m in MODES):
            break

    results: list[RunResult] = []
    print(f"=== linux-use bench  runs/mode={runs}  trials={len(schedule)}  (no LLM) ===")
    print("order:", " ".join(f"{m[0]}{i}" for m, i in schedule))

    for mode, idx in schedule:
        _close_leftovers(desktop, inp)
        PAD.write_text("", encoding="utf-8")
        launch = desktop.launch_app("mousepad", [str(PAD)])
        ready = _wait_ready(a11y, desktop)
        if ready is None or not any(
            el.role == "text" and "editable" in el.states for el in (ready.elements or {}).values()
        ):
            results.append(RunResult(mode, idx, False, 0, 0, 0, 0, 0, False, "mousepad not ready", []))
            print(f"  FAIL {mode} #{idx}: mousepad not ready")
            _close_leftovers(desktop, inp)
            continue
        desktop.focus_window(TITLE_HINT)
        # Do not reuse the setup snapshot; each mode starts with its own first observe.
        a11y.invalidate("bench-run-start")
        res = _run_once(mode, idx, a11y, desktop, inp)
        results.append(res)
        flag = "OK" if res.ok else "FAIL"
        print(
            f"  [{flag}] {mode:8} #{idx}  total={res.total_ms:.1f}ms  "
            f"observe={res.observe_ms:.1f} locate={res.locate_ms:.1f} "
            f"act={res.act_ms:.1f} walked={res.walked} file={res.file_ok}"
            + (f"  err={res.error}" if res.error else "")
        )
        _close_leftovers(desktop, inp)
        time.sleep(0.15)

    # Final tidy
    _close_leftovers(desktop, inp)

    by_mode: dict[str, list[RunResult]] = {m: [] for m in MODES}
    for r in results:
        by_mode[r.mode].append(r)

    summary: dict = {"runs_requested": runs, "llm": False, "marker": MARKER, "modes": {}}
    for m in MODES:
        rs = by_mode[m]
        ok_rs = [r for r in rs if r.ok]
        use = ok_rs or rs
        summary["modes"][m] = {
            "ok": sum(1 for r in rs if r.ok),
            "failed": sum(1 for r in rs if not r.ok),
            "total_ms": _stats([r.total_ms for r in use]),
            "observe_ms": _stats([r.observe_ms for r in use]),
            "locate_ms": _stats([r.locate_ms for r in use]),
            "observe_locate_ms": _stats([r.observe_ms + r.locate_ms for r in use]),
            "act_ms": _stats([r.act_ms for r in use]),
            "walked": _stats([float(r.walked) for r in use]),
        }

    orig_total = summary["modes"]["original"]["total_ms"].get("median")
    orig_ol = summary["modes"]["original"]["observe_locate_ms"].get("median")
    for m in MODES:
        rs = by_mode[m]
        n = len(rs)
        summary["modes"][m]["success_rate"] = f"{sum(1 for r in rs if r.ok)}/{n}" if n else "0/0"
        if m == "original":
            summary["modes"][m]["speedup_vs_original_total"] = 1.0
            summary["modes"][m]["speedup_vs_original_observe_locate"] = 1.0
            continue
        med_t = summary["modes"][m]["total_ms"].get("median")
        med_ol = summary["modes"][m]["observe_locate_ms"].get("median")
        summary["modes"][m]["speedup_vs_original_total"] = _speedup(orig_total or 0, med_t or 0)
        summary["modes"][m]["speedup_vs_original_observe_locate"] = _speedup(orig_ol or 0, med_ol or 0)

    payload = {
        "summary": summary,
        "schedule": [{"mode": m, "index": i} for m, i in schedule],
        "runs": [asdict(r) for r in results],
    }
    out = out_dir / "bench-compare.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _print_summary(summary)
    print(f"wrote {out}")
    return payload


def _print_summary(summary: dict) -> None:
    print("\n=== median (min–max)  no LLM ===")
    print(f"{'mode':10} {'total':22} {'observe+locate':22} {'act':22} {'walked':18} {'speedup':8}")
    for m in MODES:
        s = summary["modes"][m]
        def fmt(d):
            if not d or "median" not in d:
                return "—"
            return f"{d['median']:.1f} ({d['min']:.1f}–{d['max']:.1f})"
        sp = s.get("speedup_vs_original_total")
        sp_s = "1.00x" if m == "original" else (f"{sp}x" if sp else "—")
        print(f"{m:10} {fmt(s['total_ms']):22} {fmt(s['observe_locate_ms']):22} {fmt(s['act_ms']):22} {fmt(s['walked']):18} {sp_s}")
    print(f"successful runs: " + ", ".join(
        f"{m}={summary['modes'][m]['ok']}/{summary['runs_requested']}" for m in MODES
    ))


def format_markdown(payload: dict) -> str:
    """Render the comparison table for docs/SMOKE.md."""
    s = payload["summary"]
    lines = [
        "## 三种模式对照基准（同一脚本，无 LLM）",
        "",
        "日期：以本次运行为准（Asia/Shanghai）。",
        "命令：`linux-use bench --runs 5`。",
        "三种模式跑**同一段**脚本化任务，不经过 LLM（LLM 时延未计入）。",
        "任务（6 个动作，每个动作后各做一次该模式应有的观察；original 在动作前还会再全量扫一次树）：",
        "",
        "1. 初始观察并定位文本区，点击文本区",
        "2. 写入固定标记 `LINUX_USE_BENCH_OK_20261125`",
        "3. 点击 File 菜单",
        "4. Escape 关掉菜单",
        "5. `ctrl+s` 保存到临时文件",
        "6. 核对文件里确有该标记",
        "",
        "每轮先打开全新 Mousepad，测完即关闭。三种模式轮转顺序，各跑 5 次，以降低先后偏差。启动窗口的等待不计入任务时延。",
        "",
        "模式定义：",
        "",
        "- **original**：Windows-Use 风格。每一步动作前、动作后都强制全量扫描 AT-SPI 树，在刚扫出的树上查找元素，不用缓存、不用坐标图。",
        "- **cache**：事件驱动缓存 + 增量刷新，在缓存树上定位（不用分类坐标图）。",
        "- **locmap**：在 cache 之上加分类坐标图，廉价窗口几何校验通过后直接点缓存坐标。",
        "",
    ]
    def cell(d, kind="ms"):
        if not d or "median" not in d:
            return "—"
        if kind == "count":
            return f"{d['median']:.0f} ({d['min']:.0f}–{d['max']:.0f})"
        return f"{d['median']:.1f} ({d['min']:.1f}–{d['max']:.1f})"

    lines += [
        "| 模式 | 任务总时延 median (min–max) ms | 观察+定位 median (min–max) ms | 动作 median (min–max) ms | 走过节点 median (min–max) | 相对 original 加速（总时延） | 相对 original 加速（观察+定位） | 成功次数 |",
        "|--|--|--|--|--|--|--|--|",
    ]
    for m in MODES:
        md = s["modes"][m]
        sp_t = "1.00×" if m == "original" else (f"{md.get('speedup_vs_original_total')}×" if md.get("speedup_vs_original_total") else "—")
        sp_o = "1.00×" if m == "original" else (f"{md.get('speedup_vs_original_observe_locate')}×" if md.get("speedup_vs_original_observe_locate") else "—")
        lines.append(
            f"| {m} | {cell(md['total_ms'])} | {cell(md['observe_locate_ms'])} | "
            f"{cell(md['act_ms'])} | {cell(md['walked'], 'count')} | {sp_t} | {sp_o} | "
            f"{md.get('success_rate') or str(md['ok']) + '/' + str(s['runs_requested'])} |"
        )
    lines += [
        "",
        "English: same scripted Mousepad task, no LLM, 5 alternating runs per mode. "
        "original = full AT-SPI rescan before and after every step; cache = event-driven incremental tree; "
        "locmap = cache plus classified coordinate map; hybrid = default policy (AT-SPI, then cache or locmap "
        "per action, cheap post-action verify). Setup (launching Mousepad) is excluded from the timed total. "
        "Every successful run verified the marker was saved to the temp file. Success rate is shown per mode.",
        "",
    ]
    return "\n".join(lines) + "\n"
