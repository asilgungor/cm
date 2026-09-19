"""
Faz 14A -- mac gunu ekraninin SAF yardimcilari (match_day_view.py ust bolumu, pitch.build_board / board_svg).
Veritabani ve tarayici gerekmez:

  CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_match_day_view.py

Kilitlenen sozlesmeler (kabul olcutleri):
    * time.sleep YOK (match_day_view.py ve web_app.py AST taramasi)
    * bekleme orani rutin : ana : kritik = 1 : 2 : 3,3 (±%10); "Anında" = 0
    * canli not == motorun notu (200 tohum, mac sonunda her oyuncu)
    * canli puan durumu, "Son 5 dk", tek kaynakli topla oynama (son kare == summarize)
    * ozet modlari (tam ⊇ genis ⊇ onemli; goller her modda), asistan notu (K12), afis HTML'i
    * sekil tahtasi (statik yedek, "Hareketli" kapali): top / pas oku yok, deterministik, saha icinde, ust uste binme yok
    * 14T canli 2D saha: bilesen kipi (oynat / durdur / otomatik duraklamada oynat-don / Anında ve geri sarmada son poz),
      betik bir onceki GOSTERILEN kareden devam eder, surekli oynatmada ardisik betikler dikissiz baglanir
    * determinizm: "Anında" run() ile adim adim oynatma (duraklatip devam ederek) ayni maci verir
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import itertools
import math
import random
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import match_day_view as md  # noqa: E402
import pitch  # noqa: E402
import web_view  # noqa: E402
from instructions import (  # noqa: E402
    DEFAULT_INSTRUCTIONS,
    Mentality,
    Pressing,
    TeamInstructions,
    Tempo,
)
from live_match import LiveMatch  # noqa: E402
from match_engine import (  # noqa: E402
    DWELL_CRUCIAL,
    DWELL_MAIN,
    DWELL_ROUTINE,
    EngineConfig,
    MatchEngine,
    MatchPlayer,
    MatchResult,
    MatchTeam,
)
from match_feed import FeedEvent, Frame, build_timeline, summarize  # noqa: E402
from models import Position  # noqa: E402
from ofm_theme import AA_TEXT, contrast_ratio  # noqa: E402

# ---------------------------------------------------------------------------
# Yardimcilar (tests/test_live_match.py ile ayni sentetik kadro; test_match_engine DB yokladigi icin kopya)
# ---------------------------------------------------------------------------

COMPOSITION = {Position.GK: 2, Position.DEF: 4, Position.MID: 5, Position.FWD: 4}


def make_player(pid: int, pos: Position, ovr: int) -> MatchPlayer:
    gk = ovr + 6 if pos is Position.GK else 30
    return MatchPlayer(
        id=pid, name=f"P{pid}-{pos.value}", position=pos, age=26, overall=ovr,
        pace=ovr, shooting=ovr + (4 if pos is Position.FWD else -10),
        passing=ovr + (3 if pos is Position.MID else -5),
        defending=ovr + (6 if pos is Position.DEF else -15),
        dribbling=ovr, goalkeeping=gk, form=50, morale=70,
    )


def make_team(tid: int, name: str, ovr: int) -> MatchTeam:
    players, pid = [], tid * 100
    for pos, n in COMPOSITION.items():
        for _ in range(n):
            pid += 1
            players.append(make_player(pid, pos, ovr))
    return MatchTeam(id=tid, name=name, reputation=80, players=players)


def engine(seed: int, home_ovr: int = 80, away_ovr: int = 78, cfg: EngineConfig | None = None,
           home_name: str = "Ev", away_name: str = "Dep") -> MatchEngine:
    return MatchEngine(make_team(1, home_name, home_ovr), make_team(2, away_name, away_ovr), seed=seed, config=cfg)


def play(seed: int, home_ovr: int = 80, away_ovr: int = 78) -> MatchResult:
    return engine(seed, home_ovr, away_ovr).simulate()


def fingerprint(r: MatchResult) -> str:
    ev = "\n".join(f"{e.minute}|{e.added_time}|{e.type.value}|{e.team_id}|{e.player_id}|{e.home_score}|"
                   f"{e.away_score}|{e.detail}|{e.description}" for e in r.events)
    pl = "\n".join(f"{p.id}|{p.entered_minute}|{p.left_minute}|{p.rating}|{p.energy!r}|{p.goals}|{p.assists}|"
                   f"{p.shots}|{p.saves}|{p.yellow_cards}|{p.sent_off}|{p.injured}|{p.substituted}"
                   for t in (r.home, r.away) for p in t.players)
    return hashlib.sha256((ev + "#" + pl + f"#{r.home.stats}|{r.away.stats}").encode()).hexdigest()[:16]


def frame(dwell: int, *, etype: str = "MISS", priority: int = 55, side: str | None = "home", highlight="chance",
          player: str | None = "Oyuncu", team: str | None = "Takım", description: str = "Olay") -> Frame:
    return Frame(index=0, minute=10, added_time=0, display_minute="10'", elapsed=10, phase="1. Yarı",
                 home_score=0, away_score=0,
                 event=FeedEvent(type=etype, label="ŞUT", highlight=highlight, side=side, team=team, player=player,
                                 description=description, priority=priority, dwell_ms=dwell))


def _calls_sleep(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sleep_names = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                   and node.module == "time" for alias in node.names if alias.name == "sleep"}
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "sleep":
            lines.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id in sleep_names:
            lines.append(node.lineno)
    return lines


# ---------------------------------------------------------------------------
# 1) Bloke dongu yok; bekleme kademeleri
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["match_day_view.py", "web_app.py"])
def test_no_time_sleep_calls(name):
    assert _calls_sleep(ROOT / name) == []


def test_dwell_tiers_ratio_and_instant_speed():
    routine, main, crucial = (md.next_due(0.0, frame(d), 1.0) for d in (DWELL_ROUTINE, DWELL_MAIN, DWELL_CRUCIAL))
    assert routine == pytest.approx(0.9) and main == pytest.approx(1.8) and crucial == pytest.approx(3.0)
    assert main / routine == pytest.approx(2.0, rel=0.10)
    assert crucial / routine == pytest.approx(3.3, rel=0.10)
    for label, factor in md.SPEED_FACTORS.items():
        assert md.next_due(5.0, frame(DWELL_CRUCIAL), factor) == pytest.approx(5.0 + 3.0 * factor), label
    assert md.SPEED_FACTORS["Anında"] == 0 and md.next_due(7.5, frame(DWELL_CRUCIAL), 0.0) == 7.5
    assert md.SPEED_FACTORS["Yavaş"] > md.SPEED_FACTORS["Normal"] > md.SPEED_FACTORS["Hızlı"] > 0
    # gercek akista da kademeler: goller kritik, rutin olaylar kisa
    frames = build_timeline(play(4))
    goals = [f for f in frames if f.event.type == "GOAL"]
    assert goals and all(f.dwell_ms == DWELL_CRUCIAL for f in goals)
    assert {f.dwell_ms for f in frames} <= {DWELL_ROUTINE, DWELL_MAIN, DWELL_CRUCIAL}


def test_only_resume_skips_the_full_page_redraw():
    paused = ("live", 7, True, False, False)
    running = ("live", 7, False, False, False)
    assert md._page_needs_redraw(paused, running) is False                     # DEVAM: tek parca calismasi
    assert md._page_needs_redraw(running, paused) is True                      # DURDUR: panel (degisiklik) acilir
    assert md._page_needs_redraw(running, ("live", 7, False, True, False)) is True     # mac sonu ozeti
    assert md._page_needs_redraw(running, None) is True and md._page_needs_redraw(None, running) is True
    assert md._page_needs_redraw(running, ("live", 8, False, False, False)) is True
    assert md._page_needs_redraw(("replay", 1, True, False), ("replay", 1, False, False)) is False


def test_playback_is_timer_driven_not_polling():
    """Parca kare basina bir kez: istemci zamanlayicisi (setTimeout) tetik yollar; periyot yalnizca yedek."""
    assert md.MAX_ENGINE_STEPS == 8 and md.MIN_TIMER_MS <= 100 and md.FALLBACK_TICK_S >= 1.0
    assert "setTimeout" in md.TIMER_JS and 'setTriggerValue("tick"' in md.TIMER_JS
    assert "setInterval" not in md.TIMER_JS and "innerHTML" not in md.TIMER_JS
    assert getattr(md.cb_md_tick, "requires_auth", False) is True


# ---------------------------------------------------------------------------
# 2) Canli not == motor notu
# ---------------------------------------------------------------------------

def test_live_rating_equals_engine_rating_for_200_seeds():
    checked = 0
    spread = set()
    for seed, (home, away) in itertools.product(range(100), ((80, 78), (86, 76))):
        result = play(seed, home, away)
        for team, other in ((result.home, result.away), (result.away, result.home)):
            for p in team.players:
                if not p.played:
                    assert md.live_rating(p, team.stats.goals, other.stats.goals, result.end_minute) is None
                    continue
                live = md.live_rating(p, team.stats.goals, other.stats.goals, result.end_minute)
                assert live == p.rating, (seed, home, away, p.name)
                checked += 1
                spread.add(live)
    assert checked > 200 * 22 and len(spread) > 20


def test_live_rating_moves_during_the_match_and_uses_only_visible_numbers():
    live = LiveMatch.create(engine(3), 1)
    seen = set()
    while not live.finished:
        live.run(max_steps=15)
        live.resume()
        snap = live.snapshot()
        for p in snap.home.players:
            if p.played:
                seen.add(md.live_rating(p, snap.home.stats.goals, snap.away.stats.goals, live.engine.minute))
    assert len(seen) > 5
    # formul, sans kalitesine / gizli ozelliklere dokunmaz: yalnizca gorunen sayaclar
    source = inspect.getsource(md.live_rating).split('"""')[-1]
    for word in ("chance_quality", "overall", "shooting", "passing", "defending", "attributes", "form", "morale"):
        assert word not in source


