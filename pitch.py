"""
pitch.py
========
Canli 2D saha gorunumu (7. Asama). SAF MANTIK + SVG: yalnizca standart kutuphane
(Streamlit, matplotlib ya da veritabani BILMEZ).

    build_scenes(result, frames)   Frame listesi -> Scene listesi: oyuncu noktalari,
                                   pas/sut oklari, kart/sakatlik/degisiklik isaretleri, top
    scene_svg(scene, previous)     Scene -> animasyonlu SVG (onceki sahneden hareketle)
    lineup_svg(slots, team_name)   Statik taktik tahtasi: dizilis, OVR, kondisyon halkasi
    PITCH_CSS                      Sayfaya BIR KEZ basilan <style> blogu (animasyonlar)

Koordinatlar metredir: saha 105 x 68, (0, 0) sol ust kose, kale agzi y = 30.34..37.66.
Ev sahibi ilk yari soldan saga hucum eder; ikinci yari (ve mac sonu karesi) taraflar
degisir. Devre arasi karesi hala ilk yari yonundedir.

Kimin sahada oldugu MatchPlayer.entered_minute / left_minute ve olay sirasindan
yeniden kurulur: kirmizi kart / sakatlik karesinde oyuncu hala gorunur, bir sonraki
karede yoktur; degisiklik karesinde giren oyuncu gorunur, cikan yoktur.

Determinizm: bir sahnedeki tum rastgelelik random.Random(crc32(f"{seed}|{index}"))
uretecinden gelir; ayni MatchResult -> ayni sahneler -> ayni SVG metni.

Animasyon tamamen CSS'tir (JavaScript yok). Noktalar onceki sahnedeki yerlerinden
kayar: her grup style="--dx:..px;--dy:..px" tasir ve @keyframes translate(var(--dx),
var(--dy)) -> 0 oynatir. SVG elemanlarinda CSS transform'undaki px birimi KULLANICI
birimidir (burada metre), bu yuzden SVG ekranda nasil olceklenirse olceklensin
hareket dogru gorunur. Paslar ve sut stroke-dashoffset ile sirayla "cizilir".

Streamlit markdown'i DOM dugumlerini yeniden kullanir; yeni SVG basildiginda
animasyonun bastan oynamasi icin animasyon adlari kare paritesine gore iki kopya
(cm-p-...-a / cm-p-...-b) arasinda degisir.

GUVENLIK: oyuncu/kulup adlari dis kaynakli FM dosyalarindan gelebilir; SVG'ye yazilan
her metin html.escape() ile kacirilir.
"""

from __future__ import annotations

import math
import random
import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from html import escape

from match_engine import EventType, MatchPlayer, MatchResult, MatchTeam
from match_feed import Frame, build_timeline
from models import Position

# ===========================================================================
# [1] SABITLER
# ===========================================================================

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
CENTER_X = PITCH_LENGTH / 2
CENTER_Y = PITCH_WIDTH / 2
GOAL_HALF_WIDTH = 3.66
GOAL_TOP = CENTER_Y - GOAL_HALF_WIDTH        # 30.34
GOAL_BOTTOM = CENTER_Y + GOAL_HALF_WIDTH     # 37.66
GOAL_DEPTH = 2.0
PENALTY_DEPTH, PENALTY_WIDTH = 16.5, 40.32
GOAL_AREA_DEPTH, GOAL_AREA_WIDTH = 5.5, 18.32
PENALTY_SPOT = 11.0
CIRCLE_RADIUS = 9.15

HOME_COLOR, AWAY_COLOR = "#e53935", "#1e88e5"
HOME_KEEPER_COLOR, AWAY_KEEPER_COLOR = "#8e1c19", "#0d47a1"
SIDES = ("home", "away")
SIDE_COLORS = {"home": (HOME_COLOR, HOME_KEEPER_COLOR), "away": (AWAY_COLOR, AWAY_KEEPER_COLOR)}

# Kondisyon / enerji halkasi: yesil >= 80, sari 60-79, kirmizi < 60, gri = bilinmiyor
ENERGY_COLORS = {"green": "#43a047", "yellow": "#fdd835", "red": "#e53935", "none": "#9e9e9e"}

ROLE_ORDER = (Position.GK, Position.DEF, Position.MID, Position.FWD)

# Takimin KENDI kalesinden hucum yonune olculen hat derinlikleri (metre)
SHAPES: dict[str, dict[Position, float]] = {
    "kickoff": {Position.GK: 4.0, Position.DEF: 16.0, Position.MID: 32.0, Position.FWD: 47.0},
    "neutral": {Position.GK: 4.0, Position.DEF: 22.0, Position.MID: 45.0, Position.FWD: 66.0},
}
LINE_MARGIN = 6.0                      # hat uzerindeki oyuncularin taca olan payi
ATTACK_PUSH = (10.0, 15.0)             # pozisyonda hucum eden takim bu kadar one cikar
# Savunma hatlari hucum hatlarinin ARASINA duser (ust uste binmesinler diye)
DEFEND_DROP = {Position.GK: 1.5, Position.DEF: 10.0, Position.MID: 20.0, Position.FWD: 24.0}
MIN_SEPARATION = 3.4                   # iki nokta arasi en az mesafe (yaricap 1.6)
DEFEND_COMPACT = 0.35                  # savunma topun y'sine bu oranda daralir
KEEPER_COMPACT = 0.30
SHOOTER_DEPTH = (84.0, 94.0)

WHISTLE_EVENTS = {EventType.KICK_OFF.value, EventType.HALF_TIME.value, EventType.FULL_TIME.value}
CHANCE_EVENTS = {EventType.GOAL.value, EventType.SAVE.value, EventType.MISS.value}
SHOT_KIND = {EventType.GOAL.value: "goal", EventType.SAVE.value: "save", EventType.MISS.value: "miss"}
MARKER_KIND = {
    EventType.YELLOW_CARD.value: "yellow",
    EventType.RED_CARD.value: "red",
    EventType.INJURY.value: "injury",
    EventType.SUBSTITUTION.value: "sub",
}
SECOND_HALF_PHASES = {"2. Yarı", "Maç Sonu"}

# Animasyon zamanlamasi (saniye, tempo = 1)
TEMPO_RANGE = (0.25, 3.0)
MOVE_TIME = 0.9
PASS_START = 0.7
PASS_TIME = 0.5
SHOT_TIME = 0.45
FLASH_TIME = 0.6
POP_DELAY = 0.75
POP_TIME = 0.35
PULSE_TIME = 1.2

DOT_RADIUS = 1.6
# ok turu -> (renk, kalinlik)
ARROW_STYLE: dict[str, tuple[str, float]] = {
    "pass": ("#ffffff", 0.35),
    "shot": ("#ffffff", 0.5),
    "goal": ("#ffd60a", 0.6),
    "save": ("#90caf9", 0.5),
    "miss": ("#ffffff", 0.45),
}

_NEVER = 1 << 30
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CSS_WIDTH = re.compile(r"^\d+(\.\d+)?(px|%|rem|em|vw)?$")


# ===========================================================================
# [2] SAHNE NESNELERI
# ===========================================================================

