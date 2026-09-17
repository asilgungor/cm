"""
turn_rules.py
=============
Paylasilan dunyada hafta (tur) ilerletme kurallari (Faz 12 / 14. Asama, 12A). SAF modul: veritabani ve
Streamlit bilmez; saatler cagirandan gelir (test edilebilir). Saatler UTC; saat dilimi olmayan (naive) degerler
UTC kabul edilir.

    AdvanceTrigger       -> READY (herkes hazir) / FORCED (sahip / yonetici) / DEADLINE (sure doldu)
    decide_advance       -> hafta ilerleyebilir mi? (AdvanceDecision: izin, GERCEKLESEN tetik, Turkce neden)
    deadline_for         -> tur acilis zamani + kural saati
    missed_deadline      -> koltuk bu turu kacirdi mi? (yalnizca FORCED / DEADLINE ilerlemede sayilir)
    club_rank_percentiles / club_eligible -> menajer seviyesine gore alinabilecek kulupler
                            (kulup itibar yuzdelik dilimi <= 0.30 + 0.07 * seviye)

decide_advance dogruluk tablosu (istenen tetikten bagimsiz olarak ilk uyan kazanir):
    1) READY    : rules.ready_check, en az bir aktif (kulubu olan) koltuk ve HEPSI hazir
    2) FORCED   : istenen FORCED ve istek sahibinin rolu OWNER / ADMIN
    3) DEADLINE : rules.auto_advance, tur suresi belli ve now >= deadline_at
    aksi halde izin yok; neden istenen tetige gore yazilir (orn. "2 menajer daha hazır değil.").
Boylece "hazirim" tiklamasi sure dolmussa DEADLINE, sayfa acilisindaki sure denetimi herkes hazirsa READY ile
ilerletir; yoneticinin zorlamasi herkes hazirsa READY sayilir (kimse kacirmis sayilmaz).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world_rules import WorldRules

# club_eligible: seviye 1 -> en alttaki %37'lik dilim, seviye 10 -> tum kulupler
ELIGIBLE_BASE = 0.30
ELIGIBLE_PER_LEVEL = 0.07
MIN_LEVEL = 1
MAX_LEVEL = 10
_EPSILON = 1e-9

ADMIN_ROLES = frozenset({"OWNER", "ADMIN"})


class AdvanceTrigger(str, Enum):
    READY = "READY"
    FORCED = "FORCED"
    DEADLINE = "DEADLINE"


@dataclass(frozen=True)
class AdvanceDecision:
    allowed: bool
    trigger: AdvanceTrigger | None
    reason: str


def as_utc(moment: datetime | None) -> datetime | None:
    """Saat dilimli UTC; naive deger UTC kabul edilir. None -> None."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def deadline_for(opened_at: datetime, hours: int) -> datetime:
    """Tur acilisi + kural saati (UTC). Saat 1'den az ya da tamsayi degilse ValueError."""
    if opened_at is None:
        raise ValueError("Tur açılış zamanı yok.")
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1:
        raise ValueError(f"Hafta süresi en az 1 saat olmalı: {hours!r}")
    return as_utc(opened_at) + timedelta(hours=hours)


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
    """Modul belgesindeki tablo. active_seat_ids: ACTIVE ve kulubu olan koltuklar; hazirlar bunlarla kesisir."""
    requested = AdvanceTrigger(requested)
    active = set(active_seat_ids)
    waiting = len(active - set(ready_seat_ids))
    all_ready = bool(rules.ready_check) and bool(active) and waiting == 0
    deadline = as_utc(deadline_at)
    due = bool(rules.auto_advance) and deadline is not None and as_utc(now) >= deadline
    is_admin = (actor_role or "").upper() in ADMIN_ROLES

    if all_ready:
        return AdvanceDecision(True, AdvanceTrigger.READY, "Tüm menajerler hazır; hafta oynanıyor.")
    if requested is AdvanceTrigger.FORCED and is_admin:
        return AdvanceDecision(True, AdvanceTrigger.FORCED, "Hafta dünya yöneticisi tarafından ilerletildi.")
    if due:
        return AdvanceDecision(True, AdvanceTrigger.DEADLINE,
                               "Hafta süresi doldu; hazır olmayan menajerler beklenmeden hafta oynanıyor.")

    if requested is AdvanceTrigger.FORCED:
        reason = "Haftayı yalnızca dünyanın sahibi ya da yöneticisi zorla ilerletebilir."
    elif requested is AdvanceTrigger.READY:
        if not rules.ready_check:
            reason = "Bu dünyada hazır kontrolü kapalı; hafta süre dolunca ilerler."
        elif not active:
            reason = "Kulübü olan aktif menajer yok."
        else:
            reason = f"{waiting} menajer daha hazır değil."
    elif not rules.auto_advance:
        reason = "Bu dünyada süre dolunca otomatik ilerleme kapalı."
    elif deadline is None:
        reason = "Bu hafta için süre henüz başlatılmadı."
    else:
        reason = "Hafta süresi henüz dolmadı."
    return AdvanceDecision(False, None, reason)


