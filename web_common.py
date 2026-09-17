"""
web_common.py
=============
Web arayuzunun ortak yardimcilari (Faz 12 / 14. Asama). web_app.py ve gorunum modulleri (*_view.py) buradan
alir; bu modul web_app'i ya da gorunum modullerini import ETMEZ (dongu olmasin).

    flash / show_flash       -> callback'ten sekmeye mesaj (bir sonraki cizimde gosterilip silinir)
    reset_widgets / money / live_fixture_pending / parse_seed / career_seed
    manager(db)              -> oturumdaki kariyerin CareerManager'i; paylasilan dunyada menajerin koltugu
                                (manager_user_id = oturumdaki kullanici)
    requires_auth            -> yalnizca oturum ister (dunyaya bagli olmayan callback'ler, orn. lobi)
    member_callback          -> oturum + (oturum bir dunyaya bagliysa) ACTIVE uyelik + paylasilan dunyada SHARED
                                dunya kilidi. Uyelik dusmusse callback calismaz, oturum lobiye yonlenir; kilit
                                beklemesi asilirsa (55P03) ya da dunya mesgulse Turkce uyari gosterilir.
    admin_callback           -> member_callback + dunyada OWNER / ADMIN rolu
    Uc dekorator de .requires_auth = True isaretler (tests/test_web_auth.py, tests/test_world_schema.py).

Eski (dunyaya bagli olmayan) oturumlar -- testlerin AuthSession(0, 'test_menajer', 'public') oturumu dahil --
bugunku gibi calisir: uyelik denetimi ve dunya kilidi YOKTUR.
Callback kurali: kulup cm.user_team'den alinir (widget id'sine guvenilmez); dunya verisini degistiren kod kendi
career_context'ini ACMAZ (sema cozucuden gelir; yeni baglamdaki islem kilit almaz, bkz. database.world_lock).
"""

from __future__ import annotations

import functools

import streamlit as st
from sqlalchemy.exc import OperationalError

import database
import worlds
from career_manager import CareerManager
from extensions import rules_of
from finance import format_money
from world_rules import WorldRules

WORLD_KIND_SHARED = "SHARED"
LOBBY_KEY = "world_lobby"                  # True: sayfa dunya yerine lobiyi cizer (world_lobby_view)
BUSY_FLASH_AREA = "sidebar"
BUSY_TEXT = "Dünya şu an haftayı oynatıyor; birkaç saniye sonra tekrar dene."
REMOVED_TEXT = "Bu dünyadan çıkarıldın."
ADMIN_ONLY_TEXT = "Bu işlem için dünyanın sahibi ya da yöneticisi olmalısın."


# ===========================================================================
# KUCUK YARDIMCILAR (web_app'tan tasindi; web_app ayni adlarla yeniden disari acar)
# ===========================================================================

def parse_seed(raw) -> int | None:
    """Kullanicinin yazdigi tohum; gecersizse (bos, '--5', '²') rastgele."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def career_seed() -> int | None:
    return parse_seed(st.session_state.get("career_seed", ""))


def is_shared_world(auth) -> bool:
    """Oturum paylasilan bir dunyaya mi bagli? (AuthSession.world_kind; eski oturumda alan yok -> False)"""
    return getattr(auth, "world_kind", None) == WORLD_KIND_SHARED


def manager(db) -> CareerManager:
    auth = st.session_state.get("auth")
    if is_shared_world(auth):
        # Faz 12 A2: menajerin koltugu (birincil koltuk = dunya sahibi, eski davranis)
        return CareerManager(db, seed=career_seed(), manager_user_id=auth.user_id)
    return CareerManager(db, seed=career_seed())


def flash(area: str, kind: str, text: str) -> None:
    """Callback'ten sekmeye mesaj tasir (bir sonraki cizimde gosterilip silinir)."""
    st.session_state.setdefault("flash", {}).setdefault(area, []).append((kind, text))


