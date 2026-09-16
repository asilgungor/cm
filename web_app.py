"""
web_app.py
==========
CM Menajer Paneli (7. Asama) -- tamamen tarayici tabanli kariyer arayuzu.

    streamlit run web_app.py

Sekmeler:
    Canli Mac       : 2D saha + spiker akisi + canli istatistik ve kondisyon
    Kadro & Taktik  : dizilis, asistan, ilk 11 secimi, kondisyon cubuklari
    Finans          : iki kalemli butce, 52 haftalik kaydirici
    Transfer Pazari : gozlemci sisli arama, bonservis teklifi, sozlesme masasi
    Lig             : puan durumu, gol kralligi, sonraki haftayi oyna
    Teknik Heyet    : personel, etkiler, ise alma / gonderme

Mimari:
    * Bu dosya yalnizca SUNUM: kurallar career_manager / tactics / finance / transfers /
      fitness modullerinde, veri satirlari career_views'da, HTML web_view ve pitch'te.
    * Veriyi degistiren her dugme on_click CALLBACK'i kullanir. Streamlit callback'i
      betik bastan calismadan ONCE isletir; boylece "haftayi oyna" sonrasi tum sekmeler
      guncel veriyle cizilir (bayat kadro/butce gostermez).
    * Canli mac dongusu (time.sleep) diger sekmeler cizildikten SONRA calisir; mac
      oynarken yonetim sekmeleri de dolu gorunur.
"""

from __future__ import annotations

import time
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import select

import career_views as cv
import pitch
import reputation
import staff as staff_rules
from career_manager import CareerManager, SeasonNotFinished
from database import schema_problems, session_scope, wait_for_db
from finance import BudgetError, format_money, preview_budget_shift, wage_budget_bounds
from fitness import condition_band
from match_engine import simulate_friendly
from match_feed import build_timeline, summarize, team_energy_at
from models import Player, Position, SquadRole, Staff, StaffRole, Team
from tactics import FORMATIONS, arrange_slots
from transfers import ROLE_LABELS, ContractOffer, NegotiationStatus, TransferError
from web_view import (
    CSS,
    banner_html,
    condition_bar_html,
    feed_html,
    negotiation_log_html,
    scoreboard_html,
    squad_table_html,
    stats_html,
    summary_lines,
    usage_bar_html,
)

SPEEDS = {"Yavaş": 1.2, "Normal": 0.55, "Hızlı": 0.2, "Anında": 0.0}
TABS = ["🏟️ Canlı Maç", "📋 Kadro & Taktik", "💰 Finans", "🔄 Transfer Pazarı", "🏆 Lig", "👥 Teknik Heyet"]
LIVE_MODES = ["Hazırlık maçı", "Son maçımı izle"]
POSITIONS = [p.value for p in Position]


# ===========================================================================
# ORTAK YARDIMCILAR
# ===========================================================================

