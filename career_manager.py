"""
career_manager.py
=================
Sezon dongusu kontrolcusu (3. Asama).

Sorumluluklar:
    * Mevcut haftadaki TUM liglerin maclarini match_engine ile oynatir
    * Mac sonrasi kaliciligi yazar: oyuncu mac istatistikleri, not gecmisi,
      form/moral guncellemesi, sakatlik suresi, kart cezasi, sari birikimi
    * Ceza sayaclarini hafta sonunda azaltir, haftayi ilerletir
    * Puan durumu / gol kralligi / sonraki mac / takim formu sorgulari
    * Sezon bitince yeni sezon kurar (fikstur, yas, istatistik sifirlama)

Katman: LOGIC. Terminale hicbir sey basmaz; main.py (View) sonuclari formatlar.
COMMIT ETMEZ -- cagiran taraf session_scope() ile islem sinirini belirler.
Testler ayni nedenle rollback ile temiz kalir.

Sakatlik semantigi:
    injured_until_week = oyuncunun tekrar oynayabilecegi hafta.
    W. haftada sakatlanip 'n' hafta yatan oyuncu W+1..W+n haftalarini kacirir,
    W+n+1'de doner -> injured_until_week = W + n + 1.

Ceza semantigi:
    suspended_matches > 0 iken kadroya alinmaz. Hafta sonunda, o hafta cezali
    olarak OTURAN oyuncularin sayaci 1 azalir (ayni hafta kirmizi gorenlerinki degil).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy import desc, func, select

from match_engine import EngineConfig, MatchResult, play_fixture
from models import (
    RATING_HISTORY_SIZE,
    Fixture,
    FixtureStatus,
    GameState,
    League,
    Player,
    PlayerMatchStat,
    Team,
)
from schedule import build_round_robin

# ===========================================================================
# 1) SAF KURALLAR (DB bilmez, birim testi kolay)
# ===========================================================================

NEUTRAL_RATING = 6.5            # bu notun ustu iyi, alti kotu performans
MAX_FORM_SWING = 12             # tek macta form en fazla bu kadar degisir
MAX_MORALE_SWING = 12
BENCH_FORM_DRIFT = 2            # oynamayan oyuncunun formu 50'ye dogru kayar
RESULT_MORALE = {"W": 5, "D": 1, "L": -5}
YELLOW_BAN_EVERY = 4            # her 4 sari kart = 1 mac ceza
STRAIGHT_RED_LONG_BAN_CHANCE = 0.4   # direkt kirmizida %40 ihtimalle 3 mac (siddet), aksi 1 mac
# (hafta, agirlik): cogu sakatlik kisa, nadiren sezonu bitiren
INJURY_TABLE: list[tuple[int, int]] = [(1, 45), (2, 25), (3, 15), (4, 5), (6, 5), (10, 5)]


def clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(value))))


def form_delta(rating: float) -> int:
    """Mac notuna gore form degisimi. 8.5 -> +8, 6.5 -> 0, 5.0 -> -6."""
    return clamp((rating - NEUTRAL_RATING) * 4, -MAX_FORM_SWING, MAX_FORM_SWING)


def morale_delta(rating: float | None, outcome: str) -> int:
    """
    Moral degisimi: kisisel performans + takim sonucu.
    rating None ise oyuncu oynamamistir; sadece sonuc etkisinin yarisini alir.
    """
    result = RESULT_MORALE[outcome]
    if rating is None:
        return round(result / 2)
    return clamp((rating - NEUTRAL_RATING) * 2 + result, -MAX_MORALE_SWING, MAX_MORALE_SWING)


def bench_form_drift(form: int) -> int:
    """Oynamayan oyuncunun formu notre (50) dogru kayar."""
    if form > 50:
        return -min(BENCH_FORM_DRIFT, form - 50)
    if form < 50:
        return min(BENCH_FORM_DRIFT, 50 - form)
    return 0


def injury_weeks(rng: random.Random) -> int:
    """Sakatlik suresi (hafta), INJURY_TABLE agirliklariyla."""
    weeks, weights = zip(*INJURY_TABLE, strict=True)
    return rng.choices(weeks, weights=weights, k=1)[0]


def suspension_length(rng: random.Random, second_yellow: bool) -> int:
    """Ikinci sari: 1 mac. Direkt kirmizi: 1 mac (son adam) veya 3 mac (siddet)."""
    if second_yellow:
        return 1
    return 3 if rng.random() < STRAIGHT_RED_LONG_BAN_CHANCE else 1


def outcome_for(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "W"
    if goals_for == goals_against:
        return "D"
    return "L"


def standings_key(team: Team) -> tuple:
    """Puan, averaj, atilan gol, isim. Siralama icin (buyukten kucuge)."""
    return (-team.points, -team.goal_difference, -team.goals_for, team.name)


# ===========================================================================
# 2) RAPOR NESNELERI (View bunlari formatlar)
# ===========================================================================

@dataclass
class PlayerNote:
    player_id: int
    player_name: str
    team_name: str
    detail: str


@dataclass
class WeekReport:
    season: int
    week: int
    results: list[tuple[Fixture, MatchResult]] = field(default_factory=list)
    injuries: list[PlayerNote] = field(default_factory=list)
    suspensions: list[PlayerNote] = field(default_factory=list)
    user_result: MatchResult | None = None
    season_finished: bool = False

    @property
    def played_any(self) -> bool:
        return bool(self.results)


@dataclass
class ScorerRow:
    player: Player
    team: Team
    goals: int
    assists: int
    appearances: int
    avg_rating: float


# ===========================================================================
# 3) KONTROLCU
# ===========================================================================

class SeasonNotFinished(Exception):
    pass


class CareerManager:
    def __init__(
        self,
        db,
        seed: int | None = None,
        engine_config: EngineConfig | None = None,
    ) -> None:
        self.db = db
        self.seed = seed
        self.rng = random.Random(seed)
        self.engine_config = engine_config

    # ------------------------------------------------------------------ durum

    @property
    def state(self) -> GameState:
        st = self.db.get(GameState, 1)
        if st is None:
            st = GameState(id=1, season=1, current_week=1)
            self.db.add(st)
            self.db.flush()
        return st

    @property
    def season(self) -> int:
        return self.state.season

    @property
    def current_week(self) -> int:
        return self.state.current_week

    @property
    def user_team(self) -> Team | None:
        return self.state.user_team

    def set_user_team(self, team: Team) -> None:
        self.state.user_team_id = team.id
        self.state.user_team = team
        self.db.flush()

    def find_team(self, name: str) -> Team | None:
        return self.db.scalar(select(Team).where(func.lower(Team.name) == name.strip().lower()))

    # ------------------------------------------------------------------ sorgular

    def leagues(self) -> list[League]:
        return list(self.db.scalars(select(League).order_by(League.id)))

    def teams(self) -> list[Team]:
        return list(self.db.scalars(select(Team).order_by(Team.league_id, Team.name)))

    def fixtures_for_week(self, week: int | None = None, league_id: int | None = None) -> list[Fixture]:
        week = self.current_week if week is None else week
        stmt = (
            select(Fixture)
            .where(Fixture.season == self.season, Fixture.week == week)
            .order_by(Fixture.league_id, Fixture.id)
        )
        if league_id is not None:
            stmt = stmt.where(Fixture.league_id == league_id)
        return list(self.db.scalars(stmt))

    def total_weeks(self) -> int:
        return self.db.scalar(
            select(func.max(Fixture.week)).where(Fixture.season == self.season)
        ) or 0

    def next_fixture(self, team_id: int) -> Fixture | None:
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.UNPLAYED,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(Fixture.week)
            .limit(1)
        )
        return self.db.scalar(stmt)

    def standings(self, league_id: int) -> list[Team]:
        """Bellekteki (henuz flush edilmemis olabilecek) degerlerle siralar."""
        teams = list(self.db.scalars(select(Team).where(Team.league_id == league_id)))
        return sorted(teams, key=standings_key)

    def position_of(self, team: Team) -> int:
        table = self.standings(team.league_id)
        return next(i for i, t in enumerate(table, start=1) if t.id == team.id)

    def last_played_week(self) -> int | None:
        return self.db.scalar(
            select(func.max(Fixture.week)).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.PLAYED
            )
        )

    def results_for_week(self, week: int) -> list[Fixture]:
        return [f for f in self.fixtures_for_week(week) if f.is_played]

    def team_form(self, team_id: int, n: int = 5) -> str:
        """Son n macin sonucu, kronolojik: 'G' galibiyet, 'B' beraberlik, 'M' maglubiyet."""
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.PLAYED,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(desc(Fixture.week))
            .limit(n)
        )
        letters = []
        for fx in self.db.scalars(stmt):
            gf, ga = (fx.home_score, fx.away_score) if fx.home_team_id == team_id else (fx.away_score, fx.home_score)
            letters.append({"W": "G", "D": "B", "L": "M"}[outcome_for(gf, ga)])
        return "".join(reversed(letters))

    def top_scorers(self, league_id: int | None = None, limit: int = 10) -> list[ScorerRow]:
        self.db.flush()
        goals = func.sum(PlayerMatchStat.goals)
        assists = func.sum(PlayerMatchStat.assists)
        stmt = (
            select(Player, Team, goals, assists, func.count(PlayerMatchStat.id), func.avg(PlayerMatchStat.rating))
            .join(PlayerMatchStat, PlayerMatchStat.player_id == Player.id)
            .join(Team, Team.id == PlayerMatchStat.team_id)
            .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
            .where(Fixture.season == self.season)
            .group_by(Player.id, Team.id)
            .having(goals > 0)
            .order_by(desc(goals), desc(assists), Player.name)
            .limit(limit)
        )
        if league_id is not None:
            stmt = stmt.where(Team.league_id == league_id)
        return [
            ScorerRow(player=p, team=t, goals=int(g), assists=int(a), appearances=int(n), avg_rating=round(float(r), 2))
            for p, t, g, a, n, r in self.db.execute(stmt)
        ]

    @property
    def season_finished(self) -> bool:
        remaining = self.db.scalar(
            select(func.count()).select_from(Fixture).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.UNPLAYED
            )
        )
        return remaining == 0

    def champion(self, league_id: int) -> Team | None:
        if not self.season_finished:
            return None
        table = self.standings(league_id)
        return table[0] if table else None

    # ------------------------------------------------------------------ ana dongu

    def play_week(self) -> WeekReport:
        """Mevcut haftanin tum liglerdeki maclarini oynatir ve haftayi ilerletir."""
        week = self.current_week
        report = WeekReport(season=self.season, week=week)

        fixtures = [f for f in self.fixtures_for_week(week) if not f.is_played]
        if not fixtures:
            report.season_finished = self.season_finished
            return report

        # Bu hafta cezali olarak oturanlar: mac sonrasi sayaclari 1 azalacak
        suspended_before = set(
            self.db.scalars(select(Player.id).where(Player.suspended_matches > 0))
        )
        user_team_id = self.state.user_team_id

        for fx in fixtures:
            match_seed = None if self.seed is None else self.seed * 10_000 + fx.id
            result = play_fixture(
                self.db, fx.id, seed=match_seed, persist=True,
                config=self.engine_config, current_week=week,
            )
            self._post_match(fx, result, week, report)
            if user_team_id is not None and fx.involves(user_team_id):
                report.user_result = result
            report.results.append((fx, result))

        self._decrement_suspensions(suspended_before)
        self.state.current_week = week + 1
        self.db.flush()
        report.season_finished = self.season_finished
        return report

    def _post_match(self, fx: Fixture, result: MatchResult, week: int, report: WeekReport) -> None:
        outcomes = {
            result.home.id: outcome_for(result.home_score, result.away_score),
            result.away.id: outcome_for(result.away_score, result.home_score),
        }
        for team in (result.home, result.away):
            outcome = outcomes[team.id]

            for mp in team.players:
                p = self.db.get(Player, mp.id)
                if p is None:
                    continue

                if mp.played:
                    self.db.add(PlayerMatchStat(
                        fixture_id=fx.id, player_id=p.id, team_id=team.id,
                        minutes=mp.minutes_played, goals=mp.goals, assists=mp.assists,
                        shots=mp.shots, shots_on_target=mp.shots_on_target, saves=mp.saves,
                        yellow_cards=mp.yellow_cards, red_card=mp.sent_off, injured=mp.injured,
                        rating=mp.rating,
                    ))
                    history = list(p.match_rating_history or [])
                    p.match_rating_history = (history + [mp.rating])[-RATING_HISTORY_SIZE:]
                    p.form = clamp(p.form + form_delta(mp.rating))
                    p.morale = clamp(p.morale + morale_delta(mp.rating, outcome))
                else:
                    p.form = clamp(p.form + bench_form_drift(p.form))
                    p.morale = clamp(p.morale + morale_delta(None, outcome))

                if mp.injured:
                    weeks = injury_weeks(self.rng)
                    p.injured_until_week = week + weeks + 1
                    report.injuries.append(PlayerNote(
                        p.id, p.name, team.name,
                        f"{weeks} hafta, {p.injured_until_week}. haftada dönüyor",
                    ))

                if mp.sent_off:
                    matches = suspension_length(self.rng, mp.second_yellow)
                    p.suspended_matches += matches
                    reason = "ikinci sarı" if mp.second_yellow else "direkt kırmızı"
                    report.suspensions.append(PlayerNote(
                        p.id, p.name, team.name, f"{matches} maç ({reason})",
                    ))
                elif mp.yellow_cards:
                    before = p.season_yellow_cards
                    p.season_yellow_cards = before + mp.yellow_cards
                    bans = p.season_yellow_cards // YELLOW_BAN_EVERY - before // YELLOW_BAN_EVERY
                    if bans:
                        p.suspended_matches += bans
                        report.suspensions.append(PlayerNote(
                            p.id, p.name, team.name,
                            f"{bans} maç ({p.season_yellow_cards}. sarı kart)",
                        ))

            # Sakat/cezali oyuncular da takimin sonucundan etkilenir (yarim etki)
            for mp, _reason in team.unavailable:
                p = self.db.get(Player, mp.id)
                if p is not None:
                    p.morale = clamp(p.morale + morale_delta(None, outcome))

    def _decrement_suspensions(self, player_ids: set[int]) -> None:
        for pid in player_ids:
            p = self.db.get(Player, pid)
            if p is not None and p.suspended_matches > 0:
                p.suspended_matches -= 1

    # ------------------------------------------------------------------ yeni sezon

    def start_new_season(self) -> int:
        """
        Sezon bittiyse: takim istatistikleri sifirlanir, fikstur yeniden uretilir,
        oyuncular bir yas alir, sakatlik/ceza/sari/not gecmisi temizlenir.
        Form ve moral tasinir (yeni sezona 'ruh hali' ile girilir).
        """
        if not self.season_finished:
            raise SeasonNotFinished("Sezon henüz bitmedi; oynanmamış maçlar var.")

        st = self.state
        new_season = st.season + 1

        for team in self.teams():
            team.reset_season_stats()

        for league in self.leagues():
            team_ids = [t.id for t in league.teams]
            self.rng.shuffle(team_ids)
            for week_index, pairs in enumerate(build_round_robin(team_ids), start=1):
                for home_id, away_id in pairs:
                    self.db.add(Fixture(
                        season=new_season, league_id=league.id,
                        home_team_id=home_id, away_team_id=away_id,
                        week=week_index, status=FixtureStatus.UNPLAYED,
                    ))

        for p in self.db.scalars(select(Player)):
            p.age = min(45, p.age + 1)
            p.injured_until_week = 0
            p.suspended_matches = 0
            p.season_yellow_cards = 0
            p.match_rating_history = []

        st.season = new_season
        st.current_week = 1
        self.db.flush()
        return new_season
