"""
Canli mac testleri (9. Asama): live_match.LiveMatch, MatchEngine mudahaleleri
(manual_substitution, change_formation, set_instructions) ve instructions.py.

Saf testler: veritabani ve Streamlit gerektirmez (sentetik kadrolar). Kanitlanan sey,
menajer mudahalelerinin motora GERCEKTEN ulastigidir:

    1) Determinizm   adim adim / duraklatarak oynanan mac simulate() ile bit-bit ayni
    2) Degisiklik    hak, pencere (IFAB 5/3), kaleci, uygunluk kurallari ve sonrasi durum
    3) Aninda etki   takim gucu, dizilis carpanlari, mevki disi cezasi, talimat carpanlari
    4) Istatistik    zihniyet sut uretimini/yorgunlugu, sertlik kart/sakatligi degistirir
    5) Asistan       auto_subs=False: yorgunluk degisikligi yok, sakatlikta asistan yine sokar
    6) Kontrolcu     otomatik duraklatma, gorunum satirlari, saat ve ilerleme cubugu

  CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_live_match.py
"""

from __future__ import annotations

import hashlib
import random
import sys
from collections import Counter
from collections.abc import Callable
from functools import cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instructions import (  # noqa: E402
    MENTALITY_EFFECTS,
    MENTALITY_LABELS,
    TACKLING_EFFECTS,
    TACKLING_LABELS,
    Mentality,
    Tackling,
    TeamInstructions,
    parse_mentality,
    parse_tackling,
)
from live_match import (  # noqa: E402
    ASSISTANT_MINUTES,
    AUTO_PAUSE_DEFAULTS,
    BREAK_EVENTS,
    KEY_EVENTS,
    PHASE_LABELS,
    TWO_GOALS_TEXT,
    LiveMatch,
    SubRule,
    engine_config_for,
)
from match_engine import (  # noqa: E402
    BREAK_PHASES,
    FORMATION_STYLE,
    ROLE_WEIGHTS,
    EngineConfig,
    EventType,
    InterventionError,
    KnockoutRule,
    MatchEngine,
    MatchPhase,
    MatchPlayer,
    MatchResult,
    MatchTeam,
)
from models import Position  # noqa: E402

# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------
# make_player / make_team / fingerprint tests/test_match_engine.py ve tests/test_extra_time.py
# ile BIREBIR aynidir. Oradan import edilmez: test_match_engine modul yuklenirken veritabani
# baglantisini yokluyor (CM_TEST_NO_DB'ye bakmadan); bu dosya tamamen saf kalmali.

COMPOSITION = {Position.GK: 2, Position.DEF: 4, Position.MID: 5, Position.FWD: 4}
KINDS = ("attack", "midfield", "defense")
PLAY_PHASES = frozenset({MatchPhase.FIRST_HALF, MatchPhase.SECOND_HALF,
                         MatchPhase.EXTRA_TIME_FIRST_HALF, MatchPhase.EXTRA_TIME_SECOND_HALF})
# Kart ve sakatlik yok: kural testlerinde asistanin sakatlik degisiklikleri pencere/hak harcamasin
QUIET = EngineConfig(base_card=0.0, base_injury=0.0)


def make_player(pid: int, pos: Position, ovr: int, *, form=50, morale=70, age=26) -> MatchPlayer:
    gk = ovr + 6 if pos is Position.GK else 30
    return MatchPlayer(
        id=pid, name=f"P{pid}-{pos.value}", position=pos, age=age, overall=ovr,
        pace=ovr, shooting=ovr + (4 if pos is Position.FWD else -10),
        passing=ovr + (3 if pos is Position.MID else -5),
        defending=ovr + (6 if pos is Position.DEF else -15),
        dribbling=ovr, goalkeeping=gk, form=form, morale=morale,
    )


def make_team(tid: int, name: str, ovr: int, **kw) -> MatchTeam:
    players, pid = [], tid * 100
    for pos, n in COMPOSITION.items():
        for _ in range(n):
            pid += 1
            players.append(make_player(pid, pos, ovr, **kw))
    return MatchTeam(id=tid, name=name, reputation=80, players=players)


def big_team(tid: int, name: str, ovr: int = 80) -> MatchTeam:
    """15 kisilik kadro + 6 yedek (3 DEF, 2 MID, 1 FWD): kulubede 10 oyuncu."""
    team = make_team(tid, name, ovr)
    pid = tid * 100 + 50
    for pos in (Position.DEF, Position.DEF, Position.DEF, Position.MID, Position.MID, Position.FWD):
        pid += 1
        team.players.append(make_player(pid, pos, ovr - 2))
    return team


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


# tests/test_extra_time.py GOLDEN listesinden ornekler (13A kapanisinda, 13B'de -- olay sayisi ve
# metin, skorlar AYNI -- ve YENIDEN TEMELLENDIRME 3 (14B, ozellik modeli varsayilan acik: skorlar
# da degisti) ile yeniden temellendirildi; gerekce ve kanit test_extra_time.py'de):
# (tohum, ev gucu, deplasman gucu, ev golu, deplasman golu, olay sayisi, parmak izi)
GOLDEN_SAMPLE = [
    (1, 80, 80, 3, 1, 75, '5c8599575bcada25'),
    (2, 86, 76, 1, 0, 89, '3b48b46d86bc0f40'),
    (4, 80, 80, 3, 0, 83, '02ad2272739633a4'),
    (6, 80, 80, 2, 0, 90, '2682101c63408591'),
    (7, 86, 76, 2, 2, 90, '5b29e263598753f8'),
    (9, 80, 80, 2, 2, 100, '31fe4f98872af9d7'),
    (12, 86, 76, 1, 0, 79, '334d4a56881f18dc'),
    (14, 80, 80, 2, 4, 87, 'e19edf92c5722969'),
    (15, 86, 76, 1, 0, 63, 'ce1df378958e364a'),
]


def new_engine(seed: int = 1, *, home: MatchTeam | None = None, away: MatchTeam | None = None,
               cfg: EngineConfig | None = None, knockout: KnockoutRule | None = None) -> MatchEngine:
    return MatchEngine(home or make_team(1, "Ev", 80), away or make_team(2, "Dep", 80),
                       seed=seed, config=cfg, knockout=knockout)


def new_live(seed: int = 1, *, rule: SubRule = SubRule.STANDARD, cfg: EngineConfig | None = None,
             home: MatchTeam | None = None, knockout: KnockoutRule | None = None,
             instructions: TeamInstructions | None = None, auto_subs: bool = True,
             breaks: bool = False, key_events: bool = False) -> LiveMatch:
    """Yonetilen takim ev sahibi (#1). Varsayilan: otomatik duraklatma kapali (testler kendisi durdurur)."""
    eng = new_engine(seed, home=home, cfg=engine_config_for(rule, cfg), knockout=knockout)
    return LiveMatch.create(eng, 1, instructions=instructions, auto_subs=auto_subs, sub_rule=rule,
                            pause_at_breaks=breaks, pause_on_key_events=key_events)


def tick_until(live: LiveMatch, cond: Callable[[MatchEngine], bool], limit: int = 400) -> None:
    """Kosul saglanana kadar adim atar (otomatik duraklatmalari devam ettirerek)."""
    for _ in range(limit):
        if cond(live.engine):
            return
        assert not live.finished, "maç koşul sağlanmadan bitti"
        if live.paused:
            live.resume()
        live.tick()
    raise AssertionError("koşul sağlanamadı")


def pause_at(live: LiveMatch, minute: int, added: int = 0) -> None:
    """minute'+added dakikasi oynandiktan hemen sonra maci durdurur."""
    tick_until(live, lambda e: e.minute == minute and e.added == added and e.phase in PLAY_PHASES)
    live.pause()


def to_phase(live: LiveMatch, phase: MatchPhase) -> None:
    tick_until(live, lambda e: e.phase is phase)


def pick_swap(team: MatchTeam, position: Position) -> tuple[MatchPlayer, MatchPlayer]:
    """(cikacak: o rolde sahadaki ilk oyuncu, girecek: o mevkideki ilk yedek)."""
    out = min((p for p in team.on_pitch if p.role is position), key=lambda p: p.id)
    sub = min((p for p in team.bench if p.position is position), key=lambda p: p.id)
    return out, sub


def any_swap(team: MatchTeam) -> tuple[MatchPlayer, MatchPlayer]:
    """Kulubedeki ilk saha oyuncusu ve sahada ayni roldeki ilk oyuncu."""
    for sub in sorted(team.bench, key=lambda p: p.id):
        if sub.position is Position.GK:
            continue
        same = sorted((p for p in team.on_pitch if p.role is sub.position), key=lambda p: p.id)
        if same:
            return same[0], sub
    raise AssertionError("uygun değişiklik yok")


def swap(live: LiveMatch) -> None:
    out, sub = any_swap(live.managed_team)
    live.substitute(out.id, sub.id)


def type_count(r: MatchResult, etype: EventType, team_id: int | None = None) -> int:
    return sum(1 for e in r.events if e.type is etype and (team_id is None or e.team_id == team_id))


