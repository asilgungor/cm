"""
match_day_view.py
=================
Faz 14A -- MAC GUNU EKRANI (CM 01/02 duzeni, fragment oynatma). Canli mac bolumunun tamami web_app.py'den buraya
tasindi; web_app yalnizca sayfa yonlendirmesini (render) cagirir.

Duzen (yukaridan asagiya; dar ekranda sutunlar alt alta, 375 px'te yatay kaydirma yok):
    1) Skor bandi       kulup renginde iki takim kutusu (crc32(ad) -> sabit palet, WCAG AA yazi rengi), ortada
                        skor, solda buyuk dakika + evre; yarisma satiri ("Lig · 12. hafta", kupada toplam skor)
    2) Olaylar          gol atanlar, kirmizi kart (🟥) ve sakatlik (✚) dakikalariyla, her takimin kendi sutununda
    3) Yorum afisi      TEK buyuk afis (olayin takiminin renginde; golde sari + yanip soner, kirmizida kirmizi);
                        altinda son 3 onemli an ve asistan notu. Kayan uzun liste YOK. Yaninda sekil tahtasi (K8).
    4) "Son 5 dk"       topla oynama (LiveMatch.possession_log); hazir sonuc izlenirken "Maç geneli"
    5) Kontrol satiri   DURDUR / DEVAM, Sonucu gör, Kaydet, Kapat; duraklama nedeni + eylem dugmeleri; zaman seridi.
                        (Brief'teki sirada sekmelerden sonra; sekmeler uzun olabildigi icin tek dokunusla erisilsin
                        diye sekmelerin USTUNE alindi.)
    6) Sekmeler         Maç Özeti (zincirler tek pasaj, "Tam kayıt") / İstatistik / Oyuncu Notları (canli not) /
                        Puan Durumu (lig maci, canli) / Maç Raporu (match_feed.match_report)
    7) Mudahale paneli  8 talimat ekseni, 6 tek tik bagiris, talimatin bedeli (etiket, sayi yok), dizilis, degisiklik

Oynatma ("kare imleci" + st.fragment; time.sleep YOK):
    session_state: live (LiveMatch) ya da md_replay (hazir MatchResult), md_cursor (gosterilen son gorunur karenin
    indeksi), md_due (siradaki karenin zamani, time.monotonic), md_mode (ozet modu), md_view_index (geri sarma).
    1-6 TEK parcadir (_match_view). Vakti geldiyse imleci ozet moduna uyan bir sonraki kareye tasir; yeni kare yoksa
    motoru en cok MAX_ENGINE_STEPS dakika ilerletir (LiveMatch.tick). Bekleme frame.dwell_ms x hiz carpani (next_due).
    Parcayi bir sonraki karenin vaktinde istemci zamanlayicisi (st.components.v2, TIMER_JS) yeniden calistirir: kare
    basina bir calisma, durakken hic; sabit periyot yalnizca yedek (FALLBACK_TICK_S). Kontroller parcanin icinde:
    DURDUR tiklamasi yalnizca parcayi cizer (motor callback'te durur); durum degisince (duraklama, mac sonu, kayit,
    kapatma) mudahale paneli icin tum sayfa bir kez yeniden cizilir (st.rerun(scope="app")).
    "Anında": LiveMatch.run() (bir sonraki duraklamaya ya da mac sonuna). Oynatma yalnizca GORUNTUYU zamanlar: motor
    ayni tick()/run() cagrilariyla ilerler; hiz, ozet modu ve geri sarma sonucu degistiremez (determinizm testi:
    tests/test_match_day_view.py).
    Topla oynama tek kaynaktan: MatchResult.possession_share() (sekans agirlikli). "Son 5 dk" ayni sayaclarin
    penceresidir (LiveMatch.possession_log).

K12: ekranda sans kalitesi, xG, gosterim olasiligi ya da gizli ozellik YOK. Canli not motorun not formuluyle
(MatchEngine._compute_ratings) birebir aynidir ve yalnizca gorunen sayilardan kurulur (gol, asist, isabetli sut,
kurtaris, kart, oynanan dakika, kondisyon). Asistan notu yalnizca menajerin gordugu verilerden: skor, sut / isabet,
korner, kart, topla oynama, kondisyon, rakibin (akista gorunen) taktik degisikligi.

Callback'ler web_common.member_callback ile sarilidir (oturum + dunya uyeligi + paylasilan dunyada kilit); tek
istisna bos zamanlayici tetigi cb_md_tick (requires_auth). tests/test_world_schema.py *_view.py modullerindeki her
cb_* icin requires_auth'u denetler.
"""

from __future__ import annotations

import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import select

import career_views as cv
import pitch
from career_manager import LiveMatchError
from database import session_scope
from fitness import condition_band, fatigue_rating_penalty
from instructions import (
    COUNTER_ATTACK_LABEL,
    FOCUS_LABELS,
    MENTALITY_LABELS,
    OFFSIDE_TRAP_LABEL,
    PASSING_LABELS,
    PRESSING_LABELS,
    TACKLING_LABELS,
    TEMPO_LABELS,
    AttackingFocus,
    Mentality,
    PassingStyle,
    Pressing,
    Tackling,
    TeamInstructions,
    Tempo,
    parse_mentality,
    parse_tackling,
)
from live_match import (
    ASSISTANT_MINUTES,
    AUTO_PAUSE_DEFAULTS,
    SUB_RULE_LABELS,
    LiveMatch,
    engine_config_for,
)
from match_engine import (
    PRIORITY_HIGH,
    PRIORITY_MAIN,
    InterventionError,
    KnockoutRule,
    MatchEngine,
    MatchResult,
    build_match_team,
)
from match_feed import (
    PHASE_PRE_MATCH,
    Frame,
    SideStats,
    build_timeline,
    energy_at,
    match_report,
    summarize,
)
from models import Competition, Fixture, Position, Team
from ofm_theme import contrast_ratio
from stars import stars
from tactics import MATCH_FORMATIONS
from web_common import (
    flash,
    manager,
    md_escape,
    member_callback,
    parse_seed,
    requires_auth,
    reset_widgets,
    shared_page_world,
    show_flash,
)
from web_view import condition_bar_html, event_html, stats_html, summary_lines

# ===========================================================================
# [1] SABITLER
# ===========================================================================

LIVE_MANAGE, LIVE_FRIENDLY, LIVE_REPLAY = "Maçımı yönet", "Hazırlık maçı", "Son maçımı izle"
LIVE_MODES = [LIVE_MANAGE, LIVE_FRIENDLY, LIVE_REPLAY]
SIDE_WATCH = "Sadece izle"
SIDE_LABELS = ["Ev sahibi", "Deplasman", SIDE_WATCH]
RULE_OPTIONS = list(SUB_RULE_LABELS.values())
MENTALITY_OPTIONS = list(MENTALITY_LABELS.values())
TACKLING_OPTIONS = list(TACKLING_LABELS.values())
ROLE_SAME = "Çıkanın görevi"
ROLE_CHOICES = [ROLE_SAME] + [p.value for p in Position]
SHARED_LIVE_TEXT = ("🌍 Paylaşılan dünyada resmi maçlar hafta ilerlerken birlikte oynanır: kadronu **📋 Kadro**, "
                    "talimatlarını **🎯 Taktik** sayfasında hazırla, sonra hazır ol. Hazırlık maçlarını canlı "
                    "yönetebilirsin (Mod: Hazırlık maçı).")
FRIENDLY_CAPTION = "Hazırlık maçı — veritabanına kaydedilmez."
KNOCKOUT_CAPTION = " Eleme kuralları: beraberlikte uzatma ve penaltılar."

# --- oynatma zamanlayicisi ---
SPEED_FACTORS: dict[str, float] = {"Yavaş": 1.4, "Normal": 1.0, "Hızlı": 0.5, "Anında": 0.0}   # anahtar: live_speed
DEFAULT_SPEED = "Normal"
MAX_ENGINE_STEPS = 8          # tek parca calismasinda en cok bu kadar bos dakika (CPU)
MIN_TIMER_MS = 60             # istemci zamanlayicisinin en kisa beklemesi (bos dakika zinciri icin)
FALLBACK_TICK_S = 2.0         # zamanlayici calismazsa yedek parca periyodu (yalnizca mac akarken)
DRIFT_WINDOW_S = 0.5          # bu kadar gec kalan kare, bir oncekinin vaktinden sayilir (kaymasiz zamanlama)

