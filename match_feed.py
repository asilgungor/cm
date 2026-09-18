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

Eleme maclari (8. Asama): uzatma dakikalari (91-120+X) ilerleme cubugunda kesintisiz
akar; seri penalti atislari (PENALTY_SHOOTOUT) gol/sut SAYILMAZ, kareye ayri bir
penalti skoru (home_penalties / away_penalties) olarak yazilir. Seri karelerinin
elapsed degeri toplam oyun suresine sabitlenir (saat ileri gitmez, geri de gitmez).

Canli mudahale (9. Asama): menajerin molada (devre arasi, uzatma molalari) yaptigi degisiklik
ve taktik olaylari molanin dakikasina yazilir; bu kareler molanin evresini tasir ("Devre Arası"),
"1. Yarı" gibi gorunmez. TACTICAL_CHANGE istatistige girmez. Kareler bitmemis bir macin
anlik goruntusunden (MatchEngine.snapshot) de kurulabilir.

Taktik derinlik: yeni olay turu yoktur. Duran toplar GOAL / SAVE / MISS olaylaridir (detail "penalty" /
"free_kick" / "corner") ve sut istatistigine AYNEN girer; yalnizca etiketleri ayrisir ("PENALTI GOLÜ").
Oyun plani eylemleri SUBSTITUTION / TACTICAL_CHANGE (detail "plan"), uygulanamayan plan eylemi
TACTICAL_CHANGE (detail "plan_skipped") olarak gelir; TACTICAL_CHANGE istatistige girmez.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

from match_engine import SHOOTOUT_EVENTS, EventType, MatchEvent, MatchResult

SHOT_EVENTS = {EventType.MISS, EventType.SAVE, EventType.GOAL}
ON_TARGET_EVENTS = {EventType.SAVE, EventType.GOAL}

# Faz etiketleri (skor tabelasi ve 2D saha yon secimi bunlari kullanir)
PHASE_PRE_MATCH = "Başlama öncesi"
PHASE_FIRST_HALF = "1. Yarı"
PHASE_HALF_TIME = "Devre Arası"
PHASE_SECOND_HALF = "2. Yarı"
PHASE_ET_BREAK = "Normal Süre Bitti"      # EXTRA_TIME_START karesi: uzatma oncesi mola
PHASE_ET_FIRST = "1. uzatma"
PHASE_ET_HALF = "Uzatma Arası"
PHASE_ET_SECOND = "2. uzatma"
PHASE_SHOOTOUT = "Penaltılar"
PHASE_FULL_TIME = "Maç Sonu"

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
    EventType.EXTRA_TIME_START: "whistle",
    EventType.EXTRA_TIME_HALF: "whistle",
    EventType.SHOOTOUT_START: "whistle",
    EventType.PENALTY_SHOOTOUT: "pen_goal",     # gercek vurgu atis sonucuna gore: KICK_HIGHLIGHT
    EventType.TACTICAL_CHANGE: "tactic",
}

# Mola olayi -> molanin evresi; molada yapilan mudahaleler (ayni dakika) bu evrede kalir
BREAK_EVENT_PHASES: dict[EventType, str] = {
    EventType.HALF_TIME: PHASE_HALF_TIME,
    EventType.EXTRA_TIME_START: PHASE_ET_BREAK,
    EventType.EXTRA_TIME_HALF: PHASE_ET_HALF,
}
BREAK_INTERVENTIONS = frozenset({EventType.SUBSTITUTION, EventType.TACTICAL_CHANGE})

# Seri penalti atisi: detail ("scored" / "saved" / "missed") -> vurgu ve etiket
KICK_HIGHLIGHT: dict[str, str] = {"scored": "pen_goal", "saved": "pen_miss", "missed": "pen_miss"}
KICK_LABELS: dict[str, str] = {"scored": "PENALTI GOL", "saved": "PENALTI KURTARIŞ", "missed": "PENALTI KAÇTI"}
# Mac ici duran top ve oyun plani olaylari: (tur, detail) -> etiket (yoksa LABELS)
DETAIL_LABELS: dict[tuple[EventType, str], str] = {
    (EventType.GOAL, "penalty"): "PENALTI GOLÜ",
    (EventType.SAVE, "penalty"): "PENALTI KURTARIŞ",
    (EventType.MISS, "penalty"): "PENALTI KAÇTI",
    (EventType.GOAL, "free_kick"): "FRİKİK GOLÜ",
    (EventType.SAVE, "free_kick"): "FRİKİK",
    (EventType.MISS, "free_kick"): "FRİKİK",
    (EventType.GOAL, "corner"): "KORNERDEN GOL",
    (EventType.SAVE, "corner"): "KORNER",
    (EventType.MISS, "corner"): "KORNER",
    (EventType.SUBSTITUTION, "plan"): "DEĞİŞİKLİK (PLAN)",
    (EventType.TACTICAL_CHANGE, "plan"): "OYUN PLANI",
    (EventType.TACTICAL_CHANGE, "plan_skipped"): "OYUN PLANI",
    (EventType.TACTICAL_CHANGE, "ai"): "TAKTİK",
}

