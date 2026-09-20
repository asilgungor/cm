"""
web_app.py
==========
CM Menajer Paneli -- tamamen tarayici tabanli kariyer arayuzu.

    streamlit run web_app.py

Giris (10. Asama): menajer hesabi. Giris yapmayan kullanici HICBIR oyun sekmesine erisemez;
giris / kayit ekranina yonlendirilir. Oturum st.session_state["auth"] (accounts.AuthSession)
ile tutulur; her menajerin kariyeri kendi PostgreSQL semasindadir ve bu dosyanin kaydettigi
cozucu (session_career_schema) veritabani islemlerini oturumdaki kullanicinin kariyerine yonlendirir.
14H oturum surdurme: giris / kayit yeni bir rastgele belirtec verir (accounts.issue_session; sunucuda yalnizca
sha256 ozeti) ve tarayici cerezine yazdirir (web_common.mount_session_cookie, "Beni hatirla" acikken 7 gun). Sayfa
yenilenince ya da sunucu yeniden baslayinca restore_session cerezden AYNI AuthSession'i kurar (accounts.resume_session;
kullanici / sema cerezden okunmaz), bagli belirteci dakikada en fazla bir kez yeniden dogrular. Cikis belirteci iptal
eder ve cerezi siler; Oyun Secenekleri > Hesap'ta "Tum cihazlarda cikis" (cb_logout_everywhere).
5 hatali denemeden sonra giris 30 sn kilitlenir. Arayuz OFM temalarindadir (ofm_theme.py): menajer
⚽ OFM Dark / ☀️ OFM Light secer; secim st.session_state["theme"] ve ?theme= URL parametresinde tutulur
(sayfa yenilense de kalir, giris/cikista korunur);
kadro, pazar ve akademi ekranlarinda guc/potansiyel sayi yerine YILDIZ gosterilir (stars.py).

Ilk giris (8. Asama): oyun modu secimi
    CAREER_MODE     : lig maratonu; Devler Arenasi takvimi lig haftalariyla senkron akar
    TOURNAMENT_MODE : sadece Devler Arenasi (Champions Cup)

Sekmeler:
    Canli Mac       : Faz 14A'dan beri match_day_view.py (CM 01/02 mac gunu ekrani, st.fragment oynatma):
                      skor bandi, tek yorum afisi, "Son 5 dk", sekil ve kondisyon tahtasi, sekmeler (Mac
                      Ozeti / Istatistik / Oyuncu Notlari / Puan Durumu / Mac Raporu), 8 talimat ekseni +
                      6 bagiris, genis otomatik duraklama. Maçımı yönet: haftanin gercek maci canli oynanir;
                      sonuc kaydedilince hafta tamamlanir. Hazirlik macinda da bir takim yonetilebilir.
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
    * Canli mac nesnesi (live_match.LiveMatch) session_state["live"]'dadir; oynatma match_day_view'daki
      st.fragment parcalariyla (bloke uyku dongusu YOK, kontroller hep tepkili). Kaydedilmemis kariyer canli
      maci varken hafta oynatma, takim ve mod degisikligi kilitlidir (live_blocks_week, live_fixture_pending).

Paylasilan dunyalar (Faz 12 / 14. Asama):
    * Ortak yardimcilar (flash, show_flash, reset_widgets, money, live_fixture_pending, manager, oturum
      dekoratorleri) web_common.py'dedir; bu dosya ayni adlarla yeniden disari acar. Callback govdeleri
      manager / session_scope'u BU modulun global adlariyla cozer (testler web_app.manager'i degistirebilir).
    * Oyun callback'leri modul sonunda web_common.member_callback ile sarilir (oturum + dunya uyeligi +
      paylasilan dunyada SHARED dunya kilidi); gorunum modullerindeki callback'ler kendi dekoratorlerini tasir.
    * Eski (dunyaya bagli olmayan) oturum bugunku ekrani birebir cizer. Yalnizca paylasilan / milli takimli
      dunyada ek sekmeler (TAB_HUB, TAB_NATIONAL, TAB_ADMIN), kenar cubugu dunya paneli ve lobi yolu acilir.
    * Giris / kayit ekrani login_view.py'dedir (login_screen yalnizca callback'leri verir).
    * Faz 13E: oyuncu profili player_view.py'dedir. Kadro, akademi, Transfer Pazari (hedef oyuncu) ve takip listesi
      ayni mekanizmayla (secici + "🔎 İncele" -> panel yerinde acilir; pv_open / pv_section / pv_pick_{alan}) baglanir;
      gozlemci sisi ve toplu sorgular player_view'dadir. Teklifler & Listeler ve Milli Takim kadrosu da ayni paneli acar.
    * Faz 13G: (1) kulup secimi kariyerin ILK adimidir: kulubu olmayan kisisel kariyer / turnuva modu yalnizca
      club_select_page'i (club_picker_view: ulke -> lig -> kulup) cizer; sekmeler ve kenar cubugu araclari kulup
      secilince gelir. Kariyer modunda kulup KILITLIDIR (CareerManager.choose_club / club_locked; kenar cubugunda
      secici yok, cb_set_team de reddeder), turnuva modunda ilk maca kadar katilimcilar arasinda degisebilir. Secimden
      sonra sayfa en ustten, basligin altindaki karsilama mesajiyla (flash alani "main") cizilir. (2) Oyuncu
      tablolarinda satira tek tik profili acar (player_view.selectable_table). (3) Kadro & Taktik'te surukle-birak
      taktik tahtasi (tactics_board_view; squad_board_section parcasi, st.fragment): niyetler sunucuda dogrulanir;
      liste duzenleyici (tac_editor / tac_rows / tac_save) erisilebilir ikinci yol olarak kalir.
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
    * Faz 12 B4 (12B): Transfer Pazari'nda hedef oyuncu bir menajerin kulubundeyse (market_view.human_target) AI
      "Bonservis teklifi yap" alani yerine menajerler arasi teklif paneli (mkt_kind / mkt_fee / mkt_exchange /
      mkt_loan_weeks / mkt_loan_share / adil oyun on degerlendirmesi / mkt_h_offer); yapay zekâ kulubundeki oyuncu
      icin bugunku alan + kiralik iste (mkt_ai_loan). Teklifler & Mesajlar sekmesi market_view.hub_tab, yonetici
      "Adil oyun" bolumu world_admin_view. Eski / kisisel kariyerde bu dallar hic sorgu atmaz.

Faz 13I -- CM 01/02 tarzi menu (nav_view) ve Transfer Merkezi (transfer_centre_view):
    * Kulup secildikten sonra UST SEKME YOK: sol kenar cubugu oyun menusudur (kulup basligi: ad, lig, butce; "devam"
      dugmesi nav_continue / nav_new_season / paylasilan dunyada hazir paneli; gruplu sayfa dugmeleri nav_to_{slug};
      en altta hesap & ayarlar). Her cizimde YALNIZCA secili sayfa cizilir (render_page / PAGE_RENDERERS). Secim
      st.session_state["nav_page"] ve URL'deki ?sayfa= ile tutulur; telefon genisliginde sayfanin ustunde nav_top.
      Menu sayaclari: Transfer Merkezi (sirasi menajerde olan dosyalar), Kadro (maas talepleri), Teklifler & Mesajlar
      (yanit bekleyen teklif + okunmamis mesaj / bildirim).
    * Sayfalar: Ana Sayfa (home_view), Kadro (squad_tab), Taktik (prep_tab), Canli Mac (live_tab), Akademi, Teknik
      Heyet, Fikstur & Sonuclar (fixtures_page: hafta oynatma lg_play, hafta raporu, kulubun fiksturu), Puan Durumu
      (standings_page), Devler Arenasi, Milli Takim, Transfer Merkezi (transfer_centre_view: eski Transfer Pazari
      aramasi + transfer masasi), Kulup & Finans (club_finance_page: finance_tab + club_tab), Haberler & Tarih,
      Teklifler & Mesajlar, Dunya Yonetimi. Genel mesajlar (hafta oynandi vb.) her sayfanin ustunde (flash alani main).
    * Eski "Bonservis teklifi" (mkt_offer) transfer masasina yonlendirildi; oturum belleginde tutulan eski sozlesme
      masasi (neg_*) kaldirildi -- masa dosya gecmisinden yeniden kurulur (K12: ikna skoru gosterilmez).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from streamlit.runtime.scriptrunner import get_script_run_ctx

import accounts
import arena_views as av
import career_views as cv
import club_picker_view
import club_view
import competition_view
import database
import find_view
import home_view
import links_view as lk
import login_view
import market_view
import match_day_view
import national_view
import nav_view
import pitch
import player_view as pv
import preview_views
import reputation
import staff as staff_rules
import tactics_board_view
import team_roles
import transfer_centre_view
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
    ClubChoiceError,
    ConcernError,
    FacilityError,
    FriendlyError,
    LiveMatchError,
    SeasonNotFinished,
    TacticsError,
)
from cup_draw import FORMAT_LABELS, CupFormat, DrawComplete, formats_for
from database import schema_problems, session_scope, wait_for_db
from finance import BudgetError, format_money, preview_budget_shift, wage_budget_bounds
from instructions import (
    FIELD_LABELS,
    FOCUS_LABELS,
    MENTALITY_LABELS,
    PASSING_LABELS,
    PRESSING_LABELS,
    TACKLING_LABELS,
    TEMPO_LABELS,
    TeamInstructions,
)
from match_day_view import (  # noqa: F401 -- canli mac sabitleri (testler web_app adinda da kullanir)
    LIVE_FRIENDLY,
    LIVE_MANAGE,
    LIVE_MODES,
    LIVE_REPLAY,
    SHARED_LIVE_TEXT,
    store_week_report,
)
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
    Fixture,
    GameMode,
    Player,
    Position,
    Staff,
    StaffRole,
    Team,
    TournamentStatus,
)
from ofm_theme import (
    BRAND_TITLE,
    DEFAULT_THEME,
    LANG_SCRIPT,
    THEME_LABELS,
    THEME_PREF_VERSION,
    THEME_VERSION_KEY,
    THEME_VERSION_PARAM,
    normalize_theme,
    panel_title_html,
    stat_strip_html,
    theme_css,
    theme_sync_script,
)
from tactics import FORMATIONS, MATCH_FORMATIONS
from tournament_manager import TournamentError, matchday_label
from transfer_desk import TransferDesk
from transfers import ROLE_LABELS, TransferError
from web_common import (
    BUSY_TEXT,
    COOKIE_OK_KEY,
    RESUME_TRIED_KEY,
    SESSION_BINDING_KEY,
    WORLD_KIND_SHARED,
    admin_callback,  # noqa: F401 -- gorunum modulleri ve testler icin web_app adinda da acik
    bind_session_token,
    bound_session_valid,
    callback_is_admin,
    career_seed,
    cookie_token_for_resume,
    current_world,
    flash,
    is_lock_timeout,
    is_shared_world,  # noqa: F401 -- WP0 adi (testler / gorunum modulleri)
    live_fixture_pending,
    manager,
    md_escape,
    member_callback,
    money,
    mount_session_cookie,
    page_is_admin,
    parse_seed,  # noqa: F401 -- testler web_app.parse_seed kullanir
    pin_state,
    queue_cookie_clear,
    queue_cookie_set,
    requires_auth,  # noqa: F401 -- eski ad: oturum kapisi dekoratoru
    reset_widgets,
    shared_page_world,
    show_flash,
    show_lobby,
    unbind_world,
    user_agent,
    world_rules_for,
)
from web_view import (
    CSS,
    usage_bar_html,
)
from world_rules import WorldRules

log = logging.getLogger(__name__)

# Faz 13I: sekmeler yerine menu sayfalari (nav_view). Sayfa listeleri oyun moduna / dunyaya gore nav_view.pages_for.
CAREER_PAGES = list(nav_view.CAREER_PAGES)
TOURNAMENT_PAGES = list(nav_view.TOURNAMENT_PAGES)
CLUB_FINANCE, CLUB_FACILITIES = "Bütçe ve maaşlar", "Tesisler ve sponsorluk"
CLUB_SECTIONS = [CLUB_FINANCE, CLUB_FACILITIES]
WORLD_NEWS, WORLD_HONOURS, WORLD_TRANSFERS = "Haber akışı", "Onur listesi", "Transfer kayıtları"
WORLD_SECTIONS = [WORLD_NEWS, WORLD_HONOURS, WORLD_TRANSFERS]
PREP_FRIENDLY = "Hazırlık maçı"
PREP_PREVIEW, PREP_SCOUT, PREP_PLANNER = "Maç önü raporu", "Rakip gözlem raporu", "Kadro planlayıcı"
PREP_ORDERS, PREP_PLAN, PREP_PRESETS = "Talimatlar ve duran toplar", "Maç planı", "Kayıtlı taktikler"
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
# Paylasilan dunyada KULUBU OLMAYAN koltugun sayfasi hala sekmelidir (kulup secimi + dunya sekmeleri); menu kulup
# secilince gelir. Sekme adlari nav_view sayfa etiketleriyle aynidir.
TAB_HUB = nav_view.PAGES[nav_view.INBOX].label
TAB_NATIONAL = nav_view.PAGES[nav_view.NATIONAL].label
TAB_ADMIN = nav_view.PAGES[nav_view.ADMIN].label
WORLD_TABS = [TAB_HUB, TAB_NATIONAL, TAB_ADMIN]
TAB_CLUBS = "Kulübünü Seç"                  # paylasilan dunyada kulubu olmayan koltugun sekmesi
SHARED_WEEK_TEXT = "Paylaşılan dünyada hafta, menajerler hazır olunca ya da süre dolunca ilerler (kenar çubuğu paneli)."
SHARED_TEAM_TEXT = "Paylaşılan dünyada kulübünü dünya panelinden seçersin."
MAIN_AREA = club_picker_view.WELCOME_AREA        # her sayfanin ustunde gosterilen genel mesajlar (hafta oynandi ...)
DRAW_ADMIN_TEXT = ("Bu dünyada kurayı dünyanın sahibi ya da yöneticisi çeker; kimse çekmezse hafta ilerlerken kura "
                   "otomatik tamamlanır.")
MODE_LABELS = {GameMode.CAREER: "Kariyer Modu", GameMode.TOURNAMENT: "Turnuva Modu"}
STATUS_LABELS = {TournamentStatus.DRAW: "Kura", TournamentStatus.RUNNING: "Sürüyor",
                 TournamentStatus.FINISHED: "Bitti"}
POSITIONS = [p.value for p in Position]
LOGIN_MAX_FAILURES = 5
LOGIN_COOLDOWN_SECONDS = 30


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

# Giris gerektirmeyen callback'ler; digerleri modul sonunda member_callback ile sarilir. cb_logout_everywhere
# (14H) oturumu kendisi denetler: yalnizca session_state["auth"]'un hesabini kapatir, oturum yoksa bir sey yapmaz.
PUBLIC_CALLBACKS = frozenset({"cb_login", "cb_register", "cb_logout", "cb_logout_everywhere", "cb_theme",
                              "cb_auth_view"})


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
    """
    Oturumdaki tema; yoksa URL'deki ?theme= (sayfa yenilemesi), o da yoksa OFM Klasik. URL guncel tutulur.
    14S: tercih SURUMLUDUR (ofm_theme.THEME_PREF_VERSION): surumsuz oturum / URL (?theme=dark, 13I yer imi) bir kez
    Klasik'e doner; menajer sonra secerse (?theme=dark&tv=2) secimi kalicidir.
    """
    ss = st.session_state
    theme = ss.get("theme")
    if theme not in THEME_LABELS or ss.get(THEME_VERSION_KEY) != THEME_PREF_VERSION:
        wanted = st.query_params.get("theme")
        versioned = st.query_params.get(THEME_VERSION_PARAM) == str(THEME_PREF_VERSION)
        theme = normalize_theme(wanted) if versioned and wanted in THEME_LABELS else DEFAULT_THEME
        ss["theme"], ss[THEME_VERSION_KEY] = theme, THEME_PREF_VERSION
    if st.query_params.get("theme") != theme:
        st.query_params["theme"] = theme
    if st.query_params.get(THEME_VERSION_PARAM) != str(THEME_PREF_VERSION):
        st.query_params[THEME_VERSION_PARAM] = str(THEME_PREF_VERSION)
    if ss.get("theme_choice") != THEME_LABELS[theme]:
        ss["theme_choice"] = THEME_LABELS[theme]
    return theme


def cb_theme() -> None:
    """Tema secimi (girissiz de calisir): oturuma ve URL'ye (surumuyle) yazilir."""
    theme = normalize_theme(st.session_state.get("theme_choice"))
    st.session_state["theme"], st.session_state[THEME_VERSION_KEY] = theme, THEME_PREF_VERSION
    st.query_params["theme"] = theme
    st.query_params[THEME_VERSION_PARAM] = str(THEME_PREF_VERSION)


