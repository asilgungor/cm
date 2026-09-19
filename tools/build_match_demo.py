"""
tools/build_match_demo.py
=========================
Faz 14T: sahibe gosterilecek TEK DOSYALIK 2D canli mac ornegi. Veritabanina DOKUNMAZ.

Gercek motor maci (MatchEngine, tests/engine_stats.py kadro kuruculariyla) oynatilir; olay listesi ve her gorunur
karenin canlandirma betigi (match_anim.frame_script) HTML'e gomulur. Bilesen JS'i web_assets/match_pitch.js ile AYNI
dosyadir (export default -> yerel fonksiyon); ag istegi yoktur.

    python tools/build_match_demo.py [cikti.html] [--seed N]

Varsayilan cikti: .claude/phase14/ref/mac_2d_ornek.html. Tohum verilmezse ilk 400 tohum icinden en "zengin" mac
(goller, duran toplar, kartlar, degisiklik, sakatlik) secilir -- secim deterministiktir.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from engine_stats import make_spread_team  # noqa: E402

import club_colors  # noqa: E402
import match_anim  # noqa: E402
from match_engine import EventType, MatchEngine  # noqa: E402
from match_feed import build_timeline  # noqa: E402
from name_pools import NAME_POOLS, unique_name  # noqa: E402

HOME, AWAY = "Galatasaray", "Fenerbahçe"
DEFAULT_OUT = ROOT / ".claude" / "phase14" / "ref" / "mac_2d_ornek.html"


def demo_colors(home: str, away: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """Acik veri forma renkleri (club_colors, CC0); yoksa notr kirmizi / mavi."""
    return (club_colors.colors_for(home) or ("#e53935", "#ffffff"),
            club_colors.colors_for(away) or ("#1e88e5", "#ffffff"))


def _named_team(tid: int, name: str, ovr: int, seed: int):
    team = make_spread_team(tid, name, ovr, spread=6, rng_seed=seed)
    rng = random.Random(seed * 31 + tid)
    first, last = NAME_POOLS["Turkiye"]
    used: set[str] = set()
    for p in team.players:
        p.name = unique_name(rng, first, last, used)
    return team


def play(seed: int):
    home, away = _named_team(1, HOME, 81, seed), _named_team(2, AWAY, 80, seed)
    return MatchEngine(home, away, seed=seed).simulate()


def richness(result) -> float:
    kinds = {(e.type, e.detail) for e in result.events}
    goals = result.home_score + result.away_score
    score = min(goals, 5) * 2.0
    for want in ((EventType.GOAL, "free_kick"), (EventType.SAVE, "free_kick"), (EventType.MISS, "free_kick")):
        score += 3.0 if want in kinds else 0.0
    for kind in ("corner", "penalty"):
        score += 3.0 if any(e.detail == kind and e.type in (EventType.GOAL, EventType.SAVE, EventType.MISS)
                            for e in result.events) else 0.0
    types = {e.type for e in result.events}
    for t in (EventType.INJURY, EventType.OFFSIDE, EventType.RED_CARD, EventType.YELLOW_CARD):
        score += 1.5 if t in types else 0.0
    score += 2.0 if result.home_score and result.away_score else 0.0
    return score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(DEFAULT_OUT))
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.seed is None:
        seed = max(range(400), key=lambda s: (richness(play(s)), -s))
    else:
        seed = args.seed
    result = play(seed)
    frames = build_timeline(result)
    colors = demo_colors(HOME, AWAY)
    js = (ROOT / "web_assets" / "match_pitch.js").read_text(encoding="utf-8")
    css = (ROOT / "web_assets" / "match_pitch.css").read_text(encoding="utf-8")
    html = match_anim.standalone_html(result, frames, colors, js, css)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[demo] tohum {seed}: {HOME} {result.home_score}-{result.away_score} {AWAY}, {len(frames)} kare, "
          f"{len(html) // 1024} KB -> {out}")


if __name__ == "__main__":
    main()
