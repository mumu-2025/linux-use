"""OpenAI-compatible chat client. API keys come only from the environment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .config import SETTINGS


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]


class OpenAICompatLLM:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 90.0,
    ):
        self.api_key = api_key if api_key is not None else SETTINGS.api_key
        self.base_url = (base_url or SETTINGS.base_url).rstrip("/")
        self.model = model or SETTINGS.model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError(
                "No API key. Set LINUX_USE_API_KEY or OPENAI_API_KEY, "
                "or use inspect/script mode which does not need an LLM."
            )
        url = self.base_url + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            calls.append({
                "id": tc.get("id"),
                "name": fn.get("name"),
                "arguments": args,
            })
        return LLMResponse(content=content, tool_calls=calls, raw=data)
