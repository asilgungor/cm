"""
tests/engine_stats.py
=====================
Dagilim olcum araci (13A). `.claude/phase13/scratch/harness.py` + `par.py` icindeki
teshis araci buraya TASINDI: testler ayni sentetik kadrolari, ayni `play()` cagrisini
ve ayni toplayiciyi (Agg) kullanir, boylece teshis raporundaki sayilarla kapi
testindeki sayilar ayni seyi olcer.

Veritabanina DOKUNMAZ. Paralellik en fazla 2 surecle yapilir (RAM sinirli).
"""

from __future__ import annotations

import os
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from match_engine import (  # noqa: E402
    EngineConfig,
    EventType,
    MatchEngine,
    MatchPlayer,
    MatchTeam,
)
from models import Position  # noqa: E402

# Paralellik tavani: sinirli RAM, en fazla 2 surec (13A kurali).
MAX_WORKERS = 2

COMPOSITION = {Position.GK: 2, Position.DEF: 4, Position.MID: 5, Position.FWD: 4}
BIG_COMPOSITION = {Position.GK: 3, Position.DEF: 7, Position.MID: 7, Position.FWD: 5}


# ---------------------------------------------------------------------------
# Kadro kurucular (harness.py ile ayni)
# ---------------------------------------------------------------------------

def make_player(pid: int, pos: Position, ovr: int, *, form=50, morale=70, age=26,
                shooting_delta=0) -> MatchPlayer:
    gk = ovr + 6 if pos is Position.GK else 30
    return MatchPlayer(
        id=pid, name=f"P{pid}-{pos.value}", position=pos, age=age, overall=ovr,
        pace=ovr, shooting=ovr + (4 if pos is Position.FWD else -10) + shooting_delta,
        passing=ovr + (3 if pos is Position.MID else -5),
        defending=ovr + (6 if pos is Position.DEF else -15),
        dribbling=ovr, goalkeeping=gk, form=form, morale=morale,
    )


def make_team(tid: int, name: str, ovr: int, comp=None, **kw) -> MatchTeam:
    players, pid = [], tid * 100
    for pos, n in (comp or COMPOSITION).items():
        for _ in range(n):
            pid += 1
            players.append(make_player(pid, pos, ovr, **kw))
    return MatchTeam(id=tid, name=name, reputation=80, players=players)


def make_spread_team(tid: int, name: str, ovr: int, spread: int = 6, rng_seed: int = 0,
                     comp=None, reputation: int = 80) -> MatchTeam:
    """Oyunculari `ovr` etrafinda dagilan kadro (yildizlar + kadro oyunculari)."""
    rng = random.Random(rng_seed * 7919 + tid)
    players, pid = [], tid * 100
    for pos, n in (comp or BIG_COMPOSITION).items():
        for i in range(n):
            pid += 1
            tilt = spread - (2 * spread * i / max(1, n - 1)) if n > 1 else 0
            p_ovr = max(25, min(99, int(round(ovr + tilt + rng.gauss(0, 2)))))
            players.append(make_player(pid, pos, p_ovr))
    return MatchTeam(id=tid, name=name, reputation=reputation, players=players)


def play(home_ovr=80, away_ovr=80, seed=1, cfg=None, home_kw=None, away_kw=None,
         home_inst=None, away_inst=None, home_form=None, away_form=None,
         spread=None, rep_home=80, rep_away=80, neutral=False):
    hk = dict(home_kw or {})
    ak = dict(away_kw or {})
    if spread is None:
        home = make_team(1, "Ev", home_ovr, **hk)
        away = make_team(2, "Dep", away_ovr, **ak)
    else:
        home = make_spread_team(1, "Ev", home_ovr, spread=spread, rng_seed=seed)
        away = make_spread_team(2, "Dep", away_ovr, spread=spread, rng_seed=seed)
    home.reputation, away.reputation = rep_home, rep_away
    if home_inst is not None:
        home.instructions = home_inst
        home.manager_controlled = True
    if away_inst is not None:
        away.instructions = away_inst
        away.manager_controlled = True
    if home_form is not None:
        home.formation = home_form
    if away_form is not None:
        away.formation = away_form
    return MatchEngine(home, away, seed=seed, config=cfg, neutral_venue=neutral).simulate()


# ---------------------------------------------------------------------------
# Toplayici
# ---------------------------------------------------------------------------

