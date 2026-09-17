"""
web_app.py
==========
CM Menajer Paneli -- tamamen tarayici tabanli kariyer arayuzu.

    streamlit run web_app.py

Giris (10. Asama): menajer hesabi. Giris yapmayan kullanici HICBIR oyun sekmesine erisemez;
giris / kayit ekranina yonlendirilir. Oturum st.session_state["auth"] (accounts.AuthSession)
ile tutulur; her menajerin kariyeri kendi PostgreSQL semasindadir ve bu dosyanin kaydettigi
cozucu (session_career_schema) veritabani islemlerini oturumdaki kullanicinin kariyerine yonlendirir.
5 hatali denemeden sonra giris 30 sn kilitlenir. Arayuz OFM temalarindadir (ofm_theme.py): menajer
⚽ FM Dark / ☀️ FM Light secer; secim st.session_state["theme"] ve ?theme= URL parametresinde tutulur
(sayfa yenilense de kalir, giris/cikista korunur);
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

Paylasilan dunyalar (Faz 12 / 14. Asama):
    * Ortak yardimcilar (flash, show_flash, reset_widgets, money, live_fixture_pending, manager, oturum
      dekoratorleri) web_common.py'dedir; bu dosya ayni adlarla yeniden disari acar. Callback govdeleri
      manager / session_scope'u BU modulun global adlariyla cozer (testler web_app.manager'i degistirebilir).
    * Oyun callback'leri modul sonunda web_common.member_callback ile sarilir (oturum + dunya uyeligi +
      paylasilan dunyada SHARED dunya kilidi); gorunum modullerindeki callback'ler kendi dekoratorlerini tasir.
    * Eski (dunyaya bagli olmayan) oturum bugunku ekrani birebir cizer. Yalnizca paylasilan / milli takimli
      dunyada ek sekmeler (TAB_HUB, TAB_NATIONAL, TAB_ADMIN), kenar cubugu dunya paneli ve lobi yolu acilir.
    * Giris / kayit ekrani login_view.py'dedir (login_screen yalnizca callback'leri verir).
    * Faz 12 A4: giriste oturum hesabin varsayilan dunyasina baglanir (worlds.default_world: son girilen dunya, yoksa
      kisisel kariyer; kisisel kariyer bugunku ekrani birebir cizer). Kenar cubugu "Dunyalar" (sb_worlds) lobiyi
      acar (world_lobby_view). Her cizimde uyelik yeniden okunur (web_common.current_world); dusmusse varsayilan
      dunyaya ya da lobiye yonlenir. Paylasilan dunyada: sayfa yuklenirken suresi dolan hafta oynatilir
      (world_panel_view.advance_if_due, beklemez), kulubu olmayan koltuk kulup secer (render_club_offers), takim
      secici / mod degistirme / kariyer tohumu yerine dunya paneli, "Sonraki haftayi oyna" / "Yeni sezon" yerine
      hazir paneli (lg_ready), resmi canli mac yerine bilgi (hazirlik maci canli kalir), kura dugmeleri yalnizca
      sahip / yoneticide, son hafta raporu veritabanindan (manager_week_reports).
    * Transfer tamamlama (_complete) ve personel alimi (cb_hire) satir kilitleriyle yeniden dogrular (oyuncu ->
      kulupler -> personel sirasi); alici her zaman cm.user_team, satici teklif anindaki kulup (expected_seller_id).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from streamlit.runtime.scriptrunner import get_script_run_ctx

import accounts
import arena_views as av
import career_views as cv
import database
import login_view
import market_view
import national_view
import pitch
import preview_views
import reputation
import staff as staff_rules
import team_roles
import world_admin_view
import world_lobby_view
import world_panel_view
import worlds
from auth import AuthError
from bracket_view import (
    BRACKET_CSS,
    bracket_html,
    champion_banner_html,
    draw_board_html,
    group_tables_html,
    pair_reveal_html,
)
from career_manager import (
    ACADEMY_CAPACITY,
    MAX_TACTIC_PRESETS,
    SENIOR_SQUAD_MAX,
    AcademyError,
    CareerManager,
    ConcernError,
    FacilityError,
    FriendlyError,
    LiveMatchError,
    SeasonNotFinished,
    ShortlistError,
    TacticsError,
)
from cup_draw import FORMAT_LABELS, CupFormat, DrawComplete, formats_for
from database import schema_problems, session_scope, wait_for_db
from finance import BudgetError, format_money, preview_budget_shift, wage_budget_bounds
from fitness import condition_band
from instructions import (
    FIELD_LABELS,
    FOCUS_LABELS,
    MENTALITY_LABELS,
    PASSING_LABELS,
    PRESSING_LABELS,
    TACKLING_LABELS,
    TEMPO_LABELS,
    TeamInstructions,
    parse_mentality,
    parse_tackling,
)
from live_match import SUB_RULE_LABELS, LiveMatch, engine_config_for
from match_engine import InterventionError, KnockoutRule, MatchEngine, build_match_team
from match_feed import SideStats, build_timeline, summarize, team_energy_at
from match_plan import (
    MAX_PLAN_RULES,
    SITUATION_LABELS,
    MatchPlan,
    PlanAction,
    PlanError,
    PlanRule,
    PlanTrigger,
)
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
from ofm_theme import (
    APP_NAME,
    APP_SHORT,
    LANG_SCRIPT,
    THEME_LABELS,
    normalize_theme,
    panel_title_html,
    stat_strip_html,
    theme_css,
)
from stars import FILTER_OPTIONS, star_glyphs, star_threshold, stars
from tactics import FORMATIONS, MATCH_FORMATIONS, arrange_slots
from tournament_manager import TournamentError, matchday_label
from transfers import ROLE_LABELS, ContractOffer, NegotiationStatus, TransferError
from web_common import (
    BUSY_TEXT,
    WORLD_KIND_SHARED,
    admin_callback,  # noqa: F401 -- gorunum modulleri ve testler icin web_app adinda da acik
    callback_is_admin,
    career_seed,
    current_world,
    flash,
    is_lock_timeout,
    is_shared_world,  # noqa: F401 -- WP0 adi (testler / gorunum modulleri)
    live_fixture_pending,
    manager,
    md_escape,
    member_callback,
    money,
    page_is_admin,
    parse_seed,  # noqa: F401 -- testler web_app.parse_seed kullanir
    requires_auth,  # noqa: F401 -- eski ad: oturum kapisi dekoratoru
    reset_widgets,
    shared_page_world,
    show_flash,
    show_lobby,
    unbind_world,
    world_rules_for,
)
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
from world_rules import WorldRules

SPEEDS = {"Yavaş": 1.2, "Normal": 0.55, "Hızlı": 0.2, "Anında": 0.0}
TAB_LIVE, TAB_SQUAD, TAB_FINANCE, TAB_MARKET = "🏟️ Canlı Maç", "📋 Kadro & Taktik", "💰 Finans", "🔄 Transfer Pazarı"
TAB_LEAGUE, TAB_ARENA, TAB_STAFF = "🏆 Lig", "⭐ Devler Arenası", "👥 Teknik Heyet"
TAB_ACADEMY = "🎓 Altyapı Akademisi (U-21)"
TAB_CLUB = "🏛️ Kulüp Yönetimi & Tesisler"
TAB_PREP = "🎯 Taktik Merkezi"
TAB_WORLD = "📰 Haberler & Tarih"
CAREER_TABS = [TAB_LIVE, TAB_SQUAD, TAB_PREP, TAB_ACADEMY, TAB_FINANCE, TAB_CLUB, TAB_MARKET, TAB_LEAGUE, TAB_WORLD,
               TAB_ARENA, TAB_STAFF]
WORLD_NEWS, WORLD_HONOURS, WORLD_TRANSFERS = "📰 Haber akışı", "🏅 Onur listesi", "💸 Transfer kayıtları"
WORLD_SECTIONS = [WORLD_NEWS, WORLD_HONOURS, WORLD_TRANSFERS]
PREP_FRIENDLY = "🤝 Hazırlık maçı"
PREP_PREVIEW, PREP_SCOUT, PREP_PLANNER = "📰 Maç önü raporu", "🔭 Rakip scout raporu", "🗂️ Kadro planlayıcı"
PREP_ORDERS, PREP_PLAN, PREP_PRESETS = "🧠 Talimatlar & duran toplar", "⏱️ Maç planı", "💾 Kayıtlı taktikler"
PREP_SECTIONS = [PREP_PREVIEW, PREP_SCOUT, PREP_ORDERS, PREP_PLAN, PREP_PRESETS, PREP_FRIENDLY, PREP_PLANNER]
# (alan, etiket sozlugu, yardim): Taktik Merkezi talimat secicileri ve mac plani kurali ayni sirayi kullanir
INSTRUCTION_CHOICES = [
    ("mentality", MENTALITY_LABELS, "Takımın risk alma düzeyi: hücum sayısı ile savunma güvenliği arasında denge."),
    ("tackling", TACKLING_LABELS, "Sert oyun savunmayı güçlendirir ama kart ve sakatlık riskini artırır."),
    ("passing_style", PASSING_LABELS, "Kısa pas topa sahip olur ve daha az yorar; direkt oyun daha çok şut üretir."),
    ("tempo", TEMPO_LABELS, "Hızlı tempo daha çok atak ve daha çok yorgunluk demektir."),
    ("pressing", PRESSING_LABELS, "Tüm sahada pres rakip orta sahayı boğar ama belirgin şekilde yorar."),
    ("attacking_focus", FOCUS_LABELS, "Kanatlar orta/kafa, merkez teknik ve bitiricilik gücünü kullanır."),
]
PLAN_KEEP = "— Değiştirme —"
PLAN_INSTRUCTION_FIELDS = ("mentality", "tackling", "passing_style", "tempo", "pressing")
TOURNAMENT_TABS = [TAB_LIVE, TAB_ARENA, TAB_SQUAD, TAB_STAFF]
TABS = CAREER_TABS
# Faz 12 / 14. Asama: YALNIZCA paylasilan / milli takimli dunyada eklenen sekmeler (eski listeler degismez)
TAB_HUB = "📨 Teklifler & Mesajlar"
TAB_NATIONAL = "🌍 Milli Takım"
TAB_ADMIN = "🛡️ Dünya Yönetimi"
WORLD_TABS = [TAB_HUB, TAB_NATIONAL, TAB_ADMIN]
TAB_CLUBS = "🏟️ Kulübünü Seç"                  # paylasilan dunyada kulubu olmayan koltugun sekmesi
SHARED_WEEK_TEXT = "Paylaşılan dünyada hafta, menajerler hazır olunca ya da süre dolunca ilerler (kenar çubuğu paneli)."
SHARED_TEAM_TEXT = "Paylaşılan dünyada kulübünü dünya panelinden seçersin."
SHARED_LIVE_TEXT = ("🌍 Paylaşılan dünyada resmi maçlar hafta ilerlerken birlikte oynanır: kadronu **📋 Kadro & Taktik**, "
                    "talimatlarını **🎯 Taktik Merkezi** sekmesinde hazırla, sonra hazır ol. Hazırlık maçlarını canlı "
                    "yönetebilirsin (Mod: Hazırlık maçı).")
DRAW_ADMIN_TEXT = ("Bu dünyada kurayı dünyanın sahibi ya da yöneticisi çeker; kimse çekmezse hafta ilerlerken kura "
                   "otomatik tamamlanır.")
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

# Giris gerektirmeyen callback'ler; digerleri modul sonunda member_callback ile sarilir
PUBLIC_CALLBACKS = frozenset({"cb_login", "cb_register", "cb_logout", "cb_theme", "cb_auth_view"})


# ===========================================================================
# ORTAK YARDIMCILAR (flash, show_flash, reset_widgets, money, manager, parse_seed: web_common.py)
# ===========================================================================

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

def current_theme() -> str:
    """Oturumdaki tema; yoksa URL'deki ?theme= (sayfa yenilemesi), o da yoksa FM Dark. URL guncel tutulur."""
    theme = st.session_state.get("theme")
    if theme not in THEME_LABELS:
        theme = normalize_theme(st.query_params.get("theme"))
        st.session_state["theme"] = theme
    if st.query_params.get("theme") != theme:
        st.query_params["theme"] = theme
    if st.session_state.get("theme_choice") != THEME_LABELS[theme]:
        st.session_state["theme_choice"] = THEME_LABELS[theme]
    return theme


