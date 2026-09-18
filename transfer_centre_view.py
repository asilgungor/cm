"""
transfer_centre_view.py
=======================
Faz 13I: Transfer Merkezi (sol menude "🔄 Transfer Merkezi"). 13H transfer masasinin (transfer_desk.TransferDesk)
arayuzu + eski Transfer Pazari'nin arama / takip listesi / menajerler arasi teklif dali. Kurallar ve para masada
(FLUSH eder, commit etmez); bu modul SUNUM + callback: her callback web_common.member_callback ile sarilir, kulup her
zaman cm.user_team (masa dosya sahipligini human_team_id ile dogrular), widget'tan gelen id yalnizca hedef secer.

UST SERIT      donem (acik / kapali + etiket), transfer butcesi, bos maas alani, acik dosyalar, gelen teklifler,
               planli taksit borcu, gecikmis borc (finance_summary). Bolum secici tc_section (sayaclar etikette).
BOLUMLER
  🔎 Oyuncu ara     filtreler (mkt_name, mkt_pos, mkt_age, mkt_stars, mkt_value), tablo (mkt_table; Bilgi % sutunu:
                    TransferDesk.knowledge_map), hedef (mkt_target), SISLI gozlemci raporu (scout_report: bilgi %25+
                    rapor, %50+ potansiyel / isteklilik / serbest kalma bedeli, %75+ sakatlik egilimi / sozlesme),
                    "🔭 Gozlemci gonder" (tc_scout), "💼 Kulube sor ve teklif hazirla" / "📂 Dosyayi ac" (mkt_offer:
                    eski "Bonservis teklifi" dugmesi masaya yonlendirildi), takip listesi (mkt_shortlist, sl_*).
                    Menajer kulubundeki oyuncu: market_view.human_offer_panel (insan <-> insan market_hub'da kalir).
  📂 Dosyalarım     acik / kapanmis IN dosyalari (tc_open_{id}); acik dosya (tc_deal):
                    teklif kurucu  kulubun tutumu, sisli fiyat araligi, bonservis (tc_fee), pesinat % (tc_pct), taksit
                                   suresi (tc_months: 6/12/24/36 ay), ek odemeler (tc_ao_*), sonraki satis payi
                                   (tc_sell_on), takas oyuncusu (tc_ex), gonder (tc_bid), serbest kalma bedeli (tc_release)
                    muzakere       masadaki paket, kulubun istedigi degisiklikler, sabir ETIKETI, son gecerlilik, gecmis;
                                   karsi teklifi kabul (tc_accept_counter), revize (teklif kurucu), cekil (tc_withdraw)
                    kisisel sartlar sozlesme masasi (tc_terms_open), maas / sure / rol / imza primi / sadakat primi /
                                   menajer ucreti / serbest kalma / mac ve gol primi (tc_t_*), tc_t_submit, tc_t_accept;
                                   ruh hali etiketi, kalan pazarlik hakki, negotiation_log_html konusma gecmisi
                    saglik         riskli sonuc: tc_med_go / tc_med_stop
                    tamamlama      donem aciksa tc_complete (+ maas alani kaydirma tc_shift), kapaliysa "donem acilinca
                                   tamamlanacak"
  📥 Gelen teklifler AI kuluplerinin oyuncularima teklifleri: tc_in_accept_{id}, tc_in_reject_{id} (+ tc_in_reason_{id}),
                    karsi teklif tc_in_fee_{id} / tc_in_pct_{id} / tc_in_months_{id} / tc_in_sell_{id} / tc_in_counter_{id}
  🏷️ Oyuncularım    liste bayraklari ve istenen bedel: tc_my_pick, tc_list_transfer, tc_list_loan, tc_ask, tc_ask_save,
                    tc_ask_clear
  💳 Ödemeler       odeme defteri (taksit, ek odeme, prim, pay, gecikme): tc_pay_scope

K12: kulubun hedef / taban bedeli, sabir sayisi, ikna skoru, AI alicinin ust siniri ve gizli ozellikler EKRANA
SAYI olarak cikmaz; masanin verdigi etiketler (tutum, sabir, ruh hali, isteklilik, sakatlik egilimi) ve sisli
araliklar gosterilir. Kulubun soyledigi fiyat araligi (bilgi alma) zaten sislidir.
Sorgu butcesi: listeler TEK sorgulu DealSummary satirlaridir; tam DealView yalnizca acik dosya icin okunur.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

import career_views as cv
import market_view
import nav_view
import player_view as pv
import transfer_rules as rules
from database import session_scope
from finance import BudgetError, format_money, weekly_to_transfer
from models import GameMode, Player, SquadRole, StaffRole
from ofm_theme import panel_title_html, stat_strip_html
from stars import FILTER_OPTIONS, star_range, star_threshold, stars
from transfer_desk import IN, OPEN, OUT, DeskError, TransferDesk
from transfer_rules import AddOn, DealTerms
from transfers import (
    MAX_YEARS,
    MIN_YEARS,
    ROLE_LABELS,
    ContractOffer,
    NegotiationStatus,
    TransferError,
)
from web_common import (
    flash,
    live_fixture_pending,
    manager,
    md_escape,
    member_callback,
    requires_auth,
    reset_widgets,
    show_flash,
)
from web_view import negotiation_log_html

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Team
    from transfer_desk import DealSummary, DealView

AREA = "transfer"
MARKET_AREA = "market"
SECTION_KEY = "tc_section"
DEAL_KEY = "tc_deal"
BID_SIG_KEY = "tc_bid_for"
TERMS_SIG_KEY = "tc_terms_for"
SEC_SEARCH, SEC_FILES, SEC_INCOMING = "🔎 Oyuncu ara", "📂 Dosyalarım", "📥 Gelen teklifler"
SEC_MINE, SEC_PAYMENTS = "🏷️ Oyuncularım", "💳 Ödemeler"
SECTIONS = [SEC_SEARCH, SEC_FILES, SEC_INCOMING, SEC_MINE, SEC_PAYMENTS]
POSITIONS = ["GK", "DEF", "MID", "FWD"]
STAR_FILTER_LABELS = ["Tümü"] + [label for label, _ in FILTER_OPTIONS]
FEE_STEP = 250_000
MONTH_OPTIONS = list(rules.INSTALMENT_MONTHS)
NO_EXCHANGE = 0
# (tur, esik anahtari, tutar anahtari, etiket, varsayilan esik)
ADDON_FIELDS = (
    (rules.ADD_ON_APPEARANCES, "tc_ao_apps_n", "tc_ao_apps_amt", "Resmi maç", 20),
    (rules.ADD_ON_GOALS, "tc_ao_goals_n", "tc_ao_goals_amt", "Gol", 10),
    (rules.ADD_ON_LEAGUE_TITLE, None, "tc_ao_league_amt", "Lig şampiyonluğu", None),
    (rules.ADD_ON_CUP_TITLE, None, "tc_ao_cup_amt", "Devler Arenası şampiyonluğu", None),
)
BID_WIDGETS = ("tc_fee", "tc_pct", "tc_months", "tc_sell_on", "tc_ex",
               *(k for _, n, a, _, _ in ADDON_FIELDS for k in (n, a) if k))
TERMS_WIDGETS = ("tc_t_wage", "tc_t_years", "tc_t_role", "tc_t_sign", "tc_t_loyal", "tc_t_agent", "tc_t_release",
                 "tc_t_app", "tc_t_goal")
PAY_SCOPES = {"Açık ödemeler": "OPEN", "Tümü": "ALL", "Ödeyeceklerim": "PAY", "Alacaklarım": "RECEIVE"}
TURN_TEXT = {"MANAGER": "🟢 sıra sende", "CLUB": "⏳ karşı taraf değerlendiriyor"}
ROLE_OPTIONS = [r.value for r in ROLE_LABELS]
LIVE_TEXT = "🏟️ Canlı maçın sürüyor: transfer işlemleri maç kaydedilene kadar kapalı."
TOURNAMENT_TEXT = "Turnuva modunda transfer yapılmaz."


# ===========================================================================
# METIN YARDIMCILARI
# ===========================================================================

def months_label(months: int) -> str:
    return "Taksit yok (tamamı peşin)" if not months else f"{months} ay · {rules.instalment_count(months)} taksit"


def expires_text(weeks: int | None) -> str:
    if weeks is None:
        return ""
    return "bu hafta düşer" if weeks == 0 else f"{weeks} hafta içinde düşer"


def due_text(row) -> str:
    if row.due_in_weeks is not None:
        if row.due_in_weeks == 0:
            return "bu hafta"
        return f"{row.due_in_weeks} hafta sonra" if row.due_in_weeks > 0 else f"{-row.due_in_weeks} hafta önce"
    if row.due_season is not None:
        return f"Sezon {row.due_season} başı"
    return "—"


def round_to(amount: float, step: int = 10_000) -> int:
    return int(round(float(amount) / step) * step)


def terms_from_state(ss, prefix: str = "tc_", allow_extras: bool = True) -> DealTerms:
    """Teklif kurucunun widget degerleri -> DealTerms (dogrulama masada: rules.validate_terms)."""
    fee = max(0, int(ss.get(f"{prefix}fee") or 0))
    pct = max(0, min(100, int(ss.get(f"{prefix}pct", 100))))
    upfront = fee if pct >= 100 else min(fee, round_to(fee * pct / 100))
    months = int(ss.get(f"{prefix}months") or 0) if upfront < fee else 0
    add_ons: list[AddOn] = []
    exchange = None
    if allow_extras:
        for kind, n_key, amount_key, _label, _default in ADDON_FIELDS:
            amount = int(ss.get(amount_key) or 0)
            if amount > 0:
                add_ons.append(AddOn(kind, int(ss.get(n_key) or 1) if n_key else 1, amount))
        exchange = int(ss.get("tc_ex") or 0) or None
    sell_on = int(ss.get(f"{prefix}sell_on") or 0)
    return DealTerms(fee=fee, upfront=upfront, instalment_months=months, add_ons=tuple(add_ons), sell_on_pct=sell_on,
                     exchange_player_id=exchange)


def terms_strip(terms) -> list[tuple[str, str]]:
    """Masadaki paket (TermsView) bilgi seridi."""
    items = [("Bonservis", format_money(terms.fee)), ("Peşin", format_money(terms.upfront))]
    if terms.deferred > 0:
        items.append(("Taksit", f"{terms.instalments} × {format_money(terms.instalment_amount)} · "
                                f"{terms.instalment_months} ay"))
    if terms.add_ons_total:
        items.append(("Ek ödemeler", format_money(terms.add_ons_total)))
    if terms.sell_on_pct:
        items.append(("Sonraki satış payı", f"%{terms.sell_on_pct}"))
    if terms.exchange_player_name:
        items.append(("Takas", terms.exchange_player_name))
    return items


# ===========================================================================
# SAYFA
# ===========================================================================

def render_transfer_centre(db, cm: CareerManager, team: Team) -> None:
    """Transfer Merkezi sayfasi (nav_view.TRANSFER; baslik bandi web_app/nav_view'dan)."""
    show_flash(AREA)
    show_flash(MARKET_AREA)
    if cm.game_mode is GameMode.TOURNAMENT:
        st.info(TOURNAMENT_TEXT)
        return
    if live_fixture_pending():
        st.info(LIVE_TEXT)
        return
    desk = TransferDesk(cm)
    window = desk.window()
    summary = desk.finance_summary()
    rows = desk.summaries(limit=80)
    mine_open = [r for r in rows if r.direction == IN and r.status in OPEN]
    incoming_open = [r for r in rows if r.direction == OUT and r.status in OPEN]
    strip = [("Transfer dönemi", "Açık" if window.open else "Kapalı"),
             ("Transfer bütçesi", format_money(team.transfer_budget)),
             ("Boş maaş alanı", f"{format_money(team.free_wage)}/hf"),
             ("Açık dosyam", len(mine_open)), ("Gelen teklif", len(incoming_open)),
             ("Planlı taksit borcu", format_money(summary.payable_scheduled))]
    if summary.overdue_payable:
        strip.append(("Gecikmiş borç", format_money(summary.overdue_payable)))
    if summary.receivable_scheduled:
        strip.append(("Alacak", format_money(summary.receivable_scheduled)))
    st.markdown(stat_strip_html(strip), unsafe_allow_html=True)
    st.caption(("🟢 " if window.open else "🔒 ") + md_escape(window.label))
    if summary.overdue_payable:
        st.error(f"Gecikmiş transfer ödemelerin var ({format_money(summary.overdue_payable)}): borç kapanmadan yeni "
                 "teklif yapamazsın. Taksitler kasaya para girdikçe her hafta yeniden denenir.")
    counts = {SEC_FILES: sum(1 for r in mine_open if r.needs_action),
              SEC_INCOMING: sum(1 for r in incoming_open if r.needs_action)}
    if st.session_state.get(SECTION_KEY) not in SECTIONS:
        reset_widgets(SECTION_KEY)
    section = st.radio("Bölüm", SECTIONS, key=SECTION_KEY, horizontal=True, label_visibility="collapsed",
                       format_func=lambda s: f"{s} ({counts[s]})" if counts.get(s) else s)
    if section == SEC_SEARCH:
        search_section(db, cm, team, desk)
    elif section == SEC_FILES:
        files_section(db, cm, team, desk, [r for r in rows if r.direction == IN])
    elif section == SEC_INCOMING:
        incoming_section(desk, [r for r in rows if r.direction == OUT], shared=bool(cm.rules.shared))
    elif section == SEC_MINE:
        my_players_section(cm, team)
    else:
        payments_section(desk, summary)


# ---------------------------------------------------------------------------
# 🔎 Oyuncu ara (eski Transfer Pazari)
# ---------------------------------------------------------------------------

def search_section(db, cm: CareerManager, team: Team, desk: TransferDesk) -> None:
    scout = team.best_staff(StaffRole.SCOUT, "judging_ability")
    st.caption((f"Gözlemci: {md_escape(scout.name)} (Yetenek Değerlendirme {scout.judging_ability}) · " if scout
                else "Gözlemci yok · ")
               + f"yanılma payı ±{cm.scout_margin(team)} · teklif için en az %{rules.KNOWN_THRESHOLD} bilgi gerekir "
                 f"(aynı ligdeki oyuncular %{rules.SAME_LEAGUE_KNOWLEDGE} bilinir)")
    f1, f2, f3, f4, f5 = st.columns([2, 2, 1, 2, 1])
    name = f1.text_input("İsim", key="mkt_name")
    positions = f2.multiselect("Mevki", POSITIONS, key="mkt_pos", placeholder="Tümü")
    max_age = f3.number_input("En fazla yaş", min_value=16, max_value=45, value=40, key="mkt_age")
    min_label = f4.select_slider("Tahmini güç en az", options=STAR_FILTER_LABELS, value=STAR_FILTER_LABELS[5],
                                 key="mkt_stars", help="Gözlemci tahminine göre yıldız (sayısal güç gizli).")
    min_ovr = 1 if min_label == "Tümü" else star_threshold(dict(FILTER_OPTIONS)[min_label])
    max_value_m = f5.number_input("Değer ≤ (M)", min_value=0, max_value=1000, value=0, key="mkt_value",
                                  help="0 = sınırsız")
    rows = cv.market_rows(cm, team, cv.MarketFilter(
        name=name, positions=set(positions), max_age=int(max_age), min_estimated_overall=int(min_ovr),
        max_estimated_value=int(max_value_m) * 1_000_000 if max_value_m else None,
    ))
    if not rows:
        st.info("Filtrelere uyan oyuncu yok.")
        shortlist_section(db, cm, team)
        return
    known = desk.knowledge_map([r.id for r in rows])
    managers = market_view.club_managers(cm)                  # paylasilan dunyada menajer sutunu (eski: {})
    pv.selectable_table(pv.AREA_MARKET, pd.DataFrame([
        {"Oyuncu": r.name, "Kulüp": r.club, "Mv": r.position, "Yaş": r.age, "Bilgi": f"%{known.get(r.id, 0)}",
         "Güç (tahmin)": r.stars_text, "Değer (tahmin)": r.value_text, "Sözleşme": f"{r.contract_years} yıl",
         **({"Menajer": managers.get(r.club, "Yapay zekâ")} if managers else {})}
        for r in rows
    ]), [r.id for r in rows], key="mkt_table", target_key="mkt_target")
    by_id = {r.id: r for r in rows}
    if st.session_state.get("mkt_target") not in by_id:
        reset_widgets("mkt_target")
    target_id = st.selectbox("Hedef oyuncu", list(by_id), format_func=lambda i: by_id[i].label(), key="mkt_target")
    target_row = by_id[target_id]
    left, right = st.columns([3, 2], gap="large")
    with left:
        scout_panel(desk, target_id)
        pv.inspect_button(pv.AREA_MARKET, target_id, key="pv_btn_market", label="🔎 Tam profili incele")
    with right:
        market = market_view.human_target(cm, target_id)      # paylasilan dunya: menajer kulubu mu (eski: None)
        if market is not None and market.owner_is_human:
            market_view.human_offer_panel(db, cm, team, target_id, cv.suggested_opening_fee(target_row), market)
        else:
            desk_entry_panel(cm, desk, db.get(Player, target_id), known.get(target_id, 0), market)
            if market is not None:
                market_view.ai_loan_panel(cm, team, market)
        listed = cm.is_shortlisted(target_id)
        st.button("☆ Takipten çıkar" if listed else "⭐ Takip listesine ekle", key="mkt_shortlist",
                  on_click=cb_shortlist_toggle, args=(target_id,))
    pv.profile_panel(db, cm, team, pv.AREA_MARKET)
    shortlist_section(db, cm, team)


def scout_panel(desk: TransferDesk, player_id: int) -> None:
    """Bilgi yuzdesiyle olceklenen SISLI gozlemci raporu (K12: kesin sayi yok, yildiz ve aralik)."""
    report = desk.scout_report(player_id)
    info = desk.knowledge(player_id)
    st.markdown("#### Gözlemci raporu")              # baslik buyuk harf (tema); oyuncu adi kendi yazimiyla altta
    st.markdown(f"**{md_escape(report.name)}** · " + md_escape(f"{report.team or 'Kulüpsüz'} · {report.position} · "
                                                               f"{report.age} yaş"))
    text = f"Bilgi %{info.knowledge} · {info.label}"
    if info.assigned:
        text += f" · gözlemci görevde (haftada +%{info.weekly_gain})"
    st.progress(max(0.0, min(1.0, info.knowledge / 100)), text=text)
    if info.knowledge < rules.MAX_KNOWLEDGE and not info.assigned:
        st.button("🔭 Gözlemci gönder", key="tc_scout", on_click=cb_scout, args=(player_id,),
                  help=f"Gözlemci her hafta bilgiyi yaklaşık %{info.weekly_gain} artırır; %100'de ayrıntılı rapor hazır.")
    if not report.known:
        st.info(f"Bu oyuncu hakkında rapor yok: en az %{rules.KNOWN_THRESHOLD} bilgi gerekir. Gözlemci gönder.")
        return
    labels = (("pace", "Hız"), ("shooting", "Şut"), ("passing", "Pas"), ("defending", "Defans"),
              ("dribbling", "Dribling"), ("goalkeeping", "Kalecilik"))
    table = [{"Özellik": "Genel", "Tahmin": star_range(report.overall.low, report.overall.high)}]
    table.append({"Özellik": "Potansiyel",
                  "Tahmin": star_range(*report.potential) if report.potential
                  else f"🔒 %{rules.DETAIL_THRESHOLD} bilgiyle"})
    table += [{"Özellik": label, "Tahmin": star_range(report.attributes[key].low, report.attributes[key].high)}
              for key, label in labels if key in report.attributes]
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    value = report.value
    facts = [("Değer (tahmin)", format_money(value.low) if value.exact else
              f"{format_money(value.low)} – {format_money(value.high)}"),
             ("Transfere isteği", report.interest_label)]
    if report.knowledge >= rules.DETAIL_THRESHOLD:
        facts.append(("Serbest kalma bedeli", format_money(report.release_clause) if report.release_clause else "Yok"))
    if report.injury_label:
        facts.append(("Sakatlık eğilimi", report.injury_label))
    st.markdown(stat_strip_html(facts), unsafe_allow_html=True)
    if report.contract_text:
        st.caption(md_escape(report.contract_text))
    elif report.knowledge < rules.FULL_THRESHOLD:
        st.caption(f"Sakatlık eğilimi ve sözleşme ayrıntısı %{rules.FULL_THRESHOLD} bilgiyle görünür.")


def desk_entry_panel(cm: CareerManager, desk: TransferDesk, player: Player | None, knowledge: int, market) -> None:
    """Yapay zekâ kulubundeki oyuncu: eski 'Bonservis teklifi' dugmesi (mkt_offer) masaya yonlendirir."""
    st.markdown("#### 💼 Transfer masası")
    if player is None:
        return
    banned, ban_reason = cm.transfer_ban_info(player)
    if market is not None and market.block_reason and not banned:
        banned, ban_reason = True, market.block_reason                  # kiralik oyuncu / kulup korumasi
    if banned:
        st.warning(f"⛔ {md_escape(ban_reason)}")
    deal_id = desk.open_deal_for(player.id)
    if deal_id is not None:
        st.caption("Bu oyuncu için açık bir transfer dosyan var: pazarlık, sözleşme ve tamamlama dosyada.")
        st.button("📂 Dosyayı aç", key="mkt_offer", on_click=cb_open_deal, args=(int(deal_id), IN), type="primary",
                  disabled=banned)
        return
    unknown = knowledge < rules.KNOWN_THRESHOLD
    st.caption("Kulübe oyuncunun durumunu ve fiyat beklentisini sor; teklifini peşinat, taksit, bonus ve sonraki satış "
               "payıyla dosyada kur. Kulüp karşı teklif yapabilir; bonservisten sonra oyuncuyla sözleşme, sağlık "
               "kontrolü ve tamamlama gelir.")
    st.button("💼 Kulübe sor ve teklif hazırla", key="mkt_offer", on_click=cb_offer_fee, type="primary",
              disabled=banned or unknown,
              help=f"Önce gözlemci gönder: teklif için en az %{rules.KNOWN_THRESHOLD} bilgi gerekir." if unknown else None)


def shortlist_section(db, cm: CareerManager, team: Team) -> None:
    rows = cm.shortlist()
    st.markdown(f"#### ⭐ Takip listesi ({len(rows)})")
    if not rows:
        st.caption("Gözüne kestirdiğin oyuncuları hedef oyuncu panelinden takip listesine ekle.")
        return
    # K12 (13I): kulubun istedigi bedel (hedef fiyatin tabani) artik yazilmaz; gozlemcinin SISLI deger araligi
    pv.selectable_table(pv.AREA_SHORTLIST, pd.DataFrame([
        {"Oyuncu": r.name, "Kulüp": r.team_name or "Kulüpsüz", "Mv": r.position, "Yaş": r.age,
         "Değer (tahmin)": _fogged_value(cm, team, r.player),
         "Durum": r.ban_reason if r.transfer_banned else ("Akademide" if r.in_academy else "Uygun"),
         "Not": r.note or "", "Eklendi": f"S{r.added_season} H{r.added_week}"}
        for r in rows
    ]), [r.player_id for r in rows], key="sl_table", target_key="sl_pick")
    by_id = {r.player_id: f"{r.name} · {r.position} · {r.team_name or 'Kulüpsüz'}" for r in rows}
    if st.session_state.get("sl_pick") not in by_id:
        reset_widgets("sl_pick")
    pick, inspect, remove = st.columns([3, 1, 1])
    pick.selectbox("Takipteki oyuncu", list(by_id), key="sl_pick", format_func=lambda i: by_id[i],
                   label_visibility="collapsed")
    pv.inspect_button(pv.AREA_SHORTLIST, st.session_state.get("sl_pick", next(iter(by_id))),
                      key="pv_btn_shortlist", container=inspect)
    remove.button("☆ Listeden çıkar", key="sl_remove", on_click=cb_shortlist_remove, width="stretch")
    pv.profile_panel(db, cm, team, pv.AREA_SHORTLIST)


def _fogged_value(cm: CareerManager, team: Team, player: Player) -> str:
    """Gozlemcinin deger tahmini (kendi oyuncunda kesin). Saf hesap: sorgu acmaz."""
    value = cm.scouted_report(team, player)["market_value"]
    return format_money(value.low) if value.exact else f"{format_money(value.low)} – {format_money(value.high)}"


# ---------------------------------------------------------------------------
# 📂 Dosyalarim (IN)
# ---------------------------------------------------------------------------

def files_section(db, cm: CareerManager, team: Team, desk: TransferDesk, rows: list[DealSummary]) -> None:
    deal_id = st.session_state.get(DEAL_KEY)
    if deal_id is not None:
        try:
            view = desk.deal(int(deal_id))
        except (DeskError, TypeError, ValueError):
            view = None
            reset_widgets(DEAL_KEY)
        if view is not None and view.direction == IN:
            deal_file(db, cm, team, desk, view)
    open_rows = [r for r in rows if r.status in OPEN]
    closed = [r for r in rows if r.status not in OPEN]
    st.markdown(panel_title_html(f"Açık dosyalar · {len(open_rows)}"), unsafe_allow_html=True)
    if not rows:
        st.info("Henüz transfer dosyan yok: 🔎 Oyuncu ara bölümünde bir hedef seç ve kulübe sor.")
        return
    if not open_rows:
        st.caption("Açık dosya yok.")
    for row in open_rows:
        deal_row(row, current=deal_id)
    if closed:
        with st.expander(f"Kapanan dosyalar ({len(closed)})"):
            for row in closed[:20]:
                deal_row(row, current=deal_id, closed=True)


def deal_row(row: DealSummary, *, current, closed: bool = False) -> None:
    with st.container(border=True):
        info, action = st.columns([4, 1])
        info.markdown(f"**{md_escape(row.player_name)}** · {md_escape(row.position)} · {row.age} yaş · "
                      f"{md_escape(row.seller_team or '—')} · {md_escape(row.status_label)}")
        parts = []
        if row.turn and not closed:
            parts.append(TURN_TEXT.get(row.turn, ""))
        if row.terms_text:
            parts.append(row.terms_text)
        if row.mood:
            parts.append(row.mood)
        if row.expires_in_weeks is not None:
            parts.append(expires_text(row.expires_in_weeks))
        if closed and row.reason:
            parts.append(row.reason)
        info.caption(md_escape(" · ".join(p for p in parts if p)))
        opened = current is not None and int(current) == row.id
        action.button("✓ Açık" if opened else "📂 Aç", key=f"tc_open_{row.id}", on_click=cb_open_deal,
                      args=(row.id, IN), disabled=opened, width="stretch",
                      type="primary" if row.needs_action and not opened else "secondary")


def deal_file(db, cm: CareerManager, team: Team, desk: TransferDesk, view: DealView) -> None:
    """Acik IN dosyasi: masadaki paket, kulubun mesaji / istekleri, asamaya gore eylemler, gecmis."""
    with st.container(border=True, key="tc_file"):
        st.markdown(nav_view.name_title_html(f"📂 {view.player_name} · {view.seller_team or '—'} → {team.name}"),
                    unsafe_allow_html=True)
        strip = [("Aşama", view.status_label), ("Kulübün tavrı", view.stance_label or "—"),
                 ("Değer (tahmin)", view.value_text), ("Oyuncunun isteği", view.interest_label or "Bilinmiyor")]
        if view.price_hint:
            strip.append(("Kulübün fiyat beklentisi", f"{format_money(view.price_hint[0])} – "
                                                      f"{format_money(view.price_hint[1])}"))
        if view.expires_in_weeks is not None:
            strip.append(("Geçerlilik", expires_text(view.expires_in_weeks)))
        st.markdown(stat_strip_html(strip), unsafe_allow_html=True)
        if view.mood:
            st.caption(f"🧭 {md_escape(view.mood)}")
        if view.club_message and view.status not in ("COMPLETED",):
            (st.error if view.status in ("REJECTED", "COLLAPSED", "VOIDED", "EXPIRED") else st.info)(
                md_escape(view.club_message))
        for demand in view.demands:
            st.warning("↔️ " + md_escape(demand))
        if view.terms is not None:
            st.markdown("**Masadaki paket:** " + md_escape(view.terms.text))
            st.markdown(stat_strip_html(terms_strip(view.terms)), unsafe_allow_html=True)
        stage_actions(db, cm, team, desk, view)
        if view.history:
            with st.expander(f"Dosya geçmişi ({len(view.history)})"):
                for line in view.history:
                    st.caption(md_escape(line))
        st.button("✖️ Dosyayı kapat", key="tc_close", on_click=cb_close_deal,
                  help="Yalnızca ekrandan kapanır; dosya açık kalır, listeden yeniden açabilirsin.")


def stage_actions(db, cm: CareerManager, team: Team, desk: TransferDesk, view: DealView) -> None:
    status = view.status
    if status in ("ENQUIRY", "BIDDING"):
        if view.turn == "CLUB":
            st.info("⏳ Kulüp teklifini değerlendiriyor: bu hafta yeterince yanıt verdi, cevap hafta ilerleyince gelir.")
        if view.can_accept_counter:
            c1, c2 = st.columns(2)
            c1.button("✅ Karşı teklifi kabul et", key="tc_accept_counter", on_click=cb_accept_counter,
                      args=(view.id,), type="primary", width="stretch",
                      help="Masadaki paket kabul edilir; ardından oyuncuyla kişisel şartlar görüşülür.")
            c2.caption("Ya da aşağıdan teklifini revize et.")
        if view.can_bid:
            bid_builder(db, cm, team, desk, view)
        if view.can_withdraw:
            st.button("🚪 Görüşmeden çekil", key="tc_withdraw", on_click=cb_withdraw, args=(view.id,),
                      help="Dosya kapanır; para hareket etmez.")
    elif status == "TERMS":
        terms_panel(desk, view)
        if view.can_withdraw:
            st.button("🚪 Transferden vazgeç", key="tc_withdraw", on_click=cb_withdraw, args=(view.id,))
    elif status == "MEDICAL":
        st.warning(f"🩺 {md_escape(view.medical_label or 'Sağlık kontrolü')}: "
                   + md_escape(" ".join(view.medical_notes) or "riskli."))
        if view.contract is not None:
            st.caption("Anlaşılan sözleşme: " + md_escape(view.contract.describe()))
        c1, c2 = st.columns(2)
        c1.button("✅ Riski al, devam et", key="tc_med_go", on_click=cb_medical, args=(view.id, True), type="primary",
                  width="stretch")
        c2.button("✖️ Transferden vazgeç", key="tc_med_stop", on_click=cb_medical, args=(view.id, False),
                  width="stretch")
    elif status == "AGREED":
        completion_panel(team, view)
    elif status == "COMPLETED":
        st.success(f"✅ Transfer tamamlandı: {md_escape(view.player_name)} artık {md_escape(team.name)} oyuncusu.")
        for line in view.add_on_progress:
            st.caption("➕ " + md_escape(line))
    else:
        st.caption(md_escape(view.reason or view.status_label))


def _bid_defaults(desk: TransferDesk, view: DealView) -> dict:
    """Teklif kurucunun baslangic degerleri: masadaki paket (karsi teklif / son teklifim) ya da sisli fiyat araligi."""
    ss_defaults: dict = {"tc_ex": NO_EXCHANGE}
    terms = view.terms
    for _kind, n_key, amount_key, _label, default_n in ADDON_FIELDS:
        if n_key:
            ss_defaults[n_key] = default_n
        ss_defaults[amount_key] = 0
    if terms is not None and terms.fee > 0:
        ss_defaults.update({"tc_fee": int(terms.fee),
                            "tc_pct": int(max(0, min(100, round(terms.upfront * 100 / terms.fee / 5) * 5))),
                            "tc_months": int(terms.instalment_months or 0), "tc_sell_on": int(terms.sell_on_pct)})
        for addon in terms.add_on_items:
            for kind, n_key, amount_key, _label, _default in ADDON_FIELDS:
                if addon.kind == kind:
                    ss_defaults[amount_key] = int(addon.amount)
                    if n_key:
                        ss_defaults[n_key] = int(addon.threshold)
        if terms.exchange_player_id:
            ss_defaults["tc_ex"] = int(terms.exchange_player_id)
        return ss_defaults
    if view.price_hint:
        fee = round_to(sum(view.price_hint) / 2, 50_000)
    else:
        value = desk.scout_report(view.player_id).value
        fee = round_to((value.low + value.high) / 2 if value is not None else 0, 50_000)
    ss_defaults.update({"tc_fee": int(fee), "tc_pct": 100, "tc_months": 0, "tc_sell_on": 0})
    return ss_defaults


def bid_builder(db, cm: CareerManager, team: Team, desk: TransferDesk, view: DealView) -> None:
    ss = st.session_state
    signature = (view.id, view.status, view.terms.text if view.terms else None, len(view.history))
    if ss.get(BID_SIG_KEY) != signature:
        reset_widgets(*BID_WIDGETS)
        for key, value in _bid_defaults(desk, view).items():
            ss[key] = value
        ss[BID_SIG_KEY] = signature
    st.markdown(panel_title_html("📝 Teklif hazırla" if view.terms is None else "📝 Teklifi revize et"),
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.number_input("Bonservis (garantili toplam, EUR)", min_value=0, step=FEE_STEP, key="tc_fee",
                    help="Peşinat + taksitler. Ek ödemeler ve sonraki satış payı bu tutarın dışındadır.")
    c2.slider("Peşinat (%)", min_value=0, max_value=100, step=5, key="tc_pct",
              help="Kalanı seçtiğin sürede çeyreklik taksitlerle ödenir; kasası zayıf kulüpler daha çok peşinat ister.")
    c3, c4 = st.columns(2)
    if ss.get("tc_months") not in MONTH_OPTIONS:
        ss["tc_months"] = 0
    c3.selectbox("Taksit süresi", MONTH_OPTIONS, key="tc_months", format_func=months_label)
    c4.slider("Sonraki satıştan pay (%)", min_value=0, max_value=rules.MAX_SELL_ON_PCT, step=5, key="tc_sell_on",
              help="Oyuncuyu ileride satarsan eski kulübüne bu pay ödenir; genç yeteneklerde kulüpler ister.")
    with st.expander("➕ Ek ödemeler (bonuslar)", expanded=bool(view.terms and view.terms.add_on_items)):
        st.caption("Koşul gerçekleşince bir kez ödenir; oyuncu kulüpten ayrılırsa düşer. Tutar 0 = madde yok.")
        for _kind, n_key, amount_key, label, _default in ADDON_FIELDS:
            a, b = st.columns(2)
            if n_key:
                a.number_input(f"{label} eşiği", min_value=1, max_value=300, step=1, key=n_key)
            else:
                a.caption(f"🏆 {label}")
            b.number_input(f"{label} bonusu (EUR)", min_value=0, step=100_000, key=amount_key)
    exchange = {NO_EXCHANGE: "Takas yok", **exchange_options(cm, team)}
    if ss.get("tc_ex") not in exchange:
        ss["tc_ex"] = NO_EXCHANGE
    st.selectbox("Takas oyuncusu (isteğe bağlı)", list(exchange), key="tc_ex", format_func=exchange.get,
                 help="Kulüp istemediği oyuncuyu değerli saymaz ve nakit ister.")
    terms = terms_from_state(ss)
    problems = rules.validate_terms(terms)
    if problems:
        st.warning(" ".join(problems[:2]))
    else:
        st.caption("Teklif: " + md_escape(terms.describe()))
    if terms.upfront_amount > int(team.transfer_budget):
        st.warning(f"Peşinat transfer bütçeni aşıyor ({format_money(team.transfer_budget)}).")
    st.button("📨 Teklifi gönder", key="tc_bid", on_click=cb_bid, args=(view.id,), type="primary",
              disabled=bool(problems))
    report = desk.scout_report(view.player_id)
    if report.release_clause:
        st.button(f"💥 Serbest kalma bedelini öde ({format_money(report.release_clause)})", key="tc_release",
                  on_click=cb_release, args=(view.id,),
                  help="Kulüp reddedemez; bedelin tamamı peşin ödenir. Oyuncuyla yine sözleşme yapılmalı.")


def exchange_options(cm: CareerManager, team: Team) -> dict[int, str]:
    """Takas adaylari: A takim oyuncularim (kiralik / akademi / yeni transfer disi). Iliskiden, sorgusuz."""
    week = int(cm.career_week)
    players = sorted((p for p in team.players if p.loan_from_team_id is None and not p.in_academy
                      and int(p.transfer_locked_until or 0) <= week), key=lambda p: (-p.overall_rating, p.id))
    return {p.id: f"{p.name} · {p.position.value} · {p.age} yaş" for p in players}


def terms_panel(desk: TransferDesk, view: DealView) -> None:
    """Kisisel sartlar: oyuncu ve menajeriyle sozlesme masasi (masa dosya gecmisinden yeniden kurulur)."""
    table = desk.terms_table(view.id)
    st.markdown(panel_title_html("✍️ Kişisel şartlar (oyuncu ve menajeri)"), unsafe_allow_html=True)
    if table is None:
        st.info("Kulüple bonservis konusunda anlaştın. Şimdi oyuncu ve menajeriyle maaş, süre, rol ve primleri görüş.")
        st.button("✍️ Sözleşme masasını aç", key="tc_terms_open", on_click=cb_terms_open, args=(view.id,),
                  type="primary")
        return
    step = table.step
    st.markdown(negotiation_log_html(table.log), unsafe_allow_html=True)
    st.markdown(stat_strip_html([("Oyuncunun ruh hâli", step.mood), ("Kalan pazarlık hakkı", step.rounds_left),
                                 ("Bonservis", format_money(view.terms.fee if view.terms else 0))]),
                unsafe_allow_html=True)
    if step.status is not NegotiationStatus.OPEN or step.demand is None:
        if step.message:
            st.error(md_escape(step.message))
        return
    ss = st.session_state
    demand = step.demand
    signature = (view.id, step.rounds_left, len(table.log))
    if ss.get(TERMS_SIG_KEY) != signature:
        reset_widgets(*TERMS_WIDGETS)
        ss.update({"tc_t_wage": int(demand.wage), "tc_t_years": int(demand.years), "tc_t_role": demand.role.value,
                   "tc_t_sign": int(demand.signing_fee), "tc_t_loyal": int(demand.loyalty_bonus),
                   "tc_t_agent": int(demand.agent_fee), "tc_t_release": int(demand.release_clause or 0),
                   "tc_t_app": int(demand.appearance_bonus), "tc_t_goal": int(demand.goal_bonus)})
        ss[TERMS_SIG_KEY] = signature
    st.caption("Güncel talep: " + md_escape(demand.describe()))
    c1, c2, c3 = st.columns(3)
    c1.number_input("Haftalık maaş (EUR)", min_value=0, step=1_000, key="tc_t_wage")
    c2.number_input("Sözleşme (yıl)", min_value=MIN_YEARS, max_value=MAX_YEARS, step=1, key="tc_t_years")
    c3.selectbox("Rol sözü (kulüpteki statü)", ROLE_OPTIONS, key="tc_t_role",
                 format_func=lambda v: pv.ROLE_PROMISE_LABELS[SquadRole(v)],
                 help="Söz sözleşmeye yazılır; oyuncu süre alamayıp ayrılmak isterse söz tutulmamış sayılır.")
    c4, c5, c6 = st.columns(3)
    c4.number_input("İmza primi (EUR)", min_value=0, step=10_000, key="tc_t_sign",
                    help="Tek sefer: daha düşük maaşı telafi eder.")
    c5.number_input("Sadakat primi (EUR / sezon)", min_value=0, step=10_000, key="tc_t_loyal")
    c6.number_input("Menajer ücreti (EUR)", min_value=0, step=10_000, key="tc_t_agent",
                    help="Talebin yarısının altı menajeri masadan kaldırabilir.")
    c7, c8, c9 = st.columns(3)
    c7.number_input("Serbest kalma bedeli (EUR, 0 = yok)", min_value=0, step=500_000, key="tc_t_release",
                    help="Bu bedeli ödeyen kulübe satış reddedilemez; makul bir madde oyuncuya güvence sayılır.")
    c8.number_input("Maç primi (EUR / resmi maç)", min_value=0, step=1_000, key="tc_t_app")
    c9.number_input("Gol primi (EUR / gol)", min_value=0, step=1_000, key="tc_t_goal")
    b1, b2 = st.columns(2)
    b1.button("📨 Teklifi sun", key="tc_t_submit", on_click=cb_terms_submit, args=(view.id,), type="primary",
              width="stretch")
    b2.button("✅ Talebi olduğu gibi kabul et", key="tc_t_accept", on_click=cb_terms_accept, args=(view.id,),
              width="stretch")


def contract_from_state(ss) -> ContractOffer:
    release = int(ss.get("tc_t_release") or 0)
    return ContractOffer(wage=int(ss.get("tc_t_wage") or 0), years=int(ss.get("tc_t_years") or MIN_YEARS),
                         role=SquadRole(ss.get("tc_t_role") or SquadRole.FIRST_TEAM.value),
                         signing_fee=int(ss.get("tc_t_sign") or 0), loyalty_bonus=int(ss.get("tc_t_loyal") or 0),
                         agent_fee=int(ss.get("tc_t_agent") or 0), release_clause=release or None,
                         appearance_bonus=int(ss.get("tc_t_app") or 0), goal_bonus=int(ss.get("tc_t_goal") or 0))


def completion_panel(team: Team, view: DealView) -> None:
    contract = view.contract
    st.success(f"🤝 {md_escape(view.player_name)} ile her konuda anlaşıldı"
               + (f": {md_escape(contract.describe())}" if contract is not None else "."))
    if view.medical_label:
        st.caption(f"🩺 {md_escape(view.medical_label)}")
    if not view.can_complete:
        st.info("⏳ " + md_escape(view.completes_text or view.window.label)
                + " Anlaşma dönem açılınca kendiliğinden tamamlanır.")
        return
    need = int(contract.wage) - int(team.free_wage) if contract is not None else 0
    upfront = view.terms.upfront if view.terms is not None else 0
    extras = (int(contract.signing_fee) + int(contract.agent_fee)) if contract is not None else 0
    st.caption(f"Tamamlanınca kasadan çıkacak: peşinat {format_money(upfront)} + imza primi ve menajer ücreti "
               f"{format_money(extras)}; taksitler takvime yazılır.")
    if need > 0:
        st.warning(f"Maaş havuzunda {format_money(need)}/hafta yer yok: kaydırma maliyeti "
                   f"{format_money(weekly_to_transfer(need))} transfer bütçesi.")
        st.checkbox("Maaş alanını transfer bütçesinden kaydır", key="tc_shift", value=True)
    st.button("✍️ Transferi tamamla", key="tc_complete", on_click=cb_complete, args=(view.id,), type="primary")


# ---------------------------------------------------------------------------
# 📥 Gelen teklifler (OUT)
# ---------------------------------------------------------------------------

def incoming_section(desk: TransferDesk, rows: list[DealSummary], *, shared: bool = False) -> None:
    open_rows = [r for r in rows if r.status in OPEN]
    closed = [r for r in rows if r.status not in OPEN]
    st.caption("Transfer dönemi açıkken yapay zekâ kulüpleri oyuncularına teklif yapar (listedekilere ve ayrılmak "
               "isteyenlere daha sık). Kabul edersen alıcı oyuncuyla sözleşme yapar; dönem açıksa transfer hemen "
               "tamamlanır.")
    if shared:
        # Insan <-> insan anlasmalari market_hub'da kalir: menajerlerin teklifleri Teklifler & Mesajlar sayfasinda
        st.button("📨 Menajerlerin teklifleri: Teklifler & Mesajlar", key="tc_goto_hub", on_click=nav_view.cb_nav,
                  args=(nav_view.INBOX,))
    if not rows:
        st.info("Oyuncuların için henüz teklif gelmedi. 🏷️ Oyuncularım bölümünden transfer listesine koyabilir ve "
                "istenen bedel belirleyebilirsin.")
        return
    if not open_rows:
        st.caption("Açık teklif yok.")
    for row in open_rows:
        incoming_card(row)
    if closed:
        with st.expander(f"Kapanan teklifler ({len(closed)})"):
            for row in closed[:20]:
                st.caption(md_escape(f"{row.buyer_team or '—'} → {row.player_name} · {row.status_label} · "
                                     f"{row.terms_text or ''}" + (f" · {row.reason}" if row.reason else "")))


def incoming_card(row: DealSummary) -> None:
    rid = row.id
    ss = st.session_state
    with st.container(border=True):
        st.markdown(f"**{md_escape(row.buyer_team or '—')}** → **{md_escape(row.player_name)}** "
                    f"({md_escape(row.position)}, {row.age} yaş) · teklif \\#{rid}")
        parts = [row.status_label, TURN_TEXT.get(row.turn or "", ""), expires_text(row.expires_in_weeks),
                 row.mood or ""]
        st.caption(md_escape(" · ".join(p for p in parts if p)))
        if row.terms_text:
            st.markdown("Şartlar: " + md_escape(row.terms_text))
        if row.status == "AGREED":
            st.info("🤝 Anlaşma tamam: transfer dönem açılınca tamamlanacak.")
            return
        if not row.can_accept_offer:
            return
        b1, b2 = st.columns(2)
        b1.button("✅ Kabul et", key=f"tc_in_accept_{rid}", on_click=cb_in_accept, args=(rid,), type="primary",
                  width="stretch", help="Alıcı oyuncuyla sözleşme yapar; dönem açıksa transfer hemen tamamlanır.")
        b2.button("✖️ Reddet", key=f"tc_in_reject_{rid}", on_click=cb_in_reject, args=(rid,), width="stretch")
        st.text_input("Ret nedeni (isteğe bağlı)", key=f"tc_in_reason_{rid}", max_chars=120)
        if row.can_counter_offer:
            with st.expander("↔️ Karşı teklif"):
                ss.setdefault(f"tc_in_fee_{rid}", int(row.fee))
                pct = round(row.upfront * 100 / row.fee / 5) * 5 if row.fee else 100
                ss.setdefault(f"tc_in_pct_{rid}", int(max(0, min(100, pct))))
                ss.setdefault(f"tc_in_months_{rid}", int(row.instalment_months))
                ss.setdefault(f"tc_in_sell_on_{rid}", int(row.sell_on_pct))
                if ss.get(f"tc_in_months_{rid}") not in MONTH_OPTIONS:
                    ss[f"tc_in_months_{rid}"] = 0
                c1, c2 = st.columns(2)
                c1.number_input("Bonservis (EUR)", min_value=0, step=FEE_STEP, key=f"tc_in_fee_{rid}")
                c2.slider("Peşinat (%)", 0, 100, step=5, key=f"tc_in_pct_{rid}")
                c3, c4 = st.columns(2)
                c3.selectbox("Taksit süresi", MONTH_OPTIONS, key=f"tc_in_months_{rid}", format_func=months_label)
                c4.slider("Sonraki satıştan pay (%)", 0, rules.MAX_SELL_ON_PCT, step=5, key=f"tc_in_sell_on_{rid}")
                st.button("↔️ Karşı teklifi gönder", key=f"tc_in_counter_{rid}", on_click=cb_in_counter, args=(rid,))


# ---------------------------------------------------------------------------
# 🏷️ Oyuncularim
# ---------------------------------------------------------------------------

def my_players_section(cm: CareerManager, team: Team) -> None:
    players = sorted((p for p in team.players if not p.in_academy), key=lambda p: (-p.overall_rating, p.id))
    st.caption("Transfer listesindeki ve ayrılmak isteyen oyunculara dönem açıkken daha çok teklif gelir; istenen bedel "
               "yapay zekâ tekliflerinin tabanını belirler. Serbest kalma bedelini ödeyen kulübe satış reddedilemez.")
    if not players:
        st.info("A takımda oyuncu yok.")
        return
    st.dataframe(pd.DataFrame([
        {"Oyuncu": p.name, "Mv": p.position.value, "Yaş": p.age, "Güç": stars(p.overall_rating),
         "Değer": format_money(p.market_value), "Maaş/hf": format_money(p.current_wage),
         "Sözleşme": f"{p.contract_years} yıl",
         "Liste": " · ".join(x for x in ("🏷️ Satılık" if p.transfer_listed else "",
                                          "🔁 Kiralık" if p.loan_listed else "") if x) or "—",
         "İstenen bedel": format_money(p.asking_price) if p.asking_price is not None else "—",
         "Serbest kalma": format_money(p.release_clause) if p.release_clause is not None else "—"}
        for p in players
    ]), hide_index=True, width="stretch")
    by_id = {p.id: p for p in players}
    ss = st.session_state
    if ss.get("tc_my_pick") not in by_id:
        reset_widgets("tc_my_pick", "tc_ask")
    pick = st.selectbox("Oyuncu", list(by_id), key="tc_my_pick",
                        format_func=lambda i: f"{by_id[i].name} · {by_id[i].position.value} · {by_id[i].age} yaş")
    player = by_id[pick]
    if ss.get("tc_ask_for") != pick:
        ss["tc_ask"] = int(player.asking_price if player.asking_price is not None else player.market_value or 0)
        ss["tc_ask_for"] = pick
    c1, c2 = st.columns(2)
    c1.button("✖️ Transfer listesinden çıkar" if player.transfer_listed else "🏷️ Transfer listesine koy",
              key="tc_list_transfer", on_click=cb_listing, args=(pick, "TRANSFER", not player.transfer_listed),
              width="stretch", disabled=player.loan_from_team_id is not None and not player.transfer_listed)
    c2.button("✖️ Kiralık listesinden çıkar" if player.loan_listed else "🔁 Kiralık listesine koy",
              key="tc_list_loan", on_click=cb_listing, args=(pick, "LOAN", not player.loan_listed), width="stretch",
              disabled=player.loan_from_team_id is not None and not player.loan_listed)
    a, b, c = st.columns([2, 1, 1])
    a.number_input("İstenen bedel (EUR)", min_value=0, step=FEE_STEP, key="tc_ask",
                   help="Yapay zekâ kulüpleri tekliflerini bu tabana göre yapar.")
    b.button("💾 Kaydet", key="tc_ask_save", on_click=cb_asking, args=(pick, True), width="stretch")
    c.button("Kaldır", key="tc_ask_clear", on_click=cb_asking, args=(pick, False), width="stretch",
             disabled=player.asking_price is None)


# ---------------------------------------------------------------------------
# 💳 Odemeler
# ---------------------------------------------------------------------------

def payments_section(desk: TransferDesk, summary) -> None:
    st.markdown(stat_strip_html([
        ("Ödenecek (planlı)", format_money(summary.payable_scheduled)),
        ("Alınacak (planlı)", format_money(summary.receivable_scheduled)),
        ("Gecikmiş borç", format_money(summary.overdue_payable)),
        ("Gecikmiş alacak", format_money(summary.overdue_receivable)),
    ]), unsafe_allow_html=True)
    if summary.next_payments:
        st.caption("Sıradaki ödemeler: " + md_escape(" · ".join(
            f"{p.kind_label} {format_money(p.amount - p.paid_amount)} ({due_text(p)})" for p in summary.next_payments)))
    scope = st.radio("Kapsam", list(PAY_SCOPES), key="tc_pay_scope", horizontal=True, label_visibility="collapsed")
    rows = desk.payments(PAY_SCOPES.get(scope, "OPEN"), limit=200)
    if not rows:
        st.info("Bu kapsamda ödeme yok. Taksitli bir transfer tamamlanınca taksitler burada takvime yazılır.")
        return
    st.dataframe(pd.DataFrame([
        {"Tür": r.kind_label, "Oyuncu": r.player_name or "—", "Karşı taraf": r.counterparty or "—",
         "Yön": "Ödeme" if r.direction == "PAY" else "Tahsilat", "Tutar": format_money(r.amount),
         "Ödenen": format_money(r.paid_amount), "Vade": due_text(r), "Durum": r.status_label, "Not": r.note}
        for r in rows
    ]), hide_index=True, width="stretch")


# ===========================================================================
# DOSYA ACMA (yalnizca oturum durumu)
# ===========================================================================

def open_file(deal_id: int, direction: str) -> None:
    """Transfer Merkezi'nde dosyayi one getirir: IN -> Dosyalarim (dosya acik), OUT -> Gelen teklifler."""
    ss = st.session_state
    if direction == OUT:
        ss[SECTION_KEY] = SEC_INCOMING
    else:
        ss[SECTION_KEY] = SEC_FILES
        ss[DEAL_KEY] = int(deal_id)


@requires_auth
def cb_open_deal(deal_id: int, direction: str = IN) -> None:
    """Liste / arama / ana sayfadan dosya ac (sahiplik dosya okunurken TransferDesk.deal ile denetlenir)."""
    open_file(int(deal_id), str(direction))
    nav_view.goto(nav_view.TRANSFER, scroll=False)


@requires_auth
def cb_close_deal() -> None:
    reset_widgets(DEAL_KEY, BID_SIG_KEY, TERMS_SIG_KEY)


# ===========================================================================
# CALLBACK'LER (masa islemi: tek islem; DeskError islem icinde yakalanir -> gecersiz dosya kaydi kalici)
# ===========================================================================

def _desk_call(work, area: str = AREA) -> tuple[bool, object]:
    error, result = None, None
    with session_scope() as db:
        cm = manager(db)
        try:
            result = work(TransferDesk(cm), cm)
        except (TransferError, BudgetError) as exc:            # DeskError bir TransferError
            error = str(exc)
    if error is not None:
        flash(area, "error", md_escape(error))
        return False, None
    return True, result


def _after_squad_change() -> None:
    reset_widgets("tac_editor", "tac_rows", "fin_target", "mkt_target")


@member_callback
def cb_offer_fee() -> None:
    """Eski 'Bonservis teklifi' dugmesi (mkt_offer): kulube sorulur (enquire), dosya acilir -> teklif kurucu."""
    player_id = st.session_state.get("mkt_target")
    if player_id is None:
        flash(MARKET_AREA, "error", "Önce hedef oyuncuyu seç.")
        return

    def work(desk: TransferDesk, cm):
        existing = desk.open_deal_for(int(player_id))
        if existing is not None:
            return desk.deal(existing)
        return desk.enquire(int(player_id))

    ok, view = _desk_call(work, MARKET_AREA)
    if not ok:
        return
    open_file(view.id, IN)
    flash(AREA, "info", f"💼 {md_escape(view.seller_team or '')} · {md_escape(view.player_name)}: "
                        f"{md_escape(view.club_message or view.status_label)}")


@member_callback
def cb_scout(player_id: int) -> None:
    ok, info = _desk_call(lambda desk, cm: desk.scout(int(player_id)), MARKET_AREA)
    if ok:
        flash(MARKET_AREA, "success", f"🔭 Gözlemci görevlendirildi: bilgi her hafta yaklaşık %{info.weekly_gain} artar "
                                      f"(şu an %{info.knowledge}).")


@member_callback
def cb_bid(deal_id: int) -> None:
    terms = terms_from_state(st.session_state)

    def work(desk: TransferDesk, cm):
        view = desk.deal(int(deal_id))                          # sahiplik
        return desk.make_bid(view.player_id, terms)

    ok, view = _desk_call(work)
    if not ok:
        return
    text = md_escape(view.club_message or "")
    if view.status == "TERMS":
        flash(AREA, "success", f"✅ {text} Şimdi oyuncuyla kişisel şartları görüş.")
    elif view.status == "REJECTED":
        flash(AREA, "error", f"⛔ {text}")
    elif view.turn == "CLUB":
        flash(AREA, "info", "⏳ Kulüp teklifini değerlendiriyor; yanıt hafta ilerleyince gelecek.")
    elif view.can_accept_counter:
        flash(AREA, "warning", "↔️ Kulüp karşı teklif yaptı: masadaki paketi kabul et, revize et ya da çekil.")
    else:
        flash(AREA, "error", f"✖️ {text}")


@member_callback
def cb_release(deal_id: int) -> None:
    def work(desk: TransferDesk, cm):
        view = desk.deal(int(deal_id))
        return desk.trigger_release_clause(view.player_id)

    ok, view = _desk_call(work)
    if ok:
        flash(AREA, "success", f"💥 Serbest kalma bedeli ödenecek: {md_escape(view.player_name)} ile kişisel şartları "
                               "görüş.")


@member_callback
def cb_accept_counter(deal_id: int) -> None:
    ok, view = _desk_call(lambda desk, cm: desk.accept_counter(int(deal_id)))
    if ok:
        flash(AREA, "success", f"🤝 Bonservis anlaşması tamam ({md_escape(view.player_name)}). Sözleşme masasını aç.")


@member_callback
def cb_withdraw(deal_id: int) -> None:
    ok, view = _desk_call(lambda desk, cm: desk.withdraw(int(deal_id)))
    if ok:
        flash(AREA, "info", f"🚪 {md_escape(view.player_name)} dosyasından çekildin.")
        reset_widgets(DEAL_KEY)


@member_callback
def cb_terms_open(deal_id: int) -> None:
    ok, step = _desk_call(lambda desk, cm: desk.open_terms(int(deal_id)))
    if not ok:
        return
    if step.status is NegotiationStatus.OPEN:
        flash(AREA, "success", "✍️ Sözleşme masası açıldı: oyuncu ve menajeri taleplerini açıkladı.")
    else:
        flash(AREA, "error", f"Transfer çöktü: {md_escape(step.message)}")


def _terms_result(step) -> None:
    if step.status is NegotiationStatus.ACCEPTED:
        if step.deal_status == "MEDICAL":
            flash(AREA, "warning", "🩺 Oyuncu sözleşmeyi kabul etti; sağlık kontrolü riskli çıktı: kararını ver.")
        elif step.deal_status == "COLLAPSED":
            flash(AREA, "error", "Oyuncu kabul etti ama sağlık kontrolünden kaldı: transfer çöktü.")
        else:
            flash(AREA, "success", "✅ Oyuncu sözleşmeyi kabul etti ve sağlık kontrolünden geçti.")
    elif step.status is NegotiationStatus.WALKED_AWAY:
        flash(AREA, "error", f"Transfer çöktü: {md_escape(step.message)}")
    else:
        flash(AREA, "info", md_escape(step.message))


@member_callback
def cb_terms_submit(deal_id: int) -> None:
    try:
        offer = contract_from_state(st.session_state)
    except ValueError:
        flash(AREA, "error", "Geçersiz sözleşme teklifi.")
        return
    ok, step = _desk_call(lambda desk, cm: desk.submit_terms(int(deal_id), offer))
    if ok:
        _terms_result(step)


@member_callback
def cb_terms_accept(deal_id: int) -> None:
    def work(desk: TransferDesk, cm):
        table = desk.terms_table(int(deal_id))
        if table is None or table.step.demand is None or table.step.status is not NegotiationStatus.OPEN:
            raise DeskError("Kabul edilecek bir talep yok.")
        return desk.submit_terms(int(deal_id), table.step.demand)

    ok, step = _desk_call(work)
    if ok:
        _terms_result(step)


@member_callback
def cb_medical(deal_id: int, proceed: bool) -> None:
    ok, view = _desk_call(lambda desk, cm: desk.confirm_medical(int(deal_id), bool(proceed)))
    if ok:
        flash(AREA, "success" if proceed else "info",
              "🩺 Risk kabul edildi: anlaşma tamam." if proceed else "Sağlık raporu sonrası transferden vazgeçildi.")


@member_callback
def cb_complete(deal_id: int) -> None:
    shift = bool(st.session_state.get("tc_shift", True))
    ok, view = _desk_call(lambda desk, cm: desk.complete(int(deal_id), shift_wage_room=shift))
    if ok:
        flash(AREA, "success", f"✅ TRANSFER TAMAM: {md_escape(view.player_name)} "
                               f"({md_escape(view.terms.text if view.terms else '')}).")
        _after_squad_change()


@member_callback
def cb_in_accept(deal_id: int) -> None:
    ok, view = _desk_call(lambda desk, cm: desk.accept_offer(int(deal_id)))
    if not ok:
        return
    if view.status == "COMPLETED":
        flash(AREA, "success", f"✅ TRANSFER TAMAM: {md_escape(view.player_name)} → {md_escape(view.buyer_team or '')}.")
        _after_squad_change()
    elif view.status == "AGREED":
        flash(AREA, "info", f"🤝 {md_escape(view.player_name)} için anlaşıldı; transfer dönem açılınca tamamlanacak.")
    else:
        flash(AREA, "warning", md_escape(view.reason or view.status_label))


@member_callback
def cb_in_reject(deal_id: int) -> None:
    reason = str(st.session_state.get(f"tc_in_reason_{int(deal_id)}") or "")
    ok, view = _desk_call(lambda desk, cm: desk.reject_offer(int(deal_id), reason))
    if ok:
        flash(AREA, "info", f"✖️ {md_escape(view.buyer_team or '')} teklifi reddedildi ({md_escape(view.player_name)}).")


@member_callback
def cb_in_counter(deal_id: int) -> None:
    rid = int(deal_id)
    terms = _incoming_terms(rid)
    ok, view = _desk_call(lambda desk, cm: desk.counter_offer(rid, terms))
    if not ok:
        return
    reset_widgets(*(f"tc_in_{name}_{rid}" for name in ("fee", "pct", "months", "sell_on", "reason")))
    if view.status == "COMPLETED":
        flash(AREA, "success", f"✅ Alıcı şartlarını kabul etti, TRANSFER TAMAM: {md_escape(view.player_name)}.")
        _after_squad_change()
    elif view.status == "AGREED":
        flash(AREA, "success", f"🤝 Alıcı şartlarını kabul etti ({md_escape(view.player_name)}); transfer dönem "
                               "açılınca tamamlanacak.")
    elif view.status in ("REJECTED", "COLLAPSED"):
        flash(AREA, "error", md_escape(view.club_message or view.reason or view.status_label))
    elif view.turn == "CLUB":
        flash(AREA, "info", "⏳ Alıcı karşı teklifini değerlendiriyor; yanıt hafta ilerleyince gelecek.")
    else:
        flash(AREA, "warning", f"↔️ {md_escape(view.club_message)}")


def _incoming_terms(rid: int) -> DealTerms:
    ss = st.session_state
    fee = max(0, int(ss.get(f"tc_in_fee_{rid}") or 0))
    pct = max(0, min(100, int(ss.get(f"tc_in_pct_{rid}", 100))))
    upfront = fee if pct >= 100 else min(fee, round_to(fee * pct / 100))
    months = int(ss.get(f"tc_in_months_{rid}") or 0) if upfront < fee else 0
    return DealTerms(fee=fee, upfront=upfront, instalment_months=months,
                     sell_on_pct=int(ss.get(f"tc_in_sell_on_{rid}") or 0))


@member_callback
def cb_listing(player_id: int, kind: str, listed: bool) -> None:
    kind = str(kind).upper()
    changes = {"transfer": bool(listed)} if kind == "TRANSFER" else {"loan": bool(listed)}
    ok, _ = _desk_call(lambda desk, cm: desk.set_listing(int(player_id), **changes))
    if ok:
        what = "transfer" if kind == "TRANSFER" else "kiralık"
        flash(AREA, "success", f"Oyuncu {what} listesine eklendi." if listed else f"Oyuncu {what} listesinden çıkarıldı.")


@member_callback
def cb_asking(player_id: int, save: bool) -> None:
    amount = int(st.session_state.get("tc_ask") or 0) if save else None
    ok, _ = _desk_call(lambda desk, cm: desk.set_asking_price(int(player_id), amount))
    if ok:
        flash(AREA, "success", f"İstenen bedel: {format_money(amount)}." if save else "İstenen bedel kaldırıldı.")
        reset_widgets("tc_ask_for")


# ---------------------------------------------------------------------------
# Takip listesi (web_app'tan tasindi; flash alani: market)
# ---------------------------------------------------------------------------

@member_callback
def cb_shortlist_toggle(player_id: int) -> None:
    from career_manager import ShortlistError

    with session_scope() as db:
        cm = manager(db)
        player = db.get(Player, player_id)
        if player is None or cm.user_team is None:
            return
        try:
            if cm.is_shortlisted(player_id):
                cm.shortlist_remove(player_id)
                text = f"☆ {md_escape(player.name)} takip listesinden çıkarıldı."
            else:
                cm.shortlist_add(player)
                text = f"⭐ {md_escape(player.name)} takip listesine eklendi."
        except ShortlistError as exc:
            db.rollback()
            flash(MARKET_AREA, "error", str(exc))
            return
    flash(MARKET_AREA, "success", text)


@member_callback
def cb_shortlist_remove() -> None:
    player_id = st.session_state.get("sl_pick")
    if player_id is None:
        return
    with session_scope() as db:
        manager(db).shortlist_remove(player_id)
    flash(MARKET_AREA, "success", "☆ Oyuncu takip listesinden çıkarıldı.")
    reset_widgets("sl_pick")
