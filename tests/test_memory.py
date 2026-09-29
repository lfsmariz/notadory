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


def test_project_override_preserves_global_vocabulary(tmp_path):
    service = setup_service(tmp_path)
    service.create_concept(
        "validation", "Project validation", ["checagem da entrada"], ["checar"], ["entrada"],
        {"type": "project", "project_id": "alpha"},
    )
    concept = next(item for item in service.lexicon.concepts("alpha") if item["id"] == "validation")
    assert set(concept["aliases"]) == {"valide a entrada", "validar o payload", "checagem da entrada"}


def test_create_concept_global_and_preserves_existing_lexicon(tmp_path):
    service = setup_service(tmp_path)
    result = service.create_concept(
        "operation.input_validation", "Input validation", [" validate payload ", "validate payload"],
        ["validate"], ["payload"],
    )
    assert result["status"] == "created"
    assert result["concept"]["aliases"] == ["validate payload"]
    assert {item["id"] for item in service.lexicon.concepts()} == {"validation", "deploy", "operation.input_validation"}

    duplicate = service.create_concept(
        "operation.input_validation", "Input validation", ["validate payload"], ["validate"], ["payload"],
    )
    assert duplicate["status"] == "deduplicated"
    with pytest.raises(ValueError, match="different content"):
        service.create_concept(
            "operation.input_validation", "Other", ["validate payload"], ["validate"], ["payload"],
        )


def test_create_concept_override_is_used_by_memory_and_retrieval(tmp_path):
    service = setup_service(tmp_path)
    service.create_concept(
        "project.release", "Project release", ["ship the app"], ["ship"], ["app"],
        {"type": "project", "project_id": "alpha"},
    )
    service.create(memory(
        scope={"type": "project", "project_id": "alpha"}, id="mem-release",
        title="Release", summary="Ship the app", content="Release procedure.",
        concept_ids=["project.release"],
    ))
    result = service.retrieve({
        "scope": {"type": "project", "project_id": "alpha"},
        "query": "ship the app", "conversation_key": "release",
    })
    assert [item["id"] for item in result["memories"]] == ["mem-release"]


def test_create_concept_validation_and_scope_path_safety(tmp_path):
    service = setup_service(tmp_path)
    for concept_id in ("../escape", "Operation.Input", "a..b"):
        with pytest.raises(ValueError):
            service.create_concept(concept_id, "Label", ["alias"], ["action"], ["object"])
    with pytest.raises(ValueError):
        service.create_concept("safe.id", " ", ["alias"], ["action"], ["object"])
    with pytest.raises(ValueError):
        service.create_concept("safe.id", "Label", [""], ["action"], ["object"])
    with pytest.raises(ValueError):
        service.create_concept("safe.id", "Label", ["alias"], ["action"], ["object"], {"type": "project", "project_id": "../x"})


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
