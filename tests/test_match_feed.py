"""
Canli mac akisi testleri (6. Asama): match_feed (gorunum modeli) ve web_view (HTML).
Streamlit gerektirmez.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web_view  # noqa: E402
from match_engine import EventType, MatchEngine  # noqa: E402
from match_feed import HIGHLIGHT, build_timeline, summarize  # noqa: E402
from tests.test_match_engine import make_team  # noqa: E402


def play(seed=7, home_ovr=82, away_ovr=78):
    return MatchEngine(make_team(1, "Ev Sahibi", home_ovr), make_team(2, "Deplasman", away_ovr), seed=seed).simulate()


def test_one_frame_per_event_with_whistles():
    result = play()
    frames = build_timeline(result)
    assert len(frames) == len(result.events)
    assert frames[0].event.type == "KICK_OFF" and frames[-1].event.type == "FULL_TIME"
    assert frames[-1].phase == "Maç Sonu"
    assert any(f.phase == "Devre Arası" for f in frames)


def test_cumulative_stats_match_engine_totals():
    for seed in range(25):
        result = play(seed=seed)
        last = build_timeline(result)[-1]
        for side, team in (("home", result.home), ("away", result.away)):
            stats = getattr(last, side)
            assert stats.goals == team.stats.goals
            assert stats.shots == team.stats.shots
            assert stats.on_target == team.stats.shots_on_target
            assert stats.yellow == team.stats.yellow_cards
            assert stats.red == team.stats.red_cards
            assert stats.injuries == team.stats.injuries
        assert (last.home_score, last.away_score) == (result.home_score, result.away_score)


def test_score_and_clock_never_go_backwards():
    for seed in range(15):
        frames = build_timeline(play(seed=seed))
        for prev, cur in zip(frames, frames[1:], strict=False):
            assert cur.elapsed >= prev.elapsed
            assert cur.home_score >= prev.home_score and cur.away_score >= prev.away_score
            assert cur.home.shots >= prev.home.shots


def test_goal_frames_are_highlighted_and_attributed():
    for seed in range(30):
        result = play(seed=seed)
        frames = build_timeline(result)
        goals = [f for f in frames if f.event.type == "GOAL"]
        if not goals:
            continue
        for frame in goals:
            assert frame.event.highlight == "goal" and frame.pacing > 1
            assert frame.event.side in {"home", "away"} and frame.event.player
            team = result.home if frame.event.side == "home" else result.away
            assert frame.event.team == team.name
        return
    raise AssertionError("30 maçta hiç gol yok")


def test_elapsed_includes_first_half_stoppage():
    result = play(seed=3)
    frames = build_timeline(result)
    second_half = [f for f in frames if f.minute > 45]
    if second_half:
        f = second_half[0]
        assert f.elapsed == f.minute + result.first_half_added + f.added_time
    assert frames[-1].elapsed == result.total_minutes


def test_second_yellow_counts_as_yellow_and_red():
    """Motor ikinci sariyi hem sari hem kirmizi sayar; canli istatistik de ayni sayiyi gostermeli."""
    for seed in range(200):
        result = play(seed=seed)
        reds = [e for e in result.events if e.type is EventType.RED_CARD]
        if any(e.detail == "second_yellow" for e in reds):
            last = build_timeline(result)[-1]
            assert last.home.yellow + last.away.yellow == result.home.stats.yellow_cards + result.away.stats.yellow_cards
            assert all(e.detail in {"second_yellow", "straight_red"} for e in reds)
            return
    raise AssertionError("200 maçta ikinci sarı yok")


def test_every_event_type_has_a_highlight():
    assert set(HIGHLIGHT) == set(EventType)


def test_summary():
    result = play(seed=11)
    summary = summarize(result)
    assert summary.possession_home + summary.possession_away == 100
    assert summary.home_stats.goals == result.home_score
    assert summary.total_minutes == result.total_minutes
    lines = web_view.summary_lines(summary)
    assert result.home.name in lines[0] and str(result.home_score) in lines[0]


# ---------------------------------------------------------------------------
# HTML gorunum
# ---------------------------------------------------------------------------

def test_scoreboard_flash_classes():
    frame = build_timeline(play())[-1]
    assert "cm-flash-goal" in web_view.scoreboard_html("A", "B", frame, flash="goal")
    assert "cm-flash-red" in web_view.scoreboard_html("A", "B", frame, flash="red")
    plain = web_view.scoreboard_html("A", "B", frame)
    assert "cm-flash" not in plain and f"{frame.home_score} - {frame.away_score}" in plain
    assert "0 - 0" in web_view.scoreboard_html("A", "B", None)


def test_banner_only_for_goals_and_red_cards():
    frames = build_timeline(play(seed=2))
    for frame in frames:
        html = web_view.banner_html(frame)
        if frame.event.highlight in {"goal", "red"}:
            assert f"cm-banner {frame.event.highlight}" in html
        else:
            assert html == ""


def test_web_view_escapes_html():
    """FM dosyasindan gelen kotu niyetli isim sayfaya script olarak giremez."""
    evil = '<script>alert("x")</script>'
    frames = build_timeline(play(seed=4))
    assert "<script>" not in web_view.scoreboard_html(evil, evil, frames[-1])
    frame = frames[1]
    frame.event.description = evil
    frame.event.player = evil
    assert "<script>" not in web_view.event_html(frame)
    assert "&lt;script&gt;" in web_view.event_html(frame)
    assert "<script>" not in web_view.stats_html(evil, evil, frame.home, frame.away)


def test_feed_newest_first_and_marks_latest():
    frames = build_timeline(play(seed=5))
    html = web_view.feed_html(frames[:5])
    assert html.index(frames[4].event.description[:15]) < html.index(frames[0].event.description[:15])
    assert html.count("latest") == 1
