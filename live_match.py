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
    set_instructions    zihniyet + sertlik (+ istege bagli pas stili, tempo, pres, hucum yonu, ofsayt,
                        kontra; verilmeyen eksenler korunur) -- mac akarken de verilebilir
    set_team_instructions  talimatin tamamini degistirir
    set_roles           kaptan + duran top aticilari (team_roles.SetPieceRoles)
    set_plan / set_plans_enabled   durum bazli oyun plani (match_plan.MatchPlan); menajer elle
                        mudahale etse de kalan kurallar islenmeye devam eder, kapatilmadikca

Kurulumda (create) yonetilen takim manager_controlled=True olur: EngineConfig.ai_tactics acik olsa
bile AI bu takimin talimatina dokunmaz.

Degisiklik kurali (SubRule) mac kurulurken secilir ve iki takima da uygulanir:
    STANDARD        5 hak, pencere siniri yok (otomatik oynatilan maclarla ayni kural)
    FIVE_IN_THREE   5 hak en fazla 3 duraklamada (IFAB); devre arasi pencere saymaz
    CLASSIC_THREE   klasik 3 hak

Gorunum satirlari (lineup_rows, bench_rows, sub_status) arayuz kutuphanesinden bagimsizdir;
Streamlit bunlari tablo / secim kutusu olarak cizer.

14A (mac gunu ekrani): yeni otomatik duraklama nedenleri ve topla oynama gunlugu.
    pause_on_opponent_tactics   rakibin TACTICAL_CHANGE olayi (AI talimati / plan / menajer)
    pause_on_two_goals          TWO_GOALS_WINDOW oyun dakikasi icinde iki gol yemek (sonraki tetik iki YENI gol ister)
    pause_on_tired              ilk 11'den bir oyuncunun enerjisi TIRED_ENERGY altina dusmesi (oyuncu basina bir kez)
    pause_for_assistant         ASSISTANT_MINUTES dakikalarinda asistan notu
    Alanlarin kendi varsayilani KAPALIDIR: API ile kurulan maclar (testler, kariyer testleri) eskisi gibi durur.
    Arayuzun varsayilanlari AUTO_PAUSE_DEFAULTS sabitindedir (ikisi acik, ikisi kapali).
    pause_kind duraklamanin turunu tasir (break / key / two_goals / opponent_tactics / tired / assistant / manual).
    Duraklama motoru HIC etkilemez: rastgele sayi cekilmez, olay yazilmaz (determinizm testleri).
    possession_log: her adimdan sonra (oynanan dakika, ev sahiplik sayaci, deplasman sayaci). Sayac motorun sekans
    agirligidir (TeamStats.possession_weight; yoksa dakika sayaci) -- "Son 5 dk" cubugu bu gunlukten okunur ve
    maclarin genel payi (MatchResult.possession_share) ile ayni kaynaktandir.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

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
from match_plan import MatchPlan
from models import Position
from tactics import formation_name
from team_roles import SetPieceRoles

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
# 14A: yeni duraklama nedenlerinin ARAYUZ varsayilanlari (LiveMatch alanlari kendi basina kapali)
AUTO_PAUSE_DEFAULTS: dict[str, bool] = {
    "pause_on_opponent_tactics": True,
    "pause_on_two_goals": True,
    "pause_on_tired": False,
    "pause_for_assistant": False,
}
TWO_GOALS_WINDOW = 10                      # oyun dakikasi
TIRED_ENERGY = 60                          # lineup_rows'taki "yorgun" esigiyle ayni
ASSISTANT_MINUTES = (15, 30, 60, 75)
TWO_GOALS_TEXT = f"{TWO_GOALS_WINDOW} dakikada 2 gol yedik"
_PLAY_PHASES = frozenset({MatchPhase.FIRST_HALF, MatchPhase.SECOND_HALF,
                          MatchPhase.EXTRA_TIME_FIRST_HALF, MatchPhase.EXTRA_TIME_SECOND_HALF})


