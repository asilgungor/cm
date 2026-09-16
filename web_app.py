"""
web_app.py
==========
CM Menajer Paneli -- tamamen tarayici tabanli kariyer arayuzu.

    streamlit run web_app.py

Giris (10. Asama): menajer hesabi. Giris yapmayan kullanici HICBIR oyun sekmesine erisemez;
giris / kayit ekranina yonlendirilir. Oturum st.session_state["auth"] (accounts.AuthSession)
ile tutulur; her menajerin kariyeri kendi PostgreSQL semasindadir ve bu dosyanin kaydettigi
cozucu (session_career_schema) veritabani islemlerini oturumdaki kullanicinin kariyerine yonlendirir.
5 hatali denemeden sonra giris 30 sn kilitlenir. Arayuz CM retro temasindadir (cm_theme.py);
kadro, pazar ve akademi ekranlarinda guc/potansiyel sayi yerine YILDIZ gosterilir (stars.py).

Ilk giris (8. Asama): oyun modu secimi
    CAREER_MODE     : lig maratonu; Devler Arenasi takvimi lig haftalariyla senkron akar
    TOURNAMENT_MODE : sadece Devler Arenasi (Champions Cup)

Sekmeler:
    Canli Mac       : 2D saha + spiker akisi + canli istatistik ve kondisyon. Maçımı yönet (9. Asama):
                      haftanin gercek maci (hafta ici kupa, sonra lig) canli oynanir; DURDUR / DEVAM,
                      molada ve kritik olayda otomatik durma, oyuncu degisikligi (3 ya da 5 hak,
                      istege bagli pencere kurali), canli dizilis (acil durum 5-3-2) ve Talimat
                      Paneli (zihniyet + sertlik); sonuc kaydedilince hafta tamamlanir.
                      Hazirlik macinda da bir takim yonetilebilir.
    Kadro & Taktik  : dizilis, asistan, ilk 11 secimi, kondisyon cubuklari, guc/potansiyel yildizlari
    Altyapi Akademisi (U-21): akademi kadrosu, gozlemci tahmini potansiyel, wonderkid, A takima
                      yukselt / U-21'e gonder, son genc girisi                          (kariyer)
    Finans          : iki kalemli butce, 52 haftalik kaydirici                    (kariyer)
    Transfer Pazari : gozlemci sisli arama, bonservis teklifi, sozlesme masasi    (kariyer)
    Lig             : puan durumu, gol kralligi, sonraki haftayi oyna             (kariyer)
    Devler Arenasi  : interaktif kura, turnuva agaci, gruplar, gol/asist kralligi,
                      sakat/cezali listesi, uzatma ve penalti ayrintilari
    Teknik Heyet    : personel, etkiler, ise alma / gonderme

Mimari:
    * Bu dosya yalnizca SUNUM: kurallar career_manager / tactics / finance / transfers /
      fitness modullerinde, veri satirlari career_views'da, HTML web_view ve pitch'te.
    * Veriyi degistiren her dugme on_click CALLBACK'i kullanir. Streamlit callback'i
      betik bastan calismadan ONCE isletir; boylece "haftayi oyna" sonrasi tum sekmeler
      guncel veriyle cizilir (bayat kadro/butce gostermez).
    * Canli mac dongusu (time.sleep) diger sekmeler cizildikten SONRA calisir; mac
      oynarken yonetim sekmeleri de dolu gorunur.
    * Canli mac nesnesi (live_match.LiveMatch) session_state["live"]'dadir. Dongu her dakikayi
      motorda tamamlar, sonra cizer: bir dugmeye basilinca Streamlit betigi bir sonraki cizimde
      keser, callback (orn. DURDUR) calisir ve mac kaldigi dakikadan tutarli durumla devam eder.
      Kaydedilmemis kariyer canli maci varken hafta oynatma, takim ve mod degisikligi kilitlidir.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import select
from streamlit.runtime.scriptrunner import get_script_run_ctx

import accounts
import arena_views as av
import career_views as cv
import database
import pitch
import reputation
import staff as staff_rules
from auth import AuthError
from bracket_view import (
    BRACKET_CSS,
    bracket_html,
    champion_banner_html,
    draw_board_html,
    group_tables_html,
)
from career_manager import (
    ACADEMY_CAPACITY,
    SENIOR_SQUAD_MAX,
    AcademyError,
    CareerManager,
    LiveMatchError,
    SeasonNotFinished,
)
from cm_theme import CM_THEME_CSS, login_banner_html, panel_title_html, stat_strip_html
from cup_draw import FORMAT_LABELS, CupFormat, DrawComplete, formats_for
from database import schema_problems, session_scope, wait_for_db
from finance import BudgetError, format_money, preview_budget_shift, wage_budget_bounds
from fitness import condition_band
from instructions import (
    MENTALITY_LABELS,
    TACKLING_LABELS,
    TeamInstructions,
    parse_mentality,
    parse_tackling,
)
from live_match import SUB_RULE_LABELS, LiveMatch, engine_config_for
from match_engine import InterventionError, KnockoutRule, MatchEngine, build_match_team
from match_feed import SideStats, build_timeline, summarize, team_energy_at
from models import (
    Competition,
    Fixture,
    GameMode,
    Player,
    Position,
    SquadRole,
    Staff,
    StaffRole,
    Team,
    TournamentStatus,
)
from stars import FILTER_OPTIONS, star_glyphs, star_threshold, stars
from tactics import FORMATIONS, MATCH_FORMATIONS, arrange_slots
from tournament_manager import TournamentError, matchday_label
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
TAB_LIVE, TAB_SQUAD, TAB_FINANCE, TAB_MARKET = "🏟️ Canlı Maç", "📋 Kadro & Taktik", "💰 Finans", "🔄 Transfer Pazarı"
TAB_LEAGUE, TAB_ARENA, TAB_STAFF = "🏆 Lig", "⭐ Devler Arenası", "👥 Teknik Heyet"
TAB_ACADEMY = "🎓 Altyapı Akademisi (U-21)"
CAREER_TABS = [TAB_LIVE, TAB_SQUAD, TAB_ACADEMY, TAB_FINANCE, TAB_MARKET, TAB_LEAGUE, TAB_ARENA, TAB_STAFF]
TOURNAMENT_TABS = [TAB_LIVE, TAB_ARENA, TAB_SQUAD, TAB_STAFF]
TABS = CAREER_TABS
LIVE_MANAGE, LIVE_FRIENDLY, LIVE_REPLAY = "Maçımı yönet", "Hazırlık maçı", "Son maçımı izle"
LIVE_MODES = [LIVE_MANAGE, LIVE_FRIENDLY, LIVE_REPLAY]
SIDE_WATCH = "Sadece izle"
SIDE_LABELS = ["Ev sahibi", "Deplasman", SIDE_WATCH]
LIVE_MINUTE_SHARE = 0.4          # olaysiz bir dakika, olay karesinin bu kadari surer
RULE_OPTIONS = list(SUB_RULE_LABELS.values())
MENTALITY_OPTIONS = list(MENTALITY_LABELS.values())
TACKLING_OPTIONS = list(TACKLING_LABELS.values())
ROLE_SAME = "Çıkanın görevi"
ROLE_CHOICES = [ROLE_SAME] + [p.value for p in Position]
MODE_LABELS = {GameMode.CAREER: "Kariyer Modu", GameMode.TOURNAMENT: "Turnuva Modu"}
STATUS_LABELS = {TournamentStatus.DRAW: "Kura", TournamentStatus.RUNNING: "Sürüyor",
                 TournamentStatus.FINISHED: "Bitti"}
POSITIONS = [p.value for p in Position]
LOGIN_MAX_FAILURES = 5
LOGIN_COOLDOWN_SECONDS = 30
STAR_FILTER_LABELS = ["Tümü"] + [label for label, _ in FILTER_OPTIONS]


# ===========================================================================
# OTURUM (10. Asama)
# ===========================================================================

class NoCareerSession(RuntimeError):
    """Betik calisirken oturum yok: veritabani islemi HICBIR kariyere yonlendirilmez."""


def session_career_schema() -> str | None:
    """
    Veritabani cozucusu: bu Streamlit oturumundaki menajerin kariyer semasi.
    GUVENLIK: betik (ya da callback) calisirken oturum yoksa sessizce 'public'e (ilk kullanicinin
    kariyeri) dusulmez, hata firlatilir: ornegin ayni istekte once 'Cikis' sonra 'Haftayi oyna'
    callback'i islense bile ikincisi baska bir kariyere yazamaz. Betik baglami disinda (testlerin
    dogrudan DB islemleri, CLI) None -> eski davranis.
    """
    if get_script_run_ctx() is None:
        return None
    auth = st.session_state.get("auth")
    if auth is None:
        raise NoCareerSession("Oturum kapalı: kariyer veritabanına erişilemez.")
    return auth.career_schema


database.set_career_schema_resolver(session_career_schema)

# Giris gerektirmeyen callback'ler; digerleri modul sonunda requires_auth ile sarilir
PUBLIC_CALLBACKS = frozenset({"cb_login", "cb_register", "cb_logout"})


def requires_auth(callback):
    """Oyun callback'i yalnizca oturum varken calisir (oturum yoksa sessizce hicbir sey yapmaz)."""
    @functools.wraps(callback)
    def guarded(*args, **kwargs):
        if st.session_state.get("auth") is None:
            return None
        return callback(*args, **kwargs)

    guarded.requires_auth = True
    return guarded


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