LABELS: dict[EventType, str] = {
    EventType.KICK_OFF: "BAŞLA", EventType.GOAL: "GOL", EventType.MISS: "ŞUT",
    EventType.SAVE: "KURTARIŞ", EventType.YELLOW_CARD: "SARI KART", EventType.RED_CARD: "KIRMIZI KART",
    EventType.INJURY: "SAKATLIK", EventType.SUBSTITUTION: "DEĞİŞİKLİK",
    EventType.HALF_TIME: "DEVRE ARASI", EventType.FULL_TIME: "MAÇ SONU",
    EventType.EXTRA_TIME_START: "UZATMALAR", EventType.EXTRA_TIME_HALF: "UZATMA ARASI",
    EventType.SHOOTOUT_START: "PENALTILAR", EventType.PENALTY_SHOOTOUT: "PENALTI",
    EventType.TACTICAL_CHANGE: "TAKTİK",
}

# Olay turune gore oynatma suresi carpani: gol ve kirmizi kartta sahne biraz beklesin
PACING: dict[str, float] = {
    "goal": 3.0, "red": 2.2, "injury": 1.6, "yellow": 1.3, "whistle": 1.8,
    "chance": 1.0, "sub": 0.8, "pen_goal": 1.6, "pen_miss": 2.0, "tactic": 1.2,
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
    # Seri penalti golleri: 'goals'a EKLENMEZ (penaltilar mac skoru degildir)
    penalties: int = 0
    # --- birinci sinif mac istatistikleri (13A / S2) ---
    # Bunlar olay akisindan DEGIL, motorun takim sayaclarindan gelir (korner ve faullerin
    # cogu akista gosterilmez ama sayilmalari gerekir -- yoksa istatistik yalan soyler).
    # Yalnizca `summarize()` doldurur; ara kareler 0 birakir.
    corners: int = 0
    fouls: int = 0
    offsides: int = 0


@dataclass
class FeedEvent:
    type: str
    label: str
    highlight: str
    side: str | None          # "home" / "away" / None (hakem olaylari)
    team: str | None
    player: str | None
    description: str
    detail: str | None = None  # RED_CARD: "second_yellow" / "straight_red"; PENALTY_SHOOTOUT: "scored" / ...
    kick_number: int | None = None  # seri penalti atis sirasi


@dataclass
class Frame:
    index: int
    minute: int
    added_time: int
    display_minute: str
    elapsed: int              # oynanan toplam dakika (uzatmalar dahil) -> ilerleme cubugu
    phase: str                # PHASE_* sabitlerinden biri ("1. Yarı" ... "Penaltılar", "Maç Sonu")
    home_score: int
    away_score: int
    event: FeedEvent
    home: SideStats = field(default_factory=SideStats)
    away: SideStats = field(default_factory=SideStats)
    extra_time: bool = False                  # mac uzatmaya gitti mi (EXTRA_TIME_START karesinden itibaren)
    home_penalties: int | None = None         # seri basladiysa anlik penalti skoru, yoksa None
    away_penalties: int | None = None

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
    # --- eleme maclari ---
    extra_time: bool = False
    home_penalties: int | None = None
    away_penalties: int | None = None
    decided_by: str = "normal"                # "normal" | "extra_time" | "penalties"
    knockout: bool = False
    home_aggregate: int | None = None         # yalnizca eleme macinda
    away_aggregate: int | None = None
    advancing: str | None = None              # tur atlayan takimin adi (eleme macinda)


def _side(event: MatchEvent, result: MatchResult) -> str | None:
    if event.team_id is None:
        return None
    if event.team_id == result.home.id:
        return "home"
    if event.team_id == result.away.id:
        return "away"
    return None


def _elapsed(event: MatchEvent, result: MatchResult, in_shootout: bool = False) -> int:
    """
    Uzatmalar dahil oynanan dakika. Her devrenin dakikalari onceki devrelerin uzatmalari
    kadar kayar. Seri penalti (ve arkasindaki FULL_TIME) toplam oyun suresine sabitlenir.
    """
    if in_shootout or event.type in SHOOTOUT_EVENTS:
        return result.total_minutes
    if event.minute <= 45:
        return event.minute + event.added_time
    if event.minute <= 90:
        return event.minute + result.first_half_added + event.added_time
    offset = result.first_half_added + result.second_half_added
    if event.minute <= 105:
        return event.minute + offset + event.added_time
    return event.minute + offset + result.extra_time_first_added + event.added_time


def _phase(event: MatchEvent, in_shootout: bool = False) -> str:
    if event.type is EventType.FULL_TIME:
        return PHASE_FULL_TIME
    if in_shootout or event.type in SHOOTOUT_EVENTS:
        return PHASE_SHOOTOUT
    if event.type is EventType.HALF_TIME:
        return PHASE_HALF_TIME
    if event.type is EventType.EXTRA_TIME_START:
        return PHASE_ET_BREAK
    if event.type is EventType.EXTRA_TIME_HALF:
        return PHASE_ET_HALF
    if event.minute < 45 or (event.minute == 45 and event.type is not EventType.HALF_TIME):
        return PHASE_FIRST_HALF
    if event.minute <= 90:
        return PHASE_SECOND_HALF
    if event.minute <= 105:
        return PHASE_ET_FIRST
    return PHASE_ET_SECOND


def _highlight(event: MatchEvent) -> str:
    if event.type is EventType.PENALTY_SHOOTOUT:
        return KICK_HIGHLIGHT.get(event.detail or "", "pen_miss")
    return HIGHLIGHT[event.type]


def _label(event: MatchEvent) -> str:
    if event.type is EventType.PENALTY_SHOOTOUT:
        return KICK_LABELS.get(event.detail or "", LABELS[event.type])
    if event.detail:
        return DETAIL_LABELS.get((event.type, event.detail), LABELS[event.type])
    return LABELS[event.type]


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
    elif event_type is EventType.PENALTY_SHOOTOUT and detail == "scored":
        stats.penalties += 1


def build_timeline(result: MatchResult) -> list[Frame]:
    """Mac olaylarini kumulatif skor/istatistikli karelere cevirir."""
    home, away = SideStats(), SideStats()
    frames: list[Frame] = []
    extra_time = in_shootout = False
    break_phase: tuple[str, int] | None = None      # (evre, dakika): az once mola dudugu caldi
    for index, event in enumerate(result.events):
        side = _side(event, result)
        if side == "home":
            _update(home, event.type, event.detail)
        elif side == "away":
            _update(away, event.type, event.detail)
        if event.type is EventType.EXTRA_TIME_START or event.minute > 90:
            extra_time = True
        if event.type in SHOOTOUT_EVENTS:
            in_shootout = True
        phase = _phase(event, in_shootout)
        if event.type in BREAK_EVENT_PHASES:
            break_phase = (BREAK_EVENT_PHASES[event.type], event.minute)
        elif (break_phase is not None and event.type in BREAK_INTERVENTIONS
              and event.minute == break_phase[1]):
            phase = break_phase[0]
        else:
            break_phase = None

        frames.append(Frame(
            index=index,
            minute=event.minute,
            added_time=event.added_time,
            display_minute=event.display_minute,
            elapsed=_elapsed(event, result, in_shootout),
            phase=phase,
            home_score=event.home_score,
            away_score=event.away_score,
            event=FeedEvent(
                type=event.type.value,
                label=_label(event),
                highlight=_highlight(event),
                side=side,
                team=event.team,
                player=event.player,
                description=event.description,
                detail=event.detail,
                kick_number=event.kick_number,
            ),
            home=SideStats(**asdict(home)),
            away=SideStats(**asdict(away)),
            extra_time=extra_time,
            home_penalties=event.home_penalties if in_shootout else None,
            away_penalties=event.away_penalties if in_shootout else None,
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


def _final_stats(side: SideStats, team) -> SideStats:
    """Kare istatistiklerine motorun akista gorunmeyen sayaclarini ekler (korner, faul, ofsayt)."""
    return replace(side, corners=getattr(team.stats, "corners", 0),
                   fouls=getattr(team.stats, "fouls", 0),
                   offsides=getattr(team.stats, "offsides", 0))


def summarize(result: MatchResult, frames: list[Frame] | None = None) -> MatchSummary:
    frames = frames if frames is not None else build_timeline(result)
    last = frames[-1] if frames else None
    total_pos = max(1, result.home.stats.possession_minutes + result.away.stats.possession_minutes)
    possession_home = round(100 * result.home.stats.possession_minutes / total_pos)
    motm = result.man_of_the_match
    advancing = result.advancing
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
        home_stats=_final_stats(last.home if last else SideStats(), result.home),
        away_stats=_final_stats(last.away if last else SideStats(), result.away),
        extra_time=result.extra_time,
        home_penalties=result.home_penalties,
        away_penalties=result.away_penalties,
        decided_by=result.decided_by,
        knockout=result.knockout is not None,
        home_aggregate=result.home_aggregate if result.knockout is not None else None,
        away_aggregate=result.away_aggregate if result.knockout is not None else None,
        advancing=advancing.name if advancing is not None else None,
    )