# ---------------------------------------------------------------------------
# 3) Canli puan durumu
# ---------------------------------------------------------------------------

ROWS = [
    md.TableRow(1, "Alfa", played=3, won=2, drawn=0, lost=1, goals_for=5, goals_against=3, points=6),
    md.TableRow(2, "Beta", played=3, won=2, drawn=0, lost=1, goals_for=4, goals_against=3, points=6),
    md.TableRow(3, "Gama", played=3, won=1, drawn=1, lost=1, goals_for=3, goals_against=3, points=4),
    md.TableRow(4, "Delta", played=3, won=0, drawn=1, lost=2, goals_for=1, goals_against=4, points=1),
]


def test_live_table_draw_win_and_loss():
    draw = md.live_table(ROWS, 3, 4, 1, 1)
    gama, delta = (next(r for r in draw if r.team_id == tid) for tid in (3, 4))
    assert (gama.played, gama.drawn, gama.points, gama.goals_for) == (4, 2, 5, 4)
    assert (delta.played, delta.drawn, delta.points, delta.goal_difference) == (4, 2, 2, -3)
    assert [r.name for r in draw] == ["Alfa", "Beta", "Gama", "Delta"]

    win = md.live_table(ROWS, 4, 1, 3, 0)                       # Delta evde Alfa'yi 3-0 yeniyor
    assert [r.name for r in win] == ["Beta", "Alfa", "Delta", "Gama"]
    alfa, deltaw = (next(r for r in win if r.team_id == tid) for tid in (1, 4))
    assert (alfa.lost, alfa.points, alfa.goal_difference) == (2, 6, -1)
    assert (deltaw.won, deltaw.points, deltaw.goal_difference) == (1, 4, 0)
    assert win[2].name == "Delta" and win[3].name == "Gama"      # esit puan: averaj (0 = 0) -> atilan gol (4 > 3)

    loss = md.live_table(ROWS, 2, 3, 0, 2)                       # Beta evde Gama'ya 0-2 kaybediyor
    assert [r.name for r in loss] == ["Gama", "Alfa", "Beta", "Delta"]
    assert next(r for r in loss if r.team_id == 2).lost == 2
    assert md.live_table(ROWS, 9, 8, 5, 0) == sorted(ROWS, key=lambda r: (-r.points, -r.goal_difference,
                                                                            -r.goals_for, r.name))