def cb_theme() -> None:
    """Tema secimi (girissiz de calisir): oturuma ve URL'ye yazilir."""
    theme = normalize_theme(st.session_state.get("theme_choice"))
    st.session_state["theme"] = theme
    st.query_params["theme"] = theme


def cb_auth_view(view: str) -> None:
    st.session_state["auth_view"] = view


def _clear_session() -> None:
    """Oturum durumu temizlenir; yalnizca gorsel tercih (tema) korunur."""
    theme = st.session_state.get("theme")
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if theme in THEME_LABELS:
        st.session_state["theme"] = theme


def start_session(session: accounts.AuthSession) -> None:
    """
    Yeni oturum: onceki kullanicinin ekran durumu (widget, rapor, canli mac) tasinmaz; tema kalir.
    Faz 12: oturum hesabin varsayilan dunyasina baglanir (son girilen dunya, yoksa kisisel kariyer). Baglanamazsa
    (kayit okunamadi) oturum hesabin KENDI kariyerinde dunyasiz kalir (eski davranis; baska kariyere dusmez).
    """
    _clear_session()
    try:
        ctx = worlds.default_world(session)
        session = worlds.session_for(session, ctx)
    except (worlds.WorldError, SQLAlchemyError, ValueError):
        ctx = None
    st.session_state["auth"] = session
    where = (f"«{md_escape(ctx.name)}» dünyası yüklendi." if ctx is not None and ctx.kind == WORLD_KIND_SHARED
             else "Kariyerin yüklendi.")
    flash("sidebar", "success", f"Hoş geldin, {session.username}! {where}")


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
    _clear_session()
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


def cb_wage_demand(player_id: int, accept: bool) -> None:
    """Oyuncunun yeni sozlesme (maas) talebine cevap: kabul -> maas artar, red -> moral duser."""
    with session_scope() as db:
        cm = manager(db)
        player = db.get(Player, player_id)
        if player is None or cm.user_team is None:
            return
        try:
            message = cm.respond_wage_demand(player, accept)
        except ConcernError as exc:
            db.rollback()
            flash("squad", "error", str(exc))
            return
    flash("squad", "success" if accept else "warning", message)
    reset_widgets("tac_editor", "tac_rows")


def cb_shortlist_toggle(player_id: int) -> None:
    with session_scope() as db:
        cm = manager(db)
        player = db.get(Player, player_id)
        if player is None or cm.user_team is None:
            return
        try:
            if cm.is_shortlisted(player_id):
                cm.shortlist_remove(player_id)
                text = f"☆ {player.name} takip listesinden çıkarıldı."
            else:
                cm.shortlist_add(player)
                text = f"⭐ {player.name} takip listesine eklendi."
        except ShortlistError as exc:
            db.rollback()
            flash("market", "error", str(exc))
            return
    flash("market", "success", text)


def cb_shortlist_remove() -> None:
    player_id = st.session_state.get("sl_pick")
    if player_id is None:
        return
    with session_scope() as db:
        manager(db).shortlist_remove(player_id)
    flash("market", "success", "☆ Oyuncu takip listesinden çıkarıldı.")
    reset_widgets("sl_pick")


def cb_play_friendly() -> None:
    opponent_id = st.session_state.get("fr_opponent")
    with session_scope() as db:
        cm = manager(db)
        opponent = db.get(Team, opponent_id) if opponent_id is not None else None
        if opponent is None or cm.user_team is None:
            return
        try:
            result = cm.play_friendly(opponent)
        except FriendlyError as exc:
            db.rollback()
            flash("prep", "error", str(exc))
            return
        goals = [f"{g['minute']}' {g['player']} ({g['team']})" for g in result.goals]
    flash("prep", "success", f"🤝 Hazırlık maçı: {result.home_team_name} {result.score} {result.away_team_name}"
                             + (" · goller: " + ", ".join(goals) if goals else ""))


def _error_text(exc: Exception) -> str:
    """PlanError listesi / tek mesaj -> okunur metin."""
    arg = exc.args[0] if exc.args else exc
    return " ".join(arg) if isinstance(arg, list | tuple) else str(arg)


def _tactics_action(action, success: str | None, *reset: str) -> None:
    """Taktik Merkezi islemi: kullanici takimi uzerinde calisir; hata olursa hicbir sey yazilmaz."""
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        if team is None:
            return
        try:
            message = action(cm, team)
        except (TacticsError, PlanError, ValueError) as exc:
            db.rollback()
            flash("prep", "error", _error_text(exc))
            return
    if success or message:
        flash("prep", "success", success or message)
    reset_widgets(*reset)


def instruction_widget_values() -> dict:
    ss = st.session_state
    values = {field: ss.get(f"ord_{field}") for field, _, _ in INSTRUCTION_CHOICES}
    values["offside_trap"] = bool(ss.get("ord_offside_trap", False))
    values["counter_attack"] = bool(ss.get("ord_counter_attack", False))
    return values


def cb_save_instructions() -> None:
    def action(cm, team):
        instructions = TeamInstructions(**{k: v for k, v in instruction_widget_values().items() if v is not None})
        cm.set_team_instructions(team, instructions)
        return f"🧠 Takım talimatları kaydedildi: {instructions.describe()}"
    _tactics_action(action, None)


def cb_save_roles() -> None:
    roles = team_roles.SetPieceRoles(**{f: st.session_state.get(f"role_{f}") for f in team_roles.ROLE_FIELDS})

    def action(cm, team):
        cm.set_team_roles(team, roles)
        return "🎯 Kaptan ve duran top görevleri kaydedildi."
    _tactics_action(action, None)


def cb_suggest_roles() -> None:
    def action(cm, team):
        cm.set_team_roles(team, cm.suggest_team_roles(team))
        return "🤖 Asistan kaptan ve duran top atıcılarını belirledi."
    _tactics_action(action, None, *(f"role_{f}" for f in team_roles.ROLE_FIELDS))


def plan_rule_from_widgets() -> PlanRule:
    ss = st.session_state
    situation = next(k for k, v in SITUATION_LABELS.items() if v == ss.get("pl_situation"))
    margin = int(ss.get("pl_margin") or 0) or None
    changes = {f: ss.get(f"pl_{f}") for f in PLAN_INSTRUCTION_FIELDS if ss.get(f"pl_{f}") not in (None, PLAN_KEEP)}
    formation = ss.get("pl_formation")
    action = PlanAction(formation=None if formation in (None, PLAN_KEEP) else formation, instructions=changes,
                        sub_out_id=ss.get("pl_out"), sub_in_id=ss.get("pl_in"))
    if action.is_empty:
        raise ValueError("Kural bir şeyi değiştirmeli: diziliş, talimat ya da oyuncu değişikliği seç.")
    return PlanRule(PlanTrigger(int(ss.get("pl_minute") or 60), situation, margin), action,
                    name=str(ss.get("pl_name") or "").strip())


def cb_add_plan_rule() -> None:
    def action(cm, team):
        rule = plan_rule_from_widgets()
        cm.set_team_plan(team, MatchPlan(rules=(*cm.team_plan(team).rules, rule)))
        return f"⏱️ Kural eklendi: {rule.describe({p.id: p.name for p in team.players})}"
    _tactics_action(action, None, "pl_name", "pl_margin", "pl_formation", "pl_out", "pl_in",
                    *(f"pl_{f}" for f in PLAN_INSTRUCTION_FIELDS))


def cb_toggle_plan_rule(index: int) -> None:
    def action(cm, team):
        rules = list(cm.team_plan(team).rules)
        rules[index] = dc_replace(rules[index], enabled=not rules[index].enabled)
        cm.set_team_plan(team, MatchPlan(rules=tuple(rules)))
        return f"Kural {index + 1} {'açıldı' if rules[index].enabled else 'kapatıldı'}."
    _tactics_action(action, None)


def cb_delete_plan_rule(index: int) -> None:
    def action(cm, team):
        rules = list(cm.team_plan(team).rules)
        rules.pop(index)
        cm.set_team_plan(team, MatchPlan(rules=tuple(rules)))
        return f"🗑️ Kural {index + 1} silindi."
    _tactics_action(action, None)


TACTIC_WIDGETS = ("tac_formation", "tac_editor", "tac_rows", *(f"ord_{f}" for f, _, _ in INSTRUCTION_CHOICES),
                  "ord_offside_trap", "ord_counter_attack", *(f"role_{f}" for f in team_roles.ROLE_FIELDS))


def cb_save_preset() -> None:
    name = str(st.session_state.get("preset_name") or "")
    overwrite = bool(st.session_state.get("preset_overwrite", False))

    def action(cm, team):
        preset = cm.save_tactic_preset(team, name, overwrite=overwrite)
        return f"💾 Taktik kaydedildi: {preset.name}"
    _tactics_action(action, None, "preset_name", "preset_overwrite", "preset_pick")


def cb_apply_preset() -> None:
    preset_id = st.session_state.get("preset_pick")

    def action(cm, team):
        notes = cm.apply_tactic_preset(team, preset_id)
        return "✅ Taktik uygulandı" + (": " + " · ".join(notes) if notes else ".")
    _tactics_action(action, None, *TACTIC_WIDGETS)


def cb_delete_preset() -> None:
    preset_id = st.session_state.get("preset_pick")

    def action(cm, team):
        cm.delete_tactic_preset(team, preset_id)
        return "🗑️ Kayıtlı taktik silindi."
    _tactics_action(action, None, "preset_pick")


def cb_upgrade_facility(kind: str) -> None:
    """Tesis yatirimi (altyapi / saglik merkezi / stadyum): bedel transfer kasasindan duser."""
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        if team is None:
            return
        try:
            cost = cm.upgrade_facility(team, kind)
        except FacilityError as exc:
            db.rollback()
            flash("club", "error", str(exc))
            return
        status = cm.facility_status(team)
    block = status[kind]
    after = (f"{block['capacity']:,} koltuk".replace(",", ".") if kind == "stadium"
             else f"seviye {block['level']}/{block['max_level']}")
    flash("club", "success", f"🏗️ {block['label']} yükseltildi → {after} · bedel {format_money(cost)}")