# Istemci zamanlayicisi (st.components.v2, derleme / CDN yok): Python siradaki karenin bekleme suresini verir, JS
# setTimeout bitince "tick" tetigini yollar -> Streamlit YALNIZCA parcayi yeniden calistirir. Sabit periyotlu
# run_every yerine: bu sayfada her parca calismasi tarayicida ~100-200 ms tum-agac cizimi demek (olculdu); saniyede
# 4 bos adim ana is parcacigini %70-80 mesgul edip dugmeleri geciktiriyordu. Kare basina tek calisma, durakken sifir.
TIMER_NAME = "ofm_match_day_timer"
TIMER_KEY = "md_timer"
TIMER_HTML = '<span class="md-timer" aria-hidden="true"></span>'
TIMER_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const state = parentElement.__mdTimer || (parentElement.__mdTimer = { id: null, token: null });
  const token = data ? data.token : null;
  if (state.token === token) return undefined;
  if (state.id !== null) { clearTimeout(state.id); state.id = null; }
  state.token = token;
  const delay = data && typeof data.delay_ms === "number" ? data.delay_ms : -1;
  if (delay < 0) return undefined;
  state.id = setTimeout(function () { state.id = null; setTriggerValue("tick", token); }, delay);
  return undefined;
}
"""

# --- ozet modlari ---
MODE_FULL, MODE_WIDE, MODE_HIGHLIGHTS, MODE_TEXT = "tam", "genis", "onemli", "metin"
SUMMARY_MODES: dict[str, str] = {
    MODE_FULL: "Tam maç", MODE_WIDE: "Geniş özet", MODE_HIGHLIGHTS: "Önemli anlar", MODE_TEXT: "Sadece metin",
}
# Her modda gosterilen kareler: evre dudukleri (akisin iskeleti) ve belirleyici anlar
ANCHOR_TYPES = frozenset({"KICK_OFF", "HALF_TIME", "FULL_TIME", "EXTRA_TIME_START", "EXTRA_TIME_HALF",
                          "SHOOTOUT_START"})
KEY_TYPES = frozenset({"GOAL", "RED_CARD", "SUBSTITUTION", "PENALTY_SHOOTOUT"})

# --- session_state anahtarlari ---
LIVE_KEY = "live"
REPLAY_KEY = "md_replay"
CURSOR_KEY, DUE_KEY, VIEW_KEY = "md_cursor", "md_due", "md_view_index"
FRAMES_KEY, TABLE_KEY, SYNC_KEY = "md_frames", "md_table", "md_inst_sync"
REWIND_KEY, FULL_LOG_KEY, MODE_KEY = "md_rewind", "md_full_log", "md_mode"
PLAYBACK_KEYS = (CURSOR_KEY, DUE_KEY, VIEW_KEY, FRAMES_KEY, TABLE_KEY, SYNC_KEY, REWIND_KEY, FULL_LOG_KEY)
PANEL_ANCHOR, SUBS_ANCHOR = "md-panel", "md-subs"
CONTROL_KEY, STALE_KEY = "md_control_state", "md_stale"

# 14A: yeni duraklama nedenleri -> ayar anahtari ve etiketi (varsayilanlar live_match.AUTO_PAUSE_DEFAULTS)
PAUSE_TOGGLES: dict[str, tuple[str, str]] = {
    "pause_on_opponent_tactics": ("live_pause_opp_tactics", "Rakip taktik değiştirince durdur"),
    "pause_on_two_goals": ("live_pause_two_goals", "10 dakikada 2 gol yiyince durdur"),
    "pause_on_tired": ("live_pause_tired", "İlk 11'den bir oyuncu çok yorulunca durdur (kondisyon 60 altı)"),
    "pause_for_assistant": ("live_pause_assistant", "Asistan notlarında durdur (15' · 30' · 60' · 75')"),
}


@dataclass(frozen=True)
class Axis:
    """Mac ici talimat ekseni: TeamInstructions alani, widget anahtari, etiket, secenekler (bayrakta None)."""
    field: str
    key: str
    label: str
    options: Mapping | None
    help: str


INSTRUCTION_AXES: tuple[Axis, ...] = (
    Axis("mentality", "live_mentality", "Zihniyet", MENTALITY_LABELS,
         "Çok Ofansif: şut şansı artar ama savunma açılır ve oyuncular daha çok yorulur. "
         "Çok Defansif: kale önü kapanır, hücum söner."),
    Axis("tackling", "live_tackling", "Sertlik", TACKLING_LABELS,
         "Sert Oyna savunmayı güçlendirir ama kart ve sakatlık riskini katlar."),
    Axis("passing_style", "live_passing", "Pas stili", PASSING_LABELS,
         "Kısa pas topa sahip olur ve daha az yorar; direkt oyun daha çok şut üretir."),
    Axis("tempo", "live_tempo_axis", "Tempo", TEMPO_LABELS, "Hızlı tempo daha çok atak ve daha çok yorgunluk demektir."),
    Axis("pressing", "live_pressing", "Pres", PRESSING_LABELS,
         "Tüm sahada pres rakip orta sahayı boğar ama belirgin şekilde yorar."),
    Axis("attacking_focus", "live_focus", "Hücum yönü", FOCUS_LABELS,
         "Kanatlar orta/kafa, merkez teknik ve bitiricilik gücünü kullanır."),
    Axis("offside_trap", "live_offside", OFFSIDE_TRAP_LABEL, None,
         "Yavaş forvetlere karşı savunmayı güçlendirir, hızlı forvetlere karşı riskli."),
    Axis("counter_attack", "live_counter", COUNTER_ATTACK_LABEL, None,
         "Topu rakibe bırakıp boşluğu kovalar; önde basan rakibe karşı etkili."),
)


@dataclass(frozen=True)
class Shout:
    """Tek tik bagiris: bir talimat demeti (TeamInstructions.with_changes)."""
    key: str
    label: str
    changes: Mapping
    help: str


SHOUT_PREFIX = "md_shout_"
SHOUTS: dict[str, Shout] = {s.key: s for s in (
    Shout("one_cik", "📣 Öne çık",
          {"mentality": Mentality.ALL_OUT_ATTACK, "tempo": Tempo.FAST, "pressing": Pressing.ALL_OVER},
          "Topyekûn hücum, hızlı tempo, tüm sahada pres."),
    Shout("skoru_koru", "🛡️ Skoru koru",
          {"mentality": Mentality.PARK_THE_BUS, "tempo": Tempo.SLOW, "pressing": Pressing.OWN_HALF},
          "Otobüsü çek, tempoyu düşür, kendi yarı sahanda karşıla."),
    Shout("topu_tut", "🔄 Topu tut", {"passing_style": PassingStyle.SHORT, "tempo": Tempo.SLOW},
          "Kısa pas ve yavaş tempo: topu ayağında tut."),
    Shout("kanatlara", "↔️ Kanatlara", {"attacking_focus": AttackingFocus.FLANKS}, "Hücumu kanatlardan geliştir."),
    Shout("direkt", "⏩ Direkt oyna", {"passing_style": PassingStyle.DIRECT, "counter_attack": True},
          "Direkt oyun ve kontra atak."),
    Shout("sakin", "🧊 Sakin kal", {"tackling": Tackling.CALM}, "Sert müdahaleden kaçın: kart riski düşer."),
)}

# Talimatin bedeli: YALNIZCA gorunen etiket, sayi yok (instructions.py etki tablolarinin yonu)
INSTRUCTION_COSTS: dict[tuple[str, object], str] = {
    ("mentality", Mentality.ALL_OUT_ATTACK): "savunma açılır, oyuncular daha çabuk yorulur",
    ("mentality", Mentality.PARK_THE_BUS): "hücum söner",
    ("tackling", Tackling.HARD): "kart ve sakatlık riski artar",
    ("tackling", Tackling.CALM): "savunma biraz yumuşar",
    ("passing_style", PassingStyle.SHORT): "daha az pozisyon; tüm sahada pres yapan rakibe karşı pas hatası artar",
    ("passing_style", PassingStyle.DIRECT): "orta saha kontrolü ve pozisyonların netliği biraz düşer",
    ("tempo", Tempo.FAST): "oyuncular daha çabuk yorulur, pas hatası artar",
    ("tempo", Tempo.SLOW): "daha az atak",
    ("pressing", Pressing.ALL_OVER): "oyuncular daha çabuk yorulur, kart riski artar, savunmanın arkası boşalır",
    ("pressing", Pressing.OWN_HALF): "rakibe topla oynama fırsatı verilir",
    ("attacking_focus", AttackingFocus.FLANKS): "ortalardan gelen pozisyonlar daha az net",
    ("attacking_focus", AttackingFocus.CENTRE): "daha az pozisyon",
    ("offside_trap", True): "hızlı forvetlere karşı arkaya atılan toplar tehlikeli olur",
    ("counter_attack", True): "top rakibe bırakılır; geride kapanan rakibe karşı işe yaramaz",
}

# Kulup renkleri: gercek forma verisi yok; takim adindan (crc32) deterministik secim. Yazi rengi WCAG AA.
CLUB_COLORS: tuple[str, ...] = (
    "#b71c1c", "#0d47a1", "#1b5e20", "#4a148c", "#e65100", "#fdd835",
    "#006064", "#880e4f", "#263238", "#f5f5f5", "#01579b", "#3e2723",
)
INK_LIGHT, INK_DARK = "#ffffff", "#111111"
NEUTRAL_COLORS = ("#e53935", "#1e88e5")

MOMENT_ICONS = {"GOAL": "", "RED_CARD": "🟥", "INJURY": "✚"}
STRIP_ICONS = {"GOAL": "⚽", "RED_CARD": "🟥", "INJURY": "✚", "SUBSTITUTION": "🔁", "SAVE": "🎯", "MISS": "🎯"}
OWN_ROLE = {Position.GK: "Kalecimiz", Position.DEF: "Savunmacımız", Position.MID: "Orta sahamız",
            Position.FWD: "Forvetimiz"}
ROLE_ORDER = (Position.GK, Position.DEF, Position.MID, Position.FWD)


# ===========================================================================
# [2] SAF YARDIMCILAR (Streamlit'e dokunmaz; CM_TEST_NO_DB=1 ile test edilir)
# ===========================================================================

# --- zamanlayici ve ozet modlari ---

def speed_factor(label: str | None) -> float:
    return SPEED_FACTORS.get(label or DEFAULT_SPEED, 1.0)


def next_due(now: float, frame: Frame, factor: float) -> float:
    """Karenin ekranda kalacagi sure: olay basina bekleme (rutin 900 / ana 1800 / kritik 3000 ms) x hiz."""
    return now + frame.dwell_ms / 1000.0 * factor


def in_mode(frame: Frame, mode: str) -> bool:
    """Ozet modu filtresi. Iceriklilik: onemli ⊆ genis ⊆ tam (= metin)."""
    if mode not in (MODE_WIDE, MODE_HIGHLIGHTS):
        return True
    ev = frame.event
    if ev.type in ANCHOR_TYPES or ev.type in KEY_TYPES:
        return True
    return ev.priority >= (PRIORITY_MAIN if mode == MODE_WIDE else PRIORITY_HIGH)


def mode_frames(frames: list[Frame], mode: str) -> list[Frame]:
    return [f for f in frames if in_mode(f, mode)]


def next_index(frames: list[Frame], cursor: int, mode: str) -> int | None:
    """Imlecten sonraki, moda uyan ilk karenin indeksi (yoksa None)."""
    for j in range(max(cursor + 1, 0), len(frames)):
        if in_mode(frames[j], mode):
            return j
    return None


def last_in_mode(frames: list[Frame], index: int, mode: str) -> int | None:
    """Afiste duran karenin indeksi: index'e kadar moda uyan son kare (yoksa None)."""
    for j in range(min(index, len(frames) - 1), -1, -1):
        if in_mode(frames[j], mode):
            return j
    return None


def step_playback(live: LiveMatch | None, frames: list[Frame], cursor: int, mode: str, factor: float,
                  now: float, timeline=build_timeline) -> tuple[list[Frame], int, float]:
    """
    Tek oynatma adimi (SAF: Streamlit bilmez). Donus: (kareler, yeni imlec, siradaki karenin zamani).
    Moda uyan yeni kare varsa imlec ona gecer ve bekleme baslar. Yoksa canli macta motor en cok MAX_ENGINE_STEPS
    adim (LiveMatch.tick) ilerletilir; duraklama / mac sonu olursa o ana kadarki her sey gosterilir (imlec sona).
    Motor YALNIZCA tick() ile ilerler: sonuc, hiz ve mod secimlerinden bagimsizdir.
    """
    j = next_index(frames, cursor, mode)
    if j is None and live is not None and not live.paused and not live.finished:
        for _ in range(MAX_ENGINE_STEPS):
            before = len(live.engine.events)
            live.tick()
            if len(live.engine.events) != before:
                frames = timeline(live.snapshot())
                j = next_index(frames, cursor, mode)
            if j is not None or live.paused or live.finished:
                break
        if live.paused or live.finished:
            return frames, len(frames) - 1, now
    if j is None:
        return frames, max(cursor, len(frames) - 1), now
    return frames, j, next_due(now, frames[j], factor)


# --- canli not (motorun _compute_ratings formulu, "mac sonu" yerine su anki dakika) ---

def live_rating(player, team_goals: int, opp_goals: int, now_minute: int) -> float | None:
    """
    MatchEngine._compute_ratings ile BIREBIR ayni formul (islem sirasi dahil). Yalnizca gorunen sayilar:
    gol, asist, isabetli sut, kurtaris, kart, oynanan dakika, kondisyon. Oynamamis oyuncu: None.
    Maç sonunda (now_minute = bitis dakikasi) motorun p.rating'ine esittir (parite testi, 200 tohum).
    """
    if not player.played:
        return None
    won, lost = team_goals > opp_goals, team_goals < opp_goals
    r = 6.0
    r += player.goals * 1.0 + player.assists * 0.5 + player.shots_on_target * 0.1 + player.saves * 0.2
    r -= player.yellow_cards * 0.3 + (1.5 if player.sent_off else 0)
    if opp_goals == 0 and player.role in (Position.GK, Position.DEF):
        r += 0.5
    r += 0.3 if won else (-0.3 if lost else 0)
    left = player.left_minute if player.left_minute is not None else now_minute
    played_min = left - (player.entered_minute or 0)
    if played_min < 20:
        r = 6.0 + (r - 6.0) * 0.5
    else:
        r -= fatigue_rating_penalty(player.energy)
    return round(max(1.0, min(10.0, r)), 1)


@dataclass(frozen=True)
class RatingRow:
    player_id: int
    name: str
    role: str
    status: str
    energy: int
    rating: float
    goals: int
    cards: str


def player_status(p, finished: bool) -> str:
    if p.sent_off:
        return "🟥 Atıldı"
    if p.injured:
        return "✚ Sakat"
    if p.substituted:
        return "Oyundan çıktı"
    if p.on_pitch:
        return "Oyunda"
    return "Tam maç" if finished and p.entered_minute == 0 else "Oynadı"


