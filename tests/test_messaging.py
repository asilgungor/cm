"""
Faz 12 / 14. Asama B3: menajer mesajlari, dunya panosu ve bildirimler (messaging.py) -- PostgreSQL testleri.

Sentetik 'public' dunyasi modul basina bir kez paylasilan dunyaya cevrilir (tests/world_helpers.make_shared_public);
her testten once mesaj / pano / bildirim tablolari bosaltilir ve koltuklar ACTIVE yapilir. Modul sonunda dunya
yeniden kurulur (cleanup_shared). Tur ilerlemesi entegrasyonu (world_manager.try_advance -> messaging.notify)
dosyanin sonunda. Kendi veritabaninda calistirin: TEST_DB_NAME=fm_db_test_b3.
"""

from __future__ import annotations

import dataclasses
import html
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import messaging  # noqa: E402
from messaging import MessageError, Messaging, NotificationKind, notify, system_post  # noqa: E402
from models import ManagerMessage, Notification, WorldManager, WorldPost  # noqa: E402
from seats import SeatStore  # noqa: E402

SCHEMA = "public"
OWNER, MEMBER, THIRD, SPARE = "b3_sahip", "b3_uye", "b3_ucuncu", "b3_bos"
OWNER_TEAM, MEMBER_TEAM = "Istanbul Lions", "Kadıköy Canaries"
T0 = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


# ===========================================================================
# Fikstur ve yardimcilar
# ===========================================================================

def _drop_test_users() -> None:
    database.init_accounts()
    with database.engine.begin() as conn:
        conn.execute(text('DELETE FROM "accounts".users WHERE username = ANY(:names)'),
                     {"names": [OWNER, MEMBER, THIRD, SPARE]})


@pytest.fixture(scope="module")
def world():
    from tests.world_helpers import cleanup_shared, make_shared_public

    cleanup_shared(None, reseed=False)                      # onceki kosudan kalan kayit / kullanici
    _drop_test_users()
    shared = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM), (THIRD, None), (SPARE, None)],
                                owner_team=OWNER_TEAM)
    try:
        yield shared
    finally:
        cleanup_shared(shared)


@pytest.fixture(autouse=True)
def clean_messages(world):
    with database.career_context(SCHEMA), database.session_scope() as db:
        for model in (ManagerMessage, WorldPost, Notification):
            db.execute(delete(model))
        db.execute(update(WorldManager).where(WorldManager.is_primary.is_(False))
                   .values(status="ACTIVE"))
    yield


@pytest.fixture
def clock(monkeypatch):
    """messaging.utc_now yerine elle ilerletilen saat."""
    holder = {"now": T0}
    monkeypatch.setattr(messaging, "utc_now", lambda: holder["now"])

    class Clock:
        def set(self, moment: datetime) -> None:
            holder["now"] = moment

        def advance(self, **delta) -> None:
            holder["now"] = holder["now"] + timedelta(**delta)

    return Clock()


@contextmanager
def _as(world, name: str | None, *, admin: bool = False):
    """Web callback'i gibi: SHARED dunya kilidi altinda o koltugun Messaging'i; blok sonunda commit."""
    with database.career_context(SCHEMA), database.world_lock(SCHEMA, "shared"), \
            database.session_scope() as db:
        seat = SeatStore(db).by_id(world.seat_ids[name]) if name is not None else None
        yield Messaging(db, seat, is_admin=admin)


@contextmanager
def _db():
    with database.career_context(SCHEMA), database.session_scope() as db:
        yield db


def _set_status(world, name: str, status: str) -> None:
    with _db() as db:
        db.get(WorldManager, world.seat_ids[name]).status = status


def _is_utc(moment: datetime | None) -> bool:
    return moment is not None and moment.utcoffset() == timedelta(0)


# ===========================================================================
# Mesajlar
# ===========================================================================

