"""Safe, atomic JSON persistence for memory records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MEMORY_ID = re.compile(r"^mem-[A-Za-z0-9][A-Za-z0-9_-]*$")


def normalize_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()


def safe_segment(value: str, label: str = "path segment") -> str:
    if not isinstance(value, str) or not SAFE_SEGMENT.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def validate_memory_id(value: str) -> str:
    if not isinstance(value, str) or not MEMORY_ID.fullmatch(value):
        raise ValueError("invalid memory id")
    return value


def atomic_write(path: Path, value: str) -> None:
    """Write a UTF-8 JSON document without exposing a partial destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class MemoryStorage:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root or os.environ.get("MEMORY_DATA_DIR", "memory-data")).expanduser().resolve()

    def init(self) -> None:
        for directory in (
            self.root / "lexicon" / "project-overrides",
            self.root / "memories" / "global",
            self.root / "memories" / "projects",
            self.root / "cache" / "conversations",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        concepts = self.root / "lexicon" / "concepts.json"
        if not concepts.exists():
            atomic_write(
                concepts,
                json.dumps(
                    {
                        "concepts": [
                            {
                                "id": "input-validation",
                                "aliases": ["valide a entrada", "validar o payload", "validar entrada"],
                                "action_terms": ["validar", "valide"],
                                "object_terms": ["entrada", "payload"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

    def _scope_dir(self, scope: dict[str, Any]) -> Path:
        kind, project = validate_scope(scope)
        if kind == "global":
            return self.root / "memories" / "global"
        return self.root / "memories" / "projects" / safe_segment(project, "project_id")

    def _file(self, scope: dict[str, Any], memory_id: str) -> Path:
        validate_memory_id(memory_id)
        return self._scope_dir(scope) / f"{memory_id}.json"

    def read(self, scope: dict[str, Any], memory_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._file(scope, memory_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def write(self, memory: dict[str, Any]) -> None:
        path = self._file(memory["scope"], memory["id"])
        atomic_write(path, json.dumps(memory, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _read_dir(self, directory: Path) -> list[dict[str, Any]]:
        if not directory.exists():
            return []
        result: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    result.append(value)
            except (OSError, ValueError, TypeError):
                continue
        return result

    def owned(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        return self._read_dir(self._scope_dir(scope))

    def all_for(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        kind, project = validate_scope(scope)
        global_scope = {"type": "global"}
        if kind == "global":
            return self.owned(global_scope)
        return self.owned(global_scope) + self.owned({"type": "project", "project_id": project})

    def all_scopes(self) -> list[dict[str, Any]]:
        result = self.owned({"type": "global"})
        projects = self.root / "memories" / "projects"
        if projects.exists():
            for path in sorted(projects.iterdir(), key=lambda item: item.name):
                if path.is_dir() and SAFE_SEGMENT.fullmatch(path.name):
                    result.extend(self.owned({"type": "project", "project_id": path.name}))
        return result


def validate_scope(scope: Any) -> tuple[str, str | None]:
    if not isinstance(scope, dict) or set(scope) - {"type", "project_id"}:
        raise ValueError("scope must be an object")
    kind = scope.get("type")
    if kind == "global":
        if "project_id" in scope and scope["project_id"] is not None:
            raise ValueError("global scope cannot have project_id")
        return kind, None
    if kind == "project":
        project = scope.get("project_id")
        if not isinstance(project, str):
            raise ValueError("project scope requires project_id")
        return kind, safe_segment(project, "project_id")
    raise ValueError("scope.type must be global or project")