def rating_rows(team, team_goals: int, opp_goals: int, now_minute: int, finished: bool = False) -> list[RatingRow]:
    """Oynayan oyuncular: ilk 11 dizilis sirasinda (GK, DEF, MID, FWD), sonra giris sirasina gore yedekler."""
    played = [p for p in team.players if p.played]

    def order(p):
        starter = p.entered_minute == 0
        role = p.position if starter else (p.role or p.position)
        return (0 if starter else 1, ROLE_ORDER.index(role) if starter else p.entered_minute or 0, p.id)

    rows = []
    for p in sorted(played, key=order):
        rating = live_rating(p, team_goals, opp_goals, now_minute)
        rows.append(RatingRow(
            player_id=p.id, name=p.name, role=(p.role or p.position).value, status=player_status(p, finished),
            energy=round(p.energy), rating=rating if rating is not None else 6.0, goals=p.goals,
            cards="🟨" * min(p.yellow_cards, 2) + ("🟥" if p.sent_off and p.yellow_cards < 2 else ""),
        ))
    return rows


# --- canli puan durumu ---

@dataclass(frozen=True)
class TableRow:
    team_id: int
    name: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def table_rows(teams) -> list[TableRow]:
    """ORM takimlari (CareerManager.standings sirasinda) -> TableRow."""
    return [TableRow(team_id=t.id, name=t.name, played=t.played, won=t.won, drawn=t.drawn, lost=t.lost,
                     goals_for=t.goals_for, goals_against=t.goals_against, points=t.points) for t in teams]


def _apply_result(row: TableRow, scored: int, conceded: int) -> TableRow:
    won, drawn = scored > conceded, scored == conceded
    return replace(row, played=row.played + 1, won=row.won + won, drawn=row.drawn + drawn,
                   lost=row.lost + (not won and not drawn), goals_for=row.goals_for + scored,
                   goals_against=row.goals_against + conceded, points=row.points + (3 if won else 1 if drawn else 0))


def live_table(rows: list[TableRow], home_id: int, away_id: int, hs: int, as_: int) -> list[TableRow]:
    """
    Mac oncesi tabloya bu macin O ANKI skoru islenmis hali (diger maclar oynanmadi: hafta sonunda).
    Siralama career_manager.standings_key ile ayni: puan, averaj, atilan gol, ad.
    """
    out = []
    for row in rows:
        if row.team_id == home_id:
            row = _apply_result(row, hs, as_)
        elif row.team_id == away_id:
            row = _apply_result(row, as_, hs)
        out.append(row)
    return sorted(out, key=lambda r: (-r.points, -r.goal_difference, -r.goals_for, r.name))


# --- topla oynama: tek kaynak (MatchResult.possession_share) + "Son 5 dk" penceresi ---

def _sample_at(log, elapsed: float):
    found = None
    for sample in log:
        if sample[0] > elapsed:
            break
        found = sample
    return found


def window_share(log, elapsed: int, window: int = 5) -> tuple[int, int] | None:
    """Son `window` oyun dakikasindaki sahiplik payi (ev, deplasman); gunlukten sayac farki. Veri yoksa None."""
    end = _sample_at(log, elapsed)
    if end is None:
        return None
    start = _sample_at(log, elapsed - window) or (0, 0.0, 0.0)
    home, away = end[1] - start[1], end[2] - start[2]
    if home + away <= 0:
        return None
    share = round(100 * home / (home + away))
    return share, 100 - share


def share_at(log, elapsed: int) -> tuple[int, int] | None:
    """O dakikaya kadarki toplam pay (possession_share ile ayni formul, gecmis bir karede)."""
    end = _sample_at(log, elapsed)
    if end is None or end[1] + end[2] <= 0:
        return None
    share = round(100 * end[1] / (end[1] + end[2]))
    return share, 100 - share


def stats_possession(result: MatchResult, log, elapsed: int, at_head: bool) -> tuple[int, int] | None:
    """Istatistik satiri: canlida da mac sonunda da result.possession_share(); geri sarilmis karede gunlukten."""
    if at_head or not log:
        return result.possession_share()
    return share_at(log, elapsed) or result.possession_share()


def possession_bar(result: MatchResult, log, elapsed: int) -> tuple[str, tuple[int, int] | None]:
    """(etiket, pay): canli macta "Son 5 dk"; hazir sonuc izlenirken gunluk yok -> "Maç geneli"."""
    if log:
        return "Son 5 dk", window_share(log, elapsed)
    return "Maç geneli", result.possession_share()


# --- asistan notu (K12: yalnizca gorunen veriler) ---

@dataclass(frozen=True)
class AssistantNote:
    minute: int
    kind: str
    text: str


def _frames_until(frames: list[Frame], minute: int) -> list[Frame]:
    return [f for f in frames if (f.minute, f.added_time) <= (minute, 0) and f.phase != "Penaltılar"]


def assistant_note(result: MatchResult, frames: list[Frame], side: str | None, minute: int,
                   possession_log=None) -> AssistantNote | None:
    """
    Dakika `minute` itibariyla menajerin gorebildigi verilerden tek cumlelik not (oncelik sirasinda ilk uyan).
    Kaynaklar: gorunur kareler (skor, sut, isabet, korner, kart, rakibin taktik olayi), enerji gunlugu (kondisyon),
    topla oynama gunlugu. Sans kalitesi / gosterim olasiligi / gizli ozellik OKUNMAZ.
    """
    if side not in ("home", "away"):
        return None
    upto = _frames_until(frames, minute)
    if not upto:
        return None
    other = "away" if side == "home" else "home"
    now = upto[-1]
    before = _frames_until(frames, minute - 10)
    past = before[-1] if before else None
    ours, theirs = getattr(now, side), getattr(now, other)
    ours0 = getattr(past, side) if past else SideStats()
    theirs0 = getattr(past, other) if past else SideStats()
    our_recent, their_recent = ours.shots - ours0.shots, theirs.shots - theirs0.shots
    gf, ga = (now.home_score, now.away_score) if side == "home" else (now.away_score, now.home_score)
    team = result.home if side == "home" else result.away

    def note(kind: str, text: str) -> AssistantNote:
        return AssistantNote(minute, kind, text)

    tactic = next((f for f in reversed(upto) if f.event.type == "TACTICAL_CHANGE" and f.event.side == other
                   and f.minute > minute - 15), None)
    if tactic is not None:
        return note("rakip_taktik", f"Rakip {tactic.display_minute} taktiğini değiştirdi: {tactic.event.description}")
    if their_recent >= 3 and their_recent > our_recent:
        return note("baski", f"Rakip son 10 dakikada {their_recent} şut çekti; baskı altındayız.")
    tired = []
    for p in team.players:
        if p.entered_minute is None or p.entered_minute > minute or (p.left_minute is not None
                                                                     and p.left_minute <= minute):
            continue
        energy = energy_at(p, minute)
        if energy is not None and energy < 65:
            tired.append((energy, p.id, p))
    if tired:
        energy, _, p = min(tired)
        role = OWN_ROLE.get(p.role or p.position, "Oyuncumuz")
        return note("yorgun", f"{role} {p.name} çok yoruldu (kondisyon %{energy}).")
    if gf < ga and minute >= 60:
        return note("geride", f"{ga - gf} farkla gerideyiz; normal sürenin bitmesine {max(0, 90 - minute)} "
                              "dakika var.")
    if our_recent >= 3 and our_recent > their_recent:
        return note("firsat", f"Son 10 dakikada {our_recent} şut çektik; bu baskıyı sürdürelim.")
    if ours.shots >= 4 and ours.on_target == 0:
        return note("isabet", f"{ours.shots} şutumuzun hiçbiri kaleyi bulmadı.")
    if ours.yellow >= 2:
        return note("kart", f"{ours.yellow} sarı kartımız var; sertliği düşürmek gerekebilir.")
    if theirs.corners - ours.corners >= 3:
        return note("korner", f"Rakip {theirs.corners} korner kullandı; duran toplara dikkat.")
    if possession_log:
        elapsed = minute + (result.first_half_added if minute > 45 else 0)
        share = share_at(possession_log, elapsed)
        if share is not None:
            mine = share[0] if side == "home" else share[1]
            if mine <= 42:
                return note("topla", f"Topla oynamada %{mine}'de kaldık; orta saha rakipte.")
            if mine >= 58 and ours.shots <= theirs.shots:
                return note("topla", f"Topu %{mine} bizde tutuyoruz ama şut üretemiyoruz.")
    if gf > ga:
        return note("onde", f"Öndeyiz ({gf}-{ga}); oyunun kontrolünü bırakmayalım.")
    if gf < ga:
        return note("geride", f"{ga - gf} farkla gerideyiz; hâlâ vakit var.")
    return note("denge", f"Maç dengede: şutlar {ours.shots}-{theirs.shots}, isabetli {ours.on_target}-"
                         f"{theirs.on_target}.")


def latest_note_minute(frame: Frame | None) -> int | None:
    """Karenin dakikasina kadar ulasilan son asistan dakikasi (15/30/60/75)."""
    if frame is None:
        return None
    reached = [m for m in ASSISTANT_MINUTES if frame.minute >= m]
    return reached[-1] if reached else None


# --- talimatlar ---

def axis_value(inst: TeamInstructions, axis: Axis):
    value = getattr(inst, axis.field)
    return axis.options[value] if axis.options is not None else bool(value)


def instructions_from_values(values: Mapping[str, object], base: TeamInstructions) -> TeamInstructions:
    """Widget degerleri (etiket / bool, anahtar: Axis.key) -> TeamInstructions. Gecersiz deger: ValueError."""
    changes = {axis.field: values[axis.key] for axis in INSTRUCTION_AXES if axis.key in values}
    return base.with_changes(changes)


def shout_instructions(base: TeamInstructions, key: str) -> TeamInstructions:
    return base.with_changes(SHOUTS[key].changes)


def instruction_costs(inst: TeamInstructions) -> list[str]:
    """Secili talimatlarin bedeli: yalnizca etiket (sayi yok)."""
    out = []
    for axis in INSTRUCTION_AXES:
        cost = INSTRUCTION_COSTS.get((axis.field, getattr(inst, axis.field)))
        if cost:
            label = axis_value(inst, axis) if axis.options is not None else "açık"
            out.append(f"{axis.label} ({label}): {cost}")
    return out


# --- renkler ve metin parcalari ---

def ink_for(background: str) -> str:
    return INK_LIGHT if contrast_ratio(INK_LIGHT, background) >= contrast_ratio(INK_DARK, background) else INK_DARK