def show_flash(area: str) -> None:
    for kind, text in st.session_state.get("flash", {}).pop(area, []):
        {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(text)


def money(amount: float) -> str:
    """Metrik kutulari icin kisa para: '45.0M' (birim etikette). Dar sutunlarda kesilmez."""
    return format_money(amount).replace(" EUR", "")


def reset_widgets(*keys: str) -> None:
    for key in keys:
        st.session_state.pop(key, None)


def live_fixture_pending() -> bool:
    """Kaydedilmemis kariyer canli maci var mi? (kadro / heyet degisikligi maca sizmasin)"""
    live = st.session_state.get("live")
    return live is not None and live.is_fixture and not live.saved


# ===========================================================================
# DUNYA OTURUMU (Faz 12)
# ===========================================================================

def world_rules_for(cm) -> WorldRules:
    """Kariyerin dunya kurallari (cm.rules ya da GameState.world_rules; bos = eski kurallar)."""
    return rules_of(cm)


def world_membership(auth) -> worlds.MembershipInfo | None:
    """Dunyaya bagli oturumun ACTIVE uyeligi; oturum bir dunyaya bagli degilse (eski oturum) None, sorgu yok."""
    world_id = getattr(auth, "world_id", None)
    if auth is None or world_id is None:
        return None
    return worlds.check_membership(auth.user_id, world_id)


def world_role(auth) -> str | None:
    """OWNER / ADMIN / MEMBER; dunyaya bagli degilse ya da uyelik dusmusse None."""
    try:
        membership = world_membership(auth)
    except worlds.WorldError:
        return None
    return membership.role if membership is not None else None


def show_lobby() -> bool:
    return bool(st.session_state.get(LOBBY_KEY))


def unbind_world(message: str = REMOVED_TEXT) -> None:
    """
    Uyeligi dusen oturum lobiye yonlenir; dunya ekran durumu (canli mac, sozlesme masasi) atilir.
    Oturum kimligi degismez: member_callback her cagride uyeligi yeniden denetler ve reddeder (fail-closed).
    """
    st.session_state[LOBBY_KEY] = True
    for key in ("career_ready", "live", "neg"):
        st.session_state.pop(key, None)
    flash("lobby", "error", message)


def is_lock_timeout(exc: BaseException) -> bool:
    return getattr(getattr(exc, "orig", None), "pgcode", None) == database.LOCK_TIMEOUT_PGCODE


def _run_in_world(auth, callback, args, kwargs):
    if not is_shared_world(auth):
        return callback(*args, **kwargs)
    try:
        with database.world_lock(auth.career_schema, "shared"):
            return callback(*args, **kwargs)
    except database.WorldBusyError:
        flash(BUSY_FLASH_AREA, "warning", BUSY_TEXT)
    except OperationalError as exc:
        if not is_lock_timeout(exc):
            raise
        flash(BUSY_FLASH_AREA, "warning", BUSY_TEXT)
    return None


def requires_auth(callback):
    """Callback yalnizca oturum varken calisir (oturum yoksa sessizce hicbir sey yapmaz)."""
    @functools.wraps(callback)
    def guarded(*args, **kwargs):
        if st.session_state.get("auth") is None:
            return None
        return callback(*args, **kwargs)

    guarded.requires_auth = True
    return guarded


def _world_guard(callback, *, admin: bool):
    @functools.wraps(callback)
    def guarded(*args, **kwargs):
        auth = st.session_state.get("auth")
        if auth is None:
            return None
        try:
            membership = world_membership(auth)
        except worlds.WorldError:
            unbind_world(REMOVED_TEXT)
            return None
        if admin and membership is not None and membership.role not in worlds.ADMIN_ROLES:
            flash("admin", "error", ADMIN_ONLY_TEXT)
            return None
        return _run_in_world(auth, callback, args, kwargs)

    guarded.requires_auth = True
    return guarded


def member_callback(callback):
    """Dunya callback'i: oturum + uyelik (dunyaya bagliysa) + paylasilan dunyada SHARED kilit."""
    return _world_guard(callback, admin=False)


def admin_callback(callback):
    """Yonetici callback'i: member_callback + OWNER / ADMIN rolu (eski oturumda kariyer sahibi sayilir)."""
    return _world_guard(callback, admin=True)
