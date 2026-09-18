"""
Canli 2D saha testleri (7. Asama): pitch.py sahne modeli ve SVG uretimi.
Saf testler: veritabani ve Streamlit gerektirmez.
"""

from __future__ import annotations

import itertools
import math
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pitch  # noqa: E402
from match_engine import EngineConfig, EventType, MatchEngine  # noqa: E402
from match_feed import build_timeline  # noqa: E402
from models import Position  # noqa: E402
from tests.test_match_engine import make_team  # noqa: E402

EVIL = '<script>alert("x")</script>'


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def play(seed=7, home_ovr=82, away_ovr=78, home_name="Ev Sahibi", away_name="Deplasman"):
    return MatchEngine(make_team(1, home_name, home_ovr), make_team(2, away_name, away_ovr), seed=seed).simulate()


def forced_result(seed=3, red_minute=None, card_minutes=(), injury_minute=None):
    """
    Kontrollu mac: dogal kart/sakatlik kapali; istenen dakikada ev sahibinden bir DEF
    direkt kirmizi gorur, kart dakikalarinda motorun kendi kart akisi zorla calisir,
    sakatlik dakikasinda motorun kendi sakatlik akisi (yedek girisi dahil) calisir.
    """
    cfg = EngineConfig(base_card=0.0, base_injury=0.0, straight_red_share=0.0)
    eng = MatchEngine(make_team(1, "Ev Sahibi", 80), make_team(2, "Deplasman", 80), seed=seed, config=cfg)
    real_discipline, real_injury = eng._discipline, eng._injury_check

    def discipline(attacking, defending):
        if eng.added:
            return
        if eng.minute == red_minute:
            victim = next(p for p in eng.home.on_pitch if p.role is Position.DEF)
            eng._send_off(eng.home, victim, second_yellow=False)
        elif eng.minute in card_minutes:
            eng.cfg.base_card = 5.0
            real_discipline(attacking, defending)
            eng.cfg.base_card = 0.0

    def injury():
        if eng.added == 0 and eng.minute == injury_minute:
            eng.cfg.base_injury = 1.0
            real_injury()
            eng.cfg.base_injury = 0.0

    eng._discipline = discipline
    eng._injury_check = injury
    return eng.simulate()


def scenes_of(result):
    frames = build_timeline(result)
    return frames, pitch.build_scenes(result, frames)


def chance_pairs(kinds, seeds=range(12)):
    """(result, frame, scene) uclulerini verilen olay turleri icin toplar."""
    out = []
    for seed in seeds:
        result = play(seed=seed)
        frames, scenes = scenes_of(result)
        out.extend((result, f, s) for f, s in zip(frames, scenes, strict=True) if f.event.type in kinds)
    return out


def attacks_right(scene, side):
    return scene.home_attacks_right if side == "home" else not scene.home_attacks_right


def durations(svg):
    return [float(v) for v in re.findall(r"animation-duration:([\d.]+)s", svg)]


# ---------------------------------------------------------------------------
# Kimin sahada oldugu / taraf degisimi
# ---------------------------------------------------------------------------

def test_kickoff_scene_has_22_dots_each_team_in_own_half():
    frames, scenes = scenes_of(play())
    kick = scenes[0]
    assert frames[0].event.type == "KICK_OFF"
    assert len(kick.side_dots("home")) == 11 and len(kick.side_dots("away")) == 11
    assert all(d.x < pitch.CENTER_X for d in kick.side_dots("home"))
    assert all(d.x > pitch.CENTER_X for d in kick.side_dots("away"))
    assert kick.ball == (pitch.CENTER_X, pitch.CENTER_Y)
    assert kick.keeper("home") is not None and kick.keeper("home").x < 10
    assert kick.keeper("away") is not None and kick.keeper("away").x > 95
    assert not kick.arrows and not kick.markers
    svg = pitch.scene_svg(kick)
    assert svg.count('class="cm-p-dot') == 22