SHOT_EVENTS = (EventType.GOAL, EventType.MISS, EventType.SAVE)


def _stat(team, name: str, default: int = 0) -> int:
    """Motor henuz o sayaci tanimadiysa 0 dondurur (asama asama eklenebilsin)."""
    return int(getattr(team.stats, name, default))


@dataclass
class Agg:
    """Cok sayida MatchResult uzerinde toplama. Surecler arasinda birlestirilebilir."""

    n: int = 0
    hg: int = 0
    ag: int = 0
    hw: int = 0
    dr: int = 0
    aw: int = 0
    shots: int = 0
    sot: int = 0
    saves: int = 0
    yellows: int = 0
    reds: int = 0
    second_yellows: int = 0
    injuries: int = 0
    pens: int = 0
    pen_goals: int = 0
    fks: int = 0
    corner_shots: int = 0
    corners: int = 0
    fouls: int = 0
    offsides: int = 0
    set_piece_goals: int = 0
    poss_h: int = 0
    poss_total: int = 0
    gsq: int = 0
    goals_first_half: int = 0
    goals_90plus: int = 0
    goals_last10: int = 0
    goals_first5: int = 0
    subs: int = 0
    # Guclu/zayif takim ayrimi (ev-deplasman degil): `run(swap_from=...)` doldurur
    strong_w: int = 0
    strong_d: int = 0
    strong_l: int = 0
    strong_gf: int = 0
    strong_ga: int = 0
    scorelines: Counter = field(default_factory=Counter)
    total_goals: Counter = field(default_factory=Counter)
    goal_minutes: Counter = field(default_factory=Counter)
    sub_minutes: Counter = field(default_factory=Counter)
    goals_by_role: Counter = field(default_factory=Counter)
    assists_by_role: Counter = field(default_factory=Counter)

    def add(self, r, strong: str | None = None) -> None:
        self.n += 1
        h, a = r.home_score, r.away_score
        if strong is not None:
            sg, og = (h, a) if strong == "home" else (a, h)
            self.strong_gf += sg
            self.strong_ga += og
            if sg > og:
                self.strong_w += 1
            elif sg == og:
                self.strong_d += 1
            else:
                self.strong_l += 1
        self.hg += h
        self.ag += a
        self.gsq += (h + a) ** 2
        if h > a:
            self.hw += 1
        elif h == a:
            self.dr += 1
        else:
            self.aw += 1
        self.scorelines[(h, a)] += 1
        self.total_goals[h + a] += 1
        for t in (r.home, r.away):
            self.shots += t.stats.shots
            self.sot += t.stats.shots_on_target
            self.saves += t.stats.saves
            self.yellows += t.stats.yellow_cards
            self.reds += t.stats.red_cards
            self.injuries += t.stats.injuries
            self.subs += t.stats.substitutions
            self.corners += _stat(t, "corners")
            self.fouls += _stat(t, "fouls")
            self.offsides += _stat(t, "offsides")
            for p in t.players:
                if p.goals:
                    self.goals_by_role[(p.role or p.position).value] += p.goals
                if p.assists:
                    self.assists_by_role[(p.role or p.position).value] += p.assists
        self.poss_h += r.home.stats.possession_minutes
        self.poss_total += r.home.stats.possession_minutes + r.away.stats.possession_minutes

        for e in r.events:
            if e.type in SHOT_EVENTS and e.detail:
                if e.detail == "penalty":
                    self.pens += 1
                    if e.type is EventType.GOAL:
                        self.pen_goals += 1
                elif e.detail == "free_kick":
                    self.fks += 1
                elif e.detail == "corner":
                    self.corner_shots += 1
            if e.type is EventType.GOAL:
                if e.detail:
                    self.set_piece_goals += 1
                if e.minute <= 45:
                    self.goals_first_half += 1
                if e.minute <= 5:
                    self.goals_first5 += 1
                if e.minute >= 81:
                    self.goals_last10 += 1
                if e.minute == 90 and e.added_time > 0:
                    self.goals_90plus += 1
                self.goal_minutes[min(90, e.minute)] += 1
            elif e.type is EventType.RED_CARD and e.detail == "second_yellow":
                self.second_yellows += 1
            elif e.type is EventType.SUBSTITUTION and e.player_id is not None:
                self.sub_minutes[e.minute] += 1

    # --- turetilmis olcumler -------------------------------------------------

    @property
    def goals(self) -> int:
        return self.hg + self.ag

    @property
    def goals_per_match(self) -> float:
        return self.goals / max(1, self.n)

    @property
    def home_win_pct(self) -> float:
        return 100 * self.hw / max(1, self.n)

    @property
    def draw_pct(self) -> float:
        return 100 * self.dr / max(1, self.n)

    @property
    def away_win_pct(self) -> float:
        return 100 * self.aw / max(1, self.n)

    @property
    def shots_per_match(self) -> float:
        return self.shots / max(1, self.n)

    @property
    def sot_share(self) -> float:
        return 100 * self.sot / max(1, self.shots)

    @property
    def conversion(self) -> float:
        return 100 * self.goals / max(1, self.shots)

    @property
    def yellows_per_match(self) -> float:
        return self.yellows / max(1, self.n)

    @property
    def reds_per_match(self) -> float:
        return self.reds / max(1, self.n)

    @property
    def pens_per_match(self) -> float:
        return self.pens / max(1, self.n)

    @property
    def pen_conversion(self) -> float:
        return 100 * self.pen_goals / max(1, self.pens)

    @property
    def corners_per_match(self) -> float:
        """Motorda korner sayaci varsa o; yoksa (eski davranis) korner sutu sayisi."""
        return (self.corners or self.corner_shots) / max(1, self.n)

    @property
    def fouls_per_match(self) -> float:
        return self.fouls / max(1, self.n)

    @property
    def offsides_per_match(self) -> float:
        return self.offsides / max(1, self.n)

    @property
    def set_piece_goal_share(self) -> float:
        return 100 * self.set_piece_goals / max(1, self.goals)

    @property
    def first_half_share(self) -> float:
        return 100 * self.goals_first_half / max(1, self.goals)

    @property
    def goals_90plus_share(self) -> float:
        return 100 * self.goals_90plus / max(1, self.goals)

    @property
    def last10_share(self) -> float:
        return 100 * self.goals_last10 / max(1, self.goals)

    @property
    def first5_share(self) -> float:
        return 100 * self.goals_first5 / max(1, self.goals)

    @property
    def five_plus_share(self) -> float:
        return 100 * sum(c for g, c in self.total_goals.items() if g >= 5) / max(1, self.n)

    @property
    def possession_home(self) -> float:
        return 100 * self.poss_h / max(1, self.poss_total)

    @property
    def subs_per_match(self) -> float:
        return self.subs / max(1, self.n)

    @property
    def strong_n(self) -> int:
        return self.strong_w + self.strong_d + self.strong_l

    @property
    def favourite_win_pct(self) -> float:
        return 100 * self.strong_w / max(1, self.strong_n)

    @property
    def underdog_win_pct(self) -> float:
        return 100 * self.strong_l / max(1, self.strong_n)

    @property
    def gap_draw_pct(self) -> float:
        return 100 * self.strong_d / max(1, self.strong_n)

    def role_share(self, counter: Counter, role: str) -> float:
        total = sum(counter.values())
        return 100 * counter.get(role, 0) / max(1, total)

    def report(self, title: str = "") -> str:
        n = max(1, self.n)
        var = self.gsq / n - self.goals_per_match ** 2
        lines = [f"--- {title} (n={self.n}) ---"]
        lines.append(f"goals/match={self.goals_per_match:.3f} home={self.hg/n:.3f} away={self.ag/n:.3f} "
                     f"var/mean={var/max(0.001, self.goals_per_match):.3f}")
        lines.append(f"W/D/L={self.home_win_pct:.1f}/{self.draw_pct:.1f}/{self.away_win_pct:.1f}")
        lines.append(f"shots={self.shots_per_match:.2f} SoT%={self.sot_share:.1f} "
                     f"conversion={self.conversion:.2f}% 5+goals={self.five_plus_share:.1f}%")
        lines.append(f"Y={self.yellows_per_match:.2f} R={self.reds_per_match:.3f} "
                     f"(2nd yellow {self.second_yellows/n:.3f}) inj={self.injuries/n:.3f} "
                     f"subs={self.subs_per_match:.2f}")
        lines.append(f"pens={self.pens_per_match:.3f}@{self.pen_conversion:.0f}% "
                     f"FK={self.fks/n:.3f} cornerShots={self.corner_shots/n:.2f} "
                     f"corners={self.corners_per_match:.2f} fouls={self.fouls_per_match:.2f} "
                     f"offsides={self.offsides_per_match:.2f} setPieceGoals={self.set_piece_goal_share:.1f}%")
        lines.append(f"goal timing: 1H={self.first_half_share:.1f}% last10={self.last10_share:.1f}% "
                     f"90+={self.goals_90plus_share:.1f}% first5={self.first5_share:.1f}%")
        lines.append("goals by role: " + ", ".join(
            f"{k} {self.role_share(self.goals_by_role, k):.1f}%" for k in ("FWD", "MID", "DEF", "GK")))
        lines.append("assists by role: " + ", ".join(
            f"{k} {self.role_share(self.assists_by_role, k):.1f}%" for k in ("FWD", "MID", "DEF", "GK")))
        lines.append("top scorelines: " + ", ".join(
            f"{h}-{a}:{100*c/n:.1f}%" for (h, a), c in self.scorelines.most_common(8)))
        return "\n".join(lines)


