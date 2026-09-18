"""
Eleme maci testleri (8. Asama): uzatma (2x15), seri penaltilar, tarafsiz saha ve
bunlarin match_feed / pitch / web_view katmanlarindaki karsiliklari.
Saf testler: veritabani gerektirmez (sentetik kadrolar).
"""

from __future__ import annotations

import hashlib
import sys
from functools import cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pitch  # noqa: E402
import web_view  # noqa: E402
from match_engine import (  # noqa: E402
    SHOOTOUT_EVENTS,
    EngineConfig,
    EventType,
    KnockoutRule,
    MatchEngine,
    MatchResult,
    format_stats,
)
from match_feed import build_timeline, summarize, team_energy_at  # noqa: E402
from models import Position  # noqa: E402
from tests.test_match_engine import make_player, make_team  # noqa: E402

# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------


def knockout(seed: int, carry: tuple[int, int] = (0, 0), *, extra_time: bool = True, penalties: bool = True,
             home_ovr: int = 80, away_ovr: int = 80, cfg: EngineConfig | None = None,
             neutral: bool = False) -> MatchResult:
    rule = KnockoutRule(home_carry=carry[0], away_carry=carry[1], extra_time=extra_time, penalties=penalties)
    return MatchEngine(make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr), seed=seed, config=cfg,
                       knockout=rule, neutral_venue=neutral).simulate()


@cache
def seeds_where(kind: str, carry: tuple[int, int] = (0, 0), limit: int = 400, want: int = 3) -> tuple[int, ...]:
    """kind: 'penalties' | 'extra_time' | 'normal' (decided_by), carry ile oynanan maclarda."""
    found = []
    for seed in range(limit):
        if knockout(seed, carry).decided_by == kind:
            found.append(seed)
            if len(found) >= want:
                break
    assert found, f"{limit} maçta '{kind}' yok"
    return tuple(found)


@cache
def shootout_match() -> MatchResult:
    return knockout(seeds_where("penalties")[0])


def whistle_score(result: MatchResult) -> tuple[int, int]:
    """90+X dudugundeki skor: EXTRA_TIME_START / SHOOTOUT_START / FULL_TIME'dan ilki."""
    stops = {EventType.EXTRA_TIME_START, EventType.SHOOTOUT_START, EventType.FULL_TIME}
    ev = next(e for e in result.events if e.type in stops)
    return ev.home_score, ev.away_score


def play_events(result: MatchResult):
    """Seri penalti oncesi oyun olaylari (SHOOTOUT_START haric)."""
    cut = next((i for i, e in enumerate(result.events) if e.type in SHOOTOUT_EVENTS), len(result.events))
    return result.events[:cut]


def fingerprint(r: MatchResult) -> str:
    ev = "\n".join(
        f"{e.minute}|{e.added_time}|{e.type.value}|{e.team_id}|{e.player_id}|{e.home_score}|{e.away_score}|"
        f"{e.detail}|{e.description}" for e in r.events)
    pl = "\n".join(
        f"{p.id}|{p.entered_minute}|{p.left_minute}|{p.rating}|{p.energy!r}|{p.energy_log}|{p.goals}|{p.assists}|"
        f"{p.shots}|{p.saves}|{p.yellow_cards}|{p.sent_off}|{p.injured}|{p.substituted}|{p.minutes_played}"
        for t in (r.home, r.away) for p in t.players)
    st = (f"{r.home.stats}|{r.away.stats}|{r.first_half_added}|{r.second_half_added}|{r.total_minutes}|"
          f"{r.man_of_the_match.id if r.man_of_the_match else None}")
    return hashlib.sha256((ev + "#" + pl + "#" + st).encode()).hexdigest()[:16]