@dataclass
class Dot:
    x: float
    y: float
    side: str                     # "home" / "away"
    role: Position
    name: str
    player_id: int | None
    is_keeper: bool
    highlight: str | None = None  # "shooter" / "yellow" / "red" / "injury" / "sub"
    energy: int | None = None     # energy_log'dan (0-100); kayit yoksa None


@dataclass
class Arrow:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: str                     # "pass" / "shot" / "goal" / "save" / "miss"


@dataclass
class Marker:
    x: float
    y: float
    kind: str                     # "yellow" / "red" / "injury" / "sub"


@dataclass
class Scene:
    frame_index: int
    display_minute: str
    phase: str
    attacking_side: str | None
    home_attacks_right: bool
    dots: list[Dot]
    arrows: list[Arrow]
    markers: list[Marker]
    ball: tuple[float, float]
    caption: str
    event_type: str = ""
    home_team: str = ""
    away_team: str = ""
    home_score: int = 0
    away_score: int = 0

    def side_dots(self, side: str) -> list[Dot]:
        return [d for d in self.dots if d.side == side]

    def keeper(self, side: str) -> Dot | None:
        return next((d for d in self.dots if d.side == side and d.is_keeper), None)

    @property
    def highlighted(self) -> list[Dot]:
        return [d for d in self.dots if d.highlight]

    @property
    def passes(self) -> list[Arrow]:
        return [a for a in self.arrows if a.kind == "pass"]

    @property
    def shot(self) -> Arrow | None:
        return next((a for a in reversed(self.arrows) if a.kind != "pass"), None)


@dataclass
class _Track:
    """Bir oyuncunun sahada oldugu olay indeksi araligi: enter <= i < exit."""
    player: MatchPlayer
    side: str
    enter: int
    exit: int
    key: float = 0.0              # hat icindeki sira (giren oyuncu cikanin yerini alir)
    inherited: bool = field(default=False, repr=False)

    @property
    def role(self) -> Position:
        return self.player.role or self.player.position


# ===========================================================================
# [3] KIM SAHADA? (olay sirasindan yeniden kurulum)
# ===========================================================================

def home_attacks_right(frame: Frame) -> bool:
    """Ev sahibi ilk yari saga hucum eder; 2. yari ve mac sonu karesinde taraflar degisir."""
    if frame.event.type == EventType.HALF_TIME.value:
        return True
    if frame.phase in SECOND_HALF_PHASES:
        return False
    return frame.minute <= 45


def _first_at_or_after(events, minute: int) -> int:
    return next((i for i, ev in enumerate(events) if ev.minute >= minute), len(events))


def _enter_index(p: MatchPlayer, team: MatchTeam, events) -> int | None:
    if p.entered_minute is None:
        return None
    if p.entered_minute <= 0:
        return 0
    for i, ev in enumerate(events):
        if ev.type is EventType.SUBSTITUTION and ev.player_id == p.id and ev.team_id == team.id:
            return i
    return _first_at_or_after(events, p.entered_minute)


def _exit_index(p: MatchPlayer, team: MatchTeam, events) -> int:
    left = p.left_minute
    if left is None:
        return _NEVER
    for flag, etype in ((p.sent_off, EventType.RED_CARD), (p.injured, EventType.INJURY)):
        if not flag:
            continue
        for i, ev in enumerate(events):
            if ev.type is etype and ev.player_id == p.id and ev.team_id == team.id:
                return i + 1          # kart/sakatlik karesinde oyuncu hala gorunur
    if p.substituted:
        # Cikan oyuncunun kendi olayi yok: ayni dakikadaki, ayni takimin giris olayi
        candidates = [
            i for i, ev in enumerate(events)
            if ev.type is EventType.SUBSTITUTION and ev.team_id == team.id and ev.minute == left
            and ev.player_id is not None and ev.player_id != p.id
        ]
        named = [i for i in candidates if p.name and p.name in events[i].description]
        if named or candidates:
            return (named or candidates)[0]
    if not (p.sent_off or p.injured or p.substituted) and left >= 90:
        return _NEVER                 # motor mac sonunda herkesin left_minute'ini 90 yapar
    return _first_at_or_after(events, left)


def _tracks(result: MatchResult) -> list[_Track]:
    events = result.events
    tracks: list[_Track] = []
    for side, team in (("home", result.home), ("away", result.away)):
        team_tracks: list[_Track] = []
        for p in team.players:
            enter = _enter_index(p, team, events)
            if enter is not None:
                team_tracks.append(_Track(p, side, enter, _exit_index(p, team, events)))

        starters = sorted((t for t in team_tracks if t.enter == 0), key=lambda t: t.player.id)
        for rank, t in enumerate(starters):
            t.key = float(rank)
        extra = len(starters)
        for t in sorted((t for t in team_tracks if t.enter > 0), key=lambda t: (t.enter, t.player.id)):
            gone = [o for o in team_tracks
                    if o is not t and not o.inherited and o.role is t.role and o.exit <= t.enter]
            if gone:
                replaced = max(gone, key=lambda o: (o.exit, -o.key))
                replaced.inherited = True
                t.key = replaced.key
            else:
                t.key = float(extra)
                extra += 1
        tracks.extend(team_tracks)
    return tracks


def _on_pitch(tracks: list[_Track], side: str, index: int) -> list[tuple[_Track, Position]]:
    """O karede sahadakiler, (iz, gosterilecek rol) olarak hat sirasinda."""
    present = [t for t in tracks if t.side == side and t.enter <= index < t.exit]
    keepers = [t for t in present if t.role is Position.GK]
    main_keeper = None
    if keepers:
        natural = [t for t in keepers if t.player.position is Position.GK]
        main_keeper = min(natural or keepers, key=lambda t: (t.enter, t.player.id))
    rows: list[tuple[_Track, Position]] = []
    for t in present:
        role = t.role
        if role is Position.GK and t is not main_keeper:
            # Sonradan eldiven giyen oyuncu, o ana kadar kendi mevkisinde gorunur
            role = t.player.position if t.player.position is not Position.GK else Position.DEF
        rows.append((t, role))
    rows.sort(key=lambda r: (ROLE_ORDER.index(r[1]), r[0].key, r[0].player.id))
    return rows


def energy_at(player, minute: int) -> int | None:
    """energy_log [(dakika, enerji)] icinden dakikaya kadarki son ornek; yoksa None."""
    best_minute, value = None, None
    for item in getattr(player, "energy_log", None) or []:
        try:
            sample_minute, energy = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if sample_minute <= minute and (best_minute is None or sample_minute >= best_minute):
            best_minute, value = sample_minute, energy
    return None if value is None else max(0, min(100, round(value)))


def energy_level(value: int | float | None) -> str:
    if value is None:
        return "none"
    if value >= 80:
        return "green"
    if value >= 60:
        return "yellow"
    return "red"


# ===========================================================================
# [4] DIZILIS SEKLI VE SAHNE KURULUMU
# ===========================================================================

def _line_y(i: int, n: int) -> float:
    return LINE_MARGIN + (PITCH_WIDTH - 2 * LINE_MARGIN) * (i + 0.5) / max(n, 1)


