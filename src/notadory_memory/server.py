"""MCP stdio entry point for Notadory Memory.

The public functions intentionally have explicit annotations: MCP uses them to
build the JSON schemas advertised to hosts.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server import MCPServer

from .service import MemoryService
from .storage import MemoryStorage

service = MemoryService(MemoryStorage())
server = MCPServer(name="notadory-memory", version="1.0.0")


@server.tool(name="create_concept", description="Create a durable global or project concept.", structured_output=True)
def create_concept(
    concept_id: str,
    label: str,
    aliases: list[str],
    action_terms: list[str],
    object_terms: list[str],
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return service.create_concept(concept_id, label, aliases, action_terms, object_terms, scope)


@server.tool(name="append_concept_terms", description="Append aliases or matching terms to an existing concept.", structured_output=True)
def append_concept_terms(
    concept_id: str,
    aliases: list[str] | None = None,
    action_terms: list[str] | None = None,
    object_terms: list[str] | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return service.append_concept_terms(concept_id, aliases, action_terms, object_terms, scope)


@server.tool(name="create_memory", description="Create, deduplicate, or revise a memory.", structured_output=True)
def create_memory(
    scope: dict[str, Any],
    title: str,
    summary: str,
    content: str,
    concept_ids: list[str],
    context: dict[str, str],
    tier: int,
    status: Literal["active", "archived", "superseded"],
    provenance: dict[str, Any],
    id: str | None = None,
) -> dict[str, Any]:
    data = {"scope": scope, "title": title, "summary": summary, "content": content,
            "concept_ids": concept_ids, "context": context, "tier": tier,
            "status": status, "provenance": provenance}
    if id is not None:
        data["id"] = id
    return service.create(data)


@server.tool(name="list_memories", description="List memory metadata without content.", structured_output=True)
def list_memories(
    scope: dict[str, Any],
    query: str | None = None,
    tiers: list[int] | None = None,
    limit: int = 10,
    cursor: str | None = None,
) -> dict[str, Any]:
    return service.list({"scope": scope, "query": query, "tiers": tiers if tiers is not None else [1, 2, 3, 4],
                         "limit": limit, "cursor": cursor})


@server.tool(name="get_memory", description="Get memory metadata without content.", structured_output=True)
def get_memory(scope: dict[str, Any], id: str) -> dict[str, Any]:
    return service.get(scope, id)


@server.tool(name="load_memory", description="Load content from the requested memory scope.", structured_output=True)
def load_memory(scope: dict[str, Any], id: str) -> dict[str, Any]:
    return service.load(scope, id)


@server.tool(name="retrieve_context", description="Retrieve matching new or revised context.", structured_output=True)
def retrieve_context(
    scope: dict[str, Any],
    conversation_key: str,
    query: str | None = None,
    context: dict[str, str] | None = None,
    ttl_seconds: int = 86400,
    limit: int = 8,
    force_reload: bool = False,
) -> dict[str, Any]:
    return service.retrieve({"scope": scope, "conversation_key": conversation_key, "query": query,
                             "context": context or {}, "ttl_seconds": ttl_seconds,
                             "limit": limit, "force_reload": force_reload})


@server.tool(name="reindex_archive", description="List active tier-5 candidates without changing data.", structured_output=True)
def reindex_archive(
    scope: dict[str, Any] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    return service.reindex({"scope": scope, "query": query})


async def _run() -> None:
    service.init()
    await server.run_stdio_async()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