def test_home_attacks_right_first_half_and_left_second_half():
    frames, scenes = scenes_of(play(seed=4))
    for frame, scene in zip(frames, scenes, strict=True):
        if frame.phase in {"1. Yarı", "Devre Arası"}:
            assert scene.home_attacks_right, frame.display_minute
        else:
            assert not scene.home_attacks_right, frame.display_minute
    half = next(s for f, s in zip(frames, scenes, strict=True) if f.event.type == "HALF_TIME")
    full = scenes[-1]
    assert all(d.x < pitch.CENTER_X for d in half.side_dots("home"))
    assert all(d.x > pitch.CENTER_X for d in full.side_dots("home"))   # taraflar degisti
    assert 'cm-p-dir-home" data-dir="right"' in pitch.scene_svg(scenes[0])
    assert 'cm-p-dir-home" data-dir="left"' in pitch.scene_svg(full)
    assert 'cm-p-dir-away" data-dir="right"' in pitch.scene_svg(full)


def test_red_card_leaves_ten_dots_afterwards():
    result = forced_result(red_minute=30)
    frames, scenes = scenes_of(result)
    red_i = next(i for i, f in enumerate(frames) if f.event.type == "RED_CARD")
    victim = frames[red_i].event.player
    for i, scene in enumerate(scenes):
        expected = 11 if i <= red_i else 10
        assert len(scene.side_dots("home")) == expected, (i, frames[i].event.type)
        assert len(scene.side_dots("away")) == 11
    # kart karesinde oyuncu hala sahada ve isaretli, sonraki karede yok
    red_scene = scenes[red_i]
    dot = next(d for d in red_scene.side_dots("home") if d.name == victim)
    assert dot.highlight == "red"
    assert all(d.name != victim for d in scenes[red_i + 1].dots)
    assert pitch.scene_svg(scenes[-1]).count('data-side="home"') == 10


def test_full_time_dots_match_players_who_finished():
    for seed in range(20):
        result = play(seed=seed)
        _, scenes = scenes_of(result)
        for side, team in (("home", result.home), ("away", result.away)):
            finished = {p.id for p in team.players
                        if p.entered_minute is not None and not (p.sent_off or p.injured or p.substituted)}
            assert {d.player_id for d in scenes[-1].side_dots(side)} == finished
        for scene in scenes:
            for side in pitch.SIDES:
                assert 8 <= len(scene.side_dots(side)) <= 11
                assert sum(1 for d in scene.side_dots(side) if d.is_keeper) == 1


# ---------------------------------------------------------------------------
# Pozisyon sahneleri
# ---------------------------------------------------------------------------

def test_chance_scene_pushes_attack_and_drops_defence():
    triples = chance_pairs({"GOAL", "SAVE", "MISS"}, seeds=range(6))
    assert triples
    for _, frame, scene in triples:
        side = frame.event.side
        other = "away" if side == "home" else "home"
        right = attacks_right(scene, side)
        goal_x = pitch.PITCH_LENGTH if right else 0.0

        att = scene.side_dots(side)
        neutral = pitch.team_shape([d.role for d in att], right, "neutral")
        mean_att = sum(abs(goal_x - d.x) for d in att) / len(att)
        mean_neutral = sum(abs(goal_x - x) for x, _ in neutral) / len(neutral)
        assert mean_att < mean_neutral - 5, (frame.display_minute, mean_att, mean_neutral)

        dfn = scene.side_dots(other)
        neutral_def = pitch.team_shape([d.role for d in dfn], not right, "neutral")
        mean_def = sum(abs(goal_x - d.x) for d in dfn) / len(dfn)
        mean_def_neutral = sum(abs(goal_x - x) for x, _ in neutral_def) / len(neutral_def)
        assert mean_def < mean_def_neutral, frame.display_minute
        assert scene.attacking_side == side


