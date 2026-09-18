"""
web_common.py
=============
Web arayuzunun ortak yardimcilari (Faz 12 / 14. Asama). web_app.py ve gorunum modulleri (*_view.py) buradan
alir; bu modul web_app'i ya da gorunum modullerini import ETMEZ (dongu olmasin).

    flash / show_flash       -> callback'ten sekmeye mesaj (bir sonraki cizimde gosterilip silinir)
    reset_widgets / money / live_fixture_pending / parse_seed / career_seed / md_escape
    manager(db)              -> oturumdaki kariyerin CareerManager'i; paylasilan dunyada menajerin koltugu
                                (manager_user_id = oturumdaki kullanici, tohum = dunyanin tohumu)
    requires_auth            -> yalnizca oturum ister (dunyaya bagli olmayan callback'ler, orn. lobi)
    member_callback          -> oturum + (oturum bir dunyaya bagliysa) ACTIVE uyelik + paylasilan dunyada SHARED
                                dunya kilidi (once koltugun son etkinligi yazilir). Uyelik dusmusse callback
                                calismaz, oturum kisisel kariyere (worlds.default_world) ya da lobiye yonlenir;
                                kilit beklemesi asilirsa (55P03) ya da dunya mesgulse Turkce uyari gosterilir.
    admin_callback           -> member_callback + dunyada OWNER / ADMIN rolu
    Uc dekorator de .requires_auth = True isaretler (tests/test_web_auth.py, tests/test_world_schema.py).

Dunya baglami:
    current_world()          -> sayfa cizimi: uyelik + kayit (ad, tur, tohum) yeniden okunur; dusmusse yeniden
                                baglama / lobi. Sonuc session_state[WORLD_CTX_KEY]'e yazilir (page_world()).
                                Bu kopya YALNIZCA cizim icindir; yetki icin kullanilmaz.
    callback_world()         -> callback icinde taze WorldContext (uyelik dekoratorun o anki denetiminden).
    bind_world(ctx)          -> oturum dunyaya baglanir (worlds.session_for); onceki dunyanin ekran durumu atilir.

Eski (dunyaya bagli olmayan) oturumlar -- testlerin AuthSession(0, 'test_menajer', 'public') oturumu dahil --
bugunku gibi calisir: uyelik denetimi, kayit sorgusu ve dunya kilidi YOKTUR.
Callback kurali: kulup cm.user_team'den alinir (widget id'sine guvenilmez); dunya verisini degistiren kod kendi
career_context'ini ACMAZ (sema cozucuden gelir; yeni baglamdaki islem kilit almaz, bkz. database.world_lock).
"""

from __future__ import annotations

import functools
import logging
import re
from contextvars import ContextVar
from html import escape

import streamlit as st
from sqlalchemy.exc import OperationalError, SQLAlchemyError

import database
import worlds
from career_manager import CareerManager
from extensions import rules_of
from finance import format_money
from models import World
from world_rules import WorldRules

log = logging.getLogger(__name__)

WORLD_KIND_SHARED = "SHARED"
WORLD_KIND_PERSONAL = "PERSONAL"
LOBBY_KEY = "world_lobby"                  # True: sayfa dunya yerine lobiyi cizer (world_lobby_view)
WORLD_CTX_KEY = "world_ctx"                # son cizimin WorldContext'i (yalnizca gosterim / tohum)
BUSY_FLASH_AREA = "sidebar"
BUSY_TEXT = "Dünya şu an haftayı oynatıyor; birkaç saniye sonra tekrar dene."
REMOVED_TEXT = "Bu dünyadan çıkarıldın."
ADMIN_ONLY_TEXT = "Bu işlem için dünyanın sahibi ya da yöneticisi olmalısın."
# Dunya degisince korunan oturum anahtarlari (digerleri: widget'lar, rapor, canli mac, sozlesme masasi atilir)
SESSION_KEEP_KEYS = frozenset({"auth", "theme", "theme_choice", "flash"})

KIND_LABELS = {WORLD_KIND_PERSONAL: "Kişisel kariyer", WORLD_KIND_SHARED: "Paylaşılan dünya"}
ROLE_LABELS = {"OWNER": "Sahip", "ADMIN": "Yönetici", "MEMBER": "Üye"}
VISIBILITY_LABELS = {"INVITE": "Davetli (kodla katılım)", "PUBLIC": "Açık (listede görünür)",
                     "PRIVATE": "Özel (katılım kapalı)"}
STRICTNESS_LABELS = {"LOW": "Düşük", "MEDIUM": "Orta", "HIGH": "Yüksek"}

# Dekoratorun bu callback icin okudugu uyelik (callback_world / callback_is_admin ikinci sorgu atmasin)
_guard_membership: ContextVar[worlds.MembershipInfo | None] = ContextVar("web_guard_membership", default=None)


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


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~>])")


def md_escape(text) -> str:
    """Kullanici / veritabani metni st.markdown ve st.caption'da duz metin kalsin (HTML ve Markdown kacisli)."""
    return _MD_SPECIAL.sub(r"\\\1", escape(str(text), quote=False))