def selectable_teams(cm: CareerManager, teams: list[str]) -> list[str]:
    """Turnuva modunda yalnizca Devler Arenasi katilimcilari yonetilebilir."""
    if cm.game_mode is not GameMode.TOURNAMENT:
        return teams
    t = cm.tournaments.current()
    if t is None:
        return teams
    return [team.name for team in cm.tournaments.participants(t)]


# ===========================================================================
# CALLBACK'LER (veriyi degistiren tek yer)
# ===========================================================================

def start_session(session: accounts.AuthSession) -> None:
    """Yeni oturum: onceki kullanicinin ekran durumu (widget, rapor, canli mac) tasinmaz."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["auth"] = session
    flash("sidebar", "success", f"Hoş geldin, {session.username}! Kariyerin yüklendi.")


def cb_login() -> None:
    ss = st.session_state
    remaining = ss.get("login_locked_until", 0.0) - time.time()
    if remaining > 0:
        flash("auth", "error", f"Çok fazla hatalı deneme. {int(remaining) + 1} sn sonra tekrar dene.")
        return
    try:
        session = accounts.authenticate(ss.get("login_user", ""), ss.get("login_pass", ""))
    except Exception as exc:
        if not isinstance(exc, (accounts.AccountError, AuthError)):
            exc = accounts.AccountError("Giriş şu an yapılamadı, lütfen biraz sonra tekrar dene.")
        failures = ss.get("login_failures", 0) + 1
        if failures >= LOGIN_MAX_FAILURES:
            ss["login_locked_until"] = time.time() + LOGIN_COOLDOWN_SECONDS
            failures = 0
        ss["login_failures"] = failures
        ss["login_pass"] = ""
        flash("auth", "error", str(exc))
        return
    start_session(session)


def cb_register() -> None:
    ss = st.session_state
    password = ss.get("reg_pass", "")
    if password != ss.get("reg_pass2", ""):
        ss["reg_pass"] = ss["reg_pass2"] = ""
        flash("auth", "error", "Parolalar eşleşmiyor.")
        return
    try:
        session = accounts.register(ss.get("reg_user", ""), password)
    except Exception as exc:
        if not isinstance(exc, (accounts.AccountError, AuthError)):
            exc = accounts.AccountError("Kayıt şu an tamamlanamadı, lütfen biraz sonra tekrar dene.")
        ss["reg_pass"] = ss["reg_pass2"] = ""
        flash("auth", "error", str(exc))
        return
    start_session(session)


def cb_logout() -> None:
    auth = st.session_state.get("auth")
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if auth is not None:
        flash("auth", "info", f"{auth.username} çıkış yaptı.")


def _academy_move(widget: str, move, verb: str) -> None:
    player_id = st.session_state.get(widget)
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        player = db.get(Player, player_id) if player_id is not None else None
        if team is None or player is None:
            return
        name = player.name
        try:
            move(cm, team, player)
        except AcademyError as exc:
            db.rollback()
            flash("academy", "error", str(exc))
            return
    flash("academy", "success", f"{verb}: {name}")
    reset_widgets(widget, "tac_editor", "tac_rows")


def cb_promote() -> None:
    _academy_move("acad_promote", lambda cm, team, p: cm.promote_to_senior(team, p), "⬆️ A takıma yükseltildi")


def cb_demote() -> None:
    _academy_move("acad_demote", lambda cm, team, p: cm.send_to_academy(team, p), "⬇️ U-21 akademisine gönderildi")


def cb_choose_mode(mode_value: str) -> None:
    mode = GameMode(mode_value)
    with session_scope() as db:
        cm = manager(db)
        try:
            cm.set_game_mode(mode)
        except ValueError as exc:
            flash("sidebar", "error", str(exc))
            return
        flash("sidebar", "success", f"{MODE_LABELS[mode]} seçildi.")
    reset_widgets("sb_team", "neg", "tac_editor", "tac_rows", "last_week_lines", "last_user_result",
                  "last_user_cup_result", "last_cup_lines")


def cb_reset_mode() -> None:
    with session_scope() as db:
        try:
            manager(db).reset_game_mode()
        except ValueError as exc:
            flash("sidebar", "error", str(exc))
    reset_widgets("sb_team")


def cb_set_team() -> None:
    with session_scope() as db:
        cm = manager(db)
        team = cm.find_team(st.session_state["sb_team"])
        if team is None:
            flash("sidebar", "error", f"Takım bulunamadı: {st.session_state['sb_team']}")
            return
        cm.set_user_team(team)
    reset_widgets("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee", "last_week_lines",
                  "last_user_result", "last_user_cup_result", "tac_formation")


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


def store_week_report(report) -> None:
    """Hafta (ya da yalnizca hafta ici kupa) raporunu sekmelerin okuyacagi anahtarlara yazar."""
    key = (report.season, report.week)
    if report.midweek_only:
        st.session_state["last_cup_lines"] = cv.cup_report_lines(report)
        st.session_state["last_user_cup_result"] = report.user_cup_result
        st.session_state["midweek_stored"] = key
        return
    st.session_state["last_week_lines"] = cv.week_report_lines(report)
    st.session_state["last_user_result"] = report.user_result
    intake = getattr(report, "youth_intake", None) or []
    if intake:
        st.session_state["last_intake"] = (report.season, [getattr(n, "player_id", None) for n in intake])
    # Hafta ici ayri kaydedildiyse o haftanin kupa raporu silinmesin
    if report.cup_results or st.session_state.get("midweek_stored") != key:
        st.session_state["last_cup_lines"] = cv.cup_report_lines(report)
        st.session_state["last_user_cup_result"] = report.user_cup_result


def live_blocks_week() -> bool:
    """Bitmemis (kaydedilmemis) kariyer canli maci varken hafta oynatilamaz."""
    live = st.session_state.get("live")
    return live is not None and live.is_fixture and not live.saved and not live.finished


def cb_play_week() -> None:
    live = st.session_state.get("live")
    live_results = None
    if live is not None and live.is_fixture and not live.saved:
        if not live.finished:
            for area in ("league", "arena"):
                flash(area, "error", "Canlı maçın sürüyor: önce 🏟️ Canlı Maç sekmesinde bitir.")
            return
        live_results = {live.fixture_id: live.result()}    # canli oynanan mac yeniden simule edilmez
    with session_scope() as db:
        cm = manager(db)
        try:
            report = cm.play_week(live_results)
        except LiveMatchError as exc:
            db.rollback()                 # yarim hafta commit edilmesin
            for area in ("league", "arena"):
                flash(area, "error", str(exc))
            return
    # "kaydedildi" ancak commit basariliysa (session_scope blogu hatasiz bitti)
    if live_results:
        live.saved = True
    store_week_report(report)
    for area in ("league", "arena"):
        if report.played_any:
            flash(area, "success", f"✅ {report.week}. hafta oynandı.")
        else:
            flash(area, "info", "Oynanacak maç yok — sezon tamamlandı.")
    reset_widgets("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee")


def cb_new_season() -> None:
    with session_scope() as db:
        cm = manager(db)
        try:
            season = cm.start_new_season()
            text = (f"Sezon {season} başladı!" if cm.game_mode is GameMode.CAREER
                    else f"Yeni Devler Arenası turnuvası hazır (Sezon {season}). Kurayı çek!")
            flash("league", "success", text)
            flash("arena", "success", text)
            for note in cm.new_season_notes:
                flash("academy", "warning", f"🎓 {note}")
        except SeasonNotFinished as exc:
            flash("league", "error", str(exc))
            flash("arena", "error", str(exc))
    reset_widgets("last_week_lines", "last_user_result", "last_user_cup_result", "last_cup_lines", "arena_format")


def cb_draw_ball() -> None:
    with session_scope() as db:
        cm = manager(db)
        try:
            step = cm.tournaments.draw_next()
        except (TournamentError, DrawComplete) as exc:
            flash("arena", "error", str(exc) or "Kura zaten tamamlandı.")
            return
        t = cm.tournaments.current()
        if t.status is not TournamentStatus.DRAW:
            flash("arena", "success", f"Son top: {step.team_name}. Kura tamamlandı — eşleşmeler ve fikstür hazır!")


def cb_draw_all() -> None:
    with session_scope() as db:
        cm = manager(db)
        try:
            steps = cm.tournaments.draw_all()
        except (TournamentError, DrawComplete) as exc:
            flash("arena", "error", str(exc) or "Kura zaten tamamlandı.")
            return
        flash("arena", "success", f"Kalan {len(steps)} top otomatik çekildi. Kura tamamlandı!")


def cb_set_cup_format() -> None:
    value = st.session_state.get("arena_format")
    with session_scope() as db:
        cm = manager(db)
        try:
            cm.tournaments.set_format(CupFormat(value))
        except (TournamentError, ValueError) as exc:
            flash("arena", "error", str(exc))
            return
        flash("arena", "success", f"Format: {FORMAT_LABELS[CupFormat(value)]}")


# --- canli mac (9. Asama) ---

def live_setup_values() -> dict:
    """Mac ayarlari expander'inin degerleri (widget cizilmediyse varsayilanlar)."""
    rule_label = st.session_state.get("live_rule", RULE_OPTIONS[0])
    rule = next(r for r, label in SUB_RULE_LABELS.items() if label == rule_label)
    return {
        "sub_rule": rule,
        "instructions": TeamInstructions(
            parse_mentality(st.session_state.get("live_start_mentality", MENTALITY_OPTIONS[1])),
            parse_tackling(st.session_state.get("live_start_tackling", TACKLING_OPTIONS[1])),
        ),
        "auto_subs": st.session_state.get("live_auto_subs", True),
        "pause_at_breaks": st.session_state.get("live_pause_breaks", True),
        "pause_on_key_events": st.session_state.get("live_pause_events", True),
    }


