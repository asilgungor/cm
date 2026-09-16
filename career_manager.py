"""
career_manager.py
=================
Sezon dongusu kontrolcusu (3. Asama).

Sorumluluklar:
    * Mevcut haftadaki TUM liglerin maclarini match_engine ile oynatir
    * Mac sonrasi kaliciligi yazar: oyuncu mac istatistikleri, not gecmisi,
      form/moral guncellemesi, sakatlik suresi, kart cezasi, sari birikimi
    * Haftalik maaslari oder ve butceleri gunceller (5. Asama)
    * Teknik heyet etkilerini uygular: saglikci -> sakatlik suresi ve kondisyon toparlanmasi,
      antrenor -> form, asistan -> moral, gozlemci -> bilgi sisi
    * Dinamik kondisyon (fitness.py): oynayanin mac sonu enerjisi saglikciya gore
      toparlanip kaydedilir; oynamayan tam dinlenir (100)
    * Transfer pazarini yurutur: bonservis teklifi, sozlesme masasi, AI kulupleri
    * Ceza sayaclarini hafta sonunda azaltir, haftayi ilerletir
    * Puan durumu / gol kralligi / sonraki mac / takim formu sorgulari
    * Sezon bitince yeni sezon kurar (fikstur, yas, istatistik sifirlama)
    * Oyun modu (8. Asama): CAREER_MODE'da her hafta once o haftanin Devler Arenasi maclari
      (hafta ici), sonra lig maclari (hafta sonu) oynanir; TOURNAMENT_MODE'da sadece kupa.
      Kupa orkestrasyonu tournament_manager.py'dedir.
    * Canli mac (9. Asama): kullanicinin bu haftaki gercek maci (lig ya da kupa) canli oynanir.
        live_fixture        siradaki maci: once hafta ici kupa, sonra lig (turnuva modunda sadece kupa)
        prepare_live_match  motoru otomatik yolla AYNI parametrelerle kurar (tohum, ayar, hafta,
                            kupada eleme kurali / tarafsiz saha / kupa cezalari). Lig macindan once
                            bu haftanin kupa maclari hala bekliyorsa once play_midweek oynatilir ki
                            hafta ici toparlanma ve sakatliklar lig kadrosuna yansisin.
        play_midweek        yalnizca bu haftanin kupa mac gunu (hafta ilerlemez)
        play_week / play_midweek(live_results)
                            canli maclarin bitmis sonuclari simulasyon yerine islenir; kalicilik
                            (tablo, oyuncu satirlari, form/moral/kondisyon, cezalar, tanınırlık)
                            otomatik yolla birebir aynidir. Sonuclar HICBIR SEY yazilmadan dogrulanir.
        save_live_result    arayuz kisayolu: kupa maci + bekleyen lig maci -> play_midweek, aksi play_week
      Mudahalesiz canli mac, ayni tohumla otomatik oynanan macla bit-bit aynidir.
    * Gelisim ve altyapi (10. Asama; kurallar development.py / youth.py):
        ensure_youth_setup  eski kayit/yeni dunya icin idempotent doldurma: potansiyel, tesis, akademiler
        play_week           (yalnizca kariyer modu) maclardan sonra TUM oyuncular (A takim + akademi) icin
                            haftalik gelisim/yaslanma: bu haftanin lig VE kupa dakikalari/notlari (hafta ici
                            play_midweek ile oynanmis olsa bile), genc antrenoru, moral, tesis. 32+ gerileme.
                            youth_intake_week() haftasinda sezonda bir kez TUM kuluplere genc girisi (ayri,
                            tohumdan turetilmis RNG: cm.rng dizisi bozulmaz), akademi kapasitesi uygulanir.
        promote_to_senior / send_to_academy
                            kadro kurallari (A takim en fazla 25, en az SQUAD_FLOOR ve 2 kaleci; akademi 20,
                            21 yas ustu en fazla 3). Ihlalde AcademyError (Turkce mesaj).
        potential_estimate  gozlemcinin (judging_potential) sisli potansiyel araligi; gercek tavan gizlidir
        start_new_season    akademi de yaslanir; AI kulupleri akademisini yonetir (yukseltme/serbest birakma);
                            kullanicinin kulubunde hicbir sey otomatik tasinmaz, yalnizca new_season_notes.
      Akademi oyunculari A takim mantigina (mac, kadro, transfer hedefi, cezalar) girmez: Team.players
      yalnizca A takimdir; dogrudan Player sorgulari in_academy ile suzulur.

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
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from sqlalchemy import Float, Integer, and_, column, desc, func, or_, select, update, values
from sqlalchemy.orm.attributes import set_committed_value

import development
import finance
import fitness
import reputation
import staff as staff_rules
import transfers
import youth
from club_directory import plain_key
from cup_draw import cup_size_for
from match_engine import (
    EngineConfig,
    EventType,
    MatchEngine,
    MatchResult,
    apply_result,
    play_fixture,
    prepare_fixture,
)
from models import (
    RATING_HISTORY_SIZE,
    Competition,
    Fixture,
    FixtureStatus,
    GameMode,
    GameState,
    League,
    LineupStatus,
    Player,
    PlayerMatchStat,
    Position,
    SquadRole,
    Staff,
    StaffRole,
    Team,
    TournamentStatus,
)
from name_masking import resolve_masked_club
from ratings import ENGINE_ATTRIBUTES
from schedule import build_round_robin
from tactics import FORMATIONS, LineupCheck, pick_bench, pick_best_xi, validate_lineup
from tournament_manager import (
    CUP_SHORT_NAME,
    CUP_YELLOW_BAN_EVERY,
    TournamentManager,
    matchday_label,
)
from transfers import ContractOffer, TransferError

# ===========================================================================
# 1) SAF KURALLAR (DB bilmez, birim testi kolay)
# ===========================================================================

NEUTRAL_RATING = 6.5            # bu notun ustu iyi, alti kotu performans
MAX_FORM_SWING = 12             # tek macta form en fazla bu kadar degisir
MAX_MORALE_SWING = 12
BENCH_FORM_DRIFT = 2            # oynamayan oyuncunun formu 50'ye dogru kayar (1. hafta)
MAX_IDLE_DRIFT = 6              # ritim kaybi haftalar gectikce buyur, bu kadarla sinirli
IDLE_MORALE_AFTER_WEEKS = 3     # bu kadar hafta oynamayan mutsuzlasir
GOOD_RATING = 7.0               # bu ve ustu: iyi mac
BAD_RATING = 6.0                # bunun alti: kotu mac
RESULT_MORALE = {"W": 3, "D": 0, "L": -3}
RESULT_FORM = {"W": 1, "D": 0, "L": -1}
YELLOW_BAN_EVERY = 4            # her 4 sari kart = 1 mac ceza
STRAIGHT_RED_LONG_BAN_CHANCE = 0.4   # direkt kirmizida %40 ihtimalle 3 mac (siddet), aksi 1 mac
# (hafta, agirlik): cogu sakatlik kisa, nadiren sezonu bitiren
INJURY_TABLE: list[tuple[int, int]] = [(1, 45), (2, 25), (3, 15), (4, 5), (6, 5), (10, 5)]

# --- AI transfer pazari ---
AI_TRANSFER_CHANCE = 0.30       # bir AI kulubun o hafta pazara cikma olasiligi
AI_MAX_DEALS_PER_WEEK = 2       # tum ligler toplaminda haftalik tamamlanan transfer siniri
AI_MIN_TARGET_SCORE = 1.5       # bu puanin altindaki hedefe teklif yapilmaz

# --- Altyapi akademisi (10. Asama) ---
SENIOR_SQUAD_MAX = 25           # A takim kadrosu en fazla
ACADEMY_CAPACITY = 20           # akademi (U-21) en fazla
ACADEMY_MAX_AGE = 21            # bu yasin ustu akademide "yas ustu" sayilir
ACADEMY_OVERAGE_SLOTS = 3       # akademide 22+ yas icin kontenjan
YOUTH_INTAKE_SIZE = (3, 4)      # sezonluk genc girisi (kulup basina)
MIN_SENIOR_KEEPERS = transfers.POSITION_SALE_FLOOR[Position.GK]     # A takimda en az 2 kaleci
AI_MIN_SENIOR_SQUAD = 16        # AI kulubu A takimi bunun altindaysa akademiden yukseltir
AI_OVERAGE_PROMOTE_MARGIN = 3   # AI: yas ustu fazlasi mevkisinin en zayifindan en fazla bu kadar geride ise yukselir


def clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(value))))


def form_delta(rating: float, outcome: str | None = None) -> int:
    """
    Mac notuna gore form degisimi (+ kazanan takima kucuk bonus).
    8.5 -> +8, 7.0 -> +2, 6.5 -> 0, 6.0 -> -2, 5.0 -> -6.
    """
    bonus = RESULT_FORM[outcome] if outcome else 0
    return clamp((rating - NEUTRAL_RATING) * 4 + bonus, -MAX_FORM_SWING, MAX_FORM_SWING)


def morale_delta(rating: float | None, outcome: str) -> int:
    """
    Moral degisimi = kisisel performans + takim sonucu.
        not >= 7.0 : +3 ve ustu (ne kadar iyi, o kadar fazla)
        not <  6.0 : -4 ve alti  -> galibiyette bile net dusus
        arasi      : sadece sonuc etkisi (G +3, B 0, M -3)
    rating None ise oyuncu oynamamistir; sadece sonuc etkisinin yarisini alir.
    """
    result = RESULT_MORALE[outcome]
    if rating is None:
        return round(result / 2)
    if rating >= GOOD_RATING:
        perf = 3 + round((rating - GOOD_RATING) * 2)
    elif rating < BAD_RATING:
        perf = -4 - round((BAD_RATING - rating) * 2)
    else:
        perf = 0
    return clamp(perf + result, -MAX_MORALE_SWING, MAX_MORALE_SWING)


def bench_form_drift(form: int, weeks_idle: int = 1) -> int:
    """
    Oynamayan oyuncunun formu notre (50) dogru kayar: mac ritmi kaybi.
    Kademeli: 1. hafta 2, 2. hafta 3, 3. hafta 4 ... en fazla MAX_IDLE_DRIFT.
    """
    step = min(BENCH_FORM_DRIFT + max(0, weeks_idle - 1), MAX_IDLE_DRIFT)
    if form > 50:
        return -min(step, form - 50)
    if form < 50:
        return min(step, 50 - form)
    return 0


def idle_morale_penalty(weeks_idle: int) -> int:
    """Uzun sure oynamayan oyuncu mutsuzlasir."""
    return -1 if weeks_idle >= IDLE_MORALE_AFTER_WEEKS else 0


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
class DevelopmentNote(PlayerNote):
    """Kullanicinin oyuncusunun haftalik guc degisimi (gelisim ya da yaslanma)."""
    age: int = 0
    old_overall: int = 0
    new_overall: int = 0
    potential_low: int | None = None         # gozlemci tahmini (yaslanmada None)
    potential_high: int | None = None
    in_academy: bool = False


@dataclass
class YouthIntakeNote(PlayerNote):
    """Kullanicinin akademisine katilan genc (potansiyel gozlemci tahminidir)."""
    age: int = 0
    position: str = ""
    overall: int = 0
    potential_low: int = 0
    potential_high: int = 0
    wonderkid: bool = False                  # tahmini potansiyel ortasina gore


@dataclass
class WeekReport:
    season: int
    week: int
    results: list[tuple[Fixture, MatchResult]] = field(default_factory=list)
    injuries: list[PlayerNote] = field(default_factory=list)
    suspensions: list[PlayerNote] = field(default_factory=list)
    user_result: MatchResult | None = None
    season_finished: bool = False
    lineup_notes: list[str] = field(default_factory=list)   # kullanicinin takimi icin asistan notlari
    transfers: list[TransferNews] = field(default_factory=list)
    finance_note: str | None = None                        # kullanicinin takimi icin maas ozeti
    # Menajer tanınırlığı (once, sonra). Kullanici takimi oynamadiysa None.
    manager_reputation: tuple[float, float] | None = None
    season_reputation_delta: float | None = None            # sezon bu hafta bittiyse
    # --- Devler Arenasi (8. Asama) ---
    cup_label: str | None = None                            # "Devler Arenası · Son 16 ilk maç"
    cup_results: list[tuple[Fixture, MatchResult]] = field(default_factory=list)
    cup_notes: list[str] = field(default_factory=list)      # tur atlayanlar, kura, sampiyon
    user_cup_result: MatchResult | None = None
    cup_champion: Team | None = None
    # --- Canli mac (9. Asama) ---
    midweek_only: bool = False          # play_midweek: yalnizca hafta ici kupa, hafta ilerlemedi
    # --- Gelisim ve altyapi (10. Asama) ---
    development_notes: list[PlayerNote] = field(default_factory=list)   # DevelopmentNote: kullanicinin oyunculari
    youth_intake: list[PlayerNote] = field(default_factory=list)        # YouthIntakeNote: kullanicinin yeni gencleri
    youth_intake_total: int = 0                                         # bu hafta tum kuluplere gelen genc sayisi
    academy_notes: list[str] = field(default_factory=list)              # kullanicinin akademisi: kapasite vb.

    @property
    def played_any(self) -> bool:
        return bool(self.results or self.cup_results)


@dataclass
class LivePreparation:
    """
    Canli oynanacak kariyer maci (prepare_live_match). Yalnizca id ve duz degerler tutar:
    arayuz veritabani oturumunu kapattiktan sonra da guvenle saklanabilir. Motor ORM bilmez.
    midweek_report: lig macindan once bekleyen hafta ici kupa maclari oynatildiysa onun raporu.
    """
    fixture_id: int
    competition: Competition
    engine: MatchEngine
    season: int
    week: int
    title: str                          # "Lig · 3. hafta · A - B" / "Devler Arenası · Final · A - B"
    managed_team_id: int
    midweek_report: WeekReport | None = None


@dataclass
class TransferNews:
    player_name: str
    from_team: str
    to_team: str
    fee: int
    wage: int

    def describe(self) -> str:
        return (f"{self.player_name}: {self.from_team} → {self.to_team} "
                f"({finance.format_money(self.fee)}, {finance.format_money(self.wage)}/hafta)")


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


class LiveMatchError(ValueError):
    """Canli kariyer maci hazirlanamadi / kaydedilemedi (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


class AcademyError(ValueError):
    """Akademi / A takim kadro kurali ihlali (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


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
        # start_new_season: kullanicinin akademisi icin notlar (otomatik tasima yok)
        self.new_season_notes: list[str] = []

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

    @property
    def manager_reputation(self) -> float:
        return self.state.manager_reputation

    # ------------------------------------------------------------------ oyun modu

    @property
    def tournaments(self) -> TournamentManager:
        if getattr(self, "_tournaments", None) is None:
            self._tournaments = TournamentManager(self)
        return self._tournaments

    @property
    def mode_chosen(self) -> bool:
        return self.state.game_mode is not None

    @property
    def game_mode(self) -> GameMode:
        """Secilmemisse kariyer modu gibi davranilir (CLI ve eski kayitlar icin)."""
        return self.state.game_mode or GameMode.CAREER

    def can_change_mode(self) -> bool:
        """Mod yalnizca sezon basinda, hicbir mac (lig/kupa) oynanmamisken degisebilir."""
        played = self.db.scalar(
            select(func.count()).select_from(Fixture).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.PLAYED
            )
        )
        return self.current_week == 1 and not played

    def set_game_mode(self, mode: GameMode) -> None:
        """
        Modu kaydeder, sezonun turnuvasini hazirlar ve kupa takvimini moda gore kurar
        (kariyer: lig haftalarina yayilir, turnuva: 1. haftadan itibaren her hafta).
        Turnuva modunda kullanicinin takimi katilimci degilse takim secimi sifirlanir.
        """
        st = self.state
        if st.game_mode is mode:
            return
        if not self.can_change_mode():
            raise ValueError("Sezon başladıktan sonra oyun modu değiştirilemez.")
        st.game_mode = mode
        t = self.tournaments.ensure()
        if t is not None:
            self.tournaments.refresh_calendar(t)
        if mode is GameMode.TOURNAMENT and not self.tournaments.is_participant(t, st.user_team_id):
            st.user_team_id = None
            st.user_team = None
        self.db.flush()

    def reset_game_mode(self) -> None:
        """Mod secim ekranina don (sadece sezon basinda)."""
        if not self.can_change_mode():
            raise ValueError("Sezon başladıktan sonra oyun modu değiştirilemez.")
        self.state.game_mode = None
        self.db.flush()

    def manager_reputation_for(self, team: Team) -> float:
        """Kullanicinin takimi icin gercek tanınırlık; AI kulupleri icin itibardan turetilen."""
        if team.id == self.state.user_team_id:
            return self.state.manager_reputation
        return reputation.ai_manager_reputation(team.reputation)

    def set_user_team(self, team: Team) -> None:
        self.state.user_team_id = team.id
        self.state.user_team = team
        self.db.flush()

    def find_team(self, name: str) -> Team | None:
        """
        Takimi adiyla bulur: once birebir, sonra harf buyuklugu ve aksandan bagimsiz,
        en son gercek kulup adiyla ("Galatasaray" -> maskeli "Istanbul Lions").
        (Postgres lower() ile Python lower() "İ" harfinde ayrisir; karsilastirma Python'da yapilir.)
        """
        name = (name or "").strip()
        exact = self.db.scalar(select(Team).where(Team.name == name))
        if exact is not None:
            return exact
        teams = list(self.db.scalars(select(Team)))
        key = plain_key(name)
        found = next((t for t in teams if plain_key(t.name) == key), None)
        if found is not None:
            return found
        masked = resolve_masked_club(name)
        if masked is None:
            return None
        masked_key = plain_key(masked)
        return next((t for t in teams if plain_key(t.name) == masked_key), None)

    # ------------------------------------------------------------------ sorgular

    def leagues(self) -> list[League]:
        return list(self.db.scalars(select(League).order_by(League.id)))

    def teams(self) -> list[Team]:
        return list(self.db.scalars(select(Team).order_by(Team.league_id, Team.name)))

    def fixtures_for_week(self, week: int | None = None, league_id: int | None = None) -> list[Fixture]:
        week = self.current_week if week is None else week
        stmt = (
            select(Fixture)
            .where(Fixture.season == self.season, Fixture.week == week,
                   Fixture.competition == Competition.LEAGUE)
            .order_by(Fixture.league_id, Fixture.id)
        )
        if league_id is not None:
            stmt = stmt.where(Fixture.league_id == league_id)
        return list(self.db.scalars(stmt))

    def league_weeks(self) -> int:
        """Bu sezonun lig fiksturundeki son hafta."""
        return self.db.scalar(
            select(func.max(Fixture.week)).where(
                Fixture.season == self.season, Fixture.competition == Competition.LEAGUE
            )
        ) or 0

    def total_weeks(self) -> int:
        """Sezonun son haftasi: kariyerde lig ve kupanin en gec biteni, turnuva modunda kupa."""
        cup_weeks = self.tournaments.last_week(self.tournaments.current())
        if self.game_mode is GameMode.TOURNAMENT:
            return cup_weeks
        return max(self.league_weeks(), cup_weeks)

    def next_fixture(self, team_id: int) -> Fixture | None:
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.UNPLAYED,
                Fixture.competition == Competition.LEAGUE,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(Fixture.week)
            .limit(1)
        )
        return self.db.scalar(stmt)

    def next_cup_fixture(self, team_id: int) -> Fixture | None:
        """Takimin bu sezon oynanmamis ilk kupa maci (kura bitmediyse yok)."""
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.UNPLAYED,
                Fixture.competition == Competition.CUP,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(Fixture.week, Fixture.id)
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
                Fixture.season == self.season, Fixture.status == FixtureStatus.PLAYED,
                Fixture.competition == Competition.LEAGUE,
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
                Fixture.competition == Competition.LEAGUE,
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
            .where(Fixture.season == self.season, Fixture.competition == Competition.LEAGUE)
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
    def cup_finished(self) -> bool:
        t = self.tournaments.current()
        return t is None or t.status is TournamentStatus.FINISHED

    @property
    def league_finished(self) -> bool:
        remaining = self.db.scalar(
            select(func.count()).select_from(Fixture).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.UNPLAYED,
                Fixture.competition == Competition.LEAGUE,
            )
        )
        return remaining == 0

    @property
    def season_finished(self) -> bool:
        """Kariyer: lig ve kupa bitti. Turnuva modu: kupa bitti."""
        if self.game_mode is GameMode.TOURNAMENT:
            return self.cup_finished
        return self.league_finished and self.cup_finished

    def champion(self, league_id: int) -> Team | None:
        if not self.season_finished:
            return None
        table = self.standings(league_id)
        return table[0] if table else None

    # ------------------------------------------------------------------ ana dongu

    def match_seed(self, fx: Fixture) -> int | None:
        """
        Fiksturun mac tohumu (lig ve kupa, otomatik ve canli ayni): tekrar uretilebilirlik.
        Tohumsuz kariyerde diger maclar rastgeledir, ama KULLANICININ maci fikstur ve sezona bagli
        sabit bir tohum alir: canli maci sayfayi yenileyip (ya da otomatik oynatip) bastan zar
        atarak tekrarlamak ayni kadro ve talimatlarla ayni maci verir.
        """
        if self.seed is not None:
            return self.seed * 10_000 + fx.id
        user_id = self.state.user_team_id
        if user_id is not None and fx.involves(user_id):
            return zlib.crc32(f"{self.season}|{fx.id}|{user_id}".encode())
        return None

    def _pending_league_fixtures(self, week: int) -> list[Fixture]:
        """Bu haftanin oynanmamis lig maclari (turnuva modunda lig yok)."""
        if self.game_mode is GameMode.TOURNAMENT:
            return []
        return [f for f in self.fixtures_for_week(week) if not f.is_played]

    @staticmethod
    def _team_ids(fixtures: Iterable[Fixture]) -> set[int]:
        return {team_id for f in fixtures for team_id in (f.home_team_id, f.away_team_id)}

    def play_week(self, live_results: Mapping[int, MatchResult] | None = None) -> WeekReport:
        """
        Mevcut haftayi oynatir ve ilerletir.
            1) Devler Arenasi maci varsa (hafta ici) -- kura bitmemisse otomatik cekilir;
               play_midweek ile zaten oynandiysa bu adim bos gecer
            2) Tum liglerin maclari (hafta sonu) -- turnuva modunda yok
            3) Maaslar ve AI transfer penceresi -- turnuva modunda yok
        live_results: fikstur id -> canli oynanmis BITMIS mac sonucu (9. Asama). Bu fiksturler simule
        edilmez; kalicilik otomatik yolla aynidir. Gecersiz girdi -> LiveMatchError, hicbir sey yazilmaz.
        """
        live = self._checked_live_results(live_results, allow_league=True)
        week = self.current_week
        report = WeekReport(season=self.season, week=week)
        tournament_mode = self.game_mode is GameMode.TOURNAMENT
        cup = self.tournaments
        t = cup.ensure()

        fixtures = self._pending_league_fixtures(week)
        cup_due = cup.matchday_due(t, week)
        if not fixtures and not cup_due:
            report.season_finished = self.season_finished
            return report

        user_team_id = self.state.user_team_id
        if cup_due:
            cup.play_matchday(week, report, self._team_ids(fixtures), live)

        # Bu hafta cezali olarak oturanlar: mac sonrasi sayaclari 1 azalacak (akademidekiler cezasini A takimda ceker)
        suspended_before = set(
            self.db.scalars(select(Player.id).where(Player.suspended_matches > 0,
                                                    Player.in_academy.is_(False)))
        ) if fixtures else set()

        for fx in fixtures:
            result = live.pop(fx.id, None)
            if result is None:
                result = play_fixture(
                    self.db, fx.id, seed=self.match_seed(fx), persist=True,
                    config=self.engine_config, current_week=week,
                )
            else:
                apply_result(fx, result, update_table=True)     # canli mac: ayni kalicilik
            self._post_match(fx, result, week, report)
            if user_team_id is not None and fx.involves(user_team_id):
                report.user_result = result
                mine = result.home if result.home.id == user_team_id else result.away
                report.lineup_notes = list(mine.lineup_notes)
            report.results.append((fx, result))

        self._require_consumed(live)
        self._decrement_suspensions(suspended_before)
        if not tournament_mode:
            # Gelisim/yaslanma ve genc girisi: turnuva modunda yok. Kendi RNG'leri var (cm.rng'ye dokunmaz).
            self._weekly_development(week, report)
            self._youth_intake(week, report)
            self._pay_weekly_wages(report)
            report.transfers = self.run_ai_transfer_window()
        self.state.current_week = week + 1
        self.db.flush()
        report.season_finished = self.season_finished
        self._update_manager_reputation(report)
        return report

    def play_midweek(self, live_results: Mapping[int, MatchResult] | None = None) -> WeekReport:
        """
        Yalnizca bu haftanin Devler Arenasi mac gununu oynatir (hafta ici; kura bekliyorsa cekilir).
        Lig maci, maas, transfer ve hafta ilerletme YOK: hafta play_week ile tamamlanir, onun kupa
        adimi o zaman bos gecer. Ayni hafta lig maci olan takimlar play_week'teki gibi hesaplanir
        (hafta ici yarim toparlanma). live_results yalnizca bu haftanin kupa fiksturleri olabilir.
        """
        live = self._checked_live_results(live_results, allow_league=False)
        week = self.current_week
        report = WeekReport(season=self.season, week=week, midweek_only=True)
        cup = self.tournaments
        t = cup.ensure()
        if cup.matchday_due(t, week):
            league_team_ids = self._team_ids(self._pending_league_fixtures(week))
            cup.play_matchday(week, report, league_team_ids, live)
        self._require_consumed(live)
        report.season_finished = self.season_finished
        return report

    # ------------------------------------------------------------------ canli mac (9. Asama)

    def live_fixture(self) -> tuple[Fixture, Competition] | None:
        """
        Kullanicinin bu haftaki siradaki oynanmamis maci: once hafta ici kupa, sonra lig
        (turnuva modunda yalnizca kupa). Takim yoksa ya da bu hafta maci kalmadiysa None.
        Salt sorgu: kura henuz cekilmediyse kupa fiksturu yoktur (prepare_live_match kurayi
        otomatik haftadaki gibi tamamlar ve kupa macini sunar).
        """
        user_id = self.state.user_team_id
        if user_id is None:
            return None
        week = self.current_week
        cup = self.tournaments
        t = cup.current()
        if cup.matchday_due(t, week):
            fx = next((f for f in cup.fixtures(t, week=week)
                       if f.involves(user_id) and not f.is_played), None)
            if fx is not None:
                return fx, Competition.CUP
        if self.game_mode is GameMode.CAREER:
            fx = next((f for f in self.fixtures_for_week(week)
                       if f.involves(user_id) and not f.is_played), None)
            if fx is not None:
                return fx, Competition.LEAGUE
        return None

    def live_cup_draw_pending(self) -> bool:
        """
        Kullanicinin bu hafta kupa maci var ama kura henuz cekilmedi mi? (live_fixture bu durumda
        kupa fiksturunu goremez; prepare_live_match kurayi tamamlayip kupa macini sunar.)
        """
        user_id = self.state.user_team_id
        if user_id is None:
            return False
        cup = self.tournaments
        t = cup.current()
        return (cup.matchday_due(t, self.current_week) and t.status is TournamentStatus.DRAW
                and cup.is_participant(t, user_id))

    def prepare_live_match(self, config: EngineConfig | None = None) -> LivePreparation:
        """
        Kullanicinin bu haftaki siradaki macini canli oynatmak icin motoru kurar (OYNATMAZ).
        Parametreler otomatik yolla aynidir: tohum match_seed, ayar (config yoksa kariyer ayari),
        mevcut hafta; kupada eleme kurali, tarafsiz saha ve kupa cezalari (prepare_cup_engine).
        Lig maci icin bu haftanin kupa maclari hala bekliyorsa ONCE play_midweek oynatilir
        (raporu midweek_report). Bunun disinda yazilan tek sey, otomatik haftanin da ilk isi olan
        turnuva kurulumu (ensure) ve kullanicinin kupa maci kuraya bagliysa kuranin tamamlanmasidir
        (kura tohumludur: otomatik haftayla ayni eslesmeler).
        """
        user_id = self.state.user_team_id
        if user_id is None:
            raise LiveMatchError("Canlı maç için önce yöneteceğin takımı seç.")
        if self.season_finished:
            raise LiveMatchError("Sezon bitti; canlı oynanacak maç yok. Yeni sezonu başlat.")

        week = self.current_week
        cup = self.tournaments
        t = cup.ensure()
        if (cup.matchday_due(t, week) and t.status is TournamentStatus.DRAW
                and cup.is_participant(t, user_id)):
            cup.draw_all()

        pending = self.live_fixture()
        if pending is None:
            raise LiveMatchError(
                "Bu hafta oynayacağın maç kalmadı; haftayı tamamlamak için sonraki haftayı oyna."
            )
        fx, competition = pending
        home, away = fx.home_team.name, fx.away_team.name

        if competition is Competition.CUP:
            engine = cup.prepare_cup_engine(fx, week, config)
            title = f"{CUP_SHORT_NAME} · {matchday_label(cup.matchday_for_week(t, week))} · {home} - {away}"
            midweek_report = None
        else:
            # Hafta ici kupa maclari lig macindan once: toparlanma/sakatlik/ceza kadroya yansisin
            midweek_report = self.play_midweek() if cup.matchday_pending(t, week) else None
            _, engine = prepare_fixture(
                self.db, fx.id, seed=self.match_seed(fx),
                config=config if config is not None else self.engine_config,
                current_week=week,
            )
            title = f"Lig · {week}. hafta · {home} - {away}"

        return LivePreparation(
            fixture_id=fx.id, competition=competition, engine=engine, season=self.season,
            week=week, title=title, managed_team_id=user_id, midweek_report=midweek_report,
        )

    def save_live_result(self, fixture_id: int, result: MatchResult) -> WeekReport:
        """
        Arayuz kisayolu: canli oynanan maci kaydeder. Kariyer modunda kupa maci icin bu hafta
        oynanmamis lig maci varsa yalnizca hafta ici oynatilir (play_midweek; sonra lig maci
        canli oynanabilir), aksi halde hafta tamamlanir (play_week). Gecersizse LiveMatchError.
        """
        fx = self.db.get(Fixture, fixture_id)
        live = {fixture_id: result}
        if (fx is not None and fx.competition is Competition.CUP
                and self.game_mode is GameMode.CAREER
                and self._pending_league_fixtures(self.current_week)):
            return self.play_midweek(live)
        return self.play_week(live)

    def _checked_live_results(
        self, live_results: Mapping[int, MatchResult] | None, allow_league: bool
    ) -> dict[int, MatchResult]:
        """
        Canli sonuclari HICBIR SEY yazilmadan dogrular (yarim islenmis hafta olmasin); kopyasini
        dondurur, play_* kullandikca cikarir. Kurallar: fikstur var, oynanmamis, bu sezonun bu
        haftasi; sonuc ayni ev/deplasman takimlarina ait ve bitmis (son olay FULL_TIME); kupada
        mac gunu bu hafta, eleme kurali ve tarafsiz saha fiksturle ayni; lig maci icin mod kariyer,
        eleme kurali yok ve bu haftanin kupa maclari oynanmis (lig motoru hafta ici sonrasi
        kurulmus olmali). Her girdi mutlaka islenecek bir fiksture karsilik gelir.
        """
        if not live_results:
            return {}
        season, week = self.season, self.current_week
        cup = self.tournaments
        t = cup.current()
        checked: dict[int, MatchResult] = {}
        for fixture_id, result in live_results.items():
            fx = self.db.get(Fixture, fixture_id)
            if fx is None:
                raise LiveMatchError(f"Canlı maç kaydedilemedi: fikstür bulunamadı (#{fixture_id}).")
            label = f"{fx.home_team.name} - {fx.away_team.name}"
            if fx.is_played:
                raise LiveMatchError(f"{label} maçı zaten oynanmış; canlı sonuç kaydedilemez.")
            if fx.season != season or fx.week != week:
                raise LiveMatchError(
                    f"{label} bu haftanın maçı değil ({fx.season}. sezon {fx.week}. hafta; "
                    f"şu an {season}. sezon {week}. hafta). Canlı maçı yeniden hazırla."
                )
            if not isinstance(result, MatchResult):
                raise LiveMatchError(f"{label} için geçerli bir maç sonucu verilmedi.")
            if (result.home.id, result.away.id) != (fx.home_team_id, fx.away_team_id):
                raise LiveMatchError(
                    f"Canlı maç sonucu ({result.home.name} - {result.away.name}) {label} fikstürüne ait değil."
                )
            if not result.events or result.events[-1].type != EventType.FULL_TIME:
                raise LiveMatchError(f"{label} maçı henüz bitmedi; önce maçı sonuna kadar oynat.")

            if fx.competition is Competition.CUP:
                if not cup.matchday_due(t, week) or fx.tournament_id != t.id:
                    raise LiveMatchError(f"{label} kupa maçı bu hafta oynanmıyor.")
                if (result.knockout != cup.knockout_rule(fx)
                        or bool(result.neutral_venue) != bool(fx.neutral_venue)):
                    raise LiveMatchError(
                        f"{label}: canlı maç kupa kurallarıyla (toplam skor, uzatma/penaltı, "
                        f"tarafsız saha) kurulmamış; maçı yeniden hazırla."
                    )
            else:
                if not allow_league:
                    raise LiveMatchError(
                        f"{label} bir lig maçı; hafta içinde yalnızca Devler Arenası maçları oynanır."
                    )
                if self.game_mode is GameMode.TOURNAMENT:
                    raise LiveMatchError(f"Turnuva modunda lig maçı oynanmaz ({label}).")
                if result.knockout is not None or result.neutral_venue:
                    raise LiveMatchError(f"{label} bir lig maçı; eleme kuralıyla oynanan sonuç kaydedilemez.")
                if self._midweek_blocks_league(t, week):
                    raise LiveMatchError(
                        f"{label} kaydedilemez: bu haftanın Devler Arenası maçları henüz oynanmadı. "
                        f"Lig maçını hafta içi maçlarından sonra yeniden hazırla."
                    )
            checked[fixture_id] = result
        return checked

    def _midweek_blocks_league(self, t, week: int) -> bool:
        """
        Lig macinin canli sonucu, bu haftanin kupa maclari oynanmadan kaydedilemez: motoru hafta ici
        toparlanma/sakatliklardan once kurulmus olur. Turnuva henuz hic kurulmadiysa (play_week
        kuracak) ama dunya bir turnuvaya yetiyorsa da beklemede sayilir.
        """
        if t is None:
            return cup_size_for(sum(len(league.teams) for league in self.leagues())) > 0
        return self.tournaments.matchday_pending(t, week)

    @staticmethod
    def _require_consumed(live: Mapping[int, MatchResult]) -> None:
        """Dogrulama her girdinin islenecegini garanti eder; yine de kalan olursa hafta kaydedilmez."""
        if live:
            ids = ", ".join(f"#{fixture_id}" for fixture_id in sorted(live))
            raise LiveMatchError(f"Canlı maç sonucu işlenemedi (fikstür {ids}); hafta kaydedilmedi.")

    def _update_manager_reputation(self, report: WeekReport) -> None:
        """
        Kullanicinin lig maci ve (sezon bittiyse) lig sirasi tanınırlığı degistirir.
        Kupa maci ve tur atlama etkileri kupa oynanirken zaten islenmistir; report.manager_reputation
        haftanin ilk degerinden son degerine tum degisimi gosterir.
        """
        user_team_id = self.state.user_team_id
        if user_team_id is None:
            return
        if report.user_result is not None:
            self._apply_match_reputation(report.user_result, report)

        if report.season_finished and self.game_mode is not GameMode.TOURNAMENT:
            team = self.db.get(Team, user_team_id)
            table = self.standings(team.league_id)
            position = next(i for i, t in enumerate(table, start=1) if t.id == team.id)
            delta = reputation.season_delta(position, len(table))
            report.season_reputation_delta = delta
            self._apply_reputation_delta(delta, report)
        self.db.flush()

    def _apply_reputation_delta(self, delta: float, report: WeekReport) -> None:
        """Tanınırlığa degisim uygular; raporda (hafta basi, guncel) ciftini tutar."""
        st = self.state
        if st.user_team_id is None:
            return
        before = report.manager_reputation[0] if report.manager_reputation else st.manager_reputation
        st.manager_reputation = reputation.apply(st.manager_reputation, delta)
        report.manager_reputation = (before, st.manager_reputation)

    def _apply_match_reputation(self, result: MatchResult, report: WeekReport) -> None:
        """Kullanicinin oynadigi tek macin (lig ya da kupa) tanınırlık etkisi."""
        user_team_id = self.state.user_team_id
        if user_team_id is None or user_team_id not in (result.home.id, result.away.id):
            return
        mine, theirs = (result.home, result.away) if result.home.id == user_team_id else (result.away, result.home)
        goal_diff = mine.stats.goals - theirs.stats.goals
        outcome = outcome_for(mine.stats.goals, theirs.stats.goals)
        self._apply_reputation_delta(
            reputation.match_delta(outcome, mine.reputation, theirs.reputation, goal_diff), report
        )

    def _post_match(
        self,
        fx: Fixture,
        result: MatchResult,
        week: int,
        report: WeekReport,
        competition: Competition = Competition.LEAGUE,
        midweek_team_ids: Iterable[int] = (),
    ) -> None:
        """
        Mac sonrasi kalicilik. competition=CUP ise kartlar kupa cezasina yazilir.
        midweek_team_ids: ayni hafta lig maci da olan takimlar; onlarin kupa macinda oynayanlari
        yarim toparlanir (MIDWEEK_RECOVERY_SHARE) ve oynamayanlara ritim kaybi yazilmaz
        (haftanin tek "oynamadi" kaydi lig macinda dusulur).
        """
        outcomes = {
            result.home.id: outcome_for(result.home_score, result.away_score),
            result.away.id: outcome_for(result.away_score, result.home_score),
        }
        midweek_ids = set(midweek_team_ids)
        cup = competition is Competition.CUP
        for team in (result.home, result.away):
            outcome = outcomes[team.id]
            midweek = cup and team.id in midweek_ids
            share = fitness.MIDWEEK_RECOVERY_SHARE if midweek else 1.0
            orm_team = self.db.get(Team, team.id)
            assistant = self._staff_rating(orm_team, StaffRole.ASSISTANT, "man_management")
            physio = self._staff_rating(orm_team, StaffRole.PHYSIO, "physiotherapy")

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
                    coach = self._staff_rating(
                        orm_team, StaffRole.COACH, staff_rules.coach_attribute_for(p.position)
                    )
                    p.form = clamp(p.form + staff_rules.apply_training(
                        form_delta(mp.rating, outcome), coach))
                    p.morale = clamp(p.morale + staff_rules.apply_training(
                        morale_delta(mp.rating, outcome), assistant))
                    p.weeks_since_match = 0
                    # Mac sonu enerjisi bir sonraki maca kadar saglikcinin kalitesine gore toparlanir
                    p.condition = fitness.recover_condition(mp.energy, physio, share, age=p.age)
                else:
                    if not midweek:
                        p.weeks_since_match += 1
                        p.form = clamp(p.form + bench_form_drift(p.form, p.weeks_since_match))
                        p.morale = clamp(
                            p.morale + morale_delta(None, outcome) + idle_morale_penalty(p.weeks_since_match)
                        )
                    p.condition = fitness.CONDITION_MAX          # oynamadi: tam dinlendi

                if mp.injured:
                    base_weeks = injury_weeks(self.rng)
                    # Saglikcinin tedavi yetenegi sureyi kisaltir (veya uzatir)
                    weeks = staff_rules.apply_injury_multiplier(base_weeks, physio)
                    p.injured_until_week = week + weeks + 1
                    detail = f"{weeks} hafta, {p.injured_until_week}. haftada dönüyor"
                    if physio is not None and weeks != base_weeks:
                        detail += f" (sağlıkçı {base_weeks}→{weeks} hf)"
                    report.injuries.append(PlayerNote(p.id, p.name, team.name, detail))

                if mp.sent_off:
                    matches = suspension_length(self.rng, mp.second_yellow)
                    reason = "ikinci sarı" if mp.second_yellow else "direkt kırmızı"
                    if cup:
                        p.cup_suspended_matches += matches
                        reason += ", kupa"
                    else:
                        p.suspended_matches += matches
                    report.suspensions.append(PlayerNote(
                        p.id, p.name, team.name, f"{matches} maç ({reason})",
                    ))
                elif mp.yellow_cards and cup:
                    before = p.cup_yellow_cards
                    p.cup_yellow_cards = before + mp.yellow_cards
                    bans = p.cup_yellow_cards // CUP_YELLOW_BAN_EVERY - before // CUP_YELLOW_BAN_EVERY
                    if bans:
                        p.cup_suspended_matches += bans
                        report.suspensions.append(PlayerNote(
                            p.id, p.name, team.name,
                            f"{bans} maç (kupada {p.cup_yellow_cards}. sarı kart)",
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
                    if not midweek:
                        p.weeks_since_match += 1                   # sakatken de ritim kaybi
                        p.form = clamp(p.form + bench_form_drift(p.form, p.weeks_since_match))
                        p.morale = clamp(p.morale + morale_delta(None, outcome))
                    p.condition = fitness.CONDITION_MAX              # macta yoktu: tam dinlendi

    def _decrement_suspensions(self, player_ids: set[int]) -> None:
        for pid in player_ids:
            p = self.db.get(Player, pid)
            if p is not None and p.suspended_matches > 0:
                p.suspended_matches -= 1

    # ------------------------------------------------------------------ teknik heyet

    def _staff_rating(self, team: Team | None, role: StaffRole, attribute: str) -> int | None:
        """Takimdaki en iyi personelin ilgili ozelligi. Personel yoksa None."""
        if team is None:
            return None
        best = team.best_staff(role, attribute)
        return getattr(best, attribute) if best is not None else None

    def physio_rating(self, team: Team) -> int | None:
        return self._staff_rating(team, StaffRole.PHYSIO, "physiotherapy")

    def scout_rating(self, team: Team) -> int | None:
        return self._staff_rating(team, StaffRole.SCOUT, "judging_ability")

    def scout_margin(self, team: Team) -> int:
        return staff_rules.scout_margin(self.scout_rating(team))

    def free_agent_staff(self, role: StaffRole | None = None) -> list[Staff]:
        stmt = select(Staff).where(Staff.team_id.is_(None))
        if role is not None:
            stmt = stmt.where(Staff.role == role)
        return list(self.db.scalars(stmt.order_by(desc(Staff.reputation))))

    def hire_staff(self, team: Team, member: Staff) -> None:
        """Bostaki personeli ise alir. Maas havuzu yetmezse TransferError."""
        if member.employed:
            raise TransferError(f"{member.name} zaten {member.team.name} kadrosunda.")
        limit = staff_rules.MAX_PER_ROLE[member.role]
        if len(team.staff_by_role(member.role)) >= limit:
            raise TransferError(
                f"{staff_rules.ROLE_LABELS[member.role]} kadrosu dolu (en fazla {limit}). "
                f"Önce birini gönder."
            )
        if member.wage > team.free_wage:
            raise TransferError(
                f"Maaş havuzunda yer yok: {finance.format_money(member.wage)}/hafta gerekli, "
                f"{finance.format_money(team.free_wage)}/hafta boş."
            )
        member.team_id = team.id
        member.team = team
        self.db.flush()

    def release_staff(self, team: Team, member: Staff) -> None:
        """Personeli gonderir; bostaki havuza doner."""
        if member.team_id != team.id:
            raise TransferError(f"{member.name} bu kulübün personeli değil.")
        member.team_id = None
        member.team = None
        self.db.flush()

    # ------------------------------------------------------------------ finans

    def wage_summary(self, team: Team) -> finance.WageSummary:
        return finance.wage_summary(team.player_wage_bill, team.staff_wage_bill, team.wage_budget)

    def shift_budget(self, team: Team, weekly_delta: int) -> tuple[int, int]:
        """
        Butce kaydirma. weekly_delta > 0: maas havuzunu buyut (transferden 52x duser).
        Kural ihlalinde finance.BudgetError firlatir, hicbir sey degismez.
        """
        new_transfer, new_wage = finance.plan_budget_shift(
            team.transfer_budget, team.wage_budget, weekly_delta, team.wage_bill
        )
        team.transfer_budget, team.wage_budget = new_transfer, new_wage
        self.db.flush()
        return new_transfer, new_wage

    def _pay_weekly_wages(self, report: WeekReport) -> None:
        """
        Haftalik maaslar havuzdan oder; havuzla gercek yuk arasindaki fark
        transfer kasasina yansir (artan birikir, asim kasadan duser).
        """
        user_team_id = self.state.user_team_id
        for team in self.teams():
            summary = self.wage_summary(team)
            team.transfer_budget = max(0, team.transfer_budget + summary.free)
            if team.id == user_team_id:
                verb = "kasaya eklendi" if summary.free >= 0 else "kasadan düşüldü"
                report.finance_note = (
                    f"Maaşlar ödendi: {finance.format_money(summary.total)}/hafta "
                    f"(havuz {finance.format_money(team.wage_budget)}, "
                    f"%{summary.usage_pct:.0f} dolu) · "
                    f"{finance.format_money(abs(summary.free))} {verb} · "
                    f"transfer kasası: {finance.format_money(team.transfer_budget)}"
                )
        self.db.flush()

    # ------------------------------------------------------------------ transfer pazari

    def transfer_targets(self, buyer: Team, query: str = "", limit: int = 20) -> list[Player]:
        """Baska kuluplerin A takim oyunculari (isim filtresiyle), guce gore sirali. Akademiler satilik degil."""
        stmt = (
            select(Player)
            .where(Player.team_id.isnot(None), Player.team_id != buyer.id, Player.in_academy.is_(False))
            .order_by(desc(Player.overall_rating))
            .limit(limit)
        )
        if query.strip():
            stmt = stmt.where(Player.name.ilike(f"%{query.strip()}%"))
        return list(self.db.scalars(stmt))

    def scouted_report(self, buyer: Team, player: Player) -> dict:
        """
        Oyuncunun gozlemci suzgecinden gecmis profili.
        Kendi oyuncumuzsa kesin, degilse gozlemcinin yanilma payiyla aralik.
        """
        margin = 0 if player.team_id == buyer.id else self.scout_margin(buyer)
        seed = (self.scout_rating(buyer) or 0, player.id)
        attrs = ("overall_rating", "pace", "shooting", "passing",
                 "defending", "dribbling", "goalkeeping")
        report = {
            name: staff_rules.scouted_value(getattr(player, name), margin, (*seed, name))
            for name in attrs
        }
        report["market_value"] = staff_rules.scouted_money(
            player.market_value, margin, (*seed, "value")
        )
        report["margin"] = margin
        return report

    def offer_fee(self, buyer: Team, player: Player, fee: int) -> transfers.FeeDecision:
        """1. Asama: satici kulube bonservis teklifi."""
        if player.team_id == buyer.id:
            raise TransferError("Bu oyuncu zaten senin takımında.")
        if player.team is None:
            raise TransferError("Oyuncunun kulübü yok.")
        if player.in_academy:
            raise TransferError(f"{player.name} {player.team.name} akademisinde; akademi oyuncuları satılık değil.")
        if fee < 0:
            raise TransferError("Teklif negatif olamaz.")
        if not finance.can_afford_transfer(buyer.transfer_budget, fee):
            raise TransferError(
                f"Transfer bütçen yetersiz: {finance.format_money(buyer.transfer_budget)} var, "
                f"{finance.format_money(fee)} gerekiyor."
            )
        return transfers.evaluate_fee(self.rng, player, player.team, fee, buyer.reputation)

    def open_negotiation(self, buyer: Team, player: Player, fee: int) -> transfers.ContractNegotiation:
        """
        2. Asama: sozlesme masasini acar (kulup onayindan SONRA cagrilir).
        Kulup + menajer prestiji yetmezse donen pazarlik zaten kapalidir
        (negotiation.open False, negotiation.opening_message sebebi soyler).
        """
        return transfers.ContractNegotiation(
            self.rng, player, buyer, fee, manager_reputation=self.manager_reputation_for(buyer)
        )

    def complete_transfer(
        self, buyer: Team, player: Player, fee: int, offer: ContractOffer
    ) -> TransferNews:
        """
        Anlasma tamam: oyuncu takim degistirir, butceler guncellenir.
        Maas havuzu yetmiyorsa TransferError (cagiran once butce kaydirmali).
        """
        seller = player.team
        if seller is None:
            raise TransferError("Oyuncunun kulübü yok.")
        if fee > buyer.transfer_budget:
            raise TransferError("Transfer bütçesi yetersiz.")

        wage_delta = offer.wage - 0        # gelen oyuncu havuza tamamen yeni yuk ekler
        if wage_delta > buyer.free_wage:
            raise TransferError(
                f"Maaş havuzunda yer yok: {finance.format_money(offer.wage)}/hafta gerekli, "
                f"{finance.format_money(buyer.free_wage)}/hafta boş. Bütçe kaydırman gerekiyor."
            )

        buyer.transfer_budget -= fee
        seller.transfer_budget += fee

        was_academy = player.in_academy
        player.in_academy = False                       # satin alinan oyuncu A takima katilir
        player.team_id = buyer.id
        player.team = buyer
        player.current_wage = offer.wage
        player.contract_years = offer.years
        player.squad_role = offer.role
        player.last_transfer_season = self.season
        player.lineup_status = LineupStatus.BENCH
        player.lineup_role = None
        player.market_value = finance.market_value(
            player.overall_rating, player.age, player.position, player.potential_rating
        )
        self.db.flush()
        if was_academy:
            for team in (seller, buyer):
                self.db.expire(team, ["players", "academy_players"])
        return TransferNews(player.name, seller.name, buyer.name, fee, offer.wage)

    # ------------------------------------------------------------------ AI transfer pazari

    def run_ai_transfer_window(self) -> list[TransferNews]:
        """
        AI kulupleri kendi butce ve kadro ihtiyaclarina gore teklif yapar.
        Maas alani yetmezse arka planda butce kaydirir.
        """
        news: list[TransferNews] = []
        user_team_id = self.state.user_team_id
        # Bir transfer penceresinde ayni oyuncu birden fazla kez el degistiremez
        # ve bir kulup hem alip hem satamaz (aksi halde Inter->Milan->Inter gibi
        # atlikarinca olusuyordu).
        moved_players: set[int] = set()
        busy_teams: set[int] = set()

        for league in self.leagues():
            averages = transfers.league_position_average(league.teams)
            buyers = [t for t in league.teams if t.id != user_team_id]
            self.rng.shuffle(buyers)

            for buyer in buyers:
                if len(news) >= AI_MAX_DEALS_PER_WEEK:
                    return news
                if buyer.id in busy_teams:
                    continue
                if self.rng.random() >= AI_TRANSFER_CHANCE:
                    continue
                deal = self._ai_attempt_transfer(
                    buyer, league, averages, moved_players, busy_teams
                )
                if deal is not None:
                    news.append(deal)
        return news

    def _ai_attempt_transfer(
        self,
        buyer: Team,
        league: League,
        averages,
        moved_players: set[int],
        busy_teams: set[int],
    ) -> TransferNews | None:
        needs = transfers.squad_needs(buyer, averages)
        if not needs:
            return None
        need = needs[0]

        # Kullanicinin oyunculari AI tarafindan onaysiz satin alinamaz; bu sezon zaten
        # transfer edilmis oyuncu da tekrar el degistirmez.
        user_team_id = self.state.user_team_id
        candidates = [
            p for t in league.teams
            if t.id not in (buyer.id, user_team_id) and t.id not in busy_teams
            for p in t.players
            if p.position is need.position
            and p.id not in moved_players
            and p.last_transfer_season != self.season
            and p.is_available(self.current_week)
        ]
        scored = [(transfers.target_score(p, buyer, need), p) for p in candidates]
        scored = [(s, p) for s, p in scored if s >= AI_MIN_TARGET_SCORE]
        if not scored:
            return None
        scored.sort(key=lambda sp: -sp[0])
        target = scored[0][1]

        asking = transfers.asking_price(target, target.team, buyer.reputation)
        if asking > buyer.transfer_budget:
            return None
        fee = transfers.ai_opening_offer(self.rng, asking, buyer.transfer_budget)

        decision = transfers.evaluate_fee(self.rng, target, target.team, fee, buyer.reputation)
        if not decision.accepted:
            return None

        negotiation = transfers.ContractNegotiation(
            self.rng, target, buyer, fee, manager_reputation=self.manager_reputation_for(buyer)
        )
        if not negotiation.open:          # oyuncu bu kulube gelmek istemiyor
            return None

        # Once imzalanacak teklifi belirle; imza cikmayacaksa butceyi bosuna kaydirma
        offer = transfers.ai_contract_offer(
            self.rng, negotiation, max(buyer.free_wage, negotiation.demand.wage)
        )
        if negotiation.persuasion(offer) < negotiation.required_persuasion:
            return None

        # Maas alani yetmiyorsa butce kaydir (bonservisi ayirarak)
        need_weekly = offer.wage
        if need_weekly > buyer.free_wage:
            shift = finance.auto_shift_for_wage(
                buyer.transfer_budget, buyer.wage_budget, buyer.free_wage, need_weekly, fee
            )
            if shift <= 0:
                return None
            try:
                self.shift_budget(buyer, shift)
            except finance.BudgetError:
                return None
            if need_weekly > buyer.free_wage:
                return None

        response = negotiation.respond(offer)
        if response.status is not transfers.NegotiationStatus.ACCEPTED:
            return None

        seller_id = target.team_id
        try:
            deal = self.complete_transfer(buyer, target, fee, offer)
        except TransferError:
            return None
        moved_players.add(target.id)
        busy_teams.update({buyer.id, seller_id})
        return deal

    # ------------------------------------------------------------------ altyapi ve gelisim (10. Asama)

    def _youth_rng(self, kind: str, team_id: int) -> random.Random:
        """
        Genc girisi / akademi RNG'si: cm.rng dizisinden BAGIMSIZ (mac ve transfer sonuclari degismez).
        Tohumlu kariyerde tohum + sezon + takimdan turetilir; tohumsuzda rastgele.
        """
        if self.seed is None:
            return random.Random()
        return random.Random(zlib.crc32(f"{kind}|{self.seed}|{self.season}|{team_id}".encode()))

    @staticmethod
    def _backfill_potential(p: Player) -> int:
        """Eski kayit: FM oyuncusunda potential_ability, digerlerinde oyuncuya ozgu sabit tohumla yas egrisi."""
        if p.potential_ability:
            return development.potential_from_fm(p.potential_ability, p.overall_rating)
        rng = random.Random(zlib.crc32(f"potential|{p.id}".encode()))
        return development.initial_potential(rng, p.age, p.overall_rating)

    def _academy_player(self, team: Team, spec: youth.YouthSpec) -> Player:
        """youth.YouthSpec -> akademi oyuncusu (A takim koleksiyonuna eklenmez; team_id ile baglanir)."""
        return Player(
            team_id=team.id, name=spec.name, age=spec.age, position=spec.position,
            overall_rating=spec.overall, **{a: spec.attributes[a] for a in ENGINE_ATTRIBUTES},
            form=spec.form, morale=spec.morale, condition=fitness.CONDITION_MAX,
            contract_years=spec.contract_years,
            market_value=finance.market_value(spec.overall, spec.age, spec.position, spec.potential),
            current_wage=finance.academy_wage(spec.overall, team.reputation),
            squad_role=SquadRole.BACKUP, lineup_status=LineupStatus.OUT, lineup_role=None,
            data_source="academy", potential_rating=spec.potential, in_academy=True,
            development_progress=0.0, match_rating_history=[], fm_attributes={},
        )

    def ensure_youth_setup(self) -> list[str]:
        """
        Eski kayitlar ve yeni dunyalar icin IDEMPOTENT doldurma (kariyer silinmez):
            * potential_rating NULL olan her oyuncu: FM'de potential_ability, aksi halde oyuncuya ozgu
              sabit tohumla (id) yas egrisi
            * youth_facilities NULL olan her kulup: itibardan + kulube ozgu sabit sapma
            * game_state.academy_seeded degilse: akademisi olmayan her kulube 4-6 kisilik baslangic akademisi
        Yapilanlari anlatan Turkce mesajlar dondurur (bir sey yapilmadiysa bos liste).
        """
        messages: list[str] = []
        self.db.flush()
        # Es zamanli iki giris ayni kariyeri ayni anda doldurmasin (akademiler iki kez kurulurdu):
        # game_state satiri islem sonuna kadar kilitlenir, bekleyen islem guncel satiri okur.
        st = self.db.get(GameState, 1, with_for_update=True, populate_existing=True) or self.state

        missing = list(self.db.scalars(
            select(Player).where(Player.potential_rating.is_(None)).order_by(Player.id)
        ))
        for p in missing:
            p.potential_rating = self._backfill_potential(p)
        if missing:
            messages.append(f"{len(missing)} oyuncuya potansiyel atandı.")

        no_facilities = list(self.db.scalars(
            select(Team).where(Team.youth_facilities.is_(None)).order_by(Team.id)
        ))
        for team in no_facilities:
            rng = random.Random(zlib.crc32(f"facilities|{team.id}".encode()))
            team.youth_facilities = youth.default_facilities(team.reputation, rng)
        if no_facilities:
            messages.append(f"{len(no_facilities)} kulübe altyapı tesisi puanı verildi.")

        if not st.academy_seeded:
            self.db.flush()
            used_names = set(self.db.scalars(select(Player.name)))
            clubs = created = 0
            for team in self.teams():
                if self._academy_size(team) > 0:
                    continue
                rng = self._youth_rng("academy", team.id)
                count = rng.randint(*youth.INITIAL_ACADEMY_SIZE)
                specs = youth.generate_academy(rng, team.league.country, team.youth_facilities,
                                               team.reputation, count, used_names)
                self.db.add_all(self._academy_player(team, spec) for spec in specs)
                clubs += 1
                created += len(specs)
                self.db.flush()
                self.db.expire(team, ["academy_players"])
            st.academy_seeded = True
            if created:
                messages.append(f"{clubs} kulübe toplam {created} oyunculuk başlangıç akademisi (U-21) kuruldu.")
        self.db.flush()
        return messages

    def academy_players(self, team: Team) -> list[Player]:
        """Kulubun U-21 akademisi: potansiyel (azalan), guc, id sirasiyla. Veritabanindan taze okunur."""
        self.db.flush()
        return list(self.db.scalars(
            select(Player)
            .where(Player.team_id == team.id, Player.in_academy.is_(True))
            .order_by(Player.potential_rating.desc().nulls_last(), Player.overall_rating.desc(), Player.id)
        ))

    def _senior_players(self, team: Team) -> list[Player]:
        self.db.flush()
        return list(self.db.scalars(
            select(Player)
            .where(Player.team_id == team.id, Player.in_academy.is_(False))
            .order_by(Player.overall_rating.desc(), Player.id)
        ))

    def _academy_size(self, team: Team) -> int:
        return self.db.scalar(select(func.count()).select_from(Player).where(
            Player.team_id == team.id, Player.in_academy.is_(True))) or 0

    def youth_intake_week(self) -> int:
        """
        Genc girisinin yapildigi hafta: sezonun son haftasindan bir onceki (en az 1). Sezonun ilk haftasi
        oynanmadan (turnuva henuz kurulmamisken) de ayni degeri verir: kupa takvimi varsayilan formatla
        ongorulur (tournaments.projected_last_week, yan etkisiz).
        """
        return max(1, self._projected_season_weeks() - 1)

    def _projected_season_weeks(self) -> int:
        """total_weeks gibi; turnuva henuz kurulmadiysa kupa takvimi ongorulur."""
        cup_weeks = self.tournaments.projected_last_week()
        if self.game_mode is GameMode.TOURNAMENT:
            return cup_weeks
        return max(self.league_weeks(), cup_weeks)

    def _refresh_squads(self, team: Team) -> None:
        self.db.flush()
        self.db.expire(team, ["players", "academy_players"])

    @staticmethod
    def _check_owner(team: Team, player: Player) -> None:
        if player.team_id != team.id:
            raise AcademyError(f"{player.name} {team.name} oyuncusu değil.")

    def promote_to_senior(self, team: Team, player: Player) -> None:
        """Akademi oyuncusunu A takima yukseltir (kulube). A takim en fazla SENIOR_SQUAD_MAX; aksi AcademyError."""
        self._check_owner(team, player)
        if not player.in_academy:
            raise AcademyError(f"{player.name} zaten A takım kadrosunda.")
        seniors = self._senior_players(team)
        if len(seniors) >= SENIOR_SQUAD_MAX:
            raise AcademyError(
                f"A takım kadrosu dolu (en fazla {SENIOR_SQUAD_MAX} oyuncu). "
                f"{player.name} için önce bir oyuncuyu akademiye gönder ya da sat."
            )
        player.in_academy = False
        player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        self._refresh_squads(team)

    def send_to_academy(self, team: Team, player: Player) -> None:
        """
        A takim oyuncusunu U-21 akademisine gonderir (kadro disi, ilk 11'den cikar).
        Kurallar: A takimda en az transfers.SQUAD_FLOOR oyuncu ve MIN_SENIOR_KEEPERS kaleci kalir;
        akademi en fazla ACADEMY_CAPACITY; 21 yas ustu icin ACADEMY_OVERAGE_SLOTS kontenjan. Aksi AcademyError.
        """
        self._check_owner(team, player)
        if player.in_academy:
            raise AcademyError(f"{player.name} zaten akademide.")
        remaining = [p for p in self._senior_players(team) if p.id != player.id]
        if len(remaining) < transfers.SQUAD_FLOOR:
            raise AcademyError(
                f"A takım kadrosu {transfers.SQUAD_FLOOR} oyuncunun altına düşemez; "
                f"{player.name} akademiye gönderilemez."
            )
        if (player.position is Position.GK
                and sum(1 for p in remaining if p.position is Position.GK) < MIN_SENIOR_KEEPERS):
            raise AcademyError(
                f"A takımda en az {MIN_SENIOR_KEEPERS} kaleci kalmalı; {player.name} akademiye gönderilemez."
            )
        academy = self.academy_players(team)
        if len(academy) >= ACADEMY_CAPACITY:
            raise AcademyError(f"Akademi dolu (en fazla {ACADEMY_CAPACITY} oyuncu).")
        if (player.age > ACADEMY_MAX_AGE
                and sum(1 for p in academy if p.age > ACADEMY_MAX_AGE) >= ACADEMY_OVERAGE_SLOTS):
            raise AcademyError(
                f"Akademide {ACADEMY_MAX_AGE} yaş üstü kontenjanı dolu (en fazla {ACADEMY_OVERAGE_SLOTS} oyuncu); "
                f"{player.name} ({player.age}) akademiye gönderilemez."
            )
        player.in_academy = True
        player.lineup_status, player.lineup_role = LineupStatus.OUT, None
        self._refresh_squads(team)

    def potential_scout_rating(self, team: Team) -> int | None:
        return self._staff_rating(team, StaffRole.SCOUT, "judging_potential")

    def potential_estimate(self, team: Team, player: Player) -> tuple[int, int]:
        """
        Izleyen kulubun gozlemcisine (judging_potential) gore potansiyel araligi (dusuk, yuksek).
        Kendi oyunculari icin de sislidir (tavan kesin bilinemez); gozlemci 18+ ise kesin.
        Ayni gozlemci + ayni oyuncu icin her zaman ayni aralik; gercek deger her zaman araliktadir.
        Kendi oyuncusunda alt sinir oyuncunun (kesin bilinen) gucunun altina inmez.
        """
        judging = self.potential_scout_rating(team)
        margin = development.potential_scout_margin(judging)
        true_potential = development.effective_potential(player.overall_rating, player.potential_rating)
        value = staff_rules.scouted_value(true_potential, margin, (judging or 0, player.id, "potential"))
        low, high = value.low, value.high
        if player.team_id == team.id:
            low = max(low, player.overall_rating)
            high = max(high, low)
        return low, high

    def academy_warnings(self, team: Team) -> list[str]:
        """Kullanicinin akademisi icin guncel uyarilar (yas ustu fazlasi, A takima hazir gencler, kadro siniri)."""
        academy = self.academy_players(team)
        seniors = self._senior_players(team)
        notes: list[str] = []
        overage = [p for p in academy if p.age > ACADEMY_MAX_AGE]
        if len(overage) > ACADEMY_OVERAGE_SLOTS:
            names = ", ".join(f"{p.name} ({p.age})" for p in overage)
            notes.append(
                f"Akademide {ACADEMY_MAX_AGE} yaş üstü {len(overage)} oyuncu var, kontenjan {ACADEMY_OVERAGE_SLOTS}: "
                f"{names}. Fazlasını A takıma yükselt."
            )
        for p in academy:
            group = [s.overall_rating for s in seniors if s.position is p.position]
            if group and p.overall_rating >= min(group):
                notes.append(f"{p.name} ({p.age}, {p.position.value}) A takıma hazır görünüyor: "
                             f"mevkisindeki en zayıf oyuncudan geri değil.")
        if len(seniors) > SENIOR_SQUAD_MAX:
            notes.append(f"A takım kadrosu {len(seniors)} oyuncu; sınır {SENIOR_SQUAD_MAX}. "
                         f"Yeni oyuncu yükseltmek için kadroyu daralt.")
        return notes

    # ---- haftalik gelisim

    def _week_minutes(self, week: int) -> dict[int, tuple[int, float | None]]:
        """Bu haftanin (lig + kupa, hafta ici dahil) oyuncu basina toplam dakika ve ortalama not."""
        self.db.flush()
        rows = self.db.execute(
            select(PlayerMatchStat.player_id, func.sum(PlayerMatchStat.minutes), func.avg(PlayerMatchStat.rating))
            .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
            .where(Fixture.season == self.season, Fixture.week == week)
            .group_by(PlayerMatchStat.player_id)
        )
        return {pid: (int(minutes or 0), float(avg) if avg is not None else None) for pid, minutes, avg in rows}

    def _weekly_development(self, week: int, report: WeekReport) -> None:
        """
        Haftalik gelisim ve yaslanma (kariyer modu). Deterministik: RNG kullanmaz.
        Yalnizca degisebilecek oyuncular okunur: 32+ (gerileme) ya da 30 alti ve potansiyeli gucunden yuksek.
        Guc degisirse ozellikler, potansiyel (gerilemede) ve piyasa degeri guncellenir; kullanicinin
        oyunculari icin DevelopmentNote yazilir.
        """
        if self.game_mode is GameMode.TOURNAMENT:
            return
        played = self._week_minutes(week)
        season_weeks = self._projected_season_weeks()
        teams = {t.id: t for t in self.teams()}
        coaches = {tid: self._staff_rating(t, StaffRole.COACH, "working_with_youngsters") for tid, t in teams.items()}
        user_id = self.state.user_team_id
        candidates = self.db.scalars(
            select(Player)
            .where(
                Player.team_id.isnot(None),
                or_(
                    Player.age >= development.DECLINE_START_AGE,
                    and_(Player.age < development.GROWTH_END_AGE,
                         func.coalesce(Player.potential_rating, Player.overall_rating) > Player.overall_rating),
                ),
            )
            .order_by(Player.id)
        ).all()
        progress_only: dict[int, tuple[Player, float]] = {}
        for p in candidates:
            team = teams.get(p.team_id)
            minutes, avg_rating = played.get(p.id, (0, None))
            growth = development.weekly_growth(
                p.age, p.overall_rating, p.potential_rating, minutes, avg_rating, p.morale,
                coaches.get(p.team_id), p.in_academy,
                team.youth_facilities if team is not None else None, season_weeks,
            )
            decline = development.weekly_decline(p.age, season_weeks)
            if growth <= 0 and decline <= 0:
                continue
            before = p.overall_rating
            step = development.apply_progress(
                p.position, p.overall_rating, p.potential_rating,
                {a: getattr(p, a) for a in ENGINE_ATTRIBUTES}, p.development_progress, growth, decline,
            )
            if step.change == 0:
                progress_only[p.id] = (p, step.progress)
                continue
            p.development_progress = step.progress
            p.overall_rating = step.overall
            for attr, value in step.attributes.items():
                setattr(p, attr, value)
            p.potential_rating = step.potential
            p.market_value = finance.market_value(p.overall_rating, p.age, p.position, p.potential_rating)
            if p.team_id == user_id and team is not None:
                report.development_notes.append(self._development_note(team, p, before))
        self._write_progress(progress_only)
        # Akademi oyunculari A takim maci oynamaz: A takimdan tasinan yorgunluk hafta icinde tamamen gecer
        self.db.execute(
            update(Player)
            .where(Player.in_academy.is_(True), Player.condition < fitness.CONDITION_MAX)
            .values(condition=fitness.CONDITION_MAX)
        )
        self.db.flush()

    def _write_progress(self, rows: Mapping[int, tuple[Player, float]]) -> None:
        """
        Yalnizca birikimi degisen oyuncular (haftada yuzlerce satir) TEK UPDATE ... FROM (VALUES ...) ile yazilir;
        ORM nesnesine 'kaydedilmis deger' olarak islenir (tekrar flush edilmez). Satir satir UPDATE
        haftayi ~%30 yavaslatiyordu.
        """
        if not rows:
            return
        table = Player.__table__
        data = values(column("id", Integer), column("progress", Float), name="dev_progress").data(
            [(pid, progress) for pid, (_p, progress) in rows.items()]
        )
        self.db.execute(
            update(table).where(table.c.id == data.c.id).values(development_progress=data.c.progress)
        )
        for player, progress in rows.values():
            set_committed_value(player, "development_progress", progress)

    def _development_note(self, team: Team, p: Player, before: int) -> DevelopmentNote:
        detail = f"({p.age}) {before} → {p.overall_rating}"
        low = high = None
        if p.overall_rating < before:
            detail += " · yaşlanma"
        else:
            low, high = self.potential_estimate(team, p)
            detail += f" · potansiyel {low if low == high else f'{low}-{high}'}"
        if p.in_academy:
            detail += " · akademi"
        return DevelopmentNote(
            p.id, p.name, team.name, detail, age=p.age, old_overall=before, new_overall=p.overall_rating,
            potential_low=low, potential_high=high, in_academy=p.in_academy,
        )

    # ---- genc girisi

    def _youth_intake(self, week: int, report: WeekReport) -> None:
        """
        Sezonda bir kez, youth_intake_week() haftasinda (kacirildiysa sonraki ilk oynanan haftada) TUM
        kuluplere YOUTH_INTAKE_SIZE genc. Kulup/sezon/tohumdan turetilmis ayri RNG. Ardindan akademi
        kapasitesi uygulanir. Kullanicinin kulubu icin YouthIntakeNote ve kapasite notlari rapora yazilir.
        """
        st = self.state
        if self.game_mode is GameMode.TOURNAMENT or st.last_youth_intake_season == self.season:
            return
        if week < self.youth_intake_week():
            return
        self.db.flush()
        used_names = set(self.db.scalars(select(Player.name)))
        user_id = st.user_team_id
        total = 0
        for team in self.teams():
            rng = self._youth_rng("intake", team.id)
            count = rng.randint(*YOUTH_INTAKE_SIZE)
            facilities = team.youth_facilities or youth.default_facilities(team.reputation)
            specs = youth.generate_intake(rng, team.league.country, facilities, team.reputation, count, used_names)
            newcomers = [self._academy_player(team, spec) for spec in specs]
            self.db.add_all(newcomers)
            total += len(newcomers)
            self.db.flush()
            released = self._enforce_academy_capacity(team, report if team.id == user_id else None)
            if team.id == user_id:
                report.youth_intake = [self._intake_note(team, p) for p in newcomers if p.id not in released]
        st.last_youth_intake_season = self.season
        report.youth_intake_total = total
        self.db.flush()

    def _intake_note(self, team: Team, p: Player) -> YouthIntakeNote:
        low, high = self.potential_estimate(team, p)
        wonderkid = development.is_wonderkid(p.age, p.overall_rating, (low + high) // 2)
        pot = str(low) if low == high else f"{low}-{high}"
        detail = f"{p.age} yaş · {p.position.value} · güç {p.overall_rating} · potansiyel {pot}"
        if wonderkid:
            detail += " · wonderkid"
        return YouthIntakeNote(
            p.id, p.name, team.name, detail, age=p.age, position=p.position.value,
            overall=p.overall_rating, potential_low=low, potential_high=high, wonderkid=wonderkid,
        )

    def _enforce_academy_capacity(self, team: Team, report: WeekReport | None = None) -> set[int]:
        """Akademi ACADEMY_CAPACITY'yi asarsa en dusuk potansiyelliler kulupten ayrilir (silinir)."""
        academy = self.academy_players(team)
        excess = len(academy) - ACADEMY_CAPACITY
        if excess <= 0:
            return set()
        ranked = sorted(academy, key=lambda p: (
            development.effective_potential(p.overall_rating, p.potential_rating), p.overall_rating, -p.age, p.id,
        ))
        released = ranked[:excess]
        names = ", ".join(f"{p.name} ({p.age})" for p in released)
        ids = {p.id for p in released}
        for p in released:
            self.db.delete(p)
        self._refresh_squads(team)
        if report is not None:
            report.academy_notes.append(
                f"Akademi kapasitesi ({ACADEMY_CAPACITY}) aşıldı; en düşük potansiyelli {len(released)} "
                f"oyuncu kulüpten ayrıldı: {names}."
            )
        return ids

    # ---- sezon basi akademi yonetimi

    def _season_academy_management(self) -> list[str]:
        """AI kulupleri akademisini yonetir; kullanicinin kulubu icin yalnizca uyari notlari dondurulur."""
        user_id = self.state.user_team_id
        notes: list[str] = []
        for team in self.teams():
            if team.id == user_id:
                notes += self.academy_warnings(team)
            else:
                self._ai_manage_academy(team)
        return notes

    def _ai_manage_academy(self, team: Team) -> None:
        """
        AI kulubu (sezon basi):
            1) A takim AI_MIN_SENIOR_SQUAD'in altindaysa en guclu gencler yukselir
            2) mevkisindeki en zayif A takim oyuncusundan iyi olan (ya da mevkide kimse yoksa) yukselir
            3) 21 yas ustu kontenjani (en yuksek potansiyelliler kalir) asan: yer varsa ve mevkisinin en
               zayifindan AI_OVERAGE_PROMOTE_MARGIN'den fazla geride degilse yukselir, aksi serbest birakilir
        A takim hicbir adimda SENIOR_SQUAD_MAX'i asmaz.
        """
        academy = self.academy_players(team)
        if not academy:
            return
        seniors = self._senior_players(team)
        established = list(seniors)          # olcu: bu cagrida yukselenler mevki tabanini dusurmesin

        def weakest(position: Position) -> int | None:
            group = [s.overall_rating for s in established if s.position is position]
            return min(group) if group else None

        def promote(p: Player) -> None:
            p.in_academy = False
            p.lineup_status, p.lineup_role = LineupStatus.BENCH, None
            seniors.append(p)
            academy.remove(p)

        def strongest_first(players: list[Player]) -> list[Player]:
            return sorted(players, key=lambda p: (
                -p.overall_rating, -development.effective_potential(p.overall_rating, p.potential_rating), p.id,
            ))

        for p in strongest_first(academy):
            if len(seniors) >= min(AI_MIN_SENIOR_SQUAD, SENIOR_SQUAD_MAX):
                break
            promote(p)
        for p in strongest_first(academy):
            if len(seniors) >= SENIOR_SQUAD_MAX:
                break
            floor = weakest(p.position)
            if floor is None or p.overall_rating > floor:
                promote(p)
        overage = sorted(
            (p for p in academy if p.age > ACADEMY_MAX_AGE),
            key=lambda p: (-development.effective_potential(p.overall_rating, p.potential_rating),
                           -p.overall_rating, p.id),
        )
        for p in overage[ACADEMY_OVERAGE_SLOTS:]:
            floor = weakest(p.position)
            if len(seniors) < SENIOR_SQUAD_MAX and (floor is None or p.overall_rating >= floor - AI_OVERAGE_PROMOTE_MARGIN):
                promote(p)
            else:
                academy.remove(p)
                self.db.delete(p)
        self._refresh_squads(team)

    # ------------------------------------------------------------------ kadro & taktik

    def lineup_of(self, team: Team) -> tuple[dict[int, Position], list[int], list[int]]:
        """(ilk 11: id -> rol, kulube id'leri, kadro disi id'leri)"""
        xi = {p.id: p.lineup_role for p in team.players
              if p.lineup_status is LineupStatus.XI and p.lineup_role is not None}
        bench = [p.id for p in team.players if p.lineup_status is LineupStatus.BENCH]
        out = [p.id for p in team.players if p.lineup_status is LineupStatus.OUT]
        return xi, bench, out

    def lineup_check(self, team: Team) -> LineupCheck:
        xi, bench, _ = self.lineup_of(team)
        return validate_lineup(team.players, team.formation, self.current_week, xi, bench)

    def set_formation(self, team: Team, name: str) -> LineupCheck:
        if name not in FORMATIONS:
            raise ValueError(f"Bilinmeyen diziliş: {name}. Seçenekler: {', '.join(FORMATIONS)}")
        team.formation = name
        self.db.flush()
        return self.lineup_check(team)

    def set_lineup(self, team: Team, xi: Mapping[int, Position], bench: Iterable[int]) -> LineupCheck:
        """Menajer karari. Hata varsa HICBIR sey degismez; sonuc dondurulur."""
        check = validate_lineup(team.players, team.formation, self.current_week, xi, bench)
        if check.ok:
            self._apply_lineup(team, xi, bench)
        return check

    def auto_lineup(self, team: Team) -> dict[int, Position]:
        """Asistan menajer: en yuksek efektif guce sahip uygun 11 + kulube."""
        week = self.current_week
        xi = pick_best_xi(team.players, team.formation, week)
        bench = pick_bench(team.players, xi, week)
        self._apply_lineup(team, xi, bench)
        return xi

    def clear_lineup(self, team: Team) -> None:
        self._apply_lineup(team, {}, [p.id for p in team.players])

    def _apply_lineup(self, team: Team, xi: Mapping[int, Position], bench: Iterable[int]) -> None:
        bench_ids = set(bench)
        for p in team.players:
            if p.id in xi:
                p.lineup_status, p.lineup_role = LineupStatus.XI, xi[p.id]
            elif p.id in bench_ids:
                p.lineup_status, p.lineup_role = LineupStatus.BENCH, None
            else:
                p.lineup_status, p.lineup_role = LineupStatus.OUT, None
        self.db.flush()

    # ------------------------------------------------------------------ yeni sezon

    def start_new_season(self) -> int:
        """
        Sezon bittiyse: takim istatistikleri sifirlanir, fikstur yeniden uretilir,
        oyuncular (akademi dahil) bir yas alir, sakatlik/ceza/sari/not gecmisi temizlenir, kondisyon 100'e doner.
        Form ve moral tasinir (yeni sezona 'ruh hali' ile girilir). Piyasa degeri potansiyel primiyle.
        Kariyer modunda AI kulupleri akademilerini yonetir (_ai_manage_academy); kullanicinin kulubu icin
        yalnizca new_season_notes doldurulur (hicbir oyuncu otomatik tasinmaz).
        """
        if not self.season_finished:
            raise SeasonNotFinished("Sezon henüz bitmedi; oynanmamış maçlar var.")

        st = self.state
        new_season = st.season + 1
        tournament_mode = self.game_mode is GameMode.TOURNAMENT
        previous_cup = self.tournaments.current()
        # Kupa katilimi: kariyerde biten sezonun lig siralamasi (sifirlamadan ONCE okunur)
        cup_tables = self.tournaments.qualification_tables(by_standings=not tournament_mode)

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
            p.injured_until_week = 0
            p.suspended_matches = 0
            p.season_yellow_cards = 0
            p.cup_suspended_matches = 0
            p.cup_yellow_cards = 0
            p.weeks_since_match = 0
            p.match_rating_history = []
            p.condition = fitness.CONDITION_MAX          # sezon arasi tam dinlenme
            if tournament_mode:
                continue                                 # yeni turnuva: yas ve sozlesme ilerlemez
            p.age = min(45, p.age + 1)
            # Sozlesme bir yil erir, piyasa degeri yeni yasa gore guncellenir
            p.contract_years = max(0, p.contract_years - 1)
            p.market_value = finance.market_value(p.overall_rating, p.age, p.position, p.potential_rating)

        self.new_season_notes = []
        if not tournament_mode:
            self.db.flush()
            self.new_season_notes = self._season_academy_management()

        st.season = new_season
        st.current_week = 1
        self.db.flush()
        fmt = self.tournaments.fmt(previous_cup) if previous_cup is not None else None
        self.tournaments.create(new_season, cup_tables, fmt)
        self.db.flush()
        return new_season
