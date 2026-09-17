"""
Faz 12 / 14. Asama A3: tur kurallari (turn_rules.py) -- SAF testler (veritabani gerekmez).

Kapsam: tur bitisi (UTC), decide_advance dogruluk tablosu, kacirilan tur, kulup itibar yuzdelikleri ve menajer
seviyesine gore kulup uygunlugu.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from turn_rules import (  # noqa: E402
    AdvanceDecision,
    AdvanceTrigger,
    as_utc,
    club_eligible,
    club_rank_percentiles,
    deadline_for,
    decide_advance,
    eligible_limit,
    missed_deadline,
    required_level,
)
from world_rules import WorldRules  # noqa: E402

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
SHARED = WorldRules.shared_defaults()
READY, FORCED, DEADLINE = AdvanceTrigger.READY, AdvanceTrigger.FORCED, AdvanceTrigger.DEADLINE


# ===========================================================================
# deadline_for / as_utc
# ===========================================================================

def test_deadline_for_adds_hours_in_utc_and_treats_naive_as_utc():
    assert deadline_for(NOW, 24) == NOW + timedelta(hours=24)
    istanbul = timezone(timedelta(hours=3))
    local = datetime(2026, 9, 17, 15, 0, tzinfo=istanbul)
    result = deadline_for(local, 1)
    assert result == NOW + timedelta(hours=1) and result.tzinfo == timezone.utc
    naive = deadline_for(datetime(2026, 9, 17, 12, 0), 2)
    assert naive == NOW + timedelta(hours=2) and naive.tzinfo is not None
    assert as_utc(None) is None


@pytest.mark.parametrize("hours", [0, -3, True, 1.5, "24", None])
def test_deadline_for_rejects_invalid_hours(hours):
    with pytest.raises(ValueError):
        deadline_for(NOW, hours)


# ===========================================================================
# decide_advance
# ===========================================================================

def _decide(*, active=(1, 2), ready=(), deadline=None, role=None, requested=READY, rules=SHARED, now=NOW):
    return decide_advance(now=now, deadline_at=deadline, active_seat_ids=active, ready_seat_ids=ready,
                          actor_role=role, requested=requested, rules=rules)


PAST = NOW - timedelta(minutes=1)
FUTURE = NOW + timedelta(hours=3)
MANUAL = WorldRules(shared=True, max_seats=4, auto_advance=False)          # sure dolunca otomatik ilerleme yok
NO_READY = WorldRules(shared=True, max_seats=4, ready_check=False, auto_advance=True)


@pytest.mark.parametrize(
    ("kwargs", "allowed", "trigger", "reason"),
    [
        # READY: herkes hazir
        (dict(ready=(1, 2)), True, READY, "Tüm menajerler hazır; hafta oynanıyor."),
        (dict(ready=(1,)), False, None, "1 menajer daha hazır değil."),
        (dict(ready=()), False, None, "2 menajer daha hazır değil."),
        (dict(active=(), ready=()), False, None, "Kulübü olan aktif menajer yok."),
        (dict(ready=(1, 2), rules=NO_READY), False, None,
         "Bu dünyada hazır kontrolü kapalı; hafta süre dolunca ilerler."),
        (dict(ready=(1, 2, 99)), True, READY, None),                         # aktif olmayan hazir yok sayilir
        (dict(ready=(1, 99)), False, None, "1 menajer daha hazır değil."),
        # FORCED: yalnizca sahip / yonetici
        (dict(requested=FORCED, role="OWNER"), True, FORCED, "Hafta dünya yöneticisi tarafından ilerletildi."),
        (dict(requested=FORCED, role="admin"), True, FORCED, None),
        (dict(requested=FORCED, role="MEMBER"), False, None,
         "Haftayı yalnızca dünyanın sahibi ya da yöneticisi zorla ilerletebilir."),
        (dict(requested=FORCED, role=None), False, None, None),
        (dict(requested=FORCED, role="OWNER", active=()), True, FORCED, None),
        (dict(requested=FORCED, role="MEMBER", ready=(1, 2)), True, READY, None),   # herkes hazirsa READY sayilir
        (dict(requested=FORCED, role="OWNER", ready=(1, 2)), True, READY, None),
        (dict(requested=FORCED, role="MEMBER", deadline=PAST), True, DEADLINE, None),
        # DEADLINE: sure doldu ve otomatik ilerleme acik
        (dict(requested=DEADLINE, deadline=PAST), True, DEADLINE,
         "Hafta süresi doldu; hazır olmayan menajerler beklenmeden hafta oynanıyor."),
        (dict(requested=DEADLINE, deadline=NOW), True, DEADLINE, None),                # sinir dahil
        (dict(requested=DEADLINE, deadline=FUTURE), False, None, "Hafta süresi henüz dolmadı."),
        (dict(requested=DEADLINE, deadline=None), False, None, "Bu hafta için süre henüz başlatılmadı."),
        (dict(requested=DEADLINE, deadline=PAST, rules=MANUAL), False, None,
         "Bu dünyada süre dolunca otomatik ilerleme kapalı."),
        (dict(requested=DEADLINE, deadline=FUTURE, ready=(1, 2)), True, READY, None),   # sayfa acilisi: herkes hazir
        (dict(requested=DEADLINE, active=(), deadline=PAST), True, DEADLINE, None),
        # READY istegi ama sure dolmus: DEADLINE ile ilerler
        (dict(requested=READY, ready=(1,), deadline=PAST), True, DEADLINE, None),
        (dict(requested=READY, ready=(1,), deadline=PAST, rules=MANUAL), False, None, "1 menajer daha hazır değil."),
    ],
)
def test_decide_advance_truth_table(kwargs, allowed, trigger, reason):
    decision = _decide(**kwargs)
    assert isinstance(decision, AdvanceDecision)
    assert (decision.allowed, decision.trigger) == (allowed, trigger)
    if reason is not None:
        assert decision.reason == reason
    assert decision.reason                                               # her zaman Turkce bir aciklama


def test_decide_advance_accepts_strings_and_naive_times():
    naive_now = datetime(2026, 9, 17, 12, 0)
    decision = decide_advance(now=naive_now, deadline_at=PAST, active_seat_ids=[1], ready_seat_ids=[],
                              actor_role=None, requested="DEADLINE", rules=SHARED)
    assert decision.allowed and decision.trigger is DEADLINE
    with pytest.raises(ValueError):
        _decide(requested="ZORLA")


# ===========================================================================
# missed_deadline
# ===========================================================================

OPENED = NOW - timedelta(hours=24)


@pytest.mark.parametrize(
    ("ready", "last_active", "opened", "trigger", "missed"),
    [
        (False, None, OPENED, DEADLINE, True),
        (False, OPENED - timedelta(minutes=1), OPENED, DEADLINE, True),
        (False, OPENED, OPENED, DEADLINE, False),                             # acilis aninda etkin: kacirmadi
        (False, OPENED + timedelta(hours=2), OPENED, FORCED, False),
        (False, None, OPENED, FORCED, True),
        (True, None, OPENED, DEADLINE, False),                                # hazir olan kacirmaz
        (False, None, OPENED, READY, False),                                  # READY ilerlemede sayilmaz
        (False, None, None, DEADLINE, False),                                 # tur acilisi bilinmiyor
        (False, datetime(2026, 9, 16, 11, 59), OPENED, "DEADLINE", True),     # naive = UTC
    ],
)
def test_missed_deadline(ready, last_active, opened, trigger, missed):
    assert missed_deadline(ready=ready, last_active_at=last_active, turn_opened_at=opened, trigger=trigger) is missed


# ===========================================================================
# Kulup yuzdelikleri ve uygunluk
# ===========================================================================

def test_club_rank_percentiles_rank_from_weakest_and_share_ties():
    assert club_rank_percentiles({}) == {}
    assert club_rank_percentiles({7: 80}) == {7: 0.0}
    pct = club_rank_percentiles({1: 70, 2: 90, 3: 80, 4: 80, 5: 60})
    assert pct == {5: 0.0, 1: 0.25, 3: 0.5, 4: 0.5, 2: 1.0}
    assert club_rank_percentiles({1: 50, 2: 50}) == {1: 0.0, 2: 0.0}


def test_club_eligible_opens_the_ladder_level_by_level():
    assert eligible_limit(1) == pytest.approx(0.37) and eligible_limit(10) == pytest.approx(1.0)
    assert club_eligible(1, 0.0) == (True, "")
    assert club_eligible(1, 0.37) == (True, "")                              # sinir dahil (kayan nokta payi)
    ok, reason = club_eligible(1, 0.38)
    assert not ok and reason == "Bu kulübü yönetmek için en az 2. seviye menajer olmalısın."
    assert club_eligible(10, 1.0) == (True, "")
    assert club_eligible(9, 1.0) == (False, "Bu kulübü yönetmek için en az 10. seviye menajer olmalısın.")
    assert club_eligible(99, 1.0)[0] and not club_eligible(0, 0.5)[0]         # seviye 1-10'a kirpilir
    assert club_eligible(5, 1.7)[0] is False and club_eligible(1, -2)[0] is True
    for level in range(1, 11):
        limit = eligible_limit(level)
        assert club_eligible(level, limit)[0] and required_level(limit) <= level
        if level < 10:
            assert not club_eligible(level, limit + 0.01)[0] and required_level(limit + 0.01) == level + 1


def test_synthetic_world_ladder_starts_new_managers_at_the_smallest_clubs():
    # sentetik dunyanin 24 kulubunden bir kesit (itibar): yeni menajer (seviye 1) alttaki dilimi alir
    reputations = {1: 78, 2: 77, 3: 75, 4: 73, 12: 83, 13: 95, 16: 79, 20: 83, 22: 81, 23: 81, 24: 80}
    pct = club_rank_percentiles(reputations)
    eligible = {team for team, p in pct.items() if club_eligible(1, p)[0]}
    assert eligible == {4, 3, 2, 1}
    assert not club_eligible(1, pct[13])[0] and club_eligible(10, pct[13])[0]
