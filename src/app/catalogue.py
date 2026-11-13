"""Shared catalogue (causes and skills) and the demo fixture, loaded from data/fixtures."""

import json
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from app.config import FIXTURES_DIR

# Opaque, stable identifiers. Seeded entities derive theirs from the fixture key so a reseed
# gives the same IDs; anything created at runtime gets a random one (app.sqlstore.new_id).
_NAMESPACE = uuid.UUID("6f1d0c43-8a3b-4f43-9d7e-2b3c5d8e9a10")


def stable_id(prefix: str, key: str) -> str:
    return f"{prefix}_{uuid.uuid5(_NAMESPACE, f'{prefix}:{key}').hex[:16]}"


@dataclass(frozen=True)
class Cause:
    id: str
    name: str


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str


@dataclass(frozen=True)
class Catalogue:
    causes: tuple[Cause, ...]
    skills: tuple[Skill, ...]

    @property
    def cause_ids(self) -> set[str]:
        return {c.id for c in self.causes}

    @property
    def skill_ids(self) -> set[str]:
        return {s.id for s in self.skills}

    def cause_name(self, cause_id: str) -> str:
        return next((c.name for c in self.causes if c.id == cause_id), cause_id)

    def skill_name(self, skill_id: str) -> str:
        return next((s.name for s in self.skills if s.id == skill_id), skill_id)


def load_catalogue(path: Path | None = None) -> Catalogue:
    raw = json.loads((path or FIXTURES_DIR / "catalogue.json").read_text(encoding="utf-8"))
    return Catalogue(
        causes=tuple(Cause(**c) for c in raw["causes"]),
        skills=tuple(Skill(**s) for s in raw["skills"]),
    )


@cache
def get_catalogue() -> Catalogue:
    return load_catalogue()


def load_demo(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURES_DIR / "demo.json").read_text(encoding="utf-8"))