def missed_deadline(
    *,
    ready: bool,
    last_active_at: datetime | None,
    turn_opened_at: datetime | None,
    trigger: AdvanceTrigger,
) -> bool:
    """
    Koltuk bu turu kacirdi mi? Yalnizca FORCED / DEADLINE ilerlemede ve koltuk hazir degilken: tur acildiktan
    sonra hic etkinligi yoksa (last_active_at yok ya da acilistan once). Tur acilisi bilinmiyorsa sayilmaz.
    """
    if AdvanceTrigger(trigger) is AdvanceTrigger.READY or ready:
        return False
    opened = as_utc(turn_opened_at)
    if opened is None:
        return False
    active = as_utc(last_active_at)
    return active is None or active < opened


def club_rank_percentiles(reputations: Mapping[int, int]) -> dict[int, float]:
    """
    Kulup id -> itibar yuzdelik dilimi 0.0 (en dusuk) .. 1.0 (en yuksek): kendisinden KESIN dusuk itibarli
    kulup orani (n - 1'e bolunur). Esit itibarlilar ayni (alt) dilimi paylasir; tek kulup 0.0.
    """
    if not reputations:
        return {}
    values = sorted(reputations.values())
    span = len(values) - 1
    if span == 0:
        return {team_id: 0.0 for team_id in reputations}
    lower: dict[int, int] = {}
    for index, value in enumerate(values):
        lower.setdefault(value, index)             # sirali listede ilk gorulus = kesin dusuk olanlarin sayisi
    return {team_id: round(lower[rep] / span, 6) for team_id, rep in reputations.items()}


def eligible_limit(manager_level: int) -> float:
    level = max(MIN_LEVEL, min(MAX_LEVEL, int(manager_level)))
    return ELIGIBLE_BASE + ELIGIBLE_PER_LEVEL * level


def required_level(rank_percentile: float) -> int:
    """Bu dilimdeki kulubu alabilecek en dusuk menajer seviyesi (1-10)."""
    pct = max(0.0, min(1.0, float(rank_percentile)))
    needed = math.ceil((pct - ELIGIBLE_BASE) / ELIGIBLE_PER_LEVEL - _EPSILON)
    return max(MIN_LEVEL, min(MAX_LEVEL, needed))


def club_eligible(manager_level: int, rank_percentile: float) -> tuple[bool, str]:
    """(uygun mu, neden). Uygunsa neden bos; degilse gereken seviye Turkce."""
    pct = max(0.0, min(1.0, float(rank_percentile)))
    if pct <= eligible_limit(manager_level) + _EPSILON:
        return True, ""
    return False, f"Bu kulübü yönetmek için en az {required_level(pct)}. seviye menajer olmalısın."