def test_send_inbox_outbox_read_flags_and_names(world):
    member_id = world.seat_ids[MEMBER]
    with _as(world, OWNER) as box:
        sent = box.send(member_id, "  Transfer hakkında  ", "\nMerhaba,\nforvetin satılık mı?  ")
    assert (sent.subject, sent.body) == ("Transfer hakkında", "Merhaba,\nforvetin satılık mı?")
    assert (sent.sender_name, sent.recipient_name, sent.mine, sent.read) == (OWNER, MEMBER, True, False)
    assert _is_utc(sent.created_at)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sent.read = True

    with _as(world, MEMBER) as box:
        assert box.unread_total() == 1 and box.unread_messages() == 1
        [got] = box.inbox()
        assert (got.id, got.sender_name, got.recipient_name, got.mine, got.read) == (sent.id, OWNER, MEMBER,
                                                                                       False, False)
        assert got.body == sent.body and _is_utc(got.created_at) and got.offer_id is None
        assert box.outbox() == []
        box.mark_read(sent.id)
        box.mark_read(sent.id)                                  # ikinci kez: degismez
        assert box.inbox()[0].read and box.unread_total() == 0
        assert box.message(sent.id).read
        reply = box.send(world.seat_ids[OWNER], "Re: Transfer hakkında", "Hayır.")

    with _as(world, OWNER) as box:
        outbox = box.outbox()
        assert [m.id for m in outbox] == [sent.id]
        assert outbox[0].mine and outbox[0].read                 # alici okudu
        [incoming] = box.inbox()
        assert (incoming.id, incoming.sender_name, incoming.read) == (reply.id, MEMBER, False)
        assert box.unread_total() == 1
        assert [(r_id, name) for r_id, name in box.recipients()] == [
            (world.seat_ids[MEMBER], MEMBER), (world.seat_ids[THIRD], THIRD), (world.seat_ids[SPARE], SPARE)]


def test_managers_cannot_read_or_delete_messages_of_others(world):
    with _as(world, OWNER) as box:
        msg = box.send(world.seat_ids[MEMBER], "Gizli", "Yalnızca sen oku.")
    with _as(world, THIRD) as box:
        for action in (box.mark_read, box.delete, box.message):
            with pytest.raises(MessageError, match="Mesaj bulunamadı"):
                action(msg.id)
        with pytest.raises(MessageError, match="Mesaj bulunamadı"):
            box.mark_read(999_999)                               # olmayan mesajla ayni yanit
        assert box.inbox() == [] and box.outbox() == [] and box.unread_total() == 0
    with _as(world, OWNER) as box:                               # gonderen okundu isaretleyemez
        with pytest.raises(MessageError, match="Mesaj bulunamadı"):
            box.mark_read(msg.id)
    with _db() as db:
        assert db.get(ManagerMessage, msg.id).read_at is None

    with _as(world, MEMBER) as box:                              # alici kendi tarafindan siler
        box.delete(msg.id)
        assert box.inbox() == [] and box.unread_total() == 0
        with pytest.raises(MessageError, match="Mesaj bulunamadı"):
            box.delete(msg.id)
        with pytest.raises(MessageError, match="Mesaj bulunamadı"):
            box.message(msg.id)
    with _as(world, OWNER) as box:                               # gonderen hala goruyor
        assert [m.id for m in box.outbox()] == [msg.id]
        box.delete(msg.id)
        assert box.outbox() == []
    with _db() as db:                                            # yumusak silme: satir kalir
        row = db.get(ManagerMessage, msg.id)
        assert row.deleted_by_sender and row.deleted_by_recipient and row.body == "Yalnızca sen oku."