def _to_pitch(depth: float, lateral: float, attacks_right: bool) -> tuple[float, float]:
    """Takim yerel (derinlik, yanal) -> saha koordinati. Sola hucumda 180 derece dondurulur."""
    if attacks_right:
        return depth, lateral
    return PITCH_LENGTH - depth, PITCH_WIDTH - lateral


def _local_shape(roles: Sequence[Position], mode: str) -> list[tuple[float, float]]:
    if mode not in SHAPES:
        raise ValueError(f"Bilinmeyen şekil: {mode}. Seçenekler: {', '.join(SHAPES)}")
    depths = SHAPES[mode]
    counts = {role: sum(1 for r in roles if r is role) for role in ROLE_ORDER}
    seen: dict[Position, int] = {}
    out = []
    for role in roles:
        i = seen.get(role, 0)
        seen[role] = i + 1
        out.append((depths[role], _line_y(i, counts.get(role, 1))))
    return out


def team_shape(roles: Sequence[Position], attacks_right: bool, mode: str = "neutral") -> list[tuple[float, float]]:
    """Sirali rol listesi icin gurultusuz temel koordinatlar. mode: 'kickoff' / 'neutral'."""
    return [_to_pitch(d, lat, attacks_right) for d, lat in _local_shape(roles, mode)]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _depth(dot: Dot, attacks_right: bool) -> float:
    return dot.x if attacks_right else PITCH_LENGTH - dot.x


def _find_dot(dots: list[Dot], player_id: int | None, name: str | None) -> Dot | None:
    if player_id is not None:
        found = next((d for d in dots if d.player_id == player_id), None)
        if found is not None:
            return found
    if name:
        return next((d for d in dots if d.name == name), None)
    return None


def _event_player_id(result: MatchResult, frame: Frame) -> int | None:
    if 0 <= frame.index < len(result.events):
        ev = result.events[frame.index]
        if ev.type.value == frame.event.type and ev.player == frame.event.player:
            return ev.player_id
    return None


def _caption(frame: Frame) -> str:
    ev = frame.event
    if ev.type in WHISTLE_EVENTS:
        return f"{ev.label} · {frame.home_score}-{frame.away_score}"
    if ev.player and ev.team:
        return f"{ev.label}: {ev.player} ({ev.team})"
    who = ev.player or ev.team
    return f"{ev.label}: {who}" if who else ev.label


def _make_dots(rows: list[tuple[_Track, Position]], local: list[list[float]], side: str,
               attacks_right: bool, minute: int, kickoff: bool) -> list[Dot]:
    dots = []
    for (track, role), (depth, lateral) in zip(rows, local, strict=True):
        x, y = _to_pitch(depth, lateral, attacks_right)
        if kickoff:   # baslama vurusu: herkes kendi yari sahasinda
            x = _clamp(x, 0.8, CENTER_X - 1.0) if attacks_right else _clamp(x, CENTER_X + 1.0, PITCH_LENGTH - 0.8)
        dots.append(Dot(
            x=_clamp(x, 0.8, PITCH_LENGTH - 0.8), y=_clamp(y, 0.8, PITCH_WIDTH - 0.8),
            side=side, role=role, name=track.player.name, player_id=track.player.id,
            is_keeper=role is Position.GK, energy=energy_at(track.player, minute),
        ))
    return dots


def _separate(dots: list[Dot], rounds: int = 4) -> None:
    """Birbirine gecen noktalari itip ayirir (rastgelelik yok, sira sabit -> deterministik)."""
    for _ in range(rounds):
        moved = False
        for i, a in enumerate(dots):
            for j in range(i + 1, len(dots)):
                b = dots[j]
                dx, dy = b.x - a.x, b.y - a.y
                dist = math.hypot(dx, dy)
                if dist >= MIN_SEPARATION:
                    continue
                if dist < 1e-6:
                    dx, dy, dist = 0.0, 1.0, 1.0
                push = (MIN_SEPARATION - dist) / 2
                ux, uy = dx / dist, dy / dist
                a.x, a.y = a.x - ux * push, a.y - uy * push
                b.x, b.y = b.x + ux * push, b.y + uy * push
                moved = True
        for d in dots:
            d.x = _clamp(d.x, 0.8, PITCH_LENGTH - 0.8)
            d.y = _clamp(d.y, 0.8, PITCH_WIDTH - 0.8)
        if not moved:
            return


def _shot_end(event_type: str, rng: random.Random, attacks_right: bool,
              keeper: Dot | None) -> tuple[float, float]:
    goal_x = PITCH_LENGTH if attacks_right else 0.0
    out = 1.0 if attacks_right else -1.0
    if event_type == EventType.GOAL.value:
        return goal_x + out * rng.uniform(0.6, 1.6), CENTER_Y + rng.uniform(-2.8, 2.8)
    if event_type == EventType.SAVE.value:
        if keeper is not None:
            return keeper.x, keeper.y
        return goal_x - out * 1.0, CENTER_Y + rng.uniform(-2.5, 2.5)
    wide = rng.choice((-1.0, 1.0))      # MISS: direklerin disi, cizginin gerisi
    return goal_x + out * rng.uniform(1.0, 3.2), CENTER_Y + wide * rng.uniform(4.6, 9.0)


