"""
reputation.py
=============
Menajer tanınırlığı (6. Asama). SAF MANTIK.

manager_reputation 1-20 arasidir (game_state'te tutulur) ve basariyla buyur:
    * Her mac   : galibiyet +, beraberlik az +, maglubiyet -; daha guclu rakibi yenmek bonus
    * Sezon sonu: sampiyonluk buyuk odul, ust yari kucuk odul, alt sira ceza

AI kuluplerinin menajeri yoktur; onlarin "menajer tanınırlığı" kulup itibarindan turetilir
(buyuk kulubun menajeri de tanınmıştır). Transfer pazarlıgında ayni formul kullanilir.
"""

from __future__ import annotations

MIN_REPUTATION = 1.0
MAX_REPUTATION = 20.0
START_REPUTATION = 8.0

MATCH_DELTA = {"W": 0.12, "D": 0.02, "L": -0.08}
UPSET_BONUS = 0.10              # itibari en az UPSET_GAP fazla rakibi yenmek
UPSET_GAP = 5
HEAVY_DEFEAT_PENALTY = 0.06     # 3+ farkli yenilgi

SEASON_CHAMPION = 2.0
SEASON_TOP_HALF = 0.5
SEASON_BOTTOM_HALF = -0.3
SEASON_LAST = -1.0

# Devler Arenasi (8. Asama): tur atlamak ve kupayi kaldirmak kitasal un getirir
CUP_ROUND_WON = {"GROUP": 0.4, "R16": 0.4, "QF": 0.7, "SF": 1.0}   # gecilen tur -> odul
CUP_CHAMPION = 2.5
CUP_RUNNER_UP = 0.6

LABELS: tuple[tuple[float, str], ...] = (
    (5.0, "Tanınmıyor"),
    (9.0, "Yerel"),
    (13.0, "Ulusal"),
    (17.0, "Kıtasal"),
    (MAX_REPUTATION + 1, "Dünyaca ünlü"),
)


def clamp(value: float) -> float:
    return max(MIN_REPUTATION, min(MAX_REPUTATION, value))


def apply(reputation: float, delta: float) -> float:
    return round(clamp(reputation + delta), 2)


def match_delta(outcome: str, own_team_rep: int, opponent_rep: int, goal_difference: int) -> float:
    """Tek macin tanınırlığa etkisi. outcome: 'W' / 'D' / 'L'."""
    delta = MATCH_DELTA[outcome]
    if outcome == "W" and opponent_rep - own_team_rep >= UPSET_GAP:
        delta += UPSET_BONUS
    if outcome == "L" and goal_difference <= -3:
        delta -= HEAVY_DEFEAT_PENALTY
    return delta


def season_delta(league_position: int, league_size: int) -> float:
    """Sezon sonu siralamasinin tanınırlığa etkisi."""
    if league_size <= 1:
        return 0.0
    if league_position == 1:
        return SEASON_CHAMPION
    if league_position == league_size:
        return SEASON_LAST
    if league_position <= league_size / 2:
        return SEASON_TOP_HALF
    return SEASON_BOTTOM_HALF


def label(reputation: float) -> str:
    for ceiling, text in LABELS:
        if reputation < ceiling:
            return text
    return LABELS[-1][1]


def ai_manager_reputation(team_reputation: int) -> float:
    """AI kulubunun menajer tanınırlığı: itibar 92 -> ~16, 78 -> ~12, 70 -> ~9."""
    return round(clamp((team_reputation - 40) / 3.2), 2)


def cup_round_delta(stage: str, won_tie: bool) -> float:
    """
    Kupada bir tur tamamlandiginda tanınırlık etkisi.
    stage: gecilen/elenilen tur ("GROUP", "R16", "QF", "SF", "FINAL").
    Final kazanilirsa sampiyonluk odulu, kaybedilirse finalist odulu; erken turda elenmek notrdur.
    """
    if stage == "FINAL":
        return CUP_CHAMPION if won_tie else CUP_RUNNER_UP
    return CUP_ROUND_WON.get(stage, 0.0) if won_tie else 0.0
