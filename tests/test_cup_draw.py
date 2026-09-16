"""
Devler Arenasi kural testleri (8. Asama): cup_draw.py katilim, torba, takvim, interaktif
kura butunlugu, tur ilerlemesi, grup fiksturu ve averaj kurallari.
Saf testler: veritabani ve Streamlit gerektirmez.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from itertools import combinations, permutations
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cup_draw as cd  # noqa: E402
from cup_draw import (  # noqa: E402
    CupFormat,
    CupTeam,
    DrawComplete,
    DrawSession,
    Stage,
)

KO, GR = CupFormat.KNOCKOUT, CupFormat.GROUPS


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def team(tid: int, league: str = "L", coef: float | None = None, name: str | None = None) -> CupTeam:
    return CupTeam(id=tid, name=name or f"Takım {tid}", league=league, coefficient=100.0 - tid if coef is None else coef)


def teams_from_leagues(leagues: list[str], start: int = 1) -> list[CupTeam]:
    """Lig listesi -> takimlar; katsayilar azalan, boylece torba sirasi = liste sirasi."""
    return [team(start + i, lg, coef=1000.0 - i) for i, lg in enumerate(leagues)]


def pots_from_leagues(fmt: CupFormat, pot_leagues: list[list[str]]) -> list[list[CupTeam]]:
    flat = [lg for pot in pot_leagues for lg in pot]
    teams = teams_from_leagues(flat)
    pots = cd.make_pots(teams, fmt)
    assert [[t.league for t in pot] for pot in pots] == pot_leagues
    return pots


def random_leagues(rng: random.Random, size: int) -> list[str]:
    """Carpik dagilimli rastgele ligler: az sayida baskin lig + tekil ligler."""
    pool_size = rng.randint(2, 9)
    pool = [f"L{i}" for i in range(pool_size)]
    weights = [rng.random() ** 2 + 0.02 for _ in pool]
    return [rng.choices(pool, weights)[0] for _ in range(size)]


def capped_group_leagues(rng: random.Random) -> list[str]:
    """Cogu lig tam 4 takim (sinirda uygulanabilir), arada 5'li (uygulanamaz) ligler."""
    leagues: list[str] = []
    index = 0
    while len(leagues) < 16:
        cap = 5 if rng.random() < 0.08 else 4
        count = min(rng.randint(1, cap), 16 - len(leagues))
        leagues += [f"L{index}"] * count
        index += 1
    rng.shuffle(leagues)
    return leagues


def hall_knockout(unseeded: list[str], seeded: list[str]) -> bool:
    """
    Bagimsiz kontrol (Hall): her lig kumesi L icin, L liglerindeki seri basi olmayanlarin
    komsulari (en az bir L liginden farkli ligdeki seri basilari) en az o kadar olmali.
    """
    if len(unseeded) != len(seeded):
        return False
    leagues = sorted(set(unseeded))
    for r in range(1, len(leagues) + 1):
        for subset in combinations(leagues, r):
            chosen = set(subset)
            need = sum(lg in chosen for lg in unseeded)
            have = sum(any(lg != x for x in chosen) for lg in seeded)
            if need > have:
                return False
    return True


def brute_groups_completable(groups: list[list[CupTeam | None]], pots: list[list[CupTeam]]) -> bool:
    """Bagimsiz kaba kuvvet: torba torba permutasyon (bos baslangicta torba 0 simetrik)."""
    groups = [list(g) for g in groups]
    placed = {t.id for g in groups for t in g if t is not None}

    def rec(p: int) -> bool:
        if p == 4:
            return True
        open_groups = [g for g in range(4) if groups[g][p] is None]
        remaining = [t for t in pots[p] if t.id not in placed]
        all_empty = all(all(x is None for x in g) for g in groups)
        orders = [tuple(remaining)] if all_empty else permutations(remaining)
        for order in orders:
            if all(
                order[i].league not in {t.league for t in groups[g] if t is not None}
                for i, g in enumerate(open_groups)
            ):
                for i, g in enumerate(open_groups):
                    groups[g][p] = order[i]
                if rec(p + 1):
                    return True
                for g in open_groups:
                    groups[g][p] = None
        return False

    return rec(0)


def draw_signature(session: DrawSession) -> list[tuple]:
    return [(s.team_id, s.pot, s.slot, s.partner_id) for s in session.steps]


