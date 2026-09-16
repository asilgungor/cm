"""
models.py
=========
Veritabani semasi (SQLAlchemy ORM modelleri).

Tasarim notu:
    Bu siniflar sadece VERIYI temsil eder. Mac simulasyonu, puan hesabi,
    transfer mantigi gibi kurallar buraya degil, engine/controller katmanina
    (match_engine.py, career_manager.py) gider. Buradaki tek istisna, saf
    okuma amacli kucuk yardimcilar (goal_difference, is_available gibi) --
    bunlar veritabanina yazilmaz, aninda hesaplanir.

Tablolar:
    leagues, teams, players, fixtures           (1. Asama)
    game_state, player_match_stats              (3. Asama: sezon dongusu ve kalicilik)
    staff                                       (5. Asama: teknik heyet)

Finans (5. Asama):
    teams.transfer_budget  -> bonservis kasasi (EUR)
    teams.wage_budget      -> HAFTALIK toplam maas havuzu (EUR/hafta)
    players.market_value / current_wage / contract_years / squad_role
    staff.wage             -> personel de ayni haftalik havuzdan oder
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# Oyuncunun form hesabinda kullanilan son mac notu sayisi
RATING_HISTORY_SIZE = 5


# ---------------------------------------------------------------------------
# Sabit tipler
# ---------------------------------------------------------------------------

class Position(str, enum.Enum):
    """Basitlestirilmis mevki seti."""
    GK = "GK"    # Kaleci
    DEF = "DEF"  # Defans
    MID = "MID"  # Orta saha
    FWD = "FWD"  # Forvet


class FixtureStatus(str, enum.Enum):
    """Bir fikstur maci oynandi mi?"""
    UNPLAYED = "unplayed"
    PLAYED = "played"


class LineupStatus(str, enum.Enum):
    """Menajerin kadro karari: ilk 11, yedek kulubesi, kadro disi."""
    XI = "XI"
    BENCH = "BENCH"
    OUT = "OUT"


class SquadRole(str, enum.Enum):
    """Oyuncunun sozlesmesinde soz verilen kadro rolu."""
    STAR = "STAR"                # Yildiz
    FIRST_TEAM = "FIRST_TEAM"    # As
    BACKUP = "BACKUP"            # Yedek


class StaffRole(str, enum.Enum):
    """Teknik heyet rolleri."""
    COACH = "COACH"
    SCOUT = "SCOUT"
    PHYSIO = "PHYSIO"
    ASSISTANT = "ASSISTANT"


def _enum_values(enum_cls) -> list:
    """
    ENUM degerlerini veritabanina "GK", "DEF"... olarak yazdirir.
    (SQLAlchemy'nin varsayilan davranisi Python tarafindaki ISIMLERI yazmaktir,
    bu da UNPLAYED vs unplayed karisikligina yol acar.)
    """
    return [member.value for member in enum_cls]


# Ayni PostgreSQL ENUM tipi iki sutunda kullanilir (players.position, players.lineup_role);
# tek nesne paylasilir ki CREATE/DROP TYPE bir kez calissin.
POSITION_ENUM = SQLEnum(Position, name="position_enum", values_callable=_enum_values)


# ---------------------------------------------------------------------------
# League
# ---------------------------------------------------------------------------

class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    country: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    teams: Mapped[list[Team]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
        order_by="Team.name",
    )
    fixtures: Mapped[list[Fixture]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<League {self.name} ({self.country})>"


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("league_id", "name", name="uq_team_name_per_league"),
        CheckConstraint("reputation BETWEEN 1 AND 100", name="ck_team_reputation"),
        CheckConstraint("transfer_budget >= 0", name="ck_team_transfer_budget"),
        CheckConstraint("wage_budget >= 0", name="ck_team_wage_budget"),
        CheckConstraint("played = won + drawn + lost", name="ck_team_played_consistent"),
        CheckConstraint("formation IN ('4-4-2', '4-3-3', '3-5-2')", name="ck_team_formation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # --- Iki kalemli finans (5. Asama) ---
    transfer_budget: Mapped[int] = mapped_column(                       # bonservis kasasi, EUR
        BigInteger, nullable=False, default=0, server_default="0"
    )
    wage_budget: Mapped[int] = mapped_column(                           # HAFTALIK maas havuzu, EUR
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reputation: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)   # 1-100
    # Menajerin (veya AI'nin) dizilisi. Secenekler tactics.FORMATIONS ile ayni.
    formation: Mapped[str] = mapped_column(String(5), nullable=False, default="4-4-2", server_default="4-4-2")

    # --- Lig tablosu istatistikleri (sezon basinda sifirlanir) ---
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    played: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drawn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    league: Mapped[League] = relationship(back_populates="teams")
    players: Mapped[list[Player]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
        order_by="Player.overall_rating.desc()",
    )

    # DIKKAT: delete-orphan YOK. Personel kulupsuz de var olabilir (bostaki havuz);
    # delete-orphan olsaydi release_staff() ile team=None yapmak satiri SILERDI.
    # Takim silindiginde personeli DB'deki ON DELETE CASCADE temizler.
    staff: Mapped[list[Staff]] = relationship(
        back_populates="team",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="Staff.role",
    )

    home_fixtures: Mapped[list[Fixture]] = relationship(
        back_populates="home_team",
        foreign_keys="Fixture.home_team_id",
        cascade="all, delete-orphan",
    )
    away_fixtures: Mapped[list[Fixture]] = relationship(
        back_populates="away_team",
        foreign_keys="Fixture.away_team_id",
        cascade="all, delete-orphan",
    )

    # --- Sadece okuma amacli yardimcilar (veritabaninda sutun degil) ---
    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    @property
    def squad_rating(self) -> float:
        """Kadronun ortalama gucu. Bos kadroda 0 doner."""
        if not self.players:
            return 0.0
        return round(sum(p.overall_rating for p in self.players) / len(self.players), 1)

    @property
    def player_wage_bill(self) -> int:
        """Oyuncularin haftalik toplam maasi."""
        return sum(p.current_wage for p in self.players)

    @property
    def staff_wage_bill(self) -> int:
        """Teknik heyetin haftalik toplam maasi."""
        return sum(s.wage for s in self.staff)

    @property
    def wage_bill(self) -> int:
        """Haftalik toplam maas yuku (oyuncu + personel)."""
        return self.player_wage_bill + self.staff_wage_bill

    @property
    def free_wage(self) -> int:
        """Maas havuzunda kalan haftalik alan. Negatifse butce asimi var."""
        return self.wage_budget - self.wage_bill

    def staff_by_role(self, role: StaffRole) -> list[Staff]:
        return [s for s in self.staff if s.role is role]

    def best_staff(self, role: StaffRole, attribute: str) -> Staff | None:
        """Bir roldeki en iyi personel (ilgili ozelligine gore)."""
        pool = self.staff_by_role(role)
        return max(pool, key=lambda s: getattr(s, attribute, 0)) if pool else None

    def reset_season_stats(self) -> None:
        self.points = self.played = self.won = self.drawn = self.lost = 0
        self.goals_for = self.goals_against = 0

    def __repr__(self) -> str:
        return f"<Team {self.name} (rep={self.reputation})>"


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        CheckConstraint("age BETWEEN 15 AND 45", name="ck_player_age"),
        CheckConstraint("overall_rating BETWEEN 1 AND 99", name="ck_player_overall"),
        CheckConstraint("pace BETWEEN 1 AND 99", name="ck_player_pace"),
        CheckConstraint("shooting BETWEEN 1 AND 99", name="ck_player_shooting"),
        CheckConstraint("passing BETWEEN 1 AND 99", name="ck_player_passing"),
        CheckConstraint("defending BETWEEN 1 AND 99", name="ck_player_defending"),
        CheckConstraint("dribbling BETWEEN 1 AND 99", name="ck_player_dribbling"),
        CheckConstraint("goalkeeping BETWEEN 1 AND 99", name="ck_player_goalkeeping"),
        CheckConstraint("form BETWEEN 0 AND 100", name="ck_player_form"),
        CheckConstraint("morale BETWEEN 0 AND 100", name="ck_player_morale"),
        CheckConstraint("injured_until_week >= 0", name="ck_player_injured_week"),
        CheckConstraint("suspended_matches >= 0", name="ck_player_suspended"),
        CheckConstraint("season_yellow_cards >= 0", name="ck_player_season_yellows"),
        CheckConstraint("weeks_since_match >= 0", name="ck_player_weeks_since_match"),
        CheckConstraint("market_value >= 0", name="ck_player_market_value"),
        CheckConstraint("current_wage >= 0", name="ck_player_current_wage"),
        CheckConstraint("contract_years BETWEEN 0 AND 6", name="ck_player_contract_years"),
        Index("ix_player_team_position", "team_id", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Serbest (kulupsuz) oyuncular icin ileride NULL olabilsin diye nullable.
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    age: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    position: Mapped[Position] = mapped_column(POSITION_ENUM, nullable=False)

    # --- Yetenekler (1-99) ---
    overall_rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pace: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    shooting: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    passing: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    defending: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dribbling: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    goalkeeping: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # --- Degisken durum (0-100). career_manager hafta hafta gunceller. ---
    form: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    morale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=70)

    # --- Kalicilik: sakatlik / ceza / not gecmisi (3. Asama) ---
    # 0 = sakat degil. Aksi halde oyuncunun tekrar OYNAYABILECEGI hafta;
    # injured_until_week > mevcut_hafta oldugu surece kadroya alinamaz.
    injured_until_week: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    # Kac mac daha oynayamaz. Her hafta sonunda 1 azalir.
    suspended_matches: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    # Sezon ici sari kart birikimi (her 4 sari = 1 mac ceza)
    season_yellow_cards: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    # Son RATING_HISTORY_SIZE mac notu, en yeni sonda. Form hesabi ve UI icin.
    match_rating_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    # --- Menajer kararlari ve mac ritmi (4. Asama) ---
    # XI: ilk 11 (lineup_role = oynayacagi slot), BENCH: kulube, OUT: kadro disi.
    # Hic XI yoksa maci asistan (otomatik secim) kurar.
    lineup_status: Mapped[LineupStatus] = mapped_column(
        SQLEnum(LineupStatus, name="lineup_status_enum", values_callable=_enum_values),
        nullable=False, default=LineupStatus.BENCH, server_default="BENCH",
    )
    lineup_role: Mapped[Position | None] = mapped_column(POSITION_ENUM, nullable=True)
    # Kac haftadir mac oynamiyor (ritim kaybi: form kademeli olarak 50'ye kayar)
    weeks_since_match: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )

    # --- Sozlesme ve degerleme (5. Asama) ---
    market_value: Mapped[int] = mapped_column(          # piyasa degeri, EUR
        BigInteger, nullable=False, default=0, server_default="0"
    )
    current_wage: Mapped[int] = mapped_column(          # HAFTALIK maas, EUR
        BigInteger, nullable=False, default=0, server_default="0"
    )
    contract_years: Mapped[int] = mapped_column(        # kalan sozlesme yili
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    squad_role: Mapped[SquadRole] = mapped_column(
        SQLEnum(SquadRole, name="squad_role_enum", values_callable=_enum_values),
        nullable=False, default=SquadRole.FIRST_TEAM, server_default="FIRST_TEAM",
    )

    team: Mapped[Team | None] = relationship(back_populates="players")
    match_stats: Mapped[list[PlayerMatchStat]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )

    # --- Sadece okuma amacli yardimcilar ---
    def is_injured(self, week: int) -> bool:
        return self.injured_until_week > week

    @property
    def is_suspended(self) -> bool:
        return self.suspended_matches > 0

    def is_available(self, week: int) -> bool:
        return not (self.is_injured(week) or self.is_suspended)

    def unavailability_reason(self, week: int) -> str | None:
        if self.is_injured(week):
            return f"sakat, {self.injured_until_week}. haftada dönüyor"
        if self.is_suspended:
            return f"cezalı, {self.suspended_matches} maç"
        return None

    @property
    def is_starter(self) -> bool:
        return self.lineup_status is LineupStatus.XI

    @property
    def average_rating(self) -> float | None:
        history = self.match_rating_history or []
        return round(sum(history) / len(history), 2) if history else None

    @property
    def last_rating(self) -> float | None:
        history = self.match_rating_history or []
        return history[-1] if history else None

    def __repr__(self) -> str:
        return f"<Player {self.name} {self.position.value} {self.overall_rating}>"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint("season", "league_id", "week", "home_team_id", name="uq_fixture_slot"),
        CheckConstraint("home_team_id <> away_team_id", name="ck_fixture_distinct_teams"),
        CheckConstraint("week >= 1", name="ck_fixture_week"),
        CheckConstraint("season >= 1", name="ck_fixture_season"),
        CheckConstraint(
            "(home_score IS NULL OR home_score >= 0) AND "
            "(away_score IS NULL OR away_score >= 0)",
            name="ck_fixture_scores_non_negative",
        ),
        # Oynanmis mac skorsuz, oynanmamis mac skorlu olamaz.
        CheckConstraint(
            "(status = 'played' AND home_score IS NOT NULL AND away_score IS NOT NULL) OR "
            "(status = 'unplayed' AND home_score IS NULL AND away_score IS NULL)",
            name="ck_fixture_status_scores",
        ),
        Index("ix_fixture_season_league_week", "season", "league_id", "week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")

    # Fikstur her zaman "su ligin su haftasi" seklinde sorgulanir; join'siz erisim icin.
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False, index=True
    )

    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )

    week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[FixtureStatus] = mapped_column(
        SQLEnum(FixtureStatus, name="fixture_status_enum", values_callable=_enum_values),
        nullable=False,
        default=FixtureStatus.UNPLAYED,
    )

    home_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    away_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    league: Mapped[League] = relationship(back_populates="fixtures")
    home_team: Mapped[Team] = relationship(
        back_populates="home_fixtures", foreign_keys=[home_team_id]
    )
    away_team: Mapped[Team] = relationship(
        back_populates="away_fixtures", foreign_keys=[away_team_id]
    )
    player_stats: Mapped[list[PlayerMatchStat]] = relationship(
        back_populates="fixture", cascade="all, delete-orphan"
    )

    @property
    def is_played(self) -> bool:
        return self.status == FixtureStatus.PLAYED

    def involves(self, team_id: int) -> bool:
        return team_id in (self.home_team_id, self.away_team_id)

    def __repr__(self) -> str:
        if self.is_played:
            return (
                f"<Fixture S{self.season} W{self.week} {self.home_team_id} "
                f"{self.home_score}-{self.away_score} {self.away_team_id}>"
            )
        return f"<Fixture S{self.season} W{self.week} {self.home_team_id} vs {self.away_team_id}>"


# ---------------------------------------------------------------------------
# PlayerMatchStat — oyuncunun bir mactaki performansi
# ---------------------------------------------------------------------------

class PlayerMatchStat(Base):
    """
    Mac basina oyuncu istatistigi. Gol kralligi, asist, ortalama not ve
    ileride 2D arayuzde "mac raporu" ekrani buradan beslenir.
    """
    __tablename__ = "player_match_stats"
    __table_args__ = (
        UniqueConstraint("fixture_id", "player_id", name="uq_player_match"),
        CheckConstraint("rating BETWEEN 1 AND 10", name="ck_pms_rating"),
        CheckConstraint("minutes BETWEEN 0 AND 120", name="ck_pms_minutes"),
        Index("ix_pms_team_fixture", "team_id", "fixture_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )

    minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    goals: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    assists: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    shots: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    shots_on_target: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    saves: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    yellow_cards: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    red_card: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    injured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=6.0)

    fixture: Mapped[Fixture] = relationship(back_populates="player_stats")
    player: Mapped[Player] = relationship(back_populates="match_stats")
    team: Mapped[Team] = relationship()

    def __repr__(self) -> str:
        return f"<PMS fx={self.fixture_id} p={self.player_id} {self.goals}g {self.rating}>"


# ---------------------------------------------------------------------------
# Staff — teknik heyet
# ---------------------------------------------------------------------------

class Staff(Base):
    """
    Teknik heyet uyesi. team_id NULL ise personel BOSTADIR (issiz havuzu) ve
    herhangi bir kulup tarafindan ise alinabilir.

    Alt ozellikler 1-20 arasidir ve role gore anlamlidir; ilgisiz olanlar 1'de
    kalir (bkz. staff.ROLE_ATTRIBUTES). Etkileri staff.py icinde tanimlidir.
    """
    __tablename__ = "staff"
    __table_args__ = (
        CheckConstraint("wage >= 0", name="ck_staff_wage"),
        CheckConstraint("reputation BETWEEN 1 AND 100", name="ck_staff_reputation"),
        CheckConstraint("attacking BETWEEN 1 AND 20", name="ck_staff_attacking"),
        CheckConstraint("defending BETWEEN 1 AND 20", name="ck_staff_defending"),
        CheckConstraint("tactical BETWEEN 1 AND 20", name="ck_staff_tactical"),
        CheckConstraint("working_with_youngsters BETWEEN 1 AND 20", name="ck_staff_youngsters"),
        CheckConstraint("judging_ability BETWEEN 1 AND 20", name="ck_staff_judging_ability"),
        CheckConstraint("judging_potential BETWEEN 1 AND 20", name="ck_staff_judging_potential"),
        CheckConstraint("physiotherapy BETWEEN 1 AND 20", name="ck_staff_physiotherapy"),
        CheckConstraint("man_management BETWEEN 1 AND 20", name="ck_staff_man_management"),
        CheckConstraint("determination BETWEEN 1 AND 20", name="ck_staff_determination"),
        CheckConstraint("tactical_knowledge BETWEEN 1 AND 20", name="ck_staff_tactical_knowledge"),
        Index("ix_staff_team_role", "team_id", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    role: Mapped[StaffRole] = mapped_column(
        SQLEnum(StaffRole, name="staff_role_enum", values_callable=_enum_values), nullable=False
    )
    wage: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)    # HAFTALIK EUR
    reputation: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)

    # --- Antrenor ---
    attacking: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    defending: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    tactical: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    working_with_youngsters: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    # --- Gozlemci ---
    judging_ability: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    judging_potential: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    # --- Saglikci ---
    physiotherapy: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    # --- Asistan menajer ---
    man_management: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    determination: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    tactical_knowledge: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )

    team: Mapped[Team | None] = relationship(back_populates="staff")

    @property
    def employed(self) -> bool:
        return self.team_id is not None

    def __repr__(self) -> str:
        where = self.team_id or "boşta"
        return f"<Staff {self.name} {self.role.value} rep={self.reputation} @{where}>"


# ---------------------------------------------------------------------------
# GameState — kariyerin tek satirlik durumu
# ---------------------------------------------------------------------------

class GameState(Base):
    """
    'Hangi sezon, hangi hafta, kullanici hangi takimi yonetiyor' sorusunun
    TEK dogru kaynagi. Her zaman id=1 olan tek satir vardir.
    """
    __tablename__ = "game_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_game_state_singleton"),
        CheckConstraint("season >= 1", name="ck_game_state_season"),
        CheckConstraint("current_week >= 1", name="ck_game_state_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    current_week: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    user_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )

    user_team: Mapped[Team | None] = relationship()

    def __repr__(self) -> str:
        return f"<GameState sezon={self.season} hafta={self.current_week} takim={self.user_team_id}>"