@cache
def knockout_seeds() -> dict[str, tuple[int, ...]]:
    """Mudahalesiz eleme maclarinda karar sekline gore tohumlar (her turden 2)."""
    found: dict[str, list[int]] = {"normal": [], "extra_time": [], "penalties": []}
    for seed in range(400):
        kind = new_engine(seed, knockout=KnockoutRule()).simulate().decided_by
        if len(found[kind]) < 2:
            found[kind].append(seed)
        if all(len(v) == 2 for v in found.values()):
            break
    assert all(len(v) == 2 for v in found.values()), found
    return {k: tuple(v) for k, v in found.items()}


def drive(live: LiveMatch, rng: random.Random) -> list[tuple[MatchPhase, str]]:
    """
    Maci rastgele uzunlukta dilimlerle oynatir; aralarda snapshot ve salt okunur gorunum cagrilari,
    rastgele DURDUR/DEVAM. Otomatik duraklatmalari (evre, sebep) olarak dondurur.
    """
    auto_pauses: list[tuple[MatchPhase, str]] = []
    while not live.finished:
        live.run(max_steps=rng.randint(1, 12))
        snap = live.snapshot()
        assert (snap.home_score, snap.away_score) == (live.engine.home.stats.goals, live.engine.away.stats.goals)
        if live.managed_team is not None:
            live.sub_status()
            live.lineup_rows()
            live.bench_rows()
        _ = (live.clock, live.progress, live.can_substitute_now, live.phase_label)
        if live.paused:
            auto_pauses.append((live.phase, live.pause_reason))
            live.resume()
        elif not live.finished and rng.random() < 0.35:
            live.pause()
            live.snapshot()
            assert live.run() == [] and live.tick() == []           # durakken ilerlemez
            live.resume()
    return auto_pauses


# ===========================================================================
# 1) Determinizm: canli akis == simulate()
# ===========================================================================

@pytest.mark.parametrize("seed", [0, 3, 7, 11, 19, 42])
def test_live_league_match_with_pauses_equals_simulate(seed):
    reference = new_engine(seed).simulate()
    live = LiveMatch.create(new_engine(seed), 1)                  # varsayilan: molalarda ve olaylarda durur
    auto_pauses = drive(live, random.Random(seed * 31 + 1))
    result = live.result()
    assert fingerprint(result) == fingerprint(reference)
    assert (MatchPhase.HALF_TIME, "Devre arası") in auto_pauses
    assert live.history == []                                      # hic mudahale yok


def test_live_knockout_matches_with_extra_time_and_penalties_equal_simulate():
    seeds = knockout_seeds()
    for kind, kind_seeds in seeds.items():
        for seed in kind_seeds:
            reference = new_engine(seed, knockout=KnockoutRule()).simulate()
            live = LiveMatch.create(new_engine(seed, knockout=KnockoutRule()), 1)
            auto_pauses = drive(live, random.Random(seed))
            result = live.result()
            assert result.decided_by == kind
            assert fingerprint(result) == fingerprint(reference), (kind, seed)
            assert (result.shootout is None) == (reference.shootout is None)
            if reference.shootout is not None:
                assert result.shootout.kicks == reference.shootout.kicks
            assert result.advancing.id == reference.advancing.id
            break_pauses = [p for p in auto_pauses if p[0] in BREAK_PHASES]
            if kind == "normal":
                assert break_pauses == [(MatchPhase.HALF_TIME, "Devre arası")]
            else:
                assert break_pauses == [(MatchPhase.HALF_TIME, "Devre arası"),
                                        (MatchPhase.EXTRA_TIME_BREAK, "Uzatmalar öncesi mola"),
                                        (MatchPhase.EXTRA_TIME_HALF_TIME, "Uzatmaların devre arası")]


@pytest.mark.parametrize("seed,home_ovr,away_ovr,home_goals,away_goals,n_events,digest", GOLDEN_SAMPLE)
def test_golden_results_unchanged_with_default_instructions_and_standard_rule(
        seed, home_ovr, away_ovr, home_goals, away_goals, n_events, digest):
    cfg = engine_config_for(SubRule.STANDARD)
    assert cfg == EngineConfig()
    eng = MatchEngine(make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr), seed=seed, config=cfg)
    live = LiveMatch.create(eng, 1, instructions=TeamInstructions(), auto_subs=True, sub_rule=SubRule.STANDARD)
    assert eng.set_instructions(eng.away, TeamInstructions(Mentality.BALANCED, Tackling.NORMAL)) is None
    drive(live, random.Random(seed))
    result = live.result()
    assert (result.home_score, result.away_score, len(result.events)) == (home_goals, away_goals, n_events)
    assert fingerprint(result) == digest
    assert type_count(result, EventType.TACTICAL_CHANGE) == 0


def test_step_then_simulate_finishes_identically_and_result_guards():
    reference = new_engine(5).simulate()
    eng = new_engine(5)
    with pytest.raises(RuntimeError):
        eng.result()
    assert not eng.started and eng.phase is MatchPhase.NOT_STARTED
    first = eng.step()
    assert [e.type for e in first] == [EventType.KICK_OFF] and eng.phase is MatchPhase.FIRST_HALF
    for _ in range(69):
        eng.step()
    snap = eng.snapshot()
    assert snap.man_of_the_match is None and snap.events is not eng.events and snap.events == eng.events
    snap.events.append(snap.events[0])                              # kopya: motoru bozmaz
    assert len(eng.events) == len(snap.events) - 1
    with pytest.raises(RuntimeError):
        eng.result()
    result = eng.simulate()
    assert fingerprint(result) == fingerprint(reference)
    assert eng.finished and eng.step() == [] and eng.snapshot() is result and eng.result() is result


def _scripted_match(seed: int) -> MatchResult:
    live = new_live(seed, rule=SubRule.FIVE_IN_THREE, home=big_team(1, "Ev"), breaks=True, key_events=True)
    pause_at(live, 30)
    swap(live)
    live.change_formation("4-3-3")
    live.set_instructions(Mentality.ALL_OUT_ATTACK, Tackling.HARD)
    pause_at(live, 70)
    swap(live)
    live.set_instructions(Mentality.PARK_THE_BUS, Tackling.CALM)
    live.change_formation("5-3-2")
    live.play_to_end()
    return live.result()


@pytest.mark.parametrize("seed", [2, 5, 13])
def test_same_interventions_replay_identically_and_change_the_match(seed):
    first, second = _scripted_match(seed), _scripted_match(seed)
    assert fingerprint(first) == fingerprint(second)
    untouched = new_engine(seed, home=big_team(1, "Ev")).simulate()
    assert fingerprint(first) != fingerprint(untouched)
    assert type_count(first, EventType.TACTICAL_CHANGE, 1) == 4
    # 30. dakikaya kadar akis birebir ayni; mudahale sonrasi mac farklilasir
    cut = next(i for i, e in enumerate(first.events) if e.type is EventType.SUBSTITUTION and e.detail == "manual")
    assert [e.description for e in first.events[:cut]] == [e.description for e in untouched.events[:cut]]


def test_interventions_never_draw_random_numbers():
    eng = new_engine(8, home=big_team(1, "Ev"))
    live = LiveMatch(engine=eng, managed_team_id=1)
    for _ in range(50):
        eng.step()
    state = eng.rng.getstate()
    out, sub = pick_swap(eng.home, Position.MID)
    eng.manual_substitution(eng.home, out.id, sub.id)
    eng.change_formation(eng.home, "5-3-2")
    eng.change_formation(eng.away, (4, 3, 3))
    eng.set_instructions(eng.home, TeamInstructions(Mentality.ALL_OUT_ATTACK, Tackling.HARD))
    live.sub_status()
    live.lineup_rows()
    live.bench_rows()
    eng.snapshot()
    assert eng.rng.getstate() == state


# ===========================================================================
# 2) Oyuncu degisikligi: kurallar ve durum
# ===========================================================================

def test_substitution_rejected_before_kickoff():
    live = new_live(1)
    team = live.managed_team
    out, sub = pick_swap(team, Position.MID)
    assert not live.engine.started and not live.can_substitute_now
    live.pause()
    with pytest.raises(InterventionError, match="henüz başlamadı"):
        live.substitute(out.id, sub.id)
    with pytest.raises(InterventionError, match="başlamadan"):
        live.engine.manual_substitution(team, out.id, sub.id)
    assert team.subs_used == 0 and out.on_pitch and not sub.on_pitch and live.engine.events == []


def test_substitution_requires_pause_while_running_but_not_in_break():
    live = new_live(2, cfg=QUIET)
    team = live.managed_team
    live.run(max_steps=20)
    out, sub = pick_swap(team, Position.FWD)
    assert not live.paused and not live.can_substitute_now
    with pytest.raises(InterventionError, match="önce maçı durdur"):
        live.substitute(out.id, sub.id)
    assert team.subs_used == 0 and out.on_pitch
    live.pause()
    assert live.can_substitute_now
    live.substitute(out.id, sub.id)
    assert sub.on_pitch and not out.on_pitch

    # molada (otomatik duraklatma kapaliyken bile) DURDUR gerekmez
    live.resume()
    to_phase(live, MatchPhase.HALF_TIME)
    assert not live.paused and live.engine.in_break and live.can_substitute_now
    out2, sub2 = pick_swap(team, Position.MID)
    live.substitute(out2.id, sub2.id)
    assert team.subs_used == 2 and sub2.entered_minute == 45


