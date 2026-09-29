import asyncio


def test_server_registers_all_tools():
    from notadory_memory.server import server

    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "create_concept", "create_memory", "list_memories", "get_memory", "load_memory",
        "retrieve_context", "reindex_archive",
    }
    schemas = {tool.name: tool.input_schema for tool in tools}
    assert "scope" in schemas["create_memory"]["properties"]
    assert "conversation_key" in schemas["retrieve_context"]["properties"]