def cb_auth_view(view: str) -> None:
    st.session_state["auth_view"] = view


def _clear_session() -> None:
    """
    Oturum durumu temizlenir; yalnizca gorsel tercih (tema ve surumu) korunur. 14H: temizlenen oturum ayni tarayici
    oturumunda cerezden YENIDEN devam etmez (el sikismasindaki cerez bayat olabilir: cikistan sonra geri girmesin).
    """
    theme, version = st.session_state.get("theme"), st.session_state.get(THEME_VERSION_KEY)
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if theme in THEME_LABELS:
        st.session_state["theme"] = theme
        if version is not None:
            st.session_state[THEME_VERSION_KEY] = version
    st.session_state[RESUME_TRIED_KEY] = True


def _bind_default_world(session: accounts.AuthSession):
    """
    Faz 12: oturum hesabin varsayilan dunyasina baglanir (son girilen dunya, yoksa kisisel kariyer). Baglanamazsa
    (kayit okunamadi) oturum hesabin KENDI kariyerinde dunyasiz kalir (eski davranis; baska kariyere dusmez).
    """
    try:
        ctx = worlds.default_world(session)
        return worlds.session_for(session, ctx), ctx
    except (worlds.WorldError, SQLAlchemyError, ValueError):
        return session, None


def start_session(session: accounts.AuthSession, *, remember: bool = True) -> None:
    """
    Yeni oturum (giris / kayit): onceki kullanicinin ekran durumu (widget, rapor, canli mac) tasinmaz; tema kalir.
    14H: YENI bir kalici oturum belirteci verilir ve cereze yazdirilir (tarayicida duran eski cerez terfi ettirilmez).
    """
    _clear_session()
    session, ctx = _bind_default_world(session)
    st.session_state["auth"] = session
    _persist_session(session.user_id, remember)
    where = (f"«{md_escape(ctx.name)}» dünyası yüklendi." if ctx is not None and ctx.kind == WORLD_KIND_SHARED
             else "Kariyerin yüklendi.")
    flash("sidebar", "success", f"Hoş geldin, {session.username}! {where}")


def _persist_session(user_id: int, remember: bool) -> None:
    """Belirtec verilemezse giris yine olur (yalnizca yenileme sonrasi devam etmez); belirtec loglanmaz."""
    try:
        issued = accounts.issue_session(user_id, remember=remember, user_agent=user_agent())
    except (SQLAlchemyError, accounts.AccountError, ValueError) as exc:
        log.warning("Oturum belirteci verilemedi (%s); oturum yalnizca bu sekmede sürer.", type(exc).__name__)
        return
    bind_session_token(issued.binding)
    queue_cookie_set(issued.token, issued.max_age)


SESSION_ENDED_TEXT = "Oturumun sona ermiş ya da kapatılmış; yeniden giriş yap."
SESSION_REVOKED_TEXT = "Oturumun kapatıldı (başka bir yerden çıkış yapıldı ya da süresi doldu); yeniden giriş yap."


def restore_session() -> accounts.AuthSession | None:
    """
    14H, her cizimin basinda (main): oturum yoksa tarayici cerezinden devam (tarayici oturumu basina BIR kez denenir);
    varsa bagli belirtec en fazla REVALIDATE_SECONDS'ta bir yeniden dogrulanir (iptal / sure dolmussa oturum kapanir).
    Sahte, suresi dolmus ya da iptal edilmis belirtec reddedilir ve cerez silinir. Gecici veritabani hatasinda oturum
    dusurulmez / cerez silinmez (bir sonraki cizimde yeniden denenir).
    """
    ss = st.session_state
    auth = ss.get("auth")
    if auth is None:
        token = cookie_token_for_resume()
        if token is None:
            return None
        try:
            resumed = accounts.resume_session(token)
        except (SQLAlchemyError, accounts.AccountError, ValueError) as exc:
            log.warning("Oturum sürdürülemedi (%s); sonraki çizimde yeniden denenecek.", type(exc).__name__)
            ss.pop(RESUME_TRIED_KEY, None)
            return None
        if resumed is None:
            queue_cookie_clear()
            flash("auth", "info", SESSION_ENDED_TEXT)
            return None
        session, binding = resumed
        _clear_session()
        session, _ctx = _bind_default_world(session)
        ss["auth"] = session
        bind_session_token(binding)
        ss[COOKIE_OK_KEY] = True                            # cerez zaten tarayicida
        if binding.remember:
            queue_cookie_set(token, int(accounts.SESSION_TTL_REMEMBER.total_seconds()))   # Max-Age da kaysin
        return session
    # Belirtecsiz oturum (test / hazir kabuk), onbellekteki denetim ve gecici DB hatasi: oturum surer
    if bound_session_valid():
        return auth
    _end_session(None, None, message=SESSION_REVOKED_TEXT, kind="warning")
    return None


def _end_session(auth, binding, *, message: str | None, kind: str = "info", everywhere: bool = False) -> None:
    """Cikis: sunucuda belirtec(ler) iptal, oturum temizlenir, tarayici cerezi silinir. Iptal hatasi cikisi durdurmaz."""
    try:
        if everywhere and auth is not None and getattr(auth, "user_id", None):
            accounts.revoke_all_sessions(auth.user_id)
        elif binding is not None:
            accounts.revoke_session(binding)
    except (SQLAlchemyError, accounts.AccountError, ValueError) as exc:
        log.warning("Oturum belirteci iptal edilemedi (%s).", type(exc).__name__)
    _clear_session()
    queue_cookie_clear()
    if message:
        flash("auth", kind, message)


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
    start_session(session, remember=bool(ss.get(login_view.REMEMBER_KEY, True)))


def cb_register() -> None:
    ss = st.session_state
    password = ss.get("reg_pass", "")
    if password != ss.get("reg_pass2", ""):
        ss["reg_pass"] = ss["reg_pass2"] = ""
        flash("auth", "error", "Parolalar eşleşmiyor.")
        return
    try:
        session = accounts.register(ss.get("reg_user", ""), password,
                                    source=world_lobby_view.world_source_choice(ss.get(login_view.SOURCE_KEY)))
    except Exception as exc:
        if not isinstance(exc, (accounts.AccountError, AuthError)):
            exc = accounts.AccountError("Kayıt şu an tamamlanamadı, lütfen biraz sonra tekrar dene.")
        ss["reg_pass"] = ss["reg_pass2"] = ""
        flash("auth", "error", str(exc))
        return
    start_session(session, remember=bool(ss.get(login_view.REMEMBER_KEY, True)))


def cb_logout() -> None:
    """Cikis (oturumsuz da guvenle calisir): bu tarayicinin belirteci sunucuda iptal edilir, cerez silinir."""
    auth = st.session_state.get("auth")
    binding = st.session_state.get(SESSION_BINDING_KEY)
    _end_session(auth, binding, message=f"{auth.username} çıkış yaptı." if auth is not None else None)


