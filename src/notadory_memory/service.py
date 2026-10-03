"""V1 memory semantics independent of the MCP transport."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lexicon import Lexicon, normalize_term, tokens
from .storage import MemoryStorage, atomic_write, content_hash, normalize_content, validate_memory_id, validate_scope

MAX_LIST_LIMIT = 50
MAX_RETRIEVE_LIMIT = 20
MAX_CACHE_ENTRIES = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _summary(memory: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in memory.items() if key != "content"}


def _cursor_encode(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")


def _cursor_decode(value: str | None) -> int:
    if value is None:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        offset = json.loads(base64.urlsafe_b64decode(padded).decode())["offset"]
        if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
            return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass
    raise ValueError("invalid cursor")


class MemoryService:
    def __init__(self, storage: MemoryStorage | None = None) -> None:
        self.storage = storage or MemoryStorage()
        self.lexicon = Lexicon(self.storage.root)

    def init(self) -> None:
        self.storage.init()

    def create_concept(
        self,
        concept_id: str,
        label: str,
        aliases: list[str],
        action_terms: list[str],
        object_terms: list[str],
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.lexicon.create_concept(concept_id, label, aliases, action_terms, object_terms, scope)

    def append_concept_terms(
        self,
        concept_id: str,
        aliases: list[str] | None = None,
        action_terms: list[str] | None = None,
        object_terms: list[str] | None = None,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.lexicon.append_concept_terms(concept_id, aliases, action_terms, object_terms, scope)

    @staticmethod
    def _check_scope(scope: Any) -> tuple[str, str | None]:
        return validate_scope(scope)

    @staticmethod
    def _validate_required_fields(data: dict[str, Any]) -> None:
        required = ("title", "summary", "content", "concept_ids", "context", "tier", "status", "provenance")
        if any(field not in data for field in required):
            raise ValueError("missing required memory fields")

    @staticmethod
    def _validate_text_fields(data: dict[str, Any]) -> None:
        if (
            not isinstance(data["title"], str)
            or not data["title"].strip()
            or not isinstance(data["summary"], str)
            or not data["summary"].strip()
        ):
            raise ValueError("title and summary are required strings")
        if not isinstance(data["content"], str):
            raise ValueError("content must be a string")

    @staticmethod
    def _validate_collections(data: dict[str, Any]) -> None:
        concepts = data["concept_ids"]
        if not isinstance(concepts, list) or any(not isinstance(item, str) for item in concepts):
            raise ValueError("concept_ids must be strings")
        context = data["context"]
        if not isinstance(context, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in context.items()
        ):
            raise ValueError("context must be a string map")

    @staticmethod
    def _validate_metadata(data: dict[str, Any]) -> None:
        if isinstance(data["tier"], bool) or not isinstance(data["tier"], int) or not 1 <= data["tier"] <= 5:
            raise ValueError("tier must be an integer from 1 to 5")
        if data["status"] not in {"active", "archived", "superseded"}:
            raise ValueError("invalid status")
        provenance = data["provenance"]
        if not isinstance(provenance, dict) or not isinstance(provenance.get("kind"), str) or not provenance["kind"]:
            raise ValueError("provenance.kind is required")

    def _validate_create_input(self, data: Any) -> tuple[str, str | None]:
        if not isinstance(data, dict):
            raise ValueError("memory must be an object")
        kind, project = self._check_scope(data.get("scope"))
        self._validate_required_fields(data)
        self._validate_text_fields(data)
        self._validate_collections(data)
        self._validate_metadata(data)
        self.lexicon.validate(data["concept_ids"], project)
        return kind, project

    @staticmethod
    def _validate_timestamps(data: dict[str, Any]) -> None:
        for field in ("created_at", "updated_at"):
            if field not in data:
                continue
            try:
                datetime.fromisoformat(str(data[field]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("timestamps must be ISO dates") from exc

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        kind, project = self._validate_create_input(data)
        memory_id = data.get("id") or f"mem-{uuid.uuid4().hex[:24]}"
        validate_memory_id(memory_id)
        self._validate_timestamps(data)
        scope = {"type": kind} if kind == "global" else {"type": "project", "project_id": project}
        normalized = normalize_content(data["content"])
        digest = content_hash(normalized)
        existing = self.storage.read(scope, memory_id)
        if existing and existing.get("content_hash") == digest:
            return {"status": "deduplicated", "memory": _summary(existing)}
        if existing is None:
            for candidate in self.storage.owned(scope):
                if candidate.get("content_hash") == digest:
                    return {"status": "deduplicated", "memory": _summary(candidate)}
        now = _now()
        memory = dict(data)
        memory.update({
            "id": memory_id,
            "scope": scope,
            "content": normalized,
            "content_hash": digest,
            "created_at": existing.get("created_at") if existing else data.get("created_at", now),
            "updated_at": now,
            "revision": int(existing.get("revision", 0)) + 1 if existing else 1,
        })
        memory.pop("project_id", None)
        self.storage.write(memory)
        return {"status": "updated" if existing else "created", "memory": _summary(memory)}

    def _list_options(self, args: dict[str, Any]) -> tuple[str, str | None, int, list[int]]:
        kind, project = self._check_scope(args.get("scope"))
        limit = args.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        tiers = args.get("tiers")
        if tiers is None:
            tiers = [1, 2, 3, 4]
        if not isinstance(tiers, list) or any(
            isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 5 for tier in tiers
        ):
            raise ValueError("tiers must contain integers from 1 to 5")
        return kind, project, min(limit, MAX_LIST_LIMIT), tiers

    def _filter_list_items(
        self,
        items: list[dict[str, Any]],
        query: Any,
        project: str | None,
        tiers: list[int],
    ) -> list[dict[str, Any]]:
        if query:
            concepts = set(self.lexicon.match(query, project))
            query_normalized = normalize_term(query)
            items = [
                item
                for item in items
                if concepts.intersection(item.get("concept_ids", []))
                or query_normalized in normalize_term(f"{item.get('title', '')} {item.get('summary', '')}")
            ]
        return [item for item in items if item.get("tier") in tiers]

    def list(self, args: dict[str, Any]) -> dict[str, Any]:
        kind, project, limit, tiers = self._list_options(args)
        items = self.storage.all_for({"type": kind} if kind == "global" else {"type": "project", "project_id": project})
        items = self._filter_list_items(items, args.get("query"), project, tiers)
        items.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))))
        offset = _cursor_decode(args.get("cursor"))
        page = items[offset : offset + limit]
        result: dict[str, Any] = {"memories": [_summary(item) for item in page]}
        if offset + limit < len(items):
            result["next_cursor"] = _cursor_encode(offset + limit)
        return result

    def get(self, scope: dict[str, Any], memory_id: str) -> dict[str, Any]:
        self._check_scope(scope)
        validate_memory_id(memory_id)
        item = self.storage.read(scope, memory_id)
        if item is None:
            raise ValueError("memory not found")
        return _summary(item)

    def load(self, scope: dict[str, Any], memory_id: str) -> dict[str, Any]:
        self._check_scope(scope)
        validate_memory_id(memory_id)
        item = self.storage.read(scope, memory_id)
        if item is None:
            raise ValueError("memory not found")
        return item

    @staticmethod
    def _cache_file(root: Path, scope: dict[str, Any], conversation_key: str) -> Path:
        validate_scope(scope)
        identity = json.dumps({"scope": scope, "conversation_key": conversation_key}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        return root / "cache" / "conversations" / f"{digest}.json"

    def _retrieve_options(
        self,
        args: dict[str, Any],
    ) -> tuple[str, str | None, str, str | None, dict[str, str], int, int | float]:
        kind, project = self._check_scope(args.get("scope"))
        conversation = args.get("conversation_key")
        query = args.get("query")
        context = args.get("context") or {}
        if (
            not isinstance(conversation, str)
            or not conversation
            or not isinstance(context, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in context.items())
            or (not isinstance(query, str) or not query.strip())
            and not context
        ):
            raise ValueError("conversation_key and query/context are required")
        limit = args.get("limit", 8)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        ttl = args.get("ttl_seconds", 86400)
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 60:
            raise ValueError("ttl_seconds must be at least 60")
        return kind, project, conversation, query, context, min(limit, MAX_RETRIEVE_LIMIT), min(ttl, 604800)

    @staticmethod
    def _load_retrieve_cache(cache_file: Path, ttl: int | float) -> dict[str, Any]:
        cache: dict[str, Any] = {"updated": 0, "delivered": {}}
        try:
            parsed = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - float(parsed.get("updated", 0)) <= ttl:
                delivered = parsed.get("delivered", {})
                if isinstance(delivered, dict):
                    cache = {"updated": parsed.get("updated", 0), "delivered": delivered}
        except (FileNotFoundError, ValueError, TypeError, OSError):
            pass
        return cache

    @staticmethod
    def _candidate_score(
        item: dict[str, Any],
        concepts: set[str],
        query_tokens: set[str],
        context: dict[str, str],
    ) -> int | None:
        if item.get("status") != "active" or not isinstance(item.get("tier"), int) or not 1 <= item["tier"] <= 4:
            return None
        if context and any(
            str(item.get("context", {}).get(key, "")).casefold() != value.casefold()
            for key, value in context.items()
        ):
            return None
        searchable = " ".join([
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("content", "")),
            *item.get("context", {}).values(),
        ])
        score = len(concepts.intersection(item.get("concept_ids", []))) * 10
        score += len(query_tokens.intersection(tokens(searchable)))
        score += sum(
            2
            for key, value in context.items()
            if str(item.get("context", {}).get(key, "")).casefold() == str(value).casefold()
        )
        return score

    def _retrieve_candidates(
        self,
        scope: dict[str, Any],
        project: str | None,
        query: str | None,
        context: dict[str, str],
        limit: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        search = " ".join([query or "", *[f"{key} {value}" for key, value in context.items()]])
        concepts = set(self.lexicon.match(search, project))
        query_tokens = set(tokens(search))
        candidates: list[tuple[int, dict[str, Any]]] = []
        for item in self.storage.all_for(scope):
            score = self._candidate_score(item, concepts, query_tokens, context)
            if score is not None and score > 0:
                candidates.append((score, item))
        candidates.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
        return candidates[:limit]

    @staticmethod
    def _select_deliveries(
        candidates: list[tuple[int, dict[str, Any]]],
        delivered: dict[str, Any],
        force: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        already: list[dict[str, Any]] = []
        for _, item in candidates:
            item_id, revision = item["id"], item["revision"]
            delivery_key = json.dumps({"scope": item.get("scope"), "id": item_id}, sort_keys=True, ensure_ascii=False)
            if force or delivered.get(delivery_key) != revision:
                selected.append(item)
            else:
                already.append({"id": item_id, "revision": revision})
        return selected, already

    @staticmethod
    def _update_delivery_cache(
        delivered: dict[str, Any],
        selected: list[dict[str, Any]],
    ) -> dict[str, Any]:
        for item in selected:
            delivery_key = json.dumps(
                {"scope": item.get("scope"), "id": item["id"]}, sort_keys=True, ensure_ascii=False
            )
            delivered[delivery_key] = item["revision"]
        if len(delivered) > MAX_CACHE_ENTRIES:
            delivered = {key: delivered[key] for key in sorted(delivered)[-MAX_CACHE_ENTRIES:]}
        return delivered

    def retrieve(self, args: dict[str, Any]) -> dict[str, Any]:
        kind, project, conversation, query, context, limit, ttl = self._retrieve_options(args)
        scope = {"type": kind} if kind == "global" else {"type": "project", "project_id": project}
        cache_file = self._cache_file(self.storage.root, args["scope"], conversation)
        cache = self._load_retrieve_cache(cache_file, ttl)
        candidates = self._retrieve_candidates(scope, project, query, context, limit)
        delivered: dict[str, Any] = cache["delivered"]
        selected, already = self._select_deliveries(candidates, delivered, bool(args.get("force_reload", False)))
        delivered = self._update_delivery_cache(delivered, selected)
        cache = {"updated": time.time(), "delivered": delivered}
        atomic_write(cache_file, json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
        return {"memories": [{**_summary(item), "content": item["content"]} for item in selected], "already_delivered": already}

    def reindex(self, args: dict[str, Any]) -> dict[str, Any]:
        scope = args.get("scope")
        query = args.get("query")
        if scope is None and not query:
            raise ValueError("scope or query is required")
        if scope is not None:
            kind, project = self._check_scope(scope)
            items = self.storage.all_for(scope)
        else:
            project = None
            items = self.storage.all_scopes()
        if query:
            concepts = set(self.lexicon.match(query, project))
            normalized = normalize_term(query)
            items = [item for item in items if concepts.intersection(item.get("concept_ids", [])) or normalized in normalize_term(f"{item.get('title', '')} {item.get('summary', '')}")]
        return {"memories": [_summary(item) for item in items if item.get("status") == "active" and item.get("tier") == 5]}
