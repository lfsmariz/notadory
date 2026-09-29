"""Deterministic multilingual-ish concept matching for the V1 lexicon."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .storage import safe_segment

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


class Lexicon:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

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
            old.update(item)
            for field in ("aliases", "action_terms", "object_terms"):
                if field in item or field in old:
                    old[field] = list(dict.fromkeys([*(old.get(field) or []), *(item.get(field) or [])]))
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