def check_knockout_complete(session: DrawSession, pots: list[list[CupTeam]]) -> None:
    by_id = {t.id: t for pot in pots for t in pot}
    seeded = {t.id for t in pots[0]}
    unseeded = {t.id for t in pots[1]}
    pairs = session.pairs()
    assert len(pairs) == len(pots[0])
    ids = [tid for pair in pairs for tid in pair]
    assert sorted(ids) == sorted(by_id), "her takim tam bir kez"
    for first_home, second_home in pairs:
        assert first_home in unseeded and second_home in seeded, "bir seri basi + bir seri basi olmayan"
        if session.protected:
            assert by_id[first_home].league != by_id[second_home].league
    # adim yapisi: 2. torba eslesmeyi acar, 1. torba tamamlar
    for i, step in enumerate(session.steps):
        assert step.number == i + 1
        assert step.slot == i // 2
        if i % 2 == 0:
            assert step.pot == 1 and step.partner_id is None
        else:
            assert step.pot == 0 and step.partner_id == session.steps[i - 1].team_id
    with pytest.raises(DrawComplete):
        session.draw_next()


def check_groups_complete(session: DrawSession, pots: list[list[CupTeam]]) -> None:
    by_id = {t.id: t for pot in pots for t in pot}
    groups = session.groups()
    assert len(groups) == 4 and all(len(g) == 4 for g in groups)
    assert sorted(tid for g in groups for tid in g) == sorted(by_id)
    for group in groups:
        for p, tid in enumerate(group):
            assert by_id[tid] in pots[p], "her grupta her torbadan bir takim"
        if session.protected:
            leagues = [by_id[tid].league for tid in group]
            assert len(set(leagues)) == 4, f"ayni lig ayni grupta: {leagues}"
    for i, step in enumerate(session.steps):
        assert step.pot == i // 4 and step.partner_id is None
    with pytest.raises(DrawComplete):
        session.draw_next()


# ---------------------------------------------------------------------------
# Katilim, format, torbalar
# ---------------------------------------------------------------------------

def test_cup_size_for():
    assert [cd.cup_size_for(n) for n in (40, 16, 15, 9, 8, 7, 0)] == [16, 16, 8, 8, 8, 0, 0]


def test_qualify_tiers_follow_league_strength():
    a = [team(1, "A", 90), team(2, "A", 80), team(3, "A", 70), team(4, "A", 60)]          # ort. 75
    b = [team(11, "B", 95), team(12, "B", 50), team(13, "B", 40), team(14, "B", 30)]      # ort. 53.75
    c = [team(21, "C", 60), team(22, "C", 60), team(23, "C", 60), team(24, "C", 60),      # ort. 60
         team(25, "C", 999)]                                                              # 5. takim sayilmaz
    picked = cd.qualify([b, c, a], slots=8)
    assert [t.id for t in picked] == [1, 21, 11, 2, 22, 12, 3, 23]


def test_qualify_uneven_leagues_name_tiebreak_and_errors():
    short = [team(1, "Zeta", 50)]
    x = [team(10, "Beta", 50), team(11, "Beta", 50), team(12, "Beta", 50)]
    y = [team(20, "Alfa", 50), team(21, "Alfa", 50), team(22, "Alfa", 50)]
    picked = cd.qualify([short, x, y], slots=7)
    # esit guc -> lig adi: Alfa, Beta, Zeta; Zeta 2. kademede yok
    assert [t.id for t in picked] == [20, 10, 1, 21, 11, 22, 12]
    assert len(cd.qualify([short, x, y, []], slots=3)) == 3
    with pytest.raises(ValueError):
        cd.qualify([short, x], slots=8)
    with pytest.raises(ValueError):
        cd.qualify([x, [team(10, "Q", 1)]], slots=2)            # ayni id iki kez


def test_formats_and_stages():
    assert cd.formats_for(16) == [KO, GR]
    assert cd.formats_for(8) == [KO]
    assert cd.formats_for(12) == [] and cd.formats_for(0) == []
    assert cd.stages_for(KO, 16) == [Stage.R16, Stage.QF, Stage.SF, Stage.FINAL]
    assert cd.stages_for(KO, 8) == [Stage.QF, Stage.SF, Stage.FINAL]
    assert cd.stages_for(GR, 16) == [Stage.GROUP, Stage.QF, Stage.SF, Stage.FINAL]
    with pytest.raises(ValueError):
        cd.stages_for(GR, 8)
    with pytest.raises(ValueError):
        cd.stages_for(KO, 12)
    assert cd.TWO_LEGGED == {Stage.R16, Stage.QF, Stage.SF} and Stage.FINAL not in cd.TWO_LEGGED
    assert cd.STAGE_LABELS[Stage.QF] == "Çeyrek Final" and set(cd.STAGE_LABELS) == set(Stage)
    assert cd.slot_title(KO, 2) == "Eşleşme 3" and cd.slot_title(GR, 1) == "Grup B"


