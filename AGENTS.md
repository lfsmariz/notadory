# Notadory Memory MCP

- Python 3.10+ package managed with `uv`; install development dependencies with `uv sync --extra dev` (use `uv sync --locked --extra dev` for CI-reproducible installs).
- Run the required verification in CI order: `uv run ruff check .` then `uv run pytest`. Pytest only discovers `tests/` and fails below 80% coverage.
- Smoke-test the stdio server with `uv run notadory-memory-mcp </dev/null`. Never write logs or diagnostics to stdout: it is the MCP protocol channel.

## Structure and behavior

- `src/notadory_memory/server.py` is the MCP/tool-schema boundary; its public function annotations are used to build MCP JSON schemas. Keep business rules in `service.py`, persistence in `storage.py`, and deterministic matching in `lexicon.py`.
- Launch through `uv run notadory-memory-mcp` (or `uv run python -m notadory_memory.server`); the console script maps to `notadory_memory.server:main`.
- Storage is local JSON under `memory-data/` by default; set `MEMORY_DATA_DIR` to an absolute path when the working directory is not controlled. This runtime data is gitignored.
- Preserve the V1 model: atomic file writes, explicit global/project scopes, and deterministic lexicon matching. It intentionally has no embeddings, LLM, database, or semantic scoring.
- A project-scoped search includes global and project records, but `get_memory` and `load_memory` only read the explicitly requested scope.
