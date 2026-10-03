"""Output token accounting for calls arriving through the MCP server."""

from __future__ import annotations

import json
import sys
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text_blocks(result: Any) -> list[str]:
    if isinstance(result, Mapping):
        content = result.get("content", [])
    else:
        content = getattr(result, "content", [])
    blocks: list[str] = []
    for item in content:
        item_type = item.get("type") if isinstance(item, Mapping) else getattr(item, "type", None)
        text = item.get("text") if isinstance(item, Mapping) else getattr(item, "text", None)
        if item_type == "text" and isinstance(text, str):
            blocks.append(text)
    return blocks


def _structured(result: Any) -> Any:
    if isinstance(result, Mapping):
        return result.get("structuredContent", result.get("structured_content"))
    return getattr(result, "structured_content", None)


class UsageTracker:
    """Thread-safe, output-only usage totals for one server session."""

    encoding_name = "cl100k_base"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._encoding: Any = None
        self._ready = False
        self._reset_locked()

    @property
    def ready(self) -> bool:
        return self._ready

    def start(self) -> bool:
        """Load the tokenizer once, returning false without breaking startup."""
        try:
            import tiktoken

            self._encoding = tiktoken.get_encoding(self.encoding_name)
        except Exception as exc:  # tokenizer availability must not kill stdio
            print(f"Notadory token tracker disabled: {exc}", file=sys.stderr)
            self._ready = False
            return False
        self._ready = True
        self.reset()
        return True

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._session_id = str(uuid.uuid4())
        self._started_at = _now()
        self._total_tokens = 0
        self._total_calls = 0
        self._tools: dict[str, dict[str, int]] = {}
        self._last_call: dict[str, Any] | None = None

    def count(self, tool: str, result: Any) -> int:
        if not self._ready or self._encoding is None:
            return 0
        texts = _text_blocks(result)
        if texts:
            tokens = sum(len(self._encoding.encode_ordinary(text)) for text in texts)
        else:
            structured = _structured(result)
            if structured is None:
                tokens = 0
            else:
                payload = json.dumps(structured, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                tokens = len(self._encoding.encode_ordinary(payload))
        with self._lock:
            self._total_tokens += tokens
            self._total_calls += 1
            item = self._tools.setdefault(tool, {"calls": 0, "tokens": 0})
            item["calls"] += 1
            item["tokens"] += tokens
            self._last_call = {"tool": tool, "tokens": tokens, "at": _now()}
        return tokens

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tools = [{"name": name, **values} for name, values in sorted(self._tools.items())]
            last = dict(self._last_call) if self._last_call is not None else None
            return {
                "session_id": self._session_id,
                "started_at": self._started_at,
                "encoding": self.encoding_name,
                "total_tokens": self._total_tokens,
                "total_calls": self._total_calls,
                "tools": tools,
                "last_call": last,
            }


def make_usage_middleware(tracker: UsageTracker):
    """Build public SDK middleware; only completed ``tools/call`` is counted."""

    async def middleware(ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method == "tools/call" and isinstance(ctx.params, Mapping):
            tool = ctx.params.get("name")
            if isinstance(tool, str):
                tracker.count(tool, result)
        return result

    return middleware
