"""
penalties.py
============
Seri penalti atislari (8. Asama). SAF MANTIK: veritabani, ORM ve mac motorunu BILMEZ.

Motor (match_engine.py) uzatmalarin sonunda sahada kalan oyunculardan ShootoutSide
nesneleri kurar ve run_shootout'u kendi rastgele uretecisiyle cagirir. Boylece ayni
tohum -> ayni seri; seri mantigi da motordan bagimsiz test edilir.

Uygulanan IFAB kurallari:
    * Atislara yalnizca macin sonunda SAHADA olan oyuncular katilir (atilan, sakatlanip
      cikan ya da oyundan alinan oyuncu atamaz).
    * "Esitlemek icin azaltma" (reduce to equate): oyuncu sayisi fazla olan taraf en zayif
      kaleci-disi atiscilarini listeden cikarir -> iki listede ayni sayida oyuncu kalir.
    * Yazi-tura ilk atan tarafi belirler; atislar kesin sirayla ABAB yapilir.
    * 5'er atis: bir taraf kalan TUM atislarini atsa bile yetisemeyecek hale geldigi anda
      seri biter (fazladan atis yok, eksik atis yok).
    * 5 atis sonunda esitlik -> ani olum: tur iki taraf da attiktan sonra biter; biri atip
      digeri kacirdiysa seri sona erer.
    * Takimdaki herkes birer kez atmadan kimse ikinci kez atamaz; sonraki donguler ayni
      sirayla ilerler. Sira: en yetenekli atisci once, kaleci en sonda.

Olasilik modeli (ShootoutConfig):
    gol = base_conversion
          + skill_influence * ((atisci - taker_reference) - (kaleci - keeper_reference))
          - sudden_death_pressure (ani olumde)
    -> [min_conversion, max_conversion] araligina sikistirilir.
    Gol olmayan atisin kurtaris olma payi kalecinin yetenegiyle artar (gerisi auta gider).
    Iyi kaleci hem golu azaltir hem de kurtaris payini buyutur.

Sonsuz dongu korumasi: ani olum max_sudden_death_rounds turu asarsa (olasiligi pratikte
sifir) son tur deterministik bozulur: gol olasiligi yuksek olan atis gol, digeri auta.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

SIDES = ("home", "away")
OUTCOMES = ("scored", "saved", "missed")


# ===========================================================================
# [1] VERI NESNELERI
# ===========================================================================

@dataclass(frozen=True)
class PenaltyTaker:
    """Atisci adayi. skill 0-100 olcegindedir (motor: sut + moral + form + enerji)."""
    id: int
    name: str
    skill: float


@dataclass
class ShootoutSide:
    """Bir takimin seriye giren kadrosu ve kalesini koruyan oyuncu."""
    team_id: int
    team_name: str
    takers: list[PenaltyTaker]
    keeper_id: int | None
    keeper_name: str
    keeper_skill: float


@dataclass(frozen=True)
class PenaltyKick:
    """Tek bir atis. home_score/away_score bu atistan SONRAKI seri skorudur."""
    number: int                  # serideki genel atis sirasi (1'den baslar)
    round: int                   # tur (1-5 normal, 6+ ani olum)
    side: str                    # "home" / "away"
    team_id: int
    player_id: int
    player_name: str
    keeper_name: str
    outcome: str                 # "scored" / "saved" / "missed"
    home_score: int
    away_score: int
    sudden_death: bool

    @property
    def scored(self) -> bool:
        return self.outcome == "scored"


@dataclass
class ShootoutResult:
    kicks: list[PenaltyKick]
    home_score: int
    away_score: int
    first_side: str
    winner_side: str

    @property
    def loser_side(self) -> str:
        return other_side(self.winner_side)

    @property
    def rounds(self) -> int:
        return max((k.round for k in self.kicks), default=0)

    @property
    def went_to_sudden_death(self) -> bool:
        return any(k.sudden_death for k in self.kicks)

    @property
    def scoreline(self) -> str:
        return f"{self.home_score}-{self.away_score}"

    def side_kicks(self, side: str) -> list[PenaltyKick]:
        return [k for k in self.kicks if k.side == side]


@dataclass(frozen=True)
class ShootoutConfig:
    """Seri penalti kalibrasyonu. Varsayilanlar: esit takimlarda ~%75 gol, ~%2/3'u kurtaris."""
    regulation_kicks: int = 5
    base_conversion: float = 0.76
    taker_reference: float = 66.0          # bu yetenekteki atisci notr
    keeper_reference: float = 78.0         # bu yetenekteki kaleci notr
    skill_influence: float = 0.004         # referansa gore net yetenek farki basina olasilik
    sudden_death_pressure: float = 0.03    # ani olumde sinir: gol olasiligindan dusulur
    min_conversion: float = 0.45
    max_conversion: float = 0.93
    base_save_share: float = 0.62          # gol olmayan atislarin kurtaris payi (notr kaleci)
    save_share_per_point: float = 0.006    # kaleci yetenek puani basina kurtaris payi artisi
    save_share_range: tuple[float, float] = (0.35, 0.85)
    max_sudden_death_rounds: int = 60