# ---------------------------------------------------------------------------
# 4) Topla oynama: "Son 5 dk" ve tek kaynak
# ---------------------------------------------------------------------------

def test_last_five_minutes_window_from_synthetic_log():
    log = [(m, float(m), 0.0) for m in range(1, 11)]                 # ilk 10 dk hep ev sahibinde
    log += [(m, 10.0, float(m - 10) * 3) for m in range(11, 21)]    # sonra dakikada 3 birim deplasman
    assert md.window_share(log, 10) == (100, 0)
    assert md.window_share(log, 20) == (0, 100)
    assert md.window_share(log, 13) == (18, 82)                      # 8..13: ev 2, dep 9 birim
    assert md.window_share([], 30) is None and md.window_share(log, 0) is None
    assert md.share_at(log, 20) == (25, 75)                          # 10 / (10 + 30)

    result = play(5)
    assert md.possession_bar(result, None, 90) == ("Maç geneli", result.possession_share())
    assert md.possession_bar(result, [], 90) == ("Maç geneli", result.possession_share())
    assert md.possession_bar(result, log, 13) == ("Son 5 dk", (18, 82))


def test_possession_single_source_live_and_final_frame_matches_summarize():
    for seed in (2, 9, 14):
        live = LiveMatch.create(engine(seed), 1)
        while not live.finished:
            live.tick()
            live.resume()
            snap = live.snapshot()
            assert md.stats_possession(snap, live.possession_log, live.elapsed, at_head=True) == \
                snap.possession_share()
            if live.possession_log and snap.possession_share() is not None:
                assert md.share_at(live.possession_log, live.elapsed) == snap.possession_share()
        result = live.result()
        frames = build_timeline(result)
        summary = summarize(result, frames)
        last = frames[-1]
        possession = md.stats_possession(result, live.possession_log, live.elapsed, at_head=True)
        assert possession == (summary.possession_home, summary.possession_away)
        assert web_view.stats_html("Ev", "Dep", last.home, last.away, possession) == web_view.stats_html(
            "Ev", "Dep", summary.home_stats, summary.away_stats, (summary.possession_home, summary.possession_away))


