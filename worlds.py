"""
worlds.py
=========
Dunya kaydi (Faz 12 / 14. Asama, 12A): kisisel ve paylasilan dunyalar, uyelik, davet kodu, menajer profili.
Hesap kontrolcusu gibi calisir (accounts.py): Streamlit bilmez, "(own txn)" fonksiyonlar kendi islemlerini
acar; mark_membership cagiranin islemine katilir. Hatalar ValueError alt siniflaridir, mesajlar Turkce.

    accounts.worlds / world_memberships / manager_profiles (models.World / WorldMembership / ManagerProfile)
    Katilim: SELECT ... FROM accounts.worlds WHERE id = :id FOR UPDATE (kapasite yarisi), uyelik ve dunya
    semasindaki world_managers koltugu TEK islemde yazilir.
    Gecersiz davet kodu (yok / baska dunyanin / dondurulmus) icin TEK genel hata metni (kod tahmini sizmasin).

Durum: iskelet; Faz 12 A1 paketinde doldurulacak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounts import AuthSession
    from models import ManagerProfile
    from world_rules import WorldRules

_PACKAGE = "Faz 12: worlds (A1)"

ADMIN_ROLES = frozenset({"OWNER", "ADMIN"})


class WorldError(ValueError):
    """Dunya islemi yapilamaz (mesaj Turkce)."""


class WorldNotFound(WorldError):
    pass


class WorldPermissionError(WorldError):
    pass


class WorldFullError(WorldError):
    pass


class NotAMemberError(WorldError):
    pass


class LevelTooLowError(WorldError):
    pass


@dataclass(frozen=True)
class WorldInfo:
    id: int
    name: str
    schema: str
    kind: str
    visibility: str
    owner_user_id: int | None
    owner_name: str | None
    max_managers: int
    active_managers: int
    min_manager_level: int
    status: str
    created_at: datetime | None
    my_role: str | None
    invite_code: str | None          # yalnizca OWNER / ADMIN icin dolu


@dataclass(frozen=True)
class MembershipInfo:
    world_id: int
    user_id: int
    username: str
    role: str
    status: str
    joined_at: datetime | None
    last_seen_at: datetime | None
    team_name: str | None


@dataclass(frozen=True)
class WorldContext:
    world_id: int
    schema: str
    name: str
    kind: str
    role: str
    user_id: int
    world_seed: int | None


def ensure_personal_world(session: AuthSession) -> WorldInfo:
    raise NotImplementedError(_PACKAGE)


def default_world(session: AuthSession) -> WorldContext:
    raise NotImplementedError(_PACKAGE)


def list_my_worlds(user_id: int) -> list[WorldInfo]:
    raise NotImplementedError(_PACKAGE)


def list_public_worlds(user_id: int, query: str = "", limit: int = 50) -> list[WorldInfo]:
    raise NotImplementedError(_PACKAGE)


def create_world(
    user_id: int,
    name: str,
    *,
    visibility: str,
    max_managers: int,
    min_manager_level: int,
    rules: WorldRules,
    world_seed: int | None = None,
    source: str = "auto",
) -> WorldContext:
    raise NotImplementedError(_PACKAGE)


def join_by_code(user_id: int, code: str) -> WorldContext:
    raise NotImplementedError(_PACKAGE)


def join_public(user_id: int, world_id: int) -> WorldContext:
    raise NotImplementedError(_PACKAGE)


def enter_world(user_id: int, world_id: int) -> WorldContext:
    raise NotImplementedError(_PACKAGE)


def leave_world(user_id: int, world_id: int) -> None:
    """Sahip ayrilamaz; ayrilanin koltugu birakilir."""
    raise NotImplementedError(_PACKAGE)


def check_membership(user_id: int, world_id: int) -> MembershipInfo:
    """ACTIVE uyelik yoksa NotAMemberError."""
    raise NotImplementedError(_PACKAGE)


def members(actor_id: int, world_id: int) -> list[MembershipInfo]:
    raise NotImplementedError(_PACKAGE)


def set_role(actor_id: int, world_id: int, target_user_id: int, role: str) -> None:
    raise NotImplementedError(_PACKAGE)


def mark_membership(db, world_id: int, user_id: int, status: str) -> None:
    """Cagiranin islemine katilir (commit etmez)."""
    raise NotImplementedError(_PACKAGE)


def rotate_invite_code(actor_id: int, world_id: int) -> str:
    raise NotImplementedError(_PACKAGE)


def update_world_meta(actor_id: int, world_id: int, **fields) -> WorldInfo:
    raise NotImplementedError(_PACKAGE)


def convert_personal_to_shared(actor_id: int, world_id: int, name: str, visibility: str, max_managers: int) -> WorldInfo:
    raise NotImplementedError(_PACKAGE)


def session_for(session: AuthSession, ctx: WorldContext) -> AuthSession:
    raise NotImplementedError(_PACKAGE)


def profile(user_id: int) -> ManagerProfile:
    raise NotImplementedError(_PACKAGE)


def record_season(user_id: int, reputation: float, titles: int) -> None:
    raise NotImplementedError(_PACKAGE)
