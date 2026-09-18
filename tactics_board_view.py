"""
tactics_board_view.py
=====================
Surukle-birak taktik tahtasi (Faz 13G): st.components.v2 satir ici bileseni (derleme adimi / npm / CDN YOK) +
sunucu tarafi dogrulama. Saf kurallar tactics_board.py'de; kalici kayit CareerManager.set_lineup (tam
tactics.validate_lineup) ve set_team_roles (team_roles) ile -- eski tac_rows / st.data_editor yoluyla AYNI kayit.

BILESEN
    ad            "ofm_tactics_board" (her cizimde kaydedilir: ayni tanim uyarisiz yenilenir, AppTest'te de calisir)
    kaynaklar     web_assets/tactics_board.js + .css (vanilla JS, harici istek yok, eval yok, innerHTML yok)
    anahtar       tb_board; veri: tactics_board.board_payload (slotlar, kulube, kadro disi, rev, locked, metinler)
    olay          "intent" (tetikleyici; sema tactics_board.py basliginda) -> cb_tb_intent (member_callback)

AKIS
    cizim   : veritabanindaki kadro (cm.lineup_of) -> tb_state (BoardState: kulup, kadro imzasi, yerlesim). Kadro baska
              yoldan degistiyse (asistan, temizle, dizilis, liste duzenleyici, hazir taktik, mac) yerlesim yeniden
              kurulur (hat icindeki sira korunur). tb_rev her islenen niyetle artar -> bilesen sunucu durumuyla cizilir.
    niyet   : cb_tb_intent -> process_intent. Kulup HER ZAMAN cm.user_team'den; istemci yalnizca oyuncu id'leri ve
              slot sirasi yollar. Sema disi / eski surum (rev) / kadro disi oyuncu / sakat-cezali oyuncu / dolu kulube ->
              Turkce flash ile RED, yerlesim degismez. Gecerli ve 11'i tam yerlesim hemen kaydedilir; eksik yerlesim
              (orn. bir oyuncu kulubeye alindi) TASLAK olarak kalir: "💾 Tahtayı kaydet" / "↩️ Taslağı at".
    roller  : sag tik / uzun bas menusu -> kaptan, penalti, serbest vurus, korner (cm.set_team_roles; Taktik Merkezi
              secicileri sifirlanir ki eski deger geri yazilmasin ve tum sayfa bir kez yeniden cizilir:
              rerun_app_if_needed, cunku tahta web_app.squad_board_section parcasinda (st.fragment) calisir)
    profil  : cift tik / menu "Profil" -> player_view.open_profile(AREA_SQUAD) (yalnizca kendi A takim oyuncun)
    kilit   : kaydedilmemis canli kariyer maci varken tahta salt okunur (yalnizca profil)

WIDGET ANAHTARLARI: tb_board (bilesen), tb_save, tb_discard; oturum: tb_state, tb_rev. Flash alani: board.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import player_view as pv
import tactics_board as tb
from career_manager import TacticsError
from database import session_scope
from stars import star_glyphs
from tactics import LineupCheck, validate_lineup
from team_roles import ROLE_FIELDS
from web_common import (
    flash,
    live_fixture_pending,
    manager,
    member_callback,
    requires_auth,
    reset_widgets,
    show_flash,
)

COMPONENT_NAME = "ofm_tactics_board"
ASSETS = Path(__file__).resolve().with_name("web_assets")
HTML = '<div class="tb-root"><div class="tb-view"></div><div class="tb-live" role="status" aria-live="polite"></div></div>'
CSS = (ASSETS / "tactics_board.css").read_text(encoding="utf-8")
JS = (ASSETS / "tactics_board.js").read_text(encoding="utf-8")

BOARD_KEY = "tb_board"
STATE_KEY = "tb_state"
REV_KEY = "tb_rev"
FLASH_AREA = "board"
FULL_RERUN_KEY = "tb_full_rerun"          # gorev degisti: parca yerine tum sayfa yeniden cizilsin (squad_board_section)
LINEUP_WIDGETS = ("tac_editor", "tac_rows")                    # liste duzenleyici kayitli kadrodan yeniden kurulsun
ROLE_WIDGETS = tuple(f"role_{f}" for f in ROLE_FIELDS)          # Taktik Merkezi secicileri (eski deger geri yazilmasin)

STALE_TEXT = "Tahta bu arada yenilendi: hareketi tekrar yap."
CHANGED_TEXT = "Kadro başka bir yerden değişti; tahta kayıtlı kadroyla yeniden çizildi."
LOCKED_TEXT = "🏟️ Canlı maçın sürüyor: kadro ve görevler maç kaydedilene kadar değişmez."


@dataclass(frozen=True)
class BoardState:
    team_id: int
    base: tuple                   # tahta kurulurken veritabanindaki kadronun imzasi (tactics_board.signature)
    layout: tb.Layout


@dataclass
class Outcome:
    """process_intent sonucu (Streamlit'siz; testler dogrudan okur)."""
    state: BoardState | None
    messages: list[tuple[str, str]]
    profile: int | None = None
    lineup_changed: bool = False
    roles_changed: bool = False


# ===========================================================================
# DURUM
# ===========================================================================

def board_state(cm, team, previous: BoardState | None) -> BoardState:
    """Kayitli kadroya gore tahta durumu; kadro degismediyse onceki yerlesim (taslak dahil) aynen doner."""
    xi, bench, _out = cm.lineup_of(team)
    base = tb.signature(team.formation, xi, bench)
    if previous is not None and previous.team_id == team.id and previous.base == base:
        return previous
    keep = previous.layout if previous is not None and previous.team_id == team.id else None
    layout = tb.layout_from_lineup(team.players, team.formation, xi, bench, keep)
    return BoardState(team.id, base, layout)


def is_draft(state: BoardState) -> bool:
    return tb.layout_signature(state.layout) != state.base


def draft_check(cm, team, state: BoardState) -> LineupCheck:
    return validate_lineup(team.players, team.formation, cm.current_week, state.layout.xi(), state.layout.bench,
                           allow_incomplete=True)


def _save(cm, team, state: BoardState) -> tuple[BoardState, list[tuple[str, str]], bool]:
    """Tam ve gecerli yerlesimi kaydeder; degilse taslak kalir. (yeni durum, mesajlar, kaydedildi mi)"""
    layout = state.layout
    if not layout.complete:                                  # neden ve yol tahtanin ustundeki taslak kutusunda
        return state, [], False
    check = cm.set_lineup(team, layout.xi(), list(layout.bench))
    if not check.ok:
        return state, [("warning", "✏️ Taslak kaydedilmedi: " + " · ".join(check.errors))], False
    base = tb.signature(team.formation, layout.xi(), layout.bench)
    # Uyarilar (mevki disi, dusuk kondisyon) kayitli kadronun denetimi olarak tahtanin ustunde zaten kalici gosterilir
    return BoardState(team.id, base, layout), [], True


# ===========================================================================
# NIYET (Streamlit'siz cekirdek)
# ===========================================================================

def process_intent(cm, team, state: BoardState | None, raw, rev: int, *, locked: bool = False) -> Outcome:
    """
    Bilesenden gelen niyeti dogrular ve uygular. team: HER ZAMAN cm.user_team (cagiran saglar). Red -> durum aynen
    kalir, mesaj ("error", ...). Veritabani yazimi yalnizca cm uzerinden (commit cagiranin session_scope'unda).
    """
    try:
        intent = tb.parse_intent(raw)
    except tb.IntentError as exc:
        return Outcome(state, [("error", str(exc))])
    if state is None or state.team_id != team.id or intent.rev != rev:
        return Outcome(state, [("warning", STALE_TEXT)])
    fresh = board_state(cm, team, state)
    if fresh is not state:                                   # kadro tahta cizildikten sonra baska yoldan degisti
        return Outcome(fresh, [("warning", CHANGED_TEXT)])
    by_id = {p.id: p for p in team.players}                  # A takim (akademi haric), sunucudan
    if intent.action == "profile":
        if intent.player not in by_id:
            return Outcome(state, [("error", "Bu oyuncu A takım kadronda değil.")])
        return Outcome(state, [], profile=intent.player)
    if locked:
        return Outcome(state, [("error", LOCKED_TEXT)])
    if intent.action == "role":
        try:
            roles, text = tb.apply_role(cm.team_roles(team), intent, by_id)
            cm.set_team_roles(team, roles)
        except (tb.IntentError, TacticsError) as exc:
            return Outcome(state, [("error", str(exc))])
        return Outcome(state, [("success", text)], roles_changed=True)
    try:
        result = tb.apply_intent(state.layout, intent, by_id, cm.current_week)
    except tb.IntentError as exc:
        return Outcome(state, [("error", str(exc))])
    if not result.changed:
        return Outcome(state, [])
    moved = BoardState(state.team_id, state.base, result.layout)
    messages = [("warning", note) for note in result.notes]
    saved_state, save_messages, saved = _save(cm, team, moved)
    if saved:
        messages.insert(0, ("success", "✅ Kadro kaydedildi."))
    return Outcome(saved_state, messages + save_messages, lineup_changed=True)


# ===========================================================================
# STREAMLIT
# ===========================================================================

def _rev() -> int:
    return int(st.session_state.get(REV_KEY, 0))


def _bump() -> None:
    st.session_state[REV_KEY] = _rev() + 1


def _trigger_value():
    value = st.session_state.get(BOARD_KEY)
    return value.get("intent") if isinstance(value, dict) else None


@member_callback
def cb_tb_intent() -> None:
    """Bilesen niyeti (sunucuda dogrulanir; kulup cm.user_team). Her durumda tb_rev artar: tahta sunucudan cizilir."""
    raw = _trigger_value()
    try:
        if raw is None:
            return
        with session_scope() as db:
            cm = manager(db)
            team = cm.user_team
            if team is None:
                flash(FLASH_AREA, "error", "Önce kulübünü seç.")
                return
            outcome = process_intent(cm, team, st.session_state.get(STATE_KEY), raw, _rev(),
                                     locked=live_fixture_pending())
            if any(kind == "error" for kind, _ in outcome.messages):
                db.rollback()
        if outcome.state is not None:
            st.session_state[STATE_KEY] = outcome.state
        for kind, text in outcome.messages:
            flash(FLASH_AREA, kind, text)
        if outcome.profile is not None:
            pv.open_profile(pv.AREA_SQUAD, outcome.profile)
        if outcome.lineup_changed:
            reset_widgets(*LINEUP_WIDGETS)
        if outcome.roles_changed:
            reset_widgets(*ROLE_WIDGETS)
            # Taktik Merkezi'ndeki gorev secicileri parcanin DISINDA: ekranda eski kaptan kalmasin diye tum sayfa
            st.session_state[FULL_RERUN_KEY] = True
    finally:
        _bump()


@member_callback
def cb_tb_save() -> None:
    """Taslak yerlesimi kaydeder (dizilis degisince ya da eski gecersiz kadroda; tam denetim set_lineup'ta)."""
    state = st.session_state.get(STATE_KEY)
    try:
        if live_fixture_pending():
            flash(FLASH_AREA, "error", LOCKED_TEXT)
            return
        with session_scope() as db:
            cm = manager(db)
            team = cm.user_team
            if team is None or state is None or state.team_id != team.id:
                flash(FLASH_AREA, "warning", STALE_TEXT)
                return
            fresh = board_state(cm, team, state)
            if fresh is not state:
                st.session_state[STATE_KEY] = fresh
                flash(FLASH_AREA, "warning", CHANGED_TEXT)
                return
            new_state, messages, saved = _save(cm, team, state)
        st.session_state[STATE_KEY] = new_state
        if saved:
            flash(FLASH_AREA, "success", "✅ Kadro kaydedildi.")
            reset_widgets(*LINEUP_WIDGETS)
        for kind, text in messages:
            flash(FLASH_AREA, kind, text)
    finally:
        _bump()


@requires_auth
def cb_tb_discard() -> None:
    """Taslagi atar: tahta kayitli kadroyla yeniden cizilir (veritabanina yazmaz)."""
    reset_widgets(STATE_KEY)
    _bump()
    flash(FLASH_AREA, "info", "Taslak atıldı: tahta kayıtlı kadroyu gösteriyor.")


def rerun_app_if_needed() -> None:
    """
    Tahta st.fragment icinde calisir (bir hareket yalnizca parcayi cizer). Kaptan / duran top gorevi degistiyse
    parcanin disindaki Taktik Merkezi secicileri de guncellensin: parca calismasinda tum sayfa yeniden cizilir.
    """
    if not st.session_state.pop(FULL_RERUN_KEY, False):
        return
    ctx = get_script_run_ctx()
    if ctx is not None and ctx.fragment_ids_this_run:
        st.rerun(scope="app")


def mount(payload: dict) -> None:
    """Bileseni (her cizimde) kaydeder ve yerlestirir."""
    component = st.components.v2.component(COMPONENT_NAME, html=HTML, css=CSS, js=JS)
    component(key=BOARD_KEY, data=payload, on_intent_change=cb_tb_intent)


def render_board(db, cm, team) -> BoardState:
    """Taktik tahtasi: flash, taslak / kadro denetimi, bilesen. Durum oturuma yazilir (cb_tb_intent okur)."""
    state = board_state(cm, team, st.session_state.get(STATE_KEY))
    st.session_state[STATE_KEY] = state
    locked = live_fixture_pending()
    show_flash(FLASH_AREA)
    if locked:
        st.info(LOCKED_TEXT)
    if is_draft(state):
        check = draft_check(cm, team, state)
        missing = len(state.layout.slots) - state.layout.filled
        reasons = ([f"ilk 11'de {missing} boş slot var — boş slota bir oyuncu sürükle, 11 tamamlanınca kadro "
                    "kendiliğinden kaydedilir"] if missing else []) + check.errors
        st.warning("✏️ **Taslak — kaydedilmedi.** Maçta kayıtlı kadro oynar. "
                   + ("; ".join(reasons) + "." if reasons else "Diziliş değişti: yerleşimi kontrol et ve kaydet."))
        c1, c2 = st.columns(2)
        c1.button("💾 Tahtayı kaydet", key="tb_save", on_click=cb_tb_save, type="primary", width="stretch",
                  disabled=locked or bool(missing) or not check.ok)
        c2.button("↩️ Taslağı at", key="tb_discard", on_click=cb_tb_discard, width="stretch")
    else:
        check = cm.lineup_check(team)
        for err in check.errors:
            st.error(err)
        if check.warnings:                                   # tek kutu: telefonda tahta asagi itilmesin
            st.warning("\n".join(f"- {w}" for w in check.warnings))
    payload = tb.board_payload(state.layout, list(team.players), cm.current_week, cm.team_roles(team), star_glyphs,
                               rev=_rev(), locked=locked, team_name=team.name)
    mount(payload)
    return state
