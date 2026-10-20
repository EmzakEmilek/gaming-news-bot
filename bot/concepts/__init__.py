"""Koncepty postov mimo bežných správ. Striedajú sa podľa dňa v týždni (config.yaml: concepts.schedule),
každý má vlastnú šablónu snímok. Keď koncept nevyjde (málo dát, chyba, neprešiel kontrolou), slot dostane
bežnú správu."""
from __future__ import annotations

from datetime import date

from ..common import log
from .common import ConceptSkip

NAMES = ("radar", "vs", "deal", "quiz", "take", "retro", "digest")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def scheduled(cfg: dict, today: date, slot: str) -> str | None:
    """Koncept pre tento slot podľa rozvrhu, None = bežná správa."""
    c = cfg.get("concepts") or {}
    if not c.get("enabled") or slot != c.get("slot", "vecer"):
        return None
    name = (c.get("schedule") or {}).get(DAYS[today.weekday()])
    return name if name in NAMES else None


def make(name: str, cfg: dict, posted: list[dict], today: date) -> dict | None:
    """Hotový post konceptu, alebo None, keď dnes nevyjde (dôvod ide do logu)."""
    from importlib import import_module
    try:
        post = import_module(f"{__name__}.{name}").make(cfg, posted, today)
    except ConceptSkip as e:
        log.info("Koncept %s dnes nevyšiel: %s", name, e)
        return None
    log.info("Koncept %s: %s", name, post["headline"])
    return post
