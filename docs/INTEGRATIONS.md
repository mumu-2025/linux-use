# Plugging linux-use into other agents

The repo name stays `linux-use` (https://github.com/mumu-2025/linux-use). The same
package auto-detects the OS and exposes one policy (`HYBRID` by default) over
CLI, Python, JSON-RPC, and MCP. An optional `desktop-use` command is an alias
of `linux-use`.

Windows and macOS backends are implemented but **untested on real hardware**.
See the README for the tested / untested matrix.

## MCP (Claude Desktop, Cursor, and other MCP clients)

Start the server on stdio:

```bash
linux-use mcp
# or: desktop-use mcp
```

It speaks MCP 2024-11-05 (JSON-RPC 2.0 with `Content-Length` framing). Tools:
`inspect`, `map`, `act`, `run`, `windows`. `run` needs `LINUX_USE_API_KEY` or
`OPENAI_API_KEY`; the others work without an LLM.

### Claude Desktop / Claude Code `claude_desktop_config.json`

```json
{
  "mcpServers": {
    "linux-use": {
      "command": "linux-use",
      "args": ["mcp"],
      "env": {
        "DISPLAY": ":0",
        "LINUX_USE_API_KEY": "",
        "LINUX_USE_BASE_URL": "https://api.openai.com/v1",
        "LINUX_USE_MODEL": "gpt-4o-mini"
      }
    }
  }
}
```

Point `command` at the venv binary if it is not on `PATH` (for example
`/path/to/linux-use/venv/bin/linux-use`). Leave the API key empty if you only
need `inspect` / `map` / `act`.

### Cursor MCP (`mcp.json`)

```json
{
  "mcpServers": {
    "linux-use": {
      "command": "linux-use",
      "args": ["mcp"]
    }
  }
}
```

## OpenAI-style function calling

The file `docs/openai-tools.json` is a drop-in `tools` array for Chat
Completions. Dispatch each tool call through the Python API (or JSON-RPC):

```python
import json
from pathlib import Path
from linux_use import inspect_desktop, act, get_location_map, Agent, OpenAICompatLLM
from linux_use.platform import get_backends

SCHEMAS = json.loads(Path("docs/openai-tools.json").read_text())

def dispatch(name: str, arguments: dict):
    if name == "inspect":
        snap = inspect_desktop(force=bool(arguments.get("force")))
        return snap.observation()
    if name == "map":
        return get_location_map(refresh=bool(arguments.get("refresh"))).dump_text()
    if name == "act":
        tool = arguments.pop("tool")
        res = act(tool, **arguments)
        return {"ok": res.ok, "message": res.message, "data": res.data, "act_ms": res.act_ms}
    if name == "windows":
        _, desktop, _ = get_backends()
        return [w.__dict__ for w in desktop.list_windows()]
    if name == "run":
        agent = Agent(llm=OpenAICompatLLM(), max_steps=int(arguments.get("max_steps") or 25))
        result = agent.invoke(arguments["task"])
        return {"ok": result.ok, "answer": result.answer}
    raise ValueError(name)

# llm.chat(messages, tools=SCHEMAS) then dispatch(tool_name, json.loads(args))
```

The LLM provider stays generic: any OpenAI-compatible endpoint via
`LINUX_USE_BASE_URL` / `LINUX_USE_MODEL` / `LINUX_USE_API_KEY` (or the
`OPENAI_*` aliases).

## LangChain-style wrapper

```python
from langchain_core.tools import tool
from linux_use import inspect_desktop, act, get_location_map

@tool
def desktop_inspect(force: bool = False) -> str:
    """Dump the desktop accessibility tree."""
    return inspect_desktop(force=force).observation()

@tool
def desktop_map(refresh: bool = False) -> str:
    """Dump the classified element location map."""
    return get_location_map(refresh=refresh).dump_text()

@tool
def desktop_act(tool_name: str, **kwargs) -> str:
    """Run one scripted desktop action (click, type, shortcut, ...)."""
    res = act(tool_name, **kwargs)
    return f"{res.ok}: {res.message}"

# tools = [desktop_inspect, desktop_map, desktop_act]
# bind them to any LangChain chat model with bind_tools(tools)
```

There is no hard dependency on LangChain; the snippet is an example only.

## JSON-RPC (already in the CLI)

```bash
linux-use serve --stdio
# or: linux-use serve --host 127.0.0.1 --port 8765
```

Methods: `inspect`, `act`, `map`, `windows`, `run`, `ping`.
