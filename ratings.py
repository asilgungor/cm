"""
ratings.py
==========
Oyuncu gucu matematigi (6. Asama'da seed.py'den ayrildi). SAF MANTIK.

Iki kaynagi AYNI olcege indirir:
    * Sentetik oyuncular : takim guc bandindan uretilir (seed.py)
    * FM verisi          : 1-20 arasi FM ozellikleri ve CA (0-200) (fm_parser.py)

Motorun kullandigi 6 ozellik 1-99 olcegindedir:
    pace, shooting, passing, defending, dribbling, goalkeeping
overall_rating her zaman bu ozelliklerin mevkiye gore AGIRLIKLI ORTALAMASIDIR;
boylece "overall 85 ama tum ozellikleri 60" gibi tutarsiz oyuncu olusmaz.
"""

from __future__ import annotations

from collections.abc import Mapping

from models import Position

ENGINE_ATTRIBUTES = ("pace", "shooting", "passing", "defending", "dribbling", "goalkeeping")

# overall_rating = bu agirliklarla hesaplanan agirlikli ortalama (her mevkide toplam 1.0)
POSITION_WEIGHTS: dict[Position, dict[str, float]] = {
    Position.GK:  {"goalkeeping": 0.70, "defending": 0.12, "passing": 0.10,
                   "pace": 0.05, "dribbling": 0.03, "shooting": 0.00},
    Position.DEF: {"defending": 0.50, "pace": 0.18, "passing": 0.15,
                   "dribbling": 0.10, "shooting": 0.07, "goalkeeping": 0.00},
    Position.MID: {"passing": 0.35, "dribbling": 0.22, "defending": 0.18,
                   "pace": 0.13, "shooting": 0.12, "goalkeeping": 0.00},
    Position.FWD: {"shooting": 0.40, "pace": 0.25, "dribbling": 0.22,
                   "passing": 0.10, "defending": 0.03, "goalkeeping": 0.00},
}

# Mevkiye gore ozellik sapmalari. Agirlikli toplamlari ~0: uretilen overall hedefe yakin cikar.
# Sentetik uretimde ve FM verisinde eksik ozellik tamamlamada kullanilir.
POSITION_OFFSETS: dict[Position, dict[str, int]] = {
    Position.GK:  {"goalkeeping": +8, "defending": -20, "passing": -10,
                   "pace": -18, "dribbling": -25, "shooting": -35},
    Position.DEF: {"defending": +6, "pace": +1, "passing": -3,
                   "dribbling": -7, "shooting": -18, "goalkeeping": -45},
    Position.MID: {"passing": +4, "dribbling": +2, "defending": -3,
                   "pace": 0, "shooting": -4, "goalkeeping": -45},
    Position.FWD: {"shooting": +4, "pace": +2, "dribbling": +2,
                   "passing": -8, "defending": -25, "goalkeeping": -45},
}

# Motor ozelligi <- FM ozellikleri (agirlikli). Eksik FM ozellikleri atlanir,
# kalan agirliklar yeniden normalize edilir.
FM_SOURCES: dict[str, dict[str, float]] = {
    "pace":        {"pace": 0.55, "acceleration": 0.45},
    "shooting":    {"finishing": 0.60, "long_shots": 0.25, "composure": 0.15},
    "passing":     {"passing": 0.50, "vision": 0.30, "technique": 0.20},
    "defending":   {"tackling": 0.35, "marking": 0.35, "positioning": 0.30},
    "dribbling":   {"dribbling": 0.50, "technique": 0.30, "agility": 0.20},
    "goalkeeping": {"reflexes": 0.35, "handling": 0.30, "one_on_ones": 0.20, "aerial_reach": 0.15},
}

DEFAULT_OVERALL = 60
OUTFIELD_GK_CEILING = 40      # saha oyuncusunun kalecilik degeri bunu gecemez
OUTFIELD_GK_DEFAULT = 20      # FM saha oyuncularinda kalecilik ozelligi yoktur ('-')


def clamp(value: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(value))))


def compute_overall(position: Position, attrs: Mapping[str, int]) -> int:
    """Motor ozelliklerinden (1-99) mevkiye gore agirlikli genel guc."""
    weights = POSITION_WEIGHTS[position]
    return clamp(sum(weights[a] * attrs[a] for a in ENGINE_ATTRIBUTES), 1, 99)


# ---------------------------------------------------------------------------
# FM olcek donusumleri
# ---------------------------------------------------------------------------

def fm_scale(value: float) -> int:
    """
    FM ozelligi (1-20) -> motor olcegi (1-99).
        1 -> 24    10 -> 60    15 -> 79    18 -> 91    20 -> 99
    Dogrusal degil 'kaydirilmis' olcek: FM'de 10 vasat profesyonel demektir,
    motorumuzda 60 civari da vasat profesyonel bandina denk gelir.
    """
    return clamp(20 + value * 3.95, 1, 99)


def ca_to_overall(current_ability: int) -> int:
    """FM Current Ability (0-200) -> overall (1-99). CA 170 ~ 91, CA 130 ~ 78, CA 100 ~ 68."""
    return clamp(35 + current_ability * 0.33, 30, 99)


def _weighted_fm(fm_attrs: Mapping[str, float], sources: Mapping[str, float]) -> float | None:
    total = weight_sum = 0.0
    for key, weight in sources.items():
        value = fm_attrs.get(key)
        if value is None:
            continue
        total += value * weight
        weight_sum += weight
    return total / weight_sum if weight_sum else None


def derive_engine_attributes(
    position: Position,
    fm_attrs: Mapping[str, float],
    overall_hint: int | None = None,
) -> tuple[dict[str, int], list[str]]:
    """
    FM 1-20 ozelliklerinden motor ozelliklerini turetir.
    Donus: (ozellikler, kaynagi eksik olup tahminle doldurulan ozellikler)
    """
    base = overall_hint if overall_hint is not None else DEFAULT_OVERALL
    attrs: dict[str, int] = {}
    estimated: list[str] = []
    for attr in ENGINE_ATTRIBUTES:
        raw = _weighted_fm(fm_attrs, FM_SOURCES[attr])
        if raw is not None:
            attrs[attr] = fm_scale(raw)
        elif attr == "goalkeeping" and position is not Position.GK:
            attrs[attr] = OUTFIELD_GK_DEFAULT
        else:
            attrs[attr] = clamp(base + POSITION_OFFSETS[position][attr], 20, 99)
            estimated.append(attr)

    if position is not Position.GK:
        attrs["goalkeeping"] = min(attrs["goalkeeping"], OUTFIELD_GK_CEILING)
    return attrs, estimated


def rate_fm_player(
    position: Position,
    fm_attrs: Mapping[str, float],
    current_ability: int | None = None,
) -> tuple[int, dict[str, int], list[str]]:
    """
    FM oyuncusunun (overall, motor ozellikleri, tahmin edilen ozellikler) uclusu.

    CA varsa overall ondan gelir ve eksik ozellikler ona gore tamamlanir.
    CA yoksa overall ozelliklerden hesaplanir; eksik ozellik varsa bu overall
    ile bir tur daha tamamlanir ki tahminler oyuncunun gercek seviyesine otursun.
    """
    if current_ability is not None:
        overall = ca_to_overall(current_ability)
        attrs, estimated = derive_engine_attributes(position, fm_attrs, overall)
        return overall, attrs, estimated

    attrs, estimated = derive_engine_attributes(position, fm_attrs)
    overall = compute_overall(position, attrs)
    if estimated:
        attrs, estimated = derive_engine_attributes(position, fm_attrs, overall)
        overall = compute_overall(position, attrs)
    return overall, attrs, estimated
