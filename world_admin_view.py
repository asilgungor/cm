"""
world_admin_view.py
===================
Dunya yonetimi sekmesi (Faz 12 / 14. Asama, 12A; web_app.TAB_ADMIN, yalnizca OWNER / ADMIN): menajerler,
hafta, kurallar, adil oyun incelemesi, davet ve olay kaydi. Callback'ler web_common.admin_callback ile ACIKCA
sarilir; yetki her islemde ayrica WorldController / worlds tarafinda ayni islemde yeniden okunur.

    adm_section (varsayilan Hafta):
        Menajerler  -> tablo; adm_kick_reason + adm_kick_ok + adm_kick_{seat_id} (sahip atilamaz, kendini atamazsin),
                       adm_role_{user_id} (sahip: yonetici yap / uye yap; yonetici yalnizca kendini uyeye indirir)
        Hafta       -> tur durumu, adm_force (zorla oynat), adm_deadline_hours + adm_pause_auto + adm_turn_save
        Kurallar    -> adm_rule_<alan> + adm_rules_save / adm_rules_reset (oyun kurallari sezon basladiktan sonra
                       kilitli: WorldRules.editable_changes; widget'lar devre disi ve aciklamali)
        Adil oyun   -> inceleme kuyrugu: adm_review_reason_{id} + adm_review_approve_{id} / adm_review_deny_{id};
                       geri alinabilir anlasmalar (son 8 hafta): adm_reverse_reason + adm_reverse_ok (onay) +
                       adm_reverse_{offer_id}; adil oyun puanlari (market_hub.MarketHub; yonetici kendi kulubunun
                       anlasmasini inceleyemez / geri alamaz)
        Davet       -> adm_name, adm_visibility, adm_min_level + adm_meta_save; adm_invite_rotate (kod yalnizca burada
                       ve lobide sahip / yoneticiye gosterilir)
        Olaylar     -> world_events (son 50)
Flash alani: admin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

import market_view
import reputation
import world_manager
import worlds
from database import session_scope
from finance import BudgetError
from market_hub import MarketHub
from models import Fixture, FixtureStatus
from ofm_theme import panel_title_html, stat_strip_html
from transfers import TransferError
from turn_rules import AdvanceTrigger
from web_common import (
    ROLE_LABELS,
    STRICTNESS_LABELS,
    VISIBILITY_LABELS,
    WORLD_KIND_SHARED,
    admin_callback,
    callback_world,
    flash,
    manager,
    md_escape,
    reset_widgets,
    shared_page_world,
    show_flash,
)
from world_panel_view import advance_world, deadline_text, ready_text, season_text
from world_rules import FIELD_LABELS, GAMEPLAY_FIELDS, RulesError, WorldRules

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Team

SEC_MANAGERS, SEC_TURN, SEC_RULES, SEC_FAIR, SEC_INVITE, SEC_EVENTS = (
    "👥 Menajerler", "⏩ Hafta", "📜 Kurallar", "⚖️ Adil oyun", "✉️ Davet", "🗒️ Olaylar")
# Varsayilan bolum "Hafta": sekme her cizimde calisir (st.tabs), menajer listesi yalnizca secilince okunur
ADMIN_SECTIONS = [SEC_TURN, SEC_MANAGERS, SEC_RULES, SEC_FAIR, SEC_INVITE, SEC_EVENTS]
STATUS_LABELS = {"ACTIVE": "Aktif", "RELEASED": "Kulübünü kaybetti", "LEFT": "Ayrıldı", "KICKED": "Atıldı"}
EVENT_LABELS = {"ADVANCE": "Hafta", "CLAIM": "Kulüp seçimi", "RELEASE": "Kulüp bırakma", "KICK": "Atılma",
                "RULES": "Kurallar", "REVIEW": "İnceleme", "REVERSAL": "İptal", "NATIONAL": "Milli takım"}
TRIGGER_LABELS = {"READY": "herkes hazır", "FORCED": "yönetici", "DEADLINE": "süre doldu"}

# Kurallar bolumu: (alan, widget turu). Hafta suresi / otomatik ilerleme "Hafta" bolumunde; shared degismez.
RULE_WIDGETS: tuple[tuple[str, str], ...] = (
    ("max_seats", "int"),
    ("ready_check", "bool"),
    ("max_missed_deadlines", "int"),
    ("protection_weeks", "int"),
    ("club_offers_by_level", "bool"),
    ("offer_expiry_weeks", "int"),
    ("fairness_strictness", "strictness"),
    ("live_matches", "bool"),
    ("win_points", "points"),
    ("human_market", "bool"),
    ("loans", "bool"),
    ("internationals", "bool"),
    ("world_cup_every_seasons", "int"),
    ("board_confidence", "bool"),        # 15C: sonuclara gore kovulma (yonetim kurulu) -- varsayilan KAPALI
)
RULE_BOUNDS = {"max_seats": (2, 64), "max_missed_deadlines": (1, 20), "protection_weeks": (0, 52),
               "offer_expiry_weeks": (1, 8), "world_cup_every_seasons": (1, 4)}
TURN_KEYS = ("adm_deadline_hours", "adm_pause_auto")
META_KEYS = ("adm_name", "adm_visibility", "adm_min_level")
REVERSAL_WEEKS = 8
REVIEW_REASON_MAX = 300
REVIEW_OWN_TEXT = "Kendi kulübünün anlaşmasını yönetici olarak inceleyemez ya da geri alamazsın."
LOCKED_RULES_TEXT = ("🔒 Oyun kuralları (canlı maç, galibiyet puanı, pazar, kiralık, milli takımlar) sezon başladıktan "
                     "sonra kilitli; bir sonraki sezon başında (ilk maçtan önce) değiştirilebilir. Tur ve yönetim "
                     "ayarları her zaman değişir.")


def _rule_key(name: str) -> str:
    return f"adm_rule_{name}"


def season_started(db, cm: CareerManager) -> bool:
    """Bu sezon mac oynandi mi ya da 1. hafta gecildi mi (oyun kurallari kilidi; WorldController ile ayni olcut)."""
    if int(cm.current_week) > 1:
        return True
    played = db.scalar(select(func.count()).select_from(Fixture).where(
        Fixture.season == cm.season, Fixture.status == FixtureStatus.PLAYED))
    return bool(played)


# ===========================================================================
# SEKME
# ===========================================================================

def admin_tab(db, cm: CareerManager, team: Team | None) -> None:
    """Flash alani: admin."""
    st.markdown(panel_title_html("🛡️ Dünya yönetimi"), unsafe_allow_html=True)
    show_flash("admin")
    ctx = shared_page_world()
    if ctx is None:
        st.info("Dünya yönetimi yalnızca paylaşılan dünyada kullanılır.")
        return
    wc = world_manager.WorldController(db, ctx)
    if not wc.is_admin():
        st.warning("Bu bölüm yalnızca dünyanın sahibi ve yöneticileri içindir.")
        return
    section = st.radio("Bölüm", ADMIN_SECTIONS, key="adm_section", horizontal=True, label_visibility="collapsed")
    if section == SEC_MANAGERS:
        _managers_section(wc, ctx)
    elif section == SEC_TURN:
        _turn_section(wc)
    elif section == SEC_RULES:
        _rules_section(db, cm, wc)
    elif section == SEC_FAIR:
        _fair_play_section(cm, wc)
    elif section == SEC_INVITE:
        _invite_section(ctx)
    else:
        _events_section(wc)


def _managers_section(wc: world_manager.WorldController, ctx) -> None:
    rows = wc.managers()
    my_role = wc.role()
    career_week = wc.career_week
    st.dataframe(pd.DataFrame([
        {"Menajer": r.name, "Rol": ROLE_LABELS.get(r.role or "", "—"), "Kulüp": r.team_name or "—",
         "Seviye": r.level_title, "Tanınırlık": f"{r.reputation:.1f}", "Durum": STATUS_LABELS.get(r.status, r.status),
         "Hazır": "✅" if r.ready else "—", "Kaçırılan hafta": r.missed_deadlines, "Adil oyun": f"{r.fair_play:.0f}",
         "Son etkinlik": r.last_active_at.strftime("%d.%m %H:%M") if r.last_active_at else "—"}
        for r in rows
    ]), hide_index=True, width="stretch")
    st.caption(f"Kariyer haftası {career_week}. Menajer atıldığında kulübü yapay zekâya geçer ve bir süre korunur; "
               "atılan menajer bu dünyaya yeniden katılamaz.")
    others = [r for r in rows if r.user_id is not None and r.user_id != ctx.user_id and r.role != "OWNER"]
    k1, k2 = st.columns([3, 1])
    k1.text_input("Atma nedeni (isteğe bağlı)", key="adm_kick_reason", max_chars=world_manager.KICK_REASON_MAX)
    kick_ok = k2.checkbox("Atmayı onaylıyorum", key="adm_kick_ok")
    for r in rows:
        if r.user_id is None:
            continue
        with st.container(border=True):
            info, kick, role = st.columns([3, 1, 1])
            info.markdown(f"**{md_escape(r.name)}** · {ROLE_LABELS.get(r.role or '', '—')}"
                          + (f" · {md_escape(r.team_name)}" if r.team_name else " · kulüpsüz"))
            if r in others and not (r.role == "ADMIN" and my_role != "OWNER"):
                kick.button("🚫 At", key=f"adm_kick_{r.seat_id}", on_click=cb_admin_kick, args=(r.seat_id,),
                            disabled=not kick_ok, width="stretch", help=None if kick_ok else "Önce onay kutusunu işaretle.")
            if my_role == "OWNER" and r.role in ("ADMIN", "MEMBER"):
                target = "MEMBER" if r.role == "ADMIN" else "ADMIN"
                role.button("⬇️ Üye yap" if target == "MEMBER" else "⬆️ Yönetici yap", key=f"adm_role_{r.user_id}",
                            on_click=cb_admin_role, args=(r.user_id, target), width="stretch")
            elif r.user_id == ctx.user_id and r.role == "ADMIN":
                role.button("⬇️ Yöneticilikten çekil", key=f"adm_role_{r.user_id}", on_click=cb_admin_role,
                            args=(r.user_id, "MEMBER"), width="stretch")


def _turn_section(wc: world_manager.WorldController) -> None:
    status = wc.turn_status()
    rules = wc.rules
    state = wc.state
    last = "—"
    if state.last_advance_at is not None:
        last = (state.last_advance_at.strftime("%d.%m %H:%M") + " · "
                + TRIGGER_LABELS.get(state.last_advance_trigger or "", state.last_advance_trigger or ""))
    st.markdown(stat_strip_html([
        ("Tur", season_text(status)),
        ("Hazır", f"{status.ready}/{status.active}"),
        ("Otomatik ilerleme", "açık" if rules.auto_advance else "duraklatıldı"),
        ("Son ilerleme", last),
    ]), unsafe_allow_html=True)
    st.caption(deadline_text(status) + " · " + ready_text(status))
    st.button("⏩ Haftayı şimdi oynat", key="adm_force", on_click=cb_admin_force, args=(status.season, status.week),
              type="primary", help="Hazır olmayanları beklemeden oynatır; tur açıldıktan sonra hiç işlem yapmayan "
                                   "menajerin kaçırılan hafta sayacı artar.")
    st.markdown("#### Tur ayarları")
    ss = st.session_state
    ss.setdefault("adm_deadline_hours", int(rules.deadline_hours))
    ss.setdefault("adm_pause_auto", not rules.auto_advance)
    c1, c2 = st.columns(2)
    c1.number_input("Hafta süresi (saat)", min_value=1, max_value=168, step=1, key="adm_deadline_hours")
    c2.toggle("Otomatik ilerlemeyi duraklat", key="adm_pause_auto",
              help="Duraklatınca hafta yalnızca herkes hazır olunca ya da yönetici oynatınca ilerler.")
    st.button("💾 Tur ayarlarını kaydet", key="adm_turn_save", on_click=cb_admin_turn_save)


def _rule_value(field: str, rules: WorldRules):
    return getattr(rules, field)


def _rules_section(db, cm: CareerManager, wc: world_manager.WorldController) -> None:
    rules = wc.rules
    locked = season_started(db, cm)
    if locked:
        st.info(LOCKED_RULES_TEXT)
    ss = st.session_state
    left, right = st.columns(2, gap="large")
    for index, (field, kind) in enumerate(RULE_WIDGETS):
        key = _rule_key(field)
        ss.setdefault(key, _rule_value(field, rules))
        disabled = locked and field in GAMEPLAY_FIELDS
        label = FIELD_LABELS.get(field, field) + (" 🔒" if disabled else "")
        column = left if index % 2 == 0 else right
        if kind == "bool":
            column.toggle(label, key=key, disabled=disabled)
        elif kind == "int":
            low, high = RULE_BOUNDS[field]
            column.number_input(label, min_value=low, max_value=high, step=1, key=key, disabled=disabled)
        elif kind == "points":
            column.selectbox(label, [3, 2], key=key, disabled=disabled)
        else:
            column.selectbox(label, list(STRICTNESS_LABELS), key=key, format_func=STRICTNESS_LABELS.get,
                             disabled=disabled)
    b1, b2 = st.columns(2)
    b1.button("💾 Kuralları kaydet", key="adm_rules_save", on_click=cb_admin_rules_save, type="primary",
              width="stretch")
    b2.button("↩️ Değişiklikleri geri al", key="adm_rules_reset", on_click=cb_admin_rules_reset, width="stretch")


def _fair_play_section(cm: CareerManager, wc: world_manager.WorldController) -> None:
    """Adil oyun: inceleme kuyrugu (onay / ret + neden), geri alinabilir anlasmalar (neden + onay), puanlar."""
    rules = wc.rules
    if not (rules.human_market or rules.loans):
        st.info("Bu dünyada menajerler arası pazar ve kiralık kapalı; kurallardan açılabilir (sezon başında).")
    hub = MarketHub(cm)
    team = cm.user_team
    my_team_id = team.id if team is not None else None

    st.markdown("#### ⚖️ İnceleme kuyruğu")
    queue = hub.review_queue()
    if not queue:
        st.caption("İnceleme bekleyen anlaşma yok.")
    for view in queue:
        with st.container(border=True):
            market_view.offer_summary(view)
            help_text = None if view.can_review else REVIEW_OWN_TEXT
            st.text_input("Ret nedeni (taraflara gösterilir)", key=f"adm_review_reason_{view.id}",
                          max_chars=REVIEW_REASON_MAX, disabled=not view.can_review)
            b1, b2 = st.columns(2)
            b1.button("✅ Onayla", key=f"adm_review_approve_{view.id}", on_click=cb_admin_review_approve,
                      args=(view.id,), type="primary", disabled=not view.can_review, help=help_text, width="stretch")
            b2.button("⛔ Reddet", key=f"adm_review_deny_{view.id}", on_click=cb_admin_review_deny, args=(view.id,),
                      disabled=not view.can_review, help=help_text, width="stretch")
    st.caption("Onay: anlaşma sözleşme aşamasına geçer, iki menajere +2 adil oyun. Ret: anlaşma engellenir, iki menajere "
               "-15 adil oyun.")

    st.markdown(f"#### ↩️ Geri alınabilir anlaşmalar (son {REVERSAL_WEEKS} hafta)")
    reversible = hub.reversible_offers(weeks=REVERSAL_WEEKS)
    if not reversible:
        st.caption("Geri alınabilir menajerler arası anlaşma yok.")
    else:
        r1, r2 = st.columns([3, 1])
        r1.text_input("Geri alma nedeni (taraflara gösterilir)", key="adm_reverse_reason", max_chars=REVIEW_REASON_MAX)
        confirmed = r2.checkbox("Geri almayı onaylıyorum", key="adm_reverse_ok")
        st.caption("Oyuncular eski kulübüne eski sözleşmesiyle döner, bedel satıcının kasasındaki kadar iade edilir, iki "
                   "menajere -25 adil oyun. Geri alınamaz.")
        for view in reversible:
            own = my_team_id is not None and my_team_id in (view.buyer_team_id, view.seller_team_id)
            with st.container(border=True):
                market_view.offer_summary(view)
                st.button("↩️ Anlaşmayı geri al", key=f"adm_reverse_{view.id}", on_click=cb_admin_reverse,
                          args=(view.id,), disabled=own or not confirmed,
                          help=REVIEW_OWN_TEXT if own else (None if confirmed else "Önce onay kutusunu işaretle."))

    st.markdown("#### 📊 Adil oyun puanları")
    rows = [r for r in wc.managers() if r.status in ("ACTIVE", "RELEASED")]
    st.dataframe(pd.DataFrame([
        {"Menajer": r.name, "Kulüp": r.team_name or "—", "Adil oyun": f"{r.fair_play:.0f}"} for r in rows
    ]), hide_index=True, width="stretch")
    st.caption("Engellenen anlaşma -10, reddedilen inceleme -15, geri alınan anlaşma -25; puan her hafta +1 toparlanır. "
               "Puan düştükçe denetim eşikleri sertleşir.")


def _world_info(ctx) -> worlds.WorldInfo | None:
    return next((w for w in worlds.list_my_worlds(ctx.user_id) if w.id == ctx.world_id), None)


def _invite_section(ctx) -> None:
    info = _world_info(ctx)
    if info is None:
        st.warning("Dünya kaydı okunamadı.")
        return
    st.markdown(stat_strip_html([
        ("Menajer", f"{info.active_managers}/{info.max_managers}"),
        ("Görünürlük", VISIBILITY_LABELS.get(info.visibility, info.visibility)),
        ("En düşük seviye", f"{info.min_manager_level}"),
    ]), unsafe_allow_html=True)
    if info.invite_code:
        st.markdown("**Davet kodu** (arkadaşlarına ver; yenilersen eskisi hemen geçersiz olur)")
        st.code(info.invite_code, language=None)
        st.button("🔁 Davet kodunu yenile", key="adm_invite_rotate", on_click=cb_admin_invite_rotate)
    else:
        st.caption("Özel dünyanın davet kodu yok: katılım kapalı. Görünürlüğü davetli ya da açık yaparsan kod üretilir.")
    st.markdown("#### Dünya bilgileri")
    ss = st.session_state
    ss.setdefault("adm_name", info.name)
    ss.setdefault("adm_visibility", info.visibility)
    ss.setdefault("adm_min_level", int(info.min_manager_level))
    st.text_input("Dünya adı", key="adm_name", max_chars=worlds.NAME_MAX)
    c1, c2 = st.columns(2)
    c1.selectbox("Görünürlük", list(VISIBILITY_LABELS), key="adm_visibility", format_func=VISIBILITY_LABELS.get)
    c2.number_input("En düşük menajer seviyesi", min_value=1, max_value=reputation.MAX_LEVEL, step=1,
                    key="adm_min_level")
    st.button("💾 Kaydet", key="adm_meta_save", on_click=cb_admin_meta_save)


def _events_section(wc: world_manager.WorldController) -> None:
    events = wc.events(limit=50)
    if not events:
        st.info("Henüz kayıtlı olay yok.")
        return
    st.dataframe(pd.DataFrame([
        {"Zaman": e.created_at.strftime("%d.%m %H:%M") if e.created_at else "—",
         "Sezon/Hafta": f"S{e.season} H{e.week}", "Tür": EVENT_LABELS.get(e.kind, e.kind),
         "Kim": e.actor_name or "Sistem", "Olay": e.text}
        for e in events
    ]), hide_index=True, width="stretch")


# ===========================================================================
# CALLBACK'LER (admin_callback: OWNER / ADMIN; kontrolcu rolu ayni islemde yeniden okur)
# ===========================================================================

def _shared_ctx():
    ctx = callback_world()
    return ctx if ctx is not None and ctx.kind == WORLD_KIND_SHARED else None


@admin_callback
def cb_admin_kick(seat_id: int) -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    if not st.session_state.get("adm_kick_ok"):
        flash("admin", "error", "Menajeri atmak için önce onay kutusunu işaretle.")
        return
    reason = str(st.session_state.get("adm_kick_reason") or "")
    try:
        with session_scope() as db:
            wc = world_manager.WorldController(db, ctx)
            target = wc.seats.by_id(int(seat_id))
            wc.kick(int(seat_id), reason)
    except (worlds.WorldError, world_manager.TurnError) as exc:
        flash("admin", "error", str(exc))
        return
    reset_widgets("adm_kick_reason", "adm_kick_ok")
    flash("admin", "success", f"🚫 {md_escape(target.display_name) if target else 'Menajer'} dünyadan çıkarıldı.")


@admin_callback
def cb_admin_role(user_id: int, role: str) -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    try:
        worlds.set_role(ctx.user_id, ctx.world_id, int(user_id), role)
    except worlds.WorldError as exc:
        flash("admin", "error", str(exc))
        return
    flash("admin", "success", "Rol güncellendi: " + ROLE_LABELS.get(role, role) + ".")


@admin_callback
def cb_admin_force(expected_season: int, expected_week: int) -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    result = advance_world(ctx, AdvanceTrigger.FORCED, (int(expected_season), int(expected_week)))
    if result is not None and result.advanced:
        flash("admin", "success", f"⏩ {result.message}")


def _update_rules(ctx, changes: dict) -> WorldRules | None:
    try:
        with session_scope() as db:
            return world_manager.WorldController(db, ctx).update_rules(changes)
    except (RulesError, worlds.WorldError) as exc:
        flash("admin", "error", str(exc))
        return None


@admin_callback
def cb_admin_turn_save() -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    ss = st.session_state
    changes = {"deadline_hours": int(ss.get("adm_deadline_hours", 24)),
               "auto_advance": not bool(ss.get("adm_pause_auto", False))}
    if _update_rules(ctx, changes) is None:
        return
    reset_widgets(*TURN_KEYS)
    flash("admin", "success", "Tur ayarları kaydedildi.")


@admin_callback
def cb_admin_rules_save() -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    ss = st.session_state
    with session_scope() as db:
        current = world_manager.WorldController(db, ctx).rules
    changes = {}
    for field, _kind in RULE_WIDGETS:
        key = _rule_key(field)
        if key in ss and ss[key] != getattr(current, field):
            changes[field] = ss[key]
    if not changes:
        flash("admin", "info", "Değişiklik yok.")
        return
    if _update_rules(ctx, changes) is None:
        return
    reset_widgets(*(_rule_key(field) for field, _kind in RULE_WIDGETS))
    flash("admin", "success", "Kurallar kaydedildi: " + ", ".join(FIELD_LABELS.get(f, f) for f in changes) + ".")


@admin_callback
def cb_admin_rules_reset() -> None:
    reset_widgets(*(_rule_key(field) for field, _kind in RULE_WIDGETS))


@admin_callback
def cb_admin_invite_rotate() -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    try:
        code = worlds.rotate_invite_code(ctx.user_id, ctx.world_id)
    except worlds.WorldError as exc:
        flash("admin", "error", str(exc))
        return
    flash("admin", "success", f"Yeni davet kodu: {code} (eski kod artık geçersiz).")


def _market_admin_call(work):
    """
    Yonetici pazar islemi (SHARED kilit, tek islem). MarketError islem icinde yakalanir ve islem commit edilir
    (OfferVoided kalici); hata 'admin' alanina yazilir. Basarida (True, sonuc).
    """
    if _shared_ctx() is None:
        return False, None
    error, result = None, None
    with session_scope() as db:
        hub = MarketHub(manager(db))
        try:
            result = work(hub)
        except (TransferError, BudgetError) as exc:
            error = str(exc)
    if error is not None:
        flash("admin", "error", md_escape(error))
        return False, None
    return True, result


@admin_callback
def cb_admin_review_approve(offer_id: int) -> None:
    ok, view = _market_admin_call(lambda hub: hub.approve_review(int(offer_id)))
    if ok:
        flash("admin", "success", f"✅ Anlaşma onaylandı: {md_escape(view.player_name)} "
                                  f"({md_escape(view.seller_team)} → {md_escape(view.buyer_team)}).")
        reset_widgets(f"adm_review_reason_{offer_id}")


@admin_callback
def cb_admin_review_deny(offer_id: int) -> None:
    reason = str(st.session_state.get(f"adm_review_reason_{offer_id}") or "")
    ok, view = _market_admin_call(lambda hub: hub.deny_review(int(offer_id), reason))
    if ok:
        flash("admin", "success", f"⛔ Anlaşma reddedildi: {md_escape(view.player_name)} · {md_escape(view.reason)}")
        reset_widgets(f"adm_review_reason_{offer_id}")


@admin_callback
def cb_admin_reverse(offer_id: int) -> None:
    ss = st.session_state
    if not ss.get("adm_reverse_ok"):
        flash("admin", "error", "Anlaşmayı geri almak için önce onay kutusunu işaretle.")
        return
    reason = str(ss.get("adm_reverse_reason") or "")
    ok, news = _market_admin_call(lambda hub: hub.reverse_transfer(int(offer_id), reason))
    if ok:
        moves = "; ".join(md_escape(n.describe()) for n in news) or "kayıt yok"
        flash("admin", "success", f"↩️ Anlaşma geri alındı: {moves}")
        reset_widgets("adm_reverse_reason", "adm_reverse_ok")


@admin_callback
def cb_admin_meta_save() -> None:
    ctx = _shared_ctx()
    if ctx is None:
        return
    ss = st.session_state
    info = _world_info(ctx)
    if info is None:
        return
    wanted = {"name": str(ss.get("adm_name", info.name)), "visibility": ss.get("adm_visibility", info.visibility),
              "min_manager_level": int(ss.get("adm_min_level", info.min_manager_level))}
    changes = {k: v for k, v in wanted.items() if v != getattr(info, k)}
    if not changes:
        flash("admin", "info", "Değişiklik yok.")
        return
    try:
        worlds.update_world_meta(ctx.user_id, ctx.world_id, **changes)
    except worlds.WorldError as exc:
        flash("admin", "error", str(exc))
        return
    reset_widgets(*META_KEYS)
    flash("admin", "success", "Dünya bilgileri kaydedildi.")
