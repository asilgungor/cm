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

Yeni jenerasyon olcegi (Faz 15B, replacement_intake): emeklilik acigi kuluplere bolusturulur; uretim
kurallari ve isim havuzlari AYNIDIR, yalnizca sezonluk sayi degisir.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
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
    shift: float = 0.0,
    positions: tuple[tuple[Position, int], ...] = INTAKE_POSITIONS,
) -> YouthSpec:
    """
    Tek genc oyuncu: mevki, ozellikler, guc, potansiyel, ad.
    shift (Faz 15B): dunyanin guc capasina gore kaydirma (anchor_shift). 0.0 -> eski davranis birebir aynidir;
    kalan pay (potansiyel - guc) ve wonderkid orani DEGISMEZ, yalnizca mutlak seviye kayar.
    positions (Faz 15B): mevki agirliklari (replacement_positions). Varsayilan INTAKE_POSITIONS = eski davranis.
    """
    position = _weighted(rng, positions)
    if shift and position is Position.GK:            # capali uretimde kaleci duzeltmesi (shift 0 -> eski davranis)
        shift += GK_ANCHOR_BONUS
    years = max(0, age - 16)
    lo, hi = OVERALL_MIN + shift, OVERALL_MAX + shift
    target = rng.gauss(OVERALL_MEAN + shift + OVERALL_QUALITY * quality + OVERALL_PER_YEAR * years, OVERALL_SPREAD)
    target = _clamp(target, lo, hi)
    attrs = youth_attributes(rng, position, round(target))
    overall = int(_clamp(compute_overall(position, attrs), max(1.0, lo), min(float(RATING_MAX), hi)))

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
    shift: float = 0.0,
    positions: tuple[tuple[Position, int], ...] | None = None,
) -> list[YouthSpec]:
    """
    Sezonluk genc girisi: `count` oyuncu, yas 16-17. used_names verilirse adlar onunla cakismaz
    (kume yerinde guncellenir). shift / positions: Faz 15B (varsayilanlar = eski davranis birebir).
    """
    used = used_names if used_names is not None else set()
    quality = quality_index(facilities, reputation)
    mix = positions or INTAKE_POSITIONS
    return [make_youth(rng, country, _weighted(rng, INTAKE_AGES), quality, used, shift, mix)
            for _ in range(max(0, count))]


def generate_academy(
    rng: random.Random,
    country: str,
    facilities: int | None,
    reputation: int | None,
    count: int,
    used_names: set[str] | None = None,
    shift: float = 0.0,
) -> list[YouthSpec]:
    """Baslangic akademisi: `count` oyuncu, yas 16-19 (yeni dunya / eski kayit doldurma)."""
    used = used_names if used_names is not None else set()
    quality = quality_index(facilities, reputation)
    return [make_youth(rng, country, _weighted(rng, ACADEMY_AGES), quality, used, shift)
            for _ in range(max(0, count))]


# ===========================================================================
# YENI JENERASYON OLCEGI (Faz 15B)
# ===========================================================================
#
# Emeklilik acigini kapatan genc girisi. Dunyanin nufus hedefi (game_state.population_target) ile bugunku
# nufus arasindaki fark + bu sezon emekli olmasi BEKLENEN oyuncu sayisi kuluplere bolusturulur; boylece
# nufus emeklilik dalgasinin ONUNDEN doldurulur ve sezon icinde de hedefin etrafinda kalir.
# Kulup basina sayi REPLACEMENT_MIN..REPLACEMENT_MAX araligina kirpilir (genc girisi olcegi: varsayilan 3-4).

REPLACEMENT_MIN, REPLACEMENT_MAX = 0, 8

# Kusak dalgasini sonumleme: emeklilik dalgali gelir (buyuk bir kusak ayni sezonlarda birakir). Acigi oldugu
# gibi doldurursak ayni dalga 16 yasindan geri doner: dunyanin ortalama yasi ve gol/mac bandi salinir
# (olculdu: yas 25,6 -> 26,9 -> 24,9). Bu yuzden beklenen emeklilik SABIT yenilenme oranina dogru yumusatilir
# ve nufus acigi tek sezonda degil, kismen kapatilir.
REPLACEMENT_SEASONS = 19          # ortalama kariyer uzunlugu (16 -> ~35): dogal yenilenme orani
REPLACEMENT_SMOOTH = 0.6          # 1 = tamamen sabit oran, 0 = tamamen bu sezonun beklentisi
REPLACEMENT_GAP_SHARE = 0.5       # nufus acigi bu oranda kapatilir (kalani sonraki sezonlara yayilir)


def replacement_deficit(target: int, population: int, expected_retirements: int) -> int:
    """
    Bu sezon uretilecek genc sayisi: yumusatilmis yenilenme + nufus aciginin bir kismi.
        yenilenme = SMOOTH x (hedef / REPLACEMENT_SEASONS) + (1 - SMOOTH) x beklenen emeklilik
        acik      = GAP_SHARE x (hedef - bugunku nufus)
    Hedef 0 ise (kural hic calismamis) 0 doner.
    """
    target, population = max(0, int(target)), max(0, int(population))
    if target <= 0:
        return 0
    steady = target / REPLACEMENT_SEASONS
    renewal = REPLACEMENT_SMOOTH * steady + (1.0 - REPLACEMENT_SMOOTH) * max(0, int(expected_retirements))
    return max(0, int(round(renewal + REPLACEMENT_GAP_SHARE * (target - population))))