def test_recipient_and_sender_must_be_members_of_the_world(world):
    owner_id, member_id, third_id, spare_id = (world.seat_ids[n] for n in (OWNER, MEMBER, THIRD, SPARE))
    with _as(world, MEMBER) as box:
        with pytest.raises(MessageError, match="Kendine mesaj"):
            box.send(member_id, "Konu", "Metin")
        for bad in (999_999, 0, -1, True, "5", None):
            with pytest.raises(MessageError, match="aktif bir menajer değil"):
                box.send(bad, "Konu", "Metin")
        assert box.send(owner_id, "Sahibe", "Birincil koltuk da alıcı olabilir.").recipient_name == OWNER

    for status in ("LEFT", "KICKED"):
        _set_status(world, SPARE, status)
        with _as(world, MEMBER) as box:
            with pytest.raises(MessageError, match="aktif bir menajer değil"):
                box.send(spare_id, "Konu", "Metin")
            assert spare_id not in {seat_id for seat_id, _ in box.recipients()}
        with _as(world, SPARE) as box:                           # ayrilan / atilan koltuk yazamaz
            with pytest.raises(MessageError, match="menajer koltuğun yok"):
                box.send(member_id, "Konu", "Metin")
            with pytest.raises(MessageError, match="menajer koltuğun yok"):
                box.post("Merhaba")

    _set_status(world, THIRD, "RELEASED")                        # kulubunu kaybeden menajer hala uye
    with _as(world, MEMBER) as box:
        assert box.send(third_id, "Geçmiş olsun", "Yeni kulübün hayırlı olsun.").recipient_name == THIRD
    with _as(world, THIRD) as box:
        assert box.send(member_id, "Sağ ol", "Teşekkürler.").sender_name == THIRD

    with _as(world, None) as box:                                # izleyici: okumalar bos, yazmalar hata
        assert (box.inbox(), box.outbox(), box.notifications(), box.recipients()[0][1]) == ([], [], [], OWNER)
        assert box.unread_total() == 0 and box.mark_notifications_read() == 0
        with pytest.raises(MessageError, match="menajer koltuğun yok"):
            box.send(member_id, "Konu", "Metin")
        with pytest.raises(MessageError, match="menajer koltuğun yok"):
            box.post("Merhaba")


def test_subject_body_and_offer_validation(world):
    to = world.seat_ids[MEMBER]
    with _as(world, OWNER) as box:
        for subject in ("", "   ", "\n\t", None):
            with pytest.raises(MessageError, match="konusu boş"):
                box.send(to, subject, "Metin")
        with pytest.raises(MessageError, match="en fazla 80"):
            box.send(to, "k" * 81, "Metin")
        for body in ("", "  \n  ", "\x00\x01"):
            with pytest.raises(MessageError, match="Mesaj boş"):
                box.send(to, "Konu", body)
        with pytest.raises(MessageError, match="en fazla 1000"):
            box.send(to, "Konu", "g" * 1001)
        with pytest.raises(MessageError, match="Teklif bulunamadı"):
            box.send(to, "Konu", "Metin", offer_id=999_999)

        longest = box.send(to, "ş" * 80, "ğ" * 1000)             # Turkce harf tek karakter
        assert (len(longest.subject), len(longest.body)) == (80, 1000)
        cleaned = box.send(to, "Satır\nbir\r\n  iki\x00", "a\x00b\r\nc\td\x07")
        assert (cleaned.subject, cleaned.body) == ("Satır bir iki", "ab\nc\td")
    with _db() as db:
        rows = {r.id: (r.subject, r.body) for r in db.scalars(select(ManagerMessage))}
    assert rows[longest.id] == ("ş" * 80, "ğ" * 1000) and rows[cleaned.id] == ("Satır bir iki", "ab\nc\td")