# ===========================================================================
# [2] YARDIMCILAR
# ===========================================================================

def other_side(side: str) -> str:
    if side not in SIDES:
        raise ValueError(f"Geçersiz taraf: {side!r}")
    return "away" if side == "home" else "home"


def conversion_probability(taker_skill: float, keeper_skill: float, sudden_death: bool = False,
                           config: ShootoutConfig | None = None) -> float:
    """Atisin gol olma olasiligi (sikistirilmis)."""
    cfg = config or ShootoutConfig()
    edge = (taker_skill - cfg.taker_reference) - (keeper_skill - cfg.keeper_reference)
    p = cfg.base_conversion + cfg.skill_influence * edge
    if sudden_death:
        p -= cfg.sudden_death_pressure
    return max(cfg.min_conversion, min(cfg.max_conversion, p))


def save_share(keeper_skill: float, config: ShootoutConfig | None = None) -> float:
    """Gol olmayan bir atisin kaleci kurtarisi olma olasiligi (kalani auta / direge)."""
    cfg = config or ShootoutConfig()
    lo, hi = cfg.save_share_range
    share = cfg.base_save_share + cfg.save_share_per_point * (keeper_skill - cfg.keeper_reference)
    return max(lo, min(hi, share))


def equalize_takers(
    home_takers: Sequence[PenaltyTaker],
    away_takers: Sequence[PenaltyTaker],
    home_keeper_id: int | None,
    away_keeper_id: int | None,
) -> tuple[list[PenaltyTaker], list[PenaltyTaker]]:
    """
    IFAB "esitlemek icin azaltma": oyuncusu fazla olan taraf en zayif kaleci-disi
    atiscilarini cikarir. Kalanlarin sirasi korunur. Kaleci asla cikarilmaz
    (listede yalnizca kaleci kaldiysa daha fazla azaltilamaz).
    """
    home, away = list(home_takers), list(away_takers)
    target = min(len(home), len(away))
    return _reduce(home, target, home_keeper_id), _reduce(away, target, away_keeper_id)


def _reduce(takers: list[PenaltyTaker], target: int, keeper_id: int | None) -> list[PenaltyTaker]:
    excess = len(takers) - target
    if excess <= 0:
        return takers
    candidates = sorted(
        (i for i, t in enumerate(takers) if t.id != keeper_id),
        key=lambda i: (takers[i].skill, -i),          # en zayif once; esitlikte listede sonraki
    )
    dropped = set(candidates[:excess])
    return [t for i, t in enumerate(takers) if i not in dropped]


def kick_order(takers: Sequence[PenaltyTaker], keeper_id: int | None) -> list[PenaltyTaker]:
    """Atis sirasi: en yetenekli once (esitlikte liste sirasi), kaleci en sonda."""
    indexed = list(enumerate(takers))
    outfield = [(i, t) for i, t in indexed if t.id != keeper_id]
    keepers = [(i, t) for i, t in indexed if t.id == keeper_id]
    outfield.sort(key=lambda it: (-it[1].skill, it[0]))
    return [t for _, t in outfield] + [t for _, t in keepers]