def test_pass_chain_ends_at_highlighted_shooter():
    triples = chance_pairs({"GOAL", "SAVE", "MISS"}, seeds=range(6))
    for _, frame, scene in triples:
        shooter = [d for d in scene.dots if d.highlight]
        assert len(shooter) == 1 and shooter[0].highlight == "shooter"
        shooter = shooter[0]
        assert shooter.name == frame.event.player and shooter.side == frame.event.side
        passes, shot = scene.passes, scene.shot
        assert 2 <= len(passes) <= 3
        for a, b in zip(passes, passes[1:], strict=False):
            assert (a.x2, a.y2) == (b.x1, b.y1)
        assert (passes[-1].x2, passes[-1].y2) == (shooter.x, shooter.y)
        assert (shot.x1, shot.y1) == (shooter.x, shooter.y)
        assert scene.arrows[-1] is shot and scene.ball == (shot.x2, shot.y2)
        attackers = {(d.x, d.y) for d in scene.side_dots(frame.event.side)}
        assert all((a.x1, a.y1) in attackers for a in passes)   # paslar yalnizca hucum edenler arasinda
        assert not scene.markers


def test_goal_arrow_ends_inside_goal_mouth_on_correct_end():
    triples = chance_pairs({"GOAL"}, seeds=range(15))
    halves = set()
    for _, frame, scene in triples:
        shot = scene.shot
        assert shot.kind == "goal"
        assert pitch.GOAL_TOP < shot.y2 < pitch.GOAL_BOTTOM
        home_goal_first_half = (frame.event.side == "home") == scene.home_attacks_right
        if home_goal_first_half:   # saga hucum eden takim
            assert shot.x2 >= pitch.PITCH_LENGTH
        else:
            assert shot.x2 <= 0.0
        halves.add((frame.event.side, scene.home_attacks_right))
        svg = pitch.scene_svg(scene)
        assert 'class="cm-p-flash"' in svg and 'data-kind="goal"' in svg
    assert len(halves) >= 3, halves   # iki takim, iki devre


def test_save_arrow_ends_at_defending_keeper():
    triples = chance_pairs({"SAVE"}, seeds=range(6))
    assert triples
    for _, frame, scene in triples:
        other = "away" if frame.event.side == "home" else "home"
        keeper = scene.keeper(other)
        assert scene.shot.kind == "save"
        assert (scene.shot.x2, scene.shot.y2) == (keeper.x, keeper.y)
        assert 'class="cm-p-flash"' not in pitch.scene_svg(scene)


def test_miss_arrow_ends_outside_the_posts():
    triples = chance_pairs({"MISS"}, seeds=range(6))
    assert triples
    for _, frame, scene in triples:
        shot = scene.shot
        assert shot.kind == "miss"
        assert abs(shot.y2 - pitch.CENTER_Y) > pitch.GOAL_HALF_WIDTH
        if attacks_right(scene, frame.event.side):
            assert shot.x2 > pitch.PITCH_LENGTH
        else:
            assert shot.x2 < 0.0
        assert 'stroke-dasharray="1.2 0.8"' in pitch.scene_svg(scene)


# ---------------------------------------------------------------------------
# Kart / sakatlik / degisiklik
# ---------------------------------------------------------------------------

def test_card_injury_and_sub_markers_at_the_right_dot():
    result = forced_result(red_minute=30, card_minutes=(20, 70), injury_minute=55)
    frames, scenes = scenes_of(result)
    seen = set()
    for frame, scene in zip(frames, scenes, strict=True):
        kind = pitch.MARKER_KIND.get(frame.event.type)
        if kind is None or frame.event.player is None:
            continue
        seen.add(kind)
        assert scene.shot is None and not scene.arrows
        assert [m.kind for m in scene.markers] == [kind]
        marker = scene.markers[0]
        dot = next(d for d in scene.side_dots(frame.event.side) if d.name == frame.event.player)
        assert (marker.x, marker.y) == (dot.x, dot.y)
        assert dot.highlight == kind
        assert math.dist(scene.ball, (dot.x, dot.y)) < 4
        svg = pitch.scene_svg(scene, show_labels=True)
        assert f'cm-p-mk-{kind}' in svg and 'class="cm-p-label"' in svg
        assert "cm-p-draw" not in svg
    assert {"yellow", "red", "injury", "sub"} <= seen, seen