_SCALARS = ("n hg ag hw dr aw shots sot saves yellows reds second_yellows injuries pens pen_goals fks "
            "corner_shots corners fouls offsides set_piece_goals poss_h poss_total gsq goals_first_half "
            "goals_90plus goals_last10 goals_first5 subs strong_w strong_d strong_l strong_gf "
            "strong_ga").split()
_COUNTERS = ("scorelines", "total_goals", "goal_minutes", "sub_minutes", "goals_by_role", "assists_by_role")


def merge(dst: Agg, src: Agg) -> Agg:
    for k in _SCALARS:
        setattr(dst, k, getattr(dst, k) + getattr(src, k))
    for k in _COUNTERS:
        getattr(dst, k).update(getattr(src, k))
    return dst


# ---------------------------------------------------------------------------
# Kosum (en fazla 2 surec)
# ---------------------------------------------------------------------------

def _chunk(args) -> Agg:
    seeds, kw, swap_from = args
    agg = Agg()
    home_ovr, away_ovr = kw.get("home_ovr", 80), kw.get("away_ovr", 80)
    for s in seeds:
        if swap_from is not None and s >= swap_from:
            kw2 = dict(kw)
            kw2["home_ovr"], kw2["away_ovr"] = away_ovr, home_ovr
            strong = "home" if away_ovr > home_ovr else ("away" if away_ovr < home_ovr else None)
            agg.add(play(seed=s, **kw2), strong=strong)
        else:
            strong = "home" if home_ovr > away_ovr else ("away" if home_ovr < away_ovr else None)
            agg.add(play(seed=s, **kw), strong=strong)
    return agg


