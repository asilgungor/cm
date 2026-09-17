"""
national_rules.py
=================
Milli takim kurallari (Faz 12 / 14. Asama, 12C). SAF modul.

    nation_of          -> oyuncunun milli takimi: uyruk, yoksa (sentetik oyuncu) liginin ulkesi
    eligible_nations   -> en az MIN_NATIONAL_PLAYERS oyuncusu olan uluslar
    ai_callups         -> AI milli kadro secimi (en fazla limit oyuncu id'si)
    job_offer_eligible -> menajer seviyesi ve ulke siralamasina gore milli takim teklifi
    sack_decision      -> beklenen tura gore gorevden alma
    intl_reputation_delta -> milli mac / turnuva sonucunun menajer tanınırlığına etkisi

Durum: iskelet (sabitler tanimli); Faz 12 C1 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

_PACKAGE = "Faz 12: national_rules (C1)"

MIN_NATIONAL_PLAYERS = 23
MAX_CALLUPS = 30
MIN_MATCHDAY_SQUAD = 16
CONTRACT_MAX_SEASONS = 2


def nation_of(player_nationality: str | None, league_country: str) -> str:
    raise NotImplementedError(_PACKAGE)


def eligible_nations(counts: Mapping[str, int]) -> list[str]:
    raise NotImplementedError(_PACKAGE)


def ai_callups(players: Sequence, limit: int = 23) -> list[int]:
    raise NotImplementedError(_PACKAGE)


def job_offer_eligible(manager_level: int, nation_rank_pct: float) -> bool:
    raise NotImplementedError(_PACKAGE)


def sack_decision(expected_stage: str, reached_stage: str, points_share: float) -> tuple[bool, str]:
    raise NotImplementedError(_PACKAGE)


def intl_reputation_delta(outcome: str, stage: str | None, won_tournament: bool) -> float:
    raise NotImplementedError(_PACKAGE)