# (tohum, ev gucu, deplasman gucu, ev golu, deplasman golu, olay sayisi, parmak izi)
# YENIDEN TEMELLENDIRME (13A "motor dogrulugu"): 8. Asama'dan beri dondurulan degerler, 13A
# kapanisinda EngineConfig'teki on bayragin (set_pieces, match_stats, discipline_v2, role_realism,
# weak_link, flat_superiority, match_form, goal_timing, sub_timing, fatigue_balance) TEK adimda
# acilmasiyla degisti. Kanit: bayraklarin hepsi False iken motor 5.500 macta (2.500 tohum x 2 guc
# senaryosu + 500 eleme maci) eski surumle BIT-BIT ayni kaliyor; yani asagidaki fark yalnizca
# kasitli kalibrasyon degisikligidir. Duran toplar artik her macta acik oldugu icin olay sayilari
# da buyudu (korner / frikik / penalti sutlari).
# YENIDEN TEMELLENDIRME 2 (13B "anlatim"): olay SAYILARI ve METINLER degisti, SONUCLAR degismedi.
# Motor korner / kartsiz faul / ofsayt / kurulus zinciri / ambiyans olaylarini da yaziyor (akista
# cogu gizlenir) ve anlatim commentary.py bankasindan ayri bir RNG ile geliyor. Kanit: 2.200 macta
# (1.000 tohum x 2 senaryo + 200 eleme) skor, tum oyuncu istatistikleri, takim sayaclari, uzatma
# dakikalari ve macin adami 13A ile BIT-BIT ayni; 13B bayraklari kapaliyken tam parmak izi de ayni
# (.claude/phase13/scratch/b13/evidence.py). Skor sutunlari aynen korunuyor.
GOLDEN = [
    (1, 80, 80, 3, 1, 75, 'd1f6ec024032fc6a'),
    (1, 86, 76, 1, 0, 75, '7169651d6ceafaff'),
    (2, 80, 80, 1, 0, 82, '946c3f9b4ffa4242'),
    (2, 86, 76, 1, 0, 82, '532b6a8182cda258'),
    (3, 80, 80, 0, 1, 71, '26f9cda1a6e5654b'),
    (3, 86, 76, 0, 1, 66, '83f2b776b2915188'),
    (4, 80, 80, 3, 0, 80, '78b3862830f99183'),
    (4, 86, 76, 1, 3, 91, '135ec63736bc2845'),
    (5, 80, 80, 0, 1, 89, 'd31a2ce75d782314'),
    (5, 86, 76, 1, 0, 100, '000b4180a71d1fe8'),
    (6, 80, 80, 2, 0, 87, '08b049291249dfe2'),
    (6, 86, 76, 0, 0, 101, '65ebc1a238a7f194'),
    (7, 80, 80, 3, 4, 104, 'afcdb238bf1afabb'),
    (7, 86, 76, 2, 2, 90, '51e6852116d67325'),
    (8, 80, 80, 0, 2, 81, '65b373e6fad9f81b'),
    (8, 86, 76, 2, 0, 80, '4de961675ed68e1b'),
    (9, 80, 80, 1, 0, 80, '675cff3e6f984256'),
    (9, 86, 76, 3, 1, 90, 'ca5520aee09f60f4'),
    (10, 80, 80, 1, 3, 101, '208549719b2eed72'),
    (10, 86, 76, 2, 0, 80, '44444ac425604d77'),
    (11, 80, 80, 2, 4, 85, '473c146131674c55'),
    (11, 86, 76, 3, 1, 95, '428d7bcda54a38fb'),
    (12, 80, 80, 3, 1, 84, 'a431b8389bfc5768'),
    (12, 86, 76, 2, 1, 78, 'f7f2e5a8e489a772'),
    (13, 80, 80, 0, 1, 86, '18d1370bf23d0ff1'),
    (13, 86, 76, 2, 1, 96, '3c9ddcf86ee202a7'),
    (14, 80, 80, 1, 2, 84, '913b84c40ddf156b'),
    (14, 86, 76, 2, 0, 78, '74953e39471f5b1e'),
    (15, 80, 80, 2, 0, 74, '2b7d300da61e679e'),
    (15, 86, 76, 1, 1, 77, '767063b0f1b4c9f6'),
]


# ---------------------------------------------------------------------------
# Regresyon: lig maclari bit-bit ayni
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed,home_ovr,away_ovr,home_goals,away_goals,n_events,digest", GOLDEN)
def test_golden_regression_for_non_knockout_matches(seed, home_ovr, away_ovr, home_goals, away_goals,
                                                    n_events, digest):
    for kwargs in ({}, {"knockout": None, "neutral_venue": False}):
        r = MatchEngine(make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr), seed=seed, **kwargs).simulate()
        assert (r.home_score, r.away_score, len(r.events)) == (home_goals, away_goals, n_events)
        assert fingerprint(r) == digest
        assert not r.extra_time and r.shootout is None and r.knockout is None
        assert r.decided_by == "normal" and r.advancing is None
        assert r.home_penalties is None and r.away_penalties is None


def test_level_carry_knockout_plays_identical_regular_time():
    """carry 0-0: 90 dakika lig maciyla ayni rastgele cekis sirasi -> ayni olaylar (bitis dudugu haric)."""
    for seed in range(12):
        league = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed).simulate()
        cup = knockout(seed)
        n = len(league.events) - 1
        assert [e.description for e in cup.events[:n]] == [e.description for e in league.events[:n]]
        if not cup.extra_time and cup.shootout is None:
            assert (cup.home_score, cup.away_score) == (league.home_score, league.away_score)
            assert "tur atlıyor" in cup.events[-1].description


def test_no_extra_time_in_non_knockout_draws():
    draws = 0
    for seed in range(40):
        r = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed).simulate()
        if not r.is_draw:
            continue
        draws += 1
        assert r.events[-1].type is EventType.FULL_TIME and r.events[-1].minute == 90
        assert not any(e.type in SHOOTOUT_EVENTS or e.type in {EventType.EXTRA_TIME_START,
                                                                EventType.EXTRA_TIME_HALF} for e in r.events)
        assert r.total_minutes == 90 + r.first_half_added + r.second_half_added
        assert all(e.minute <= 90 for e in r.events)
    assert draws > 3


