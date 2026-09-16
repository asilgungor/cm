"""
live_match.py
=============
Canli mac kontrolcusu (9. Asama). SAF MANTIK: Streamlit ve veritabani BILMEZ.

MatchEngine'i dakika dakika ilerletir ve menajerin mac ici mudahalelerini yonetir:

    tick / run          bir adim / duraklatilana ya da mac bitene kadar
    pause / resume      DURDUR ve DEVAM; molalarda (devre arasi, uzatma molalari) ve kendi
                        takiminda sakatlik / kirmizi kartta otomatik duraklatma (istege bagli)
    play_to_end         kalan dakikalari duraklamadan bitirir ("Sonucu gör")
    substitute          oyuncu degisikligi: yalnizca mac DURAKKEN ya da molada (kural motorda:
                        hak, pencere, kaleci, sakat/atilmis/oyundan cikmis oyuncu)
    change_formation    dizilis (acil durum 5-3-2 dahil) -- mac akarken de verilebilir
    set_instructions    zihniyet + sertlik talimati -- mac akarken de verilebilir

Degisiklik kurali (SubRule) mac kurulurken secilir ve iki takima da uygulanir:
    STANDARD        5 hak, pencere siniri yok (otomatik oynatilan maclarla ayni kural)
    FIVE_IN_THREE   5 hak en fazla 3 duraklamada (IFAB); devre arasi pencere saymaz
    CLASSIC_THREE   klasik 3 hak

Gorunum satirlari (lineup_rows, bench_rows, sub_status) arayuz kutuphanesinden bagimsizdir;
Streamlit bunlari tablo / secim kutusu olarak cizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from instructions import (
    MENTALITY_LABELS,
    TACKLING_LABELS,
    Mentality,
    Tackling,
    TeamInstructions,
    parse_mentality,
    parse_tackling,
)
from match_engine import (
    EngineConfig,
    EventType,
    InterventionError,
    MatchEngine,
    MatchEvent,
    MatchPhase,
    MatchPlayer,
    MatchResult,
    MatchTeam,
)
from models import Position
from tactics import formation_name

ROLE_ORDER = (Position.GK, Position.DEF, Position.MID, Position.FWD)


class SubRule(str, Enum):
    STANDARD = "STANDARD"
    FIVE_IN_THREE = "FIVE_IN_THREE"
    CLASSIC_THREE = "CLASSIC_THREE"


SUB_RULE_LABELS: dict[SubRule, str] = {
    SubRule.STANDARD: "5 değişiklik (pencere sınırı yok)",
    SubRule.FIVE_IN_THREE: "5 değişiklik · en fazla 3 pencere (IFAB)",
    SubRule.CLASSIC_THREE: "3 değişiklik (klasik)",
}


def engine_config_for(rule: SubRule, base: EngineConfig | None = None) -> EngineConfig:
    """Kurala gore motor ayari. STANDARD taban ayari degistirmez (otomatik maclarla birebir ayni)."""
    cfg = base or EngineConfig()
    if rule is SubRule.FIVE_IN_THREE:
        return replace(cfg, max_subs=5, sub_windows=3)
    if rule is SubRule.CLASSIC_THREE:
        return replace(cfg, max_subs=3, sub_windows=None)
    return cfg


# Otomatik duraklatma: molalar ve menajerin takimindaki kritik olaylar
BREAK_EVENTS: dict[EventType, str] = {
    EventType.HALF_TIME: "Devre arası",
    EventType.EXTRA_TIME_START: "Uzatmalar öncesi mola",
    EventType.EXTRA_TIME_HALF: "Uzatmaların devre arası",
}
KEY_EVENTS: dict[EventType, str] = {
    EventType.INJURY: "Sakatlık",
    EventType.RED_CARD: "Kırmızı kart",
}
# Ilerleme cubugu: henuz aciklanmamis uzatma dakikalarinin tavanlari
# (match_engine._compute_added_time: ilk yari en fazla 4, ikinci yari en fazla 7 dakika;
# uzatma devrelerinin tavanlari EngineConfig'te)
FIRST_HALF_ADDED_CAP = 4
SECOND_HALF_ADDED_CAP = 7
_PHASE_ORDER = list(MatchPhase)            # MatchPhase kronolojik sirada tanimli

PHASE_LABELS: dict[MatchPhase, str] = {
    MatchPhase.NOT_STARTED: "Başlama öncesi",
    MatchPhase.FIRST_HALF: "1. Yarı",
    MatchPhase.HALF_TIME: "Devre Arası",
    MatchPhase.SECOND_HALF: "2. Yarı",
    MatchPhase.EXTRA_TIME_BREAK: "Uzatma Öncesi Mola",
    MatchPhase.EXTRA_TIME_FIRST_HALF: "1. uzatma",
    MatchPhase.EXTRA_TIME_HALF_TIME: "Uzatma Arası",
    MatchPhase.EXTRA_TIME_SECOND_HALF: "2. uzatma",
    MatchPhase.PENALTIES: "Penaltılar",
    MatchPhase.FINISHED: "Maç Sonu",
}


@dataclass(frozen=True)
class SubStatus:
    used: int
    limit: int
    windows_used: int | None          # pencere kurali yoksa None
    window_limit: int | None
    block: str | None                 # su an degisiklik yapilamiyorsa sebep

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def text(self) -> str:
        out = f"{self.used}/{self.limit} değişiklik"
        if self.window_limit is not None:
            out += f" · pencere {self.windows_used}/{self.window_limit}"
        return out


@dataclass
class LiveMatch:
    engine: MatchEngine
    managed_team_id: int | None = None         # menajerin yonettigi takim; None: yalnizca izleme
    fixture_id: int | None = None              # kariyer maci ise fikstur (hazirlik macinda None)
    competition: str = "friendly"              # "league" | "cup" | "friendly"
    season: int | None = None
    week: int | None = None
    title: str = ""
    sub_rule: SubRule = SubRule.STANDARD
    pause_at_breaks: bool = True
    pause_on_key_events: bool = True
    paused: bool = False
    pause_reason: str | None = None
    saved: bool = False                        # kariyer sonucu veritabanina islendi mi
    history: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ kurulum

    @classmethod
    def create(
        cls,
        engine: MatchEngine,
        managed_team_id: int | None = None,
        *,
        instructions: TeamInstructions | None = None,
        auto_subs: bool = True,
        **kwargs,
    ) -> LiveMatch:
        """Motoru baslama dudugune hazirlar. Talimatlar ve asistan tercihi dudukten once uygulanir."""
        live = cls(engine=engine, managed_team_id=managed_team_id, **kwargs)
        team = live.managed_team
        if team is not None:
            team.auto_subs = auto_subs
            if instructions is not None:
                engine.set_instructions(team, instructions)
        engine.start()
        return live

    # ------------------------------------------------------------------ durum

    @property
    def managed_team(self) -> MatchTeam | None:
        if self.managed_team_id is None:
            return None
        return self.engine.team_by_id(self.managed_team_id)

    @property
    def opponent_team(self) -> MatchTeam | None:
        team = self.managed_team
        if team is None:
            return None
        return self.engine.away if team is self.engine.home else self.engine.home

    @property
    def finished(self) -> bool:
        return self.engine.finished

    @property
    def phase(self) -> MatchPhase:
        return self.engine.phase

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS[self.engine.phase]

    @property
    def is_fixture(self) -> bool:
        return self.fixture_id is not None

    @property
    def can_substitute_now(self) -> bool:
        """Degisiklik icin mac durmus (ya da molada) olmali ve bitmemis olmali."""
        eng = self.engine
        return (self.managed_team is not None and eng.started and not eng.finished
                and eng.phase is not MatchPhase.PENALTIES and (self.paused or eng.in_break))

    @property
    def clock(self) -> str:
        eng = self.engine
        if not eng.started:
            return "0'"
        return f"{eng.minute}+{eng.added}'" if eng.added else f"{eng.minute}'"

    @property
    def elapsed(self) -> int:
        """
        Uzatma dakikalari dahil oynanan dakika (match_feed._elapsed ile ayni kaydirma). Seri
        penaltilarda motor saati son oyun dakikasina (90' / 120', +0) alir; oynanan sure ise
        match_feed'deki gibi toplam oyun suresinde sabit kalir (geri gitmez).
        """
        eng = self.engine
        if eng.shootout is not None:
            total = 90 + eng.first_half_added + eng.second_half_added
            if eng.extra_time_played:
                total += 30 + eng.extra_time_first_added + eng.extra_time_second_added
            return total
        m, a = eng.minute, eng.added
        if m <= 45:
            return m + a
        if m <= 90:
            return m + eng.first_half_added + a
        offset = eng.first_half_added + eng.second_half_added
        if m <= 105:
            return m + offset + a
        return m + offset + eng.extra_time_first_added + a

    def _added_known(self, phase: MatchPhase, last_minute: int) -> bool:
        """Devrenin uzatma dakikasi aciklandi mi? Motor bunu son normal dakikadan sonraki adimda hesaplar."""
        now, half = _PHASE_ORDER.index(self.engine.phase), _PHASE_ORDER.index(phase)
        if now != half:
            return now > half
        return self.engine.minute == last_minute and self.engine.added > 0

    @property
    def expected_total(self) -> int:
        """
        Ilerleme cubugu paydasi: macin EN FAZLA surebilecegi dakika. Henuz aciklanmamis uzatma
        dakikalari tavanlariyla, eleme macinda (uzatma kurali varken) olasi 2x15 uzatma da
        tavanlariyla sayilir. Bilgi netlestikce payda yalnizca kuculur, oynanan dakika ise hic
        azalmaz: ilerleme geri gitmez ve son oyun dakikasi oynanmadan %100 olmaz (90'da %100
        gorunup uzatma dakikalari aciklaninca geri dusmez).
        """
        eng, cfg = self.engine, self.engine.cfg
        total = 90
        total += eng.first_half_added if self._added_known(MatchPhase.FIRST_HALF, 45) else FIRST_HALF_ADDED_CAP
        total += eng.second_half_added if self._added_known(MatchPhase.SECOND_HALF, 90) else SECOND_HALF_ADDED_CAP
        rule = eng.knockout
        if eng.extra_time_played or (rule is not None and rule.extra_time and not eng.finished):
            total += 30
            total += (eng.extra_time_first_added if self._added_known(MatchPhase.EXTRA_TIME_FIRST_HALF, 105)
                      else cfg.extra_time_first_added_cap)
            total += (eng.extra_time_second_added if self._added_known(MatchPhase.EXTRA_TIME_SECOND_HALF, 120)
                      else cfg.extra_time_second_added_cap)
        return max(total, self.elapsed, 1)

    @property
    def progress(self) -> float:
        if self.finished:
            return 1.0
        return min(1.0, self.elapsed / self.expected_total)

    def snapshot(self) -> MatchResult:
        return self.engine.snapshot()

    def result(self) -> MatchResult:
        return self.engine.result()

    # ------------------------------------------------------------------ akis

    def tick(self) -> list[MatchEvent]:
        """Bir adim. Durakken ya da bitmisse hicbir sey yapmaz."""
        if self.paused or self.finished:
            return []
        events = self.engine.step()
        self._auto_pause(events)
        return events

    def run(self, max_steps: int | None = None) -> list[MatchEvent]:
        """Duraklatilana, mac bitene ya da max_steps adima kadar oynatir (anlik hiz)."""
        events: list[MatchEvent] = []
        steps = 0
        while not self.paused and not self.finished and (max_steps is None or steps < max_steps):
            events.extend(self.tick())
            steps += 1
        return events

    def play_to_end(self) -> list[MatchEvent]:
        """Kalan dakikalari otomatik duraklatma olmadan bitirir."""
        self.resume()
        before = len(self.engine.events)
        self.engine.run_to_end()
        return self.engine.events[before:]

    def pause(self, reason: str = "Menajer maçı durdurdu") -> None:
        if not self.finished:
            self.paused = True
            self.pause_reason = reason

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = None

    def _auto_pause(self, events: list[MatchEvent]) -> None:
        if self.managed_team_id is None or self.finished:
            return
        reason = None
        for ev in events:
            if self.pause_at_breaks and ev.type in BREAK_EVENTS:
                reason = BREAK_EVENTS[ev.type]
            elif (self.pause_on_key_events and ev.type in KEY_EVENTS
                  and ev.team_id == self.managed_team_id and ev.player):
                reason = f"{KEY_EVENTS[ev.type]}: {ev.player}"
        if reason:
            self.pause(reason)

    # ------------------------------------------------------------------ mudahaleler

    def _require_managed(self) -> MatchTeam:
        team = self.managed_team
        if team is None:
            raise InterventionError("Bu maçta yönettiğin bir takım yok (yalnızca izleme).")
        return team

    def _remember(self, event: MatchEvent | None) -> MatchEvent | None:
        if event is not None:
            self.history.append(f"{event.display_minute} {event.description}")
        return event

    def substitute(self, out_id: int, in_id: int, role: Position | None = None) -> MatchEvent:
        team = self._require_managed()
        if not self.engine.started:
            raise InterventionError("Maç henüz başlamadı; ilk 11'i Kadro & Taktik sekmesinden kur.")
        if not self.finished and not (self.paused or self.engine.in_break):
            raise InterventionError("Oyuncu değişikliği için önce maçı durdur (⏸ DURDUR).")
        event = self.engine.manual_substitution(team, out_id, in_id, role)
        self._remember(event)
        return event

    def change_formation(self, formation: str) -> MatchEvent | None:
        team = self._require_managed()
        return self._remember(self.engine.change_formation(team, formation))

    def set_instructions(self, mentality: Mentality | str, tackling: Tackling | str) -> MatchEvent | None:
        team = self._require_managed()
        instructions = TeamInstructions(parse_mentality(mentality), parse_tackling(tackling))
        return self._remember(self.engine.set_instructions(team, instructions))

    # ------------------------------------------------------------------ gorunum satirlari

    @property
    def formation(self) -> str:
        team = self.managed_team
        return formation_name(team.formation) if team is not None else ""

    @property
    def instructions(self) -> TeamInstructions:
        team = self.managed_team
        return team.instructions if team is not None else TeamInstructions()

    def sub_status(self) -> SubStatus:
        team = self._require_managed()
        eng = self.engine
        block = eng.substitution_block(team)
        if block is None and not team.bench:
            block = "kulübede oyuncu kalmadı"
        return SubStatus(
            used=team.subs_used, limit=eng.max_subs,
            windows_used=team.sub_windows_used if eng.max_windows is not None else None,
            window_limit=eng.max_windows, block=block,
        )

    @staticmethod
    def _player_row(p: MatchPlayer, role: Position | None) -> dict:
        cards = "🟨" * p.yellow_cards
        note = []
        if role is not None and role is not p.position:
            note.append("mevki dışı")
        if p.energy < 60:
            note.append("yorgun")
        return {
            "id": p.id,
            "Oyuncu": p.name,
            "Görev": (role or p.position).value,
            "Mevki": p.position.value,
            "OVR": p.overall,
            "Kondisyon": round(p.energy),
            "Kart": cards,
            "Gol": p.goals,
            "Not": ", ".join(note),
        }

    def lineup_rows(self) -> list[dict]:
        """Yonetilen takimin sahadaki oyunculari: gorev sirasi, ayni gorevde dusuk kondisyon once."""
        team = self._require_managed()
        players = sorted(team.on_pitch, key=lambda p: (ROLE_ORDER.index(p.role or p.position), p.energy, p.id))
        return [self._player_row(p, p.role or p.position) for p in players]

    def bench_rows(self) -> list[dict]:
        """Oyuna girebilecek yedekler (saglikli, oynamamis, kadroda)."""
        team = self._require_managed()
        players = sorted(team.bench, key=lambda p: (ROLE_ORDER.index(p.position), -p.effective_power, p.id))
        return [self._player_row(p, None) for p in players]

    def instructions_text(self) -> str:
        inst = self.instructions
        return f"{MENTALITY_LABELS[inst.mentality]} · {TACKLING_LABELS[inst.tackling]}"