def _build_scene(result: MatchResult, frame: Frame, tracks: list[_Track]) -> Scene:
    seed = result.seed if result.seed is not None else 0
    rng = random.Random(zlib.crc32(f"{seed}|{frame.index}".encode()))
    ev = frame.event
    home_right = home_attacks_right(frame)
    right = {"home": home_right, "away": not home_right}
    rows = {side: _on_pitch(tracks, side, frame.index) for side in SIDES}
    event_pid = _event_player_id(result, frame)

    whistle = ev.type in WHISTLE_EVENTS
    chance = ev.type in CHANCE_EVENTS and ev.side in SIDES
    mode = "kickoff" if whistle else "neutral"
    jitter = 0.6 if whistle else 1.5
    local: dict[str, list[list[float]]] = {}
    for side in SIDES:
        base = _local_shape([role for _, role in rows[side]], mode)
        local[side] = [[d + rng.uniform(-jitter, jitter), lat + rng.uniform(-jitter, jitter)] for d, lat in base]

    arrows: list[Arrow] = []
    markers: list[Marker] = []
    attacking_side: str | None = None

    if chance:
        attacking_side = ev.side
        defending_side = "away" if attacking_side == "home" else "home"
        att_rows, att_local = rows[attacking_side], local[attacking_side]
        push = rng.uniform(*ATTACK_PUSH)
        for (_, role), pos in zip(att_rows, att_local, strict=True):
            pos[0] = min(pos[0] + (push * 0.4 if role is Position.GK else push), 96.0)

        shooter_i = next((i for i, (t, _) in enumerate(att_rows)
                          if (event_pid is not None and t.player.id == event_pid)
                          or (event_pid is None and t.player.name == ev.player)), None)
        if shooter_i is None:
            outfield = [i for i, (_, role) in enumerate(att_rows) if role is not Position.GK]
            shooter_i = max(outfield, key=lambda i: att_local[i][0], default=None)
        if shooter_i is not None:
            spot = att_local[shooter_i]
            spot[0] = max(spot[0], rng.uniform(*SHOOTER_DEPTH))
            spot[1] = CENTER_Y + (spot[1] - CENTER_Y) * 0.4 + rng.uniform(-3.0, 3.0)
        att_dots = _make_dots(att_rows, att_local, attacking_side, right[attacking_side], frame.minute, False)
        shooter = att_dots[shooter_i] if shooter_i is not None else None
        ball_y = shooter.y if shooter is not None else CENTER_Y

        for (_, role), pos in zip(rows[defending_side], local[defending_side], strict=True):
            pos[0] = max(pos[0] - DEFEND_DROP[role], 1.0 if role is Position.GK else 3.0)
        def_dots = _make_dots(rows[defending_side], local[defending_side], defending_side,
                              right[defending_side], frame.minute, False)
        for d in def_dots:
            if d.is_keeper:
                d.y = _clamp(d.y + (ball_y - d.y) * KEEPER_COMPACT, GOAL_TOP - 0.5, GOAL_BOTTOM + 0.5)
            else:
                d.y = _clamp(d.y + (ball_y - d.y) * DEFEND_COMPACT, 0.8, PITCH_WIDTH - 0.8)

        dots_by_side = {attacking_side: att_dots, defending_side: def_dots}
        _separate([*att_dots, *def_dots])
        ball = (CENTER_X, CENTER_Y)
        if shooter is not None:
            shooter.highlight = "shooter"
            others = [d for d in att_dots if d is not shooter and not d.is_keeper]
            passers = rng.sample(others, min(rng.choice((2, 3)), len(others)))
            passers.sort(key=lambda d: _depth(d, right[attacking_side]))
            chain = [*passers, shooter]
            arrows = [Arrow(a.x, a.y, b.x, b.y, "pass") for a, b in zip(chain, chain[1:], strict=False)]
            keeper = next((d for d in def_dots if d.is_keeper), None)
            end = _shot_end(ev.type, rng, right[attacking_side], keeper)
            arrows.append(Arrow(shooter.x, shooter.y, end[0], end[1], SHOT_KIND[ev.type]))
            ball = end
    else:
        dots_by_side = {
            side: _make_dots(rows[side], local[side], side, right[side], frame.minute, whistle)
            for side in SIDES
        }
        ball = (CENTER_X, CENTER_Y)
        if not whistle:
            _separate([*dots_by_side["home"], *dots_by_side["away"]])
        if ev.type in MARKER_KIND:
            pool = dots_by_side.get(ev.side, []) if ev.side in SIDES else [*dots_by_side["home"], *dots_by_side["away"]]
            target = _find_dot(pool, event_pid, ev.player) if ev.player else None
            if target is not None:
                kind = MARKER_KIND[ev.type]
                target.highlight = kind
                markers.append(Marker(target.x, target.y, kind))
                angle, dist = rng.uniform(0.0, 2 * math.pi), rng.uniform(1.8, 3.0)
                ball = (_clamp(target.x + dist * math.cos(angle), 0.5, PITCH_LENGTH - 0.5),
                        _clamp(target.y + dist * math.sin(angle), 0.5, PITCH_WIDTH - 0.5))
            else:
                ball = (CENTER_X + rng.uniform(-12.0, 12.0), CENTER_Y + rng.uniform(-10.0, 10.0))
        elif not whistle:
            ball = (CENTER_X + rng.uniform(-12.0, 12.0), CENTER_Y + rng.uniform(-10.0, 10.0))

    return Scene(
        frame_index=frame.index,
        display_minute=frame.display_minute,
        phase=frame.phase,
        attacking_side=attacking_side,
        home_attacks_right=home_right,
        dots=[*dots_by_side["home"], *dots_by_side["away"]],
        arrows=arrows,
        markers=markers,
        ball=ball,
        caption=_caption(frame),
        event_type=ev.type,
        home_team=result.home.name,
        away_team=result.away.name,
        home_score=frame.home_score,
        away_score=frame.away_score,
    )


def build_scenes(result: MatchResult, frames: list[Frame] | None = None) -> list[Scene]:
    """Her kare icin bir Scene (ayni sirada). frames verilmezse build_timeline(result)."""
    frames = frames if frames is not None else build_timeline(result)
    tracks = _tracks(result)
    return [_build_scene(result, frame, tracks) for frame in frames]


# ===========================================================================
# [5] CSS (animasyonlar)
# ===========================================================================

_KEYFRAMES: dict[str, str] = {
    "move": "from{transform:translate(var(--dx),var(--dy))}to{transform:translate(0px,0px)}",
    "in": "from{opacity:0}to{opacity:1}",
    "draw": "0%{stroke-dashoffset:var(--len);opacity:0}1%{opacity:1}100%{stroke-dashoffset:0px;opacity:1}",
    "kick": ("0%{opacity:0;transform:translate(var(--dx),var(--dy))}"
             "20%{opacity:1;transform:translate(var(--dx),var(--dy))}"
             "100%{opacity:1;transform:translate(0px,0px)}"),
    "flash": "0%{opacity:0}35%{opacity:.85}100%{opacity:0}",
    "pulse": "0%,100%{stroke-opacity:.15}50%{stroke-opacity:1}",
}
# sinif -> keyframe (sira onemli: ayni ozgulukte sonraki kural kazanir)
_ANIMATED: tuple[tuple[str, str], ...] = (
    ("cm-p-mv", "move"),
    ("cm-p-in", "in"),
    ("cm-p-draw", "draw"),
    ("cm-p-fade", "in"),
    ("cm-p-pop", "in"),
    ("cm-p-kick", "kick"),
    ("cm-p-flash", "flash"),
    ("cm-p-hl", "pulse"),
)


def _build_css() -> str:
    rules = [
        "<style>",
        ".cm-p-wrap{max-width:100%;margin:.25rem 0 .5rem}",
        ".cm-p-svg{display:block;width:100%;height:auto;border-radius:14px;user-select:none;"
        "box-shadow:0 6px 24px rgba(0,0,0,.25)}",
        ".cm-p-svg text{font-family:system-ui,-apple-system,\"Segoe UI\",Roboto,sans-serif}",
        ".cm-p-mv,.cm-p-kick{--dx:0px;--dy:0px;transform-box:view-box;transform-origin:0 0}",
        ".cm-p-mv,.cm-p-in,.cm-p-draw,.cm-p-fade,.cm-p-pop,.cm-p-kick,.cm-p-flash,.cm-p-hl"
        "{animation-fill-mode:both;animation-timing-function:cubic-bezier(.33,0,.2,1)}",
        ".cm-p-flash,.cm-p-hl{animation-iteration-count:3;animation-timing-function:ease-in-out}",
        ".cm-p-draw,.cm-p-kick{animation-timing-function:linear}",
    ]
    for variant in ("a", "b"):
        for name, body in _KEYFRAMES.items():
            rules.append(f"@keyframes cm-p-{name}-{variant}{{{body}}}")
        for cls, name in _ANIMATED:
            rules.append(f".cm-p-{variant} .{cls}{{animation-name:cm-p-{name}-{variant}}}")
    rules.append("@media (prefers-reduced-motion:reduce){.cm-p-svg *{animation:none!important}}")
    rules.append("</style>")
    return "\n".join(rules)