def begin_live(live: LiveMatch) -> None:
    st.session_state["live"] = live
    st.session_state["live_mentality"] = MENTALITY_LABELS[live.instructions.mentality]
    st.session_state["live_tackling"] = TACKLING_LABELS[live.instructions.tackling]
    st.session_state["live_formation"] = live.formation if live.formation in MATCH_FORMATIONS else "4-4-2"
    reset_widgets("live_sub_out", "live_sub_in", "live_sub_role")


def current_live() -> LiveMatch | None:
    return st.session_state.get("live")


def cb_live_start_fixture() -> None:
    setup = live_setup_values()
    with session_scope() as db:
        cm = manager(db)
        try:
            prep = cm.prepare_live_match(config=engine_config_for(setup["sub_rule"], cm.engine_config))
        except LiveMatchError as exc:
            db.rollback()
            flash("live", "error", str(exc))
            return
        if prep.midweek_report is not None and prep.midweek_report.played_any:
            store_week_report(prep.midweek_report)
            flash("live", "info", "Önce hafta içi Devler Arenası maçları oynandı; kondisyonlar güncel.")
    live = LiveMatch.create(
        prep.engine, prep.managed_team_id, instructions=setup["instructions"], auto_subs=setup["auto_subs"],
        fixture_id=prep.fixture_id, competition="cup" if prep.competition is Competition.CUP else "league",
        season=prep.season, week=prep.week, title=prep.title, sub_rule=setup["sub_rule"],
        pause_at_breaks=setup["pause_at_breaks"], pause_on_key_events=setup["pause_on_key_events"],
    )
    begin_live(live)


def cb_live_start_friendly() -> None:
    """Yonetilen hazirlik maci (izleme secildiyse ya da takimlar ayniysa betik kendisi ele alir)."""
    ss = st.session_state
    side = ss.get("live_side", SIDE_LABELS[0])
    home, away = ss.get("live_home"), ss.get("live_away")
    if side == SIDE_WATCH or not home or home == away:
        return
    setup = live_setup_values()
    with session_scope() as db:
        engine = friendly_engine(db, home, away, parse_seed(ss.get("live_seed", "")),
                                 ss.get("live_knockout", False), engine_config_for(setup["sub_rule"]))
    live = LiveMatch.create(
        engine, engine.home.id if side == SIDE_LABELS[0] else engine.away.id,
        instructions=setup["instructions"], auto_subs=setup["auto_subs"],
        title=f"Hazırlık maçı · {home} - {away}", sub_rule=setup["sub_rule"],
        pause_at_breaks=setup["pause_at_breaks"], pause_on_key_events=setup["pause_on_key_events"],
    )
    begin_live(live)


def cb_live_pause() -> None:
    live = current_live()
    if live is not None:
        live.pause()


def cb_live_resume() -> None:
    live = current_live()
    if live is not None:
        live.resume()


def cb_live_finish() -> None:
    live = current_live()
    if live is not None and not live.finished:
        live.play_to_end()


def cb_live_close() -> None:
    live = current_live()
    if live is None:
        return
    if live.is_fixture and not live.saved and not live_is_stale(live):
        flash("live", "error", "Kariyer maçını kapatmadan önce sonucu kaydet.")
        return
    st.session_state.pop("live", None)
    reset_widgets("live_sub_out", "live_sub_in", "live_sub_role", "live_mentality", "live_tackling",
                  "live_formation")


def cb_live_instructions() -> None:
    live = current_live()
    if live is None:
        return
    try:
        event = live.set_instructions(st.session_state["live_mentality"], st.session_state["live_tackling"])
    except InterventionError as exc:
        flash("live", "error", str(exc))
        return
    if event is not None:
        flash("live", "info", f"📋 {event.description}")


def cb_live_formation() -> None:
    live = current_live()
    if live is None:
        return
    try:
        event = live.change_formation(st.session_state.get("live_formation", live.formation))
    except InterventionError as exc:
        flash("live", "error", str(exc))
        return
    if event is not None:
        flash("live", "info", f"📋 {event.description}")


def cb_live_substitute() -> None:
    live = current_live()
    if live is None:
        return
    role_value = st.session_state.get("live_sub_role", ROLE_SAME)
    role = None if role_value == ROLE_SAME else Position(role_value)
    try:
        event = live.substitute(st.session_state.get("live_sub_out"), st.session_state.get("live_sub_in"), role)
    except InterventionError as exc:
        flash("live", "error", str(exc))
        return
    flash("live", "success", f"🔁 {event.display_minute} {event.description}")
    reset_widgets("live_sub_out", "live_sub_in", "live_sub_role")


