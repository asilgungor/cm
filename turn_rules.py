"""
turn_rules.py
=============
Paylasilan dunyada hafta (tur) ilerletme kurallari (Faz 12 / 14. Asama, 12A). SAF modul: veritabani ve
Streamlit bilmez; saatler cagirandan gelir (test edilebilir).

    AdvanceTrigger       -> READY (herkes hazir) / FORCED (sahip / yonetici) / DEADLINE (sure doldu)
    decide_advance       -> hafta ilerleyebilir mi? (AdvanceDecision: izin, tetik, Turkce neden)
    deadline_for         -> tur acilis zamani + kural saati
    missed_deadline      -> koltuk bu turu kacirdi mi? (yalnizca FORCED / DEADLINE ilerlemede sayilir)
    club_rank_percentiles / club_eligible -> menajer seviyesine gore alinabilecek kulupler
                            (kulup itibar yuzdelik dilimi <= 0.30 + 0.07 * seviye)

Durum: iskelet; Faz 12 A3 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world_rules import WorldRules

_PACKAGE = "Faz 12: turn_rules (A3)"

# club_eligible: seviye 1 -> en alttaki %37'lik dilim, seviye 10 -> tum kulupler
ELIGIBLE_BASE = 0.30
ELIGIBLE_PER_LEVEL = 0.07


class AdvanceTrigger(str, Enum):
    READY = "READY"
    FORCED = "FORCED"
    DEADLINE = "DEADLINE"


@dataclass(frozen=True)
class AdvanceDecision:
    allowed: bool
    trigger: AdvanceTrigger | None
    reason: str


def deadline_for(opened_at: datetime, hours: int) -> datetime:
    raise NotImplementedError(_PACKAGE)


def decide_advance(
    *,
    now: datetime,
    deadline_at: datetime | None,
    active_seat_ids: Collection[int],
    ready_seat_ids: Collection[int],
    actor_role: str | None,
    requested: AdvanceTrigger,
    rules: WorldRules,
) -> AdvanceDecision:
    raise NotImplementedError(_PACKAGE)


def missed_deadline(
    *,
    ready: bool,
    last_active_at: datetime | None,
    turn_opened_at: datetime | None,
    trigger: AdvanceTrigger,
) -> bool:
    raise NotImplementedError(_PACKAGE)


def club_rank_percentiles(reputations: Mapping[int, int]) -> dict[int, float]:
    raise NotImplementedError(_PACKAGE)


def club_eligible(manager_level: int, rank_percentile: float) -> tuple[bool, str]:
    raise NotImplementedError(_PACKAGE)