def test_make_pots():
    teams = [team(i, "L", coef=float(i % 5), name=f"T{i:02d}") for i in range(1, 17)]
    random.Random(3).shuffle(teams)
    ko = cd.make_pots(teams, KO)
    flat = [t for pot in ko for t in pot]
    assert [len(p) for p in ko] == [8, 8]
    assert flat == sorted(teams, key=lambda t: (-t.coefficient, t.name))
    gr = cd.make_pots(teams, GR)
    assert [len(p) for p in gr] == [4, 4, 4, 4] and [t for p in gr for t in p] == flat
    assert [len(p) for p in cd.make_pots(teams[:8], KO)] == [4, 4]
    with pytest.raises(ValueError):
        cd.make_pots(teams[:8], GR)
    with pytest.raises(ValueError):
        cd.make_pots(teams[:12], KO)
    with pytest.raises(ValueError):
        cd.make_pots(teams[:15] + [teams[0]], KO)


# ---------------------------------------------------------------------------
# Takvim
# ---------------------------------------------------------------------------

EXPECTED_PLAN = {
    (KO, 16): [("R16", 1), ("R16", 2), ("QF", 1), ("QF", 2), ("SF", 1), ("SF", 2), ("FINAL", 1)],
    (KO, 8): [("QF", 1), ("QF", 2), ("SF", 1), ("SF", 2), ("FINAL", 1)],
    (GR, 16): [("GROUP", r) for r in range(1, 7)]
    + [("QF", 1), ("QF", 2), ("SF", 1), ("SF", 2), ("FINAL", 1)],
}


@pytest.mark.parametrize(("fmt", "size"), list(EXPECTED_PLAN))
def test_build_calendar(fmt, size):
    plan = EXPECTED_PLAN[(fmt, size)]
    n = len(plan)
    tour = cd.build_calendar(fmt, size, league_weeks=34, tournament_only=True)
    assert [(m.stage.value, m.leg) for m in tour] == plan
    assert [m.number for m in tour] == list(range(1, n + 1))
    assert [m.week for m in tour] == list(range(1, n + 1))
    # lig haftasi yetersiz -> ardisik
    assert [m.week for m in cd.build_calendar(fmt, size, n - 1, False)] == list(range(1, n + 1))
    for league_weeks in (n, n + 1, 18, 30, 34, 38, 46):
        spread = cd.build_calendar(fmt, size, league_weeks, tournament_only=False)
        weeks = [m.week for m in spread]
        assert [(m.stage.value, m.leg) for m in spread] == plan
        assert all(b > a for a, b in zip(weeks, weeks[1:], strict=False)), weeks
        assert weeks[0] >= 1 and weeks[-1] == league_weeks
        assert spread[-1].stage is Stage.FINAL


def test_build_calendar_formula_example():
    weeks = [m.week for m in cd.build_calendar(KO, 16, 34, False)]
    assert weeks == [5, 10, 15, 20, 25, 30, 34]


# ---------------------------------------------------------------------------
# Kura butunlugu
# ---------------------------------------------------------------------------