# ---------------------------------------------------------------------------
# 5) Ozet modlari
# ---------------------------------------------------------------------------

def test_summary_modes_shrink_in_order_and_keep_every_goal():
    strictly = 0
    for seed in range(20):
        frames = build_timeline(play(seed))
        sizes = [len(md.mode_frames(frames, mode)) for mode in (md.MODE_FULL, md.MODE_WIDE, md.MODE_HIGHLIGHTS)]
        assert sizes[0] >= sizes[1] >= sizes[2] and len(md.mode_frames(frames, md.MODE_TEXT)) == sizes[0]
        strictly += sizes[0] > sizes[1] > sizes[2]
        goals = [f.index for f in frames if f.event.type == "GOAL"]
        for mode in md.SUMMARY_MODES:
            kept = {f.index for f in md.mode_frames(frames, mode)}
            assert set(goals) <= kept, (seed, mode)
            assert frames[-1].index in kept                          # mac sonu dudugu her modda
        wide = {f.index for f in md.mode_frames(frames, md.MODE_WIDE)}
        assert {f.index for f in md.mode_frames(frames, md.MODE_HIGHLIGHTS)} <= wide
    assert strictly >= 15


def test_step_playback_follows_mode_and_caps_engine_steps():
    live = LiveMatch.create(engine(8), 1)
    frames, cursor, due = md.step_playback(live, [], -1, md.MODE_FULL, 1.0, 100.0)
    assert cursor == 0 and frames[0].event.type == "KICK_OFF" and due == pytest.approx(100.0 + 1.8)
    # onemli anlar modunda bos dakikalar tek periyotta en cok MAX_ENGINE_STEPS adim; imlec hep moda uyan kare
    steps: list[int] = []
    original = live.tick

    def counting_tick():
        steps.append(1)
        return original()

    live.tick = counting_tick
    calls = 0
    while not live.finished:
        steps.clear()
        frames, cursor, due = md.step_playback(live, frames, cursor, md.MODE_HIGHLIGHTS, 1.0, 100.0)
        calls += 1
        assert len(steps) <= md.MAX_ENGINE_STEPS
        if live.paused:
            live.resume()
        elif due > 100.0:
            assert md.in_mode(frames[cursor], md.MODE_HIGHLIGHTS)
    assert calls < 60 and cursor == len(frames) - 1


