"""
world_manager.py
================
Paylasilan dunya tur motoru ve yonetim arka ucu (Faz 12 / 14. Asama, 12A). Dunya semasinda calisir.

    WorldController   -> bir menajerin dunyadaki islemleri (kulup alma/birakma, hazir, rapor, yonetici
                         islemleri). Commit ETMEZ; web callback'i (web_common.member_callback) SHARED dunya
                         kilidi altinda cagirir.
    try_advance       -> hafta / sezon ilerletme. KENDI islemini acar, database.world_lock(sema, "try_exclusive")
                         alir; expected=(sezon, hafta) karsilastir-ve-degistir: hafta bu arada ilerlediyse
                         hicbir sey yapmaz. Acik bir oturum varken ASLA cagrilmaz (kilit yukseltme yok).
    maybe_advance_due -> sayfa yuklenirken suresi dolmus tur varsa beklemeden dener
    advance_due_worlds-> CLI: python main.py world-tick [--all]

Durum: iskelet; Faz 12 A3 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from turn_rules import AdvanceTrigger
from worlds import WorldError

if TYPE_CHECKING:
    from seats import Seat
    from world_rules import WorldRules
    from worlds import WorldContext

_PACKAGE = "Faz 12: world_manager (A3)"


class TurnError(ValueError):
    """Tur islemi yapilamaz (mesaj Turkce)."""


class AdvanceNotAllowed(TurnError):
    pass


class AdvanceInProgress(TurnError):
    pass


class StaleTurnError(TurnError):
    pass


class ClubUnavailableError(WorldError):
    pass


@dataclass(frozen=True)
class TurnStatus:
    season: int
    week: int
    career_week: int
    phase: str                        # SEASON / SEASON_END / CLOSE_SEASON
    opened_at: datetime | None
    deadline_at: datetime | None
    seconds_left: int | None
    active: int
    ready: int
    ready_names: list[str]
    waiting_names: list[str]
    me_ready: bool
    can_force: bool
    can_ready: bool
    auto_due: bool


@dataclass(frozen=True)
class AdvanceResult:
    advanced: bool
    kind: str                         # WEEK / NEW_SEASON / CLOSE_SEASON_MATCHDAY / NONE
    trigger: AdvanceTrigger | None
    season: int
    week: int
    released: list[str]
    message: str


@dataclass(frozen=True)
class ClubOffer:
    team_id: int
    team_name: str
    league_name: str
    reputation: int
    stars: float
    squad_rating: float
    transfer_budget: int
    eligible: bool
    reason: str
    protected: bool


@dataclass(frozen=True)
class ManagerRow:
    seat_id: int
    user_id: int | None
    name: str
    role: str | None
    team_name: str | None
    reputation: float
    level_title: str
    status: str
    ready: bool
    last_active_at: datetime | None
    missed_deadlines: int
    fair_play: float
    nation_name: str | None


@dataclass(frozen=True)
class ManagerReport:
    season: int
    week: int
    midweek: bool
    lines: list[tuple[str, str]]
    cup_lines: list[tuple[str, str]]


@dataclass(frozen=True)
class WorldEventView:
    """world_events satirinin okunur hali (plan 2.1'de adi gecer, alanlari burada sabitlendi)."""
    id: int
    season: int
    week: int
    career_week: int
    kind: str
    actor_name: str | None
    text: str
    created_at: datetime | None
    payload: Mapping[str, Any] = field(default_factory=dict)


class WorldController:
    def __init__(self, db, ctx: WorldContext) -> None:
        raise NotImplementedError(_PACKAGE)

    def turn_status(self, now: datetime | None = None) -> TurnStatus:
        raise NotImplementedError(_PACKAGE)

    def managers(self) -> list[ManagerRow]:
        raise NotImplementedError(_PACKAGE)

    def club_offers(self, query: str = "", only_eligible: bool = True) -> list[ClubOffer]:
        raise NotImplementedError(_PACKAGE)

    def my_reports(self, limit: int = 5) -> list[ManagerReport]:
        raise NotImplementedError(_PACKAGE)

    def events(self, limit: int = 50) -> list[WorldEventView]:
        raise NotImplementedError(_PACKAGE)

    def claim_club(self, team_id: int) -> Seat:
        raise NotImplementedError(_PACKAGE)

    def release_club(self) -> None:
        raise NotImplementedError(_PACKAGE)

    def set_ready(self, ready: bool, expected_career_week: int) -> TurnStatus:
        raise NotImplementedError(_PACKAGE)

    def touch(self) -> None:
        raise NotImplementedError(_PACKAGE)

    def kick(self, seat_id: int, reason: str) -> None:
        raise NotImplementedError(_PACKAGE)

    def update_rules(self, changes: Mapping[str, object]) -> WorldRules:
        raise NotImplementedError(_PACKAGE)

    def can_draw_cup(self) -> bool:
        raise NotImplementedError(_PACKAGE)


def try_advance(
    ctx: WorldContext,
    trigger: AdvanceTrigger,
    expected: tuple[int, int] | None,
    now: datetime | None = None,
) -> AdvanceResult:
    raise NotImplementedError(_PACKAGE)


def maybe_advance_due(ctx: WorldContext, now: datetime | None = None) -> AdvanceResult | None:
    raise NotImplementedError(_PACKAGE)


def advance_due_worlds(now: datetime | None = None) -> list[AdvanceResult]:
    raise NotImplementedError(_PACKAGE)