def test_substitute_takes_the_place_of_the_injured_player():
    result = forced_result(injury_minute=55)
    frames, scenes = scenes_of(result)
    inj = next(i for i, f in enumerate(frames) if f.event.type == "INJURY")
    sub = inj + 1
    assert frames[sub].event.type == "SUBSTITUTION" and frames[sub].event.player
    side = frames[inj].event.side
    injured = frames[inj].event.player
    assert any(d.name == injured for d in scenes[inj].side_dots(side))
    names_after = {d.name for d in scenes[sub].side_dots(side)}
    assert injured not in names_after and frames[sub].event.player in names_after
    assert len(scenes[sub].side_dots(side)) == 11


# ---------------------------------------------------------------------------
# SVG: determinizm, guvenlik, tempo, id'ler, hareket
# ---------------------------------------------------------------------------

def render_all(result):
    _, scenes = scenes_of(result)
    return [pitch.scene_svg(s, scenes[i - 1] if i else None) for i, s in enumerate(scenes)]


def test_same_result_gives_identical_svg():
    assert render_all(play(seed=21)) == render_all(play(seed=21))
    assert render_all(play(seed=21)) != render_all(play(seed=22))


def test_seedless_result_is_still_deterministic():
    result = play(seed=5)
    result.seed = None
    first = [pitch.scene_svg(s) for s in pitch.build_scenes(result)]
    assert first == [pitch.scene_svg(s) for s in pitch.build_scenes(result)]


def test_names_are_escaped_in_scene_and_lineup():
    home = make_team(1, EVIL, 80)
    home.players[3].name = EVIL        # DEF
    home.players[12].name = EVIL + "2"  # FWD
    result = MatchEngine(home, make_team(2, EVIL, 80), seed=9).simulate()
    _, scenes = scenes_of(result)
    for i, scene in enumerate(scenes):
        svg = pitch.scene_svg(scene, scenes[i - 1] if i else None)
        assert "<script>" not in svg and "</script>" not in svg
    assert "&lt;script&gt;" in pitch.scene_svg(scenes[0])
    board = pitch.lineup_svg([(Position.GK, EVIL, 80, 90), (Position.DEF, None, None, None)], EVIL,
                             formation_label=EVIL)
    assert "<script>" not in board and "&lt;script&gt;" in board
    # renk ve genislik parametreleri de nitelik disina tasamaz
    assert '"><x>' not in pitch.lineup_svg([], "A", color='"><x>')
    assert 'width:100%' in pitch.scene_svg(scenes[0], width='1px;background:url(x)')


def test_tempo_scales_and_clamps_durations():
    _, _, scene = chance_pairs({"GOAL"}, seeds=range(15))[0]
    base = durations(pitch.scene_svg(scene, tempo=1.0))
    slow = durations(pitch.scene_svg(scene, tempo=2.0))
    fast = durations(pitch.scene_svg(scene, tempo=0.5))
    assert base and len(base) == len(slow) == len(fast)
    for b, s, f in zip(base, slow, fast, strict=True):
        assert abs(s - 2 * b) <= 0.011 and abs(f - b / 2) <= 0.011
    assert pitch.scene_svg(scene, tempo=10) == pitch.scene_svg(scene, tempo=3)
    assert pitch.scene_svg(scene, tempo=0) == pitch.scene_svg(scene, tempo=0.25)
    assert pitch.clamp_tempo(float("nan")) == 1.0 and pitch.clamp_tempo("x") == 1.0


def test_marker_ids_are_unique_per_frame_and_referenced():
    _, scenes = scenes_of(play(seed=3))
    all_ids = set()
    for scene in scenes:
        svg = pitch.scene_svg(scene)
        ids = re.findall(r'\sid="([^"]+)"', svg)
        assert len(ids) == len(set(ids))
        assert all(i.endswith(f"-{scene.frame_index}") for i in ids)
        assert not (set(ids) & all_ids)
        all_ids.update(ids)
        for ref in re.findall(r"url\(#([^)]+)\)", svg):
            assert ref in ids