def test_message_rate_limit_is_per_seat_and_slides_with_the_clock(world, clock):
    owner_id = world.seat_ids[OWNER]
    with _as(world, MEMBER) as box:
        for i in range(messaging.RATE_LIMIT_PER_HOUR):
            clock.advance(seconds=10)
            box.send(owner_id, f"Mesaj {i}", "spam değil")
        first_at = T0 + timedelta(seconds=10)
        with pytest.raises(MessageError, match=r"Saatte en fazla 30 mesaj.*\d+ dakika sonra"):
            box.send(owner_id, "Bir tane daha", "metin")
        box.delete(box.outbox()[0].id)                          # silmek butceyi geri vermez
        with pytest.raises(MessageError, match="Saatte en fazla 30 mesaj"):
            box.send(owner_id, "Bir tane daha", "metin")
        assert box.post("Panoya yazmak ayrı bütçe.").kind == "POST"
    with _as(world, OWNER) as box:                               # sinir koltuk basina
        box.send(world.seat_ids[MEMBER], "Yanıt", "Sakin ol.")

    clock.set(first_at + timedelta(minutes=59))
    with _as(world, MEMBER) as box:
        with pytest.raises(MessageError, match="1 dakika sonra"):
            box.send(owner_id, "Hâlâ erken", "metin")
    clock.set(first_at + timedelta(hours=1, seconds=1))          # ilk mesaj pencereden cikti
    with _as(world, MEMBER) as box:
        box.send(owner_id, "Yeni saat", "metin")
        with pytest.raises(MessageError, match="Saatte en fazla 30 mesaj"):
            box.send(owner_id, "Yine erken", "metin")
    with _db() as db:
        count = db.scalar(select(func.count()).select_from(ManagerMessage)
                          .where(ManagerMessage.sender_manager_id == world.seat_ids[MEMBER]))
    assert count == messaging.RATE_LIMIT_PER_HOUR + 1


def test_concurrent_sends_from_one_seat_never_exceed_the_rate_limit(world, monkeypatch):
    """
    Her gonderim ayri islem (web callback'i gibi); ayni koltugun paralel istekleri siniri asamaz, kilitlenmez.
    Sayim ile commit arasi yavaslatilir; uc is parcacigi + onceden bir mesaj: kilitsiz surum 30'u asar.
    """
    owner_id, member_id = world.seat_ids[OWNER], world.seat_ids[MEMBER]
    with _as(world, MEMBER) as box:
        box.send(owner_id, "İlk", "metin")
    check_rate = Messaging._check_rate

    def slow_check_rate(self, *args, **kwargs):                 # sayim ile commit arasindaki yaris penceresi
        check_rate(self, *args, **kwargs)
        time.sleep(0.02)

    monkeypatch.setattr(Messaging, "_check_rate", slow_check_rate)
    workers, attempts = 3, 12                                   # 36 deneme, 29'u gecer
    barrier = threading.Barrier(workers + 1)
    sent, refused, errors = [], [], []

    def worker(tag: int) -> None:
        barrier.wait(10)
        for i in range(attempts):
            try:
                with _as(world, MEMBER) as box:
                    sent.append(box.send(owner_id, f"Paralel {tag}-{i}", "metin").id)
            except MessageError:
                refused.append(tag)
            except Exception as exc:                            # kilitlenme / beklenmeyen hata
                errors.append(exc)

    def owner_side() -> None:                                   # karsi yon: alicinin koltugu da mesaj yazar
        barrier.wait(10)
        for i in range(10):
            try:
                with _as(world, OWNER) as box:
                    box.send(member_id, f"Karşı {i}", "metin")
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(workers)]
    threads.append(threading.Thread(target=owner_side))
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [] and not any(t.is_alive() for t in threads)
    with _db() as db:
        counts = dict(db.execute(select(ManagerMessage.sender_manager_id, func.count())
                                 .group_by(ManagerMessage.sender_manager_id)).all())
    assert counts == {member_id: messaging.RATE_LIMIT_PER_HOUR, owner_id: 10}
    assert len(sent) == messaging.RATE_LIMIT_PER_HOUR - 1 and len(refused) == workers * attempts - len(sent)


# ===========================================================================
# Dunya panosu
# ===========================================================================