# ---------------------------------------------------------------------------
# Uzatmaya gidis kurali (toplam skor)
# ---------------------------------------------------------------------------

def test_extra_time_iff_level_on_aggregate_at_ninety():
    carry = (2, 0)
    level = not_level = 0
    for seed in range(160):
        r = knockout(seed, carry)
        h, a = whistle_score(r)
        is_level = h + carry[0] == a + carry[1]
        assert r.extra_time == is_level, (seed, h, a)
        if is_level:
            level += 1
            start = next(e for e in r.events if e.type is EventType.EXTRA_TIME_START)
            assert start.minute == 90 and start.added_time == r.second_half_added
            assert f"Normal süre {h}-{a} bitti (toplam {h + 2}-{a})" in start.description
            assert any(e.type is EventType.EXTRA_TIME_HALF and e.minute == 105 for e in r.events)
        else:
            not_level += 1
            assert r.decided_by == "normal" and r.shootout is None
            assert r.events[-1].minute == 90
            assert r.advancing is (r.home if h + 2 > a else r.away)
    assert level >= 3 and not_level >= 100


def test_carry_two_nil_then_nil_one_does_not_go_to_extra_time():
    """Ilk mac 2-0, rovans 0-1 (ya da 1-2): toplam 2-1 / 3-2 -> uzatma yok, ev sahibi tur atlar."""
    found = 0
    for seed in range(300):
        r = knockout(seed, (2, 0))
        h, a = whistle_score(r)
        if a - h != 1:
            continue
        assert (r.home_score, r.away_score) == (h, a)
        found += 1
        assert not r.extra_time and r.shootout is None and r.decided_by == "normal"
        assert r.advancing is r.home
        assert r.total_minutes == 90 + r.first_half_added + r.second_half_added
        assert "toplam" in r.events[-1].description and "Ev tur atlıyor" in r.events[-1].description
        if found >= 3:
            break
    assert found


def test_carry_two_nil_then_nil_two_goes_to_extra_time():
    for seed in seeds_where("extra_time", (2, 0), want=2) + seeds_where("penalties", (2, 0), want=2):
        r = knockout(seed, (2, 0))
        h, a = whistle_score(r)
        assert a - h == 2 and r.extra_time
        assert r.advancing is not None
        if r.shootout is None:
            assert r.home_aggregate != r.away_aggregate
            assert r.advancing is (r.home if r.home_aggregate > r.away_aggregate else r.away)


def test_straight_to_penalties_when_extra_time_disabled():
    checked = 0
    for seed in range(120):
        r = knockout(seed, extra_time=False)
        h, a = whistle_score(r)
        if h != a:
            assert r.shootout is None and r.decided_by == "normal"
            continue
        checked += 1
        assert not r.extra_time and r.shootout is not None and r.decided_by == "penalties"
        assert not any(e.type in {EventType.EXTRA_TIME_START, EventType.EXTRA_TIME_HALF} for e in r.events)
        start = next(e for e in r.events if e.type is EventType.SHOOTOUT_START)
        assert (start.minute, start.added_time) == (90, 0) and "Normal sürede" in start.description
        kicks = [e for e in r.events if e.type is EventType.PENALTY_SHOOTOUT]
        assert kicks and all((e.minute, e.added_time) == (90, 0) for e in kicks)
        assert r.total_minutes == 90 + r.first_half_added + r.second_half_added
        assert all(p.left_minute <= 90 for t in (r.home, r.away) for p in t.players if p.played)
        if checked >= 3:
            break
    assert checked


def test_extra_time_without_penalties_can_end_level():
    for seed in range(400):
        r = knockout(seed, penalties=False)
        if r.extra_time and r.is_draw:
            assert r.shootout is None and r.advancing is None and r.decided_by == "extra_time"
            assert "eşitlik bozulmadı" in r.events[-1].description
            return
    raise AssertionError("uzatmada berabere biten maç bulunamadı")


def test_no_decider_when_both_disabled():
    for seed in range(60):
        r = knockout(seed, extra_time=False, penalties=False)
        assert not r.extra_time and r.shootout is None
        if r.is_draw:
            assert r.advancing is None and r.events[-1].minute == 90
            return
    raise AssertionError("beraberlik bulunamadı")


# ---------------------------------------------------------------------------
# Dakikalar, enerji, olay sirasi
# ---------------------------------------------------------------------------