def regulation_decided(home_score: int, away_score: int, home_taken: int, away_taken: int,
                       regulation_kicks: int = 5) -> bool:
    """Bir taraf kalan tum normal atislarini atsa bile yetisemiyorsa True."""
    home_left = max(0, regulation_kicks - home_taken)
    away_left = max(0, regulation_kicks - away_taken)
    return home_score + home_left < away_score or away_score + away_left < home_score


# ===========================================================================
# [3] SERI
# ===========================================================================

def run_shootout(
    rng: random.Random,
    home: ShootoutSide,
    away: ShootoutSide,
    first: str | None = None,
    config: ShootoutConfig | None = None,
) -> ShootoutResult:
    """
    Seriyi oynatir. first verilmezse yazi-tura (rng) ilk atan tarafi secer.
    Rastgelelik sirasi: [yazi-tura], her atis icin bir cekis (+ gol degilse kurtaris/aut icin bir cekis).
    """
    cfg = config or ShootoutConfig()
    if cfg.regulation_kicks < 1:
        raise ValueError("regulation_kicks en az 1 olmalı")
    home_takers, away_takers = equalize_takers(home.takers, away.takers, home.keeper_id, away.keeper_id)
    if not home_takers or not away_takers:
        raise ValueError("Seri penaltı için iki tarafta da en az bir atıcı gerekir")
    if first is None:
        first = "home" if rng.random() < 0.5 else "away"
    second = other_side(first)

    sides = {"home": home, "away": away}
    orders = {"home": kick_order(home_takers, home.keeper_id), "away": kick_order(away_takers, away.keeper_id)}
    score = {"home": 0, "away": 0}
    taken = {"home": 0, "away": 0}
    kicks: list[PenaltyKick] = []

    def next_taker(side: str) -> PenaltyTaker:
        order = orders[side]
        return order[taken[side] % len(order)]

    def probability(side: str, sudden: bool) -> float:
        return conversion_probability(next_taker(side).skill, sides[other_side(side)].keeper_skill, sudden, cfg)

    def kick(side: str, rnd: int, sudden: bool, forced: str | None = None) -> None:
        taker = next_taker(side)
        keeper = sides[other_side(side)]
        if forced is not None:
            outcome = forced
        elif rng.random() < probability(side, sudden):
            outcome = "scored"
        else:
            outcome = "saved" if rng.random() < save_share(keeper.keeper_skill, cfg) else "missed"
        taken[side] += 1
        if outcome == "scored":
            score[side] += 1
        kicks.append(PenaltyKick(
            number=len(kicks) + 1, round=rnd, side=side, team_id=sides[side].team_id,
            player_id=taker.id, player_name=taker.name, keeper_name=keeper.keeper_name,
            outcome=outcome, home_score=score["home"], away_score=score["away"], sudden_death=sudden,
        ))

    n = cfg.regulation_kicks
    decided = False
    for rnd in range(1, n + 1):
        for side in (first, second):
            kick(side, rnd, False)
            if regulation_decided(score["home"], score["away"], taken["home"], taken["away"], n):
                decided = True
                break
        if decided:
            break

    rnd = n
    while score["home"] == score["away"]:
        rnd += 1
        if rnd - n > max(0, cfg.max_sudden_death_rounds):
            # Pratikte hic gerceklesmez; yine de seri sonsuza gidemez.
            first_wins = probability(first, True) >= probability(second, True)
            kick(first, rnd, True, forced="scored" if first_wins else "missed")
            kick(second, rnd, True, forced="missed" if first_wins else "scored")
            break
        kick(first, rnd, True)
        kick(second, rnd, True)

    winner = "home" if score["home"] > score["away"] else "away"
    return ShootoutResult(kicks=kicks, home_score=score["home"], away_score=score["away"],
                          first_side=first, winner_side=winner)