def test_board_orders_pinned_first_then_newest(world, clock):
    with _as(world, MEMBER) as box:
        oldest = box.post("İlk gönderi")
        clock.advance(minutes=1)
        middle = box.post("İkinci gönderi")
    clock.advance(minutes=1)
    with _as(world, OWNER) as box:
        newest = box.post("Sahibin gönderisi")
    clock.advance(minutes=1)
    with _db() as db:
        system_post(db, "  Sezon 1 başladı!  ")
        system_post(db, "d" * 600)                               # sistem metni kisaltilir
        with pytest.raises(MessageError, match="Gönderi boş"):
            system_post(db, " \n ")

    with _as(world, OWNER, admin=True) as admin:
        pinned = admin.pin_post(oldest.id)
        assert pinned.pinned and pinned.author_name == MEMBER
        board = admin.board()
        assert all(p.can_delete for p in board)                 # yonetici her gonderiyi silebilir
    with _as(world, MEMBER) as box:
        board = box.board()
        bodies = [p.body for p in board]
        assert bodies[0] == "İlk gönderi" and board[0].pinned
        assert bodies[1] == "d" * 499 + "…" and bodies[2] == "Sezon 1 başladı!"
        assert bodies[3:] == ["Sahibin gönderisi", "İkinci gönderi"]
        system = board[2]
        assert (system.author_name, system.kind, system.can_delete) == (None, "SYSTEM", False)
        by_id = {p.id: p for p in board}
        assert by_id[middle.id].can_delete and by_id[oldest.id].can_delete
        assert not by_id[newest.id].can_delete and by_id[newest.id].author_name == OWNER
        assert all(_is_utc(p.created_at) for p in board)
        assert [p.body for p in box.board(limit=2)] == bodies[:2]
        with pytest.raises(MessageError, match="yalnızca dünya yöneticisi"):
            box.pin_post(middle.id)

        with pytest.raises(MessageError, match="Gönderi boş"):
            box.post("   ")
        with pytest.raises(MessageError, match="en fazla 500"):
            box.post("x" * 501)
        assert len(box.post("x" * 500).body) == 500


def test_post_delete_permissions_include_the_admin_and_the_post_rate_limit(world, clock):
    with _as(world, OWNER) as box:
        owner_post = box.post("Sahibin gönderisi")
    with _as(world, MEMBER) as box:
        member_post = box.post("Üyenin gönderisi")
        other_post = box.post("Üyenin ikinci gönderisi")
    with _db() as db:
        system_post(db, "Sistem duyurusu", pinned=True)
        system_id = db.scalar(select(WorldPost.id).where(WorldPost.kind == "SYSTEM"))

    with _as(world, MEMBER) as box:
        for foreign in (owner_post.id, system_id):
            with pytest.raises(MessageError, match="yalnızca yazarı ya da dünya yöneticisi"):
                box.delete_post(foreign)
        box.delete_post(member_post.id)                          # yazar kendi gonderisini siler
        with pytest.raises(MessageError, match="Gönderi bulunamadı"):
            box.delete_post(member_post.id)
        with pytest.raises(MessageError, match="Gönderi bulunamadı"):
            box.delete_post(999_999)
    with _as(world, THIRD, admin=True) as admin:                 # yonetici (sahip olmayan) herkesinkini siler
        admin.delete_post(other_post.id)
        admin.delete_post(system_id)
        with pytest.raises(MessageError, match="Gönderi bulunamadı"):
            admin.pin_post(system_id)
        assert [p.id for p in admin.board()] == [owner_post.id]
    with _db() as db:                                            # silinen gonderi saklanir, panoda gorunmez
        kinds = dict(db.execute(select(WorldPost.id, WorldPost.kind)).all())
    assert kinds[member_post.id] == kinds[other_post.id] == kinds[system_id] == "DELETED"

    with _as(world, SPARE) as box:                               # gonderi hiz siniri (silinenler de sayilir)
        posts = [box.post(f"Gönderi {i}") for i in range(messaging.RATE_LIMIT_PER_HOUR)]
        box.delete_post(posts[0].id)
        with pytest.raises(MessageError, match="Saatte en fazla 30 gönderi"):
            box.post("Bir tane daha")
        box.send(world.seat_ids[OWNER], "Mesaj", "Mesaj bütçesi ayrı.")
    clock.advance(hours=1, seconds=1)
    with _as(world, SPARE) as box:
        box.post("Saat doldu")


