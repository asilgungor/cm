"""
tournament_manager.py
=====================
Devler Arenasi (Champions Cup) kontrolcusu (8. Asama).

Sorumluluklar:
    * Katilimcilari belirler (sezon 1: itibar, sonraki sezonlar: bir onceki lig siralamasi)
      ve torbalari kurar (kurallar cup_draw.py)
    * Interaktif kurayi yurutur: her top cup_draw.DrawSession ile cekilir, durum JSON olarak
      tournaments.draw_state'e yazilir -> sayfa yenilense de kura kaldigi toptan devam eder
    * Kura bitince eslesmeleri (cup_ties) ve fiksturleri uretir
    * Takvim lig haftalariyla senkron akar (cup_draw.build_calendar). Ayni hafta lig maci da
      olan takim icin kupa "hafta ici" oynanir: kondisyon yarim toparlanir, rotasyon gerekir
    * Eleme: iki macli turlarda toplam skor; rovans toplamda esitse match_engine uzatma ve
      penalti atislari oynatir (KnockoutRule). Final tek mac, tarafsiz saha
    * Kupa cezalari ligden ayri: kirmizi -> kupa cezasi, her 3 sari -> 1 mac,
      sari birikimi ceyrek final sonunda silinir (UEFA kurali)
    * Tur atlamak ve kupayi kaldirmak menajer tanınırlığını artirir (reputation.cup_round_delta)
    * Sorgular: gol/asist kralligi, sakat/cezali listesi, agac ve grup tablosu verisi
    * Canli mac (9. Asama): prepare_cup_engine kupa fiksturunun motorunu otomatik mac gunuyle
      AYNI kurallarla (tohum, eleme kurali, tarafsiz saha, kupa cezalari) kurar; play_matchday
      live_results ile menajerin canli oynadigi macin bitmis sonucunu simulasyon yerine isler

Katman: LOGIC (controller). COMMIT ETMEZ; CareerManager ile ayni session'i kullanir.
career_manager'i calisma zamaninda import etmez (dongusel import olmasin diye).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import desc, func, select

import reputation
from cup_draw import (
    STAGE_LABELS,
    CupFormat,
    CupTeam,
    DrawSession,
    DrawStep,
    Matchday,
    Stage,
    build_calendar,
    cup_size_for,
    formats_for,
    group_qualifier_pairs,
    group_schedule,
    make_pots,
    next_round_pairs,
    qualify,
    rank_group,
    stages_for,
)
from match_engine import (
    EngineConfig,
    KnockoutRule,
    MatchEngine,
    MatchResult,
    apply_result,
    prepare_fixture,
    update_standings,
)
from models import (
    Competition,
    CupTie,
    Fixture,
    FixtureStatus,
    GameMode,
    Player,
    PlayerMatchStat,
    Team,
    Tournament,
    TournamentEntry,
    TournamentStatus,
)

if TYPE_CHECKING:
    from career_manager import CareerManager, WeekReport

CUP_NAME = "Devler Arenası (Champions Cup)"
CUP_SHORT_NAME = "Devler Arenası"         # canli mac basligi gibi dar alanlar icin
DEFAULT_FORMAT = CupFormat.KNOCKOUT
CUP_YELLOW_BAN_EVERY = 3                 # kupada her 3 sari = 1 mac ceza
YELLOW_RESET_AFTER = Stage.QF            # bu tur bitince kupa sari birikimi silinir
GROUP_LABELS = "ABCD"
DECIDED_LABELS = {"normal": "", "extra_time": "uzatmalarda", "penalties": "penaltılarla"}

# Kupa fiksturune ozet olarak yazilan olaylar (tam mac akisi yerine)
KEY_EVENT_TYPES = frozenset({
    "GOAL", "RED_CARD", "INJURY", "EXTRA_TIME_START", "EXTRA_TIME_HALF",
    "SHOOTOUT_START", "PENALTY_SHOOTOUT", "FULL_TIME",
})


class TournamentError(Exception):
    """Kullaniciya gosterilebilir kupa hatasi."""


@dataclass
class CupPlayerRow:
    player: Player
    team: Team
    goals: int
    assists: int
    appearances: int
    avg_rating: float


@dataclass
class UnavailableRow:
    player: Player
    team: Team
    kind: str            # "injury" | "ban" | "risk"
    reason: str


def matchday_label(md: Matchday) -> str:
    if md.stage is Stage.GROUP:
        return f"Grup Aşaması {md.leg}. maç"
    if md.stage is Stage.FINAL:
        return "Final"
    return f"{STAGE_LABELS[md.stage]} {'ilk maç' if md.leg == 1 else 'rövanş'}"


def _event_dict(ev) -> dict:
    kind = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
    return {
        "minute": ev.minute, "added": ev.added_time, "type": kind,
        "team_id": ev.team_id, "team": ev.team, "player_id": ev.player_id, "player": ev.player,
        "detail": ev.detail, "home_score": ev.home_score, "away_score": ev.away_score,
        "home_penalties": getattr(ev, "home_penalties", 0),
        "away_penalties": getattr(ev, "away_penalties", 0),
        "kick_number": getattr(ev, "kick_number", None),
        "text": ev.description,
    }


class TournamentManager:
    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm
        self.db = cm.db

    # ================================================================== sorgular

    def current(self, season: int | None = None) -> Tournament | None:
        season = self.cm.season if season is None else season
        return self.db.scalar(select(Tournament).where(Tournament.season == season))

    @staticmethod
    def fmt(t: Tournament) -> CupFormat:
        return CupFormat(t.format)

    def stages(self, t: Tournament) -> list[Stage]:
        return stages_for(self.fmt(t), t.size)

    @staticmethod
    def calendar(t: Tournament) -> list[Matchday]:
        return [
            Matchday(number=int(m["number"]), stage=Stage(m["stage"]), leg=int(m["leg"]), week=int(m["week"]))
            for m in (t.calendar or [])
        ]

    def matchday_for_week(self, t: Tournament, week: int) -> Matchday | None:
        return next((md for md in self.calendar(t) if md.week == week), None)

    def last_week(self, t: Tournament | None) -> int:
        if t is None:
            return 0
        return max((md.week for md in self.calendar(t)), default=0)

    def projected_last_week(self) -> int:
        """
        Bu sezonun kupa takviminin son haftasi; turnuva henuz kurulmadiysa varsayilan formatla KURULACAK
        takvimden hesaplanir (yan etkisiz: ensure cagirmaz). Dunya kupaya yetmiyorsa 0.
        """
        t = self.current()
        if t is not None:
            return self.last_week(t)
        size = cup_size_for(sum(len(league.teams) for league in self.cm.leagues()))
        if size == 0:
            return 0
        allowed = formats_for(size)
        fmt = DEFAULT_FORMAT if DEFAULT_FORMAT in allowed else allowed[0]
        days = build_calendar(fmt, size, self.cm.league_weeks(), self.cm.game_mode is GameMode.TOURNAMENT)
        return max((md.week for md in days), default=0)

    def next_matchday(self, t: Tournament, week: int | None = None) -> Matchday | None:
        """Bu haftadan itibaren oynanmamis ilk kupa gunu."""
        week = self.cm.current_week if week is None else week
        if t.status is TournamentStatus.FINISHED:
            return None
        return next((md for md in self.calendar(t) if md.week >= week), None)

    def entries(self, t: Tournament) -> dict[int, TournamentEntry]:
        return {e.team_id: e for e in t.entries}

    def is_participant(self, t: Tournament | None, team_id: int | None) -> bool:
        return t is not None and team_id is not None and any(e.team_id == team_id for e in t.entries)

    def participants(self, t: Tournament) -> list[Team]:
        return [e.team for e in sorted(t.entries, key=lambda e: e.seed_rank)]

    def fixtures(self, t: Tournament, week: int | None = None, stage: Stage | None = None) -> list[Fixture]:
        stmt = select(Fixture).where(Fixture.tournament_id == t.id).order_by(Fixture.week, Fixture.id)
        if week is not None:
            stmt = stmt.where(Fixture.week == week)
        if stage is not None:
            stmt = stmt.where(Fixture.stage == stage.value)
        return list(self.db.scalars(stmt))

    def ties(self, t: Tournament, stage: Stage | None = None) -> list[CupTie]:
        ties = [tie for tie in t.ties if stage is None or tie.stage == stage.value]
        return sorted(ties, key=lambda tie: (self.stages(t).index(Stage(tie.stage)), tie.slot))

    def draw_session(self, t: Tournament) -> DrawSession | None:
        return DrawSession.from_state(t.draw_state) if t.draw_state else None

    def has_unplayed(self, t: Tournament | None) -> bool:
        return t is not None and t.status is not TournamentStatus.FINISHED

    def user_status(self, t: Tournament | None, team_id: int | None) -> str:
        """Kullanici takiminin kupadaki durumu (kisa metin)."""
        if t is None:
            return "Bu sezon Devler Arenası yok"
        entry = self.entries(t).get(team_id) if team_id is not None else None
        if entry is None:
            return "Takımın bu sezon Devler Arenası'nda yok"
        if t.status is TournamentStatus.DRAW:
            return "Kura bekleniyor"
        if t.champion_team_id == team_id:
            return "🏆 ŞAMPİYON"
        if entry.eliminated_stage:
            label = "Grup aşamasında" if entry.eliminated_stage == Stage.GROUP.value \
                else STAGE_LABELS[Stage(entry.eliminated_stage)]
            return f"{label} elendi" if entry.eliminated_stage == Stage.GROUP.value \
                else f"{label} turunda elendi"
        if t.status is TournamentStatus.FINISHED:
            return "Turnuva bitti"
        open_ties = [tie for tie in t.ties if tie.involves(team_id) and not tie.decided]
        if open_ties:
            return f"{STAGE_LABELS[Stage(open_ties[0].stage)]} turunda"
        if self.fmt(t) is CupFormat.GROUPS and entry.group_index is not None:
            return f"Grup {GROUP_LABELS[entry.group_index]}"
        return "Sıradaki turu bekliyor"

    # ================================================================== kurulum

    def qualification_tables(self, by_standings: bool) -> list[list[CupTeam]]:
        """Her lig icin siralanmis katilimci adaylari (lig siralamasi ya da itibar)."""
        from career_manager import standings_key  # dongusel import: yalniz calisma aninda

        tables: list[list[CupTeam]] = []
        for league in self.cm.leagues():
            key = standings_key if by_standings else (lambda t: (-t.reputation, t.name))
            teams = sorted(league.teams, key=key)
            tables.append([CupTeam(t.id, t.name, league.name, float(t.reputation)) for t in teams])
        return tables

    def ensure(self, fmt: CupFormat | None = None) -> Tournament | None:
        """Bu sezonun turnuvasi yoksa kurar (itibara gore katilim). Yeterli takim yoksa None."""
        t = self.current()
        if t is not None:
            return t
        return self.create(self.cm.season, self.qualification_tables(by_standings=False), fmt)

    def create(self, season: int, tables: list[list[CupTeam]], fmt: CupFormat | None = None) -> Tournament | None:
        total = sum(len(table) for table in tables)
        size = cup_size_for(total)
        if size == 0:
            return None
        allowed = formats_for(size)
        fmt = fmt if fmt in allowed else (DEFAULT_FORMAT if DEFAULT_FORMAT in allowed else allowed[0])
        teams = qualify(tables, size)

        t = Tournament(season=season, name=CUP_NAME, format=fmt.value, size=size,
                       status=TournamentStatus.DRAW)
        ranked = sorted(teams, key=lambda c: (-c.coefficient, c.name))
        t.entries = [
            TournamentEntry(team_id=c.id, seed_rank=rank, coefficient=c.coefficient, league_name=c.league)
            for rank, c in enumerate(ranked, start=1)
        ]
        self.db.add(t)
        self.db.flush()
        self._prepare_draw(t, fmt, ranked)
        return t

    def _cup_teams(self, t: Tournament) -> list[CupTeam]:
        return [
            CupTeam(e.team_id, e.team.name, e.league_name, e.coefficient)
            for e in sorted(t.entries, key=lambda e: e.seed_rank)
        ]

    def _prepare_draw(self, t: Tournament, fmt: CupFormat, teams: list[CupTeam]) -> None:
        pots = make_pots(teams, fmt)
        seed = self.cm.rng.randrange(1, 2**31)
        session = DrawSession(fmt, pots, seed=seed)
        t.format = fmt.value
        t.draw_state = session.to_state()
        by_team = self.entries(t)
        for pot_index, pot in enumerate(pots):
            for team in pot:
                by_team[team.id].pot = pot_index
        self.refresh_calendar(t)
        self.db.flush()

    def refresh_calendar(self, t: Tournament) -> None:
        """Takvimi mod ve lig uzunluguna gore (yeniden) hesaplar. Mac oynanmadan cagrilmali."""
        tournament_only = self.cm.game_mode is GameMode.TOURNAMENT
        matchdays = build_calendar(self.fmt(t), t.size, self.cm.league_weeks(), tournament_only)
        t.calendar = [
            {"number": md.number, "stage": md.stage.value, "leg": md.leg, "week": md.week}
            for md in matchdays
        ]
        # Kura bittiyse uretilmis fiksturlerin haftalari da yeni takvime tasinir
        for fx in self.fixtures(t):
            if fx.is_played:
                raise TournamentError("Kupa maçı oynanmış; takvim değiştirilemez.")
            md = self._matchday_of(t, Stage(fx.stage), fx.leg)
            fx.week = md.week

    def _matchday_of(self, t: Tournament, stage: Stage, leg: int) -> Matchday:
        return next(md for md in self.calendar(t) if md.stage is stage and md.leg == leg)

    def set_format(self, fmt: CupFormat) -> Tournament:
        """Kura baslamadan formati degistirir (eleme / gruplar)."""
        t = self.current()
        if t is None:
            raise TournamentError("Bu sezon turnuva yok.")
        session = self.draw_session(t)
        if t.status is not TournamentStatus.DRAW or (session and session.steps):
            raise TournamentError("Kura başladıktan sonra format değiştirilemez.")
        if fmt not in formats_for(t.size):
            raise TournamentError(f"{t.size} takımlı turnuvada bu format yok.")
        self._prepare_draw(t, fmt, self._cup_teams(t))
        return t

    # ================================================================== kura

    def _draw_target(self) -> tuple[Tournament, DrawSession]:
        t = self.ensure()
        if t is None:
            raise TournamentError("Turnuva kurulamadı: dünyada en az 8 takım gerekli.")
        if t.status is not TournamentStatus.DRAW:
            raise TournamentError("Kura zaten tamamlandı.")
        return t, self.draw_session(t)

    def draw_next(self) -> DrawStep:
        """Tek top ceker ve kaydeder. Kura bu topla bittiyse eslesmeler/fiksturler uretilir."""
        t, session = self._draw_target()
        step = session.draw_next()
        self._save_draw(t, session)
        return step

    def draw_all(self) -> list[DrawStep]:
        """'Kurayi otomatik cek': kalan tum toplar."""
        t, session = self._draw_target()
        steps = session.draw_all()
        self._save_draw(t, session)
        return steps

    def _save_draw(self, t: Tournament, session: DrawSession) -> None:
        t.draw_state = session.to_state()          # yeni dict: JSONB degisikligi algilanir
        if session.complete:
            self._finalize_draw(t, session)
        self.db.flush()

    def _finalize_draw(self, t: Tournament, session: DrawSession) -> None:
        first_stage = self.stages(t)[0]
        if first_stage is Stage.GROUP:
            by_team = self.entries(t)
            for group_index, team_ids in enumerate(session.groups()):
                for team_id in team_ids:
                    by_team[team_id].group_index = group_index
                for round_index, pairs in enumerate(group_schedule(team_ids), start=1):
                    md = self._matchday_of(t, Stage.GROUP, round_index)
                    for home_id, away_id in pairs:
                        self.db.add(self._fixture(t, None, Stage.GROUP, round_index, md.week, home_id, away_id))
        else:
            for slot, (first_id, second_id) in enumerate(session.pairs()):
                self._create_tie(t, first_stage, slot, first_id, second_id)
        t.status = TournamentStatus.RUNNING

    def _fixture(self, t: Tournament, tie: CupTie | None, stage: Stage, leg: int, week: int,
                 home_id: int, away_id: int, neutral: bool = False) -> Fixture:
        # Iliskiler nesne olarak baglanir: ayni oturumda tie.fixtures / t.fixtures guncel kalir
        return Fixture(
            season=t.season, competition=Competition.CUP, tournament=t,
            tie=tie, stage=stage.value, leg=leg, week=week,
            home_team_id=home_id, away_team_id=away_id, neutral_venue=neutral,
            status=FixtureStatus.UNPLAYED,
        )

    def _create_tie(self, t: Tournament, stage: Stage, slot: int, first_id: int, second_id: int) -> CupTie:
        tie = CupTie(stage=stage.value, slot=slot, first_team_id=first_id, second_team_id=second_id)
        t.ties.append(tie)
        self.db.flush()
        if stage is Stage.FINAL:
            md = self._matchday_of(t, stage, 1)
            self.db.add(self._fixture(t, tie, stage, 1, md.week, first_id, second_id, neutral=True))
        else:
            leg1, leg2 = self._matchday_of(t, stage, 1), self._matchday_of(t, stage, 2)
            self.db.add(self._fixture(t, tie, stage, 1, leg1.week, first_id, second_id))
            self.db.add(self._fixture(t, tie, stage, 2, leg2.week, second_id, first_id))
        self.db.flush()
        return tie

    # ================================================================== mac gunu

    def matchday_due(self, t: Tournament | None, week: int) -> bool:
        """Bu hafta (bitmemis) turnuvanin bir mac gunu var mi? Kura bekliyor olabilir."""
        return (t is not None and t.status is not TournamentStatus.FINISHED
                and self.matchday_for_week(t, week) is not None)

    def matchday_pending(self, t: Tournament | None, week: int) -> bool:
        """Bu haftanin mac gununde oynanacak kupa maci kaldi mi? (kura bekliyorsa maclar da bekler)"""
        if not self.matchday_due(t, week):
            return False
        if t.status is TournamentStatus.DRAW:
            return True
        return any(not fx.is_played for fx in self.fixtures(t, week=week))

    def prepare_cup_engine(self, fx: Fixture, week: int, config: EngineConfig | None = None) -> MatchEngine:
        """
        Kupa fiksturunun motorunu kurar ama OYNATMAZ (9. Asama, canli mac). Otomatik mac gunu
        (play_matchday) da bu kurulumu kullanir: tohum, eleme kurali (rovansta tasinan goller,
        finalde uzatma/penalti), tarafsiz saha ve yalnizca kupada gecerli cezalar birebir aynidir.
        config None ise kariyerin motor ayari kullanilir.
        """
        def cup_reason(player, w=week):
            return player.unavailability_reason(w, Competition.CUP)

        _, engine = prepare_fixture(
            self.db, fx.id, seed=self.cm.match_seed(fx),
            config=config if config is not None else self.cm.engine_config,
            current_week=week, knockout=self._knockout_rule(fx), neutral_venue=fx.neutral_venue,
            unavailability=cup_reason,
        )
        return engine

    def knockout_rule(self, fx: Fixture) -> KnockoutRule | None:
        """Fiksturun eleme kurali (ilk mac / grup maci: None). Canli sonuc dogrulamasi icin."""
        return self._knockout_rule(fx)

    def play_matchday(
        self,
        week: int,
        report: WeekReport,
        league_team_ids: set[int],
        live_results: dict[int, MatchResult] | None = None,
    ) -> bool:
        """
        Bu haftanin kupa maclarini oynatir. league_team_ids: ayni hafta lig maci da olan
        takimlar (onlar icin kupa hafta ici sayilir). Mac oynandiysa True.
        live_results: fikstur id -> menajerin canli oynadigi macin BITMIS sonucu. Bu fiksturler
        simule edilmez, sonuc aynen islenir; kullanilan girdiler sozlukten cikarilir.
        Dogrulama cagiranin isidir (CareerManager canli sonuclari once dogrular).
        """
        t = self.current()
        if t is None or t.status is TournamentStatus.FINISHED:
            return False
        md = self.matchday_for_week(t, week)
        if md is None:
            return False
        if t.status is TournamentStatus.DRAW:
            self.draw_all()
            report.cup_notes.append("Kura çekilmemişti; asistan kurayı otomatik tamamladı.")

        fixtures = [fx for fx in self.fixtures(t, week=week) if not fx.is_played]
        if not fixtures:
            return False
        report.cup_label = f"{t.name} · {matchday_label(md)}"

        team_ids = {fx.home_team_id for fx in fixtures} | {fx.away_team_id for fx in fixtures}
        # Akademidekiler (10. Asama) kupa cezasini A takima donunce ceker
        banned_before = set(self.db.scalars(
            select(Player.id).where(Player.team_id.in_(team_ids), Player.cup_suspended_matches > 0,
                                    Player.in_academy.is_(False))
        ))
        user_id = self.cm.state.user_team_id

        for fx in fixtures:
            result = live_results.pop(fx.id, None) if live_results else None
            if result is None:
                result = self.prepare_cup_engine(fx, week).simulate()
            apply_result(fx, result, update_table=False)
            self._store_details(fx, result)
            self.cm._post_match(fx, result, week, report, competition=Competition.CUP,
                                midweek_team_ids=league_team_ids)
            if fx.stage == Stage.GROUP.value:
                self._update_group(t, fx)
            else:
                self._update_tie(t, fx, report)
            report.cup_results.append((fx, result))
            if user_id is not None and fx.involves(user_id):
                report.user_cup_result = result
                self.cm._apply_match_reputation(result, report)

        for pid in banned_before:
            p = self.db.get(Player, pid)
            if p is not None and p.cup_suspended_matches > 0:
                p.cup_suspended_matches -= 1

        self._advance(t, md, report)
        self.db.flush()
        return True

    def _knockout_rule(self, fx: Fixture) -> KnockoutRule | None:
        if fx.stage in (None, Stage.GROUP.value):
            return None
        if fx.stage == Stage.FINAL.value:
            return KnockoutRule()
        if fx.leg != 2:
            return None
        first_leg = next(f for f in fx.tie.fixtures if f.leg == 1)
        goals = {first_leg.home_team_id: first_leg.home_score, first_leg.away_team_id: first_leg.away_score}
        return KnockoutRule(home_carry=goals[fx.home_team_id], away_carry=goals[fx.away_team_id])

    def _store_details(self, fx: Fixture, result: MatchResult) -> None:
        fx.extra_time = bool(getattr(result, "extra_time", False))
        if getattr(result, "shootout", None) is not None:
            fx.home_penalties = result.home_penalties
            fx.away_penalties = result.away_penalties
        fx.key_events = [_event_dict(ev) for ev in result.events
                         if (ev.type.value if hasattr(ev.type, "value") else ev.type) in KEY_EVENT_TYPES]

    def _update_group(self, t: Tournament, fx: Fixture) -> None:
        by_team = self.entries(t)
        update_standings(by_team[fx.home_team_id], fx.home_score, fx.away_score)
        update_standings(by_team[fx.away_team_id], fx.away_score, fx.home_score)

    def _update_tie(self, t: Tournament, fx: Fixture, report: WeekReport) -> None:
        tie = fx.tie
        legs = [f for f in tie.fixtures if f.is_played]
        tie.aggregate_first = sum(f.home_score if f.home_team_id == tie.first_team_id else f.away_score for f in legs)
        tie.aggregate_second = sum(f.home_score if f.home_team_id == tie.second_team_id else f.away_score for f in legs)
        final_leg = fx.stage == Stage.FINAL.value or fx.leg == 2
        if not final_leg:
            return

        if fx.home_penalties is not None:
            pens = {fx.home_team_id: fx.home_penalties, fx.away_team_id: fx.away_penalties}
            tie.penalties_first, tie.penalties_second = pens[tie.first_team_id], pens[tie.second_team_id]
            if tie.penalties_first == tie.penalties_second:
                raise TournamentError(f"Penaltı serisi berabere bitti: {tie!r}")
            winner_id = tie.first_team_id if tie.penalties_first > tie.penalties_second else tie.second_team_id
            tie.decided_by = "penalties"
        elif tie.aggregate_first != tie.aggregate_second:
            winner_id = tie.first_team_id if tie.aggregate_first > tie.aggregate_second else tie.second_team_id
            tie.decided_by = "extra_time" if fx.extra_time else "normal"
        else:
            raise TournamentError(f"Eşleşme berabere kaldı ama uzatma/penaltı oynanmadı: {tie!r}")

        tie.winner_team_id = winner_id
        loser_id = tie.second_team_id if winner_id == tie.first_team_id else tie.first_team_id
        by_team = self.entries(t)
        by_team[loser_id].eliminated_stage = tie.stage
        winner, loser = self.db.get(Team, winner_id), self.db.get(Team, loser_id)

        score = f"toplam {tie.aggregate_first}-{tie.aggregate_second}" if fx.stage != Stage.FINAL.value \
            else f"{fx.home_score}-{fx.away_score}"
        how = DECIDED_LABELS[tie.decided_by]
        if tie.decided_by == "penalties":
            how = f"penaltılarla ({max(tie.penalties_first, tie.penalties_second)}-" \
                  f"{min(tie.penalties_first, tie.penalties_second)})"
        verb = "kupayı kaldırdı" if fx.stage == Stage.FINAL.value \
            else f"{STAGE_LABELS[Stage(tie.stage)]} turunu geçti"
        report.cup_notes.append(f"{winner.name} {loser.name} karşısında {verb} ({score}{', ' + how if how else ''}).")

        user_id = self.cm.state.user_team_id
        if user_id in (winner_id, loser_id):
            self.cm._apply_reputation_delta(reputation.cup_round_delta(tie.stage, winner_id == user_id), report)

    def _advance(self, t: Tournament, md: Matchday, report: WeekReport) -> None:
        stage = md.stage
        stages = self.stages(t)
        if stage is Stage.GROUP:
            group_fixtures = self.fixtures(t, stage=Stage.GROUP)
            if not all(fx.is_played for fx in group_fixtures):
                return
            rankings = self.group_rankings(t)
            by_team = self.entries(t)
            for group_index, rows in enumerate(rankings):
                for row in rows[2:]:
                    by_team[row.team_id].eliminated_stage = Stage.GROUP.value
                qualified = ", ".join(self.db.get(Team, r.team_id).name for r in rows[:2])
                report.cup_notes.append(f"Grup {GROUP_LABELS[group_index]}: {qualified} çeyrek finale yükseldi.")
            user_id = self.cm.state.user_team_id
            if user_id in by_team:
                advanced = by_team[user_id].eliminated_stage is None
                self.cm._apply_reputation_delta(reputation.cup_round_delta("GROUP", advanced), report)
            pairs = group_qualifier_pairs([[r.team_id for r in rows] for rows in rankings])
            next_stage = stages[stages.index(stage) + 1]
            for slot, (first_id, second_id) in enumerate(pairs):
                self._create_tie(t, next_stage, slot, first_id, second_id)
            return

        ties = self.ties(t, stage)
        if not ties or not all(tie.decided for tie in ties):
            return
        if stage is Stage.FINAL:
            t.champion_team_id = ties[0].winner_team_id
            t.status = TournamentStatus.FINISHED
            report.cup_champion = self.db.get(Team, t.champion_team_id)
            report.cup_notes.append(f"🏆 {report.cup_champion.name} {t.name} şampiyonu!")
            return
        if stage is YELLOW_RESET_AFTER:
            # Kulubun TUM oyunculari (akademiye gonderilmis olanlar dahil; Team.players yalnizca A takim)
            for p in self.db.scalars(select(Player).where(Player.team_id.in_(list(self.entries(t))))):
                p.cup_yellow_cards = 0
            report.cup_notes.append("Çeyrek final sonrası kupa sarı kartları silindi.")
        winners = [tie.winner_team_id for tie in sorted(ties, key=lambda tie: tie.slot)]
        next_stage = stages[stages.index(stage) + 1]
        for slot, (first_id, second_id) in enumerate(next_round_pairs(winners)):
            self._create_tie(t, next_stage, slot, first_id, second_id)

    # ================================================================== grup ve istatistik

    def group_rankings(self, t: Tournament):
        """Gruplar A-D icin cup_draw.rank_group siralamalari (sadece grup formatinda)."""
        by_team = self.entries(t)
        coefficients = {team_id: e.coefficient for team_id, e in by_team.items()}
        names = {team_id: e.team.name for team_id, e in by_team.items()}
        played = [fx for fx in self.fixtures(t, stage=Stage.GROUP) if fx.is_played]
        rankings = []
        for group_index in range(4):
            ids = [e.team_id for e in sorted(t.entries, key=lambda e: e.pot) if e.group_index == group_index]
            results = [(fx.home_team_id, fx.away_team_id, fx.home_score, fx.away_score)
                       for fx in played if fx.home_team_id in ids]
            rankings.append(rank_group(ids, results, coefficients, names))
        return rankings

    def top_players(self, t: Tournament, by: str = "goals", limit: int = 10) -> list[CupPlayerRow]:
        self.db.flush()
        goals = func.sum(PlayerMatchStat.goals)
        assists = func.sum(PlayerMatchStat.assists)
        primary, secondary = (goals, assists) if by == "goals" else (assists, goals)
        stmt = (
            select(Player, Team, goals, assists, func.count(PlayerMatchStat.id), func.avg(PlayerMatchStat.rating))
            .join(PlayerMatchStat, PlayerMatchStat.player_id == Player.id)
            .join(Team, Team.id == PlayerMatchStat.team_id)
            .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
            .where(Fixture.tournament_id == t.id)
            .group_by(Player.id, Team.id)
            .having(primary > 0)
            .order_by(desc(primary), desc(secondary), Player.name)
            .limit(limit)
        )
        return [
            CupPlayerRow(p, team, int(g), int(a), int(n), round(float(r), 2))
            for p, team, g, a, n, r in self.db.execute(stmt)
        ]

    def unavailable(self, t: Tournament, week: int | None = None, active_only: bool = True) -> list[UnavailableRow]:
        """Kupada kullanilamayan / ceza sinirindaki oyuncular (turnuvada kalan takimlar)."""
        week = self.cm.current_week if week is None else week
        entries = [e for e in t.entries if not (active_only and e.eliminated_stage)]
        rows: list[UnavailableRow] = []
        for entry in sorted(entries, key=lambda e: e.seed_rank):
            for p in entry.team.players:
                if p.is_injured(week):
                    rows.append(UnavailableRow(p, entry.team, "injury",
                                               f"sakat, {p.injured_until_week}. haftada dönüyor"))
                if p.cup_suspended_matches > 0:
                    rows.append(UnavailableRow(p, entry.team, "ban",
                                               f"kupada cezalı ({p.cup_suspended_matches} maç)"))
                elif p.cup_yellow_cards % CUP_YELLOW_BAN_EVERY == CUP_YELLOW_BAN_EVERY - 1:
                    rows.append(UnavailableRow(p, entry.team, "risk",
                                               f"{p.cup_yellow_cards} sarı: bir sarı daha ceza getirir"))
        return rows