PITCH_CSS = _build_css()


# ===========================================================================
# [6] SVG YARDIMCILARI
# ===========================================================================

def clamp_tempo(tempo: float) -> float:
    """0.5 = iki kat hizli, 2.0 = iki kat yavas; 0.25..3 araligina sikistirilir."""
    try:
        value = float(tempo)
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(value):
        return 1.0
    lo, hi = TEMPO_RANGE
    return max(lo, min(hi, value))


def _n(value: float) -> str:
    return f"{value:.2f}"


def _anim(delay: float, duration: float, tempo: float) -> str:
    return f"animation-delay:{delay * tempo:.2f}s;animation-duration:{duration * tempo:.2f}s"


def _offset(dx: float, dy: float) -> str:
    return f"--dx:{dx:.2f}px;--dy:{dy:.2f}px"


def _truncate(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _css_width(width: str) -> str:
    width = str(width).strip()
    return width if _CSS_WIDTH.match(width) else "100%"


def _shade(color: str, factor: float) -> str:
    """#rrggbb rengini koyulastirir (factor < 1)."""
    if not _HEX_COLOR.match(color):
        return color
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c * factor))) for c in (r, g, b)))


def _dot_key(dot: Dot) -> tuple[str, object]:
    return dot.side, dot.player_id if dot.player_id is not None else dot.name


def _role_value(role) -> str:
    return str(getattr(role, "value", role))


def _pitch_markings() -> str:
    L, W = PITCH_LENGTH, PITCH_WIDTH
    pa_y, ga_y = (W - PENALTY_WIDTH) / 2, (W - GOAL_AREA_WIDTH) / 2
    arc_dy = math.sqrt(CIRCLE_RADIUS ** 2 - (PENALTY_DEPTH - PENALTY_SPOT) ** 2)
    r = CIRCLE_RADIUS
    stripes = "".join(
        f'<rect x="{i * 10.5:.1f}" y="0" width="10.5" height="{W:g}" fill="#338a38"/>' for i in range(0, 10, 2)
    )
    lines = (
        f'<rect x="0" y="0" width="{L:g}" height="{W:g}"/>'
        f'<line x1="{CENTER_X:g}" y1="0" x2="{CENTER_X:g}" y2="{W:g}"/>'
        f'<circle cx="{CENTER_X:g}" cy="{CENTER_Y:g}" r="{r:g}"/>'
        f'<rect x="0" y="{pa_y:.2f}" width="{PENALTY_DEPTH:g}" height="{PENALTY_WIDTH:g}"/>'
        f'<rect x="{L - PENALTY_DEPTH:g}" y="{pa_y:.2f}" width="{PENALTY_DEPTH:g}" height="{PENALTY_WIDTH:g}"/>'
        f'<rect x="0" y="{ga_y:.2f}" width="{GOAL_AREA_DEPTH:g}" height="{GOAL_AREA_WIDTH:g}"/>'
        f'<rect x="{L - GOAL_AREA_DEPTH:g}" y="{ga_y:.2f}" width="{GOAL_AREA_DEPTH:g}" height="{GOAL_AREA_WIDTH:g}"/>'
        f'<path d="M{PENALTY_DEPTH:g},{CENTER_Y - arc_dy:.2f}A{r:g},{r:g} 0 0 1 {PENALTY_DEPTH:g},{CENTER_Y + arc_dy:.2f}"/>'
        f'<path d="M{L - PENALTY_DEPTH:g},{CENTER_Y - arc_dy:.2f}A{r:g},{r:g} 0 0 0 '
        f'{L - PENALTY_DEPTH:g},{CENTER_Y + arc_dy:.2f}"/>'
        f'<path d="M0,1A1,1 0 0 0 1,0M{L - 1:g},0A1,1 0 0 0 {L:g},1'
        f'M{L:g},{W - 1:g}A1,1 0 0 0 {L - 1:g},{W:g}M1,{W:g}A1,1 0 0 0 0,{W - 1:g}"/>'
    )
    spots = (
        f'<circle cx="{CENTER_X:g}" cy="{CENTER_Y:g}" r="0.45"/>'
        f'<circle cx="{PENALTY_SPOT:g}" cy="{CENTER_Y:g}" r="0.35"/>'
        f'<circle cx="{L - PENALTY_SPOT:g}" cy="{CENTER_Y:g}" r="0.35"/>'
    )
    goals = "".join(
        f'<rect class="cm-p-goal" x="{x:g}" y="{GOAL_TOP:.2f}" width="{GOAL_DEPTH:g}" height="{2 * GOAL_HALF_WIDTH:.2f}"/>'
        for x in (-GOAL_DEPTH, L)
    )
    return (
        f'<rect x="0" y="0" width="{L:g}" height="{W:g}" fill="#2e7d32"/>{stripes}'
        f'<g fill="none" stroke="#ffffff" stroke-opacity="0.85" stroke-width="0.3">{lines}</g>'
        f'<g fill="#ffffff" fill-opacity="0.9">{spots}</g>'
        f'<g fill="#ffffff" fill-opacity="0.2" stroke="#ffffff" stroke-width="0.3">{goals}</g>'
    )


_PITCH_MARKINGS = _pitch_markings()


def _arrow_defs(uid: str) -> str:
    items = "".join(
        f'<marker id="cm-p-ah-{kind}-{uid}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="2.2" '
        f'markerHeight="2.2" markerUnits="userSpaceOnUse" orient="auto">'
        f'<path d="M0,0L10,5L0,10z" fill="{color}"/></marker>'
        for kind, (color, _) in ARROW_STYLE.items()
    )
    return f"<defs>{items}</defs>"


def _trimmed(arrow: Arrow) -> tuple[float, float, float, float]:
    """Ok uclarini nokta yaricapi kadar kisaltir (veri degismez, yalnizca cizim)."""
    start = DOT_RADIUS + (0.5 if arrow.kind == "pass" else 0.3)
    end = {"pass": DOT_RADIUS + 0.9, "save": DOT_RADIUS + 0.6}.get(arrow.kind, 0.0)
    dx, dy = arrow.x2 - arrow.x1, arrow.y2 - arrow.y1
    length = math.hypot(dx, dy)
    if length <= start + end + 0.5:
        return arrow.x1, arrow.y1, arrow.x2, arrow.y2
    ux, uy = dx / length, dy / length
    return arrow.x1 + ux * start, arrow.y1 + uy * start, arrow.x2 - ux * end, arrow.y2 - uy * end


def _arrow_svg(arrow: Arrow, uid: str, delay: float, duration: float, tempo: float) -> str:
    kind = arrow.kind if arrow.kind in ARROW_STYLE else "shot"
    color, width = ARROW_STYLE[kind]
    x1, y1, x2, y2 = _trimmed(arrow)
    common = (f'data-kind="{kind}" x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
              f'stroke="{color}" stroke-width="{width:g}" marker-end="url(#cm-p-ah-{kind}-{uid})"')
    if kind == "miss":   # isabetsiz: kesik cizgi, belirerek gelir
        return (f'<line class="cm-p-arrow cm-p-k-miss cm-p-fade" {common} stroke-dasharray="1.2 0.8" '
                f'style="{_anim(delay, duration, tempo)}"/>')
    length = math.hypot(x2 - x1, y2 - y1)
    return (f'<line class="cm-p-arrow cm-p-k-{kind} cm-p-draw" {common} stroke-linecap="butt" '
            f'style="stroke-dasharray:{length:.2f}px;--len:{length:.2f}px;{_anim(delay, duration, tempo)}"/>')


