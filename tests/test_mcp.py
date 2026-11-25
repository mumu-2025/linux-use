
"""MCP protocol unit tests (no live desktop required for handshake / framing)."""

import io
import json

from linux_use.mcp_server import (
    PROTOCOL_VERSION, TOOL_DEFS, handle_rpc, handle_tool, serve_mcp, _write_message, _read_message,
)


def test_initialize_and_tools_list():
    init = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert init["result"]["serverInfo"]["name"] == "linux-use"
    assert "tools" in init["result"]["capabilities"]
    listed = handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"inspect", "map", "act", "run", "windows"}
    assert {t["name"] for t in TOOL_DEFS} == names


def test_ping_and_unknown_method():
    ping = handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert ping["result"] == {}
    err = handle_rpc({"jsonrpc": "2.0", "id": 4, "method": "nope"})
    assert err["error"]["code"] == -32601


def test_notification_has_no_response():
    assert handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) is None


def test_unknown_tool_is_error():
    res = handle_tool("not-a-tool", {})
    assert res["isError"] is True


def test_act_requires_tool():
    res = handle_tool("act", {})
    assert res["isError"] is True
    assert "tool" in res["content"][0]["text"]


def test_framing_roundtrip():
    buf = io.StringIO()
    _write_message(buf, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    raw = buf.getvalue()
    assert raw.startswith("Content-Length:")
    parsed = _read_message(io.StringIO(raw))
    assert parsed["result"]["ok"] is True


def test_newline_json_and_shutdown(monkeypatch):
    # drive serve_mcp with two newline-delimited requests
    body = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "shutdown"}) + "\n"
    )
    inp = io.StringIO(body)
    out = io.StringIO()
    rc = serve_mcp(stdin=inp, stdout=out)
    assert rc == 0
    dumped = out.getvalue()
    assert "Content-Length:" in dumped
    # two responses
    assert dumped.count("Content-Length:") == 2
