"""
market_view.py
==============
Teklifler & Mesajlar sekmesi (Faz 12 / 14. Asama, 12B; web_app.TAB_HUB, paylasilan dunyada) ve Transfer Pazari
sekmesinin menajer kulubu dali. Kurallar market_hub.MarketHub'da (commit etmez); bu modul yalnizca SUNUM +
callback. Callback'ler web_common.member_callback ile ACIKCA sarilir (tests/test_world_schema.py denetler).

    hub_tab(db, cm, team)            -> flash alani: hub. Ust seritte sayaclar (sekme etiketinde sayac YOK), sozlesme
                                        masasi (hneg_*), hub_section:
        Gelen teklifler / Giden teklifler -> teklif kartlari: bedel, takas, kiralik sartlari, tur, kalan sure, adil oyun
                                        karari (etiket + gerekceler), durum, gecmis. off_accept_{id}, off_reject_{id}
                                        (+ off_reject_reason_{id}), off_counter_fee_{id} / off_counter_ex_{id} (transfer)
                                        / off_counter_weeks_{id} + off_counter_share_{id} (kiralik) + off_counter_{id},
                                        off_withdraw_{id}, off_contract_{id} (transfer: sozlesme masasi; kiralik:
                                        tamamla, off_shift_{id} butce kaydirma), off_msg_{id} (karsi tarafa mesaj)
        Kiraliklar                   -> loan_recall_{loan_id}; yapay zekâ kulubune kiralik: loan_out_player,
                                        loan_out_team, loan_out_weeks, loan_out_share, loan_out_send
        Listelerim                   -> list_transfer_{player_id}, list_loan_{player_id}; diger menajerlerin listeleri
        Mesajlar / Dunya panosu / Bildirimler -> messages_view
    sozlesme masasi (TRANSFER, alici) -> hneg_wage, hneg_years, hneg_role, hneg_submit, hneg_accept, hneg_shift_sign,
                                        hneg_leave: open_contract -> submit_contract -> ACCEPTED ise complete(id,
                                        step.demand[, shift_wage_room]). Masa durumu session_state["hneg"]'de yalnizca
                                        gosterim icindir; gercek masa teklifin contract_log'undan yeniden kurulur.
    Transfer Pazari (web_app.transfer_tab):
        human_target(cm, player_id)  -> paylasilan dunyada PlayerMarketStatus (eski kariyer: None, sorgu yok)
        human_offer_panel(...)       -> menajer kulubundeki oyuncu: mkt_kind, mkt_fee, mkt_exchange, mkt_loan_weeks,
                                        mkt_loan_share, mkt_note, adil oyun on degerlendirmesi (yazma yok), mkt_h_offer;
                                        engel nedenleri (kiralik, yasak, koruma, acik teklif)
        ai_loan_panel(...)           -> yapay zekâ kulubunden kiralik: mkt_ai_loan_fee, mkt_ai_loan_weeks,
                                        mkt_ai_loan_share, mkt_ai_loan (flash alani: market)

Guvenlik: kulup her zaman cm.user_team (MarketHub._require_actor); widget'tan gelen id'ler yalnizca hedef secer,
yetki MarketHub'da. MarketError / TransferError / BudgetError islem ICINDE yakalanir ve islem commit edilir
(OfferVoided, adil oyun cezasi kalici olsun). Kullanici / veritabani metinleri md_escape ile duz metin; HTML yalnizca
kacisli yardimcilarla (negotiation_log_html, stat_strip_html).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st
from sqlalchemy import select

import market_rules
import messages_view
import player_view
from database import session_scope
from fair_play import Decision
from finance import BudgetError, format_money, weekly_to_transfer
from market_hub import MarketHub, OfferDraft
from market_rules import BUYER, SELLER, OfferKind, OfferStatus
from models import SquadRole, Team, TransferOffer
from ofm_theme import panel_title_html, stat_strip_html
from stars import star_range, stars
from transfers import ROLE_LABELS, ContractOffer, NegotiationStatus, TransferError
from web_common import (
    WORLD_KIND_SHARED,
    callback_world,
    flash,
    manager,
    md_escape,
    member_callback,
    reset_widgets,
    shared_page_world,
    show_flash,
)
from web_view import negotiation_log_html

if TYPE_CHECKING:
    from career_manager import CareerManager
    from fair_play import FairnessVerdict
    from market_hub import LoanView, OfferView, PlayerMarketStatus
    from models import Player

SEC_IN, SEC_OUT, SEC_LOANS, SEC_LISTS = "📥 Gelen teklifler", "📤 Giden teklifler", "🔁 Kiralıklar", "🏷️ Listelerim"
SEC_MESSAGES, SEC_BOARD, SEC_NOTIFICATIONS = "✉️ Mesajlar", "📌 Dünya panosu", "🔔 Bildirimler"
HUB_SECTIONS = [SEC_IN, SEC_OUT, SEC_LOANS, SEC_LISTS, SEC_MESSAGES, SEC_BOARD, SEC_NOTIFICATIONS]
HNEG_KEY = "hneg"
HNEG_WIDGETS = ("hneg_wage", "hneg_years", "hneg_role")
MKT_HUMAN_WIDGETS = ("mkt_kind", "mkt_fee", "mkt_exchange", "mkt_loan_weeks", "mkt_loan_share", "mkt_note")
NO_EXCHANGE = 0
FEE_STEP = 250_000

NOT_SHARED_TEXT = "Teklifler ve mesajlar yalnızca paylaşılan dünyada kullanılır."
NO_SEAT_TEXT = "Bu dünyada menajer koltuğun yok; teklif yapamaz ve mesaj gönderemezsin."
NO_TEAM_TEXT = "Önce yöneteceğin kulübü seç (🏟️ Kulübünü Seç)."
MARKET_OFF_TEXT = "Bu dünyada menajerler arası transfer pazarı ve kiralık sistemi kapalı."
LOANS_OFF_TEXT = "Bu dünyada kiralık sistemi kapalı."
WEEKS_HELP = "0 = sezon sonuna kadar. Kiralık en az 4 hafta sürer ve sezonu aşmaz."
SHARE_HELP = "Oyuncunun haftalık maaşının kiralayan kulüpçe ödenen yüzdesi; kalanını ana kulüp öder."

DECISION_ICONS = {Decision.ALLOW: "✅", Decision.REVIEW: "🟠", Decision.BLOCK: "⛔"}
LOAN_STATUS_LABELS = {"ACTIVE": "Sürüyor", "RETURNED": "Döndü", "RECALLED": "Geri çağrıldı", "REVERSED": "Geri alındı"}
ROLE_OPTIONS = [r.value for r in ROLE_LABELS]
OPEN_VALUES = frozenset(s.value for s in market_rules.OPEN_STATUSES)


# ===========================================================================
# METIN YARDIMCILARI (world_admin_view de kullanir)
# ===========================================================================

def _kind_label(kind: str) -> str:
    try:
        return market_rules.KIND_LABELS[OfferKind(kind)]
    except ValueError:
        return str(kind)


def terms_text(view: OfferView) -> str:
    """Teklifin sartlari (duz metin): bedel, takas oyuncusu, kiralik suresi ve maas payi."""
    text = format_money(view.fee)
    if view.exchange_player_name:
        text += f" + takas: {view.exchange_player_name}"
    if view.kind == OfferKind.LOAN.value:
        text += f" · {view.loan_weeks} hafta" if view.loan_weeks else " · sezon sonuna kadar"
        text += f" · maaşın %{view.loan_wage_share} payı kiralayanda"
    return text


def fairness_text(verdict: FairnessVerdict, *, preview: bool = False) -> str:
    """Adil oyun karari (duz metin): etiket, puan ve uygulanan esikler."""
    prefix = "Adil oyun ön değerlendirmesi" if preview else "Adil oyun"
    return (f"{DECISION_ICONS.get(verdict.decision, '⚖️')} {prefix}: {verdict.label} · puan {verdict.score:.0f} "
            f"(inceleme eşiği {verdict.review_at:.0f}, engel eşiği {verdict.block_at:.0f})")


def status_text(view: OfferView) -> str:
    """Durum etiketi + sira / kalan sure (teklifi goren tarafa gore)."""
    parts = [f"Durum: {view.status_label}"]
    my_side = SELLER if view.direction == "IN" else BUYER if view.direction == "OUT" else None
    status = view.status
    if status in (OfferStatus.PENDING.value, OfferStatus.COUNTERED.value):
        parts.append(f"karşı teklif {view.round}/{market_rules.MAX_COUNTER_ROUNDS}")
        if my_side is not None:
            parts.append("🟢 sıra sende" if view.turn == my_side else "⏳ karşı tarafın yanıtı bekleniyor")
    elif status == OfferStatus.CONTRACT.value and my_side is not None:
        if view.kind == OfferKind.LOAN.value:
            parts.append("alıcı kiralığı tamamlayacak" if my_side == SELLER else "✍️ kiralığı tamamlaman bekleniyor")
        else:
            parts.append("alıcı oyuncuyla sözleşme görüşüyor" if my_side == SELLER
                         else "✍️ sözleşme masası seni bekliyor")
    elif status == OfferStatus.REVIEW.value:
        parts.append("⚖️ yönetici incelemesinde")
    if view.expires_in_weeks is not None:
        parts.append(f"{view.expires_in_weeks} hafta içinde düşer" if view.expires_in_weeks else "bu hafta düşer")
    return " · ".join(parts)


def offer_summary(view: OfferView) -> None:
    """Teklif karti govdesi (dugmesiz): baslik, durum, sartlar, adil oyun, neden, gecmis. Metinler kacisli."""
    route = f"{view.seller_team or '—'} → {view.buyer_team or '—'}"
    position = f" ({view.player_position})" if view.player_position else ""
    st.markdown(f"**{md_escape(view.player_name)}**{md_escape(position)} · {md_escape(view.kind_label or _kind_label(view.kind))}"
                f" · {md_escape(route)} · teklif \\#{view.id}")
    st.caption(md_escape(status_text(view)))
    st.markdown("Şartlar: " + md_escape(terms_text(view)))
    if view.fairness is not None:
        st.caption(md_escape(fairness_text(view.fairness)))
        for reason in view.fairness.reasons:
            st.caption("• " + md_escape(reason))
    if view.reason:
        text = md_escape(view.reason)
        if view.status in (OfferStatus.BLOCKED.value, OfferStatus.VOIDED.value, OfferStatus.REJECTED.value):
            st.error(text)
        elif view.status == OfferStatus.REVIEW.value:
            st.warning(text)
        else:
            st.caption(text)
    if view.history:
        with st.expander("Teklif geçmişi"):
            for line in view.history:
                st.caption(md_escape(line))


def _player_option(player: Player) -> str:
    return f"{player.name} · {player.position.value} · {player.age} yaş · {stars(player.overall_rating)}"


# ===========================================================================
# SEKME
# ===========================================================================

def hub_tab(db, cm: CareerManager, team: Team | None) -> None:
    """Flash alani: hub."""
    st.markdown(panel_title_html("📨 Teklifler & Mesajlar"), unsafe_allow_html=True)
    show_flash("hub")
    ctx = shared_page_world()
    if ctx is None or not cm.rules.shared:
        st.info(NOT_SHARED_TEXT)
        return
    hub = MarketHub(cm)
    counts = hub.counts()
    strip = [("Yanıt bekleyen teklif", counts.offers_action), ("Gelen açık teklif", counts.offers_in),
             ("Okunmamış mesaj", counts.messages), ("Okunmamış bildirim", counts.notifications)]
    admin = hub.is_admin()
    if admin:
        strip.append(("İnceleme bekleyen anlaşma", counts.reviews))
    st.markdown(stat_strip_html(strip), unsafe_allow_html=True)
    if cm.acting_seat is None:
        st.warning(NO_SEAT_TEXT)
    contract_panel()
    section = st.radio("Bölüm", HUB_SECTIONS, key="hub_section", horizontal=True, label_visibility="collapsed")
    if section in (SEC_IN, SEC_OUT):
        offers_section(hub, cm, team, incoming=section == SEC_IN)
    elif section == SEC_LOANS:
        loans_section(db, hub, cm, team)
    elif section == SEC_LISTS:
        listings_section(hub, cm, team)
    elif section == SEC_MESSAGES:
        messages_view.messages_section(db, cm)
    elif section == SEC_BOARD:
        messages_view.board_section(db, cm)
    else:
        messages_view.notifications_section(db, cm)
    # Faz 13E: bu sekmede acilan oyuncu profili (teklif kartlari ve listeler ayni mekanizmayi kullanir)
    player_view.profile_panel(db, cm, team, player_view.AREA_HUB)


# ---------------------------------------------------------------------------
# Teklifler
# ---------------------------------------------------------------------------

def offers_section(hub: MarketHub, cm: CareerManager, team: Team | None, *, incoming: bool) -> None:
    rules = cm.rules
    if not (rules.human_market or rules.loans):
        st.info(MARKET_OFF_TEXT)
    if team is None:
        st.info(NO_TEAM_TEXT)
        return
    views = hub.inbox() if incoming else hub.outbox()
    open_views = [v for v in views if v.status in OPEN_VALUES]
    closed = [v for v in views if v.status not in OPEN_VALUES]
    if not views:
        st.info("Kulübüne henüz teklif gelmedi. Oyuncularını 🏷️ Listelerim bölümünden satışa ya da kiralığa "
                "çıkarabilirsin." if incoming else
                "Henüz teklif yapmadın: 🔄 Transfer Pazarı sekmesinde bir menajerin kulübündeki oyuncuyu seç.")
        return
    if not open_views:
        st.caption("Açık teklif yok.")
    for view in open_views:
        offer_card(hub, view)
    if closed:
        with st.expander(f"Kapanan teklifler ({len(closed)})"):
            for view in closed:
                with st.container(border=True):
                    offer_summary(view)


def offer_card(hub: MarketHub, view: OfferView) -> None:
    """Acik teklif karti + tarafin yapabilecegi eylemler (MarketHub.allowed_actions'tan gelen can_* bayraklari)."""
    oid = view.id
    loan = view.kind == OfferKind.LOAN.value
    with st.container(border=True):
        offer_summary(view)
        b1, b2, b3, b4 = st.columns(4)
        if view.can_accept:
            b1.button("✅ Kabul et", key=f"off_accept_{oid}", on_click=cb_offer_accept, args=(oid,), type="primary",
                      width="stretch", help="Bonservis şartlarını kabul eder; adil oyun denetimi burada yapılır.")
        if view.can_contract:
            b1.button("✍️ Kiralığı tamamla" if loan else "✍️ Sözleşme masasına otur", key=f"off_contract_{oid}",
                      on_click=cb_offer_contract, args=(oid,), type="primary", width="stretch")
        if view.can_withdraw:
            b2.button("↩️ Geri çek", key=f"off_withdraw_{oid}", on_click=cb_offer_withdraw, args=(oid,),
                      width="stretch")
        if view.can_reject:
            b3.button("✖️ Reddet", key=f"off_reject_{oid}", on_click=cb_offer_reject, args=(oid,), width="stretch")
        b4.button("💬 Mesaj yaz", key=f"off_msg_{oid}", on_click=cb_offer_message, args=(oid,), width="stretch",
                  help="Karşı tarafın menajerine bu teklif hakkında mesaj.")
        # Faz 13E: teklifin konusu olan oyuncunun profili (panel sekmenin altinda acilir)
        player_view.inspect_button(player_view.AREA_HUB, view.player_id, key=f"pv_row_hub_{oid}",
                                   label=f"{player_view.INSPECT_LABEL}: {md_escape(view.player_name)}")
        if view.can_contract and loan:
            st.checkbox("Maaş alanı yetmezse transfer bütçesinden kaydır", key=f"off_shift_{oid}")
        if view.can_reject:
            st.text_input("Ret nedeni (isteğe bağlı)", key=f"off_reject_reason_{oid}", max_chars=120)
        if view.can_counter:
            with st.expander("↔️ Karşı teklif"):
                counter_form(hub, view)


def counter_form(hub: MarketHub, view: OfferView) -> None:
    oid = view.id
    loan = view.kind == OfferKind.LOAN.value
    ss = st.session_state
    ss.setdefault(f"off_counter_fee_{oid}", int(view.fee))
    st.number_input("Bedel (EUR)", min_value=0, step=FEE_STEP, key=f"off_counter_fee_{oid}")
    if loan:
        ss.setdefault(f"off_counter_weeks_{oid}", int(view.loan_weeks or 0))
        ss.setdefault(f"off_counter_share_{oid}", int(view.loan_wage_share))
        c1, c2 = st.columns(2)
        c1.number_input("Kiralık süresi (hafta)", min_value=0, max_value=market_rules.MAX_LOAN_WEEKS, step=1,
                        key=f"off_counter_weeks_{oid}", help=WEEKS_HELP)
        c2.slider("Kiralayanın maaş payı (%)", 0, 100, step=5, key=f"off_counter_share_{oid}", help=SHARE_HELP)
    else:
        try:
            candidates = hub.exchange_candidates(oid)
        except TransferError:
            candidates = []
        options = {NO_EXCHANGE: "Takas yok", **{p.id: _player_option(p) for p in candidates}}
        key = f"off_counter_ex_{oid}"
        if ss.get(key) not in options:
            ss[key] = view.exchange_player_id if view.exchange_player_id in options else NO_EXCHANGE
        st.selectbox("Takas oyuncusu (alıcının kadrosundan)", list(options), key=key, format_func=options.get)
    st.button("↔️ Karşı teklifi gönder", key=f"off_counter_{oid}", on_click=cb_offer_counter, args=(oid,))


# ---------------------------------------------------------------------------
# Sozlesme masasi (alici, TRANSFER)
# ---------------------------------------------------------------------------

def contract_panel() -> None:
    neg = st.session_state.get(HNEG_KEY)
    if not neg:
        return
    with st.container(border=True):
        st.markdown(f"#### 🤝 Sözleşme masası — {md_escape(neg['player'])}")
        st.caption(md_escape(f"Bonservis {format_money(neg['fee'])} · {neg['seller']} → {neg['buyer']} · "
                             f"teklif #{neg['offer_id']}"))
        st.markdown(negotiation_log_html(neg["log"]), unsafe_allow_html=True)
        status = neg["status"]
        if status == NegotiationStatus.OPEN.value and neg.get("demand"):
            wage, years, role = neg["demand"]
            st.caption(md_escape(f"Güncel talep: {ContractOffer(wage, years, SquadRole(role)).describe()} · "
                                 f"kalan pazarlık hakkı {neg['rounds_left']}"))
            c1, c2, c3 = st.columns(3)
            c1.number_input("Haftalık maaş (EUR)", min_value=0, step=1_000, key="hneg_wage")
            c2.number_input("Sözleşme (yıl)", min_value=1, max_value=5, key="hneg_years")
            c3.selectbox("Kadro rolü", ROLE_OPTIONS, key="hneg_role", format_func=lambda v: ROLE_LABELS[SquadRole(v)])
            b1, b2, b3 = st.columns(3)
            b1.button("📨 Teklifi sun", key="hneg_submit", on_click=cb_hneg_submit, type="primary", width="stretch")
            b2.button("✅ Talebi kabul et", key="hneg_accept", on_click=cb_hneg_accept, width="stretch")
            b3.button("🚪 Masadan kalk", key="hneg_leave", on_click=cb_hneg_leave, width="stretch",
                      help="Masa kapanır; bonservis anlaşması açık kalır, Giden teklifler'den yeniden oturabilirsin.")
        elif status == NegotiationStatus.ACCEPTED.value and neg.get("agreed"):
            needed = neg.get("needs_room")
            if needed:
                st.warning(md_escape(f"Anlaşma sağlandı ama maaş havuzunda {format_money(needed)}/hafta yer yok. "
                                     f"Kaydırma maliyeti: {format_money(weekly_to_transfer(needed))} transfer bütçesi."))
            else:
                st.info("Oyuncuyla anlaştın; imzayla transfer tamamlanır.")
            b1, b2 = st.columns(2)
            b1.button("💱 Maaş alanı aç ve imzala" if needed else "✍️ İmzala", key="hneg_shift_sign",
                      on_click=cb_hneg_shift_sign, type="primary", width="stretch")
            b2.button("🚪 Masadan kalk", key="hneg_leave", on_click=cb_hneg_leave, width="stretch")
        else:
            st.button("Masayı kapat", key="hneg_leave", on_click=cb_hneg_leave)


# ---------------------------------------------------------------------------
# Kiraliklar
# ---------------------------------------------------------------------------

def loans_section(db, hub: MarketHub, cm: CareerManager, team: Team | None) -> None:
    rules = cm.rules
    if not rules.loans:
        st.info(LOANS_OFF_TEXT)
    if team is None:
        st.info(NO_TEAM_TEXT)
        return
    views = hub.loans("ALL")
    if not views:
        st.caption("Kiralık oyuncun yok. Menajer kulüplerinden kiralık için 🔄 Transfer Pazarı'nda teklif türünü "
                   "Kiralık seç; yapay zekâ kulüplerinden de aynı sekmede kiralık isteyebilirsin.")
    for view in views:
        loan_card(view)
    if rules.loans:
        loan_out_form(db, cm, team)


def loan_card(view: LoanView) -> None:
    with st.container(border=True):
        way = {"OUT": "Kiralığa verdiğin", "IN": "Kiraladığın"}.get(view.direction, "Kiralık")
        st.markdown(f"**{md_escape(view.player_name)}** · {way} · "
                    f"{md_escape(view.parent_team or '—')} → {md_escape(view.borrower_team or '—')}")
        parts = [f"Durum: {LOAN_STATUS_LABELS.get(view.status, view.status)}",
                 f"maaşın %{view.wage_share} payı kiralayanda"]
        if view.weeks_left is not None:
            parts.append(f"{view.weeks_left} hafta kaldı")
        if view.status == "ACTIVE":
            parts.append(f"oyuncunun durumu: {view.concern_label}")
        st.caption(md_escape(" · ".join(parts)))
        if view.direction == "OUT" and view.status == "ACTIVE":
            st.button("📞 Geri çağır", key=f"loan_recall_{view.id}", on_click=cb_loan_recall, args=(view.id,),
                      disabled=not view.can_recall, help=None if view.can_recall else view.recall_reason or None)


def loan_out_form(db, cm: CareerManager, team: Team) -> None:
    st.markdown("#### 🔁 Oyuncunu yapay zekâ kulübüne kirala")
    players = sorted((p for p in team.players if p.loan_from_team_id is None and not p.in_academy),
                     key=lambda p: (-p.overall_rating, p.id))
    humans = sorted(cm.human_team_ids())
    clubs = list(db.scalars(select(Team).where(Team.id.notin_(humans or [-1])).order_by(Team.league_id, Team.name)))
    if not players or not clubs:
        st.caption("Kiralığa verilebilecek oyuncu ya da yapay zekâ kulübü yok.")
        return
    names = {p.id: _player_option(p) for p in players}
    club_names = {t.id: t.name for t in clubs}
    ss = st.session_state
    for key, options in (("loan_out_player", names), ("loan_out_team", club_names)):
        if ss.get(key) not in options:
            reset_widgets(key)
    c1, c2 = st.columns(2)
    c1.selectbox("Oyuncu", list(names), key="loan_out_player", format_func=names.get)
    c2.selectbox("Kiralayacak kulüp", list(club_names), key="loan_out_team", format_func=club_names.get)
    c3, c4 = st.columns(2)
    c3.number_input("Süre (hafta)", min_value=0, max_value=market_rules.MAX_LOAN_WEEKS, value=0, step=1,
                    key="loan_out_weeks", help=WEEKS_HELP)
    c4.slider("Kiralayanın maaş payı (%)", 0, 100, value=50, step=5, key="loan_out_share", help=SHARE_HELP)
    st.button("🔁 Kiralık öner", key="loan_out_send", on_click=cb_loan_out,
              help="Yapay zekâ kulübü hemen karar verir (bedel ödemez).")


# ---------------------------------------------------------------------------
# Listeler
# ---------------------------------------------------------------------------

def listings_section(hub: MarketHub, cm: CareerManager, team: Team | None) -> None:
    rules = cm.rules
    if team is None:
        st.info(NO_TEAM_TEXT)
    else:
        st.markdown("#### 🏷️ Oyuncularım")
        if not (rules.human_market or rules.loans):
            st.info(MARKET_OFF_TEXT)
        st.caption("Listeye koyduğun oyuncular diğer menajerlerin listelerinde görünür; teklifler yine Gelen "
                   "teklifler'e düşer.")
        players = sorted((p for p in team.players if not p.in_academy), key=lambda p: (-p.overall_rating, p.id))
        for p in players:
            loaned_in = p.loan_from_team_id is not None
            info, look, sale, loan = st.columns([3, 1, 2, 2])
            flags = (" · 🏷️ satılık" if p.transfer_listed else "") + (" · 🔁 kiralık" if p.loan_listed else "")
            info.markdown(md_escape(f"{p.name} · {p.position.value} · {p.age} yaş · {stars(p.overall_rating)}")
                          + (" · kiralık geldi" if loaned_in else "") + flags)
            # Faz 13E: listeye koymadan once oyuncuyu incele (profil sekmenin altinda acilir)
            player_view.inspect_button(player_view.AREA_HUB, p.id, key=f"pv_row_hub_p{p.id}", container=look)
            sale.button("✖️ Satış listesinden çıkar" if p.transfer_listed else "🏷️ Satışa çıkar",
                        key=f"list_transfer_{p.id}", on_click=cb_listing, args=(p.id, "TRANSFER", not p.transfer_listed),
                        disabled=not rules.human_market or (loaned_in and not p.transfer_listed), width="stretch")
            loan.button("✖️ Kiralık listesinden çıkar" if p.loan_listed else "🔁 Kiralığa çıkar",
                        key=f"list_loan_{p.id}", on_click=cb_listing, args=(p.id, "LOAN", not p.loan_listed),
                        disabled=not rules.loans or (loaned_in and not p.loan_listed), width="stretch")
    st.markdown("#### 🔎 Menajerlerin listeleri")
    listed: dict[int, str] = {}
    for kind, title in (("TRANSFER", "Satılık"), ("LOAN", "Kiralık")):
        rows = [p for p in hub.listed_players(kind) if team is None or p.team_id != team.id]
        st.caption(f"{title}: {len(rows)} oyuncu")
        if rows:
            # Faz 13G: satira tek tik -> profil (panel sekmenin altinda, hub_tab)
            player_view.selectable_table(player_view.AREA_HUB, pd.DataFrame([listed_row(cm, team, p) for p in rows]),
                                         [p.id for p in rows], key=f"hub_list_{kind}")
            listed.update({p.id: player_view.option_label(
                p.name, p.position.value, f"{title} · {p.team.name if p.team else '—'}") for p in rows})
    if listed:                                           # Faz 13E: listedeki oyuncuyu incele (sorgu eklemez)
        player_view.picker(player_view.AREA_HUB, listed)
    st.caption("Teklif için oyuncuyu 🔄 Transfer Pazarı sekmesinde seç.")


def listed_row(cm: CareerManager, team: Team | None, player: Player) -> dict:
    row = {"Oyuncu": player.name, "Kulüp": player.team.name if player.team else "—",
           "Mv": player.position.value, "Yaş": player.age}
    if team is not None:
        report = cm.scouted_report(team, player)
        row["Güç (tahmin)"] = star_range(report["overall_rating"].low, report["overall_rating"].high)
        value = report["market_value"]
        row["Değer (tahmin)"] = f"{format_money(value.low)} – {format_money(value.high)}"
    return row


# ===========================================================================
# TRANSFER PAZARI DALI (web_app.transfer_tab)
# ===========================================================================

def human_target(cm: CareerManager, player_id: int) -> PlayerMarketStatus | None:
    """Paylasilan dunyada hedef oyuncunun pazar durumu; eski / kisisel kariyerde None (sorgu yok)."""
    if not cm.rules.shared or player_id is None:
        return None
    try:
        return MarketHub(cm).player_status(int(player_id))
    except TransferError:
        return None


def club_managers(cm: CareerManager) -> dict[str, str]:
    """Paylasilan dunyada kulup adi -> menajer adi (Transfer Pazari tablosunun Menajer sutunu); eski kariyer: {}."""
    if not cm.rules.shared:
        return {}
    names: dict[str, str] = {}
    for seat in cm.seats.members():
        team_id = cm.state.user_team_id if seat.is_primary else seat.team_id
        team = cm.db.get(Team, team_id) if team_id is not None else None
        if team is not None:
            names[team.name] = seat.display_name
    return names


def human_offer_panel(db, cm: CareerManager, team: Team, player_id: int, suggested_fee: int,
                      status: PlayerMarketStatus) -> None:
    """Menajer kulubundeki oyuncuya teklif (AI 'Bonservis teklifi yap' alaninin yerine; flash alani: market)."""
    rules = cm.rules
    hub = MarketHub(cm)
    ss = st.session_state
    st.markdown("#### 🤝 Menajerler arası teklif")
    st.caption(f"👤 Bu kulübü **{md_escape(status.seller_seat_name or 'bir menajer')}** yönetiyor: teklifin ona gider, "
               "yanıtı 📨 Teklifler & Mesajlar sekmesinde görürsün.")
    kinds = ([OfferKind.TRANSFER.value] if rules.human_market else []) + ([OfferKind.LOAN.value] if rules.loans else [])
    if not kinds:
        st.info(MARKET_OFF_TEXT)
        return
    blocked = status.block_reason
    if blocked:
        st.warning("⛔ " + md_escape(blocked))
    if status.my_open_offer_id is not None:
        st.info(f"Bu oyuncu için açık teklifin var (\\#{status.my_open_offer_id}): 📤 Giden teklifler'den güncelle "
                "ya da geri çek.")
    elif status.open_offers:
        st.caption(f"Bu oyuncu için başka kulüplerin {status.open_offers} açık teklifi var.")
    if ss.get("mkt_kind") not in kinds:
        reset_widgets("mkt_kind")
    kind = st.radio("Teklif türü", kinds, key="mkt_kind", horizontal=True, format_func=_kind_label)
    loan = kind == OfferKind.LOAN.value
    if ss.get("mkt_fee_kind") != kind:
        reset_widgets("mkt_fee")
        ss["mkt_fee_kind"] = kind
    st.number_input("Kiralık bedeli (EUR)" if loan else "Bonservis (EUR)", min_value=0, step=500_000,
                    value=0 if loan else int(suggested_fee), key="mkt_fee")
    exchange = None
    weeks, share = None, 100
    if loan:
        c1, c2 = st.columns(2)
        weeks = int(c1.number_input("Süre (hafta)", min_value=0, max_value=market_rules.MAX_LOAN_WEEKS, value=0,
                                    step=1, key="mkt_loan_weeks", help=WEEKS_HELP)) or None
        share = int(c2.slider("Maaş payın (%)", 0, 100, value=100, step=5, key="mkt_loan_share", help=SHARE_HELP))
    else:
        try:
            candidates = hub.exchange_candidates()
        except TransferError:
            candidates = []
        options = {NO_EXCHANGE: "Takas yok", **{p.id: _player_option(p) for p in candidates}}
        if ss.get("mkt_exchange") not in options:
            reset_widgets("mkt_exchange")
        exchange = st.selectbox("Takas oyuncusu (isteğe bağlı)", list(options), key="mkt_exchange",
                                format_func=options.get) or None
    st.text_input("Not (isteğe bağlı)", key="mkt_note", max_chars=200)
    draft = OfferDraft(player_id=int(player_id), kind=OfferKind(kind), fee=int(ss.get("mkt_fee") or 0),
                       exchange_player_id=exchange, loan_weeks=weeks, loan_wage_share=share)
    try:
        verdict = hub.preview_fairness(draft)
    except TransferError as exc:
        st.caption("⚖️ Ön değerlendirme yapılamadı: " + md_escape(exc))
    else:
        st.caption(md_escape(fairness_text(verdict, preview=True)))
        for reason in verdict.reasons:
            st.caption("• " + md_escape(reason))
        if verdict.decision is not Decision.ALLOW:
            st.caption("Karşı taraf kabul ederse anlaşma " + ("engellenir ve iki menajerin adil oyun puanı düşer."
                                                             if verdict.decision is Decision.BLOCK
                                                             else "yönetici incelemesine gider."))
    st.button("📨 Teklifi gönder", key="mkt_h_offer", on_click=cb_market_offer, type="primary",
              disabled=bool(blocked) or status.my_open_offer_id is not None)


def ai_loan_panel(cm: CareerManager, team: Team, status: PlayerMarketStatus) -> None:
    """Yapay zekâ kulubundeki oyuncuyu kiralik iste (rules.loans; aninda karar)."""
    if not cm.rules.loans:
        return
    st.markdown("#### 🔁 Kiralık iste")
    if status.block_reason:
        st.caption("⛔ " + md_escape(status.block_reason))
    c1, c2, c3 = st.columns(3)
    c1.number_input("Kiralık bedeli (EUR)", min_value=0, step=FEE_STEP, value=0, key="mkt_ai_loan_fee")
    c2.number_input("Süre (hafta)", min_value=0, max_value=market_rules.MAX_LOAN_WEEKS, value=0, step=1,
                    key="mkt_ai_loan_weeks", help=WEEKS_HELP)
    c3.slider("Maaş payın (%)", 0, 100, value=100, step=5, key="mkt_ai_loan_share", help=SHARE_HELP)
    st.button("🔁 Kiralık iste", key="mkt_ai_loan", on_click=cb_market_ai_loan, disabled=bool(status.block_reason),
              help="Yapay zekâ kulübü isteğe hemen karar verir.")


# ===========================================================================
# CALLBACK YARDIMCILARI
# ===========================================================================

def _callback_ctx(area: str):
    ctx = callback_world()
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        flash(area, "error", NOT_SHARED_TEXT)
        return None
    return ctx


def _hub_call(area: str, work) -> tuple[bool, object]:
    """
    work(hub) SHARED dunya kilidi altindaki tek islemde calisir. Pazar hatasi islem ICINDE yakalanir ve islem commit
    edilir (OfferVoided / adil oyun cezasi kalici); hata mesaji alana yazilir. Basarida (True, sonuc).
    """
    if _callback_ctx(area) is None:
        return False, None
    error = None
    result = None
    with session_scope() as db:
        hub = MarketHub(manager(db))
        try:
            result = work(hub)
        except (TransferError, BudgetError) as exc:
            error = str(exc)
    if error is not None:
        flash(area, "error", md_escape(error))
        return False, None
    return True, result


def _reset_offer_widgets(offer_id: int) -> None:
    reset_widgets(*(f"off_{name}_{offer_id}" for name in (
        "counter_fee", "counter_ex", "counter_weeks", "counter_share", "reject_reason", "shift")))


def accept_messages(view: OfferView) -> list[tuple[str, str]]:
    """Kabul sonucu (CONTRACT / REVIEW / BLOCKED) icin Turkce flash mesajlari (kacisli)."""
    name = md_escape(view.player_name)
    loan = view.kind == OfferKind.LOAN.value
    if view.status == OfferStatus.CONTRACT.value:
        if loan:
            nxt = ("📤 Giden teklifler'den kiralığı tamamlayabilirsin." if view.direction == "OUT"
                   else "Alıcı kiralığı tamamlayınca oyuncu kiralanır.")
        else:
            nxt = ("📤 Giden teklifler'den oyuncuyla sözleşme masasına otur." if view.direction == "OUT"
                   else "Alıcı şimdi oyuncuyla sözleşme görüşecek.")
        return [("success", f"🤝 Anlaşma sağlandı ({name}). {nxt}")]
    if view.status == OfferStatus.REVIEW.value:
        return [("warning", f"⚖️ Anlaşma yönetici incelemesine gönderildi ({name}): {md_escape(view.reason)}")]
    if view.status == OfferStatus.BLOCKED.value:
        return [("error", f"⛔ Anlaşma engellendi ({name}): {md_escape(view.reason)} "
                          "İki menajerin adil oyun puanı düştü.")]
    return [("info", f"Teklif durumu: {md_escape(view.status_label)}")]


# ===========================================================================
# CALLBACK'LER: teklifler
# ===========================================================================

@member_callback
def cb_offer_accept(offer_id: int) -> None:
    ok, view = _hub_call("hub", lambda hub: hub.accept(int(offer_id)))
    if ok:
        for kind, text in accept_messages(view):
            flash("hub", kind, text)
        _reset_offer_widgets(int(offer_id))


@member_callback
def cb_offer_reject(offer_id: int) -> None:
    reason = str(st.session_state.get(f"off_reject_reason_{offer_id}") or "")
    ok, view = _hub_call("hub", lambda hub: hub.reject(int(offer_id), reason))
    if ok:
        flash("hub", "info", f"✖️ Teklif reddedildi ({md_escape(view.player_name)}).")
        _reset_offer_widgets(int(offer_id))


@member_callback
def cb_offer_withdraw(offer_id: int) -> None:
    ok, view = _hub_call("hub", lambda hub: hub.withdraw(int(offer_id)))
    if ok:
        flash("hub", "info", f"↩️ Teklif geri çekildi ({md_escape(view.player_name)}).")
        _reset_offer_widgets(int(offer_id))
        neg = st.session_state.get(HNEG_KEY)
        if neg and neg.get("offer_id") == int(offer_id):
            _close_contract()


@member_callback
def cb_offer_counter(offer_id: int) -> None:
    oid = int(offer_id)
    ss = st.session_state

    def work(hub: MarketHub):
        current = hub.offer(oid)
        fee = int(ss.get(f"off_counter_fee_{oid}", current.fee) or 0)
        if current.kind == OfferKind.LOAN.value:
            weeks = int(ss.get(f"off_counter_weeks_{oid}", current.loan_weeks or 0) or 0) or None
            share = int(ss.get(f"off_counter_share_{oid}", current.loan_wage_share))
            return hub.counter(oid, fee=fee, loan_weeks=weeks, loan_wage_share=share)
        exchange = ss.get(f"off_counter_ex_{oid}", current.exchange_player_id or NO_EXCHANGE)
        return hub.counter(oid, fee=fee, exchange_player_id=int(exchange) if exchange else None)

    ok, view = _hub_call("hub", work)
    if ok:
        flash("hub", "success", f"↔️ Karşı teklif gönderildi ({md_escape(view.player_name)}): "
                                f"{md_escape(terms_text(view))}.")
        _reset_offer_widgets(oid)


@member_callback
def cb_offer_contract(offer_id: int) -> None:
    """TRANSFER: sozlesme masasi acilir (ilk acilis kaydi yazilir); LOAN: kiralik tamamlanir."""
    oid = int(offer_id)
    shift = bool(st.session_state.get(f"off_shift_{oid}"))

    def work(hub: MarketHub):
        view = hub.offer(oid)
        if view.kind == OfferKind.LOAN.value:
            return view, None, hub.complete(oid, None, shift_wage_room=shift)
        return view, hub.open_contract(oid), None

    ok, result = _hub_call("hub", work)
    if not ok:
        return
    view, step, news = result
    if news is not None:
        flash("hub", "success", f"🔁 Kiralık tamamlandı: {md_escape(news.describe())}")
        _reset_offer_widgets(oid)
        return
    log = [("me", f"Bonservis anlaşması: {format_money(view.fee)}"
                  + (f" + takas {view.exchange_player_name}" if view.exchange_player_name else ""))]
    st.session_state[HNEG_KEY] = {
        "offer_id": oid, "player": view.player_name, "fee": int(view.fee), "seller": view.seller_team or "—",
        "buyer": view.buyer_team or "—", "log": log, "status": NegotiationStatus.OPEN.value, "demand": None,
        "agreed": None, "rounds_left": 0, "needs_room": None,
    }
    _apply_step(step, opening=True)


@member_callback
def cb_offer_message(offer_id: int) -> None:
    """Teklifin karsi tarafina mesaj: alici / koltugu ve konu doldurulur, Mesajlar bolumu acilir."""
    oid = int(offer_id)

    def work(hub: MarketHub):
        view = hub.offer(oid)                                   # yetki: taraf ya da yonetici
        row = hub.db.get(TransferOffer, oid)
        seat = hub.cm.acting_seat
        mine = seat.id if seat is not None else None
        other = row.responded_by_manager_id if row.created_by_manager_id == mine else row.created_by_manager_id
        return view, other

    ok, result = _hub_call("hub", work)
    if not ok:
        return
    view, other = result
    if other is None:
        flash("hub", "error", "Bu teklifin karşı tarafında menajer yok.")
        return
    ss = st.session_state
    ss["msg_to"], ss["msg_offer_id"] = int(other), oid
    ss["msg_subject"] = f"Teklif: {view.player_name}"[:80]
    ss["hub_section"] = SEC_MESSAGES


# ===========================================================================
# CALLBACK'LER: sozlesme masasi
# ===========================================================================

def _close_contract() -> None:
    st.session_state.pop(HNEG_KEY, None)
    reset_widgets(*HNEG_WIDGETS)


def _apply_step(step, *, opening: bool = False, offer: ContractOffer | None = None) -> None:
    """ContractStep'i ekran durumuna yazar (log, talep, anlasma, maas alani)."""
    neg = st.session_state.get(HNEG_KEY)
    if neg is None or step is None:
        return
    if offer is not None:
        neg["log"].append(("me", f"Teklif: {offer.describe()}"))
    status = step.status
    neg["log"].append(("bad" if status is NegotiationStatus.WALKED_AWAY else "him", step.message))
    for complaint in step.complaints:
        neg["log"].append(("him", f"· {complaint}"))
    neg["status"], neg["rounds_left"] = status.value, int(step.rounds_left)
    if status is NegotiationStatus.OPEN and step.demand is not None:
        demand = step.demand
        neg["demand"] = (int(demand.wage), int(demand.years), demand.role.value)
        st.session_state["hneg_wage"] = int(demand.wage)
        st.session_state["hneg_years"] = int(demand.years)
        st.session_state["hneg_role"] = demand.role.value
    elif status is NegotiationStatus.ACCEPTED and step.demand is not None:
        agreed = step.demand
        neg["agreed"] = (int(agreed.wage), int(agreed.years), agreed.role.value)
        neg["needs_room"] = step.needs_room
    elif status is NegotiationStatus.WALKED_AWAY:
        flash("hub", "error", "Transfer iptal oldu: " + md_escape(step.message))
    if opening and status is NegotiationStatus.OPEN:
        flash("hub", "success", f"🤝 Sözleşme masası açıldı: {md_escape(neg['player'])}.")


def _contract_offer(neg: dict, offer: ContractOffer) -> None:
    """Oyuncuya sozlesme teklifi; anlasilirsa (maas alani yetiyorsa) ayni islemde transfer tamamlanir."""
    oid = int(neg["offer_id"])
    outcome: dict = {}

    def work(hub: MarketHub):
        step = hub.submit_contract(oid, offer)
        outcome["step"] = step
        if step.status is NegotiationStatus.ACCEPTED and not step.needs_room:
            outcome["news"] = hub.complete(oid, step.demand)
        return outcome

    ok, _ = _hub_call("hub", work)
    if not ok and "step" not in outcome:
        _close_contract()                                     # teklif kapanmis olabilir: masa teklif kaydindan yeniden acilir
        flash("hub", "info", "Sözleşme masası kapandı; anlaşma hâlâ açıksa 📤 Giden teklifler'den yeniden otur.")
        return
    _finish_contract(neg, outcome, offer)


def _finish_contract(neg: dict, outcome: dict, offer: ContractOffer | None) -> None:
    news = outcome.get("news")
    if news is not None:
        flash("hub", "success", f"✅ TRANSFER TAMAM: {md_escape(news.describe())}")
        _close_contract()
        return
    if "step" in outcome:
        _apply_step(outcome["step"], offer=offer)


@member_callback
def cb_hneg_submit() -> None:
    neg = st.session_state.get(HNEG_KEY)
    if not neg or neg.get("status") != NegotiationStatus.OPEN.value:
        return
    ss = st.session_state
    try:
        offer = ContractOffer(wage=int(ss.get("hneg_wage") or 0), years=int(ss.get("hneg_years") or 1),
                              role=SquadRole(ss.get("hneg_role") or (neg.get("demand") or (0, 0, "BACKUP"))[2]))
    except ValueError:
        flash("hub", "error", "Geçersiz sözleşme teklifi.")
        return
    _contract_offer(neg, offer)


@member_callback
def cb_hneg_accept() -> None:
    neg = st.session_state.get(HNEG_KEY)
    if not neg or neg.get("status") != NegotiationStatus.OPEN.value or not neg.get("demand"):
        return
    wage, years, role = neg["demand"]
    _contract_offer(neg, ContractOffer(wage=int(wage), years=int(years), role=SquadRole(role)))


@member_callback
def cb_hneg_shift_sign() -> None:
    """Anlasilan sozlesmeyle imza; maas alani yetmiyorsa ayni islemde butce kaydirilir."""
    neg = st.session_state.get(HNEG_KEY)
    if not neg or neg.get("status") != NegotiationStatus.ACCEPTED.value or not neg.get("agreed"):
        return
    wage, years, role = neg["agreed"]
    contract = ContractOffer(wage=int(wage), years=int(years), role=SquadRole(role))
    outcome: dict = {}

    def work(hub: MarketHub):
        outcome["news"] = hub.complete(int(neg["offer_id"]), contract, shift_wage_room=bool(neg.get("needs_room")))
        return outcome

    _hub_call("hub", work)
    _finish_contract(neg, outcome, None)


@member_callback
def cb_hneg_leave() -> None:
    if st.session_state.get(HNEG_KEY):
        _close_contract()
        flash("hub", "info", "Sözleşme masasından kalkıldı; anlaşma açık kaldıysa Giden teklifler'den dönebilirsin.")


# ===========================================================================
# CALLBACK'LER: kiralik ve listeler
# ===========================================================================

@member_callback
def cb_loan_recall(loan_id: int) -> None:
    ok, view = _hub_call("hub", lambda hub: hub.recall_loan(int(loan_id)))
    if ok:
        flash("hub", "success", f"📞 {md_escape(view.player_name)} kiralıktan geri çağrıldı.")


@member_callback
def cb_loan_out() -> None:
    ss = st.session_state
    player_id, team_id = ss.get("loan_out_player"), ss.get("loan_out_team")
    if player_id is None or team_id is None:
        flash("hub", "error", "Oyuncuyu ve kiralayacak kulübü seç.")
        return
    draft = OfferDraft(player_id=int(player_id), kind=OfferKind.LOAN, fee=0,
                       loan_weeks=int(ss.get("loan_out_weeks") or 0) or None,
                       loan_wage_share=int(ss.get("loan_out_share", 50)), target_team_id=int(team_id))
    ok, view = _hub_call("hub", lambda hub: hub.request_ai_loan(draft))
    if ok:
        flash("hub", "success", f"🔁 Kiralık onaylandı: {md_escape(view.player_name)} → {md_escape(view.borrower_team)}"
                                f" (maaşın %{view.wage_share} payı kiralayanda).")
        reset_widgets("loan_out_player")


@member_callback
def cb_listing(player_id: int, kind: str, listed: bool) -> None:
    kind = str(kind).upper()
    changes = {"transfer_listed": bool(listed)} if kind == "TRANSFER" else {"loan_listed": bool(listed)}
    ok, _ = _hub_call("hub", lambda hub: hub.set_listing(int(player_id), **changes))
    if ok:
        what = "satış" if kind == "TRANSFER" else "kiralık"
        flash("hub", "success", f"Oyuncu {what} listesine eklendi." if listed
              else f"Oyuncu {what} listesinden çıkarıldı.")


# ===========================================================================
# CALLBACK'LER: Transfer Pazari (flash alani: market)
# ===========================================================================

@member_callback
def cb_market_offer() -> None:
    ss = st.session_state
    player_id = ss.get("mkt_target")
    if player_id is None:
        flash("market", "error", "Önce hedef oyuncuyu seç.")
        return
    try:
        kind = OfferKind(str(ss.get("mkt_kind") or OfferKind.TRANSFER.value))
    except ValueError:
        flash("market", "error", "Geçersiz teklif türü.")
        return
    loan = kind is OfferKind.LOAN
    exchange = None if loan else (int(ss.get("mkt_exchange") or 0) or None)
    draft = OfferDraft(player_id=int(player_id), kind=kind, fee=int(ss.get("mkt_fee") or 0),
                       exchange_player_id=exchange,
                       loan_weeks=(int(ss.get("mkt_loan_weeks") or 0) or None) if loan else None,
                       loan_wage_share=int(ss.get("mkt_loan_share", 100)) if loan else 100,
                       note=str(ss.get("mkt_note") or ""))
    ok, view = _hub_call("market", lambda hub: hub.make_offer(draft))
    if ok:
        flash("market", "success", f"📨 {md_escape(view.kind_label)} teklifi gönderildi: {md_escape(view.player_name)}"
                                   f" ({md_escape(terms_text(view))}). Yanıtı 📨 Teklifler & Mesajlar sekmesinde "
                                   "göreceksin.")
        reset_widgets("mkt_note", "mkt_exchange")


@member_callback
def cb_market_ai_loan() -> None:
    ss = st.session_state
    player_id = ss.get("mkt_target")
    if player_id is None:
        flash("market", "error", "Önce hedef oyuncuyu seç.")
        return
    draft = OfferDraft(player_id=int(player_id), kind=OfferKind.LOAN, fee=int(ss.get("mkt_ai_loan_fee") or 0),
                       loan_weeks=int(ss.get("mkt_ai_loan_weeks") or 0) or None,
                       loan_wage_share=int(ss.get("mkt_ai_loan_share", 100)))
    ok, view = _hub_call("market", lambda hub: hub.request_ai_loan(draft))
    if ok:
        weeks = f"{view.weeks_left} hafta" if view.weeks_left is not None else "sezon sonuna kadar"
        flash("market", "success", f"🔁 Kiralık onaylandı: {md_escape(view.player_name)}, "
                                   f"{md_escape(view.parent_team)} → {md_escape(view.borrower_team)} "
                                   f"({md_escape(weeks)}, maaşın %{view.wage_share} payı sende).")