def _move_style(dot_x: float, dot_y: float, prev: tuple[float, float] | None, tempo: float) -> str:
    if prev is None:
        return ""
    return f"{_offset(prev[0] - dot_x, prev[1] - dot_y)};{_anim(0.0, MOVE_TIME, tempo)}"


def _dot_svg(dot: Dot, prev: Dot | None, animate: bool, tempo: float) -> str:
    base, keeper = SIDE_COLORS.get(dot.side, (HOME_COLOR, HOME_KEEPER_COLOR))
    cx, cy = _n(dot.x), _n(dot.y)
    cls, style = "cm-p-dot", ""
    if animate and prev is None:        # yeni giren oyuncu belirir
        cls, style = "cm-p-dot cm-p-in", _anim(0.0, MOVE_TIME * 0.6, tempo)
    elif animate and prev is not None:
        cls, style = "cm-p-dot cm-p-mv", _move_style(dot.x, dot.y, (prev.x, prev.y), tempo)
    attrs = f' data-side="{escape(dot.side)}" data-role="{escape(_role_value(dot.role))}"'
    if dot.player_id is not None:
        attrs += f' data-pid="{escape(str(dot.player_id))}"'
    if dot.highlight:
        attrs += f' data-hl="{escape(dot.highlight)}"'
    if style:
        attrs += f' style="{style}"'
    parts = [f'<g class="{cls}"{attrs}>']
    if dot.energy is not None:
        level = energy_level(dot.energy)
        parts.append(f'<circle class="cm-p-energy cm-p-ring-{level}" cx="{cx}" cy="{cy}" r="2.35" fill="none" '
                     f'stroke="{ENERGY_COLORS[level]}" stroke-width="0.4"/>')
    tired = ' fill-opacity="0.7"' if dot.energy is not None and dot.energy < 60 else ""
    stroke_w = "0.55" if dot.is_keeper else "0.3"
    parts.append(f'<circle class="cm-p-body" cx="{cx}" cy="{cy}" r="{DOT_RADIUS:g}" '
                 f'fill="{keeper if dot.is_keeper else base}" stroke="#ffffff" stroke-width="{stroke_w}"{tired}/>')
    if dot.highlight:
        parts.append(f'<circle class="cm-p-hl" cx="{cx}" cy="{cy}" r="3.1" fill="none" stroke="#ffd60a" '
                     f'stroke-width="0.45" style="{_anim(0.0, PULSE_TIME, tempo)}"/>')
    label = f"{dot.name} ({_role_value(dot.role)})"
    if dot.energy is not None:
        label += f" · kondisyon {dot.energy}"
    parts.append(f"<title>{escape(label)}</title></g>")
    return "".join(parts)


def _label_svg(dot: Dot, prev: Dot | None, tempo: float) -> str:
    ly = dot.y + 4.4 if dot.y < PITCH_WIDTH - 6 else dot.y - 3.0
    anchor, lx = "middle", dot.x
    if dot.x < 8:
        anchor, lx = "start", dot.x - DOT_RADIUS
    elif dot.x > PITCH_LENGTH - 8:
        anchor, lx = "end", dot.x + DOT_RADIUS
    style = _move_style(dot.x, dot.y, (prev.x, prev.y), tempo) if prev is not None else ""
    cls = "cm-p-lbl cm-p-mv" if style else "cm-p-lbl"
    style_attr = f' style="{style}"' if style else ""
    return (f'<g class="{cls}"{style_attr}><text class="cm-p-label" x="{_n(lx)}" y="{_n(ly)}" '
            f'text-anchor="{anchor}" font-size="2.3" font-weight="700" fill="#ffffff" stroke="#000000" '
            f'stroke-opacity="0.7" stroke-width="0.5" paint-order="stroke">{escape(_truncate(dot.name, 22))}</text></g>')


def _marker_svg(marker: Marker, prev: Dot | None, dot: Dot | None, tempo: float) -> str:
    x, y = marker.x + 1.9, marker.y - 2.6
    if marker.kind in ("yellow", "red"):
        fill = "#fdd835" if marker.kind == "yellow" else "#e53935"
        shape = (f'<rect x="{_n(x - 0.75)}" y="{_n(y - 1.05)}" width="1.5" height="2.1" rx="0.2" fill="{fill}" '
                 f'stroke="#1a1a1a" stroke-width="0.15" transform="rotate(12 {_n(x)} {_n(y)})"/>')
    elif marker.kind == "injury":
        shape = (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="1.25" fill="#ffffff" stroke="#b71c1c" stroke-width="0.15"/>'
                 f'<path d="M{_n(x - 0.75)},{_n(y)}H{_n(x + 0.75)}M{_n(x)},{_n(y - 0.75)}V{_n(y + 0.75)}" '
                 f'stroke="#e53935" stroke-width="0.45"/>')
    else:
        shape = (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="1.25" fill="#43a047" stroke="#ffffff" stroke-width="0.15"/>'
                 f'<path d="M{_n(x)},{_n(y + 0.8)}V{_n(y - 0.5)}M{_n(x - 0.55)},{_n(y - 0.05)}L{_n(x)},{_n(y - 0.75)}'
                 f'L{_n(x + 0.55)},{_n(y - 0.05)}" fill="none" stroke="#ffffff" stroke-width="0.3"/>')
    move = _move_style(dot.x, dot.y, (prev.x, prev.y), tempo) if dot is not None and prev is not None else ""
    cls = f"cm-p-marker cm-p-mk-{escape(marker.kind)}" + (" cm-p-mv" if move else "")
    move_attr = f' style="{move}"' if move else ""
    return (f'<g class="{cls}" data-kind="{escape(marker.kind)}"{move_attr}>'
            f'<g class="cm-p-pop" style="{_anim(POP_DELAY, POP_TIME, tempo)}">{shape}</g></g>')


def _ball_svg(scene: Scene, previous: Scene | None, shot_start: float, tempo: float) -> str:
    bx, by = scene.ball
    circle = f'<circle cx="{_n(bx)}" cy="{_n(by)}" r="0.85" fill="#ffffff" stroke="#111111" stroke-width="0.2"/>'
    shot = scene.shot
    if shot is not None:
        # top paslar bitince sutcunun ayaginda belirir ve okla birlikte gider
        duration = SHOT_TIME / 0.8
        style = f"{_offset(shot.x1 - bx, shot.y1 - by)};{_anim(shot_start - 0.2 * duration, duration, tempo)}"
        return f'<g class="cm-p-ball cm-p-kick" style="{style}">{circle}</g>'
    if previous is not None:
        style = _move_style(bx, by, previous.ball, tempo)
        return f'<g class="cm-p-ball cm-p-mv" style="{style}">{circle}</g>'
    return f'<g class="cm-p-ball">{circle}</g>'


