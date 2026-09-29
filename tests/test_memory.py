from __future__ import annotations

import json

import pytest

from notadory_memory.service import MemoryService
from notadory_memory.storage import MemoryStorage, content_hash


def setup_service(tmp_path):
    storage = MemoryStorage(tmp_path)
    storage.init()
    (tmp_path / "lexicon" / "concepts.json").write_text(
        json.dumps({"concepts": [
            {"id": "validation", "aliases": ["valide a entrada", "validar o payload"],
             "action_terms": ["validar"], "object_terms": ["entrada", "payload"]},
            {"id": "deploy", "aliases": ["implantação"], "action_terms": ["publicar"], "object_terms": ["serviço"]},
        ]}), encoding="utf-8"
    )
    return MemoryService(storage)


def memory(**overrides):
    value = {
        "scope": {"type": "global"}, "id": "mem-one", "title": "Validation",
        "summary": "How to validate", "content": "Validate the payload.\r\n",
        "concept_ids": ["validation"], "context": {"area": "api"}, "tier": 1,
        "status": "active", "provenance": {"kind": "test"},
    }
    value.update(overrides)
    return value


def test_persistence_hash_dedup_and_revision(tmp_path):
    service = setup_service(tmp_path)
    first = service.create(memory())
    assert first["status"] == "created"
    assert first["memory"]["content_hash"] == content_hash("Validate the payload.")
    assert service.create(memory())["status"] == "deduplicated"
    revised = service.create(memory(content="A revised payload."))
    assert revised["status"] == "updated"
    assert revised["memory"]["revision"] == 2


def test_pagination_and_no_content(tmp_path):
    service = setup_service(tmp_path)
    service.create(memory(id="mem-a"))
    service.create(memory(id="mem-b", title="Second", content="Another payload."))
    page = service.list({"scope": {"type": "global"}, "limit": 1})
    assert len(page["memories"]) == 1 and "next_cursor" in page
    assert "content" not in page["memories"][0]
    assert "content" not in service.get({"type": "global"}, "mem-a")
    assert len(service.list({"scope": {"type": "global"}, "limit": 1, "cursor": page["next_cursor"]})["memories"]) == 1


def test_scope_isolation_and_project_aggregation(tmp_path):
    service = setup_service(tmp_path)
    service.create(memory(id="mem-global"))
    service.create(memory(id="mem-project", scope={"type": "project", "project_id": "alpha"}, content="Project payload."))
    with pytest.raises(ValueError):
        service.load({"type": "project", "project_id": "alpha"}, "mem-global")
    assert len(service.list({"scope": {"type": "project", "project_id": "alpha"}})["memories"]) == 2
    with pytest.raises(ValueError):
        service.create(memory(scope={"type": "project", "project_id": "../escape"}))
    with pytest.raises(ValueError):
        service.create(memory(scope="project"))


def test_aliases_and_action_object_forms(tmp_path):
    service = setup_service(tmp_path)
    assert service.lexicon.match("valide a entrada") == ["validation"]
    assert service.lexicon.match("validar o payload") == ["validation"]
    assert service.lexicon.match("VALIDAR a ENTRADA") == ["validation"]


def test_retrieve_cache_revision_force_and_excludes_tier_five(tmp_path):
    service = setup_service(tmp_path)
    service.create(memory(id="mem-one"))
    service.create(memory(id="mem-five", tier=5))
    args = {"scope": {"type": "global"}, "query": "validate payload", "conversation_key": "one"}
    first = service.retrieve(args)
    assert [item["id"] for item in first["memories"]] == ["mem-one"]
    second = service.retrieve(args)
    assert not second["memories"] and second["already_delivered"] == [{"id": "mem-one", "revision": 1}]
    forced = service.retrieve({**args, "force_reload": True})
    assert len(forced["memories"]) == 1 and not forced["already_delivered"]
    service.create(memory(id="mem-one", content="revised payload"))
    assert len(service.retrieve({**args, "query": "revised payload"})["memories"]) == 1


def test_retrieve_cache_has_deterministic_entry_limit(tmp_path, monkeypatch):
    service = setup_service(tmp_path)
    for index in range(3):
        service.create(memory(id=f"mem-{index}", content=f"payload validation {index}"))
    monkeypatch.setattr("notadory_memory.service.MAX_CACHE_ENTRIES", 2)
    service.retrieve({"scope": {"type": "global"}, "query": "validation payload", "conversation_key": "bounded", "limit": 20})
    cache = next((tmp_path / "cache" / "conversations").glob("*.json"))
    assert len(json.loads(cache.read_text(encoding="utf-8"))["delivered"]) == 2


def test_reindex_is_read_only_and_only_active_tier_five(tmp_path):
    service = setup_service(tmp_path)
    service.create(memory(id="mem-five", tier=5))
    service.create(memory(id="mem-four", tier=4))
    result = service.reindex({"scope": {"type": "global"}})
    assert [item["id"] for item in result["memories"]] == ["mem-five"]
    assert service.load({"type": "global"}, "mem-five")["revision"] == 1
    with pytest.raises(ValueError):
        service.reindex({})