def run(n: int, workers: int = MAX_WORKERS, swap_from: int | None = None, **kw) -> Agg:
    """
    n mac oynatir ve Agg dondurur. `swap_from` verilirse o tohumdan itibaren ev/deplasman
    gucleri yer degistirir (ev avantajini notrlemek icin: zayif takim yarisinda ev sahibi).
    """
    seeds = list(range(n))
    workers = max(1, min(workers, MAX_WORKERS))
    size = max(1, n // (workers * 2))
    tasks = [(seeds[i:i + size], kw, swap_from) for i in range(0, n, size)]
    out = Agg()
    if workers == 1 or os.getenv("CM_STATS_SERIAL"):
        for t in tasks:
            merge(out, _chunk(t))
        return out
    try:
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        with ctx.Pool(workers) as pool:
            for part in pool.imap_unordered(_chunk, tasks):
                merge(out, part)
    except Exception:                      # havuz kurulamadi: seri calis (kapi yine gecerli)
        out = Agg()
        for t in tasks:
            merge(out, _chunk(t))
    return out


# ---------------------------------------------------------------------------
# Sezon: gol krali
# ---------------------------------------------------------------------------

def _reset_for_next_match(team: MatchTeam) -> None:
    for p in team.players:
        p.goals = p.assists = p.shots = p.shots_on_target = p.saves = 0
        p.on_pitch = False
        p.entered_minute = p.left_minute = None
        p.substituted = p.injured = p.sent_off = False
        p.yellow_cards = 0
        p.energy = 100.0
        p.energy_log.clear()
        p.role = None
        p.role_changes.clear()


def _season_top_scorer(season: int, games: int = 38, cfg: EngineConfig | None = None) -> int:
    """Guclu bir kulubun (gercek gol krallari oradan cikar) 38 maclik sezondaki en golcusu."""
    team = make_spread_team(1, "Bizim", 85, spread=9, rng_seed=season)
    goals: Counter = Counter()
    for wk in range(games):
        opp = make_spread_team(2, "Rakip", 70 + (wk % 5) * 4, spread=6, rng_seed=season * 100 + wk)
        _reset_for_next_match(team)
        seed = season * 1000 + wk
        if wk % 2 == 0:
            MatchEngine(team, opp, seed=seed, config=cfg).simulate()
        else:
            MatchEngine(opp, team, seed=seed, config=cfg).simulate()
        for p in team.players:
            goals[p.id] += p.goals
    return max(goals.values(), default=0)


def _season_chunk(args) -> list[int]:
    seasons, games, cfg = args
    return [_season_top_scorer(s, games, cfg) for s in seasons]


def season_top_scorers(seasons: int = 20, games: int = 38, workers: int = MAX_WORKERS,
                       cfg: EngineConfig | None = None) -> list[int]:
    workers = max(1, min(workers, MAX_WORKERS))
    ids = list(range(seasons))
    size = max(1, seasons // (workers * 2))
    tasks = [(ids[i:i + size], games, cfg) for i in range(0, seasons, size)]
    if workers == 1 or os.getenv("CM_STATS_SERIAL"):
        return [g for t in tasks for g in _season_chunk(t)]
    try:
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        out: list[int] = []
        with ctx.Pool(workers) as pool:
            for part in pool.imap_unordered(_season_chunk, tasks):
                out.extend(part)
        return out
    except Exception:
        return [g for t in tasks for g in _season_chunk(t)]


# ---------------------------------------------------------------------------
# 14B: ozellik supurmesi (attribute_model.READERS)
# ---------------------------------------------------------------------------
# Kurulum: 80 v 80; iki takimin HER oyuncusuna tipik sayfa (attribute_model.expected_sheet) enjekte edilir, ev
# sahibinin test oyuncusunda (ya da `squad` ozelliklerde tum kadroda) tek ozellik `low` / `high` yapilir. Iki kol AYNI
# tohumlarla oynar (esli tohum): fark yalnizca o ozelliktir. Olcutler test oyuncusu / ev sahibi takim icindir.

SWEEP_LOW, SWEEP_HIGH = 6, 16
SWEEP_OVR = 80


def sweep_config(**kw) -> EngineConfig:
    return EngineConfig(attribute_model=True, **kw)


def sweep_teams(attr: str | None, value: int | None, subject: Position, squad: bool = False,
                setup: str = "", engine_values: dict[str, int] | None = None, typical: bool = True):
    """(ev, deplasman, test oyuncusu, ev sahibi rolleri). Test oyuncusu mevkinin ilk oyuncusudur (esit kadroda
    select_lineup'in kararli sirasiyla ilk 11'de). attr None: yalniz tipik sayfalar. engine_values: test oyuncusunun
    MOTOR ozellikleri (bugunku motorla kiyas). typical False: sayfa enjekte edilmez (oyuncunun kendi sayfasi)."""
    import attribute_model
    import cm_attributes
    from team_roles import SetPieceRoles

    home, away = make_team(1, "Ev", SWEEP_OVR), make_team(2, "Dep", SWEEP_OVR)
    subject_player = next(p for p in home.players if p.position is subject)
    if engine_values:
        for key, v in engine_values.items():
            setattr(subject_player, key, v)
    if typical:
        for team in (home, away):
            for p in team.players:
                p.sheet = attribute_model.expected_sheet(p)
    targets = home.players if squad else [subject_player]
    if attr is not None:
        for p in targets:
            if attr == attribute_model.INJURY_TRAIT:
                if not p.sheet:
                    p.sheet = cm_attributes.player_attributes(p)
                p.attributes = cm_attributes.as_fm_attributes(p.sheet) | {attr: value}
            else:
                p.sheet[attr] = value
    roles = None
    if setup == "captain":
        roles = SetPieceRoles(captain_id=subject_player.id)
    elif setup == "set_pieces":
        roles = SetPieceRoles(free_kick_taker_id=subject_player.id, corner_taker_id=subject_player.id)
    return home, away, subject_player, roles


class _SweepEngine(MatchEngine):
    """
    Supurme gozlemi (yalniz OKUR, rastgele sayi CEKMEZ; motorun cekilis sirasi aynen kalir):
        records  her atakta: savunan takim, cekilen savunmaci, sutor, isabet, uzak pay, gol olasiligi, gol
        rb       'beklenen' sayaclar: agirlikli cekilislerde (sutor / cekilen savunmaci / asist / kart / sakatlik)
                 test oyuncusunun SECILME OLASILIGI toplami. Gerceklesen sayimin beklenen degeri; nadir olaylarda
                 (asist, kart, sakatlik) varyansi cok daha dusuk (Rao-Blackwell).
    """

    def __init__(self, *a, probe: int | None = None, **kw) -> None:
        super().__init__(*a, **kw)
        self.records: list[list] = []   # [savunan takim, cekilen id, sutor id, isabet, uzak pay, p_gol, gol]
        self.rb: Counter = Counter()
        self._rec: list | None = None
        self._probe = probe
        self._ctx: str | None = None

    def _weighted_choice(self, players, weight):
        ctx, probe = self._ctx, self._probe
        if ctx is not None and players and any(p.id == probe for p in players):
            ws = [max(weight(p), 0.001) for p in players]
            self.rb[ctx] += sum(w for p, w in zip(players, ws, strict=True) if p.id == probe) / sum(ws)
            self.rb[ctx + "_n"] += 1
        return super()._weighted_choice(players, weight)

    def _within(self, ctx: str | None, fn, *args):
        previous, self._ctx = self._ctx, ctx
        try:
            return fn(*args)
        finally:
            self._ctx = previous

    def _pick_shooter(self, attacking):
        return self._within("shooter", super()._pick_shooter, attacking)

    def _discipline(self, attacking, defending) -> None:
        self._within("card", super()._discipline, attacking, defending)

    def _injury_check(self) -> None:
        self._within("injury", super()._injury_check)

    def _goal(self, team, scorer, kind=None, assister=None) -> None:
        self._within("assist" if kind is None else None, super()._goal, team, scorer, kind, assister)

    def _attack(self, attacking, defending) -> None:
        self._rec = None
        goals = attacking.stats.goals
        super()._attack(attacking, defending)
        if self._rec is not None:
            self._rec[6] = attacking.stats.goals > goals
            self.records.append(self._rec)

    def _defensive_resistance(self, defending):
        value = self._within("contest", super()._defensive_resistance, defending)
        d = self._nar.defender
        self._rec = [defending.id, d.id if d is not None else None, None, False, None, 0.0, False]
        return value

    def _goal_probability(self, shooter, shooter_str, defending, quality, defender_str):
        import attribute_model

        p = super()._goal_probability(shooter, shooter_str, defending, quality, defender_str)
        if self._rec is not None:
            clear = self._defender_quality(defender_str) * self._density
            self._rec[2], self._rec[3], self._rec[5] = shooter.id, True, p
            self._rec[4] = attribute_model.far_share(shooter.role or shooter.position, clear, self.cfg.attributes)
        return p


def _sweep_add(acc: Counter, r, eng: _SweepEngine, x_id: int) -> None:
    home, away = r.home, r.away
    x = next(p for p in home.players if p.id == x_id)
    acc["n"] += 1
    acc["points"] += 3 if r.home_score > r.away_score else 1 if r.home_score == r.away_score else 0
    acc["goals_for"] += r.home_score
    acc["conceded"] += r.away_score
    acc["assists_x"] += x.assists
    acc["injuries_x"] += int(x.injured)
    acc["poss_h"] += home.stats.possession_minutes
    acc["poss_total"] += home.stats.possession_minutes + away.stats.possession_minutes
    energy = dict(x.energy_log)
    if 75 in energy:
        acc["energy75_x"] += energy[75]
        acc["energy75_n"] += 1
    for e in r.events:
        if e.team_id != home.id:
            continue
        if e.type in SHOT_EVENTS and not e.detail:
            acc["shots_team"] += 1
            if e.player_id == x_id:
                acc["shots_x"] += 1
                if e.type is not EventType.MISS:
                    acc["sot_x"] += 1
                if e.type is EventType.GOAL:
                    acc["goals_x"] += 1
        if e.type in (EventType.YELLOW_CARD, EventType.RED_CARD):
            acc["team_cards"] += 1
            if e.player_id == x_id:
                acc["cards_x"] += 1
        if e.type is EventType.GOAL:
            if e.detail in ("free_kick", "corner"):
                acc["set_piece_goals"] += 1
                if e.detail == "corner" and e.player_id == x_id:
                    acc["header_goals_x"] += 1
            if e.minute >= 70 and e.home_score - 1 < e.away_score:
                acc["trailing_goals"] += 1
    for team_id, d_id, shooter_id, on_target, far, p_goal, goal in eng.records:
        if team_id == home.id:
            acc["contests_team"] += 1
            if d_id == x_id:
                acc["contests_x"] += 1
                acc["contest_goals_x"] += int(goal)
            if on_target and far is not None:
                acc["near_sot_opp"] += 1 - far
                acc["near_goals_opp"] += (1 - far) * goal
                acc["near_pgoal_opp"] += (1 - far) * p_goal
        elif on_target and shooter_id == x_id and far is not None:
            acc["sot_rb_x"] += 1
            acc["pgoal_x"] += p_goal
            acc["far_sot_x"] += far
            acc["far_goals_x"] += far * goal
            acc["far_pgoal_x"] += far * p_goal
    for key, value in eng.rb.items():
        acc["rb_" + key] += value


def _ratio(a: float, b: float) -> float:
    return a / b if b else 0.0


# olcut -> (aciklama, BEKLENEN deger (kabul olcutu), GERCEKLESEN deger). Beklenen: agirlikli cekilislerde test
# oyuncusunun secilme olasiligi ya da isabetli sutun gol olasiligi toplanir (ayni olcunun beklenen degeri, dusuk
# varyans); olasiligi olmayan olcutlerde ikisi aynidir.
SWEEP_METRICS = {
    "shot_share": ("oyuncunun takim sutlarindaki payi (akan oyun, sahadayken)",
                   lambda a: _ratio(a["rb_shooter"], a["rb_shooter_n"]),
                   lambda a: _ratio(a["shots_x"], a["shots_team"])),
    "conversion": ("oyuncunun gol / isabetli sut (akan oyun)", lambda a: _ratio(a["pgoal_x"], a["sot_rb_x"]),
                   lambda a: _ratio(a["goals_x"], a["sot_x"])),
    "far_conversion": ("oyuncunun uzak payli gol / isabetli sut", lambda a: _ratio(a["far_pgoal_x"], a["far_sot_x"]),
                       lambda a: _ratio(a["far_goals_x"], a["far_sot_x"])),
    "sot_share": ("oyuncunun isabet / sut (akan oyun)", lambda a: _ratio(a["sot_x"], a["shots_x"]), None),
    "conceded": ("takimin yedigi gol / mac", lambda a: _ratio(a["conceded"], a["n"]), None),
    "near_conceded": ("rakibin yakin payli gol / isabetli sut (akan oyun)",
                      lambda a: _ratio(a["near_pgoal_opp"], a["near_sot_opp"]),
                      lambda a: _ratio(a["near_goals_opp"], a["near_sot_opp"])),
    "contest_share": ("suta karsi cekilme payi (sahadayken)", lambda a: _ratio(a["rb_contest"], a["rb_contest_n"]),
                      lambda a: _ratio(a["contests_x"], a["contests_team"])),
    "contest_conversion": ("cekildigi sutlarda gol / sut", lambda a: _ratio(a["contest_goals_x"], a["contests_x"]),
                           None),
    "player_cards": ("oyuncunun karti / mac", lambda a: _ratio(a["rb_card"], a["n"]),
                     lambda a: _ratio(a["cards_x"], a["n"])),
    "team_cards": ("takimin karti / mac", lambda a: _ratio(a["team_cards"], a["n"]), None),
    "assists": ("oyuncunun asisti / mac (akan oyun)", lambda a: _ratio(a["rb_assist"], a["n"]),
                lambda a: _ratio(a["assists_x"], a["n"])),
    "possession": ("takimin topla oynama payi", lambda a: _ratio(a["poss_h"], a["poss_total"]), None),
    "energy75": ("oyuncunun 75. dakika enerjisi", lambda a: _ratio(a["energy75_x"], a["energy75_n"]), None),
    "injuries": ("oyuncunun sakatlanmasi / mac", lambda a: _ratio(a["rb_injury"], a["n"]),
                 lambda a: _ratio(a["injuries_x"], a["n"])),
    "points": ("takim puan / mac", lambda a: _ratio(a["points"], a["n"]), None),
    "trailing_goals": ("geride iken 70+ gol / mac", lambda a: _ratio(a["trailing_goals"], a["n"]), None),
    "header_goals": ("oyuncunun korner kafa golu / mac", lambda a: _ratio(a["header_goals_x"], a["n"]), None),
    "set_piece_goals": ("takimin frikik + korner golu / mac", lambda a: _ratio(a["set_piece_goals"], a["n"]), None),
}


def _sweep_chunk(args) -> tuple[Counter, Counter]:
    seeds, spec = args
    arms = []
    for value, engine_values in ((spec["low"], spec["low_engine"]), (spec["high"], spec["high_engine"])):
        acc: Counter = Counter()
        for s in seeds:
            home, away, x, roles = sweep_teams(spec["attr"], value, spec["subject"], spec["squad"], spec["setup"],
                                               engine_values, spec["typical"])
            eng = _SweepEngine(home, away, seed=s, config=spec["cfg"], home_roles=roles, probe=x.id)
            _sweep_add(acc, eng.simulate(), eng, x.id)
        arms.append(acc)
    return arms[0], arms[1]


@dataclass
class SweepResult:
    attr: str | None
    metric: str
    low: Counter
    high: Counter

    @property
    def n(self) -> int:
        return self.low["n"]

    def value(self, arm: str, metric: str | None = None, realized: bool = False) -> float:
        """Beklenen deger (kabul olcutu); realized=True: gerceklesen sayim (olasiligi olmayan olcutte ayni)."""
        _label, expected, actual = SWEEP_METRICS[metric or self.metric]
        fn = actual if realized and actual is not None else expected
        return fn(self.low if arm == "low" else self.high)

    def change(self, metric: str | None = None, absolute: bool = False, realized: bool = False) -> float:
        """high - low (mutlak) ya da high / low - 1 (goreli)."""
        lo, hi = self.value("low", metric, realized), self.value("high", metric, realized)
        return hi - lo if absolute else (_ratio(hi, lo) - 1.0 if lo else 0.0)

    @property
    def points_delta(self) -> float:
        return self.value("high", "points") - self.value("low", "points")


def attribute_sweep(attr: str | None, n: int = 3000, *, metric: str | None = None, low: int = SWEEP_LOW,
                    high: int = SWEEP_HIGH, subject: Position | None = None, squad: bool | None = None,
                    setup: str | None = None, cfg: EngineConfig | None = None, workers: int = MAX_WORKERS,
                    low_engine: dict[str, int] | None = None, high_engine: dict[str, int] | None = None,
                    typical: bool = True, seed0: int = 0) -> SweepResult:
    """
    Esli tohumlu 6 -> 16 supurmesi (bayrak acik). Varsayilanlar attribute_model.READERS[attr]'dan: olcut, test
    oyuncusunun mevkii, kadro (squad: 11 oyuncu), kurulum (kaptan / duran top aticisi).
    attr None + low_engine / high_engine: test oyuncusunun MOTOR ozelliklerini degistiren kiyas (orn. bugunku
    pace + shooting etkisi; cfg=EngineConfig() ile bayrak kapali). En fazla 2 surec.
    """
    import attribute_model

    reader = attribute_model.READERS.get(attr) if attr else None
    spec = {
        "attr": attr, "low": low, "high": high,
        "subject": subject or (reader.subject if reader else Position.FWD),
        "squad": (reader.squad if reader else False) if squad is None else squad,
        "setup": (reader.setup if reader else "") if setup is None else setup,
        "cfg": cfg or sweep_config(), "low_engine": low_engine, "high_engine": high_engine, "typical": typical,
    }
    seeds = list(range(seed0, seed0 + n))
    workers = max(1, min(workers, MAX_WORKERS))
    size = max(1, n // (workers * 4))
    tasks = [(seeds[i:i + size], spec) for i in range(0, n, size)]
    if workers == 1 or os.getenv("CM_STATS_SERIAL"):
        parts = [_sweep_chunk(t) for t in tasks]
    else:
        try:
            import multiprocessing as mp

            ctx = mp.get_context("spawn")
            with ctx.Pool(workers) as pool:
                parts = list(pool.imap_unordered(_sweep_chunk, tasks))
        except Exception:                  # havuz kurulamadi: seri calis
            parts = [_sweep_chunk(t) for t in tasks]
    low_acc, high_acc = Counter(), Counter()
    for lo_part, hi_part in parts:
        low_acc.update(lo_part)
        high_acc.update(hi_part)
    return SweepResult(attr, metric or (reader.metric if reader else "points"), low_acc, high_acc)