def test_substitution_rejected_after_final_whistle():
    live = new_live(3, key_events=False)
    out, sub = pick_swap(live.managed_team, Position.MID)
    live.play_to_end()
    assert live.finished and not live.can_substitute_now
    with pytest.raises(InterventionError, match="Maç bitti"):
        live.substitute(out.id, sub.id)
    live.pause()
    assert not live.paused                                          # bitmis mac duraklatilamaz
    for action in (lambda: live.change_formation("5-3-2"), lambda: live.set_instructions("PARK_THE_BUS", "HARD")):
        with pytest.raises(InterventionError, match="Maç bitti"):
            action()
    with pytest.raises(InterventionError, match="Maç bitti"):
        live.engine.change_formation(live.managed_team, "5-3-2")
    with pytest.raises(InterventionError, match="Maç bitti"):
        live.engine.set_instructions(live.managed_team, TeamInstructions(Mentality.PARK_THE_BUS))


def test_substitution_player_eligibility():
    home = make_team(1, "Ev", 80)
    outcast = make_player(160, Position.MID, 70)
    outcast.matchday = False                                         # menajer kadro disi birakti
    home.players.append(outcast)
    live = new_live(4, home=home, cfg=QUIET)
    eng, team = live.engine, live.managed_team
    assert outcast in team.players and outcast not in team.bench and not outcast.on_pitch
    pause_at(live, 30)
    starters = sorted(team.on_pitch, key=lambda p: p.id)
    bench_mid = next(p for p in team.bench if p.position is Position.MID)
    mids = [p for p in starters if p.role is Position.MID]

    def rejected(out_id: int, in_id: int, pattern: str) -> None:
        state = (team.subs_used, [p.id for p in team.on_pitch], len(eng.events))
        with pytest.raises(InterventionError, match=pattern):
            live.substitute(out_id, in_id)
        assert (team.subs_used, [p.id for p in team.on_pitch], len(eng.events)) == state

    rejected(bench_mid.id, mids[0].id, "şu an sahada değil")         # cikan sahada degil
    rejected(9999, bench_mid.id, "kadrosunda değil")
    rejected(mids[0].id, 9999, "kadrosunda değil")
    rejected(mids[0].id, 201, "kadrosunda değil")                    # rakibin oyuncusu
    rejected(mids[0].id, mids[1].id, "zaten sahada")
    rejected(mids[0].id, outcast.id, "kadro dışı")

    # oyundan cikmis oyuncu tekrar giremez
    live.substitute(mids[0].id, bench_mid.id)
    assert team.subs_used == 1
    rejected(bench_mid.id, mids[0].id, "tekrar giremez")

    # sakatlanan ve kirmizi kart goren oyuncu tekrar giremez
    injured = next(p for p in team.on_pitch if p.role is Position.FWD)
    injured.injured = True
    team.remove_player(injured, eng.minute)
    sent = next(p for p in team.on_pitch if p.role is Position.DEF)
    eng._send_off(team, sent, second_yellow=False)
    victim = next(p for p in team.on_pitch if p.role is Position.MID)
    rejected(victim.id, injured.id, "tekrar giremez")
    rejected(victim.id, sent.id, "tekrar giremez")
    assert team.player_count == 9

    with pytest.raises(InterventionError, match="takım yok"):
        eng.manual_substitution(99, victim.id, 0)
    with pytest.raises(InterventionError, match="oynamıyor"):
        eng.manual_substitution(make_team(7, "Yabancı", 80), victim.id, 0)


def test_goalkeeper_substitution_rules():
    live = new_live(5, cfg=QUIET)
    team = live.managed_team
    pause_at(live, 25)
    keeper = team.keeper
    backup = next(p for p in team.bench if p.position is Position.GK)
    defender = next(p for p in team.on_pitch if p.role is Position.DEF)
    bench_mid = next(p for p in team.bench if p.position is Position.MID)

    with pytest.raises(InterventionError, match="kaleye geçmeli"):
        live.substitute(keeper.id, backup.id, Position.DEF)
    with pytest.raises(InterventionError, match="kaleye geçmeli"):
        live.substitute(keeper.id, bench_mid.id, Position.MID)
    with pytest.raises(InterventionError, match="Kalede zaten"):
        live.substitute(defender.id, backup.id, Position.GK)
    with pytest.raises(InterventionError, match="Kalede zaten"):
        live.substitute(defender.id, bench_mid.id, Position.GK)
    assert team.subs_used == 0 and team.keeper is keeper

    event = live.substitute(keeper.id, backup.id)                    # rol verilmezse kalecinin gorevi
    assert team.keeper is backup and backup.role is Position.GK and not keeper.on_pitch
    assert "mevki dışı" not in event.description and team.player_count == 11
    assert [p.role for p in team.on_pitch].count(Position.GK) == 1

    # yedek kaleci yoksa saha oyuncusu kaleye gecebilir (mevki disi)
    live2 = new_live(6, cfg=QUIET)
    team2 = live2.managed_team
    pause_at(live2, 25)
    keeper2, mid2 = team2.keeper, next(p for p in team2.bench if p.position is Position.MID)
    event2 = live2.substitute(keeper2.id, mid2.id)
    assert team2.keeper is mid2 and mid2.role is Position.GK
    assert "(MID → GK, mevki dışı)" in event2.description
    assert live2.engine._keeper_strength(team2) < 40                # saha oyuncusu kalede zayif


def test_classic_three_substitution_limit():
    live = new_live(7, rule=SubRule.CLASSIC_THREE, cfg=QUIET, home=big_team(1, "Ev"))
    eng, team = live.engine, live.managed_team
    assert (eng.max_subs, eng.max_windows) == (3, None)
    pause_at(live, 55)
    for _ in range(3):
        swap(live)
    status = live.sub_status()
    assert (status.used, status.limit, status.remaining, status.block) == (3, 3, 0, "değişiklik hakkı kalmadı")
    assert status.text() == "3/3 değişiklik"
    out, sub = any_swap(team)
    with pytest.raises(InterventionError, match=r"değişiklik hakkı kalmadı \(3/3 değişiklik\)"):
        live.substitute(out.id, sub.id)
    live.resume()
    pause_at(live, 80)
    with pytest.raises(InterventionError, match="değişiklik hakkı kalmadı"):
        live.substitute(out.id, sub.id)
    assert team.subs_used == 3 and team.stats.substitutions == 3 and out.on_pitch


def test_five_in_three_windows_in_one_pause_and_fourth_window_blocked():
    live = new_live(8, rule=SubRule.FIVE_IN_THREE, cfg=QUIET, home=big_team(1, "Ev"), auto_subs=False)
    eng, team = live.engine, live.managed_team
    assert (eng.max_subs, eng.max_windows) == (5, 3)
    assert live.sub_status().text() == "0/5 değişiklik · pencere 0/3"

    pause_at(live, 20)
    swap(live)
    swap(live)                                                      # ayni duraklama: ayni pencere
    assert (team.subs_used, team.sub_windows_used) == (2, 1)
    assert live.sub_status().text() == "2/5 değişiklik · pencere 1/3"
    live.resume()
    live.pause()                                                    # adim atmadan tekrar durdur: ayni an
    swap(live)
    assert (team.subs_used, team.sub_windows_used) == (3, 1)

    pause_at(live, 40)
    swap(live)
    assert (team.subs_used, team.sub_windows_used) == (4, 2)
    pause_at(live, 60)
    assert live.sub_status().block is None
    live.resume()
    pause_at(live, 65)                                              # sadece durdurmak pencere harcamaz
    assert team.sub_windows_used == 2
    live.resume()
    live.change_formation("4-3-3")                                  # taktik degisikligi de harcamaz
    live.set_instructions(Mentality.ALL_OUT_ATTACK, Tackling.NORMAL)
    pause_at(live, 70)
    assert (team.subs_used, team.sub_windows_used) == (4, 2)
    swap(live)
    assert (team.subs_used, team.sub_windows_used) == (5, 3)
    assert live.sub_status().block == "değişiklik hakkı kalmadı"


def test_fourth_separate_pause_is_blocked_by_window_rule():
    live = new_live(9, rule=SubRule.FIVE_IN_THREE, cfg=QUIET, home=big_team(1, "Ev"), auto_subs=False)
    team = live.managed_team
    for minute in (15, 35, 55):
        pause_at(live, minute)
        swap(live)
    assert (team.subs_used, team.sub_windows_used) == (3, 3)
    pause_at(live, 70)
    status = live.sub_status()
    assert status.block == "değişiklik penceresi kalmadı" and status.remaining == 2
    assert status.text() == "3/5 değişiklik · pencere 3/3"
    out, sub = any_swap(team)
    with pytest.raises(InterventionError, match=r"değişiklik penceresi kalmadı \(3/5 değişiklik\)"):
        live.substitute(out.id, sub.id)
    assert out.on_pitch and not sub.played and (team.subs_used, team.sub_windows_used) == (3, 3)


