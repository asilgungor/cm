"""
match_feed.py
=============
Mac akisi "gorunum modeli" (6. Asama). SAF MANTIK: arayuz kutuphanesi bilmez.

MatchResult -> kare (Frame) listesi. Her kare bir olayi ve O ANKI skor/istatistik
durumunu tasir. Web arayuzu (Streamlit), ileride Pygame ya da baska bir istemci
ayni kareleri sirayla oynatarak "canli mac" gosterir. Motor maci aninda bitirir;
canlilik yalnizca sunumdadir.

Istatistikler olaylardan KUMULATIF hesaplanir. Motor her sutu tam olarak bir
olayla kaydeder (MISS = isabetsiz, SAVE = kurtarilan, GOAL = gol), bu yuzden son
karedeki sayilar MatchResult'taki takim istatistikleriyle birebir ayni olmalidir
(testlerle kilitli).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from match_engine import EventType, MatchEvent, MatchResult

SHOT_EVENTS = {EventType.MISS, EventType.SAVE, EventType.GOAL}
ON_TARGET_EVENTS = {EventType.SAVE, EventType.GOAL}

# Olay -> gorsel vurgu turu (arayuz renk/animasyon secer)
HIGHLIGHT: dict[EventType, str] = {
    EventType.GOAL: "goal",
    EventType.RED_CARD: "red",
    EventType.YELLOW_CARD: "yellow",
    EventType.INJURY: "injury",
    EventType.SAVE: "chance",
    EventType.MISS: "chance",
    EventType.SUBSTITUTION: "sub",
    EventType.KICK_OFF: "whistle",
    EventType.HALF_TIME: "whistle",
    EventType.FULL_TIME: "whistle",
}

LABELS: dict[EventType, str] = {
    EventType.KICK_OFF: "BAŞLA", EventType.GOAL: "GOL", EventType.MISS: "ŞUT",
    EventType.SAVE: "KURTARIŞ", EventType.YELLOW_CARD: "SARI KART", EventType.RED_CARD: "KIRMIZI KART",
    EventType.INJURY: "SAKATLIK", EventType.SUBSTITUTION: "DEĞİŞİKLİK",
    EventType.HALF_TIME: "DEVRE ARASI", EventType.FULL_TIME: "MAÇ SONU",
}

# Olay turune gore oynatma suresi carpani: gol ve kirmizi kartta sahne biraz beklesin
PACING: dict[str, float] = {
    "goal": 3.0, "red": 2.2, "injury": 1.6, "yellow": 1.3, "whistle": 1.8,
    "chance": 1.0, "sub": 0.8,
}


@dataclass
class SideStats:
    goals: int = 0
    shots: int = 0
    on_target: int = 0
    yellow: int = 0
    red: int = 0
    injuries: int = 0
    subs: int = 0


@dataclass
class FeedEvent:
    type: str
    label: str
    highlight: str
    side: str | None          # "home" / "away" / None (hakem olaylari)
    team: str | None
    player: str | None
    description: str
    detail: str | None = None  # orn. RED_CARD: "second_yellow" / "straight_red"


@dataclass
class Frame:
    index: int
    minute: int
    added_time: int
    display_minute: str
    elapsed: int              # oynanan toplam dakika (uzatmalar dahil) -> ilerleme cubugu
    phase: str                # "1. Yarı" / "Devre Arası" / "2. Yarı" / "Maç Sonu"
    home_score: int
    away_score: int
    event: FeedEvent
    home: SideStats = field(default_factory=SideStats)
    away: SideStats = field(default_factory=SideStats)

    @property
    def pacing(self) -> float:
        return PACING.get(self.event.highlight, 1.0)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MatchSummary:
    home: str
    away: str
    home_score: int
    away_score: int
    home_scorers: list[tuple[str, int]]
    away_scorers: list[tuple[str, int]]
    possession_home: int
    possession_away: int
    man_of_the_match: str | None
    motm_rating: float | None
    total_minutes: int
    home_stats: SideStats
    away_stats: SideStats


def _side(event: MatchEvent, result: MatchResult) -> str | None:
    if event.team_id is None:
        return None
    if event.team_id == result.home.id:
        return "home"
    if event.team_id == result.away.id:
        return "away"
    return None


def _elapsed(event: MatchEvent, result: MatchResult) -> int:
    """Uzatmalar dahil oynanan dakika. 2. yarinin dakikalari ilk yari uzatmasi kadar kayar."""
    if event.minute <= 45:
        return event.minute + event.added_time
    return event.minute + result.first_half_added + event.added_time


def _phase(event: MatchEvent) -> str:
    if event.type is EventType.FULL_TIME:
        return "Maç Sonu"
    if event.type is EventType.HALF_TIME:
        return "Devre Arası"
    if event.minute < 45 or (event.minute == 45 and event.type is not EventType.HALF_TIME):
        return "1. Yarı"
    return "2. Yarı"


def _update(stats: SideStats, event_type: EventType, detail: str | None = None) -> None:
    if event_type in SHOT_EVENTS:
        stats.shots += 1
    if event_type in ON_TARGET_EVENTS:
        stats.on_target += 1
    if event_type is EventType.GOAL:
        stats.goals += 1
    elif event_type is EventType.YELLOW_CARD:
        stats.yellow += 1
    elif event_type is EventType.RED_CARD:
        stats.red += 1
        if detail == "second_yellow":     # motor ikinci sariyi sari sayacina da yazar
            stats.yellow += 1
    elif event_type is EventType.INJURY:
        stats.injuries += 1
    elif event_type is EventType.SUBSTITUTION:
        stats.subs += 1


def build_timeline(result: MatchResult) -> list[Frame]:
    """Mac olaylarini kumulatif skor/istatistikli karelere cevirir."""
    home, away = SideStats(), SideStats()
    frames: list[Frame] = []
    for index, event in enumerate(result.events):
        side = _side(event, result)
        if side == "home":
            _update(home, event.type, event.detail)
        elif side == "away":
            _update(away, event.type, event.detail)

        frames.append(Frame(
            index=index,
            minute=event.minute,
            added_time=event.added_time,
            display_minute=event.display_minute,
            elapsed=_elapsed(event, result),
            phase=_phase(event),
            home_score=event.home_score,
            away_score=event.away_score,
            event=FeedEvent(
                type=event.type.value,
                label=LABELS[event.type],
                highlight=HIGHLIGHT[event.type],
                side=side,
                team=event.team,
                player=event.player,
                description=event.description,
                detail=event.detail,
            ),
            home=SideStats(**asdict(home)),
            away=SideStats(**asdict(away)),
        ))
    return frames


def energy_at(player, minute: int) -> int | None:
    """
    Oyuncunun verilen dakikadaki kondisyonu (motorun energy_log orneklerinden).
    Kayit yoksa ya da oyuncu henuz sahada degilse None.
    """
    log = getattr(player, "energy_log", None) or []
    value = None
    for sample_minute, energy in log:
        if sample_minute > minute:
            break
        value = energy
    return value


def team_energy_at(team, minute: int) -> int | None:
    """Takimin o dakikada sahada olan oyuncularinin ortalama kondisyonu."""
    values = []
    for p in team.players:
        entered, left = p.entered_minute, p.left_minute
        if entered is None or entered > minute or (left is not None and left < minute):
            continue
        energy = energy_at(p, minute)
        if energy is not None:
            values.append(energy)
    return round(sum(values) / len(values)) if values else None


def summarize(result: MatchResult, frames: list[Frame] | None = None) -> MatchSummary:
    frames = frames if frames is not None else build_timeline(result)
    last = frames[-1] if frames else None
    total_pos = max(1, result.home.stats.possession_minutes + result.away.stats.possession_minutes)
    possession_home = round(100 * result.home.stats.possession_minutes / total_pos)
    motm = result.man_of_the_match
    return MatchSummary(
        home=result.home.name,
        away=result.away.name,
        home_score=result.home_score,
        away_score=result.away_score,
        home_scorers=result.scorers(result.home),
        away_scorers=result.scorers(result.away),
        possession_home=possession_home,
        possession_away=100 - possession_home,
        man_of_the_match=motm.name if motm else None,
        motm_rating=motm.rating if motm else None,
        total_minutes=result.total_minutes,
        home_stats=last.home if last else SideStats(),
        away_stats=last.away if last else SideStats(),
    )