def is_shared_world(auth) -> bool:
    """Oturum paylasilan bir dunyaya mi bagli? (AuthSession.world_kind; eski oturumda alan yok -> False)"""
    return getattr(auth, "world_kind", None) == WORLD_KIND_SHARED


def manager(db) -> CareerManager:
    auth = st.session_state.get("auth")
    if is_shared_world(auth):
        # Faz 12: menajerin koltugu (birincil koltuk = dunya sahibi); tohum dunyanin kaydindan (kariyer tohumu yok)
        ctx = page_world()
        return CareerManager(db, seed=ctx.world_seed if ctx is not None else None, manager_user_id=auth.user_id)
    return CareerManager(db, seed=career_seed())


PIN_KEY = "ofm_pinned"


def pin_state(db, cm: CareerManager) -> CareerManager:
    """
    Faz 13I (cizim hizi): YALNIZCA sayfa cizimi icin. SQLAlchemy kimlik haritasi zayif referans tutar; cm.state
    (GameState) her erisimde yeniden SELECT ediliyordu (bir cizimde ~40 kez). Oturumun info sozlugunde guclu referans
    tutulur: ayni oturumda tek sorgu. Callback'ler (hafta oynatma, transfer) bunu KULLANMAZ: oyun islemlerinin nesne
    yasam dongusu eskisiyle birebir kalir.
    """
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info.setdefault(PIN_KEY, []).append(cm.state)
    return cm


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


def world_context(auth, membership: worlds.MembershipInfo | None = None) -> worlds.WorldContext:
    """
    Oturumun dunyasi: ACTIVE uyelik (verilmediyse worlds.check_membership) + kayit (ad, tur, tohum). Kayit yoksa ya da
    semasi oturumunkiyle eslesmiyorsa NotAMemberError (fail-closed). Hesap sorgusu 'public'e sabit baglamda, dunya
    kilidi almaz.
    """
    if membership is None:
        membership = worlds.check_membership(auth.user_id, auth.world_id)
    with database.career_context(database.LEGACY_CAREER_SCHEMA), database.session_scope() as db:
        world = db.get(World, auth.world_id)
        meta = None if world is None else (world.id, world.schema_name, world.name, world.kind, world.world_seed)
    if meta is None or meta[1] != auth.career_schema:
        raise worlds.NotAMemberError(REMOVED_TEXT)
    return worlds.WorldContext(world_id=meta[0], schema=meta[1], name=meta[2], kind=meta[3], role=membership.role,
                               user_id=auth.user_id, world_seed=meta[4])


def page_world() -> worlds.WorldContext | None:
    """Son cizimin dunya baglami (yalnizca oturumun su anki dunyasiyla eslesiyorsa). Yetki icin KULLANILMAZ."""
    auth = st.session_state.get("auth")
    ctx = st.session_state.get(WORLD_CTX_KEY)
    if auth is None or ctx is None or getattr(auth, "world_id", None) != ctx.world_id or ctx.user_id != auth.user_id:
        return None
    return ctx


def shared_page_world() -> worlds.WorldContext | None:
    """Cizilen dunya paylasilan dunyaysa baglami (sekmelerin paylasilan dunya dallari icin)."""
    ctx = page_world()
    return ctx if ctx is not None and ctx.kind == WORLD_KIND_SHARED else None


def page_is_admin() -> bool:
    """Cizim: kura / yonetim dugmeleri gorunsun mu? Eski oturum ve kisisel kariyer: kariyerin sahibi (True)."""
    ctx = shared_page_world()
    return ctx is None or ctx.role in worlds.ADMIN_ROLES


def show_lobby() -> bool:
    return bool(st.session_state.get(LOBBY_KEY))


def bind_world(ctx: worlds.WorldContext, *, lobby: bool = False) -> None:
    """
    Oturum dunyaya baglanir (worlds.session_for: yalnizca kendi baglami). Onceki dunyanin ekran durumu (widget'lar,
    hafta raporu, canli mac, sozlesme masasi, career_ready) atilir; tema ve bekleyen mesajlar kalir.
    """
    auth = st.session_state.get("auth")
    bound = worlds.session_for(auth, ctx)
    for key in list(st.session_state.keys()):
        if key not in SESSION_KEEP_KEYS:
            del st.session_state[key]
    st.session_state["auth"] = bound
    if lobby:
        st.session_state[LOBBY_KEY] = True


def unbind_world(message: str = REMOVED_TEXT) -> None:
    """
    Uyeligi dusen oturum: once hesabin varsayilan dunyasina (son girilen aktif uyelik, yoksa kisisel kariyer;
    worlds.default_world) baglanir; o da yoksa lobiye yonlenir ve dunya ekran durumu atilir. Oturum baska bir
    kariyere ASLA dunyasiz (eski oturum) olarak dusurulmez: member_callback her cagride uyeligi yeniden denetler.
    """
    auth = st.session_state.get("auth")
    target = None
    if auth is not None:
        try:
            candidate = worlds.default_world(auth)
            if candidate.world_id != getattr(auth, "world_id", None):
                target = candidate
        except (worlds.WorldError, SQLAlchemyError, ValueError):
            target = None
    if target is not None:
        bind_world(target)
        flash("sidebar", "error", f"{message} «{md_escape(target.name)}» yüklendi.")
        return
    st.session_state[LOBBY_KEY] = True
    for key in ("career_ready", "live", "neg", WORLD_CTX_KEY):
        st.session_state.pop(key, None)
    flash("lobby", "error", message)


