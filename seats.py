"""
seats.py
========
Dunyadaki insan menajer koltuklari (Faz 12 / 14. Asama, 12A). Veritabani katmani; commit ETMEZ (cagiranin
islemine katilir). Birincil koltuk / diger koltuklar ayrimini YALNIZCA bu modul bilir:

    * Birincil koltuk = eski tek menajer = dunya sahibi (game_state.user_id). Kulubu ve tanınırlığı
      GameState.user_team_id / GameState.manager_reputation'da kalir (CLI, testler ve eski kayitlar birebir);
      world_managers satiri (is_primary) yalnizca uyelik verisini tutar. Satir henuz yoksa (eski kayit) birincil
      koltuk yine vardir: Seat.id None (ensure_primary_row satiri kurar).
    * Diger koltuklar: kulup ve tanınırlık world_managers satirindadir.
    * human_team_ids() = aktif birincil olmayan koltuklarin kulupleri + {GameState.user_team_id}.
      Eski kariyerde (koltuk satiri yok ya da yalnizca birincil) = {GameState.user_team_id}.

Koltuk durumlari (models.SeatStatus): ACTIVE (kulubu olabilir de olmayabilir de), RELEASED (hafta kacirdigi icin
kulubu alindi; uyelik surer, yeni kulup secebilir), LEFT / KICKED (dunyadan ayrildi / atildi; kulup yok).
Kulup benzersizligi: kismi benzersiz indeks (uq_world_manager_team) + GameState.user_team_id kodda denetlenir.

    SeatStore(db)
        primary / resolve(user_id) / by_team / by_id / active / members
        human_team_ids / reputation_for_team / apply_reputation(team_id, delta) -> (once, sonra)
        ensure_primary_row / create_seat / assign_team / release / set_ready / touch / adjust_fair_play
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

import reputation as reputation_rules
from models import FairPlayLog, GameState, SeatStatus, Team, User, WorldManager

DISPLAY_NAME_MAX = 32
FAIR_PLAY_MIN, FAIR_PLAY_MAX = 0.0, 100.0
FAIR_PLAY_REASON_MAX = 120
TOUCH_INTERVAL = timedelta(minutes=5)
DEFAULT_PRIMARY_NAME = "Menajer"
# Dunyada kalan (uye) koltuklar: kulubu alinmis (RELEASED) koltuk da uyedir
MEMBER_STATUSES = (SeatStatus.ACTIVE.value, SeatStatus.RELEASED.value)
RELEASE_STATUSES = (SeatStatus.RELEASED.value, SeatStatus.LEFT.value, SeatStatus.KICKED.value)


@dataclass(frozen=True)
class Seat:
    id: int | None                 # None: birincil koltugun satiri henuz yok (ensure_primary_row)
    user_id: int | None
    display_name: str
    is_primary: bool
    team_id: int | None
    reputation: float
    status: str                    # models.SeatStatus degeri
    ready_career_week: int | None
    missed_deadlines: int
    fair_play: float
    joined_career_week: int
    nation_id: int | None

    @property
    def active(self) -> bool:
        return self.status == SeatStatus.ACTIVE.value

    def is_ready(self, career_week: int) -> bool:
        """Hazir = ready_career_week bu kariyer haftasina esit (hafta ilerleyince sifirlamak gerekmez)."""
        return self.ready_career_week is not None and self.ready_career_week == career_week


class SeatError(ValueError):
    """Koltuk islemi yapilamaz (mesaj Turkce)."""


def clean_display_name(name: str | None) -> str:
    """Bosluklari kirpilmis, en fazla DISPLAY_NAME_MAX karakter; bos ad SeatError."""
    text = " ".join(str(name or "").split())[:DISPLAY_NAME_MAX]
    if not text:
        raise SeatError("Menajer adı boş olamaz.")
    return text


class SeatStore:
    def __init__(self, db) -> None:
        self.db = db                   # kurucu sorgu atmaz (CareerManager her istekte kurulur)

    # ------------------------------------------------------------------ okuma

    def _state(self) -> GameState | None:
        return self.db.get(GameState, 1)

    def _primary_row(self) -> WorldManager | None:
        return self.db.scalar(select(WorldManager).where(WorldManager.is_primary.is_(True)))

    def _career_week(self) -> int:
        st = self._state()
        return 1 if st is None else int(st.career_week_offset or 0) + int(st.current_week)

    def _primary_seat(self, row: WorldManager | None) -> Seat:
        st = self._state()
        state_user = st.user_id if st is not None else None
        return Seat(
            id=row.id if row is not None else None,
            user_id=row.user_id if row is not None and row.user_id is not None else state_user,
            display_name=row.display_name if row is not None else DEFAULT_PRIMARY_NAME,
            is_primary=True,
            team_id=st.user_team_id if st is not None else None,
            reputation=float(st.manager_reputation) if st is not None else reputation_rules.START_REPUTATION,
            status=SeatStatus.ACTIVE.value,          # dunya sahibi dunyadan ayrilamaz
            ready_career_week=row.ready_career_week if row is not None else None,
            missed_deadlines=int(row.missed_deadlines or 0) if row is not None else 0,
            fair_play=float(row.fair_play) if row is not None else FAIR_PLAY_MAX,
            joined_career_week=int(row.joined_career_week) if row is not None else 1,
            nation_id=row.nation_id if row is not None else None,
        )

    @staticmethod
    def _seat(row: WorldManager) -> Seat:
        return Seat(
            id=row.id, user_id=row.user_id, display_name=row.display_name, is_primary=False,
            team_id=row.team_id,
            reputation=float(row.reputation) if row.reputation is not None else reputation_rules.START_REPUTATION,
            status=row.status, ready_career_week=row.ready_career_week,
            missed_deadlines=int(row.missed_deadlines or 0), fair_play=float(row.fair_play),
            joined_career_week=int(row.joined_career_week), nation_id=row.nation_id,
        )

    def _as_seat(self, row: WorldManager) -> Seat:
        return self._primary_seat(row) if row.is_primary else self._seat(row)

    def primary(self) -> Seat:
        """Birincil koltuk (satir yoksa id None). Kulup ve tanınırlık GameState'ten."""
        return self._primary_seat(self._primary_row())

    def resolve(self, user_id: int | None) -> Seat | None:
        """Kullanicinin koltugu: None ya da dunya sahibi -> birincil; koltugu olmayan -> None (izleyici)."""
        if user_id is None:
            return self.primary()
        st = self._state()
        if st is not None and st.user_id is not None and st.user_id == user_id:
            return self.primary()
        row = self.db.scalar(select(WorldManager).where(WorldManager.user_id == user_id))
        return self._as_seat(row) if row is not None else None

    def by_team(self, team_id: int) -> Seat | None:
        """Kulubu yoneten insan koltugu (yoksa None: AI kulubu)."""
        if team_id is None:
            return None
        st = self._state()
        if st is not None and st.user_team_id == team_id:
            return self.primary()
        row = self.db.scalar(select(WorldManager).where(
            WorldManager.team_id == team_id, WorldManager.is_primary.is_(False),
            WorldManager.status == SeatStatus.ACTIVE.value))
        return self._seat(row) if row is not None else None

    def by_id(self, seat_id: int) -> Seat | None:
        row = self.db.get(WorldManager, seat_id) if isinstance(seat_id, int) and not isinstance(seat_id, bool) else None
        return self._as_seat(row) if row is not None else None

    def _other_rows(self, statuses: tuple[str, ...]) -> list[WorldManager]:
        return list(self.db.scalars(
            select(WorldManager)
            .where(WorldManager.is_primary.is_(False), WorldManager.status.in_(statuses))
            .order_by(WorldManager.id)
        ))

    def active(self) -> list[Seat]:
        """Birincil (her zaman) + ACTIVE durumdaki diger koltuklar, id sirasiyla."""
        return [self.primary(), *(self._seat(row) for row in self._other_rows((SeatStatus.ACTIVE.value,)))]

    def members(self) -> list[Seat]:
        """Dunyada kalan koltuklar: birincil + ACTIVE ve RELEASED (kulubu alinmis ama uye) koltuklar."""
        return [self.primary(), *(self._seat(row) for row in self._other_rows(MEMBER_STATUSES))]

    def member_count(self) -> int:
        """members() uzunlugu (tek sorgu, satir nesnesi kurmadan)."""
        others = self.db.scalar(select(func.count()).select_from(WorldManager).where(
            WorldManager.is_primary.is_(False), WorldManager.status.in_(MEMBER_STATUSES))) or 0
        return 1 + int(others)

    def human_team_ids(self) -> frozenset[int]:
        st = self._state()
        ids = set(self.db.scalars(select(WorldManager.team_id).where(
            WorldManager.is_primary.is_(False), WorldManager.status == SeatStatus.ACTIVE.value,
            WorldManager.team_id.isnot(None))))
        if st is not None and st.user_team_id is not None:
            ids.add(st.user_team_id)
        return frozenset(ids)

    def reputation_for_team(self, team_id: int) -> float | None:
        """Kulubu yoneten insan menajerin tanınırlığı; AI kulubu icin None."""
        seat = self.by_team(team_id)
        return seat.reputation if seat is not None else None

    # ------------------------------------------------------------------ yazma

    def apply_reputation(self, team_id: int, delta: float) -> tuple[float, float]:
        """
        Kulubu yoneten koltugun tanınırlığına degisim (reputation.apply). Birincil: GameState.manager_reputation;
        diger: world_managers.reputation. (once, sonra) dondurur. Insan kulubu degilse SeatError.
        """
        st = self._state()
        if st is not None and st.user_team_id is not None and st.user_team_id == team_id:
            before = st.manager_reputation
            st.manager_reputation = reputation_rules.apply(st.manager_reputation, delta)
            return before, st.manager_reputation
        row = self.db.scalar(select(WorldManager).where(
            WorldManager.team_id == team_id, WorldManager.is_primary.is_(False),
            WorldManager.status == SeatStatus.ACTIVE.value))
        if row is None:
            raise SeatError("Bu kulübü yöneten bir menajer yok.")
        before = float(row.reputation) if row.reputation is not None else reputation_rules.START_REPUTATION
        row.reputation = reputation_rules.apply(before, delta)
        return before, row.reputation

    def _owner_name(self, user_id: int | None) -> str:
        if user_id is None:
            return DEFAULT_PRIMARY_NAME
        name = self.db.scalar(select(User.username).where(User.id == user_id))
        return (name or DEFAULT_PRIMARY_NAME)[:DISPLAY_NAME_MAX]

    def ensure_primary_row(self, user_id: int | None, display_name: str | None = None) -> Seat:
        """
        Birincil koltuk satirini (yoksa) kurar; idempotent. display_name bossa sahibin kullanici adi. Sahip baska bir
        koltukta da kayitliysa (tutarsiz eski veri) birincil satir kullanicisiz kurulur. Es zamanli iki kurulumda
        kismi benzersiz indeks ikinciyi durdurur; kaybeden guncel satiri okur.
        """
        row = self._primary_row()
        taken = user_id is not None and self.db.scalar(
            select(WorldManager.id).where(WorldManager.user_id == user_id)) is not None
        if row is not None:
            if row.user_id is None and user_id is not None and not taken:
                row.user_id = user_id                    # sahipsiz satir sahibine baglanir (onarim)
                self.db.flush()
            return self._primary_seat(row)
        name = clean_display_name(display_name) if display_name and str(display_name).strip() else \
            self._owner_name(user_id)
        if taken:
            user_id = None
        try:
            with self.db.begin_nested():
                row = WorldManager(user_id=user_id, display_name=name, is_primary=True,
                                   joined_career_week=self._career_week())
                self.db.add(row)
                self.db.flush()
        except IntegrityError:
            row = self._primary_row()
            if row is None:
                raise
        return self._primary_seat(row)

    def create_seat(self, user_id: int, display_name: str, reputation: float, career_week: int) -> Seat:
        """
        Birincil olmayan koltuk (kulupsuz, ACTIVE). Ayrilmis / atilmis koltugu (LEFT/KICKED) yeniden etkinlestirir.
        Dunya sahibi ya da zaten uye olan kullanici -> SeatError.
        """
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise SeatError("Geçersiz kullanıcı.")
        name = clean_display_name(display_name)
        st = self._state()
        primary = self._primary_row()
        if (st is not None and st.user_id == user_id) or (primary is not None and primary.user_id == user_id):
            raise SeatError("Dünyanın sahibi zaten bu dünyada menajer.")
        rep = reputation_rules.clamp(float(reputation)) if reputation is not None else \
            reputation_rules.START_REPUTATION
        self.db.flush()
        row = self.db.scalar(select(WorldManager).where(WorldManager.user_id == user_id))
        new = row is None
        if not new:
            if row.status in MEMBER_STATUSES:
                raise SeatError("Bu dünyada zaten bir menajer koltuğun var.")
            row.status = SeatStatus.ACTIVE.value
            row.display_name, row.team_id, row.reputation = name, None, rep
            row.ready_career_week, row.missed_deadlines = None, 0
            row.joined_career_week = int(career_week)
        else:
            row = WorldManager(user_id=user_id, display_name=name, is_primary=False, team_id=None, reputation=rep,
                               status=SeatStatus.ACTIVE.value, joined_career_week=int(career_week))
        try:
            with self.db.begin_nested():             # es zamanli katilim: benzersiz indeks ihlali geri alinir
                if new:
                    self.db.add(row)
                self.db.flush()
        except IntegrityError as exc:
            raise SeatError("Bu dünyada zaten bir menajer koltuğun var.") from exc
        return self._seat(row)

    def _locked_state(self) -> GameState | None:
        """Kulup secimleri GameState satiri uzerinden siralanir (birincilin set_user_team'i ile yarismasin)."""
        self.db.flush()
        return self.db.get(GameState, 1, with_for_update=True, populate_existing=True)

    def assign_team(self, seat: Seat, team_id: int | None) -> Seat:
        """
        Koltuga kulup verir (None: kulubu birakir, durum degismez). Kulup baska bir koltukta ya da birincilin
        kulubuyse SeatError. Birincil koltukta GameState.user_team_id yazilir. Kulup alan koltuk ACTIVE olur.
        """
        st = self._locked_state()
        team = None
        if team_id is not None:
            team = self.db.get(Team, team_id) if isinstance(team_id, int) and not isinstance(team_id, bool) else None
            if team is None:
                raise SeatError("Kulüp bulunamadı.")
        if seat.is_primary:
            if team is not None and self.db.scalar(select(WorldManager.id).where(
                    WorldManager.team_id == team.id, WorldManager.is_primary.is_(False))) is not None:
                raise SeatError(f"{team.name} başka bir menajerin kulübü.")
            if st is not None:
                st.user_team_id = team.id if team is not None else None
                st.user_team = team
            self.db.flush()
            return self.primary()

        row = self.db.get(WorldManager, seat.id) if seat.id is not None else None
        if row is None or row.is_primary:
            raise SeatError("Menajer koltuğu bulunamadı.")
        if row.status not in MEMBER_STATUSES:
            raise SeatError("Bu menajer dünyadan ayrılmış; kulüp atanamaz.")
        if team is None:
            row.team_id = None
            self.db.flush()
            return self._seat(row)
        if st is not None and st.user_team_id == team.id:
            raise SeatError(f"{team.name} dünya sahibinin kulübü.")
        holder = self.db.scalar(select(WorldManager).where(WorldManager.team_id == team.id))
        if holder is not None and holder.id != row.id:
            raise SeatError(f"{team.name} başka bir menajerin kulübü.")
        try:
            with self.db.begin_nested():             # es zamanli kulup secimi: benzersiz indeks ihlali geri alinir
                row.team_id = team.id
                row.status = SeatStatus.ACTIVE.value
                self.db.flush()
        except IntegrityError as exc:
            raise SeatError(f"{team.name} başka bir menajerin kulübü.") from exc
        return self._seat(row)

    def release(self, seat: Seat, status: str, protected_until: int | None) -> int | None:
        """
        Koltugun kulubunu birakir: RELEASED (uyelik surer), LEFT ya da KICKED. protected_until (mutlak kariyer
        haftasi) verilirse kulup o haftaya kadar AI transferlerine kapali (teams.ai_protected_until; mevcut daha
        uzunsa korunur). Birincil koltuk yalnizca RELEASED olabilir (sahip dunyadan ayrilamaz): GameState kulubu
        bosalir. Birakilan kulup id'si (yoksa None). Hazir isareti silinir.
        """
        status = getattr(status, "value", status)
        if status not in RELEASE_STATUSES:
            raise SeatError(f"Geçersiz koltuk durumu: {status!r}.")
        if seat.is_primary:
            if status != SeatStatus.RELEASED.value:
                raise SeatError("Dünyanın sahibi dünyadan ayrılamaz ya da atılamaz.")
            st = self._locked_state()
            team_id = st.user_team_id if st is not None else None
            if st is not None:
                st.user_team_id, st.user_team = None, None
            row = self._primary_row()
            if row is not None:
                row.ready_career_week = None
        else:
            row = self.db.get(WorldManager, seat.id) if seat.id is not None else None
            if row is None or row.is_primary:
                raise SeatError("Menajer koltuğu bulunamadı.")
            team_id = row.team_id
            row.team_id, row.status, row.ready_career_week = None, status, None
        if team_id is not None and protected_until is not None:
            team = self.db.get(Team, team_id)
            if team is not None:
                team.ai_protected_until = max(int(protected_until), int(team.ai_protected_until or 0))
        self.db.flush()
        return team_id

    def _row_for(self, seat: Seat) -> WorldManager:
        row = self.db.get(WorldManager, seat.id) if seat.id is not None else None
        if row is None:
            raise SeatError("Menajer koltuğu bulunamadı.")
        return row

    def set_ready(self, seat: Seat, career_week: int | None) -> None:
        """Hazir isareti: ready_career_week = verilen kariyer haftasi (None: hazir degil)."""
        row = self._row_for(seat)
        row.ready_career_week = None if career_week is None else int(career_week)
        self.db.flush()

    def touch(self, seat: Seat, now: datetime) -> None:
        """last_active_at; en fazla 5 dakikada bir yazilir (satiri olmayan birincil koltuk yazilmaz)."""
        if seat.id is None:
            return
        self.db.execute(
            update(WorldManager)
            .where(WorldManager.id == seat.id,
                   or_(WorldManager.last_active_at.is_(None), WorldManager.last_active_at < now - TOUCH_INTERVAL))
            .values(last_active_at=now)
        )

    def record_missed_deadline(self, seat: Seat) -> int:
        """Kacirilan hafta sayacini bir artirir; yeni sayi."""
        row = self._row_for(seat)
        row.missed_deadlines = int(row.missed_deadlines or 0) + 1
        self.db.flush()
        return row.missed_deadlines

    def adjust_fair_play(self, seat_id: int, delta: float, reason: str, offer_id: int | None = None) -> float:
        """Adil oyun puanina degisim (0-100 arasinda kirpilir) + fair_play_log satiri. Yeni puan."""
        row = self.db.get(WorldManager, seat_id, with_for_update=True) \
            if isinstance(seat_id, int) and not isinstance(seat_id, bool) else None
        if row is None:
            raise SeatError("Menajer koltuğu bulunamadı.")
        before = float(row.fair_play)
        after = max(FAIR_PLAY_MIN, min(FAIR_PLAY_MAX, before + float(delta)))
        row.fair_play = after
        self.db.add(FairPlayLog(manager_id=row.id, delta=round(after - before, 4),
                                reason=(str(reason or "").strip() or "adil oyun")[:FAIR_PLAY_REASON_MAX],
                                offer_id=offer_id, career_week=self._career_week()))
        self.db.flush()
        return after
