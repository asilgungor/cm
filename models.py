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
    tournaments, tournament_entries, cup_ties   (8. Asama: Devler Arenasi / Champions Cup)
    accounts.users                              (10. Asama: hesaplar, kariyerlerden AYRI semada)
    transfer_log, season_honours, news_items,
    shortlist, friendlies                       (12. Asama: kariyer paketi)
    tactic_presets                              (13. Asama: kayitli taktikler)

Finans (5. Asama):
    teams.transfer_budget  -> bonservis kasasi (EUR)
    teams.wage_budget      -> HAFTALIK toplam maas havuzu (EUR/hafta)
    players.market_value / current_wage / contract_years / squad_role
    staff.wage             -> personel de ayni haftalik havuzdan oder

Dinamik kondisyon:
    players.condition      -> 0-100, maclar arasi tasinir (kurallar fitness.py)

Kupa (8. Asama):
    fixtures.competition   -> LEAGUE / CUP. Kupa fiksturunun league_id'si bostur,
                              tournament_id + tie_id + stage + leg doludur.
    players.cup_suspended_matches / cup_yellow_cards -> kupa cezalari ligden AYRI sayilir.
    game_state.game_mode   -> CAREER_MODE (lig + kupa) / TOURNAMENT_MODE (sadece kupa);
                              NULL = oyuncu henuz mod secmedi (ilk giris ekrani).

Hesaplar, altyapi ve gelisim (10. Asama):
    accounts.users         -> kullanici adi + parola ozeti; career_schema kullanicinin kariyer semasi
    game_state.user_id     -> kariyerin sahibi (accounts.users). Her kariyer ayri PostgreSQL semasi.
    players.potential_rating (1-99, overall ile ayni olcek) / development_progress (birikim)
    players.in_academy     -> U-21 akademi kadrosu. Team.players YALNIZCA A takimini dondurur
                              (mac motoru, taktik, transfer akademiyi gormez); akademi:
                              Team.academy_players.
    teams.youth_facilities -> altyapi tesisleri 1-20 (genc girisi kalitesi)

Tesisler ve sponsorluk (11. Asama, kurallar facilities.py):
    teams.stadium_capacity / medical_facilities       -> mac gunu geliri / kondisyon toparlanmasi
    teams.sponsor_name / sponsor_weekly / sponsor_until_season / sponsor_offers (JSONB liste)

Kariyer paketi (12. Asama; kurallar finance.py / concerns.py, orkestrasyon career_manager.py):
    game_state.career_week_offset -> onceki sezonlarda oynanan hafta toplami; mutlak kariyer haftasi =
                                     offset + current_week (sezon devrinde kesintisiz artar)
    players.transfer_locked_until -> transfer yasagi: mutlak kariyer haftasi bu degere ulasana kadar
                                     oyuncu satilamaz / teklif alamaz (NULL: yasak yok)
    players.minutes_window        -> oynama suresi penceresi (JSONB, son CONCERN_WINDOW olay):
                                     [oynadigi dk, beklenen maclik dk, beklenti payi, hazirlik dk]
    players.concern_level         -> 0 yok · 1 sure bekliyor · 2 sikayetci · 3 ayrilmak istiyor
    players.contract_overall      -> maasi belirlendiginde (transfer / yeni sozlesme) gucu
    players.wage_demand           -> bekleyen yeni sozlesme (maas) talebi, haftalik EUR
    transfer_log, season_honours, news_items, shortlist, friendlies (tablolar asagida)

Taktik kaliciligi (13. Asama; orkestrasyon career_manager.py, kurallar instructions.py / team_roles.py /
match_plan.py):
    teams.tactic_instructions -> kayitli takim talimati (TeamInstructions.to_dict; {} = varsayilan)
    teams.set_piece_roles     -> kaptan + duran top aticilari (SetPieceRoles.to_dict; {} = belirlenmedi)
    teams.match_plan          -> durum bazli oyun plani (MatchPlan.to_dict; {} = plan yok)
    tactic_presets            -> kulup basina en fazla 7 adli taktik: dizilis, talimat, roller, plan ve
                                 kadro (ilk 11 + kulube) anlik goruntusu
    JSONB'deki oyuncu id'leri FK degildir: kulupten ayrilan oyuncular okunurken (lazy) ayiklanir.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import ACCOUNTS_SCHEMA, Base

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