def cb_live_save() -> None:
    live = current_live()
    if live is None or not live.is_fixture or live.saved or not live.finished:
        return
    with session_scope() as db:
        cm = manager(db)
        try:
            report = cm.save_live_result(live.fixture_id, live.result())
        except LiveMatchError as exc:
            db.rollback()                 # yarim hafta commit edilmesin
            flash("live", "error", str(exc))
            return
        next_match = cm.live_fixture() if report.midweek_only else None
    # "kaydedildi" ancak commit basariliysa (session_scope blogu hatasiz bitti)
    live.saved = True
    store_week_report(report)
    if report.midweek_only:
        text = "✅ Devler Arenası maçın kaydedildi."
        if next_match is not None:
            text += " Bu hafta lig maçın da var: maçı kapatıp **Maçımı yönet** ile canlı oynayabilirsin."
        else:
            text += " Haftanın lig maçları **🏆 Lig** sekmesinden oynatılabilir."
        flash("live", "success", text)
    else:
        text = f"✅ Sonuç kaydedildi, {report.week}. hafta tamamlandı."
        for area in ("live", "league", "arena"):
            flash(area, "success", text)
    reset_widgets("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee")


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
        auth = st.session_state.get("auth")
        if auth is not None:
            u1, u2 = st.columns([3, 2])
            u1.markdown(f"👤 **{escape(auth.username)}**")
            u2.button("Çıkış", key="sb_logout", on_click=cb_logout, use_container_width=True,
                      help="Oturumu kapatır; kaydedilmemiş canlı maç kaybolur.")
        st.header("Kariyer")
        show_flash("sidebar")
        with session_scope() as db:
            cm = manager(db)
            team = cm.user_team
            current = team.name if team else None
            mode = cm.game_mode
            total = cm.total_weeks()
            st.caption(f"{MODE_LABELS[mode]} · Sezon {cm.season} · Hafta {min(cm.current_week, total)} / {total}"
                       + (" · sezon bitti" if cm.season_finished else ""))
            rep = cm.manager_reputation
            st.caption(f"Menajer tanınırlığı {rep:.1f}/20 · {reputation.label(rep)}")
            if team is not None and mode is GameMode.CAREER:
                st.caption(f"Transfer {format_money(team.transfer_budget)} · "
                           f"maaş havuzu {format_money(team.wage_budget)}/hf")
            t = cm.tournaments.current()
            st.caption(f"⭐ Devler Arenası: {cm.tournaments.user_status(t, team.id if team else None)}")
            teams = selectable_teams(cm, teams)
            can_change = cm.can_change_mode()

        index = teams.index(current) if current in teams else 0
        chosen = st.selectbox("Takımın", teams, index=index, key="sb_team")
        live = st.session_state.get("live")
        live_pending = live is not None and live.is_fixture and not live.saved
        st.button("Takımı ayarla", key="sb_set_team", on_click=cb_set_team,
                  disabled=chosen == current or live_pending, use_container_width=True,
                  help="Kaydedilmemiş canlı maç varken takım değiştirilemez." if live_pending else None)
        st.button("🔁 Oyun modunu değiştir", key="sb_change_mode", on_click=cb_reset_mode,
                  disabled=not can_change or live_pending, use_container_width=True,
                  help="Yalnızca sezon başında, hiç maç oynanmamışken.")
        with st.expander("Gelişmiş"):
            st.text_input("Kariyer tohumu (boş = rastgele)", key="career_seed",
                          help="Aynı tohum aynı sonuçları üretir (test ve tekrar için).")


# ===========================================================================
# SEKME: KADRO & TAKTIK
# ===========================================================================