def cb_sign_sponsor(index: int) -> None:
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        if team is None:
            return
        try:
            offer = cm.sign_sponsor(team, index)
        except FacilityError as exc:
            db.rollback()
            flash("club", "error", str(exc))
            return
    bonus = f" · imza primi {format_money(offer.signing_bonus)} kasaya eklendi" if offer.signing_bonus else ""
    flash("club", "success", f"🤝 {offer.brand} ile {offer.seasons} sezonluk sponsorluk imzalandı: "
                             f"haftalık {format_money(offer.weekly)}{bonus}.")


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
        if cm.rules.shared:                       # Faz 12: kulup yalnizca dunya panelinden (claim_club) alinir
            flash("sidebar", "error", SHARED_TEAM_TEXT)
            return
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
        if team is None:
            return
        cm.lock_rows(Team, [team.id])             # Faz 12: kasa hafta ilerlemesi / transferle ayni anda yazilmasin
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
            "seller_id": player.team_id, "fee": fee, "log": log, "needs_room": None,
        }
        _reset_offer_inputs(negotiation)


def _reset_offer_inputs(negotiation) -> None:
    demand = negotiation.demand
    st.session_state["neg_wage"] = int(demand.wage)
    st.session_state["neg_years"] = int(demand.years)
    st.session_state["neg_role"] = demand.role.value


def _negotiating_team(cm: CareerManager, neg: dict) -> Team:
    """Sozlesme masasinin alicisi: HER ZAMAN oturumdaki menajerin kulubu (session_state'teki id'ye guvenilmez)."""
    buyer = cm.user_team
    if buyer is None or buyer.id != neg.get("buyer_id"):
        raise TransferError("Kulübün değişti; bu sözleşme masası artık geçersiz.")
    return buyer


def _complete(db, cm: CareerManager, neg: dict, offer: ContractOffer, shift: int = 0) -> None:
    """
    Faz 12: satir kilitleri (oyuncu -> kulupler, id sirasiyla) altinda yeniden dogrulanir: oyuncu hala teklif anindaki
    kulupte mi (expected_seller_id), butceler guncel mi. shift > 0: once maas alani acilir (ayni islem).
    """
    buyer = _negotiating_team(cm, neg)
    locked = cm.lock_rows(Player, [neg["player_id"]])
    if not locked:
        raise TransferError("Oyuncu bulunamadı.")
    player = locked[0]
    seller_id = neg.get("seller_id", player.team_id)
    if player.team_id is None or player.team_id != seller_id:
        raise TransferError(f"{player.name} artık bu kulübün oyuncusu değil; teklif geçersiz.")
    cm.lock_rows(Team, [buyer.id, seller_id])
    if shift:
        cm.shift_budget(buyer, shift)
    news = cm.complete_transfer(buyer, player, neg["fee"], offer, expected_seller_id=seller_id)
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
        try:
            buyer = _negotiating_team(cm, neg)
            if offer.wage > buyer.free_wage:
                neg["needs_room"] = offer.wage - buyer.free_wage
                neg["agreed"] = offer
                return
            _complete(db, cm, neg, offer)
        except TransferError as exc:
            db.rollback()
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
        try:
            _complete(db, cm, neg, neg["agreed"], shift=int(neg["needs_room"]))
        except (BudgetError, TransferError) as exc:
            db.rollback()                         # kaydirma transfer olmadan kalici olmasin
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
        if cm.rules.shared:                       # Faz 12: hafta yalnizca tur motoruyla (hazir / sure / yonetici)
            for area in ("league", "arena"):
                flash(area, "error", SHARED_WEEK_TEXT)
            return
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
        if cm.rules.shared:                       # Faz 12: yeni sezon tur motorunun ilerlemesiyle baslar
            flash("league", "error", SHARED_WEEK_TEXT)
            flash("arena", "error", SHARED_WEEK_TEXT)
            return
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


def _locked_text(cm: CareerManager) -> str:
    t = cm.tournaments.current()
    fixtures = len(cm.tournaments.fixtures(t, stage=cm.tournaments.stages(t)[0]))
    return f"🔒 Kura tamamlandı ve kilitlendi — {fixtures} maçlık ilk tur fikstürü doğrulanıp kaydedildi."


def _draw_allowed() -> bool:
    """Faz 12: paylasilan dunyada kurayi yalnizca sahip / yonetici ceker (uyelik rolu dekoratorun denetiminden)."""
    if callback_is_admin():
        return True
    flash("arena", "error", DRAW_ADMIN_TEXT)
    return False


def cb_draw_pair() -> None:
    """Kura gecesi tiklamasi: bir eslesme (iki top) ya da grup kurasinda bir top."""
    if not _draw_allowed():
        return
    with session_scope() as db:
        cm = manager(db)
        try:
            cm.tournaments.draw_pair()
        except (TournamentError, DrawComplete) as exc:
            db.rollback()                        # dogrulanmayan kura kalici olmaz
            flash("arena", "error", str(exc) or "Kura zaten tamamlandı.")
            return
        if cm.tournaments.current().status is not TournamentStatus.DRAW:
            flash("arena", "success", _locked_text(cm))


def cb_draw_all() -> None:
    if not _draw_allowed():
        return
    with session_scope() as db:
        cm = manager(db)
        try:
            steps = cm.tournaments.draw_all()
        except (TournamentError, DrawComplete) as exc:
            db.rollback()
            flash("arena", "error", str(exc) or "Kura zaten tamamlandı.")
            return
        flash("arena", "success", f"Kalan {len(steps)} top otomatik çekildi. " + _locked_text(cm))


def cb_set_cup_format() -> None:
    value = st.session_state.get("arena_format")
    if not _draw_allowed():
        return
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
        "use_saved": st.session_state.get("live_use_saved", True),
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
        if not competitive_live_ok(cm):
            flash("live", "error", SHARED_LIVE_TEXT)
            return
        try:
            prep = cm.prepare_live_match(config=engine_config_for(setup["sub_rule"], cm.engine_config))
        except LiveMatchError as exc:
            db.rollback()
            flash("live", "error", str(exc))
            return
        if prep.midweek_report is not None and prep.midweek_report.played_any:
            store_week_report(prep.midweek_report)
            flash("live", "info", "Önce hafta içi Devler Arenası maçları oynandı; kondisyonlar güncel.")
    # Kayitli taktik: prepare_live_match talimat/rol/plani motora zaten uygular (None = motordakini koru)
    live = LiveMatch.create(
        prep.engine, prep.managed_team_id, auto_subs=setup["auto_subs"],
        instructions=None if setup["use_saved"] else setup["instructions"],
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
        if cm.rules.shared:                       # Faz 12: sonucu kaydetmek haftayi tur motoru disinda ilerletirdi
            flash("live", "error", SHARED_WEEK_TEXT)
            return
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
        team = cm.user_team
        if team is None or staff_id is None:
            return
        # Faz 12: iki kulup ayni bostaki personeli ayni anda alamasin: kilit (kulup -> personel) altinda yeniden dogrula
        cm.lock_rows(Team, [team.id])
        locked = cm.lock_rows(Staff, [staff_id])
        member = locked[0] if locked else None
        if member is None:
            return
        try:
            cm.hire_staff(team, member)
            flash("staff", "success", f"{member.name} kadroya katıldı ({format_money(member.wage)}/hafta).")
        except TransferError as exc:
            db.rollback()
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

def sidebar_account() -> None:
    """Kenar cubugu ust bolumu: menajer, cikis, tema (st.sidebar icinde; lobi sayfasi da kullanir)."""
    auth = st.session_state.get("auth")
    if auth is not None:
        u1, u2 = st.columns([3, 2])
        u1.markdown(f"👤 **{escape(auth.username)}**")
        u2.button("Çıkış", key="sb_logout", on_click=cb_logout, width="stretch",
                  help="Oturumu kapatır; kaydedilmemiş canlı maç kaybolur.")
    st.radio("Tema", list(THEME_LABELS.values()), key="theme_choice", horizontal=True, on_change=cb_theme)
    browser = getattr(getattr(st.context, "theme", None), "type", None)
    chosen = st.session_state.get("theme")
    if browser in THEME_LABELS and chosen in THEME_LABELS and browser != chosen:
        st.caption("Tablolar tarayıcı temasıyla çizilir: tam uyum için sağ üst ⋮ → Settings → Theme → "
                   + ("Light" if chosen == "light" else "Dark") + ".")


def sidebar(teams: list[str], world: worlds.WorldContext | None = None) -> None:
    """world: paylasilan dunyanin baglami (current_world); verilirse takim secici / mod / tohum yerine dunya paneli."""
    shared = world is not None and world.kind == WORLD_KIND_SHARED
    with st.sidebar:
        sidebar_account()
        st.header("Kariyer")
        show_flash("sidebar")
        with session_scope() as db:
            cm = manager(db)
            team = cm.user_team
            current = team.name if team else None
            mode = cm.game_mode
            total = cm.total_weeks()
            if not shared:                        # paylasilan dunyada sezon / hafta dunya panelinde
                st.caption(f"{MODE_LABELS[mode]} · Sezon {cm.season} · Hafta {min(cm.current_week, total)} / {total}"
                           + (" · sezon bitti" if cm.season_finished else ""))
            rep = cm.manager_reputation
            lvl = reputation.level(rep)
            st.caption(f"{reputation.badge(lvl)} {lvl.title} · seviye {lvl.level}/10 · "
                       f"Menajer tanınırlığı {rep:.1f}/20 · {reputation.label(rep)}")
            if lvl.next_at is not None:
                st.progress(max(0.0, min(1.0, lvl.progress)),
                            text=f"Sonraki unvan: tanınırlık {lvl.next_at:.1f}")
            if team is not None and mode is GameMode.CAREER:
                st.caption(f"Transfer {format_money(team.transfer_budget)} · "
                           f"maaş havuzu {format_money(team.wage_budget)}/hf")
            t = cm.tournaments.current()
            st.caption(f"⭐ Devler Arenası: {cm.tournaments.user_status(t, team.id if team else None)}")
            if not shared:
                teams = selectable_teams(cm, teams)
                can_change = cm.can_change_mode()

        if shared:
            # Faz 12: paylasilan dunyada kulup secimi, mod degisikligi ve tohum yerine dunya paneli (sb_worlds dahil)
            world_panel_view.sidebar_panel(world, current)
            return
        index = teams.index(current) if current in teams else 0
        chosen = st.selectbox("Takımın", teams, index=index, key="sb_team")
        live = st.session_state.get("live")
        live_pending = live is not None and live.is_fixture and not live.saved
        st.button("Takımı ayarla", key="sb_set_team", on_click=cb_set_team,
                  disabled=chosen == current or live_pending, width="stretch",
                  help="Kaydedilmemiş canlı maç varken takım değiştirilemez." if live_pending else None)
        st.button("🔁 Oyun modunu değiştir", key="sb_change_mode", on_click=cb_reset_mode,
                  disabled=not can_change or live_pending, width="stretch",
                  help="Yalnızca sezon başında, hiç maç oynanmamışken.")
        with st.expander("Gelişmiş"):
            st.text_input("Kariyer tohumu (boş = rastgele)", key="career_seed",
                          help="Aynı tohum aynı sonuçları üretir (test ve tekrar için).")
        st.button("🌍 Dünyalar", key="sb_worlds", on_click=world_lobby_view.cb_open_lobby, width="stretch",
                  help="Dünyalarım, paylaşılan dünya kur, davet koduyla katıl, açık dünyalar.")


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
    c2.button("🤖 Asistana bırak", key="tac_auto", on_click=cb_auto_lineup, width="stretch")
    c3.button("🧹 Kadroyu temizle", key="tac_clear", on_click=cb_clear_lineup, width="stretch")

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
        width="stretch",
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
    concerns_section(cm, team)


CONCERN_ICONS = {"NONE": "🙂", "WATCH": "🟡", "CONCERNED": "🟠", "ANGRY": "🔴"}


def concerns_section(cm: CareerManager, team: Team) -> None:
    """Oyuncu memnuniyeti: sure beklentisi / oynadigi ve bekleyen maas talepleri."""
    rows = cm.player_concerns(team)
    unhappy = [r for r in rows if r.level != "NONE" or r.wage_demand]
    counts = {label: sum(1 for r in rows if r.label == label) for label in dict.fromkeys(r.label for r in rows)}
    st.markdown("#### 😟 Oyuncu memnuniyeti")
    st.caption("Oyuncular kadro rollerine göre süre bekler (son resmi maçlar, kupa yarım sayılır). Oynayan oyuncunun "
               "şikayeti ilerlemez; uzun süre oynamayan önce süre bekler, sonra şikayet eder, en sonunda ayrılmak ister. "
               "Gücü artan ya da piyasanın çok altında kazanan oyuncu yeni maaş ister.")
    st.markdown(stat_strip_html([(label, count) for label, count in counts.items()] or [("Kadro", 0)]),
                unsafe_allow_html=True)
    if not unhappy:
        st.success("Tüm oyuncular mutlu.")
        return
    st.dataframe(pd.DataFrame([
        {"Oyuncu": r.name, "Mv": r.position, "Rol": r.role_label, "Durum": f"{CONCERN_ICONS.get(r.level, '')} {r.label}",
         "İstenen maç": f"{r.wanted:.1f}", "Oynadığı": f"{r.played:.1f}", "Neden": r.reason,
         "Maaş talebi": format_money(r.wage_demand) + "/hf" if r.wage_demand else "—"}
        for r in unhappy
    ]), hide_index=True, width="stretch")
    for r in (r for r in unhappy if r.wage_demand):
        with st.container(border=True):
            text, accept, refuse = st.columns([4, 1, 1])
            text.markdown(f"✍️ **{escape(r.name)}** yeni sözleşme istiyor: {format_money(r.current_wage)} → "
                          f"**{format_money(r.wage_demand)}**/hafta")
            accept.button("Kabul", key=f"wage_accept_{r.player_id}", on_click=cb_wage_demand, args=(r.player_id, True),
                          type="primary", width="stretch", disabled=live_fixture_pending())
            refuse.button("Reddet", key=f"wage_refuse_{r.player_id}", on_click=cb_wage_demand,
                          args=(r.player_id, False), width="stretch", disabled=live_fixture_pending(),
                          help="Maaş aynı kalır; oyuncunun morali düşer ve şikayeti bir kademe artar.")


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
        st.dataframe(pd.DataFrame([r.to_dict() for r in rows]), hide_index=True, width="stretch")
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
                  disabled=locked or not candidates, width="stretch")
    with right:
        st.markdown(panel_title_html("⬇️ U-21'e gönder"), unsafe_allow_html=True)
        seniors = {r.id: r for r in cv.demotion_rows(cm, team)}
        if st.session_state.get("acad_demote") not in seniors:
            reset_widgets("acad_demote")
        st.selectbox("A takım oyuncusu", list(seniors), format_func=lambda i: seniors[i].label(), key="acad_demote",
                     help="21 yaş üstü en fazla birkaç oyuncu akademide kalabilir.")
        st.button("⬇️ U-21'e Gönder", key="acad_demote_btn", on_click=cb_demote,
                  disabled=locked or not seniors, width="stretch")

    intake = st.session_state.get("last_intake")
    if intake and intake[0] == cm.season:
        ids = {pid for pid in intake[1] if pid is not None}
        fresh = [r for r in cv.academy_rows(cm, team) if r.id in ids]
        if fresh:
            with st.expander(f"🎓 Bu sezonun genç girişi ({len(fresh)} oyuncu)", expanded=True):
                st.dataframe(pd.DataFrame([r.to_dict() for r in fresh]), hide_index=True, width="stretch")


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
    ]), hide_index=True, width="stretch")