def club_colors(home: str, away: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """((ev zemin, ev yazi), (dep zemin, dep yazi)); crc32(ad) ile deterministik, iki takim ayni renk almaz."""
    n = len(CLUB_COLORS)
    h = zlib.crc32(str(home).encode("utf-8")) % n
    a = zlib.crc32(str(away).encode("utf-8")) % n
    if a == h:
        a = (a + 1) % n
    return (CLUB_COLORS[h], ink_for(CLUB_COLORS[h])), (CLUB_COLORS[a], ink_for(CLUB_COLORS[a]))


def _minute_text(frame: Frame) -> str:
    return frame.display_minute.rstrip("'")


def side_moments(frames: list[Frame]) -> dict[str, list[str]]:
    """Skorun altindaki satirlar: 'Pérez 29', '🟥 Ahmet 55', '✚ Mehmet 23' (her takimin kendi sutunu)."""
    out: dict[str, list[str]] = {"home": [], "away": []}
    for f in frames:
        ev = f.event
        if ev.type not in MOMENT_ICONS or ev.side not in out:
            continue
        who = pitch.short_name(ev.player, 18) if ev.player else (ev.team or "")
        pen = " (pen.)" if ev.type == "GOAL" and ev.detail == "penalty" else ""
        icon = MOMENT_ICONS[ev.type]
        out[ev.side].append(f"{icon + ' ' if icon else ''}{who} {_minute_text(f)}{pen}")
    return out


def competition_line(kind: str, title: str, week: int | None, result: MatchResult) -> str:
    """'Lig · 12. hafta' / 'Devler Arenası · Çeyrek final 2. maç · toplam 2-1' / 'Hazırlık maçı'."""
    if kind == "league":
        return f"Lig · {week}. hafta" if week else "Lig"
    base = title.rsplit(" · ", 1)[0] if " · " in (title or "") else (title or "")
    knockout = result.knockout
    if kind == "cup":
        base = base or "Kupa"
        if knockout is not None and (knockout.home_carry or knockout.away_carry):
            base += f" · toplam {result.home_aggregate}-{result.away_aggregate}"
        return base
    return (base or "Hazırlık maçı") + (" · eleme" if knockout is not None else "")


def chain_groups(frames: list[Frame]) -> list[list[Frame]]:
    """Ardisik ayni zincirli (chain_id) kareler tek pasaj."""
    groups: list[list[Frame]] = []
    for f in frames:
        cid = f.event.chain_id
        if cid is not None and groups and groups[-1][-1].event.chain_id == cid:
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def key_moments(frames: list[Frame]) -> list[tuple[int, str]]:
    """Zaman seridi: (kare konumu, etiket) -- gol, kirmizi, sakatlik, degisiklik, buyuk sans."""
    out = []
    for i, f in enumerate(frames):
        ev = f.event
        big_chance = ev.type in ("SAVE", "MISS") and ev.priority >= PRIORITY_HIGH
        if ev.type in ("GOAL", "RED_CARD", "INJURY", "SUBSTITUTION") or big_chance:
            who = pitch.short_name(ev.player, 14) if ev.player else (ev.team or "")
            out.append((i, f"{f.display_minute} {STRIP_ICONS[ev.type]} {who}"))
    return out


# --- HTML ---

MD_CSS = """
<style>
.md-band{border-radius:14px;overflow:hidden;border:1px solid var(--ofm-border,rgba(127,127,127,.35));
  background:var(--ofm-panel,#1e2538);margin:.2rem 0 .55rem}
.md-top{display:flex;align-items:center;gap:.55rem;padding:.45rem .8rem .35rem;flex-wrap:wrap}
.md-clock{font-size:2.3rem;font-weight:800;line-height:1;font-variant-numeric:tabular-nums;color:var(--ofm-text,#fff)}
.md-phase{font-size:.8rem;font-weight:700;padding:.12rem .55rem;border-radius:999px;color:var(--ofm-text,#fff);
  background:var(--ofm-panel-alt,rgba(127,127,127,.2));border:1px solid var(--ofm-border,rgba(127,127,127,.35))}
.md-comp{margin-left:auto;font-size:.85rem;color:var(--ofm-muted,#aeb6c8);text-align:right}
.md-row{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)}
.md-team{display:flex;align-items:center;padding:.6rem .8rem;font-size:1.3rem;font-weight:800;line-height:1.15;
  overflow-wrap:anywhere;min-width:0}
.md-team.home{justify-content:flex-end;text-align:right}
.md-score{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:.2rem .9rem;
  background:#0b0f18;color:#ffffff;font-size:2.4rem;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.md-score .x{font-size:.72rem;font-weight:700;color:#ffd60a;margin-top:.2rem}
.md-events{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:.8rem;padding:.4rem .8rem .5rem;
  font-size:.86rem;color:var(--ofm-text,#fff);min-height:1.2rem}
.md-events .home{text-align:right}.md-events span{display:inline-block}
.md-prog{height:4px;background:rgba(127,127,127,.25)}.md-prog>span{display:block;height:100%;
  background:var(--ofm-accent,#FFCD00)}
.md-banner{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.1rem 0 .55rem;padding:.85rem 1rem;
  border-radius:12px;background:var(--md-bg,var(--ofm-panel-alt,#242b3d));color:var(--md-fg,var(--ofm-text,#fff));
  border:1px solid var(--ofm-border,rgba(127,127,127,.35));min-height:3.6rem}
.md-banner .m{font-weight:800;font-variant-numeric:tabular-nums}
.md-banner .t{font-size:.72rem;font-weight:800;letter-spacing:.04em;padding:.1rem .45rem;border-radius:6px;
  background:rgba(0,0,0,.24);color:inherit}
.md-banner .d{flex:1 1 14rem;font-size:1.15rem;font-weight:700;line-height:1.3;min-width:0;overflow-wrap:anywhere}
.md-banner.goal{--md-bg:#ffd60a;--md-fg:#1a1a1a;animation:mdGoal .45s ease-in-out 6 alternate}
.md-banner.red{--md-bg:#c62828;--md-fg:#ffffff}
@keyframes mdGoal{from{filter:none}to{filter:brightness(1.3);box-shadow:0 0 0 5px rgba(255,214,10,.45)}}
.md-recent{display:flex;flex-direction:column;gap:.2rem;margin:0 0 .5rem}
.md-recent div{font-size:.84rem;color:var(--ofm-muted,#aeb6c8);display:flex;gap:.5rem;min-width:0}
.md-recent b{color:var(--ofm-text,#fff);font-variant-numeric:tabular-nums;flex:0 0 3rem}
.md-recent span{overflow-wrap:anywhere;min-width:0}
.md-note{margin:.1rem 0 .55rem;padding:.5rem .75rem;border-radius:10px;border-left:4px solid var(--ofm-accent,#FFCD00);
  background:var(--ofm-panel-alt,rgba(127,127,127,.12));font-size:.9rem;color:var(--ofm-text,#fff)}
.md-poss{margin:.2rem 0 .6rem}
.md-poss .l{font-size:.78rem;font-weight:700;color:var(--ofm-muted,#aeb6c8);letter-spacing:.04em}
.md-poss .bar{display:flex;height:12px;border-radius:6px;overflow:hidden;margin:.2rem 0;
  border:1px solid var(--ofm-border,rgba(127,127,127,.35));background:rgba(127,127,127,.2)}
.md-poss .v{display:flex;justify-content:space-between;font-weight:800;font-variant-numeric:tabular-nums;
  font-size:.9rem;color:var(--ofm-text,#fff)}
.md-feed{display:flex;flex-direction:column;gap:.5rem;max-height:430px;overflow-y:auto;overflow-x:hidden;
  padding-right:.2rem}
.md-passage{display:flex;flex-direction:column;gap:.3rem;border-left:3px solid var(--ofm-border,rgba(127,127,127,.35));
  padding-left:.4rem}
.md-feed .cm-ev{grid-template-columns:3rem minmax(0,5.6rem) minmax(0,1fr)}
.md-feed .cm-ev span{overflow-wrap:anywhere;min-width:0}
.md-rt{width:100%;border-collapse:collapse;font-size:.85rem;table-layout:auto}
.md-rt th{text-align:left;font-weight:600;color:var(--ofm-muted,#aeb6c8);padding:.25rem .3rem;
  border-bottom:1px solid var(--ofm-border,rgba(127,127,127,.3))}
.md-rt td{padding:.22rem .3rem;border-bottom:1px solid rgba(127,127,127,.14);color:var(--ofm-text,#fff);
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.md-rt td.r{text-align:center;font-weight:800;border-radius:6px}
.md-rt td.r.hi{background:#2e7d32;color:#ffffff}.md-rt td.r.lo{background:#c62828;color:#ffffff}
.md-rt tr.off td{opacity:.7}
.md-rt .cm-cond{min-width:4.2rem}
.md-live{display:inline-block;margin-left:.4rem;padding:.05rem .45rem;border-radius:999px;background:#c62828;
  color:#ffffff;font-size:.72rem;font-weight:800;letter-spacing:.05em;vertical-align:middle}
.md-rt tr.me td{font-weight:800}
.md-report{display:flex;flex-direction:column;gap:.5rem;color:var(--ofm-text,#fff)}
.md-report .h{font-size:1.1rem;font-weight:800}
.md-report p{margin:0;line-height:1.45}
.md-board{margin:0 0 .4rem}
.st-key-md_timer{display:none}
@media (max-width:640px){
  .md-clock{font-size:1.7rem}.md-team{font-size:1rem;padding:.5rem .55rem}
  .md-score{font-size:1.8rem;padding:.2rem .55rem}.md-events{font-size:.78rem;gap:.5rem;padding:.35rem .5rem}
  .md-banner{padding:.7rem .75rem}.md-banner .d{font-size:1rem}.md-comp{margin-left:0;text-align:left;width:100%}
  .md-rt{font-size:.78rem}.md-rt .cm-cond{min-width:3.4rem}.md-rt .cm-cond .val{width:2.2rem}
}
@media (prefers-reduced-motion:reduce){.md-banner,.md-board *{animation:none !important}}
</style>
"""


def band_html(home: str, away: str, colors, *, score: tuple[int, int], clock: str, phase: str, comp: str,
              moments: Mapping[str, list[str]], extra: str = "", progress: float | None = None) -> str:
    """Skor bandi: dakika + evre + yarisma satiri, kulup renginde takim kutulari, skor, olay satirlari."""
    (hb, hf), (ab, af) = colors

    def events(side: str) -> str:
        items = moments.get(side) or []
        return ", ".join(f"<span>{escape(item)}</span>" for item in items) or "&nbsp;"

    bar = ""
    if progress is not None:
        bar = f'<div class="md-prog"><span style="width:{max(0.0, min(1.0, progress)) * 100:.1f}%"></span></div>'
    x = f'<span class="x">{escape(extra)}</span>' if extra else ""
    return (
        '<div class="md-band">'
        f'<div class="md-top"><span class="md-clock">{escape(clock)}</span><span class="md-phase">{escape(phase)}</span>'
        f'<span class="md-comp">{escape(comp)}</span></div>'
        '<div class="md-row">'
        f'<div class="md-team home" style="background:{hb};color:{hf}">{escape(home)}</div>'
        f'<div class="md-score"><span>{int(score[0])} - {int(score[1])}</span>{x}</div>'
        f'<div class="md-team away" style="background:{ab};color:{af}">{escape(away)}</div>'
        '</div>'
        f'<div class="md-events"><div class="home">{events("home")}</div><div class="away">{events("away")}</div></div>'
        f"{bar}</div>"
    )


def extra_text(frame: Frame | None) -> str:
    if frame is None:
        return ""
    parts = []
    if frame.extra_time:
        parts.append("uzt.")
    if frame.home_penalties is not None and frame.away_penalties is not None:
        parts.append(f"pen. {frame.home_penalties}-{frame.away_penalties}")
    return " · ".join(parts)


def banner_html(frame: Frame | None, colors) -> str:
    """Tek buyuk yorum afisi. Golde sari + yanip sonme, kirmizi kartta kirmizi; diger olaylar takim renginde."""
    if frame is None:
        return '<div class="md-banner"><span class="d">Başlama düdüğü bekleniyor…</span></div>'
    ev = frame.event
    cls = {"goal": " goal", "red": " red"}.get(ev.highlight, "")
    style = ""
    if not cls and ev.side in ("home", "away"):
        bg, fg = colors[0] if ev.side == "home" else colors[1]
        style = f' style="--md-bg:{bg};--md-fg:{fg}"'
    return (f'<div class="md-banner{cls}"{style} data-frame="{frame.index}" data-type="{escape(ev.type)}" '
            f'data-dwell="{int(frame.dwell_ms)}">'
            f'<span class="m">{escape(frame.display_minute)}</span><span class="t">{escape(ev.label)}</span>'
            f'<span class="d">{escape(ev.description)}</span></div>')


def recent_html(frames: list[Frame], index: int, limit: int = 3) -> str:
    """Afisin altinda son 3 onemli an (priority >= PRIORITY_MAIN), afisteki kareden onceki."""
    picked = []
    for j in range(min(index, len(frames)) - 1, -1, -1):
        f = frames[j]
        if f.event.priority >= PRIORITY_MAIN or f.event.type in KEY_TYPES:
            picked.append(f)
            if len(picked) == limit:
                break
    if not picked:
        return ""
    rows = "".join(f"<div><b>{escape(f.display_minute)}</b><span>{escape(f.event.description)}</span></div>"
                   for f in picked)
    return f'<div class="md-recent">{rows}</div>'


def note_html(note: AssistantNote | None) -> str:
    if note is None:
        return ""
    return f'<div class="md-note">🗒️ <b>Asistan ({note.minute}\')</b> {escape(note.text)}</div>'


def possession_html(label: str, share: tuple[int, int] | None, colors) -> str:
    (hb, _), (ab, _) = colors
    if share is None:
        return (f'<div class="md-poss"><div class="l">{escape(label)} · topla oynama</div><div class="bar"></div>'
                '<div class="v"><span>—</span><span>—</span></div></div>')
    h, a = share
    return (f'<div class="md-poss"><div class="l">{escape(label)} · topla oynama</div>'
            f'<div class="bar"><span style="width:{h}%;background:{hb}"></span>'
            f'<span style="width:{a}%;background:{ab}"></span></div>'
            f'<div class="v"><span>%{h}</span><span>%{a}</span></div></div>')


def passages_html(frames: list[Frame]) -> str:
    """Mac ozeti akisi: zincirler tek pasaj, en yeni ustte."""
    groups = chain_groups(frames)
    blocks = []
    for n, group in enumerate(reversed(groups)):
        rows = "".join(event_html(f, latest=(n == 0 and i == len(group) - 1)) for i, f in enumerate(group))
        blocks.append(f'<div class="md-passage">{rows}</div>' if len(group) > 1 else rows)
    return f'<div class="md-feed">{"".join(blocks)}</div>'


def text_feed_html(frames: list[Frame], limit: int = 14) -> str:
    """"Sadece metin" modu: afis ve tahta yerine akis (en yeni ustte)."""
    recent = list(reversed(frames[-limit:]))
    return f'<div class="md-feed">{"".join(event_html(f, latest=(i == 0)) for i, f in enumerate(recent))}</div>'


def ratings_html(team_name: str, rows: list[RatingRow]) -> str:
    head = "<tr><th>Oyuncu</th><th>Görev</th><th>Durum</th><th>Kondisyon</th><th>Not</th></tr>"
    body = []
    for r in rows:
        cls = "hi" if r.rating >= 7.5 else "lo" if r.rating <= 5.5 else ""
        off = ' class="off"' if r.status not in ("Oyunda", "Tam maç") else ""
        goals = " ⚽" * min(r.goals, 3)
        body.append(f"<tr{off}><td>{escape(r.name)}{goals}{(' ' + r.cards) if r.cards else ''}</td>"
                    f"<td>{escape(r.role)}</td><td>{escape(r.status)}</td>"
                    f"<td>{condition_bar_html(r.energy, condition_band(r.energy))}</td>"
                    f'<td class="r {cls}">{r.rating:.1f}</td></tr>')
    return (f'<div class="md-ratings"><b>{escape(team_name)}</b>'
            f'<table class="md-rt">{head}{"".join(body)}</table></div>')


def table_html(rows: list[TableRow], highlight: set[int], live: bool) -> str:
    head = "<tr><th>#</th><th>Takım</th><th>O</th><th>G</th><th>B</th><th>M</th><th>Av</th><th>P</th></tr>"
    body = "".join(
        f'<tr{" class=me" if r.team_id in highlight else ""}><td>{i}</td><td>{escape(r.name)}</td><td>{r.played}</td>'
        f"<td>{r.won}</td><td>{r.drawn}</td><td>{r.lost}</td><td>{r.goal_difference:+d}</td><td>{r.points}</td></tr>"
        for i, r in enumerate(rows, start=1))
    badge = '<span class="md-live">CANLI</span>' if live else ""
    return f'<div><b>Puan durumu</b>{badge}<table class="md-rt">{head}{body}</table></div>'


def report_html(result: MatchResult) -> str:
    report = match_report(result)
    paragraphs = "".join(f"<p>{escape(p)}</p>" for p in report.paragraphs)
    return f'<div class="md-report"><div class="h">{escape(report.headline)}</div>{paragraphs}</div>'


# ===========================================================================
# [3] OTURUM DURUMU
# ===========================================================================

@dataclass
class Replay:
    """Hazir sonucun oynatilmasi (izleme / "Son maçımı izle"): ayni oynatici, motor yok."""
    result: MatchResult
    title: str
    mode: str                        # LIVE_FRIENDLY / LIVE_REPLAY (secili mod degisince kapanir)
    caption: str = ""
    side: str | None = None          # menajerin tarafi (asistan notu); izlemede None
    competition: str = "friendly"    # "league" / "cup" / "friendly"
    paused: bool = False
    frames: list[Frame] = field(default_factory=list, repr=False)


def current_live() -> LiveMatch | None:
    return st.session_state.get(LIVE_KEY)


def current_replay() -> Replay | None:
    return st.session_state.get(REPLAY_KEY)


def _reset_playback() -> None:
    ss = st.session_state
    for key in PLAYBACK_KEYS:
        ss.pop(key, None)
    ss[CURSOR_KEY] = -1
    ss[DUE_KEY] = 0.0


def current_speed() -> float:
    return speed_factor(st.session_state.get("live_speed", DEFAULT_SPEED))


def current_mode() -> str:
    mode = st.session_state.get(MODE_KEY, MODE_FULL)
    return mode if mode in SUMMARY_MODES else MODE_FULL


def _timeline(live: LiveMatch | None, replay: Replay | None) -> tuple[MatchResult, list[Frame]]:
    """Gosterilen sonuc ve gorunur kareler (olay sayisina gore onbellekli; oturum basina tek kopya)."""
    if live is None:
        if not replay.frames:
            replay.frames = build_timeline(replay.result)
        return replay.result, replay.frames
    result = live.snapshot()
    key = (id(live), result.seed, result.home.id, result.away.id, len(result.events), live.finished)
    memo = st.session_state.get(FRAMES_KEY)
    if memo is not None and memo[0] == key:
        return result, memo[1]
    frames = build_timeline(result)
    st.session_state[FRAMES_KEY] = (key, frames)
    return result, frames


def _to_head(live: LiveMatch | None = None, replay: Replay | None = None) -> None:
    """Imlec son kareye (duraklatma / sonucu gor / aninda). Callback'te ya da widget'lardan ONCE cagrilir."""
    ss = st.session_state
    if live is not None:
        frames = build_timeline(live.snapshot())
    elif replay is not None:
        frames = _timeline(None, replay)[1]
    else:
        return
    ss[CURSOR_KEY] = len(frames) - 1
    ss[DUE_KEY] = 0.0
    ss[VIEW_KEY] = None
    ss[REWIND_KEY] = None


def _running(live: LiveMatch | None, replay: Replay | None) -> bool:
    if live is not None:
        return not live.paused and not live.finished
    if replay is not None:
        frames = _timeline(None, replay)[1]
        return not replay.paused and st.session_state.get(CURSOR_KEY, -1) < len(frames) - 1
    return False


def _replay_finished(replay: Replay) -> bool:
    return st.session_state.get(CURSOR_KEY, -1) >= len(_timeline(None, replay)[1]) - 1


def store_week_report(report) -> None:
    """Hafta (ya da yalnizca hafta ici kupa) raporunu sayfalarin okuyacagi anahtarlara yazar (web_app de kullanir)."""
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


def live_setup_values() -> dict:
    """Mac ayarlari expander'inin degerleri (widget cizilmediyse varsayilanlar)."""
    ss = st.session_state
    rule_label = ss.get("live_rule", RULE_OPTIONS[0])
    rule = next(r for r, label in SUB_RULE_LABELS.items() if label == rule_label)
    values = {
        "sub_rule": rule,
        "instructions": TeamInstructions(
            parse_mentality(ss.get("live_start_mentality", MENTALITY_OPTIONS[1])),
            parse_tackling(ss.get("live_start_tackling", TACKLING_OPTIONS[1])),
        ),
        "use_saved": ss.get("live_use_saved", True),
        "auto_subs": ss.get("live_auto_subs", True),
        "pause_at_breaks": ss.get("live_pause_breaks", True),
        "pause_on_key_events": ss.get("live_pause_events", True),
    }
    for name, (key, _label) in PAUSE_TOGGLES.items():
        values[name] = bool(ss.get(key, AUTO_PAUSE_DEFAULTS[name]))
    return values


def _pause_kwargs(setup: dict) -> dict:
    names = ("pause_at_breaks", "pause_on_key_events", *PAUSE_TOGGLES)
    return {name: setup[name] for name in names}


def _sync_instruction_widgets(live: LiveMatch, force: bool = False) -> None:
    """Talimat widget'lari motordaki talimatla ayni olsun (plan / bagiris degistirdiyse). Widget'lardan ONCE."""
    if live.managed_team is None:
        return
    inst = live.instructions
    ss = st.session_state
    if not force and ss.get(SYNC_KEY) == inst:
        return
    for axis in INSTRUCTION_AXES:
        ss[axis.key] = axis_value(inst, axis)
    ss[SYNC_KEY] = inst


def begin_live(live: LiveMatch) -> None:
    ss = st.session_state
    ss[LIVE_KEY] = live
    ss.pop(REPLAY_KEY, None)
    _reset_playback()
    ss["live_formation"] = live.formation if live.formation in MATCH_FORMATIONS else "4-4-2"
    _sync_instruction_widgets(live, force=True)
    reset_widgets("live_sub_out", "live_sub_in", "live_sub_role")


def _begin_replay(replay: Replay) -> None:
    st.session_state[REPLAY_KEY] = replay
    _reset_playback()
    if current_speed() == 0:
        _to_head(replay=replay)


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


def live_is_stale(live: LiveMatch) -> bool:
    """Kaydedilmemis kariyer maci artik gecersiz mi (hafta ilerledi / fikstur baska yoldan oynandi)."""
    if not live.is_fixture or live.saved:
        return False
    with session_scope() as db:
        cm = manager(db)
        fx = db.get(Fixture, live.fixture_id)
        return fx is None or fx.is_played or cm.season != live.season or cm.current_week != live.week


def competitive_live_ok(cm) -> bool:
    """
    Resmi (lig / kupa) mac canli oynanabilir mi? Faz 12: cm.live_allowed() (kural acik + dunyada tek menajer) VE
    paylasilan dunya degil: paylasilan dunyada hafta yalnizca tur motoruyla ilerler. Hazirlik maci her zaman canli.
    """
    return cm.live_allowed() and not cm.rules.shared


# ===========================================================================
# [4] CALLBACK'LER (hepsi member_callback: oturum + dunya uyeligi + paylasilan dunyada kilit)
# ===========================================================================

@member_callback
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
        season=prep.season, week=prep.week, title=prep.title, sub_rule=setup["sub_rule"], **_pause_kwargs(setup),
    )
    begin_live(live)