def test_minutes_up_to_120_and_minutes_played_consistent():
    for seed in seeds_where("extra_time", want=2) + seeds_where("penalties", want=2):
        r = knockout(seed)
        assert r.extra_time and r.end_minute == 120
        assert 0 <= r.extra_time_first_added <= 3 and 0 <= r.extra_time_second_added <= 4
        assert r.total_minutes == (120 + r.first_half_added + r.second_half_added
                                   + r.extra_time_first_added + r.extra_time_second_added)
        assert r.home.stats.possession_minutes + r.away.stats.possession_minutes == r.total_minutes
        assert all(e.minute <= 120 for e in r.events)
        assert max(e.added_time for e in r.events if e.minute == 105) == r.extra_time_first_added
        for team in (r.home, r.away):
            for p in team.players:
                if not p.played:
                    assert p.minutes_played == 0
                    continue
                assert p.left_minute is not None and p.left_minute <= 120
                assert p.minutes_played == p.left_minute - p.entered_minute
                finished = not (p.sent_off or p.injured or p.substituted)
                if finished:
                    assert p.left_minute == 120
                    assert p.energy_log[-1] == (120, round(p.energy))
                if finished and p.entered_minute == 0:
                    assert p.minutes_played == 120
                    minutes = [m for m, _ in p.energy_log]
                    assert minutes == list(range(0, 121, 5))           # ornekler uzatmada da surer
                assert 1.0 <= p.rating <= 10.0


def test_extra_time_break_recovers_energy_and_structure():
    """Uzatma akisi (dakika oynatmasi devre disi): mola toparlanmasi, olaylar, dakikalar."""
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, knockout=KnockoutRule())
    played: list[tuple[int, int]] = []
    eng._play_minute = lambda minute, added: played.append((minute, added))
    eng.minute, eng.added, eng.second_half_added = 90, 4, 4
    for p in eng.home.on_pitch + eng.away.on_pitch:
        p.energy = 50.0
    eng.away.on_pitch[0].energy = 99.0
    eng._play_extra_time()
    assert all(p.energy == 53.0 for p in eng.home.on_pitch)
    assert eng.away.on_pitch[0].energy == 100.0                           # tavan
    assert eng.in_extra_time and eng.extra_time_played and eng.end_minute == 120
    assert [m for m, a in played if a == 0] == list(range(91, 121))
    assert sum(1 for m, a in played if a and m == 105) == eng.extra_time_first_added
    assert sum(1 for m, a in played if a and m == 120) == eng.extra_time_second_added
    assert (eng.minute, eng.added) == (120, eng.extra_time_second_added)
    start, half = eng.events[-2:]
    assert (start.type, start.minute, start.added_time) == (EventType.EXTRA_TIME_START, 90, 4)
    assert (half.type, half.minute, half.added_time) == (EventType.EXTRA_TIME_HALF, 105, eng.extra_time_first_added)


def test_fatigue_keeps_applying_in_extra_time():
    for seed in seeds_where("penalties", want=2):
        r = knockout(seed)
        finishers = [p for p in r.home.players + r.away.players if p.entered_minute == 0 and p.left_minute == 120
                     and p.role is not Position.GK]
        assert finishers
        for p in finishers:
            log = dict(p.energy_log)
            assert log[120] < log[95] <= log[90] + EngineConfig().extra_time_break_recovery
        ninety = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed).simulate()
        avg = sum(p.energy for p in finishers) / len(finishers)
        avg90 = sum(p.energy for p in ninety.home.players + ninety.away.players
                    if p.entered_minute == 0 and p.left_minute == 90 and p.role is not Position.GK)
        avg90 /= sum(1 for p in ninety.home.players + ninety.away.players
                     if p.entered_minute == 0 and p.left_minute == 90 and p.role is not Position.GK)
        assert avg < avg90 - 10                                            # 120 dk daha yorucu


def test_rating_uses_real_end_minute():
    def rating(extra_time_played: bool) -> float:
        eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, knockout=KnockoutRule())
        eng.extra_time_played = extra_time_played
        p = next(x for x in eng.home.on_pitch if x.role is Position.MID)
        p.goals, p.entered_minute, p.left_minute, p.energy = 1, 95, None, 80.0
        eng._compute_ratings()
        return p.rating

    assert rating(True) == pytest.approx(7.0)      # 95-120: 25 dk -> tam not
    assert rating(False) == pytest.approx(6.5)     # 90'da bitseydi < 20 dk -> sonumlenmis


def test_events_chronological_with_shootout_last():
    checked = 0
    for seed in range(0, 200, 3):
        r = knockout(seed, (1, 0))
        play = play_events(r)
        keys = [(e.minute, e.added_time) for e in play if e.type is not EventType.FULL_TIME or not r.shootout]
        assert keys == sorted(keys), seed
        assert r.events[0].type is EventType.KICK_OFF and r.events[-1].type is EventType.FULL_TIME
        if r.shootout is None:
            continue
        checked += 1
        tail = r.events[len(play):]
        assert tail[0].type is EventType.SHOOTOUT_START
        assert [e.type for e in tail[1:-1]] == [EventType.PENALTY_SHOOTOUT] * len(r.shootout.kicks)
        assert all((e.minute, e.added_time) == (r.end_minute, 0) for e in tail)
        assert tail[-1].type is EventType.FULL_TIME
    assert checked