# ===========================================================================
# SEKME: TAKTIK MERKEZI (mac onu raporu, rakip gozlem raporu, kadro planlayici)
# ===========================================================================

FORM_ICONS = {"G": "🟩", "B": "🟨", "M": "🟥"}
CONFIDENCE_ICONS = {"Yüksek": "🟢", "Orta": "🟡", "Düşük": "🔴"}
STATUS_ICONS = {"Yeterli": "🟢", "İnce": "🟡", "Kritik": "🔴"}


def form_text(form: str) -> str:
    return " ".join(FORM_ICONS.get(letter, letter) for letter in form) if form else "—"


def _pos(value) -> str:
    return str(getattr(value, "value", value))


def absentee_rows(players) -> list[dict]:
    return [{"Oyuncu": a.name, "Mv": _pos(a.position), "Güç": a.stars, "Durum": a.reason,
             "Ayrıntı": a.detail or "", "Dönüş": f"Hafta {a.return_week}" if a.return_week else "—"}
            for a in players]


def watch_rows(players) -> list[dict]:
    return [{"Oyuncu": w.name, "Mv": _pos(w.position), "Güç": w.stars, "Gol": w.goals, "Asist": w.assists,
             "Maç": w.appearances, "Ort. maç puanı": "—" if w.average_rating is None else f"{w.average_rating:.2f}",
             "Neden": w.reason, "Oynayabilir": "✅" if w.available else "❌"}
            for w in players]


def team_preview_block(tp) -> None:
    marker = " (sen)" if tp.is_viewer else ""
    venue = "İç saha" if tp.is_home else "Deplasman"
    st.markdown(panel_title_html(f"{tp.name}{marker} · {venue}"), unsafe_allow_html=True)
    position = f"{tp.league_position}. / {tp.league_size}" if tp.league_position else "—"
    st.markdown(stat_strip_html([
        ("Sıra", position), ("Puan", tp.points if tp.league_position else "—"),
        ("Takım gücü", tp.team_stars), ("Diziliş", tp.formation),
    ]), unsafe_allow_html=True)
    st.markdown(f"**Son maçlar:** {form_text(tp.form)} · gol {tp.form_goals_for}-{tp.form_goals_against}")
    st.caption(f"İç saha: {tp.home_record.text} · Deplasman: {tp.away_record.text}")
    if tp.recent_matches:
        st.dataframe(pd.DataFrame([
            {"Hafta": f"S{m.season} H{m.week}", "Turnuva": m.competition_label, "Rakip": m.opponent_name,
             "Yer": m.venue, "Skor": m.score_text, "Sonuç": FORM_ICONS.get(m.result, m.result)}
            for m in tp.recent_matches
        ]), hide_index=True, width="stretch")
    absent = [*tp.injured, *tp.suspended]
    if absent:
        st.markdown("**Eksikler**")
        st.dataframe(pd.DataFrame(absentee_rows(absent)), hide_index=True, width="stretch")
    else:
        st.caption("Sakat ya da cezalı oyuncu yok.")
    if tp.players_to_watch:
        st.markdown("**Dikkat edilecek oyuncular**")
        st.dataframe(pd.DataFrame(watch_rows(tp.players_to_watch)), hide_index=True, width="stretch")
    tend = tp.tendencies
    if tend.matches:
        st.caption(f"Maç başına {tend.goals_scored_per_match:.1f} gol atıyor, {tend.goals_conceded_per_match:.1f} "
                   f"yiyor · {tend.clean_sheets} gol yemediği maç · 🟨 {tend.yellow_cards} 🟥 {tend.red_cards}")
    for note in tend.notes:
        st.caption(f"• {note}")


def preview_section(db, team: Team, fixture) -> None:
    preview = preview_views.build_match_preview(db, fixture.id, team.id)
    st.markdown(panel_title_html(preview.title), unsafe_allow_html=True)
    venue = " · tarafsız saha" if preview.neutral_venue else ""
    st.caption(f"{preview.competition_label} · Sezon {preview.season} · Hafta {preview.week}{venue}")
    st.info(preview.verdict, icon="🧾")
    home, away = st.columns(2)
    with home:
        team_preview_block(preview.home)
    with away:
        team_preview_block(preview.away)
    h2h = preview.head_to_head
    st.markdown(panel_title_html("Aralarındaki maçlar"), unsafe_allow_html=True)
    st.markdown(h2h.summary)
    if h2h.last_meetings:
        st.dataframe(pd.DataFrame([
            {"Sezon/Hafta": f"S{m.season} H{m.week}", "Turnuva": m.competition_label, "Maç": m.text}
            for m in h2h.last_meetings
        ]), hide_index=True, width="stretch")


def scout_section(db, team: Team, fixture) -> None:
    report = preview_views.scout_opposition(db, fixture.id, team.id, rng_seed=career_seed() or 0)
    icon = CONFIDENCE_ICONS.get(report.confidence, "")
    st.markdown(panel_title_html(f"Gözlem raporu · {report.opponent_name}"), unsafe_allow_html=True)
    st.markdown(stat_strip_html([
        ("Tahmini diziliş", report.predicted_formation),
        ("Güvenilirlik", f"{icon} {report.confidence}"),
        ("Gözlemci", report.scout_name or "yok"),
        ("Saha", "Rakip iç sahada" if report.opponent_is_home else "Biz iç sahadayız"),
    ]), unsafe_allow_html=True)
    if report.scout_name is None:
        st.warning("Kulüpte gözlemci yok: rapor büyük ölçüde tahmin. Teknik Heyet sekmesinden gözlemci işe al.")
    st.markdown("**Muhtemel ilk 11**")
    st.dataframe(pd.DataFrame([
        {"Görev": _pos(p.role), "Oyuncu": p.name, "Mv": _pos(p.position), "Yaş": p.age, "Güç": p.stars}
        for p in report.predicted_xi
    ]), hide_index=True, width="stretch")
    left, right = st.columns(2)
    with left:
        st.markdown("**Eksikler**")
        if report.absentees:
            st.dataframe(pd.DataFrame(absentee_rows(report.absentees)), hide_index=True, width="stretch")
        else:
            st.caption("Bilinen eksik yok.")
    with right:
        st.markdown("**Dikkat edilecek oyuncular**")
        if report.players_to_watch:
            st.dataframe(pd.DataFrame(watch_rows(report.players_to_watch)), hide_index=True,
                         width="stretch")
        else:
            st.caption("Öne çıkan oyuncu yok.")
    for note in (*report.tendencies.notes, *report.notes):
        st.caption(f"• {note}")
    st.caption(f"ℹ️ {report.disclaimer}")