@member_callback
def cb_live_start() -> None:
    """Hazirlik maci (yonetilen ya da yalnizca izleme) veya "Son maçımı izle"."""
    ss = st.session_state
    mode = ss.get("live_mode", LIVE_MANAGE)
    if mode == LIVE_REPLAY:
        _start_replay()
        return
    if mode != LIVE_FRIENDLY:
        return
    home, away = ss.get("live_home"), ss.get("live_away")
    if not home or not away:
        return
    if home == away:
        flash("live", "error", "Bir takım kendisiyle oynayamaz.")
        return
    side = ss.get("live_side", SIDE_LABELS[0])
    seed, knockout = parse_seed(ss.get("live_seed", "")), bool(ss.get("live_knockout", False))
    title = f"Hazırlık maçı · {home} - {away}"
    if side == SIDE_WATCH:
        with session_scope() as db:
            result = friendly_result(db, home, away, seed, knockout)
        _begin_replay(Replay(result, title=title, mode=LIVE_FRIENDLY,
                             caption=FRIENDLY_CAPTION + (KNOCKOUT_CAPTION if knockout else "")))
        return
    setup = live_setup_values()
    with session_scope() as db:
        engine = friendly_engine(db, home, away, seed, knockout, engine_config_for(setup["sub_rule"]))
    live = LiveMatch.create(
        engine, engine.home.id if side == SIDE_LABELS[0] else engine.away.id,
        instructions=setup["instructions"], auto_subs=setup["auto_subs"], title=title, sub_rule=setup["sub_rule"],
        **_pause_kwargs(setup),
    )
    begin_live(live)