def _goal_flash(shot: Arrow, delay: float, tempo: float) -> str:
    x = PITCH_LENGTH - 0.5 if shot.x2 > CENTER_X else -GOAL_DEPTH - 0.5
    return (f'<rect class="cm-p-flash" x="{x:g}" y="{GOAL_TOP - 1:.2f}" width="{GOAL_DEPTH + 1:g}" '
            f'height="{2 * GOAL_HALF_WIDTH + 2:.2f}" rx="0.6" fill="#ffd60a" opacity="0" '
            f'style="{_anim(delay, FLASH_TIME, tempo)}"/>')


def _direction_arrow(x0: float, y: float, to_right: bool, color: str, side: str) -> str:
    pts = [(0.0, -0.35), (4.0, -0.35), (4.0, -1.2), (6.0, 0.0), (4.0, 1.2), (4.0, 0.35), (0.0, 0.35)]
    coords = " ".join(f"{_n(x0 + (px if to_right else 6.0 - px))},{_n(y + py)}" for px, py in pts)
    direction = "right" if to_right else "left"
    return (f'<polygon class="cm-p-dir cm-p-dir-{side}" data-dir="{direction}" points="{coords}" '
            f'fill="{color}" stroke="#ffffff" stroke-width="0.15"/>')


def _legend_svg(scene: Scene) -> str:
    text = 'font-size="2.8" font-weight="700" fill="#f4f7f2"'
    return (
        f'<g class="cm-p-legend">'
        f'<circle cx="0" cy="-5.2" r="1.5" fill="{HOME_COLOR}" stroke="#ffffff" stroke-width="0.3"/>'
        f'{_direction_arrow(2.3, -5.2, scene.home_attacks_right, HOME_COLOR, "home")}'
        f'<text x="9.6" y="-4.2" {text}>{escape(_truncate(scene.home_team, 16))}</text>'
        f'<text x="52.5" y="-6.7" text-anchor="middle" font-size="2.1" fill="#f4f7f2" fill-opacity="0.8">'
        f'{escape(scene.display_minute)} · {escape(scene.phase)}</text>'
        f'<text x="52.5" y="-2.4" text-anchor="middle" font-size="3.4" font-weight="800" fill="#ffffff">'
        f'{int(scene.home_score)} - {int(scene.away_score)}</text>'
        f'<text x="95" y="-4.2" text-anchor="end" {text}>{escape(_truncate(scene.away_team, 16))}</text>'
        f'{_direction_arrow(96.2, -5.2, not scene.home_attacks_right, AWAY_COLOR, "away")}'
        f'<circle cx="{PITCH_LENGTH:g}" cy="-5.2" r="1.5" fill="{AWAY_COLOR}" stroke="#ffffff" stroke-width="0.3"/>'
        f"</g>"
    )


# ===========================================================================
# [7] SAHNE SVG
# ===========================================================================

def scene_svg(scene: Scene, previous: Scene | None = None, tempo: float = 1.0,
              width: str = "100%", show_labels: bool = True) -> str:
    """
    Sahne -> <div class="cm-p-wrap"><svg>...</svg></div> (tek satir, bos satir yok:
    Streamlit markdown'i HTML blogunu bolmesin). previous verilirse noktalar ve top
    onceki sahnedeki yerlerinden kayar. tempo tum sure/gecikmeleri olcekler.
    """
    t = clamp_tempo(tempo)
    uid = str(scene.frame_index)
    variant = "a" if scene.frame_index % 2 == 0 else "b"
    prev_dots = {_dot_key(d): d for d in previous.dots} if previous is not None else {}
    animate = previous is not None

    passes, shot = scene.passes, scene.shot
    shot_start = PASS_START + PASS_TIME * len(passes)
    shot_end = shot_start + SHOT_TIME

    inner = [_PITCH_MARKINGS]
    if shot is not None and shot.kind == "goal":
        inner.append(_goal_flash(shot, shot_end, t))
    inner.extend(_arrow_svg(a, uid, PASS_START + i * PASS_TIME, PASS_TIME, t) for i, a in enumerate(passes))
    ordered = [d for d in scene.dots if not d.highlight] + scene.highlighted
    inner.extend(_dot_svg(d, prev_dots.get(_dot_key(d)), animate, t) for d in ordered)
    if shot is not None:
        inner.append(_arrow_svg(shot, uid, shot_start, SHOT_TIME, t))
    inner.append(_ball_svg(scene, previous, shot_start, t))
    for marker in scene.markers:
        dot = next((d for d in scene.highlighted if (d.x, d.y) == (marker.x, marker.y)), None)
        prev = prev_dots.get(_dot_key(dot)) if dot is not None else None
        inner.append(_marker_svg(marker, prev, dot, t))
    if show_labels:
        inner.extend(_label_svg(d, prev_dots.get(_dot_key(d)), t) for d in scene.highlighted)

    caption = ""
    if show_labels and scene.caption:
        caption = (f'<text class="cm-p-caption" x="{CENTER_X:g}" y="73.4" text-anchor="middle" font-size="2.5" '
                   f'fill="#f4f7f2">{escape(_truncate(scene.caption, 72))}</text>')
    aria = f"{scene.home_team} {scene.home_score}-{scene.away_score} {scene.away_team}, {scene.display_minute}"
    return (
        f'<div class="cm-p-wrap" style="width:{_css_width(width)}">'
        f'<svg class="cm-p-svg cm-p-{variant}" xmlns="http://www.w3.org/2000/svg" viewBox="-4 -10 113 86" '
        f'role="img" aria-label="{escape(aria)}" data-frame="{scene.frame_index}">'
        f"{_arrow_defs(uid)}"
        f'<rect x="-4" y="-10" width="113" height="86" fill="#0b1f14"/>'
        f'<rect x="-4" y="-1.2" width="113" height="70.4" fill="#256b2a"/>'
        f'<svg x="0" y="0" width="{PITCH_LENGTH:g}" height="{PITCH_WIDTH:g}" '
        f'viewBox="0 0 {PITCH_LENGTH:g} {PITCH_WIDTH:g}" overflow="visible">'
        f'{"".join(inner)}</svg>'
        f"{_legend_svg(scene)}{caption}"
        f"</svg></div>"
    )


# ===========================================================================
# [8] TAKTIK TAHTASI (statik)
# ===========================================================================

# Dikey tahta: kendi kale cizgisi altta (y = 78), orta saha cizgisi y = 25.5
BOARD_LENGTH = 78.0
BOARD_DEPTH = {"GK": 5.0, "DEF": 20.0, "MID": 38.0, "FWD": 60.0}


def short_name(name: str | None, limit: int = 13) -> str:
    """'Mauro Icardi' -> 'M. Icardi' (tek kelimelik adlar oldugu gibi), gerekirse kirpilir."""
    parts = (name or "").split()
    text = f"{parts[0][0]}. {parts[-1]}" if len(parts) >= 2 else (name or "")
    return _truncate(text, limit)