def planner_section(db, team: Team) -> None:
    plan = preview_views.build_squad_plan(db, team.id)
    size = f"{plan.squad_size} / {plan.squad_max}" if plan.squad_max else str(plan.squad_size)
    st.markdown(stat_strip_html([
        ("A takım", size), ("Diziliş", plan.formation),
        ("Sözleşmesi biten", len(plan.contracts_ending)), ("Plan ufku", f"{plan.horizon_seasons} sezon"),
    ]), unsafe_allow_html=True)
    for advice in plan.recommendations:
        st.warning(f"📌 {advice}")
    if not plan.recommendations:
        st.success("Kadro dengeli: önümüzdeki sezonlar için acil takviye gerekmiyor.")
    for group in plan.groups:
        icon = STATUS_ICONS.get(group.status, "")
        title = f"{icon} {group.label} · {group.count} oyuncu (önerilen {group.recommended}) · {group.status}"
        with st.expander(title, expanded=group.status != "Yeterli"):
            st.caption(f"İlk 11 ihtiyacı {group.starters} · sağlam {group.available} · grubun gücü {group.stars}")
            st.dataframe(pd.DataFrame([
                {"Oyuncu": pl.name, "Mv": _pos(pl.position), "Yaş": pl.age, "Güç": pl.stars,
                 "Potansiyel (tahmini)": pl.potential_stars, "Sözleşme bitişi": f"Sezon {pl.contract_expiry_season}",
                 "Notlar": ", ".join(pl.flags)}
                for pl in group.players
            ]), hide_index=True, width="stretch")
            if group.prospects:
                st.caption("Akademiden aday: " + ", ".join(f"{pl.name} ({pl.age}, {pl.potential_stars})"
                                                         for pl in group.prospects))
            if group.projections:
                st.dataframe(pd.DataFrame([
                    {"Sezon": pr.season, "Oyuncu": pr.count, "Ayrılacak": ", ".join(pr.leaving) or "—",
                     "32+ olacak": ", ".join(pr.aging) or "—", "Durum": f"{STATUS_ICONS.get(pr.status, '')} {pr.status}"}
                    for pr in group.projections
                ]), hide_index=True, width="stretch")


def friendly_section(cm: CareerManager, team: Team) -> None:
    st.markdown(panel_title_html("Hazırlık maçı"), unsafe_allow_html=True)
    st.caption("Haftada bir hazırlık maçı: sakatlık ve kart yok, kondisyon düşmez, sakat/cezalı oyuncular da oynar. "
               "Puan tablosu, form ve moral etkilenmez; oynayan yedekler süre beklentisine yarım maç sayar.")
    if cm.season_finished:
        st.info("Sezon tamamlandı: hazırlık maçı için yeni sezonu başlat.")
        return
    opponents = cm.friendly_opponents(team)
    by_id = {t.id: f"{t.name} · {star_glyphs(t.reputation)}" for t in opponents}
    if st.session_state.get("fr_opponent") not in by_id:
        reset_widgets("fr_opponent")
    pick, play = st.columns([3, 1])
    pick.selectbox("Rakip", list(by_id), key="fr_opponent", format_func=lambda i: by_id[i],
                   label_visibility="collapsed")
    history = cm.friendlies(cm.season, team_id=team.id)          # Faz 12: yalnizca bu kulubun maclari
    played_this_week = any(f.week == cm.current_week for f in history)
    play.button("🤝 Maçı oyna", key="fr_play", on_click=cb_play_friendly, type="primary", width="stretch",
                disabled=played_this_week or live_fixture_pending(),
                help="Bu hafta hazırlık maçı oynandı." if played_this_week else None)
    if history:
        st.dataframe(pd.DataFrame([
            {"Hafta": f.week, "Maç": f"{f.home_team_name} {f.home_score} - {f.away_score} {f.away_team_name}",
             "Goller": ", ".join(f"{g.get('minute')}' {g.get('player')}" for g in (f.events or [])) or "—"}
            for f in history
        ]), hide_index=True, width="stretch")


def _player_label(p) -> str:
    return f"{p.name} · {p.position.value} · {star_glyphs(p.overall_rating)}"


def _sync_select(key: str, options: list) -> None:
    """Kayitli secim artik secenekte yoksa (satilan oyuncu) widget durumu sifirlanir."""
    if key in st.session_state and st.session_state[key] not in options:
        reset_widgets(key)


def orders_section(cm: CareerManager, team: Team) -> None:
    current = cm.team_instructions(team)
    st.markdown(panel_title_html("Takım talimatları"), unsafe_allow_html=True)
    st.caption("Kayıtlı talimatlar otomatik oynanan maçlarda ve canlı maç başlangıcında kullanılır. "
               "Varsayılan (Dengeli · Normal · Karışık · Orta sahada pres) motorun nötr davranışıdır.")
    cols = st.columns(3)
    for i, (field, labels, help_text) in enumerate(INSTRUCTION_CHOICES):
        options = list(labels.values())
        _sync_select(f"ord_{field}", options)
        cols[i % 3].selectbox(FIELD_LABELS[field], options, index=options.index(labels[getattr(current, field)]),
                              key=f"ord_{field}", help=help_text)
    t1, t2 = st.columns(2)
    t1.toggle(FIELD_LABELS["offside_trap"], value=current.offside_trap, key="ord_offside_trap",
              help="Savunma hattı öne çıkar: yavaş forvetlere karşı etkili, hızlı forvetlere karşı riskli.")
    t2.toggle(FIELD_LABELS["counter_attack"], value=current.counter_attack, key="ord_counter_attack",
              help="Rakip öne çıktıkça (tam hücum, önde pres, skor peşinde) daha tehlikeli olur.")
    st.button("💾 Talimatları kaydet", key="ord_save", on_click=cb_save_instructions, type="primary",
              disabled=live_fixture_pending())
    st.caption(f"Şu an kayıtlı: {current.describe()}")

    st.markdown(panel_title_html("Kaptan ve duran toplar"), unsafe_allow_html=True)
    players = sorted(team.players, key=lambda p: (-p.overall_rating, p.name))
    labels = {p.id: _player_label(p) for p in players}
    options = [None, *labels]
    roles = cm.team_roles(team)
    cols = st.columns(2)
    for i, field in enumerate(team_roles.ROLE_FIELDS):
        _sync_select(f"role_{field}", options)
        value = getattr(roles, field)
        cols[i % 2].selectbox(team_roles.ROLE_LABELS[field], options,
                              index=options.index(value) if value in options else 0, key=f"role_{field}",
                              format_func=lambda pid, labels=labels: "— Asistan seçsin —" if pid is None else labels[pid])
    b1, b2 = st.columns(2)
    b1.button("💾 Görevleri kaydet", key="role_save", on_click=cb_save_roles, type="primary",
              width="stretch", disabled=live_fixture_pending())
    b2.button("🤖 Asistan belirlesin", key="role_suggest", on_click=cb_suggest_roles, width="stretch",
              disabled=live_fixture_pending())
    st.caption("Belirlenen atıcı sahadaysa penaltı, serbest vuruş ve kornerleri o kullanır; kaptan sahadayken "
               "kart riski azalır ve geride kalınan son dakikalarda takım dağılmaz. Oyuncu satılırsa görev boşalır.")


def plan_section(cm: CareerManager, team: Team) -> None:
    plan = cm.team_plan(team)
    players = sorted(team.players, key=lambda p: (p.position.value, -p.overall_rating))
    names = {p.id: p.name for p in players}
    st.markdown(panel_title_html(f"Maç planı · {len(plan)} / {MAX_PLAN_RULES} kural"), unsafe_allow_html=True)
    st.caption("Kurallar maç sırasında her dakika kontrol edilir; koşulu ilk sağlandığında bir kez uygulanır "
               "(değişiklik sınırı ve sakat/cezalı kontrolleri geçerlidir). Karşıt koşullu iki kural aynı maçta "
               "sırayla ikisi de çalışabilir.")
    for problem in plan.squad_errors(names):
        st.warning(problem)
    if plan.is_empty:
        st.info("Henüz kural yok. Örnek: 60. dakikada gerideysek Çok Ofansif + Hızlı tempo.")
    for i, rule in enumerate(plan.rules):
        with st.container(border=True):
            text, toggle, delete = st.columns([6, 1, 1])
            state = "🟢" if rule.enabled else "⚪"
            text.markdown(f"{state} **Kural {i + 1}{' · ' + rule.name if rule.name else ''}** — "
                          f"{rule.describe(names)}")
            toggle.button("Kapat" if rule.enabled else "Aç", key=f"plan_toggle_{i}", on_click=cb_toggle_plan_rule,
                          args=(i,), width="stretch")
            delete.button("🗑️", key=f"plan_del_{i}", on_click=cb_delete_plan_rule, args=(i,),
                          width="stretch", help="Kuralı sil")
    if len(plan) >= MAX_PLAN_RULES:
        st.caption(f"En fazla {MAX_PLAN_RULES} kural: yenisi için birini sil.")
        return
    with st.expander("➕ Yeni kural", expanded=plan.is_empty):
        a, b, c = st.columns([2, 1, 1])
        a.text_input("Kural adı (isteğe bağlı)", key="pl_name", max_chars=40, placeholder="Skor peşinde")
        b.number_input("Dakika", min_value=1, max_value=120, value=60, step=1, key="pl_minute")
        c.selectbox("Skor durumu", list(SITUATION_LABELS.values()), key="pl_situation")
        d, e = st.columns(2)
        d.number_input("En az gol farkı (0 = fark yok)", min_value=0, max_value=5, value=0, step=1, key="pl_margin",
                       help="Yalnızca 'Öndeyken' ve 'Gerideyken' için.")
        e.selectbox("Diziliş", [PLAN_KEEP, *MATCH_FORMATIONS], key="pl_formation")
        cols = st.columns(len(PLAN_INSTRUCTION_FIELDS))
        choices = dict((f, labels) for f, labels, _ in INSTRUCTION_CHOICES)
        for col, field in zip(cols, PLAN_INSTRUCTION_FIELDS, strict=True):
            col.selectbox(FIELD_LABELS[field], [PLAN_KEEP, *choices[field].values()], key=f"pl_{field}")
        options = [None, *names]
        for key in ("pl_out", "pl_in"):
            _sync_select(key, options)
        f, g = st.columns(2)
        f.selectbox("Çıkacak oyuncu", options, key="pl_out",
                    format_func=lambda pid: "— Değişiklik yok —" if pid is None else names[pid])
        g.selectbox("Girecek oyuncu", options, key="pl_in",
                    format_func=lambda pid: "— Değişiklik yok —" if pid is None else names[pid])
        st.button("➕ Kuralı ekle", key="plan_add", on_click=cb_add_plan_rule, type="primary",
                  disabled=live_fixture_pending())