def recorded_matches() -> dict[str, MatchResult]:
    ss = st.session_state
    return {label: ss.get(key) for label, key in (("Lig maçı", "last_user_result"),
                                                  ("Devler Arenası maçı", "last_user_cup_result"))
            if ss.get(key) is not None}


def _start_replay() -> None:
    recorded = recorded_matches()
    if not recorded:
        flash("live", "warning", "Henüz izlenecek maç yok. Haftayı menüdeki **⏭️ Devam** düğmesiyle oyna.")
        return
    choice = st.session_state.get("live_which")
    label = choice if choice in recorded else next(iter(recorded))
    result = recorded[label]
    side = None
    with session_scope() as db:
        team = manager(db).user_team
        if team is not None:
            side = "home" if team.id == result.home.id else "away" if team.id == result.away.id else None
    competition = "cup" if label.startswith("Devler") else "league"
    _begin_replay(Replay(result, title=f"{label} · tekrar", mode=LIVE_REPLAY, side=side, competition=competition))


@member_callback
def cb_live_pause() -> None:
    live, replay = current_live(), current_replay()
    if live is not None:
        live.pause()
        _to_head(live=live)
    elif replay is not None:
        replay.paused = True


@member_callback
def cb_live_resume() -> None:
    live, replay = current_live(), current_replay()
    ss = st.session_state
    if live is not None:
        live.resume()
    elif replay is not None:
        replay.paused = False
    ss[VIEW_KEY] = None
    ss[REWIND_KEY] = None
    ss[DUE_KEY] = 0.0


@member_callback
def cb_live_finish() -> None:
    live, replay = current_live(), current_replay()
    if live is not None:
        if not live.finished:
            live.play_to_end()
        _to_head(live=live)
    elif replay is not None:
        replay.paused = False
        _to_head(replay=replay)


@member_callback
def cb_live_close() -> None:
    ss = st.session_state
    live = current_live()
    if live is None:
        ss.pop(REPLAY_KEY, None)
        _reset_playback()
        return
    if live.is_fixture and not live.saved and not live_is_stale(live):
        flash("live", "error", "Kariyer maçını kapatmadan önce sonucu kaydet.")
        return
    ss.pop(LIVE_KEY, None)
    _reset_playback()
    reset_widgets("live_sub_out", "live_sub_in", "live_sub_role", "live_formation",
                  *(axis.key for axis in INSTRUCTION_AXES))


def _apply_instructions(live: LiveMatch, target: TeamInstructions, prefix: str = "📋") -> None:
    try:
        event = live.set_team_instructions(target)
    except InterventionError as exc:
        flash("live", "error", str(exc))
        return
    _sync_instruction_widgets(live, force=True)
    if event is not None:
        flash("live", "info", f"{prefix} {event.description}")
    else:
        flash("live", "info", f"{prefix} Talimatlar zaten bu yönde.")


@member_callback
def cb_live_instructions() -> None:
    live = current_live()
    if live is None or live.managed_team is None:
        return
    ss = st.session_state
    values = {axis.key: ss.get(axis.key, axis_value(live.instructions, axis)) for axis in INSTRUCTION_AXES}
    try:
        event = live.set_team_instructions(instructions_from_values(values, live.instructions))
    except (ValueError, InterventionError) as exc:
        ss[SYNC_KEY] = None                       # widget'lar bir sonraki cizimde motordaki talimata doner
        flash("live", "error", str(exc))
        return
    ss[SYNC_KEY] = live.instructions
    if event is not None:
        flash("live", "info", f"📋 {event.description}")


@member_callback
def cb_live_shout(key: str) -> None:
    live = current_live()
    if live is None or live.managed_team is None or key not in SHOUTS:
        return
    _apply_instructions(live, shout_instructions(live.instructions, key), prefix=SHOUTS[key].label + ":")


@member_callback
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


@member_callback
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


@member_callback
def cb_live_prepare_sub(player_id: int) -> None:
    """Duraklama eylemi: yorulan oyuncuyu 'Çıkan' kutusuna koyar (degisikligi menajer onaylar)."""
    st.session_state["live_sub_out"] = player_id


@member_callback
def cb_live_save() -> None:
    live = current_live()
    if live is None or not live.is_fixture or live.saved or not live.finished:
        return
    with session_scope() as db:
        cm = manager(db)
        if cm.rules.shared:                       # Faz 12: sonucu kaydetmek haftayi tur motoru disinda ilerletirdi
            flash("live", "error", SHARED_LIVE_TEXT)
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
    st.session_state.pop(TABLE_KEY, None)
    store_week_report(report)
    if report.midweek_only:
        text = "✅ Devler Arenası maçın kaydedildi."
        if next_match is not None:
            text += " Bu hafta lig maçın da var: maçı kapatıp **Maçımı yönet** ile canlı oynayabilirsin."
        else:
            text += " Haftanın lig maçları menüdeki **⏭️ Devam** düğmesiyle oynatılabilir."
        flash("live", "success", text)
    else:
        flash("live", "success", f"✅ Sonuç kaydedildi, {report.week}. hafta tamamlandı.")
    reset_widgets("tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee")


@member_callback
def cb_md_rewind() -> None:
    """Zaman seridi: goruntu secilen kareye (motor ilerlemez)."""
    st.session_state[VIEW_KEY] = st.session_state.get(REWIND_KEY)


@member_callback
def cb_md_live_head() -> None:
    st.session_state[VIEW_KEY] = None
    st.session_state[REWIND_KEY] = None


@requires_auth
def cb_md_tick() -> None:
    """
    Istemci zamanlayicisinin tetigi. Bilerek BOS: ilerleme parcanin kendisinde (_match_view). member_callback degil,
    requires_auth: saniyede bir gelen tetik paylasilan dunyada kilit almasin / koltuga yazmasin.
    """


# ===========================================================================
# [5] CIZIM: oynatma parcalari
# ===========================================================================

def _advance(live: LiveMatch | None, replay: Replay | None, mode: str, factor: float) -> bool:
    """Bir oynatma adimi; durum degisti mi (duraklama / mac sonu / tekrar sonu) -> tum sayfa yeniden cizilsin."""
    ss = st.session_state
    _result, frames = _timeline(live, replay)
    cursor = ss.get(CURSOR_KEY, -1)
    now = time.monotonic()
    due = ss.get(DUE_KEY, 0.0)
    if 0.0 <= now - due < DRIFT_WINDOW_S:
        now = due          # kaymasiz zamanlama: bekleme bir onceki karenin vaktinden sayilir (parca adimi yuvarlamasi)
    if live is not None:
        before = (live.paused, live.finished)
        _frames, cursor, due = step_playback(live, frames, cursor, mode, factor, now)
        ss[CURSOR_KEY], ss[DUE_KEY] = cursor, due
        return (live.paused, live.finished) != before
    was_end = cursor >= len(frames) - 1
    _frames, cursor, due = step_playback(None, frames, cursor, mode, factor, now)
    ss[CURSOR_KEY], ss[DUE_KEY] = cursor, due
    return not was_end and cursor >= len(frames) - 1


def _display_index(frames: list[Frame]) -> int:
    ss = st.session_state
    view = ss.get(VIEW_KEY)
    if isinstance(view, int) and 0 <= view < len(frames):
        return view
    return min(ss.get(CURSOR_KEY, -1), len(frames) - 1)


def _side_of(live: LiveMatch | None, replay: Replay | None, result: MatchResult) -> str | None:
    if live is not None:
        team = live.managed_team
        if team is None:
            return None
        return "home" if team is result.home or team.id == result.home.id else "away"
    return replay.side if replay is not None else None


def _context(live: LiveMatch | None, replay: Replay | None):
    """Cizim baglami: sonuc, kareler, gosterilen indeks, bas konumda mi, renkler."""
    result, frames = _timeline(live, replay)
    index = _display_index(frames)
    rewound = st.session_state.get(VIEW_KEY) is not None
    at_head = not rewound and index >= len(frames) - 1
    colors = club_colors(result.home.name, result.away.name)
    return result, frames, index, at_head, colors


def _block(html: str) -> None:
    """
    HTML parcasi. Hep st.markdown(unsafe_allow_html): st.html'in temizleyicisi SVG'yi (sekil tahtasi) tamamen siliyor
    (olculdu: bos <div class="md-board">), ayrica AppTest ve metin aramalari markdown ogelerini okur.
    """
    st.markdown(html, unsafe_allow_html=True)


def _ticking(live: LiveMatch | None, replay: Replay | None) -> bool:
    return current_speed() > 0 and _running(live, replay)


def _draw_main(live: LiveMatch | None, replay: Replay | None, mode: str) -> None:
    result, frames, index, at_head, colors = _context(live, replay)
    frame = frames[index] if index >= 0 else None
    ss = st.session_state
    if live is not None and at_head and not live.finished:
        clock, phase = live.clock, live.phase_label
        progress = live.progress
    elif frame is not None:
        clock, phase = frame.display_minute, frame.phase
        progress = min(1.0, frame.elapsed / max(1, result.total_minutes if (live is None or live.finished)
                                                else live.expected_total))
    else:
        clock, phase, progress = "0'", PHASE_PRE_MATCH, 0.0
    if live is not None:
        comp = competition_line(live.competition, live.title, live.week, result)
    else:
        comp = competition_line(replay.competition, replay.title, None, result)
    score = (frame.home_score, frame.away_score) if frame is not None else (0, 0)
    _block(band_html(result.home.name, result.away.name, colors, score=score, clock=clock, phase=phase, comp=comp,
                     moments=side_moments(frames[: index + 1]), extra=extra_text(frame), progress=progress))

    log = live.possession_log if live is not None else None
    elapsed = (live.elapsed if (live is not None and at_head) else frame.elapsed if frame is not None else 0)
    label, share = possession_bar(result, log, elapsed)
    side = _side_of(live, replay, result)
    note_minute = latest_note_minute(frame)
    note = (assistant_note(result, frames[: index + 1], side, note_minute, log)
            if note_minute is not None and side is not None else None)
    if mode == MODE_TEXT:
        _block(note_html(note) + text_feed_html(frames[: index + 1]))
        _block(possession_html(label, share, colors))
        return
    shown = last_in_mode(frames, index, mode) if index >= 0 else None
    _block(banner_html(frames[shown] if shown is not None else None, colors))
    show_board = bool(ss.get("live_pitch", True)) and index >= 0
    left, right = st.columns([3, 2], gap="medium") if show_board else (st.container(), None)
    with left:
        _block(recent_html(frames, shown if shown is not None else index) + note_html(note)
               + possession_html(label, share, colors))
    if right is not None:
        with right:
            board = pitch.build_board(result, frames, index)
            _block(pitch.board_svg(board, colors[0][0], colors[1][0]))