def test_penalty_events_match_shootout_result_and_never_count_as_goals():
    for seed in seeds_where("penalties", want=4):
        r = knockout(seed)
        so = r.shootout
        kicks = [e for e in r.events if e.type is EventType.PENALTY_SHOOTOUT]
        assert len(kicks) == len(so.kicks)
        for ev, kick in zip(kicks, so.kicks, strict=True):
            assert ev.kick_number == kick.number
            assert ev.detail == kick.outcome
            assert (ev.home_penalties, ev.away_penalties) == (kick.home_score, kick.away_score)
            assert ev.player_id == kick.player_id and ev.player == kick.player_name
            team = r.home if kick.side == "home" else r.away
            assert (ev.team_id, ev.team) == (team.id, team.name)
            assert (ev.home_score, ev.away_score) == (r.home_score, r.away_score)
        full = r.events[-1]
        assert f"(pen. {so.home_score}-{so.away_score})" in full.description
        assert (full.home_penalties, full.away_penalties) == (so.home_score, so.away_score)
        assert (r.home_penalties, r.away_penalties) == (so.home_score, so.away_score)
        # seri golleri mac istatistiklerine girmez
        goal_events = [e for e in r.events if e.type is EventType.GOAL]
        assert r.home_score + r.away_score == len(goal_events)
        for team in (r.home, r.away):
            assert team.stats.goals == sum(p.goals for p in team.players)
            assert team.stats.shots == sum(p.shots for p in team.players)
            assert team.stats.goals == sum(1 for e in goal_events if e.team_id == team.id)
        assert r.is_draw and r.winner is None
        assert r.decided_by == "penalties"
        assert r.advancing is (r.home if so.winner_side == "home" else r.away)
        report = format_stats(r)
        assert "Penaltılar:" in report and "Uzatma devreleri" in report and "tur atladı" in report


def test_advancing_and_decided_by_are_consistent():
    for seed in range(0, 240, 2):
        carry = [(0, 0), (1, 0), (0, 2), (3, 3)][seed % 4]
        r = knockout(seed, carry)
        assert r.home_aggregate == r.home_score + carry[0]
        assert r.away_aggregate == r.away_score + carry[1]
        if r.shootout is not None:
            assert r.decided_by == "penalties" and r.home_aggregate == r.away_aggregate
            assert r.advancing is (r.home if r.shootout.winner_side == "home" else r.away)
        elif r.extra_time:
            assert r.decided_by == "extra_time" and r.home_aggregate != r.away_aggregate
        else:
            assert r.decided_by == "normal" and r.home_aggregate != r.away_aggregate
        if r.home_aggregate != r.away_aggregate:
            assert r.advancing is (r.home if r.home_aggregate > r.away_aggregate else r.away)
        assert r.advancing is not None


# ---------------------------------------------------------------------------
# Seri penalti kadrosu: yalnizca sahadakiler
# ---------------------------------------------------------------------------

def test_sent_off_and_substituted_players_never_kick():
    cfg = EngineConfig(base_card=0.10, straight_red_share=0.25)
    checked = with_reds = 0
    for seed in range(300):
        r = knockout(seed, cfg=cfg)
        if r.shootout is None:
            continue
        checked += 1
        finishers = {p.id: p for t in (r.home, r.away) for p in t.players
                     if p.played and not (p.sent_off or p.injured or p.substituted)}
        for kick in r.shootout.kicks:
            assert kick.player_id in finishers, (seed, kick)
            team = r.home if kick.side == "home" else r.away
            assert kick.player_id in {p.id for p in team.players}
        counts = {side: len({k.player_id for k in r.shootout.side_kicks(side)}) for side in ("home", "away")}
        on_pitch = {"home": sum(1 for p in r.home.players if p.id in finishers),
                    "away": sum(1 for p in r.away.players if p.id in finishers)}
        assert max(counts.values()) <= min(on_pitch.values())            # esitlemek icin azaltma
        if r.home.stats.red_cards + r.away.stats.red_cards:
            with_reds += 1
    assert checked >= 10 and with_reds >= 5


def test_shootout_side_uses_emergency_keeper_and_excludes_sent_off():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, knockout=KnockoutRule())
    eng.minute = 118
    eng.home.subs_used = eng.max_subs
    gk = eng.home.keeper
    eng._send_off(eng.home, gk, second_yellow=False)
    emergency = eng.home.keeper
    assert emergency is not None and emergency.position is not Position.GK
    side = eng._shootout_side(eng.home)
    assert side.keeper_id == emergency.id
    assert gk.id not in {t.id for t in side.takers} and len(side.takers) == 10
    assert side.keeper_skill < eng._shootout_side(eng.away).keeper_skill
    tired = next(p for p in eng.home.on_pitch if p.role is Position.FWD)
    fresh_skill = eng._penalty_taker_skill(tired)
    tired.energy = 10.0
    assert eng._penalty_taker_skill(tired) < fresh_skill