def presets_section(cm: CareerManager, team: Team) -> None:
    presets = cm.tactic_presets(team)
    st.markdown(panel_title_html(f"Kayıtlı taktikler · {len(presets)} / {MAX_TACTIC_PRESETS}"), unsafe_allow_html=True)
    st.caption("Bir taktik; dizilişi, ilk 11 ve kulübeyi, takım talimatlarını, kaptan/duran top görevlerini ve maç "
               "planını birlikte saklar. Uygularken satılan, sakat ya da cezalı oyuncular atlanır.")
    if presets:
        st.dataframe(pd.DataFrame([
            {"Taktik": t.name, "Diziliş": t.formation, "Talimatlar": t.instructions.describe(),
             "Plan": f"{len(t.plan)} kural", "Kadro": f"{t.lineup_size} oyuncu",
             "Kaydedildi": f"Sezon {t.created_season} · Hafta {t.created_week}"}
            for t in presets
        ]), hide_index=True, width="stretch")
        by_id = {t.id: t.name for t in presets}
        _sync_select("preset_pick", list(by_id))
        pick, apply, delete = st.columns([3, 1, 1])
        pick.selectbox("Taktik seç", list(by_id), key="preset_pick", format_func=lambda pid: by_id[pid],
                       label_visibility="collapsed")
        apply.button("✅ Uygula", key="preset_apply", on_click=cb_apply_preset, type="primary",
                     width="stretch", disabled=live_fixture_pending())
        delete.button("🗑️ Sil", key="preset_delete", on_click=cb_delete_preset, width="stretch")
    else:
        st.info("Henüz kayıtlı taktik yok: mevcut dizilişini ve talimatlarını bir isimle kaydet.")
    a, b, c = st.columns([3, 1, 1])
    a.text_input("Taktik adı", key="preset_name", max_chars=40, placeholder="Deplasman 4-5-1",
                 label_visibility="collapsed")
    b.checkbox("Üzerine yaz", key="preset_overwrite", help="Aynı adlı taktik varsa güncellenir.")
    c.button("💾 Kaydet", key="preset_save", on_click=cb_save_preset, width="stretch",
             disabled=len(presets) >= MAX_TACTIC_PRESETS and not st.session_state.get("preset_overwrite"))


def prep_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("prep")
    section = st.radio("Bölüm", PREP_SECTIONS, key="prep_section", horizontal=True, label_visibility="collapsed")
    if section == PREP_ORDERS:
        orders_section(cm, team)
        return
    if section == PREP_PLAN:
        plan_section(cm, team)
        return
    if section == PREP_PRESETS:
        presets_section(cm, team)
        return
    if section == PREP_PLANNER:
        planner_section(db, team)
        return
    if section == PREP_FRIENDLY:
        friendly_section(cm, team)
        return
    fixture = preview_views.next_preview_fixture(db, team.id)
    if fixture is None:
        st.info("Önümüzde oynanmamış maç yok (sezon bitti ya da kura henüz çekilmedi).")
        return
    if section == PREP_SCOUT:
        scout_section(db, team, fixture)
    else:
        preview_section(db, team, fixture)


# ===========================================================================
# SEKME: HABERLER & TARIH (haber akisi, onur listesi, transfer kayitlari)
# ===========================================================================

NEWS_ICONS = {"TRANSFER": "💸", "LEAGUE_CHAMPION": "🏆", "CUP_CHAMPION": "⭐", "SPONSOR": "🤝",
              "BIG_RESULT": "💥", "WONDERKID": "🌟", "CHAIRMAN": "🏛️"}


def transfer_log_rows(logs) -> list[dict]:
    return [{"Sezon/Hafta": f"S{t.season} H{t.week}", "Oyuncu": t.player_name, "Nereden": t.from_team_name or "Kulüpsüz",
             "Nereye": t.to_team_name, "Bonservis": format_money(t.fee) if t.fee else "Bedelsiz",
             "Maaş/hf": format_money(t.wage)} for t in logs]


def world_tab(db, cm: CareerManager, team: Team) -> None:
    section = st.radio("Bölüm", WORLD_SECTIONS, key="world_section", horizontal=True, label_visibility="collapsed")
    if section == WORLD_NEWS:
        mine = st.toggle("Yalnızca kulübümü ilgilendirenler", key="news_mine")
        news = cm.world_news(limit=40, team_id=team.id if mine else None)
        if not news:
            st.info("Henüz haber yok: hafta oynandıkça transferler, şampiyonluklar ve büyük skorlar burada görünür.")
        for item in news:
            st.markdown(f"{NEWS_ICONS.get(item.kind, '📰')} **S{item.season} · H{item.week}** — {escape(item.text)}")
        return
    if section == WORLD_HONOURS:
        club = cm.club_honours(team)
        st.markdown(stat_strip_html([
            ("Kulübün kupaları", club.total_titles), ("Lig şampiyonluğu", club.league_titles),
            ("Kupa", club.cup_titles), ("İkincilik", club.runner_up_finishes),
        ]), unsafe_allow_html=True)
        honours = cm.season_honours()
        if not honours:
            st.info("Onur listesi sezon sonunda dolmaya başlar: şampiyonlar, gol kralları ve sezonun oyuncuları.")
            return
        st.dataframe(pd.DataFrame([
            {"Sezon": h.season, "Yarışma": h.competition_name, "Şampiyon": h.champion_name,
             "İkinci": h.runner_up_name or "—",
             "Gol kralı": f"{h.top_scorer_name} ({h.top_scorer_goals})" if h.top_scorer_name else "—",
             "Sezonun oyuncusu": (f"{h.player_of_season_name} · {h.player_of_season_rating:.2f}"
                                  if h.player_of_season_name and h.player_of_season_rating else "—"),
             "Senin sıran": f"{h.user_team_position}." if h.user_team_position else "—"}
            for h in honours
        ]), hide_index=True, width="stretch")
        return
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### 🏷️ Rekor transferler")
        records = cm.record_transfers(limit=10)
        if records:
            st.dataframe(pd.DataFrame(transfer_log_rows(records)), hide_index=True, width="stretch")
        else:
            st.caption("Henüz transfer yapılmadı.")
    with right:
        st.markdown(f"#### 🔁 {team.name} transferleri")
        mine = cm.transfer_history(team=team, limit=30)
        if mine:
            st.dataframe(pd.DataFrame(transfer_log_rows(mine)), hide_index=True, width="stretch")
        else:
            st.caption("Kulübünün transfer kaydı boş.")
    st.markdown(f"#### 📋 Sezon {cm.season} tüm transferler")
    season_logs = cm.transfer_history(season=cm.season, limit=50)
    if season_logs:
        st.dataframe(pd.DataFrame(transfer_log_rows(season_logs)), hide_index=True, width="stretch")
    else:
        st.caption("Bu sezon transfer yok.")


# ===========================================================================
# SEKME: KULUP YONETIMI & TESISLER
# ===========================================================================

def seats(value: int | None) -> str:
    return "—" if value is None else f"{value:,}".replace(",", ".")


def _multiplier_text(value: float | None) -> str:
    return "—" if value is None else f"x{value:.2f}"


def _shift_text(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}"


def facility_card(block: dict, effects: list[tuple[str, str, str]], cost_key: str, button_label: str) -> None:
    """Tek tesis karti: seviye cubugu, simdiki -> sonraki etki tablosu, yatirim dugmesi."""
    kind = block["kind"]
    with st.container(border=True):
        st.markdown(panel_title_html(block["label"]), unsafe_allow_html=True)
        if kind == "stadium":
            span = block["max_capacity"] - block["min_capacity"]
            share = (block["capacity"] - block["min_capacity"]) / span if span else 1.0
            st.progress(max(0.0, min(1.0, share)), text=f"{seats(block['capacity'])} / {seats(block['max_capacity'])} koltuk")
        else:
            st.progress(block["level"] / block["max_level"], text=f"Seviye {block['level']} / {block['max_level']}")
        st.dataframe(pd.DataFrame([{"Etki": name, "Şimdi": now, "Sonraki": nxt} for name, now, nxt in effects]),
                     hide_index=True, width="stretch")
        cost = block[cost_key]
        if block["at_max"]:
            st.success("En üst seviyede.")
            help_text = "Bu tesis zaten en üst seviyede."
        elif not block["affordable"]:
            help_text = f"Transfer bütçesi yetmiyor: {format_money(cost)} gerekli."
        else:
            help_text = f"Bedel transfer bütçesinden düşer: {format_money(cost)}"
        st.button(button_label if cost is None else f"{button_label} · {money(cost)}", key=f"club_up_{kind}",
                  on_click=cb_upgrade_facility, args=(kind,), type="primary", width="stretch",
                  disabled=block["at_max"] or not block["affordable"] or live_fixture_pending(), help=help_text)


def club_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("club")
    status = cm.facility_status(team)
    youth_b, medical_b, stadium_b, sponsor = status["youth"], status["medical"], status["stadium"], status["sponsor"]

    st.markdown(stat_strip_html([
        ("Transfer bütçesi", format_money(status["transfer_budget"])),
        ("Stadyum", f"{seats(stadium_b['capacity'])} koltuk"),
        ("Maç günü geliri", f"{format_money(stadium_b['gate_income_now'])} / iç saha maçı"),
        ("Sponsor", f"{format_money(sponsor['weekly'])} / hafta" if sponsor["active"] else "yok"),
    ]), unsafe_allow_html=True)
    if not status["configured"]:
        st.info("Tesis verileri bu kariyerde henüz kurulmamış; değerler varsayılandır ve ilk yatırımda kaydedilir.")
    if live_fixture_pending():
        st.warning("Kaydedilmemiş canlı maç var: yatırımlar maç kaydedildikten sonra yapılabilir.")

    st.caption("Kulüp yatırımları: bedel transfer bütçesinden düşer, etkisi kalıcıdır. "
               "Altyapı genç girişini, sağlık merkezi toparlanmayı, stadyum iç saha gelirini büyütür.")
    c1, c2, c3 = st.columns(3)
    with c1:
        facility_card(youth_b, [
            ("Genç potansiyeli", _shift_text(youth_b["potential_shift_now"]),
             _shift_text(youth_b["potential_shift_next"])),
            ("Akademi gelişim hızı", _multiplier_text(youth_b["growth_multiplier_now"]),
             _multiplier_text(youth_b["growth_multiplier_next"])),
        ], "upgrade_cost", "⬆️ Altyapıyı yükselt")
    with c2:
        facility_card(medical_b, [
            ("Toparlanma hızı", _multiplier_text(medical_b["recovery_multiplier_now"]),
             _multiplier_text(medical_b["recovery_multiplier_next"])),
        ], "upgrade_cost", "⬆️ Sağlık merkezini yükselt")
        st.caption("Sağlık merkezi çarpanı fizyoterapistin etkisiyle birlikte uygulanır (10. seviye nötr).")
    with c3:
        facility_card(stadium_b, [
            ("Seyirci / maç", seats(stadium_b["attendance_now"]), seats(stadium_b["attendance_next"])),
            ("Gelir / iç saha maçı", format_money(stadium_b["gate_income_now"]),
             "—" if stadium_b["gate_income_next"] is None else format_money(stadium_b["gate_income_next"])),
        ], "expansion_cost", "🏟️ Stadyumu genişlet")
        st.caption(f"Taraftar talebi ~{seats(stadium_b['demand'])} kişi: talebin üstünde koltuk gelir getirmez.")

    st.markdown(panel_title_html("Sponsorluk"), unsafe_allow_html=True)
    if sponsor["active"]:
        left = sponsor["seasons_left"]
        st.success(f"🤝 **{sponsor['name']}** · haftalık {format_money(sponsor['weekly'])} · "
                   f"{sponsor['until_season']}. sezon sonuna kadar ({left} sezon)")
    else:
        st.warning("Aktif sponsor sözleşmesi yok: haftalık sponsor geliri alınmıyor.")
    offers = cm.sponsor_offers(team)
    if not offers:
        st.caption("Bekleyen teklif yok. Yeni teklifler her sezon başında itibarına göre gelir.")
        return
    st.caption("Teklifler: yüksek haftalık gelir, uzun vade güvencesi ya da imzada peşin prim. İmzalanan teklif "
               "mevcut sözleşmenin yerine geçer; kalan teklifler düşer.")
    for i, (col, offer) in enumerate(zip(st.columns(len(offers)), offers, strict=True)):
        with col, st.container(border=True):
            st.markdown(f"**{offer.brand}**")
            st.markdown(stat_strip_html([
                ("Haftalık", format_money(offer.weekly)),
                ("Süre", f"{offer.seasons} sezon"),
                ("İmza primi", format_money(offer.signing_bonus) if offer.signing_bonus else "—"),
            ]), unsafe_allow_html=True)
            st.button("✍️ İmzala", key=f"club_sponsor_{i}", on_click=cb_sign_sponsor, args=(i,),
                      width="stretch", disabled=live_fixture_pending())


