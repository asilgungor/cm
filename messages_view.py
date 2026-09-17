"""
messages_view.py
================
Mesajlar, dunya panosu ve bildirimler (Faz 12 / 14. Asama, 12B; Teklifler & Mesajlar sekmesinin bolumleri, flash
alani: hub). Kurallar messaging.Messaging'de (uyelik, uzunluk, saatlik hiz siniri, silme yetkisi).

    messages_section(db, cm)       -> yaz: msg_to (Messaging.recipients), msg_subject, msg_body, msg_send
                                      (teklif hakkinda: session_state msg_offer_id, msg_offer_clear);
                                      msg_box (Gelen / Giden), msg_open_{id} (mesaj + okundu), msg_reply_{id}
                                      (alici ve konu doldurulur), msg_delete_{id}; acik mesaj: msg_close
    board_section(db, cm)          -> board_body, board_post; board_delete_{id} (yazar / yonetici),
                                      board_pin_{id} (yonetici)
    notifications_section(db, cm)  -> bildirim listesi, ntf_read_{id}, ntf_mark_all

GUVENLIK: konu, govde, gonderi, bildirim metni ve menajer adlari DUZ METIN cizilir (md_escape: HTML + Markdown
kacisi; asla unsafe_allow_html). Yonetici bilgisi callback'te dekoratorun uyelik rolunden (callback_is_admin),
cizimde yalnizca dugme gorunurlugu icin (page_is_admin). Mesaj / teklif yetkisi Messaging ve MarketHub'da.
Callback'ler web_common.member_callback ile ACIKCA sarilir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

import messaging
from database import session_scope
from market_hub import MarketHub
from messaging import MessageError, Messaging
from models import ManagerMessage
from transfers import TransferError
from web_common import (
    WORLD_KIND_SHARED,
    callback_is_admin,
    callback_world,
    flash,
    manager,
    md_escape,
    member_callback,
    page_is_admin,
    reset_widgets,
)

if TYPE_CHECKING:
    from datetime import datetime

    from career_manager import CareerManager
    from messaging import MessageView

BOX_IN, BOX_OUT = "📥 Gelen kutusu", "📤 Giden kutusu"
MSG_OPEN_KEY = "msg_open"
REPLY_PREFIX = "Ynt: "
NO_SEAT_TEXT = "Mesajlaşmak için bu dünyada menajer koltuğun olmalı."
NOT_SHARED_TEXT = "Mesajlar yalnızca paylaşılan dünyada kullanılır."


def when(moment: datetime | None) -> str:
    return moment.strftime("%d.%m.%Y %H:%M") if moment is not None else "—"


def body_markdown(text: str) -> str:
    """Cok satirli duz metin: kacisli, satir sonlari korunur."""
    return "  \n".join(md_escape(line) for line in str(text).split("\n"))


def _seat(cm: CareerManager):
    seat = cm.acting_seat
    return seat if seat is not None and seat.id is not None else None


# ===========================================================================
# MESAJLAR
# ===========================================================================

def messages_section(db, cm: CareerManager) -> None:
    seat = _seat(cm)
    if seat is None:
        st.info(NO_SEAT_TEXT)
        return
    box = Messaging(db, seat, page_is_admin())
    ss = st.session_state
    opened = ss.get(MSG_OPEN_KEY)
    if opened is not None:
        try:
            message = box.message(int(opened))
        except MessageError:
            ss.pop(MSG_OPEN_KEY, None)
        else:
            open_message(message)

    with st.expander("✏️ Yeni mesaj", expanded=True):
        recipients = dict(box.recipients())
        if not recipients:
            st.caption("Bu dünyada mesaj gönderebileceğin başka menajer yok.")
        else:
            if ss.get("msg_to") not in recipients:
                reset_widgets("msg_to")
            st.selectbox("Alıcı", list(recipients), key="msg_to", format_func=recipients.get)
            st.text_input("Konu", key="msg_subject", max_chars=messaging.SUBJECT_MAX)
            st.text_area("Mesaj", key="msg_body", max_chars=messaging.BODY_MAX, height=120)
            offer_id = ss.get("msg_offer_id")
            if offer_id:
                c1, c2 = st.columns([3, 1])
                c1.caption(f"Bu mesaj teklif \\#{int(offer_id)} ile ilişkilendirilecek.")
                c2.button("Bağlantıyı kaldır", key="msg_offer_clear", on_click=cb_msg_offer_clear, width="stretch")
            st.button("📨 Gönder", key="msg_send", on_click=cb_msg_send, type="primary",
                      help=f"Saatte en fazla {messaging.RATE_LIMIT_PER_HOUR} mesaj.")

    which = st.radio("Kutu", [BOX_IN, BOX_OUT], key="msg_box", horizontal=True, label_visibility="collapsed")
    incoming = which == BOX_IN
    rows = box.inbox() if incoming else box.outbox()
    if not rows:
        st.caption("Gelen kutun boş." if incoming else "Henüz mesaj göndermedin.")
    for message in rows:
        message_row(message, incoming=incoming)


def message_row(message: MessageView, *, incoming: bool) -> None:
    with st.container(border=True):
        info, b1, b2, b3 = st.columns([5, 1, 1, 1])
        other = message.sender_name if incoming else message.recipient_name
        unread = incoming and not message.read
        head = ("🆕 " if unread else "") + f"**{md_escape(message.subject)}**"
        info.markdown(head)
        state = "" if incoming else (" · okundu" if message.read else " · okunmadı")
        info.caption(("Kimden: " if incoming else "Kime: ") + md_escape(other or "—") + " · " + when(message.created_at)
                     + state + (f" · teklif \\#{message.offer_id}" if message.offer_id else ""))
        b1.button("Aç", key=f"msg_open_{message.id}", on_click=cb_msg_open, args=(message.id,), width="stretch")
        if incoming:
            b2.button("Yanıtla", key=f"msg_reply_{message.id}", on_click=cb_msg_reply, args=(message.id,),
                      width="stretch")
        b3.button("Sil", key=f"msg_delete_{message.id}", on_click=cb_msg_delete, args=(message.id,), width="stretch",
                  help="Yalnızca senin kutundan siler; karşı taraf görmeye devam eder.")


def open_message(message: MessageView) -> None:
    with st.container(border=True):
        st.markdown(f"#### ✉️ {md_escape(message.subject)}")
        st.caption(f"Kimden: {md_escape(message.sender_name or '—')} · Kime: {md_escape(message.recipient_name or '—')}"
                   f" · {when(message.created_at)}" + (f" · teklif \\#{message.offer_id}" if message.offer_id else ""))
        st.markdown(body_markdown(message.body))
        c1, c2 = st.columns(2)
        if not message.mine:
            c1.button("↩️ Yanıtla", key="msg_reply_open", on_click=cb_msg_reply, args=(message.id,), width="stretch")
        c2.button("Kapat", key="msg_close", on_click=cb_msg_close, width="stretch")


# ===========================================================================
# PANO
# ===========================================================================

def board_section(db, cm: CareerManager) -> None:
    seat = _seat(cm)
    admin = page_is_admin()
    box = Messaging(db, seat, admin)
    if seat is not None:
        st.text_area("Panoya yaz", key="board_body", max_chars=messaging.POST_MAX, height=90,
                     placeholder="Dünyadaki bütün menajerler görür.")
        st.button("📌 Paylaş", key="board_post", on_click=cb_board_post, type="primary")
    else:
        st.caption(NO_SEAT_TEXT)
    posts = box.board()
    if not posts:
        st.caption("Panoda henüz gönderi yok.")
    for post in posts:
        with st.container(border=True):
            author = "📢 Sistem duyurusu" if post.author_name is None else f"👤 **{md_escape(post.author_name)}**"
            st.markdown(("📌 " if post.pinned else "") + author + f" · {when(post.created_at)}")
            st.markdown(body_markdown(post.body))
            c1, c2, _ = st.columns([1, 1, 3])
            if post.can_delete:
                c1.button("Sil", key=f"board_delete_{post.id}", on_click=cb_board_delete, args=(post.id,),
                          width="stretch")
            if admin:
                c2.button("Sabitlemeyi kaldır" if post.pinned else "Sabitle", key=f"board_pin_{post.id}",
                          on_click=cb_board_pin, args=(post.id, not post.pinned), width="stretch")


# ===========================================================================
# BILDIRIMLER
# ===========================================================================

def notifications_section(db, cm: CareerManager) -> None:
    seat = _seat(cm)
    if seat is None:
        st.info(NO_SEAT_TEXT)
        return
    box = Messaging(db, seat, page_is_admin())
    items = box.notifications(limit=50)
    unread = sum(1 for n in items if not n.read)
    st.button("🔔 Tümünü okundu say", key="ntf_mark_all", on_click=cb_notifications_read, args=(None,),
              disabled=unread == 0)
    if not items:
        st.caption("Bildirim yok.")
    for item in items:
        with st.container(border=True):
            text, action = st.columns([5, 1])
            text.markdown(("🆕 " if not item.read else "") + f"**{md_escape(messaging.kind_label(item.kind))}** · "
                          + when(item.created_at))
            text.markdown(body_markdown(item.text))
            if not item.read:
                action.button("Okundu", key=f"ntf_read_{item.id}", on_click=cb_notifications_read, args=(item.id,),
                              width="stretch")


# ===========================================================================
# CALLBACK'LER
# ===========================================================================

def _box_call(work) -> tuple[bool, object]:
    """work(box, cm, db) paylasilan dunyada tek islemde; MessageError / TransferError islem icinde yakalanir."""
    ctx = callback_world()
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        flash("hub", "error", NOT_SHARED_TEXT)
        return False, None
    error, result = None, None
    with session_scope() as db:
        cm = manager(db)
        seat = _seat(cm)
        if seat is None:
            error = NO_SEAT_TEXT
        else:
            try:
                result = work(Messaging(db, seat, callback_is_admin()), cm, db)
            except (MessageError, TransferError) as exc:
                error = str(exc)
    if error is not None:
        flash("hub", "error", md_escape(error))
        return False, None
    return True, result


@member_callback
def cb_msg_send() -> None:
    ss = st.session_state
    to, subject, body = ss.get("msg_to"), ss.get("msg_subject", ""), ss.get("msg_body", "")
    offer_id = ss.get("msg_offer_id")

    def work(box: Messaging, cm, db):
        if not isinstance(to, int):
            raise MessageError(messaging.RECIPIENT_TEXT)
        if offer_id:
            MarketHub(cm).offer(int(offer_id))                  # teklife erisim: taraf ya da yonetici
        return box.send(to, subject, body, offer_id=int(offer_id) if offer_id else None)

    ok, sent = _box_call(work)
    if ok:
        flash("hub", "success", f"📨 Mesaj gönderildi: {md_escape(sent.recipient_name)}.")
        reset_widgets("msg_subject", "msg_body", "msg_offer_id")


@member_callback
def cb_msg_offer_clear() -> None:
    reset_widgets("msg_offer_id")


@member_callback
def cb_msg_open(message_id: int) -> None:
    def work(box: Messaging, cm, db):
        message = box.message(int(message_id))
        if not message.mine:
            box.mark_read(message.id)
        return message

    ok, message = _box_call(work)
    if ok:
        st.session_state[MSG_OPEN_KEY] = message.id


@member_callback
def cb_msg_close() -> None:
    st.session_state.pop(MSG_OPEN_KEY, None)


@member_callback
def cb_msg_reply(message_id: int) -> None:
    """Gelen mesaja yanit: alici gonderen koltuk, konu 'Ynt: ...' (mesaj okundu sayilir)."""
    def work(box: Messaging, cm, db):
        message = box.message(int(message_id))                 # yetki: gorunur mesaj
        if message.mine:
            raise MessageError(messaging.SELF_TEXT)
        box.mark_read(message.id)
        sender = db.get(ManagerMessage, message.id).sender_manager_id
        if sender is None or sender not in dict(box.recipients()):
            raise MessageError(messaging.RECIPIENT_TEXT)
        return message, sender

    ok, result = _box_call(work)
    if not ok:
        return
    message, sender = result
    subject = message.subject if message.subject.startswith(REPLY_PREFIX) else REPLY_PREFIX + message.subject
    ss = st.session_state
    ss["msg_to"], ss["msg_subject"] = int(sender), subject[: messaging.SUBJECT_MAX]
    if message.offer_id:
        ss["msg_offer_id"] = message.offer_id
    ss[MSG_OPEN_KEY] = message.id


@member_callback
def cb_msg_delete(message_id: int) -> None:
    ok, _ = _box_call(lambda box, cm, db: box.delete(int(message_id)))
    if ok:
        if st.session_state.get(MSG_OPEN_KEY) == int(message_id):
            st.session_state.pop(MSG_OPEN_KEY, None)
        flash("hub", "info", "Mesaj silindi.")


@member_callback
def cb_board_post() -> None:
    body = st.session_state.get("board_body", "")
    ok, _ = _box_call(lambda box, cm, db: box.post(body))
    if ok:
        flash("hub", "success", "📌 Gönderi panoya eklendi.")
        reset_widgets("board_body")


@member_callback
def cb_board_delete(post_id: int) -> None:
    ok, _ = _box_call(lambda box, cm, db: box.delete_post(int(post_id)))
    if ok:
        flash("hub", "info", "Gönderi silindi.")


@member_callback
def cb_board_pin(post_id: int, pinned: bool) -> None:
    ok, _ = _box_call(lambda box, cm, db: box.pin_post(int(post_id), bool(pinned)))
    if ok:
        flash("hub", "info", "Gönderi sabitlendi." if pinned else "Sabitleme kaldırıldı.")


@member_callback
def cb_notifications_read(notification_id: int | None) -> None:
    ids = None if notification_id is None else [int(notification_id)]
    ok, count = _box_call(lambda box, cm, db: box.mark_notifications_read(ids))
    if ok and notification_id is None:
        flash("hub", "info", f"{count} bildirim okundu olarak işaretlendi.")