# ---------------------------------------------------------------------------
# Ek degisiklik hakki, tarafsiz saha
# ---------------------------------------------------------------------------

def big_squad(tid: int, name: str):
    team = make_team(tid, name, 80)
    pid = tid * 100 + 50
    for pos in (Position.DEF,) * 3 + (Position.MID,) * 3 + (Position.FWD,):
        pid += 1
        team.players.append(make_player(pid, pos, 78))
    return team


def test_extra_substitution_only_in_extra_time():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, knockout=KnockoutRule())
    eng.minute = 80
    eng.home.subs_used = eng.cfg.max_subs
    out = next(p for p in eng.home.on_pitch if p.role is Position.FWD)
    out.injured = True
    eng.home.remove_player(out, 80)
    eng._substitute_for(eng.home, out)
    assert eng.home.player_count == 10 and "hakkı kalmadı" in eng.events[-1].description

    eng.in_extra_time = True
    eng.minute = 100
    assert eng.max_subs == eng.cfg.max_subs + eng.cfg.extra_time_extra_subs
    out2 = next(p for p in eng.home.on_pitch if p.role is Position.FWD)
    out2.injured = True
    eng.home.remove_player(out2, 100)
    eng._substitute_for(eng.home, out2)
    assert eng.home.player_count == 10 and eng.home.subs_used == eng.cfg.max_subs + 1
    assert eng.events[-1].type is EventType.SUBSTITUTION and eng.events[-1].player_id is not None

    # Tum maclarda: uzatmasiz macta en fazla max_subs, uzatmalida en fazla +1
    cfg = EngineConfig(tired_threshold=99)
    fifth_tactical_in_et = False
    for seed in range(60):
        r = MatchEngine(big_squad(1, "Ev"), big_squad(2, "Dep"), seed=seed, config=cfg,
                        knockout=KnockoutRule()).simulate()
        for team in (r.home, r.away):
            subs = [e for e in r.events if e.type is EventType.SUBSTITUTION and e.team_id == team.id
                    and e.description.startswith("Değişiklik (")]       # acil kaleci hak harcamaz
            assert team.subs_used == len(subs)
            if not r.extra_time:
                assert team.subs_used <= cfg.max_subs
                continue
            assert team.subs_used <= cfg.max_subs + cfg.extra_time_extra_subs
            assert sum(1 for e in subs if e.minute <= 90) <= cfg.max_subs
            if len(subs) >= cfg.max_subs and subs[cfg.max_subs - 1].minute > 90 and "yoruldu" in \
                    subs[cfg.max_subs - 1].description:
                fifth_tactical_in_et = True
    assert fifth_tactical_in_et


def test_neutral_venue_removes_home_advantage():
    assert MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, neutral_venue=True).home_advantage == 1.0
    assert MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0).home_advantage > 1.05
    n = 120
    normal = neutral = 0.0
    for seed in range(n):
        a = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed).simulate()
        b = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed, neutral_venue=True).simulate()
        normal += a.home.stats.possession_minutes / a.total_minutes
        neutral += b.home.stats.possession_minutes / b.total_minutes
        assert b.neutral_venue and not a.neutral_venue
    normal, neutral = normal / n, neutral / n
    assert normal > 0.52
    assert 0.47 <= neutral <= 0.53
    assert neutral < normal - 0.03


# ---------------------------------------------------------------------------
# Gorunum katmanlari: match_feed, pitch, web_view
# ---------------------------------------------------------------------------

def test_timeline_for_match_decided_on_penalties():
    r = shootout_match()
    frames = build_timeline(r)
    # 13B / K7: varsayilan akis gorunur alt kumedir; gizliler dahil tam akis olay basina bir kare
    assert len(build_timeline(r, include_hidden=True)) == len(r.events)
    phases = [f.phase for f in frames]
    for phase in ("1. Yarı", "Devre Arası", "2. Yarı", "Normal Süre Bitti", "1. uzatma",
                  "Uzatma Arası", "2. uzatma", "Penaltılar"):
        assert phase in phases, phase
    assert phases[-1] == "Maç Sonu"
    for prev, cur in zip(frames, frames[1:], strict=False):
        assert cur.elapsed >= prev.elapsed
        assert cur.home.goals >= prev.home.goals
    assert frames[-1].elapsed == r.total_minutes
    et_half = next(f for f in frames if f.event.type == "EXTRA_TIME_HALF")
    assert et_half.elapsed == 105 + r.first_half_added + r.second_half_added + r.extra_time_first_added
    last = frames[-1]
    assert (last.home.goals, last.away.goals) == (r.home_score, r.away_score)       # penaltilar gol degil
    assert (last.home.penalties, last.away.penalties) == (r.home_penalties, r.away_penalties)
    assert (last.home_penalties, last.away_penalties) == (r.home_penalties, r.away_penalties)
    assert last.extra_time
    kicks = [f for f in frames if f.event.type == "PENALTY_SHOOTOUT"]
    for f in kicks:
        assert f.event.highlight == ("pen_goal" if f.event.detail == "scored" else "pen_miss")
        assert f.event.label.startswith("PENALTI") and f.event.kick_number is not None
        assert f.phase == "Penaltılar" and f.pacing > 1
        assert f.elapsed == r.total_minutes
    assert all(f.home_penalties is None for f in frames if f.phase not in {"Penaltılar", "Maç Sonu"})
    assert not any(f.extra_time for f in frames if f.minute < 90)

    summary = summarize(r, frames)
    assert summary.extra_time and summary.decided_by == "penalties" and summary.knockout
    assert (summary.home_penalties, summary.away_penalties) == (r.home_penalties, r.away_penalties)
    assert summary.advancing == r.advancing.name
    assert summary.total_minutes == r.total_minutes
    lines = web_view.summary_lines(summary)
    assert f"(pen. {r.home_penalties}-{r.away_penalties})" in lines[0] and "(uzt.)" in lines[0]
    assert any("tur atladı" in line and "penaltılarla" in line for line in lines)

    for minute in (95, 110, 120):
        assert team_energy_at(r.home, minute) is not None


