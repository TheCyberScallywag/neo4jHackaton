"""Project identity and theme colours from brand/brand.toml (the CC0 neutral brand kit kept
from the template)."""

import tomllib
from dataclasses import dataclass
from functools import cache

from app.config import BRAND_DIR


@dataclass(frozen=True)
class Meta:
    name: str
    description: str
    primary: str
    secondary: str
    tertiary: str


@cache
def get_meta() -> Meta:
    raw = tomllib.loads((BRAND_DIR / "brand.toml").read_text(encoding="utf-8"))
    return Meta(
        name=raw["project"]["name"],
        description=raw["project"]["description"],
        primary=raw["theme"]["primary"],
        secondary=raw["theme"]["secondary"],
        tertiary=raw["theme"]["tertiary"],
    )
