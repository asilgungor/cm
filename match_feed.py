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

13B "anlatim" (K7): SIMULE EDILEN AKIS ile GOSTERILEN AKIS ayrisir.
    Motor artik korner, kartsiz faul, ofsayt ve kurulus (BUILD_UP) olaylarini da YAZAR --
    istatistikler onlardan turer. Akista bunlarin cogu GIZLENIR: her olay bir
    `display_probability` tasir ve `build_timeline` gorunur kareleri ona gore secer.
    Gizleme KARE duzeyindedir; olay uretimi asla atlanmaz, yoksa istatistik yalan soyler.
    Kumulatif istatistikler GIZLENEN olaylari da sayar, bu yuzden son kare motorun
    takim sayaclariyla birebir tutar.
    `build_timeline(result, include_hidden=True)` gizlenenler dahil TAM akisi verir.

    Olu hava tabani: `DEAD_AIR_MINUTES` dakikadir hicbir satir gosterilmediyse sirada ne
    varsa gosterilir (gizlenmesi gereken bir korner bile olsa). Boylece 30 dakikalik
    sessizlikler kalmaz ama akis da sismez.

    Bekleme kademeleri: her kare `dwell_ms` tasir (rutin ~900 / ana ~1800 / kritik ~3000).
    Oynatma katmani (13C) bunu kullanir; mevcut arayuz `pacing` ile calismaya devam eder.

    Gecmis zaman mac raporu: `match_report(result)` -> MatchReport (kirilma ani, belirleyici
    isim, okunabilir paragraflar). Motor onemli olaylara `report` alanini yazar.