def test_dots_move_from_previous_positions_and_variant_alternates():
    _, scenes = scenes_of(play(seed=8))
    prev, cur = scenes[1], scenes[2]
    svg = pitch.scene_svg(cur, prev)
    assert "--dx:" in svg and "cm-p-mv" in svg
    moved = next(d for d in cur.dots if any(p.player_id == d.player_id for p in prev.dots))
    old = next(p for p in prev.dots if p.player_id == moved.player_id)
    assert f"--dx:{old.x - moved.x:.2f}px;--dy:{old.y - moved.y:.2f}px" in svg
    assert "--dx:" not in pitch.scene_svg(cur).split("cm-p-ball")[0]
    assert "cm-p-a" in pitch.scene_svg(scenes[0]) and "cm-p-b" in pitch.scene_svg(scenes[1])
    assert "\n" not in svg          # markdown HTML blogu bolunmesin
    css = pitch.PITCH_CSS
    assert css.startswith("<style>") and css.rstrip().endswith("</style>")
    assert "\n\n" not in css
    for variant in ("a", "b"):
        assert f"@keyframes cm-p-move-{variant}" in css and f".cm-p-{variant} .cm-p-draw" in css


def test_goal_scene_sequences_passes_before_shot():
    _, _, scene = chance_pairs({"GOAL"}, seeds=range(15))[0]
    svg = pitch.scene_svg(scene)
    delays = [float(d) for d in re.findall(r'cm-p-draw" [^>]*animation-delay:([\d.]+)s', svg)]
    assert len(delays) == len(scene.passes) + 1
    assert delays == sorted(delays) and len(set(delays)) == len(delays)
    assert svg.index('data-kind="goal"') > svg.index('data-kind="pass"')


# ---------------------------------------------------------------------------
# Enerji (energy_log opsiyonel)
# ---------------------------------------------------------------------------

def test_energy_log_drives_fatigue_ring_and_opacity():
    result = play(seed=12)
    for team in (result.home, result.away):
        for p in team.players:
            p.energy_log = []
    frames = build_timeline(result)
    late = next(f for f in frames if f.minute >= 60)
    early = frames[0]
    plain = pitch.build_scenes(result, [early, late])
    assert all(d.energy is None for s in plain for d in s.dots)
    assert "cm-p-energy" not in pitch.scene_svg(plain[1])

    target = next(p for p in result.home.players if p.entered_minute == 0 and not p.sent_off
                  and not p.injured and not p.substituted)
    target.energy_log = [(0, 100), (30, 72), (55, 41)]
    early_scene, late_scene = pitch.build_scenes(result, [early, late])
    assert next(d for d in early_scene.dots if d.player_id == target.id).energy == 100
    late_dot = next(d for d in late_scene.dots if d.player_id == target.id)
    assert late_dot.energy == 41
    assert sum(1 for d in late_scene.dots if d.energy is not None) == 1
    late_svg = pitch.scene_svg(late_scene)
    assert late_svg.count("cm-p-energy") == 1 and "cm-p-ring-red" in late_svg
    assert 'fill-opacity="0.7"' in late_svg
    assert "cm-p-ring-green" in pitch.scene_svg(early_scene)
    assert pitch.energy_at(target, 45) == 72 and pitch.energy_at(target, -1) is None


def test_energy_attribute_is_optional():
    result = play(seed=2)
    for team in (result.home, result.away):
        for p in team.players:
            if hasattr(p, "energy_log"):
                del p.energy_log
    scenes = pitch.build_scenes(result)
    assert all(d.energy is None for s in scenes for d in s.dots)
    assert "cm-p-energy" not in pitch.scene_svg(scenes[-1])


# ---------------------------------------------------------------------------
# Taktik tahtasi
# ---------------------------------------------------------------------------

