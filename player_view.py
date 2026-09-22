"""
player_view.py
==============
Oyuncu profili (Faz 13E / K14; Faz 13I: Championship Manager 01/02 duzeni). Bir oyuncuya tiklayinca (ya da
"🔎 İncele") acilan inceleme paneli. Kurallar baska modullerde (career_manager, transfers, transfer_desk, development,
concerns, ratings, cm_attributes); bu modul VERI TOPLAMA + SUNUM yapar. Yazan tek yer Eylem menusu (cb_pv_action:
teklif dosyasi, takip listesi, gozlemci, liste bayragi) -- o da masa / kariyer yoneticisi uzerinden, member_callback ile.

DUZEN (CM 01/02 fikri, kendi temamiz ve kodumuz; oyundan gorsel / metin alinmadi):
    ust cubuk   ◀ Geri / İleri ▶ (profilin acildigi listede onceki / sonraki oyuncu) ... Eylem ▾ (pv_actions)
    baslik      mevki kodu + ad + (kulup), genis; biyografi satiri: yas, uyruk (milli mac), rozetler
    sekmeler    Profil · Sakatlık & Cezalar · Sözleşme · Transfer · Geçmiş  (pv_section)
    Profil      31 CM ozelligi 1-20, uc sutunlu yogun izgara: cm_attributes.ATTRIBUTE_GROUPS (Teknik | Zihinsel |
                Fiziksel + Kalecilik), grup icinde Turkce alfabetik (renk bantlari 1-5 / 6-10 / 11-15 / 16-20, yalniz
                tema tokenlari); son hucrelerde (Durum) tercih ettigi ayak, form, moral, kondisyon %; alti: bu sezonun
                istatistik matrisi (Hazirlik / Lig / Devler Arenasi / Milli / Toplam -- yalnizca veri olan satirlar,
                yalnizca SAKLANAN sutunlar: mac, dk, gol, asist, sut, isabetli sut, kurtaris, kart, ort. not);
                mevki satiri; gelisim ve ayni mevkidekilerle karsilastirma acilir bolumlerde
    Sakatlık & Cezalar  bugunku durum, lig / kupa cezasi ve sari kart birikimi, sakatlik egilimi (gozlemci %75+),
                sakatlik gecmisi
    Sözleşme    maas, sure, BITIS SEZONU, deger, kadro rolu, serbest kalma bedeli, sozlesme maddeleri, sure
                beklentisi; 15A: suren sozlesme gorusmesinin durumu (+ "Sözleşme masasını aç"), sozlesmesi bu sezon
                bitiyorsa uyari, kendi oyuncum ON SOZLESME imzaladiysa "sezon sonunda X kulübüne gidecek"
    Transfer    liste / kiralik / yasak durumu, istenen bedel, acik transfer dosyasi (Transfer Merkezi'ne baglanti),
                kulubun fiyat beklentisi (yalnizca SISLI aralik), transfer gecmisi
    Geçmiş      kariyer toplami, sezon sezon tablo, son maclar

OZELLIK GORUNURLUGU ("CM gibi + gozlemci", sahibin karari). Sayfa cm_attributes'tan gelir (tek kaynak: FM verisi
varsa o, yoksa motorun alti ozelliginden deterministik turetilen, motorla tutarli 1-20); gorunurluk kurali tek yerde,
cm_attributes.attribute_display'de: kendi oyuncun (akademi dahil) bilgi %100 -> kesin sayi; gozlemcinin iyi bildigi
oyuncu (%70+) kesin; kismen bilinen araligi (bilgi arttikca daralir); bilinmeyen "?". Renk bandi EKRANDAKI metinden
(araligin orta noktasi) hesaplanir, gercek degerden degil: renk gizli bilgi sizdirmaz. Gizli potansiyel ve motorun
1-99 sayilari hicbir yerde yazilmaz (potansiyel her zaman gozlemci tahmini, yildiz). Form / moral / kondisyon /
kulup ici rol yalnizca kendi oyuncunda. Kulubun hedef / taban bedeli gosterilmez; fiyat beklentisi masanin sisli
araligidir (K12).

GIRIS NOKTALARI (panel ayni sayfada yerinde acilir):
    1) SATIRA TEK TIK (Faz 13G, birincil yol): selectable_table -> st.dataframe(on_select=cb_pv_row,
       selection_mode=["single-row", "single-cell"]). Satir -> oyuncu eslemesi SUNUCUDA tutulur ({anahtar}__ids);
       tablonun oyuncu listesi Geri / İleri icin pv_list'e yazilir.
    2) secici + "🔎 İncele" (ikincil / klavye yolu) ve kart icindeki satir dugmeleri (teklif kartlari)
    3) taktik tahtasinda cift tik / sag tik "Profil" (tactics_board_view)
    squad      Kadro               web_app.squad_tab (sq_table)
    market     Transfer Merkezi    transfer_centre_view.search_section (mkt_table: satir ayni zamanda hedef oyuncu)
    shortlist  Takip listesi       transfer_centre_view.shortlist_section (sl_table)
    academy    Akademi             web_app.academy_tab (acad_table)
    hub        Teklifler & Listeler market_view.offers_section / listings_section (hub_list_TRANSFER / _LOAN)
    national   Milli Takım kadrosu national_view._squad_section (nt_table)

WIDGET ANAHTARLARI:
    pv_open                oturum durumu: (alan, oyuncu_id); pv_list: Geri / İleri listesi
    pv_section             sekme satiri (segmented control)
    pv_prev / pv_next      listede onceki / sonraki oyuncu
    pv_actions             Eylem menusu (popover): pv_act_bid, pv_act_shortlist, pv_act_scout, pv_act_loan,
                           pv_act_list, pv_act_ask, pv_act_renew, pv_act_deal; 15A: pv_act_contract (yenileme
                           masasi), pv_act_terminate (fesih onay ekranina), pv_act_free (serbest oyuncuyla sozlesme),
                           pv_act_pre (on sozlesme), pv_talk_open (Sözleşme sekmesi: suren gorusmeyi ac)
    pv_pick_{alan}         secici (selectbox) -- kendi secicisi olmayan alanlarda
    pv_btn_{alan}          "🔎 İncele" dugmesi (secicili alanlar)
    pv_row_{alan}_{id}     satir ici "🔎 İncele" dugmesi (teklif / liste kartlari)
    pv_close               "✖️ Profili kapat"
    {tablo}                secilebilir oyuncu tablosu (st.dataframe); {tablo}__ids satir -> oyuncu id (sunucu)
    pv_cmp                 karsilastirma kapsami (kendi kadrom / lig)
    pv_panel               panel kabi (st.container key; profil CSS'i bununla daraltilir)

CIZIM MALIYETI: giris noktalari SORGU EKLEMEZ. Profil acikken yalnizca SECILI sekmenin verisi okunur ve sorgu sayisi
mac / mevkidas sayisindan BAGIMSIZDIR: baslik 1-2 (gozlemci), bilgi yuzdesi 1-2, sezon ozeti 2 (tek GROUP BY + kulup
adlari tek IN), hazirlik golleri 1 (JSONB), son maclar 1, transfer gecmisi 1, karsilastirma havuzu 1.

GUVENLIK: oturum durumu callback'leri requires_auth; veritabanina yazan Eylem callback'i member_callback (kulup
cm.user_team; masa sahipligi dogrular). Kullanici / veritabani metinleri HTML'e html.escape, Streamlit metnine md_escape.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import aliased

import career_views as cv
import cm_attributes
import concerns as concern_rules
import development
import fitness
import ratings
import transfers
from career_manager import ShortlistError
from database import session_scope
from finance import format_money
from models import (
    Competition,
    Fixture,
    Loan,
    LoanStatus,
    Player,
    PlayerMatchStat,
    Position,
    SquadRole,
    Team,
    TransferLog,
)
from ofm_theme import panel_title_html
from stars import star_value
from transfer_desk import TransferDesk
from transfers import TransferError
from web_common import (
    flash,
    manager,
    md_escape,
    member_callback,
    money,
    page_world,
    requires_auth,
    reset_widgets,
)

# ===========================================================================
# ANAHTARLAR VE SABITLER
# ===========================================================================

PROFILE_KEY = "pv_open"                 # (alan, oyuncu_id)
SECTION_KEY = "pv_section"
COMPARE_KEY = "pv_cmp"
CLOSE_KEY = "pv_close"
PANEL_KEY = "pv_panel"                  # panel kabi (CSS: .st-key-pv_panel)
SCROLL_KEY = "pv_scroll"                # Faz 13G: panel yeni acildi -> bir kez gorunur alana kaydirilir

AREA_SQUAD = "squad"
AREA_MARKET = "market"
AREA_SHORTLIST = "shortlist"
AREA_ACADEMY = "academy"
AREA_HUB = "hub"
AREA_NATIONAL = "national"
AREA_CLUBS = "clubs"                    # 14S: Ulkeler ve Kulupler > kulup kadrosu (club_view)
AREA_FIND = "find"                      # 14S: Bul > oyuncu sonuclari (find_view)
AREA_PAGE = "page"                      # 14F: parametreli oyuncu sayfasi (?sayfa=oyuncu&id=..): nav gecmisine girer
AREAS: tuple[str, ...] = (AREA_SQUAD, AREA_MARKET, AREA_SHORTLIST, AREA_ACADEMY, AREA_HUB, AREA_NATIONAL,
                          AREA_CLUBS, AREA_FIND)
FULL_RERUN_KEY = "tb_full_rerun"        # tactics_board_view.FULL_RERUN_KEY: parca (kadro) icinden acilan profil icin
                                        # tum sayfa yeniden cizilir (profil sayfa duzeyinde bir ekrandir)

LIST_KEY = "pv_list"                    # Geri / Ileri: profilin acildigi listenin oyuncu id'leri
TARGET_KEY = "pv_target"                # (secici anahtari, oyuncu id): Geri'de secici bu oyuncuya doner (14S)

# Faz 13I: CM 01/02 sekme satiri
SEC_PROFILE, SEC_INJURY, SEC_CONTRACT = "Profil", "Sakatlık ve Cezalar", "Sözleşme"
SEC_TRANSFER, SEC_HISTORY = "Transfer", "Geçmiş"
SECTIONS: tuple[str, ...] = (SEC_PROFILE, SEC_INJURY, SEC_CONTRACT, SEC_TRANSFER, SEC_HISTORY)
SEC_ATTRIBUTES, SEC_STATS, SEC_COMPARE = SEC_PROFILE, SEC_HISTORY, SEC_PROFILE      # eski adlar (13E)

CMP_SQUAD = "Kendi kadrom"
CMP_LEAGUE = "Ligdeki mevkidaşları"
CMP_OPTIONS: tuple[str, ...] = (CMP_SQUAD, CMP_LEAGUE)

INSPECT_LABEL = "İncele"
RECENT_MATCHES = 8                      # son maclar tablosunda en fazla satir
LEAGUE_PEERS = 12                       # ligden karsilastirmaya alinan mevkidas sayisi
NO_DATA_TEXT = "Henüz resmi maç oynamadı."
FOG_TEXT = ("Bu oyuncu senin kulübünde değil: özellikler gözlemcinin tahmin aralığıdır; "
            "form, moral, kondisyon ve kulüp içi rolü bilinmez.")

# 14G: etiketler TEK KAYNAKTAN (career_views): motorun alti ozelligi, mevki adlari, CM kadro statusu, yetenek ve
# form / moral sozcukleri. Buradaki adlar geriye uyumluluk icin ayni nesneleri gosterir.
ATTRIBUTE_LABELS: tuple[tuple[str, str], ...] = cv.ENGINE_ATTRIBUTE_LABELS
POSITION_LABELS: dict[str, str] = cv.POSITION_LABELS
# CM 01/02 "Set Role At Club -> Squad Status" (Faz 13I). KURAL DEGISMEDI: motorun uc kadro rolu transfers.ROLE_LABELS'taki
# CM etiketleriyle yazilir; genc (21 ve alti) yedekler CM'deki iki gelecek duzeyine ayrilir (yalnizca gosterim).
SQUAD_STATUS_LABELS: tuple[str, ...] = cv.SQUAD_STATUS_LABELS
ROLE_PROMISE_LABELS: dict[SquadRole, str] = dict(transfers.ROLE_LABELS)     # sozlesme masasinin rol sozu (tek kaynak)
YOUNG_STATUS_AGE = cv.YOUNG_STATUS_AGE
squad_status = cv.squad_status


COMPETITION_LABELS: dict[str, str] = {Competition.LEAGUE.value: "Lig", Competition.CUP.value: "Devler Arenası"}

# Yas bantlari -- development.py kurallarindan (age_growth_factor / DECLINE_START_AGE) turetilmistir.
AGE_BANDS: tuple[tuple[int, int, str], ...] = (
    (15, 21, "Hızlı gelişim"),
    (22, 25, "Gelişim"),
    (26, 29, "Zirve"),
    (30, 31, "Duraklama"),
    (32, 99, "Gerileme"),
)

# Yildiz degerinin (0.5-5.0) sozle karsiligi. Sayi yerine gecen tek "olcek" budur (tek kaynak: career_views).
STAR_WORDS: tuple[tuple[float, str], ...] = cv.ABILITY_WORDS

PROFILE_CSS = """
<style>
.st-key-pv_bandrow{gap:6px !important;align-items:stretch !important;flex-wrap:nowrap !important}
.st-key-pv_bandrow>div:has(.ofm-band){flex:1 1 auto;min-width:0}
.st-key-pv_arrows{gap:2px !important;flex:0 0 auto}
[data-testid="stMain"] .st-key-pv_arrows button[data-testid]{min-height:3.3rem;min-width:1.9rem;padding:0 .3rem !important;
  background:var(--band-bg,#b71c1c) !important;border:1px solid #000 !important;border-radius:0 !important;
  box-shadow:none !important}
[data-testid="stMain"] .st-key-pv_arrows button[data-testid] p{color:#ffffff !important;font-weight:700;
  text-shadow:1px 1px 0 #000 !important}
[data-testid="stMain"] .st-key-pv_arrows button:disabled{opacity:.45 !important}
[data-testid="stMain"] .st-key-pv_bandrow [data-testid="stPopoverButton"]{background:#ffffff !important;
  border:1px solid #000 !important;border-radius:0 !important;box-shadow:none !important;min-width:9.5rem;
  min-height:1.8rem !important;justify-content:space-between !important;padding:.1rem .45rem !important}
[data-testid="stMain"] .st-key-pv_bandrow [data-testid="stPopoverButton"] *{color:#000000 !important;
  font-weight:400 !important;text-shadow:none !important}
.pv-head{text-align:center;margin:.3rem 0 .15rem}
.pv-head .bio{color:var(--ofm-bio);font-size:1.15rem;font-weight:700;text-shadow:1px 1px 0 var(--ofm-shadow);
  overflow-wrap:anywhere}
.pv-head .tags{display:flex;flex-wrap:wrap;justify-content:center;gap:.1rem .9rem;font-size:.82rem;margin-top:.15rem}
.pv-head .tags span{color:var(--ofm-muted);text-shadow:1px 1px 0 var(--ofm-shadow)}
.pv-head .tags span.bad{color:#ff8a80;font-weight:700}
.pv-sheet{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));column-gap:1.7rem;margin:.25rem 0 .2rem;
  padding:.3rem .9rem .5rem;background:var(--ofm-sheet);border:1px solid #000}
.pv-sheet .g{background:var(--ofm-group);color:var(--ofm-group-text);font-size:.74rem;font-weight:700;
  padding:.06rem .45rem;margin:.4rem 0 .12rem;border:1px solid #000;text-shadow:1px 1px 0 var(--ofm-shadow)}
.pv-sheet .col>.g:first-child{margin-top:.12rem}
.pv-sheet .c{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem;padding:.05rem .1rem;
  font-size:.93rem;line-height:1.32;text-shadow:1px 1px 0 var(--ofm-shadow)}
.pv-sheet .k{color:var(--ofm-text);overflow-wrap:anywhere}
.pv-sheet .v{min-width:1.5rem;text-align:right;font-weight:700;color:var(--ofm-value);font-variant-numeric:tabular-nums;
  white-space:nowrap}
.pv-sheet .x .v{color:var(--ofm-status)}
.pv-sheet .q .v{color:var(--ofm-muted);font-weight:400}
.pv-sheet-note{font-size:.8rem;color:var(--ofm-muted);text-align:center;margin:.25rem 0 .5rem;line-height:1.35}
.pv-stats .cm-table td.l{min-width:7.5rem}
.pv-pos{text-align:center;color:var(--ofm-pos);font-size:1.12rem;margin:.35rem 0 .1rem;
  text-shadow:1px 1px 0 var(--ofm-shadow)}
.pv-curve{display:flex;flex-wrap:wrap;gap:2px;margin:.2rem 0 .5rem}
.pv-curve .seg{flex:1 1 5.4rem;text-align:center;padding:.25rem .3rem;font-size:.74rem;background:var(--ofm-tab);
  border:1px solid #000;color:var(--ofm-tab-text)}
.pv-curve .seg .y{display:block;font-size:.68rem;opacity:.85}
.pv-curve .seg.now{background:var(--ofm-tab-sel);color:var(--ofm-tab-on);font-weight:700;outline:1px solid var(--ofm-tab-text);
  outline-offset:-3px}
.pv-attrs{display:grid;grid-template-columns:7rem minmax(4rem,1fr) max-content;gap:.3rem .6rem;align-items:center;
  margin:.2rem 0 .5rem}
.pv-attr{display:contents}
.pv-attr .k{font-size:.8rem;color:var(--ofm-text)}
.pv-attr .track{position:relative;height:9px;background:var(--ofm-tab);border:1px solid #000;overflow:hidden}
.pv-attr .fill{position:absolute;top:0;bottom:0;background:var(--ofm-pos)}
.pv-attr .fill.fog{background:var(--ofm-status);opacity:.7}
.pv-attr .s{font-size:.82rem;color:var(--ofm-value);text-align:right;white-space:nowrap}
.pv-attr.top .k,.pv-attr.top .s{color:var(--ofm-bio);font-weight:700}
.st-key-pv_steps{gap:6px !important;margin-top:.45rem}
.st-key-pv_steps>div{flex:1 1 0;min-width:0}
@media (max-width:640px){
  .pv-sheet{grid-template-columns:repeat(2,minmax(0,1fr));column-gap:.9rem;padding:.3rem .5rem}
  .pv-sheet .c{font-size:.84rem}
  .pv-head .bio{font-size:1rem}
  .st-key-pv_bandrow{flex-wrap:wrap !important}
  .st-key-pv_bandrow>div:has(.ofm-band){flex:1 1 60%}
  .pv-attrs{grid-template-columns:5.5rem minmax(3rem,1fr) max-content}
}
</style>
"""


# ===========================================================================
# 1) VERI: gozlemci sisinden gecmis profil
# ===========================================================================

@dataclass(frozen=True)
class Estimate:
    """Ekrana giden tek bir tahmin: CM sozcugu + cubuk bandi. SAYI EKRANA GITMEZ. known=False: bilinmiyor ("?")."""
    low: int
    high: int
    exact: bool
    known: bool = True

    @property
    def text(self) -> str:
        """14G: yildiz yerine CM sozcugu ('İyi' / 'İyi – Çok iyi' / '?'); tek sis modeliyle ayni metin."""
        if not self.known:
            return cv.UNKNOWN_TEXT
        return cv.ability_text((self.low, self.high))

    @property
    def word(self) -> str:
        """Tek sozcuk ('İyi', 'Vasat'); sisli tahminde orta noktaya bakar."""
        if not self.known:
            return cv.UNKNOWN_TEXT
        return cv.ability_word((self.low + self.high) // 2)

    @property
    def mid(self) -> int:
        return (self.low + self.high) // 2 if self.known else 0


UNKNOWN_ESTIMATE = Estimate(0, 0, False, known=False)


def estimate_of(bounds: tuple[int, int] | None) -> Estimate:
    """Tek sis modelinin araligi (career_views.fog_rating) -> Estimate."""
    if bounds is None:
        return UNKNOWN_ESTIMATE
    low, high = int(min(bounds)), int(max(bounds))
    return Estimate(low, high, low == high)


@dataclass
class ProfileHeader:
    player_id: int
    name: str
    club: str
    position: str
    age: int
    nationality: str | None
    own: bool                                   # izleyen menajerin kulubunun oyuncusu mu
    in_academy: bool
    wonderkid: bool
    tags: list[tuple[str, bool]] = field(default_factory=list)      # (metin, uyari mi)
    club_id: int | None = None
    ability_text: str = ""                      # tek sis modeli: "Mevcut yetenek" sozcugu / araligi / "?"
    potential_text: str = ""
    knowledge: int = 0


@dataclass
class SeasonRow:
    season: int
    competition: str
    team_name: str
    appearances: int
    minutes: int
    goals: int
    assists: int
    yellow: int
    red: int
    injuries: int
    rating: float | None
    shots: int = 0
    on_target: int = 0
    saves: int = 0


@dataclass
class Profile:
    header: ProfileHeader
    overall: Estimate
    potential: Estimate
    attributes: dict[str, Estimate]
    fit: dict[str, Estimate]                    # mevki kodu -> ozellik uyumu (gozlemci notu)


def position_fit(position: Position, attributes: dict[str, Estimate]) -> dict[str, Estimate]:
    """
    'Özellikleri hangi mevkiye uyuyor' GOZLEMCI NOTU: ratings.POSITION_WEIGHTS agirliklariyla her mevki icin
    bir yildiz (sisli oyuncuda aralik). Motor bu skoru OKUMAZ; motorun kurali dogal mevki / degil ayrimidir
    (ekranda ayrica yazilir). Kaleci mevkisi yalnizca kaleciler icin anlamlidir, digerlerinde gizlenir.
    """
    fit: dict[str, Estimate] = {}
    for pos in Position:
        if pos is Position.GK and position is not Position.GK:
            continue
        if pos is not Position.GK and position is Position.GK:
            continue
        if not all(attributes[name].known for name, _label in ATTRIBUTE_LABELS):
            fit[pos.value] = UNKNOWN_ESTIMATE                  # sis: ozellikler bilinmiyorsa uygunluk da "?"
            continue
        lows = {name: attributes[name].low for name, _label in ATTRIBUTE_LABELS}
        highs = {name: attributes[name].high for name, _label in ATTRIBUTE_LABELS}
        exact = all(attributes[name].exact for name, _label in ATTRIBUTE_LABELS)
        fit[pos.value] = Estimate(
            low=ratings.compute_overall(pos, lows), high=ratings.compute_overall(pos, highs), exact=exact)
    return fit


def build_profile(cm, viewer: Team | None, player: Player, knowledge: int | None = None) -> Profile:
    """
    Tek oyuncunun sisten gecmis profili -- 14G TEK SIS MODELI (career_views.player_fog): yetenek, potansiyel, deger ve
    alti motor ozelligi izgarayla AYNI bilgi esiklerinden (%25 alti "?", %25-69 aralik, %70+ kesin). knowledge
    verilmezse izleyenin bilgisi okunur (kendi oyuncun 100, digerleri gozlem kaydi / ayni lig; 1-2 sorgu).
    viewer None ise (kulupsuz menajer) her sey bilinmez.
    """
    own = viewer is not None and player.team_id == viewer.id
    if knowledge is None:
        knowledge = 100 if own else (viewer_knowledge(cm, viewer, player) if viewer is not None else 0)
    fog = cv.player_fog(cm, viewer, player, knowledge)
    attrs = {name: estimate_of(cv.fog_rating(getattr(player, name), fog.knowledge, (player.id, name)))
             for name, _label in ATTRIBUTE_LABELS}
    overall = estimate_of(fog.ability)
    potential = estimate_of(fog.potential)

    club = player.team.name if player.team is not None else "Kulüpsüz"
    header = ProfileHeader(
        player_id=player.id, name=player.name, club=club, position=player.position.value, age=player.age,
        nationality=player.nationality, own=own, in_academy=bool(player.in_academy),
        wonderkid=overall.known and potential.known and development.is_wonderkid(player.age, overall.mid,
                                                                                 potential.mid),
        tags=profile_tags(cm, player, own), club_id=player.team_id, ability_text=fog.ability_text,
        potential_text=fog.potential_text, knowledge=fog.knowledge,
    )
    return Profile(header=header, overall=overall, potential=potential, attributes=attrs,
                   fit=position_fit(player.position, attrs))


def profile_tags(cm, player: Player, own: bool) -> list[tuple[str, bool]]:
    """
    Herkesin gorebilecegi durum rozetleri: (metin, uyari mi). Uyari (kirmizi) = oynayamaz ya da satin alinamaz
    (sakat / cezali / transfer yasagi); digerleri notr bilgi (akademi, kiralik, listede, maas talebi).
    """
    tags: list[tuple[str, bool]] = []
    reason = player.unavailability_reason(cm.current_week)
    if reason:
        tags.append((reason, True))
    elif player.cup_suspended_matches:
        tags.append((f"kupada cezalı, {player.cup_suspended_matches} maç", True))
    if player.in_academy:
        tags.append(("U-21 akademisi", False))
    if player.loan_from_team_id is not None:
        tags.append(("kiralık oynuyor", False))
    if player.transfer_listed:
        tags.append(("satış listesinde", False))
    if player.loan_listed:
        tags.append(("kiralık listesinde", False))
    banned, ban_reason = cm.transfer_ban_info(player)
    if banned:
        tags.append((ban_reason, True))
    if own and player.wage_demand:
        tags.append(("yeni sözleşme istiyor", False))
    return tags


# ===========================================================================
# 2) VERI: veritabanindan toplu okunan gecmis (sorgular burada, hepsi toplu)
# ===========================================================================

def season_rows(db, player_id: int) -> list[SeasonRow]:
    """
    Sezon x kupa/lig x kulup kirilimli kariyer tablosu. TEK GROUP BY sorgusu + kulup adlari icin TEK IN sorgusu.
    (Satir basina sorgu yoktur; oyuncunun butun maclari bir kerede ozetlenir.)
    """
    stmt = (
        select(
            Fixture.season.label("season"),
            Fixture.competition.label("competition"),
            PlayerMatchStat.team_id.label("team_id"),
            func.count(PlayerMatchStat.id).label("apps"),
            func.coalesce(func.sum(PlayerMatchStat.minutes), 0).label("minutes"),
            func.coalesce(func.sum(PlayerMatchStat.goals), 0).label("goals"),
            func.coalesce(func.sum(PlayerMatchStat.assists), 0).label("assists"),
            func.coalesce(func.sum(PlayerMatchStat.yellow_cards), 0).label("yellow"),
            func.coalesce(func.sum(PlayerMatchStat.shots), 0).label("shots"),
            func.coalesce(func.sum(PlayerMatchStat.shots_on_target), 0).label("on_target"),
            func.coalesce(func.sum(PlayerMatchStat.saves), 0).label("saves"),
            func.count(PlayerMatchStat.id).filter(PlayerMatchStat.red_card.is_(True)).label("red"),
            func.count(PlayerMatchStat.id).filter(PlayerMatchStat.injured.is_(True)).label("injuries"),
            func.avg(PlayerMatchStat.rating).label("rating"),
        )
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .where(PlayerMatchStat.player_id == player_id)
        .group_by(Fixture.season, Fixture.competition, PlayerMatchStat.team_id)
        .order_by(Fixture.season.desc(), Fixture.competition, PlayerMatchStat.team_id)
    )
    records = db.execute(stmt).all()
    if not records:
        return []
    names = team_names(db, {r.team_id for r in records})
    return [
        SeasonRow(
            season=int(r.season),
            competition=COMPETITION_LABELS.get(getattr(r.competition, "value", r.competition),
                                               str(getattr(r.competition, "value", r.competition))),
            team_name=names.get(r.team_id, "—"),
            appearances=int(r.apps), minutes=int(r.minutes), goals=int(r.goals), assists=int(r.assists),
            yellow=int(r.yellow), red=int(r.red), injuries=int(r.injuries),
            rating=round(float(r.rating), 2) if r.rating is not None else None,
            shots=int(r.shots), on_target=int(r.on_target), saves=int(r.saves),
        )
        for r in records
    ]


def team_names(db, team_ids) -> dict[int, str]:
    """Kulup adlari TEK sorguda (satir basina Team nesnesi yuklenmez)."""
    ids = {int(i) for i in team_ids if i is not None}
    if not ids:
        return {}
    return {int(tid): name for tid, name in db.execute(select(Team.id, Team.name).where(Team.id.in_(ids))).all()}


def recent_rows(db, player_id: int, limit: int = RECENT_MATCHES) -> list[dict]:
    """Son maclar: istatistik + fikstur + iki kulup adi TEK sorguda (takma adli JOIN; iliski gezilmez)."""
    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(PlayerMatchStat, Fixture, home.name.label("home_name"), away.name.label("away_name"))
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .join(home, home.id == Fixture.home_team_id)
        .join(away, away.id == Fixture.away_team_id)
        .where(PlayerMatchStat.player_id == player_id)
        .order_by(Fixture.season.desc(), Fixture.week.desc(), Fixture.id.desc())
        .limit(limit)
    )
    rows: list[dict] = []
    for stat, fx, home_name, away_name in db.execute(stmt).all():
        at_home = stat.team_id == fx.home_team_id
        opponent = away_name if at_home else home_name
        score = f"{fx.home_score}-{fx.away_score}" if fx.is_played else "—"
        yellow = int(stat.yellow_cards or 0)
        cards = "Kırmızı" if stat.red_card else ("Sarı" if yellow == 1 else f"{yellow} sarı" if yellow else "")
        rows.append({
            "Sezon": fx.season,
            "Hf": fx.week,
            "Kupa": COMPETITION_LABELS.get(fx.competition.value, fx.competition.value),
            "Rakip": ("" if at_home else "@ ") + opponent,
            "Skor": score,
            "Dk": int(stat.minutes or 0),
            "G": int(stat.goals or 0),
            "A": int(stat.assists or 0),
            "Not": round(float(stat.rating), 1) if stat.rating is not None else None,
            "Kart": cards or "",
            "Durum": "sakatlandı" if stat.injured else "",
        })
    return rows


def transfer_rows(db, player_id: int, player_name: str) -> list[dict]:
    """TransferLog gecmisi TEK sorguda (id silinmisse ada gore de eslesir)."""
    stmt = (
        select(TransferLog)
        .where(or_(TransferLog.player_id == player_id,
                   and_(TransferLog.player_id.is_(None), TransferLog.player_name == player_name)))
        .order_by(TransferLog.season, TransferLog.week, TransferLog.id)
    )
    return [
        {"Sezon": log.season, "Hafta": log.week, "Nereden": log.from_team_name or "Kulüpsüz",
         "Nereye": log.to_team_name, "Bonservis": format_money(log.fee),
         "Maaş": f"{format_money(log.wage)}/hf" if log.wage else "—",
         "Tür": "Kiralık" if log.kind == "LOAN" else "Transfer"}
        for log in db.scalars(stmt)
    ]


def league_peers(db, player: Player) -> list[tuple[Player, str]]:
    """
    Oyuncunun liginde ayni mevkideki oyuncular: oyuncu satiri + kulup adi TEK sorguda (N+1 yok).
    Kulupsuz ya da ligi olmayan oyuncuda bos liste.
    """
    club = player.team
    if club is None or club.league_id is None:
        return []
    stmt = (
        select(Player, Team.name)
        .join(Team, Team.id == Player.team_id)
        .where(Team.league_id == club.league_id, Player.position == player.position,
               Player.in_academy.is_(False))
    )
    return [(p, name) for p, name in db.execute(stmt).all()]


def loan_note(db, cm, player: Player) -> str | None:
    """Aktif kiralamanin ozeti (ana kulup, maas payi, kalan hafta). Kiralik degilse None."""
    if player.loan_from_team_id is None and not player.loan_id:
        return None
    parent = db.get(Team, player.loan_from_team_id) if player.loan_from_team_id else None
    parts = [f"Ana kulüp: {parent.name}" if parent is not None else "Kiralık"]
    if player.loan_wage_share is not None:
        parts.append(f"maaşın %{int(player.loan_wage_share)}'ini kiralayan kulüp ödüyor")
    loan = db.get(Loan, int(player.loan_id)) if player.loan_id else None
    if loan is not None and loan.status == LoanStatus.ACTIVE.value and loan.end_career_week is not None:
        remaining = int(loan.end_career_week) - cm.career_week
        parts.append(f"{max(0, remaining)} hafta kaldı")
    return " · ".join(parts)


# ===========================================================================
# 3) HTML PARCALARI (yalnizca tema degiskenleri; yeni renk tanimlanmaz)
# ===========================================================================

STAR_CELL_PCT = 10                      # yarim yildizin cubuktaki genisligi (%)


def bar_pct(value: int) -> int:
    """
    Cubuk genisligi YILDIZDAN turetilir (0.5 yildiz = %10 ... 5 yildiz = %100), motorun 1-99 sayisindan degil.
    Boylece cubuk yildizin gosterdiginden bir gram fazla bilgi tasimaz ve motor degeri sayfanin kaynagina da
    sizmaz (K12). Gorsel fark yok: olcek zaten yildizdir.
    """
    star = star_value(value)
    return int(round((star or 0.5) * 20))


def attribute_html(items: list[tuple[str, Estimate]], highlight: set[str] | None = None) -> str:
    """Ozellik cubuklari: kesin degerde dolu cubuk, sisli tahminde alt-ust arasi 'sis bandi'; yazi olarak yalnizca
    sozcuk (14S: profilde yildiz yok)."""
    highlight = highlight or set()
    rows = []
    for label, est in items:
        low, high = bar_pct(min(est.low, est.high)), bar_pct(max(est.low, est.high))
        if est.exact:
            bar = f'<span class="fill" style="left:0;width:{low}%"></span>'
        else:
            # Sis bandi araligin kapsadigi yarim yildiz hucrelerini ORTER: [alt hucrenin basi, ust hucrenin sonu].
            # (Iki uc ayni yildizdaysa tek hucre; 5 yildizda bant sagdan tasmaz.)
            start = max(0, low - STAR_CELL_PCT)
            bar = f'<span class="fill fog" style="left:{start}%;width:{high - start}%"></span>'
        cls = "pv-attr top" if label in highlight else "pv-attr"
        rows.append(f'<div class="{cls}"><div class="k">{escape(label)}</div>'
                    f'<div class="track">{bar}</div>'
                    f'<div class="s">{escape(est.word)}</div></div>')
    return f'<div class="pv-attrs">{"".join(rows)}</div>'


def age_curve_html(age: int) -> str:
    segments = []
    for low, high, label in AGE_BANDS:
        now = low <= age <= high
        span = f"{low}-{high}" if high < 99 else f"{low}+"
        segments.append(f'<div class="seg{" now" if now else ""}">{escape(label)}'
                        f'<span class="y">{escape(span)} yaş</span></div>')
    return f'<div class="pv-curve">{"".join(segments)}</div>'


# ===========================================================================
# 4) CALLBACK'LER (yalnizca oturum durumu; veritabanina yazmaz -> requires_auth)
# ===========================================================================

@requires_auth
def cb_pv_open(area: str, player_id: int | None = None) -> None:
    """Profili acar. player_id verilmezse alanin kendi secicisinden (pv_pick_{alan}) okunur."""
    if player_id is None:
        player_id = st.session_state.get(pick_key(area))
    if player_id is None:
        return
    open_profile(area, int(player_id))


@requires_auth
def cb_pv_close() -> None:
    """Geri: profil ekrani kapanir, listeye donulur. Satirla secilen hedef (Transfer Merkezi'nde teklif hedefi, takip
    listesinde secili oyuncu) geri yazilir: profil acikken o secici cizilmedigi icin Streamlit durumunu atmisti."""
    target = st.session_state.pop(TARGET_KEY, None)
    reset_widgets(PROFILE_KEY, LIST_KEY)
    if isinstance(target, tuple) and len(target) == 2 and isinstance(target[0], str):
        st.session_state[target[0]] = target[1]


# ===========================================================================
# 5) GIRIS NOKTALARI (tek mekanizma: secici + "🔎 İncele")
# ===========================================================================

def pick_key(area: str) -> str:
    return f"pv_pick_{area}"


def open_profile(area: str, player_id: int, ids=None) -> None:
    """
    Profili acar (callback'ler, taktik tahtasi ve testler): panel bir sonraki cizimde gorunur alana kayar. ids: Geri /
    Ileri listesi (verilmezse alanin tablosunun oyuncu listesi; oyuncu listede yoksa tek oyuncu).
    """
    ss = st.session_state
    pid = int(player_id)
    if ids is None:
        table = AREA_TABLES.get(area)
        ids = ss.get(table_ids_key(table)) if table else None
    ids = [int(i) for i in ids or ()]
    ss[PROFILE_KEY] = (area, pid)
    ss[LIST_KEY] = ids if pid in ids else [pid]
    ss[SCROLL_KEY] = int(ss.get(SCROLL_KEY) or 0) + 1
    ss[FULL_RERUN_KEY] = True                   # kadro parcasindan acildiysa tum sayfa (profil ekrani) cizilsin


def _scroll_script(nonce: int) -> str:
    """Paneli gorunur alana kaydirir (sabit metin + sayi; veri icermez). Nonce: ayni betik tekrar calissin."""
    return (f"<script>/*{int(nonce)}*/(function(){{try{{requestAnimationFrame(function(){{"
            "var p=document.querySelector('.st-key-pv_panel');"
            "if(p&&p.scrollIntoView){p.scrollIntoView({behavior:'smooth',block:'start'});}"
            "});}catch(e){}})()</script>")


def opened(area: str) -> int | None:
    """Bu alanda acik profilin oyuncu id'si; acik degilse None."""
    value = st.session_state.get(PROFILE_KEY)
    if not isinstance(value, tuple) or len(value) != 2 or value[0] != area:
        return None
    return int(value[1])


def inspect_button(area: str, player_id: int | None, *, key: str | None = None, label: str = INSPECT_LABEL,
                   container=None, disabled: bool = False, width: str = "stretch", help: str | None = None) -> None:
    """Tek satirlik '🔎 İncele' dugmesi (secicisi zaten olan alanlar ve satir kartlari icin)."""
    target = container if container is not None else st
    target.button(label, key=key or f"pv_btn_{area}", on_click=cb_pv_open, args=(area, player_id),
                  disabled=disabled or player_id is None, width=width,
                  help=help or "Oyuncunun profilini aç: özellikler, sözleşme, istatistik, karşılaştırma.")


def picker(area: str, options: dict[int, str], *, label: str = "Oyuncu", ratio=(3, 1)) -> None:
    """
    Kendi secicisi olmayan alanlar icin: secilebilir oyuncu kutusu + '🔎 İncele'. Satir basina dugme
    uretmez (telefonda da tek satir kalir, her cizimde onlarca widget olusmaz).
    """
    if not options:
        return
    key = pick_key(area)
    if st.session_state.get(key) not in options:
        reset_widgets(key)
    pick, go = st.columns(list(ratio))
    pick.selectbox(label, list(options), key=key, format_func=lambda i: options.get(i, str(i)),
                   label_visibility="collapsed")
    inspect_button(area, st.session_state.get(key, next(iter(options))), key=f"pv_btn_{area}", container=go)


EMPTY_SELECTION = {"selection": {"rows": [], "columns": [], "cells": []}}
ROW_HEIGHT = 28                         # 14S: CM yogunlugu (kadro listeleri)
MAX_ROWS_SHOWN = 32


def table_height(rows: int) -> int:
    """Yogun listenin yuksekligi: tum satirlar (en fazla MAX_ROWS_SHOWN) kaydirmasiz gorunsun."""
    return ROW_HEIGHT * (min(max(int(rows), 1), MAX_ROWS_SHOWN) + 1) + 3
ROW_HINT = "Satıra tıkla: oyuncunun profili açılır."


def table_ids_key(key: str) -> str:
    return f"{key}__ids"


def _selected_rows(state) -> list:
    """
    st.dataframe secim durumu (DataframeState ya da duz sozluk) -> satir sira numaralari. Satir secimi (sol kutu) ya da
    hucre secimi (satirin herhangi bir hucresine tek tik: [satir, sutun]) ayni satiri verir.
    """
    if not isinstance(state, dict):
        return []
    selection = state.get("selection")
    if not isinstance(selection, dict):
        return []
    rows = selection.get("rows")
    if isinstance(rows, list | tuple) and rows:
        return list(rows)
    cells = selection.get("cells")
    if isinstance(cells, list | tuple):
        return [cell[0] for cell in cells if isinstance(cell, list | tuple) and cell]
    return []


def row_player(key: str, state=None) -> int | None:
    """Tablonun secili satirinin oyuncu id'si (sunucudaki esleme ile); gecersiz / aralik disi -> None."""
    rows = _selected_rows(st.session_state.get(key) if state is None else state)
    ids = st.session_state.get(table_ids_key(key)) or []
    if not rows:
        return None
    index = rows[0]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(ids):
        return None
    return int(ids[index])


@requires_auth
def cb_pv_row(area: str, key: str, target_key: str | None = None) -> None:
    """
    Satira tiklandi: o satirin oyuncusunun profili acilir (target_key verilirse o secici de ayni oyuncuya gecer:
    Transfer Pazari'nda hedef oyuncu, takip listesinde secili oyuncu). Secim temizlenir: vurgu kalmaz, ayni satir
    yeniden tiklanabilir. Oturum durumu disinda hicbir sey yazilmaz.
    """
    club = club_cell(key)
    if club is not None:                                     # 14F: kulup hucresine tik -> kulup sayfasi
        import nav_view

        st.session_state[key] = {"selection": {"rows": [], "columns": [], "cells": []}}
        nav_view.goto(nav_view.CLUB_PAGE, param=club)
        return
    player_id = row_player(key)
    if player_id is not None:
        open_profile(area, player_id, ids=st.session_state.get(table_ids_key(key)))
        if target_key:
            st.session_state[target_key] = player_id
            st.session_state[TARGET_KEY] = (target_key, player_id)
    st.session_state[key] = {"selection": {"rows": [], "columns": [], "cells": []}}


def club_cell(key: str, state=None) -> int | None:
    """Secili hucre bir kulup sutunundaysa (selectable_table(clubs=...)) o satirin kulup id'si; yoksa None."""
    state = st.session_state.get(key) if state is None else state
    clubs = st.session_state.get(f"{key}__clubs")
    if not isinstance(state, dict) or not isinstance(clubs, dict):
        return None
    cells = (state.get("selection") or {}).get("cells") if isinstance(state.get("selection"), dict) else None
    if not isinstance(cells, list | tuple) or not cells:
        return None
    cell = cells[0]
    if not isinstance(cell, list | tuple) or len(cell) != 2:
        return None
    row, col = cell
    ids = clubs.get(str(col))
    if isinstance(row, bool) or not isinstance(row, int) or not isinstance(ids, list) or not 0 <= row < len(ids):
        return None
    return int(ids[row]) if ids[row] is not None else None


def selectable_table(area: str, frame: pd.DataFrame, ids: Sequence[int], *, key: str,
                     target_key: str | None = None, hint: bool = True, clubs=None, **kwargs) -> None:
    """
    Satirina tek tikla profil acan oyuncu tablosu. ids: frame satirlariyla AYNI sirada oyuncu id'leri. Satir secimi
    istemcide siralama yapilsa da ozgun satir sirasini dondurur (Streamlit), esleme bu yuzden cizim sirasina gore.
    14F: clubs={"Kulüp": [kulup id...]} verilirse o sutundaki HUCREYE tik kulup sayfasini acar (satirin geri kalani
    profil).
    """
    st.session_state[table_ids_key(key)] = [int(i) for i in ids]
    if clubs:
        st.session_state[f"{key}__clubs"] = {str(col): [int(v) if v is not None else None for v in values]
                                             for col, values in clubs.items()}
    else:
        st.session_state.pop(f"{key}__clubs", None)
    options = {"hide_index": True, "width": "stretch", **kwargs}
    st.dataframe(frame, key=key, on_select=functools.partial(cb_pv_row, area, key, target_key),
                 selection_mode=["single-row", "single-cell"], **options)
    if hint and len(ids):
        st.caption(ROW_HINT + (" Kulüp adına tıkla: kulüp sayfası." if clubs else ""))


def option_label(name: str, position: str, extra: str = "") -> str:
    """Secici etiketi: ad · mevki · ek bilgi (yildiz / kulup). Duz metin (Streamlit kendi kacisini yapar)."""
    parts = [name, position]
    if extra:
        parts.append(extra)
    return " · ".join(parts)


# ===========================================================================
# 6) PANEL (Faz 13I: Championship Manager 01/02 duzeni)
# ===========================================================================

AREA_TABLES: dict[str, str] = {AREA_SQUAD: "sq_table", AREA_ACADEMY: "acad_table", AREA_MARKET: "mkt_table",
                               AREA_SHORTLIST: "sl_table", AREA_NATIONAL: "nt_table", AREA_CLUBS: "nc_squad",
                               AREA_FIND: "fd_players"}
# 1-20 renk bantlari (yalniz tema tokenlari: soluk / metin / vurgu / birincil zemin)
BANDS: tuple[tuple[int, int], ...] = ((16, 4), (11, 3), (6, 2), (1, 1))
TR_ALPHABET = "abcçdefgğhıijklmnoöprsştuüvyz"
KNOWN_FOOT = 25                          # tercih ettigi ayak: bilgi en az bu kadarsa gorunur (gozlem esigi)
UNKNOWN_CELL = "?"


@dataclass(frozen=True)
class SheetCell:
    key: str
    label: str
    text: str                            # "15" / "12-15" / "?" (cm_attributes.attribute_display)
    extra: bool = False                  # ayak / form / moral / kondisyon: 1-20 degil, renk bandi yok

    @property
    def band(self) -> int:
        """Renk bandi EKRANDAKI metinden (araligin orta noktasi): gercek deger renkle sizmaz."""
        if self.extra:
            return 0
        numbers = [int(n) for n in re.findall(r"\d+", self.text)][:2]
        if not numbers:
            return 0
        mid = sum(numbers) / len(numbers)
        return next(band for low, band in BANDS if mid >= low)


def tr_sort_key(text: str) -> tuple:
    """Turkce alfabetik sira (Ç, Ğ, İ, Ö, Ş, Ü dogru yerde)."""
    lowered = str(text).replace("I", "ı").replace("İ", "i").lower()
    return tuple(TR_ALPHABET.index(ch) if ch in TR_ALPHABET else 100 + ord(ch) for ch in lowered)


def _world_seed() -> int | None:
    ctx = page_world()
    return ctx.world_seed if ctx is not None else None


def viewer_knowledge(cm, team: Team | None, player: Player) -> int:
    """Izleyen kulubun oyuncu hakkindaki bilgisi (%): kendi oyuncusu (akademi dahil) 100; digerleri transfer masasinin
    gozlem kurali (ayni lig en az %35, gozlemci gorevi); kulupsuz izleyici 0."""
    if team is None:
        return 0
    if player.team_id == team.id:
        return 100
    return int(TransferDesk(cm).knowledge_of(team, player))


def attribute_sheet(player: Player, knowledge: int) -> dict[str, SheetCell]:
    """31 CM ozelligi (anahtar -> hucre); gorunurluk yalnizca cm_attributes.attribute_display'de (tek yer)."""
    values = cm_attributes.player_attributes(player, world_seed=_world_seed())
    return {key: SheetCell(key, cm_attributes.ATTRIBUTE_LABELS[key],
                           cm_attributes.attribute_display(int(values[key]), float(knowledge), f"{player.id}:{key}"))
            for key in cm_attributes.ATTRIBUTE_KEYS}


MOOD_WORDS: tuple[tuple[int, str], ...] = cv.MOOD_WORDS          # tek kaynak: career_views
mood_word = cv.mood_word


def extra_cells(player: Player, own: bool, knowledge: int) -> list[SheetCell]:
    """
    Turuncu durum blogu: tercih ettigi ayak (gozlemle bilinir), form, moral (sozcukle), kondisyon %. Form / moral /
    kondisyon YALNIZCA kendi oyuncunda (K12): baska kulubun oyuncusunda blokta yalnizca ayak kalir.
    """
    foot = (cm_attributes.preferred_foot(player, world_seed=_world_seed()) if own or knowledge >= KNOWN_FOOT
            else UNKNOWN_CELL)
    cells = [SheetCell("foot", "Tercih ettiği ayak", foot, extra=True)]
    if own:
        condition = int(getattr(player, "condition", 100))
        cells += [SheetCell("form", "Form", mood_word(player.form), extra=True),
                  SheetCell("morale", "Moral", mood_word(player.morale), extra=True),
                  SheetCell("condition", "Kondisyon", f"%{condition}", extra=True)]
    return cells


SHEET_COLUMNS = 3
STATE_GROUP_LABEL = "Durum"
SheetColumn = list[tuple[str, list[SheetCell]]]          # (grup basligi, hucreler) listesi


def sheet_columns(cells: dict[str, SheetCell], extras: list[SheetCell]) -> list[SheetColumn]:
    """
    Sahip karari 2: FM gibi GRUPLU, uc sutun -- cm_attributes.ATTRIBUTE_GROUPS sirasiyla (Teknik | Zihinsel |
    Fiziksel + Kalecilik); son sutunun altinda turuncu durum blogu. Grup icinde Turkce alfabetik sira.
    """
    groups = [(label, sorted((cells[k] for k in keys if k in cells), key=lambda c: tr_sort_key(c.label)))
              for _key, label, keys in cm_attributes.ATTRIBUTE_GROUPS]
    columns: list[SheetColumn] = [[group] for group in groups[:SHEET_COLUMNS - 1]]
    columns.append([*groups[SHEET_COLUMNS - 1:], (STATE_GROUP_LABEL, list(extras))])
    return columns


def _cell_html(c: SheetCell) -> str:
    """CM hucresi: beyaz etiket, saga yasli kalin beyaz sayi (rozet / renkli kutu yok); durum satiri turuncu."""
    cls = "c x" if c.extra else ("c q" if c.text == UNKNOWN_CELL or "-" in c.text else "c")
    return f'<div class="{cls}"><span class="k">{escape(c.label)}</span><span class="v">{escape(c.text)}</span></div>'


def sheet_html(columns: list[SheetColumn]) -> str:
    """Uc sutunlu yogun izgara (gri-mavi grup seritleri; telefonda iki sutun). Tum metinler kacisli."""
    body = "".join(
        '<div class="col">' + "".join(f'<div class="g">{escape(label)}</div>' + "".join(map(_cell_html, cells))
                                      for label, cells in column if cells) + "</div>"
        for column in columns)
    return f'<div class="pv-sheet">{body}</div>'


def bio_text(player: Player) -> str:
    """
    CM biyografi satiri: "26 yaşında · Türkiye · 14 A milli maç, 3 gol". Uyruk yoksa yazilmaz; milli maci yoksa milli
    bolum yazilmaz. Dogum tarihi YAZILMAZ: oyunda takvim yili yok, hesaplanan bir tarih uydurma olurdu.
    """
    parts = [f"{player.age} yaşında"]
    if player.nationality:
        parts.append(str(player.nationality))
    caps = int(player.international_caps or 0)
    if caps:
        parts.append(f"{caps} A milli maç, {int(player.international_goals or 0)} gol")
    return " · ".join(parts)


def header_html(header: ProfileHeader, bio: str = "", knowledge: int | None = None) -> str:
    """Biyografi satiri (sari, ortali) + durum notlari (sakat / cezali kirmizi). Tum metinler kacisli."""
    tags = [(text, warn) for text, warn in header.tags]
    if header.own:
        tags.insert(0, ("kendi oyuncun", False))
    else:
        known = f" · bilgi %{int(knowledge)}" if knowledge is not None else ""
        tags.insert(0, (f"gözlemci raporu{known}", False))
    if header.ability_text:                                  # 14G: tek sis modeli (izgarayla ayni esikler)
        tags.insert(1, (f"{cv.ABILITY_LABEL}: {header.ability_text}", False))
        tags.insert(2, (f"{cv.POTENTIAL_LABEL}: {header.potential_text or cv.UNKNOWN_TEXT}", False))
    if header.wonderkid:
        tags.append(("geleceğin yıldızı (gözlemci)", False))
    spans = "".join(f'<span class="{"bad" if warn else ""}">{escape(text)}</span>' for text, warn in tags)
    bio_line = bio or f"{header.age} yaşında"
    return (f'<div class="pv-head"><div class="bio">{escape(bio_line)}</div>'
            f'<div class="tags">{spans}</div></div>')


def _list_ids() -> list[int]:
    ids = st.session_state.get(LIST_KEY) or []
    return [int(i) for i in ids if isinstance(i, int) and not isinstance(i, bool)]


@requires_auth
def cb_pv_step(delta: int) -> None:
    """◄ ►: profilin acildigi listede onceki / sonraki oyuncu (yalnizca oturum durumu)."""
    value = st.session_state.get(PROFILE_KEY)
    ids = _list_ids()
    if not isinstance(value, tuple) or len(value) != 2 or int(value[1]) not in ids:
        return
    index = ids.index(int(value[1])) + int(delta)
    if 0 <= index < len(ids):
        open_profile(value[0], ids[index], ids=ids)


@requires_auth
def cb_pv_page_step(delta: int) -> None:
    """Parametreli oyuncu sayfasinda ◄ ►: listede onceki / sonraki oyuncu (gecmise yeni kayit eklemez)."""
    import nav_view

    ids = _list_ids()
    current = nav_view.current_param()
    if not isinstance(current, int) or current not in ids:
        return
    index = ids.index(current) + int(delta)
    if 0 <= index < len(ids):
        nav_view.replace_param(ids[index])


def open_area() -> str | None:
    """Acik profilin alani (squad / market / ...); acik degilse None."""
    value = st.session_state.get(PROFILE_KEY)
    if isinstance(value, tuple) and len(value) == 2 and value[0] in AREAS:
        return str(value[0])
    return None


def profile_panel(db, cm, team: Team | None, area: str) -> None:
    """
    Eski (13E-13I) yerinde panel. 14S'ten beri profil SAYFA DUZEYINDE bir CM ekranidir (profile_screen, web_app
    render_page); bu cagri geriye uyumluluk icin durur ve hicbir sey cizmez (sorgu da acmaz).
    """
    return None


def profile_screen(db, cm, team: Team | None, area: str) -> None:
    """
    CM 01/02 oyuncu ekrani (sahibin onayladigi ornek, ref/cm_profil_ornek.html): kulup renginde bant "Ad (Kulup)" +
    ◄ ► (listede onceki / sonraki) + beyaz Eylem kutusu -> sekmeler -> sari biyografi -> gruplu ozellikler + turuncu
    durum -> istatistik matrisi (mor Ort. not) -> acik mavi mevki satiri -> Geri / Ileri. Yildiz ve emoji yok.
    team: izleyen menajerin kulubu (sis kurallarinin referansi).
    """
    player_id = opened(area)
    if player_id is None:
        return
    player = db.get(Player, player_id)
    if player is None:
        reset_widgets(PROFILE_KEY)
        st.info("Oyuncu artık bu dünyada değil (transfer edilmiş ya da silinmiş olabilir).")
        return
    _screen(db, cm, team, player, area)


NOT_FOUND_TEXT = "Oyuncu bulunamadı: bu dünyada yok ya da bilgisine erişemiyorsun."


def profile_page(db, cm, team: Team | None, player_id) -> None:
    """
    14F: parametreli oyuncu sayfasi (?sayfa=oyuncu&id=..; nav gecmisine girer, Geri / Ileri sayfa altindan). Ayni CM
    ekrani ve ayni sis kurallari (viewer_knowledge, attribute_sheet, K12). Gecersiz / silinmis id ya da BASKA kulubun
    akademi oyuncusu (gizli): "bulunamadi" + Bul'a baglanti, istisna yok.
    """
    import nav_view

    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        pid = None
    player = db.get(Player, pid) if pid else None
    hidden = player is not None and player.in_academy and (team is None or player.team_id != team.id)
    if player is None or hidden:
        st.markdown(nav_view.band_html("Oyuncu"), unsafe_allow_html=True)
        st.markdown(f'<div class="cm-empty">{escape(NOT_FOUND_TEXT)}</div>', unsafe_allow_html=True)
        st.button("Bul", key="pv_nf_find", on_click=nav_view.cb_nav, args=(nav_view.FIND, nav_view.SEC_FIND))
        return
    _screen(db, cm, team, player, AREA_PAGE)


def _screen(db, cm, team: Team | None, player: Player, area: str) -> None:
    import nav_view

    knowledge = viewer_knowledge(cm, team, player)
    profile = build_profile(cm, team, player, knowledge)
    colors = nav_view.club_band_colors(player.team.name) if player.team is not None else None
    with st.container(key=PANEL_KEY):
        _band_row(db, cm, team, player, profile, knowledge, colors, area)
        if st.session_state.get(SECTION_KEY) not in SECTIONS:
            reset_widgets(SECTION_KEY)
        st.segmented_control("Sekme", list(SECTIONS), key=SECTION_KEY, required=True, default=SECTIONS[0],
                             label_visibility="collapsed", width="stretch")
        st.markdown(header_html(profile.header, bio_text(player), None if profile.header.own else knowledge),
                    unsafe_allow_html=True)
        section = st.session_state.get(SECTION_KEY) or SECTIONS[0]
        if section == SEC_PROFILE:
            _profile_section(db, cm, team, player, profile, knowledge)
        elif section == SEC_INJURY:
            _injury_section(db, cm, team, player, profile, knowledge)
        elif section == SEC_CONTRACT:
            _contract_section(db, cm, team, player, profile, knowledge)
        elif section == SEC_TRANSFER:
            _transfer_section(db, cm, team, player, profile)
        else:
            _stats_section(db, player)
        if area != AREA_PAGE:                       # parametreli sayfa: Geri / Ileri web_app'in sayfa altinda (gecmis)
            _bottom_bar()


def _band_row(db, cm, team: Team | None, player: Player, profile: Profile, knowledge: int, colors,
              area: str = AREA_SQUAD) -> None:
    """Bant satiri: ◄ ► (listede onceki / sonraki oyuncu), "Ad (Kulup)" bandi, beyaz Eylem kutusu."""
    import nav_view

    ids = _list_ids()
    index = ids.index(player.id) if player.id in ids else -1
    bg = colors[0] if colors else nav_view.BAND_DEFAULT[0]
    position = f" ({index + 1} / {len(ids)})" if index >= 0 else ""
    step = cb_pv_page_step if area == AREA_PAGE else cb_pv_step
    with st.container(horizontal=True, key="pv_bandrow", gap=None):
        with st.container(horizontal=True, width="content", key="pv_arrows", gap=None):
            st.html(f"<style>.st-key-pv_arrows{{--band-bg:{bg}}}</style>")
            st.button("◄", key="pv_prev", on_click=step, args=(-1,), disabled=index <= 0,
                      help="Listede önceki oyuncu" + position)
            st.button("►", key="pv_next", on_click=step, args=(1,),
                      disabled=index < 0 or index >= len(ids) - 1, help="Listede sonraki oyuncu" + position)
        club = player.team.name if player.team is not None else "Kulüpsüz"
        st.markdown(nav_view.band_html(f"{player.name} ({club})", colors), unsafe_allow_html=True)
        with st.popover("Eylem", key="pv_actions", width="content"):
            _links(player, area)
            _actions(db, cm, team, player, profile, knowledge)


def _links(player: Player, area: str) -> None:
    """Eylem menusunun ust kismi (CM: kulup / ulke adina tik): kulup sayfasi, ulke sayfasi, tam sayfa (yerinde
    acilan profilde; parametreli sayfa adrese ve Geri / Ileri gecmisine girer)."""
    import nav_view

    if player.team_id is not None:
        st.button("Kulüp sayfası", key="pv_go_club", on_click=nav_view.cb_open_club, args=(int(player.team_id),),
                  width="stretch", help="Kulübün kadrosu, fikstürü, bilgisi ve tarihi.")
    if player.nationality:
        st.button("Ülke sayfası", key="pv_go_nation", on_click=nav_view.cb_open_nation,
                  args=(str(player.nationality),), width="stretch")
    if area != AREA_PAGE:
        st.button("Tam sayfa", key="pv_go_page", on_click=nav_view.cb_open_player, args=(int(player.id), _list_ids()),
                  width="stretch", help="Oyuncu ekranı adrese ve Geri / İleri geçmişine girer.")


def _bottom_bar() -> None:
    """CM iskeleti: altta genis, kabartmali Geri (listeye don) / Ileri (ileri gecmis yok: pasif, ornekteki gibi)."""
    with st.container(horizontal=True, key="pv_steps"):
        st.button("Geri", key=CLOSE_KEY, on_click=cb_pv_close, width="stretch", help="Listeye dön")
        st.button("İleri", key="pv_forward", disabled=True, width="stretch")


def _actions(db, cm, team: Team | None, player: Player, profile: Profile, knowledge: int) -> None:
    import contracts as crules

    pid = player.id
    if team is None:
        st.caption("Eylem için bir kulübün olmalı.")
        return
    cycle = cv.contract_cycle_on(cm)
    if profile.header.own:
        st.button("Satış listesinden çıkar" if player.transfer_listed else "Satış listesine koy",
                  key="pv_act_list", on_click=cb_pv_action, args=("list", pid), width="stretch",
                  disabled=player.in_academy or player.loan_from_team_id is not None)
        st.button("İstenen fiyat", key="pv_act_ask", on_click=cb_pv_action, args=("ask", pid), width="stretch",
                  help="Transfer Merkezi › Oyuncularım: yapay zekâ tekliflerinin tabanı.")
        if cycle and player.loan_from_team_id is None:
            st.button("Sözleşmeyi yenile", key="pv_act_contract", on_click=cb_pv_action,
                      args=("contract_renew", pid), width="stretch",
                      type="primary" if crules.expiring(player.contract_years) else "secondary",
                      help="Sözleşme masası: maaş, süre, rol sözü ve primler.")
            st.button("Sözleşmeyi feshet", key="pv_act_terminate", on_click=cb_pv_action,
                      args=("contract_terminate", pid), width="stretch",
                      help="Transfer Merkezi › Sözleşmeler: tazminat onaydan önce gösterilir.")
        if player.wage_demand:
            st.button("Sözleşme talebine yanıt ver", key="pv_act_renew", on_click=cb_pv_action,
                      args=("renew", pid), width="stretch")
        return
    tournament = getattr(getattr(cm, "game_mode", None), "value", "") == "TOURNAMENT_MODE"
    if cycle and player.team_id is None:                    # 15A: kulupsuz oyuncu -- bonservis yok
        st.button("Sözleşme teklif et", key="pv_act_free", on_click=cb_pv_action, args=("contract_free", pid),
                  width="stretch", type="primary", help="Serbest oyuncu: bonservis gerekmez, doğrudan sözleşme.")
    if cycle and player.team_id is not None and not player.in_academy and not tournament \
            and crules.expiring(player.contract_years) and knowledge >= cv.FOG_CONTRACT_FROM:
        st.button("Ön sözleşme teklif et", key="pv_act_pre", on_click=cb_pv_action, args=("contract_pre", pid),
                  width="stretch", help="Sözleşmesi bitiyor: sezonun ikinci yarısında bedelsiz anlaşabilirsin.")
    if player.team_id is not None and not player.in_academy and not tournament:
        deal_id = TransferDesk(cm).open_deal_for(pid)
        label = "Transfer dosyasını aç" if deal_id else "Teklif yap"
        st.button(label, key="pv_act_bid", on_click=cb_pv_action, args=("bid", pid), width="stretch", type="primary",
                  help="Transfer Merkezi'nde teklif kurucu: peşin, taksit, bonus, sonraki satış payı.")
    listed = cm.is_shortlisted(pid)
    st.button("Takip listesinden çıkar" if listed else "Takip listesine ekle", key="pv_act_shortlist",
              on_click=cb_pv_action, args=("shortlist", pid), width="stretch")
    if knowledge < 100 and not tournament:
        st.button("Gözlemci gönder", key="pv_act_scout", on_click=cb_pv_action, args=("scout", pid),
                  width="stretch", help=f"Şu an bilgi %{knowledge}; gözlemci her hafta artırır.")
    if cm.rules.loans and player.team_id is not None and not player.in_academy:
        st.button("Kiralık iste", key="pv_act_loan", on_click=cb_pv_action, args=("loan", pid), width="stretch")


# ---------------------------------------------------------------- 6a) Profil

def _profile_section(db, cm, team: Team | None, player: Player, profile: Profile, knowledge: int) -> None:
    own = profile.header.own
    cells = attribute_sheet(player, knowledge)
    st.markdown(sheet_html(sheet_columns(cells, extra_cells(player, own, knowledge))), unsafe_allow_html=True)
    if not own:
        st.markdown(f'<div class="pv-sheet-note">{escape(_fog_note(cells, knowledge))}</div>',
                    unsafe_allow_html=True)
    rows = season_matrix(db, player, int(cm.season))
    st.markdown(stats_html(rows, player.position is Position.GK), unsafe_allow_html=True)
    if all(r["Maç"] in (BLANK, 0) for r in rows):
        st.caption(NO_DATA_TEXT)
    natural = POSITION_LABELS.get(profile.header.position, profile.header.position)
    st.markdown(f'<div class="pv-pos">{escape(natural)} ({escape(profile.header.position)})</div>',
                unsafe_allow_html=True)
    with st.expander("Gelişim ve mevki uygunluğu"):
        st.markdown(age_curve_html(player.age), unsafe_allow_html=True)
        st.caption(_development_text(profile, player))
        best = max(profile.fit.items(), key=lambda kv: kv[1].mid)[0] if profile.fit else profile.header.position
        if best != profile.header.position:
            st.caption(f"Gözlemci notu: özelliklerine göre {POSITION_LABELS.get(best, best).lower()} de oynayabilir.")
        fit_items = [(POSITION_LABELS.get(code, code) + (" (doğal)" if code == profile.header.position else ""), est)
                     for code, est in profile.fit.items()]
        st.markdown(attribute_html(fit_items, highlight={natural + " (doğal)"}), unsafe_allow_html=True)
        st.caption("Motorun kuralı: doğal mevkisi dışında oynatılan oyuncu sabit bir güç kaybına uğrar; uygunluk "
                   "satırları gözlemci notudur.")
    with st.expander("Aynı mevkideki oyuncular"):
        _compare_section(db, cm, team, player, profile)


def _fog_note(cells: dict[str, SheetCell], knowledge: int) -> str:
    detail = ("özellikler kesin." if all(c.text.isdigit() for c in cells.values())
              else "aralıklar bilgi arttıkça daralır; %70 bilgiyle kesinleşir, %25 altında bilinmez (?).")
    return f"Gözlemci bilgisi %{knowledge}: {detail} {FOG_TEXT}"


def friendly_goals(db, player_id: int, season: int) -> int:
    """Hazirlik maci golleri (friendlies.events JSON; hazirlikta oyuncu istatistigi saklanmaz, yalnizca goller)."""
    return int(db.execute(text(
        "SELECT count(*) FROM friendlies f, jsonb_array_elements(f.events) e "
        "WHERE f.season = :season AND (e->>'player_id') = :pid"),
        {"season": int(season), "pid": str(int(player_id))}).scalar() or 0)


BLANK = "-"
AVR_BLANK = "----"
STAT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Maç", "apps"), ("Dk", "minutes"), ("Gol", "goals"), ("Ast", "assists"), ("Şut", "shots"),
    ("İsab. şut", "on_target"), ("Kurt.", "saves"), ("Sarı", "yellow"), ("Kırm.", "red"),
)
ROW_FRIENDLY, ROW_LEAGUE, ROW_CUP = "Hazırlık", "Lig", "Devler Arenası"
ROW_NATIONAL, ROW_CAREER = "Milli", "Kariyer (resmi)"
_ACC_FIELDS = ("apps", "minutes", "goals", "assists", "shots", "on_target", "saves", "yellow", "red")


def _empty_acc() -> dict:
    return {**dict.fromkeys(_ACC_FIELDS, 0), "rating_sum": 0.0, "rated": 0}


def _add(acc: dict, r: SeasonRow) -> None:
    for name in _ACC_FIELDS:
        acc[name] += getattr(r, "appearances" if name == "apps" else name)
    if r.rating is not None:
        acc["rating_sum"] += r.rating * r.appearances
        acc["rated"] += r.appearances


def season_matrix(db, player: Player, season: int) -> list[dict]:
    """
    CM istatistik matrisi: bu sezonun yarisma satirlari (Hazirlik / Lig / Devler Arenasi) + Milli (kariyer) + Kariyer
    (tum resmi maclar). Satirlar CM'deki gibi HEP vardir; veri yoksa "-" / "----". Yalnizca SAKLANAN sutunlar
    (player_match_stats): mac, dk, gol, asist, sut, isabetli sut, kurtaris (kaleci), sari, kirmizi, ort. not. Hazirlikta
    yalnizca gol saklanir; milli satirda kariyer milli mac / gol. Motorun simule etmedigi sutun (pas, top kapma) YOK.
    """
    this_season: dict[str, dict] = {}
    career: dict | None = None
    for r in season_rows(db, player.id):
        career = career or _empty_acc()
        _add(career, r)
        if r.season == season:
            _add(this_season.setdefault(r.competition, _empty_acc()), r)

    def row(label: str, acc: dict | None, **only) -> dict:
        out: dict = {"Yarışma": label}
        for title, name in STAT_COLUMNS:
            out[title] = acc[name] if acc is not None else only.get(name, BLANK)
        out["Ort. not"] = f"{acc['rating_sum'] / acc['rated']:.2f}" if acc and acc["rated"] else AVR_BLANK
        return out

    friendly = friendly_goals(db, player.id, season)
    caps = int(player.international_caps or 0)
    return [
        row(ROW_FRIENDLY, None, goals=friendly) if friendly else row(ROW_FRIENDLY, None),
        row(ROW_LEAGUE, this_season.get(COMPETITION_LABELS[Competition.LEAGUE.value])),
        row(ROW_CUP, this_season.get(COMPETITION_LABELS[Competition.CUP.value])),
        (row(ROW_NATIONAL, None, apps=caps, goals=int(player.international_goals or 0)) if caps
         else row(ROW_NATIONAL, None)),
        row(ROW_CAREER, career),
    ]


def stats_html(rows: list[dict], keeper: bool) -> str:
    """CM istatistik matrisi (HTML): gri kabartmali baslik dugmeleri, beyaz sayilar, mor "Ort. not" sutunu."""
    import nav_view

    titles = [t for t, name in STAT_COLUMNS if keeper or name != "saves"]
    header = ["", *titles, "Ort. not"]
    body = [[r["Yarışma"], *(r[t] for t in titles), r["Ort. not"]] for r in rows]
    table = nav_view.table_html(header, body, left=(0,), avr=len(header) - 1)
    return f'<div class="pv-stats">{table}</div>'


def _condition_word(condition: int) -> str:
    band = fitness.condition_band(condition)
    return {"good": "dinç", "warn": "yorgun", "low": "bitkin"}.get(band, "—")


def _development_text(profile: Profile, player: Player) -> str:
    """Yasa ve tahmini potansiyele gore duz Turkce degerlendirme (gizli sayi ve yildiz kullanmadan)."""
    band = next(label for low, high, label in AGE_BANDS if low <= player.age <= high)
    gap = profile.potential.mid - profile.overall.mid
    if player.age >= development.DECLINE_START_AGE:
        outlook = "Bu yaştan sonra özellikleri sezon sezon geriler; sözleşme kararlarında bunu hesaba kat."
    elif gap >= 12:
        outlook = "Gözlemciye göre önünde ciddi bir gelişim payı var; düzenli oynarsa hızlı ilerler."
    elif gap >= 5:
        outlook = "Gelişimi sürüyor ama tavanına yaklaşıyor."
    else:
        outlook = "Gözlemciye göre tavanına yakın: buradan sonrası büyük ölçüde form meselesi."
    view = (f" Gözlemcinin görüşü: şu an **{profile.overall.word.lower()}**, potansiyeli "
            f"**{profile.potential.word.lower()}**.")
    wonder = " Yaşına göre tahmini tavanı çok yüksek." if profile.header.wonderkid else ""
    return f"Yaş bandı: **{band}** ({player.age}). {outlook}{view}{wonder}"


# ---------------------------------------------------------------- 6b) Sakatlik & Cezalar

def _pairs(rows, *, status: bool = False) -> None:
    import nav_view

    st.markdown(nav_view.pairs_html(rows, status=status), unsafe_allow_html=True)


def _injury_section(db, cm, team: Team | None, player: Player, profile: Profile, knowledge: int) -> None:
    week = int(cm.current_week)
    injured = player.is_injured(week)
    rows = [
        ("Durum", f"sakat · {player.injured_until_week}. haftada döner" if injured else "Sağlıklı"),
        ("Lig cezası", f"{player.suspended_matches} maç" if player.suspended_matches else "Yok"),
        ("Sarı kart (lig)", f"{player.season_yellow_cards} · 4'te 1 maç ceza"),
        ("Devler Arenası cezası", f"{player.cup_suspended_matches} maç" if player.cup_suspended_matches else "Yok"),
        ("Sarı kart (kupa)", player.cup_yellow_cards),
    ]
    if profile.header.own and player.condition is not None:
        condition = int(player.condition)
        rows.append(("Kondisyon", f"%{condition} ({_condition_word(condition)})"))
    rows.append(("Sakatlık eğilimi", _injury_label(cm, team, player, knowledge)))
    _pairs(rows)
    season = season_rows(db, player.id)
    injuries = sum(r.injuries for r in season)
    yellow, red = sum(r.yellow for r in season), sum(r.red for r in season)
    st.caption(f"Kariyerinde {injuries} maçta sakatlanarak çıktı · {yellow} sarı, {red} kırmızı kart."
               if season else NO_DATA_TEXT)
    injured_matches = [r for r in recent_rows(db, player.id, limit=RECENT_MATCHES) if r.get("Durum")]
    if injured_matches:
        st.dataframe(pd.DataFrame(injured_matches), hide_index=True, width="stretch")


def _injury_label(cm, team: Team | None, player: Player, knowledge: int) -> str:
    """Gizli sakatlik egilimi yalnizca ETIKET olarak ve gozlem esiginde (transfer masasiyla ayni kural, %75)."""
    import transfer_rules as rules

    if team is None or knowledge < rules.FULL_THRESHOLD:
        return f"%{rules.FULL_THRESHOLD} gözlem bilgisiyle görünür"
    prone = rules.hidden_trait("injury", player.id, (player.fm_attributes or {}).get("injury_proneness"))
    return rules.proneness_label(prone)


# ---------------------------------------------------------------- 6c) Sozlesme

def _contract_section(db, cm, team: Team | None, player: Player, profile: Profile, knowledge: int = 0) -> None:
    import transfer_rules as rules

    own = profile.header.own
    value_text = _value_text(cm, team, player, knowledge)
    wage_text = money(player.current_wage) if own else "Bilinmiyor"
    if player.release_clause is not None and (own or knowledge >= rules.DETAIL_THRESHOLD):
        release = money(player.release_clause)
    elif own or knowledge >= rules.DETAIL_THRESHOLD:
        release = "Yok"
    else:
        release = f"%{rules.DETAIL_THRESHOLD} bilgiyle görünür"
    import contracts as crules

    cycle = cv.contract_cycle_on(cm)
    rows = [("Haftalık maaş (EUR)", wage_text),
            ("Sözleşme", f"{player.contract_years} yıl" if player.contract_years else "Son sezon"),
            ("Piyasa değeri (EUR)", value_text)]
    if own or knowledge >= cv.FOG_CONTRACT_FROM:            # 15A: bitis sezonu (sozlesme suresiyle ayni sis esigi)
        rows.insert(2, ("Bitiş (sezon)", str(crules.expiry_season(int(cm.season), player.contract_years))))
    if not own or player.in_academy:              # A takim oyuncusunda statu asagidaki blokta (tekrar yok)
        rows.append(("Kulüpteki statü", squad_status(player.squad_role, player.age, profile.header.wonderkid)
                     if own else "Bilinmiyor"))
    rows.append(("Serbest kalma bedeli (EUR)", release))
    clauses = (player.contract_clauses or {}) if own else {}
    if clauses.get("promised_role"):
        role = ROLE_PROMISE_LABELS.get(SquadRole(clauses["promised_role"]), clauses["promised_role"])
        rows.append(("Rol sözü", role + (" · tutulmadı" if clauses.get("promise_broken") else "")))
    for key, label in (("loyalty_bonus", "Sadakat primi (sezon)"), ("appearance_bonus", "Maç primi"),
                       ("goal_bonus", "Gol primi")):
        if clauses.get(key):
            rows.append((label, format_money(int(clauses[key]))))
    _pairs(rows)
    if cycle:
        _talk_state(db, team, player, own)
    if not own:
        st.caption("Maaşı ve kulüp içi rolü başka kulübün defterinde; piyasa değeri gözlemci tahminidir.")
        return
    if cycle and crules.expiring(player.contract_years) and not player.in_academy:
        st.warning("Sözleşmesi bu sezon sonunda bitiyor: yenilemezsen kulüpten ayrılır ve sezonun ikinci yarısında "
                   "başka kulüple ön sözleşme imzalayabilir (Eylem › Sözleşmeyi yenile).")
    if player.wage_demand:
        st.markdown(f"Yeni sözleşme istiyor: {format_money(player.current_wage)} → "
                    f"**{format_money(player.wage_demand)}**/hafta (Kadro sayfasında cevapla).")
    elif not any(clauses.get(k) for k in ("promised_role", "loyalty_bonus", "appearance_bonus", "goal_bonus")):
        st.caption("Sözleşmesinde ek madde yok.")
    _role_block(cm, team, player, profile.header.wonderkid)


def _talk_state(db, team: Team | None, player: Player, own: bool) -> None:
    """15A: bu oyuncuyla SUREN sozlesme gorusmesi (kendi kulubumun defteri; K12: baska kulubun gorusmesi gorunmez)."""
    entry = cv.contract_talk_map(db, team).get(player.id)
    if entry is None:
        return
    _kind, _status, talk_id, other = entry
    if other:                                    # kendi oyuncum baska kulupte on sozlesme imzaladi
        st.warning(md_escape(f"Ön sözleşme imzaladı: sezon sonunda {other} kulübüne bedelsiz gidecek. "
                             "Artık satılamaz ve sözleşmesi yenilenemez."))
        return
    (st.info if own else st.success)(md_escape(cv.talk_state_text(entry)))
    if talk_id is not None:
        st.button("Sözleşme masasını aç", key="pv_talk_open", on_click=cb_pv_action,
                  args=("contract_desk", player.id))


def _role_block(cm, team: Team | None, player: Player, wonderkid: bool = False) -> None:
    st.markdown(panel_title_html("Kulüpteki statü ve süre"), unsafe_allow_html=True)
    if player.in_academy:
        st.info("Akademi oyuncusu: A takım süre beklentisi işlemez. U-21 maçları gelişimini besler; "
                "A takıma yükseltince kadro rolü ve süre beklentisi başlar.")
        return
    row = next((r for r in cm.player_concerns(team) if r.player_id == player.id), None)
    if row is None:
        st.caption("Bu oyuncu için süre değerlendirmesi yok.")
        return
    _pairs([("Kulüpteki statü", squad_status(row.role, player.age, wonderkid)), ("Beklediği maç", f"{row.wanted:.1f}"),
            ("Oynadığı", f"{row.played:.1f}"), ("Memnuniyet", row.label)])
    st.markdown(f"**Durum:** {md_escape(row.reason)}")
    if row.overloaded:
        st.warning("Kadro şişkin: bu mevkide beklenti toplamı dağıtılabilir süreyi aşıyor, yedekler daha "
                   "çabuk şikayet eder.")
    st.caption(f"Süre beklentisi statüden gelir (Vazgeçilmez > Önemli ilk 11 > Rotasyon / yedek) ve son "
               f"{concern_rules.CONCERN_WINDOW} resmi maça bakar; kupa maçları yarım sayılır.")


def _value_text(cm, team: Team | None, player: Player, knowledge: int | None = None) -> str:
    """Piyasa degeri (kisa para, birim etikette) TEK SIS MODELINDEN: kendi oyuncunda kesin, %70+ kesin, %25-69
    aralik, altinda "?"."""
    if team is None:
        return cv.UNKNOWN_TEXT
    if knowledge is None:
        knowledge = viewer_knowledge(cm, team, player)
    return cv.player_fog(None, team, player, knowledge).value_text


# ---------------------------------------------------------------- 6d) Transfer

def _transfer_section(db, cm, team: Team | None, player: Player, profile: Profile) -> None:
    own = profile.header.own
    lines: list[str] = []
    note = loan_note(db, cm, player)
    if note:
        lines.append(md_escape(note))
    if player.transfer_listed:
        lines.append("Kulübü onu satış listesine koydu.")
    if player.loan_listed:
        lines.append("Kiralık listesinde.")
    banned, ban_reason = cm.transfer_ban_info(player)
    if banned:
        lines.append(f"Transfer yasağı: {md_escape(ban_reason)}")
    if own:
        lines.append("İstenen bedel: " + (format_money(player.asking_price) if player.asking_price is not None
                                           else "belirlenmedi (kulüp kendi değerlemesini kullanır)"))
    if not lines:
        lines.append("Özel bir durumu yok: sözleşmesi işliyor, listede değil.")
    for line in lines:
        st.markdown(line)
    if not own:
        st.markdown(panel_title_html("Bana kaça mal olur?"), unsafe_allow_html=True)
        st.markdown(_cost_text(cm, team, player))
    st.markdown(panel_title_html("Transfer geçmişi"), unsafe_allow_html=True)
    rows = transfer_rows(db, player.id, player.name)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("Bu oyuncu bu kariyerde hiç kulüp değiştirmedi.")


def _cost_text(cm, team: Team | None, player: Player) -> str:
    """
    K12: kulubun hedef bedeli (transfers.asking_price) ARTIK YAZILMAZ. Acik dosya varsa kulubun soyledigi SISLI
    aralik; yoksa gozlemcinin deger tahmini ve 'kulube sor' yonlendirmesi.
    """
    if team is None or player.team is None:
        return "Kulübü yok: bonservis istemez, yalnızca sözleşme masası kurulur."
    banned, ban_reason = cm.transfer_ban_info(player)
    if banned:
        return f"Şu an satın alınamaz — {md_escape(ban_reason)}."
    if player.in_academy:
        return "Kulübünün akademisinde: akademi oyuncuları satılık değil."
    desk = TransferDesk(cm)
    deal_id = desk.open_deal_for(player.id)
    if deal_id is not None:
        view = desk.deal(deal_id)
        if view.price_hint:
            return (f"{md_escape(player.team.name)} fiyat beklentisini **{format_money(view.price_hint[0])} – "
                    f"{format_money(view.price_hint[1])}** civarı olarak söyledi (kulübün tavrı: "
                    f"{md_escape(view.stance_label or '—')}). Dosya: Transfer Merkezi › Dosyalarım.")
        return f"Açık bir transfer dosyan var ({md_escape(view.status_label)}): Transfer Merkezi › Dosyalarım."
    value = _value_text(cm, team, player)
    if value == cv.UNKNOWN_TEXT:
        return (f"Gözlemcin bu oyuncuyu henüz tanımıyor (bilgi %{cv.FOG_RANGE_FROM} altında): değeri bilinmiyor. "
                "**Eylem › Gözlemci gönder** ile tanı ya da kulübe sor.")
    return (f"Gözlemcin değerini **{value}** EUR civarında görüyor. Kulübün fiyat "
            f"beklentisini öğrenmek için **Eylem › Teklif yap** ile kulübe sor; transfer bütçen "
            f"{format_money(team.transfer_budget)}.")


# ---------------------------------------------------------------- 6e) Gecmis

def _stats_section(db, player: Player) -> None:
    rows = season_rows(db, player.id)
    totals = _totals(rows)
    _pairs([
        ("Maç", totals["apps"]), ("Gol", totals["goals"]), ("Asist", totals["assists"]),
        ("Ort. not", totals["rating"] if totals["rating"] is not None else "—"),
        ("Dakika", totals["minutes"]),
        ("Kart", f"{totals['yellow']} sarı, {totals['red']} kırmızı"),
        ("Milli maç", f"{player.international_caps} ({player.international_goals} gol)"),
    ])
    if not rows:
        st.info(NO_DATA_TEXT + " Sezon tablosu ilk resmi maçtan sonra dolar.")
        return
    st.markdown(panel_title_html("Sezon sezon"), unsafe_allow_html=True)
    st.dataframe(pd.DataFrame([
        {"Sezon": r.season, "Kulüp": r.team_name, "Kupa/Lig": r.competition, "Maç": r.appearances,
         "Dk": r.minutes, "Gol": r.goals, "Asist": r.assists, "Şut": r.shots, "Sarı": r.yellow, "Kırm.": r.red,
         "Sakatlık": r.injuries, "Ort. not": r.rating if r.rating is not None else "—"}
        for r in rows
    ]).astype(str), hide_index=True, width="stretch")
    injuries = sum(r.injuries for r in rows)
    st.caption(f"Sakatlık geçmişi: {injuries} maçta sakatlanarak çıktı." if injuries
               else "Sakatlık geçmişi: maç içinde hiç sakatlanmadı.")
    st.markdown(panel_title_html("Son maçlar"), unsafe_allow_html=True)
    recent = recent_rows(db, player.id)
    if recent:
        st.dataframe(pd.DataFrame(recent), hide_index=True, width="stretch")
    else:
        st.caption(NO_DATA_TEXT)
    history = list(player.match_rating_history or [])
    if history:
        st.caption("Son maç notları: " + " · ".join(f"{value:.1f}" for value in history[-8:]))


def _totals(rows: list[SeasonRow]) -> dict:
    """Kariyer toplami: sezon satirlarindan hesaplanir (ikinci sorgu acilmaz)."""
    apps = sum(r.appearances for r in rows)
    weighted = sum((r.rating or 0) * r.appearances for r in rows if r.rating is not None)
    rated = sum(r.appearances for r in rows if r.rating is not None)
    return {
        "apps": apps,
        "minutes": sum(r.minutes for r in rows),
        "goals": sum(r.goals for r in rows),
        "assists": sum(r.assists for r in rows),
        "yellow": sum(r.yellow for r in rows),
        "red": sum(r.red for r in rows),
        "rating": round(weighted / rated, 2) if rated else None,
    }


# ---------------------------------------------------------------- 6f) Eylem (veritabanina yazan tek callback)

ACTION_AREA = "main"                     # web_app: her sayfanin ustunde gosterilen genel mesajlar
# 15A sozlesme eylemleri: masayi ACAR (yenileme / serbest imza / on sozlesme) ya da Sozlesmeler bolumune gotururur.
# FESIH burada YAPILMAZ: tazminat onay ekraninda gosterilir (Transfer Merkezi › Sözleşmeler).
CONTRACT_ACTIONS = ("contract_renew", "contract_free", "contract_pre", "contract_terminate", "contract_desk")


def _contract_action(cm, action: str, player: Player, tc) -> tuple[str, str] | None:
    """Eylem menusunun 15A dalı. ContractDesk hatalari (DeskError) cagiranin try blogunda yakalanir."""
    import contracts as crules
    from transfer_desk import ContractDesk

    pid = int(player.id)
    if action in ("contract_terminate", "contract_desk"):
        kind = crules.KIND_RENEWAL if player.team_id == cm.user_team.id else (
            crules.KIND_FREE_AGENT if player.team_id is None else crules.KIND_PRE_CONTRACT)
        tc.open_contracts(tc.CONTRACT_TABS.get(kind, tc.C_MINE), pid)
        if action == "contract_terminate":
            return ("info", f"{md_escape(player.name)}: fesih bedeli Transfer Merkezi › Sözleşmeler bölümünde, "
                            "onaydan önce gösterilir.")
        return ("info", f"{md_escape(player.name)}: sözleşme masası Transfer Merkezi › Sözleşmeler bölümünde.")
    kind = {"contract_renew": crules.KIND_RENEWAL, "contract_free": crules.KIND_FREE_AGENT,
            "contract_pre": crules.KIND_PRE_CONTRACT}[action]
    opener = {crules.KIND_RENEWAL: "open_renewal", crules.KIND_FREE_AGENT: "open_free_agent",
              crules.KIND_PRE_CONTRACT: "open_pre_contract"}[kind]
    step = getattr(ContractDesk(cm), opener)(pid)
    tc.open_contracts(tc.CONTRACT_TABS[kind], pid)
    if step.status is transfers.NegotiationStatus.WALKED_AWAY:
        return ("error", md_escape(step.message or "Oyuncu görüşmeye oturmadı."))
    return ("success", md_escape(step.message))


@member_callback
def cb_pv_action(action: str, player_id: int) -> None:
    """
    Eylem menusu. Kulup her zaman cm.user_team; masa / kariyer yoneticisi yetkiyi dogrular (baska kulubun dosyasi,
    kendi oyuncun olmayan oyuncunun listesi reddedilir). Sayfa degistiren eylemler nav_view.goto kullanir.
    """
    import nav_view
    import transfer_centre_view as tc
    from transfer_desk import IN, DeskError

    action = str(action)
    pid = int(player_id)
    ss = st.session_state
    message = None
    with session_scope() as db:
        cm = manager(db)
        team = cm.user_team
        player = db.get(Player, pid)
        if team is None or player is None:
            return
        desk = TransferDesk(cm)
        try:
            if action in ("bid", "loan") and (player.team_id in cm.human_team_ids() or action == "loan"):
                ss["mkt_name"], ss["mkt_level"], ss["mkt_target"] = player.name, cv.LEVEL_FILTER_ALL, pid
                ss[tc.SECTION_KEY] = tc.SEC_SEARCH
                nav_view.goto(nav_view.TRANSFER)
                message = ("info", f"{md_escape(player.name)}: teklif / kiralık paneli Transfer Merkezi'nde.")
            elif action == "bid":
                deal_id = desk.open_deal_for(pid)
                view = desk.deal(deal_id) if deal_id is not None else desk.enquire(pid)
                tc.open_file(view.id, IN)
                nav_view.goto(nav_view.TRANSFER)
                message = ("info", md_escape(view.club_message or view.status_label))
            elif action == "shortlist":
                if cm.is_shortlisted(pid):
                    cm.shortlist_remove(pid)
                    message = ("success", f"{md_escape(player.name)} takip listesinden çıkarıldı.")
                else:
                    cm.shortlist_add(player)
                    message = ("success", f"{md_escape(player.name)} takip listesine eklendi.")
            elif action == "scout":
                info = desk.scout(pid)
                message = ("success", f"Gözlemci {md_escape(player.name)} için görevlendirildi (bilgi %"
                                      f"{info.knowledge}, haftada ~%{info.weekly_gain}).")
            elif action == "list":
                desk.set_listing(pid, transfer=not player.transfer_listed)
                message = ("success", f"{md_escape(player.name)} " + (
                    "satış listesine kondu." if player.transfer_listed else "satış listesinden çıkarıldı."))
            elif action == "ask":
                ss[tc.SECTION_KEY], ss["tc_my_pick"] = tc.SEC_MINE, pid
                ss.pop("tc_ask_for", None)
                nav_view.goto(nav_view.TRANSFER)
            elif action == "renew":
                nav_view.goto(nav_view.SQUAD)
                message = ("info", f"{md_escape(player.name)} sözleşme talebi: Kadro › Oyuncu memnuniyeti.")
            elif action in CONTRACT_ACTIONS:                        # 15A sozlesme masasi (Sozlesmeler bolumu)
                message = _contract_action(cm, action, player, tc)
                nav_view.goto(nav_view.TRANSFER)
        except (DeskError, TransferError, ShortlistError) as exc:
            message = ("error", md_escape(str(exc)))
    if message is not None:
        flash(ACTION_AREA, *message)


# ---------------------------------------------------------------- 6d) karsilastirma ve rol

def _compare_section(db, cm, team: Team | None, player: Player, profile: Profile) -> None:
    st.caption("Kendi oyuncuların kesin, diğerleri gözlemci tahminiyle sıralanır: liste gizli bir sayıyı "
               "değil, senin bildiğin kadarını gösterir.")
    scope = st.radio("Karşılaştırma", list(CMP_OPTIONS), key=COMPARE_KEY, horizontal=True,
                     label_visibility="collapsed")
    rows = (_squad_compare_rows(cm, team, player) if scope == CMP_SQUAD
            else _league_compare_rows(db, cm, team, player))
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    elif scope == CMP_SQUAD:
        st.info("Kadronda bu mevkide karşılaştırılacak oyuncu yok.")
    else:
        st.info("Bu oyuncunun ligi yok (kulüpsüz) ya da ligde aynı mevkide başka oyuncu bulunamadı.")



def _squad_compare_rows(cm, team: Team | None, player: Player) -> list[dict]:
    """Kendi kadron: ek SORGU YOK (team.players zaten yuklu)."""
    if team is None:
        return []
    peers = [p for p in team.players if p.position is player.position]
    if player.team_id == team.id and player not in peers:
        peers.append(player)
    rows = []
    for p in sorted(peers, key=lambda x: (-x.overall_rating, x.name)):
        rows.append({
            "Oyuncu": ("► " if p.id == player.id else "") + p.name,
            "Yaş": p.age,
            cv.ABILITY_LABEL: cv.ability_word(p.overall_rating),
            f"{cv.POTENTIAL_LABEL} (gözlemci)": cv.ability_text(cm.potential_estimate(team, p)),
            "Form": mood_word(p.form),
            "Kondisyon": f"%{int(getattr(p, 'condition', 100))}",
            "Rol": transfers.ROLE_LABELS[p.squad_role],
            "Ort. not": f"{p.average_rating:.2f}" if p.average_rating is not None else "—",
        })
    return rows


def _league_compare_rows(db, cm, team: Team | None, player: Player) -> list[dict]:
    """Ligdeki mevkidaslari: TEK sorgu + bilgi haritasi; siralama ve gosterim TEK SIS MODELI uzerinden (sizinti yok:
    yetenegi bilinmeyen "?" sonda)."""
    pool = league_peers(db, player)
    if not pool:
        return []
    known = cv.knowledge_map(db, team, [p.id for p, _c in pool])
    scored = []
    for p, club_name in pool:
        fog = cv.player_fog(None, team, p, known.get(p.id, 0))
        scored.append((fog.ability_mid, p, club_name, fog))
    scored.sort(key=lambda item: (item[0] is None, -(item[0] or 0), item[1].name))
    top = scored[:LEAGUE_PEERS]
    if not any(item[1].id == player.id for item in top):
        me = next((item for item in scored if item[1].id == player.id), None)
        if me is not None:
            top = [*top[: LEAGUE_PEERS - 1], me]
    return [
        {"Oyuncu": ("► " if p.id == player.id else "") + p.name, "Kulüp": club_name, "Yaş": p.age,
         f"{cv.ABILITY_LABEL} (gözlemci)": fog.ability_text, "Sözleşme": fog.contract_text}
        for _mid, p, club_name, fog in top
    ]
