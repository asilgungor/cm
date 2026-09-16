"""
youth.py
========
Altyapi: genc girisi ve baslangic akademisi uretimi (10. Asama). SAF MANTIK: veritabani bilmez.

Genc girisi (generate_intake):
    * yas 16-17, mevki agirlikli (kaleci az: %8)
    * guc ~35-60: ozellikler ratings.POSITION_OFFSETS ile tutarli uretilir, overall agirlikli ortalamadir
    * potansiyel dagilimi kalite endeksiyle (quality_index: tesis %60 + itibar %40, -1..1) kayar:
          pay ~ N(9 + 2.5q, 4)  -> cogu vasat: tavan 50'ler-60'lar
          nadir cevher: %5 + %3q ihtimalle +14..26 ek pay (gercek wonderkid, tavan 75-95)
      Olculen (2000 tohum x 4 genc): wonderkid orani tesis 3/itibar 60 -> %5.5, 10/75 -> %11,
      18/92 -> %22, 20/95 -> %25 (azinlik); potansiyel >= 80: %0.4 / %1.6 / %4.2 / %5.0;
      ortalama guc 45.5 / 48 / 51 / 52, ortalama potansiyel 53 / 58 / 63 / 64
    * adlar kulubun ulkesinin havuzundan (name_pools.YOUTH_NAME_POOLS): Turk kulubune Turk adlari

Baslangic akademisi (generate_academy): ayni uretim, yas 16-19 (buyukler biraz daha gelismis,
kalan payi biraz daha az). Eski kayitlar ve yeni dunyalar icin.

default_facilities: tesis puani (1-20) kulup itibarindan + kucuk sapma.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from development import RATING_MAX, clamp_rating, is_wonderkid
from models import Position
from name_pools import youth_name
from ratings import ENGINE_ATTRIBUTES, POSITION_OFFSETS, compute_overall

INTAKE_AGES: tuple[tuple[int, int], ...] = ((16, 55), (17, 45))
ACADEMY_AGES: tuple[tuple[int, int], ...] = ((16, 25), (17, 30), (18, 25), (19, 20))
INTAKE_POSITIONS: tuple[tuple[Position, int], ...] = (
    (Position.GK, 8), (Position.DEF, 32), (Position.MID, 33), (Position.FWD, 27),
)
OVERALL_MIN, OVERALL_MAX = 35, 60
OVERALL_MEAN = 46.0
OVERALL_QUALITY = 4.0           # kalite endeksi +1 -> ortalama +4
OVERALL_SPREAD = 5.0
OVERALL_PER_YEAR = 2.5          # 16 yas ustu her yil ortalama guce eklenir

HEADROOM_MEAN = 9.0
HEADROOM_QUALITY = 2.5
HEADROOM_SPREAD = 4.0
HEADROOM_MIN = 2
HEADROOM_AGE_DROP = 1.0         # 16 yas ustu her yil kalan pay azalir
GEM_CHANCE = 0.05
GEM_CHANCE_QUALITY = 0.03
GEM_BONUS = (14.0, 26.0)
POTENTIAL_CAP = 95

FACILITIES_MIN, FACILITIES_MAX = 1, 20
INITIAL_ACADEMY_SIZE = (4, 6)   # yeni dunya / eski kayit: kulup basina baslangic akademisi


@dataclass
class YouthSpec:
    """Uretilen genc oyuncu (saf veri). Kayit/maas/deger cagiran taraftadir."""
    name: str
    age: int
    position: Position
    overall: int
    potential: int
    attributes: dict[str, int] = field(default_factory=dict)
    form: int = 55
    morale: int = 70
    contract_years: int = 3

    @property
    def gap(self) -> int:
        return self.potential - self.overall


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def default_facilities(reputation: int, rng: random.Random | None = None) -> int:
    """
    Kulup itibarindan altyapi tesisi puani (1-20): itibar 50 -> 4, 73 -> ~11, 95 -> ~17.5;
    rng verilirse -2..+2 sapma.
    """
    base = 4 + (reputation - 50) * 0.3
    jitter = rng.randint(-2, 2) if rng is not None else 0
    return int(_clamp(round(base + jitter), FACILITIES_MIN, FACILITIES_MAX))


def quality_index(facilities: int | None, reputation: int | None) -> float:
    """Genc girisi kalitesi -1..1: tesis %60 (1 -> -1, 20 -> +1) + itibar %40 (45 -> -1, 95 -> +1)."""
    fac = 10.5 if facilities is None else _clamp(facilities, FACILITIES_MIN, FACILITIES_MAX)
    rep = 70.0 if reputation is None else float(reputation)
    f = (fac - 10.5) / 9.5
    r = _clamp((rep - 70.0) / 25.0, -1.0, 1.0)
    return _clamp(0.6 * f + 0.4 * r, -1.0, 1.0)


def _weighted(rng: random.Random, options: tuple[tuple[object, int], ...]):
    values, weights = zip(*options, strict=True)
    return rng.choices(values, weights=weights, k=1)[0]


def youth_attributes(rng: random.Random, position: Position, target: int) -> dict[str, int]:
    """Hedef guce ve mevkiye uygun tutarli ozellikler (ratings ofsetleri + kucuk sapma)."""
    offsets = POSITION_OFFSETS[position]
    attrs: dict[str, int] = {}
    for attr in ENGINE_ATTRIBUTES:
        value = target + offsets[attr] + rng.randint(-3, 3)
        if attr == "goalkeeping" and position is not Position.GK:
            attrs[attr] = int(_clamp(round(value), 5, 30))
        else:
            attrs[attr] = int(_clamp(round(value), 10, RATING_MAX))
    return attrs


def make_youth(
    rng: random.Random,
    country: str,
    age: int,
    quality: float,
    used_names: set[str],
) -> YouthSpec:
    """Tek genc oyuncu: mevki, ozellikler, guc, potansiyel, ad."""
    position = _weighted(rng, INTAKE_POSITIONS)
    years = max(0, age - 16)
    target = rng.gauss(OVERALL_MEAN + OVERALL_QUALITY * quality + OVERALL_PER_YEAR * years, OVERALL_SPREAD)
    target = _clamp(target, OVERALL_MIN, OVERALL_MAX)
    attrs = youth_attributes(rng, position, round(target))
    overall = int(_clamp(compute_overall(position, attrs), OVERALL_MIN, OVERALL_MAX))

    headroom = rng.gauss(HEADROOM_MEAN + HEADROOM_QUALITY * quality - HEADROOM_AGE_DROP * years, HEADROOM_SPREAD)
    headroom = max(float(HEADROOM_MIN), headroom)
    if rng.random() < _clamp(GEM_CHANCE + GEM_CHANCE_QUALITY * quality, 0.0, 1.0):
        headroom += rng.uniform(*GEM_BONUS)
    potential = int(_clamp(round(overall + headroom), overall + 1, POTENTIAL_CAP))

    return YouthSpec(
        name=youth_name(rng, country, used_names),
        age=age,
        position=position,
        overall=clamp_rating(overall),
        potential=potential,
        attributes=attrs,
        form=rng.randint(50, 62),
        morale=rng.randint(65, 82),
        contract_years=rng.randint(2, 3),
    )


def generate_intake(
    rng: random.Random,
    country: str,
    facilities: int | None,
    reputation: int | None,
    count: int,
    used_names: set[str] | None = None,
) -> list[YouthSpec]:
    """
    Sezonluk genc girisi: `count` oyuncu, yas 16-17. used_names verilirse adlar onunla cakismaz
    (kume yerinde guncellenir).
    """
    used = used_names if used_names is not None else set()
    quality = quality_index(facilities, reputation)
    return [make_youth(rng, country, _weighted(rng, INTAKE_AGES), quality, used) for _ in range(max(0, count))]


def generate_academy(
    rng: random.Random,
    country: str,
    facilities: int | None,
    reputation: int | None,
    count: int,
    used_names: set[str] | None = None,
) -> list[YouthSpec]:
    """Baslangic akademisi: `count` oyuncu, yas 16-19 (yeni dunya / eski kayit doldurma)."""
    used = used_names if used_names is not None else set()
    quality = quality_index(facilities, reputation)
    return [make_youth(rng, country, _weighted(rng, ACADEMY_AGES), quality, used) for _ in range(max(0, count))]


def wonderkid_share(specs: list[YouthSpec]) -> float:
    """Bir listedeki wonderkid orani (istatistik/test yardimcisi)."""
    if not specs:
        return 0.0
    return sum(1 for s in specs if is_wonderkid(s.age, s.overall, s.potential)) / len(specs)
