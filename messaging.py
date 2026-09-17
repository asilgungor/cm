"""
messaging.py
============
Menajerler arasi mesajlar, dunya panosu ve bildirimler (Faz 12 / 14. Asama, 12B). Veritabani katmani; commit
ETMEZ. Metinler DUZ METINDIR: arayuz st.text ya da st.markdown(html.escape(...)) ile cizer, asla
unsafe_allow_html. Uzunluk sinirlari tablo sutunlariyla aynidir; koltuk basina saatte en fazla
RATE_LIMIT_PER_HOUR mesaj/gonderi.

Durum: iskelet (enum, veri siniflari ve sabitler tanimli); Faz 12 B3 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seats import Seat

_PACKAGE = "Faz 12: messaging (B3)"

SUBJECT_MAX = 80                  # manager_messages.subject
BODY_MAX = 1000                   # manager_messages.body
POST_MAX = 500                    # world_posts.body
NOTIFICATION_MAX = 300            # notifications.text
RATE_LIMIT_PER_HOUR = 30


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


@dataclass(frozen=True)
class MessageView:
    id: int
    sender_name: str | None
    recipient_name: str | None
    subject: str
    body: str
    offer_id: int | None
    created_at: datetime | None
    read: bool
    mine: bool


@dataclass(frozen=True)
class PostView:
    id: int
    author_name: str | None           # None: sistem duyurusu
    kind: str
    body: str
    created_at: datetime | None
    can_delete: bool


@dataclass(frozen=True)
class NotificationView:
    id: int
    kind: str
    text: str
    ref_type: str | None
    ref_id: int | None
    created_at: datetime | None
    read: bool


class Messaging:
    def __init__(self, db, seat: Seat, is_admin: bool = False) -> None:
        raise NotImplementedError(_PACKAGE)

    def send(self, to_seat_id: int, subject: str, body: str, offer_id: int | None = None) -> MessageView:
        raise NotImplementedError(_PACKAGE)

    def inbox(self, limit: int = 50) -> list[MessageView]:
        raise NotImplementedError(_PACKAGE)

    def outbox(self, limit: int = 50) -> list[MessageView]:
        raise NotImplementedError(_PACKAGE)

    def mark_read(self, message_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def delete(self, message_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def post(self, body: str) -> PostView:
        raise NotImplementedError(_PACKAGE)

    def board(self, limit: int = 50) -> list[PostView]:
        raise NotImplementedError(_PACKAGE)

    def delete_post(self, post_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def notifications(self, unread_only: bool = False, limit: int = 50) -> list[NotificationView]:
        raise NotImplementedError(_PACKAGE)

    def mark_notifications_read(self, ids: Iterable[int] | None = None) -> int:
        raise NotImplementedError(_PACKAGE)

    def unread_total(self) -> int:
        raise NotImplementedError(_PACKAGE)


def notify(
    db,
    seat_id: int,
    kind: NotificationKind,
    text: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> None:
    raise NotImplementedError(_PACKAGE)


def system_post(db, text: str) -> None:
    raise NotImplementedError(_PACKAGE)