def test_scoreboard_and_feed_html_for_penalties():
    r = shootout_match()
    frames = build_timeline(r)
    last = frames[-1]
    board = web_view.scoreboard_html("A", "B", last)
    assert "uzt." in board and f"(pen. {r.home_penalties}-{r.away_penalties})" in board
    assert f"{r.home_score} - {r.away_score}" in board
    before_et = next(f for f in frames if f.event.type == "HALF_TIME")
    assert "uzt." not in web_view.scoreboard_html("A", "B", before_et)
    assert "pen." not in web_view.scoreboard_html("A", "B", before_et)
    from_summary = web_view.scoreboard_html("A", "B", None, summary=summarize(r, frames))
    assert "uzt." in from_summary and "(pen." in from_summary
    assert "uzt." not in web_view.scoreboard_html("A", "B", None)

    kick = next(f for f in frames if f.event.type == "PENALTY_SHOOTOUT")
    html = web_view.event_html(kick)
    assert f"cm-ev {kick.event.highlight}" in html and web_view.KICK_ICONS[kick.event.detail] in html
    assert web_view.EVENT_ICONS["SHOOTOUT_START"] in web_view.feed_html(frames)
    assert ".cm-ev.pen_goal" in web_view.CSS and ".cm-extra" in web_view.CSS
    evil = '<script>alert("x")</script>'
    kick.event.description = evil
    assert "<script>" not in web_view.event_html(kick)
    assert "<script>" not in web_view.scoreboard_html(evil, evil, last)
    assert web_view.banner_html(kick) == ""