# Dunyanin guc capasi: uretim formulunun notr kulupteki ortalama POTANSIYELI (46 + 9). Acik veri dunyasinda
# A takim oyuncularinin potansiyeli ~78'dir; capa verilmezse her yeni jenerasyon dunyayi 20 puan asagi ceker
# (CM dersi: uzun kayitta ozellik kaymasi). anchor_shift kaydirmayi verir, kalan payi (gap) DEGISTIRMEZ.
ANCHOR_REFERENCE = OVERALL_MEAN + HEADROOM_MEAN
ANCHOR_SHIFT_BOUNDS = (-25.0, 35.0)
# Kaleci duzeltmesi: bir kalecinin GUCU neredeyse dogrudan goalkeeping'idir (agirlik 0,7), saha oyuncusununki
# ise alti ozelligin karisimidir. Ayni capayla uretilen kaleci, dunyanin kalecilerinin ~2 puan altinda kalir
# (olculdu: 1. kaleci goalkeeping ortalamasi 88,4 -> 86,4) ve gol/mac bandi yukari kacar. Capaya eklenir.
GK_ANCHOR_BONUS = 3.0


def anchor_shift(anchor: float | None) -> float:
    """Dunyanin guc capasi (ortalama potansiyel) -> uretim kaydirmasi. None -> 0.0 (eski davranis birebir)."""
    if anchor is None:
        return 0.0
    return _clamp(float(anchor) - ANCHOR_REFERENCE, *ANCHOR_SHIFT_BOUNDS)


# Mevki dagilimi: taban INTAKE_POSITIONS kaleciye %8 verir, ama bir A takiminda kaleci payi ~%11'dir
# (22-26 kisilik kadroda 2-3 kaleci). Fark kapanmazsa kaleciler yaslanir, kulupler 2 kaleciyle kalir
# (emeklilik guvencesi onlari birakamaz) ve gol/mac bandi yukari kacar -- olculdu, bkz. 15B teslim notu.
# Cozum: yeni jenerasyonun mevki dagilimi, BIRAKMASI BEKLENEN kusagin mevki dagilimiyla karistirilir
# (negatif geri besleme: kaleciler yaslandikca daha cok kaleci uretilir).
REPLACEMENT_POSITION_BLEND = 0.25       # 0 = tamamen kayip dagilimi, 1 = tamamen taban dagilim
REPLACEMENT_POSITION_MIN = 4            # hicbir mevki agirligi bunun altina inmez (yuzde)
# Kadro gerekliliginden gelen taban paylar: 22-26 kisilik kadroda 2-3 kaleci = ~%11.
REPLACEMENT_POSITION_FLOOR: dict[Position, int] = {Position.GK: 10}


def replacement_positions(expected: Mapping[object, float] | None) -> tuple[tuple[Position, int], ...]:
    """
    Bu sezon birakmasi beklenen kusagin mevki dagilimi (mevki -> beklenen sayi) ile taban dagilimin karisimi;
    kaleci payi kadro gerekliliginin (REPLACEMENT_POSITION_FLOOR) altina inmez.
    Bos / gecersiz girdi -> INTAKE_POSITIONS (eski davranis birebir).
    """
    if not expected:
        return INTAKE_POSITIONS
    total = sum(max(0.0, float(expected.get(pos, 0.0))) for pos, _w in INTAKE_POSITIONS)
    if total <= 0:
        return INTAKE_POSITIONS
    weights = []
    for pos, base in INTAKE_POSITIONS:
        share = max(0.0, float(expected.get(pos, 0.0))) / total
        blended = REPLACEMENT_POSITION_BLEND * base / 100.0 + (1.0 - REPLACEMENT_POSITION_BLEND) * share
        floor = REPLACEMENT_POSITION_FLOOR.get(pos, REPLACEMENT_POSITION_MIN)
        weights.append((pos, max(floor, int(round(blended * 100)))))
    return tuple(weights)


def replacement_intake(deficit: int, clubs: int) -> tuple[int, int]:
    """
    Dunya acigini kuluplere bolusturur: (kulup basina taban, fazladan bir genc alacak kulup sayisi).
    Taban REPLACEMENT_MIN..REPLACEMENT_MAX arasinda kirpilir; tavandayken artik dagitilacak fazla yoktur.
    Acik kulup sayisindan kucukse taban 0'dir ve yalnizca `acik` kadar kulup birer genc alir (sirasi
    career_manager'da sezona gore doner: her kulup birkac sezonda bir girise girer).
        1000 acik / 114 kulup -> (8, 0)   200 / 114 -> (1, 86)   40 / 114 -> (0, 40)   0 / 114 -> (0, 0)
    """
    clubs = max(0, int(clubs))
    if clubs == 0:
        return 0, 0
    deficit = max(0, int(deficit))
    per_club = int(_clamp(deficit // clubs, REPLACEMENT_MIN, REPLACEMENT_MAX))
    if per_club >= REPLACEMENT_MAX or per_club > deficit // clubs:
        return per_club, 0
    return per_club, max(0, min(clubs, deficit - per_club * clubs))


def wonderkid_share(specs: list[YouthSpec]) -> float:
    """Bir listedeki wonderkid orani (istatistik/test yardimcisi)."""
    if not specs:
        return 0.0
    return sum(1 for s in specs if is_wonderkid(s.age, s.overall, s.potential)) / len(specs)
