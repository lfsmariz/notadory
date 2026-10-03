from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import tiktoken

from notadory_memory.dashboard import DashboardServer
from notadory_memory.usage import UsageTracker, make_usage_middleware


def ready_tracker() -> UsageTracker:
    tracker = UsageTracker()
    assert tracker.start()
    return tracker


def result(text: str | None = None, structured: object = None) -> dict:
    value = {"content": []}
    if text is not None:
        value["content"] = [{"type": "text", "text": text}]
    if structured is not None:
        value["structuredContent"] = structured
    return value


def test_exact_output_tokens_and_structured_fallback() -> None:
    tracker = ready_tracker()
    encoding = tiktoken.get_encoding("cl100k_base")
    text = "Olá 世界 <|endoftext|> café"
    expected = len(encoding.encode_ordinary(text))
    assert tracker.count("load_memory", result(text, {"secret": "not counted twice"})) == expected
    assert tracker.snapshot()["total_tokens"] == expected
    fallback = json.dumps({"a": "世界", "z": 1}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert tracker.count("structured", result(structured={"z": 1, "a": "世界"})) == len(encoding.encode_ordinary(fallback))


def test_totals_reset_and_snapshots_are_independent() -> None:
    tracker = ready_tracker()
    tracker.count("one", result("a"))
    tracker.count("one", result("bb"))
    snapshot = tracker.snapshot()
    snapshot["tools"][0]["tokens"] = 999
    assert tracker.snapshot()["tools"][0]["tokens"] != 999
    old_session = snapshot["session_id"]
    tracker.reset()
    fresh = tracker.snapshot()
    assert fresh["session_id"] != old_session
    assert fresh["total_calls"] == fresh["total_tokens"] == 0


def test_thread_safe_increment_and_middleware() -> None:
    tracker = ready_tracker()
    calls = 20

    def worker() -> None:
        for _ in range(calls):
            tracker.count("parallel", result("x"))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert tracker.snapshot()["total_calls"] == calls * len(threads)

    async def exercise() -> None:
        ctx = SimpleNamespace(method="tools/call", params={"name": "middleware"})

        async def next_call(_ctx):
            return result("hook")

        await make_usage_middleware(tracker)(ctx, next_call)

    asyncio.run(exercise())
    assert any(item["name"] == "middleware" for item in tracker.snapshot()["tools"])


def test_dashboard_http_is_read_only_and_localhost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = ready_tracker()
    dashboard = DashboardServer(tracker)
    monkeypatch.setenv("NOTADORY_DASHBOARD_PORT", "0")
    assert dashboard.start()
    root = dashboard.url
    assert root is not None
    assert urllib.request.urlopen(root).headers["Content-Type"].startswith("text/html")
    before = tracker.snapshot()
    response = urllib.request.urlopen(root + "api/stats")
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"
    assert json.loads(response.read()) == before
    _foreign_request(root)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(root + "missing")
    assert error.value.code == 404
    dashboard.stop()


def _foreign_request(root: str):
    request = urllib.request.Request(root + "api/stats", headers={"Host": "example.invalid"})
    try:
        urllib.request.urlopen(request)
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
        return ()
    raise AssertionError("foreign Host was accepted")


def test_real_stdio_transport_counts_wire_text_and_exits(tmp_path) -> None:
    stats, stdout_lines, returncode = asyncio.run(_stdio_session(tmp_path))
    assert returncode == 0
    assert all(isinstance(line, dict) and line.get("jsonrpc") == "2.0" for line in stdout_lines)
    assert stats["total_calls"] == 2
    assert stats["total_tokens"] > 0
    assert {item["name"] for item in stats["tools"]} == {"reindex_archive"}
    assert stdout_lines[-1]["result"]["isError"] is True


async def _stdio_session(tmp_path):
    root = Path(__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "MEMORY_DATA_DIR": str(tmp_path), "NOTADORY_DASHBOARD_PORT": "0"}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "notadory_memory.server", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    dashboard_line = await asyncio.wait_for(process.stderr.readline(), timeout=10)
    url = dashboard_line.decode().strip().split()[-1]
    wire_responses, texts = await _send_stdio_requests(process)
    expected = sum(len(tiktoken.get_encoding("cl100k_base").encode_ordinary(text)) for text in texts)
    stats = json.loads(urllib.request.urlopen(url + "api/stats").read())
    process.stdin.close()
    returncode = await asyncio.wait_for(process.wait(), timeout=10)
    assert stats["total_tokens"] == expected
    return stats, wire_responses, returncode


async def _send_stdio_requests(process):
    responses = []
    await _write_request(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
    await _write_request(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    responses.append(await _read_response(process))
    await _write_request(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    responses.append(await _read_response(process))
    tool_responses = []
    texts = []
    for request_id, arguments in ((3, {"scope": {"type": "global"}}), (4, {})):
        await _write_request(process, {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": "reindex_archive", "arguments": arguments}})
        response = await _read_response(process)
        tool_responses.append(response)
        texts.append(response["result"]["content"][0]["text"])
    return responses + tool_responses, texts


async def _write_request(process, value):
    process.stdin.write((json.dumps(value) + "\n").encode())
    await process.stdin.drain()


async def _read_response(process):
    line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    assert line
    return json.loads(line)
