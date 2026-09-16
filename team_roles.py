"""
team_roles.py
=============
Duran top ve liderlik rolleri (Soccer Manager tarzi). SAF MANTIK: veritabani, ORM ya da Streamlit
BILMEZ; oyuncu nesnelerine yalnizca ozellik adlariyla (duck typing) bakar. Hem motorun MatchPlayer'i
hem ORM Player (overall_rating, fm_attributes) ile calisir.

    SetPieceRoles(captain_id, penalty_taker_id, free_kick_taker_id, corner_taker_id)
        Hepsi istege bagli (None = belirlenmedi). Varsayilan (hepsi None) motorun eski davranisidir.

Motordaki karsiliklari (match_engine.MatchEngine):
    penalty_taker_id     mac ici penaltiyi (duran top modeli aciksa) sahadaysa o atar; seri
                         penaltilarda ilk atisi o yapar ve "esitlemek icin azaltma"da listeden
                         cikarilmaz. Sahada degilse eski sira (_penalty_taker_skill) gecerlidir.
    free_kick_taker_id   direkt serbest vurus pozisyonlarinda sutu o atar (kalite: frikik becerisi)
    corner_taker_id      kornerlerde ortayi o yapar (kalite: korner/orta becerisi takim seviyesine gore)
    captain_id           sahadayken: kart riski biraz duser, son bolumde geride kalinca savunma
                         dususunun bir kismi silinir, penalti atislarinda sogukkanlilik bonusu.
                         Oyundan cikar / atilirsa etki kaybolur.

Beceri yardimcilari (0-100 olcek): FM ozelligi (1-20, oyuncu.attributes ya da fm_attributes) varsa
x5 ile olceklenir; yoksa motorun temel ozelliklerinden (shooting, passing, pace ...) turetilir.
Motor ve suggest_roles ayni formulleri kullanir.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

ROLE_FIELDS: tuple[str, ...] = ("captain_id", "penalty_taker_id", "free_kick_taker_id", "corner_taker_id")
ROLE_LABELS: dict[str, str] = {
    "captain_id": "Kaptan",
    "penalty_taker_id": "Penaltı atıcısı",
    "free_kick_taker_id": "Serbest vuruş atıcısı",
    "corner_taker_id": "Korner atıcısı",
}
SET_PIECE_FIELDS: tuple[str, ...] = ("penalty_taker_id", "free_kick_taker_id", "corner_taker_id")


@dataclass(frozen=True)
class SetPieceRoles:
    captain_id: int | None = None
    penalty_taker_id: int | None = None
    free_kick_taker_id: int | None = None
    corner_taker_id: int | None = None

    def __post_init__(self) -> None:
        for name in ROLE_FIELDS:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{ROLE_LABELS[name]} oyuncu numarası (tam sayı) olmalı: {value!r}")

    @property
    def is_default(self) -> bool:
        return all(getattr(self, name) is None for name in ROLE_FIELDS)

    @property
    def has_set_piece_takers(self) -> bool:
        """Penalti / frikik / korner aticisindan en az biri belirlendi mi (duran top modeli otomatik acilir)."""
        return any(getattr(self, name) is not None for name in SET_PIECE_FIELDS)

    def to_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in ROLE_FIELDS}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SetPieceRoles:
        """Hosgorulu: eksik / bilinmeyen / gecersiz anahtar -> None."""
        if not isinstance(data, Mapping):
            return cls()
        kwargs: dict[str, int | None] = {}
        for name in ROLE_FIELDS:
            value = data.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                kwargs[name] = value
            elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
                kwargs[name] = int(value.strip())
        return cls(**kwargs)

    def without_player(self, player_id: int) -> SetPieceRoles:
        """Oyuncu kadrodan ciktiysa (satis, sakatlik) rollerinden temizlenmis kopya."""
        return SetPieceRoles(**{name: (None if getattr(self, name) == player_id else getattr(self, name))
                                for name in ROLE_FIELDS})

    def describe(self, names: Mapping[int, str] | None = None) -> str:
        names = names or {}
        parts = []
        for name in ROLE_FIELDS:
            pid = getattr(self, name)
            if pid is not None:
                parts.append(f"{ROLE_LABELS[name]}: {names.get(pid, f'#{pid}')}")
        return " · ".join(parts) if parts else "Roller belirlenmedi"


# ===========================================================================
# Beceri yardimcilari (0-100)
# ===========================================================================

FM_SCALE = 5.0          # FM 1-20 -> 0-100


def _attrs(player: Any) -> Mapping[str, Any]:
    attrs = getattr(player, "attributes", None)
    if not attrs:
        attrs = getattr(player, "fm_attributes", None)
    return attrs if isinstance(attrs, Mapping) else {}


def attribute(player: Any, key: str, fallback: float) -> float:
    """FM ozelligi (1-20) varsa 0-100 olcegine cevrilir; yoksa (ya da bozuksa) fallback."""
    value = _attrs(player).get(key)
    if value is None or isinstance(value, bool):
        return float(fallback)
    try:
        return max(0.0, min(100.0, float(value) * FM_SCALE))
    except (TypeError, ValueError):
        return float(fallback)


def _base(player: Any, name: str, default: float = 50.0) -> float:
    value = getattr(player, name, None)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def overall_of(player: Any) -> float:
    value = getattr(player, "overall", None)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = getattr(player, "overall_rating", None)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 50.0


def is_goalkeeper(player: Any) -> bool:
    pos = getattr(player, "position", None)
    return getattr(pos, "value", pos) == "GK"


def finishing_skill(p: Any) -> float:
    return attribute(p, "finishing", _base(p, "shooting"))


def composure_skill(p: Any) -> float:
    return attribute(p, "composure", _base(p, "morale"))


def penalty_skill(p: Any) -> float:
    """Penalti becerisi: penalti vurusu + bitiricilik + sogukkanlilik."""
    return (0.45 * attribute(p, "penalty_taking", _base(p, "shooting")) + 0.35 * finishing_skill(p)
            + 0.20 * composure_skill(p))


def crossing_skill(p: Any) -> float:
    return attribute(p, "crossing", 0.6 * _base(p, "passing") + 0.4 * _base(p, "dribbling"))


def free_kick_skill(p: Any) -> float:
    """Direkt serbest vurus: frikik + uzaktan sut + teknik."""
    fallback = 0.6 * _base(p, "shooting") + 0.4 * _base(p, "passing")
    return (0.55 * attribute(p, "free_kicks", fallback) + 0.25 * attribute(p, "long_shots", _base(p, "shooting"))
            + 0.20 * attribute(p, "technique", _base(p, "dribbling")))


def corner_skill(p: Any) -> float:
    """Korner ortasi: korner + orta."""
    cross = crossing_skill(p)
    return 0.6 * attribute(p, "corners", cross) + 0.4 * cross


def aerial_skill(p: Any) -> float:
    """Hava topu: kafa vurusu + ziplama. Veri yoksa sut ve defans gucunun ortalamasi."""
    heading = attribute(p, "heading", 0.5 * _base(p, "shooting") + 0.5 * _base(p, "defending"))
    return 0.7 * heading + 0.3 * attribute(p, "jumping_reach", heading)


def technique_skill(p: Any) -> float:
    """Merkezden oyun: kisa pas + top surme / teknik."""
    return (0.5 * attribute(p, "passing", _base(p, "passing"))
            + 0.5 * attribute(p, "technique", _base(p, "dribbling")))


def pace_skill(p: Any) -> float:
    """Hiz: motorun pace'i + (varsa) FM hizlanma."""
    pace = _base(p, "pace")
    return 0.6 * pace + 0.4 * attribute(p, "acceleration", pace)