class GameMode(str, enum.Enum):
    """Oyun modu: lig maratonu (kupa takvimi senkron akar) veya sadece Devler Arenasi."""
    CAREER = "CAREER_MODE"
    TOURNAMENT = "TOURNAMENT_MODE"


class Competition(str, enum.Enum):
    """Fiksturun ait oldugu organizasyon."""
    LEAGUE = "LEAGUE"
    CUP = "CUP"


class TournamentStatus(str, enum.Enum):
    """DRAW: kura cekiliyor · RUNNING: maclar oynaniyor · FINISHED: sampiyon belli."""
    DRAW = "DRAW"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class TransferKind(str, enum.Enum):
    """transfer_log.kind (12. Asama). Veritabaninda duz metin: yeni tur eklemek goc gerektirmez."""
    TRANSFER = "TRANSFER"          # bonservisli kulup degisikligi (kullanici ya da AI)
    FREE_AGENT = "FREE_AGENT"      # kulupsuz oyuncunun imzasi


class HonourKind(str, enum.Enum):
    """season_honours.kind: lig ya da kupa."""
    LEAGUE = "LEAGUE"
    CUP = "CUP"


class NewsKind(str, enum.Enum):
    """news_items.kind (duz metin; yeni tur goc gerektirmez)."""
    TRANSFER = "TRANSFER"
    LEAGUE_CHAMPION = "LEAGUE_CHAMPION"
    CUP_CHAMPION = "CUP_CHAMPION"
    SPONSOR = "SPONSOR"
    BIG_RESULT = "BIG_RESULT"
    WONDERKID = "WONDERKID"
    CHAIRMAN = "CHAIRMAN"


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
# User -- hesaplar (10. Asama). Kariyer semalarindan AYRI 'accounts' semasinda.
# ---------------------------------------------------------------------------

