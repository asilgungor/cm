"""
world_lobby_view.py
===================
Dunya lobisi (Faz 12 / 14. Asama, 12A): Dunyalarim / Dunya olustur / Davet koduyla katil / Acik dunyalar ve
kulup secimi. Kenar cubugundaki "Dunyalar" (sb_worlds) ya da uyeligi dusen oturum buraya yonlenir
(web_common.LOBBY_KEY). Callback'ler requires_auth / member_callback ile ACIKCA sarilir (web_app'in otomatik
sarma dongusu bu modulu gormez; tests/test_world_schema.py her cb_* icin denetler).

    render_lobby()                     -> lobi sayfasi (flash alani: lobby)
        lobby_section: Dunyalarim (lobby_enter_{id}, lobby_leave_ok_{id} + lobby_leave_{id},
                       lconv_name_{id} / lconv_visibility_{id} / lconv_max_{id} + lobby_convert_{id}),
                       Dunya olustur (wc_name, wc_visibility, wc_max, wc_min_level, wc_deadline_hours,
                       wc_auto_advance, wc_by_level, wc_market, wc_intl, wc_strictness, wc_seed, wc_create),
                       Davet koduyla katil (wj_code, wj_join), Acik dunyalar (wb_query, wb_join_{id});
                       lobby_back: bagli dunyaya donus
    render_club_offers(db, cm, ctx)    -> paylasilan dunyada kulubu olmayan koltuk: ulke -> lig -> kulup
                                          (club_picker_view.render_picker; co_country, co_league), co_query (tum
                                          ulkelerde arama), co_only_eligible, co_claim_{team_id} (flash alani: clubs)

Guvenlik: dunyaya giris yalnizca worlds.enter_world / join_* / create_world / convert donusunden (uyelik orada
denetlenir) web_common.bind_world ile; oturum hicbir zaman dunyasiz (eski oturum) olarak baska kariyere
dusurulmez. Dunya, kulup ve kullanici adlari md_escape ile duz metin gosterilir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st
from sqlalchemy import select

import club_picker_view
import reputation
import world_manager
import worlds
from database import session_scope
from models import Team
from ofm_theme import panel_title_html, stat_strip_html
from web_common import (
    KIND_LABELS,
    LOBBY_KEY,
    ROLE_LABELS,
    STRICTNESS_LABELS,
    VISIBILITY_LABELS,
    WORLD_KIND_PERSONAL,
    WORLD_KIND_SHARED,
    bind_world,
    callback_world,
    flash,
    md_escape,
    member_callback,
    parse_seed,
    requires_auth,
    reset_widgets,
    show_flash,
)
from world_rules import RulesError, WorldRules

if TYPE_CHECKING:
    from career_manager import CareerManager
    from worlds import WorldContext, WorldInfo

SEC_MINE, SEC_CREATE, SEC_CODE, SEC_PUBLIC = ("🗂️ Dünyalarım", "🌱 Dünya oluştur", "🔑 Davet koduyla katıl",
                                              "🌐 Açık dünyalar")
LOBBY_SECTIONS = [SEC_MINE, SEC_CREATE, SEC_CODE, SEC_PUBLIC]
CREATING_TEXT = "Dünya kuruluyor: ligler, kulüpler ve fikstür hazırlanıyor…"


def _auth():
    return st.session_state.get("auth")


def _level_text(level: int) -> str:
    title = reputation.MANAGER_LEVELS[max(1, min(level, reputation.MAX_LEVEL)) - 1][1]
    return f"{level}. seviye ({title})"


# ===========================================================================
# LOBI
# ===========================================================================

def render_lobby() -> None:
    """Lobi sayfasi (flash alani: lobby)."""
    auth = _auth()
    st.markdown(panel_title_html("🌍 Dünyalar"), unsafe_allow_html=True)
    show_flash("lobby")
    if auth is None:
        return
    mine = worlds.list_my_worlds(auth.user_id)
    current = next((w for w in mine if w.id == getattr(auth, "world_id", None)), None)
    if getattr(auth, "world_id", None) is None:
        st.button("↩️ Kariyerine dön", key="lobby_back", on_click=cb_lobby_back)
    elif current is not None:
        st.button(f"↩️ {md_escape(current.name)} dünyasına dön", key="lobby_back", on_click=cb_lobby_back)
    else:
        st.info("Şu an bir dünyaya bağlı değilsin: listeden bir dünyaya gir, dünya kur ya da bir dünyaya katıl.")

    section = st.radio("Bölüm", LOBBY_SECTIONS, key="lobby_section", horizontal=True, label_visibility="collapsed")
    if section == SEC_MINE:
        _my_worlds(auth, mine)
    elif section == SEC_CREATE:
        _create_form()
    elif section == SEC_CODE:
        _join_by_code_form()
    else:
        _public_worlds(auth)


def _world_caption(w: WorldInfo) -> str:
    parts = [KIND_LABELS.get(w.kind, w.kind), ROLE_LABELS.get(w.my_role or "", "—")]
    if w.kind == WORLD_KIND_SHARED:
        parts += [f"{w.active_managers}/{w.max_managers} menajer", VISIBILITY_LABELS.get(w.visibility, w.visibility),
                  f"en az {w.min_manager_level}. seviye"]
        if w.owner_name:
            parts.append(f"sahibi {md_escape(w.owner_name)}")
    return " · ".join(parts)


def _my_worlds(auth, mine: list[WorldInfo]) -> None:
    if not mine:
        st.info("Henüz bir dünyan yok. **🌱 Dünya oluştur** ya da davet koduyla bir dünyaya katıl.")
        return
    for w in mine:
        here = w.id == getattr(auth, "world_id", None)
        with st.container(border=True):
            info, actions = st.columns([3, 2])
            info.markdown(f"**{md_escape(w.name)}**" + (" · 📍 buradasın" if here else ""))
            info.caption(_world_caption(w))
            if w.invite_code:
                info.caption(f"Davet kodu: `{w.invite_code}`")
            actions.button("▶ Gir", key=f"lobby_enter_{w.id}", on_click=cb_enter_world, args=(w.id,),
                           type="primary", width="stretch")
            if w.kind == WORLD_KIND_SHARED and w.my_role != "OWNER":
                ok = actions.checkbox("Ayrılmak istiyorum", key=f"lobby_leave_ok_{w.id}",
                                      help="Kulübün yapay zekâya geçer; yeniden katılmak için boş koltuk gerekir.")
                actions.button("🚪 Dünyadan ayrıl", key=f"lobby_leave_{w.id}", on_click=cb_leave_world,
                               args=(w.id,), disabled=not ok, width="stretch")
            if w.kind == WORLD_KIND_PERSONAL and w.my_role == "OWNER":
                with st.expander("👥 Paylaşılan dünyaya çevir (arkadaşlarını davet et)"):
                    st.caption("Kariyerin aynen kalır; sen dünyanın sahibi olursun. Davet koduyla katılan menajerler "
                               "boştaki kulüpleri seçer. Paylaşılan dünyada hafta, herkes hazır olunca ilerler.")
                    st.text_input("Dünya adı", key=f"lconv_name_{w.id}", max_chars=worlds.NAME_MAX)
                    st.selectbox("Görünürlük", list(VISIBILITY_LABELS), key=f"lconv_visibility_{w.id}",
                                 format_func=VISIBILITY_LABELS.get)
                    st.number_input("En fazla menajer", min_value=worlds.MIN_SHARED_MANAGERS,
                                    max_value=worlds.MAX_MANAGERS, value=8, step=1, key=f"lconv_max_{w.id}")
                    st.button("👥 Paylaşıma aç", key=f"lobby_convert_{w.id}", on_click=cb_convert_world,
                              args=(w.id,))


def _create_form() -> None:
    defaults = WorldRules.shared_defaults()
    st.caption("Yeni paylaşılan dünya: sentetik ligler ve kulüpler kurulur, sen sahibi olursun. Arkadaşların davet "
               "koduyla ya da açık dünyalar listesinden katılıp boştaki kulüpleri seçer.")
    st.text_input("Dünya adı", key="wc_name", max_chars=worlds.NAME_MAX,
                  help="1-40 karakter: harf, rakam, boşluk ve - _ .")
    st.selectbox("Görünürlük", list(VISIBILITY_LABELS), key="wc_visibility", format_func=VISIBILITY_LABELS.get)
    c1, c2, c3 = st.columns(3)
    c1.number_input("En fazla menajer", min_value=worlds.MIN_SHARED_MANAGERS, max_value=worlds.MAX_MANAGERS,
                    value=defaults.max_seats, step=1, key="wc_max")
    c2.number_input("En düşük menajer seviyesi", min_value=1, max_value=reputation.MAX_LEVEL, value=1, step=1,
                    key="wc_min_level")
    c3.number_input("Hafta süresi (saat)", min_value=1, max_value=168, value=defaults.deadline_hours, step=1,
                    key="wc_deadline_hours")
    t1, t2 = st.columns(2)
    t1.toggle("Süre dolunca hafta otomatik ilerlesin", value=defaults.auto_advance, key="wc_auto_advance")
    t2.toggle("Kulüp seçenekleri menajer seviyesine göre", value=defaults.club_offers_by_level, key="wc_by_level")
    t1.toggle("Menajerler arası transfer ve kiralık", value=defaults.human_market, key="wc_market")
    t2.toggle("Milli takımlar ve Dünya Kupası", value=defaults.internationals, key="wc_intl")
    st.selectbox("Adil oyun denetimi", list(STRICTNESS_LABELS), index=list(STRICTNESS_LABELS).index(
        defaults.fairness_strictness), key="wc_strictness", format_func=STRICTNESS_LABELS.get)
    with st.expander("Gelişmiş"):
        st.text_input("Dünya tohumu (boş = rastgele)", key="wc_seed",
                      help="Aynı tohum aynı ligleri ve fikstürü üretir.")
    st.button("🌱 Dünyayı kur", key="wc_create", on_click=cb_create_world, type="primary")


def _join_by_code_form() -> None:
    st.caption("Arkadaşının verdiği davet kodunu yaz (büyük/küçük harf ve tire önemli değil).")
    st.text_input("Davet kodu", key="wj_code", max_chars=16)
    st.button("🔑 Katıl", key="wj_join", on_click=cb_join_by_code, type="primary")


def _public_worlds(auth) -> None:
    query = st.text_input("Dünya ara", key="wb_query", max_chars=worlds.NAME_MAX)
    rows = worlds.list_public_worlds(auth.user_id, query or "")
    if not rows:
        st.info("Katılabileceğin açık dünya yok. Bir dünya kurup arkadaşlarını davet edebilirsin.")
        return
    for w in rows:
        with st.container(border=True):
            info, action = st.columns([4, 1])
            info.markdown(f"**{md_escape(w.name)}**")
            info.caption(" · ".join([f"{w.active_managers}/{w.max_managers} menajer",
                                     f"en az {_level_text(w.min_manager_level)}"]
                                    + ([f"sahibi {md_escape(w.owner_name)}"] if w.owner_name else [])))
            action.button("Katıl", key=f"wb_join_{w.id}", on_click=cb_join_public, args=(w.id,), type="primary",
                          width="stretch")


# ===========================================================================
# KULUP SECIMI
# ===========================================================================

def render_club_offers(db, cm: CareerManager, ctx: WorldContext | None) -> None:
    """Paylasilan dunyada kulupsuz koltuk: kulup teklifleri (co_query, co_only_eligible, co_claim_{team_id})."""
    show_flash("clubs")
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        st.info("Kulüp seçimi yalnızca paylaşılan dünyada yapılır.")
        return
    wc = world_manager.WorldController(db, ctx)
    seat = wc.my_seat()
    rules = wc.rules
    if seat is None or seat.status not in world_manager.SEATED_STATUSES:
        st.warning("Bu dünyada menajer koltuğun yok. Dünyalar sayfasından yeniden katılabilirsin.")
        return
    lvl = reputation.level(seat.reputation)
    st.markdown(panel_title_html("🏟️ Kulübünü seç"), unsafe_allow_html=True)
    if seat.status == "RELEASED":
        st.warning("Üst üste haftaları kaçırdığın için kulübünü kaybettin. Yeni bir kulüp seçebilirsin.")
    st.markdown(stat_strip_html([
        ("Menajer", seat.display_name),
        ("Seviye", f"{reputation.badge(lvl)} {lvl.level}/{reputation.MAX_LEVEL} · {lvl.title}"),
        ("Tanınırlık", f"{seat.reputation:.1f}/20"),
    ]), unsafe_allow_html=True)
    if rules.club_offers_by_level:
        st.caption("Bu dünyada kulüp seçenekleri menajer seviyene göre: büyük kulüpleri yönetmek için seviye atla. "
                   "🛡️ işaretli kulüpler bir süre yapay zekâ transferlerine karşı korunuyor.")
    # Faz 13G: ulke -> lig -> kulup (club_picker_view); arama kutusu (co_query) doluyken tum ulkelerde arar
    q1, q2 = st.columns([3, 1])
    q1.text_input("Kulüp ya da lig ara", key="co_query", max_chars=40, placeholder="🔎 …ya da kulüp / lig ara",
                  label_visibility="collapsed")
    only_eligible = q2.toggle("Yalnızca uygun", value=True, key="co_only_eligible")
    offers = wc.club_offers("", only_eligible=bool(only_eligible))
    club_picker_view.render_picker(
        club_picker_view.cards_from_offers(offers), prefix="co", button_key=lambda c: f"co_claim_{c.team_id}",
        on_choose=cb_claim_club, button_label="Yönet", query_key="co_query",
        empty_text="Şu an seçebileceğin boş kulüp yok.")


# ===========================================================================
# CALLBACK'LER
# ===========================================================================

def _enter(ctx: WorldContext, text: str) -> None:
    """Dunyaya giris sonrasi: oturum baglanir (career_ready ve eski ekran durumu atilir), lobi kapanir."""
    bind_world(ctx)
    flash("sidebar", "success", text)


@requires_auth
def cb_open_lobby() -> None:
    """Kenar cubugu "Dunyalar" (sb_worlds): dunya kilidi almaz (hafta oynarken de lobi acilir)."""
    st.session_state[LOBBY_KEY] = True


@requires_auth
def cb_lobby_back() -> None:
    st.session_state.pop(LOBBY_KEY, None)


@requires_auth
def cb_enter_world(world_id: int) -> None:
    auth = _auth()
    if getattr(auth, "world_id", None) == world_id:
        try:
            worlds.enter_world(auth.user_id, world_id)          # uyelik hala gecerli mi + son giris
        except worlds.WorldError as exc:
            flash("lobby", "error", str(exc))
            return
        st.session_state.pop(LOBBY_KEY, None)
        return
    try:
        ctx = worlds.enter_world(auth.user_id, int(world_id))
    except worlds.WorldError as exc:
        flash("lobby", "error", str(exc))
        return
    _enter(ctx, f"«{md_escape(ctx.name)}» yüklendi.")


@requires_auth
def cb_leave_world(world_id: int) -> None:
    auth = _auth()
    try:
        worlds.leave_world(auth.user_id, int(world_id))
    except worlds.WorldError as exc:
        flash("lobby", "error", str(exc))
        return
    flash("lobby", "success", "Dünyadan ayrıldın; kulübün yapay zekâya geçti.")
    if getattr(auth, "world_id", None) != world_id:
        return
    try:                                                          # bagli oldugu dunyadan ayrildi: varsayilan dunya
        ctx = worlds.default_world(auth)
    except worlds.WorldError:
        return                                                    # oturum lobide kalir (uyeliksiz dunya fail-closed)
    if ctx.world_id != world_id:
        bind_world(ctx, lobby=True)


@requires_auth
def cb_convert_world(world_id: int) -> None:
    auth = _auth()
    ss = st.session_state
    try:
        info = worlds.convert_personal_to_shared(
            auth.user_id, int(world_id), ss.get(f"lconv_name_{world_id}", ""),
            ss.get(f"lconv_visibility_{world_id}", "INVITE"), int(ss.get(f"lconv_max_{world_id}", 8)))
        ctx = worlds.enter_world(auth.user_id, info.id)
    except worlds.WorldError as exc:
        flash("lobby", "error", str(exc))
        return
    code = f" Davet kodu: {info.invite_code}" if info.invite_code else ""
    _enter(ctx, f"«{md_escape(info.name)}» artık paylaşılan dünya.{code}")


def _create_rules(ss) -> WorldRules:
    market = bool(ss.get("wc_market", True))
    return WorldRules.shared_defaults().with_changes({
        "auto_advance": bool(ss.get("wc_auto_advance", True)),
        "deadline_hours": int(ss.get("wc_deadline_hours", 24)),
        "club_offers_by_level": bool(ss.get("wc_by_level", True)),
        "human_market": market,
        "loans": market,
        "internationals": bool(ss.get("wc_intl", False)),
        "fairness_strictness": ss.get("wc_strictness", "MEDIUM"),
    })


@requires_auth
def cb_create_world() -> None:
    auth = _auth()
    ss = st.session_state
    seed_raw = str(ss.get("wc_seed") or "").strip()
    seed = parse_seed(seed_raw) if seed_raw else None
    if seed_raw and seed is None:
        flash("lobby", "error", worlds.BAD_SEED)
        return
    try:
        rules = _create_rules(ss)
        with st.spinner(CREATING_TEXT):
            ctx = worlds.create_world(auth.user_id, ss.get("wc_name", ""), visibility=ss.get("wc_visibility", "INVITE"),
                                      max_managers=int(ss.get("wc_max", 8)),
                                      min_manager_level=int(ss.get("wc_min_level", 1)), rules=rules, world_seed=seed)
    except (worlds.WorldError, RulesError) as exc:
        flash("lobby", "error", str(exc))
        return
    _enter(ctx, f"«{md_escape(ctx.name)}» kuruldu! Önce kulübünü seç; davet kodu 🛡️ Dünya Yönetimi sekmesinde.")


@requires_auth
def cb_join_by_code() -> None:
    auth = _auth()
    try:
        ctx = worlds.join_by_code(auth.user_id, str(st.session_state.get("wj_code") or ""))
    except worlds.WorldError as exc:
        flash("lobby", "error", str(exc))
        return
    _enter(ctx, f"«{md_escape(ctx.name)}» dünyasına katıldın. Şimdi kulübünü seç.")


@requires_auth
def cb_join_public(world_id: int) -> None:
    auth = _auth()
    try:
        ctx = worlds.join_public(auth.user_id, int(world_id))
    except worlds.WorldError as exc:
        flash("lobby", "error", str(exc))
        return
    _enter(ctx, f"«{md_escape(ctx.name)}» dünyasına katıldın. Şimdi kulübünü seç.")


@member_callback
def cb_claim_club(team_id: int) -> None:
    """Kulupsuz koltuk kulup alir (uygunluk, insan kulubu ve koruma denetimi WorldController.claim_club'da)."""
    ctx = callback_world()
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        return
    try:
        with session_scope() as db:
            seat = world_manager.WorldController(db, ctx).claim_club(int(team_id))
            name = db.scalar(select(Team.name).where(Team.id == seat.team_id)) if seat.team_id is not None else None
    except worlds.WorldError as exc:
        flash("clubs", "error", str(exc))
        return
    reset_widgets("co_query", "co_country", "co_league")
    flash("sidebar", "success", f"🏟️ {md_escape(name or 'Kulüp')} artık senin! Kadronu ve taktiğini hazırla, sonra hazır ol.")
    flash(club_picker_view.WELCOME_AREA, "success", f"🏟️ {md_escape(name or 'Kulüp')} artık senin! Kadronu ve taktiğini hazırla, sonra hazır ol.")
    st.session_state[club_picker_view.SCROLL_TOP_KEY] = True

