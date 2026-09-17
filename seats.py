"""
seats.py
========
Dunyadaki insan menajer koltuklari (Faz 12 / 14. Asama, 12A). Veritabani katmani; commit ETMEZ (cagiranin
islemine katilir). Birincil koltuk / diger koltuklar ayrimini YALNIZCA bu modul bilir:

    * Birincil koltuk = eski tek menajer = dunya sahibi (game_state.user_id). Kulubu ve tanınırlığı
      GameState.user_team_id / GameState.manager_reputation'da kalir (CLI, testler ve eski kayitlar birebir);
      world_managers satiri (is_primary) yalnizca uyelik verisini tutar.
    * Diger koltuklar: kulup ve tanınırlık world_managers satirindadir.
    * human_team_ids() = aktif birincil olmayan koltuklarin kulupleri + {GameState.user_team_id}.
      Eski kariyerde (koltuk satiri yok ya da yalnizca birincil) = {GameState.user_team_id}.

Durum: iskelet; Faz 12 A2 paketinde doldurulacak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_PACKAGE = "Faz 12: seats (A2)"


@dataclass(frozen=True)
class Seat:
    id: int | None                 # None: birincil koltugun satiri henuz yok (ensure_primary_row)
    user_id: int | None
    display_name: str
    is_primary: bool
    team_id: int | None
    reputation: float
    status: str                    # models.SeatStatus degeri
    ready_career_week: int | None
    missed_deadlines: int
    fair_play: float
    joined_career_week: int
    nation_id: int | None


class SeatError(ValueError):
    """Koltuk islemi yapilamaz (mesaj Turkce)."""


class SeatStore:
    def __init__(self, db) -> None:
        raise NotImplementedError(_PACKAGE)

    def primary(self) -> Seat:
        raise NotImplementedError(_PACKAGE)

    def resolve(self, user_id: int | None) -> Seat | None:
        raise NotImplementedError(_PACKAGE)

    def by_team(self, team_id: int) -> Seat | None:
        raise NotImplementedError(_PACKAGE)

    def by_id(self, seat_id: int) -> Seat | None:
        raise NotImplementedError(_PACKAGE)

    def active(self) -> list[Seat]:
        raise NotImplementedError(_PACKAGE)

    def human_team_ids(self) -> frozenset[int]:
        raise NotImplementedError(_PACKAGE)

    def reputation_for_team(self, team_id: int) -> float | None:
        raise NotImplementedError(_PACKAGE)

    def apply_reputation(self, team_id: int, delta: float) -> tuple[float, float]:
        raise NotImplementedError(_PACKAGE)

    def ensure_primary_row(self, user_id: int | None, display_name: str) -> Seat:
        raise NotImplementedError(_PACKAGE)

    def create_seat(self, user_id: int, display_name: str, reputation: float, career_week: int) -> Seat:
        raise NotImplementedError(_PACKAGE)

    def assign_team(self, seat: Seat, team_id: int | None) -> Seat:
        """Kulup benzersizligi GameState.user_team_id dahil denetlenir."""
        raise NotImplementedError(_PACKAGE)

    def release(self, seat: Seat, status: str, protected_until: int | None) -> int | None:
        raise NotImplementedError(_PACKAGE)

    def set_ready(self, seat: Seat, career_week: int | None) -> None:
        raise NotImplementedError(_PACKAGE)

    def touch(self, seat: Seat, now: datetime) -> None:
        """last_active_at; en fazla 5 dakikada bir yazilir."""
        raise NotImplementedError(_PACKAGE)

    def adjust_fair_play(self, seat_id: int, delta: float, reason: str, offer_id: int | None = None) -> float:
        raise NotImplementedError(_PACKAGE)
