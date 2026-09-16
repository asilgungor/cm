"""
models.py
=========
Veritabani semasi (SQLAlchemy ORM modelleri).

Tasarim notu:
    Bu siniflar sadece VERIYI temsil eder. Mac simulasyonu, puan hesabi,
    transfer mantigi gibi kurallar buraya degil, ileride yazacagimiz
    engine/ katmanina gidecek. Buradaki tek istisna, saf okuma amacli
    kucuk yardimci ozellikler (goal_difference gibi) -- bunlar veritabanina
    yazilmaz, ekranda gostermek icin aninda hesaplanir.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

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


def _enum_values(enum_cls) -> list:
    """
    ENUM degerlerini veritabanina "GK", "DEF"... olarak yazdirir.
    (SQLAlchemy'nin varsayilan davranisi Python tarafindaki ISIMLERI yazmaktir,
    bu da UNPLAYED vs unplayed karisikligina yol acar.)
    """
    return [member.value for member in enum_cls]


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
        CheckConstraint("budget >= 0", name="ck_team_budget"),
        CheckConstraint("played = won + drawn + lost", name="ck_team_played_consistent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    budget: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)          # Euro
    reputation: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)   # 1-100

    # --- Lig tablosu istatistikleri (simulasyon motoru gunceller) ---
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
        Index("ix_player_team_position", "team_id", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Serbest (kulupsuz) oyuncular icin ileride NULL olabilsin diye nullable.
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    age: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    position: Mapped[Position] = mapped_column(
        SQLEnum(Position, name="position_enum", values_callable=_enum_values),
        nullable=False,
    )

    # --- Yetenekler (1-99) ---
    overall_rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pace: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    shooting: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    passing: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    defending: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dribbling: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    goalkeeping: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # --- Degisken durum (0-100). Simulasyon motoru hafta hafta gunceller. ---
    form: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    morale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=70)

    team: Mapped[Team | None] = relationship(back_populates="players")

    def __repr__(self) -> str:
        return f"<Player {self.name} {self.position.value} {self.overall_rating}>"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint("league_id", "week", "home_team_id", name="uq_fixture_slot"),
        CheckConstraint("home_team_id <> away_team_id", name="ck_fixture_distinct_teams"),
        CheckConstraint("week >= 1", name="ck_fixture_week"),
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
        Index("ix_fixture_league_week", "league_id", "week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # NOT: Senin listende yoktu, ekledim. Fikstur her zaman "su ligin su haftasi"
    # seklinde sorgulanacagi icin bu sutun olmadan her sorguda teams tablosuna
    # join atmamiz gerekirdi. Istemezsen cikarabiliriz.
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

    @property
    def is_played(self) -> bool:
        return self.status == FixtureStatus.PLAYED

    def __repr__(self) -> str:
        if self.is_played:
            return (
                f"<Fixture W{self.week} {self.home_team_id} "
                f"{self.home_score}-{self.away_score} {self.away_team_id}>"
            )
        return f"<Fixture W{self.week} {self.home_team_id} vs {self.away_team_id}>"