# ===========================================================================
# SEKME: TRANSFER PAZARI
# ===========================================================================

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
        ]), hide_index=True, width="stretch")

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
                         hide_index=True, width="stretch")
        with right:
            st.markdown("#### Bonservis teklifi")
            st.number_input("Teklif (EUR)", min_value=0, step=500_000,
                            value=cv.suggested_opening_fee(target_row), key="mkt_fee")
            banned, ban_reason = cm.transfer_ban_info(db.get(Player, target_id))
            if banned:
                st.warning(f"⛔ {ban_reason}")
            st.button("💶 Bonservis teklifi yap", key="mkt_offer", on_click=cb_offer_fee, type="primary",
                      disabled=banned or ("neg" in st.session_state and st.session_state["neg"]["negotiation"].open))
            listed = cm.is_shortlisted(target_id)
            st.button("☆ Takipten çıkar" if listed else "⭐ Takip listesine ekle", key="mkt_shortlist",
                      on_click=cb_shortlist_toggle, args=(target_id,))

    negotiation_panel(team)
    shortlist_section(cm)


def shortlist_section(cm: CareerManager) -> None:
    rows = cm.shortlist()
    st.markdown(f"#### ⭐ Takip listesi ({len(rows)})")
    if not rows:
        st.caption("Gözüne kestirdiğin oyuncuları hedef oyuncu panelinden takip listesine ekle.")
        return
    st.dataframe(pd.DataFrame([
        {"Oyuncu": r.name, "Kulüp": r.team_name or "Kulüpsüz", "Mv": r.position, "Yaş": r.age,
         "İstenen bonservis": format_money(r.asking_price) if r.asking_price is not None else "Satılık değil",
         "Durum": r.ban_reason if r.transfer_banned else ("Akademide" if r.in_academy else "Uygun"),
         "Not": r.note or "", "Eklendi": f"S{r.added_season} H{r.added_week}"}
        for r in rows
    ]), hide_index=True, width="stretch")
    by_id = {r.player_id: r.name for r in rows}
    if st.session_state.get("sl_pick") not in by_id:
        reset_widgets("sl_pick")
    pick, remove = st.columns([3, 1])
    pick.selectbox("Takipteki oyuncu", list(by_id), key="sl_pick", format_func=lambda i: by_id[i],
                   label_visibility="collapsed")
    remove.button("☆ Listeden çıkar", key="sl_remove", on_click=cb_shortlist_remove, width="stretch")


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
                  width="stretch")
        b2.button("✅ Talebi kabul et", key="neg_accept", on_click=cb_neg_accept, width="stretch")
        b3.button("🚪 Masadan kalk", key="neg_leave", on_click=cb_neg_leave, width="stretch")
    elif neg.get("needs_room"):
        needed = int(neg["needs_room"])
        st.warning(f"Anlaşma sağlandı ama maaş havuzunda {format_money(needed)}/hafta yer yok. "
                   f"Kaydırma maliyeti: {format_money(needed * 52)} bonservis bütçesi.")
        affordable = team.transfer_budget >= needed * 52 + neg["fee"]
        b1, b2 = st.columns(2)
        b1.button("💱 Maaş alanı aç ve imzala", key="neg_shift_sign", on_click=cb_neg_shift_sign,
                  type="primary", disabled=not affordable, width="stretch")
        b2.button("Vazgeç", key="neg_leave", on_click=cb_neg_leave, width="stretch")
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

    world = shared_page_world()
    if world is not None:
        # Faz 12: paylasilan dunyada hafta hazir paneliyle ilerler; rapor veritabanindan (menajerin kendi kulubu)
        world_panel_view.ready_panel(db, world)
        report = world_panel_view.latest_report(db, world)
        lines = report.lines if report is not None else None
        title = f"Son haftanın raporu · Sezon {report.season}, {report.week}. hafta" if report is not None else ""
    else:
        b1, b2 = st.columns(2)
        b1.button("⏭️ Sonraki haftayı oyna", key="lg_play", on_click=cb_play_week, type="primary",
                  disabled=cm.season_finished or live_blocks_week(), width="stretch",
                  help="Canlı maçın sürüyor; önce bitir." if live_blocks_week() else None)
        if cm.season_finished:
            b2.button("🆕 Yeni sezonu başlat", key="lg_new_season", on_click=cb_new_season, width="stretch")
        lines = st.session_state.get("last_week_lines")
        title = "Son haftanın raporu"

    if lines:
        with st.expander(title, expanded=True):
            for kind, text in lines:
                (st.success if kind == "season" else st.markdown)(text if kind != "result" else f"- {text}")
        if world is None and st.session_state.get("last_user_result") is not None:
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
                     width="stretch")
    with right:
        st.markdown("#### Gol krallığı")
        scorers = cv.scorer_rows(cm, league_id)
        if scorers:
            st.dataframe(pd.DataFrame(scorers), hide_index=True, width="stretch")
        else:
            st.caption("Henüz gol atılmadı.")

    week = cm.last_played_week()
    results = [r for r in cv.result_rows(cm, week) if r["Lig"] == names[league_id]]
    if results:
        st.markdown(f"#### {week}. hafta sonuçları")
        st.dataframe(pd.DataFrame(results), hide_index=True, width="stretch")


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
        st.dataframe(pd.DataFrame(rows).drop(columns=["id"]), hide_index=True, width="stretch")
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
        st.dataframe(pd.DataFrame(av.participant_rows(tm, t, user_id)), hide_index=True, width="stretch")


def draw_section(cm: CareerManager, t) -> None:
    tm = cm.tournaments
    session = tm.draw_session(t)
    can_draw = page_is_admin()                    # Faz 12: paylasilan dunyada kura sahip / yonetici isi
    st.markdown("#### 🎱 Kura çekimi")
    st.markdown(panel_title_html("Kura gecesi · Devler Arenası"), unsafe_allow_html=True)
    if not can_draw:
        st.info(DRAW_ADMIN_TEXT)
    elif not session.steps:
        options = [f.value for f in formats_for(t.size)]
        if st.session_state.get("arena_format") not in options:
            st.session_state["arena_format"] = t.format
        st.radio("Turnuva formatı", options, key="arena_format", horizontal=True,
                 format_func=lambda v: FORMAT_LABELS[CupFormat(v)], on_change=cb_set_cup_format,
                 help="Kura başladıktan sonra değiştirilemez.")
    if session.relaxed_note:
        st.warning(session.relaxed_note)

    done, total, unit = av.draw_progress(tm, t)
    if total:
        st.progress(done / total, text=f"{done}/{total} {unit} çekildi")
    reveal = av.pair_reveal(tm, t)
    if reveal is not None:
        st.markdown(pair_reveal_html(reveal.title, reveal.home, reveal.away, reveal.detail), unsafe_allow_html=True)
    pots, slots, headline, _announcement = av.draw_board(tm, t)
    st.markdown(draw_board_html(pots, slots, headline, None), unsafe_allow_html=True)

    knockout = tm.fmt(t) is CupFormat.KNOCKOUT
    pot = session.current_pot
    balls = (total - done) if knockout else (len(session.remaining(pot)) if pot is not None else 0)
    if can_draw and balls:
        st.caption("Kapalı toplardan birine tıkla: iki top açılır — ilk top ilk maçın ev sahibi, ikincisi "
                   "deplasman." if knockout else "Bir topa tıkla: açılan takım grubuna yerleşir.")
        per_row = min(8, balls)
        for row_start in range(0, balls, per_row):
            cols = st.columns(per_row)
            for i in range(row_start, min(balls, row_start + per_row)):
                cols[i - row_start].button(str(i + 1), key=f"arena_ball_{i}", on_click=cb_draw_pair,
                                           help="Topu aç", width="stretch")
    if can_draw:
        st.button("⏭️ Kurayı otomatik çek (atla)", key="arena_draw_all", on_click=cb_draw_all, type="primary",
                  help="Kalan tüm toplar çekilir; kura kilitlenir ve fikstüre işlenir.")
    with st.expander("Torbalarda kalan takımlar"):
        for label, names in av.pot_remaining_names(tm, t):
            st.markdown(f"**{escape(label)}:** " + (escape(", ".join(names)) if names else "—"))


def cup_progress_section(cm: CareerManager, t, user_id: int | None) -> None:
    tm = cm.tournaments
    if t.status is TournamentStatus.FINISHED and t.champion is not None:
        st.markdown(champion_banner_html(t.champion.name, f"Sezon {t.season} · {t.name}"), unsafe_allow_html=True)
    locked = av.locked_fixture_rows(tm, t)
    if locked:
        problems = tm.draw_fixture_problems(t)
        with st.expander(f"🔒 Kura sonucu · kilitli ilk tur fikstürü ({len(locked)} maç)",
                         expanded=not any(row["Durum"] == "oynandı" for row in locked)):
            if problems:
                st.error("Fikstür bütünlüğü sorunu: " + "; ".join(problems))
            else:
                st.caption("Kura kesinleşti: eşleşmeler ve maç haftaları doğrulandı, değiştirilemez.")
            st.dataframe(pd.DataFrame(locked), hide_index=True, width="stretch")

    md = tm.next_matchday(t)
    total = cm.total_weeks()
    if md is not None:
        st.caption(f"Sıradaki kupa günü: {md.week}. hafta · {matchday_label(md)} · "
                   f"şu an {min(cm.current_week, total)}. hafta")
    world = shared_page_world()
    if world is not None:
        # Faz 12: hafta dunya paneliyle ilerler; kupa raporu menajerin veritabanindaki son hafta raporundan
        st.caption("🌍 " + SHARED_WEEK_TEXT)
        report = world_panel_view.latest_report(cm.db, world)
        lines = report.cup_lines if report is not None else None
    else:
        b1, b2 = st.columns(2)
        b1.button("⏭️ Sonraki haftayı oyna", key="arena_play", on_click=cb_play_week, type="primary",
                  disabled=cm.season_finished or live_blocks_week(), width="stretch",
                  help="Canlı maçın sürüyor; önce bitir." if live_blocks_week() else None)
        if cm.season_finished:
            label = "🆕 Yeni sezonu başlat" if cm.game_mode is GameMode.CAREER else "🆕 Yeni turnuva"
            b2.button(label, key="arena_new_season", on_click=cb_new_season, width="stretch")
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
        st.dataframe(pd.DataFrame(results).drop(columns=["id"]), hide_index=True, width="stretch")
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
            st.dataframe(pd.DataFrame(scorers), hide_index=True, width="stretch")
        else:
            st.caption("Henüz gol yok.")
    with right:
        st.markdown("#### Asist krallığı")
        assists = av.player_rows(tm, t, by="assists")
        if assists:
            st.dataframe(pd.DataFrame(assists), hide_index=True, width="stretch")
        else:
            st.caption("Henüz asist yok.")

    st.markdown("#### Sakatlar ve cezalılar")
    unavailable = av.unavailable_rows(tm, t)
    if unavailable:
        st.dataframe(pd.DataFrame(unavailable), hide_index=True, width="stretch")
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
                      type="primary", width="stretch")


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