def test_pitch_scenes_for_extra_time_and_shootout():
    r = shootout_match()
    frames = build_timeline(r)
    scenes = pitch.build_scenes(r, frames)
    assert len(scenes) == len(frames)
    for i, (frame, scene) in enumerate(zip(frames, scenes, strict=True)):
        expected = {"1. Yarı": True, "Devre Arası": True, "2. Yarı": False, "Normal Süre Bitti": False,
                    "1. uzatma": True, "Uzatma Arası": True, "2. uzatma": False, "Penaltılar": True,
                    "Maç Sonu": False}[frame.phase]
        assert scene.home_attacks_right is expected, (frame.phase, frame.display_minute)
        for side in pitch.SIDES:
            assert 7 <= len(scene.side_dots(side)) <= 11
            assert sum(1 for d in scene.side_dots(side) if d.is_keeper) == 1
        points = [(d.x, d.y) for d in scene.dots]
        for j, a in enumerate(points):
            assert 0 < a[0] < pitch.PITCH_LENGTH and 0 < a[1] < pitch.PITCH_WIDTH
            for b in points[j + 1:]:
                assert (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 >= (2 * pitch.DOT_RADIUS) ** 2, (i, frame.phase)
        svg = pitch.scene_svg(scene, scenes[i - 1] if i else None)
        assert svg.startswith('<div class="cm-p-wrap"') and "\n" not in svg

    et_start = next(s for f, s in zip(frames, scenes, strict=True) if f.event.type == "EXTRA_TIME_START")
    assert not et_start.arrows and all(d.x > pitch.CENTER_X for d in et_start.side_dots("home"))
    et_half = next(s for f, s in zip(frames, scenes, strict=True) if f.event.type == "EXTRA_TIME_HALF")
    assert all(d.x < pitch.CENTER_X for d in et_half.side_dots("home"))

    start = next(s for f, s in zip(frames, scenes, strict=True) if f.event.type == "SHOOTOUT_START")
    assert not start.arrows and start.ball == pitch.SHOOTOUT_SPOT and start.shootout
    start_svg = pitch.scene_svg(start)
    assert "(pen. 0-0)" in start_svg
    # seri tek kalede: iki takimin yon oku da o kaleyi gosterir
    assert 'cm-p-dir-home" data-dir="right"' in start_svg and 'cm-p-dir-away" data-dir="right"' in start_svg
    assert not any(s.shootout for f, s in zip(frames, scenes, strict=True) if f.phase != "Penaltılar")

    kinds = set()
    for frame, scene in zip(frames, scenes, strict=True):
        if frame.event.type != "PENALTY_SHOOTOUT":
            continue
        side = frame.event.side
        other = "away" if side == "home" else "home"
        taker = [d for d in scene.dots if d.highlight]
        assert len(taker) == 1 and taker[0].side == side and taker[0].name == frame.event.player
        assert (taker[0].x, taker[0].y) == pitch.SHOOTOUT_SPOT
        keeper = scene.keeper(other)
        assert keeper.x > pitch.PITCH_LENGTH - 2 and pitch.GOAL_TOP - 0.6 < keeper.y < pitch.GOAL_BOTTOM + 0.6
        own_keeper = scene.keeper(side)
        assert own_keeper.x < pitch.PITCH_LENGTH - pitch.PENALTY_DEPTH + 0.5
        shot = scene.shot
        assert shot is not None and not scene.passes and scene.attacking_side == side
        assert (shot.x1, shot.y1) == pitch.SHOOTOUT_SPOT and scene.ball == (shot.x2, shot.y2)
        kinds.add(shot.kind)
        assert shot.kind == pitch.KICK_KIND[frame.event.detail]
        if shot.kind == "goal":
            assert shot.x2 >= pitch.PITCH_LENGTH and pitch.GOAL_TOP < shot.y2 < pitch.GOAL_BOTTOM
            assert 'class="cm-p-flash"' in pitch.scene_svg(scene)
        elif shot.kind == "save":
            assert (shot.x2, shot.y2) == (keeper.x, keeper.y)
        else:
            assert shot.x2 > pitch.PITCH_LENGTH and abs(shot.y2 - pitch.CENTER_Y) > pitch.GOAL_HALF_WIDTH
        others = [d for d in scene.dots if d is not taker[0] and not d.is_keeper]
        assert all(abs(d.x - pitch.CENTER_X) < 20 and abs(d.y - pitch.CENTER_Y) < 4 for d in others)
        assert "pen." in scene.caption and f"(pen. {frame.home_penalties}-{frame.away_penalties})" in \
            pitch.scene_svg(scene)
    assert "goal" in kinds and kinds & {"save", "miss"}
    # determinizm
    assert [pitch.scene_svg(s) for s in scenes] == [pitch.scene_svg(s) for s in pitch.build_scenes(r)]


def test_every_new_event_type_renders_everywhere():
    from match_engine import EVENT_ICONS, format_event
    from match_feed import HIGHLIGHT, LABELS

    for etype in EventType:
        assert etype in EVENT_ICONS and etype in HIGHLIGHT and etype in LABELS
    r = shootout_match()
    for ev in r.events:
        assert format_event(ev)


# ---------------------------------------------------------------------------
# DB adaptoru (PostgreSQL, degisiklikler geri alinir)
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    import os

    if os.getenv("CM_TEST_NO_DB"):
        return False
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@integration
@pytest.mark.integration
def test_play_fixture_knockout_skips_table_and_uses_custom_unavailability():
    from sqlalchemy import select

    from database import SessionLocal
    from match_engine import build_match_team, play_fixture
    from models import Fixture, FixtureStatus

    with SessionLocal() as db:
        fx = db.scalar(select(Fixture).where(Fixture.status == FixtureStatus.UNPLAYED).order_by(Fixture.id))
        assert fx is not None, "oynanmamış fikstür yok"
        home, away = fx.home_team, fx.away_team
        before = (home.played, home.points, home.goals_for, away.played, away.points, away.goals_for)
        banned = max(home.players, key=lambda p: p.overall_rating)

        def cup_only(player) -> str | None:
            return "Kupa cezası" if player.id == banned.id else None

        team = build_match_team(home, True, current_week=1, unavailability=cup_only)
        assert [(p.id, reason) for p, reason in team.unavailable] == [(banned.id, "Kupa cezası")]
        assert banned.id not in {p.id for p in team.players}

        rule = KnockoutRule(home_carry=1, away_carry=1)
        r = play_fixture(db, fx.id, seed=5, persist=True, knockout=rule, neutral_venue=True,
                         update_table=False, unavailability=cup_only)
        db.flush()
        assert fx.status is FixtureStatus.PLAYED
        assert (fx.home_score, fx.away_score) == (r.home_score, r.away_score)
        assert (home.played, home.points, home.goals_for, away.played, away.points, away.goals_for) == before
        assert r.knockout == rule and r.neutral_venue and r.advancing is not None
        assert banned.id not in {p.id for p in r.home.players}
        db.rollback()
