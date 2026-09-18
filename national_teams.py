"""
national_teams.py
=================
Milli takimlar ve Dunya Kupasi kontrolcusu (Faz 12 / 14. Asama, 12C). Commit ETMEZ (cagiranin islemine katilir).
Kurallar saf modullerdedir: national_rules (kadro, is teklifi, sozlesme, gorevden alma, taninirlik), world_cup
(boy, kura, fikstur, tablo, agac), intl_calendar (milli ara haftalari, Dunya Kupasi sezonu).

    NationalTeams     -> uluslar, is teklifleri, kadro cagrisi, ilk 11, fikstur, gruplar, agac; mac gunleri
    NationalExtension -> kariyer eklentisi (extensions.load; yalnizca WorldRules.internationals acikken)

Kurulum (ensure_setup, IDEMPOTENT; eklenti ilk oynanan haftada kendisi de cagirir):
    * Uluslar A takim kulup oyunculari uzerinden: national_rules.nation_of(uyruk, lig ulkesi). En az 23 oyuncusu
      olan (eligible_nations) her ulus bir nations satiri: itibar = en guclu 23 oyuncunun ortalama gucu (1-100),
      dizilis = AI kadrosuna en uygun kayitli dizilis, AI yonetir. Sezon basinda itibar ve (menajersiz ulusta)
      dizilis tazelenir, yeni uygun uluslar eklenir; eski ulus silinmez.
    * Sezon kadrosu (national_callups): kadrosu olmayan her ulusa AI kadrosu (ai_callups, 23 kisi). Kadro karari
      (XI / BENCH / OUT, rol) bu tabloda tutulur; kulup satirindaki lineup_status'a DOKUNULMAZ.
    * Dunya Kupasi sezonu (intl_calendar.is_world_cup_season): boy = world_cup_size(uygun ulus); 0 ise Dunya
      Kupasi yok (world_cup_status Turkce nedeni verir). Dunya Kupasi satiri (WORLD_CUP, takvim = plan.stage_days)
      her zaman kurulur. Uygun ulus boydan fazlaysa ve bu sezon oynanacak milli ara haftasi kaldiysa ELEMELER
      (QUALIFIER): qualifier_groups + qualifier_fixtures, mac gunleri matchday_weeks ile milli aralara (bir
      pencereye iki gun dusebilir); milli ara kalmadiysa itibara gore ilk `boy` ulus, eleme gerekmiyorsa hepsi
      dogrudan katilir ve kura hemen cekilir (world_cup_draw).
    * Is teklifleri: kulubu olan, milli gorevi olmayan her koltuga (satiri olan birincil dahil), seviyesinin
      yettigi (job_offer_eligible) bos uluslardan en gucluden baslayarak en fazla MAX_JOB_OFFERS bekleyen teklif;
      ulus + koltuk + sezon basina tek teklif (reddedilen / suresi dolan ayni sezonda yeniden gelmez). Her hafta
      tamamlanir. NATIONAL bildirimi. Kabul: sozlesme contract_until(sezon), menajer basina tek milli gorev
      (nations.uq_nation_manager), ulusun ve koltugun diger bekleyen teklifleri WITHDRAWN.

Takvim:
    sezon ici    on_week (lig haftasi, gelisim, maas ve AI transfer penceresinden SONRA): haftasi gelmis eleme
                 mac gunleri; elemeler bitince katilanlar (qualified_from_groups) ve Dunya Kupasi kurasi
    sezon arasi  close_season_pending / play_close_season_matchday (dunya ilerlemesi her turda bir gun): kalan
                 eleme gunleri (yetisme), Dunya Kupasi grup gunleri, sonra tek macli eleme turlari (uzatma ve
                 penalti: KnockoutRule). Final -> sampiyon: international_tournaments.champion_nation_id +
                 status FINISHED (arsiv), haber (news_items WORLD_CUP), dunya olayi (NATIONAL), dunya panosu
    yeni sezon   new_season_blocker: Dunya Kupasi sezonunda milli maclar bitmeden Turkce neden
                 (start_new_season -> SeasonNotFinished). on_season_end: kampanya degerlendirmesi (expected_stage,
                 ulasilan tur, puan orani -> sack_decision) ve biten sozlesmeler; gorevden alma / sozlesme sonu
                 NATIONAL bildirimi. on_season_start: uluslar, kadrolar, turnuvalar, teklifler (+ insan kulubu
                 sezon notlari)

Milli mac motoru (match_engine DEGISMEZ):
    * NationalSquad ordek nesnesi (.id = ulus id, .name, .reputation, .formation, .players). Oyuncular
      NationalPlayer: kulup oyuncu satirinin veritabanindan okunmus ANLIK KOPYASI (ORM nesnesi degil, yazilamaz)
      + milli kadro karari (lineup_status / lineup_role national_callups'tan). build_match_team'e verilir.
    * Uygunluk: yalnizca kulup sakatligi + milli ceza (ulusun son milli macinda kirmizi kart: bir sonraki milli
      macta oturur; national_callups.intl_suspended gosterim icin yansitilir). Kulup cezalari milli macta
      gecerli degildir.
    * EngineConfig: kariyer ayari (ai_tactics) + base_injury=0.0. Tohum crc32("intl|tohum|sezon|fikstur id")
      (tohumsuz kariyerde "free"). Roller team_roles.suggest_roles (rastgele sayi cekmez). Kuralar
      "intl-qual|..." / "intl-wc|..." metin tohumlariyla.
    * Kulup oyuncu satirina YALNIZCA international_caps / international_goals yazilir: kondisyon, form, moral,
      sakatlik, ceza, not gecmisi, mac istatistigi ve kaygi penceresi degismez. cm.rng hic kullanilmaz ve ORM
      Player / Team nesnesi yuklenmez: milli maclar acik ya da kapali, ayni tohumla lig ve kupa sonuclari
      birebir aynidir.

Rapor ve taninirlik:
    * Haftalik raporda mac gunu ozeti honours_notes'a; sezon arasi raporunda (mac oynanmamis rapor) cup_label /
      cup_notes'a (career_views.cup_report_lines). WeekReport'ta intl_notes alani varsa hepsi oraya yazilir.
    * Milli menajerin mac / tur taninirligi (intl_reputation_delta; eleme maci stage None, penaltili eleme turu
      W/L, Dunya Kupasi'na katilma ("Q", QUAL), grubu gecme ("Q", GROUP)): kulubu insan kulubuyse
      CareerManager._apply_reputation_delta (SeatStore.apply_reputation; birincil -> GameState, raporun
      manager_reputation alanina), kulupsuz koltukta dogrudan koltuk satirina. Her mac NATIONAL bildirimi.
"""

from __future__ import annotations

import zlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

import intl_calendar
import messaging
import national_rules
import reputation
import team_roles
import world_cup
from bracket_view import RoundView, TieView
from club_directory import plain_key
from extensions import CareerExtension, rules_of
from match_engine import (
    EngineConfig,
    KnockoutRule,
    MatchEngine,
    MatchResult,
    build_match_team,
    update_standings,
)
from models import (
    GameMode,
    InternationalEntry,
    InternationalFixture,
    InternationalTournament,
    League,
    LineupStatus,
    Nation,
    NationalCallup,
    NationalJobOffer,
    NewsItem,
    Player,
    Position,
    SeatStatus,
    Team,
    WorldEvent,
    WorldEventKind,
    WorldManager,
)
from stars import star_value
from tactics import (
    DEFAULT_FORMATION,
    FORMATIONS,
    ROLE_ORDER,
    LineupCheck,
    pick_bench,
    pick_best_xi,
    validate_lineup,
)
from tournament_manager import KEY_EVENT_TYPES, _event_dict

if TYPE_CHECKING:
    from career_manager import CareerManager

# --- turnuva / fikstur / teklif degerleri (models CHECK kisitlariyla ayni) ---
QUALIFIER = "QUALIFIER"
WORLD_CUP = "WORLD_CUP"
COMPETITIONS = (QUALIFIER, WORLD_CUP)
COMPETITION_LABELS: Mapping[str, str] = {QUALIFIER: "Dünya Kupası Elemeleri", WORLD_CUP: "Dünya Kupası"}
T_DRAW, T_RUNNING, T_FINISHED = "DRAW", "RUNNING", "FINISHED"
FX_UNPLAYED, FX_PLAYED = "unplayed", "played"
OFFER_PENDING, OFFER_ACCEPTED, OFFER_DECLINED = "PENDING", "ACCEPTED", "DECLINED"
OFFER_EXPIRED, OFFER_WITHDRAWN = "EXPIRED", "WITHDRAWN"

JOB_OFFER_WEEKS = 6                 # teklif bu kadar kariyer haftasi gecerli
MAX_JOB_OFFERS = 3                  # koltuk basina ayni anda bekleyen teklif
REPUTATION_SQUAD = national_rules.DEFAULT_CALLUPS     # ulus itibari: en guclu bu kadar oyuncunun ortalamasi
OUT_OF_POSITION_SHARE = 0.85        # dizilis secimi: eksik mevki en iyi kalan saha oyuncusuyla, bu carpanla
CALLUP_NONE = "NONE"                # CallupRow.status: kadroya cagrilmamis aday
NEWS_KIND = "WORLD_CUP"             # news_items.kind (duz metin)
# notifications.ref_type (messaging.REF_TYPE_MAX = 16)
REF_OFFER, REF_FIXTURE, REF_NATION = "national_offer", "intl_fixture", "nation"
RESACK_SEASONS = 1                  # gorevden alinan menajere ayni ulustan bu kadar sezon teklif gelmez
REPORT_MAX_RESULTS = 8              # haftalik rapor satirinda en fazla bu kadar skor
MEMBER_STATUSES = (SeatStatus.ACTIVE.value, SeatStatus.RELEASED.value)
STATUS_ORDER = {LineupStatus.XI.value: 0, LineupStatus.BENCH.value: 1, LineupStatus.OUT.value: 2, CALLUP_NONE: 3}

DISABLED_TEXT = "Bu dünyada milli takımlar kapalı."
NO_SEAT_TEXT = "Milli takım işlemleri için bu dünyada bir menajer koltuğun olmalı."
NO_JOB_TEXT = "Milli takım görevin yok."
OFFER_NOT_FOUND_TEXT = "Milli takım teklifi bulunamadı."
OFFER_CLOSED_TEXT = "Bu teklif artık geçerli değil."
OFFER_EXPIRED_TEXT = "Teklifin süresi doldu."
NATION_NOT_FOUND_TEXT = "Milli takım bulunamadı."


class NationalTeamError(ValueError):
    """Milli takim islemi yapilamaz (mesaj Turkce)."""


@dataclass(frozen=True)
class NationView:
    id: int
    name: str
    reputation: int
    rank: int
    manager_name: str | None
    ai_managed: bool
    contract_until_season: int | None
    squad_size: int
    qualified: bool | None          # Dunya Kupasi'nda mi? None: bu sezon kampanya yok / elemeler suruyor
    stage_label: str


@dataclass(frozen=True)
class NationalJobOfferView:
    id: int
    nation_id: int
    nation_name: str
    reputation: int
    seasons: int
    expires_in_weeks: int | None


@dataclass(frozen=True)
class CallupRow:
    player_id: int
    name: str
    club: str | None
    position: str
    age: int
    stars: float
    status: str                     # XI / BENCH / OUT (kadroda) ya da CALLUP_NONE (aday)
    role: str | None
    available: bool
    reason: str


@dataclass(frozen=True)
class IntlFixtureView:
    id: int
    competition: str
    stage_label: str
    week_label: str
    home: str
    away: str
    score: str | None
    penalties: str | None


# ===========================================================================
# Mac motoru icin ordek nesneleri
# ===========================================================================

_PLAYER_FIELDS: tuple[str, ...] = (
    "id", "name", "position", "age", "overall_rating", "pace", "shooting", "passing", "defending", "dribbling",
    "goalkeeping", "form", "morale", "condition", "fm_attributes", "injured_until_week", "team_id",
)
_PLAYER_COLUMNS = tuple(getattr(Player, name) for name in _PLAYER_FIELDS)