def competitive_live_ok(cm: CareerManager) -> bool:
    """
    Resmi (lig / kupa) mac canli oynanabilir mi? Faz 12: cm.live_allowed() (kural acik + dunyada tek menajer) VE
    paylasilan dunya degil: paylasilan dunyada hafta yalnizca tur motoruyla ilerler (canli sonucu kaydetmek
    haftayi tur disinda oynatir, tur suresi / raporlar / hazir bayraklari bozulurdu). Hazirlik maci her zaman canli.
    """
    return cm.live_allowed() and not cm.rules.shared


def live_setup_options() -> None:
    with st.expander("⚙️ Maç ayarları (değişiklik kuralı, başlangıç talimatı, otomatik durdurma)"):
        st.radio("Değişiklik kuralı", RULE_OPTIONS, key="live_rule",
                 help="Kural iki takıma da uygulanır. Devre arası pencere saymaz.")
        st.toggle("Kariyer maçında Taktik Merkezi'ndeki kayıtlı talimat, görev ve planı kullan", value=True,
                  key="live_use_saved", help="Kapalıysa aşağıdaki başlangıç zihniyeti ve sertliği kullanılır.")
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
        if not competitive_live_ok(cm):           # Faz 12: resmi maclar hafta ilerlerken (tur motoru)
            st.info(SHARED_LIVE_TEXT)
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
        f2.button("Uygula", key="live_formation_apply", on_click=cb_live_formation, width="stretch")
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
                                   for r in rows]), hide_index=True, width="stretch")
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
                  width="stretch")


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
                      width="stretch")
        else:
            c1.button("⏸ DURDUR", key="live_pause", on_click=cb_live_pause, type="primary",
                      width="stretch")
        c2.button("⏭ Sonucu gör", key="live_finish", on_click=cb_live_finish, width="stretch",
                  help="Kalan dakikaları durmadan oynatır.")
    if live.is_fixture and live.finished and not live.saved and not stale:
        c3.button("💾 Sonucu kaydet", key="live_save", on_click=cb_live_save, type="primary",
                  width="stretch")
    if not live.is_fixture or live.saved or stale:
        c4.button("✖ Maçı kapat", key="live_close", on_click=cb_live_close, width="stretch")
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
        if shared_page_world() is not None:
            st.info("Paylaşılan dünyada maçlar hafta ilerlerken oynanır; sonuçlar **🏆 Lig** sekmesindeki haftalık "
                    "raporda. Maç tekrarı yakında.")
            return
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
    """Giris / kayit: taktik tahtasi vitrini (login_view.py). Oturum yokken YALNIZCA bu ekran cizilir."""
    login_view.render_login(
        theme=st.session_state.get("theme", "dark"),
        view=st.session_state.get("auth_view", "login"),
        show_flash=lambda: show_flash("auth"),
        on_theme=cb_theme,
        on_login=cb_login,
        on_register=cb_register,
        on_view=cb_auth_view,
    )


def world_tab_names(rules: WorldRules, shared: bool, role: str | None) -> list[str]:
    """
    Faz 12: dunya sekmeleri. Eski kariyer (bos kurallar, dunyaya bagli olmayan oturum) -> [] (sekme sayisi ayni).
    Teklifler & Mesajlar paylasilan dunyada, Milli Takim milli takimlar aciksa, Dunya Yonetimi sahip/yoneticiye.
    """
    names: list[str] = []
    if shared:
        names.append(TAB_HUB)
    if rules.internationals:
        names.append(TAB_NATIONAL)
    if shared and role in ("OWNER", "ADMIN"):
        names.append(TAB_ADMIN)
    return names


def lobby_page() -> None:
    """Faz 12 lobi yolu: dunya secilmemis / uyeligi dusmus oturum dunya verisine dokunmadan lobiyi gorur."""
    with st.sidebar:
        sidebar_account()
    world_lobby_view.render_lobby()


def prepare_career(auth) -> None:
    """Eski kayitlar: eksik tablo/sutunlar eklenir, potansiyel/akademi doldurulur (kariyer silinmez)."""
    applied = accounts.ensure_career_ready(auth)
    st.session_state["career_ready"] = auth.career_schema
    if applied:
        flash("sidebar", "info", "Kariyer kaydı yeni sürüme yükseltildi: " + "; ".join(applied[:4])
              + (" …" if len(applied) > 4 else ""))


def schema_check(auth) -> list[str]:
    """Kariyer semasi hazir mi? Gerekirse kayipsiz yukseltme (prepare_career); kalan sorunlar."""
    if st.session_state.get("career_ready") != auth.career_schema:
        prepare_career(auth)
        return schema_problems()
    problems = schema_problems()
    if problems:
        # Oturum eski kod surumunde hazirlanmisti (uygulama guncellendi): once kayipsiz yukseltme denenir
        prepare_career(auth)
        problems = schema_problems()
    return problems


def render_world_tabs(db, cm: CareerManager, team: Team | None, tabs: dict) -> None:
    """Faz 12: dunya sekmeleri (eski kariyerde hicbiri yok); kulupsuz menajer de gorur."""
    for name, render in ((TAB_HUB, market_view.hub_tab), (TAB_NATIONAL, national_view.national_tab),
                         (TAB_ADMIN, world_admin_view.admin_tab)):
        if name in tabs:
            with tabs[name]:
                render(db, cm, team)


def club_pick_page(world: worlds.WorldContext, rules: WorldRules) -> None:
    """Paylasilan dunyada kulubu olmayan koltuk: kulup secimi + dunya sekmeleri (yonetim sekmesi sahip/yoneticide)."""
    names = [TAB_CLUBS] + world_tab_names(rules, True, world.role)
    tabs = dict(zip(names, st.tabs(names), strict=True))
    with session_scope() as db:
        cm = manager(db)
        with tabs[TAB_CLUBS]:
            world_lobby_view.render_club_offers(db, cm, world)
        render_world_tabs(db, cm, None, tabs)


def main() -> None:
    st.set_page_config(page_title=f"{APP_SHORT} · {APP_NAME}", page_icon="⚽", layout="wide")
    theme = current_theme()
    auth = st.session_state.get("auth")
    st.markdown(CSS + pitch.PITCH_CSS + BRACKET_CSS + MODE_CSS + theme_css(theme, login=auth is None),
                unsafe_allow_html=True)
    st.html(LANG_SCRIPT, unsafe_allow_javascript=True)          # Turkce buyuk harf (GİRİŞ, TESİSLERİ)

    if not wait_for_db(retries=2, delay=0.5, verbose=False):
        st.error("Veritabanına bağlanılamadı. `docker compose up -d` çalışıyor mu?")
        st.stop()
    if auth is None:
        login_screen()
        return
    st.title(f"⚽ {APP_SHORT} · {APP_NAME.upper()}")            # h1 buyuk harf; sayfa dili tr iken ONLİNE olmasin
    if show_lobby():                                            # Faz 12: dunya secimi (kariyer semasina dokunmaz)
        lobby_page()
        return
    # Faz 12: dunyaya bagli oturumda uyelik + kayit her cizimde yeniden okunur (eski oturum: sorgu yok)
    world = current_world()
    if show_lobby():
        lobby_page()
        return
    auth = st.session_state["auth"]
    try:
        problems = schema_check(auth)
    except OperationalError as exc:                             # yukseltme hafta ilerlemesini bekledi (55P03)
        if not is_lock_timeout(exc):
            raise
        st.warning(BUSY_TEXT)
        st.stop()
    if problems:
        st.error("Veritabanı şeması bu sürümden eski (" + ", ".join(problems[:4]) + "). "
                 "`python seed.py` ile yeniden kur — kariyer kaydı sıfırlanır.")
        st.stop()
    shared = world is not None and world.kind == WORLD_KIND_SHARED
    if shared:
        world_panel_view.advance_if_due(world)                  # suresi dolan tur (beklemez; cizim oturumundan once)
    teams = load_teams()
    if len(teams) < 2:
        st.warning("Veritabanında takım yok. Önce `python seed.py` çalıştır.")
        st.stop()

    with session_scope() as db:
        cm = manager(db)
        chosen, mode = cm.mode_chosen, cm.game_mode
        rules = world_rules_for(cm)
        has_team = cm.user_team is not None
    if rules.shared and world is None:
        # Dunyasiz (eski) oturum paylasilan bir kariyere bakiyor: uyelikli dunya baglamina gecilir (yoksa lobi)
        unbind_world("Bu kariyer artık paylaşılan bir dünya: Dünyalar sayfasından gir.")
        st.rerun()
    if not chosen:
        mode_screen()
        return

    sidebar(teams, world if shared else None)
    if shared and not has_team:
        club_pick_page(world, rules)
        return
    names = CAREER_TABS if mode is GameMode.CAREER else TOURNAMENT_TABS
    extra = world_tab_names(rules, shared or rules.shared, world.role if world is not None else None)
    if extra:
        names = names + extra
    tabs = dict(zip(names, st.tabs(names), strict=True))
    renderers = {TAB_SQUAD: squad_tab, TAB_PREP: prep_tab, TAB_ACADEMY: academy_tab, TAB_FINANCE: finance_tab,
                 TAB_CLUB: club_tab, TAB_WORLD: world_tab,
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
        render_world_tabs(db, cm, team, tabs)

    with tabs[TAB_LIVE]:
        live_tab(teams)


# Oturum kapisi (P1 guvenlik): giris/kayit/cikis disindaki TUM callback'ler oturum ister; Faz 12: dunyaya bagli
# oturumda uyelik ve paylasilan dunyada SHARED dunya kilidi (web_common.member_callback).
# Widget'lar callback'i cizim aninda global adla alir; sarma, main() calismadan once yapilir.
for _name, _callback in list(globals().items()):
    if _name.startswith("cb_") and callable(_callback) and _name not in PUBLIC_CALLBACKS:
        globals()[_name] = member_callback(_callback)


if __name__ == "__main__":
    main()