def _match_view() -> None:
    """
    MAC GUNU PARCASI (st.fragment): bant, afis, tahta, "Son 5 dk", kontrol satiri, duraklama notu, zaman seridi ve
    sekmeler. Vakti geldiyse bir kare ilerler (motor gerekirse LiveMatch.tick ile). Bir sonraki karenin zamani
    istemci zamanlayicisina (_schedule_timer) verilir: parca kare basina BIR kez calisir, durakken hic calismaz.
    DURDUR / DEVAM gibi dugmeler parcanin icindedir: tiklama yalnizca parcayi yeniden cizer (hizli); durum degistiyse
    (duraklama, mac sonu, kayit, kapatma) mudahale paneli ve mac sonu ozeti icin tum sayfa bir kez yeniden cizilir.
    """
    live, replay = current_live(), current_replay()
    ss = st.session_state
    if live is None and replay is None:
        if ss.get(CONTROL_KEY) is not None:
            ss[CONTROL_KEY] = None
            st.rerun(scope="app")
        return
    mode, factor = current_mode(), current_speed()
    if (factor > 0 and ss.get(VIEW_KEY) is None and _running(live, replay)
            and time.monotonic() >= ss.get(DUE_KEY, 0.0)):
        _advance(live, replay, mode, factor)
    _draw_main(live, replay, mode)
    _schedule_timer(live, replay)
    control_row(live, replay, bool(ss.get(STALE_KEY, False)))
    if live is not None:
        pause_notice(live)
    frames = _timeline(live, replay)[1]
    if live is not None and (live.paused or live.finished):
        rewind_strip(frames, len(frames) - 1)
    elif replay is not None and (replay.paused or _replay_finished(replay)):
        rewind_strip(frames, len(frames) - 1 if _replay_finished(replay) else max(0, ss.get(CURSOR_KEY, 0)))
    _draw_tabs(live, replay, mode)
    state = _control_state(live, replay)
    previous = ss.get(CONTROL_KEY)
    if previous != state:
        ss[CONTROL_KEY] = state
        if _page_needs_redraw(previous, state):
            st.rerun(scope="app")


def _page_needs_redraw(previous: tuple | None, state: tuple | None) -> bool:
    """
    Parcanin disi (mudahale paneli, mac sonu ozeti, kurulum ekrani) yeniden cizilmeli mi? Yalnizca DEVAM (durak ->
    akiyor, baska hicbir sey degismedi) tum sayfayi yeniden cizdirmez: tiklama tek parca calismasiyla sonuclanir.
    """
    if previous is None or state is None or previous[:2] != state[:2]:
        return True
    resumed = previous[2] is True and state[2] is False
    return not (resumed and previous[3:] == state[3:])


def _league_table(live: LiveMatch) -> list[TableRow] | None:
    """Lig macinin puan durumu (mac oncesi hali; kaydedildiyse guncel), oturumda onbellekli."""
    ss = st.session_state
    key = (live.fixture_id, live.saved)
    cached = ss.get(TABLE_KEY)
    if cached is not None and cached[0] == key:
        return cached[1]
    with session_scope() as db:
        cm = manager(db)
        fx = db.get(Fixture, live.fixture_id)
        league_id = getattr(fx, "league_id", None) if fx is not None else None
        rows = table_rows(cm.standings(league_id)) if league_id is not None else None
    ss[TABLE_KEY] = (key, rows)
    return rows


def _draw_tabs(live: LiveMatch | None, replay: Replay | None, mode: str) -> None:
    result, frames, index, at_head, _colors = _context(live, replay)
    frame = frames[index] if index >= 0 else None
    finished = live.finished if live is not None else _replay_finished(replay)
    league = live is not None and live.is_fixture and live.competition == "league"
    labels = ["📜 Maç Özeti", "📊 İstatistik", "⭐ Oyuncu Notları"] + (["🏆 Puan Durumu"] if league else []) \
        + ["📰 Maç Raporu"]
    tabs = dict(zip(labels, st.tabs(labels), strict=True))
    home, away = result.home.name, result.away.name

    with tabs["📜 Maç Özeti"]:
        full = st.toggle("Tam kayıt (akışta gizlenen korner, faul, ofsayt ve ataklar dahil)", key=FULL_LOG_KEY)
        if frame is None:
            st.caption("Maç henüz başlamadı.")
        elif full:
            complete = build_timeline(result, include_hidden=True)
            _block(passages_html([f for f in complete if f.index <= frame.index]))
        else:
            shown = frames[: index + 1] if mode == MODE_TEXT else mode_frames(frames[: index + 1], mode)
            _block(passages_html(shown))

    with tabs["📊 İstatistik"]:
        home_stats = frame.home if frame is not None else SideStats()
        away_stats = frame.away if frame is not None else SideStats()
        log = live.possession_log if live is not None else None
        elapsed = live.elapsed if (live is not None and at_head) else frame.elapsed if frame is not None else 0
        possession = stats_possession(result, log, elapsed, at_head)
        _block(stats_html(home, away, home_stats, away_stats, possession))
        if not at_head:
            st.caption("Geri sarılmış görüntü: istatistikler o anki haliyle.")

    with tabs["⭐ Oyuncu Notları"]:
        if live is None and not finished:
            st.caption("Maç tekrarında notlar maç sonunda gösterilir (oyuncu sayaçları maçın tamamını içerir).")
        else:
            now_minute = result.end_minute if finished else live.engine.minute
            cols = st.columns(2, gap="medium")
            for col, team, other in ((cols[0], result.home, result.away), (cols[1], result.away, result.home)):
                rows = rating_rows(team, team.stats.goals, other.stats.goals, now_minute, finished)
                with col:
                    _block(ratings_html(team.name, rows))
            st.caption("Canlı not: gol, asist, isabetli şut, kurtarış, kart, süre ve kondisyondan; maç sonunda "
                       "resmi not olur.")

    if league:
        with tabs["🏆 Puan Durumu"]:
            rows = _league_table(live)
            if not rows:
                st.caption("Puan durumu bulunamadı.")
            elif live.saved:
                _block(table_html(rows, {result.home.id, result.away.id}, live=False))
            else:
                score = (frame.home_score, frame.away_score) if frame is not None else (0, 0)
                table = live_table(rows, result.home.id, result.away.id, *score)
                _block(table_html(table, {result.home.id, result.away.id}, live=True))
                st.caption("Bu maçın o anki skoru işlendi; diğer maçlar hafta sonunda oynanır.")

    with tabs["📰 Maç Raporu"]:
        if finished:
            _block(report_html(result))
        else:
            st.caption("Maç raporu son düdükle hazırlanır.")


# ===========================================================================
# [6] CIZIM: kontroller, paneller, sayfa
# ===========================================================================

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
        st.markdown("**Otomatik durdurma**")
        st.toggle("Devre arasında maçı durdur", value=True, key="live_pause_breaks")
        st.toggle("Takımımda sakatlık / kırmızı kartta durdur", value=True, key="live_pause_events")
        for name, (key, label) in PAUSE_TOGGLES.items():
            st.toggle(label, value=AUTO_PAUSE_DEFAULTS[name], key=key)


def _preview_band(home: str, away: str, comp: str) -> None:
    st.markdown(band_html(home, away, club_colors(home, away), score=(0, 0), clock="0'", phase=PHASE_PRE_MATCH,
                          comp=comp, moments={}), unsafe_allow_html=True)


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
                    "için **⭐ Devler Arenası** sayfasına geç.")
            live_setup_options()
            st.button("▶ Maça çık", key="live_fixture_start", on_click=cb_live_start_fixture, type="primary")
            return
        pending = cm.live_fixture()
        if pending is None:
            st.info(f"{cm.current_week}. haftada {md_escape(team.name)} için oynanacak maç yok. Haftayı "
                    "menüdeki **⏭️ Devam** düğmesiyle oynatabilirsin.")
            return
        fx, competition = pending
        home_name, away_name = fx.home_team.name, fx.away_team.name
        at_home = fx.home_team_id == team.id
        opponent = fx.away_team if at_home else fx.home_team
        form = cm.team_form(opponent.id) or "-"
        week = cm.current_week
    label = "⭐ Devler Arenası (hafta içi)" if competition is Competition.CUP else "🏆 Lig"
    _preview_band(home_name, away_name, f"{label} · {week}. hafta")
    st.caption(f"{label} · {week}. hafta · {'ev' if at_home else 'deplasman'} · rakip formu {form} · "
               "ilk 11 ve diziliş **📋 Kadro** sayfasından gelir.")
    live_setup_options()
    st.button("▶ Maça çık", key="live_fixture_start", on_click=cb_live_start_fixture, type="primary")


def control_row(live: LiveMatch | None, replay: Replay | None, stale: bool = False) -> None:
    c1, c2, c3, c4 = st.columns(4)
    if live is not None:
        if not live.finished:
            if live.paused:
                c1.button("▶ DEVAM", key="live_resume", on_click=cb_live_resume, type="primary", width="stretch")
            else:
                c1.button("⏸ DURDUR", key="live_pause", on_click=cb_live_pause, type="primary", width="stretch")
            c2.button("⏭ Sonucu gör", key="live_finish", on_click=cb_live_finish, width="stretch",
                      help="Kalan dakikaları durmadan oynatır.")
        if live.is_fixture and live.finished and not live.saved and not stale:
            c3.button("💾 Sonucu kaydet", key="live_save", on_click=cb_live_save, type="primary", width="stretch")
        if not live.is_fixture or live.saved or stale:
            c4.button("✖ Maçı kapat", key="live_close", on_click=cb_live_close, width="stretch")
        return
    if not _replay_finished(replay):
        if replay.paused:
            c1.button("▶ DEVAM", key="live_resume", on_click=cb_live_resume, type="primary", width="stretch")
        else:
            c1.button("⏸ DURDUR", key="live_pause", on_click=cb_live_pause, type="primary", width="stretch")
        c2.button("⏭ Sonucu gör", key="live_finish", on_click=cb_live_finish, width="stretch")
    c4.button("✖ Kapat", key="live_close", on_click=cb_live_close, width="stretch")


def _schedule_timer(live: LiveMatch | None, replay: Replay | None) -> None:
    """Istemci zamanlayicisi: siradaki karenin vaktinde parcayi yeniden calistirir (akmiyorsa zamanlayici yok)."""
    ss = st.session_state
    delay = -1
    if _ticking(live, replay) and ss.get(VIEW_KEY) is None:
        delay = max(MIN_TIMER_MS, int(round((ss.get(DUE_KEY, 0.0) - time.monotonic()) * 1000)))
    token = f"{ss.get(CURSOR_KEY, -1)}|{ss.get(DUE_KEY, 0.0):.3f}|{time.monotonic():.3f}"
    component = st.components.v2.component(TIMER_NAME, html=TIMER_HTML, js=TIMER_JS)
    component(key=TIMER_KEY, data={"delay_ms": delay, "token": token}, on_tick_change=cb_md_tick)


def _control_state(live: LiveMatch | None, replay: Replay | None) -> tuple | None:
    if live is not None:
        return ("live", id(live), live.paused, live.finished, live.saved)
    if replay is not None:
        return ("replay", id(replay), replay.paused, _replay_finished(replay))
    return None