def test_knockout_draw_integrity_random_distributions():
    rng = random.Random(2026)
    seen_protected = seen_relaxed = 0
    for seed in range(300):
        size = 16 if seed % 3 else 8
        pots = cd.make_pots(teams_from_leagues(random_leagues(rng, size)), KO)
        session = DrawSession(KO, pots, seed=seed)
        feasible = hall_knockout([t.league for t in pots[1]], [t.league for t in pots[0]])
        assert session.protected is feasible
        # Konig: iki parcali cizgede her lig en fazla size/2 takimsa eslestirme hep vardir
        assert feasible is (max(Counter(t.league for p in pots for t in p).values()) <= size // 2)
        assert (session.relaxed_note is None) is feasible
        while not session.complete:
            step = session.draw_next()
            if session.protected:
                # bagimsiz kontrol: her adimdan sonra kura hala tamamlanabilir
                pending = [session.team(t[0]).league for t in session.slots() if t[0] is not None and t[1] is None]
                assert hall_knockout(
                    [t.league for t in session.remaining(1)] + pending,
                    [t.league for t in session.remaining(0)],
                ), (seed, step)
        check_knockout_complete(session, pots)
        seen_protected += feasible
        seen_relaxed += not feasible
    assert seen_protected > 100 and seen_relaxed > 5


def test_knockout_barely_feasible_adversarial():
    rng = random.Random(7)
    for seed in range(200):
        seeded_x = rng.randint(1, 7)
        seeded = ["X"] * seeded_x + [f"S{i}" for i in range(8 - seeded_x)]
        unseeded = ["X"] * (8 - seeded_x) + [f"U{i}" for i in range(seeded_x)]
        if seed % 2:                      # iki lig birden sinirda (8'er takim)
            seeded = ["X", "X", "X", "Y", "Y", "Y", "Y", "Y"]
            unseeded = ["X", "X", "X", "X", "X", "Y", "Y", "Y"]
        pots = pots_from_leagues(KO, [seeded, unseeded])
        session = DrawSession(KO, pots, seed=f"adv-{seed}")
        assert session.protected, seed
        session.draw_all()
        check_knockout_complete(session, pots)


def test_knockout_infeasible_relaxes_with_note():
    seeded = ["X"] * 5 + ["A", "B", "C"]
    unseeded = ["X"] * 4 + ["D", "E", "F", "G"]            # 9 takim ayni ligden
    pots = pots_from_leagues(KO, [seeded, unseeded])
    session = DrawSession(KO, pots, seed=1)
    assert session.protected is False
    assert session.relaxed_note and "koruma" in session.relaxed_note.lower()
    session.draw_all()
    check_knockout_complete(session, pots)
    # 8'lik elemede ayni ligden 5 takim
    pots2 = pots_from_leagues(KO, [["X", "X", "X", "A"], ["X", "X", "B", "C"]])
    assert DrawSession(KO, pots2, seed=1).protected is False
    assert cd.protection_feasible(KO, pots2) is False and cd.protection_feasible(KO, pots) is False
    assert cd.protection_feasible(KO, pots_from_leagues(KO, [["X", "X", "Y", "Y"], ["Y", "Y", "X", "X"]]))


def test_protection_disabled_on_request():
    pots = pots_from_leagues(KO, [["X"] * 4, ["X"] * 4])
    session = DrawSession(KO, pots, seed=5, protect_leagues=False)
    assert session.protected is False and session.relaxed_note is None
    session.draw_all()
    check_knockout_complete(session, pots)


def test_groups_draw_integrity_random_distributions():
    rng = random.Random(99)
    seen = Counter()
    for seed in range(220):
        leagues = random_leagues(rng, 16) if seed % 4 == 0 else capped_group_leagues(rng)
        pots = cd.make_pots(teams_from_leagues(leagues), GR)
        session = DrawSession(GR, pots, seed=seed)
        empty = [[None] * 4 for _ in range(4)]
        feasible = brute_groups_completable(empty, pots)
        assert session.protected is feasible, seed
        assert feasible is (max(Counter(leagues).values()) <= 4)      # Konig kenar boyama
        session.draw_all()
        check_groups_complete(session, pots)
        seen[feasible] += 1
    assert seen[True] > 60 and seen[False] > 5


def test_groups_first_group_rule_with_independent_oracle():
    """Her top, kurallara uyan VE tamamlanabilir ilk gruba gider (bagimsiz kaba kuvvetle)."""
    rng = random.Random(11)
    checked = 0
    for seed in range(60):
        pools = [["A", "A", "B", "B"], ["X", "X", "C", "C"], ["A", "A", "X", "X"], ["P", "Q", "R", "S"]]
        leagues = [lg for pot in pools for lg in pot] if seed % 3 == 0 else capped_group_leagues(rng)
        pots = cd.make_pots(teams_from_leagues(leagues), GR)
        session = DrawSession(GR, pots, seed=seed)
        if not session.protected:
            continue
        by_id = {t.id: t for pot in pots for t in pot}
        while not session.complete:
            before = [[by_id[x] if x else None for x in g] for g in session.slots()]
            step = session.draw_next()
            ball = by_id[step.team_id]
            expected = None
            for g in range(4):
                if before[g][step.pot] is not None:
                    continue
                if ball.league in {t.league for t in before[g] if t is not None}:
                    continue
                trial = [list(x) for x in before]
                trial[g][step.pot] = ball
                if brute_groups_completable(trial, pots):
                    expected = g
                    break
            assert step.slot == expected, (seed, step)
        check_groups_complete(session, pots)
        checked += 1
    assert checked > 30


def test_groups_barely_feasible_adversarial():
    # Tek cozum ailesi: 2. torbadaki X'ler B gruplarina gitmek zorunda
    layout = [["A", "A", "B", "B"], ["X", "X", "C", "C"], ["A", "A", "X", "X"], ["P", "Q", "R", "S"]]
    pots = pots_from_leagues(GR, layout)
    for seed in range(150):
        session = DrawSession(GR, pots, seed=seed)
        assert session.protected
        session.draw_all()
        check_groups_complete(session, pots)
    # iki torbada ayni lig ciftleri: yine tamamlanmali
    layout2 = [["A", "A", "B", "B"], ["X", "X", "Y", "Y"], ["A", "A", "X", "X"], ["B", "B", "Y", "Y"]]
    pots2 = pots_from_leagues(GR, layout2)
    for seed in range(100):
        session = DrawSession(GR, pots2, seed=seed)
        assert session.protected
        session.draw_all()
        check_groups_complete(session, pots2)


def test_groups_infeasible_relaxes_and_unconstrained_goes_in_order():
    pots = pots_from_leagues(GR, [["X", "X", "X", "A"], ["X", "X", "B", "C"], ["D", "E", "F", "G"],
                                  ["H", "I", "J", "K"]])       # 5 X, 4 grup
    session = DrawSession(GR, pots, seed=4)
    assert not session.protected and session.relaxed_note
    session.draw_all()
    check_groups_complete(session, pots)
    # tum ligler farkli: her torba A, B, C, D sirasiyla dolar
    distinct = cd.make_pots(teams_from_leagues([f"L{i}" for i in range(16)]), GR)
    s2 = DrawSession(GR, distinct, seed="sira")
    s2.draw_all()
    assert [st.slot for st in s2.steps] == [0, 1, 2, 3] * 4


def test_draw_never_raises_from_tampered_infeasible_state():
    pots = pots_from_leagues(KO, [["X", "X", "X", "X"], ["X", "A", "B", "C"]])
    state = DrawSession(KO, pots, seed=1, protect_leagues=False).to_state()
    x_unseeded = pots[1][0]
    state["protect_leagues"], state["protected"], state["relaxed_note"] = True, True, None
    state["steps"] = [{"number": 1, "team_id": x_unseeded.id, "team_name": x_unseeded.name,
                       "pot": 1, "slot": 0, "partner_id": None}]
    session = DrawSession.from_state(state)
    session.draw_all()                     # kilitlenmez: korumayi gevsetir
    assert session.complete and not session.protected and session.relaxed_note


# ---------------------------------------------------------------------------
# Determinizm ve kalicilik
# ---------------------------------------------------------------------------

def sample_pots(fmt: CupFormat, seed: int = 0) -> list[list[CupTeam]]:
    rng = random.Random(seed)
    return cd.make_pots(teams_from_leagues(random_leagues(rng, 16)), fmt)


@pytest.mark.parametrize("fmt", [KO, GR])
def test_step_by_step_equals_draw_all(fmt):
    for seed in range(40):
        pots = sample_pots(fmt, seed)
        a = DrawSession(fmt, pots, seed=seed)
        steps = []
        while True:
            try:
                steps.append(a.draw_next())
            except DrawComplete:
                break
        b = DrawSession(fmt, pots, seed=seed)
        assert b.draw_all() == steps == a.steps
        assert b.draw_all() == []                          # bitmis kurada bos
        assert a.slots() == b.slots()


@pytest.mark.parametrize("fmt", [KO, GR])
def test_state_roundtrip_resumes_identically(fmt):
    for seed in (0, 1, 17, "kura-2026"):
        pots = sample_pots(fmt, 5 if isinstance(seed, str) else seed)
        reference = DrawSession(fmt, pots, seed=seed)
        reference.draw_all()
        for cut in range(0, 17):
            session = DrawSession(fmt, pots, seed=seed)
            for _ in range(cut):
                session.draw_next()
            text = json.dumps(session.to_state())
            restored = DrawSession.from_state(json.loads(text))
            assert restored.steps == session.steps
            assert restored.slots() == session.slots()
            assert restored.current_pot == session.current_pot
            assert restored.protected == session.protected
            restored.draw_all()
            assert restored.steps == reference.steps, (seed, cut)
        assert DrawSession.from_state(reference.to_state()).complete


def test_state_is_plain_json_and_validated():
    pots = sample_pots(KO, 3)
    session = DrawSession(KO, pots, seed=9)
    session.draw_next()
    session.draw_next()
    state = session.to_state()

    def plain(value) -> bool:
        if isinstance(value, dict):
            return all(isinstance(k, str) and plain(v) for k, v in value.items())
        if isinstance(value, list):
            return all(plain(v) for v in value)
        return value is None or type(value) in (int, str, float, bool)

    assert plain(state)
    assert json.loads(json.dumps(state)) == state
    bad = json.loads(json.dumps(state))
    bad["steps"][1]["team_id"] = bad["steps"][0]["team_id"]
    with pytest.raises(ValueError):
        DrawSession.from_state(bad)
    bad = json.loads(json.dumps(state))
    bad["steps"][1]["slot"] = 3
    with pytest.raises(ValueError):
        DrawSession.from_state(bad)
    with pytest.raises(ValueError):
        DrawSession.from_state({**state, "version": 99})


@pytest.mark.parametrize("fmt", [KO, GR])
def test_determinism_and_seed_variety(fmt):
    pots = cd.make_pots(teams_from_leagues([f"L{i % 6}" for i in range(16)]), fmt)
    first = DrawSession(fmt, pots, seed=123)
    first.draw_all()
    again = DrawSession(fmt, pots, seed=123)
    again.draw_all()
    assert draw_signature(first) == draw_signature(again)
    results = set()
    for seed in range(40):
        s = DrawSession(fmt, pots, seed=seed)
        s.draw_all()
        results.add(tuple(map(tuple, s.slots())))
    assert len(results) >= 30


def test_first_ball_roughly_uniform():
    pots = cd.make_pots(teams_from_leagues([f"L{i}" for i in range(8)]), KO)
    counts = Counter(DrawSession(KO, pots, seed=s).draw_next().team_id for s in range(800))
    assert set(counts) == {t.id for t in pots[1]}
    assert min(counts.values()) > 140


def test_session_views_headline_pairs_and_groups():
    pots = sample_pots(KO, 21)
    session = DrawSession(KO, pots, seed=4)
    assert session.current_pot == 1 and "2. torba" in session.headline()
    with pytest.raises(ValueError):
        session.pairs()
    with pytest.raises(ValueError):
        session.groups()
    first = session.draw_next()
    assert session.current_pot == 0 and "1. torba" in session.headline()
    assert "Son 16" in session.headline()
    assert session.slots()[0] == [first.team_id, None]
    assert len(session.remaining(1)) == 7 and len(session.remaining(0)) == 8
    assert session.describe_step(first).endswith("– ?")
    second = session.draw_next()
    assert second.partner_id == first.team_id and session.describe_step(second).startswith("Eşleşme 1:")
    session.draw_all()
    assert session.current_pot is None and session.headline() == "Kura tamamlandı"
    seeded = {t.id for t in pots[0]}
    assert all(home2 in seeded and home1 not in seeded for home1, home2 in session.pairs())
    assert session.first_stage is Stage.R16

    g = DrawSession(GR, sample_pots(GR, 21), seed=4)
    assert g.current_pot == 0
    pots_seen = []
    while not g.complete:
        pots_seen.append(g.current_pot)
        step = g.draw_next()
        assert "Grup" in g.describe_step(step)
    assert pots_seen == [0] * 4 + [1] * 4 + [2] * 4 + [3] * 4
    assert all(None not in slot for slot in g.slots()) and g.groups() == g.slots()
    with pytest.raises(ValueError):
        g.pairs()


def test_session_rejects_bad_pots():
    teams = teams_from_leagues([f"L{i}" for i in range(16)])
    with pytest.raises(ValueError):
        DrawSession(KO, [teams[:8], teams[8:15]], seed=1)
    with pytest.raises(ValueError):
        DrawSession(GR, [teams[:8], teams[8:]], seed=1)
    with pytest.raises(ValueError):
        DrawSession(KO, [teams[:8], teams[:8]], seed=1)


# ---------------------------------------------------------------------------
# Tur ilerlemesi
# ---------------------------------------------------------------------------

def test_next_round_pairs_and_bracket_siblings():
    assert cd.next_round_pairs([1, 2, 3, 4]) == [(2, 1), (4, 3)]
    assert cd.next_round_pairs([7, 9]) == [(9, 7)]
    for bad in ([1], [1, 2, 3], [1, 1]):
        with pytest.raises(ValueError):
            cd.next_round_pairs(bad)

    rng = random.Random(1)
    pots = sample_pots(KO, 2)
    session = DrawSession(KO, pots, seed=2)
    session.draw_all()
    ties = session.pairs()
    while len(ties) > 1:
        winners = [rng.choice(pair) for pair in ties]
        nxt = cd.next_round_pairs(winners)
        assert len(nxt) == len(ties) // 2
        for j, (first, second) in enumerate(nxt):
            assert first in ties[2 * j + 1] and second in ties[2 * j]
        flat = [tid for pair in nxt for tid in pair]
        assert len(set(flat)) == len(flat)
        ties = nxt


def test_group_qualifier_pairs_bracket():
    rankings = [[11, 12, 13, 14], [21, 22, 23, 24], [31, 32, 33, 34], [41, 42, 43, 44]]
    qf = cd.group_qualifier_pairs(rankings)
    assert qf == [(22, 11), (42, 31), (12, 21), (32, 41)]      # A1-B2, C1-D2, B1-A2, D1-C2
    winners = {11, 21, 31, 41}
    assert all(second in winners and first not in winners for first, second in qf)
    halves = [{t for pair in qf[:2] for t in pair}, {t for pair in qf[2:] for t in pair}]
    assert not halves[0] & halves[1]
    half_of = {t: h for h, members in enumerate(halves) for t in members}
    assert half_of[11] != half_of[21] and half_of[31] != half_of[41]   # A1/B1 ancak finalde
    for group in rankings:
        assert half_of[group[0]] != half_of[group[1]]                  # ayni grup ancak finalde
    sf = cd.next_round_pairs([11, 31, 21, 41])
    assert sf == [(31, 11), (41, 21)]
    with pytest.raises(ValueError):
        cd.group_qualifier_pairs(rankings[:3])
    with pytest.raises(ValueError):
        cd.group_qualifier_pairs([[1, 2], [3, 4], [5, 6], [7, 1]])


# ---------------------------------------------------------------------------
# Grup fiksturu ve puan tablosu
# ---------------------------------------------------------------------------

def test_group_schedule_properties():
    ids = [5, 9, 13, 2]
    rounds = cd.group_schedule(ids)
    assert len(rounds) == 6
    matches = [m for rnd in rounds for m in rnd]
    assert len(matches) == 12
    for rnd in rounds:
        playing = [t for m in rnd for t in m]
        assert sorted(playing) == sorted(ids)
    assert Counter(matches) == Counter({(a, b): 1 for a in ids for b in ids if a != b})
    home_counts = Counter(h for h, _ in matches)
    assert all(home_counts[t] == 3 for t in ids)
    with pytest.raises(ValueError):
        cd.group_schedule([1, 1, 2, 3])


def ranked_ids(*args, **kwargs) -> list[int]:
    return [row.team_id for row in cd.rank_group(*args, **kwargs)]


def test_rank_group_basic_rows_and_outside_matches_ignored():
    results = [(1, 2, 3, 1), (2, 1, 2, 2), (3, 4, 0, 1), (4, 3, 0, 0), (1, 99, 9, 0)]
    rows = {r.team_id: r for r in cd.rank_group([1, 2, 3, 4], results)}
    r1 = rows[1]
    assert (r1.played, r1.won, r1.drawn, r1.lost, r1.goals_for, r1.goals_against, r1.points) == (2, 1, 1, 0, 5, 3, 4)
    assert r1.goal_difference == 2
    assert rows[4].points == 4 and rows[3].points == 1 and rows[2].points == 1
    assert ranked_ids([1, 2, 3, 4], results)[0] == 1


def test_rank_group_head_to_head_beats_goal_difference():
    results = [
        (1, 2, 1, 0), (2, 1, 0, 0),
        (1, 3, 0, 1), (3, 1, 0, 1),
        (1, 4, 0, 0), (4, 1, 0, 0),
        (2, 3, 5, 0), (3, 2, 0, 0),
        (2, 4, 4, 0), (4, 2, 0, 0),
        (3, 4, 0, 0), (4, 3, 0, 0),
    ]
    rows = cd.rank_group([4, 3, 2, 1], results)
    assert [r.team_id for r in rows] == [1, 2, 3, 4]
    assert rows[0].points == rows[1].points == 9
    assert rows[1].goal_difference > rows[0].goal_difference            # genel averaj 2'nin lehine


def test_rank_group_three_way_tie_mini_league():
    results = [
        (1, 2, 3, 0), (2, 1, 1, 0), (2, 3, 1, 0), (3, 2, 2, 0), (3, 1, 1, 0), (1, 3, 1, 0),
        (1, 4, 1, 0), (4, 1, 0, 1), (2, 4, 10, 0), (4, 2, 0, 10), (3, 4, 1, 0), (4, 3, 0, 1),
    ]
    rows = cd.rank_group([1, 2, 3, 4], results)
    assert [r.points for r in rows[:3]] == [12, 12, 12]
    # ikili maclar: 1 (+2), 3 (+1), 2 (-3); genel averajda 2 en iyi olurdu
    assert [r.team_id for r in rows] == [1, 3, 2, 4]


def test_rank_group_head_to_head_reapplied_to_remaining_subset():
    results = [
        (1, 2, 2, 1), (2, 1, 1, 1), (1, 3, 3, 0), (3, 1, 1, 1),
        (2, 3, 1, 0), (3, 2, 2, 0),
        (1, 4, 0, 0), (4, 1, 0, 0), (2, 4, 5, 0), (4, 2, 0, 5), (3, 4, 1, 0), (4, 3, 0, 1),
    ]
    rows = cd.rank_group([1, 2, 3, 4], results)
    assert [r.points for r in rows[:3]] == [10, 10, 10]
    # 3'lu mini lig: 1 ayrisir, 2 ve 3 esit (4 puan, -2, 3 gol) -> yalnizca 2-3 maclari: 3 onde
    assert [r.team_id for r in rows] == [1, 3, 2, 4]


def test_rank_group_fallback_coefficient_then_name():
    results = [(a, b, 0, 0) for a in (1, 2, 3, 4) for b in (1, 2, 3, 4) if a != b]
    assert ranked_ids([4, 3, 2, 1], results) == [1, 2, 3, 4]
    coefs = {1: 10.0, 2: 30.0, 3: 30.0, 4: 5.0}
    names = {1: "Beta", 2: "Zeta", 3: "alfa", 4: "Gama"}
    assert ranked_ids([1, 2, 3, 4], results, coefficients=coefs, names=names) == [3, 2, 1, 4]
    assert ranked_ids([1, 2, 3, 4], [], coefficients=coefs) == [2, 3, 1, 4]



# ---------------------------------------------------------------------------
# Kura gecesi (11. Asama): tek tiklamada bir eslesme
# ---------------------------------------------------------------------------

def _pots(fmt: CupFormat, size: int = 16) -> list[list[CupTeam]]:
    leagues = [f"L{i % 6}" for i in range(size)]
    return cd.make_pots(teams_from_leagues(leagues), fmt)


@pytest.mark.parametrize("seed", range(6))
def test_draw_pair_opens_home_then_away_and_matches_ball_by_ball(seed):
    pots = _pots(KO)
    by_pair, by_ball = DrawSession(KO, pots, seed=seed), DrawSession(KO, pots, seed=seed)
    assert (by_pair.pair_count, by_pair.pairs_drawn) == (8, 0)
    for click in range(1, 9):
        home, away = by_pair.draw_pair()
        assert home.partner_id is None and away.partner_id == home.team_id and home.slot == away.slot
        assert home.pot == 1 and away.pot == 0                     # once seri basi olmayan: ilk mac ev sahibi
        assert by_pair.pairs_drawn == click
        assert [by_ball.draw_next(), by_ball.draw_next()] == [home, away]   # ayni tohum: ayni kura
    assert by_pair.complete and by_pair.pairs() == by_ball.pairs()
    with pytest.raises(DrawComplete):
        by_pair.draw_pair()


def test_draw_pair_completes_a_half_open_pair_with_one_ball():
    session = DrawSession(KO, _pots(KO), seed=3)
    first = session.draw_next()
    assert session.pairs_drawn == 0
    (away,) = session.draw_pair()
    assert away.partner_id == first.team_id and session.pairs_drawn == 1


def test_draw_pair_in_groups_opens_a_single_ball():
    session = DrawSession(GR, _pots(GR), seed=2)
    assert session.pair_count == 16
    for click in range(1, 17):
        assert len(session.draw_pair()) == 1 and session.pairs_drawn == click
    assert session.complete and sorted(len(g) for g in session.groups()) == [4, 4, 4, 4]