def cb_logout_everywhere() -> None:
    """
    14H "Tum cihazlarda cikis": hesabin TUM belirtecleri iptal edilir, bu oturum kapanir. Kullanici YALNIZCA sunucu
    tarafi oturumdan (session_state["auth"]) alinir; oturum yoksa hicbir sey yapmaz (cb_logout gibi PUBLIC: dunya
    kilidi / uyelik beklemeden calissin). Diger acik sekmeler en gec REVALIDATE_SECONDS icinde giris ekranina doner.
    """
    auth = st.session_state.get("auth")
    if auth is None:
        return
    _end_session(auth, st.session_state.get(SESSION_BINDING_KEY), everywhere=True,
                 message=f"{auth.username}: tüm cihazlardaki oturumların kapatıldı.")


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
    _academy_move("acad_promote", lambda cm, team, p: cm.promote_to_senior(team, p), "A takıma yükseltildi")


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
    flash("prep", "success", f"Hazırlık maçı: {result.home_team_name} {result.score} {result.away_team_name}"
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
        return f"Takım talimatları kaydedildi: {instructions.describe()}"
    _tactics_action(action, None)


def cb_save_roles() -> None:
    roles = team_roles.SetPieceRoles(**{f: st.session_state.get(f"role_{f}") for f in team_roles.ROLE_FIELDS})

    def action(cm, team):
        cm.set_team_roles(team, roles)
        return "Kaptan ve duran top görevleri kaydedildi."
    _tactics_action(action, None)


def cb_suggest_roles() -> None:
    def action(cm, team):
        cm.set_team_roles(team, cm.suggest_team_roles(team))
        return "Asistan kaptan ve duran top atıcılarını belirledi."
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
        return f"Kural {index + 1} silindi."
    _tactics_action(action, None)


TACTIC_WIDGETS = ("tac_formation", "tac_editor", "tac_rows", *(f"ord_{f}" for f, _, _ in INSTRUCTION_CHOICES),
                  "ord_offside_trap", "ord_counter_attack", *(f"role_{f}" for f in team_roles.ROLE_FIELDS))


def cb_save_preset() -> None:
    name = str(st.session_state.get("preset_name") or "")
    overwrite = bool(st.session_state.get("preset_overwrite", False))

    def action(cm, team):
        preset = cm.save_tactic_preset(team, name, overwrite=overwrite)
        return f"Taktik kaydedildi: {preset.name}"
    _tactics_action(action, None, "preset_name", "preset_overwrite", "preset_pick")


def cb_apply_preset() -> None:
    preset_id = st.session_state.get("preset_pick")

    def action(cm, team):
        notes = cm.apply_tactic_preset(team, preset_id)
        return "Taktik uygulandı" + (": " + " · ".join(notes) if notes else ".")
    _tactics_action(action, None, *TACTIC_WIDGETS)


def cb_delete_preset() -> None:
    preset_id = st.session_state.get("preset_pick")

    def action(cm, team):
        cm.delete_tactic_preset(team, preset_id)
        return "Kayıtlı taktik silindi."
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
    flash("club", "success", f"{block['label']} yükseltildi → {after} · bedel {format_money(cost)}")


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
    flash("club", "success", f"{offer.brand} ile {offer.seasons} sezonluk sponsorluk imzalandı: "
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
    """
    Kenar cubugu takim secici (yalnizca turnuva modunda, ilk mactan once). Faz 13G: kurallar CareerManager.choose_club'da
    -- kariyer modunda kulup secildikten sonra (eski kayitlar dahil) degismez; istek yine de gelirse reddedilir.
    """
    if live_fixture_pending():
        flash("sidebar", "error", "Kaydedilmemiş canlı maç varken takım değiştirilemez.")
        return
    try:
        with session_scope() as db:
            cm = manager(db)
            if cm.rules.shared:                   # Faz 12: kulup yalnizca dunya panelinden (claim_club) alinir
                flash("sidebar", "error", SHARED_TEAM_TEXT)
                return
            team = cm.find_team(str(st.session_state.get("sb_team") or ""))
            if team is None:
                flash("sidebar", "error", f"Takım bulunamadı: {md_escape(st.session_state.get('sb_team'))}")
                return
            cm.choose_club(team)
    except ClubChoiceError as exc:
        flash("sidebar", "error", str(exc))
        reset_widgets("sb_team")
        return
    reset_widgets(*club_picker_view.TEAM_WIDGETS)


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


def live_blocks_week() -> bool:
    """Bitmemis (kaydedilmemis) kariyer canli maci varken hafta oynatilamaz."""
    live = st.session_state.get("live")
    return live is not None and live.is_fixture and not live.saved and not live.finished


def cb_play_week() -> None:
    live = st.session_state.get("live")
    live_results = None
    if live is not None and live.is_fixture and not live.saved:
        if not live.finished:
            flash(MAIN_AREA, "error", "Canlı maçın sürüyor: önce Canlı Maç sayfasında bitir.")
            return
        live_results = {live.fixture_id: live.result()}    # canli oynanan mac yeniden simule edilmez
    with session_scope() as db:
        cm = manager(db)
        if cm.rules.shared:                       # Faz 12: hafta yalnizca tur motoruyla (hazir / sure / yonetici)
            flash(MAIN_AREA, "error", SHARED_WEEK_TEXT)
            return
        try:
            report = cm.play_week(live_results)
        except LiveMatchError as exc:
            db.rollback()                 # yarim hafta commit edilmesin
            flash(MAIN_AREA, "error", str(exc))
            return
    # "kaydedildi" ancak commit basariliysa (session_scope blogu hatasiz bitti)
    if live_results:
        live.saved = True
    store_week_report(report)
    # Faz 13I: devam dugmesi her sayfada (kenar cubugu / ana sayfa): mesaj sayfanin ustunde (main)
    if report.played_any:
        notes = len(getattr(report, "transfer_notes", None) or [])
        flash(MAIN_AREA, "success", f"{report.week}. hafta oynandı."
              + (f" Transfer masasında {notes} gelişme var (hafta raporu)." if notes else ""))
    else:
        flash(MAIN_AREA, "info", "Oynanacak maç yok — sezon tamamlandı.")
    reset_widgets("tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee", transfer_centre_view.BID_SIG_KEY,
                  transfer_centre_view.TERMS_SIG_KEY)


def cb_new_season() -> None:
    with session_scope() as db:
        cm = manager(db)
        if cm.rules.shared:                       # Faz 12: yeni sezon tur motorunun ilerlemesiyle baslar
            flash(MAIN_AREA, "error", SHARED_WEEK_TEXT)
            return
        try:
            season = cm.start_new_season()
            text = (f"Sezon {season} başladı!" if cm.game_mode is GameMode.CAREER
                    else f"Yeni Devler Arenası turnuvası hazır (Sezon {season}). Kurayı çek!")
            flash(MAIN_AREA, "success", text)
            for note in cm.new_season_notes:
                flash("academy", "warning", note)
        except SeasonNotFinished as exc:
            flash(MAIN_AREA, "error", str(exc))
    reset_widgets("last_week_lines", "last_user_result", "last_user_cup_result", "last_cup_lines", "arena_format")


def _locked_text(cm: CareerManager) -> str:
    t = cm.tournaments.current()
    fixtures = len(cm.tournaments.fixtures(t, stage=cm.tournaments.stages(t)[0]))
    return f"Kura tamamlandı ve kilitlendi — {fixtures} maçlık ilk tur fikstürü doğrulanıp kaydedildi."


def _draw_allowed() -> bool:
    """Faz 12: paylasilan dunyada kurayi yalnizca sahip / yonetici ceker (uyelik rolu dekoratorun denetiminden)."""
    if callback_is_admin():
        return True
    flash("arena", "error", DRAW_ADMIN_TEXT)
    return False


def cb_draw_ball() -> None:
    """Kura gecesi tiklamasi: TEK top. Eleme kurasinda iki tiklama bir eslesmeyi tamamlar (ev sahibi, deplasman)."""
    if not _draw_allowed():
        return
    with session_scope() as db:
        cm = manager(db)
        try:
            cm.tournaments.draw_next()
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


# --- canli mac: Faz 14A'dan beri match_day_view.py (callback'ler orada, member_callback ile) ---


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

def sidebar_account(*, theme: bool = True) -> None:
    """Hesap: menajer, cikis (+ tema secici; oyun menusunde tema Oyun Secenekleri'ndedir -> theme=False)."""
    auth = st.session_state.get("auth")
    if auth is not None:
        u1, u2 = st.columns([3, 2], vertical_alignment="center")
        u1.markdown(f"**{escape(auth.username)}**")
        u2.button("Çıkış", key="sb_logout", on_click=cb_logout, width="stretch",
                  help="Oturumu kapatır; kaydedilmemiş canlı maç kaybolur.")
    if theme:
        theme_picker()


def theme_reload_safe() -> bool:
    """14H: tema icin sayfa yenilenebilir mi? Tarayici kalici cerezi onayladi (yenileme oturumu korur) ve canli mac yok."""
    return bool(st.session_state.get(COOKIE_OK_KEY)) and not live_fixture_pending()


def theme_picker() -> None:
    """Tema secici (theme_choice): Oyun Secenekleri, lobi, kulup secimi, giris sayfasi."""
    st.radio("Tema", list(THEME_LABELS.values()), key="theme_choice", horizontal=True, on_change=cb_theme)
    # Streamlit temayi yalnizca sayfa acilisinda okur. 14H: oturum cerezle surduruldugunden sayfa bir kez yenilenir
    # (main, theme_reload_safe); yenilenemiyorsa (cerez yok / canli mac) SADECE tablolar (canvas) eski paletle kalir.
    browser = str(getattr(getattr(st.context, "theme", None), "type", "") or "").lower()
    chosen = ofm_streamlit_base(st.session_state.get("theme"))
    if browser in ("dark", "light") and chosen and browser != chosen and not theme_reload_safe():
        st.caption("Yeni tema her yerde geçerli; **tablolar** sayfa yenilenince uyacak"
                   + (" (canlı maç bitince yenile)." if live_fixture_pending() else "."))


def ofm_streamlit_base(theme) -> str | None:
    """OFM temasinin Streamlit tabani ('dark' / 'light'); Klasik -> 'dark'."""
    from ofm_theme import STREAMLIT_THEME_NAMES

    name = STREAMLIT_THEME_NAMES.get(theme) if theme in THEME_LABELS else None
    return name.lower() if name else None


def nav_counts(db, cm: CareerManager, team: Team | None, world: worlds.WorldContext | None):
    """
    Menu sayaclari (ucuz: sayfa basina sabit sorgu). Transfer Merkezi: sirasi menajerde olan dosyalar (+ donem acikken
    tamamlanabilir anlasmalar); Kadro: bekleyen maas talepleri; Teklifler & Mesajlar (paylasilan dunya): yanit bekleyen
    teklif + okunmamis mesaj + okunmamis bildirim. (sayaclar, dunya rozet sayilari) doner.
    """
    counts: dict[str, int] = {}
    if team is None:
        return counts, None
    if cm.game_mode is GameMode.CAREER:
        counts[nav_view.TRANSFER] = TransferDesk(cm).action_count()
    counts[nav_view.SQUAD] = int(db.scalar(select(func.count()).select_from(Player).where(
        Player.team_id == team.id, Player.wage_demand.isnot(None))) or 0)
    hub = None
    if world is not None and world.kind == WORLD_KIND_SHARED:
        hub = world_panel_view.inbox_counts(db, world)
        if hub is not None:
            counts[nav_view.INBOX] = int(hub.offers_action) + int(hub.messages) + int(hub.notifications)
    return counts, hub


def continue_buttons(cm: CareerManager, prefix: str, db=None, world: worlds.WorldContext | None = None, *,
                     short: bool = False) -> None:
    """
    CM'deki "Devam": haftayi oyna ({prefix}_continue) / yeni sezon ({prefix}_new_season) / bitmemis canli maca don
    ({prefix}_live). Paylasilan dunyada hazir dugmesi ({prefix}_ready). Kenar cubugu menusu (nav, short: yalnizca
    "Devam"), telefon ust menusu (top) ve Gelen Kutusu (home) kullanir.
    """
    if world is not None and world.kind == WORLD_KIND_SHARED:
        import world_manager

        wc = world_manager.WorldController(db, world)
        status = wc.turn_status()
        world_panel_view.ready_button(status, wc.rules.ready_check, key=f"{prefix}_ready")
        st.caption(world_panel_view.ready_text(status) + " · " + world_panel_view.deadline_text(status))
        return
    if live_blocks_week():
        st.button("Canlı maça dön", key=f"{prefix}_live", on_click=nav_view.cb_nav, args=(nav_view.MATCH,),
                  type="primary", width="stretch", help="Kaydedilmemiş canlı maçın sürüyor; hafta maç bitince ilerler.")
        return
    if cm.season_finished:
        label = "Yeni sezonu başlat" if cm.game_mode is GameMode.CAREER else "Yeni turnuva"
        st.button("Yeni sezon" if short else label, key=f"{prefix}_new_season", on_click=cb_new_season,
                  type="primary", width="stretch", help=label)
        if not short:
            national_view.new_season_hint(cm)
        return
    week_text = f"{cm.current_week}. haftayı oyna"
    st.button("Devam" if short else f"Devam · {week_text}", key=f"{prefix}_continue", on_click=cb_play_week,
              type="primary", width="stretch",
              help=f"Devam: {week_text}. Haftanın bütün maçları oynanır; maçını canlı yönetmek için önce Canlı Maç.")


@dataclass(frozen=True)
class ShellContext:
    """Kenar cubugunun okudugu, ekran iskeletinin (bant, sekmeler) de kullandigi kucuk ozet (ORM nesnesi yok)."""
    counts: dict
    date_text: str
    team_name: str | None
    league_name: str | None
    username: str
    season_finished: bool = False


def game_sidebar(teams: list[str], world: worlds.WorldContext | None, pages: list[str],
                 page: str) -> ShellContext:
    """
    Faz 14S: kulup secildikten sonra kenar cubugu = CM 01/02 kisa menusu. En ustte oyun tarihi (sezon / hafta) + ◄ ►
    (ziyaret gecmisi), Devam (paylasilan dunyada dunya paneli + hazir), sonra menu: [Kulup adi] · Menajer · Yarismalar
    · Ulkeler ve Kulupler · Bul · Gelen Kutusu (n) · Oyun Secenekleri. Altta hesap (cikis, mod, tohum, dunyalar).
    """
    shared = world is not None and world.kind == WORLD_KIND_SHARED
    auth = st.session_state.get("auth")
    with st.sidebar:
        with session_scope() as db:
            cm = pin_state(db, manager(db))                   # cizim: GameState oturum boyunca tek sorgu
            team = cm.user_team
            mode = cm.game_mode
            total = cm.total_weeks()
            current = team.name if team else None
            league_name = team.league.name if team is not None and team.league is not None else None
            week = min(cm.current_week, total)
            season_finished = bool(cm.season_finished)
            date_lines = [f"Sezon {cm.season}", f"{week}. hafta" + (" · bitti" if season_finished else "")]
            date_text = f"Sezon {cm.season} · {week}. hafta" + (" · sezon bitti" if cm.season_finished else "")
            nav_view.date_bar(date_lines)
            counts, hub = nav_counts(db, cm, team, world if shared else None)
            nav_view.menu(pages, page, counts, club_name=current or "Kulüp",
                          continue_action=None if shared else (lambda prefix: continue_buttons(cm, prefix, short=True)))
            show_flash("sidebar")
            rep = cm.manager_reputation
            lvl = reputation.level(rep)
            rep_text = (f"{lvl.title} · seviye {lvl.level}/10 · Menajer tanınırlığı {rep:.1f}/20 · "
                        f"{reputation.label(rep)}")
            if not shared:
                mode_text = (f"{MODE_LABELS[mode]} · Sezon {cm.season} · Hafta {week} / {total}"
                             + (" · sezon bitti" if cm.season_finished else ""))
                teams = selectable_teams(cm, teams)
                can_change = cm.can_change_mode()
                club_locked = cm.club_locked()                    # Faz 13G: kariyerde kulup secilince kilitli
                mode_locked = cm.career_mode_locked()
        live_pending = live_fixture_pending()
        if shared:
            # Faz 12: paylasilan dunyada kulup secimi, mod degisikligi ve tohum yerine dunya paneli (hazir, sb_worlds)
            world_panel_view.sidebar_panel(world, current, counts=hub)
        elif not (club_locked or current is None):
            # Turnuva modu, ilk mactan once: katilimcilar arasinda degistirilebilir
            index = teams.index(current) if current in teams else 0
            chosen = st.selectbox("Takımın", teams, index=index, key="sb_team",
                                  help="Turnuva başlayınca (ilk maçtan sonra) kulüp değişmez.")
            st.button("Takımı ayarla", key="sb_set_team", on_click=cb_set_team,
                      disabled=chosen == current or live_pending, width="stretch",
                      help="Kaydedilmemiş canlı maç varken takım değiştirilemez." if live_pending else None)
        with st.expander("Hesap", key="ofm_account"):
            sidebar_account(theme=False)
            st.caption(rep_text)
            if not shared:
                st.caption(mode_text)
                if club_locked or current is None:
                    # Kariyer modunda (ve baslamis turnuvada) takim secici YOK: kulup degisikligi sunucuda da reddedilir
                    st.caption("Kulüp kilitli · " + ("kariyer boyunca" if mode is GameMode.CAREER else "turnuva boyunca"))
                st.button("Oyun modunu değiştir", key="sb_change_mode", on_click=cb_reset_mode,
                          disabled=not can_change or live_pending or mode_locked, width="stretch",
                          help=("Kulübünü seçtin: kariyer modu kilitli." if mode_locked
                                else "Yalnızca sezon başında, hiç maç oynanmamışken."))
                st.text_input("Kariyer tohumu (boş = rastgele)", key="career_seed",
                              help="Aynı tohum aynı sonuçları üretir (test ve tekrar için).")
                st.button("Dünyalar", key="sb_worlds", on_click=world_lobby_view.cb_open_lobby, width="stretch",
                          help="Dünyalarım, paylaşılan dünya kur, davet koduyla katıl, açık dünyalar.")
    return ShellContext(counts=counts, date_text=date_text, team_name=current, league_name=league_name,
                        username=getattr(auth, "username", None) or "Menajer", season_finished=season_finished)


def pick_sidebar(world: worlds.WorldContext) -> None:
    """Paylasilan dunyada kulubu olmayan koltuk: hesap + mesajlar + dunya paneli (menu kulup secilince gelir)."""
    with st.sidebar:
        sidebar_account()
        show_flash("sidebar")
        with session_scope() as db:
            rep = manager(db).manager_reputation
        lvl = reputation.level(rep)
        st.caption(f"{reputation.badge(lvl)} {lvl.title} · Menajer tanınırlığı {rep:.1f}/20")
        world_panel_view.sidebar_panel(world, None)


# ===========================================================================
# SEKME: KADRO & TAKTIK
# ===========================================================================

def squad_tab(db, cm: CareerManager, team: Team) -> None:
    """
    Kadro (CM 01/02 kadro ekrani, 14S): ustte dizilis + asistan, sonra YOGUN kadro listesi (mevki, ad, yas, kulupteki
    statu, durum, kondisyon, moral, form, ort. not; satira tek tik -> oyuncunun CM profil ekrani), altinda surukle-birak
    taktik tahtasi ve liste duzenleyici (erisilebilir ikinci yol), en altta oyuncu memnuniyeti. Liste, tahta ve
    duzenleyici squad_board_section parcasindadir (st.fragment): tahtadaki bir hareket yalnizca parcayi yeniden cizer.
    """
    names = list(FORMATIONS)
    c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
    if st.session_state.get("tac_formation") not in names:
        reset_widgets("tac_formation")
    c1.selectbox("Diziliş", names, index=names.index(team.formation), key="tac_formation",
                 on_change=cb_set_formation)
    c2.button("Asistana bırak", key="tac_auto", on_click=cb_auto_lineup, width="stretch")
    c3.button("Kadroyu temizle", key="tac_clear", on_click=cb_clear_lineup, width="stretch")
    squad_board_section()
    concerns_section(cm, team)


@st.fragment
def squad_board_section() -> None:
    """Kadro listesi + tahta + liste duzenleyici (kendi oturumuyla: parca tek basina yeniden calisir)."""
    tactics_board_view.rerun_app_if_needed()                  # gorev degisti / profil acildi: tum sayfa
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        if team is None:
            return
        show_flash("squad")
        rows = cv.squad_rows(team, cm.current_week, cm, stats=True)
        squad_table_section(rows)
        st.markdown("#### Taktik tahtası")
        tactics_board_view.render_board(db, cm, team)
        lineup_editor_section(rows)


def squad_status_text(r) -> str:
    """Tablodaki durum hucresi: sakat / cezali nedeni, yoksa ilk 11 (slotuyla) / kulube / kadro disi."""
    if r.unavailable:
        return r.unavailable
    return r.status + (f" · {r.slot}" if r.slot else "")


SQUAD_VIEW_KEY = "sq_view"


def squad_column_config(frame: pd.DataFrame) -> dict:
    """14G: sayisal sutunlarin bicimi (tablo SAYIYLA siralar: '850K' < '12.5M'); para kisa (compact)."""
    config = {}
    for col in frame.columns:
        if col in cv.MONEY_COLUMNS:
            config[col] = st.column_config.NumberColumn(col, format="compact")
        elif col in cv.PERCENT_COLUMNS:
            config[col] = st.column_config.NumberColumn(col, format="%d%%")
        elif col in cv.RATING_COLUMNS or col == "Gol/maç":
            config[col] = st.column_config.NumberColumn(col, format="%.2f")
    return config


def squad_table_section(rows) -> None:
    """
    CM yogun kadro listesi + "Görünüm" secici (14G: Genel / Sozlesme / Mac istatistikleri / Kondisyon, her biri >= 10
    sutun, sayisal siralama): satira tek tik -> profil (pv.selectable_table); secici ikincil yol. Yildiz yok.
    """
    if st.session_state.get(SQUAD_VIEW_KEY) not in cv.SQUAD_VIEWS:
        st.session_state[SQUAD_VIEW_KEY] = cv.VIEW_GENERAL
    view = st.segmented_control("Görünüm", list(cv.SQUAD_VIEWS), key=SQUAD_VIEW_KEY, required=True,
                                label_visibility="collapsed", width="stretch")
    frame = pd.DataFrame(cv.squad_view_rows(rows, view or cv.VIEW_GENERAL))
    for col in frame.columns:                             # sayisal sutun SAYI kalir (tablo sayiyla siralar)
        if col in cv.NUMERIC_COLUMNS:
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0)
    pv.selectable_table(pv.AREA_SQUAD, frame, [r.id for r in rows], key="sq_table",
                        column_config=squad_column_config(frame), row_height=pv.ROW_HEIGHT,
                        height=pv.table_height(len(rows)))
    with st.expander("Listeden oyuncu seç (klavye)"):
        pv.picker(pv.AREA_SQUAD, {r.id: pv.option_label(r.name, r.position, f"{r.age} yaş") for r in rows})


def lineup_editor_section(rows) -> None:
    """Surukleme olmadan kadro: eski tablo duzenleyici (erisilebilirlik ve AppTest yolu; tac_rows -> cb_save_lineup)."""
    with st.expander("Liste ile düzenle (sürüklemeden)"):
        st.caption("Durum ve Slot hücrelerine tıklayarak ilk 11'i ve kulübeyi belirle, sonra kaydet. "
                   "Sakat/cezalı oyuncular kaydedilirken reddedilir.")
        frame = pd.DataFrame([
            {
                "id": r.id, "Oyuncu": r.name, "Mv": r.position, "Form": r.form,
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
            column_order=["Oyuncu", "Mv", "Form", "Moral", "Kondisyon", "Durum", "Slot", "Not"],
            disabled=["id", "Oyuncu", "Mv", "Form", "Moral", "Kondisyon", "Not"],
            column_config={
                "Kondisyon": st.column_config.ProgressColumn("Kondisyon", min_value=0, max_value=100, format="%d%%"),
                "Durum": st.column_config.SelectboxColumn("Durum", options=list(cv.STATUS_LABELS.values()),
                                                          required=True),
                "Slot": st.column_config.SelectboxColumn("Slot", options=POSITIONS),
            },
        )
        st.session_state["tac_rows"] = edited.to_dict("records")
        st.button("Kadroyu kaydet", key="tac_save", on_click=cb_save_lineup, type="primary")


def concerns_section(cm: CareerManager, team: Team) -> None:
    """Oyuncu memnuniyeti: sure beklentisi / oynadigi ve bekleyen maas talepleri (oyuncuya tik -> oyuncu sayfasi)."""
    rows = cm.player_concerns(team)
    unhappy = [r for r in rows if r.level != "NONE" or r.wage_demand]
    counts = {label: sum(1 for r in rows if r.label == label) for label in dict.fromkeys(r.label for r in rows)}
    st.markdown("#### Oyuncu memnuniyeti")
    st.caption("Oyuncular kadro rollerine göre süre bekler (son resmi maçlar, kupa yarım sayılır). Oynayan oyuncunun "
               "şikayeti ilerlemez; uzun süre oynamayan önce süre bekler, sonra şikayet eder, en sonunda ayrılmak ister. "
               "Gücü artan ya da piyasanın çok altında kazanan oyuncu yeni maaş ister.")
    st.markdown(nav_view.facts_html([(label, count) for label, count in counts.items()] or [("Kadro", 0)]),
                unsafe_allow_html=True)
    if not unhappy:
        st.markdown('<div class="cm-empty">Tüm oyuncular mutlu.</div>', unsafe_allow_html=True)
        return
    frame = pd.DataFrame([
        {"Oyuncu": r.name, "Mv": r.position, "Rol": r.role_label, "Durum": r.label,
         "İstenen maç": round(float(r.wanted), 1), "Oynadığı": round(float(r.played), 1), "Neden": r.reason,
         "Maaş talebi/hf (EUR)": int(r.wage_demand or 0)}
        for r in unhappy
    ])
    lk.link_table("lk_concerns", frame, players=[r.player_id for r in unhappy], column_config={
        "Maaş talebi/hf (EUR)": st.column_config.NumberColumn("Maaş talebi/hf (EUR)", format="compact"),
        "İstenen maç": st.column_config.NumberColumn("İstenen maç", format="%.1f"),
        "Oynadığı": st.column_config.NumberColumn("Oynadığı", format="%.1f")})
    for r in (r for r in unhappy if r.wage_demand):
        with st.container(border=True):
            text, accept, refuse = st.columns([4, 1, 1])
            text.markdown(f"**{escape(r.name)}** yeni sözleşme istiyor: {format_money(r.current_wage)} → "
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
    st.markdown(nav_view.facts_html([
        ("U-21 akademi", f"{len(academy)}/{ACADEMY_CAPACITY}"),
        ("A takım", f"{len(team.players)}/{SENIOR_SQUAD_MAX}"),
        ("Altyapı tesisleri", f"{team.youth_facilities or '-'}/20"),
        ("Gençlerle çalışma (antrenör)", coach.working_with_youngsters if coach else "–"),
    ]), unsafe_allow_html=True)
    st.caption(f"Genç girişi her sezon {cm.youth_intake_week()}. haftada (son haftadan önce) yapılır. "
               "Potansiyel yetenek gözlemci tahminidir; gerçek tavan gizlidir. Oynayan gençler daha hızlı gelişir.")
    if locked:
        st.info("Canlı maçın sürüyor: kadro hareketleri maç kaydedilene kadar kapalı.")
    for note in cm.academy_warnings(team):
        st.warning(note)

    f1, f2, f3 = st.columns([3, 1, 2])
    positions = f1.multiselect("Mevki", POSITIONS, key="acad_pos", placeholder="Tümü")
    wonder_only = f2.toggle(cv.ACADEMY_WONDER_LABEL, key="acad_wonder",
                            help="Yalnızca geleceğin yıldızları (16-21 yaş, gözlemciye göre büyük potansiyel)")
    sort = f3.selectbox("Sırala", list(cv.ACADEMY_SORTS), key="acad_sort")
    rows = cv.academy_rows(cm, team, cv.AcademyFilter(set(positions), wonder_only, sort))

    st.markdown(panel_title_html("U-21 kadrosu"), unsafe_allow_html=True)
    if rows:
        # Faz 13G: satira tek tik -> genc oyuncunun profili (potansiyel tahmini, gelisim egrisi, maclari)
        pv.selectable_table(pv.AREA_ACADEMY, pd.DataFrame([r.to_dict() for r in rows]), [r.id for r in rows],
                            key="acad_table")
    else:
        st.info("Filtreye uyan akademi oyuncusu yok." if academy else "Akademide oyuncu yok. Genç girişini bekle.")

    # Faz 13E: secici + "İncele" ikincil (klavye) yol olarak kalir
    if rows:
        pv.picker(pv.AREA_ACADEMY, {r.id: pv.option_label(r.name, r.position, f"{r.age} yaş · {r.stars}")
                                    for r in rows})
    pv.profile_panel(db, cm, team, pv.AREA_ACADEMY)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(panel_title_html("A takıma yükselt"), unsafe_allow_html=True)
        candidates = {r.id: r for r in cv.academy_rows(cm, team)}
        if candidates:
            if st.session_state.get("acad_promote") not in candidates:
                reset_widgets("acad_promote")
            st.selectbox("Akademi oyuncusu", list(candidates), format_func=lambda i: candidates[i].label(),
                         key="acad_promote")
        st.button("A takıma yükselt", key="acad_promote_btn", on_click=cb_promote, type="primary",
                  disabled=locked or not candidates, width="stretch")
    with right:
        st.markdown(panel_title_html("U-21'e gönder"), unsafe_allow_html=True)
        seniors = {r.id: r for r in cv.demotion_rows(cm, team)}
        if st.session_state.get("acad_demote") not in seniors:
            reset_widgets("acad_demote")
        st.selectbox("A takım oyuncusu", list(seniors), format_func=lambda i: seniors[i].label(), key="acad_demote",
                     help="21 yaş üstü en fazla birkaç oyuncu akademide kalabilir.")
        st.button("U-21'e gönder", key="acad_demote_btn", on_click=cb_demote,
                  disabled=locked or not seniors, width="stretch")

    intake = st.session_state.get("last_intake")
    if intake and intake[0] == cm.season:
        ids = {pid for pid in intake[1] if pid is not None}
        fresh = [r for r in cv.academy_rows(cm, team) if r.id in ids]
        if fresh:
            with st.expander(f"Bu sezonun genç girişi ({len(fresh)} oyuncu)", expanded=True):
                lk.link_table("lk_intake", pd.DataFrame([r.to_dict() for r in fresh]), players=[r.id for r in fresh])


# ===========================================================================
# SEKME: FINANS
# ===========================================================================

def finance_tab(db, cm: CareerManager, team: Team) -> None:
    """Butce ve maaslar (CM: yogun bilgi satiri, kaydirici, en yuksek maaslar -- metrik kartlari yok)."""
    show_flash("finance")
    summary = cm.wage_summary(team)
    st.markdown(nav_view.facts_html([
        ("Transfer bütçesi (EUR)", money(team.transfer_budget)),
        ("Maaş havuzu / hf (EUR)", money(team.wage_budget)),
        ("Maaş yükü / hf (EUR)", money(summary.total)),
        ("Oyuncular / personel (EUR)", f"{money(summary.player_wages)} / {money(summary.staff_wages)}"),
        ("Boş alan / hf (EUR)", money(summary.free)),
    ]), unsafe_allow_html=True)
    st.markdown(usage_bar_html(summary.usage_pct), unsafe_allow_html=True)
    st.caption(f"Maaş havuzu %{summary.usage_pct:.0f} dolu")
    if summary.overspending:
        st.error(f"Maaş bütçesi {format_money(-summary.free)}/hafta aşılıyor! "
                 f"Fark her hafta transfer bütçesinden düşülecek.")

    st.markdown("#### Bütçe kaydırıcı")
    st.caption("Haftalık maaş havuzunu kaydır: 1 EUR haftalık alan = 52 EUR bonservis.")
    low, high = wage_budget_bounds(team.transfer_budget, team.wage_budget, team.wage_bill)
    if high <= low:
        st.info("Kaydırılabilecek bütçe yok (transfer kasası boş ve havuz maaş yüküne eşit).")
    else:
        current = st.session_state.get("fin_target")
        if current is not None and not low <= current <= high:
            reset_widgets("fin_target")
        target = st.slider("Haftalık maaş havuzu (EUR)", min_value=int(low), max_value=int(high),
                           value=int(team.wage_budget), step=100, key="fin_target")
        preview = preview_budget_shift(team.transfer_budget, team.wage_budget, int(target), team.wage_bill)
        st.markdown(nav_view.facts_html([
            ("Haftalık değişim (EUR)", money(preview.weekly_delta)),
            ("Transfer bütçesine etkisi (EUR)", money(preview.transfer_impact)),
            ("Yeni transfer bütçesi (EUR)", money(preview.new_transfer_budget)),
        ]), unsafe_allow_html=True)
        if not preview.valid:
            st.error(preview.message)
        st.button("Bütçeyi uygula", key="fin_apply", on_click=cb_apply_budget, type="primary",
                  disabled=not (preview.changed and preview.valid))

    st.markdown("#### En yüksek maaşlar")
    top = sorted(team.players, key=lambda p: -p.current_wage)[:10]
    lk.link_table("lk_wages", pd.DataFrame([
        {"Oyuncu": p.name, "Mv": p.position.value, "Yaş": p.age, "Rol": ROLE_LABELS[p.squad_role],
         "Maaş/hf (EUR)": int(p.current_wage or 0), "Değer (EUR)": int(p.market_value or 0),
         "Sözleşme (yıl)": int(p.contract_years or 0)}
        for p in top
    ]), players=[p.id for p in top], column_config={
        "Maaş/hf (EUR)": st.column_config.NumberColumn("Maaş/hf (EUR)", format="compact"),
        "Değer (EUR)": st.column_config.NumberColumn("Değer (EUR)", format="compact")})


# ===========================================================================
# SEKME: TAKTIK MERKEZI (mac onu raporu, rakip gozlem raporu, kadro planlayici)
# ===========================================================================

FORM_LETTERS = {"G": "G", "B": "B", "M": "M"}                # 14FG: CM gibi harf (emoji yok)


def form_text(form: str) -> str:
    return " ".join(FORM_LETTERS.get(letter, letter) for letter in form) if form else "—"


def _pos(value) -> str:
    return str(getattr(value, "value", value))


def absentee_rows(players) -> list[dict]:
    return [{"Oyuncu": a.name, "Mv": _pos(a.position), cv.ABILITY_LABEL: cv.star_text_word(a.stars), "Durum": a.reason,
             "Ayrıntı": a.detail or "", "Dönüş": f"Hafta {a.return_week}" if a.return_week else "—"}
            for a in players]


def watch_rows(players) -> list[dict]:
    return [{"Oyuncu": w.name, "Mv": _pos(w.position), cv.ABILITY_LABEL: cv.star_text_word(w.stars), "Gol": w.goals,
             "Asist": w.assists, "Maç": w.appearances,
             "Ort. not": None if w.average_rating is None else round(float(w.average_rating), 2),
             "Neden": w.reason, "Oynayabilir": "Evet" if w.available else "Hayır"}
            for w in players]


RATING_CONFIG = {"Ort. not": st.column_config.NumberColumn("Ort. not", format="%.2f")}


def team_preview_block(tp, key: str) -> None:
    """Mac onu: bir takimin CM paneli (sira / puan / takim gucu sozcukle / dizilis, son maclar, eksikler, dikkat
    edilecek oyuncular). Oyuncu adina tik -> oyuncu sayfasi (sisli)."""
    marker = " (sen)" if tp.is_viewer else ""
    venue = "İç saha" if tp.is_home else "Deplasman"
    st.markdown(nav_view.name_title_html(f"{tp.name}{marker} · {venue}"), unsafe_allow_html=True)
    position = f"{tp.league_position}. / {tp.league_size}" if tp.league_position else "—"
    st.markdown(nav_view.facts_html([
        ("Sıra", position), ("Puan", tp.points if tp.league_position else "—"),
        ("Takım gücü", cv.star_text_word(tp.team_stars)), ("Diziliş", tp.formation),
        ("Son maçlar", form_text(tp.form)), ("Gol", f"{tp.form_goals_for}-{tp.form_goals_against}"),
    ]), unsafe_allow_html=True)
    st.caption(f"İç saha: {tp.home_record.text} · Deplasman: {tp.away_record.text}")
    if tp.recent_matches:
        st.markdown("**Son maçlar**")
        st.markdown(nav_view.table_html(
            ["Hafta", "Turnuva", "Rakip", "Yer", "Skor", "Sonuç"],
            [[f"S{m.season} H{m.week}", m.competition_label, m.opponent_name, m.venue, m.score_text,
              FORM_LETTERS.get(m.result, m.result)] for m in tp.recent_matches], left=(1, 2)),
            unsafe_allow_html=True)
    absent = [*tp.injured, *tp.suspended]
    if absent:
        st.markdown("**Eksikler**")
        lk.link_table(f"lk_prev_absent_{key}", pd.DataFrame(absentee_rows(absent)),
                      players=[a.player_id for a in absent], hint=False)
    else:
        st.caption("Sakat ya da cezalı oyuncu yok.")
    if tp.players_to_watch:
        st.markdown("**Dikkat edilecek oyuncular**")
        lk.link_table(f"lk_prev_watch_{key}", pd.DataFrame(watch_rows(tp.players_to_watch)),
                      players=[w.player_id for w in tp.players_to_watch], column_config=RATING_CONFIG, hint=False)
    tend = tp.tendencies
    if tend.matches:
        st.caption(f"Maç başına {tend.goals_scored_per_match:.1f} gol atıyor, {tend.goals_conceded_per_match:.1f} "
                   f"yiyor · {tend.clean_sheets} gol yemediği maç · {tend.yellow_cards} sarı, {tend.red_cards} kırmızı")
    for note in tend.notes:
        st.caption(f"• {note}")


def preview_section(db, team: Team, fixture) -> None:
    preview = preview_views.build_match_preview(db, fixture.id, team.id)
    st.markdown(nav_view.name_title_html(preview.title), unsafe_allow_html=True)
    venue = " · tarafsız saha" if preview.neutral_venue else ""
    st.caption(f"{preview.competition_label} · Sezon {preview.season} · Hafta {preview.week}{venue}")
    st.info(preview.verdict)
    home, away = st.columns(2)
    with home:
        team_preview_block(preview.home, "home")
    with away:
        team_preview_block(preview.away, "away")
    st.caption(lk.LINK_HINT)
    h2h = preview.head_to_head
    st.markdown(panel_title_html("Aralarındaki maçlar"), unsafe_allow_html=True)
    st.markdown(md_escape(h2h.summary))
    if h2h.last_meetings:
        st.markdown(nav_view.table_html(["Sezon/Hafta", "Turnuva", "Maç"],
                                        [[f"S{m.season} H{m.week}", m.competition_label, m.text]
                                         for m in h2h.last_meetings], left=(1, 2)), unsafe_allow_html=True)


def scout_section(db, team: Team, fixture) -> None:
    report = preview_views.scout_opposition(db, fixture.id, team.id, rng_seed=career_seed() or 0)
    st.markdown(nav_view.name_title_html(f"Gözlem raporu · {report.opponent_name}"), unsafe_allow_html=True)
    st.markdown(nav_view.facts_html([
        ("Tahmini diziliş", report.predicted_formation), ("Güvenilirlik", report.confidence),
        ("Gözlemci", report.scout_name or "yok"),
        ("Saha", "Rakip iç sahada" if report.opponent_is_home else "Biz iç sahadayız"),
    ]), unsafe_allow_html=True)
    if report.scout_name is None:
        st.warning("Kulüpte gözlemci yok: rapor büyük ölçüde tahmin. Teknik Heyet sayfasından gözlemci işe al.")
    st.markdown("**Muhtemel ilk 11**")
    xi = list(report.predicted_xi)
    lk.link_table("lk_scout_xi", pd.DataFrame([
        {"Görev": _pos(p.role), "Oyuncu": p.name, "Mv": _pos(p.position), "Yaş": p.age,
         cv.ABILITY_LABEL: cv.star_text_word(p.stars)}
        for p in xi
    ]), players=[p.player_id for p in xi])
    left, right = st.columns(2)
    with left:
        st.markdown("**Eksikler**")
        if report.absentees:
            lk.link_table("lk_scout_absent", pd.DataFrame(absentee_rows(report.absentees)),
                          players=[a.player_id for a in report.absentees], hint=False)
        else:
            st.caption("Bilinen eksik yok.")
    with right:
        st.markdown("**Dikkat edilecek oyuncular**")
        if report.players_to_watch:
            lk.link_table("lk_scout_watch", pd.DataFrame(watch_rows(report.players_to_watch)),
                          players=[w.player_id for w in report.players_to_watch], column_config=RATING_CONFIG,
                          hint=False)
        else:
            st.caption("Öne çıkan oyuncu yok.")
    for note in (*report.tendencies.notes, *report.notes):
        st.caption(f"• {note}")
    st.caption(report.disclaimer)


def planner_section(db, team: Team) -> None:
    plan = preview_views.build_squad_plan(db, team.id)
    size = f"{plan.squad_size} / {plan.squad_max}" if plan.squad_max else str(plan.squad_size)
    st.markdown(nav_view.facts_html([
        ("A takım", size), ("Diziliş", plan.formation),
        ("Sözleşmesi biten", len(plan.contracts_ending)), ("Plan ufku", f"{plan.horizon_seasons} sezon"),
    ]), unsafe_allow_html=True)
    for advice in plan.recommendations:
        st.warning(advice)
    if not plan.recommendations:
        st.success("Kadro dengeli: önümüzdeki sezonlar için acil takviye gerekmiyor.")
    for n, group in enumerate(plan.groups):
        title = f"{group.label} · {group.count} oyuncu (önerilen {group.recommended}) · {group.status}"
        with st.expander(title, expanded=group.status != "Yeterli"):
            st.caption(f"İlk 11 ihtiyacı {group.starters} · sağlam {group.available} · grubun gücü "
                       f"{cv.star_text_word(group.stars)}")
            players = list(group.players)
            lk.link_table(f"lk_planner_{n}", pd.DataFrame([
                {"Oyuncu": pl.name, "Mv": _pos(pl.position), "Yaş": pl.age,
                 cv.ABILITY_LABEL: cv.star_text_word(pl.stars),
                 f"{cv.POTENTIAL_LABEL} (tahmin)": cv.star_text_word(pl.potential_stars),
                 "Sözleşme bitişi": f"Sezon {pl.contract_expiry_season}", "Notlar": ", ".join(pl.flags)}
                for pl in players
            ]), players=[pl.player_id for pl in players], hint=False)
            if group.prospects:
                st.caption("Akademiden aday: " + ", ".join(
                    f"{pl.name} ({pl.age}, {cv.star_text_word(pl.potential_stars)})" for pl in group.prospects))
            if group.projections:
                st.markdown(nav_view.table_html(
                    ["Sezon", "Oyuncu", "Ayrılacak", "32+ olacak", "Durum"],
                    [[pr.season, pr.count, ", ".join(pr.leaving) or "—", ", ".join(pr.aging) or "—", pr.status]
                     for pr in group.projections], left=(2, 3)), unsafe_allow_html=True)
    st.caption(lk.LINK_HINT)


def friendly_section(cm: CareerManager, team: Team) -> None:
    st.markdown(panel_title_html("Hazırlık maçı"), unsafe_allow_html=True)
    st.caption("Haftada bir hazırlık maçı: sakatlık ve kart yok, kondisyon düşmez, sakat/cezalı oyuncular da oynar. "
               "Puan tablosu, form ve moral etkilenmez; oynayan yedekler süre beklentisine yarım maç sayar.")
    if cm.season_finished:
        st.info("Sezon tamamlandı: hazırlık maçı için yeni sezonu başlat.")
        return
    opponents = cm.friendly_opponents(team)
    by_id = {t.id: f"{t.name} · {club_view.reputation_label(t.reputation)}" for t in opponents}
    if st.session_state.get("fr_opponent") not in by_id:
        reset_widgets("fr_opponent")
    pick, play = st.columns([3, 1])
    pick.selectbox("Rakip", list(by_id), key="fr_opponent", format_func=lambda i: by_id[i],
                   label_visibility="collapsed")
    history = cm.friendlies(cm.season, team_id=team.id)          # Faz 12: yalnizca bu kulubun maclari
    played_this_week = any(f.week == cm.current_week for f in history)
    play.button("Maçı oyna", key="fr_play", on_click=cb_play_friendly, type="primary", width="stretch",
                disabled=played_this_week or live_fixture_pending(),
                help="Bu hafta hazırlık maçı oynandı." if played_this_week else None)
    if history:
        st.dataframe(pd.DataFrame([
            {"Hafta": f.week, "Maç": f"{f.home_team_name} {f.home_score} - {f.away_score} {f.away_team_name}",
             "Goller": ", ".join(f"{g.get('minute')}' {g.get('player')}" for g in (f.events or [])) or "—"}
            for f in history
        ]), hide_index=True, width="stretch")


def _player_label(p) -> str:
    return f"{p.name} · {p.position.value} · {cv.ability_word(p.overall_rating)}"


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
    st.button("Talimatları kaydet", key="ord_save", on_click=cb_save_instructions, type="primary",
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
    b1.button("Görevleri kaydet", key="role_save", on_click=cb_save_roles, type="primary",
              width="stretch", disabled=live_fixture_pending())
    b2.button("Asistan belirlesin", key="role_suggest", on_click=cb_suggest_roles, width="stretch",
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
            state = "Açık" if rule.enabled else "Kapalı"
            text.markdown(f"**Kural {i + 1}{' · ' + rule.name if rule.name else ''}** ({state}) — "
                          f"{rule.describe(names)}")
            toggle.button("Kapat" if rule.enabled else "Aç", key=f"plan_toggle_{i}", on_click=cb_toggle_plan_rule,
                          args=(i,), width="stretch")
            delete.button("Sil", key=f"plan_del_{i}", on_click=cb_delete_plan_rule, args=(i,),
                          width="stretch", help="Kuralı sil")
    if len(plan) >= MAX_PLAN_RULES:
        st.caption(f"En fazla {MAX_PLAN_RULES} kural: yenisi için birini sil.")
        return
    with st.expander("Yeni kural", expanded=plan.is_empty):
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
        st.button("Kuralı ekle", key="plan_add", on_click=cb_add_plan_rule, type="primary",
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
        apply.button("Uygula", key="preset_apply", on_click=cb_apply_preset, type="primary",
                     width="stretch", disabled=live_fixture_pending())
        delete.button("Sil", key="preset_delete", on_click=cb_delete_preset, width="stretch")
    else:
        st.info("Henüz kayıtlı taktik yok: mevcut dizilişini ve talimatlarını bir isimle kaydet.")
    a, b, c = st.columns([3, 1, 1])
    a.text_input("Taktik adı", key="preset_name", max_chars=40, placeholder="Deplasman 4-5-1",
                 label_visibility="collapsed")
    b.checkbox("Üzerine yaz", key="preset_overwrite", help="Aynı adlı taktik varsa güncellenir.")
    c.button("Kaydet", key="preset_save", on_click=cb_save_preset, width="stretch",
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

NEWS_TAGS = {"TRANSFER": "Transfer", "LEAGUE_CHAMPION": "Şampiyon", "CUP_CHAMPION": "Kupa", "SPONSOR": "Sponsor",
             "BIG_RESULT": "Skor", "WONDERKID": "Genç", "CHAIRMAN": "Yönetim"}          # 14FG: CM etiketi (emoji yok)


def transfer_log_rows(logs) -> list[dict]:
    return [{"Sezon/Hafta": f"S{t.season} H{t.week}", "Oyuncu": t.player_name, "Nereden": t.from_team_name or "Kulüpsüz",
             "Nereye": t.to_team_name, "Bonservis": format_money(t.fee) if t.fee else "Bedelsiz",
             "Maaş/hf": format_money(t.wage)} for t in logs]


def world_tab(db, cm: CareerManager, team: Team) -> None:
    """Haberler ve Tarih (CM): haber akisi (tarihli CM listesi), onur listesi, transfer kayitlari. Her isim tiklanir."""
    if st.session_state.get("world_section") not in WORLD_SECTIONS:
        reset_widgets("world_section")
    section = st.radio("Bölüm", WORLD_SECTIONS, key="world_section", horizontal=True, label_visibility="collapsed")
    if section == WORLD_NEWS:
        mine = st.toggle("Yalnızca kulübümü ilgilendirenler", key="news_mine")
        news = cm.world_news(limit=40, team_id=team.id if mine else None)
        if not news:
            st.markdown('<div class="cm-empty">Henüz haber yok: hafta oynandıkça transferler, şampiyonluklar ve büyük '
                        'skorlar burada görünür.</div>', unsafe_allow_html=True)
            return
        st.markdown(nav_view.table_html(["Tarih", "Tür", "Haber"],
                                        [[f"S{item.season} H{item.week}", NEWS_TAGS.get(item.kind, "Haber"),
                                          item.text] for item in news], left=(1, 2)), unsafe_allow_html=True)
        return
    if section == WORLD_HONOURS:
        club = cm.club_honours(team)
        st.markdown(nav_view.facts_html([
            ("Kulübün kupaları", club.total_titles), ("Lig şampiyonluğu", club.league_titles),
            ("Kupa", club.cup_titles), ("İkincilik", club.runner_up_finishes),
        ]), unsafe_allow_html=True)
        honours = cm.season_honours()
        if not honours:
            st.markdown('<div class="cm-empty">Onur listesi sezon sonunda dolmaya başlar: şampiyonlar, gol kralları ve '
                        'sezonun oyuncuları.</div>', unsafe_allow_html=True)
            return
        lk.link_table("lk_honours", pd.DataFrame([
            {"Sezon": h.season, "Yarışma": h.competition_name, "Şampiyon": h.champion_name,
             "İkinci": h.runner_up_name or "—",
             "Gol kralı": f"{h.top_scorer_name} ({h.top_scorer_goals})" if h.top_scorer_name else "—",
             "Sezonun oyuncusu": (f"{h.player_of_season_name} · {h.player_of_season_rating:.2f}"
                                  if h.player_of_season_name and h.player_of_season_rating else "—"),
             "Senin sıran": f"{h.user_team_position}." if h.user_team_position else "—"}
            for h in honours
        ]), clubs={"Şampiyon": [h.champion_team_id for h in honours],
                   "İkinci": [h.runner_up_team_id for h in honours]},
            leagues={"Yarışma": [h.league_id for h in honours]},
            player_cols={"Gol kralı": [h.top_scorer_player_id for h in honours],
                         "Sezonun oyuncusu": [h.player_of_season_id for h in honours]})
        return
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Rekor transferler")
        records = cm.record_transfers(limit=10)
        if records:
            club_view.transfer_table("lk_records", records)
        else:
            st.caption("Henüz transfer yapılmadı.")
    with right:
        st.markdown(f"#### {md_escape(team.name)} transferleri")
        mine = cm.transfer_history(team=team, limit=30)
        if mine:
            club_view.transfer_table("lk_my_transfers", mine)
        else:
            st.caption("Kulübünün transfer kaydı boş.")
    st.markdown(f"#### Sezon {cm.season} tüm transferler")
    season_logs = cm.transfer_history(season=cm.season, limit=50)
    if season_logs:
        club_view.transfer_table("lk_season_transfers", season_logs)
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


def stadium_payback_text(stadium_b: dict) -> str:
    """Stadyum genisletmesinin uzun vadeli getirisi: ek sezonluk bilet geliri ve amorti suresi (transfer kasasi)."""
    if stadium_b["at_max"]:
        return "Stadyum en büyük kapasitede: bilet geliri her iç saha maçında transfer bütçesine eklenir."
    payback = stadium_b["payback_seasons"]
    if payback is None:
        return ("Genişletme şu an ek bilet geliri getirmez: taraftar talebi mevcut koltukları doldurmuyor. "
                "İtibar arttıkça talep büyür.")
    extra = stadium_b["season_gate_next"] - stadium_b["season_gate_now"]
    return (f"Uzun vade: genişletme her sezon transfer bütçesine +{format_money(extra)} ek bilet geliri katar "
            f"({stadium_b['home_matches_per_season']} iç saha lig maçı; kupa maçları ek gelirdir) ve bedelini "
            f"~{payback:g} sezonda geri öder.")


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
        ], "upgrade_cost", "Altyapıyı yükselt")
    with c2:
        facility_card(medical_b, [
            ("Toparlanma hızı", _multiplier_text(medical_b["recovery_multiplier_now"]),
             _multiplier_text(medical_b["recovery_multiplier_next"])),
        ], "upgrade_cost", "Sağlık merkezini yükselt")
        st.caption("Sağlık merkezi çarpanı fizyoterapistin etkisiyle birlikte uygulanır (10. seviye nötr).")
    with c3:
        facility_card(stadium_b, [
            ("Seyirci / maç", seats(stadium_b["attendance_now"]), seats(stadium_b["attendance_next"])),
            ("Gelir / iç saha maçı", format_money(stadium_b["gate_income_now"]),
             "—" if stadium_b["gate_income_next"] is None else format_money(stadium_b["gate_income_next"])),
            ("Bilet geliri / sezon", format_money(stadium_b["season_gate_now"]),
             "—" if stadium_b["season_gate_next"] is None else format_money(stadium_b["season_gate_next"])),
        ], "expansion_cost", "Stadyumu genişlet")
        st.caption(f"Taraftar talebi ~{seats(stadium_b['demand'])} kişi: talebin üstünde koltuk gelir getirmez.")
        st.caption(stadium_payback_text(stadium_b))

    st.markdown(panel_title_html("Sponsorluk"), unsafe_allow_html=True)
    if sponsor["active"]:
        left = sponsor["seasons_left"]
        st.success(f"**{sponsor['name']}** · haftalık {format_money(sponsor['weekly'])} · "
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
            st.button("İmzala", key=f"club_sponsor_{i}", on_click=cb_sign_sponsor, args=(i,),
                      width="stretch", disabled=live_fixture_pending())


# ===========================================================================
# SAYFA: KULUP & FINANS (Faz 13I: eski Finans + Kulup Yonetimi & Tesisler sekmeleri tek sayfada)
# ===========================================================================

def club_finance_page(db, cm: CareerManager, team: Team) -> None:
    if cm.game_mode is GameMode.CAREER:
        summary = TransferDesk(cm).finance_summary()
        if summary.payable_scheduled or summary.overdue_payable or summary.receivable_scheduled:
            st.caption(f"Transfer taksitleri: ödenecek {format_money(summary.payable_scheduled)}"
                       + (f" · gecikmiş {format_money(summary.overdue_payable)}" if summary.overdue_payable else "")
                       + f" · alınacak {format_money(summary.receivable_scheduled)} (Transfer Merkezi › Ödemeler)")
    if st.session_state.get("club_section") not in CLUB_SECTIONS:
        reset_widgets("club_section")
    section = st.radio("Bölüm", CLUB_SECTIONS, key="club_section", horizontal=True, label_visibility="collapsed")
    if section == CLUB_FINANCE:
        finance_tab(db, cm, team)
    else:
        club_tab(db, cm, team)


# ===========================================================================
# SAYFALAR: FIKSTUR & SONUCLAR, PUAN DURUMU (Faz 13I: eski Lig sekmesi ikiye ayrildi)
# ===========================================================================

def week_report_block(lines, title: str, *, expanded: bool = True) -> None:
    """Hafta raporu satirlari. 'desk' (transfer masasi notlari) veritabani metni icerir: kacisli yazilir."""
    if not lines:
        return
    with st.expander(title, expanded=expanded):
        for kind, text in lines:
            if kind == "season":
                st.success(text)
            elif kind == "desk":
                st.markdown(md_escape(text))
            else:
                st.markdown(text if kind != "result" else f"- {text}")


def week_report(db) -> tuple[list | None, str, worlds.WorldContext | None]:
    """(satirlar, baslik, paylasilan dunya): paylasilan dunyada veritabanindan, kisisel kariyerde oturumdan."""
    world = shared_page_world()
    if world is not None:
        report = world_panel_view.latest_report(db, world)
        lines = report.lines if report is not None else None
        title = f"Son haftanın raporu · Sezon {report.season}, {report.week}. hafta" if report is not None else ""
        return lines, title, world
    return st.session_state.get("last_week_lines"), "Son haftanın raporu", None


def fixtures_page(db, cm: CareerManager, team: Team) -> None:
    """Fikstur ve Sonuclar (CM): bilgi satiri, hafta oynatma, hafta raporu, kulubun fiksturu (rakibe tik -> kulup
    sayfasi) ve haftanin sonuclari (kulube tik -> kulup sayfasi)."""
    show_flash("league")
    total = cm.total_weeks()
    facts = [("Sezon", cm.season), ("Hafta", f"{min(cm.current_week, total)} / {total}")]
    if team.league_id is not None:
        facts += [("Sıra", f"{cm.position_of(team)}."), ("Puan", team.points)]
    nxt = cm.next_fixture(team.id)
    if nxt is not None:
        home = nxt.home_team_id == team.id
        opponent = nxt.away_team if home else nxt.home_team
        facts.append(("Sıradaki maç", f"{nxt.week}. hf · {opponent.name} ({'ev' if home else 'dep.'})"))
    st.markdown(nav_view.facts_html(facts), unsafe_allow_html=True)
    cup_next = cm.next_cup_fixture(team.id)
    if cup_next is not None:
        opponent = cup_next.away_team if cup_next.home_team_id == team.id else cup_next.home_team
        st.caption(f"Devler Arenası: {cup_next.week}. hafta (hafta içi) · {md_escape(team.name)} vs "
                   f"{md_escape(opponent.name)}")

    lines, title, world = week_report(db)
    if world is not None:
        # Faz 12: paylasilan dunyada hafta hazir paneliyle ilerler; rapor veritabanindan (menajerin kendi kulubu)
        world_panel_view.ready_panel(db, world)
    else:
        b1, b2 = st.columns(2)
        b1.button("Sonraki haftayı oyna", key="lg_play", on_click=cb_play_week, type="primary",
                  disabled=cm.season_finished or live_blocks_week(), width="stretch",
                  help="Canlı maçın sürüyor; önce bitir." if live_blocks_week() else None)
        if cm.season_finished:
            b2.button("Yeni sezonu başlat", key="lg_new_season", on_click=cb_new_season, width="stretch")
            national_view.new_season_hint(cm)             # Faz 12C: milli mac gunleri bekliyorsa neden
    week_report_block(lines, title)
    if lines and world is None and st.session_state.get("last_user_result") is not None:
        st.caption("Maçını **Canlı Maç** sayfasında *Son maçımı izle* ile 2D sahada izleyebilirsin.")

    fixtures = home_view.team_fixtures(db, team.id, cm.season)
    st.markdown(f"#### {md_escape(team.name)} · Sezon {cm.season} fikstürü")
    if fixtures:
        lk.link_table("lk_fixtures", pd.DataFrame([
            {"Hafta": f.week, "Turnuva": f.competition_label, "Rakip": f.opponent,
             "Yer": "İç saha" if f.at_home else "Deplasman", "Skor": f.score_text,
             "Sonuç": home_view.FORM_ICONS.get(f.result or "", "—")}
            for f in fixtures
        ]), clubs={"Rakip": [f.opponent_id for f in fixtures]})
    else:
        st.caption("Bu sezon için fikstür yok.")

    week = cm.last_played_week()
    if week is not None and team.league_id is not None:
        rows = competition_view.week_fixtures(db, team.league_id, int(cm.season), int(week))
        if rows:
            league_name = team.league.name if team.league is not None else ""
            st.markdown(f"#### {week}. hafta sonuçları · {md_escape(league_name)}")
            lk.link_table("lk_week_results", pd.DataFrame([{"Ev sahibi": h, "Skor": sc, "Deplasman": a}
                                                           for _hid, h, sc, _aid, a in rows]),
                          clubs={"Ev sahibi": [r[0] for r in rows], "Deplasman": [r[3] for r in rows]})


# ===========================================================================
# SEKME: TEKNIK HEYET
# ===========================================================================

def staff_tab(db, cm: CareerManager, team: Team) -> None:
    show_flash("staff")
    if live_fixture_pending():
        st.info("Canlı maçın sürüyor: teknik heyet değişiklikleri maç kaydedilene kadar kapalı.")
        return
    effects = cv.staff_effects(cm, team)
    st.markdown(nav_view.facts_html([
        ("Sakatlık süresi", f"×{effects.injury_multiplier:.2f} (4 hf → {effects.four_week_injury} hf)"),
        ("Kondisyon toparlanma", f"%{effects.recovery_rate * 100:.0f}"),
        ("Gözlemci yanılma payı", f"±{effects.scout_margin}"),
        ("Form çarpanı (hücum / savunma)", f"×{effects.attack_training:.2f} / ×{effects.defense_training:.2f}"),
    ]), unsafe_allow_html=True)
    st.caption("Toparlanma: maçta harcanan kondisyonun hafta içinde geri kazanılan payı.")

    st.markdown("#### Kadro")
    rows = cv.staff_rows(team.staff)
    if rows:
        frame = pd.DataFrame(rows).drop(columns=["id"])
        frame["Maaş/hf"] = [int(m.wage or 0) for m in team.staff]
        st.dataframe(frame.rename(columns={"Maaş/hf": "Maaş/hf (EUR)"}), hide_index=True, width="stretch",
                     row_height=lk.ROW_HEIGHT, height=lk.table_height(len(rows)), column_config={
                         "Maaş/hf (EUR)": st.column_config.NumberColumn("Maaş/hf (EUR)", format="compact")})
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

    st.markdown(nav_view.facts_html([
        ("Sezon", t.season), ("Format", "Eleme" if fmt is CupFormat.KNOCKOUT else "Gruplar"),
        ("Durum", STATUS_LABELS[t.status]), ("Katılımcı", f"{t.size} takım"),
        ("Takımın", tm.user_status(t, user_id) if team else "takım seçilmedi"),
    ]), unsafe_allow_html=True)
    st.caption(FORMAT_LABELS[fmt])

    if t.status is TournamentStatus.DRAW:
        draw_section(cm, t)
    else:
        cup_progress_section(cm, t, user_id)

    with st.expander(f"Katılımcılar ({t.size} takım)"):
        frame, ids = lk.split_ids(av.participant_rows(tm, t, user_id))
        lk.link_table("lk_arena_teams", frame, clubs={"Takım": ids.get("_tid", [])}, hint=False)


def draw_section(cm: CareerManager, t) -> None:
    tm = cm.tournaments
    session = tm.draw_session(t)
    can_draw = page_is_admin()                    # Faz 12: paylasilan dunyada kura sahip / yonetici isi
    st.markdown("#### Kura çekimi")
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
    balls = len(session.remaining(pot)) if pot is not None else 0          # acilacak torbadaki kapali toplar
    if can_draw and balls:
        waiting = knockout and session.last_step is not None and session.last_step.partner_id is None
        if not knockout:
            hint = "Bir topa tıkla: açılan takım grubuna yerleşir."
        elif waiting:
            hint = "Rakip torbasından bir top aç: eşleşme tamamlanır ve deplasman takımı belli olur."
        else:
            hint = "Kapalı toplardan birine tıkla: açılan takım ilk maçın ev sahibi olur; ikinci top rakibini açar."
        st.caption(hint)
        per_row = min(8, balls)
        for row_start in range(0, balls, per_row):
            cols = st.columns(per_row)
            for i in range(row_start, min(balls, row_start + per_row)):
                cols[i - row_start].button(str(i + 1), key=f"arena_ball_{i}", on_click=cb_draw_ball,
                                           help="Topu aç", width="stretch")
    if can_draw:
        st.button("Kurayı otomatik çek (atla)", key="arena_draw_all", on_click=cb_draw_all, type="primary",
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
        with st.expander(f"Kura sonucu · kilitli ilk tur fikstürü ({len(locked)} maç)",
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
        st.caption(SHARED_WEEK_TEXT)
        report = world_panel_view.latest_report(cm.db, world)
        lines = report.cup_lines if report is not None else None
    else:
        b1, b2 = st.columns(2)
        b1.button("Sonraki haftayı oyna", key="arena_play", on_click=cb_play_week, type="primary",
                  disabled=cm.season_finished or live_blocks_week(), width="stretch",
                  help="Canlı maçın sürüyor; önce bitir." if live_blocks_week() else None)
        if cm.season_finished:
            label = "Yeni sezonu başlat" if cm.game_mode is GameMode.CAREER else "Yeni turnuva"
            b2.button(label, key="arena_new_season", on_click=cb_new_season, width="stretch")
            national_view.new_season_hint(cm)             # Faz 12C: milli mac gunleri bekliyorsa neden
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
        frame, ids = lk.split_ids(results)
        lk.link_table("lk_arena_results", frame.drop(columns=["id"]),
                      clubs={"Ev sahibi": ids.get("_home_id", []), "Deplasman": ids.get("_away_id", [])})
        shown = 0
        for row in results:
            fx = cm.db.get(Fixture, row["id"])
            if fx is None or not fx.went_to_penalties or shown >= 6:
                continue
            shown += 1
            with st.expander(f"Penaltılar: {row['Ev sahibi']} {row['Skor']} {row['Deplasman']}"):
                for line in av.shootout_lines(fx):
                    st.markdown(f"- {escape(line)}")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Gol krallığı")
        scorers = av.player_rows(tm, t, by="goals")
        if scorers:
            frame, ids = lk.split_ids(scorers)
            lk.link_table("lk_arena_goals", frame, players=ids.get("_pid"), clubs={"Takım": ids.get("_tid", [])},
                          hint=False)
        else:
            st.caption("Henüz gol yok.")
    with right:
        st.markdown("#### Asist krallığı")
        assists = av.player_rows(tm, t, by="assists")
        if assists:
            frame, ids = lk.split_ids(assists)
            lk.link_table("lk_arena_assists", frame, players=ids.get("_pid"), clubs={"Takım": ids.get("_tid", [])},
                          hint=False)
        else:
            st.caption("Henüz asist yok.")

    st.markdown("#### Sakatlar ve cezalılar")
    unavailable = av.unavailable_rows(tm, t)
    if unavailable:
        frame, ids = lk.split_ids(unavailable)
        lk.link_table("lk_arena_out", frame, players=ids.get("_pid"), clubs={"Takım": ids.get("_tid", [])})
    else:
        st.caption("Turnuvada kalan takımlarda sakat ya da cezalı oyuncu yok.")


# ===========================================================================
# ILK GIRIS: OYUN MODU
# ===========================================================================

MODE_CARDS = {
    GameMode.CAREER: ("Kariyer Modu", "Lig maratonu: 6 lig, transfer, finans ve teknik heyet. "
                     "Devler Arenası maçları lig takvimiyle aynı haftalarda (hafta içi) oynanır."),
    GameMode.TOURNAMENT: ("Turnuva Modu", "Sadece Devler Arenası (Champions Cup): 16 dev kulüp, "
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


def club_select_page() -> None:
    """
    Faz 13G: kulubu olmayan menajerin (kisisel kariyer / turnuva modu) ILK sayfasi: ulke -> lig -> kulup. Sekmeler,
    kenar cubugu araclari ve transfer pazari kulup secilene kadar cizilmez. Kenar cubugunda yalnizca hesap / tema,
    oyun moduna donus (sezon basinda) ve Dunyalar kalir.
    """
    with st.sidebar:
        sidebar_account()
        show_flash("sidebar")
        with session_scope() as db:
            can_change = manager(db).can_change_mode()
        st.button("Oyun modunu değiştir", key="sb_change_mode", on_click=cb_reset_mode, disabled=not can_change,
                  width="stretch", help="Yalnızca sezon başında, hiç maç oynanmamışken.")
        st.button("Dünyalar", key="sb_worlds", on_click=world_lobby_view.cb_open_lobby, width="stretch",
                  help="Dünyalarım, paylaşılan dünya kur, davet koduyla katıl, açık dünyalar.")
    with session_scope() as db:
        club_picker_view.render_career_picker(db, manager(db))


SCROLL_TOP_SCRIPT = ("<script>(function(){try{var m=document.querySelector('[data-testid=\"stMain\"]');"
                     "if(m&&m.scrollTo){m.scrollTo(0,0);}window.scrollTo(0,0);}catch(e){}})()</script>")


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
    st.set_page_config(page_title=BRAND_TITLE, page_icon="⚽", layout="wide")
    # 14H: yenileme / sunucu yeniden baslatmasi oturumu kapatmaz (cerezden devam) + belirtecin periyodik dogrulamasi.
    # Temadan ONCE: devam eden oturum temiz baslar, tema sonra URL'den okunur.
    auth = restore_session()
    theme = current_theme()
    st.markdown(CSS + pitch.PITCH_CSS + BRACKET_CSS + MODE_CSS + pv.PROFILE_CSS + club_picker_view.PICKER_CSS
                + nav_view.NAV_CSS + theme_css(theme, login=auth is None),
                unsafe_allow_html=True)
    mount_session_cookie()                                      # cerez yaz / sil (gorunmez, kendi parcasi)
    st.html(LANG_SCRIPT, unsafe_allow_javascript=True)          # Turkce buyuk harf (GİRİŞ, TESİSLERİ)
    # Streamlit'in KENDI temasi (widget icleri + canvas tablolar) uygulama secimine sabitlenir. Deger degisince sayfa
    # BIR KEZ yenilenir: oturum yokken hep; oturum varken yalnizca tarayici kalici cerezi yazdigini onayladiysa
    # (yenileme oturumu korur, 14H) ve kaydedilmemis canli mac yoksa. Aksi halde deger sonraki acilis icin yazilir.
    st.html(theme_sync_script(theme, reload=auth is None or theme_reload_safe()), unsafe_allow_javascript=True)

    if not wait_for_db(retries=2, delay=0.5, verbose=False):
        st.error("Veritabanına bağlanılamadı. `docker compose up -d` çalışıyor mu?")
        st.stop()
    if auth is None:
        login_screen()
        return
    # 14S: dev "ONLINE FOOTBALL MANAGER" basligi yok (CM iskeleti: her ekranin tepesi kulup renginde bant)
    if st.session_state.pop(club_picker_view.SCROLL_TOP_KEY, False):
        # Faz 13G: kulup secildi -> yeni sayfa en ustten baslar (uzun listenin altinda kalan kaydirma "ekran
        # degismedi" gibi gorunuyordu); karsilama mesaji basligin altinda (telefonda kenar cubugu kapali).
        st.html(SCROLL_TOP_SCRIPT, unsafe_allow_javascript=True)
    show_flash(club_picker_view.WELCOME_AREA)
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
    if not shared and not has_team:
        club_select_page()                                      # Faz 13G: kulup secimi kariyerin ilk adimi
        return

    if shared and not has_team:
        pick_sidebar(world)                                     # kulupsuz koltuk: kulup secimi + dunya sekmeleri
        club_pick_page(world, rules)
        return
    # Faz 13I / 14S: CM iskeleti -- menu, bant, sekmeler, yalnizca secili sayfa, alt eylemler + Geri / Ileri
    pages = nav_view.with_shell(nav_view.pages_for(tournament=mode is GameMode.TOURNAMENT,
                                                   shared=shared or rules.shared,
                                                   internationals=bool(rules.internationals),
                                                   role=world.role if world is not None else None))
    page = nav_view.current_page(pages)
    shell = game_sidebar(teams, world if shared else None, pages, page)
    nav_view.top_nav(pages, page, shell.counts, continue_action=None if shared else top_continue,
                     date_text=shell.date_text, club_name=shell.team_name or "Kulüp",
                     notes_action=top_notes if shell.season_finished and not shared else None)
    profile = profile_area(page)
    if profile is None and page not in nav_view.PARAM_PAGES:    # 14F: oyuncu / kulup / ulke sayfasi kendi bandini cizer
        screen_header(page, pages, shell)
    render_page(page, teams, world if shared else None, profile=profile)
    if page != nav_view.MATCH and profile is None:     # canli mac dongusu sayfanin sonunda calisir (13C: mac ekrani)
        nav_view.page_footer(page, [(text, slug) for text, slug in FOOTER_ACTIONS.get(page, ()) if slug in pages])


def screen_title(page: str, shell: ShellContext) -> str:
    """CM bandinin yazisi: kulup ekranlarinda kulubun adi, yarismalarda lig, gelen kutusunda menajerin adi."""
    section = nav_view.section_for(page).key
    if section == nav_view.SEC_CLUB:
        return shell.team_name or nav_view.PAGES[page].label
    if section == nav_view.SEC_INBOX:
        return f"{shell.username} · {nav_view.PAGES[page].label}"
    if section == nav_view.SEC_MANAGER:
        return shell.username
    if page == nav_view.TABLE:
        return param_league_name() or shell.league_name or nav_view.PAGES[page].label
    return nav_view.PAGES[page].label


def param_league_name() -> str | None:
    """14F: lig sayfasi parametreliyse (?sayfa=puan-durumu&lig=..) bant o ligin adini yazar (tek kucuk sorgu)."""
    param = nav_view.current_param()
    if not isinstance(param, int):
        return None
    from models import League

    with session_scope() as db:
        return db.scalar(select(League.name).where(League.id == param))


def screen_header(page: str, pages: list[str], shell: ShellContext) -> None:
    """Ekranin tepesi (CM): kulup renginde tam genislik bant + bolumun sekme satiri."""
    colors = nav_view.club_band_colors(shell.team_name) if shell.team_name else None
    st.markdown(nav_view.band_html(screen_title(page, shell), colors), unsafe_allow_html=True)
    nav_view.tab_row(pages, page, shell.counts)


# Profil ekrani (14S): bu sayfanin alanlarindan birinde profil aciksa sayfa yerine oyuncunun CM ekrani cizilir
PROFILE_AREAS: dict[str, tuple[str, ...]] = {
    nav_view.SQUAD: (pv.AREA_SQUAD,), nav_view.ACADEMY: (pv.AREA_ACADEMY,),
    nav_view.TRANSFER: (pv.AREA_MARKET, pv.AREA_SHORTLIST), nav_view.INBOX: (pv.AREA_HUB,),
    nav_view.NATIONAL: (pv.AREA_NATIONAL,), nav_view.NATIONS: (pv.AREA_CLUBS,), nav_view.FIND: (pv.AREA_FIND,),
}


def profile_area(page: str) -> str | None:
    """Bu sayfada acik profilin alani (yoksa None)."""
    area = pv.open_area()
    return area if area is not None and area in PROFILE_AREAS.get(page, ()) else None


def top_continue(prefix: str) -> None:
    """Telefon ust menusundeki Devam (kendi oturumuyla; masaustunde gizli kapta): CM izgarasinin ilk hucresi."""
    with session_scope() as db:
        continue_buttons(pin_state(db, manager(db)), prefix, short=True)


def top_notes() -> None:
    """Telefon izgarasinin altinda Devam'in notu: sezon bitti ama yeni sezon bekliyorsa nedeni (milli mac gunleri)."""
    with session_scope() as db:
        national_view.new_season_hint(pin_state(db, manager(db)))


# CM iskeleti: her ekranin altindaki eylem dugmeleri (baska ekranlara kisayol) -- nav_view.page_footer
FOOTER_ACTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    nav_view.HOME: (("Teklifler ve Mesajlar", nav_view.INBOX), ("Kadro", nav_view.SQUAD),
                    ("Taktik", nav_view.TACTICS), ("Maçlar", nav_view.FIXTURES),
                    ("Transfer Merkezi", nav_view.TRANSFER), ("Haberler ve Tarih", nav_view.NEWS)),
    nav_view.INBOX: (("Gelen Kutusu", nav_view.HOME), ("Transfer Merkezi", nav_view.TRANSFER)),
    nav_view.SQUAD: (("Taktik", nav_view.TACTICS), ("Canlı Maç", nav_view.MATCH),
                     ("Maçlar", nav_view.FIXTURES), ("Akademi", nav_view.ACADEMY),
                     ("Transfer Merkezi", nav_view.TRANSFER)),
    nav_view.TRANSFER: (("Kadro", nav_view.SQUAD), ("Finans", nav_view.CLUB),
                        ("Teknik Heyet", nav_view.STAFF)),
    nav_view.TACTICS: (("Kadro", nav_view.SQUAD), ("Canlı Maç", nav_view.MATCH)),
    nav_view.FIXTURES: (("Puan Durumu", nav_view.TABLE), ("Canlı Maç", nav_view.MATCH)),
    nav_view.TABLE: (("Fikstür ve Sonuçlar", nav_view.FIXTURES), ("Devler Arenası", nav_view.ARENA)),
    nav_view.CLUB: (("Transfer Merkezi", nav_view.TRANSFER),),
    nav_view.MATCH: (("Kadro", nav_view.SQUAD), ("Taktik", nav_view.TACTICS)),
    nav_view.MANAGER: (("Haberler ve Tarih", nav_view.NEWS), ("Gelen Kutusu", nav_view.HOME)),
    nav_view.NEWS: (("Gelen Kutusu", nav_view.HOME),),
}


def home_page(db, cm: CareerManager, team: Team, world: worlds.WorldContext | None = None) -> None:
    """Gelen Kutusu (CM haber ekrani): home_view + web_app'in devam eylemi ve hafta raporu."""
    lines, title, shared_world = week_report(db)
    hub = world_panel_view.inbox_counts(db, world) if world is not None else None
    deals = TransferDesk(cm).summaries(open_only=True, limit=30) if cm.game_mode is GameMode.CAREER else []
    auth = st.session_state.get("auth")
    when = f"S{cm.season} H{max(1, cm.current_week - 1)}" if lines else ""
    home_view.render_home(db, cm, team, report_lines=lines, report_when=when, hub_counts=hub, deals=deals,
                          manager_name=getattr(auth, "username", None) or "Menajer",
                          continue_action=lambda prefix: continue_buttons(cm, prefix, db, shared_world))


def manager_page(db, cm: CareerManager, team: Team | None) -> None:
    """Menajer (CM "The Manager"): tanirlik, kulup, kariyer ozeti. Yalnizca menajerin gorebilecegi bilgiler."""
    auth = st.session_state.get("auth")
    rep = cm.manager_reputation
    lvl = reputation.level(rep)
    rows = [("Menajer", getattr(auth, "username", None) or "Menajer"), ("Unvan", lvl.title),
            ("Seviye", f"{lvl.level}/10"), ("Menajer tanınırlığı", f"{rep:.1f}/20 · {reputation.label(rep)}"),
            ("Oyun modu", MODE_LABELS.get(cm.game_mode, "—"))]
    if team is not None:
        rows.append(("Kulüp", team.name))
        if team.league is not None and cm.game_mode is GameMode.CAREER:
            rows += [("Lig", team.league.name), ("Lig sırası", f"{cm.position_of(team)}."), ("Puan", team.points)]
        rows.append(("Form", cm.team_form(team.id) or "—"))
    total = cm.total_weeks()
    rows.append(("Sezon · hafta", f"{cm.season} · {min(cm.current_week, total)} / {total}"))
    st.markdown(cm_pairs_html(rows), unsafe_allow_html=True)
    st.caption("Menajer tanınırlığı kazandıkça büyür (lig sırası, kupa turları, şampiyonluk); daha büyük kulüplerin "
               "iş teklifleri ve oyuncuların ikna olması buna bağlıdır.")


cm_pairs_html = nav_view.pairs_html


def options_page(db, cm: CareerManager, team: Team | None) -> None:
    """Oyun Secenekleri (CM "Game Options"): tema. Hesap (cikis, mod, tohum, dunyalar) menunun altindaki Hesap'ta."""
    st.markdown(panel_title_html("Görünüm"), unsafe_allow_html=True)
    theme_picker()
    st.caption("OFM Klasik: Championship Manager 01/02 düzeni (varsayılan). OFM Dark / OFM Light: aynı ekranlar, "
               "modern renklerle. Seçim bu tarayıcıda hatırlanır.")
    st.markdown(panel_title_html("Hesap"), unsafe_allow_html=True)
    st.caption("Çıkış, oyun modu, kariyer tohumu ve dünyalar: menünün altındaki **Hesap** bölümünde.")
    session_security_panel()


def session_security_panel() -> None:
    """14H: oturum guvenligi -- acik oturum sayisi ve "Tum cihazlarda cikis" (hesabin tum belirtecleri iptal)."""
    auth = st.session_state.get("auth")
    if auth is None:
        return
    try:
        count = accounts.active_session_count(auth.user_id)
    except (SQLAlchemyError, ValueError):
        count = None
    remembered = st.session_state.get(COOKIE_OK_KEY)
    here = ("Bu tarayıcıda oturumun sayfa yenilense de sürer." if remembered
            else "Bu sekmedeki oturum sayfa yenilenince kapanabilir (tarayıcı çerezi yazılmadı).")
    st.caption(here + (f" Açık oturum: **{count}**." if count else ""))
    st.button("Tüm cihazlarda çıkış yap", key="opt_logout_all", on_click=cb_logout_everywhere,
              help="Bu tarayıcı dahil hesabının bütün açık oturumları kapanır; diğer cihazlar en geç bir dakika "
                   "içinde giriş ekranına döner. Ortak bir bilgisayarda açık bıraktıysan kullan.")


# Sayfa -> cizici (db, cm, team). Canli Mac ayri: mac dongusu kendi oturumlarini acar ve sayfanin sonunda calisir.
PAGE_RENDERERS = {
    nav_view.SQUAD: squad_tab, nav_view.TACTICS: prep_tab, nav_view.ACADEMY: academy_tab,
    nav_view.STAFF: staff_tab, nav_view.FIXTURES: fixtures_page, nav_view.TABLE: competition_view.render_competition,
    nav_view.ARENA: arena_tab, nav_view.NEWS: world_tab, nav_view.CLUB: club_finance_page,
    nav_view.TRANSFER: transfer_centre_view.render_transfer_centre,
    nav_view.INBOX: market_view.hub_tab, nav_view.NATIONAL: national_view.national_tab,
    nav_view.ADMIN: world_admin_view.admin_tab, nav_view.MANAGER: manager_page,
    nav_view.NATIONS: club_view.render_nations, nav_view.FIND: find_view.render_find,
    nav_view.OPTIONS: options_page,
    nav_view.PLAYER: lambda db, cm, team: pv.profile_page(db, cm, team, nav_view.current_param()),
    nav_view.CLUB_PAGE: club_view.render_club, nav_view.NATION: club_view.render_nation,
}
TEAMLESS_PAGES = frozenset({nav_view.ARENA, nav_view.INBOX, nav_view.NATIONAL, nav_view.ADMIN, nav_view.MANAGER,
                            nav_view.NATIONS, nav_view.FIND, nav_view.OPTIONS, *nav_view.PARAM_PAGES})


def render_page(page: str, teams: list[str], world: worlds.WorldContext | None = None,
                profile: str | None = None) -> None:
    """Secili sayfayi (ya da o sayfada acik oyuncu profilini) cizer; diger sayfalar HIC cizilmez (sorgu da atilmaz)."""
    if page == nav_view.MATCH:
        match_day_view.render(teams)                          # Faz 14A: mac gunu ekrani
        return
    with session_scope() as db:
        cm = pin_state(db, manager(db))                       # cizim: GameState oturum boyunca tek sorgu
        team = cm.user_team
        if profile is not None:
            pv.profile_screen(db, cm, team, profile)         # 14S: CM oyuncu ekrani (bant, sekmeler, Geri)
            return
        if team is None and page not in TEAMLESS_PAGES:
            st.info("Bu sayfa için bir kulübün olmalı.")
            return
        if page == nav_view.HOME:
            home_page(db, cm, team, world)
            return
        PAGE_RENDERERS[page](db, cm, team)


# Oturum kapisi (P1 guvenlik): giris/kayit/cikis disindaki TUM callback'ler oturum ister; Faz 12: dunyaya bagli
# oturumda uyelik ve paylasilan dunyada SHARED dunya kilidi (web_common.member_callback).
# Widget'lar callback'i cizim aninda global adla alir; sarma, main() calismadan once yapilir.
for _name, _callback in list(globals().items()):
    if _name.startswith("cb_") and callable(_callback) and _name not in PUBLIC_CALLBACKS:
        globals()[_name] = member_callback(_callback)


if __name__ == "__main__":
    main()