def test_half_time_substitutions_do_not_consume_windows():
    live = new_live(10, rule=SubRule.FIVE_IN_THREE, cfg=QUIET, home=big_team(1, "Ev"), auto_subs=False,
                    breaks=True)
    team = live.managed_team
    live.run()
    assert live.paused and live.phase is MatchPhase.HALF_TIME and live.pause_reason == "Devre arası"
    swap(live)
    swap(live)
    assert (team.subs_used, team.sub_windows_used) == (2, 0)
    assert live.sub_status().text() == "2/5 değişiklik · pencere 0/3"
    for minute in (50, 60, 70):
        pause_at(live, minute)
        swap(live)
    assert (team.subs_used, team.sub_windows_used) == (5, 3)
    live.play_to_end()
    assert team.stats.substitutions == 5
    assert type_count(live.result(), EventType.SUBSTITUTION, 1) == 5


def test_assistant_injury_sub_and_manager_sub_in_same_stoppage_share_one_window():
    """Sakatlikta asistan yedegi sokar, mac o an otomatik durur; menajerin ayni duraklamadaki degisikligi yeni pencere acmaz."""
    cfg = EngineConfig(base_card=0.0, base_injury=0.03)
    for seed in range(40):
        live = new_live(seed, rule=SubRule.FIVE_IN_THREE, cfg=cfg, home=big_team(1, "Ev"), auto_subs=False,
                        key_events=True)
        team = live.managed_team
        live.run()
        if live.finished or not (live.pause_reason or "").startswith("Sakatlık"):
            continue
        last = live.engine.events[-1]
        assert last.type is EventType.SUBSTITUTION and last.team_id == 1 and last.player_id is not None
        assert (team.subs_used, team.sub_windows_used) == (1, 1)
        assert live.sub_status().text() == "1/5 değişiklik · pencere 1/3" and live.sub_status().block is None
        swap(live)
        assert (team.subs_used, team.sub_windows_used) == (2, 1)
        assert live.sub_status().text() == "2/5 değişiklik · pencere 1/3"
        return
    raise AssertionError("40 maçta yönetilen takımda sakatlık duraklaması yok")


def test_assistant_fatigue_sub_right_after_manager_pause_shares_the_window():
    """Menajer 59'dan sonra durdurup degistirir; asistanin 60' basindaki yorgunluk degisikligi ayni duraklamadir."""
    cfg = EngineConfig(base_card=0.0, base_injury=0.0, tired_threshold=99.0,
                       tired_threshold_v2=99.0, sub_window_spread=1.0,
                       tactical_sub_from_minute_v2=60)
    live = new_live(30, rule=SubRule.FIVE_IN_THREE, cfg=cfg, home=big_team(1, "Ev"))
    team = live.managed_team
    pause_at(live, 59)
    assert team.subs_used == 0
    swap(live)
    assert (team.subs_used, team.sub_windows_used) == (1, 1)
    live.resume()

    def tired_subs(events) -> int:
        return sum(1 for e in events if e.team_id == 1 and "yoruldu" in e.description)

    # 60' basi: top oyuna girmedi -> menajerin duraklamasi. Pencere kurali varken asistan tum
    # yorgunlari AYNI duraklamada degistirir (1 hak acil durum icin saklanir): pencere harcamaz.
    n = tired_subs(live.tick())
    assert n >= 2                                                   # birden cok yorgun, tek duraklama
    assert (team.subs_used, team.sub_windows_used) == (1 + n, 1)
    live.run(max_steps=20)
    assert team.sub_windows_used <= 2 and team.subs_used <= 4       # acil durum hakki saklanir


def test_assistant_subs_right_after_half_time_do_not_consume_a_window():
    """46' basindaki asistan degisiklikleri moladan sonra top oyuna girmeden yapilir: pencere saymaz."""
    cfg = EngineConfig(base_card=0.0, base_injury=0.0, tired_threshold=99.0, tactical_sub_from_minute=46,
                       tired_threshold_v2=99.0, sub_window_spread=1.0, tactical_sub_from_minute_v2=46)
    live = new_live(30, rule=SubRule.FIVE_IN_THREE, cfg=cfg, home=big_team(1, "Ev"))
    team = live.managed_team
    to_phase(live, MatchPhase.HALF_TIME)
    assert team.subs_used == 0
    tick_until(live, lambda e: e.phase is MatchPhase.SECOND_HALF)       # 46. dakika oynandi
    assert team.subs_used >= 2 and team.sub_windows_used == 0            # molanin duraklamasi
    live.pause()
    used = team.subs_used
    swap(live)                                                           # 46'dan sonra: ilk gercek pencere
    assert (team.subs_used, team.sub_windows_used) == (used + 1, 1)


def test_manual_substitution_waits_for_the_first_minute():
    """Baslama dudugu ile ilk dakika arasinda degisiklik yok (2D sahada baslama karesi 12 oyuncu cizerdi)."""
    live = new_live(3)
    live.tick()                                                          # KICK_OFF
    live.pause()
    out, sub = any_swap(live.managed_team)
    with pytest.raises(InterventionError, match="ilk dakika"):
        live.substitute(out.id, sub.id)
    live.resume()
    live.tick()
    live.pause()
    live.substitute(out.id, sub.id)
    assert sub.on_pitch and live.managed_team.player_count == 11


def test_emergency_keeper_role_change_is_recorded_for_pitch_history():
    from match_feed import build_timeline
    from pitch import build_scenes

    eng = new_engine(5, home=make_team(1, "Ev", 80))
    eng.start()
    for _ in range(10):
        eng.step()
    team = eng.home
    for p in [p for p in team.players if p.position is Position.GK and not p.on_pitch]:
        p.matchday = False                                               # yedek kaleci yok
    keeper = team.keeper
    index = len(eng.events)
    eng._send_off(team, keeper, second_yellow=False)
    emergency = team.keeper
    assert emergency is not None and emergency.position is not Position.GK
    assert emergency.role_changes and emergency.role_changes[-1][0] >= index
    assert emergency.role_at(0) is emergency.position and emergency.role_at(len(eng.events)) is Position.GK
    eng.run_to_end()
    result = eng.result()
    frames = build_timeline(result)
    scenes = build_scenes(result, frames)
    early = next(d for d in scenes[1].dots if d.player_id == emergency.id)
    assert not early.is_keeper                                           # gecmis karelerde kendi mevkisinde


def test_step_raises_instead_of_spinning_when_the_flow_breaks():
    eng = new_engine(1)
    eng.start()
    eng.step()

    def broken(minute, added):
        raise ValueError("boom")

    eng._play_minute = broken
    with pytest.raises(ValueError):
        eng.step()
    with pytest.raises(RuntimeError, match="yarıda kesildi"):
        eng.step()


def test_half_time_intervention_frames_keep_break_phase_and_direction():
    from match_feed import PHASE_HALF_TIME, build_timeline
    from pitch import build_scene, build_scenes, home_attacks_right

    live = new_live(8, breaks=True)
    to_phase(live, MatchPhase.HALF_TIME)
    swap(live)
    live.change_formation("5-3-2")
    live.play_to_end()
    result = live.result()
    frames = build_timeline(result)
    ht = next(i for i, f in enumerate(frames) if f.event.type == EventType.HALF_TIME.value)
    for frame in frames[ht + 1: ht + 3]:
        assert frame.event.type in (EventType.SUBSTITUTION.value, EventType.TACTICAL_CHANGE.value)
        assert frame.phase == PHASE_HALF_TIME and home_attacks_right(frame)
    scenes = build_scenes(result, frames)
    for i in (0, ht, ht + 2, len(frames) - 1):
        assert build_scene(result, frames, i) == scenes[i]


def test_extra_time_grants_one_more_substitution_and_window():
    # Pozisyon uretimi en alt sinirda: maclar neredeyse hep 0-0 -> uzatma
    cfg = EngineConfig(base_card=0.0, base_injury=0.0, base_chance=0.0)
    for seed in range(20):
        live = new_live(seed, rule=SubRule.FIVE_IN_THREE, cfg=cfg, home=big_team(1, "Ev"), auto_subs=False,
                        knockout=KnockoutRule(), breaks=True)
        eng, team = live.engine, live.managed_team
        for minute in (20, 40, 60):
            pause_at(live, minute)
            swap(live)
        pause_at(live, 75)
        assert live.sub_status().block == "değişiklik penceresi kalmadı"
        live.resume()
        live.run()                                                  # devre arasi 60'tan once gecildi
        if live.phase is not MatchPhase.EXTRA_TIME_BREAK:
            assert live.finished
            continue
        assert live.pause_reason == "Uzatmalar öncesi mola"
        assert (eng.max_subs, eng.max_windows) == (6, 4)
        status = live.sub_status()
        assert status.block is None and status.text() == "3/6 değişiklik · pencere 3/4"
        swap(live)                                                  # molada: pencere saymaz
        assert (team.subs_used, team.sub_windows_used) == (4, 3)
        pause_at(live, 95)
        swap(live)                                                  # uzatmanin ek penceresi
        assert (team.subs_used, team.sub_windows_used) == (5, 4)
        pause_at(live, 100)
        assert live.sub_status().block == "değişiklik penceresi kalmadı"
        out, sub = any_swap(team)
        with pytest.raises(InterventionError, match="değişiklik penceresi kalmadı"):
            live.substitute(out.id, sub.id)
        live.resume()
        live.run()
        assert live.paused and live.phase is MatchPhase.EXTRA_TIME_HALF_TIME
        swap(live)                                                  # uzatma arasi: ek hak, pencere yok
        assert (team.subs_used, team.sub_windows_used) == (6, 4)
        pause_at(live, 110)
        assert live.sub_status().block == "değişiklik hakkı kalmadı"
        with pytest.raises(InterventionError, match=r"değişiklik hakkı kalmadı \(6/6 değişiklik\)"):
            live.substitute(*[p.id for p in any_swap(team)])
        live.play_to_end()
        result = live.result()
        assert team.stats.substitutions == 6 and result.extra_time and result.end_minute == 120
        manual = [e for e in result.events if e.type is EventType.SUBSTITUTION and e.detail == "manual"]
        assert [e.minute for e in manual] == [20, 40, 60, 90, 95, 105]
        by_id = {p.id: p for p in team.players}
        for e in manual:
            entered = by_id[e.player_id]
            assert entered.entered_minute == e.minute and entered.left_minute == 120
            assert entered.minutes_played == 120 - e.minute
            assert entered.energy_log[0][0] == e.minute
        assert sum(p.minutes_played for p in team.players) == 11 * 120
        return
    raise AssertionError("20 maçta uzatmaya giden maç yok")