def _mount_match_view(live: LiveMatch | None, replay: Replay | None, stale: bool = False) -> None:
    """
    Parca her tam cizimde yeniden kaydedilir. Yedek periyot (FALLBACK_TICK_S) yalnizca mac akarken: istemci
    zamanlayicisi herhangi bir sebeple calismazsa oynatma yine ilerler.
    """
    ss = st.session_state
    ss[CONTROL_KEY] = _control_state(live, replay)          # tam cizimde esit: parca yeniden cizim istemez
    ss[STALE_KEY] = stale
    st.fragment(_match_view, run_every=FALLBACK_TICK_S if _ticking(live, replay) else None)()


def pause_notice(live: LiveMatch) -> None:
    """Duraklama nedeni ve iki uc anlamli eylem (degisiklik hazirla, bagiris, panele git)."""
    if not live.paused or not live.pause_reason or live.finished:
        return
    st.info(f"⏸ Maç durdu — {md_escape(live.pause_reason)}. Değişikliklerini yap, sonra **▶ DEVAM**'a bas.")
    team = live.managed_team
    if team is None:
        return
    kind = live.pause_kind
    if kind == "assistant":
        result, frames = _timeline(live, None)
        side = _side_of(live, None, result)
        note = assistant_note(result, frames, side, live.engine.minute, live.possession_log)
        if note is not None:
            st.markdown(note_html(note), unsafe_allow_html=True)
    elif kind == "opponent_tactics":
        opponent = live.opponent_team
        result, frames = _timeline(live, None)
        last = next((f for f in reversed(frames) if f.event.type == "TACTICAL_CHANGE" and opponent is not None
                     and f.event.team == opponent.name), None)
        if last is not None:
            st.caption(f"Rakibin kararı: {md_escape(last.event.description)}")
    cols = st.columns(3)
    if kind == "tired":
        tired = next((p for p in team.on_pitch if p.id in live.tired_seen and p.energy < 60), None)
        if tired is not None and live.can_substitute_now:
            cols[0].button(f"🔁 {pitch.short_name(tired.name, 16)} için değişiklik hazırla", key="md_act_sub",
                           on_click=cb_live_prepare_sub, args=(tired.id,), width="stretch")
    if kind == "two_goals":
        diff = team.stats.goals - live.opponent_team.stats.goals
        shout = "skoru_koru" if diff > 0 else "sakin" if diff == 0 else "one_cik"
        cols[1].button(SHOUTS[shout].label, key="md_act_shout", on_click=cb_live_shout, args=(shout,),
                       help=SHOUTS[shout].help, width="stretch")
    cols[2].markdown(f"[🧠 Talimat Paneli](#{PANEL_ANCHOR}) · [🔁 Değişiklik](#{SUBS_ANCHOR})")


def rewind_strip(frames: list[Frame], head: int) -> None:
    """Zaman seridi (yalnizca durakken / mac bitince): onemli ana tiklayinca goruntu o kareye sarilir."""
    moments = key_moments(frames[: head + 1])
    if not moments:
        return
    labels = dict(moments)
    if st.session_state.get(REWIND_KEY) not in labels:
        st.session_state[REWIND_KEY] = None
    st.pills("⏪ Zaman şeridi — bir ana tıkla, görüntü o kareye döner (maç ilerlemez)", list(labels),
             format_func=labels.get, key=REWIND_KEY, on_change=cb_md_rewind, selection_mode="single")
    if st.session_state.get(VIEW_KEY) is not None:
        st.button("⏩ Canlıya dön", key="md_live_head", on_click=cb_md_live_head)


def intervention_panels(live: LiveMatch) -> None:
    _sync_instruction_widgets(live)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(f'<a id="{PANEL_ANCHOR}"></a>', unsafe_allow_html=True)
        st.markdown("#### 🧠 Talimat Paneli")
        st.caption(f"Şu an: {live.instructions_text()} · diziliş {live.formation}")
        mentality, tackling, *rest = INSTRUCTION_AXES
        st.radio(mentality.label, list(mentality.options.values()), key=mentality.key,
                 on_change=cb_live_instructions, horizontal=True, help=mentality.help)
        st.radio(tackling.label, list(tackling.options.values()), key=tackling.key,
                 on_change=cb_live_instructions, horizontal=True, help=tackling.help)
        a, b = st.columns(2)
        for i, axis in enumerate(rest):
            col = a if i % 2 == 0 else b
            if axis.options is None:
                col.toggle(axis.label, key=axis.key, on_change=cb_live_instructions, help=axis.help)
            else:
                col.selectbox(axis.label, list(axis.options.values()), key=axis.key,
                              on_change=cb_live_instructions, help=axis.help)
        costs = instruction_costs(live.instructions)
        if costs:
            st.caption("Talimatın bedeli — " + " · ".join(costs))
        st.markdown("**📣 Tek tık bağırışlar**")
        cols = st.columns(3)
        for i, shout in enumerate(SHOUTS.values()):
            cols[i % 3].button(shout.label, key=f"{SHOUT_PREFIX}{shout.key}", on_click=cb_live_shout,
                               args=(shout.key,), help=shout.help, width="stretch")
        f1, f2 = st.columns([2, 1])
        f1.selectbox("Diziliş", list(MATCH_FORMATIONS), key="live_formation",
                     help="5-3-2 yalnızca maç içi acil durum dizilişidir.")
        f2.button("Uygula", key="live_formation_apply", on_click=cb_live_formation, width="stretch")
        if live.history:
            st.caption("Son müdahaleler: " + " · ".join(md_escape(h) for h in live.history[-3:]))
    with right:
        st.markdown(f'<a id="{SUBS_ANCHOR}"></a>', unsafe_allow_html=True)
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
        if st.session_state.get("live_sub_out") not in out_labels:
            st.session_state.pop("live_sub_out", None)
        st.selectbox("Çıkan", list(out_labels), format_func=out_labels.get, key="live_sub_out")
        st.selectbox("Giren", list(in_labels), format_func=in_labels.get, key="live_sub_in")
        st.selectbox("Görev", ROLE_CHOICES, key="live_sub_role",
                     help="Varsayılan: giren oyuncu çıkanın görevini alır.")
        st.button("✅ Değişikliği yap", key="live_sub_confirm", on_click=cb_live_substitute, type="primary",
                  width="stretch")


def final_summary(result: MatchResult, live: LiveMatch | None) -> None:
    summary = summarize(result, build_timeline(result))
    st.divider()
    st.markdown("### Maç sonu")
    for line in summary_lines(summary):
        st.markdown(line)
    if live is None:
        return
    if live.history:
        with st.expander("📋 Menajer müdahaleleri"):
            for item in live.history:
                st.markdown(f"- {md_escape(item)}")
    if live.is_fixture:
        if live.saved:
            st.success("Sonuç kariyerine işlendi.")
        else:
            st.warning("Sonucu kariyerine işlemek için **💾 Sonucu kaydet**'e bas.")


def live_match_screen(live: LiveMatch) -> None:
    stale = live_is_stale(live)
    if stale:
        st.warning("Bu canlı maç artık geçerli değil (hafta ilerledi ya da maç başka yoldan oynandı); "
                   "sonuç kaydedilemez. **✖ Maçı kapat** ile çık.")
    factor = current_speed()
    if factor == 0 and not live.paused and not live.finished:
        live.run()                         # anında: bir sonraki duraklamaya ya da maç sonuna kadar
        _to_head(live=live)
    elif factor == 0 or live.paused or live.finished:
        # durakken / bitmisken ekran her zaman o ana kadarki son kareyi gosterir (geri sarma haric)
        frames = _timeline(live, None)[1]
        if st.session_state.get(CURSOR_KEY, -1) < len(frames) - 1 and st.session_state.get(VIEW_KEY) is None:
            st.session_state[CURSOR_KEY] = len(frames) - 1

    team = live.managed_team
    parts = [live.title or "Canlı maç", SUB_RULE_LABELS[live.sub_rule]]
    if team is not None:
        parts.append(f"yönettiğin takım: {team.name}")
    st.markdown("**" + md_escape(parts[0]) + "** · " + " · ".join(md_escape(p) for p in parts[1:]))

    _mount_match_view(live, None, stale)
    if team is not None and not live.finished:
        intervention_panels(live)
    if live.finished:
        final_summary(live.result(), live)


def replay_screen(replay: Replay) -> None:
    if replay.caption:
        st.caption(replay.caption)
    if current_speed() == 0 and not replay.paused:
        _to_head(replay=replay)
    st.markdown(f"**{md_escape(replay.title)}**")
    _mount_match_view(None, replay)
    if _replay_finished(replay):
        final_summary(replay.result, None)


def render(teams: list[str]) -> None:
    """Canli Mac sayfasi (nav slug canli-mac)."""
    st.markdown(MD_CSS, unsafe_allow_html=True)
    show_flash("live")
    live = current_live()
    c1, c2, c3, c4 = st.columns([3, 2, 3, 1.5])
    mode = c1.radio("Mod", LIVE_MODES, key="live_mode", horizontal=True, disabled=live is not None)
    c2.select_slider("Yorum hızı", options=list(SPEED_FACTORS), value=DEFAULT_SPEED, key="live_speed",
                     help="Olay başına bekleme: rutin olay kısa, gol ve kırmızı kart uzun. Anında: bir sonraki "
                          "duraklamaya kadar.")
    c3.radio("Özet", list(SUMMARY_MODES), format_func=SUMMARY_MODES.get, key=MODE_KEY, horizontal=True,
             help="Tam maç: bütün görünür anlar. Geniş özet: önemli olaylar. Önemli anlar: goller, kırmızılar, "
                  "değişiklikler ve büyük fırsatlar. Sadece metin: afiş ve tahta yok.")
    c4.toggle("Şekil tahtası", value=True, key="live_pitch",
              help="Oyuncular diziliş yerinde, kondisyon halkasıyla. Motor topun yerini bilmediği için top ve pas "
                   "okları çizilmez.")

    if live is not None:
        live_match_screen(live)
        return
    replay = current_replay()
    if replay is not None and replay.mode != mode:
        st.session_state.pop(REPLAY_KEY, None)
        replay = None
    if mode == LIVE_MANAGE:
        manage_setup()
        return

    if mode == LIVE_FRIENDLY:
        h1, h2, h3, h4 = st.columns([2, 2, 1, 1])
        home = h1.selectbox("Ev sahibi", teams, index=0, key="live_home")
        away = h2.selectbox("Deplasman", teams, index=1 if len(teams) > 1 else 0, key="live_away")
        h3.text_input("Tohum", value="", key="live_seed")
        h4.toggle("Eleme maçı", value=False, key="live_knockout",
                  help="Beraberlikte uzatma ve penaltılar oynanır (tarafsız saha).")
        side = st.radio("Yönettiğim takım", SIDE_LABELS, key="live_side", horizontal=True,
                        help="Yönettiğin takımda maçı durdurup değişiklik ve taktik talimatı verebilirsin.")
        if side != SIDE_WATCH:
            live_setup_options()
    else:
        recorded = recorded_matches()
        if not recorded:
            if shared_page_world() is not None:
                st.info("Paylaşılan dünyada maçlar hafta ilerlerken oynanır; sonuçlar **📅 Fikstür & Sonuçlar** "
                        "sayfasındaki haftalık raporda. Maç tekrarı yakında.")
            else:
                st.warning("Henüz izlenecek maç yok. Haftayı menüdeki **⏭️ Devam** düğmesiyle oyna.")
            return
        if len(recorded) > 1:
            st.selectbox("Maç", list(recorded), key="live_which")
    st.button("▶ Maçı başlat", key="live_start", type="primary", on_click=cb_live_start)

    if replay is None:
        if mode == LIVE_FRIENDLY:
            _preview_band(home, away, "Hazırlık maçı")
        st.info("Ayarları seç ve **Maçı başlat**'a bas.")
        return
    replay_screen(replay)
