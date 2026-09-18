"""
tests/test_narration.py
=======================
13B "anlatim" kapisi: anlatim veri dosyasinda (commentary.py), olaylar sunum meta verisi
tasir (K5), sans kalitesi saklanir ama ASLA metne dokulmez (K6), simule edilen akis ile
gosterilen akis ayrilir (K7), sonuc RNG'si anlatimdan etkilenmez (K11).

Olculen hedefler (2000 macta; burada daha az macla, bant payli):
    tek cumle tum satirlarin %3'unu gecmez · en az ~600 farkli gerceklesen cumle
    akis yogunlugu 35-50 satir/mac · bekleme kademeleri (900 / 1800 / 3000 ms)
    topla oynama yayilimi gercege yakin (sd ~10, p5 <= 40, p95 >= 60)
    en uzun sessizlik <= 10 dakika (13A'da maks 30)
Kosum ~40 sn. `CM_GATE_SCALE=0.3` ile kisaltilabilir (istatistiksel olarak zayif).

Veritabani gerektirmez.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import statistics
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import commentary  # noqa: E402
import match_feed  # noqa: E402
from match_engine import (  # noqa: E402
    DWELL_CRUCIAL,
    DWELL_MAIN,
    DWELL_ROUTINE,
    EngineConfig,
    EventType,
    KnockoutRule,
    LazyEvent,
    MatchEngine,
    MatchEvent,
    MatchTeam,
)
from tests.engine_stats import make_spread_team, make_team  # noqa: E402

SCALE = float(os.getenv("CM_GATE_SCALE", "1.0"))
N_FEED = max(60, int(700 * SCALE))

LEGACY = dict(narration=False, build_up_chains=False, flow_events=False, possession_weighted=False)
NAME_RE = re.compile(r"P\d+-(GK|DEF|MID|FWD)")
TEAM_RE = re.compile(r"\b(Ev|Dep)\b")
NUM_RE = re.compile(r"\d+")


def normalise(text: str) -> str:
    """Isim / takim / sayi cikarilmis cumle iskeleti (farkli cumle sayimi)."""
    return NUM_RE.sub("#", TEAM_RE.sub("<t>", NAME_RE.sub("<p>", text))).strip()


def spread_match(seed: int, cfg: EngineConfig | None = None, **kw):
    home = make_spread_team(1, "Ev", 80, spread=6, rng_seed=seed)
    away = make_spread_team(2, "Dep", 80, spread=6, rng_seed=seed + 900)
    return MatchEngine(home, away, seed=seed, config=cfg, **kw).simulate()


@cache
def corpus():
    """Esit (80 v 80) genis kadrolu maclar: akis ve anlatim olcumlerinin ortak orneklemi."""
    out = []
    for seed in range(N_FEED):
        r = spread_match(seed)
        out.append((r, match_feed.build_timeline(r)))
    return tuple(out)


# ---------------------------------------------------------------------------
# K11: anlatim sonuc akisina dokunmaz
# ---------------------------------------------------------------------------

def _outcome(r) -> str:
    pl = "|".join(f"{p.id}:{p.goals}:{p.assists}:{p.shots}:{p.shots_on_target}:{p.saves}:{p.yellow_cards}:"
                  f"{p.sent_off}:{p.injured}:{p.substituted}:{p.entered_minute}:{p.left_minute}:{p.rating}:"
                  f"{p.energy!r}" for t in (r.home, r.away) for p in t.players)
    st = "|".join(repr(t.stats) for t in (r.home, r.away))
    return hashlib.sha256(f"{r.home_score}-{r.away_score}#{pl}#{st}#{r.first_half_added}|"
                          f"{r.second_half_added}#{r.man_of_the_match.id if r.man_of_the_match else None}"
                          .encode()).hexdigest()


@pytest.mark.parametrize("knockout", [False, True])
def test_narration_never_changes_a_seeded_result(knockout):
    """13B bayraklari acik ya da kapali: skor, oyuncu istatistikleri, kartlar, notlar AYNI."""
    for seed in range(40):
        rule = KnockoutRule() if knockout else None
        on = spread_match(seed, knockout=rule)
        off = spread_match(seed, EngineConfig(**LEGACY), knockout=rule)
        assert _outcome(on) == _outcome(off), seed
        assert len(on.events) > len(off.events)          # korner / faul / ofsayt / kurulus artik yaziliyor


def test_legacy_flags_restore_13a_text_exactly():
    """Bayraklar kapaliyken eski metinler (ve olay listesi) birebir geri gelir."""
    r = spread_match(3, EngineConfig(**LEGACY))
    kinds = {e.type for e in r.events}
    assert not kinds & {EventType.CORNER, EventType.FOUL, EventType.OFFSIDE, EventType.BUILD_UP}
    assert all(type(e) is MatchEvent for e in r.events)


def test_narration_is_deterministic_and_independent_of_reading_order():
    """Tembel (LazyEvent) cumleler okunma sirasindan bagimsizdir: ters sirada okumak ayni metni verir."""
    a, b = spread_match(21), spread_match(21)
    forward = [e.description for e in a.events]
    backward = [e.description for e in reversed(b.events)][::-1]
    assert forward == backward
    assert all(forward)


def test_live_stepping_narrates_exactly_like_simulate():
    home = make_spread_team(1, "Ev", 80, spread=6, rng_seed=5)
    away = make_spread_team(2, "Dep", 80, spread=6, rng_seed=905)
    engine = MatchEngine(home, away, seed=5)
    live_texts = []
    while not engine.finished:
        live_texts.extend(event.description for event in engine.step())   # canli ekran gibi: geldikce oku
    assert all(live_texts)
    stepped = [e.description for e in engine.result().events]
    assert stepped == [e.description for e in spread_match(5).events]


def test_lazy_events_are_complete_match_events():
    r = spread_match(8)
    lazy = [e for e in r.events if isinstance(e, LazyEvent)]
    assert lazy and all(isinstance(e, MatchEvent) for e in lazy)
    names = [f.name for f in dataclasses.fields(MatchEvent)]
    for e in lazy[:40]:
        record = dataclasses.asdict(e)
        assert list(record) == names and record["description"]
        assert e == dataclasses.replace(e)


# ---------------------------------------------------------------------------
# Anlatim dagilimi (K5 / D11)
# ---------------------------------------------------------------------------

def test_no_single_line_dominates_and_many_distinct_sentences():
    visible: Counter = Counter()
    emitted: Counter = Counter()
    for r, frames in corpus():
        visible.update(normalise(f.event.description) for f in frames)
        emitted.update(normalise(e.description) for e in r.events)
    total = sum(visible.values())
    line, count = visible.most_common(1)[0]
    assert count / total <= 0.03, (line, count / total)
    # 2000 macta ~660; burada daha az mac oldugu icin esik orantili gevsetilir
    assert len(emitted) >= min(600, int(560 + 60 * SCALE)), len(emitted)
    assert len(visible) >= min(560, int(520 + 40 * SCALE)), len(visible)


def test_feed_is_not_a_shooting_gallery():
    kinds: Counter = Counter()
    pairs = same = 0
    longest_run = 0
    for _, frames in corpus():
        run = 1
        for prev, cur in zip(frames, frames[1:], strict=False):
            kinds[cur.event.type] += 1
            pairs += 1
            if cur.event.type == prev.event.type:
                same += 1
                run += 1
                longest_run = max(longest_run, run)
            else:
                run = 1
    total = sum(kinds.values())
    assert kinds["MISS"] / total <= 0.30                 # 13A: %42 (tek tek iska satirlari)
    assert same / pairs <= 0.22                          # 13A: ardisik olaylarin %27-36'si ayni tur
    assert longest_run <= 7, longest_run


def test_bank_body_part_and_distance_lines_are_tagged():
    """Vucut bolumu ya da mesafe soyleyen satir etiketsiz olamaz (yerdeki sut 'kafa' olmasin)."""
    header = re.compile(r"kafay[ıl]|kafa vuru|kafa gol|kafayla")
    distance = re.compile(r"uzaktan|otuz metre|ceza sahası dışından|ceza yayının dışından")
    for key, lines in commentary.BANK.items():
        for line in lines:
            if header.search(line.live) and not key.endswith(".corner"):
                assert "header" in line.when, (key, line.live)
            if distance.search(line.live) and "free_kick" not in key:
                assert "long" in line.when, (key, line.live)


def test_optional_placeholders_are_guarded_by_tags():
    """{a} (pas veren) ve {d} (savunmaci) her olayda yoktur: yalnizca onlari garanti eden etiketle."""
    for key, lines in commentary.BANK.items():
        for line in lines:
            for text in (line.live, line.report):
                slots = {name.split("_")[0] for name in re.findall(r"\{(\w+)\}", text)}
                if "a" in slots:
                    assert "assisted" in line.when, (key, text)
                if "d" in slots:
                    assert "defended" in line.when, (key, text)
                if slots & {"pin", "pout"}:
                    assert key.startswith("sub."), (key, text)


def test_rendered_text_matches_the_shot_form():
    for r, _ in corpus()[:200]:
        for e in r.events:
            if e.type not in (EventType.MISS, EventType.SAVE, EventType.GOAL) or e.detail:
                continue
            text = e.description
            if re.search(r"kafay[ıl]|kafa vuru|kafa gol", text):
                assert "header" in e.tags, text
            if "header" not in e.tags:
                assert "kafayı" not in text and "kafayla" not in text, text


def test_every_placeholder_is_filled_and_bank_is_turkish_data():
    assert commentary.total_lines() >= 600
    for key, lines in commentary.BANK.items():         # rapor cumlesi olayin sahibini anar
        if key.startswith(("goal.", "red.", "yellow.", "injury.")) or key.endswith(".penalty"):
            assert all("{p" in (line.report or "{p") for line in lines), key
    for r, _ in corpus()[:120]:
        for e in r.events:
            assert "{" not in e.description and "}" not in e.description, e.description
    assert commentary.genitive("Burak") == "Burak'ın" and commentary.genitive("Ali") == "Ali'nin"
    assert commentary.dative("Ahmet") == "Ahmet'e" and commentary.ablative("Mert") == "Mert'ten"


# ---------------------------------------------------------------------------
# K6 / K12: sans kalitesi saklanir, ASLA sayi olarak gosterilmez
# ---------------------------------------------------------------------------

def _alpha_team(tid: int, name: str, letters: str) -> MatchTeam:
    """Isimlerinde rakam olmayan kadro: metne sizan her sayi gercekten sayidir."""
    team = make_team(tid, name, 80)
    for i, p in enumerate(team.players):
        p.name = f"{letters} {chr(65 + i)}{letters.lower()}"
    return team


def test_chance_quality_is_stored_but_never_rendered():
    seen = 0
    for seed in range(60):
        r = MatchEngine(_alpha_team(1, "Aslanlar", "Kaya"), _alpha_team(2, "Kartallar", "Demir"),
                        seed=seed).simulate()
        frames = match_feed.build_timeline(r, include_hidden=True)
        for e, f in zip(r.events, frames, strict=True):
            text = f.event.description + " " + (e.report or "")
            assert "xG" not in text and "%" not in text, text
            q = e.chance_quality
            if q is None:
                continue
            seen += 1
            for token in (f"{q:.1f}", f"{q:.2f}", f"{q:.3f}", str(round(q * 100)), repr(q)):
                assert token not in text, (token, text)
        report = match_feed.match_report(r).text()
        assert "%" not in re.sub(r"topla oynamada %\d+-%\d+", "", report)
    assert seen > 500
    feed_fields = {f.name for f in dataclasses.fields(match_feed.FeedEvent)}
    frame_fields = {f.name for f in dataclasses.fields(match_feed.Frame)}
    assert not {n for n in feed_fields | frame_fields if "quality" in n}


# ---------------------------------------------------------------------------
# K5: meta veri, bekleme kademeleri, zincirler
# ---------------------------------------------------------------------------

def test_feed_density_and_dwell_tiers():
    density = [len(frames) for _, frames in corpus()]
    assert 35 <= statistics.mean(density) <= 50, statistics.mean(density)
    dwell: Counter = Counter()
    for _, frames in corpus():
        for f in frames:
            dwell[f.dwell_ms] += 1
            assert f.dwell_ms == f.event.dwell_ms
            if f.event.type in ("GOAL", "RED_CARD"):
                assert f.dwell_ms == DWELL_CRUCIAL
            if f.event.type in ("FOUL", "CORNER", "OFFSIDE"):
                assert f.dwell_ms == DWELL_ROUTINE
    assert set(dwell) == {DWELL_ROUTINE, DWELL_MAIN, DWELL_CRUCIAL}
    total = sum(dwell.values())
    assert 0.30 <= dwell[DWELL_ROUTINE] / total <= 0.65
    assert 0.25 <= dwell[DWELL_MAIN] / total <= 0.60
    assert 0.04 <= dwell[DWELL_CRUCIAL] / total <= 0.15


def test_goals_arrive_with_their_build_up():
    """Gol tek satirla gelmez: akan oyun golunden hemen once ayni zincirin gorunur kurulusu var."""
    goals = 0
    for r, frames in corpus()[:250]:
        by_index = {f.index: f for f in frames}
        for i, e in enumerate(r.events):
            if e.type is not EventType.GOAL or e.detail:
                continue
            goals += 1
            prev = r.events[i - 1]
            assert prev.type is EventType.BUILD_UP and prev.chain_id == e.chain_id is not None
            assert (prev.minute, prev.added_time) == (e.minute, e.added_time)
            assert prev.chain_step == e.chain_step - 1
            assert i - 1 in by_index and i in by_index        # ikisi de gosterilir
            assert (prev.home_score, prev.away_score) != (e.home_score, e.away_score)
    assert goals > 300


def test_chains_are_contiguous_and_ordered():
    for r, _ in corpus()[:120]:
        chains: dict[int, list[tuple[int, MatchEvent]]] = {}
        for i, e in enumerate(r.events):
            if e.chain_id is not None:
                chains.setdefault(e.chain_id, []).append((i, e))
        assert chains
        for links in chains.values():
            positions = [i for i, _ in links]
            assert positions == list(range(positions[0], positions[0] + len(links)))
            assert [e.chain_step for _, e in links] == list(range(len(links)))
            assert 2 <= len(links) <= 3
            assert links[-1][1].type in (EventType.MISS, EventType.SAVE, EventType.GOAL)


# ---------------------------------------------------------------------------
# K7: gosterim filtresi akis duzeyinde; istatistik gizlenenleri de sayar
# ---------------------------------------------------------------------------

def test_hidden_events_are_emitted_and_counted():
    for seed in range(30):
        rule = KnockoutRule() if seed % 3 == 0 else None
        r = spread_match(seed, knockout=rule)
        full = match_feed.build_timeline(r, include_hidden=True)
        shown = match_feed.build_timeline(r)
        assert len(full) == len(r.events) > len(shown)
        corners = sum(1 for e in r.events if e.type is EventType.CORNER)
        corner_shots = sum(1 for e in r.events if e.detail == "corner")
        assert corners + corner_shots == r.home.stats.corners + r.away.stats.corners
        offsides = sum(1 for e in r.events if e.type is EventType.OFFSIDE)
        assert offsides == r.home.stats.offsides + r.away.stats.offsides
        summary = match_feed.summarize(r, shown)
        for side, team in (("home", r.home), ("away", r.away)):
            last = getattr(shown[-1], side)
            for name in ("corners", "fouls", "offsides"):
                assert getattr(last, name) == getattr(team.stats, name), (seed, side, name)
                assert getattr(getattr(summary, f"{side}_stats"), name) == getattr(team.stats, name)
            assert last.shots == team.stats.shots and last.goals == team.stats.goals


def test_most_low_stakes_events_are_hidden():
    shown: Counter = Counter()
    made: Counter = Counter()
    for r, frames in corpus():
        made.update(e.type.value for e in r.events)
        shown.update(f.event.type for f in frames)
    for kind, ceiling in (("FOUL", 0.20), ("CORNER", 0.35), ("OFFSIDE", 0.55), ("BUILD_UP", 0.50)):
        assert made[kind] > 0 and shown[kind] / made[kind] <= ceiling, (kind, shown[kind] / made[kind])
    assert shown["GOAL"] == made["GOAL"]                      # gol hic gizlenmez


def test_dead_air_ceiling():
    """En uzun gorunur sessizlik (13A: p90 16, maks 30 dakika)."""
    longest = []
    for _, frames in corpus():
        worst = max(cur.elapsed - prev.elapsed for prev, cur in zip(frames, frames[1:], strict=False))
        longest.append(worst)
    assert max(longest) <= 10, max(longest)
    assert statistics.mean(longest) <= 8


def test_display_decision_is_stable_across_snapshots():
    """Canli ekran ayni maci anlik goruntulerden tekrar tekrar kurar: satirlar yer degistirmez."""
    home = make_spread_team(1, "Ev", 80, spread=6, rng_seed=12)
    away = make_spread_team(2, "Dep", 80, spread=6, rng_seed=912)
    engine = MatchEngine(home, away, seed=12)
    engine.start()
    for _ in range(60):
        engine.step()
    early = [(f.index, f.visible) for f in match_feed.build_timeline(engine.snapshot(), include_hidden=True)]
    engine.run_to_end()
    late = [(f.index, f.visible) for f in match_feed.build_timeline(engine.result(), include_hidden=True)]
    assert late[:len(early)] == early


# ---------------------------------------------------------------------------
# D12: durust topla oynama
# ---------------------------------------------------------------------------

def test_possession_spread_is_realistic():
    shares = [match_feed.summarize(r, frames).possession_home for r, frames in corpus()]
    sd = statistics.pstdev(shares)
    ordered = sorted(shares)
    p5, p95 = ordered[len(ordered) // 20], ordered[19 * len(ordered) // 20]
    assert 8.5 <= sd <= 13.0, sd                    # 13A: 5.6-6.6 (dakika sahipligi)
    assert p5 <= 40 and p95 >= 62, (p5, p95)        # 13A: %45 / %64
    assert 45 <= statistics.mean(shares) <= 60      # ev sahibi hafif ustun, esit takimlar


def test_possession_follows_team_strength():
    strong = []
    for seed in range(80):
        r = MatchEngine(make_team(1, "Ev", 88), make_team(2, "Dep", 72), seed=seed).simulate()
        strong.append(r.possession_share()[0])
    assert statistics.mean(strong) >= 55


# ---------------------------------------------------------------------------
# Gecmis zaman mac raporu
# ---------------------------------------------------------------------------

def test_match_report_is_past_tense_and_complete():
    checked = 0
    for r, _ in corpus()[:150]:
        report = match_feed.match_report(r)
        goals = [e for e in r.events if e.type is EventType.GOAL]
        assert len([m for m in report.moments if m.kind == "goal"]) == len(goals)
        assert all(e.report for e in goals)
        assert report.headline.startswith(f"{r.home.name} {r.home_score}-{r.away_score} {r.away.name}")
        for moment in report.moments:
            assert not moment.text.startswith("GOOOL") and "!" not in moment.text, moment.text
            assert moment.text.endswith(".")
        text = report.text()
        assert text.count("\n\n") == len(report.paragraphs)
        for name, _count in r.scorers(r.home) + r.scorers(r.away):
            assert name in text
        if r.winner is not None and goals:
            assert report.turning_point is not None
            checked += 1
    assert checked > 50


def test_report_for_penalty_shootout():
    for seed in range(200):
        r = spread_match(seed, knockout=KnockoutRule())
        if r.decided_by != "penalties":
            continue
        report = match_feed.match_report(r)
        assert "(pen." in report.headline and r.advancing.name in report.verdict
        return
    raise AssertionError("seri penaltiya giden mac yok")


# ---------------------------------------------------------------------------
# Yardimcilar icin kucuk kilitler
# ---------------------------------------------------------------------------

def test_every_new_event_type_has_feed_metadata():
    for etype in (EventType.CORNER, EventType.FOUL, EventType.OFFSIDE, EventType.BUILD_UP):
        assert etype in match_feed.HIGHLIGHT and etype in match_feed.LABELS
        assert match_feed.HIGHLIGHT[etype] in match_feed.PACING


def test_player_names_with_turkish_suffixes():
    slots = commentary.Slots(p="Burak", a="Ali", gk="Uğur")
    assert "{p_gen} ve {a_dat}, {gk_acc}".format_map(slots) == "Burak'ın ve Ali'ye, Uğur'u"