def test_lineup_svg_rings_and_empty_slots():
    slots = [
        (Position.GK, "Kaleci Birinci", 84, 92),
        (Position.DEF, "Defans Çelik", 80, 70),
        (Position.DEF, None, None, None),
        (Position.DEF, "Stoper Güneş", 78, 45),
        (Position.DEF, "Bek Yılmaz", 76, None),
        (Position.MID, "Orta Demir", 83, 85),
        (Position.MID, "Kanat Öztürk", 81, 61),
        (Position.MID, None, None, None),
        (Position.MID, "Oyun Kurucu", 79, 79),
        (Position.FWD, "Mehmet Golcü", 86, 59),
        (Position.FWD, "Hızlı Forvet", 88, 80),
    ]
    svg = pitch.lineup_svg(slots, "Takım <A>", color="#c62828", formation_label="4-4-2")
    assert svg.count('class="cm-p-slot') == 11
    assert svg.count("cm-p-slot-empty") == 2 and svg.count(">boş<") == 2
    assert svg.count('stroke-dasharray="0.9 0.7"') == 2
    assert svg.count("cm-p-ring-green") == 3        # 92, 85, 80
    assert svg.count("cm-p-ring-yellow") == 3       # 70, 61, 79
    assert svg.count("cm-p-ring-red") == 2          # 45, 59
    assert svg.count("cm-p-ring-none") == 1         # kondisyon bilinmiyor
    assert pitch.ENERGY_COLORS["none"] in svg and ">86<" in svg
    assert "M. Golcü" in svg and "Takım &lt;A&gt;" in svg and "4-4-2" in svg
    assert 'fill="#c62828"' in svg
    ys = [float(y) for y in re.findall(r'<circle cx="[\d.]+" cy="([\d.]+)" r="3.2"', svg)]
    gk_y, fwd_y = ys[0], ys[-1]
    assert gk_y > fwd_y        # kendi kalesi altta, forvetler ustte
    assert pitch.lineup_svg(slots, "X", color="red; x") == pitch.lineup_svg(slots, "X")


def test_dots_never_overlap():
    for seed in range(8):
        for scene in pitch.build_scenes(play(seed=seed)):
            points = [(d.x, d.y) for d in scene.dots]
            for i, a in enumerate(points):
                assert 0 < a[0] < pitch.PITCH_LENGTH and 0 < a[1] < pitch.PITCH_WIDTH
                for b in points[i + 1:]:
                    assert math.dist(a, b) >= 2 * pitch.DOT_RADIUS, (seed, scene.frame_index)


def test_team_shape_mirrors_and_keeps_lines():
    roles = [Position.GK] + [Position.DEF] * 4 + [Position.MID] * 4 + [Position.FWD] * 2
    right = pitch.team_shape(roles, True)
    left = pitch.team_shape(roles, False)
    assert right[0] == (4.0, pitch.CENTER_Y)
    for (xr, yr), (xl, yl) in zip(right, left, strict=True):
        assert math.isclose(xr + xl, pitch.PITCH_LENGTH) and math.isclose(yr + yl, pitch.PITCH_WIDTH)
    assert all(x < pitch.CENTER_X for x, _ in pitch.team_shape(roles, True, "kickoff"))
    assert EventType.GOAL.value in pitch.CHANCE_EVENTS


# ---------------------------------------------------------------------------
# Eleme maclari (8. Asama): uzatmada taraf degisimi, seri penalti sahnesi
# ---------------------------------------------------------------------------

def _frame(event_type, phase, minute=100, side=None, detail=None):
    from match_feed import FeedEvent, Frame

    return Frame(index=0, minute=minute, added_time=0, display_minute=f"{minute}'", elapsed=minute, phase=phase,
                 home_score=1, away_score=1,
                 event=FeedEvent(type=event_type, label="X", highlight="whistle", side=side, team=None,
                                 player=None, description="", detail=detail))


def test_sides_swap_at_extra_time_start_and_half():
    cases = [
        ("GOAL", "2. Yarı", 88, False),
        ("EXTRA_TIME_START", "Normal Süre Bitti", 90, False),   # mola karesi: 2. yari yonu
        ("GOAL", "1. uzatma", 100, True),                      # uzatmaya girerken taraf degisti
        ("EXTRA_TIME_HALF", "Uzatma Arası", 105, True),
        ("MISS", "2. uzatma", 110, False),                      # uzatma devre arasinda yine degisti
        ("FULL_TIME", "Maç Sonu", 120, False),
        ("SHOOTOUT_START", "Penaltılar", 120, pitch.SHOOTOUT_GOAL_RIGHT),
        ("PENALTY_SHOOTOUT", "Penaltılar", 120, pitch.SHOOTOUT_GOAL_RIGHT),
    ]
    for etype, phase, minute, expected in cases:
        assert pitch.home_attacks_right(_frame(etype, phase, minute)) is expected, etype