def captaincy_skill(p: Any) -> float:
    """Kaptanlik: liderlik (varsa) + tecrube (yas) + genel guc."""
    age = _base(p, "age", 25.0)
    experience = max(0.0, min(1.0, (age - 18.0) / 14.0)) * 100.0
    overall = overall_of(p)
    if "leadership" in _attrs(p):
        return 0.5 * attribute(p, "leadership", 50.0) + 0.25 * experience + 0.25 * overall
    return 0.6 * experience + 0.4 * overall


def top_average(values: Iterable[float], n: int) -> float:
    ordered = sorted(values, reverse=True)[:max(1, n)]
    return sum(ordered) / len(ordered) if ordered else 0.0


# ===========================================================================
# Rol onerisi
# ===========================================================================

def _best(players: Sequence[Any], score) -> int | None:
    if not players:
        return None
    best = max(players, key=lambda p: (score(p), -int(getattr(p, "id", 0) or 0)))
    return getattr(best, "id", None)


def suggest_roles(players: Iterable[Any]) -> SetPieceRoles:
    """
    Makul varsayilan roller (esitlikte kucuk id). Cagiran genellikle ilk 11'i verir.

        kaptan   liderlik (FM) + tecrube (yas) + genel guc; kaleci de olabilir
        penalti  penalti vurusu + bitiricilik + sogukkanlilik (kaleci haric)
        frikik   frikik + uzaktan sut + teknik (kaleci haric)
        korner   korner + orta (kaleci haric)
    Saha oyuncusu yoksa aticilar kalecilerden secilir; liste bossa bos roller.
    """
    squad = [p for p in players if getattr(p, "id", None) is not None]
    outfield = [p for p in squad if not is_goalkeeper(p)] or squad
    return SetPieceRoles(
        captain_id=_best(squad, captaincy_skill),
        penalty_taker_id=_best(outfield, penalty_skill),
        free_kick_taker_id=_best(outfield, free_kick_skill),
        corner_taker_id=_best(outfield, corner_skill),
    )