"""

from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass, field, replace

from match_engine import (
    DWELL_MAIN,
    SHOOTOUT_EVENTS,
    EventType,
    MatchEvent,
    MatchResult,
)

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
    # 13B: dusuk etkili akis olaylari (cogu gizlenir, gosterilenler sakin vurgu alir)
    EventType.CORNER: "flow",
    EventType.FOUL: "flow",
    EventType.OFFSIDE: "flow",
    EventType.BUILD_UP: "build",
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
    (EventType.BUILD_UP, "win"): "ATAK",
    (EventType.BUILD_UP, "entry"): "ATAK",
    (EventType.BUILD_UP, "final"): "SON PAS",
    (EventType.BUILD_UP, "pressure"): "BASKI",
}

LABELS: dict[EventType, str] = {
    EventType.KICK_OFF: "BAŞLA", EventType.GOAL: "GOL", EventType.MISS: "ŞUT",
    EventType.SAVE: "KURTARIŞ", EventType.YELLOW_CARD: "SARI KART", EventType.RED_CARD: "KIRMIZI KART",
    EventType.INJURY: "SAKATLIK", EventType.SUBSTITUTION: "DEĞİŞİKLİK",
    EventType.HALF_TIME: "DEVRE ARASI", EventType.FULL_TIME: "MAÇ SONU",
    EventType.EXTRA_TIME_START: "UZATMALAR", EventType.EXTRA_TIME_HALF: "UZATMA ARASI",
    EventType.SHOOTOUT_START: "PENALTILAR", EventType.PENALTY_SHOOTOUT: "PENALTI",
    EventType.TACTICAL_CHANGE: "TAKTİK",
    EventType.CORNER: "KORNER", EventType.FOUL: "FAUL", EventType.OFFSIDE: "OFSAYT",
    EventType.BUILD_UP: "ATAK",
}

# Olay turune gore oynatma suresi carpani: gol ve kirmizi kartta sahne biraz beklesin
PACING: dict[str, float] = {
    "goal": 3.0, "red": 2.2, "injury": 1.6, "yellow": 1.3, "whistle": 1.8,
    "chance": 1.0, "sub": 0.8, "pen_goal": 1.6, "pen_miss": 2.0, "tactic": 1.2,
    "flow": 0.6, "build": 0.7,
}

# 13B: bu kadar (oyun) dakikasi hicbir satir gosterilmediyse siradaki olay gosterilir.
DEAD_AIR_MINUTES = 5


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
    # --- birinci sinif mac istatistikleri (13A / S2, 13B) ---
    # 13B: artik OLAY AKISINDAN kumulatif sayilir (korner, faul, ofsayt olaylari uretiliyor;
    # cogu akista gizlenir ama sayilir). Her karede o ana kadarki deger okunur ve son
    # kare motorun takim sayaclariyla birebir tutar (testle kilitli).
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
    # --- 13B / K5 sunum meta verisi (sans kalitesi BILEREK tasinmaz: K6, K12) ---
    priority: int = 55
    dwell_ms: int = DWELL_MAIN
    chain_id: int | None = None     # ayni atak pasajindaki kareler (follow-on)
    chain_step: int = 0


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
    visible: bool = True                      # 13B: akista gosterilir mi (gizliler yine sayilir)

    @property
    def pacing(self) -> float:
        return PACING.get(self.event.highlight, 1.0)

    @property
    def dwell_ms(self) -> int:
        """13B: olay basina bekleme (ms). 13C oynatma katmani bunu kullanir."""
        return self.event.dwell_ms

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


_FOUL_SET_PIECES = frozenset({"free_kick", "penalty"})
_CARD_EVENTS = frozenset({EventType.YELLOW_CARD, EventType.RED_CARD})


def _update(stats: SideStats, event_type: EventType, detail: str | None = None,
            other: SideStats | None = None) -> None:
    """
    Kumulatif istatistik. `other` rakibin sayaclaridir: frikik / penalti sutu, SAVUNAN
    takimin faulunden dogar (motor faulu oraya yazar).
    """
    if event_type in SHOT_EVENTS:
        stats.shots += 1
        if detail == "corner":
            stats.corners += 1
        elif detail in _FOUL_SET_PIECES and other is not None:
            other.fouls += 1
    if event_type in ON_TARGET_EVENTS:
        stats.on_target += 1
    if event_type in _CARD_EVENTS:
        stats.fouls += 1                  # motor her kart olayini bir faul olarak da sayar
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
    elif event_type is EventType.CORNER:
        stats.corners += 1
    elif event_type is EventType.FOUL:
        stats.fouls += 1
    elif event_type is EventType.OFFSIDE:
        stats.offsides += 1


def _display_roll(seed: int | None, index: int) -> float:
    """
    Gosterim karari icin DETERMINISTIK sayi: (mac tohumu, olay sirasi) -> [0, 1).
    Motorun RNG'sine dokunmaz; ayni mac her kuruluşta ayni akisi verir (canli ekranda
    anlik goruntuden tekrar tekrar kurulsa bile satirlar yer degistirmez).
    """
    return (zlib.crc32(f"{seed}|{index}|feed".encode()) & 0xFFFFFFFF) / 4294967296.0


def is_visible(event: MatchEvent, index: int, seed: int | None, silent_for: int) -> bool:
    """
    K7 gosterim filtresi. `silent_for`: son gorunur satirdan beri gecen oyun dakikasi.
    Olay uretimi hicbir zaman atlanmaz; bu yalnizca KAREYI gizler.
    """
    probability = event.display_probability
    if probability >= 1.0:
        return True
    if silent_for >= DEAD_AIR_MINUTES:
        return True                       # olu hava tabani: sirada ne varsa gosterilir
    if probability <= 0.0:
        return False
    return _display_roll(seed, index) < probability


def build_timeline(result: MatchResult, include_hidden: bool = False) -> list[Frame]:
    """
    Mac olaylarini kumulatif skor/istatistikli karelere cevirir.

    Varsayilan: yalnizca GORUNUR kareler (13B / K7). Istatistikler gizli olaylari da sayar,
    bu yuzden her karedeki sayilar o ana kadarki TAM akisi yansitir. `include_hidden=True`
    her olay icin bir kare dondurur (gizliler `visible=False`). `Frame.index` her zaman
    olayin `result.events` icindeki sirasidir (pitch.py rol gecmisini buna gore okur).
    """
    home, away = SideStats(), SideStats()
    frames: list[Frame] = []
    extra_time = in_shootout = False
    break_phase: tuple[str, int] | None = None      # (evre, dakika): az once mola dudugu caldi
    last_shown = 0                                  # son gorunur karenin oynanan dakikasi
    seed = result.seed
    for index, event in enumerate(result.events):
        side = _side(event, result)
        if side == "home":
            _update(home, event.type, event.detail, away)
        elif side == "away":
            _update(away, event.type, event.detail, home)
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

        elapsed = _elapsed(event, result, in_shootout)
        visible = is_visible(event, index, seed, elapsed - last_shown)
        if visible:
            last_shown = elapsed
        elif not include_hidden:
            continue

        frames.append(Frame(
            index=index,
            minute=event.minute,
            added_time=event.added_time,
            display_minute=event.display_minute,
            elapsed=elapsed,
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
                priority=event.priority,
                dwell_ms=event.dwell_ms,
                chain_id=event.chain_id,
                chain_step=event.chain_step,
            ),
            home=replace(home),
            away=replace(away),
            extra_time=extra_time,
            home_penalties=event.home_penalties if in_shootout else None,
            away_penalties=event.away_penalties if in_shootout else None,
            visible=visible,
        ))
    return frames


def visible_frames(frames: list[Frame]) -> list[Frame]:
    """`include_hidden=True` ile kurulmus bir akistan yalnizca gorunur kareler."""
    return [f for f in frames if f.visible]


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
    """
    Son karenin istatistikleri. 13B'den beri korner / faul / ofsayt akistan sayilir ve
    motorun sayaclariyla birebir tutar (testle kilitli); motor bu olaylari yazmiyorsa
    (eski yol: `EngineConfig.flow_events=False`) takim sayaclari kullanilir.
    """
    corners = getattr(team.stats, "corners", 0)
    fouls = getattr(team.stats, "fouls", 0)
    offsides = getattr(team.stats, "offsides", 0)
    return replace(side, corners=max(side.corners, corners), fouls=max(side.fouls, fouls),
                   offsides=max(side.offsides, offsides))


def summarize(result: MatchResult, frames: list[Frame] | None = None) -> MatchSummary:
    frames = frames if frames is not None else build_timeline(result)
    last = frames[-1] if frames else None
    # 13B / D12: dakika sahipligi degil, sekans agirlikli topla oynama
    possession_home, possession_away = result.possession_share() or (50, 50)
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
        possession_away=possession_away,
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


# ===========================================================================
# 13B: GECMIS ZAMAN MAC RAPORU
# ===========================================================================
# Motor onemli olaylara (gol, kirmizi kart, penalti, sakatlik, kart) `report` alanina
# gecmis zaman cumlesini yazar. Rapor bu cumlelerden KURULUR: yeni bilgi uydurulmaz,
# sans kalitesi hicbir yerde sayi olarak gecmez (K6).

REPORT_KINDS: dict[EventType, str] = {
    EventType.GOAL: "goal",
    EventType.RED_CARD: "red",
    EventType.INJURY: "injury",
    EventType.SAVE: "save",
    EventType.MISS: "miss",
}


@dataclass
class ReportMoment:
    minute: str                 # "67'" / "90+3'"
    elapsed: int                # siralama ve devre ayrimi icin oynanan dakika
    kind: str                   # goal / red / injury / penalty_save / penalty_miss
    side: str | None
    team: str | None
    text: str                   # gecmis zaman cumlesi
    score: tuple[int, int]      # o andan sonraki skor (ev, deplasman)


@dataclass
class MatchReport:
    headline: str
    intro: str
    moments: list[ReportMoment]
    turning_point: str | None
    verdict: str
    paragraphs: list[str]

    def text(self) -> str:
        return "\n\n".join([self.headline, *self.paragraphs])


def _report_moments(result: MatchResult) -> list[ReportMoment]:
    moments: list[ReportMoment] = []
    for event in result.events:
        if not event.report or event.type in SHOOTOUT_EVENTS:
            continue
        kind = REPORT_KINDS.get(event.type)
        if kind is None:
            continue
        if kind in ("save", "miss"):
            if event.detail != "penalty":
                continue                      # siradan sutlar rapora girmez
            kind = f"penalty_{kind}"
        moments.append(ReportMoment(
            minute=event.display_minute, elapsed=_elapsed(event, result), kind=kind,
            side=_side(event, result), team=event.team, text=event.report,
            score=(event.home_score, event.away_score),
        ))
    return moments


def _turning_point(result: MatchResult, moments: list[ReportMoment]) -> str | None:
    """
    Macin kirilma ani: kazananin one gectigi ve bir daha geri dusmedigi gol; kazanan
    yoksa ya da boyle bir gol yoksa ilk kirmizi kart; beraberlikte son esitlik golu.
    """
    winner = result.winner
    goals = [m for m in moments if m.kind == "goal"]
    if winner is not None and goals:
        side = "home" if winner is result.home else "away"
        decisive = None
        for m in goals:
            h, a = m.score
            lead = h - a if side == "home" else a - h
            if m.side == side and lead == 1:
                decisive = m                  # son "one gecis" golu = geri donulmeyen an
            elif lead <= 0:
                decisive = None               # kazanan sonradan geri dustu / yakalandi
        if decisive is not None:
            return f"Kırılma anı {decisive.minute} oldu: {decisive.text}"
    reds = [m for m in moments if m.kind == "red"]
    if reds:
        return f"Maçın dengesi {reds[0].minute} değişti: {reds[0].text}"
    if goals and result.is_draw:
        last = goals[-1]
        return f"Son sözü {last.minute} gelen gol söyledi: {last.text}"
    return None


def _headline(result: MatchResult) -> str:
    h, a = result.home_score, result.away_score
    home, away = result.home.name, result.away.name
    if result.decided_by == "penalties" and result.advancing is not None:
        return (f"{home} {h}-{a} {away} (pen. {result.home_penalties}-{result.away_penalties}): "
                f"{result.advancing.name} seride güldü")
    if h == a:
        return f"{home} {h}-{a} {away}: puanlar paylaşıldı" if h else f"{home} 0-0 {away}: gol sesi çıkmadı"
    winner, loser = (home, away) if h > a else (away, home)
    margin = abs(h - a)
    if margin >= 3:
        return f"{home} {h}-{a} {away}: {winner} farka koştu"
    if margin == 2:
        return f"{home} {h}-{a} {away}: {winner} rahat kazandı"
    return f"{home} {h}-{a} {away}: {winner}, {loser} karşısında kıl payı güldü"


def _verdict(result: MatchResult) -> str:
    scorers: dict[str, int] = {}
    for team in (result.home, result.away):
        for name, count in result.scorers(team):
            scorers[name] = scorers.get(name, 0) + count
    parts: list[str] = []
    if scorers:
        top, count = max(scorers.items(), key=lambda kv: kv[1])
        if count >= 3:
            parts.append(f"Gecenin adı hat-trick yapan {top} oldu.")
        elif count == 2:
            parts.append(f"{top} iki golle öne çıktı.")
    else:
        parts.append("İki takım da bitiricilikte sınıfta kaldı.")
    motm = result.man_of_the_match
    if motm is not None:
        parts.append(f"Maçın adamı {motm.name} seçildi.")
    if result.shootout is not None and result.advancing is not None:
        parts.append(f"Seriyi {result.home_penalties}-{result.away_penalties} kazanan "
                     f"{result.advancing.name} tur atladı.")
    return " ".join(parts)


def match_report(result: MatchResult) -> MatchReport:
    """
    Okunabilir, gecmis zaman mac raporu (13B). Olaylarin `report` cumlelerinden kurulur.
    Arayuz `paragraphs` listesini ya da `text()` ciktisini dogrudan gosterebilir.
    """
    moments = _report_moments(result)
    headline = _headline(result)
    home, away = result.home, result.away
    intro = (f"{home.name} ile {away.name} arasındaki maç {result.home_score}-{result.away_score} "
             f"sona erdi. Şutlarda {home.stats.shots}-{away.stats.shots}")
    poss = result.possession_share()
    intro += f", topla oynamada %{poss[0]}-%{poss[1]} bir tablo vardı." if poss else " bir tablo vardı."
    half_line = 45 + result.first_half_added
    paragraphs = [intro]
    for title, block in (("İlk yarıda", [m for m in moments if m.elapsed <= half_line]),
                         ("İkinci yarıda", [m for m in moments if m.elapsed > half_line])):
        if block:
            paragraphs.append(f"{title} " + " ".join(f"{m.minute} {m.text}" for m in block))
        elif title == "İlk yarıda":
            paragraphs.append("İlk yarı skor tabelasını değiştirecek bir an doğurmadı.")
    turning = _turning_point(result, moments)
    if turning:
        paragraphs.append(turning)
    verdict = _verdict(result)
    paragraphs.append(verdict)
    return MatchReport(headline=headline, intro=intro, moments=moments, turning_point=turning,
                       verdict=verdict, paragraphs=paragraphs)
