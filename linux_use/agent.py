"""Agent loop: observe (cached/incremental) -> LLM -> act, until done."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import SETTINGS
from .llm import OpenAICompatLLM
from .platform import get_backends
from .prompts import system_prompt
from .tools import Toolbelt
from .tree import TreeSnapshot


@dataclass
class StepRecord:
    index: int
    thought: str
    tool: str | None
    arguments: dict[str, Any]
    result: str
    observe_ms: float
    act_ms: float
    refresh_mode: str


@dataclass
class AgentResult:
    ok: bool
    answer: str
    steps: list[StepRecord] = field(default_factory=list)
    total_ms: float = 0.0
    observe_ms_total: float = 0.0
    act_ms_total: float = 0.0


class Agent:
    def __init__(
        self,
        llm: OpenAICompatLLM | None = None,
        mode: str = "normal",
        max_steps: int | None = None,
        experimental: bool = False,
        instructions: list[str] | None = None,
        log_to_console: bool = True,
        event_subscriber: Callable[[dict], None] | None = None,
    ):
        self.llm = llm or OpenAICompatLLM()
        self.mode = mode
        self.max_steps = max_steps or SETTINGS.max_steps
        self.instructions = instructions or []
        self.log_to_console = log_to_console
        self.event_subscriber = event_subscriber
        self.a11y, self.desktop, self.input = get_backends()
        self.tools = Toolbelt(self.a11y, self.desktop, self.input, experimental=experimental)

    def _emit(self, typ: str, data: dict[str, Any]) -> None:
        if self.event_subscriber:
            self.event_subscriber({"type": typ, "data": data})
        if self.log_to_console:
            brief = data.get("message") or data.get("answer") or data.get("tool") or typ
            print(f"[{typ}] {brief}", flush=True)

    def _observe(self, force: bool = False, first: bool = False) -> tuple[str, TreeSnapshot]:
        snap = self.a11y.snapshot(force=force)
        if first or force or snap.refresh_mode == "full":
            text = "Desktop observation:\n" + snap.observation()
        elif snap.cache_hit:
            text = (
                f"UI unchanged (cache hit, observe_ms={snap.observe_ms:.1f}, "
                f"generation={snap.generation}).\n"
            )
            if snap.focused_id and snap.get(snap.focused_id):
                el = snap.get(snap.focused_id)
                text += f"Focused still [{el.id}] {el.role} {el.name!r}\n"
            if el_text := (snap.get(snap.focused_id).text if snap.focused_id and snap.get(snap.focused_id) else ""):
                text += f"Focused text={el_text!r}\n"
        else:
            text = "Incremental observation:\n" + snap.observation()
        wins = self.desktop.list_windows()
        if first or force:
            wlines = ["Windows:"]
            for w in wins:
                mark = "*" if w.active else " "
                wlines.append(f"  {mark} {w.wid} {w.width}x{w.height}+{w.x}+{w.y} {w.title}")
            text = "\n".join(wlines) + "\n" + text
        try:
            self.tools.note_snapshot(snap)
        except Exception:
            pass
        return text, snap

    def invoke(self, task: str) -> AgentResult:
        t0 = time.perf_counter()
        self.a11y.ensure_ready()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(self.mode, self.instructions)},
            {"role": "user", "content": f"Task: {task}"},
        ]
        steps: list[StepRecord] = []
        observe_total = act_total = 0.0
        obs, snap = self._observe(first=True)
        observe_total += snap.observe_ms
        messages.append({"role": "user", "content": obs})

        answer = ""
        ok = False
        for i in range(1, self.max_steps + 1):
            resp = self.llm.chat(messages, tools=self.tools.schemas())
            thought = resp.content or ""
            if thought:
                self._emit("THOUGHT", {"message": thought[:400]})
            if not resp.tool_calls:
                # Model replied in prose; treat as done if it looks final, else nudge
                if thought.strip():
                    answer = thought.strip()
                    ok = True
                    steps.append(StepRecord(i, thought, "done", {"answer": answer}, answer, 0, 0, "n/a"))
                    self._emit("DONE", {"answer": answer})
                    break
                messages.append({"role": "user", "content": "Please call a tool, or call done."})
                continue

            # One tool per step keeps the loop short and observations aligned
            call = resp.tool_calls[0]
            name = call["name"]
            args = call["arguments"] or {}
            self._emit("TOOL_CALL", {"tool": name, "message": f"{name} {args}"})
            result = self.tools.call(name, args)
            act_ms = result.act_ms
            act_total += act_ms
            self._emit("TOOL_RESULT", {"tool": name, "message": result.message[:500], "ok": result.ok})

            # Build assistant tool-call message (OpenAI format)
            messages.append({
                "role": "assistant",
                "content": thought or None,
                "tool_calls": [{
                    "id": call.get("id") or f"call_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or f"call_{i}",
                "content": result.message[:6000],
            })

            refresh_mode = "n/a"
            observe_ms = 0.0
            if result.data.get("done"):
                answer = result.data.get("answer") or result.message
                ok = True
                steps.append(StepRecord(i, thought, name, args, result.message, 0, act_ms, "n/a"))
                self._emit("DONE", {"answer": answer})
                break

            # Skip a full tree dump after inspect (already in the tool result)
            if name != "inspect":
                obs, snap = self._observe(force=False, first=False)
                observe_ms = snap.observe_ms
                observe_total += observe_ms
                refresh_mode = snap.refresh_mode
                messages.append({"role": "user", "content": obs})
            else:
                refresh_mode = str(result.data.get("mode") or "inspect")
                observe_ms = float(result.data.get("observe_ms") or 0)
                observe_total += observe_ms

            steps.append(StepRecord(i, thought, name, args, result.message[:1000], observe_ms, act_ms, refresh_mode))
        else:
            answer = answer or f"Stopped after {self.max_steps} steps without calling done."
            ok = False

        total_ms = (time.perf_counter() - t0) * 1000.0
        return AgentResult(
            ok=ok,
            answer=answer,
            steps=steps,
            total_ms=total_ms,
            observe_ms_total=observe_total,
            act_ms_total=act_total,
        )
