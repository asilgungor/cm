"""
world_cup.py
============
Dunya Kupasi ve eleme gruplari kurallari (Faz 12 / 14. Asama, 12C). SAF modul.

    world_cup_size   -> uygun milli takim sayisina gore turnuva boyu: 32 / 16 / 8 / 4, yetmezse 0
    plan             -> gruplar, grup boyu, eleme turlari ve mac gunu sayisi (32: 8x4 grup + son 16 .. final)
    qualifier_groups / seed_pots / group_draw -> torbalar ve deterministik kura (rng cagirandan)
    knockout_pairs   -> grup siralamalarindan eleme eslesmeleri
    Kura siralama/fikstur yardimcilari cup_draw.rank_group ve cup_draw.group_schedule'dir (DrawSession 16
    kulube bagli oldugundan kullanilmaz).

Durum: iskelet (veri siniflari tanimli); Faz 12 C1 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_PACKAGE = "Faz 12: world_cup (C1)"

WORLD_CUP_SIZES = (32, 16, 8, 4)


@dataclass(frozen=True)
class NationSeed:
    """Kura girdisi (plan 2.3'te adi gecer, alanlari burada sabitlendi)."""
    nation_id: int
    name: str
    reputation: int


@dataclass(frozen=True)
class WorldCupPlan:
    size: int
    groups: int
    group_size: int
    knockout_stages: tuple[str, ...]
    matchdays: int


def world_cup_size(eligible: int) -> int:
    raise NotImplementedError(_PACKAGE)


def plan(size: int) -> WorldCupPlan:
    raise NotImplementedError(_PACKAGE)


def qualifier_groups(nations: Sequence[NationSeed], slots: int, rng) -> list[list[int]]:
    raise NotImplementedError(_PACKAGE)


def seed_pots(nations: Sequence[NationSeed], groups: int) -> list[list[NationSeed]]:
    raise NotImplementedError(_PACKAGE)


def group_draw(rng, pots) -> list[list[int]]:
    raise NotImplementedError(_PACKAGE)


def knockout_pairs(group_rankings) -> list[tuple[int, int]]:
    raise NotImplementedError(_PACKAGE)