def parse_seed(raw) -> int | None:
    """Kullanicinin yazdigi tohum; gecersizse (bos, '--5', '²') rastgele."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def career_seed() -> int | None:
    return parse_seed(st.session_state.get("career_seed", ""))


def manager(db) -> CareerManager:
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


def load_teams() -> list[str]:
    with session_scope() as db:
        return [t.name for t in db.scalars(select(Team).order_by(Team.league_id, Team.name))]


# ===========================================================================
# CALLBACK'LER (veriyi degistiren tek yer)
# ===========================================================================

def cb_set_team() -> None:
    with session_scope() as db:
        cm = manager(db)
        team = cm.find_team(st.session_state["sb_team"])
        if team is None:
            flash("sidebar", "error", f"Takım bulunamadı: {st.session_state['sb_team']}")
            return
        cm.set_user_team(team)
    reset_widgets("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee", "last_week_lines",
                  "last_user_result", "tac_formation")


def cb_set_formation() -> None:
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        check = cm.set_formation(team, st.session_state["tac_formation"])
        flash("squad", "success", f"Diziliş {team.formation} olarak ayarlandı.")
        for err in check.errors:
            flash("squad", "warning", f"Mevcut ilk 11 yeni dizilişe uymuyor: {err}")
    reset_widgets("tac_editor", "tac_rows")


def cb_auto_lineup() -> None:
    with session_scope() as db:
        cm = manager(db)
        xi = cm.auto_lineup(cm.user_team)
        flash("squad", "success", f"Asistan {len(xi)} kişilik ilk 11'i ve kulübeyi kurdu "
                                  f"(overall × form × moral × kondisyon).")
    reset_widgets("tac_editor", "tac_rows")


def cb_clear_lineup() -> None:
    with session_scope() as db:
        cm = manager(db)
        cm.clear_lineup(cm.user_team)
        flash("squad", "info", "Kadro temizlendi; maçta asistan en iyi 11'i kuracak.")
    reset_widgets("tac_editor", "tac_rows")


def cb_save_lineup() -> None:
    rows = st.session_state.get("tac_rows") or []
    try:
        xi, bench = cv.lineup_from_editor(rows)
    except ValueError as exc:
        flash("squad", "error", f"Geçersiz slot: {exc}")
        return
    with session_scope() as db:
        cm = manager(db)
        check = cm.set_lineup(cm.user_team, xi, bench)
    if check.ok:
        flash("squad", "success", "Kadro kaydedildi.")
        reset_widgets("tac_editor", "tac_rows")
    else:
        flash("squad", "error", "Kadro kaydedilmedi: " + " · ".join(check.errors))
    for warning in check.warnings:
        flash("squad", "warning", warning)


def cb_apply_budget() -> None:
    target = int(st.session_state.get("fin_target", 0))
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        delta = target - team.wage_budget
        if delta == 0:
            flash("finance", "info", "Değişiklik yok.")
            return
        try:
            transfer, wage = cm.shift_budget(team, delta)
        except BudgetError as exc:
            flash("finance", "error", str(exc))
            return
    direction = "maaş havuzuna" if delta > 0 else "transfer bütçesine"
    flash("finance", "success",
          f"Haftalık {format_money(abs(delta))} {direction} aktarıldı. "
          f"Transfer: {format_money(transfer)} · Maaş havuzu: {format_money(wage)}/hafta")
    reset_widgets("fin_target")


def cb_offer_fee() -> None:
    player_id = st.session_state.get("mkt_target")
    fee = int(st.session_state.get("mkt_fee", 0) or 0)
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        player = db.get(Player, player_id) if player_id is not None else None
        if player is None:
            flash("market", "error", "Oyuncu bulunamadı.")
            return
        try:
            decision = cm.offer_fee(team, player, fee)
        except TransferError as exc:
            flash("market", "error", str(exc))
            return
        if not decision.accepted:
            flash("market", "warning", f"{player.team.name}: {decision.reason} Daha yüksek bir teklifle tekrar dene.")
            return
        negotiation = cm.open_negotiation(team, player, fee)
        log = [("me", f"Bonservis teklifi: {format_money(fee)}"),
               ("him", f"{player.team.name}: {decision.reason}")]
        if negotiation.open:
            log.append(("him", f"{player.name} talebini açıkladı: {negotiation.demand.describe()}"))
            flash("market", "success", f"{player.team.name} teklifi kabul etti. Sözleşme masası açıldı.")
        else:
            log.append(("bad", negotiation.opening_message))
            flash("market", "error", negotiation.opening_message)
        st.session_state["neg"] = {
            "negotiation": negotiation, "player_id": player.id, "buyer_id": team.id,
            "fee": fee, "log": log, "needs_room": None,
        }
        _reset_offer_inputs(negotiation)


def _reset_offer_inputs(negotiation) -> None:
    demand = negotiation.demand
    st.session_state["neg_wage"] = int(demand.wage)
    st.session_state["neg_years"] = int(demand.years)
    st.session_state["neg_role"] = demand.role.value


def _complete(db, cm: CareerManager, neg: dict, offer: ContractOffer) -> None:
    buyer = db.get(Team, neg["buyer_id"])
    player = db.get(Player, neg["player_id"])
    news = cm.complete_transfer(buyer, player, neg["fee"], offer)
    flash("market", "success", f"TRANSFER TAMAM: {news.describe()}")
    st.session_state.pop("neg", None)
    reset_widgets("mkt_target", "mkt_fee", "tac_editor", "tac_rows", "fin_target")


def _respond(offer: ContractOffer) -> None:
    neg = st.session_state.get("neg")
    if not neg:
        return
    negotiation = neg["negotiation"]
    if not negotiation.open:
        return
    neg["log"].append(("me", f"Teklif: {offer.describe()}"))
    response = negotiation.respond(offer)
    kind = {"ACCEPTED": "him", "OPEN": "him", "WALKED_AWAY": "bad"}[response.status.value]
    neg["log"].append((kind, response.message))
    for complaint in response.complaints:
        neg["log"].append(("him", f"· {complaint}"))

    if response.status is NegotiationStatus.OPEN:
        _reset_offer_inputs(negotiation)
        return
    if response.status is NegotiationStatus.WALKED_AWAY:
        flash("market", "error", "Transfer iptal oldu.")
        return

    with session_scope() as db:
        cm = manager(db)
        buyer = db.get(Team, neg["buyer_id"])
        if offer.wage > buyer.free_wage:
            neg["needs_room"] = offer.wage - buyer.free_wage
            neg["agreed"] = offer
            return
        try:
            _complete(db, cm, neg, offer)
        except TransferError as exc:
            flash("market", "error", str(exc))


def cb_neg_submit() -> None:
    offer = ContractOffer(
        wage=int(st.session_state.get("neg_wage", 0)),
        years=int(st.session_state.get("neg_years", 3)),
        role=SquadRole(st.session_state.get("neg_role")),
    )
    _respond(offer)


def cb_neg_accept() -> None:
    neg = st.session_state.get("neg")
    if neg and neg["negotiation"].open:
        _respond(neg["negotiation"].demand)


def cb_neg_shift_sign() -> None:
    neg = st.session_state.get("neg")
    if not neg or not neg.get("needs_room"):
        return
    with session_scope() as db:
        cm = manager(db)
        buyer = db.get(Team, neg["buyer_id"])
        try:
            cm.shift_budget(buyer, int(neg["needs_room"]))
            _complete(db, cm, neg, neg["agreed"])
        except (BudgetError, TransferError) as exc:
            flash("market", "error", str(exc))


def cb_neg_leave() -> None:
    if st.session_state.pop("neg", None):
        flash("market", "info", "Sözleşme masasından kalkıldı.")


def cb_play_week() -> None:
    with session_scope() as db:
        cm = manager(db)
        report = cm.play_week()
        st.session_state["last_week_lines"] = cv.week_report_lines(report)
        if report.user_result is not None:
            st.session_state["last_user_result"] = report.user_result
        if report.played_any:
            flash("league", "success", f"{report.week}. hafta oynandı.")
        else:
            flash("league", "info", "Oynanacak maç yok — sezon tamamlandı.")
    reset_widgets("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee")


def cb_new_season() -> None:
    with session_scope() as db:
        cm = manager(db)
        try:
            season = cm.start_new_season()
            flash("league", "success", f"Sezon {season} başladı!")
        except SeasonNotFinished as exc:
            flash("league", "error", str(exc))
    reset_widgets("last_week_lines", "last_user_result")


def cb_hire() -> None:
    staff_id = st.session_state.get("st_hire")
    with session_scope() as db:
        cm = manager(db)
        member = db.get(Staff, staff_id) if staff_id is not None else None
        if member is None:
            return
        try:
            cm.hire_staff(cm.user_team, member)
            flash("staff", "success", f"{member.name} kadroya katıldı ({format_money(member.wage)}/hafta).")
        except TransferError as exc:
            flash("staff", "error", str(exc))
    reset_widgets("st_hire", "fin_target")


def cb_release() -> None:
    staff_id = st.session_state.get("st_release")
    with session_scope() as db:
        cm = manager(db)
        member = db.get(Staff, staff_id) if staff_id is not None else None
        if member is None:
            return
        try:
            cm.release_staff(cm.user_team, member)
            flash("staff", "success", f"{member.name} gönderildi; havuzda {format_money(member.wage)}/hafta boşaldı.")
        except TransferError as exc:
            flash("staff", "error", str(exc))
    reset_widgets("st_release", "fin_target")


# ===========================================================================
# KENAR CUBUGU
# ===========================================================================

def sidebar(teams: list[str]) -> None:
    with st.sidebar:
        st.header("Kariyer")
        show_flash("sidebar")
        with session_scope() as db:
            cm = manager(db)
            team = cm.user_team
            current = team.name if team else None
            total = cm.total_weeks()
            st.caption(f"Sezon {cm.season} · Hafta {min(cm.current_week, total)} / {total}"
                       + (" · sezon bitti" if cm.season_finished else ""))
            rep = cm.manager_reputation
            st.caption(f"Menajer tanınırlığı {rep:.1f}/20 · {reputation.label(rep)}")
            if team is not None:
                st.caption(f"Transfer {format_money(team.transfer_budget)} · "
                           f"maaş havuzu {format_money(team.wage_budget)}/hf")

        index = teams.index(current) if current in teams else 0
        chosen = st.selectbox("Takımın", teams, index=index, key="sb_team")
        st.button("Takımı ayarla", key="sb_set_team", on_click=cb_set_team,
                  disabled=chosen == current, use_container_width=True)
        with st.expander("Gelişmiş"):
            st.text_input("Kariyer tohumu (boş = rastgele)", key="career_seed",
                          help="Aynı tohum aynı sonuçları üretir (test ve tekrar için).")


# ===========================================================================
# SEKME: KADRO & TAKTIK
# ===========================================================================

def squad_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("squad")
    rows = cv.squad_rows(team, cm.current_week)
    names = list(FORMATIONS)

    c1, c2, c3 = st.columns([2, 1, 1])
    if st.session_state.get("tac_formation") not in names:
        reset_widgets("tac_formation")
    c1.selectbox("Diziliş", names, index=names.index(team.formation), key="tac_formation",
                 on_change=cb_set_formation)
    c2.button("🤖 Asistana bırak", key="tac_auto", on_click=cb_auto_lineup, use_container_width=True)
    c3.button("🧹 Kadroyu temizle", key="tac_clear", on_click=cb_clear_lineup, use_container_width=True)

    check = cm.lineup_check(team)
    for err in check.errors:
        st.error(err)
    for warning in check.warnings:
        st.warning(warning)

    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown("#### Taktik tahtası")
        xi, _bench, _out = cm.lineup_of(team)
        slots = [
            (role, p.name if p else None, p.overall_rating if p else None,
             getattr(p, "condition", None) if p else None)
            for role, p in arrange_slots(team.players, team.formation, xi)
        ]
        st.markdown(pitch.lineup_svg(slots, team.name, formation_label=team.formation), unsafe_allow_html=True)
    with right:
        st.markdown("#### Kadro durumu")
        st.markdown(squad_table_html(rows), unsafe_allow_html=True)

    st.markdown("#### Kadro seçimi")
    st.caption("Durum ve Slot hücrelerine tıklayarak ilk 11'i ve kulübeyi belirle, sonra kaydet. "
               "Sakat/cezalı oyuncular kaydedilirken reddedilir.")
    frame = pd.DataFrame([
        {
            "id": r.id, "Oyuncu": r.name, "Mv": r.position, "OVR": r.overall, "Form": r.form,
            "Moral": r.morale, "Kondisyon": r.condition, "Durum": r.status, "Slot": r.slot,
            "Not": r.unavailable or ("Kondisyon düşük" if r.low_condition else ""),
        }
        for r in rows
    ])
    edited = st.data_editor(
        frame,
        key="tac_editor",
        hide_index=True,
        use_container_width=True,
        column_order=["Oyuncu", "Mv", "OVR", "Form", "Moral", "Kondisyon", "Durum", "Slot", "Not"],
        disabled=["id", "Oyuncu", "Mv", "OVR", "Form", "Moral", "Kondisyon", "Not"],
        column_config={
            "Kondisyon": st.column_config.ProgressColumn("Kondisyon", min_value=0, max_value=100, format="%d%%"),
            "Durum": st.column_config.SelectboxColumn("Durum", options=list(cv.STATUS_LABELS.values()), required=True),
            "Slot": st.column_config.SelectboxColumn("Slot", options=POSITIONS),
        },
    )
    st.session_state["tac_rows"] = edited.to_dict("records")
    st.button("💾 Kadroyu kaydet", key="tac_save", on_click=cb_save_lineup, type="primary")


# ===========================================================================
# SEKME: FINANS
# ===========================================================================

def finance_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("finance")
    summary = cm.wage_summary(team)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Transfer bütçesi (EUR)", money(team.transfer_budget))
    m2.metric("Maaş havuzu / hf (EUR)", money(team.wage_budget))
    m3.metric("Maaş yükü / hf (EUR)", money(summary.total),
              help=f"Oyuncular {format_money(summary.player_wages)} + personel {format_money(summary.staff_wages)}")
    m4.metric("Boş alan / hf (EUR)", money(summary.free))

    st.markdown(usage_bar_html(summary.usage_pct), unsafe_allow_html=True)
    st.caption(f"Maaş havuzu %{summary.usage_pct:.0f} dolu")
    if summary.overspending:
        st.error(f"🔴 Maaş bütçesi {format_money(-summary.free)}/hafta aşılıyor! "
                 f"Fark her hafta transfer bütçesinden düşülecek.")

    st.markdown("#### Bütçe kaydırıcı")
    st.caption("Haftalık maaş havuzunu kaydır: 1 EUR haftalık alan = 52 EUR bonservis.")
    low, high = wage_budget_bounds(team.transfer_budget, team.wage_budget, team.wage_bill)
    if high <= low:
        st.info("Kaydırılabilecek bütçe yok (transfer kasası boş ve havuz maaş yüküne eşit).")
        return
    current = st.session_state.get("fin_target")
    if current is not None and not low <= current <= high:
        reset_widgets("fin_target")
    target = st.slider("Haftalık maaş havuzu (EUR)", min_value=int(low), max_value=int(high),
                       value=int(team.wage_budget), step=100, key="fin_target")

    preview = preview_budget_shift(team.transfer_budget, team.wage_budget, int(target), team.wage_bill)
    p1, p2, p3 = st.columns(3)
    p1.metric("Haftalık değişim (EUR)", money(preview.weekly_delta))
    p2.metric("Transfer bütçesine etkisi (EUR)", money(preview.transfer_impact))
    p3.metric("Yeni transfer bütçesi (EUR)", money(preview.new_transfer_budget))
    if not preview.valid:
        st.error(preview.message)
    st.button("✅ Bütçeyi uygula", key="fin_apply", on_click=cb_apply_budget, type="primary",
              disabled=not (preview.changed and preview.valid))

    st.markdown("#### En yüksek maaşlar")
    top = sorted(team.players, key=lambda p: -p.current_wage)[:8]
    st.dataframe(pd.DataFrame([
        {"Oyuncu": p.name, "Mv": p.position.value, "Rol": ROLE_LABELS[p.squad_role],
         "Maaş/hf": format_money(p.current_wage), "Sözleşme": f"{p.contract_years} yıl"}
        for p in top
    ]), hide_index=True, use_container_width=True)


# ===========================================================================
# SEKME: TRANSFER PAZARI
# ===========================================================================

def transfer_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("market")
    scout = team.best_staff(StaffRole.SCOUT, "judging_ability")
    margin = cm.scout_margin(team)
    st.caption(
        (f"Gözlemci: {scout.name} (Yetenek Değerlendirme {scout.judging_ability}) · " if scout else "Gözlemci yok · ")
        + f"yanılma payı ±{margin} · transfer bütçesi {format_money(team.transfer_budget)} · "
          f"boş maaş alanı {format_money(team.free_wage)}/hf"
    )

    f1, f2, f3, f4, f5 = st.columns([2, 2, 1, 2, 1])
    name = f1.text_input("İsim", key="mkt_name")
    positions = f2.multiselect("Mevki", POSITIONS, key="mkt_pos")
    max_age = f3.number_input("En fazla yaş", min_value=16, max_value=45, value=40, key="mkt_age")
    min_ovr = f4.slider("Tahmini genel güç ≥", min_value=40, max_value=99, value=60, key="mkt_ovr")
    max_value_m = f5.number_input("Değer ≤ (M)", min_value=0, max_value=1000, value=0, key="mkt_value",
                                  help="0 = sınırsız")
    rows = cv.market_rows(cm, team, cv.MarketFilter(
        name=name, positions=set(positions), max_age=int(max_age), min_estimated_overall=int(min_ovr),
        max_estimated_value=int(max_value_m) * 1_000_000 if max_value_m else None,
    ))

    if not rows:
        st.info("Filtrelere uyan oyuncu yok.")
    else:
        st.dataframe(pd.DataFrame([
            {"Oyuncu": r.name, "Kulüp": r.club, "Mv": r.position, "Yaş": r.age,
             "Genel (tahmin)": r.overall_text, "Değer (tahmin)": r.value_text, "Sözleşme": f"{r.contract_years} yıl"}
            for r in rows
        ]), hide_index=True, use_container_width=True)

        by_id = {r.id: r for r in rows}
        if st.session_state.get("mkt_target") not in by_id:
            reset_widgets("mkt_target")
        target_id = st.selectbox("Hedef oyuncu", list(by_id), format_func=lambda i: by_id[i].label(),
                                 key="mkt_target")
        target_row = by_id[target_id]
        if st.session_state.get("mkt_fee_for") != target_id:
            reset_widgets("mkt_fee")
            st.session_state["mkt_fee_for"] = target_id

        left, right = st.columns([3, 2], gap="large")
        with left:
            st.markdown(f"#### Gözlemci raporu — {target_row.name}")
            st.caption(f"{target_row.club} · {target_row.position} · {target_row.age} yaş · "
                       f"değer {target_row.value_text}")
            st.dataframe(pd.DataFrame(cv.scouted_profile_rows(cm, team, db.get(Player, target_id))),
                         hide_index=True, use_container_width=True)
        with right:
            st.markdown("#### Bonservis teklifi")
            st.number_input("Teklif (EUR)", min_value=0, step=500_000,
                            value=cv.suggested_opening_fee(target_row), key="mkt_fee")
            st.button("💶 Bonservis teklifi yap", key="mkt_offer", on_click=cb_offer_fee, type="primary",
                      disabled="neg" in st.session_state and st.session_state["neg"]["negotiation"].open)

    negotiation_panel(team)


def negotiation_panel(team: Team) -> None:
    neg = st.session_state.get("neg")
    if not neg:
        return
    negotiation = neg["negotiation"]
    st.divider()
    st.subheader(f"🤝 Sözleşme masası — {negotiation.player.name}")
    interest = negotiation.interest
    i1, i2, i3 = st.columns(3)
    i1.metric("Prestij (kulüp %40 + menajer %30)", f"{interest.prestige:.1f}")
    i2.metric("Oyuncunun beklentisi", f"{interest.required_prestige:.1f}")
    i3.metric("Bonservis (EUR)", money(neg["fee"]))
    st.markdown(negotiation_log_html(neg["log"]), unsafe_allow_html=True)

    if negotiation.open:
        demand = negotiation.demand
        st.caption(f"Güncel talep: {demand.describe()} · kalan pazarlık hakkı {negotiation.rounds_left}")
        c1, c2, c3 = st.columns(3)
        wage = c1.number_input("Haftalık maaş (EUR)", min_value=0, step=1_000, key="neg_wage")
        years = c2.number_input("Sözleşme (yıl)", min_value=1, max_value=5, key="neg_years")
        roles = [r.value for r in ROLE_LABELS]
        role_value = c3.selectbox("Kadro rolü", roles, key="neg_role",
                                  format_func=lambda v: ROLE_LABELS[SquadRole(v)])
        offer = ContractOffer(int(wage), int(years), SquadRole(role_value))
        score = negotiation.persuasion(offer)
        required = negotiation.required_persuasion
        st.progress(min(1.0, score / 100), text=f"İkna skoru {score:.1f} / gereken {required:.1f}")
        b1, b2, b3 = st.columns(3)
        b1.button("📨 Teklifi sun", key="neg_submit", on_click=cb_neg_submit, type="primary",
                  use_container_width=True)
        b2.button("✅ Talebi kabul et", key="neg_accept", on_click=cb_neg_accept, use_container_width=True)
        b3.button("🚪 Masadan kalk", key="neg_leave", on_click=cb_neg_leave, use_container_width=True)
    elif neg.get("needs_room"):
        needed = int(neg["needs_room"])
        st.warning(f"Anlaşma sağlandı ama maaş havuzunda {format_money(needed)}/hafta yer yok. "
                   f"Kaydırma maliyeti: {format_money(needed * 52)} bonservis bütçesi.")
        affordable = team.transfer_budget >= needed * 52 + neg["fee"]
        b1, b2 = st.columns(2)
        b1.button("💱 Maaş alanı aç ve imzala", key="neg_shift_sign", on_click=cb_neg_shift_sign,
                  type="primary", disabled=not affordable, use_container_width=True)
        b2.button("Vazgeç", key="neg_leave", on_click=cb_neg_leave, use_container_width=True)
        if not affordable:
            st.error("Transfer bütçesi hem bonservisi hem maaş kaydırmasını karşılamıyor.")
    else:
        st.button("Masayı kapat", key="neg_leave", on_click=cb_neg_leave)


# ===========================================================================
# SEKME: LIG
# ===========================================================================

def league_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("league")
    total = cm.total_weeks()
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Sezon", cm.season)
    m2.metric("Hafta", f"{min(cm.current_week, total)} / {total}")
    m3.metric("Sıra", f"{cm.position_of(team)}.")
    m4.metric("Puan", team.points)
    m5.metric("Menajer tanınırlığı", f"{cm.manager_reputation:.1f}/20", help=reputation.label(cm.manager_reputation))

    nxt = cm.next_fixture(team.id)
    if nxt is not None:
        home = nxt.home_team_id == team.id
        opponent = nxt.away_team if home else nxt.home_team
        st.caption(f"Yaklaşan maç: {nxt.week}. hafta · {team.name} vs {opponent.name} "
                   f"({'ev' if home else 'deplasman'}) · rakip formu {cm.team_form(opponent.id) or '-'}")

    b1, b2 = st.columns(2)
    b1.button("⏭️ Sonraki haftayı oyna", key="lg_play", on_click=cb_play_week, type="primary",
              disabled=cm.season_finished, use_container_width=True)
    if cm.season_finished:
        b2.button("🆕 Yeni sezonu başlat", key="lg_new_season", on_click=cb_new_season, use_container_width=True)

    lines = st.session_state.get("last_week_lines")
    if lines:
        with st.expander("Son haftanın raporu", expanded=True):
            for kind, text in lines:
                (st.success if kind == "season" else st.markdown)(text if kind != "result" else f"- {text}")
        if st.session_state.get("last_user_result") is not None:
            st.caption("Maçını **🏟️ Canlı Maç** sekmesinde *Son maçımı izle* ile 2D sahada izleyebilirsin.")

    leagues = cm.leagues()
    ids = [lg.id for lg in leagues]
    names = {lg.id: lg.name for lg in leagues}
    default = ids.index(team.league_id) if team.league_id in ids else 0
    league_id = st.selectbox("Lig", ids, index=default, format_func=lambda i: names[i], key="lg_league")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("#### Puan durumu")
        st.dataframe(pd.DataFrame(cv.standings_rows(cm, league_id, team.id)), hide_index=True,
                     use_container_width=True)
    with right:
        st.markdown("#### Gol krallığı")
        scorers = cv.scorer_rows(cm, league_id)
        if scorers:
            st.dataframe(pd.DataFrame(scorers), hide_index=True, use_container_width=True)
        else:
            st.caption("Henüz gol atılmadı.")

    week = cm.last_played_week()
    results = [r for r in cv.result_rows(cm, week) if r["Lig"] == names[league_id]]
    if results:
        st.markdown(f"#### {week}. hafta sonuçları")
        st.dataframe(pd.DataFrame(results), hide_index=True, use_container_width=True)


# ===========================================================================
# SEKME: TEKNIK HEYET
# ===========================================================================

def staff_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("staff")
    effects = cv.staff_effects(cm, team)
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Sakatlık süresi", f"×{effects.injury_multiplier:.2f}",
              help=f"4 haftalık sakatlık → {effects.four_week_injury} hafta")
    e2.metric("Kondisyon toparlanma", f"%{effects.recovery_rate * 100:.0f}",
              help="Maçta harcanan kondisyonun hafta içinde geri kazanılan payı")
    e3.metric("Gözlemci yanılma payı", f"±{effects.scout_margin}")
    e4.metric("Form çarpanı (hücum/savunma)", f"×{effects.attack_training:.2f} / ×{effects.defense_training:.2f}")

    st.markdown("#### Kadro")
    rows = cv.staff_rows(team.staff)
    if rows:
        st.dataframe(pd.DataFrame(rows).drop(columns=["id"]), hide_index=True, use_container_width=True)
    else:
        st.caption("Teknik heyet boş.")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### İşe al")
        pool = cm.free_agent_staff()
        if pool:
            labels = {m.id: f"{m.name} · {staff_rules.ROLE_LABELS[m.role]} · itibar {m.reputation} · "
                            f"{format_money(m.wage)}/hf" for m in pool}
            if st.session_state.get("st_hire") not in labels:
                reset_widgets("st_hire")
            st.selectbox("Boştaki personel", list(labels), format_func=labels.get, key="st_hire")
            st.button("İşe al", key="st_hire_btn", on_click=cb_hire, type="primary")
        else:
            st.caption("Boşta personel yok.")
    with right:
        st.markdown("#### Gönder")
        if team.staff:
            labels = {m.id: f"{m.name} · {staff_rules.ROLE_LABELS[m.role]} · {format_money(m.wage)}/hf"
                      for m in team.staff}
            if st.session_state.get("st_release") not in labels:
                reset_widgets("st_release")
            st.selectbox("Personel", list(labels), format_func=labels.get, key="st_release")
            st.button("Gönder", key="st_release_btn", on_click=cb_release)


# ===========================================================================
# SEKME: CANLI MAC
# ===========================================================================

def energy_html(home: str, away: str, home_energy: int | None, away_energy: int | None) -> str:
    def cell(name, value):
        band = condition_band(value) if value is not None else None
        return f"<div style='margin:.2rem 0'><small>{escape(name)}</small>{condition_bar_html(value, band)}</div>"

    return "<div><b>Kondisyon (sahadakiler ort.)</b>" + cell(home, home_energy) + cell(away, away_energy) + "</div>"


def play_live(result, delay: float, tempo: float, show_pitch: bool) -> None:
    frames = build_timeline(result)
    scenes = pitch.build_scenes(result, frames) if show_pitch else []
    home, away = result.home.name, result.away.name

    board = st.empty()
    banner = st.empty()
    progress = st.progress(0.0, text="Başlama düdüğü bekleniyor")
    pitch_col, stats_col = st.columns([3, 2], gap="large")
    with pitch_col:
        pitch_slot = st.empty()
    with stats_col:
        st.markdown("#### İstatistikler")
        stats_slot = st.empty()
        energy_slot = st.empty()
    st.markdown("#### Maç akışı")
    feed_slot = st.empty()

    total = max(1, result.total_minutes)
    for i, frame in enumerate(frames):
        flash_kind = frame.event.highlight if frame.event.highlight in {"goal", "red"} else None
        board.markdown(scoreboard_html(home, away, frame, flash_kind), unsafe_allow_html=True)
        if flash_kind:
            banner.markdown(banner_html(frame), unsafe_allow_html=True)
        elif frame.event.highlight not in {"injury"}:
            banner.empty()
        progress.progress(min(1.0, frame.elapsed / total), text=f"{frame.display_minute} · {frame.phase}")
        if show_pitch:
            previous = scenes[i - 1] if i else None
            pitch_slot.markdown(pitch.scene_svg(scenes[i], previous, tempo=tempo), unsafe_allow_html=True)
        stats_slot.markdown(stats_html(home, away, frame.home, frame.away), unsafe_allow_html=True)
        energy_slot.markdown(
            energy_html(home, away, team_energy_at(result.home, frame.minute), team_energy_at(result.away, frame.minute)),
            unsafe_allow_html=True,
        )
        feed_slot.markdown(feed_html(frames[: i + 1]), unsafe_allow_html=True)
        if delay:
            time.sleep(delay * frame.pacing)

    summary = summarize(result, frames)
    stats_slot.markdown(
        stats_html(home, away, summary.home_stats, summary.away_stats,
                   (summary.possession_home, summary.possession_away)),
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown("### Maç sonu")
    for line in summary_lines(summary):
        st.markdown(line)


def live_tab(teams: list[str]) -> None:
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    mode = c1.radio("Mod", LIVE_MODES, key="live_mode", horizontal=True)
    speed = c2.select_slider("Maç hızı", options=list(SPEEDS), value="Normal", key="live_speed")
    tempo = c3.slider("Animasyon temposu", min_value=0.5, max_value=2.0, value=1.0, step=0.25, key="live_tempo",
                      help="Saha animasyonlarının süresi: 0.5 hızlı, 2.0 ağır çekim.")
    show_pitch = c4.toggle("2D saha", value=True, key="live_pitch")

    if mode == "Hazırlık maçı":
        h1, h2, h3 = st.columns([2, 2, 1])
        home = h1.selectbox("Ev sahibi", teams, index=0, key="live_home")
        away = h2.selectbox("Deplasman", teams, index=1 if len(teams) > 1 else 0, key="live_away")
        seed_text = h3.text_input("Tohum", value="", key="live_seed")
    start = st.button("▶ Maçı başlat", key="live_start", type="primary")

    if not start:
        if mode == "Hazırlık maçı":
            st.markdown(scoreboard_html(home, away, None), unsafe_allow_html=True)
        st.info("Ayarları seç ve **Maçı başlat**'a bas.")
        return

    delay = SPEEDS[speed]
    if mode == "Hazırlık maçı":
        if home == away:
            st.error("Bir takım kendisiyle oynayamaz.")
            return
        seed = parse_seed(seed_text)
        with session_scope() as db:
            result = simulate_friendly(db, home, away, seed=seed)
        st.caption("Hazırlık maçı — veritabanına kaydedilmez.")
        play_live(result, delay, tempo, show_pitch)
        return

    result = st.session_state.get("last_user_result")
    if result is None:
        st.warning("Henüz izlenecek maç yok. **🏆 Lig** sekmesinden haftayı oyna.")
        return
    play_live(result, delay, tempo, show_pitch)


# ===========================================================================
# GIRIS
# ===========================================================================

def main() -> None:
    st.set_page_config(page_title="CM Menajer Paneli", page_icon="⚽", layout="wide")
    st.markdown(CSS + pitch.PITCH_CSS, unsafe_allow_html=True)
    st.title("⚽ CM — Menajer Paneli")

    if not wait_for_db(retries=2, delay=0.5, verbose=False):
        st.error("Veritabanına bağlanılamadı. `docker compose up -d` çalışıyor mu?")
        st.stop()
    problems = schema_problems()
    if problems:
        st.error("Veritabanı şeması bu sürümden eski (" + ", ".join(problems[:4]) + "). "
                 "`python seed.py` ile yeniden kur — kariyer kaydı sıfırlanır.")
        st.stop()
    teams = load_teams()
    if len(teams) < 2:
        st.warning("Veritabanında takım yok. Önce `python seed.py` çalıştır.")
        st.stop()

    sidebar(teams)
    tabs = st.tabs(TABS)

    # Yonetim sekmeleri once cizilir; canli mac dongusu en sonda (bkz. dosya basligi)
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        for index, render in ((1, squad_tab), (2, finance_tab), (3, transfer_tab), (4, league_tab), (5, staff_tab)):
            with tabs[index]:
                if team is None:
                    st.info("Önce kenar çubuğundan takımını seç ve **Takımı ayarla**'ya bas.")
                    continue
                render(db, cm, team)

    with tabs[0]:
        live_tab(teams)


if __name__ == "__main__":
    main()