# ===========================================================================
# Bildirimler
# ===========================================================================

def test_notifications_unread_counts_and_mark_read(world, clock):
    member_id, owner_id = world.seat_ids[MEMBER], world.seat_ids[OWNER]
    with _db() as db:
        notify(db, member_id, NotificationKind.OFFER_IN, "Forvetine teklif geldi.", "OFFER", 7)
        clock.advance(minutes=1)
        notify(db, member_id, "TURN", "Sezon 1, 1. hafta oynandı.")
        clock.advance(minutes=1)
        notify(db, member_id, NotificationKind.REVIEW, "u" * 400)
        notify(db, owner_id, NotificationKind.TURN, "Sahibin bildirimi")
        owner_note = db.scalar(select(Notification.id).where(Notification.manager_id == owner_id))
        for bad_kind in ("NOPE", None, 3):
            with pytest.raises(MessageError, match="Geçersiz bildirim türü"):
                notify(db, member_id, bad_kind, "metin")
        for bad_seat in (999_999, None, True):
            with pytest.raises(MessageError, match="Menajer koltuğu bulunamadı"):
                notify(db, bad_seat, NotificationKind.TURN, "metin")
        with pytest.raises(MessageError, match="boş olamaz"):
            notify(db, member_id, NotificationKind.TURN, "   ")
        with pytest.raises(MessageError, match="bağlantısı"):
            notify(db, member_id, NotificationKind.TURN, "metin", "X" * 17, 1)
        with pytest.raises(MessageError, match="bağlantısı"):
            notify(db, member_id, NotificationKind.TURN, "metin", "OFFER", "7")
    with _as(world, OWNER) as box:
        box.send(member_id, "Selam", "Okunmamış mesaj da sayılır.")

    with _as(world, MEMBER) as box:
        items = box.notifications()
        assert [n.kind for n in items] == ["REVIEW", "TURN", "OFFER_IN"]          # en yeni ustte
        review, turn, offer = items
        assert len(review.text) == 300 and review.text.endswith("…")
        assert (offer.ref_type, offer.ref_id, offer.read) == ("OFFER", 7, False)
        assert (turn.ref_type, turn.ref_id) == (None, None)
        assert all(_is_utc(n.created_at) for n in items) and offer.created_at == T0
        assert box.unread_notifications() == 3 and box.unread_messages() == 1 and box.unread_total() == 4

        assert box.mark_notifications_read([offer.id]) == 1
        assert box.mark_notifications_read([offer.id]) == 0                       # zaten okundu
        assert box.mark_notifications_read([owner_note, "x", None]) == 0          # baskasinin bildirimi
        assert box.mark_notifications_read([]) == 0
        assert [n.id for n in box.notifications(unread_only=True)] == [review.id, turn.id]
        assert box.notifications()[2].read and box.unread_total() == 3
        assert len(box.notifications(limit=1)) == 1
        assert box.mark_notifications_read() == 2
        assert box.unread_notifications() == 0 and box.unread_total() == 1         # mesaj hala okunmadi
        assert box.notifications(unread_only=True) == []
    with _as(world, OWNER) as box:
        assert box.unread_notifications() == 1
    assert messaging.kind_label("OFFER_IN") == "Gelen teklif" and messaging.kind_label("TURN") == "Hafta"


