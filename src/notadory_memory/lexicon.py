"""Deterministic multilingual-ish concept matching for the V1 lexicon."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .storage import atomic_write, safe_segment, validate_scope

STOP_WORDS = {
    "a", "o", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das",
    "e", "ou", "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "que", "se", "ao", "aos",
}


def normalize_term(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _stem(token: str) -> str:
    for ending in ("ando", "endo", "indo", "ções", "ção", "mente", "ar", "er", "ir", "es", "s", "e"):
        if len(token) > len(ending) + 2 and token.endswith(ending):
            return token[: -len(ending)]
    return token


def tokens(value: str) -> list[str]:
    return [_stem(item) for item in normalize_term(value).split() if item and item not in STOP_WORDS]


def _items(data: Any) -> list[dict[str, Any]]:
    values = data if isinstance(data, list) else data.get("concepts", []) if isinstance(data, dict) else []
    return [item for item in values if isinstance(item, dict) and isinstance(item.get("id"), str)]


CONCEPT_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


def _concept_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array of strings")
    normalized: dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain non-empty strings")
        item = re.sub(r"\s+", " ", item.strip())
        normalized.setdefault(item.casefold(), item)
    return [normalized[key] for key in sorted(normalized)]


def normalize_concept(
    concept_id: Any,
    label: Any,
    aliases: Any,
    action_terms: Any,
    object_terms: Any,
) -> dict[str, Any]:
    if not isinstance(concept_id, str) or not CONCEPT_ID.fullmatch(concept_id):
        raise ValueError("concept_id must be a canonical safe identifier")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label must be a non-empty string")
    return {
        "id": concept_id,
        "label": re.sub(r"\s+", " ", label.strip()),
        "aliases": _concept_string_list(aliases, "aliases"),
        "action_terms": _concept_string_list(action_terms, "action_terms"),
        "object_terms": _concept_string_list(object_terms, "object_terms"),
    }


class Lexicon:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _read_document(path: Path) -> tuple[dict[str, Any], list[Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"concepts": []}, []
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError("concept lexicon is not valid JSON") from exc

        if isinstance(raw, dict):
            concepts = raw.get("concepts", [])
            if not isinstance(concepts, list):
                raise ValueError("concept lexicon concepts must be an array")
            return dict(raw), concepts
        if isinstance(raw, list):
            return {"concepts": raw}, raw
        raise ValueError("concept lexicon must be an object")

    @staticmethod
    def _append_terms(concept: dict[str, Any], additions: dict[str, list[str]]) -> bool:
        changed = False
        for field, terms in additions.items():
            existing = concept.get(field, [])
            if not isinstance(existing, list):
                raise ValueError(f"{field} must be an array in the concept lexicon")
            present = {
                re.sub(r"\s+", " ", item.strip()).casefold()
                for item in existing
                if isinstance(item, str)
            }
            for term in terms:
                if term.casefold() in present:
                    continue
                existing.append(term)
                present.add(term.casefold())
                changed = True
            concept[field] = existing
        return changed

    @staticmethod
    def _append_request(
        concept_id: Any,
        aliases: Any,
        action_terms: Any,
        object_terms: Any,
    ) -> dict[str, list[str]]:
        if not isinstance(concept_id, str) or not CONCEPT_ID.fullmatch(concept_id):
            raise ValueError("concept_id must be a canonical safe identifier")
        provided = {
            field: _concept_string_list(value, field)
            for field, value in (
                ("aliases", aliases),
                ("action_terms", action_terms),
                ("object_terms", object_terms),
            )
            if value is not None
        }
        if not provided:
            raise ValueError("at least one non-empty term list is required")
        return provided

    @staticmethod
    def _find_concept(concepts: list[Any], concept_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in concepts if isinstance(item, dict) and item.get("id") == concept_id),
            None,
        )

    def _append_global_terms(
        self,
        concept_id: str,
        additions: dict[str, list[str]],
    ) -> dict[str, Any]:
        path = self.root / "lexicon" / "concepts.json"
        document, concepts = self._read_document(path)
        target = self._find_concept(concepts, concept_id)
        if target is None:
            raise ValueError(f"concept_id does not exist: {concept_id}")
        changed = self._append_terms(target, additions)
        if changed:
            document["concepts"] = concepts
            self._write_document(path, document)
        return {"status": "updated" if changed else "deduplicated", "concept": target}

    def _append_project_terms(
        self,
        concept_id: str,
        additions: dict[str, list[str]],
        project: str,
    ) -> dict[str, Any]:
        global_path = self.root / "lexicon" / "concepts.json"
        self._read_document(global_path)
        path = self.root / "lexicon" / "project-overrides" / f"{safe_segment(project, 'project_id')}.json"
        document, concepts = self._read_document(path)
        if self._find_concept(self.concepts(project), concept_id) is None:
            raise ValueError(f"concept_id does not exist: {concept_id}")

        target = self._find_concept(concepts, concept_id)
        if target is None:
            target = {"id": concept_id, **{field: list(terms) for field, terms in additions.items()}}
            concepts.append(target)
            changed = True
        else:
            changed = self._append_terms(target, additions)
        if changed:
            document["concepts"] = concepts
            self._write_document(path, document)

        updated_effective = self._find_concept(self.concepts(project), concept_id)
        return {"status": "updated" if changed else "deduplicated", "concept": updated_effective}

    @staticmethod
    def _write_document(path: Path, document: dict[str, Any]) -> None:
        atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def append_concept_terms(
        self,
        concept_id: Any,
        aliases: Any = None,
        action_terms: Any = None,
        object_terms: Any = None,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provided = self._append_request(concept_id, aliases, action_terms, object_terms)
        kind, project = validate_scope({"type": "global"} if scope is None else scope)
        if kind == "global":
            return self._append_global_terms(concept_id, provided)
        return self._append_project_terms(concept_id, provided, project)

    def create_concept(
        self,
        concept_id: Any,
        label: Any,
        aliases: Any,
        action_terms: Any,
        object_terms: Any,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        concept = normalize_concept(concept_id, label, aliases, action_terms, object_terms)
        kind, project = validate_scope({"type": "global"} if scope is None else scope)
        path = self.root / "lexicon" / "concepts.json"
        if kind == "project":
            path = self.root / "lexicon" / "project-overrides" / f"{safe_segment(project, 'project_id')}.json"

        document, concepts = self._read_document(path)

        for existing in concepts:
            if not isinstance(existing, dict) or existing.get("id") != concept_id:
                continue
            try:
                existing_content = normalize_concept(
                    existing.get("id"), existing.get("label"), existing.get("aliases"),
                    existing.get("action_terms"), existing.get("object_terms"),
                )
            except ValueError:
                existing_content = None
            if existing_content == concept:
                return {"status": "deduplicated", "concept": existing}
            raise ValueError(f"concept_id already exists with different content: {concept_id}")

        concepts.append(concept)
        document["concepts"] = concepts
        atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return {"status": "created", "concept": concept}

    def concepts(self, project_id: str | None = None) -> list[dict[str, Any]]:
        path = self.root / "lexicon" / "concepts.json"
        try:
            base = _items(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            base = []
        if not project_id:
            return base
        override_path = self.root / "lexicon" / "project-overrides" / f"{safe_segment(project_id, 'project_id')}.json"
        try:
            extras = _items(json.loads(override_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            extras = []
        merged: dict[str, dict[str, Any]] = {item["id"]: dict(item) for item in base}
        for item in extras:
            if item["id"] not in merged:
                merged[item["id"]] = dict(item)
                continue
            old = merged[item["id"]]
            previous_lists = {field: list(old.get(field) or []) for field in ("aliases", "action_terms", "object_terms")}
            old.update(item)
            for field in ("aliases", "action_terms", "object_terms"):
                if field in item or field in old:
                    old[field] = list(dict.fromkeys([*previous_lists[field], *(item.get(field) or [])]))
        return list(merged.values())

    def validate(self, concept_ids: list[str], project_id: str | None = None) -> None:
        known = {item["id"] for item in self.concepts(project_id)}
        missing = [item for item in concept_ids if item not in known]
        if missing:
            raise ValueError(f"unknown concept_ids: {', '.join(missing)}")

    def match(self, text: str, project_id: str | None = None) -> list[str]:
        normalized = normalize_term(text)
        text_tokens = set(tokens(text))
        result: list[str] = []
        for concept in self.concepts(project_id):
            aliases = [normalize_term(value) for value in concept.get("aliases", []) if isinstance(value, str)]
            alias_hit = any(alias and (normalized == alias or f" {alias} " in f" {normalized} ") for alias in aliases)
            actions = {_stem(token) for value in concept.get("action_terms", []) for token in tokens(str(value))}
            objects = {_stem(token) for value in concept.get("object_terms", []) for token in tokens(str(value))}
            if alias_hit or (actions and objects and actions & text_tokens and objects & text_tokens):
                result.append(concept["id"])
        return result