def possession_counters(home_stats, away_stats) -> tuple[float, float]:
    """
    Topla oynama sayaclari (ev, deplasman): sekans agirligi (MatchResult.possession_share ile ayni kaynak);
    motor agirlik yazmadiysa dakika sayaci.
    """
    home_w = getattr(home_stats, "possession_weight", 0.0)
    away_w = getattr(away_stats, "possession_weight", 0.0)
    if home_w + away_w > 0:
        return float(home_w), float(away_w)
    return float(home_stats.possession_minutes), float(away_stats.possession_minutes)


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
    # 14A: yeni duraklama nedenleri (arayuz varsayilanlari: AUTO_PAUSE_DEFAULTS)
    pause_on_opponent_tactics: bool = False
    pause_on_two_goals: bool = False
    pause_on_tired: bool = False
    pause_for_assistant: bool = False
    paused: bool = False
    pause_reason: str | None = None
    pause_kind: str | None = None              # break / key / two_goals / opponent_tactics / tired / assistant / manual
    saved: bool = False                        # kariyer sonucu veritabanina islendi mi
    history: list[str] = field(default_factory=list)
    # 14A: (oynanan dakika, ev sayaci, deplasman sayaci) -- "Son 5 dk" topla oynama
    possession_log: list[tuple[int, float, float]] = field(default_factory=list)
    conceded: list[int] = field(default_factory=list)       # yenen gollerin oynanan dakikasi
    two_goal_mark: int = 0                                  # son "2 gol" tetiginde yenen gol sayisi
    tired_seen: set[int] = field(default_factory=set)       # yorgunluk duraklamasi yapilmis oyuncular
    assistant_seen: set[int] = field(default_factory=set)   # asistan notu duraklamasi yapilmis dakikalar

    # ------------------------------------------------------------------ kurulum

    @classmethod
    def create(
        cls,
        engine: MatchEngine,
        managed_team_id: int | None = None,
        *,
        instructions: TeamInstructions | None = None,
        auto_subs: bool = True,
        plan: MatchPlan | None = None,
        roles: SetPieceRoles | None = None,
        plans_enabled: bool = True,
        **kwargs,
    ) -> LiveMatch:
        """
        Motoru baslama dudugune hazirlar. Talimatlar, roller, oyun plani ve asistan tercihi dudukten
        once yonetilen takima uygulanir (verilmeyenler motordaki mevcut degerini korur).
        """
        live = cls(engine=engine, managed_team_id=managed_team_id, **kwargs)
        team = live.managed_team
        if team is not None:
            team.auto_subs = auto_subs
            team.manager_controlled = True
            if instructions is not None:
                engine.set_instructions(team, instructions)
            if roles is not None:
                engine.set_roles(team, roles)
            if plan is not None:
                engine.set_plan(team, plan)
            engine.set_plans_enabled(team, plans_enabled)
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
        self._log_possession()
        self._auto_pause(events)
        return events

    def _log_possession(self) -> None:
        """Adim sonu topla oynama sayaclari (motoru okur, degistirmez). Ayni dakikaya ikinci kayit oncekinin yerine."""
        eng = self.engine
        home, away = possession_counters(eng.home.stats, eng.away.stats)
        sample = (self.elapsed, home, away)
        if self.possession_log and self.possession_log[-1][0] == sample[0]:
            self.possession_log[-1] = sample
        else:
            self.possession_log.append(sample)

    def run(self, max_steps: int | None = None) -> list[MatchEvent]:
        """Duraklatilana, mac bitene ya da max_steps adima kadar oynatir (anlik hiz)."""
        events: list[MatchEvent] = []
        steps = 0
        while not self.paused and not self.finished and (max_steps is None or steps < max_steps):
            events.extend(self.tick())
            steps += 1
        return events

    def play_to_end(self) -> list[MatchEvent]:
        """
        Kalan dakikalari otomatik duraklatma olmadan bitirir. Adimlar MatchEngine.run_to_end ile birebir aynidir
        (while not finished: step); aralarda yalnizca topla oynama gunlugu yazilir.
        """
        self.resume()
        before = len(self.engine.events)
        while not self.engine.finished:
            self.engine.step()
            self._log_possession()
        return self.engine.events[before:]

    def pause(self, reason: str = "Menajer maçı durdurdu", kind: str = "manual") -> None:
        if not self.finished:
            self.paused = True
            self.pause_reason = reason
            self.pause_kind = kind

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = None
        self.pause_kind = None

    def _auto_pause(self, events: list[MatchEvent]) -> None:
        if self.managed_team_id is None or self.finished:
            return
        reason = kind = None
        for ev in events:
            if self.pause_at_breaks and ev.type in BREAK_EVENTS:
                reason, kind = BREAK_EVENTS[ev.type], "break"
            elif (self.pause_on_key_events and ev.type in KEY_EVENTS
                  and ev.team_id == self.managed_team_id and ev.player):
                reason, kind = f"{KEY_EVENTS[ev.type]}: {ev.player}", "key"
        extra = self._decision_reasons(events)
        if reason is None and extra:
            kind, reason = extra[0]
        if reason:
            self.pause(reason, kind)
            if kind == "tired":
                self.tired_seen.add(self._tired_candidate().id)
            elif kind == "assistant":
                self.assistant_seen.add(self.engine.minute)

    # ------------------------------------------------------------------ 14A: karar anlari

    def _decision_reasons(self, events: list[MatchEvent]) -> list[tuple[str, str]]:
        """
        Yeni duraklama nedenleri, oncelik sirasinda: iki gol > rakip taktigi > yorgunluk > asistan notu.
        Yalnizca GORUNEN bilgi kullanilir (skor, olaylar, enerji, dakika); motor okunur, degistirilmez.
        """
        managed, opponent = self.managed_team, self.opponent_team
        if managed is None or opponent is None:
            return []
        out: list[tuple[str, str]] = []
        for ev in events:
            if ev.type is EventType.GOAL and ev.team_id == opponent.id:
                self.conceded.append(self.elapsed)
        fresh = self.conceded[self.two_goal_mark:]
        if len(fresh) >= 2 and fresh[-1] - fresh[-2] <= TWO_GOALS_WINDOW:
            self.two_goal_mark = len(self.conceded)
            if self.pause_on_two_goals:
                out.append(("two_goals", TWO_GOALS_TEXT))
        if self.pause_on_opponent_tactics:
            changes = [ev for ev in events if ev.type is EventType.TACTICAL_CHANGE and ev.team_id == opponent.id
                       and ev.detail != "plan_skipped"]
            if changes:
                out.append(("opponent_tactics", f"Rakip taktik değiştirdi ({changes[-1].display_minute})"))
        if self.pause_on_tired:
            tired = self._tired_candidate()
            if tired is not None:
                out.append(("tired", f"Yorgunluk: {tired.name} (kondisyon %{int(tired.energy)})"))
        eng = self.engine
        if (self.pause_for_assistant and eng.phase in _PLAY_PHASES and eng.added == 0
                and eng.minute in ASSISTANT_MINUTES and eng.minute not in self.assistant_seen):
            out.append(("assistant", f"Asistan notu ({eng.minute}')"))
        return out

    def _tired_candidate(self) -> MatchPlayer | None:
        """Ilk 11'den sahada olup enerjisi TIRED_ENERGY altina dusen, daha once bildirilmemis en yorgun oyuncu."""
        team = self.managed_team
        if team is None:
            return None
        pool = [p for p in team.on_pitch
                if p.entered_minute == 0 and p.energy < TIRED_ENERGY and p.id not in self.tired_seen]
        return min(pool, key=lambda p: (p.energy, p.id), default=None)

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
            raise InterventionError("Maç henüz başlamadı; ilk 11'i 📋 Kadro sayfasından kur.")
        if not self.finished and not (self.paused or self.engine.in_break):
            raise InterventionError("Oyuncu değişikliği için önce maçı durdur (⏸ DURDUR).")
        event = self.engine.manual_substitution(team, out_id, in_id, role)
        self._remember(event)
        return event

    def change_formation(self, formation: str) -> MatchEvent | None:
        team = self._require_managed()
        return self._remember(self.engine.change_formation(team, formation))

    def set_instructions(self, mentality: Mentality | str | None = None, tackling: Tackling | str | None = None,
                         **changes: Any) -> MatchEvent | None:
        """
        Zihniyet / sertlik (enum, deger ya da etiket) ve istege bagli diger eksenler (passing_style,
        tempo, pressing, attacking_focus, offside_trap, counter_attack). Verilmeyen eksenler korunur.
        """
        team = self._require_managed()
        if mentality is not None:
            changes["mentality"] = parse_mentality(mentality)
        if tackling is not None:
            changes["tackling"] = parse_tackling(tackling)
        instructions = team.instructions.with_changes(changes)
        return self._remember(self.engine.set_instructions(team, instructions))

    def set_team_instructions(self, instructions: TeamInstructions) -> MatchEvent | None:
        team = self._require_managed()
        return self._remember(self.engine.set_instructions(team, instructions))

    def set_roles(self, roles: SetPieceRoles) -> None:
        self.engine.set_roles(self._require_managed(), roles)

    def set_plan(self, plan: MatchPlan) -> None:
        self.engine.set_plan(self._require_managed(), plan)

    def set_plans_enabled(self, enabled: bool) -> None:
        self.engine.set_plans_enabled(self._require_managed(), enabled)

    @property
    def roles(self) -> SetPieceRoles:
        team = self.managed_team
        return team.roles if team is not None else SetPieceRoles()

    @property
    def plan(self) -> MatchPlan:
        team = self.managed_team
        return team.plan if team is not None else MatchPlan()

    @property
    def plans_enabled(self) -> bool:
        team = self.managed_team
        return team.plans_enabled if team is not None else False

    def plan_status(self) -> list[dict]:
        """Gorunum satirlari: kural, acik mi, islendi mi."""
        team = self._require_managed()
        return [{"Kural": index + 1, "Açıklama": rule.describe({p.id: p.name for p in team.players}),
                 "Açık": rule.enabled, "İşlendi": index in team.plan_fired}
                for index, rule in enumerate(team.plan.rules)]

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
        text = f"{MENTALITY_LABELS[inst.mentality]} · {TACKLING_LABELS[inst.tackling]}"
        return text + "".join(f" · {part}" for part in inst.extended_parts())
