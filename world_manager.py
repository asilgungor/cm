"""
world_manager.py
================
Paylasilan dunya tur motoru ve yonetim arka ucu (Faz 12 / 14. Asama, 12A). Dunya semasinda calisir.

    WorldController   -> bir menajerin dunyadaki islemleri (kulup alma/birakma, hazir, rapor, yonetici
                         islemleri). Commit ETMEZ; web callback'i (web_common.member_callback) SHARED dunya
                         kilidi altinda cagirir. Kulup her zaman oturumdaki menajerin koltugundan (ctx.user_id)
                         cozulur; rol (OWNER / ADMIN / MEMBER) her islemde accounts.world_memberships'ten AYNI
                         islemde yeniden okunur (ctx.role'e guvenilmez).
    try_advance       -> hafta / sezon ilerletme. KENDI islemini acar: career_context(sema) DISARIDA, sonra
                         database.world_lock(sema, "try_exclusive") (mesgulse AdvanceInProgress, beklemez);
                         GameState FOR UPDATE yeniden okunur, expected=(sezon, hafta) karsilastir-ve-degistir:
                         hafta bu arada ilerlediyse hicbir sey yazmaz (advanced=False). Acik bir oturum varken
                         ASLA cagrilmaz (kilit yukseltme yok: hazir callback'i once commit eder).
    maybe_advance_due -> sayfa yuklenirken: kilitsiz on denetim (sure doldu ve otomatik ilerleme acik, ya da
                         herkes hazir); gerekiyorsa try_advance. Mesgul / gerek yok / izin yok -> None.
    advance_due_worlds-> CLI: python main.py world-tick [--all | --world ID]. Web'in aksine kilidi SINIRLI
                         bekler (wait=True: exclusive + lock_timeout): bekleyen EXCLUSIVE istegin arkasina yeni
                         SHARED callback'ler siralanir, surekli menajer islemleri haftayi aclikta birakmaz.

Ilerleme algoritmasi (try_advance, tek islem, dunya kilidi EXCLUSIVE):
    1) GameState FOR UPDATE; kurallar (paylasilan degilse AdvanceNotAllowed); CAS (sezon, hafta).
    2) turn_rules.decide_advance: READY (tum aktif kuluplu koltuklar hazir) / FORCED (yalnizca OWNER / ADMIN;
       aksi halde AdvanceNotAllowed) / DEADLINE (sure doldu + auto_advance). READY / DEADLINE uymuyorsa
       advanced=False ve Turkce neden (hata degil).
    3) Birincil (sistem) CareerManager(db, seed=dunya tohumu): sezon suruyorsa play_week; sezon bittiyse ve
       eklentide bekleyen sezon arasi milli mac gunu varsa onu (CLOSE_SEASON_MATCHDAY), yoksa start_new_season.
    4) Her aktif kuluplu koltugun raporu manager_week_reports'a (WeekReport.view_for(takim) satirlari; yeni sezon
       notlari new_season_notes_by_team). Ayni anahtar (sezon, hafta, midweek) varsa satirlar EKLENIR.
    5) FORCED / DEADLINE: hazir olmayan ve tur acildiktan sonra hic etkin olmayan koltugun kacirma sayaci +1;
       hazir ya da etkin olanlarinki 0. Sayac rules.max_missed_deadlines'i ASARSA koltuk RELEASED
       (SeatStore.release; kulup rules.protection_weeks hafta AI korumasinda), eklentilere on_club_released.
    6) Tum hazir bayraklari temizlenir (sezon devrinde mutlak kariyer haftasi degismez), yeni tur acilir
       (turn_opened_at = now, turn_deadline_at = now + deadline_hours; otomatik ilerleme kapaliysa bitis yok),
       last_advance_*; world_events ADVANCE /
       RELEASE; bildirimler (messaging.notify B3 paketi hazirsa, savepoint icinde).
Herhangi bir adim hata verirse islem geri alinir: yarim hafta yazilmaz.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

import database
import reputation
import turn_rules
import worlds
from club_directory import plain_key
from models import (
    Fixture,
    FixtureStatus,
    GameState,
    League,
    ManagerWeekReport,
    MembershipRole,
    MembershipStatus,
    Nation,
    Player,
    SeatStatus,
    Team,
    User,
    World,
    WorldEvent,
    WorldEventKind,
    WorldKind,
    WorldManager,
    WorldMembership,
    WorldStatus,
)
from seats import SeatError, SeatStore
from stars import star_value
from turn_rules import AdvanceTrigger
from world_rules import RulesError, WorldRules
from worlds import WorldError

if TYPE_CHECKING:
    from seats import Seat
    from worlds import WorldContext

log = logging.getLogger(__name__)

KIND_WEEK = "WEEK"
KIND_NEW_SEASON = "NEW_SEASON"
KIND_CLOSE_SEASON = "CLOSE_SEASON_MATCHDAY"
KIND_NONE = "NONE"
PHASE_SEASON = "SEASON"
PHASE_SEASON_END = "SEASON_END"
PHASE_CLOSE_SEASON = "CLOSE_SEASON"
SYSTEM_ROLE = "SYSTEM"                      # advance_due_worlds baglami (user_id 0: zorlama yetkisi yok)

BUSY_TEXT = "Dünya şu an haftayı oynatıyor ya da bir menajer işlem yapıyor; birkaç saniye sonra tekrar dene."
MOVED_TEXT = "Hafta zaten ilerledi."
STALE_TEXT = "Hafta bu arada ilerledi; güncel durumu görüp tekrar dene."
ADMIN_ONLY_TEXT = "Bu işlem için dünyanın sahibi ya da yöneticisi olmalısın."
NOT_SHARED_TEXT = "Bu dünya paylaşılan bir dünya değil; hafta 'Sonraki haftayı oyna' ile ilerler."
NO_SEAT_TEXT = "Bu dünyada menajer koltuğun yok."
KICK_REASON_MAX = 200
NOTIFICATION_MAX = 300

SEATED_STATUSES = (SeatStatus.ACTIVE.value, SeatStatus.RELEASED.value)
RELEASE_INACTIVE = "INACTIVE"
RELEASE_VOLUNTARY = "VOLUNTARY"
RELEASE_KICK = "KICK"


class TurnError(ValueError):
    """Tur islemi yapilamaz (mesaj Turkce)."""


class AdvanceNotAllowed(TurnError):
    pass


class AdvanceInProgress(TurnError):
    pass


class StaleTurnError(TurnError):
    pass


class ClubUnavailableError(WorldError):
    pass


@dataclass(frozen=True)
class TurnStatus:
    season: int
    week: int
    career_week: int
    phase: str                        # SEASON / SEASON_END / CLOSE_SEASON
    opened_at: datetime | None
    deadline_at: datetime | None
    seconds_left: int | None
    active: int
    ready: int
    ready_names: list[str]
    waiting_names: list[str]
    me_ready: bool
    can_force: bool
    can_ready: bool
    auto_due: bool


@dataclass(frozen=True)
class AdvanceResult:
    advanced: bool
    kind: str                         # WEEK / NEW_SEASON / CLOSE_SEASON_MATCHDAY / NONE
    trigger: AdvanceTrigger | None
    season: int
    week: int
    released: list[str]
    message: str
    world_id: int | None = None       # A3 eki: hangi dunya (CLI world-tick ciktisi)


@dataclass(frozen=True)
class ClubOffer:
    team_id: int
    team_name: str
    league_name: str
    reputation: int
    stars: float
    squad_rating: float
    transfer_budget: int
    eligible: bool
    reason: str
    protected: bool
    league_id: int | None = None           # Faz 13G: ulke -> lig -> kulup secici
    country: str = ""
    stadium_capacity: int | None = None


@dataclass(frozen=True)
class ManagerRow:
    seat_id: int
    user_id: int | None
    name: str
    role: str | None
    team_name: str | None
    reputation: float
    level_title: str
    status: str
    ready: bool
    last_active_at: datetime | None
    missed_deadlines: int
    fair_play: float
    nation_name: str | None


@dataclass(frozen=True)
class ManagerReport:
    season: int
    week: int
    midweek: bool
    lines: list[tuple[str, str]]
    cup_lines: list[tuple[str, str]]


@dataclass(frozen=True)
class WorldEventView:
    """world_events satirinin okunur hali (plan 2.1'de adi gecer, alanlari burada sabitlendi)."""
    id: int
    season: int
    week: int
    career_week: int
    kind: str
    actor_name: str | None
    text: str
    created_at: datetime | None
    payload: Mapping[str, Any] = field(default_factory=dict)


# ===========================================================================
# ORTAK YARDIMCILAR
# ===========================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now(now: datetime | None) -> datetime:
    return turn_rules.as_utc(now) if now is not None else utc_now()


def _career_manager(db, seed: int | None = None):
    """Birincil (sistem) CareerManager. Gec import: career_manager bu modulu hic import etmesin diye."""
    from career_manager import CareerManager

    return CareerManager(db, seed=seed)


def _game_state(db, *, for_update: bool = False) -> GameState:
    stmt = select(GameState).where(GameState.id == 1)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    state = db.scalar(stmt)
    if state is None:
        raise TurnError("Dünyanın oyun durumu bulunamadı; kariyer henüz kurulmamış.")
    return state


def _career_week(state: GameState) -> int:
    return int(state.career_week_offset or 0) + int(state.current_week)


def _rules(state: GameState) -> WorldRules:
    return WorldRules.from_dict(state.world_rules)


def _seat_team(seat: Seat | None, state: GameState) -> int | None:
    """Koltugun kulubu. Birincil koltugun kulubu GameState'tedir (SeatStore Seat.team_id'ye koymadiysa)."""
    if seat is None:
        return None
    if seat.team_id is not None:
        return seat.team_id
    return state.user_team_id if seat.is_primary else None


def _seat_key(seat: Seat) -> int:
    return seat.id if seat.id is not None else -1          # satiri olmayan birincil koltuk hazir olamaz


@dataclass(frozen=True)
class _SeatFact:
    seat: Seat
    team_id: int | None
    ready: bool


def _seat_facts(seats: SeatStore, state: GameState) -> list[_SeatFact]:
    """ACTIVE koltuklar (kulubu olsun olmasin), hazir bilgisiyle."""
    career_week = _career_week(state)
    facts = []
    for seat in seats.active():
        if seat.status != SeatStatus.ACTIVE.value:
            continue
        ready = seat.id is not None and seat.ready_career_week == career_week
        facts.append(_SeatFact(seat, _seat_team(seat, state), ready))
    return facts


def _playing(facts: Iterable[_SeatFact]) -> list[_SeatFact]:
    """Hazir denetimine giren koltuklar: ACTIVE ve kulubu olan."""
    return [f for f in facts if f.team_id is not None]


def _decide(state: GameState, rules: WorldRules, facts: list[_SeatFact], role: str | None,
            requested: AdvanceTrigger, now: datetime) -> turn_rules.AdvanceDecision:
    return turn_rules.decide_advance(
        now=now,
        deadline_at=state.turn_deadline_at,
        active_seat_ids=[_seat_key(f.seat) for f in facts],
        ready_seat_ids=[_seat_key(f.seat) for f in facts if f.ready],
        actor_role=role,
        requested=requested,
        rules=rules,
    )


def _membership_role(db, ctx: WorldContext) -> str | None:
    """Istek sahibinin bu dunyadaki ACTIVE rolu (ayni islemde; kayit semasi ctx.schema ile eslesmeli)."""
    if not ctx.user_id or ctx.world_id is None:
        return None
    return db.scalar(
        select(WorldMembership.role)
        .join(World, World.id == WorldMembership.world_id)
        .where(
            WorldMembership.world_id == ctx.world_id,
            WorldMembership.user_id == ctx.user_id,
            WorldMembership.status == MembershipStatus.ACTIVE.value,
            World.schema_name == ctx.schema,
            World.status == WorldStatus.ACTIVE.value,
        )
    )


def _add_event(db, state: GameState, kind: WorldEventKind, actor_id: int | None, payload: Mapping[str, Any],
               *, season: int | None = None, week: int | None = None, career_week: int | None = None) -> None:
    db.add(WorldEvent(
        season=int(season if season is not None else state.season),
        week=int(week if week is not None else state.current_week),
        career_week=int(career_week if career_week is not None else _career_week(state)),
        kind=kind.value,
        actor_manager_id=actor_id,
        payload=dict(payload),
    ))


def _notify(db, notes: list[tuple[int, str, str]]) -> None:
    """
    (koltuk id, NotificationKind adi, metin) bildirimleri. messaging.notify (B3) henuz iskeletse ya da hata verirse
    ilerleme bozulmaz: savepoint geri alinir, uyari loglanir.
    """
    if not notes:
        return
    try:
        import messaging
    except ImportError:                                  # pragma: no cover - modul her zaman var
        return
    notify = getattr(messaging, "notify", None)
    kinds = getattr(messaging, "NotificationKind", None)
    if notify is None or kinds is None:
        return
    try:
        with db.begin_nested():
            for seat_id, kind, text in notes:
                notify(db, seat_id, kinds(kind), text[:NOTIFICATION_MAX])
    except NotImplementedError:
        return                                           # Faz 12 B3 paketi henuz yok
    except (SQLAlchemyError, ValueError) as exc:
        log.warning("Dünya bildirimleri yazılamadı: %s", exc)


def _club_released_hooks(cm, team_id: int | None) -> None:
    """Kulup insan menajerden cikti: eklentiler (pazar tekliflerini dusurur vb.). Koltuk onbellekleri yenilenir."""
    if team_id is None:
        return
    refresh = getattr(cm, "refresh_seats", None)
    if callable(refresh):
        refresh()
    run = getattr(cm, "run_extensions", None)
    if callable(run):                                     # Faz 12 A2: CareerManager'in yukledigi eklentiler
        run("on_club_released", team_id)
        return
    import extensions

    for ext in extensions.load(cm):
        hook = getattr(ext, "on_club_released", None)
        if callable(hook):
            hook(team_id)


def _turn_deadline(opened_at: datetime, rules: WorldRules) -> datetime | None:
    """Turun bitisi: otomatik ilerleme kapaliysa yok (worlds.create_world ile ayni kural)."""
    return turn_rules.deadline_for(opened_at, rules.deadline_hours) if rules.auto_advance else None


def _close_season_hook(cm):
    """
    Sezon bitti ama sezon arasi milli mac gunu bekliyorsa onu oynatan cagrilabilir (report -> bool), yoksa None.
    Once yuklu eklentiler, sonra kurallar milli takimlari aciyorsa national_teams.NationalTeams denetlenir.
    """
    import extensions

    candidates = list(extensions.load(cm))
    if extensions.rules_of(cm).internationals:
        try:
            import national_teams

            candidates.append(national_teams.NationalTeams(cm))
        except (ImportError, AttributeError, NotImplementedError):
            pass
    for candidate in candidates:
        pending = getattr(candidate, "close_season_pending", None)
        play = getattr(candidate, "play_close_season_matchday", None)
        if not (callable(pending) and callable(play)):
            continue
        try:
            if pending():
                return play
        except NotImplementedError:
            continue
    return None


def _lines_json(lines: Iterable) -> list[list[str]]:
    return [[str(kind), str(text)] for kind, text in lines]


def _store_report(db, seat_id: int, season: int, week: int, midweek: bool, lines, cup_lines) -> None:
    """manager_week_reports: ayni (koltuk, sezon, hafta, midweek) varsa satirlar sona eklenir."""
    stmt = pg_insert(ManagerWeekReport).values(
        manager_id=seat_id, season=season, week=week, midweek=midweek,
        lines=_lines_json(lines), cup_lines=_lines_json(cup_lines),
    )
    table = ManagerWeekReport.__table__
    stmt = stmt.on_conflict_do_update(
        constraint="uq_manager_week_report",
        set_={
            "lines": table.c.lines.op("||")(stmt.excluded.lines),
            "cup_lines": table.c.cup_lines.op("||")(stmt.excluded.cup_lines),
            "created_at": func.now(),
        },
    )
    db.execute(stmt)


def _protected_until(rules: WorldRules, career_week: int) -> int | None:
    return career_week + rules.protection_weeks if rules.protection_weeks > 0 else None


def _team_name(db, team_id: int | None) -> str | None:
    if team_id is None:
        return None
    return db.scalar(select(Team.name).where(Team.id == team_id))


# ===========================================================================
# HAFTA ILERLETME
# ===========================================================================

@dataclass
class _TurnOutcome:
    kind: str
    message: str
    reports: list[tuple[int, int, int, bool, list, list]] = field(default_factory=list)


def _play_turn(cm, facts: list[_SeatFact]) -> _TurnOutcome:
    """
    Oyun adimi (dunya kilidi altinda, birincil CareerManager): hafta, sezon arasi milli mac gunu ya da yeni sezon.
    Raporlar: (koltuk id, sezon, hafta, midweek, satirlar, kupa satirlari). Testler bu fonksiyonu sarabilir.
    """
    from career_views import cup_report_lines, week_report_lines

    state = cm.state
    season, week = int(state.season), int(state.current_week)
    seated = [f for f in facts if f.seat.id is not None]

    if cm.season_finished:
        play_close = _close_season_hook(cm)
        if play_close is not None:
            from career_manager import WeekReport

            report = WeekReport(season=season, week=week)
            play_close(report)
            rows = []
            for f in seated:
                view = report.view_for(f.team_id)
                rows.append((f.seat.id, season, week, True, week_report_lines(view), cup_report_lines(view)))
            return _TurnOutcome(KIND_CLOSE_SEASON, "Sezon arası milli maç günü oynandı.", rows)

        primary_team = state.user_team_id
        new_season = cm.start_new_season()
        notes_by_team = getattr(cm, "new_season_notes_by_team", None) or {}
        rows = []
        for f in seated:
            notes = notes_by_team.get(f.team_id)
            if notes is None and f.team_id == primary_team:
                notes = cm.new_season_notes
            lines = [("season", f"Sezon {new_season} başladı!")] + [("info", note) for note in notes or []]
            rows.append((f.seat.id, season, week, False, lines, []))
        return _TurnOutcome(KIND_NEW_SEASON, f"Sezon {new_season} başladı!", rows)

    report = cm.play_week()
    if (int(state.season), int(state.current_week)) == (season, week):
        log.warning("Dünya haftası ilerlemedi: sezon %s hafta %s oynanacak maç yok", season, week)
        return _TurnOutcome(KIND_NONE, "Bu hafta oynanacak maç bulunamadı.")
    rows = []
    for f in seated:
        view = report.view_for(f.team_id)
        rows.append((f.seat.id, season, week, False, week_report_lines(view), cup_report_lines(view)))
    return _TurnOutcome(KIND_WEEK, f"Sezon {season}, {week}. hafta oynandı.", rows)


def _apply_missed_deadlines(db, cm, seats: SeatStore, state: GameState, rules: WorldRules,
                            facts: list[_SeatFact], trigger: AdvanceTrigger, opened_at: datetime | None,
                            career_week_before: int) -> tuple[list[str], list[tuple[int, str, str]]]:
    """Kacirma sayaclari ve hareketsizlik nedeniyle kulup kaybi. Donus: (kulubunu kaybedenler, bildirimler)."""
    ids = [f.seat.id for f in facts if f.seat.id is not None]
    if not ids:
        return [], []
    rows = {row.id: row for row in db.scalars(select(WorldManager).where(WorldManager.id.in_(ids)))}
    opened = turn_rules.as_utc(opened_at)
    to_release: list[tuple[_SeatFact, WorldManager]] = []
    for f in facts:
        row = rows.get(f.seat.id)
        if row is None:
            continue
        if turn_rules.missed_deadline(ready=f.ready, last_active_at=row.last_active_at,
                                      turn_opened_at=opened_at, trigger=trigger):
            row.missed_deadlines = int(row.missed_deadlines or 0) + 1
            if row.missed_deadlines > rules.max_missed_deadlines:
                to_release.append((f, row))
            continue
        last = turn_rules.as_utc(row.last_active_at)
        if f.ready or (opened is not None and last is not None and last >= opened):
            row.missed_deadlines = 0
    db.flush()

    released: list[str] = []
    notes: list[tuple[int, str, str]] = []
    protected_until = _protected_until(rules, _career_week(state))
    for f, row in to_release:
        missed = int(row.missed_deadlines)
        seat = seats.by_id(f.seat.id) or f.seat
        team_id = seats.release(seat, SeatStatus.RELEASED.value, protected_until)
        team_id = team_id if team_id is not None else f.team_id
        db.flush()
        row.missed_deadlines = 0
        _club_released_hooks(cm, team_id)
        team_name = _team_name(db, team_id) or "kulübünü"
        protection = (f" Kulüp {rules.protection_weeks} hafta AI transferlerine karşı korunuyor."
                      if protected_until is not None else "")
        text = (f"{f.seat.display_name}, üst üste {missed} haftayı kaçırdığı için {team_name} kulübünü "
                f"kaybetti.{protection}")
        _add_event(db, state, WorldEventKind.RELEASE, None, {
            "seat_id": f.seat.id, "user_id": f.seat.user_id, "team_id": team_id, "team_name": team_name,
            "reason": RELEASE_INACTIVE, "missed": missed, "protected_until": protected_until, "text": text,
        }, career_week=career_week_before)
        released.append(f.seat.display_name)
        notes.append((f.seat.id, "RELEASED", f"Üst üste {missed} haftayı kaçırdığın için {team_name} kulübünü "
                                             "kaybettin. Yeni bir kulüp seçebilirsin."))
    db.flush()
    return released, notes


def _advance_locked(db, ctx: WorldContext, requested: AdvanceTrigger, expected: tuple[int, int] | None,
                    now: datetime) -> AdvanceResult:
    state = _game_state(db, for_update=True)
    rules = _rules(state)
    if not rules.shared:
        raise AdvanceNotAllowed(NOT_SHARED_TEXT)
    season, week = int(state.season), int(state.current_week)
    if expected is not None and (int(expected[0]), int(expected[1])) != (season, week):
        return AdvanceResult(False, KIND_NONE, None, season, week, [], MOVED_TEXT)

    seats = SeatStore(db)
    all_facts = _seat_facts(seats, state)
    facts = _playing(all_facts)
    role = _membership_role(db, ctx)
    decision = _decide(state, rules, facts, role, requested, now)
    if not decision.allowed:
        if requested is AdvanceTrigger.FORCED:
            raise AdvanceNotAllowed(decision.reason)
        return AdvanceResult(False, KIND_NONE, None, season, week, [], decision.reason)
    trigger = decision.trigger

    career_week_before = _career_week(state)
    opened_before = state.turn_opened_at
    actor_seat = seats.resolve(ctx.user_id) if ctx.user_id and trigger is not AdvanceTrigger.DEADLINE else None
    actor_id = actor_seat.id if actor_seat is not None else None

    from career_manager import SeasonNotFinished

    cm = _career_manager(db, seed=ctx.world_seed)
    try:
        outcome = _play_turn(cm, facts)
    except SeasonNotFinished as exc:                  # eklenti yeni sezonu engelliyor (orn. Dunya Kupasi)
        raise AdvanceNotAllowed(str(exc)) from exc
    if outcome.kind == KIND_NONE:
        return AdvanceResult(False, KIND_NONE, None, season, week, [], outcome.message)

    db.flush()
    for seat_id, r_season, r_week, midweek, lines, cup_lines in outcome.reports:
        _store_report(db, seat_id, r_season, r_week, midweek, lines, cup_lines)

    released, notes = _apply_missed_deadlines(db, cm, seats, state, rules, facts, trigger, opened_before,
                                              career_week_before)
    # Hazir bayraklari: yeni tur temiz baslar (sezon devrinde mutlak hafta ayni kalir)
    db.execute(update(WorldManager).where(WorldManager.ready_career_week.is_not(None))
               .values(ready_career_week=None).execution_options(synchronize_session=False))

    state.turn_opened_at = now
    state.turn_deadline_at = _turn_deadline(now, rules)
    state.last_advance_at = now
    state.last_advance_trigger = trigger.value
    state.last_advance_by = ctx.user_id if (ctx.user_id and trigger is not AdvanceTrigger.DEADLINE) else None

    message = outcome.message
    if released:
        message += " Hareketsizlik nedeniyle kulübünü kaybeden: " + ", ".join(released) + "."
    _add_event(db, state, WorldEventKind.ADVANCE, actor_id, {
        "trigger": trigger.value, "result": outcome.kind, "reason": decision.reason,
        "from": [season, week], "to": [int(state.season), int(state.current_week)],
        "released": released, "user_id": ctx.user_id or None, "text": message,
    }, season=season, week=week, career_week=career_week_before)
    notes += [(f.seat.id, "TURN", message) for f in all_facts if f.seat.id is not None]
    db.flush()
    _notify(db, notes)
    return AdvanceResult(True, outcome.kind, trigger, int(state.season), int(state.current_week), released, message)


def try_advance(
    ctx: WorldContext,
    trigger: AdvanceTrigger,
    expected: tuple[int, int] | None,
    now: datetime | None = None,
    *,
    wait: bool = False,
) -> AdvanceResult:
    """
    Kendi islemi + EXCLUSIVE dunya kilidi. Varsayilan beklemez (try_exclusive: web). wait=True (CLI tick): kilit
    database.EXCLUSIVE_LOCK_TIMEOUT_MS kadar beklenir -- bekleyen EXCLUSIVE istegin arkasina yeni SHARED istekler
    siralanir, surekli menajer islemleri ilerlemeyi aclik icinde birakmaz. Mesgul / bekleme asildi ->
    AdvanceInProgress; hafta bu arada ilerlediyse (expected uyusmuyor) advanced=False; FORCED yetkisiz ->
    AdvanceNotAllowed; READY / DEADLINE kosulu yoksa advanced=False ve Turkce neden.
    Acik bir oturumun icinden CAGRILMAZ (ayni dunyada SHARED tutan islem varken kendini bekler).
    """
    now = _now(now)
    requested = AdvanceTrigger(trigger)
    mode = "exclusive" if wait else "try_exclusive"
    try:
        with database.career_context(ctx.schema), database.world_lock(ctx.schema, mode), \
                database.session_scope() as db:
            result = _advance_locked(db, ctx, requested, expected, now)
    except database.WorldBusyError as exc:
        raise AdvanceInProgress(BUSY_TEXT) from exc
    except OperationalError as exc:
        if getattr(getattr(exc, "orig", None), "pgcode", None) != database.LOCK_TIMEOUT_PGCODE:
            raise
        raise AdvanceInProgress(BUSY_TEXT) from exc
    return replace(result, world_id=ctx.world_id)


def maybe_advance_due(ctx: WorldContext, now: datetime | None = None, *, wait: bool = False) -> AdvanceResult | None:
    """
    Sayfa yuklenirken / CLI: tur suresi dolduysa (auto_advance) ya da herkes hazirsa ilerletir (wait: try_advance).
    Gerek yoksa, dunya mesgulse ya da ilerleme reddedildiyse None; ilerlediyse sonuc.
    """
    now = _now(now)
    with database.career_context(ctx.schema), database.session_scope() as db:
        state = _game_state(db)
        rules = _rules(state)
        if not rules.shared:
            return None
        expected = (int(state.season), int(state.current_week))
        facts = _playing(_seat_facts(SeatStore(db), state))
        decision = _decide(state, rules, facts, None, AdvanceTrigger.DEADLINE, now)
    if not decision.allowed:
        return None
    try:
        result = try_advance(ctx, AdvanceTrigger.DEADLINE, expected, now, wait=wait)
    except AdvanceInProgress:
        return None
    except AdvanceNotAllowed as exc:
        log.warning("Dünya %s ilerletilemedi: %s", ctx.world_id, exc)
        return None
    return result if result.advanced else None


def shared_world_contexts(world_ids: Iterable[int] | None = None, schema: str | None = None) -> list[WorldContext]:
    """Kayittaki ACTIVE paylasilan dunyalarin sistem baglamlari (role SYSTEM, user_id 0)."""
    from worlds import WorldContext

    with database.career_context(database.LEGACY_CAREER_SCHEMA), database.session_scope() as db:
        stmt = (select(World).where(World.kind == WorldKind.SHARED.value, World.status == WorldStatus.ACTIVE.value)
                .order_by(World.id))
        if world_ids is not None:
            stmt = stmt.where(World.id.in_(list(world_ids)))
        if schema is not None:
            stmt = stmt.where(World.schema_name == schema)
        return [
            WorldContext(world_id=w.id, schema=w.schema_name, name=w.name, kind=w.kind, role=SYSTEM_ROLE,
                         user_id=0, world_seed=w.world_seed)
            for w in db.scalars(stmt)
        ]


def advance_due_worlds(now: datetime | None = None, *, world_ids: Iterable[int] | None = None,
                       schema: str | None = None, wait: bool = True) -> list[AdvanceResult]:
    """
    CLI tick: suresi dolan (ya da herkesin hazir oldugu) paylasilan dunyalari ilerletir; yalnizca ilerleyenler.
    Varsayilan olarak dunya kilidini sinirli sure bekler (wait); bozuk / eksik semali dunya loglanip atlanir.
    """
    now = _now(now)
    results: list[AdvanceResult] = []
    for ctx in shared_world_contexts(world_ids, schema):
        try:
            result = maybe_advance_due(ctx, now, wait=wait)
        except (TurnError, SQLAlchemyError, ValueError) as exc:
            log.warning("Dünya %s (%s) denetlenemedi: %s", ctx.world_id, ctx.schema, exc)
            continue
        if result is not None:
            results.append(result)
    return results


def turn_status_for(ctx: WorldContext, now: datetime | None = None) -> TurnStatus:
    """Salt okunur tur durumu (kendi islemi; CLI world-list)."""
    with database.career_context(ctx.schema), database.session_scope() as db:
        return WorldController(db, ctx).turn_status(now)


# ===========================================================================
# DUNYA KONTROLCUSU (commit etmez)
# ===========================================================================

_EVENT_TEXTS = {
    WorldEventKind.ADVANCE.value: "Hafta ilerledi.",
    WorldEventKind.CLAIM.value: "Kulüp seçildi.",
    WorldEventKind.RELEASE.value: "Kulüp bırakıldı.",
    WorldEventKind.KICK.value: "Menajer dünyadan çıkarıldı.",
    WorldEventKind.RULES.value: "Dünya kuralları değişti.",
    WorldEventKind.REVIEW.value: "Transfer incelemesi.",
    WorldEventKind.REVERSAL.value: "Transfer iptal edildi.",
    WorldEventKind.NATIONAL.value: "Milli takım görevi.",
}


class WorldController:
    """Oturumdaki menajerin (ctx.user_id) dunya islemleri. Commit ETMEZ."""

    def __init__(self, db, ctx: WorldContext) -> None:
        self.db = db
        self.ctx = ctx
        self.seats = SeatStore(db)
        self._role_loaded = False
        self._role: str | None = None

    # ------------------------------------------------------------------ durum

    @property
    def state(self) -> GameState:
        return _game_state(self.db)

    @property
    def rules(self) -> WorldRules:
        return _rules(self.state)

    @property
    def career_week(self) -> int:
        return _career_week(self.state)

    def role(self) -> str | None:
        """OWNER / ADMIN / MEMBER (accounts.world_memberships, ACTIVE); uye degilse None."""
        if not self._role_loaded:
            self._role = _membership_role(self.db, self.ctx)
            self._role_loaded = True
        return self._role

    def is_admin(self) -> bool:
        return (self.role() or "") in worlds.ADMIN_ROLES

    def my_seat(self) -> Seat | None:
        return self.seats.resolve(self.ctx.user_id) if self.ctx.user_id else None

    def _require_seat(self) -> Seat:
        seat = self.my_seat()
        if seat is None or seat.status not in SEATED_STATUSES:
            raise worlds.NotAMemberError(NO_SEAT_TEXT)
        return seat

    def _require_admin(self) -> None:
        if not self.is_admin():
            raise worlds.WorldPermissionError(ADMIN_ONLY_TEXT)

    def _phase(self) -> str:
        cm = _career_manager(self.db)
        if not cm.season_finished:
            return PHASE_SEASON
        return PHASE_CLOSE_SEASON if _close_season_hook(cm) is not None else PHASE_SEASON_END

    def turn_status(self, now: datetime | None = None) -> TurnStatus:
        now = _now(now)
        state = self.state
        rules = _rules(state)
        career_week = _career_week(state)
        facts = _playing(_seat_facts(self.seats, state))
        deadline = turn_rules.as_utc(state.turn_deadline_at)
        me = self.my_seat()
        me_team = _seat_team(me, state)
        return TurnStatus(
            season=int(state.season),
            week=int(state.current_week),
            career_week=career_week,
            phase=self._phase(),
            opened_at=turn_rules.as_utc(state.turn_opened_at),
            deadline_at=deadline,
            seconds_left=max(0, int((deadline - now).total_seconds())) if deadline is not None else None,
            active=len(facts),
            ready=sum(1 for f in facts if f.ready),
            ready_names=[f.seat.display_name for f in facts if f.ready],
            waiting_names=[f.seat.display_name for f in facts if not f.ready],
            me_ready=bool(me is not None and me.id is not None and me.ready_career_week == career_week),
            can_force=bool(rules.shared and self.is_admin()),
            can_ready=bool(rules.shared and rules.ready_check and me is not None
                           and me.status == SeatStatus.ACTIVE.value and me_team is not None),
            auto_due=bool(rules.shared and rules.auto_advance and deadline is not None and now >= deadline),
        )

    def managers(self) -> list[ManagerRow]:
        """Dunyadaki menajerler (ACTIVE ve kulubunu kaybetmis RELEASED); birincil koltuk once."""
        db, state = self.db, self.state
        career_week = _career_week(state)
        rows = list(db.scalars(
            select(WorldManager).where(WorldManager.status.in_(SEATED_STATUSES))
            .order_by(WorldManager.is_primary.desc(), WorldManager.display_name, WorldManager.id)
        ))
        roles = dict(db.execute(
            select(WorldMembership.user_id, WorldMembership.role).where(
                WorldMembership.world_id == self.ctx.world_id,
                WorldMembership.status == MembershipStatus.ACTIVE.value)
        ).all()) if self.ctx.world_id is not None else {}
        nation_ids = {r.nation_id for r in rows if r.nation_id is not None}
        nations = dict(db.execute(select(Nation.id, Nation.name).where(Nation.id.in_(nation_ids))).all()) \
            if nation_ids else {}
        result = []
        for row in rows:
            seat = self.seats.by_id(row.id)
            team_id = _seat_team(seat, state) if seat is not None else row.team_id
            rep = float(seat.reputation) if seat is not None else float(row.reputation or reputation.START_REPUTATION)
            role = roles.get(row.user_id)
            if role is None and row.is_primary:
                role = MembershipRole.OWNER.value
            result.append(ManagerRow(
                seat_id=row.id, user_id=row.user_id, name=row.display_name, role=role,
                team_name=_team_name(db, team_id), reputation=round(rep, 2),
                level_title=reputation.level(rep).title, status=row.status,
                ready=row.ready_career_week == career_week, last_active_at=row.last_active_at,
                missed_deadlines=int(row.missed_deadlines or 0), fair_play=float(row.fair_play),
                nation_name=nations.get(row.nation_id),
            ))
        return result

    def _inactive_release_teams(self, seat_id: int | None) -> set[int]:
        """Koltugun hareketsizlik nedeniyle kaybettigi kulupler (koruma surerken geri alinamaz)."""
        if seat_id is None:
            return set()
        teams: set[int] = set()
        for payload in self.db.scalars(select(WorldEvent.payload).where(WorldEvent.kind == WorldEventKind.RELEASE.value)):
            if (payload or {}).get("reason") == RELEASE_INACTIVE and payload.get("seat_id") == seat_id \
                    and payload.get("team_id") is not None:
                teams.add(int(payload["team_id"]))
        return teams

    @staticmethod
    def _protected(team: Team, career_week: int) -> bool:
        return team.ai_protected_until is not None and int(team.ai_protected_until) >= career_week

    def club_offers(self, query: str = "", only_eligible: bool = True) -> list[ClubOffer]:
        """
        Insan menajeri olmayan kulupler. Kural club_offers_by_level acikken uygunluk menajer seviyesi ve kulubun
        itibar yuzdelik dilimiyle (turn_rules.club_eligible); korumadaki kulupler isaretlenir.
        """
        db, state = self.db, self.state
        rules = _rules(state)
        career_week = _career_week(state)
        me = self.my_seat()
        level = reputation.level(me.reputation).level if me is not None else 1
        humans = self.seats.human_team_ids()
        teams = db.execute(select(Team, League.name, League.country).join(League, League.id == Team.league_id)).all()
        percentiles = turn_rules.club_rank_percentiles({t.id: int(t.reputation) for t, _, _ in teams})
        ratings = dict(db.execute(
            select(Player.team_id, func.avg(Player.overall_rating))
            .where(Player.team_id.is_not(None), Player.in_academy.is_(False))
            .group_by(Player.team_id)
        ).all())
        blocked = self._inactive_release_teams(me.id if me is not None else None)
        key = plain_key(query or "")
        offers: list[ClubOffer] = []
        for team, league_name, country in teams:
            if team.id in humans:
                continue
            if key and key not in plain_key(team.name) and key not in plain_key(league_name):
                continue
            eligible, reason = (turn_rules.club_eligible(level, percentiles[team.id])
                                if rules.club_offers_by_level else (True, ""))
            protected = self._protected(team, career_week)
            if eligible and protected and team.id in blocked:
                eligible, reason = False, self._reclaim_text(team)
            if only_eligible and not eligible:
                continue
            squad = round(float(ratings.get(team.id) or 0.0), 1)
            offers.append(ClubOffer(
                team_id=team.id, team_name=team.name, league_name=league_name, reputation=int(team.reputation),
                stars=float(star_value(squad) or 0.0) if squad else 0.0, squad_rating=squad,
                transfer_budget=int(team.transfer_budget), eligible=eligible, reason=reason, protected=protected,
                league_id=team.league_id, country=country or "", stadium_capacity=team.stadium_capacity,
            ))
        offers.sort(key=lambda o: (not o.eligible, -o.reputation, o.team_name))
        return offers

    @staticmethod
    def _reclaim_text(team: Team) -> str:
        return (f"Hareketsizlik nedeniyle kaybettiğin {team.name} kulübünü koruma süresi bitmeden "
                f"({team.ai_protected_until}. kariyer haftası) geri alamazsın.")

    def my_reports(self, limit: int = 5) -> list[ManagerReport]:
        me = self.my_seat()
        if me is None or me.id is None:
            return []
        rows = self.db.scalars(
            select(ManagerWeekReport).where(ManagerWeekReport.manager_id == me.id)
            .order_by(ManagerWeekReport.season.desc(), ManagerWeekReport.week.desc(),
                      ManagerWeekReport.created_at.desc(), ManagerWeekReport.id.desc())
            .limit(max(0, int(limit)))
        )
        return [
            ManagerReport(season=int(r.season), week=int(r.week), midweek=bool(r.midweek),
                          lines=[(str(k), str(t)) for k, t in r.lines or []],
                          cup_lines=[(str(k), str(t)) for k, t in r.cup_lines or []])
            for r in rows
        ]

    def events(self, limit: int = 50) -> list[WorldEventView]:
        rows = self.db.execute(
            select(WorldEvent, WorldManager.display_name)
            .outerjoin(WorldManager, WorldManager.id == WorldEvent.actor_manager_id)
            .order_by(WorldEvent.id.desc()).limit(max(0, int(limit)))
        ).all()
        views = []
        for event, actor_name in rows:
            payload = dict(event.payload or {})
            views.append(WorldEventView(
                id=event.id, season=int(event.season), week=int(event.week), career_week=int(event.career_week),
                kind=event.kind, actor_name=actor_name,
                text=str(payload.get("text") or _EVENT_TEXTS.get(event.kind, event.kind)),
                created_at=event.created_at, payload=payload,
            ))
        return views

    # ------------------------------------------------------------------ menajer islemleri

    def _touch_seat(self, seat: Seat, now: datetime) -> None:
        """Son etkinlik. SeatStore.touch 5 dakikada bir yazar; tur acildiktan sonraki ilk etkinlik hemen yazilir."""
        if seat.id is None:
            return
        self.seats.touch(seat, now)
        opened = self.state.turn_opened_at
        if opened is None:
            return
        self.db.flush()
        self.db.execute(
            update(WorldManager)
            .where(WorldManager.id == seat.id,
                   or_(WorldManager.last_active_at.is_(None), WorldManager.last_active_at < opened))
            .values(last_active_at=now)
            .execution_options(synchronize_session=False)
        )

    def _ensure_turn_open(self, state: GameState, rules: WorldRules, now: datetime) -> None:
        """Paylasilan dunyanin ilk turu (donusumden / kurulumdan sonra) ilk menajer isleminde acilir."""
        if rules.shared and state.turn_opened_at is None:
            state.turn_opened_at = now
            state.turn_deadline_at = _turn_deadline(now, rules)

    def _ensure_primary_row(self, seat: Seat) -> Seat:
        state = self.state
        username = self.db.scalar(select(User.username).where(User.id == state.user_id)) if state.user_id else None
        return self.seats.ensure_primary_row(state.user_id, (username or "Menajer")[:32])

    def _cache_team_name(self, user_id: int | None, team_name: str | None) -> None:
        if user_id is None or self.ctx.world_id is None:
            return
        self.db.execute(
            update(WorldMembership)
            .where(WorldMembership.world_id == self.ctx.world_id, WorldMembership.user_id == user_id)
            .values(team_name_cache=team_name)
            .execution_options(synchronize_session=False)
        )

    def claim_club(self, team_id: int) -> Seat:
        """Kulupsuz koltuk (ACTIVE / RELEASED) insan menajeri olmayan, seviyesine uygun bir kulubu alir."""
        db, state = self.db, self.state
        rules = _rules(state)
        if not rules.shared:
            raise ClubUnavailableError("Kulüp seçimi yalnızca paylaşılan dünyada yapılır.")
        seat = self._require_seat()
        if _seat_team(seat, state) is not None:
            raise ClubUnavailableError("Zaten bir kulübü yönetiyorsun; başka kulübe geçmek için önce kulübünü bırak.")
        # Ayni kulubu es zamanli secenler bu satir kilidinde siraya girer (ikinci, ilkinin commit'ini gorur)
        team = db.scalar(select(Team).where(Team.id == team_id).with_for_update()
                         .execution_options(populate_existing=True))
        if team is None:
            raise ClubUnavailableError("Kulüp bulunamadı.")
        if team.id in self.seats.human_team_ids():
            raise ClubUnavailableError("Bu kulübü başka bir menajer yönetiyor.")
        career_week = _career_week(state)
        if rules.club_offers_by_level:
            percentiles = turn_rules.club_rank_percentiles(
                dict(db.execute(select(Team.id, Team.reputation)).all()))
            eligible, reason = turn_rules.club_eligible(reputation.level(seat.reputation).level,
                                                        percentiles.get(team.id, 1.0))
            if not eligible:
                raise ClubUnavailableError(reason)
        if self._protected(team, career_week) and team.id in self._inactive_release_teams(seat.id):
            raise ClubUnavailableError(self._reclaim_text(team))
        try:
            with db.begin_nested():
                new_seat = self.seats.assign_team(seat, team.id)
        except IntegrityError as exc:
            raise ClubUnavailableError("Bu kulübü başka bir menajer yönetiyor.") from exc
        except SeatError as exc:
            raise ClubUnavailableError(str(exc)) from exc
        team.ai_protected_until = None                # kulup artik insan menajerde: AI korumasi gereksiz
        now = utc_now()
        self._ensure_turn_open(state, rules, now)
        self._touch_seat(new_seat, now)
        self._cache_team_name(new_seat.user_id, team.name)
        _add_event(db, state, WorldEventKind.CLAIM, new_seat.id, {
            "seat_id": new_seat.id, "user_id": new_seat.user_id, "team_id": team.id, "team_name": team.name,
            "text": f"{new_seat.display_name}, {team.name} kulübünün başına geçti.",
        })
        db.flush()
        return new_seat

    def release_club(self) -> None:
        """Menajer kulubunu birakir (koltuk RELEASED; kulup protection_weeks hafta AI korumasinda)."""
        db, state = self.db, self.state
        rules = _rules(state)
        if not rules.shared:
            raise ClubUnavailableError("Kulüp bırakma yalnızca paylaşılan dünyada yapılır.")
        seat = self._require_seat()
        team_id = _seat_team(seat, state)
        if team_id is None:
            raise ClubUnavailableError("Yönettiğin bir kulüp yok.")
        protected_until = _protected_until(rules, _career_week(state))
        released = self.seats.release(seat, SeatStatus.RELEASED.value, protected_until)
        team_id = released if released is not None else team_id
        db.flush()
        _club_released_hooks(_career_manager(db), team_id)
        team_name = _team_name(db, team_id)
        self._cache_team_name(seat.user_id, None)
        _add_event(db, state, WorldEventKind.RELEASE, seat.id, {
            "seat_id": seat.id, "user_id": seat.user_id, "team_id": team_id, "team_name": team_name,
            "reason": RELEASE_VOLUNTARY, "protected_until": protected_until,
            "text": f"{seat.display_name}, {team_name} kulübünden ayrıldı.",
        })
        self._touch_seat(seat, utc_now())
        db.flush()

    def set_ready(self, ready: bool, expected_career_week: int) -> TurnStatus:
        """Hazir bildirimi. Hafta bu arada ilerlediyse StaleTurnError. Hafta ilerletmeyi CAGIRMAZ (commit sonrasi
        cagiran try_advance(READY) ile yeni islemde dener)."""
        db, state = self.db, self.state
        rules = _rules(state)
        career_week = _career_week(state)
        if int(expected_career_week) != career_week:
            raise StaleTurnError(STALE_TEXT)
        if not rules.shared:
            raise TurnError("Hazır bildirimi yalnızca paylaşılan dünyada kullanılır.")
        if ready and not rules.ready_check:
            raise TurnError("Bu dünyada hazır kontrolü kapalı; hafta süre dolunca ilerler.")
        seat = self._require_seat()
        if seat.status != SeatStatus.ACTIVE.value or _seat_team(seat, state) is None:
            raise TurnError("Kulübü olmayan menajer hazır olamaz; önce bir kulüp seç.")
        now = utc_now()
        self._ensure_turn_open(state, rules, now)
        if seat.id is None:
            seat = self._ensure_primary_row(seat)
        self.seats.set_ready(seat, career_week if ready else None)
        self._touch_seat(seat, now)
        db.flush()
        return self.turn_status(now)

    def touch(self) -> None:
        seat = self.my_seat()
        if seat is None or seat.id is None or seat.status not in SEATED_STATUSES:
            return
        self._touch_seat(seat, utc_now())
        self.db.flush()

    def kick(self, seat_id: int, reason: str) -> None:
        """Yonetici: menajeri dunyadan atar (koltuk KICKED, kulubu serbest ve korumada, uyelik KICKED)."""
        self._require_admin()
        db, state = self.db, self.state
        rules = _rules(state)
        target = self.seats.by_id(seat_id)
        if target is None:
            raise worlds.WorldNotFound("Menajer bulunamadı.")
        owner_id = self._owner_user_id(state)
        if target.is_primary or (target.user_id is not None and target.user_id == owner_id):
            raise worlds.WorldPermissionError("Dünyanın sahibi dünyadan atılamaz.")
        if target.user_id == self.ctx.user_id:
            raise worlds.WorldPermissionError("Kendini atamazsın; dünyadan ayrılmak için Dünyalar sayfasını kullan.")
        if target.status not in SEATED_STATUSES:
            raise WorldError("Bu menajer zaten dünyada değil.")
        target_role = db.scalar(select(WorldMembership.role).where(
            WorldMembership.world_id == self.ctx.world_id, WorldMembership.user_id == target.user_id))
        if target_role == MembershipRole.ADMIN.value and self.role() != MembershipRole.OWNER.value:
            raise worlds.WorldPermissionError("Yöneticiyi yalnızca dünyanın sahibi atabilir.")
        reason = " ".join((reason or "").split())[:KICK_REASON_MAX]
        team_id = _seat_team(target, state)
        protected_until = _protected_until(rules, _career_week(state)) if team_id is not None else None
        released = self.seats.release(target, SeatStatus.KICKED.value, protected_until)
        team_id = released if released is not None else team_id
        worlds.mark_membership(db, self.ctx.world_id, target.user_id, MembershipStatus.KICKED.value)
        db.flush()
        _club_released_hooks(_career_manager(db), team_id)
        team_name = _team_name(db, team_id)
        me = self.my_seat()
        text = f"{target.display_name} dünyadan çıkarıldı." + (f" Neden: {reason}" if reason else "")
        _add_event(db, state, WorldEventKind.KICK, me.id if me is not None else None, {
            "seat_id": target.id, "user_id": target.user_id, "name": target.display_name, "team_id": team_id,
            "team_name": team_name, "reason": reason, "protected_until": protected_until, "text": text,
        })
        db.flush()

    def _owner_user_id(self, state: GameState) -> int | None:
        owner = None
        if self.ctx.world_id is not None:
            owner = self.db.scalar(select(World.owner_user_id).where(World.id == self.ctx.world_id))
        return owner if owner is not None else state.user_id

    def _season_started(self, state: GameState) -> bool:
        """Bu sezon mac oynandi mi (ya da 1. hafta gecildi mi)? Oyun kurallari o zaman kilitlidir."""
        if int(state.current_week) > 1:
            return True
        played = self.db.scalar(select(func.count()).select_from(Fixture).where(
            Fixture.season == state.season, Fixture.status == FixtureStatus.PLAYED))
        return bool(played)

    def update_rules(self, changes: Mapping[str, object]) -> WorldRules:
        """
        Yonetici: kural degisikligi. Gecersiz deger / butunluk / sezon kilidi -> RulesError (Turkce). Koltuk sayisi
        dunyadaki menajerlerden az olamaz; accounts.worlds.max_managers ayni islemde esitlenir. Hafta suresi
        degisirse acik turun bitisi yeniden hesaplanir. world_events RULES.
        """
        self._require_admin()
        db, state = self.db, self.state
        current = _rules(state)
        if not current.shared:
            raise RulesError("Dünya kuralları yalnızca paylaşılan dünyada yönetici panelinden değişir.")
        new = current.with_changes(changes)
        problems = new.validate()
        if problems:
            raise RulesError(" ".join(problems))
        locked = current.editable_changes(new, self._season_started(state))
        if locked:
            raise RulesError(" ".join(locked))
        diff = current.changed_fields(new)
        if not diff:
            return current
        if "max_seats" in diff:
            seated = int(db.scalar(select(func.count()).select_from(WorldManager)
                                   .where(WorldManager.status.in_(SEATED_STATUSES))) or 0)
            has_primary = db.scalar(select(func.count()).select_from(WorldManager)
                                    .where(WorldManager.is_primary.is_(True)))
            seated += 0 if has_primary else 1
            if new.max_seats < seated:
                raise RulesError(f"En fazla menajer, dünyadaki menajer sayısından ({seated}) az olamaz.")
        state.world_rules = new.to_dict()
        if ("deadline_hours" in diff or "auto_advance" in diff) and state.turn_opened_at is not None:
            state.turn_deadline_at = _turn_deadline(state.turn_opened_at, new)
        if "max_seats" in diff and self.ctx.world_id is not None:
            db.execute(update(World).where(World.id == self.ctx.world_id, World.schema_name == self.ctx.schema)
                       .values(max_managers=new.max_seats).execution_options(synchronize_session=False))
        me = self.my_seat()
        from world_rules import FIELD_LABELS

        summary = ", ".join(f"{FIELD_LABELS.get(k, k)}: {old} → {value}" for k, (old, value) in diff.items())
        _add_event(db, state, WorldEventKind.RULES, me.id if me is not None else None, {
            "changes": {k: [old, value] for k, (old, value) in diff.items()},
            "user_id": self.ctx.user_id, "text": f"Dünya kuralları değişti: {summary}",
        })
        db.flush()
        return new

    def can_draw_cup(self) -> bool:
        """Paylasilan dunyada kupa kurasini yalnizca sahip / yonetici ceker; kisisel kariyerde herkes."""
        return (not self.rules.shared) or self.is_admin()