def test_every_event_type_builds_a_scene():
    from match_engine import KnockoutRule

    seen = set()
    for seed in range(200):
        result = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed,
                             knockout=KnockoutRule()).simulate()
        if result.shootout is None:
            continue
        frames, scenes = scenes_of(result)
        seen |= {f.event.type for f in frames}
        for i, scene in enumerate(scenes):
            assert pitch.scene_svg(scene, scenes[i - 1] if i else None)
        kick = next(s for f, s in zip(frames, scenes, strict=True) if f.event.type == "PENALTY_SHOOTOUT")
        taker = next(d for d in kick.dots if d.highlight == "shooter")
        assert (taker.x, taker.y) == pitch.SHOOTOUT_SPOT
        assert kick.home_penalties is not None
        if {"EXTRA_TIME_START", "EXTRA_TIME_HALF", "SHOOTOUT_START", "PENALTY_SHOOTOUT"} <= seen:
            return
    raise AssertionError(f"eksik olay türleri: {seen}")


# ---------------------------------------------------------------------------
# 14A: sekil ve kondisyon tahtasi -- etiketler ust uste binmez (uzun adlar, farkli dizilisler)
# ---------------------------------------------------------------------------

_LABEL = re.compile(r'<text class="md-b-name" x="([\d.-]+)" y="([\d.-]+)" text-anchor="(\w+)"[^>]*>([^<]*)</text>')


def _board_for(home_shape, away_shape):
    dots = []
    long_name = "Abdurrahman Kucukbayraktaroglu"
    for side, shape in (("home", home_shape), ("away", away_shape)):
        roles = [Position.GK] + [Position.DEF] * shape[0] + [Position.MID] * shape[1] + [Position.FWD] * shape[2]
        for i, ((depth, lateral), role) in enumerate(zip(pitch._board_local(roles), roles, strict=True)):
            x, y = (depth, lateral) if side == "home" else (pitch.PITCH_LENGTH - depth, pitch.PITCH_WIDTH - lateral)
            dots.append(pitch.BoardDot(x=x, y=y, side=side, role=role, name=long_name, player_id=i,
                                       is_keeper=role is Position.GK, energy=70, yellow=1, subbed_on=True,
                                       highlight=i == 5, shot="save" if i == 5 else None))
    return pitch.Board(frame_index=1, display_minute="10'", phase="1. Yarı", home_team=EVIL, away_team="Dep",
                       home_score=0, away_score=0, dots=tuple(dots), caption="x")


@pytest.mark.parametrize("home_shape,away_shape", [((4, 4, 2), (5, 3, 2)), ((4, 3, 3), (3, 5, 2)),
                                                    ((4, 5, 1), (3, 4, 3)), ((5, 4, 1), (4, 4, 2))])
def test_board_labels_do_not_overlap_each_other_or_other_players(home_shape, away_shape):
    board = _board_for(home_shape, away_shape)
    svg = pitch.board_svg(board)
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    char_w, height = 0.6 * 2.1, 2.1
    boxes = []
    for x, y, anchor, text in _LABEL.findall(svg):
        x, y, w = float(x), float(y), char_w * len(text.replace("&amp;", "&"))
        left = x - w / 2 if anchor == "middle" else x if anchor == "start" else x - w
        boxes.append((left, left + w, y - height * 0.8, y + height * 0.2, text))
    assert len(boxes) == len(board.dots)
    for a, b in itertools.combinations(boxes, 2):
        overlap = a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]
        assert not overlap, (a, b)
    rings = [(d.x, d.y) for d in board.dots]
    ordered = [d for d in board.dots if not d.highlight] + [d for d in board.dots if d.highlight]
    for box, own in zip(boxes, ordered, strict=True):
        for cx, cy in rings:
            if (cx, cy) == (own.x, own.y):
                continue
            nearest_x, nearest_y = min(max(cx, box[0]), box[1]), min(max(cy, box[2]), box[3])
            assert math.hypot(cx - nearest_x, cy - nearest_y) >= 2.45, (box, cx, cy)