class User(Base):
    """
    Menajer hesabi. Parola ASLA duz metin saklanmaz: password_hash (auth.py) yazilir.
    career_schema: kullanicinin kariyerinin yasadigi PostgreSQL semasi ('public' ya da
    'career_<id>'); kariyer henuz kurulmadiysa NULL.
    """
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("char_length(username) BETWEEN 3 AND 32", name="ck_user_username_length"),
        Index("uq_user_username_lower", func.lower(text("username")), unique=True),
        {"schema": ACCOUNTS_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sunucu tarafi kaba kuvvet korumasi: art arda hatali parola sayaci ve kilit bitis zamani
    failed_logins: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    locked_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    career_schema: Mapped[str | None] = mapped_column(String(63), nullable=True, unique=True)

    def __repr__(self) -> str:
        return f"<User {self.username} kariyer={self.career_schema}>"


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
        CheckConstraint("youth_facilities IS NULL OR youth_facilities BETWEEN 1 AND 20",
                        name="ck_team_youth_facilities"),
        # 11. Asama. Kapasite siniri oyun kuralindan (facilities.STADIUM_MIN/MAX) genistir: goc araci
        # olmadigindan kural degisirse eski kayitlardaki CHECK'i degistirmek gerekmesin.
        CheckConstraint("stadium_capacity IS NULL OR stadium_capacity BETWEEN 1000 AND 200000",
                        name="ck_team_stadium_capacity"),
        CheckConstraint("medical_facilities IS NULL OR medical_facilities BETWEEN 1 AND 20",
                        name="ck_team_medical_facilities"),
        CheckConstraint("sponsor_weekly >= 0", name="ck_team_sponsor_weekly"),
        CheckConstraint("sponsor_until_season IS NULL OR sponsor_until_season >= 1",
                        name="ck_team_sponsor_until_season"),
        CheckConstraint("jsonb_typeof(sponsor_offers) = 'array'", name="ck_team_sponsor_offers"),
        # 13. Asama: kayitli taktik
        CheckConstraint("jsonb_typeof(tactic_instructions) = 'object'", name="ck_team_tactic_instructions"),
        CheckConstraint("jsonb_typeof(set_piece_roles) = 'object'", name="ck_team_set_piece_roles"),
        CheckConstraint("jsonb_typeof(match_plan) = 'object'", name="ck_team_match_plan"),
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
    # Altyapi tesisleri 1-20 (10. Asama): genc girisinin potansiyel dagilimini belirler
    youth_facilities: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # --- Tesisler ve sponsorluk (11. Asama; kurallar facilities.py) ---
    # NULL tesis: eski kayit, CareerManager.ensure_club_setup henuz doldurmadi -> etkisiz (eski davranis)
    stadium_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)        # koltuk
    medical_facilities: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1-20, 10 notr
    # Gecerli sponsor sozlesmesi. sponsor_until_season: son gecerli sezon (dahil). Sozlesme bitince ad ve
    # bedel silinir, bitis sezonu kalir: "hic sozlesmesi olmamis" (ad ve bitis NULL) kulupten ayrilir.
    sponsor_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sponsor_weekly: Mapped[int] = mapped_column(                        # HAFTALIK EUR
        BigInteger, nullable=False, default=0, server_default="0"
    )
    sponsor_until_season: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Bekleyen teklifler: facilities.SponsorOffer.to_dict listesi
    sponsor_offers: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # --- Kayitli taktik (13. Asama; CareerManager.team_instructions / team_roles / team_plan okur) ---
    # Hosgorulu okunur: bozuk ya da eksik anahtar varsayilana doner, kadrodan ayrilan oyuncu ayiklanir.
    tactic_instructions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    set_piece_roles: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    match_plan: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    # --- Lig tablosu istatistikleri (sezon basinda sifirlanir) ---
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    played: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drawn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    league: Mapped[League] = relationship(back_populates="teams")
    # A TAKIM kadrosu (akademi haric). Mac motoru, taktik, finans ve transfer yalnizca bunu gorur.
    # DIKKAT: oyuncu akademiye alinip A takima yukseltilince (in_academy degisince) bellekteki
    # koleksiyonlar kendiliginden yenilenmez: db.expire(team, ["players", "academy_players"]).
    players: Mapped[list[Player]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
        primaryjoin="and_(Team.id == Player.team_id, Player.in_academy.is_(False))",
        # Esit guclu oyuncularin sirasi DB'ye birakilmasin: ayni tohum -> ayni kadro sirasi -> ayni mac
        order_by="[Player.overall_rating.desc(), Player.id]",
    )
    # U-21 akademi kadrosu (salt okunur; oyuncu Player.team_id + in_academy ile yazilir)
    academy_players: Mapped[list[Player]] = relationship(
        primaryjoin="and_(Team.id == Player.team_id, Player.in_academy.is_(True))",
        viewonly=True,
        order_by="[Player.potential_rating.desc().nulls_last(), Player.overall_rating.desc(), Player.id]",
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
        """
        Oyuncularin haftalik toplam maasi: A takim + U-21 akademi. Akademiye gonderilen oyuncunun
        maasi yukten dusmez (maas alani acmak icin akademiye park etme acigi kapali).
        """
        return sum(p.current_wage for p in self.players) + sum(p.current_wage for p in self.academy_players)

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
        CheckConstraint("condition BETWEEN 0 AND 100", name="ck_player_condition"),
        CheckConstraint("injured_until_week >= 0", name="ck_player_injured_week"),
        CheckConstraint("suspended_matches >= 0", name="ck_player_suspended"),
        CheckConstraint("season_yellow_cards >= 0", name="ck_player_season_yellows"),
        CheckConstraint("cup_suspended_matches >= 0", name="ck_player_cup_suspended"),
        CheckConstraint("cup_yellow_cards >= 0", name="ck_player_cup_yellows"),
        CheckConstraint("weeks_since_match >= 0", name="ck_player_weeks_since_match"),
        CheckConstraint("market_value >= 0", name="ck_player_market_value"),
        CheckConstraint("current_wage >= 0", name="ck_player_current_wage"),
        CheckConstraint("contract_years BETWEEN 0 AND 6", name="ck_player_contract_years"),
        CheckConstraint("data_source IN ('synthetic', 'fm', 'academy')", name="ck_player_data_source"),
        CheckConstraint(
            "current_ability IS NULL OR current_ability BETWEEN 1 AND 200", name="ck_player_ca"
        ),
        CheckConstraint(
            "potential_ability IS NULL OR potential_ability BETWEEN 1 AND 200", name="ck_player_pa"
        ),
        CheckConstraint(
            "potential_rating IS NULL OR potential_rating BETWEEN 1 AND 99", name="ck_player_potential"
        ),
        CheckConstraint("concern_level BETWEEN 0 AND 3", name="ck_player_concern_level"),
        CheckConstraint("wage_demand IS NULL OR wage_demand >= 0", name="ck_player_wage_demand"),
        CheckConstraint(
            "contract_overall IS NULL OR contract_overall BETWEEN 1 AND 99", name="ck_player_contract_overall"
        ),
        CheckConstraint("jsonb_typeof(minutes_window) = 'array'", name="ck_player_minutes_window"),
        Index("ix_player_team_position", "team_id", "position"),
        Index("ix_player_team_academy", "team_id", "in_academy"),
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
    # Mac kondisyonu (dinamik). Macta enerji buradan baslar; mac sonrasi saglikciya
    # gore toparlanir, oynamayan tam dinlenir (100). Kurallar: fitness.py
    condition: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=100, server_default="100"
    )

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
    # Kupa cezalari lig cezalarindan bagimsizdir (UEFA kurali): kupada kirmizi goren
    # ligde oynayabilir, kupa macinda oynayamaz. Sari birikimi de ayri sayilir.
    cup_suspended_matches: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    cup_yellow_cards: Mapped[int] = mapped_column(
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
    # Son transfer edildigi sezon. AI kulupleri ayni sezon icinde yeni transfer edilen
    # oyuncuyu tekrar satin almaz (haftalar arasi "atlikarinca" transferlerini onler).
    last_transfer_season: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # --- Veri kaynagi (6. Asama) ---
    # synthetic: kurgusal uretim · fm: Football Manager disa aktarimi · academy: kadro tamamlama
    data_source: Mapped[str] = mapped_column(
        String(12), nullable=False, default="synthetic", server_default="synthetic"
    )
    nationality: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fm_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    current_ability: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)     # FM CA 1-200
    potential_ability: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)   # FM PA 1-200
    # Ham FM ozellikleri (1-20), orn. {"finishing": 16, "pace": 14}. Motor ozellikleri bunlardan turetilir.
    fm_attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    # --- Potansiyel, gelisim ve altyapi (10. Asama) ---
    # Tavan guc (1-99, overall ile ayni olcek). NULL: henuz atanmadi (eski kayit) -> overall sayilir.
    potential_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # U-21 akademi kadrosunda mi? (A takim kadro sinirlarina ve maclara dahil degil)
    in_academy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Haftalik gelisim/gerileme birikimi (overall birimi): +1 / -1 olunca guc kalici degisir
    development_progress: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    # --- Kariyer paketi (12. Asama) ---
    # Transfer yasagi: mutlak kariyer haftasi (GameState.career_week_offset + current_week) bu degerin
    # altindayken oyuncu satilamaz ve teklif alamaz. NULL: yasak yok.
    transfer_locked_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Oynama suresi penceresi (concerns.py): [[oynadigi dk, beklenen maclik dk, beklenti payi, hazirlik dk], ...]
    minutes_window: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    concern_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    # Maasi belirlendigi andaki guc (NULL: bilinmiyor; ilk guc degisiminde eski guc yazilir)
    contract_overall: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Bekleyen yeni sozlesme talebi (haftalik EUR). NULL: talep yok.
    wage_demand: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

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

    def is_suspended_for(self, competition: Competition = Competition.LEAGUE) -> bool:
        if competition is Competition.CUP:
            return self.cup_suspended_matches > 0
        return self.suspended_matches > 0

    def is_available(self, week: int, competition: Competition = Competition.LEAGUE) -> bool:
        return not (self.is_injured(week) or self.is_suspended_for(competition))

    def unavailability_reason(
        self, week: int, competition: Competition = Competition.LEAGUE
    ) -> str | None:
        if self.is_injured(week):
            return f"sakat, {self.injured_until_week}. haftada dönüyor"
        if competition is Competition.CUP:
            if self.cup_suspended_matches > 0:
                return f"kupada cezalı, {self.cup_suspended_matches} maç"
            return None
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
        # Lig maci lige, kupa maci turnuvaya bagli olmak zorunda
        CheckConstraint(
            "(competition = 'LEAGUE' AND league_id IS NOT NULL) OR "
            "(competition = 'CUP' AND tournament_id IS NOT NULL)",
            name="ck_fixture_competition_owner",
        ),
        CheckConstraint(
            "(home_penalties IS NULL) = (away_penalties IS NULL)", name="ck_fixture_penalties_pair"
        ),
        CheckConstraint("leg IS NULL OR leg BETWEEN 1 AND 6", name="ck_fixture_leg"),
        Index("ix_fixture_season_league_week", "season", "league_id", "week"),
        Index("ix_fixture_tournament_week", "tournament_id", "week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")

    # Fikstur her zaman "su ligin su haftasi" seklinde sorgulanir; join'siz erisim icin.
    # Kupa maclarinda bos (bkz. ck_fixture_competition_owner).
    league_id: Mapped[int | None] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=True, index=True
    )
    competition: Mapped[Competition] = mapped_column(
        SQLEnum(Competition, name="competition_enum", values_callable=_enum_values),
        nullable=False, default=Competition.LEAGUE, server_default="LEAGUE",
    )
    # --- Kupa baglami (8. Asama) ---
    tournament_id: Mapped[int | None] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=True
    )
    tie_id: Mapped[int | None] = mapped_column(
        ForeignKey("cup_ties.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)   # cup_draw.Stage degeri
    leg: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1/2 veya grup turu
    neutral_venue: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    extra_time: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    home_penalties: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    away_penalties: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Kupa maclarinin ozet olay kaydi (gol, kart, sakatlik, uzatma, penalti vuruslari).
    # Mac tekrar izlenemese de "kim atti / kim kacirdi" raporu buradan okunur.
    key_events: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
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

    league: Mapped[League | None] = relationship(back_populates="fixtures")
    tournament: Mapped[Tournament | None] = relationship(back_populates="fixtures")
    tie: Mapped[CupTie | None] = relationship(back_populates="fixtures", foreign_keys=[tie_id])
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

    @property
    def is_cup(self) -> bool:
        return self.competition is Competition.CUP

    @property
    def went_to_penalties(self) -> bool:
        return self.home_penalties is not None

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
        CheckConstraint("manager_reputation BETWEEN 1 AND 20", name="ck_game_state_manager_rep"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    current_week: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    user_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    # Menajer tanınırlığı 1-20 (6. Asama). Kurallar: reputation.py
    manager_reputation: Mapped[float] = mapped_column(
        Float, nullable=False, default=8.0, server_default="8"
    )
    # 8. Asama: NULL = ilk giris, mod secim ekrani gosterilir
    game_mode: Mapped[GameMode | None] = mapped_column(
        SQLEnum(GameMode, name="game_mode_enum", values_callable=_enum_values), nullable=True
    )
    # --- 10. Asama ---
    # Kariyerin sahibi (accounts.users). NULL: sahipsiz eski kariyer (ilk kayit olan devralir).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{ACCOUNTS_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    # Baslangic akademileri ve potansiyeller dolduruldu mu? (eski kayitlar icin tek seferlik)
    academy_seeded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_youth_intake_season: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # --- 12. Asama ---
    # Onceki sezonlarda oynanan haftalar toplami: mutlak kariyer haftasi = offset + current_week
    career_week_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    user_team: Mapped[Team | None] = relationship()

    def __repr__(self) -> str:
        return f"<GameState sezon={self.season} hafta={self.current_week} takim={self.user_team_id}>"


# ---------------------------------------------------------------------------
# Tournament — Devler Arenasi (Champions Cup), sezon basina bir tane
# ---------------------------------------------------------------------------

class Tournament(Base):
    """
    Sezonluk kupa. Kura durumu (cup_draw.DrawSession.to_state) ve takvim JSON olarak
    saklanir; boylece sayfa yenilense de kura kaldigi toptan devam eder.
    Kurallar cup_draw.py'de, orkestrasyon tournament_manager.py'de.
    """
    __tablename__ = "tournaments"
    __table_args__ = (
        UniqueConstraint("season", name="uq_tournament_season"),
        CheckConstraint("format IN ('knockout', 'groups')", name="ck_tournament_format"),
        CheckConstraint("size IN (8, 16)", name="ck_tournament_size"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    format: Mapped[str] = mapped_column(String(12), nullable=False, default="knockout")
    size: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=16)
    status: Mapped[TournamentStatus] = mapped_column(
        SQLEnum(TournamentStatus, name="tournament_status_enum", values_callable=_enum_values),
        nullable=False, default=TournamentStatus.DRAW, server_default="DRAW",
    )
    draw_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # [{"number": 1, "stage": "R16", "leg": 1, "week": 1}, ...]
    calendar: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    champion_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )

    entries: Mapped[list[TournamentEntry]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan",
        order_by="TournamentEntry.seed_rank",
    )
    ties: Mapped[list[CupTie]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan", order_by="CupTie.id"
    )
    fixtures: Mapped[list[Fixture]] = relationship(back_populates="tournament")
    champion: Mapped[Team | None] = relationship()

    def __repr__(self) -> str:
        return f"<Tournament S{self.season} {self.format} {self.status.value}>"


class TournamentEntry(Base):
    """Turnuvaya katilan takim: torba, katsayi, (grup formatinda) grup istatistikleri."""
    __tablename__ = "tournament_entries"
    __table_args__ = (
        UniqueConstraint("tournament_id", "team_id", name="uq_tournament_entry"),
        CheckConstraint("played = won + drawn + lost", name="ck_entry_played_consistent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seed_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)     # 1 = en guclu
    pot: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    coefficient: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    league_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    group_index: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)   # 0=A..3=D
    played: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drawn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eliminated_stage: Mapped[str | None] = mapped_column(String(8), nullable=True)

    tournament: Mapped[Tournament] = relationship(back_populates="entries")
    team: Mapped[Team] = relationship()

    def __repr__(self) -> str:
        return f"<TournamentEntry t={self.tournament_id} team={self.team_id} pot={self.pot}>"


class CupTie(Base):
    """
    Eleme turu eslesmesi. first_team ilk maci evinde oynar; second_team (torba 1 / grup
    birincisi) rovansi evinde oynar. Finalde tek mac, tarafsiz saha.
    aggregate_first/second: toplam skor (first/second takim bakisiyla).
    """
    __tablename__ = "cup_ties"
    __table_args__ = (
        UniqueConstraint("tournament_id", "stage", "slot", name="uq_cup_tie_slot"),
        CheckConstraint("first_team_id <> second_team_id", name="ck_cup_tie_distinct"),
        CheckConstraint(
            "decided_by IS NULL OR decided_by IN ('normal', 'extra_time', 'penalties')",
            name="ck_cup_tie_decided_by",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(8), nullable=False)
    slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    first_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    second_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    winner_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(12), nullable=True)
    aggregate_first: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    aggregate_second: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    penalties_first: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    penalties_second: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    tournament: Mapped[Tournament] = relationship(back_populates="ties")
    first_team: Mapped[Team] = relationship(foreign_keys=[first_team_id])
    second_team: Mapped[Team] = relationship(foreign_keys=[second_team_id])
    winner: Mapped[Team | None] = relationship(foreign_keys=[winner_team_id])
    fixtures: Mapped[list[Fixture]] = relationship(
        back_populates="tie", foreign_keys="Fixture.tie_id", order_by="Fixture.leg"
    )

    @property
    def decided(self) -> bool:
        return self.winner_team_id is not None

    def involves(self, team_id: int | None) -> bool:
        return team_id in (self.first_team_id, self.second_team_id)

    def __repr__(self) -> str:
        return (f"<CupTie {self.stage}#{self.slot} {self.first_team_id}-{self.second_team_id} "
                f"agg {self.aggregate_first}-{self.aggregate_second} w={self.winner_team_id}>")


# ---------------------------------------------------------------------------
# 12. Asama: transfer gecmisi, sezon onurlari, haber akisi, izleme listesi, hazirlik maclari
# ---------------------------------------------------------------------------
# Kayit tablolari takim/oyuncu silinse de okunabilsin diye adlari da saklar (FK'lar SET NULL).

class TransferLog(Base):
    """Tamamlanan her transfer (kullanici ve AI). Rekor transferler ve kulup transfer gecmisi buradan okunur."""
    __tablename__ = "transfer_log"
    __table_args__ = (
        CheckConstraint("fee >= 0", name="ck_transfer_log_fee"),
        CheckConstraint("wage >= 0", name="ck_transfer_log_wage"),
        CheckConstraint("season >= 1 AND week >= 1", name="ck_transfer_log_when"),
        Index("ix_transfer_log_season_week", "season", "week"),
        Index("ix_transfer_log_fee", "fee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True
    )
    player_name: Mapped[str] = mapped_column(String(80), nullable=False)
    from_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    from_team_name: Mapped[str | None] = mapped_column(String(80), nullable=True)   # NULL: kulupsuz
    to_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    to_team_name: Mapped[str] = mapped_column(String(80), nullable=False)
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)       # EUR
    wage: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)      # haftalik EUR
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default=TransferKind.TRANSFER.value)

    def __repr__(self) -> str:
        return f"<TransferLog S{self.season}W{self.week} {self.player_name} {self.from_team_name}->{self.to_team_name}>"


class SeasonHonour(Base):
    """
    Sezon arsivi: her lig ve kupa icin sezonda TEK satir (sampiyon, ikinci, gol krali, sezonun oyuncusu).
    Lig: lig bittiginde (en gec yeni sezon kurulmadan once); kupa: final oynandiginda yazilir.
    """
    __tablename__ = "season_honours"
    __table_args__ = (
        UniqueConstraint("season", "kind", "competition_name", name="uq_season_honour"),
        CheckConstraint("kind IN ('LEAGUE', 'CUP')", name="ck_season_honour_kind"),
        CheckConstraint("season >= 1", name="ck_season_honour_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    league_id: Mapped[int | None] = mapped_column(
        ForeignKey("leagues.id", ondelete="SET NULL"), nullable=True
    )
    competition_name: Mapped[str] = mapped_column(String(80), nullable=False)
    champion_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    champion_name: Mapped[str] = mapped_column(String(80), nullable=False)
    runner_up_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    runner_up_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    top_scorer_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    top_scorer_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    top_scorer_team: Mapped[str | None] = mapped_column(String(80), nullable=True)
    top_scorer_goals: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    player_of_season_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    player_of_season_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    player_of_season_team: Mapped[str | None] = mapped_column(String(80), nullable=True)
    player_of_season_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Kullanicinin takiminin bu ligdeki sirasi (lig kaydi ve takim bu ligdeyse; aksi NULL)
    user_team_position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    def __repr__(self) -> str:
        return f"<SeasonHonour S{self.season} {self.kind} {self.competition_name}: {self.champion_name}>"


class NewsItem(Base):
    """Dunya haber akisi (transferler, sampiyonlar, buyuk skorlar, sponsor, wonderkid...). Sira: id."""
    __tablename__ = "news_items"
    __table_args__ = (
        Index("ix_news_season_week", "season", "week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)       # NewsKind degeri
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Haberin ikinci tarafi (transferde satan kulup, buyuk skorda rakip)
    other_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )

    def __repr__(self) -> str:
        return f"<NewsItem S{self.season}W{self.week} {self.kind}: {self.text[:40]}>"


class ShortlistEntry(Base):
    """Menajerin izleme listesi. Kariyer semasi basina tek menajer: oyuncu basina tek satir."""
    __tablename__ = "shortlist"

    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    added_season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    added_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)

    player: Mapped[Player] = relationship()

    def __repr__(self) -> str:
        return f"<ShortlistEntry player={self.player_id} S{self.added_season}W{self.added_week}>"


class Friendly(Base):
    """
    Kullanicinin hazirlik maci (haftada en fazla bir). Puan tablosu, istatistik, form ve itibari ETKILEMEZ;
    yalnizca sonuc ve kisa gol ozeti saklanir.
    """
    __tablename__ = "friendlies"
    __table_args__ = (
        UniqueConstraint("season", "week", name="uq_friendly_week"),
        CheckConstraint("home_score >= 0 AND away_score >= 0", name="ck_friendly_scores"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    home_team_name: Mapped[str] = mapped_column(String(80), nullable=False)
    away_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    away_team_name: Mapped[str] = mapped_column(String(80), nullable=False)
    home_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    away_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Gol ozeti: [{"minute", "added", "team_id", "team", "player_id", "player", "text"}]
    events: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    def __repr__(self) -> str:
        return (f"<Friendly S{self.season}W{self.week} {self.home_team_name} {self.home_score}-"
                f"{self.away_score} {self.away_team_name}>")


# ---------------------------------------------------------------------------
# 13. Asama: kayitli taktikler (Soccer Manager "Tactics" slotlari)
# ---------------------------------------------------------------------------

class TacticPreset(Base):
    """
    Kulubun adli taktigi (en fazla career_manager.MAX_TACTIC_PRESETS). Kaydedildigi andaki dizilis, talimat,
    roller, oyun plani ve kadro anlik goruntusu. Ad kulup icinde harf buyuklugunden bagimsiz tekildir.

        lineup  {"xi": [{"player_id": 12, "role": "DEF"}, ...], "bench": [ids], "out": [ids]}
                (oyuncularin lineup_status / lineup_role degerleri; bos xi: asistan kurar)
    Oyuncu id'leri FK degildir: uygulanirken kulupten ayrilan / akademideki / oynayamayan oyuncular atlanir.
    """
    __tablename__ = "tactic_presets"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 40", name="ck_tactic_preset_name"),
        CheckConstraint("formation IN ('4-4-2', '4-3-3', '3-5-2')", name="ck_tactic_preset_formation"),
        CheckConstraint("created_season >= 1 AND created_week >= 1", name="ck_tactic_preset_created"),
        CheckConstraint("jsonb_typeof(instructions) = 'object'", name="ck_tactic_preset_instructions"),
        CheckConstraint("jsonb_typeof(roles) = 'object'", name="ck_tactic_preset_roles"),
        CheckConstraint("jsonb_typeof(plan) = 'object'", name="ck_tactic_preset_plan"),
        CheckConstraint("jsonb_typeof(lineup) = 'object'", name="ck_tactic_preset_lineup"),
        Index("uq_tactic_preset_team_name", "team_id", func.lower(text("name")), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    formation: Mapped[str] = mapped_column(String(5), nullable=False, default="4-4-2", server_default="4-4-2")
    instructions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    roles: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    plan: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    lineup: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    def __repr__(self) -> str:
        return f"<TacticPreset #{self.id} team={self.team_id} {self.name!r} {self.formation}>"
