"""
messaging.py
============
Menajerler arasi mesajlar, dunya panosu ve bildirimler (Faz 12 / 14. Asama, 12B). Veritabani katmani; commit
ETMEZ (cagiranin islemine katilir, yazdiktan sonra flush eder -- hatalar cagiranin savepoint'i icinde yuzeye cikar).

GUVENLIK (XSS): metinler DUZ METIN olarak oldugu gibi saklanir ve gorunumlerde (MessageView / PostView /
NotificationView) oldugu gibi doner; bu modul HTML uretmez ve kacis YAPMAZ. Arayuz (messages_view, B4) her metni
st.text ya da st.markdown(html.escape(...)) ile cizer, asla unsafe_allow_html. Yalnizca PostgreSQL'in kabul etmedigi
/ gorunmez denetim karakterleri temizlenir (NUL vb.; govdede satir sonu ve sekme kalir), bas / son bosluk kirpilir.

Kurallar:
    * Mesaj: gonderen ve alici bu dunyanin UYE koltuklari (ACTIVE ya da RELEASED -- kulubunu kaybeden menajer de
      uyedir); LEFT / KICKED koltuk gonderemez ve alamaz; kendine mesaj yok. Konu 1-80, govde 1-1000 karakter
      (kirpildiktan sonra). offer_id verilirse teklif var olmali (teklife erisim yetkisini arayuz MarketHub ile denetler).
    * Silme her taraf icin ayridir (deleted_by_sender / deleted_by_recipient); satir kalir. Baskasinin mesajina
      dokunmak -> MessageError("Mesaj bulunamadı.") (var olup olmadigi sizdirilmaz).
    * Pano: gonderi 1-500 karakter; en yeni ustte, sabitlenenler en ustte. Silme: yazar ya da dunya yoneticisi
      (is_admin); silinen gonderi kind="DELETED" olur (panoda gorunmez, hiz sinirina sayilmaya devam eder).
      Sabitleme yalnizca yonetici (pin_post). system_post: yazari olmayan (author None) SYSTEM duyurusu.
    * Hiz siniri: koltuk basina kayan 1 saatte RATE_LIMIT_PER_HOUR mesaj VE ayrica RATE_LIMIT_PER_HOUR gonderi
      (iki ayri butce; silinenler de sayilir). Gonderen koltuk satiri FOR NO KEY UPDATE ile kilitlenir: ayni
      koltugun es zamanli gonderimleri siniri asamaz (FK'nin KEY SHARE kilitleriyle cakismaz).
    * Bildirim: notify(db, seat_id, kind, text, ref_type, ref_id) -- metin 300 karakterden uzunsa "…" ile kisaltilir.
      Mesaj gonderimi bildirim YAZMAZ (okunmamis mesajlar zaten unread_total'a sayilir; cift sayim olmasin).
    * Zaman: tum created_at / read_at degerleri utc_now() ile Python'dan yazilir (testler saati degistirebilir);
      gorunumlerde saat dilimli UTC.

    Messaging(db, seat, is_admin=False)
        send / inbox / outbox / message / mark_read / delete / recipients
        post / board / delete_post / pin_post
        notifications / mark_notifications_read / unread_messages / unread_notifications / unread_total
    notify(db, seat_id, kind, text, ref_type=None, ref_id=None)      (world_manager tur ilerlemesi, market_hub)
    system_post(db, text, *, pinned=False)
    kind_label(kind) -> Turkce bildirim turu etiketi
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from models import ManagerMessage, Notification, TransferOffer, WorldManager, WorldPost
from seats import MEMBER_STATUSES, Seat, SeatStore

SUBJECT_MAX = 80                  # manager_messages.subject
BODY_MAX = 1000                   # manager_messages.body
POST_MAX = 500                    # world_posts.body
NOTIFICATION_MAX = 300            # notifications.text
REF_TYPE_MAX = 16                 # notifications.ref_type
RATE_LIMIT_PER_HOUR = 30
RATE_WINDOW = timedelta(hours=1)
LIST_LIMIT_DEFAULT, LIST_LIMIT_MAX = 50, 200
ELLIPSIS = "…"

POST_KIND = "POST"
SYSTEM_KIND = "SYSTEM"
DELETED_KIND = "DELETED"
FORMER_MANAGER = "Ayrılan menajer"      # yazari / gondereni silinmis (FK SET NULL) menajer satiri

NO_SEAT_TEXT = "Bu dünyada menajer koltuğun yok."
SELF_TEXT = "Kendine mesaj gönderemezsin."
RECIPIENT_TEXT = "Alıcı bu dünyada aktif bir menajer değil."
SUBJECT_EMPTY_TEXT = "Mesaj konusu boş olamaz."
SUBJECT_LONG_TEXT = f"Mesaj konusu en fazla {SUBJECT_MAX} karakter olabilir."
BODY_EMPTY_TEXT = "Mesaj boş olamaz."
BODY_LONG_TEXT = f"Mesaj en fazla {BODY_MAX} karakter olabilir."
POST_EMPTY_TEXT = "Gönderi boş olamaz."
POST_LONG_TEXT = f"Gönderi en fazla {POST_MAX} karakter olabilir."
MESSAGE_RATE_TEXT = "Saatte en fazla {limit} mesaj gönderebilirsin; {minutes} dakika sonra tekrar dene."
POST_RATE_TEXT = "Saatte en fazla {limit} gönderi paylaşabilirsin; {minutes} dakika sonra tekrar dene."
MESSAGE_NOT_FOUND_TEXT = "Mesaj bulunamadı."
POST_NOT_FOUND_TEXT = "Gönderi bulunamadı."
POST_DELETE_FORBIDDEN_TEXT = "Bu gönderiyi yalnızca yazarı ya da dünya yöneticisi silebilir."
PIN_FORBIDDEN_TEXT = "Gönderileri yalnızca dünya yöneticisi sabitleyebilir."
OFFER_NOT_FOUND_TEXT = "Teklif bulunamadı."
SEAT_NOT_FOUND_TEXT = "Menajer koltuğu bulunamadı."
NOTIFICATION_EMPTY_TEXT = "Bildirim metni boş olamaz."
KIND_INVALID_TEXT = "Geçersiz bildirim türü."
REF_INVALID_TEXT = "Geçersiz bildirim bağlantısı."

# Govde: satir sonu (\n) ve sekme (\t) disindaki C0 denetim karakterleri + DEL (PostgreSQL NUL kabul etmez)
_BODY_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ANY_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MessageError(ValueError):
    """Mesaj islemi yapilamaz (mesaj Turkce)."""


class NotificationKind(str, Enum):
    OFFER_IN = "OFFER_IN"
    OFFER_UPDATE = "OFFER_UPDATE"
    MESSAGE = "MESSAGE"
    TURN = "TURN"
    KICK_WARNING = "KICK_WARNING"
    RELEASED = "RELEASED"
    REVIEW = "REVIEW"
    NATIONAL = "NATIONAL"


KIND_LABELS: dict[str, str] = {
    NotificationKind.OFFER_IN.value: "Gelen teklif",
    NotificationKind.OFFER_UPDATE.value: "Teklif güncellemesi",
    NotificationKind.MESSAGE.value: "Mesaj",
    NotificationKind.TURN.value: "Hafta",
    NotificationKind.KICK_WARNING.value: "Uyarı",
    NotificationKind.RELEASED.value: "Kulüp kaybı",
    NotificationKind.REVIEW.value: "Adil oyun incelemesi",
    NotificationKind.NATIONAL.value: "Milli takım",
}


@dataclass(frozen=True)
class MessageView:
    id: int
    sender_name: str | None
    recipient_name: str | None
    subject: str
    body: str
    offer_id: int | None
    created_at: datetime | None       # saat dilimli UTC
    read: bool                        # alici okudu mu (gelen ve giden kutusunda ayni anlam)
    mine: bool                        # gonderen bu koltuk


@dataclass(frozen=True)
class PostView:
    id: int
    author_name: str | None           # None: sistem duyurusu
    kind: str                         # POST / SYSTEM
    body: str
    created_at: datetime | None       # saat dilimli UTC
    can_delete: bool
    pinned: bool = False


@dataclass(frozen=True)
class NotificationView:
    id: int
    kind: str
    text: str
    ref_type: str | None
    ref_id: int | None
    created_at: datetime | None       # saat dilimli UTC
    read: bool


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """Modul saati (testler monkeypatch ile degistirir)."""
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        return LIST_LIMIT_DEFAULT
    return max(1, min(LIST_LIMIT_MAX, limit))


def clean_body(text) -> str:
    """Cok satirli duz metin: CRLF -> LF, gorunmez denetim karakterleri silinir, bas / son bosluk kirpilir."""
    raw = "" if text is None else str(text)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    return _BODY_CONTROL.sub("", raw).strip()


def clean_line(text) -> str:
    """Tek satir duz metin (konu): denetim karakterleri ve ardisik bosluklar tek bosluk."""
    raw = "" if text is None else str(text)
    return " ".join(_ANY_CONTROL.sub(" ", raw).split())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + ELLIPSIS


def _validated(text: str, limit: int, empty_text: str, long_text: str) -> str:
    if not text:
        raise MessageError(empty_text)
    if len(text) > limit:
        raise MessageError(long_text)
    return text


def kind_label(kind) -> str:
    """Bildirim turunun Turkce etiketi (bilinmeyen tur oldugu gibi)."""
    value = getattr(kind, "value", kind)
    return KIND_LABELS.get(str(value), str(value))


# ---------------------------------------------------------------------------
# Kontrolcu
# ---------------------------------------------------------------------------

class Messaging:
    """
    Oturumdaki koltugun mesajlari, panosu ve bildirimleri. Commit ETMEZ. seat None (izleyici / satiri olmayan birincil
    koltuk): okumalar bos, yazmalar MessageError. is_admin cagiran tarafindan (dunya rolu OWNER/ADMIN) verilir.
    """

    def __init__(self, db, seat: Seat | None, is_admin: bool = False) -> None:
        self.db = db
        self.seat = seat
        self.is_admin = bool(is_admin)
        self.seats = SeatStore(db)

    @property
    def seat_id(self) -> int | None:
        return self.seat.id if self.seat is not None else None

    # ------------------------------------------------------------------ ortak

    def _require_member(self, *, lock: bool = False) -> Seat:
        """Koltuk bu dunyanin uyesi mi (DB'den taze okunur). lock: ayni koltugun gonderimleri siralanir."""
        seat_id = self.seat_id
        if seat_id is None:
            raise MessageError(NO_SEAT_TEXT)
        if lock:
            self.db.execute(select(WorldManager.id).where(WorldManager.id == seat_id)
                            .with_for_update(key_share=True))           # FOR NO KEY UPDATE
        fresh = self.seats.by_id(seat_id)
        if fresh is None or fresh.status not in MEMBER_STATUSES:
            raise MessageError(NO_SEAT_TEXT)
        return fresh

    def _check_rate(self, created_col, owner_col, seat_id: int, now: datetime, template: str) -> None:
        since = now - RATE_WINDOW
        count, oldest = self.db.execute(
            select(func.count(), func.min(created_col)).where(owner_col == seat_id, created_col > since)
        ).one()
        if int(count or 0) < RATE_LIMIT_PER_HOUR:
            return
        oldest = _as_utc(oldest) or now
        minutes = max(1, math.ceil((oldest + RATE_WINDOW - now).total_seconds() / 60))
        raise MessageError(template.format(limit=RATE_LIMIT_PER_HOUR, minutes=minutes))

    # ------------------------------------------------------------------ mesajlar

    def recipients(self) -> list[tuple[int, str]]:
        """Mesaj gonderilebilecek koltuklar (uye, bu koltuk haric): (koltuk id, gorunen ad)."""
        me = self.seat_id
        return [(s.id, s.display_name) for s in self.seats.members() if s.id is not None and s.id != me]

    def send(self, to_seat_id: int, subject: str, body: str, offer_id: int | None = None) -> MessageView:
        me = self._require_member(lock=True)
        if not _is_id(to_seat_id):
            raise MessageError(RECIPIENT_TEXT)
        if to_seat_id == me.id:
            raise MessageError(SELF_TEXT)
        recipient = self.seats.by_id(to_seat_id)
        if recipient is None or recipient.status not in MEMBER_STATUSES:
            raise MessageError(RECIPIENT_TEXT)
        subject = _validated(clean_line(subject), SUBJECT_MAX, SUBJECT_EMPTY_TEXT, SUBJECT_LONG_TEXT)
        body = _validated(clean_body(body), BODY_MAX, BODY_EMPTY_TEXT, BODY_LONG_TEXT)
        if offer_id is not None and (not _is_id(offer_id) or self.db.get(TransferOffer, offer_id) is None):
            raise MessageError(OFFER_NOT_FOUND_TEXT)
        now = utc_now()
        self._check_rate(ManagerMessage.created_at, ManagerMessage.sender_manager_id, me.id, now,
                         MESSAGE_RATE_TEXT)
        row = ManagerMessage(sender_manager_id=me.id, recipient_manager_id=recipient.id, offer_id=offer_id,
                             subject=subject, body=body, created_at=now)
        self.db.add(row)
        self.db.flush()
        return MessageView(id=row.id, sender_name=me.display_name, recipient_name=recipient.display_name,
                           subject=subject, body=body, offer_id=offer_id, created_at=_as_utc(now), read=False,
                           mine=True)

    def _message_rows(self, *where, limit: int) -> list[MessageView]:
        sender, recipient = aliased(WorldManager), aliased(WorldManager)
        rows = self.db.execute(
            select(ManagerMessage, sender.display_name, recipient.display_name)
            .outerjoin(sender, sender.id == ManagerMessage.sender_manager_id)
            .outerjoin(recipient, recipient.id == ManagerMessage.recipient_manager_id)
            .where(*where)
            .order_by(ManagerMessage.created_at.desc(), ManagerMessage.id.desc())
            .limit(limit)
        ).all()
        return [self._message_view(msg, s_name, r_name) for msg, s_name, r_name in rows]

    def _message_view(self, msg: ManagerMessage, sender_name: str | None, recipient_name: str | None) -> MessageView:
        return MessageView(
            id=msg.id,
            sender_name=sender_name if sender_name is not None else FORMER_MANAGER,
            recipient_name=recipient_name if recipient_name is not None else FORMER_MANAGER,
            subject=msg.subject,
            body=msg.body,
            offer_id=msg.offer_id,
            created_at=_as_utc(msg.created_at),
            read=msg.read_at is not None,
            mine=self.seat_id is not None and msg.sender_manager_id == self.seat_id,
        )

    def inbox(self, limit: int = 50) -> list[MessageView]:
        """Gelen kutusu (alicinin sildikleri haric), en yeni ustte."""
        if self.seat_id is None:
            return []
        return self._message_rows(ManagerMessage.recipient_manager_id == self.seat_id,
                                  ManagerMessage.deleted_by_recipient.is_(False), limit=_limit(limit))

    def outbox(self, limit: int = 50) -> list[MessageView]:
        """Giden kutusu (gonderenin sildikleri haric), en yeni ustte. read: alici okudu mu."""
        if self.seat_id is None:
            return []
        return self._message_rows(ManagerMessage.sender_manager_id == self.seat_id,
                                  ManagerMessage.deleted_by_sender.is_(False), limit=_limit(limit))

    def _visible_row(self, message_id: int) -> ManagerMessage:
        """Bu koltugun gorebildigi (gonderdigi ya da aldigi, kendi tarafinda silinmemis) mesaj."""
        me = self.seat_id
        msg = self.db.get(ManagerMessage, message_id) if _is_id(message_id) and me is not None else None
        if msg is None:
            raise MessageError(MESSAGE_NOT_FOUND_TEXT)
        if msg.recipient_manager_id == me and not msg.deleted_by_recipient:
            return msg
        if msg.sender_manager_id == me and not msg.deleted_by_sender:
            return msg
        raise MessageError(MESSAGE_NOT_FOUND_TEXT)

    def message(self, message_id: int) -> MessageView:
        """Tek mesaj (gonderen ya da alici). Okundu isaretlemez: mark_read ayri cagrilir."""
        msg = self._visible_row(message_id)
        names = dict(self.db.execute(select(WorldManager.id, WorldManager.display_name).where(
            WorldManager.id.in_([i for i in (msg.sender_manager_id, msg.recipient_manager_id) if i is not None])
        )).all())
        return self._message_view(msg, names.get(msg.sender_manager_id), names.get(msg.recipient_manager_id))

    def mark_read(self, message_id: int) -> None:
        """Yalnizca alici okundu isaretler (zaten okunduysa degismez)."""
        msg = self._visible_row(message_id)
        if msg.recipient_manager_id != self.seat_id:
            raise MessageError(MESSAGE_NOT_FOUND_TEXT)
        if msg.read_at is None:
            msg.read_at = utc_now()
            self.db.flush()

    def delete(self, message_id: int) -> None:
        """Mesaji bu koltugun tarafindan siler (karsi taraf gormeye devam eder)."""
        msg = self._visible_row(message_id)
        if msg.recipient_manager_id == self.seat_id:
            msg.deleted_by_recipient = True
        if msg.sender_manager_id == self.seat_id:
            msg.deleted_by_sender = True
        self.db.flush()

    # ------------------------------------------------------------------ pano

    def _post_view(self, post: WorldPost, author_name: str | None) -> PostView:
        if post.author_manager_id is None:
            name = None if post.kind == SYSTEM_KIND else FORMER_MANAGER
        else:
            name = author_name if author_name is not None else FORMER_MANAGER
        mine = self.seat_id is not None and post.author_manager_id == self.seat_id
        return PostView(id=post.id, author_name=name, kind=post.kind, body=post.body,
                        created_at=_as_utc(post.created_at), can_delete=self.is_admin or mine,
                        pinned=bool(post.pinned))

    def post(self, body: str) -> PostView:
        me = self._require_member(lock=True)
        body = _validated(clean_body(body), POST_MAX, POST_EMPTY_TEXT, POST_LONG_TEXT)
        now = utc_now()
        self._check_rate(WorldPost.created_at, WorldPost.author_manager_id, me.id, now, POST_RATE_TEXT)
        row = WorldPost(author_manager_id=me.id, kind=POST_KIND, body=body, pinned=False, created_at=now)
        self.db.add(row)
        self.db.flush()
        return self._post_view(row, me.display_name)

    def board(self, limit: int = 50) -> list[PostView]:
        """Dunya panosu: sabitlenenler ustte, sonra en yeni. Silinen gonderiler gorunmez."""
        author = aliased(WorldManager)
        rows = self.db.execute(
            select(WorldPost, author.display_name)
            .outerjoin(author, author.id == WorldPost.author_manager_id)
            .where(WorldPost.kind != DELETED_KIND)
            .order_by(WorldPost.pinned.desc(), WorldPost.created_at.desc(), WorldPost.id.desc())
            .limit(_limit(limit))
        ).all()
        return [self._post_view(post, name) for post, name in rows]

    def _live_post(self, post_id: int) -> WorldPost:
        post = self.db.get(WorldPost, post_id) if _is_id(post_id) else None
        if post is None or post.kind == DELETED_KIND:
            raise MessageError(POST_NOT_FOUND_TEXT)
        return post

    def delete_post(self, post_id: int) -> None:
        """Yazar ya da yonetici siler (sistem duyurusunu yalnizca yonetici)."""
        post = self._live_post(post_id)
        mine = self.seat_id is not None and post.author_manager_id == self.seat_id
        if not (self.is_admin or mine):
            raise MessageError(POST_DELETE_FORBIDDEN_TEXT)
        post.kind, post.pinned = DELETED_KIND, False
        self.db.flush()

    def pin_post(self, post_id: int, pinned: bool = True) -> PostView:
        """Yonetici gonderiyi panonun ustune sabitler / sabitlemeyi kaldirir."""
        if not self.is_admin:
            raise MessageError(PIN_FORBIDDEN_TEXT)
        post = self._live_post(post_id)
        post.pinned = bool(pinned)
        self.db.flush()
        name = self.db.scalar(select(WorldManager.display_name).where(WorldManager.id == post.author_manager_id)) \
            if post.author_manager_id is not None else None
        return self._post_view(post, name)

    # ------------------------------------------------------------------ bildirimler

    def notifications(self, unread_only: bool = False, limit: int = 50) -> list[NotificationView]:
        if self.seat_id is None:
            return []
        stmt = select(Notification).where(Notification.manager_id == self.seat_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        rows = self.db.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
                               .limit(_limit(limit)))
        return [NotificationView(id=n.id, kind=n.kind, text=n.text, ref_type=n.ref_type, ref_id=n.ref_id,
                                 created_at=_as_utc(n.created_at), read=n.read_at is not None) for n in rows]

    def mark_notifications_read(self, ids: Iterable[int] | None = None) -> int:
        """ids None: tum okunmamislar; aksi halde yalnizca bu koltugun verilen bildirimleri. Isaretlenen sayi."""
        if self.seat_id is None:
            return 0
        stmt = update(Notification).where(Notification.manager_id == self.seat_id, Notification.read_at.is_(None))
        if ids is not None:
            if _is_id(ids):
                ids = [ids]
            wanted = sorted({i for i in ids if _is_id(i)}) if not isinstance(ids, (str, bytes)) else []
            if not wanted:
                return 0
            stmt = stmt.where(Notification.id.in_(wanted))
        result = self.db.execute(stmt.values(read_at=utc_now()).execution_options(synchronize_session="fetch"))
        return int(result.rowcount or 0)

    def unread_messages(self) -> int:
        if self.seat_id is None:
            return 0
        return int(self.db.scalar(select(func.count()).select_from(ManagerMessage).where(
            ManagerMessage.recipient_manager_id == self.seat_id, ManagerMessage.deleted_by_recipient.is_(False),
            ManagerMessage.read_at.is_(None))) or 0)

    def unread_notifications(self) -> int:
        if self.seat_id is None:
            return 0
        return int(self.db.scalar(select(func.count()).select_from(Notification).where(
            Notification.manager_id == self.seat_id, Notification.read_at.is_(None))) or 0)

    def unread_total(self) -> int:
        """Okunmamis mesajlar + okunmamis bildirimler (kenar cubugu rozeti)."""
        return self.unread_messages() + self.unread_notifications()


# ---------------------------------------------------------------------------
# Modul fonksiyonlari (sistem yazilari)
# ---------------------------------------------------------------------------

def notify(
    db,
    seat_id: int,
    kind: NotificationKind,
    text: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> None:
    """
    Koltuga bildirim yazar ve flush eder (hata cagiranin savepoint'inde yuzeye cikar). kind: NotificationKind ya da
    degeri. Metin 300 karakteri asarsa "…" ile kisaltilir; bos metin, bilinmeyen tur / koltuk -> MessageError.
    Koltuk durumuna bakilmaz (cagiran karar verir).
    """
    try:
        kind = NotificationKind(getattr(kind, "value", kind))
    except ValueError as exc:
        raise MessageError(KIND_INVALID_TEXT) from exc
    if not _is_id(seat_id) or db.get(WorldManager, seat_id) is None:
        raise MessageError(SEAT_NOT_FOUND_TEXT)
    body = _clip(clean_body(text), NOTIFICATION_MAX)
    if not body:
        raise MessageError(NOTIFICATION_EMPTY_TEXT)
    if ref_type is not None:
        ref_type = clean_line(ref_type)
        if not ref_type or len(ref_type) > REF_TYPE_MAX:
            raise MessageError(REF_INVALID_TEXT)
    if ref_id is not None and (isinstance(ref_id, bool) or not isinstance(ref_id, int)):
        raise MessageError(REF_INVALID_TEXT)
    db.add(Notification(manager_id=seat_id, kind=kind.value, text=body, ref_type=ref_type, ref_id=ref_id,
                        created_at=utc_now()))
    db.flush()


def system_post(db, text: str, *, pinned: bool = False) -> None:
    """Yazarsiz (sistem) pano duyurusu; 500 karakteri asan metin "…" ile kisaltilir, bos metin MessageError."""
    body = _clip(clean_body(text), POST_MAX)
    if not body:
        raise MessageError(POST_EMPTY_TEXT)
    db.add(WorldPost(author_manager_id=None, kind=SYSTEM_KIND, body=body, pinned=bool(pinned),
                     created_at=utc_now()))
    db.flush()