def _board_markings() -> str:
    W, L = PITCH_WIDTH, BOARD_LENGTH
    half = L - CENTER_X
    pa_x, ga_x = (W - PENALTY_WIDTH) / 2, (W - GOAL_AREA_WIDTH) / 2
    spot = L - PENALTY_SPOT
    arc_dx = math.sqrt(CIRCLE_RADIUS ** 2 - (PENALTY_DEPTH - PENALTY_SPOT) ** 2)
    r = CIRCLE_RADIUS
    stripes = "".join(f'<rect x="0" y="{i * 9:g}" width="{W:g}" height="9" fill="#338a38"/>' for i in range(0, 9, 2))
    lines = (
        f'<path d="M0,0V{L:g}H{W:g}V0"/>'
        f'<line x1="0" y1="{half:g}" x2="{W:g}" y2="{half:g}"/>'
        f'<circle cx="{CENTER_Y:g}" cy="{half:g}" r="{r:g}"/>'
        f'<rect x="{pa_x:.2f}" y="{L - PENALTY_DEPTH:g}" width="{PENALTY_WIDTH:g}" height="{PENALTY_DEPTH:g}"/>'
        f'<rect x="{ga_x:.2f}" y="{L - GOAL_AREA_DEPTH:g}" width="{GOAL_AREA_WIDTH:g}" height="{GOAL_AREA_DEPTH:g}"/>'
        f'<path d="M{CENTER_Y - arc_dx:.2f},{L - PENALTY_DEPTH:g}A{r:g},{r:g} 0 0 1 '
        f'{CENTER_Y + arc_dx:.2f},{L - PENALTY_DEPTH:g}"/>'
    )
    return (
        f'<rect x="0" y="0" width="{W:g}" height="{L:g}" fill="#2e7d32"/>{stripes}'
        f'<g fill="none" stroke="#ffffff" stroke-opacity="0.85" stroke-width="0.3">{lines}</g>'
        f'<g fill="#ffffff" fill-opacity="0.9"><circle cx="{CENTER_Y:g}" cy="{half:g}" r="0.45"/>'
        f'<circle cx="{CENTER_Y:g}" cy="{spot:g}" r="0.35"/></g>'
        f'<rect x="{GOAL_TOP:.2f}" y="{L:g}" width="{2 * GOAL_HALF_WIDTH:.2f}" height="1.8" fill="#ffffff" '
        f'fill-opacity="0.2" stroke="#ffffff" stroke-width="0.3"/>'
    )


def _int_text(value) -> str:
    if value is None:
        return "–"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return escape(str(value))


def lineup_svg(slots: Sequence[tuple], team_name: str, color: str = HOME_COLOR,
               formation_label: str = "4-4-2") -> str:
    """
    Statik taktik tahtasi. slots: [(rol, ad | None, overall | None, kondisyon | None), ...]
    dizilis sirasinda (GK, DEF..., MID..., FWD...; tactics.arrange_slots ile ayni).
    Kondisyon halkasi: yesil >= 80, sari 60-79, kirmizi < 60, gri = bilinmiyor.
    Bos slot kesik cizgili daire ve 'boş' yazisiyla gosterilir.
    """
    fill = color if isinstance(color, str) and _HEX_COLOR.match(color) else HOME_COLOR
    keeper_fill = _shade(fill, 0.62)
    roles = [_role_value(slot[0]) for slot in slots]
    counts = {role: roles.count(role) for role in set(roles)}
    seen: dict[str, int] = {}
    items = []
    for slot, role in zip(slots, roles, strict=True):
        _, name, overall, condition = (tuple(slot) + (None, None, None))[:4]
        i = seen.get(role, 0)
        seen[role] = i + 1
        n = counts[role]
        x = 7.0 + 54.0 * (i + 0.5) / n
        y = BOARD_LENGTH - BOARD_DEPTH.get(role, 38.0) - (2.6 if n >= 5 and i % 2 else 0.0)
        cx, cy = _n(x), _n(y)
        font = "2.2" if n >= 5 else "2.4"
        if not name:
            items.append(
                f'<g class="cm-p-slot cm-p-slot-empty" data-role="{escape(role)}">'
                f'<circle class="cm-p-empty" cx="{cx}" cy="{cy}" r="3.2" fill="#000000" fill-opacity="0.15" '
                f'stroke="#ffffff" stroke-opacity="0.8" stroke-width="0.4" stroke-dasharray="0.9 0.7"/>'
                f'<text x="{cx}" y="{_n(y + 0.8)}" text-anchor="middle" font-size="2.2" fill="#ffffff">boş</text>'
                f'<text x="{cx}" y="{_n(y + 6.0)}" text-anchor="middle" font-size="{font}" fill="#ffffff" '
                f'fill-opacity="0.75">{escape(role)}</text></g>'
            )
            continue
        level = energy_level(condition)
        is_keeper = role == "GK"
        tooltip = f"{name} · {role} · OVR {_int_text(overall)} · Kondisyon {_int_text(condition)}"
        # kalecinin adi kale cizgisine binmesin: dairenin sagina yazilir
        label_x, label_y, anchor = ((_n(x + 5.0), _n(y + 0.9), "start") if is_keeper
                                    else (cx, _n(y + 6.6), "middle"))
        items.append(
            f'<g class="cm-p-slot" data-role="{escape(role)}">'
            f'<circle class="cm-p-ring cm-p-ring-{level}" cx="{cx}" cy="{cy}" r="4.2" fill="none" '
            f'stroke="{ENERGY_COLORS[level]}" stroke-width="0.8"/>'
            f'<circle cx="{cx}" cy="{cy}" r="3.2" fill="{keeper_fill if is_keeper else fill}" stroke="#ffffff" '
            f'stroke-width="{"0.6" if is_keeper else "0.4"}"/>'
            f'<text class="cm-p-ovr" x="{cx}" y="{_n(y + 1.0)}" text-anchor="middle" font-size="2.8" '
            f'font-weight="800" fill="#ffffff">{_int_text(overall)}</text>'
            f'<text class="cm-p-name" x="{label_x}" y="{label_y}" text-anchor="{anchor}" font-size="{font}" '
            f'font-weight="700" fill="#ffffff" stroke="#000000" stroke-opacity="0.6" stroke-width="0.45" '
            f'paint-order="stroke">{escape(short_name(name))}</text>'
            f"<title>{escape(tooltip)}</title></g>"
        )
    return (
        f'<div class="cm-p-wrap">'
        f'<svg class="cm-p-svg cm-p-board" xmlns="http://www.w3.org/2000/svg" viewBox="-3 -9 74 91" role="img" '
        f'aria-label="{escape(f"{team_name} {formation_label}")}">'
        f'<rect x="-3" y="-9" width="74" height="91" fill="#0b1f14"/>'
        f'<rect x="-3" y="-1.5" width="74" height="83.5" fill="#256b2a"/>'
        f"{_board_markings()}"
        f'<text x="-1" y="-3.6" font-size="3" font-weight="700" fill="#f4f7f2">'
        f"{escape(_truncate(team_name, 26))}</text>"
        f'<text x="69" y="-3.6" text-anchor="end" font-size="3" font-weight="700" fill="{fill}">'
        f"{escape(_truncate(formation_label, 10))}</text>"
        f'{"".join(items)}</svg></div>'
    )
