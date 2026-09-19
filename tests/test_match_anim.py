"""
Faz 14T -- canli mac 2D canlandirma betikleri (match_anim.py) ve bilesen (web_assets/match_pitch.js). Veritabani ve
tarayici gerekmez:

  CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_match_anim.py

Kilitlenen sozlesmeler (K-S17 durustluk kurallari):
    * her olay turu bir betik uretir; sema tutarli (oyuncu / konum / hareket / top / efekt)
    * ISKA kaleye girmez (cizgiyi direk disindan ya da ust direk yuksekliginin ustunden gecer, aga hic dusmez)
    * GOL gercek sutcunun ayagindan, hucum yonundeki kalenin agina gider (ag dalgasi)
    * KURTARIS savunan takimin O ANKI gercek kalecisinde biter (kaleci topa uzanir)
    * top dogru takimda: sut aninda top sutcude, zincir halkasinin oyuncusu topu alir, karenin sonunda top
      olayin mantigina gore dogru takimda (kurtaris -> kaleci, faul -> faule ugrayan takim, ofsayt -> savunan ...)
    * olaydaki gercek oyuncular: sutcu, zincirdeki pasorler, korner aticisi (motorun kurali), frikik / penalti
      aticisi, kart goren, sakatlanan, giren / cikan
    * determinizm: ayni mac -> ayni betikler (baska bir surecte de); motor RNG'sine ve sonuca dokunulmaz
    * bekleme butcesi: betik karenin dwell_ms * FIT suresine sigar; bilesen hiz carpanini uygular
    * yuk boyutu kucuk; gizli sayi yok (sans kalitesi, olasilik, guc, not, ham kondisyon)
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from engine_stats import make_spread_team, play  # noqa: E402

import match_anim as ma  # noqa: E402
import pitch  # noqa: E402
import team_roles  # noqa: E402
from live_match import LiveMatch  # noqa: E402
from match_engine import EventType, KnockoutRule, MatchEngine  # noqa: E402
from match_feed import build_timeline  # noqa: E402
from models import Position  # noqa: E402

L, W = pitch.PITCH_LENGTH, pitch.PITCH_WIDTH
POST_LO, POST_HI = pitch.GOAL_TOP, pitch.GOAL_BOTTOM
SHOTS = {"GOAL", "SAVE", "MISS"}


# ---------------------------------------------------------------------------
# Derlem: normal maclar + seri penaltili eleme maclari + atanmis korner aticisi
# ---------------------------------------------------------------------------

def _knockout(seed: int):
    home = make_spread_team(1, "Ev", 80, spread=6, rng_seed=seed)
    away = make_spread_team(2, "Dep", 80, spread=6, rng_seed=seed)
    return MatchEngine(home, away, seed=seed, knockout=KnockoutRule(), neutral_venue=True).simulate()


@pytest.fixture(scope="module")
def corpus():
    matches = [play(80, 78, seed=s, spread=6) for s in range(14)]
    shootouts = []
    for seed in range(80):
        r = _knockout(seed)
        if r.decided_by == "penalties":
            shootouts.append(r)
        if len(shootouts) == 2:
            break
    matches += shootouts
    extra = 100
    need = {("RED_CARD", None), ("GOAL", "penalty"), ("GOAL", "free_kick"), ("GOAL", "corner"), ("SAVE", "penalty"),
            ("MISS", "free_kick"), ("SAVE", "corner")}

    def have():
        return {(e.type.value, e.detail if e.type in (EventType.GOAL, EventType.SAVE, EventType.MISS) else None)
                for r in matches for e in r.events}

    while not need <= have() and extra < 400:
        r = play(80, 78, seed=extra, spread=6)
        kinds = {(e.type.value, e.detail if e.type in (EventType.GOAL, EventType.SAVE, EventType.MISS) else None)
                 for e in r.events}
        if kinds & (need - have()):
            matches.append(r)
        extra += 1
    return matches


@pytest.fixture(scope="module")
def scripted(corpus):
    """(result, frames, i, script) -- her olay icin bir kare (gizliler dahil), surekli oynatma sirasiyla."""
    out = []
    for r in corpus:
        frames = build_timeline(r, include_hidden=True)
        ctx = ma.MatchCtx(r)
        for i in range(len(frames)):
            out.append((r, frames, i, ma.frame_script(r, frames, i, i - 1 if i else None, ctx)))
    return out


def _events(scripted, *types):
    return [(r, f, i, s) for r, f, i, s in scripted if f[i].event.type in types]


# ---------------------------------------------------------------------------
# Tarayicinin oynatma kuralinin Python kopyasi (web_assets/match_pitch.js prepare / posAt / ballAt)
# ---------------------------------------------------------------------------

def _ease(u):
    return u * u * (3 - 2 * u)


class Sim:
    def __init__(self, s: dict):
        self.s = s
        n = len(s["pl"])
        self.segs = {i: [] for i in range(n)}
        start = {i: (s["p0"][2 * i], s["p0"][2 * i + 1]) for i in range(n)}
        self.start = start
        for m in s["mv"]:
            prev = self.segs[m[0]][-1] if self.segs[m[0]] else None
            x0, y0 = (prev["x1"], prev["y1"]) if prev else start[m[0]]
            self.segs[m[0]].append({"t0": m[1], "t1": m[2], "x0": x0, "y0": y0, "x1": m[3], "y1": m[4],
                                    "c": (m[5], m[6]) if len(m) > 5 else None})
        bx, by, holder = s["b0"]
        self.flights = []
        cur = (bx, by, holder)
        for b in s["bl"]:
            t0, t1, x, y, h, hold, prof = b
            sx, sy = self.pos(cur[2], t0) if cur[2] >= 0 else (cur[0], cur[1])
            self.flights.append({"t0": t0, "t1": t1, "sx": sx, "sy": sy, "x": x, "y": y, "h": h, "holder": hold,
                                 "prof": prof, "from": cur[2]})
            cur = (x, y, hold)

    def pos(self, i, t):
        x, y = self.start[i]
        for sg in self.segs[i]:
            if t <= sg["t0"]:
                return x, y
            if t >= sg["t1"]:
                x, y = sg["x1"], sg["y1"]
                continue
            u = _ease((t - sg["t0"]) / (sg["t1"] - sg["t0"]))
            if sg["c"] is None:
                return sg["x0"] + (sg["x1"] - sg["x0"]) * u, sg["y0"] + (sg["y1"] - sg["y0"]) * u
            v = 1 - u
            cx, cy = sg["c"]
            return (v * v * sg["x0"] + 2 * v * u * cx + u * u * sg["x1"],
                    v * v * sg["y0"] + 2 * v * u * cy + u * u * sg["y1"])
        return x, y

    def holder_at(self, t):
        holder = self.s["b0"][2]
        for f in self.flights:
            if t < f["t0"]:
                break
            if t >= f["t1"]:
                holder = f["holder"]
                continue
            return -1
        return holder

    def side(self, i):
        return "home" if self.s["pl"][i][1] == 0 else "away"


def _idx(s, pid):
    return next((i for i, row in enumerate(s["pl"]) if row[0] == pid), None)


def _in_net(x, y):
    """Top agin icinde: kale cizgisinin arkasi, ag derinligi kadar, direkler arasi."""
    return (L < x <= L + pitch.GOAL_DEPTH or -pitch.GOAL_DEPTH <= x < 0) and POST_LO < y < POST_HI


def _height(f, u):
    return f["h"] * u if f["prof"] == 1 else 4 * f["h"] * u * (1 - u)


def _line_crossings(f):
    """Ucusun kale cizgilerini (x = 0 / 105) kestigi noktalar: (u, y, h)."""
    out = []
    for gx in (0.0, L):
        dx = f["x"] - f["sx"]
        if abs(dx) < 1e-9:
            continue
        u = (gx - f["sx"]) / dx
        if 0 < u <= 1:
            out.append((u, f["sy"] + (f["y"] - f["sy"]) * u, _height(f, u)))
    return out


def _shot_flight(sim, shooter):
    """Sut: sutcunun ayagindan cikan SON ucus (oncesinde sutcu pas vermis olabilir: zincirde topu kazanip verdi)."""
    return next((f for f in reversed(sim.flights) if f["from"] == shooter and f["holder"] != shooter
                 and f["prof"] != ma.RESPOT), None)


def _attacks_right(r, frames, i, side):
    right = pitch.home_attacks_right(frames[i])
    if frames[i].phase == "Penaltılar":
        return pitch.SHOOTOUT_GOAL_RIGHT
    return right if side == "home" else not right


# ===========================================================================
# 1) Her olay turu bir betik uretir; sema
# ===========================================================================

def test_every_event_type_produces_a_script(scripted):
    seen = Counter(f[i].event.type for _r, f, i, _s in scripted)
    wanted = {t.value for t in EventType} - {"TACTICAL_CHANGE"}          # taktik: ayri test (canli mac)
    assert wanted <= set(seen), wanted - set(seen)
    for _r, f, i, s in scripted:
        n = len(s["pl"])
        assert s["v"] == ma.SCRIPT_VERSION and s["f"] == f[i].index and s["k"] == f[i].event.type
        assert len(s["p0"]) == len(s["p1"]) == 2 * n and 20 <= n <= 23
        assert s["mv"] or s["fx"] or s["bl"], f[i].event.type
        assert all(0 <= m[0] < n for m in s["mv"]) and all(-1 <= fx[3] < n for fx in s["fx"])
        assert all(fx[2] in ma.FX_CODES for fx in s["fx"])
        assert all(-1 <= b[5] < n for b in s["bl"]) and -1 <= s["b0"][2] < n and -1 <= s["b1"][2] < n
        assert all(0 <= k < n for k in s["hl"])


def test_tactical_change_and_live_snapshots_produce_scripts():
    home, away = make_spread_team(1, "Ev", 80, rng_seed=4), make_spread_team(2, "Dep", 79, rng_seed=4)
    live = LiveMatch.create(MatchEngine(home, away, seed=4), 1)
    while live.engine.minute < 30 and not live.finished:
        live.tick()
        live.resume()                                                      # otomatik duraklamalari gec
    live.pause()
    from instructions import Mentality
    event = live.set_team_instructions(live.instructions.with_changes({"mentality": Mentality.ALL_OUT_ATTACK}))
    assert event is not None
    live.resume()
    while live.engine.minute < 60 and not live.finished:
        live.tick()
        live.resume()
        snap = live.snapshot()
        frames = build_timeline(snap)
        s = ma.frame_script(snap, frames, len(frames) - 1, len(frames) - 2 if len(frames) > 1 else None)
        assert s["pl"] and s["T"] <= frames[-1].dwell_ms * ma.FIT
    snap = live.snapshot()
    frames = build_timeline(snap, include_hidden=True)
    tac = [i for i, f in enumerate(frames) if f.event.type == "TACTICAL_CHANGE"]
    assert tac
    s = ma.frame_script(snap, frames, tac[0], tac[0] - 1)
    assert any(fx[2] == "tac" for fx in s["fx"]) and s["mv"]


# ===========================================================================
# 2) Durustluk: ISKA / GOL / KURTARIS
# ===========================================================================

def test_miss_never_enters_the_goal(scripted):
    checked = Counter()
    for _r, f, i, s in _events(scripted, "MISS", "PENALTY_SHOOTOUT"):
        ev = f[i].event
        if ev.type == "PENALTY_SHOOTOUT" and ev.detail == "scored":
            continue
        sim = Sim(s)
        for fl in sim.flights:
            assert not _in_net(fl["x"], fl["y"]), (ev.type, ev.description, fl)
            if fl["prof"] == ma.RESPOT:
                continue                                                     # oyun disi yerlestirme: gorunmez
            for u, y, h in _line_crossings(fl):
                assert not (POST_LO - 0.3 < y < POST_HI + 0.3) or h > ma.CROSSBAR + 0.3, (ev.description, u, y, h)
        assert not any(fx[2] == "net" for fx in s["fx"])
        checked[ma.miss_kind(ev.description) if ev.type == "MISS" else ev.detail] += 1
    assert checked["wide"] and checked["over"], checked


def test_goal_goes_into_the_net_off_the_real_scorer(scripted):
    goals = _events(scripted, "GOAL")
    assert len(goals) >= 10
    for r, f, i, s in goals:
        ev = r.events[f[i].index]
        sim = Sim(s)
        scorer = _idx(s, ev.player_id)
        assert scorer is not None and scorer in s["hl"] and sim.side(scorer) == f[i].event.side
        shot = _shot_flight(sim, scorer)
        assert shot is not None, ev.description
        right = _attacks_right(r, f, i, f[i].event.side)
        assert _in_net(shot["x"], shot["y"]) and (shot["x"] > L if right else shot["x"] < 0), shot
        assert any(fx[2] == "net" for fx in s["fx"])
        assert all(not _in_net(fl["x"], fl["y"]) for fl in sim.flights if fl is not shot)


def _real_keeper(r, index, side):
    rows = pitch._on_pitch(pitch._tracks(r), side, index)
    return next((t.player.id for t, role in rows if role is Position.GK), None)


def test_save_ends_on_the_real_keeper_of_the_defending_side(scripted):
    saves = _events(scripted, "SAVE")
    assert len(saves) >= 20
    kinds = Counter()
    for r, f, i, s in saves:
        ev = f[i].event
        defending = "away" if ev.side == "home" else "home"
        keeper_pid = _real_keeper(r, f[i].index, defending)
        k = _idx(s, keeper_pid)
        assert k is not None and s["pl"][k][3] == 1 and k in s["hl"], ev.description
        sim = Sim(s)
        shooter = _idx(s, r.events[f[i].index].player_id)
        shot = _shot_flight(sim, shooter)
        assert shot is not None
        kx, ky = sim.pos(k, shot["t1"])
        assert math.dist((kx, ky), (shot["x"], shot["y"])) < 0.6, (ev.description, (kx, ky), shot)
        assert not _in_net(shot["x"], shot["y"])
        assert any(fx[2] in ("save", "dive") and fx[3] == k for fx in s["fx"])
        kinds[ma.save_kind(ev.description)] += 1
        if shot["holder"] == k:
            kinds["caught"] += 1
    assert kinds["catch"] and kinds["parry"] and kinds["caught"], kinds


def test_shootout_kicks_use_one_goal_the_taker_and_the_defending_keeper(scripted):
    kicks = _events(scripted, "PENALTY_SHOOTOUT")
    assert kicks
    for r, f, i, s in kicks:
        ev = r.events[f[i].index]
        sim = Sim(s)
        taker = _idx(s, ev.player_id)
        shot = _shot_flight(sim, taker)
        assert shot is not None and shot["t0"] > 0
        assert shot["x"] > L - 1.0 if pitch.SHOOTOUT_GOAL_RIGHT else shot["x"] < 1.0
        if ev.detail == "scored":
            assert _in_net(shot["x"], shot["y"])
        defending = "away" if f[i].event.side == "home" else "home"
        k = next(n for n, row in enumerate(s["pl"]) if row[3] == 1 and sim.side(n) == defending)
        kx, _ky = sim.pos(k, shot["t0"])
        assert kx > L - 2.0                                                  # savunan kaleci cizgide
        others = [n for n in range(len(s["pl"])) if n not in (taker,) and s["pl"][n][3] == 0]
        assert all(abs(sim.pos(n, shot["t0"])[0] - ma.CX) < 30 for n in others)      # digerleri orta yuvarlakta


# ===========================================================================
# 3) Top dogru takimda / gercek oyuncular
# ===========================================================================

def test_ball_is_with_the_shooter_when_the_shot_is_struck(scripted):
    for r, f, i, s in _events(scripted, "GOAL", "SAVE", "MISS"):
        ev = r.events[f[i].index]
        sim = Sim(s)
        shooter = _idx(s, ev.player_id)
        assert shooter is not None and sim.side(shooter) == f[i].event.side
        shot = _shot_flight(sim, shooter)
        assert shot is not None, ev.description
        assert shot["from"] == shooter                                       # top o anda sutcude
        assert math.dist(sim.pos(shooter, shot["t0"]), (shot["sx"], shot["sy"])) < 0.2


def test_chain_players_receive_the_ball_in_order(scripted):
    """Gol karelerinde (kritik bekleme) zincirin gercek oyunculari sirayla topu alir; son pasi veren golcuye verir."""
    checked = 0
    for r, f, i, _s in _events(scripted, "GOAL"):
        ctx = ma.MatchCtx(r)
        s = ma.frame_script(r, f, i, None, ctx)                               # baglamsiz: zincirin tamami
        chain = ctx.chain(f[i].index)
        links = [r.events[j] for j in chain[:-1] if r.events[j].player_id is not None
                 and r.events[j].detail != "pressure"]
        if not links:
            continue
        sim = Sim(s)
        receivers = ([s["b0"][2]] if s["b0"][2] >= 0 else []) + [fl["holder"] for fl in sim.flights
                                                                  if fl["holder"] >= 0]
        pids = [s["pl"][k][0] for k in receivers]
        order = [ev.player_id for ev in links] + [r.events[f[i].index].player_id]
        # her zincir oyuncusu topu alir (ardisik tekrarlar tek sayilir) ve sira korunur
        compact = [p for n, p in enumerate(pids) if n == 0 or p != pids[n - 1]]
        pos = 0
        for pid in order:
            assert pid in compact[pos:], (order, compact)
            pos = compact.index(pid, pos)
        assert all(_idx(s, ev.player_id) in s["hl"] for ev in links)
        side = f[i].event.side
        for k in receivers:
            if s["pl"][k][0] in order:
                assert sim.side(k) == side
        checked += 1
    assert checked >= 5


REST_SIDE = {"SAVE": "defend", "FOUL": "other", "YELLOW_CARD": "other", "OFFSIDE": "defend", "CORNER": "defend",
             "BUILD_UP": "same"}


def test_ball_ends_with_the_right_team(scripted):
    counted = Counter()
    for _r, f, i, s in scripted:
        ev = f[i].event
        rule = REST_SIDE.get(ev.type)
        if ev.type == "SAVE" and ma.save_kind(ev.description) != "catch":
            rule = None
        if ev.type == "RED_CARD":
            rule = "other"
        if ev.type == "MISS":
            rule = "defend"
        if rule is None or ev.side not in ("home", "away"):
            continue
        holder = s["b1"][2]
        if ev.type == "BUILD_UP" and ev.detail == "final" and holder < 0:
            continue
        assert holder >= 0, (ev.type, ev.description)
        side = "home" if s["pl"][holder][1] == 0 else "away"
        want = ev.side if rule == "same" else ("away" if ev.side == "home" else "home")
        assert side == want, (ev.type, ev.description, side)
        counted[ev.type] += 1
    assert counted["SAVE"] and counted["FOUL"] and counted["MISS"] and counted["BUILD_UP"], counted


def test_card_injury_and_substitution_mark_the_real_players(scripted):
    for r, f, i, s in _events(scripted, "YELLOW_CARD", "RED_CARD", "INJURY", "SUBSTITUTION"):
        ev = r.events[f[i].index]
        k = _idx(s, ev.player_id)
        assert k is not None and k in s["hl"], ev.description
        codes = {fx[2] for fx in s["fx"] if fx[3] == k}
        if ev.type is EventType.YELLOW_CARD:
            assert "yc" in codes
        elif ev.type is EventType.RED_CARD:
            assert "rc" in codes and s["pl"][k][6] == 2                     # kirmizi: sahadan cikar
            if ev.detail == "second_yellow":
                assert "yc" in codes
        elif ev.type is EventType.INJURY:
            assert {"inj", "fall"} <= codes
        else:
            assert "sub_in" in codes and s["pl"][k][6] == 1
            out = [n for n, row in enumerate(s["pl"]) if row[6] == 2]
            assert len(out) == 1 and any(fx[2] == "sub_out" and fx[3] == out[0] for fx in s["fx"])
            assert s["pl"][out[0]][1] == s["pl"][k][1]
            gone = next(t for t in pitch._tracks(r) if t.player.id == s["pl"][out[0]][0])
            assert gone.exit == f[i].index                                   # cikan gercekten bu olayda cikti


def test_foul_uses_the_real_fouler_and_the_ball_goes_to_the_other_team(scripted):
    for r, f, i, s in _events(scripted, "FOUL"):
        ev = r.events[f[i].index]
        k = _idx(s, ev.player_id)
        assert k is not None and k in s["hl"]
        assert any(fx[2] == "wh" for fx in s["fx"])
        holder = s["b1"][2]
        assert holder >= 0 and s["pl"][holder][1] != s["pl"][k][1]
        assert holder not in s["hl"]                                         # faule ugrayan temsili: vurgulanmaz


def test_offside_raises_the_flag_on_the_real_player(scripted):
    offs = _events(scripted, "OFFSIDE")
    assert offs
    for r, f, i, s in offs:
        ev = r.events[f[i].index]
        k = _idx(s, ev.player_id)
        assert k is not None and k in s["hl"]
        flag = next(fx for fx in s["fx"] if fx[2] == "fl")
        assert flag[5] < 0 or flag[5] > W                                    # bayrak yan cizgide


def _corner_taker_by_engine_rule(r, index, side):
    team = r.home if side == "home" else r.away
    rows = pitch._on_pitch(pitch._tracks(r), side, index)
    ids = {t.player.id for t, _ in rows}
    if team.roles.corner_taker_id in ids:
        return team.roles.corner_taker_id
    pool = [t.player for t, role in rows if role is not Position.GK]
    return max(pool, key=lambda p: (team_roles.corner_skill(p), -p.id)).id


def test_corner_taker_follows_the_engine_rule_and_runs_to_the_right_flag(scripted):
    corners = [(r, f, i, s) for r, f, i, s in scripted
               if f[i].event.type == "CORNER" or (f[i].event.type in SHOTS and f[i].event.detail == "corner")]
    assert len(corners) >= 10
    for r, f, i, s in corners:
        ev = r.events[f[i].index]
        side = f[i].event.side
        taker_pid = _corner_taker_by_engine_rule(r, f[i].index, side)
        if ev.type is EventType.CORNER and "hazırlan" in ev.description and ev.player and ev.player in ev.description:
            taker_pid = ev.player_id                                         # cumle aticiyi soyluyor: o kullanir
        if ev.type is not EventType.CORNER and ev.player_id == taker_pid:
            continue                                                         # tek saha oyuncusu: kendi korneri
        k = _idx(s, taker_pid)
        assert k is not None and k in s["hl"], ev.description
        sim = Sim(s)
        flag = next(fx for fx in s["fx"] if fx[2] == "fl")
        right = _attacks_right(r, f, i, side)
        assert (flag[4] > L - 2 if right else flag[4] < 2) and (flag[5] < 2 or flag[5] > W - 2)
        cross = next(fl for fl in reversed(sim.flights) if fl["from"] == k and fl["prof"] != ma.RESPOT)
        tx, ty = sim.pos(k, cross["t0"])
        assert math.dist((tx, ty), (flag[4], flag[5])) < 1.5                  # orta bayraktan kalkar


def test_designated_corner_taker_is_used():
    for seed in range(60):
        home = make_spread_team(1, "Ev", 80, spread=6, rng_seed=seed)
        away = make_spread_team(2, "Dep", 79, spread=6, rng_seed=seed)
        mid = next(p for p in home.players if p.position is Position.MID)
        from team_roles import SetPieceRoles
        home.roles = SetPieceRoles(corner_taker_id=mid.id)
        r = MatchEngine(home, away, seed=seed).simulate()
        frames = build_timeline(r, include_hidden=True)
        hits = [i for i, f in enumerate(frames) if f.event.side == "home" and
                (f.event.type == "CORNER" or (f.event.type in SHOTS and f.event.detail == "corner"))
                and mid.entered_minute == 0 and (mid.left_minute is None or f.minute < mid.left_minute)]
        if not hits:
            continue
        s = ma.frame_script(r, frames, hits[0], None)
        k = _idx(s, mid.id)
        assert k is not None and k in s["hl"]
        return
    pytest.fail("atanmis aticili korner bulunamadi")


def test_free_kick_and_penalty_takers_and_set_up(scripted):
    fks = [(r, f, i, s) for r, f, i, s in scripted if f[i].event.type in SHOTS and f[i].event.detail == "free_kick"]
    pens = [(r, f, i, s) for r, f, i, s in scripted if f[i].event.type in SHOTS and f[i].event.detail == "penalty"]
    assert fks and pens
    for r, f, i, s in fks:
        ev = r.events[f[i].index]
        sim = Sim(s)
        k = _idx(s, ev.player_id)
        shot = _shot_flight(sim, k)
        bx, by = sim.pos(k, shot["t0"])
        defending = [n for n in range(len(s["pl"])) if sim.side(n) != sim.side(k) and s["pl"][n][3] == 0]
        wall = [n for n in defending if abs(math.dist(sim.pos(n, shot["t0"]), (bx, by)) - ma.WALL_DIST) < 2.2]
        assert len(wall) >= 3, (ev.description, len(wall))                  # baraj
        assert any(fx[2] == "wh" for fx in s["fx"])
    for r, f, i, s in pens:
        ev = r.events[f[i].index]
        sim = Sim(s)
        k = _idx(s, ev.player_id)
        shot = _shot_flight(sim, k)
        right = _attacks_right(r, f, i, f[i].event.side)
        for n in range(len(s["pl"])):
            if n == k or s["pl"][n][3] == 1:
                continue
            x, y = sim.pos(n, shot["t0"])
            depth = x if right else L - x
            in_box = depth > ma.BOX_D + 0.2 and ma.BOX_LO < y < ma.BOX_HI
            assert not in_box, (ev.description, s["pl"][n][2], x, y)         # ceza sahasi bos


# ===========================================================================
# 4) Determinizm, motor dokunulmazligi
# ===========================================================================

def _digest(results) -> str:
    h = hashlib.sha256()
    for r in results:
        frames = build_timeline(r)
        for s in ma.all_scripts(r, frames):
            h.update(json.dumps(s, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def test_scripts_are_deterministic_across_processes():
    here = _digest([play(80, 78, seed=s, spread=6) for s in (2, 5)])
    assert here == _digest([play(80, 78, seed=s, spread=6) for s in (2, 5)])
    code = (f"import sys; sys.path[:0] = [r'{ROOT}', r'{ROOT / 'tests'}']; from tests.test_match_anim import _digest; "
            "from engine_stats import play; print(_digest([play(80, 78, seed=s, spread=6) for s in (2, 5)]))")
    env = {**__import__("os").environ, "CM_TEST_NO_DB": "1", "PYTHONHASHSEED": "12345"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=env, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == here


def test_building_scripts_does_not_touch_the_engine():
    def fingerprint(r):
        return [(e.minute, e.type.value, e.player_id, e.home_score, e.away_score) for e in r.events]

    reference = fingerprint(play(80, 78, seed=9, spread=6))
    home, away = make_spread_team(1, "Ev", 80, spread=6, rng_seed=9), make_spread_team(2, "Dep", 78, spread=6,
                                                                                         rng_seed=9)
    live = LiveMatch.create(MatchEngine(home, away, seed=9), None)
    while not live.finished:
        live.tick()
        live.resume()
        snap = live.snapshot()
        frames = build_timeline(snap)
        if frames:
            ma.frame_script(snap, frames, len(frames) - 1, len(frames) - 2 if len(frames) > 1 else None)
    assert fingerprint(live.result()) == reference


def test_module_uses_only_seeded_presentation_randomness():
    source = (ROOT / "match_anim.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "hash"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "random":
            assert node.func.attr == "Random", node.func.attr                # modul duzeyi random.* yok
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "Random"]
    assert len(calls) == 1 and "zlib.crc32" in ast.unparse(calls[0])      # tek uretici: crc32(tohum|olay|tuz)
    banned_attrs = {"chance_quality", "display_probability", "tags", "overall", "rating", "narrator", "_nar"}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attrs & banned_attrs, attrs & banned_attrs
    rng_owners = {ast.unparse(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute) and node.attr == "rng"}
    assert rng_owners <= {"self.ctx", "ctx"}, rng_owners                  # motorun RNG'si (engine.rng) okunmaz
    assert "import streamlit" not in source and "database" not in source


# ===========================================================================
# 5) Bekleme butcesi, yuk boyutu, gizli sayi yok
# ===========================================================================

def test_every_script_fits_the_frame_dwell(scripted):
    for _r, f, i, s in scripted:
        budget = f[i].dwell_ms * ma.FIT
        assert 0 < s["T"] <= budget + 1
        assert all(m[2] <= s["T"] + 1 for m in s["mv"]) and all(b[1] <= s["T"] + 1 for b in s["bl"])
        for fx in s["fx"]:
            assert fx[0] <= s["T"]
            if fx[2] not in ma.PERSISTENT_FX:
                assert fx[0] + fx[1] <= s["T"] + 1


def test_time_warp_keeps_key_moments_and_fits():
    warp, rate = ma.time_warp([(1000, 1400)], 3000, 1600)
    assert warp(3000) <= 1600 + 1e-6 and rate == 1.0
    assert abs((warp(1400) - warp(1000)) - 400) < 1e-6                        # asil an hizini korur
    warp, rate = ma.time_warp([(0, 3000)], 3000, 1500)
    assert abs(warp(3000) - 1500) < 1e-6 and rate == 0.5
    warp, rate = ma.time_warp([(100, 200)], 800, 1000)
    assert warp(800) == 800 and rate == 1.0


def test_speed_factor_scales_time_in_the_component():
    js = (ROOT / "web_assets" / "match_pitch.js").read_text(encoding="utf-8")
    assert "(now - st.start) / st.k" in js                                    # betik zamani = gecen sure / hiz
    assert "requestAnimationFrame" in js and "setInterval" not in js


def test_payload_is_small_and_reported(scripted, capsys):
    sizes = [ma.script_bytes(s) for *_x, s in scripted]
    payload = ma.component_payload(scripted[0][3], "play", 1.0, (("#ff0000", "#ffff00"), ("#000080", "#ffff00")),
                                   ("Ev", "Dep"))
    whole = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    mean = sum(sizes) / len(sizes)
    with capsys.disabled():
        print(f"\n[14T] betik boyutu: ortalama {mean:.0f} B, en fazla {max(sizes)} B, "
              f"%95 {sorted(sizes)[int(0.95 * len(sizes))]} B ({len(sizes)} kare); paket {whole} B")
    assert mean < 3000 and max(sizes) < 6000 and whole < max(sizes) + 400


ALLOWED_KEYS = {"v", "f", "pf", "T", "dw", "dir", "snap", "k", "pl", "p0", "p1", "mv", "b0", "bl", "b1", "fx", "hl",
                "cap", "min", "ph", "sc", "pen"}


def test_no_hidden_numbers_in_the_payload(scripted):
    for r, f, i, s in scripted[::3]:
        assert set(s) == ALLOWED_KEYS
        text = json.dumps(s, ensure_ascii=False)
        for banned in ("chance_quality", "display_probability", "quality", "xG", "olasılık", "kalite", "overall",
                       "rating", "priority", '"big"', '"good"', '"far"', '"normal"'):
            assert banned not in text, banned
        for row in s["pl"]:
            pid, side, name, gk, energy, yellow, status = row
            assert side in (0, 1) and gk in (0, 1) and energy in (0, 1, 2, 3) and yellow in (0, 1, 2)
            assert status in (0, 1, 2) and isinstance(name, str) and len(name) <= 11
        ev = r.events[f[i].index]
        assert str(ev.chance_quality) not in text or ev.chance_quality is None or \
            len(str(ev.chance_quality)) < 5


def test_payload_names_are_plain_text_and_script_is_safe():
    js = (ROOT / "web_assets" / "match_pitch.js").read_text(encoding="utf-8")
    code = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", js)
    css = (ROOT / "web_assets" / "match_pitch.css").read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "fetch(",
                   "XMLHttpRequest", "WebSocket", "import(", "document.write", "setAttribute(\"on"):
        assert banned not in code, banned
    assert "http" not in code.replace("http://www.w3.org/2000/svg", "") and "url(" not in css
    assert "textContent" in code
    home = make_spread_team(1, "<b>Ev</b>", 80, rng_seed=1)
    home.players[3].name = '<img src=x onerror="alert(1)">'
    r = MatchEngine(home, make_spread_team(2, "Dep", 79, rng_seed=1), seed=1).simulate()
    frames = build_timeline(r)
    html = ma.standalone_html(r, frames, (("#ff0000", "#ffffff"), ("#0000ff", "#ffffff")), js, css)
    assert "<img src=x" not in html and "<b>Ev</b>" not in html


def test_standalone_demo_embeds_every_visible_frame():
    r = play(80, 78, seed=3, spread=6)
    frames = build_timeline(r)
    js = (ROOT / "web_assets" / "match_pitch.js").read_text(encoding="utf-8")
    css = (ROOT / "web_assets" / "match_pitch.css").read_text(encoding="utf-8")
    html = ma.standalone_html(r, frames, (("#ff0000", "#ffffff"), ("#0000ff", "#ffffff")), js, css)
    blob = re.search(r'<script id="ofm-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    data = json.loads(blob.replace("<\\/", "</"))
    assert len(data["scripts"]) == len(frames) and data["scripts"][0]["k"] == "KICK_OFF"
    assert "export default" not in html and "const ofmPitch = function" in html


def test_keeper_kits_differ_from_both_teams_and_the_ball():
    for home, away in (("#ff0000", "#000080"), ("#000000", "#ffffff"), ("#fdd835", "#26c6da"),
                       ("#2e7d32", "#ffffff")):
        hk, ak = ma.keeper_colors(home, away)
        assert hk != ak and "#ffffff" not in (hk, ak)
        for kit in (hk, ak):
            assert min(ma._color_gap(kit, c) for c in (home, away, ma.PITCH_GREEN)) > 60


def test_text_hints_come_from_the_sentence_only():
    assert ma.miss_kind("X (Ev) uzaktan denedi, top üstten auta gitti.") == "over"
    assert ma.miss_kind("X (Ev) DİREĞE vurdu! Top oyun alanına döndü") == "post"
    assert ma.miss_kind("X (Ev) topu üst direğe gönderdi! Az kalsın!") == "bar"
    assert ma.miss_kind("X (Ev) ceza noktası civarından vurdu, top direğin yanından çıktı.") == "wide"
    assert ma.miss_kind("X serbest vuruşu barajdan döndü.") == "wall"
    assert ma.save_kind("X kaleye gönderdi, K kornere çeldi.") == "parry"
    assert ma.save_kind("X karşı karşıya! K ayaklarıyla KURTARIYOR!") == "block"
    assert ma.save_kind("X denedi ama K topu tuttu.") == "catch"
    assert ma.shot_form("X (Ev) kafayı vurdu, K kurtardı") == "header"
    assert ma.shot_form("X (Ev) uzaktan patlattı") == "long"
    assert ma.shot_form("X vurdu", "corner") == "header"