def current_world() -> worlds.WorldContext | None:
    """
    Sayfa cizimi (callback degil): dunyaya bagli oturumun uyeligi ve kaydi yeniden okunur. Eski oturum -> None
    (sorgu yok). Uyelik dustuyse unbind_world (varsayilan dunya ya da lobi). Kayittaki tur degistiyse (kisisel kariyer
    paylasilan dunyaya cevrildi) oturum guncellenir. Sonuc WORLD_CTX_KEY'e yazilir.
    """
    for _attempt in range(2):
        auth = st.session_state.get("auth")
        if auth is None or getattr(auth, "world_id", None) is None:
            st.session_state.pop(WORLD_CTX_KEY, None)
            return None
        try:
            ctx = world_context(auth)
        except worlds.WorldError:
            unbind_world(REMOVED_TEXT)
            if show_lobby():
                return None
            continue                                  # yeni dunya: bir kez daha denetlenir
        if ctx.kind != getattr(auth, "world_kind", None):
            st.session_state["auth"] = worlds.session_for(auth, ctx)
        st.session_state[WORLD_CTX_KEY] = ctx
        return ctx
    st.session_state[LOBBY_KEY] = True
    return None


def callback_world() -> worlds.WorldContext | None:
    """Callback icinde oturumun dunya baglami (uyelik dekoratorun denetiminden; kayit taze okunur). Eski oturum: None."""
    auth = st.session_state.get("auth")
    if auth is None or getattr(auth, "world_id", None) is None:
        return None
    membership = _guard_membership.get()
    if membership is not None and (membership.world_id, membership.user_id) != (auth.world_id, auth.user_id):
        membership = None
    return world_context(auth, membership)


def callback_is_admin() -> bool:
    """Callback: paylasilan dunyada OWNER / ADMIN mi? (eski oturum ve kisisel kariyer: kariyerin sahibi, True)"""
    auth = st.session_state.get("auth")
    if not is_shared_world(auth):
        return True
    membership = _guard_membership.get()
    if membership is None or (membership.world_id, membership.user_id) != (auth.world_id, auth.user_id):
        try:
            membership = world_membership(auth)
        except worlds.WorldError:
            return False
    return membership is not None and membership.role in worlds.ADMIN_ROLES


# ===========================================================================
# DEKORATORLER
# ===========================================================================

def is_lock_timeout(exc: BaseException) -> bool:
    return getattr(getattr(exc, "orig", None), "pgcode", None) == database.LOCK_TIMEOUT_PGCODE


def _touch_seat(auth, membership: worlds.MembershipInfo | None) -> None:
    """
    Paylasilan dunya callback'i = menajer etkinligi (kacirilan tur sayaci icin world_managers.last_active_at).
    Callback'ten ONCE, ayni SHARED kilit baglaminda kendi kisa isleminde yazilir (hafta ilerletmeyle cakismaz).
    Kilit beklemesi asilirsa hata yukari cikar (mesgul uyarisi, callback calismaz); diger hatalar callback'i
    engellemez.
    """
    import world_manager

    ctx = worlds.WorldContext(world_id=auth.world_id, schema=auth.career_schema, name="", kind=auth.world_kind,
                              role=membership.role if membership is not None else "MEMBER",
                              user_id=auth.user_id, world_seed=None)
    try:
        with database.session_scope() as db:
            world_manager.WorldController(db, ctx).touch()
    except OperationalError as exc:
        if is_lock_timeout(exc):
            raise
        log.warning("Menajer etkinliği yazılamadı: %s", exc)
    except (SQLAlchemyError, ValueError) as exc:
        log.warning("Menajer etkinliği yazılamadı: %s", exc)


def _run_in_world(auth, membership, callback, args, kwargs):
    if not is_shared_world(auth):
        return callback(*args, **kwargs)
    try:
        with database.world_lock(auth.career_schema, "shared"):
            _touch_seat(auth, membership)
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
        token = _guard_membership.set(membership)
        try:
            return _run_in_world(auth, membership, callback, args, kwargs)
        finally:
            _guard_membership.reset(token)

    guarded.requires_auth = True
    return guarded


def member_callback(callback):
    """Dunya callback'i: oturum + uyelik (dunyaya bagliysa) + paylasilan dunyada SHARED kilit."""
    return _world_guard(callback, admin=False)


def admin_callback(callback):
    """Yonetici callback'i: member_callback + OWNER / ADMIN rolu (eski oturumda kariyer sahibi sayilir)."""
    return _world_guard(callback, admin=True)