def test_valid_manual_substitution_updates_state_event_and_minutes():
    live = new_live(11, cfg=QUIET)
    eng, team = live.engine, live.managed_team
    pause_at(live, 60)
    out, sub = pick_swap(team, Position.MID)
    out_energy = round(out.energy)
    event = live.substitute(out.id, sub.id)

    assert event is eng.events[-1]
    assert (event.type, event.detail, event.team_id, event.player_id) == (EventType.SUBSTITUTION, "manual", 1, sub.id)
    assert (event.minute, event.added_time) == (60, 0)
    assert out.name in event.description and sub.name in event.description
    assert "menajer kararı" in event.description and "mevki dışı" not in event.description
    assert live.history == [f"60' {event.description}"]
    assert out.substituted and not out.on_pitch and out.left_minute == 60
    assert out.energy_log[-1] == (60, out_energy)
    assert sub.on_pitch and sub.entered_minute == 60 and sub.role is Position.MID and sub.played
    assert sub.energy_log == [(60, 100)]
    assert team.player_count == 11 and team.subs_used == 1 and team.stats.substitutions == 1
    assert sub not in team.bench and out not in team.bench
    assert sub.id not in {r["id"] for r in live.bench_rows()}
    assert sub.id in {r["id"] for r in live.lineup_rows()} and out.id not in {r["id"] for r in live.lineup_rows()}

    # mevki disi degisiklik aciklamada belirtilir
    defender = next(p for p in team.on_pitch if p.role is Position.DEF)
    forward = next(p for p in team.bench if p.position is Position.FWD)
    event2 = live.substitute(defender.id, forward.id)
    assert "(FWD → DEF, mevki dışı)" in event2.description and forward.role is Position.DEF

    live.play_to_end()
    result = live.result()
    assert out.minutes_played == 60 and sub.minutes_played == 30 and sub.left_minute == 90
    assert out.minutes_played + sub.minutes_played == 90
    assert all(m <= 60 for m, _ in out.energy_log)
    assert sub.energy_log[0] == (60, 100) and all(m >= 60 for m, _ in sub.energy_log)
    assert [m for m, _ in sub.energy_log] == [60, 65, 70, 75, 80, 85, 90]
    assert sub.energy < 100                                         # oyuna girince yorulur
    manual = [e for e in result.events if e.type is EventType.SUBSTITUTION and e.detail == "manual"]
    assert [e.player_id for e in manual] == [sub.id, forward.id]


# ===========================================================================
# 3) Mudahaleler motora aninda ulasir
# ===========================================================================

def test_replacing_exhausted_player_raises_team_strength_immediately():
    eng = new_engine(12, cfg=QUIET)
    for _ in range(61):
        eng.step()
    team = eng.home
    tired, fresh = pick_swap(team, Position.MID)
    tired.energy = 5.0
    before = {k: eng._team_strength(team, k) for k in KINDS}
    # 13A/S5: oyuna yeni giren oyuncu kisa sureli "taze bacak" carpani tasir (_freshness),
    # bu yuzden beklenen toplam da o carpanla kurulur.
    sums = {k: sum(eng._player_strength(p, k) * eng._freshness(p) for p in team.on_pitch) for k in KINDS}
    tired_part = {k: eng._player_strength(tired, k) * eng._freshness(tired) for k in KINDS}
    cohesion = eng._am_team(team).cohesion                   # 14B: takim uyumu sahadakilerden (varsayilan acik)
    eng.manual_substitution(team, tired.id, fresh.id)
    cohesion = eng._am_team(team).cohesion / cohesion
    for k in KINDS:
        after = eng._team_strength(team, k)
        expected = before[k] * cohesion * (sums[k] - tired_part[k]
                                           + eng._player_strength(fresh, k) * eng._freshness(fresh)) / sums[k]
        assert after == pytest.approx(expected, rel=1e-12)
        assert after > before[k]
    assert eng._team_strength(team, "midfield") > before["midfield"] * 1.02


def _role_counts(team: MatchTeam) -> dict[Position, int]:
    counts = Counter(p.role for p in team.on_pitch)
    return {pos: counts.get(pos, 0) for pos in Position}


def test_formation_change_reassigns_roles_applies_style_and_logs_event(monkeypatch):
    eng = new_engine(13, cfg=QUIET)
    for _ in range(31):
        eng.step()
    team = eng.home
    before = {k: eng._team_strength(team, k) for k in KINDS}
    old_roles = {p.id: p.role for p in team.on_pitch}

    event = eng.change_formation(team, "5-3-2")
    index = len(eng.events) - 1
    assert event is eng.events[index]
    assert team.formation == (5, 3, 2)
    assert _role_counts(team) == {Position.GK: 1, Position.DEF: 5, Position.MID: 3, Position.FWD: 2}
    moved = [p for p in team.on_pitch if p.role is not old_roles[p.id]]
    assert len(moved) == 1
    mover = moved[0]
    assert (mover.position, mover.role) == (Position.MID, Position.DEF)
    assert mover.role_changes == [(index, Position.MID, Position.DEF)]
    assert all(p.role_changes == [] for p in team.players if p is not mover)
    assert mover.role_at(index) is Position.DEF and mover.role_at(index - 1) is Position.MID
    assert mover.role_at(0) is Position.MID and mover.role_at(10_000) is Position.DEF

    assert (event.type, event.detail, event.team_id, event.player) == (EventType.TACTICAL_CHANGE, "formation", 1, None)
    assert (event.minute, event.added_time) == (30, 0)
    assert "Taktik değişikliği (Ev): diziliş 4-4-2 → 5-3-2." in event.description
    assert f"Yeni görevler: {mover.name} MID→DEF." in event.description
    assert f"Mevki dışı oynayanlar: {mover.name}." in event.description

    # mevki disi cezasi
    base = 0.4 * mover.overall + 0.6 * mover.defense_rating
    expected = (base * mover.condition_factor * mover._am.defense      # 14B: savunma kanali (varsayilan acik)
                * mover.fatigue_factor * ROLE_WEIGHTS["defense"][Position.DEF] * eng.cfg.out_of_position_penalty)
    assert eng._player_strength(mover, "defense") == pytest.approx(expected, rel=1e-12)

    # dizilis tarzi carpani (5-3-2: hucum 0.92, orta saha 0.95, savunma 1.10) kalan dakikalarda gecerli
    after = {k: eng._team_strength(team, k) for k in KINDS}
    assert after["defense"] > before["defense"] and after["attack"] < before["attack"]
    monkeypatch.setitem(FORMATION_STYLE, (5, 3, 2), {"attack": 1.0, "midfield": 1.0, "defense": 1.0})
    for k in KINDS:
        assert after[k] / eng._team_strength(team, k) == pytest.approx({"attack": 0.92, "midfield": 0.95,
                                                                         "defense": 1.10}[k], rel=1e-12)
    monkeypatch.undo()

    # ikinci degisiklik: gecmis rol sorgusu her donemi dogru verir
    for _ in range(20):
        eng.step()
    event2 = eng.change_formation(team, "4-3-3")
    index2 = eng.events.index(event2)
    assert _role_counts(team) == {Position.GK: 1, Position.DEF: 4, Position.MID: 3, Position.FWD: 3}
    assert mover.role_changes == [(index, Position.MID, Position.DEF), (index2, Position.DEF, Position.FWD)]
    assert [mover.role_at(i) for i in (index - 1, index, index2 - 1, index2)] == \
        [Position.MID, Position.DEF, Position.DEF, Position.FWD]
    assert "diziliş 5-3-2 → 4-3-3" in event2.description
    assert eng._team_strength(team, "attack") > after["attack"]


def test_formation_change_validation_and_prekickoff_behaviour():
    eng = new_engine(14)
    team = eng.home
    assert eng.change_formation(team, "4-4-2") is None                  # ayni dizilis
    with pytest.raises(InterventionError, match="Bilinmeyen diziliş: 4-2-4"):
        eng.change_formation(team, "4-2-4")
    with pytest.raises(InterventionError, match="Bilinmeyen diziliş"):
        eng.change_formation(team, (2, 3, 5))
    assert eng.change_formation(team, "4-3-3") is None                  # baslama oncesi: olay yok
    assert eng.events == [] and team.formation == (4, 3, 3)
    assert _role_counts(team) == {Position.GK: 1, Position.DEF: 4, Position.MID: 3, Position.FWD: 3}
    assert all(p.role_changes == [] for p in team.players)
    assert eng.change_formation(team, (4, 3, 3)) is None

    live = LiveMatch.create(new_engine(14), 1)
    assert live.formation == "4-4-2" and live.change_formation("3-5-2") is None
    live.tick()
    event = live.change_formation("5-3-2")                            # mac akarken de verilebilir
    assert event.type is EventType.TACTICAL_CHANGE and live.formation == "5-3-2"
    assert live.history == [f"0' {event.description}"]
    assert live.change_formation("5-3-2") is None and len(live.history) == 1