class NationalPlayer:
    """
    Milli kadro oyuncusu: kulup oyuncu satirinin salt okunur kopyasi + milli kadro karari. __slots__ disinda alan
    yazilamaz (kulup satirina yanlislikla yazmak mumkun degil). tactics.validate_lineup / pick_best_xi ve
    match_engine.build_match_team ile ordek tipi uyumludur.
    """
    __slots__ = (*_PLAYER_FIELDS, "lineup_status", "lineup_role", "intl_suspended")

    def __init__(self, row: Any, lineup_status: LineupStatus = LineupStatus.BENCH,
                 lineup_role: Position | None = None, intl_suspended: bool = False) -> None:
        for name in _PLAYER_FIELDS:
            setattr(self, name, getattr(row, name))
        if isinstance(self.fm_attributes, dict):
            self.fm_attributes = dict(self.fm_attributes)
        self.lineup_status = lineup_status
        self.lineup_role = lineup_role
        self.intl_suspended = bool(intl_suspended)

    def is_injured(self, week: int | None) -> bool:
        return week is not None and int(self.injured_until_week or 0) > week

    def unavailability_reason(self, week: int | None, competition: Any = None) -> str | None:
        """Milli macta yalnizca kulup sakatligi ve milli ceza gecerlidir (competition yok sayilir)."""
        if self.is_injured(week):
            return f"sakat, {self.injured_until_week}. haftada dönüyor"
        if self.intl_suspended:
            return "milli maçta cezalı (son milli maçta kırmızı kart)"
        return None

    def is_available(self, week: int | None, competition: Any = None) -> bool:
        return self.unavailability_reason(week) is None


@dataclass
class NationalSquad:
    """build_match_team icin milli takim (Team yerine)."""
    id: int
    name: str
    reputation: int
    formation: str
    players: list[NationalPlayer]


@lru_cache(maxsize=4096)
def _nation_name(nationality: str | None, country: str) -> str:
    """national_rules.nation_of onbellekli (uyruk / ulke cifti az; oyuncu satiri cok)."""
    return national_rules.nation_of(nationality, country)


def _position(value: Any) -> Position | None:
    if value is None:
        return None
    return Position(getattr(value, "value", value))