def test_script_bodies_are_stored_raw_and_views_are_plain_strings(world):
    script = '<script>alert("xss")</script> & <b>kalın</b>'
    subject = "<img src=x onerror=alert(1)>"
    with _as(world, OWNER) as box:
        sent = box.send(world.seat_ids[MEMBER], subject, script)
        post = box.post(script)
    with _db() as db:
        notify(db, world.seat_ids[MEMBER], NotificationKind.MESSAGE, script)
        raw = db.get(ManagerMessage, sent.id)
        assert (raw.subject, raw.body) == (subject, script)                         # kacis yok: oldugu gibi
        assert db.get(WorldPost, post.id).body == script
    with _as(world, MEMBER) as box:
        [message] = box.inbox()
        [board_post] = box.board()
        [note] = box.notifications()
    for value, expected in ((message.subject, subject), (message.body, script), (board_post.body, script),
                            (note.text, script), (message.sender_name, OWNER)):
        assert type(value) is str and value == expected                           # isaretli / HTML nesnesi degil
    assert "<script>" not in html.escape(message.body) and "&lt;script&gt;" in html.escape(board_post.body)


# ===========================================================================
# Tur ilerlemesi entegrasyonu (A3 world_manager)
# ===========================================================================

def test_notify_matches_the_advance_call_shape_and_its_savepoint(world):
    import world_manager as wm

    member_id, owner_id = world.seat_ids[MEMBER], world.seat_ids[OWNER]
    with _db() as db:
        system_post(db, "Dış işlem yazısı")
        wm._notify(db, [(member_id, "TURN", "iyi"), (999_999, "TURN", "koltuk yok")])   # savepoint geri alinir
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
        wm._notify(db, [(member_id, "TURN", "Hafta oynandı."), (owner_id, "RELEASED", "r" * 500)])
    with _db() as db:
        rows = {n.manager_id: (n.kind, n.text) for n in db.scalars(select(Notification))}
        assert rows[member_id] == ("TURN", "Hafta oynandı.")
        assert rows[owner_id][0] == "RELEASED" and len(rows[owner_id][1]) == 300
        assert db.scalar(select(WorldPost.body)) == "Dış işlem yazısı"             # dis islem bozulmadi


def test_forced_turn_advance_notifies_every_active_seat(world):
    import world_manager as wm
    from turn_rules import AdvanceTrigger
    from worlds import WorldContext

    _set_status(world, SPARE, "LEFT")
    ctx = WorldContext(world_id=world.world_id, schema=SCHEMA, name="Test Dünyası", kind="SHARED", role="OWNER",
                       user_id=world.user_ids[OWNER], world_seed=None)
    with _db() as db:
        season, week = db.execute(text("SELECT season, current_week FROM game_state WHERE id = 1")).one()
    result = wm.try_advance(ctx, AdvanceTrigger.FORCED, expected=(season, week))
    assert result.advanced and result.kind == "WEEK"

    with _db() as db:
        rows = db.execute(select(Notification.manager_id, Notification.kind, Notification.text)).all()
    # 15A: ayni turda sozlesmesi biten oyuncu bildirimi de yazilabilir; burada olculen TUR bildirimidir
    turns = [(seat_id, kind, note) for seat_id, kind, note in rows if kind == NotificationKind.TURN.value]
    by_seat = {seat_id: (kind, note) for seat_id, kind, note in turns}
    expected_seats = {world.seat_ids[n] for n in (OWNER, MEMBER, THIRD)}          # ACTIVE (kulupsuz dahil)
    assert set(by_seat) == expected_seats and len(turns) == len(expected_seats)
    assert all(value == ("TURN", result.message) for value in by_seat.values())
    for name in (OWNER, MEMBER, THIRD):
        with _as(world, name) as box:
            turn_notes = [n for n in box.notifications(unread_only=True) if n.kind == NotificationKind.TURN.value]
            assert len(turn_notes) == 1 and box.unread_total() >= 1
    with _as(world, SPARE) as box:
        assert box.notifications() == []