# ---------------------------------------------------------------------------
# 6) Asistan notu (K12)
# ---------------------------------------------------------------------------

def test_assistant_notes_use_only_visible_data():
    kinds = set()
    sentinel = 0.4321
    forbidden = ("olasılık", "olasilik", "kalite", "xg", "0.43", "0,43", "43.2", "43,2", "beklenen gol")
    for seed in range(100):
        live = LiveMatch.create(engine(seed, 80, 80 + (seed % 7) - 3), 1)
        live.play_to_end()
        result = live.result()
        for event in result.events:
            event.chance_quality = sentinel
        frames = build_timeline(result)
        for minute in md.ASSISTANT_MINUTES:
            note = md.assistant_note(result, frames, "home", minute, live.possession_log)
            assert note is not None and note.minute == minute
            text = note.text.lower()
            assert not any(word in text for word in forbidden), note.text
            kinds.add(note.kind)
    assert len(kinds) >= 3, kinds
    assert md.assistant_note(play(1), build_timeline(play(1)), None, 30) is None      # izleyicide asistan yok


def test_match_day_view_never_reads_hidden_numbers():
    """K12: modul sans kalitesine ve gosterim olasiligina hic dokunmaz (AST)."""
    for name in ("match_day_view.py", "match_anim.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not attrs & {"chance_quality", "display_probability", "possession_weight"}, name


# ---------------------------------------------------------------------------
# 7) HTML: afis, bant, renkler, talimatlar
# ---------------------------------------------------------------------------

def test_banner_and_band_escape_names_and_style_goals():
    evil = "<x>"
    colors = md.club_colors("A", "B")
    goal = frame(DWELL_CRUCIAL, etype="GOAL", priority=95, highlight="goal", player=evil, team=evil,
                 description=f"{evil} golü attı")
    html = md.banner_html(goal, colors)
    assert "<x>" not in html and "&lt;x&gt;" in html and "md-banner goal" in html
    red = frame(DWELL_CRUCIAL, etype="RED_CARD", priority=95, highlight="red", description="kırmızı")
    assert "md-banner red" in md.banner_html(red, colors)
    band = md.band_html(evil, evil, colors, score=(1, 0), clock="10'", phase="1. Yarı", comp=evil,
                        moments={"home": [f"{evil} 10"], "away": []})
    assert "<x>" not in band and "&lt;x&gt;" in band
    assert "prefers-reduced-motion" in md.MD_CSS and "@keyframes mdGoal" in md.MD_CSS
    assert "<x>" not in md.recent_html([goal, goal], 1) and "<x>" not in md.ratings_html(evil, [])


def test_club_colors_are_deterministic_distinct_and_readable():
    for color in md.CLUB_COLORS:
        assert contrast_ratio(md.ink_for(color), color) >= AA_TEXT, color
    first = md.club_colors("Merseyside Reds", "London Gunners")
    assert first == md.club_colors("Merseyside Reds", "London Gunners")
    for a, b in itertools.combinations(["Ev", "Dep", "Madrid Blancos", "Paris", "Roma", "Lions"], 2):
        (hb, _), (ab, _) = md.club_colors(a, b)
        assert hb != ab
    assert md.club_colors("X", "X")[0][0] != md.club_colors("X", "X")[1][0]


def test_eight_axes_six_shouts_and_costs_without_numbers():
    assert [a.field for a in md.INSTRUCTION_AXES] == ["mentality", "tackling", "passing_style", "tempo", "pressing",
                                                     "attacking_focus", "offside_trap", "counter_attack"]
    assert [a.key for a in md.INSTRUCTION_AXES][:2] == ["live_mentality", "live_tackling"]
    assert len(md.SHOUTS) == 6
    forward = md.shout_instructions(DEFAULT_INSTRUCTIONS, "one_cik")
    assert (forward.mentality, forward.tempo, forward.pressing) == (Mentality.ALL_OUT_ATTACK, Tempo.FAST,
                                                                    Pressing.ALL_OVER)
    for key in md.SHOUTS:
        assert md.shout_instructions(DEFAULT_INSTRUCTIONS, key) != DEFAULT_INSTRUCTIONS, key
    values = {a.key: md.axis_value(forward, a) for a in md.INSTRUCTION_AXES}
    assert md.instructions_from_values(values, DEFAULT_INSTRUCTIONS) == forward
    costs = md.instruction_costs(forward)
    assert any("yorulur" in c for c in costs) and any("kart riski" in c for c in costs)
    assert all(not re.search(r"\d", c) for c in costs)
    assert md.instruction_costs(TeamInstructions()) == []


def test_side_moments_competition_line_and_chains():
    result = play(4)
    frames = build_timeline(result)
    moments = md.side_moments(frames)
    goals = sum(1 for f in frames if f.event.type == "GOAL")
    assert sum(len(v) for v in moments.values()) >= goals
    assert md.competition_line("league", "x", 12, result) == "Lig · 12. hafta"
    assert md.competition_line("friendly", "Hazırlık maçı · Ev - Dep", None, result) == "Hazırlık maçı"
    groups = md.chain_groups(frames)
    assert sum(len(g) for g in groups) == len(frames)
    assert all(len({f.event.chain_id for f in g}) == 1 for g in groups if len(g) > 1)


# ---------------------------------------------------------------------------
# 8) Sekil ve kondisyon tahtasi (K8)
# ---------------------------------------------------------------------------

def test_board_has_no_ball_or_arrows_and_is_deterministic():
    fields = set(pitch.Board.__dataclass_fields__) | set(pitch.BoardDot.__dataclass_fields__)
    assert not fields & {"ball", "arrows", "passes", "markers"}
    for seed in (1, 6):
        result = play(seed)
        frames = build_timeline(result)
        for index in range(len(frames)):
            board = pitch.build_board(result, frames, index)
            svg = pitch.board_svg(board, "#b71c1c", "#f5f5f5")
            assert svg == pitch.board_svg(pitch.build_board(result, frames, index), "#b71c1c", "#f5f5f5")
            assert 'viewBox="-4 -10 113 86"' in svg and f'data-frame="{frames[index].index}"' in svg
            for banned in ("cm-p-ball", "cm-p-arrow", "marker-end", 'data-kind="pass"', "<animate", "cm-p-mv"):
                assert banned not in svg
            dots = board.dots
            assert all(0 < d.x < pitch.PITCH_LENGTH and 0 < d.y < pitch.PITCH_WIDTH for d in dots)
            for a, b in itertools.combinations(dots, 2):
                assert math.hypot(a.x - b.x, a.y - b.y) >= pitch.MIN_SEPARATION, (index, a.name, b.name)
            assert len(board.side_dots("home")) <= 11 and len(board.side_dots("away")) <= 11
            assert all(d.x < pitch.CENTER_X for d in board.side_dots("home"))
            assert all(d.x > pitch.CENTER_X for d in board.side_dots("away"))


def test_board_marks_the_event_player_and_shot_outcome():
    for seed in range(12):
        result = play(seed)
        frames = build_timeline(result)
        for i, f in enumerate(frames):
            if f.event.type not in ("GOAL", "SAVE", "MISS") or not f.event.player:
                continue
            board = pitch.build_board(result, frames, i)
            marked = [d for d in board.dots if d.highlight]
            assert len(marked) == 1 and marked[0].name == f.event.player and marked[0].side == f.event.side
            assert marked[0].shot == {"GOAL": "goal", "SAVE": "save", "MISS": "miss"}[f.event.type]
            svg = pitch.board_svg(board)
            assert f'data-kind="{marked[0].shot}"' in svg
    kick = pitch.build_board(play(3), build_timeline(play(3)), 0)
    assert len(kick.dots) == 22 and not any(d.highlight for d in kick.dots)
    assert all(d.energy is not None for d in kick.dots)


def test_board_escapes_names():
    result = MatchEngine(make_team(1, "<b>Ev</b>", 80), make_team(2, "Dep", 78), seed=2).simulate()
    result.home.players[0].name = '<script>alert("x")</script>'
    frames = build_timeline(result)
    svg = pitch.board_svg(pitch.build_board(result, frames, len(frames) - 1))
    assert "<script>" not in svg and "<b>Ev" not in svg


# ---------------------------------------------------------------------------
# 9) Determinizm: oynatma yalnizca goruntuyu zamanlar
# ---------------------------------------------------------------------------

def _drive_playback(live: LiveMatch, rng: random.Random) -> int:
    frames, cursor, now, pauses = [], -1, 0.0, 0
    modes = list(md.SUMMARY_MODES)
    while not live.finished:
        if live.paused:
            pauses += 1
            live.resume()
        elif rng.random() < 0.08:
            live.pause()
            assert live.tick() == []                                  # durakken motor ilerlemez
            live.resume()
        mode = rng.choice(modes)
        factor = rng.choice([1.4, 1.0, 0.5])
        frames, cursor, due = md.step_playback(live, frames, cursor, mode, factor, now)
        now = due + rng.random()
    return pauses


@pytest.mark.parametrize("seed", [0, 3, 7, 12, 21])
def test_instant_run_and_stepwise_playback_give_the_same_match(seed):
    reference = engine(seed, 82, 79).simulate()
    flags = dict(pause_on_opponent_tactics=True, pause_on_two_goals=True, pause_on_tired=True,
                 pause_for_assistant=True)
    instant = LiveMatch.create(engine(seed, 82, 79), 1, **flags)
    while not instant.finished:
        instant.run()
        instant.resume()
    stepped = LiveMatch.create(engine(seed, 82, 79), 1, **flags)
    pauses = _drive_playback(stepped, random.Random(seed))
    a, b = instant.result(), stepped.result()
    assert (a.home_score, a.away_score, len(a.events)) == (b.home_score, b.away_score, len(b.events))
    assert fingerprint(a) == fingerprint(b) == fingerprint(reference)
    assert pauses >= 5                                                # 4 asistan notu + devre arasi
    assert instant.possession_log == stepped.possession_log


def test_replay_playback_walks_frames_without_an_engine():
    result = play(9)
    frames = build_timeline(result)
    cursor, now, seen = -1, 0.0, []
    while True:
        frames2, cursor, due = md.step_playback(None, frames, cursor, md.MODE_WIDE, 1.0, now)
        assert frames2 is frames
        if seen and cursor == seen[-1]:
            break
        seen.append(cursor)
        now = due
    assert seen[-1] == len(frames) - 1
    assert all(md.in_mode(frames[i], md.MODE_WIDE) for i in seen)


# ---------------------------------------------------------------------------
# 10) 14T: canli 2D saha (bilesen kipi, sureklilik)
# ---------------------------------------------------------------------------

import match_anim  # noqa: E402


def test_pitch_mode_pause_hold_jump_and_play():
    assert md.pitch_mode(False, None, False, 1.0) == "play"
    assert md.pitch_mode(True, "manual", False, 1.0) == "pause"               # DURDUR: o anki pozda donar
    assert md.pitch_mode(True, "break", False, 1.0) == "hold"                 # otomatik: yeni kareyi oynat, don
    assert md.pitch_mode(True, "key_event", False, 0.5) == "hold"
    assert md.pitch_mode(False, None, True, 1.0) == "jump"                    # geri sarma: son poz
    assert md.pitch_mode(True, "manual", True, 1.0) == "pause"
    assert md.pitch_mode(False, None, False, 0.0) == "jump"                   # Anında
    assert md.pitch_mode(True, "break", False, 0.0) == "pause"


def test_previous_shown_follows_the_summary_mode():
    result = play(4)
    frames = build_timeline(result)
    assert md.previous_shown(frames, 0, md.MODE_FULL) is None
    for mode in (md.MODE_FULL, md.MODE_WIDE, md.MODE_HIGHLIGHTS):
        shown = md.mode_frames(frames, mode)
        positions = [frames.index(f) for f in shown]
        for a, b in zip(positions, positions[1:], strict=False):
            assert md.previous_shown(frames, b, mode) == a


def test_consecutive_scripts_chain_seamlessly_in_every_summary_mode():
    """
    Bir karenin bitisi bir sonraki gosterilen karenin baslangicidir (yon degisimi haric): topun sahibi AYNI oyuncu,
    oyuncularin en az %97'si ayni yerde (kalanlar tarayicida zaten o anki konumdan devam eder).
    """
    same = total = 0
    for seed in (6, 11):
        result = play(seed)
        frames = build_timeline(result)
        ctx = match_anim.MatchCtx(result)
        for mode in (md.MODE_FULL, md.MODE_HIGHLIGHTS):
            shown = [frames.index(f) for f in md.mode_frames(frames, mode)]
            previous = None
            for i in shown:
                script = match_anim.frame_script(result, frames, i, md.previous_shown(frames, i, mode), ctx)
                if previous is not None and not script["snap"]:
                    ends = {row[0]: (previous["p1"][2 * n], previous["p1"][2 * n + 1])
                            for n, row in enumerate(previous["pl"]) if row[6] != 2}
                    for n, row in enumerate(script["pl"]):
                        if row[0] in ends and row[6] == 0:
                            total += 1
                            same += math.dist(ends[row[0]], (script["p0"][2 * n], script["p0"][2 * n + 1])) < 0.11
                    held = previous["pl"][previous["b1"][2]][0] if previous["b1"][2] >= 0 else None
                    starts = script["pl"][script["b0"][2]][0] if script["b0"][2] >= 0 else None
                    assert held == starts, (mode, i, script["k"])
                    assert script["pf"] == previous["f"]
                previous = script
    assert total > 500 and same / total >= 0.97, (same, total)


def test_pitch_payload_carries_club_and_keeper_colours_and_no_hidden_numbers():
    result = play(2)
    frames = build_timeline(result)
    colors = md.club_colors(result.home.name, result.away.name)
    script = match_anim.frame_script(result, frames, len(frames) - 1)
    payload = match_anim.component_payload(script, "play", 1.0, colors, (result.home.name, result.away.name))
    assert payload["c"][0][:2] == list(colors[0]) and payload["c"][1][:2] == list(colors[1])
    assert payload["c"][0][2] != payload["c"][1][2] and payload["m"] == "play"
    assert set(payload) == {"s", "m", "k", "tok", "c", "tn"}