def test_formation_change_when_short_handed_drops_forwards_first():
    eng = new_engine(15, cfg=QUIET)
    for _ in range(31):
        eng.step()
    team = eng.home
    eng._send_off(team, next(p for p in team.on_pitch if p.role is Position.DEF), second_yellow=False)
    eng.change_formation(team, "4-3-3")                                # 10 slot, 9 saha oyuncusu
    assert _role_counts(team) == {Position.GK: 1, Position.DEF: 4, Position.MID: 3, Position.FWD: 2}
    eng._send_off(team, next(p for p in team.on_pitch if p.role is Position.MID), second_yellow=False)
    eng.change_formation(team, "5-3-2")                                # 10 slot, 8 saha oyuncusu
    assert _role_counts(team) == {Position.GK: 1, Position.DEF: 5, Position.MID: 2, Position.FWD: 1}
    eng.change_formation(team, "3-5-2")
    assert _role_counts(team) == {Position.GK: 1, Position.DEF: 3, Position.MID: 4, Position.FWD: 1}
    assert team.player_count == 9 and team.keeper is not None


def test_default_instructions_are_exactly_neutral():
    inst = TeamInstructions()
    assert inst.is_default and inst == TeamInstructions(Mentality.BALANCED, Tackling.NORMAL)
    assert [inst.strength_factor(k) for k in KINDS] == [1.0, 1.0, 1.0]
    assert (inst.fatigue_factor, inst.card_factor, inst.straight_red_factor, inst.injury_factor) == (1.0, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        inst.strength_factor("goalkeeping")
    assert not TeamInstructions(tackling=Tackling.CALM).is_default
    assert parse_mentality("ALL_OUT_ATTACK") is Mentality.ALL_OUT_ATTACK
    assert parse_mentality(MENTALITY_LABELS[Mentality.PARK_THE_BUS]) is Mentality.PARK_THE_BUS
    assert parse_tackling(TACKLING_LABELS[Tackling.HARD]) is Tackling.HARD
    assert parse_tackling(Tackling.CALM) is Tackling.CALM
    with pytest.raises(ValueError):
        parse_mentality("Gegenpress")
    with pytest.raises(ValueError):
        parse_tackling("HARDER")


@pytest.mark.parametrize("mentality", list(Mentality))
@pytest.mark.parametrize("tackling", list(Tackling))
def test_instructions_multiply_team_strength_exactly(mentality, tackling):
    inst = TeamInstructions(mentality, tackling)
    m, t = MENTALITY_EFFECTS[mentality], TACKLING_EFFECTS[tackling]
    assert inst.strength_factor("attack") == m.attack
    assert inst.strength_factor("midfield") == m.midfield
    assert inst.strength_factor("defense") == pytest.approx(m.defense * t.defense, rel=1e-15)
    assert inst.fatigue_factor == pytest.approx(m.fatigue * t.fatigue, rel=1e-15)
    assert (inst.card_factor, inst.straight_red_factor, inst.injury_factor) == (t.card, t.straight_red, t.injury)

    eng = new_engine(16)
    for _ in range(40):
        eng.step()
    base = {team.id: {k: eng._team_strength(team, k) for k in KINDS} for team in (eng.home, eng.away)}
    event = eng.set_instructions(eng.home, inst)
    assert (event is None) == inst.is_default
    for k in KINDS:
        assert eng._team_strength(eng.home, k) == pytest.approx(base[1][k] * inst.strength_factor(k), rel=1e-12)
        assert eng._team_strength(eng.away, k) == base[2][k]         # rakip etkilenmez

    # yorgunluk: dakikalik enerji kaybi tam olarak fatigue_factor kadar olceklenir
    energy = {p.id: p.energy for p in eng.home.on_pitch}
    eng.set_instructions(eng.home, TeamInstructions())
    eng._apply_fatigue()
    neutral_loss = {p.id: energy[p.id] - p.energy for p in eng.home.on_pitch}
    for p in eng.home.on_pitch:
        p.energy = energy[p.id]
    eng.set_instructions(eng.home, inst)
    eng._apply_fatigue()
    for p in eng.home.on_pitch:
        assert energy[p.id] - p.energy == pytest.approx(neutral_loss[p.id] * inst.fatigue_factor, rel=1e-9)


def test_set_instructions_events_and_live_parsing():
    eng = new_engine(17)
    assert eng.set_instructions(eng.home, TeamInstructions(Mentality.PARK_THE_BUS)) is None     # baslama oncesi
    assert eng.events == [] and eng.home.instructions.mentality is Mentality.PARK_THE_BUS

    live = LiveMatch.create(new_engine(17), 1, instructions=TeamInstructions(tackling=Tackling.HARD))
    assert live.instructions == TeamInstructions(tackling=Tackling.HARD) and live.engine.events == []
    assert live.instructions_text() == "Dengeli · Sert Oyna"
    live.run(max_steps=11)
    event = live.set_instructions("ALL_OUT_ATTACK", "Sert Oyna")
    assert (event.type, event.detail, event.team_id) == (EventType.TACTICAL_CHANGE, "instructions", 1)
    assert event.description == "Talimat (Ev): zihniyet Çok Ofansif (Topyekûn Hücum)."
    event2 = live.set_instructions(Mentality.ALL_OUT_ATTACK, Tackling.CALM)
    assert event2.description == "Talimat (Ev): sertlik Sakin Kal."
    assert live.set_instructions("Çok Ofansif (Topyekûn Hücum)", "CALM") is None
    assert live.engine.home.instructions == TeamInstructions(Mentality.ALL_OUT_ATTACK, Tackling.CALM)
    assert live.engine.away.instructions.is_default
    assert len(live.history) == 2
    with pytest.raises(ValueError):
        live.set_instructions("Gegenpress", "NORMAL")


# ===========================================================================
# 4) Istatistiksel davranis (sabit tohumlar -> deterministik)
# ===========================================================================

STAT_SEEDS = range(120)
BALANCED = TeamInstructions()
ATTACK = TeamInstructions(Mentality.ALL_OUT_ATTACK)
BUS = TeamInstructions(Mentality.PARK_THE_BUS)
HARD = TeamInstructions(tackling=Tackling.HARD)
CALM = TeamInstructions(tackling=Tackling.CALM)


@cache
def season(home: TeamInstructions, away: TeamInstructions = BALANCED) -> dict[str, float]:
    """Ev sahibi (yonetilen) talimati LiveMatch.create ile, deplasmaninki duduk oncesi verilir; mac basi ortalamalar."""
    tot: Counter[str] = Counter()
    energy: list[float] = []
    for seed in STAT_SEEDS:
        eng = new_engine(seed)
        eng.set_instructions(eng.away, away)
        live = LiveMatch.create(eng, 1, instructions=home, pause_at_breaks=False, pause_on_key_events=False)
        live.play_to_end()
        r = live.result()
        for side, team in (("home", r.home), ("away", r.away)):
            tot[f"{side}_shots"] += team.stats.shots
            tot[f"{side}_cards"] += team.stats.yellow_cards + team.stats.red_cards
            tot["injuries"] += team.stats.injuries
            tot["reds"] += team.stats.red_cards
        energy += [p.energy for p in r.home.players
                   if p.entered_minute == 0 and p.left_minute == 90 and p.role is not Position.GK]
    out = {k: v / len(STAT_SEEDS) for k, v in tot.items()}
    out["cards"] = out["home_cards"] + out["away_cards"]
    out["home_energy"] = sum(energy) / len(energy)
    return out


def test_all_out_attack_creates_more_shots_but_concedes_more():
    base, attack = season(BALANCED), season(ATTACK)
    msg = f"dengeli {base}, ofansif {attack}"
    assert attack["home_shots"] > base["home_shots"] * 1.15, msg
    assert attack["away_shots"] > base["away_shots"] * 1.06, msg


def test_park_the_bus_takes_and_concedes_fewer_shots():
    base, bus = season(BALANCED), season(BUS)
    msg = f"dengeli {base}, defansif {bus}"
    assert bus["home_shots"] < base["home_shots"] * 0.82, msg
    assert bus["away_shots"] < base["away_shots"] * 0.94, msg


def test_all_out_attack_tires_players_faster():
    base, attack, bus = season(BALANCED), season(ATTACK), season(BUS)
    assert attack["home_energy"] < base["home_energy"] - 3, (base["home_energy"], attack["home_energy"])
    assert bus["home_energy"] > base["home_energy"] + 1, (base["home_energy"], bus["home_energy"])


def test_hard_tackling_doubles_cards_and_raises_injuries_calm_lowers_cards():
    normal, hard, calm = season(BALANCED), season(HARD, HARD), season(CALM, CALM)
    msg = f"normal {normal}, sert {hard}, sakin {calm}"
    assert 1.7 < hard["cards"] / normal["cards"] < 2.6, msg
    assert hard["reds"] > normal["reds"] * 2, msg
    assert hard["injuries"] > normal["injuries"] * 1.4, msg
    assert calm["cards"] < normal["cards"] * 0.75, msg
    assert calm["injuries"] <= normal["injuries"], msg


@pytest.mark.parametrize("hard_side", ["home", "away"])
def test_one_sided_hard_tackling_takes_the_larger_card_share(hard_side):
    stats = season(HARD, BALANCED) if hard_side == "home" else season(BALANCED, HARD)
    other = "away" if hard_side == "home" else "home"
    share = stats[f"{hard_side}_cards"] / stats["cards"]
    assert share > 0.6, stats
    assert stats[f"{hard_side}_cards"] > 1.6 * season(BALANCED)[f"{hard_side}_cards"], stats
    assert stats[f"{other}_cards"] < 1.25 * season(BALANCED)[f"{other}_cards"], stats


# ===========================================================================
# 5) Asistan: auto_subs=False
# ===========================================================================

def test_auto_subs_off_stops_fatigue_subs_but_assistant_still_replaces_injuries():
    cfg = EngineConfig(base_injury=0.02)
    tired_with_assistant = tired_opponent = replaced = 0
    for seed in range(30):
        reference = new_engine(seed, cfg=cfg).simulate()
        tired_with_assistant += sum(1 for e in reference.events if e.team_id == 1 and "yoruldu" in e.description)

        live = new_live(seed, cfg=cfg, auto_subs=False)
        assert live.managed_team.auto_subs is False and live.opponent_team.auto_subs is True
        live.play_to_end()
        r = live.result()
        assert not any(e.team_id == 1 and "yoruldu" in e.description for e in r.events)
        tired_opponent += sum(1 for e in r.events if e.team_id == 2 and "yoruldu" in e.description)
        for i, e in enumerate(r.events):
            if e.type is not EventType.INJURY or e.team_id != 1:
                continue
            nxt = r.events[i + 1]
            assert nxt.type is EventType.SUBSTITUTION and nxt.team_id == 1, (seed, nxt.description)
            if nxt.player_id is None or "eldiven" in nxt.description:    # hak/kulube bitti ya da acil kaleci
                assert "kalmadı" in nxt.description or "yok" in nxt.description
                continue
            entered = next(p for p in r.home.players if p.id == nxt.player_id)
            assert entered.entered_minute == e.minute and entered.id != e.player_id
            replaced += 1
    assert tired_with_assistant >= 15 and tired_opponent >= 15 and replaced >= 8, \
        (tired_with_assistant, tired_opponent, replaced)


# ===========================================================================
# 6) LiveMatch kontrolcusu
# ===========================================================================

def test_auto_pause_at_half_time_and_resume():
    live = new_live(18, breaks=True)
    assert live.phase_label == PHASE_LABELS[MatchPhase.NOT_STARTED] and live.clock == "0'"
    events = live.run()
    assert live.paused and live.phase is MatchPhase.HALF_TIME and live.pause_reason == BREAK_EVENTS[EventType.HALF_TIME]
    assert events[-1].type is EventType.HALF_TIME and live.phase_label == "Devre Arası"
    assert live.clock == f"45+{live.engine.first_half_added}'"
    assert live.run() == [] and live.tick() == [] and live.phase is MatchPhase.HALF_TIME
    live.resume()
    live.run()
    assert live.finished and not live.paused and live.phase_label == "Maç Sonu"


def test_auto_pause_at_extra_time_breaks():
    seed = knockout_seeds()["penalties"][0]
    live = new_live(seed, knockout=KnockoutRule(), breaks=True)
    stops = []
    while not live.finished:
        live.run()
        if live.paused:
            stops.append((live.phase, live.pause_reason, live.engine.in_break))
            live.resume()
    assert stops == [(MatchPhase.HALF_TIME, "Devre arası", True),
                     (MatchPhase.EXTRA_TIME_BREAK, "Uzatmalar öncesi mola", True),
                     (MatchPhase.EXTRA_TIME_HALF_TIME, "Uzatmaların devre arası", True)]
    assert live.result().shootout is not None


def test_auto_pause_only_for_managed_team_injuries_and_red_cards():
    cfg = EngineConfig(base_injury=0.03, base_card=0.08, straight_red_share=0.3,
                       straight_red_share_v2=0.3)
    own = {EventType.INJURY: 0, EventType.RED_CARD: 0}
    opponent_without_pause = 0
    for seed in range(8):
        live = new_live(seed, cfg=cfg, key_events=True)
        while not live.finished:
            if live.paused:
                live.resume()
            events = live.tick()
            if live.finished:
                break
            mine = [e for e in events if e.type in KEY_EVENTS and e.team_id == 1 and e.player]
            theirs = [e for e in events if e.type in KEY_EVENTS and e.team_id == 2]
            if mine:
                assert live.paused
                assert live.pause_reason == f"{KEY_EVENTS[mine[-1].type]}: {mine[-1].player}"
                for e in mine:
                    own[e.type] += 1
            else:
                assert not live.paused, [e.description for e in events]
                opponent_without_pause += len(theirs)
    assert own[EventType.INJURY] >= 3 and own[EventType.RED_CARD] >= 3 and opponent_without_pause >= 6, \
        (own, opponent_without_pause)


def test_no_auto_pause_when_flags_off_and_spectator_mode():
    cfg = EngineConfig(base_injury=0.03, base_card=0.08, straight_red_share=0.3,
                       straight_red_share_v2=0.3)
    live = new_live(21, cfg=cfg, knockout=KnockoutRule())
    events = live.run()
    assert live.finished and not live.paused and events == live.engine.events
    r = live.result()
    assert type_count(r, EventType.INJURY, 1) + type_count(r, EventType.RED_CARD, 1) > 0
    assert type_count(r, EventType.HALF_TIME) == 1

    watch = LiveMatch.create(new_engine(21, cfg=cfg), None)
    assert watch.managed_team is None and watch.opponent_team is None
    watch.run()
    assert watch.finished and not watch.paused
    assert watch.formation == "" and watch.instructions == TeamInstructions()
    for action in (lambda: watch.substitute(101, 111), lambda: watch.change_formation("5-3-2"),
                   lambda: watch.set_instructions("BALANCED", "NORMAL"), watch.sub_status,
                   watch.lineup_rows, watch.bench_rows):
        with pytest.raises(InterventionError, match="yönettiğin"):
            action()


def test_play_to_end_finishes_from_a_pause():
    reference = new_engine(22).simulate()
    live = LiveMatch.create(new_engine(22), 1)
    live.run()
    assert live.paused
    before = len(live.engine.events)
    events = live.play_to_end()
    assert live.finished and not live.paused and live.pause_reason is None
    assert events == live.engine.events[before:] and events[-1].type is EventType.FULL_TIME
    assert fingerprint(live.result()) == fingerprint(reference)
    assert live.play_to_end() == [] and live.tick() == [] and live.run() == []


def test_sub_status_text_and_rule_configs():
    base = EngineConfig(base_card=0.01)
    assert engine_config_for(SubRule.STANDARD) == EngineConfig()
    assert engine_config_for(SubRule.STANDARD, base) is base
    five, three = engine_config_for(SubRule.FIVE_IN_THREE, base), engine_config_for(SubRule.CLASSIC_THREE, base)
    assert (five.max_subs, five.sub_windows, five.base_card) == (5, 3, 0.01)
    assert (three.max_subs, three.sub_windows, three.base_card) == (3, None, 0.01)
    assert base.sub_windows is None and base.max_subs == 5                     # taban degismez
    windowed = EngineConfig(max_subs=4, sub_windows=2)
    assert engine_config_for(SubRule.CLASSIC_THREE, windowed).sub_windows is None

    for rule, text in ((SubRule.STANDARD, "0/5 değişiklik"), (SubRule.FIVE_IN_THREE, "0/5 değişiklik · pencere 0/3"),
                       (SubRule.CLASSIC_THREE, "0/3 değişiklik")):
        live = new_live(23, rule=rule)
        status = live.sub_status()
        assert status.text() == text and status.block is None and status.remaining == status.limit
        assert (status.windows_used is None) == (rule is not SubRule.FIVE_IN_THREE)

    live = new_live(24, cfg=QUIET)
    team = live.managed_team
    pause_at(live, 50)
    for sub in sorted(team.bench, key=lambda p: p.id):
        out = next(p for p in team.on_pitch if p.role is (sub.position if sub.position is not Position.GK
                                                            else Position.GK))
        live.substitute(out.id, sub.id)
    status = live.sub_status()
    assert (status.used, status.block) == (4, "kulübede oyuncu kalmadı") and status.remaining == 1


def test_lineup_and_bench_rows():
    live = new_live(25, cfg=QUIET, auto_subs=False)
    team = live.managed_team
    pause_at(live, 70)
    rows = live.lineup_rows()
    assert len(rows) == 11 and {r["id"] for r in rows} == {p.id for p in team.on_pitch}
    assert [r["Görev"] for r in rows] == ["GK"] + ["DEF"] * 4 + ["MID"] * 4 + ["FWD"] * 2
    assert set(rows[0]) == {"id", "Oyuncu", "Görev", "Mevki", "OVR", "Kondisyon", "Kart", "Gol", "Not"}
    by_id = {p.id: p for p in team.players}
    for row in rows:
        p = by_id[row["id"]]
        assert (row["Oyuncu"], row["Mevki"], row["OVR"], row["Kondisyon"], row["Gol"]) == \
            (p.name, p.position.value, p.overall, round(p.energy), p.goals)
        assert row["Kart"] == "🟨" * p.yellow_cards
        assert ("yorgun" in row["Not"]) == (p.energy < 60)
    for role in ("DEF", "MID", "FWD"):
        energies = [r["Kondisyon"] for r in rows if r["Görev"] == role]
        assert energies == sorted(energies)                            # ayni gorevde en yorgun once

    bench = live.bench_rows()
    assert [r["id"] for r in bench] == [p.id for p in sorted(team.bench, key=lambda p: (
        ["GK", "DEF", "MID", "FWD"].index(p.position.value), -p.effective_power, p.id))]
    assert [r["Mevki"] for r in bench] == ["GK", "MID", "FWD", "FWD"]
    assert all(r["Görev"] == r["Mevki"] and r["Not"] == "" and r["Kondisyon"] == 100 for r in bench)

    live.change_formation("5-3-2")
    rows = live.lineup_rows()
    assert [r["Görev"] for r in rows] == ["GK"] + ["DEF"] * 5 + ["MID"] * 3 + ["FWD"] * 2
    off = [r for r in rows if "mevki dışı" in r["Not"]]
    assert len(off) == 1 and (off[0]["Görev"], off[0]["Mevki"]) == ("DEF", "MID")


def _expected_clock(eng: MatchEngine) -> str:
    return f"{eng.minute}+{eng.added}'" if eng.added else f"{eng.minute}'"


@pytest.mark.parametrize("kind", ["league", "normal", "extra_time", "penalties"])
def test_clock_elapsed_and_progress_never_go_backwards(kind):
    if kind == "league":
        live = new_live(26)
    else:
        live = new_live(knockout_seeds()[kind][0], knockout=KnockoutRule())
    eng = live.engine
    assert (live.clock, live.elapsed, live.progress) == ("0'", 0, 0.0)
    prev_elapsed, prev_progress = 0, 0.0
    while not live.finished:
        live.tick()
        elapsed, progress = live.elapsed, live.progress
        assert elapsed >= prev_elapsed, (live.clock, elapsed, prev_elapsed)
        assert progress >= prev_progress, (live.clock, eng.phase, progress, prev_progress)
        if not live.finished:
            assert live.clock == _expected_clock(eng)
            assert 0.0 <= progress <= 1.0
            if progress == 1.0:                                        # yalnizca son oyun dakikasi oynandiginda
                assert eng.phase in PLAY_PHASES and eng.minute in (90, 120) and eng.added > 0, live.clock
            if (eng.minute, eng.added) == (90, 0):
                assert progress < 0.97, progress                       # uzatma dakikalari henuz oynanmadi
        prev_elapsed, prev_progress = elapsed, progress
    result = live.result()
    assert live.progress == 1.0 and live.elapsed == result.total_minutes
    assert result.decided_by == ("normal" if kind == "league" else kind)
    assert live.clock == _expected_clock(eng)


# ===========================================================================
# 7) 14A: yeni otomatik duraklama nedenleri (karar anlari) ve topla oynama gunlugu
# ===========================================================================

NEW_FLAGS = ("pause_on_opponent_tactics", "pause_on_two_goals", "pause_on_tired", "pause_for_assistant")


def _stops(live: LiveMatch) -> list[tuple[str | None, str | None, int]]:
    """Maci sonuna kadar oynatir (her duraklamada devam); (tur, sebep, dakika) listesi."""
    out = []
    while not live.finished:
        live.run()
        if live.paused:
            out.append((live.pause_kind, live.pause_reason, live.engine.minute))
            live.resume()
    return out


def _live_with(seed: int, flag: str | None, *, home: MatchTeam | None = None,
               cfg: EngineConfig | None = None) -> LiveMatch:
    """Yalnizca verilen yeni neden acik (molalar ve kritik olaylar kapali): duraklamalarin hepsi o nedenden."""
    flags = {name: name == flag for name in NEW_FLAGS}
    return LiveMatch.create(new_engine(seed, home=home, cfg=cfg), 1, pause_at_breaks=False,
                            pause_on_key_events=False, **flags)


def test_new_pause_flags_are_off_by_default_and_ui_defaults_live_here():
    live = LiveMatch.create(new_engine(1), 1)
    assert not any(getattr(live, name) for name in NEW_FLAGS)
    assert AUTO_PAUSE_DEFAULTS == {"pause_on_opponent_tactics": True, "pause_on_two_goals": True,
                                   "pause_on_tired": False, "pause_for_assistant": False}
    live.run()
    assert live.pause_kind == "break" and live.pause_reason == "Devre arası"
    live.resume()
    assert live.pause_kind is None and live.pause_reason is None
    live.pause()
    assert live.pause_kind == "manual"


def test_two_goals_in_ten_minutes_pauses_and_needs_two_new_goals():
    triggered = 0
    for seed in range(40):
        on = _live_with(seed, "pause_on_two_goals", home=make_team(1, "Ev", 66))
        stops = _stops(on)
        conceded = on.conceded
        assert conceded == sorted(conceded) and len(conceded) == on.result().away_score
        assert all(kind == "two_goals" and reason == TWO_GOALS_TEXT for kind, reason, _ in stops)
        close_pair = any(b - a <= 10 for a, b in zip(conceded, conceded[1:], strict=False))
        assert bool(stops) == close_pair, (seed, conceded)
        assert len(stops) <= len(conceded) // 2
        off = _live_with(seed, None, home=make_team(1, "Ev", 66))
        assert _stops(off) == []
        assert fingerprint(on.result()) == fingerprint(off.result())       # RNG sirasi degismez
        triggered += len(stops)
    assert triggered >= 3


def test_opponent_tactical_change_pauses_only_for_the_opponent():
    cfg = EngineConfig(ai_tactics=True)
    total = 0
    for seed in range(12):
        on = _live_with(seed, "pause_on_opponent_tactics", cfg=cfg)
        stops = _stops(on)
        result = on.result()
        theirs = [e for e in result.events if e.type is EventType.TACTICAL_CHANGE and e.team_id == 2]
        assert not [e for e in result.events if e.type is EventType.TACTICAL_CHANGE and e.team_id == 1]
        assert all(kind == "opponent_tactics" and reason.startswith("Rakip taktik değiştirdi")
                   for kind, reason, _ in stops)
        assert bool(stops) == bool(theirs) and len(stops) <= len(theirs)
        off = _live_with(seed, None, cfg=cfg)
        assert _stops(off) == []
        assert fingerprint(result) == fingerprint(off.result())
        total += len(stops)
    assert total >= 3


def test_tired_starter_pauses_once_per_player():
    for seed in (3, 8, 11):
        on = _live_with(seed, "pause_on_tired")
        stops = _stops(on)
        assert stops and all(kind == "tired" and reason.startswith("Yorgunluk: ") for kind, reason, _ in stops)
        names = [reason.split(": ", 1)[1].split(" (")[0] for _, reason, _ in stops]
        assert len(names) == len(set(names)) == len(on.tired_seen)             # oyuncu basina bir kez
        starters = {p.name for p in on.managed_team.players if p.entered_minute == 0}
        assert set(names) <= starters
        off = _live_with(seed, None)
        assert _stops(off) == []
        assert fingerprint(on.result()) == fingerprint(off.result()) == fingerprint(new_engine(seed).simulate())


def test_assistant_note_pauses_at_15_30_60_75():
    on = _live_with(4, "pause_for_assistant")
    stops = _stops(on)
    assert [(kind, minute) for kind, _, minute in stops] == [("assistant", m) for m in ASSISTANT_MINUTES]
    assert stops[0][1] == "Asistan notu (15')"
    off = _live_with(4, None)
    assert _stops(off) == []
    assert fingerprint(on.result()) == fingerprint(off.result()) == fingerprint(new_engine(4).simulate())


def test_all_new_reasons_together_keep_the_match_identical():
    for seed in (0, 5, 9):
        live = LiveMatch.create(new_engine(seed), 1, **{name: True for name in NEW_FLAGS})
        stops = _stops(live)
        assert {kind for kind, _, _ in stops} >= {"break", "assistant"}
        assert fingerprint(live.result()) == fingerprint(new_engine(seed).simulate())


def test_possession_log_follows_every_step_and_play_to_end():
    live = LiveMatch.create(new_engine(6), 1)
    live.run()                                                              # devre arasi
    elapsed = [e for e, _, _ in live.possession_log]
    assert len(elapsed) > 40 and elapsed == sorted(set(elapsed))
    live.play_to_end()
    result = live.result()
    last = live.possession_log[-1]
    assert last[0] == result.total_minutes
    assert (last[1], last[2]) == (result.home.stats.possession_weight, result.away.stats.possession_weight)
    share = round(100 * last[1] / (last[1] + last[2]))
    assert (share, 100 - share) == result.possession_share()
    assert fingerprint(result) == fingerprint(new_engine(6).simulate())