def squad_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("squad")
    rows = cv.squad_rows(team, cm.current_week, cm)
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
        st.markdown(pitch.lineup_svg(slots, team.name, formation_label=team.formation, rating_label=star_glyphs),
                    unsafe_allow_html=True)
    with right:
        st.markdown("#### Kadro durumu")
        st.markdown(squad_table_html(rows), unsafe_allow_html=True)

    st.markdown("#### Kadro seçimi")
    st.caption("Durum ve Slot hücrelerine tıklayarak ilk 11'i ve kulübeyi belirle, sonra kaydet. "
               "Sakat/cezalı oyuncular kaydedilirken reddedilir.")
    frame = pd.DataFrame([
        {
            "id": r.id, "Oyuncu": ("🌟 " if r.wonderkid else "") + r.name, "Mv": r.position, "Güç": r.stars,
            "Potansiyel": r.potential_stars, "Form": r.form,
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
        column_order=["Oyuncu", "Mv", "Güç", "Potansiyel", "Form", "Moral", "Kondisyon", "Durum", "Slot", "Not"],
        disabled=["id", "Oyuncu", "Mv", "Güç", "Potansiyel", "Form", "Moral", "Kondisyon", "Not"],
        column_config={
            "Kondisyon": st.column_config.ProgressColumn("Kondisyon", min_value=0, max_value=100, format="%d%%"),
            "Durum": st.column_config.SelectboxColumn("Durum", options=list(cv.STATUS_LABELS.values()), required=True),
            "Slot": st.column_config.SelectboxColumn("Slot", options=POSITIONS),
        },
    )
    st.session_state["tac_rows"] = edited.to_dict("records")
    st.button("💾 Kadroyu kaydet", key="tac_save", on_click=cb_save_lineup, type="primary")


# ===========================================================================
# SEKME: ALTYAPI AKADEMISI (U-21)
# ===========================================================================

def academy_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("academy")
    locked = live_fixture_pending()
    academy = cm.academy_players(team)
    coach = team.best_staff(StaffRole.COACH, "working_with_youngsters")
    st.markdown(stat_strip_html([
        ("U-21 akademi", f"{len(academy)}/{ACADEMY_CAPACITY}"),
        ("A takım", f"{len(team.players)}/{SENIOR_SQUAD_MAX}"),
        ("Altyapı tesisleri", f"{team.youth_facilities or '-'}/20"),
        ("Gençlerle çalışma (antrenör)", coach.working_with_youngsters if coach else "–"),
    ]), unsafe_allow_html=True)
    st.caption(f"Genç girişi her sezon {cm.youth_intake_week()}. haftada (son haftadan önce) yapılır. "
               "Potansiyel gözlemci tahminidir; gerçek tavan gizlidir. Oynayan gençler daha hızlı gelişir.")
    if locked:
        st.info("🏟️ Canlı maçın sürüyor: kadro hareketleri maç kaydedilene kadar kapalı.")
    for note in cm.academy_warnings(team):
        st.warning(f"🎓 {note}")

    f1, f2, f3 = st.columns([3, 1, 2])
    positions = f1.multiselect("Mevki", POSITIONS, key="acad_pos")
    wonder_only = f2.toggle("Sadece 🌟", key="acad_wonder", help="Yalnızca wonderkid (16-21 yaş, büyük potansiyel)")
    sort = f3.selectbox("Sırala", list(cv.ACADEMY_SORTS), key="acad_sort")
    rows = cv.academy_rows(cm, team, cv.AcademyFilter(set(positions), wonder_only, sort))

    st.markdown(panel_title_html("U-21 kadrosu"), unsafe_allow_html=True)
    if rows:
        st.dataframe(pd.DataFrame([r.to_dict() for r in rows]), hide_index=True, use_container_width=True)
    else:
        st.info("Filtreye uyan akademi oyuncusu yok." if academy else "Akademide oyuncu yok. Genç girişini bekle.")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(panel_title_html("⬆️ A takıma yükselt"), unsafe_allow_html=True)
        candidates = {r.id: r for r in cv.academy_rows(cm, team)}
        if candidates:
            if st.session_state.get("acad_promote") not in candidates:
                reset_widgets("acad_promote")
            st.selectbox("Akademi oyuncusu", list(candidates), format_func=lambda i: candidates[i].label(),
                         key="acad_promote")
        st.button("⬆️ A Takıma Yükselt", key="acad_promote_btn", on_click=cb_promote, type="primary",
                  disabled=locked or not candidates, use_container_width=True)
    with right:
        st.markdown(panel_title_html("⬇️ U-21'e gönder"), unsafe_allow_html=True)
        seniors = {r.id: r for r in cv.demotion_rows(cm, team)}
        if st.session_state.get("acad_demote") not in seniors:
            reset_widgets("acad_demote")
        st.selectbox("A takım oyuncusu", list(seniors), format_func=lambda i: seniors[i].label(), key="acad_demote",
                     help="21 yaş üstü en fazla birkaç oyuncu akademide kalabilir.")
        st.button("⬇️ U-21'e Gönder", key="acad_demote_btn", on_click=cb_demote,
                  disabled=locked or not seniors, use_container_width=True)

    intake = st.session_state.get("last_intake")
    if intake and intake[0] == cm.season:
        ids = {pid for pid in intake[1] if pid is not None}
        fresh = [r for r in cv.academy_rows(cm, team) if r.id in ids]
        if fresh:
            with st.expander(f"🎓 Bu sezonun genç girişi ({len(fresh)} oyuncu)", expanded=True):
                st.dataframe(pd.DataFrame([r.to_dict() for r in fresh]), hide_index=True, use_container_width=True)


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

def live_fixture_pending() -> bool:
    """Kaydedilmemis kariyer canli maci var mi? (kadro / heyet degisikligi maca sizmasin)"""
    live = st.session_state.get("live")
    return live is not None and live.is_fixture and not live.saved


def transfer_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("market")
    if live_fixture_pending():
        st.info("🏟️ Canlı maçın sürüyor: transfer işlemleri maç kaydedilene kadar kapalı.")
        return
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
    else:
        st.dataframe(pd.DataFrame([
            {"Oyuncu": r.name, "Kulüp": r.club, "Mv": r.position, "Yaş": r.age,
             "Güç (tahmin)": r.stars_text, "Değer (tahmin)": r.value_text, "Sözleşme": f"{r.contract_years} yıl"}
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
    cup_next = cm.next_cup_fixture(team.id)
    if cup_next is not None:
        opponent = cup_next.away_team if cup_next.home_team_id == team.id else cup_next.home_team
        st.caption(f"⭐ Devler Arenası: {cup_next.week}. hafta (hafta içi) · {team.name} vs {opponent.name}")

    b1, b2 = st.columns(2)
    b1.button("⏭️ Sonraki haftayı oyna", key="lg_play", on_click=cb_play_week, type="primary",
              disabled=cm.season_finished or live_blocks_week(), use_container_width=True,
              help="Canlı maçın sürüyor; önce bitir." if live_blocks_week() else None)
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
    if live_fixture_pending():
        st.info("🏟️ Canlı maçın sürüyor: teknik heyet değişiklikleri maç kaydedilene kadar kapalı.")
        return
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
# SEKME: DEVLER ARENASI (CHAMPIONS CUP)
# ===========================================================================

def arena_tab(db, cm: CareerManager, team: Team | None) -> None:
    show_flash("arena")
    tm = cm.tournaments
    t = tm.current()
    if t is None:
        st.info("Bu dünyada Devler Arenası kurulamadı: en az 8 takım gerekli.")
        return
    user_id = team.id if team else None
    fmt = tm.fmt(t)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Sezon", t.season)
    m2.metric("Format", "Eleme" if fmt is CupFormat.KNOCKOUT else "Gruplar", help=FORMAT_LABELS[fmt])
    m3.metric("Durum", STATUS_LABELS[t.status])
    m4.metric("Katılımcı", f"{t.size} takım")
    st.caption(f"Takımın: **{tm.user_status(t, user_id) if team else 'takım seçilmedi'}**")

    if t.status is TournamentStatus.DRAW:
        draw_section(cm, t)
    else:
        cup_progress_section(cm, t, user_id)

    with st.expander(f"Katılımcılar ({t.size} takım)"):
        st.dataframe(pd.DataFrame(av.participant_rows(tm, t, user_id)), hide_index=True, use_container_width=True)


def draw_section(cm: CareerManager, t) -> None:
    tm = cm.tournaments
    session = tm.draw_session(t)
    st.markdown("#### 🎱 Kura çekimi")
    if not session.steps:
        options = [f.value for f in formats_for(t.size)]
        if st.session_state.get("arena_format") not in options:
            st.session_state["arena_format"] = t.format
        st.radio("Turnuva formatı", options, key="arena_format", horizontal=True,
                 format_func=lambda v: FORMAT_LABELS[CupFormat(v)], on_change=cb_set_cup_format,
                 help="Kura başladıktan sonra değiştirilemez.")
    if session.relaxed_note:
        st.warning(session.relaxed_note)

    pots, slots, headline, announcement = av.draw_board(tm, t)
    st.markdown(draw_board_html(pots, slots, headline, announcement), unsafe_allow_html=True)

    pot = session.current_pot
    balls = len(session.remaining(pot)) if pot is not None else 0
    if balls:
        st.caption(f"Torbada {balls} top var — bir topa tıkla ve aç!")
        per_row = min(8, balls)
        for row_start in range(0, balls, per_row):
            cols = st.columns(per_row)
            for i in range(row_start, min(balls, row_start + per_row)):
                cols[i - row_start].button("🔮", key=f"arena_ball_{i}", on_click=cb_draw_ball,
                                           help="Topu aç", use_container_width=True)
    st.button("🎲 Kurayı otomatik çek", key="arena_draw_all", on_click=cb_draw_all, type="primary")
    with st.expander("Torbalarda kalan takımlar"):
        for label, names in av.pot_remaining_names(tm, t):
            st.markdown(f"**{escape(label)}:** " + (escape(", ".join(names)) if names else "—"))


def cup_progress_section(cm: CareerManager, t, user_id: int | None) -> None:
    tm = cm.tournaments
    if t.status is TournamentStatus.FINISHED and t.champion is not None:
        st.markdown(champion_banner_html(t.champion.name, f"Sezon {t.season} · {t.name}"), unsafe_allow_html=True)

    md = tm.next_matchday(t)
    total = cm.total_weeks()
    if md is not None:
        st.caption(f"Sıradaki kupa günü: {md.week}. hafta · {matchday_label(md)} · "
                   f"şu an {min(cm.current_week, total)}. hafta")
    b1, b2 = st.columns(2)
    b1.button("⏭️ Sonraki haftayı oyna", key="arena_play", on_click=cb_play_week, type="primary",
              disabled=cm.season_finished or live_blocks_week(), use_container_width=True,
              help="Canlı maçın sürüyor; önce bitir." if live_blocks_week() else None)
    if cm.season_finished:
        label = "🆕 Yeni sezonu başlat" if cm.game_mode is GameMode.CAREER else "🆕 Yeni turnuva"
        b2.button(label, key="arena_new_season", on_click=cb_new_season, use_container_width=True)

    lines = st.session_state.get("last_cup_lines")
    if lines:
        with st.expander("Son kupa gününün raporu", expanded=True):
            for kind, text in lines:
                (st.success if kind == "season" else st.markdown)(text if kind != "result" else f"- {text}")

    groups = av.group_views(tm, t, user_id)
    if groups:
        st.markdown("#### Gruplar")
        st.markdown(group_tables_html(groups), unsafe_allow_html=True)
    st.markdown("#### Turnuva ağacı")
    champion = t.champion.name if t.champion is not None else None
    st.markdown(bracket_html(av.bracket_rounds(tm, t, user_id), champion), unsafe_allow_html=True)

    results = av.result_rows(tm, t)
    if results:
        st.markdown("#### Sonuçlar")
        st.dataframe(pd.DataFrame(results).drop(columns=["id"]), hide_index=True, use_container_width=True)
        shown = 0
        for row in results:
            fx = cm.db.get(Fixture, row["id"])
            if fx is None or not fx.went_to_penalties or shown >= 6:
                continue
            shown += 1
            with st.expander(f"🥅 Penaltılar: {row['Ev sahibi']} {row['Skor']} {row['Deplasman']}"):
                for line in av.shootout_lines(fx):
                    st.markdown(f"- {escape(line)}")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Gol krallığı")
        scorers = av.player_rows(tm, t, by="goals")
        if scorers:
            st.dataframe(pd.DataFrame(scorers), hide_index=True, use_container_width=True)
        else:
            st.caption("Henüz gol yok.")
    with right:
        st.markdown("#### Asist krallığı")
        assists = av.player_rows(tm, t, by="assists")
        if assists:
            st.dataframe(pd.DataFrame(assists), hide_index=True, use_container_width=True)
        else:
            st.caption("Henüz asist yok.")

    st.markdown("#### Sakatlar ve cezalılar")
    unavailable = av.unavailable_rows(tm, t)
    if unavailable:
        st.dataframe(pd.DataFrame(unavailable), hide_index=True, use_container_width=True)
    else:
        st.caption("Turnuvada kalan takımlarda sakat ya da cezalı oyuncu yok.")


# ===========================================================================
# ILK GIRIS: OYUN MODU
# ===========================================================================

MODE_CARDS = {
    GameMode.CAREER: ("🏆 Kariyer Modu", "Lig maratonu: 6 lig, transfer, finans ve teknik heyet. "
                     "Devler Arenası maçları lig takvimiyle aynı haftalarda (hafta içi) oynanır."),
    GameMode.TOURNAMENT: ("⚔️ Turnuva Modu", "Sadece Devler Arenası (Champions Cup): 16 dev kulüp, "
                         "interaktif kura, çift maçlı eleme, uzatma ve penaltılar."),
}


def mode_screen() -> None:
    st.markdown("## Oyun modunu seç")
    show_flash("sidebar")
    columns = st.columns(2, gap="large")
    for column, (mode, (title, text)) in zip(columns, MODE_CARDS.items(), strict=True):
        with column:
            st.markdown(f"<div class='cm-mode-card'><h3>{escape(title)}</h3><p>{escape(text)}</p></div>",
                        unsafe_allow_html=True)
            st.button(title, key=f"mode_{mode.name.lower()}", on_click=cb_choose_mode, args=(mode.value,),
                      type="primary", use_container_width=True)


# ===========================================================================
# SEKME: CANLI MAC
# ===========================================================================

@dataclass
class MatchSlots:
    """Canli ekranin yer tutuculari (tabela, saha, istatistik, akis)."""
    board: object
    banner: object
    progress: object
    pitch: object
    stats: object
    energy: object
    feed: object


def match_slots(with_feed: bool = True) -> MatchSlots:
    """with_feed=False: akis yer tutucusu sonra feed_slot() ile eklenir (araya paneller girer)."""
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
    return MatchSlots(board, banner, progress, pitch_slot, stats_slot, energy_slot,
                      feed_slot() if with_feed else None)


def feed_slot():
    st.markdown("#### Maç akışı")
    return st.empty()


def energy_html(home: str, away: str, home_energy: int | None, away_energy: int | None) -> str:
    def cell(name, value):
        band = condition_band(value) if value is not None else None
        return f"<div style='margin:.2rem 0'><small>{escape(name)}</small>{condition_bar_html(value, band)}</div>"

    return "<div><b>Kondisyon (sahadakiler ort.)</b>" + cell(home, home_energy) + cell(away, away_energy) + "</div>"


def play_live(result, delay: float, tempo: float, show_pitch: bool) -> None:
    """Oynanmis bir maci (kayit / izleme) kare kare oynatir; mudahale yok."""
    frames = build_timeline(result)
    scenes = pitch.build_scenes(result, frames) if show_pitch else []
    home, away = result.home.name, result.away.name
    slots = match_slots()

    total = max(1, result.total_minutes)
    for i, frame in enumerate(frames):
        flash_kind = frame.event.highlight if frame.event.highlight in {"goal", "red"} else None
        slots.board.markdown(scoreboard_html(home, away, frame, flash_kind), unsafe_allow_html=True)
        if flash_kind:
            slots.banner.markdown(banner_html(frame), unsafe_allow_html=True)
        elif frame.event.highlight not in {"injury"}:
            slots.banner.empty()
        slots.progress.progress(min(1.0, frame.elapsed / total), text=f"{frame.display_minute} · {frame.phase}")
        if show_pitch:
            previous = scenes[i - 1] if i else None
            slots.pitch.markdown(pitch.scene_svg(scenes[i], previous, tempo=tempo), unsafe_allow_html=True)
        slots.stats.markdown(stats_html(home, away, frame.home, frame.away), unsafe_allow_html=True)
        slots.energy.markdown(
            energy_html(home, away, team_energy_at(result.home, frame.minute), team_energy_at(result.away, frame.minute)),
            unsafe_allow_html=True,
        )
        slots.feed.markdown(feed_html(frames[: i + 1]), unsafe_allow_html=True)
        if delay:
            time.sleep(delay * frame.pacing)

    summary = summarize(result, frames)
    slots.stats.markdown(
        stats_html(home, away, summary.home_stats, summary.away_stats,
                   (summary.possession_home, summary.possession_away)),
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown("### Maç sonu")
    for line in summary_lines(summary):
        st.markdown(line)


def friendly_engine(db, home: str, away: str, seed: int | None, knockout: bool, config=None) -> MatchEngine:
    """Hazirlik maci motoru; eleme secilirse tarafsiz sahada, beraberlikte uzatma + penalti."""
    home_team = db.scalar(select(Team).where(Team.name == home))
    away_team = db.scalar(select(Team).where(Team.name == away))
    if home_team is None or away_team is None:
        raise ValueError(f"Takım bulunamadı: {home if home_team is None else away}")
    if knockout:
        return MatchEngine(build_match_team(home_team, True), build_match_team(away_team, False),
                           seed=seed, config=config, knockout=KnockoutRule(), neutral_venue=True)
    return MatchEngine(build_match_team(home_team, True), build_match_team(away_team, False),
                       seed=seed, config=config)


def friendly_result(db, home: str, away: str, seed: int | None, knockout: bool):
    return friendly_engine(db, home, away, seed, knockout).simulate()


# --------------------------------------------------------------------------- canli mudahale

def live_now_energy(team) -> int | None:
    values = [p.energy for p in team.on_pitch]
    return round(sum(values) / len(values)) if values else None


def live_possession(result) -> tuple[int, int] | None:
    total = result.home.stats.possession_minutes + result.away.stats.possession_minutes
    if total == 0:
        return None
    home = round(100 * result.home.stats.possession_minutes / total)
    return home, 100 - home


def draw_live(slots: MatchSlots, live: LiveMatch, result, frames, index: int, previous,
              tempo: float, show_pitch: bool, flash_board: bool = True):
    """Canli macin index. karesini (ya da kare yoksa baslama oncesini) cizer; sahneyi dondurur."""
    home, away = result.home.name, result.away.name
    frame = frames[index] if frames and 0 <= index < len(frames) else None
    highlight = frame.event.highlight if frame is not None else None
    flash_kind = highlight if flash_board and highlight in {"goal", "red"} else None
    live_clock = None if live.finished else (live.clock, live.phase_label)
    slots.board.markdown(scoreboard_html(home, away, frame, flash_kind, live_clock=live_clock),
                         unsafe_allow_html=True)
    if flash_kind:
        slots.banner.markdown(banner_html(frame), unsafe_allow_html=True)
    elif highlight != "injury":
        slots.banner.empty()
    slots.progress.progress(live.progress, text=f"{live.clock} · {live.phase_label}")
    scene = previous
    if show_pitch and frame is not None:
        scene = pitch.build_scene(result, frames, index)
        slots.pitch.markdown(pitch.scene_svg(scene, previous, tempo=tempo), unsafe_allow_html=True)
    home_stats = frame.home if frame is not None else SideStats()
    away_stats = frame.away if frame is not None else SideStats()
    slots.stats.markdown(stats_html(home, away, home_stats, away_stats, live_possession(result)),
                         unsafe_allow_html=True)
    slots.energy.markdown(
        energy_html(home, away, live_now_energy(result.home), live_now_energy(result.away)),
        unsafe_allow_html=True,
    )
    if frame is not None:
        slots.feed.markdown(feed_html(frames[: index + 1]), unsafe_allow_html=True)
    return scene


def live_is_stale(live: LiveMatch) -> bool:
    """Kaydedilmemis kariyer maci artik gecersiz mi (hafta ilerledi / fikstur baska yoldan oynandi)."""
    if not live.is_fixture or live.saved:
        return False
    with session_scope() as db:
        cm = manager(db)
        fx = db.get(Fixture, live.fixture_id)
        return fx is None or fx.is_played or cm.season != live.season or cm.current_week != live.week


def live_setup_options() -> None:
    with st.expander("⚙️ Maç ayarları (değişiklik kuralı, başlangıç talimatı, otomatik durdurma)"):
        st.radio("Değişiklik kuralı", RULE_OPTIONS, key="live_rule",
                 help="Kural iki takıma da uygulanır. Devre arası pencere saymaz.")
        a, b = st.columns(2)
        a.selectbox("Başlangıç zihniyeti", MENTALITY_OPTIONS, index=1, key="live_start_mentality")
        b.selectbox("Başlangıç sertliği", TACKLING_OPTIONS, index=1, key="live_start_tackling")
        st.toggle("Asistan yorulan oyuncuları değiştirebilir", value=True, key="live_auto_subs",
                  help="Kapalıysa yorgunluk değişikliklerini sen yaparsın. Sakatlıkta asistan yine yedek sokar.")
        st.toggle("Devre arasında maçı durdur", value=True, key="live_pause_breaks")
        st.toggle("Takımımda sakatlık / kırmızı kartta durdur", value=True, key="live_pause_events")


def manage_setup() -> None:
    """Maçımı yönet: kullanicinin bu haftaki gercek maci (once hafta ici kupa, sonra lig)."""
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        if team is None:
            st.info("Maçını canlı yönetmek için önce kenar çubuğundan takımını seç ve **Takımı ayarla**'ya bas.")
            return
        if cm.season_finished:
            st.info("Sezon tamamlandı. Yeni sezonu başlatınca maçlarını canlı yönetebilirsin.")
            return
        if cm.live_cup_draw_pending():
            week = cm.current_week
            st.info(f"⭐ {week}. haftada Devler Arenası maçın var ama kura henüz çekilmedi. **Maça çık**'a "
                    "basarsan asistan kurayı otomatik tamamlar ve rakibin belirlenir; kurayı kendin çekmek "
                    "için **⭐ Devler Arenası** sekmesine geç.")
            live_setup_options()
            st.button("▶ Maça çık", key="live_fixture_start", on_click=cb_live_start_fixture, type="primary")
            return
        pending = cm.live_fixture()
        if pending is None:
            st.info(f"{cm.current_week}. haftada {team.name} için oynanacak maç yok. Haftayı "
                    "**🏆 Lig** ya da **⭐ Devler Arenası** sekmesinden oynatabilirsin.")
            return
        fx, competition = pending
        home_name, away_name = fx.home_team.name, fx.away_team.name
        at_home = fx.home_team_id == team.id
        opponent = fx.away_team if at_home else fx.home_team
        form = cm.team_form(opponent.id) or "-"
        week = cm.current_week
    label = "⭐ Devler Arenası (hafta içi)" if competition is Competition.CUP else "🏆 Lig"
    st.markdown(scoreboard_html(home_name, away_name, None), unsafe_allow_html=True)
    st.caption(f"{label} · {week}. hafta · {'ev' if at_home else 'deplasman'} · rakip formu {form} · "
               "ilk 11 ve diziliş **📋 Kadro & Taktik** sekmesinden gelir.")
    live_setup_options()
    st.button("▶ Maça çık", key="live_fixture_start", on_click=cb_live_start_fixture, type="primary")


def intervention_panels(live: LiveMatch) -> None:
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### 🧠 Talimat Paneli")
        st.caption(f"Şu an: {live.instructions_text()} · diziliş {live.formation}")
        st.radio("Zihniyet", MENTALITY_OPTIONS, key="live_mentality", on_change=cb_live_instructions,
                 horizontal=True,
                 help="Çok Ofansif: şut şansı artar ama savunma açılır ve oyuncular daha çok yorulur. "
                      "Çok Defansif: kale önü kapanır, hücum söner.")
        st.radio("Sertlik", TACKLING_OPTIONS, key="live_tackling", on_change=cb_live_instructions,
                 horizontal=True, help="Sert Oyna savunmayı güçlendirir ama kart ve sakatlık riskini katlar.")
        f1, f2 = st.columns([2, 1])
        f1.selectbox("Diziliş", list(MATCH_FORMATIONS), key="live_formation",
                     help="5-3-2 yalnızca maç içi acil durum dizilişidir.")
        f2.button("Uygula", key="live_formation_apply", on_click=cb_live_formation, use_container_width=True)
        if live.history:
            st.caption("Son müdahaleler: " + " · ".join(live.history[-3:]))
    with right:
        st.markdown("#### 🔁 Oyuncu Değişikliği")
        status = live.sub_status()
        st.caption(status.text() + (f" · {status.block}" if status.block else ""))
        if not live.can_substitute_now:
            st.caption("Değişiklik için maçı **⏸ DURDUR** (devre arasında maç kendiliğinden durur).")
            return
        rows, bench = live.lineup_rows(), live.bench_rows()
        st.dataframe(pd.DataFrame([{**{k: v for k, v in r.items() if k not in ("id", "OVR")}, "Güç": stars(r["OVR"])}
                                   for r in rows]), hide_index=True, use_container_width=True)
        if status.block:
            st.warning(f"Değişiklik yapılamaz: {status.block}.")
            return
        # Varsayilan secim anlamli olsun: en yorgun saha oyuncusu cikar, kulubenin en iyi saha oyuncusu girer
        out_rows = sorted(rows, key=lambda r: (r["Görev"] == Position.GK.value, r["Kondisyon"]))
        in_rows = sorted(bench, key=lambda r: r["Mevki"] == Position.GK.value)
        out_labels = {r["id"]: f"{r['Görev']} · {r['Oyuncu']} · kondisyon {r['Kondisyon']}"
                               + (f" {r['Kart']}" if r["Kart"] else "") + f" · #{r['id']}" for r in out_rows}
        in_labels = {r["id"]: f"{r['Mevki']} · {r['Oyuncu']} · {stars(r['OVR'])} · kondisyon {r['Kondisyon']}"
                              f" · #{r['id']}" for r in in_rows}
        st.selectbox("Çıkan", list(out_labels), format_func=out_labels.get, key="live_sub_out")
        st.selectbox("Giren", list(in_labels), format_func=in_labels.get, key="live_sub_in")
        st.selectbox("Görev", ROLE_CHOICES, key="live_sub_role",
                     help="Varsayılan: giren oyuncu çıkanın görevini alır.")
        st.button("✅ Değişikliği yap", key="live_sub_confirm", on_click=cb_live_substitute, type="primary",
                  use_container_width=True)


def live_final(live: LiveMatch, result, frames, slots: MatchSlots) -> None:
    summary = summarize(result, frames)
    home, away = result.home.name, result.away.name
    slots.stats.markdown(
        stats_html(home, away, summary.home_stats, summary.away_stats,
                   (summary.possession_home, summary.possession_away)),
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown("### Maç sonu")
    for line in summary_lines(summary):
        st.markdown(line)
    if live.history:
        with st.expander("📋 Menajer müdahaleleri"):
            for item in live.history:
                st.markdown(f"- {item}")
    if live.is_fixture:
        if live.saved:
            st.success("Sonuç kariyerine işlendi.")
        else:
            st.warning("Sonucu kariyerine işlemek için **💾 Sonucu kaydet**'e bas.")


def live_match_screen(live: LiveMatch, delay: float, tempo: float, show_pitch: bool) -> None:
    stale = live_is_stale(live)
    if stale:
        st.warning("Bu canlı maç artık geçerli değil (hafta ilerledi ya da maç başka yoldan oynandı); "
                   "sonuç kaydedilemez. **✖ Maçı kapat** ile çık.")
    if delay == 0 and not live.paused and not live.finished:
        live.run()                         # anında: bir sonraki duraklamaya ya da maç sonuna kadar

    team = live.managed_team
    parts = [live.title or "Canlı maç", SUB_RULE_LABELS[live.sub_rule]]
    if team is not None:
        parts.append(f"yönettiğin takım: {team.name}")
    st.markdown("**" + parts[0] + "** · " + " · ".join(parts[1:]))

    c1, c2, c3, c4 = st.columns(4)
    if not live.finished:
        if live.paused:
            c1.button("▶ DEVAM", key="live_resume", on_click=cb_live_resume, type="primary",
                      use_container_width=True)
        else:
            c1.button("⏸ DURDUR", key="live_pause", on_click=cb_live_pause, type="primary",
                      use_container_width=True)
        c2.button("⏭ Sonucu gör", key="live_finish", on_click=cb_live_finish, use_container_width=True,
                  help="Kalan dakikaları durmadan oynatır.")
    if live.is_fixture and live.finished and not live.saved and not stale:
        c3.button("💾 Sonucu kaydet", key="live_save", on_click=cb_live_save, type="primary",
                  use_container_width=True)
    if not live.is_fixture or live.saved or stale:
        c4.button("✖ Maçı kapat", key="live_close", on_click=cb_live_close, use_container_width=True)
    if live.paused and live.pause_reason and not live.finished:
        st.info(f"⏸ Maç durdu — {live.pause_reason}. Değişikliklerini yap, sonra **▶ DEVAM**'a bas.")

    slots = match_slots(with_feed=False)
    if team is not None and not live.finished:
        intervention_panels(live)
    slots.feed = feed_slot()
    result = live.snapshot()
    frames = build_timeline(result)
    scene = draw_live(slots, live, result, frames, len(frames) - 1, None, tempo, show_pitch)
    if live.finished:
        live_final(live, result, frames, slots)
        return
    if live.paused or delay == 0:
        return

    shown = len(frames)
    while not live.paused and not live.finished:
        live.tick()
        result = live.snapshot()
        frames = build_timeline(result)
        if len(frames) > shown:
            for i in range(shown, len(frames)):
                scene = draw_live(slots, live, result, frames, i, scene, tempo, show_pitch)
                time.sleep(delay * frames[i].pacing)
            shown = len(frames)
        else:
            draw_live(slots, live, result, frames, len(frames) - 1, scene, tempo, False, flash_board=False)
            time.sleep(delay * LIVE_MINUTE_SHARE)
    st.rerun()                              # duraklama / mac sonu: paneller guncel durumla cizilsin


def live_tab(teams: list[str]) -> None:
    show_flash("live")
    live = st.session_state.get("live")
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    mode = c1.radio("Mod", LIVE_MODES, key="live_mode", horizontal=True, disabled=live is not None)
    speed = c2.select_slider("Maç hızı", options=list(SPEEDS), value="Normal", key="live_speed")
    tempo = c3.slider("Animasyon temposu", min_value=0.5, max_value=2.0, value=1.0, step=0.25, key="live_tempo",
                      help="Saha animasyonlarının süresi: 0.5 hızlı, 2.0 ağır çekim.")
    show_pitch = c4.toggle("2D saha", value=True, key="live_pitch")
    delay = SPEEDS[speed]

    if live is not None:
        live_match_screen(live, delay, tempo, show_pitch)
        return
    if mode == LIVE_MANAGE:
        manage_setup()
        return

    recorded = {
        label: st.session_state.get(key)
        for label, key in (("Lig maçı", "last_user_result"), ("Devler Arenası maçı", "last_user_cup_result"))
        if st.session_state.get(key) is not None
    }
    if mode == LIVE_FRIENDLY:
        h1, h2, h3, h4 = st.columns([2, 2, 1, 1])
        home = h1.selectbox("Ev sahibi", teams, index=0, key="live_home")
        away = h2.selectbox("Deplasman", teams, index=1 if len(teams) > 1 else 0, key="live_away")
        seed_text = h3.text_input("Tohum", value="", key="live_seed")
        knockout = h4.toggle("Eleme maçı", value=False, key="live_knockout",
                             help="Beraberlikte uzatma ve penaltılar oynanır (tarafsız saha).")
        side = st.radio("Yönettiğim takım", SIDE_LABELS, key="live_side", horizontal=True,
                        help="Yönettiğin takımda maçı durdurup değişiklik ve taktik talimatı verebilirsin.")
        if side != SIDE_WATCH:
            live_setup_options()
    elif len(recorded) > 1:
        st.selectbox("Maç", list(recorded), key="live_which")
    start = st.button("▶ Maçı başlat", key="live_start", type="primary",
                      on_click=cb_live_start_friendly if mode == LIVE_FRIENDLY else None)

    if not start:
        if mode == LIVE_FRIENDLY:
            st.markdown(scoreboard_html(home, away, None), unsafe_allow_html=True)
        st.info("Ayarları seç ve **Maçı başlat**'a bas.")
        return

    if mode == LIVE_FRIENDLY:
        if home == away:
            st.error("Bir takım kendisiyle oynayamaz.")
            return
        seed = parse_seed(seed_text)
        with session_scope() as db:
            result = friendly_result(db, home, away, seed, knockout)
        st.caption("Hazırlık maçı — veritabanına kaydedilmez."
                   + (" Eleme kuralları: beraberlikte uzatma ve penaltılar." if knockout else ""))
        play_live(result, delay, tempo, show_pitch)
        return

    if not recorded:
        st.warning("Henüz izlenecek maç yok. Haftayı **🏆 Lig** ya da **⭐ Devler Arenası** sekmesinden oyna.")
        return
    choice = st.session_state.get("live_which") if len(recorded) > 1 else next(iter(recorded))
    play_live(recorded.get(choice) or next(iter(recorded.values())), delay, tempo, show_pitch)


# ===========================================================================
# GIRIS
# ===========================================================================

MODE_CSS = """
<style>
.cm-mode-card{border:1px solid rgba(128,128,128,.35);border-radius:14px;padding:1rem 1.2rem;
  margin-bottom:.6rem;background:rgba(128,128,128,.06)}
.cm-mode-card h3{margin:.1rem 0 .4rem}
.cm-mode-card p{margin:0;opacity:.85}
</style>
"""


def login_screen() -> None:
    """Giris / kayit (CM retro karsilama paneli). Oturum yokken yalnizca bu ekran cizilir."""
    st.markdown(login_banner_html(), unsafe_allow_html=True)
    _left, mid, _right = st.columns([1, 2, 1])
    with mid:
        show_flash("auth")
        login_tab, register_tab = st.tabs(["🔑 Giriş Yap", "📝 Kayıt Ol"])
        with login_tab:
            st.text_input("Kullanıcı adı", key="login_user")
            st.text_input("Parola", type="password", key="login_pass")
            st.button("Giriş yap", key="login_btn", on_click=cb_login, type="primary", use_container_width=True)
        with register_tab:
            st.text_input("Kullanıcı adı", key="reg_user",
                          help="3-32 karakter: harf, rakam, _ . - (harf ya da rakamla başlamalı)")
            st.text_input("Parola", type="password", key="reg_pass",
                          help="En az 8 karakter; en az bir harf ve bir rakam; kullanıcı adını içermemeli.")
            st.text_input("Parola (tekrar)", type="password", key="reg_pass2")
            st.button("Kayıt ol ve kariyere başla", key="reg_btn", on_click=cb_register, type="primary",
                      use_container_width=True)
            st.caption("Parolan şifrelenmiş (scrypt) olarak saklanır. Her menajerin kariyeri kendine aittir: "
                       "ilk kayıt olan mevcut kariyeri devralır, sonrakilere yeni bir dünya kurulur.")


def main() -> None:
    st.set_page_config(page_title="CM Menajer Paneli", page_icon="⚽", layout="wide")
    st.markdown(CSS + pitch.PITCH_CSS + BRACKET_CSS + MODE_CSS + CM_THEME_CSS, unsafe_allow_html=True)
    st.title("⚽ CM — Menajer Paneli")

    if not wait_for_db(retries=2, delay=0.5, verbose=False):
        st.error("Veritabanına bağlanılamadı. `docker compose up -d` çalışıyor mu?")
        st.stop()
    auth = st.session_state.get("auth")
    if auth is None:
        login_screen()
        return
    if st.session_state.get("career_ready") != auth.career_schema:
        # Eski kayitlar: eksik sutunlar eklenir, potansiyel/akademi doldurulur (kariyer silinmez)
        applied = accounts.ensure_career_ready(auth)
        st.session_state["career_ready"] = auth.career_schema
        if applied:
            flash("sidebar", "info", "Kariyer kaydı yeni sürüme yükseltildi: " + "; ".join(applied[:4])
                  + (" …" if len(applied) > 4 else ""))
    problems = schema_problems()
    if problems:
        st.error("Veritabanı şeması bu sürümden eski (" + ", ".join(problems[:4]) + "). "
                 "`python seed.py` ile yeniden kur — kariyer kaydı sıfırlanır.")
        st.stop()
    teams = load_teams()
    if len(teams) < 2:
        st.warning("Veritabanında takım yok. Önce `python seed.py` çalıştır.")
        st.stop()

    with session_scope() as db:
        cm = manager(db)
        chosen, mode = cm.mode_chosen, cm.game_mode
    if not chosen:
        mode_screen()
        return

    sidebar(teams)
    names = CAREER_TABS if mode is GameMode.CAREER else TOURNAMENT_TABS
    tabs = dict(zip(names, st.tabs(names), strict=True))
    renderers = {TAB_SQUAD: squad_tab, TAB_ACADEMY: academy_tab, TAB_FINANCE: finance_tab,
                 TAB_MARKET: transfer_tab, TAB_LEAGUE: league_tab, TAB_STAFF: staff_tab}

    # Yonetim sekmeleri once cizilir; canli mac dongusu en sonda (bkz. dosya basligi)
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        with tabs[TAB_ARENA]:
            arena_tab(db, cm, team)
        for name, render in renderers.items():
            if name not in tabs:
                continue
            with tabs[name]:
                if team is None:
                    st.info("Önce kenar çubuğundan takımını seç ve **Takımı ayarla**'ya bas.")
                    continue
                render(db, cm, team)

    with tabs[TAB_LIVE]:
        live_tab(teams)


# Oturum kapisi (P1 guvenlik): giris/kayit/cikis disindaki TUM callback'ler oturum ister.
# Widget'lar callback'i cizim aninda global adla alir; sarma, main() calismadan once yapilir.
for _name, _callback in list(globals().items()):
    if _name.startswith("cb_") and callable(_callback) and _name not in PUBLIC_CALLBACKS:
        globals()[_name] = requires_auth(_callback)


if __name__ == "__main__":
    main()