def _outcome(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "W"
    return "D" if goals_for == goals_against else "L"


def _int_id(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _score_text(fx: InternationalFixture, names: Mapping[int, str]) -> str:
    text = f"{names.get(fx.home_nation_id, '?')} {fx.home_score}-{fx.away_score} {names.get(fx.away_nation_id, '?')}"
    if fx.extra_time:
        text += " (uzt.)"
    if fx.home_penalties is not None:
        text += f" · pen. {fx.home_penalties}-{fx.away_penalties}"
    return text


class NationalTeams:
    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm
        self.db = getattr(cm, "db", None)   # kurucu sorgu atmaz (sayfa cizimlerinde / eklenti yukleyicide kurulur)
        self._pending_notes: list[tuple[str, list[str], bool]] = []

    # ================================================================== durum

    def enabled(self) -> bool:
        return rules_of(self.cm).internationals

    def _active(self) -> bool:
        return self.enabled() and self.cm.game_mode is GameMode.CAREER

    def _require_enabled(self) -> None:
        if not self.enabled():
            raise NationalTeamError(DISABLED_TEXT)

    @property
    def _base_seed(self) -> int | str:
        return self.cm.seed if self.cm.seed is not None else "free"

    def _match_seed(self, fx: InternationalFixture) -> int:
        return zlib.crc32(f"intl|{self._base_seed}|{fx.season}|{fx.id}".encode())

    def _engine_config(self) -> EngineConfig:
        """Kariyer mac ayari (AI talimatlari acik kariyerde acik) + sakatlik yok."""
        cfg = self.cm.engine_config
        if self.cm.ai_tactics and (cfg is None or not cfg.ai_tactics):
            cfg = replace(cfg if cfg is not None else EngineConfig(), ai_tactics=True)
        return replace(cfg if cfg is not None else EngineConfig(), base_injury=0.0)

    # ================================================================== kurulum

    def ensure_setup(self) -> list[str]:
        """Uluslar, sezon kadrolari, sezon turnuvalari ve is teklifleri (IDEMPOTENT). Yapilanlar (Turkce)."""
        if not self._active():
            return []
        notes: list[str] = []
        pool = self._pool()
        created = self._sync_nations(refresh=False, pool=pool)
        if created:
            notes.append(f"{len(created)} milli takım kuruldu: {', '.join(created)}.")
        called = self._ensure_callups(self.cm.season, pool)
        if called:
            notes.append(f"{called} milli takımın kadrosu çağrıldı.")
        notes += self._ensure_season_tournaments(self.cm.season, pool)
        offers = self._offer_jobs()
        if offers:
            notes.append(f"{len(offers)} milli takım iş teklifi gönderildi.")
        self.db.flush()
        return notes

    def _pool_rows(self) -> list:
        """A takim kulup oyunculari (hafif kolonlar; ORM nesnesi yuklenmez)."""
        return list(self.db.execute(
            select(Player.id, Player.name, Player.age, Player.position, Player.overall_rating, Player.form,
                   Player.morale, Player.condition, Player.injured_until_week, Player.nationality,
                   Team.name.label("club"), League.country.label("country"))
            .join(Team, Team.id == Player.team_id)
            .join(League, League.id == Team.league_id)
            .where(Player.in_academy.is_(False))
            .order_by(Player.id)
        ).all())

    def _pool(self) -> dict[str, list]:
        """Ulus adi -> o ulusun oyunculari (national_rules.nation_of)."""
        pool: dict[str, list] = defaultdict(list)
        for row in self._pool_rows():
            pool[_nation_name(row.nationality, row.country)].append(row)
        return pool

    @staticmethod
    def _eligible_names(pool: Mapping[str, Sequence]) -> list[str]:
        return national_rules.eligible_nations({name: len(rows) for name, rows in pool.items()})

    @staticmethod
    def _nation_code(name: str) -> str:
        for alias in national_rules._NATION_ALIASES.get(name, ()):
            if len(alias) == 3 and alias.isascii() and alias.isalpha() and alias.isupper():
                return alias
        letters = "".join(ch for ch in plain_key(name) if ch.isalpha())
        return (letters[:3] or "nat").upper()

    @staticmethod
    def _nation_reputation(rows: Sequence) -> int:
        top = sorted(rows, key=lambda r: (-int(r.overall_rating), int(r.id)))[:REPUTATION_SQUAD]
        if not top:
            return 1
        return max(1, min(100, round(sum(int(r.overall_rating) for r in top) / len(top))))

    @staticmethod
    def _best_formation(rows: Sequence) -> str:
        """AI kadrosunun (23) dogal mevki gucune en uygun kayitli dizilis; esitlikte FORMATIONS sirasi."""
        picked = set(national_rules.ai_callups(rows, national_rules.DEFAULT_CALLUPS))
        squad = [r for r in rows if int(r.id) in picked]
        by_pos = {pos: sorted((int(r.overall_rating) for r in squad if r.position is pos), reverse=True)
                  for pos in ROLE_ORDER}
        best, best_score = DEFAULT_FORMATION, -1.0
        for name, (defenders, midfielders, forwards) in FORMATIONS.items():
            needs = {Position.GK: 1, Position.DEF: defenders, Position.MID: midfielders, Position.FWD: forwards}
            score, missing_outfield, leftovers = 0.0, 0, []
            for pos, count in needs.items():
                values = by_pos[pos]
                score += sum(values[:count])
                if pos is not Position.GK:
                    missing_outfield += max(0, count - len(values))
                    leftovers.extend(values[count:])
            leftovers.sort(reverse=True)
            score += OUT_OF_POSITION_SHARE * sum(leftovers[:missing_outfield])
            if score > best_score:
                best, best_score = name, score
        return best

    def _sync_nations(self, refresh: bool, pool: Mapping[str, Sequence] | None = None) -> list[str]:
        """Uygun uluslari kurar (refresh: mevcutlarin itibari ve menajersiz ulusun dizilisi tazelenir)."""
        pool = pool if pool is not None else self._pool()
        existing = {n.name: n for n in self.db.scalars(select(Nation))}
        created: list[str] = []
        for name in self._eligible_names(pool):
            rows = pool.get(name, [])
            nation = existing.get(name)
            if nation is None:
                self.db.add(Nation(name=name[:60], code=self._nation_code(name),
                                   reputation=self._nation_reputation(rows), formation=self._best_formation(rows),
                                   ai_managed=True))
                created.append(name)
            elif refresh:
                nation.reputation = self._nation_reputation(rows)
                if nation.manager_id is None:
                    nation.formation = self._best_formation(rows)
                    nation.ai_managed = True
        self.db.flush()
        return created

    def _callups(self, nation_id: int, season: int) -> list[NationalCallup]:
        return list(self.db.scalars(
            select(NationalCallup)
            .where(NationalCallup.nation_id == nation_id, NationalCallup.season == season)
            .order_by(NationalCallup.player_id)
        ))

    def _called_nations(self, season: int) -> dict[int, int]:
        """Oyuncu id -> bu sezon kadrosuna cagrildigi ulus."""
        return dict(self.db.execute(
            select(NationalCallup.player_id, NationalCallup.nation_id).where(NationalCallup.season == season)
        ).all())

    def _ensure_callups(self, season: int, pool: Mapping[str, Sequence]) -> int:
        """Bu sezon kadrosu olmayan uluslara AI kadrosu. Kadro kurulan ulus sayisi."""
        counts = dict(self.db.execute(
            select(NationalCallup.nation_id, func.count()).where(NationalCallup.season == season)
            .group_by(NationalCallup.nation_id)
        ).all())
        taken = self._called_nations(season)
        done = 0
        for nation in self.db.scalars(select(Nation).order_by(Nation.id)):
            if counts.get(nation.id):
                continue
            rows = [r for r in pool.get(nation.name, []) if taken.get(r.id, nation.id) == nation.id]
            picks = national_rules.ai_callups(rows, national_rules.DEFAULT_CALLUPS, week=self.cm.current_week)
            for pid in picks:
                self.db.add(NationalCallup(nation_id=nation.id, season=season, player_id=pid,
                                           lineup_status=LineupStatus.BENCH.value, lineup_role=None,
                                           intl_suspended=0))
                taken[pid] = nation.id
            done += 1 if picks else 0
        self.db.flush()
        return done

    # ------------------------------------------------------------------ sezon turnuvalari

    def _tournament(self, season: int, kind: str) -> InternationalTournament | None:
        return self.db.scalar(select(InternationalTournament).where(
            InternationalTournament.season == season, InternationalTournament.kind == kind))

    def _entries(self, t: InternationalTournament) -> list[InternationalEntry]:
        return list(self.db.scalars(
            select(InternationalEntry).where(InternationalEntry.tournament_id == t.id)
            .order_by(InternationalEntry.group_index.nulls_last(), InternationalEntry.pot, InternationalEntry.nation_id)
        ))

    def _fixtures(self, t: InternationalTournament) -> list[InternationalFixture]:
        return list(self.db.scalars(
            select(InternationalFixture).where(InternationalFixture.tournament_id == t.id)
            .order_by(InternationalFixture.close_season_day.nulls_first(), InternationalFixture.week.nulls_last(),
                      InternationalFixture.leg, InternationalFixture.group_index.nulls_last(), InternationalFixture.id)
        ))

    @staticmethod
    def _wc_size(t: InternationalTournament | None) -> int:
        calendar = list(t.calendar or []) if t is not None else []
        try:
            return int(calendar[0]["size"])
        except (IndexError, KeyError, TypeError, ValueError):
            return 0

    def _nations_by_id(self, ids: Iterable[int] | None = None) -> dict[int, Nation]:
        stmt = select(Nation)
        if ids is not None:
            stmt = stmt.where(Nation.id.in_(sorted(set(ids))))
        return {n.id: n for n in self.db.scalars(stmt)}

    def _ensure_season_tournaments(self, season: int, pool: Mapping[str, Sequence] | None = None) -> list[str]:
        """Dunya Kupasi sezonunda (henuz kurulmadiysa) elemeler ve Dunya Kupasi. Notlar (Turkce)."""
        if not self._active():
            return []
        rules = rules_of(self.cm)
        if not intl_calendar.is_world_cup_season(season, rules.world_cup_every_seasons):
            return []
        exists = self.db.scalar(select(func.count()).select_from(InternationalTournament)
                                .where(InternationalTournament.season == season))
        if exists or season != self.cm.season or self.cm.season_finished:
            return []
        pool = pool if pool is not None else self._pool()
        names = self._eligible_names(pool)
        nations = list(self.db.scalars(select(Nation).where(Nation.name.in_(names)).order_by(Nation.id)))
        size = world_cup.world_cup_size(len(nations))
        if size == 0:
            return []
        seeds = [world_cup.NationSeed(n.id, n.name, n.reputation) for n in nations]
        wc = InternationalTournament(
            season=season, kind=WORLD_CUP, status=T_DRAW,
            calendar=[{"day": day, "stage": stage, "round": rnd, "size": size}
                      for day, (stage, rnd) in enumerate(world_cup.plan(size).stage_days(), start=1)],
        )
        self.db.add(wc)
        self.db.flush()
        notes: list[str] = []
        if len(nations) > size:
            windows = [w for w in intl_calendar.international_weeks(self.cm.league_weeks()) if w >= self.cm.current_week]
            if windows:
                notes.append(self._create_qualifiers(season, seeds, size, windows))
                return notes
            entrants = [s.nation_id for s in world_cup.rank_by_reputation(seeds)[:size]]
            notes.append(f"Bu sezon eleme için milli ara kalmadı; itibara göre ilk {size} milli takım "
                         "Dünya Kupası'na katılıyor.")
        else:
            entrants = [s.nation_id for s in seeds]
        notes += self._draw_world_cup(wc, entrants)
        return notes

    def _create_qualifiers(self, season: int, seeds: Sequence[world_cup.NationSeed], size: int,
                           windows: Sequence[int]) -> str:
        groups = world_cup.qualifier_groups(seeds, size, f"intl-qual|{self._base_seed}|{season}")
        days = world_cup.qualifier_fixtures(groups)
        weeks = intl_calendar.matchday_weeks(len(days), windows)
        qualifier = InternationalTournament(
            season=season, kind=QUALIFIER, status=T_RUNNING,
            calendar=[{"day": day, "week": week, "size": size} for day, week in enumerate(weeks, start=1)],
        )
        self.db.add(qualifier)
        self.db.flush()
        for group_index, nation_ids in enumerate(groups):
            for pot, nation_id in enumerate(nation_ids):
                self.db.add(InternationalEntry(tournament_id=qualifier.id, nation_id=nation_id,
                                               group_index=group_index, pot=pot))
        for day, matches in enumerate(days, start=1):
            for group_index, home_id, away_id in matches:
                self.db.add(InternationalFixture(
                    tournament_id=qualifier.id, season=season, stage=world_cup.GROUP, leg=day,
                    group_index=group_index, week=weeks[day - 1], home_nation_id=home_id, away_nation_id=away_id,
                    status=FX_UNPLAYED, key_events=[], neutral=False,
                ))
        self.db.flush()
        week_text = ", ".join(str(w) for w in sorted(set(weeks)))
        return (f"Dünya Kupası elemeleri kuruldu: {len(groups)} grup, {len(days)} maç günü "
                f"({week_text}. haftalarda); {size} takım Dünya Kupası'na katılacak.")

    def _draw_world_cup(self, wc: InternationalTournament, nation_ids: Sequence[int]) -> list[str]:
        """Katilimcilar belli: grup kurasi ve grup fiksturu (sezon arasi gunleri)."""
        if wc.status != T_DRAW or self._entries(wc):
            return []
        nations = self._nations_by_id(nation_ids)
        seeds = [world_cup.NationSeed(nid, nations[nid].name, nations[nid].reputation) for nid in nation_ids]
        groups = world_cup.world_cup_draw(seeds, f"intl-wc|{self._base_seed}|{wc.season}")
        for group_index, ids in enumerate(groups):
            for pot, nation_id in enumerate(ids):
                self.db.add(InternationalEntry(tournament_id=wc.id, nation_id=nation_id,
                                               group_index=group_index, pot=pot))
        for day, matches in enumerate(world_cup.world_cup_group_fixtures(groups), start=1):
            for group_index, home_id, away_id in matches:
                self.db.add(InternationalFixture(
                    tournament_id=wc.id, season=wc.season, stage=world_cup.GROUP, leg=day, group_index=group_index,
                    week=None, close_season_day=day, home_nation_id=home_id, away_nation_id=away_id,
                    status=FX_UNPLAYED, key_events=[], neutral=True,
                ))
        wc.status = T_RUNNING
        self.db.flush()
        text = "; ".join(
            f"Grup {world_cup.group_letter(g)}: {', '.join(nations[nid].name for nid in ids)}"
            for g, ids in enumerate(groups)
        )
        return [f"Dünya Kupası kurası çekildi — {text}."]

    # ================================================================== is teklifleri

    def _ranking(self, nations: Sequence[Nation]) -> dict[int, int]:
        seeds = [world_cup.NationSeed(n.id, n.name, n.reputation) for n in nations]
        return {seed.nation_id: rank for rank, seed in enumerate(world_cup.rank_by_reputation(seeds), start=1)}

    def _expire_offers(self) -> None:
        self.db.execute(
            update(NationalJobOffer)
            .where(NationalJobOffer.status == OFFER_PENDING, NationalJobOffer.expires_career_week.isnot(None),
                   NationalJobOffer.expires_career_week < self.cm.career_week)
            .values(status=OFFER_EXPIRED)
        )

    def _offer_jobs(self) -> list[tuple[int, int, int]]:
        """Bekleyen teklifleri tamamlar. Donus: [(koltuk id, teklif id, ulus id)]."""
        if not self._active():
            return []
        nations = list(self.db.scalars(select(Nation).order_by(Nation.id)))
        vacant = [n for n in nations if n.manager_id is None]
        if not vacant:
            return []
        busy = {n.manager_id for n in nations if n.manager_id is not None}
        seats = [s for s in self.cm.seats.active()
                 if s.id is not None and s.team_id is not None and s.id not in busy]
        if not seats:
            return []
        season, career_week = self.cm.season, self.cm.career_week
        ranking = self._ranking(nations)
        total = len(nations)
        mine: dict[int, list[NationalJobOffer]] = defaultdict(list)
        for offer in self.db.scalars(select(NationalJobOffer).where(
                NationalJobOffer.manager_id.in_([s.id for s in seats]), NationalJobOffer.season == season)):
            mine[offer.manager_id].append(offer)
        # Gorevden alindigi ulustan yakin zamanda teklif gelmez
        sacked: dict[int, set[int]] = defaultdict(set)
        for actor, nation_id in self.db.execute(
                select(WorldEvent.actor_manager_id, WorldEvent.payload["nation_id"].astext)
                .where(WorldEvent.kind == WorldEventKind.NATIONAL.value,
                       WorldEvent.payload["action"].astext == "SACKED",
                       WorldEvent.actor_manager_id.in_([s.id for s in seats]),
                       WorldEvent.season >= season - RESACK_SEASONS)).all():
            if nation_id is not None and str(nation_id).isdigit():
                sacked[actor].add(int(nation_id))
        created: list[tuple[int, int, int]] = []
        for seat in seats:
            offers = mine[seat.id]
            pending = [o for o in offers if o.status == OFFER_PENDING
                       and (o.expires_career_week is None or o.expires_career_week >= career_week)]
            room = MAX_JOB_OFFERS - len(pending)
            if room <= 0:
                continue
            offered = {o.nation_id for o in offers} | sacked[seat.id]
            level = reputation.level(seat.reputation).level
            candidates = sorted(
                (n for n in vacant if n.id not in offered
                 and national_rules.job_offer_eligible(level, national_rules.rank_pct(ranking[n.id], total))),
                key=lambda n: ranking[n.id],
            )
            for nation in candidates[:room]:
                offer = NationalJobOffer(nation_id=nation.id, manager_id=seat.id, season=season,
                                         status=OFFER_PENDING, expires_career_week=career_week + JOB_OFFER_WEEKS)
                self.db.add(offer)
                self.db.flush()
                created.append((seat.id, offer.id, nation.id))
                seasons = national_rules.contract_until(season) - season + 1
                self._notify(seat.id, f"{nation.name} milli takımı sana menajerlik teklif etti "
                                      f"({seasons} sezonluk sözleşme, {JOB_OFFER_WEEKS} hafta geçerli).",
                             REF_OFFER, offer.id)
        return created

    def job_offers(self) -> list[NationalJobOfferView]:
        if not self.enabled():
            return []
        seat = self.cm.acting_seat
        if seat is None or seat.id is None:
            return []
        career_week = self.cm.career_week
        rows = self.db.execute(
            select(NationalJobOffer, Nation).join(Nation, Nation.id == NationalJobOffer.nation_id)
            .where(NationalJobOffer.manager_id == seat.id, NationalJobOffer.status == OFFER_PENDING,
                   Nation.manager_id.is_(None),
                   or_(NationalJobOffer.expires_career_week.is_(None),
                       NationalJobOffer.expires_career_week >= career_week))
            .order_by(Nation.reputation.desc(), NationalJobOffer.id)
        ).all()
        return [
            NationalJobOfferView(
                id=offer.id, nation_id=nation.id, nation_name=nation.name, reputation=int(nation.reputation),
                seasons=national_rules.contract_until(offer.season) - offer.season + 1,
                expires_in_weeks=(offer.expires_career_week - career_week
                                  if offer.expires_career_week is not None else None),
            )
            for offer, nation in rows
        ]

    def _my_seat_row(self) -> WorldManager:
        self._require_enabled()
        seat = self.cm.acting_seat
        row = self.db.get(WorldManager, seat.id) if seat is not None and seat.id is not None else None
        if row is None or (not row.is_primary and row.status not in MEMBER_STATUSES):
            raise NationalTeamError(NO_SEAT_TEXT)
        return row

    def _my_offer(self, offer_id: int) -> tuple[WorldManager, NationalJobOffer]:
        row = self._my_seat_row()
        key = _int_id(offer_id)
        offer = self.db.get(NationalJobOffer, key) if key is not None else None
        if offer is None or offer.manager_id != row.id:
            raise NationalTeamError(OFFER_NOT_FOUND_TEXT)
        if offer.status != OFFER_PENDING:
            raise NationalTeamError(OFFER_CLOSED_TEXT)
        if offer.expires_career_week is not None and offer.expires_career_week < self.cm.career_week:
            offer.status = OFFER_EXPIRED
            self.db.flush()
            raise NationalTeamError(OFFER_EXPIRED_TEXT)
        return row, offer

    def accept_job(self, offer_id: int) -> NationView:
        row, offer = self._my_offer(offer_id)
        current = self.db.scalar(select(Nation).where(Nation.manager_id == row.id))
        if current is not None:
            raise NationalTeamError(f"Zaten {current.name} milli takımının menajerisin; bir menajer aynı anda "
                                    "tek milli takımı yönetebilir.")
        locked = self.cm.lock_rows(Nation, [offer.nation_id])
        nation = locked[0] if locked else None
        if nation is None:
            offer.status = OFFER_WITHDRAWN
            self.db.flush()
            raise NationalTeamError(NATION_NOT_FOUND_TEXT)
        if nation.manager_id is not None:
            offer.status = OFFER_WITHDRAWN
            self.db.flush()
            raise NationalTeamError(f"{nation.name} milli takımı artık başka bir menajerle çalışıyor.")
        until = national_rules.contract_until(self.cm.season)
        try:
            with self.db.begin_nested():
                nation.manager_id, nation.ai_managed, nation.contract_until_season = row.id, False, until
                row.nation_id, row.national_until_season = nation.id, until
                offer.status = OFFER_ACCEPTED
                self.db.flush()
        except IntegrityError as exc:
            raise NationalTeamError("Bir menajer aynı anda tek milli takımı yönetebilir.") from exc
        others = list(self.db.scalars(select(NationalJobOffer).where(
            NationalJobOffer.status == OFFER_PENDING, NationalJobOffer.id != offer.id,
            or_(NationalJobOffer.manager_id == row.id, NationalJobOffer.nation_id == nation.id))))
        for other in others:
            other.status = OFFER_WITHDRAWN
            if other.manager_id != row.id:
                self._notify(other.manager_id, f"{nation.name} milli takımı teklifini geri çekti: görevi "
                                               f"{row.display_name} kabul etti.", REF_OFFER, other.id)
        self._event(row.id, {"action": "ACCEPT", "nation_id": nation.id,
                             "text": f"{row.display_name} {nation.name} milli takımının menajeri oldu "
                                     f"({until}. sezon sonuna kadar)."})
        self.db.flush()
        return self._nation_view(nation)

    def decline_job(self, offer_id: int) -> None:
        _row, offer = self._my_offer(offer_id)
        offer.status = OFFER_DECLINED
        self.db.flush()

    def resign(self) -> None:
        row = self._my_seat_row()
        nation = self.db.scalar(select(Nation).where(Nation.manager_id == row.id))
        if nation is None:
            raise NationalTeamError(NO_JOB_TEXT)
        self._vacate(nation, row, "RESIGN", f"{row.display_name} {nation.name} milli takımındaki görevinden "
                                            "istifa etti.")
        self.db.flush()

    def _vacate(self, nation: Nation, seat_row: WorldManager | None, action: str, text: str) -> None:
        """Ulusun menajerligi biter: ulus AI'ya gecer, koltugun milli gorev alanlari temizlenir, dunya olayi."""
        actor = nation.manager_id
        nation.manager_id, nation.ai_managed, nation.contract_until_season = None, True, None
        if seat_row is not None:
            seat_row.nation_id, seat_row.national_until_season = None, None
        self._event(actor, {"action": action, "nation_id": nation.id, "text": text})

    def _vacate_departed(self) -> None:
        """Dunyadan ayrilmis / atilmis koltuklarin milli gorevi biter."""
        rows = self.db.execute(
            select(Nation, WorldManager).join(WorldManager, WorldManager.id == Nation.manager_id)
        ).all()
        for nation, seat_row in rows:
            if not seat_row.is_primary and seat_row.status not in MEMBER_STATUSES:
                self._vacate(nation, seat_row, "LEFT", f"{seat_row.display_name} dünyadan ayrıldığı için "
                                                       f"{nation.name} milli takımı menajersiz kaldı.")

    # ================================================================== goruntuler

    def nations(self) -> list[NationView]:
        if not self.enabled():
            return []
        nations = list(self.db.scalars(select(Nation).order_by(Nation.id)))
        ranking = self._ranking(nations)
        context = self._view_context()
        return sorted((self._nation_view(n, ranking, context) for n in nations), key=lambda v: v.rank)

    def my_nation(self) -> NationView | None:
        if not self.enabled():
            return None
        seat = self.cm.acting_seat
        if seat is None or seat.id is None:
            return None
        nation = self.db.scalar(select(Nation).where(Nation.manager_id == seat.id))
        return self._nation_view(nation) if nation is not None else None

    def _view_context(self) -> dict[str, Any]:
        season = self.cm.season
        qualifier, wc = self._tournament(season, QUALIFIER), self._tournament(season, WORLD_CUP)
        managers = dict(self.db.execute(select(WorldManager.id, WorldManager.display_name)).all())
        squads = dict(self.db.execute(
            select(NationalCallup.nation_id, func.count()).where(NationalCallup.season == season)
            .group_by(NationalCallup.nation_id)).all())
        latest_stage: dict[int, str] = {}
        if wc is not None:
            for fx in self._fixtures(wc):
                for nid in (fx.home_nation_id, fx.away_nation_id):
                    latest_stage[nid] = fx.stage
        return {
            "qualifier": qualifier, "wc": wc, "managers": managers, "squads": squads,
            "q_entries": {e.nation_id: e for e in self._entries(qualifier)} if qualifier is not None else {},
            "wc_entries": {e.nation_id: e for e in self._entries(wc)} if wc is not None else {},
            "latest_stage": latest_stage,
        }

    def _nation_view(self, nation: Nation, ranking: Mapping[int, int] | None = None,
                     context: Mapping[str, Any] | None = None) -> NationView:
        if ranking is None:
            ranking = self._ranking(list(self.db.scalars(select(Nation))))
        context = context if context is not None else self._view_context()
        qualified, label = self._campaign_status(nation.id, context)
        return NationView(
            id=nation.id, name=nation.name, reputation=int(nation.reputation), rank=ranking.get(nation.id, 0),
            manager_name=context["managers"].get(nation.manager_id) if nation.manager_id is not None else None,
            ai_managed=bool(nation.manager_id is None or nation.ai_managed),
            contract_until_season=nation.contract_until_season,
            squad_size=int(context["squads"].get(nation.id, 0)), qualified=qualified, stage_label=label,
        )

    def _campaign_status(self, nation_id: int, context: Mapping[str, Any]) -> tuple[bool | None, str]:
        qualifier, wc = context["qualifier"], context["wc"]
        wc_entry, q_entry = context["wc_entries"].get(nation_id), context["q_entries"].get(nation_id)
        if wc_entry is not None:
            if wc.champion_nation_id == nation_id:
                return True, "Dünya Kupası şampiyonu"
            stage = wc_entry.eliminated_stage
            if stage == world_cup.GROUP:
                return True, "Dünya Kupası · gruptan elendi"
            if stage == world_cup.FINAL:
                return True, "Dünya Kupası · finalist"
            if stage:
                return True, f"Dünya Kupası · {world_cup.stage_label(stage)} turunda elendi"
            current = context["latest_stage"].get(nation_id, world_cup.GROUP)
            if current == world_cup.GROUP:
                return True, f"Dünya Kupası · Grup {world_cup.group_letter(wc_entry.group_index or 0)}"
            return True, f"Dünya Kupası · {world_cup.stage_label(current)}"
        if q_entry is not None:
            if qualifier.status == T_FINISHED:
                return False, "Elemelerde elendi"
            return None, f"Elemeler · Grup {world_cup.group_letter(q_entry.group_index or 0)}"
        if wc is not None or qualifier is not None:
            return False, "Bu sezon Dünya Kupası'na katılamadı"
        return None, "Bu sezon milli turnuva yok"

    def world_cup_status(self) -> str:
        """Bu sezonun Dunya Kupasi durumu (Turkce, arayuz icin)."""
        if not self.enabled():
            return DISABLED_TEXT
        season, rules = self.cm.season, rules_of(self.cm)
        every = rules.world_cup_every_seasons
        if not intl_calendar.is_world_cup_season(season, every):
            upcoming = next(s for s in range(season + 1, season + every + 1)
                            if intl_calendar.is_world_cup_season(s, every))
            return f"Bu sezon Dünya Kupası yok; sıradaki Dünya Kupası {upcoming}. sezonun sonunda oynanacak."
        wc = self._tournament(season, WORLD_CUP)
        if wc is None:
            count = len(self._eligible_names(self._pool()))
            if world_cup.world_cup_size(count) == 0:
                return (f"Dünya Kupası yok: en az 4 milli takım gerekir; bu dünyada {count} ülkenin "
                        f"{national_rules.MIN_NATIONAL_PLAYERS} ya da daha fazla oyuncusu var.")
            if self.cm.season_finished:
                return "Bu sezon Dünya Kupası kurulmadı; bir sonraki Dünya Kupası sezonunu bekle."
            return "Dünya Kupası ilk milli maç haftasında kurulacak."
        size = self._wc_size(wc)
        if wc.status == T_FINISHED:
            champion = self.db.get(Nation, wc.champion_nation_id) if wc.champion_nation_id else None
            return f"Dünya Kupası şampiyonu: {champion.name if champion else '?'}"
        qualifier = self._tournament(season, QUALIFIER)
        if qualifier is not None and qualifier.status != T_FINISHED:
            fixtures = self._fixtures(qualifier)
            played = len({fx.leg for fx in fixtures if fx.status == FX_PLAYED})
            total = len({fx.leg for fx in fixtures})
            return (f"Dünya Kupası elemeleri sürüyor ({played}/{total} maç günü oynandı); "
                    f"{size} takım sezon sonunda Dünya Kupası'nda.")
        if wc.status == T_DRAW:
            return f"Dünya Kupası kurası bekleniyor ({size} takım)."
        remaining = self._remaining_days(season)
        return f"Dünya Kupası ({size} takım) sezon sonunda oynanıyor; kalan {remaining} maç günü."

    def champion_name(self, season: int | None = None) -> str | None:
        wc = self._tournament(self.cm.season if season is None else season, WORLD_CUP)
        if wc is None or wc.champion_nation_id is None:
            return None
        nation = self.db.get(Nation, wc.champion_nation_id)
        return nation.name if nation is not None else None

    def champions(self) -> list[tuple[int, str]]:
        """Arsiv: (sezon, Dunya Kupasi sampiyonu), yeniden eskiye."""
        rows = self.db.execute(
            select(InternationalTournament.season, Nation.name)
            .join(Nation, Nation.id == InternationalTournament.champion_nation_id)
            .where(InternationalTournament.kind == WORLD_CUP, InternationalTournament.status == T_FINISHED)
            .order_by(InternationalTournament.season.desc())
        ).all()
        return [(int(season), name) for season, name in rows]

    # ------------------------------------------------------------------ kadro

    def _my_nation_row(self) -> Nation:
        row = self._my_seat_row()
        nation = self.db.scalar(select(Nation).where(Nation.manager_id == row.id))
        if nation is None:
            raise NationalTeamError(NO_JOB_TEXT)
        return nation

    def _red_carded(self, nation_id: int) -> set[int]:
        """Ulusun son oynanan milli macinda kirmizi kart goren oyuncular (bir sonraki milli macta cezali)."""
        fx = self.db.scalar(
            select(InternationalFixture)
            .where(or_(InternationalFixture.home_nation_id == nation_id,
                       InternationalFixture.away_nation_id == nation_id),
                   InternationalFixture.status == FX_PLAYED)
            .order_by(InternationalFixture.id.desc()).limit(1)
        )
        if fx is None:
            return set()
        return {int(ev["player_id"]) for ev in fx.key_events or []
                if ev.get("type") == "RED_CARD" and ev.get("team_id") == nation_id and ev.get("player_id") is not None}

    def _info_rows(self, ids: Iterable[int]) -> dict[int, Any]:
        keys = sorted({int(i) for i in ids})
        if not keys:
            return {}
        rows = self.db.execute(
            select(Player.id, Player.name, Player.age, Player.position, Player.overall_rating,
                   Player.injured_until_week, Team.name.label("club"))
            .outerjoin(Team, Team.id == Player.team_id)
            .where(Player.id.in_(keys))
        ).all()
        return {row.id: row for row in rows}

    def _callup_row(self, row: Any, callup: NationalCallup | None, suspended: set[int], week: int) -> CallupRow:
        reason = ""
        if int(row.injured_until_week or 0) > week:
            reason = f"sakat, {row.injured_until_week}. haftada dönüyor"
        elif row.id in suspended:
            reason = "milli maçta cezalı (son milli maçta kırmızı kart)"
        return CallupRow(
            player_id=int(row.id), name=row.name, club=row.club, position=row.position.value, age=int(row.age),
            stars=star_value(row.overall_rating) or 0.0,
            status=callup.lineup_status if callup is not None else CALLUP_NONE,
            role=callup.lineup_role if callup is not None else None, available=not reason, reason=reason,
        )

    @staticmethod
    def _row_order(row: CallupRow, overall: Mapping[int, int]) -> tuple:
        role_rank = [r.value for r in ROLE_ORDER]
        role = row.role if row.role in role_rank else row.position
        return (STATUS_ORDER.get(row.status, 9), role_rank.index(role) if role in role_rank else 9,
                -overall.get(row.player_id, 0), row.name, row.player_id)

    def candidates(self, query: str = "", position: str | None = None) -> list[CallupRow]:
        """Menajerin ulusunun kadroya cagrilabilecek oyunculari (cagrilmislar dahil; status ile)."""
        nation = self._my_nation_row()
        season, week = self.cm.season, self.cm.current_week
        taken = self._called_nations(season)
        callups = {c.player_id: c for c in self._callups(nation.id, season)}
        suspended = self._red_carded(nation.id)
        key = plain_key(query or "")
        wanted = str(getattr(position, "value", position) or "").upper() or None
        rows = [
            r for r in self._pool().get(nation.name, [])
            if taken.get(r.id, nation.id) == nation.id
            and (not key or key in plain_key(r.name))
            and (wanted is None or r.position.value == wanted)
        ]
        overall = {int(r.id): int(r.overall_rating) for r in rows}
        views = [self._callup_row(r, callups.get(r.id), suspended, week) for r in rows]
        return sorted(views, key=lambda v: (v.status == CALLUP_NONE,) + self._row_order(v, overall)[1:])

    def squad(self, nation_id: int | None = None) -> list[CallupRow]:
        """Ulusun bu sezonki milli kadrosu (varsayilan: menajerin ulusu). XI, kulube, kadro disi sirasiyla."""
        if nation_id is None:
            nation = self._my_nation_row()
        else:
            self._require_enabled()
            key = _int_id(nation_id)
            nation = self.db.get(Nation, key) if key is not None else None
            if nation is None:
                raise NationalTeamError(NATION_NOT_FOUND_TEXT)
        season, week = self.cm.season, self.cm.current_week
        callups = self._callups(nation.id, season)
        info = self._info_rows(c.player_id for c in callups)
        suspended = self._red_carded(nation.id)
        views = [self._callup_row(info[c.player_id], c, suspended, week) for c in callups if c.player_id in info]
        overall = {pid: int(row.overall_rating) for pid, row in info.items()}
        return sorted(views, key=lambda v: self._row_order(v, overall))

    def set_callups(self, player_ids: Sequence[int]) -> list[str]:
        """
        Kadro cagrisi (tum liste). Hata -> NationalTeamError (hicbir sey degismez). Kalan oyuncularin ilk 11 /
        kulube karari korunur, yeniler kulubeye. Donus: uyarilar (Turkce).
        """
        nation = self._my_nation_row()
        season, week = self.cm.season, self.cm.current_week
        ids: list[int] = []
        for pid in player_ids:
            key = _int_id(pid)
            if key is None:
                raise NationalTeamError("Geçersiz oyuncu seçimi.")
            ids.append(key)
        taken = self._called_nations(season)
        rows = {int(r.id): r for r in self._pool().get(nation.name, []) if taken.get(r.id, nation.id) == nation.id}
        errors = national_rules.validate_callups(ids, rows)
        if errors:
            raise NationalTeamError(" ".join(errors))
        wanted = set(ids)
        existing = self._callups(nation.id, season)
        for callup in existing:
            if callup.player_id not in wanted:
                self.db.delete(callup)
        self.db.flush()
        have = {c.player_id for c in existing}
        for pid in ids:
            if pid not in have:
                self.db.add(NationalCallup(nation_id=nation.id, season=season, player_id=pid,
                                           lineup_status=LineupStatus.BENCH.value, lineup_role=None, intl_suspended=0))
        self.db.flush()
        warnings: list[str] = []
        keepers = sum(1 for pid in ids if rows[pid].position is Position.GK)
        if keepers < 2:
            warnings.append(f"Kadroda {keepers} kaleci var; en az 2 kaleci çağırman önerilir.")
        injured = [rows[pid].name for pid in ids if int(rows[pid].injured_until_week or 0) > week]
        if injured:
            warnings.append(f"Sakat oyuncular maç kadrosuna giremez: {', '.join(injured)}.")
        suspended = self._red_carded(nation.id) & wanted
        if suspended:
            warnings.append("Milli maç cezalı oyuncular bir sonraki maçta oynayamaz: "
                            + ", ".join(rows[pid].name for pid in sorted(suspended)) + ".")
        return warnings

    def _squad_players(self, nation: Nation, season: int) -> tuple[list[NationalCallup], list[NationalPlayer]]:
        callups = self._callups(nation.id, season)
        ids = [c.player_id for c in callups]
        full = {row.id: row for row in self.db.execute(select(*_PLAYER_COLUMNS).where(Player.id.in_(ids)))} \
            if ids else {}
        suspended = self._red_carded(nation.id)
        players = [
            NationalPlayer(full[c.player_id], LineupStatus(c.lineup_status), _position(c.lineup_role),
                           c.player_id in suspended)
            for c in callups if c.player_id in full
        ]
        players.sort(key=lambda p: (-int(p.overall_rating), int(p.id)))      # Team.players ile ayni sira
        return callups, players

    @staticmethod
    def _apply_lineup(callups: Sequence[NationalCallup], xi: Mapping[int, Position], bench: Iterable[int]) -> None:
        bench_ids = set(bench)
        for callup in callups:
            if callup.player_id in xi:
                callup.lineup_status, callup.lineup_role = LineupStatus.XI.value, xi[callup.player_id].value
            elif callup.player_id in bench_ids:
                callup.lineup_status, callup.lineup_role = LineupStatus.BENCH.value, None
            else:
                callup.lineup_status, callup.lineup_role = LineupStatus.OUT.value, None

    def set_lineup(self, xi: Mapping[int, Position], bench: Sequence[int]) -> LineupCheck:
        """Milli ilk 11 ve kulube (tactics.validate_lineup; milli uygunluk). Hata varsa hicbir sey degismez."""
        nation = self._my_nation_row()
        try:
            chosen = {int(pid): _position(role) for pid, role in dict(xi).items()}
            bench_ids = [int(pid) for pid in bench]
        except (TypeError, ValueError) as exc:
            raise NationalTeamError("Geçersiz ilk 11 seçimi.") from exc
        callups, players = self._squad_players(nation, self.cm.season)
        check = validate_lineup(players, nation.formation, self.cm.current_week, chosen, bench_ids)
        if check.ok:
            self._apply_lineup(callups, chosen, bench_ids)
            self.db.flush()
        return check

    def auto_lineup(self) -> None:
        """Asistan: milli kadrodan uygun en iyi 11 ve kulube."""
        nation = self._my_nation_row()
        week = self.cm.current_week
        callups, players = self._squad_players(nation, self.cm.season)
        xi = pick_best_xi(players, nation.formation, week)
        self._apply_lineup(callups, xi, pick_bench(players, xi, week))
        self.db.flush()

    def set_formation(self, name: str) -> LineupCheck:
        """Milli takimin dizilisi (tactics.FORMATIONS). Donus: mevcut ilk 11'in yeni dizilisle denetimi."""
        nation = self._my_nation_row()
        if name not in FORMATIONS:
            raise NationalTeamError(f"Bilinmeyen diziliş: {name}. Seçenekler: {', '.join(FORMATIONS)}")
        nation.formation = name
        self.db.flush()
        callups, players = self._squad_players(nation, self.cm.season)
        xi = {c.player_id: Position(c.lineup_role) for c in callups
              if c.lineup_status == LineupStatus.XI.value and c.lineup_role}
        bench = [c.player_id for c in callups if c.lineup_status == LineupStatus.BENCH.value]
        return validate_lineup(players, name, self.cm.current_week, xi, bench)

    # ------------------------------------------------------------------ fikstur, gruplar, agac

    def fixtures(self, competition: str | None = None, season: int | None = None) -> list[IntlFixtureView]:
        if not self.enabled():
            return []
        season = self.cm.season if season is None else season
        kinds = [competition] if competition in COMPETITIONS else list(COMPETITIONS)
        views: list[IntlFixtureView] = []
        for kind in kinds:
            t = self._tournament(season, kind)
            if t is None:
                continue
            fixtures = self._fixtures(t)
            names = {n.id: n.name for n in self._nations_by_id(
                {fx.home_nation_id for fx in fixtures} | {fx.away_nation_id for fx in fixtures}).values()}
            for fx in fixtures:
                if fx.stage == world_cup.GROUP:
                    stage = f"Grup {world_cup.group_letter(fx.group_index or 0)} · {fx.leg}. maç"
                else:
                    stage = world_cup.stage_label(fx.stage)
                week_label = f"{fx.week}. hafta" if fx.week is not None else f"Sezon arası {fx.close_season_day}. gün"
                played = fx.status == FX_PLAYED
                score = None
                if played:
                    score = f"{fx.home_score}-{fx.away_score}" + (" (uzt.)" if fx.extra_time else "")
                views.append(IntlFixtureView(
                    id=fx.id, competition=COMPETITION_LABELS[kind], stage_label=stage, week_label=week_label,
                    home=names.get(fx.home_nation_id, "?"), away=names.get(fx.away_nation_id, "?"), score=score,
                    penalties=(f"{fx.home_penalties}-{fx.away_penalties}"
                               if played and fx.home_penalties is not None else None),
                ))
        return views

    def _group_standings(self, t: InternationalTournament, entries: Sequence[InternationalEntry],
                         fixtures: Sequence[InternationalFixture], nations: Mapping[int, Nation]):
        by_group: dict[int, list[int]] = defaultdict(list)
        for entry in entries:
            if entry.group_index is not None:
                by_group[entry.group_index].append(entry.nation_id)
        groups = [by_group[g] for g in sorted(by_group)]
        results = [(fx.home_nation_id, fx.away_nation_id, fx.home_score, fx.away_score)
                   for fx in fixtures if fx.stage == world_cup.GROUP and fx.status == FX_PLAYED]
        reps = {nid: float(nations[nid].reputation) for nid in nations}
        names = {nid: nations[nid].name for nid in nations}
        return groups, world_cup.group_standings(groups, results, reps, names), reps

    def group_tables(self, competition: str) -> list[tuple[str, list[dict]]]:
        """
        Grup tablolari: [("Grup A", satirlar)]. Satir anahtarlari bracket_view.GroupRowView alanlaridir (position,
        team, played, won, drawn, lost, goals_for, goals_against, points, qualified, highlight):
        GroupRowView(**satir). Elemelerde qualified: (oynanan maclara gore) Dunya Kupasi'na katilan.
        """
        if not self.enabled() or competition not in COMPETITIONS:
            return []
        t = self._tournament(self.cm.season, competition)
        if t is None:
            return []
        entries = self._entries(t)
        if not any(e.group_index is not None for e in entries):
            return []
        nations = self._nations_by_id(e.nation_id for e in entries)
        _groups, standings, reps = self._group_standings(t, entries, self._fixtures(t), nations)
        if competition == QUALIFIER:
            size = self._wc_size(t) or self._wc_size(self._tournament(t.season, WORLD_CUP))
            if t.status == T_FINISHED:
                qualified = {e.nation_id for e in entries if e.eliminated_stage is None}
            else:
                total = sum(len(table) for table in standings)
                qualified = set(world_cup.qualified_from_groups(standings, size, reps)) if 0 < size <= total else set()
        else:
            qualified = {row.team_id for table in standings for row in table[:world_cup.GROUP_ADVANCE]}
        mine = self._my_nation_id()
        return [
            (f"Grup {world_cup.group_letter(index)}", [
                {"position": pos, "team": nations[row.team_id].name, "played": row.played, "won": row.won,
                 "drawn": row.drawn, "lost": row.lost, "goals_for": row.goals_for,
                 "goals_against": row.goals_against, "points": row.points,
                 "qualified": row.team_id in qualified, "highlight": row.team_id == mine}
                for pos, row in enumerate(table, start=1)
            ])
            for index, table in enumerate(standings)
        ]

    def _my_nation_id(self) -> int | None:
        seat = self.cm.acting_seat
        if seat is None or seat.id is None:
            return None
        return self.db.scalar(select(Nation.id).where(Nation.manager_id == seat.id))

    @staticmethod
    def _winner_id(fx: InternationalFixture) -> int | None:
        if fx.status != FX_PLAYED:
            return None
        if fx.home_penalties is not None and fx.home_penalties != fx.away_penalties:
            return fx.home_nation_id if fx.home_penalties > fx.away_penalties else fx.away_nation_id
        if fx.home_score != fx.away_score:
            return fx.home_nation_id if fx.home_score > fx.away_score else fx.away_nation_id
        return None

    def bracket(self) -> list:
        """arena_views.RoundView uyumlu: bracket_view.bracket_html(rounds, champion_name()) ile cizilir."""
        if not self.enabled():
            return []
        wc = self._tournament(self.cm.season, WORLD_CUP)
        size = self._wc_size(wc)
        if wc is None or size not in world_cup.KNOCKOUT_STAGES:
            return []
        fixtures = [fx for fx in self._fixtures(wc) if fx.stage != world_cup.GROUP]
        names = {n.id: n.name for n in self._nations_by_id(
            {fx.home_nation_id for fx in fixtures} | {fx.away_nation_id for fx in fixtures}).values()}
        mine = self._my_nation_id()
        rounds: list[RoundView] = []
        for index, stage in enumerate(world_cup.KNOCKOUT_STAGES[size]):
            by_slot = {int(fx.leg or 1) - 1: fx for fx in fixtures if fx.stage == stage}
            ties: list[TieView] = []
            for slot in range((size // 4) >> index):
                fx = by_slot.get(slot)
                if fx is None:
                    ties.append(TieView(home=None, away=None))
                    continue
                winner_id = self._winner_id(fx)
                if fx.status == FX_PLAYED:
                    legs = [f"{fx.home_score}-{fx.away_score}" + (" uzt." if fx.extra_time else "")]
                else:
                    legs = [f"{fx.close_season_day}. gün"]
                ties.append(TieView(
                    home=names.get(fx.home_nation_id), away=names.get(fx.away_nation_id), legs=legs,
                    note=f"pen. {fx.home_penalties}-{fx.away_penalties}" if fx.home_penalties is not None else None,
                    winner=None if winner_id is None else ("home" if winner_id == fx.home_nation_id else "away"),
                    highlight=mine is not None and mine in (fx.home_nation_id, fx.away_nation_id),
                ))
            rounds.append(RoundView(world_cup.stage_label(stage), ties))
        return rounds

    # ================================================================== mac gunleri

    def play_week(self, week: int, report: Any = None) -> None:
        """Eklenti on_week: kurulum (ilk kez), teklif suresi, haftasi gelen eleme gunleri, teklifler."""
        if not self._active():
            return
        self._pending_notes = []
        self._bootstrap()
        self._vacate_departed()
        self._expire_offers()
        season = self.cm.season
        qualifier = self._tournament(season, QUALIFIER)
        if qualifier is not None and qualifier.status != T_FINISHED:
            due = [fx for fx in self._fixtures(qualifier)
                   if fx.status != FX_PLAYED and fx.week is not None and fx.week <= week]
            for leg in sorted({fx.leg for fx in due}):
                self._play_day(qualifier, [fx for fx in due if fx.leg == leg], self._day_title(qualifier, leg), report)
            if due and all(fx.status == FX_PLAYED for fx in self._fixtures(qualifier)):
                self._finish_qualifiers(qualifier, report)
        self._offer_jobs()
        self.db.flush()
        self._flush_report(report, close_season=False)

    def _bootstrap(self) -> None:
        """Eklenti ilk kez calisiyor (ensure_setup cagrilmamis) ya da bu sezonun turnuvasi eksik."""
        count = self.db.scalar(select(func.count()).select_from(Nation)) or 0
        if count == 0:
            notes = self.ensure_setup()
            if notes:
                self._note("Milli takımlar kuruldu", notes)
            return
        season = self.cm.season
        if count < min(world_cup.WORLD_CUP_SIZES) or not intl_calendar.is_world_cup_season(
                season, rules_of(self.cm).world_cup_every_seasons):
            return
        if self.db.scalar(select(func.count()).select_from(InternationalTournament)
                          .where(InternationalTournament.season == season)):
            return
        pool = self._pool()
        self._ensure_callups(season, pool)
        for note in self._ensure_season_tournaments(season, pool):
            self._note(COMPETITION_LABELS[WORLD_CUP], [note])

    def close_season_pending(self) -> bool:
        """Sezon bitti ve bu sezonun elemeleri / Dunya Kupasi bitmedi mi? (salt okuma)"""
        if not self._active() or not self.cm.season_finished:
            return False
        return self._campaign_open(self.cm.season)

    def _campaign_open(self, season: int) -> bool:
        statuses = self.db.scalars(select(InternationalTournament.status)
                                   .where(InternationalTournament.season == season)).all()
        return any(status != T_FINISHED for status in statuses)

    def _remaining_days(self, season: int) -> int:
        remaining = 0
        qualifier = self._tournament(season, QUALIFIER)
        if qualifier is not None and qualifier.status != T_FINISHED:
            remaining += len({fx.leg for fx in self._fixtures(qualifier) if fx.status != FX_PLAYED})
        wc = self._tournament(season, WORLD_CUP)
        if wc is not None and wc.status != T_FINISHED:
            played = {fx.close_season_day for fx in self._fixtures(wc) if fx.status == FX_PLAYED}
            remaining += max(0, len(wc.calendar or []) - len(played))
        return remaining

    def new_season_blocker(self) -> str | None:
        if not self.close_season_pending():
            return None
        remaining = self._remaining_days(self.cm.season)
        return ("Dünya Kupası sürüyor: sezon arası milli maç günleri bitmeden yeni sezon başlamaz "
                f"(kalan {remaining} maç günü).")

    def play_close_season_matchday(self, report: Any = None) -> bool:
        """Sezon arasi bir milli mac gunu (kalan eleme gunu ya da Dunya Kupasi gunu). Oynandiysa True."""
        if not self.close_season_pending():
            return False
        self._pending_notes = []
        season = self.cm.season
        qualifier = self._tournament(season, QUALIFIER)
        if qualifier is not None and qualifier.status != T_FINISHED:
            unplayed = [fx for fx in self._fixtures(qualifier) if fx.status != FX_PLAYED]
            if unplayed:
                leg = min(fx.leg for fx in unplayed)
                self._play_day(qualifier, [fx for fx in unplayed if fx.leg == leg],
                               self._day_title(qualifier, leg), report)
            if all(fx.status == FX_PLAYED for fx in self._fixtures(qualifier)):
                self._finish_qualifiers(qualifier, report)
            self.db.flush()
            self._flush_report(report, close_season=True)
            return True
        wc = self._tournament(season, WORLD_CUP)
        if wc is None or wc.status == T_FINISHED:
            return False
        if wc.status == T_DRAW:
            entrants = self._direct_entrants(wc, qualifier)
            if len(entrants) != self._wc_size(wc):
                wc.status = T_FINISHED               # ulus sayisi turnuvaya yetmiyor: sezon kilitli kalmasin
                self._note(COMPETITION_LABELS[WORLD_CUP], ["Yeterli milli takım kalmadığı için bu sezonun "
                                                           "Dünya Kupası iptal edildi."])
                self.db.flush()
                self._flush_report(report, close_season=True)
                return True
            self._note("Dünya Kupası kurası", self._draw_world_cup(wc, entrants))
        if not self._play_world_cup_day(wc, report):
            wc.status = T_FINISHED                   # tutarsiz veri: oynanacak gun yok, sezon kilitli kalmasin
            self._note(COMPETITION_LABELS[WORLD_CUP], ["Dünya Kupası tamamlanamadı; yeni sezona geçilebilir."])
        self.db.flush()
        self._flush_report(report, close_season=True)
        return True

    def _direct_entrants(self, wc: InternationalTournament, qualifier: InternationalTournament | None) -> list[int]:
        size = self._wc_size(wc)
        if qualifier is not None:
            ids = [e.nation_id for e in self._entries(qualifier) if e.eliminated_stage is None]
            if len(ids) == size:
                return ids
        names = self._eligible_names(self._pool())
        nations = list(self.db.scalars(select(Nation).where(Nation.name.in_(names))))
        if len(nations) < size:
            nations = list(self.db.scalars(select(Nation)))
        seeds = [world_cup.NationSeed(n.id, n.name, n.reputation) for n in nations]
        return [s.nation_id for s in world_cup.rank_by_reputation(seeds)[:size]]

    def _day_title(self, t: InternationalTournament, leg: int | None, stage: str = world_cup.GROUP) -> str:
        if t.kind == QUALIFIER:
            return f"{COMPETITION_LABELS[QUALIFIER]} · {leg}. maç günü"
        if stage == world_cup.GROUP:
            return f"{COMPETITION_LABELS[WORLD_CUP]} · {world_cup.stage_label(stage)} {leg}. maç"
        return f"{COMPETITION_LABELS[WORLD_CUP]} · {world_cup.stage_label(stage)}"

    def _play_world_cup_day(self, wc: InternationalTournament, report: Any) -> bool:
        unplayed = [fx for fx in self._fixtures(wc) if fx.status != FX_PLAYED]
        if not unplayed:
            self._advance_world_cup(wc, report)
            unplayed = [fx for fx in self._fixtures(wc) if fx.status != FX_PLAYED]
            if not unplayed:
                return False
        day = min(fx.close_season_day or 0 for fx in unplayed)
        todays = [fx for fx in unplayed if (fx.close_season_day or 0) == day]
        stage = todays[0].stage
        self._play_day(wc, todays, self._day_title(wc, todays[0].leg, stage), report)
        self._advance_world_cup(wc, report)
        return True

    # ------------------------------------------------------------------ tek mac gunu

    def _play_day(self, t: InternationalTournament, fixtures: Sequence[InternationalFixture], title: str,
                  report: Any) -> None:
        season, week = t.season, self.cm.current_week
        config = self._engine_config()
        pool = self._pool()
        taken = self._called_nations(season)
        involved = {fx.home_nation_id for fx in fixtures} | {fx.away_nation_id for fx in fixtures}
        nations = self._nations_by_id(involved)
        entries = {e.nation_id: e for e in self._entries(t)}
        humans = self.cm.human_team_ids()
        lines: list[str] = []
        for fx in sorted(fixtures, key=lambda f: (f.group_index if f.group_index is not None else -1, f.leg or 0, f.id)):
            home, away = nations[fx.home_nation_id], nations[fx.away_nation_id]
            squads = [self._matchday_squad(nation, season, week, pool, taken) for nation in (home, away)]
            result = self._simulate(fx, squads, config, week)
            self._store_result(fx, result)
            self._record_caps(result)
            self._mirror_suspensions(result, season)
            outcomes = self._outcomes(fx, result)
            if fx.stage == world_cup.GROUP:
                if fx.home_nation_id in entries and fx.away_nation_id in entries:
                    update_standings(entries[fx.home_nation_id], fx.home_score, fx.away_score)
                    update_standings(entries[fx.away_nation_id], fx.away_score, fx.home_score)
            else:
                loser = fx.away_nation_id if outcomes[fx.home_nation_id] == "W" else fx.home_nation_id
                if loser in entries:
                    entries[loser].eliminated_stage = fx.stage
            score = _score_text(fx, {home.id: home.name, away.id: away.name})
            lines.append(score)
            stage = None if t.kind == QUALIFIER else fx.stage
            for nation in (home, away):
                if nation.manager_id is None:
                    continue
                outcome = outcomes[nation.id]
                won_final = fx.stage == world_cup.FINAL and t.kind == WORLD_CUP and outcome == "W"
                delta = national_rules.intl_reputation_delta(outcome, stage, won_final)
                change = self._apply_manager_reputation(nation, delta, report, humans)
                text = f"{title}: {score}."
                if change is not None:
                    text += f" Tanınırlık {change[0]:.2f} → {change[1]:.2f}."
                self._notify(nation.manager_id, text, REF_FIXTURE, fx.id)
        self.db.flush()
        self._note(title, lines, matchday=True)

    def _matchday_squad(self, nation: Nation, season: int, week: int, pool: Mapping[str, Sequence],
                        taken: dict[int, int]) -> NationalSquad:
        """
        Mac kadrosu. Ulusu degisen / kulupsuz kalan oyuncu kadrodan duser. AI (menajersiz) ulus her mac gunu
        AI kadrosunu yeniden cagirir (sakat ve cezali haric). Insan menajerin kadrosu korunur; hic kadro yoksa ya da
        uygun oyuncu MIN_MATCHDAY_SQUAD'in altindaysa asistan en fazla MAX_CALLUPS'a kadar tamamlar (bildirim).
        """
        rows = [r for r in pool.get(nation.name, []) if taken.get(r.id, nation.id) == nation.id]
        by_id = {int(r.id): r for r in rows}
        suspended = self._red_carded(nation.id)
        callups = self._callups(nation.id, season)
        stale = [c for c in callups if c.player_id not in by_id]
        for callup in stale:
            self.db.delete(callup)
            taken.pop(callup.player_id, None)
        if stale:
            self.db.flush()
            callups = [c for c in callups if c.player_id in by_id]

        def usable(row) -> bool:
            return int(row.id) not in suspended and int(row.injured_until_week or 0) <= week

        if nation.manager_id is None:
            picks = national_rules.ai_callups([r for r in rows if int(r.id) not in suspended],
                                              national_rules.DEFAULT_CALLUPS, week=week)
            wanted = set(picks)
            for callup in callups:
                if callup.player_id not in wanted:
                    self.db.delete(callup)
                    taken.pop(callup.player_id, None)
                else:
                    callup.lineup_status, callup.lineup_role = LineupStatus.BENCH.value, None
            self.db.flush()
            have = {c.player_id for c in callups}
            self._add_callups(nation.id, season, [pid for pid in picks if pid not in have], taken)
        else:
            notes: list[str] = []
            called = {c.player_id for c in callups}
            available = sum(1 for pid in called if usable(by_id[pid]))
            needed = national_rules.MIN_MATCHDAY_SQUAD - available
            if not callups:
                needed = national_rules.DEFAULT_CALLUPS
            room = national_rules.MAX_CALLUPS - len(called)
            if needed > 0 and room > 0:
                extra = national_rules.ai_callups([r for r in rows if int(r.id) not in called and usable(r)],
                                                  min(needed, room), week=week)
                if extra:
                    self._add_callups(nation.id, season, extra, taken)
                    notes.append(f"Asistan {nation.name} kadrosunu {len(extra)} oyuncuyla tamamladı: "
                                 + ", ".join(by_id[pid].name for pid in extra) + ".")
            for note in notes:
                self._notify(nation.manager_id, note, REF_NATION, nation.id)
        _callups, players = self._squad_players(nation, season)
        return NationalSquad(id=nation.id, name=nation.name, reputation=int(nation.reputation),
                             formation=nation.formation, players=players)

    def _add_callups(self, nation_id: int, season: int, ids: Sequence[int], taken: dict[int, int]) -> None:
        for pid in ids:
            self.db.add(NationalCallup(nation_id=nation_id, season=season, player_id=pid,
                                       lineup_status=LineupStatus.BENCH.value, lineup_role=None, intl_suspended=0))
            taken[pid] = nation_id
        self.db.flush()

    def _simulate(self, fx: InternationalFixture, squads: Sequence[NationalSquad], config: EngineConfig,
                  week: int) -> MatchResult:
        def reason(player: NationalPlayer) -> str | None:
            return player.unavailability_reason(week)

        home = build_match_team(squads[0], True, week, reason)
        away = build_match_team(squads[1], False, week, reason)
        knockout = KnockoutRule() if fx.stage != world_cup.GROUP else None
        engine = MatchEngine(home, away, seed=self._match_seed(fx), config=config, knockout=knockout,
                             neutral_venue=bool(fx.neutral))
        if self.cm.ai_tactics:
            for side in (engine.home, engine.away):
                engine.set_roles(side, team_roles.suggest_roles(side.on_pitch))
        return engine.simulate()

    @staticmethod
    def _store_result(fx: InternationalFixture, result: MatchResult) -> None:
        fx.home_score, fx.away_score = result.home_score, result.away_score
        fx.status = FX_PLAYED
        fx.extra_time = bool(result.extra_time)
        if result.shootout is not None:
            fx.home_penalties, fx.away_penalties = result.home_penalties, result.away_penalties
        fx.key_events = [_event_dict(ev) for ev in result.events
                         if getattr(ev.type, "value", ev.type) in KEY_EVENT_TYPES]

    def _record_caps(self, result: MatchResult) -> None:
        """Kulup oyuncu satirina yazilan TEK sey: milli mac ve milli gol sayilari."""
        for side in (result.home, result.away):
            played = sorted(mp.id for mp in side.players if mp.played)
            if played:
                self.db.execute(update(Player).where(Player.id.in_(played))
                                .values(international_caps=Player.international_caps + 1))
            scorers: dict[int, list[int]] = defaultdict(list)
            for mp in side.players:
                if mp.goals:
                    scorers[int(mp.goals)].append(mp.id)
            for goals, ids in sorted(scorers.items()):
                self.db.execute(update(Player).where(Player.id.in_(sorted(ids)))
                                .values(international_goals=Player.international_goals + goals))

    def _mirror_suspensions(self, result: MatchResult, season: int) -> None:
        """national_callups.intl_suspended: bu macta atilanlar 1, cezasini ceken 0 (gosterim; kaynak key_events)."""
        for side in (result.home, result.away):
            sent_off = {mp.id for mp in side.players if mp.sent_off}
            for callup in self._callups(side.id, season):
                callup.intl_suspended = 1 if callup.player_id in sent_off else 0

    @staticmethod
    def _outcomes(fx: InternationalFixture, result: MatchResult) -> dict[int, str]:
        home, away = fx.home_nation_id, fx.away_nation_id
        if fx.stage == world_cup.GROUP:
            return {home: _outcome(fx.home_score, fx.away_score), away: _outcome(fx.away_score, fx.home_score)}
        advancing = result.advancing
        winner = advancing.id if advancing is not None else NationalTeams._winner_id(fx)
        if winner is None:                      # KnockoutRule penalti actigindan olusmaz
            winner = home
        return {home: "W" if winner == home else "L", away: "W" if winner == away else "L"}

    def _apply_manager_reputation(self, nation: Nation, delta: float, report: Any,
                                  humans: frozenset[int]) -> tuple[float, float] | None:
        """Milli menajerin taninirligi: insan kulubu -> CareerManager (rapor + SeatStore), kulupsuz -> satir."""
        if nation.manager_id is None:
            return None
        row = self.db.get(WorldManager, nation.manager_id)
        if row is None:
            return None
        state = self.cm.state
        if row.is_primary:
            team_id = state.user_team_id
            before = float(state.manager_reputation)
        else:
            team_id = row.team_id if row.status == SeatStatus.ACTIVE.value else None
            before = float(row.reputation) if row.reputation is not None else reputation.START_REPUTATION
        if not delta:
            return before, before
        if team_id is not None and team_id in humans:
            if report is not None and hasattr(report, "clubs"):
                self.cm._apply_reputation_delta(delta, report, team_id)
            else:
                self.cm.seats.apply_reputation(team_id, delta)
        elif row.is_primary:
            state.manager_reputation = reputation.apply(before, delta)
        else:
            row.reputation = reputation.apply(before, delta)
        after = float(state.manager_reputation) if row.is_primary else float(row.reputation)
        return before, after

    # ------------------------------------------------------------------ elemeler ve Dunya Kupasi turlari

    def _finish_qualifiers(self, qualifier: InternationalTournament, report: Any) -> None:
        entries = self._entries(qualifier)
        nations = self._nations_by_id(e.nation_id for e in entries)
        _groups, standings, reps = self._group_standings(qualifier, entries, self._fixtures(qualifier), nations)
        wc = self._tournament(qualifier.season, WORLD_CUP)
        size = self._wc_size(wc) or self._wc_size(qualifier)
        qualified = world_cup.qualified_from_groups(standings, size, reps)
        chosen = set(qualified)
        for entry in entries:
            if entry.nation_id not in chosen:
                entry.eliminated_stage = world_cup.QUAL
        qualifier.status = T_FINISHED
        humans = self.cm.human_team_ids()
        for nation_id in sorted(nations):
            nation = nations[nation_id]
            if nation.manager_id is None:
                continue
            if nation_id in chosen:
                delta = national_rules.intl_reputation_delta(national_rules.QUALIFIED, world_cup.QUAL, False)
                change = self._apply_manager_reputation(nation, delta, report, humans)
                text = f"{nation.name} Dünya Kupası'na katılmaya hak kazandı!"
                if change is not None:
                    text += f" Tanınırlık {change[0]:.2f} → {change[1]:.2f}."
            else:
                text = f"{nation.name} Dünya Kupası elemelerinde elendi."
            self._notify(nation.manager_id, text, REF_NATION, nation_id)
        lines = ["Dünya Kupası'na katılanlar: " + ", ".join(nations[nid].name for nid in qualified) + "."]
        if wc is not None:
            lines += self._draw_world_cup(wc, qualified)
        self.db.flush()
        self._note(f"{COMPETITION_LABELS[QUALIFIER]} tamamlandı", lines)

    def _advance_world_cup(self, wc: InternationalTournament, report: Any) -> None:
        """Tur bittiyse: gruplardan eleme agaci, sonraki tur ya da sampiyon."""
        fixtures = self._fixtures(wc)
        if not fixtures or any(fx.status != FX_PLAYED for fx in fixtures):
            return
        size = self._wc_size(wc)
        plan = world_cup.plan(size)
        days = plan.stage_days()
        last = max((fx.stage for fx in fixtures), key=world_cup.STAGE_ORDER.index)
        entries = self._entries(wc)
        nations = self._nations_by_id(e.nation_id for e in entries)
        humans = self.cm.human_team_ids()
        if last == world_cup.GROUP:
            _groups, standings, _reps = self._group_standings(wc, entries, fixtures, nations)
            rankings = world_cup.group_rankings(standings)
            by_nation = {e.nation_id: e for e in entries}
            lines: list[str] = []
            for index, ranking in enumerate(rankings):
                for nation_id in ranking[world_cup.GROUP_ADVANCE:]:
                    by_nation[nation_id].eliminated_stage = world_cup.GROUP
                lines.append(f"Grup {world_cup.group_letter(index)}: "
                             + ", ".join(nations[nid].name for nid in ranking[:world_cup.GROUP_ADVANCE])
                             + " üst tura çıktı.")
                for position, nation_id in enumerate(ranking):
                    nation = nations[nation_id]
                    if nation.manager_id is None:
                        continue
                    if position < world_cup.GROUP_ADVANCE:
                        delta = national_rules.intl_reputation_delta(national_rules.QUALIFIED, world_cup.GROUP, False)
                        change = self._apply_manager_reputation(nation, delta, report, humans)
                        text = f"{nation.name} Dünya Kupası'nda gruptan çıktı!"
                        if change is not None:
                            text += f" Tanınırlık {change[0]:.2f} → {change[1]:.2f}."
                    else:
                        text = f"{nation.name} Dünya Kupası'nda grupta elendi."
                    self._notify(nation.manager_id, text, REF_NATION, nation_id)
            first = plan.knockout_stages[0]
            self._create_knockout(wc, first, world_cup.knockout_pairs(rankings), days)
            self._note(f"{COMPETITION_LABELS[WORLD_CUP]} · grup aşaması tamamlandı", lines)
            return
        stage_fixtures = sorted((fx for fx in fixtures if fx.stage == last), key=lambda f: f.leg or 0)
        winners = [self._winner_id(fx) for fx in stage_fixtures]
        if last == world_cup.FINAL:
            self._crown(wc, stage_fixtures[0], winners[0], nations)
            return
        following = plan.knockout_stages[plan.knockout_stages.index(last) + 1]
        self._create_knockout(wc, following, world_cup.next_knockout_pairs(winners), days)

    def _create_knockout(self, wc: InternationalTournament, stage: str, pairs: Sequence[tuple[int, int]],
                         days: Sequence[tuple[str, int]]) -> None:
        day = list(days).index((stage, 1)) + 1
        for slot, (home_id, away_id) in enumerate(pairs):
            self.db.add(InternationalFixture(
                tournament_id=wc.id, season=wc.season, stage=stage, leg=slot + 1, group_index=None, week=None,
                close_season_day=day, home_nation_id=home_id, away_nation_id=away_id, status=FX_UNPLAYED,
                key_events=[], neutral=True,
            ))
        self.db.flush()

    def _crown(self, wc: InternationalTournament, final: InternationalFixture, champion_id: int | None,
               nations: Mapping[int, Nation]) -> None:
        """Final oynandi: sampiyon arsivlenir (turnuva satiri, haber, dunya olayi, pano)."""
        wc.champion_nation_id = champion_id
        wc.status = T_FINISHED
        champion = nations.get(champion_id)
        runner_id = final.away_nation_id if champion_id == final.home_nation_id else final.home_nation_id
        runner = nations.get(runner_id)
        name = champion.name if champion is not None else "?"
        text = f"{name}, {wc.season}. sezon Dünya Kupası şampiyonu oldu!"
        if runner is not None:
            text += f" Finalde {_score_text(final, {n.id: n.name for n in nations.values()})}."
        self.db.add(NewsItem(season=wc.season, week=max(1, int(self.cm.current_week)), kind=NEWS_KIND,
                             text=text[:300], team_id=None, other_team_id=None))
        self._event(champion.manager_id if champion is not None else None,
                    {"action": "CHAMPION", "nation_id": champion_id, "season": wc.season, "text": text})
        try:
            with self.db.begin_nested():
                messaging.system_post(self.db, text)
        except (ValueError, NotImplementedError):
            pass
        self.db.flush()
        self._note(f"{COMPETITION_LABELS[WORLD_CUP]} şampiyonu: {name}", [text])

    # ================================================================== sezon donusumu

    def season_end(self) -> None:
        """Eklenti on_season_end (sezon numarasi artmadan): kampanya degerlendirmesi ve biten sozlesmeler."""
        if not self._active():
            return
        season = self.cm.season
        self._vacate_departed()
        rows = self.db.execute(
            select(Nation, WorldManager).join(WorldManager, WorldManager.id == Nation.manager_id).order_by(Nation.id)
        ).all()
        for nation, seat_row in rows:
            review = self._campaign_review(nation, seat_row, season)
            if review is not None and review[0]:
                self._vacate(nation, seat_row, "SACKED", f"{seat_row.display_name}: {review[1]}")
                self._notify(seat_row.id, f"{nation.name}: {review[1]}", REF_NATION, nation.id)
                continue
            if review is not None:
                self._notify(seat_row.id, f"{nation.name}: {review[1]}", REF_NATION, nation.id)
            until = nation.contract_until_season
            if until is not None and until <= season:
                self._vacate(nation, seat_row, "CONTRACT_END",
                             f"{seat_row.display_name} ile {nation.name} milli takımının sözleşmesi sona erdi.")
                self._notify(seat_row.id, f"{nation.name} milli takımıyla sözleşmen sona erdi. Yeni teklifler "
                                          "sezon başında gelecek.", REF_NATION, nation.id)
        self.db.flush()

    def _campaign_review(self, nation: Nation, seat_row: WorldManager, season: int) -> tuple[bool, str] | None:
        """Dunya Kupasi kampanyasinin federasyon degerlendirmesi (sack_decision). Kampanya yoksa None."""
        qualifier, wc = self._tournament(season, QUALIFIER), self._tournament(season, WORLD_CUP)
        size = self._wc_size(wc)
        if wc is None or size == 0:
            return None
        q_entries = {e.nation_id for e in self._entries(qualifier)} if qualifier is not None else set()
        wc_entries = {e.nation_id: e for e in self._entries(wc)}
        field_ids = q_entries or set(wc_entries)
        if nation.id not in field_ids and nation.id not in wc_entries:
            return None
        played = [fx for t in (qualifier, wc) if t is not None for fx in self._fixtures(t)
                  if fx.status == FX_PLAYED and nation.id in (fx.home_nation_id, fx.away_nation_id)]
        if not played:
            return None
        state = self.cm.state
        offset = int(state.career_week_offset or 0)
        q_weeks = [fx.week for fx in self._fixtures(qualifier) if fx.week is not None] if qualifier else []
        start = offset + (min(q_weeks) if q_weeks else self.cm.league_weeks() + 1)
        hired = self.db.scalar(
            select(WorldEvent.career_week)
            .where(WorldEvent.kind == WorldEventKind.NATIONAL.value, WorldEvent.actor_manager_id == seat_row.id,
                   WorldEvent.payload["action"].astext == "ACCEPT",
                   WorldEvent.payload["nation_id"].astext == str(nation.id))
            .order_by(WorldEvent.id.desc()).limit(1)
        )
        if hired is not None and int(hired) > start:
            return None                         # kampanyanin ortasinda goreve geldi: degerlendirilmez
        field_nations = self._nations_by_id(field_ids | set(wc_entries))
        seeds = [world_cup.NationSeed(n.id, n.name, n.reputation) for n in field_nations.values()]
        rank = next(i for i, s in enumerate(world_cup.rank_by_reputation(seeds), start=1) if s.nation_id == nation.id)
        expected = national_rules.expected_stage(rank, size)
        if wc.champion_nation_id == nation.id:
            reached = world_cup.CHAMPION
        elif nation.id in wc_entries:
            reached = wc_entries[nation.id].eliminated_stage or world_cup.GROUP
        else:
            reached = world_cup.QUAL
        points = 0
        for fx in played:
            mine, theirs = ((fx.home_score, fx.away_score) if fx.home_nation_id == nation.id
                            else (fx.away_score, fx.home_score))
            points += 3 if mine > theirs else (1 if mine == theirs else 0)
        return national_rules.sack_decision(expected, reached, points / (3 * len(played)))

    def season_start(self, new_season: int) -> None:
        """Eklenti on_season_start: uluslar, kadrolar, turnuvalar ve is teklifleri (+ insan kulubu notlari)."""
        if not self._active():
            return
        self._pending_notes = []
        pool = self._pool()
        self._sync_nations(refresh=True, pool=pool)
        self._vacate_departed()
        self.db.execute(
            update(NationalJobOffer)
            .where(NationalJobOffer.status == OFFER_PENDING, NationalJobOffer.season < new_season)
            .values(status=OFFER_EXPIRED)
        )
        self._ensure_callups(new_season, pool)
        campaign = self._ensure_season_tournaments(new_season, pool)
        offers = self._offer_jobs()
        self.db.flush()
        seat_teams = {s.id: s.team_id for s in self.cm.seats.active() if s.id is not None}
        names = {n.id: n.name for n in self.db.scalars(select(Nation))}
        for seat_id, _offer_id, nation_id in offers:
            team_id = seat_teams.get(seat_id)
            if team_id is not None:
                self._season_note(team_id, f"Milli takım teklifi: {names.get(nation_id, '?')} "
                                           "(🌍 Milli Takım sayfasından kabul et ya da reddet).")
        for nation_id, manager_id in self.db.execute(
                select(Nation.id, Nation.manager_id).where(Nation.manager_id.isnot(None))).all():
            team_id = seat_teams.get(manager_id)
            if team_id is not None:
                for note in campaign:
                    self._season_note(team_id, f"{names.get(nation_id, '?')}: {note}")

    def _season_note(self, team_id: int, text: str) -> None:
        """start_new_season notlari: new_season_notes_by_team (odak kulupte new_season_notes ile esit kalir)."""
        by_team = getattr(self.cm, "new_season_notes_by_team", None)
        if by_team is None:
            return
        acting = getattr(self.cm, "_acting_team_id", None)
        focus = acting() if callable(acting) else self.cm.state.user_team_id
        if team_id == focus:
            if team_id not in by_team:
                by_team[team_id] = list(self.cm.new_season_notes)
            self.cm.new_season_notes.append(text)
        by_team.setdefault(team_id, []).append(text)

    # ================================================================== yardimcilar

    def _event(self, actor_id: int | None, payload: Mapping[str, Any]) -> None:
        state = self.cm.state
        self.db.add(WorldEvent(season=int(state.season), week=int(state.current_week),
                               career_week=int(self.cm.career_week), kind=WorldEventKind.NATIONAL.value,
                               actor_manager_id=actor_id, payload=dict(payload)))

    def _notify(self, seat_id: int | None, text: str, ref_type: str | None = None, ref_id: int | None = None) -> None:
        if seat_id is None:
            return
        try:
            messaging.notify(self.db, seat_id, messaging.NotificationKind.NATIONAL, text, ref_type, ref_id)
        except (ValueError, NotImplementedError):
            pass

    def _note(self, title: str, lines: Sequence[str], matchday: bool = False) -> None:
        if title or lines:
            self._pending_notes.append((title, [line for line in lines if line], matchday))

    def _flush_report(self, report: Any, close_season: bool) -> None:
        """
        Biriken mac gunu ozetleri rapora: WeekReport.intl_notes varsa oraya; sezon arasi (mac oynanmamis) raporda
        cup_label / cup_notes; lig haftasinda honours_notes (tek satir, en fazla REPORT_MAX_RESULTS skor).
        """
        notes, self._pending_notes = self._pending_notes, []
        if report is None or not notes:
            return
        fields = getattr(type(report), "__dataclass_fields__", {})
        if "intl_notes" in fields:
            for title, lines, _matchday in notes:
                report.intl_notes.append(title)
                report.intl_notes.extend(lines)
            return
        quiet = not getattr(report, "results", None) and not getattr(report, "cup_results", None)
        if close_season and quiet and not getattr(report, "cup_label", None) and hasattr(report, "cup_notes"):
            label = next((title for title, _lines, matchday in notes if matchday), notes[0][0])
            report.cup_label = label
            for title, lines, _matchday in notes:
                if title != label:
                    report.cup_notes.append(title)
                report.cup_notes.extend(lines)
            return
        if not hasattr(report, "honours_notes"):
            return
        for title, lines, _matchday in notes:
            shown = [line.rstrip(".") for line in lines[:REPORT_MAX_RESULTS]]
            if len(lines) > REPORT_MAX_RESULTS:
                shown.append(f"… ve {len(lines) - REPORT_MAX_RESULTS} maç daha")
            report.honours_notes.append(f"{title}: {'; '.join(shown)}" if shown else title)


class NationalExtension(CareerExtension):
    """
    Kariyer eklentisi (extensions.load, rules.internationals). Kurucu sorgu atmaz. Hafta: eleme mac gunleri;
    sezon: degerlendirme / kurulum; Dunya Kupasi bitmeden yeni sezon yok. transfer_block_reason ve
    on_player_moved bilincli olarak bos (AI penceresinde her aday icin cagrilir; kadro mac gununde budanir).
    """

    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm
        self.national = NationalTeams(cm)

    def on_week(self, week: int, report) -> None:
        self.national.play_week(week, report)

    def on_season_end(self) -> None:
        self.national.season_end()

    def on_season_start(self, new_season: int) -> None:
        self.national.season_start(new_season)

    def new_season_blocker(self) -> str | None:
        return self.national.new_season_blocker()

    def on_player_moved(self, player, seller_id: int | None, buyer_id: int) -> None:
        return None

    def on_club_released(self, team_id: int) -> None:
        return None                              # milli gorev kulupten bagimsizdir

    def transfer_block_reason(self, player) -> str | None:
        return None

    def close_season_pending(self) -> bool:
        return self.national.close_season_pending()

    def play_close_season_matchday(self, report) -> bool:
        return self.national.play_close_season_matchday(report)
