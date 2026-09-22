"""
continue_view.py
================
Faz 15D-U: CM 01/02'nin "devam"inin yanindaki **"Şuna kadar devam"**. Kural katmani `inbox.continue_until`
(15D, C seridi); bu modul yalnizca onu cizer ve uzun kosuyu PARCALARA boler.

    panel(cm)     Gelen Kutusu'nda Devam satirinin altinda: hedef secici + "Şuna kadar devam" dugmesi; kosu
                  surerken ilerleme cubugu, "Durdur" ve istemci zamanlayicisi; kosu bitince NEDEN durduğu.

Hedefler (inbox.TARGET_LABELS): Sonraki maça kadar · Transfer dönemi açılana kadar · Sezon sonuna kadar ·
Belirli hafta sayısı. Duruş nedenleri inbox.STOP_LABELS'tan gelir ("Önemli bir gelişme var: Teklif geldi",
"Oyuncun sakatlandı", "Hedefe ulaşıldı", ...).

TEPKISELLIK (14A deseni, match_day_view.TIMER_*): bir hafta ~2-3 sn surer; 38 haftalik bir kosu tek cagrida
oynatilsa sayfa dakikalarca donardi. Bunun yerine kosu st.fragment icinde HAFTA HAFTA ilerler -- ilerleme
PARCANIN KENDISINDEDIR (zamanlayicinin tetigi bilerek bostur, tipki match_day_view.cb_md_tick gibi) ve bir
sonraki parcayi istemci zamanlayicisi (st.components.v2 + setTimeout) hemen yeniden calistirir. Her hafta
sonunda parca yeniden cizilir: ilerleme cubugu ve "Durdur" hep tepkilidir, ana is parcacigi bloke olmaz.
Kosu bitince parca BIR KEZ tum sayfayi tazeler (st.rerun(scope="app")): tarih cubugu, gelen kutusu ve mac
kartlari yeni haftayi gosterir.

Paylasilan dunyada ve turnuva modunda panel HIC cizilmez (hafta orada tur motoruyla ilerler: inbox.SHARED_TEXT).
Callback'ler: until_start (kosuyu baslat), until_stop (elle durdur), until_tick (zamanlayici; BOS).
Oturum anahtarlari: until_target / until_weeks / until_important (widget), until_run (kosu durumu; widget DEGIL).
"Önemli gelişmede dur" kutusu inbox.continue_until'in stop_on_important'idir: kapaliyken kosu kesintisiz gider.
"""

from __future__ import annotations

import logging
import time

import streamlit as st

import inbox
from database import session_scope
from models import GameMode
from web_common import (
    flash,
    live_fixture_pending,
    manager,
    md_escape,
    member_callback,
    requires_auth,
)

log = logging.getLogger(__name__)

AREA = "home"                       # home_view.AREA (import etmeyiz: home_view bu modulu import eder)
TARGET_KEY, WEEKS_KEY, RUN_KEY = "until_target", "until_weeks", "until_run"
IMPORTANT_KEY = "until_important"   # "Önemli gelişmede dur" (CM: teklif / sakatlik / yonetim mesaji)
TIMER_NAME, TIMER_KEY = "ofm_continue_timer", "until_timer"
CHUNK_WEEKS = 1                     # bir parca calismasinda oynatilan hafta (tepkisellik: hafta basina bir cizim)
TICK_GAP_S = 0.05                   # iki hafta arasindaki en kisa ara (kaza ile gelen yeniden cizim hafta oynatmasin)
TIMER_DELAY_MS = 60                 # match_day_view.MIN_TIMER_MS: bir sonraki parca hemen
MAX_TOTAL_WEEKS = inbox.DEFAULT_MAX_WEEKS
DEFAULT_WEEKS = 4                   # "Belirli hafta sayısı" varsayilani
TARGET_OPTIONS = (inbox.TARGET_NEXT_MATCH, inbox.TARGET_WINDOW, inbox.TARGET_SEASON_END, inbox.TARGET_WEEKS)
START_LABEL = "Şuna kadar devam"
BUSY_TEXT = "Haftalar oynanıyor…"


# ===========================================================================
# KOSU DURUMU (oturum durumu; widget degil)
# ===========================================================================

def run_state() -> dict | None:
    run = st.session_state.get(RUN_KEY)
    return run if isinstance(run, dict) else None


def running() -> bool:
    run = run_state()
    return bool(run and run.get("active"))


def _new_run(cm, target: str, weeks: int | None, stop_on_important: bool = True) -> dict:
    total = cm.total_weeks()
    if target == inbox.TARGET_WEEKS:
        limit = max(1, int(weeks or DEFAULT_WEEKS))
    elif target == inbox.TARGET_SEASON_END:
        limit = max(1, int(total) - int(cm.current_week) + 1)
    else:
        limit = MAX_TOTAL_WEEKS
    return {"target": str(target), "weeks": int(weeks) if weeks else None, "limit": int(limit),
            "important": bool(stop_on_important), "done": 0, "active": True, "serial": 0, "due": 0.0,
            "refreshed": False, "stop": None, "reason": "", "messages": [], "seconds": 0.0,
            "season": int(cm.season), "week": int(cm.current_week)}


def _finish(run: dict, stopped_by: str, reason: str) -> None:
    run["active"] = False
    run["stop"] = str(stopped_by)
    run["reason"] = str(reason)
    _announce(run)


def _announce(run: dict) -> None:
    """Kosu bitti: nedeni bir sonraki tam cizimde Gelen Kutusu'nun tepesinde gosterilir."""
    stop = str(run.get("stop") or "")
    kind = "warning" if stop in (inbox.STOP_IMPORTANT, inbox.STOP_BLOCKED, inbox.STOP_ERROR) else "success"
    flash(AREA, kind, f"{inbox.STOP_LABELS.get(stop, 'Devam durdu')} — {md_escape(str(run.get('reason') or ''))} "
                      f"({int(run.get('done') or 0)} hafta, {float(run.get('seconds') or 0.0):.1f} sn)")


def _should_continue(target: str, run: dict, result) -> bool:
    """Bir parca bitti: kosu suruyor mu? (hedefe varildi / onemli gelisme / hata / tavan -> hayir)"""
    if int(result.weeks) <= 0 or int(run["done"]) >= MAX_TOTAL_WEEKS:
        return False
    if target == inbox.TARGET_WEEKS:
        return (result.stopped_by in (inbox.STOP_TARGET, inbox.STOP_LIMIT)
                and int(run["done"]) < int(run.get("weeks") or 0))
    return result.stopped_by == inbox.STOP_LIMIT


def advance(run: dict) -> None:
    """Bir parca (CHUNK_WEEKS hafta) oynatir ve kosu durumunu gunceller. Parcanin cizim yolunda calisir."""
    target = str(run.get("target") or inbox.TARGET_NEXT_MATCH)
    remaining = None
    if target == inbox.TARGET_WEEKS:
        remaining = max(0, int(run.get("weeks") or 0) - int(run["done"]))
        if remaining <= 0:
            _finish(run, inbox.STOP_TARGET, f"{run['done']} hafta işlendi.")
            return
    report = None
    with session_scope() as db:
        cm = manager(db)
        try:
            result = cm.continue_until(target, weeks=remaining, max_weeks=CHUNK_WEEKS, keep_reports=True,
                                       stop_on_important=bool(run.get("important", True)))
        except Exception as exc:                       # hafta islenemedi: kosu durur, oyun bozulmaz
            log.exception("'Şuna kadar devam' parçası işlenemedi: %s", exc)
            db.rollback()
            _finish(run, inbox.STOP_ERROR, str(exc))
            return
        run["done"] += int(result.weeks)
        run["seconds"] += float(result.seconds)
        run["season"], run["week"] = int(result.season), int(result.week)
        run["messages"] = list(run.get("messages") or []) + list(result.messages)
        run["serial"] = int(run.get("serial") or 0) + 1
        run["due"] = time.monotonic() + TICK_GAP_S
        report = result.reports[-1] if result.reports else None
        keep = _should_continue(target, run, result)
    if report is not None:
        # oturum anahtarlari (son maç, kupa raporu, genç girişi): cb_play_week ile ayni yol
        import match_day_view

        match_day_view.store_week_report(report)
    if not keep:
        _finish(run, result.stopped_by, result.reason)


# ===========================================================================
# CALLBACK'LER
# ===========================================================================

@member_callback
def cb_until_start() -> None:
    """"Şuna kadar devam": kosuyu baslatir (haftalar parcada oynanir)."""
    ss = st.session_state
    target = str(ss.get(TARGET_KEY) or inbox.TARGET_NEXT_MATCH)
    if target not in TARGET_OPTIONS:
        target = inbox.TARGET_NEXT_MATCH
    weeks = int(ss.get(WEEKS_KEY) or DEFAULT_WEEKS) if target == inbox.TARGET_WEEKS else None
    if live_fixture_pending():
        flash(AREA, "error", inbox.LIVE_TEXT)
        return
    with session_scope() as db:
        cm = manager(db)
        if getattr(cm.rules, "shared", False):
            flash(AREA, "error", inbox.SHARED_TEXT)
            return
        if cm.season_finished:
            flash(AREA, "info", "Sezon tamamlandı; önce yeni sezonu başlat.")
            return
        ss[RUN_KEY] = _new_run(cm, target, weeks, bool(ss.get(IMPORTANT_KEY, True)))


@requires_auth
def cb_until_stop() -> None:
    """Kosuyu elle durdurur (o anki hafta biter, sonraki parca calismaz)."""
    run = run_state()
    if run is not None and run.get("active"):
        run["active"] = False
        run["stop"] = inbox.STOP_LIMIT
        run["reason"] = f"{int(run.get('done') or 0)} hafta işlendi; elle durduruldu."


@requires_auth
def cb_until_tick() -> None:
    """
    Istemci zamanlayicisinin tetigi. Bilerek BOS (match_day_view.cb_md_tick ile ayni): ilerleme parcanin
    kendisindedir; tetik yalnizca parcayi yeniden calistirir.
    """


# ===========================================================================
# CIZIM
# ===========================================================================

def _timer(run: dict) -> None:
    """Bir sonraki parcayi hemen calistiran istemci zamanlayicisi (14A: st.components.v2 + setTimeout)."""
    import match_day_view

    component = st.components.v2.component(TIMER_NAME, html=match_day_view.TIMER_HTML, js=match_day_view.TIMER_JS)
    component(key=TIMER_KEY, data={"delay_ms": TIMER_DELAY_MS, "token": f"{run['serial']}|{run['done']}"},
              on_tick_change=cb_until_tick)


@st.fragment
def _run_fragment() -> None:
    """Kosu parcasi: bir hafta oynat, ilerlemeyi ciz, bir sonraki parcayi zamanla. Bloke uyku YOK."""
    run = run_state()
    if run is None:
        return
    if run.get("active") and time.monotonic() >= float(run.get("due") or 0.0):
        advance(run)
    if run.get("active"):
        limit = max(1, int(run.get("limit") or 1))
        done = int(run.get("done") or 0)
        with st.container(key="until_prog"):
            st.progress(min(1.0, done / limit),
                        text=f"{BUSY_TEXT} {done} / {limit} · Sezon {run['season']}, {run['week']}. hafta")
            st.button("Durdur", key="until_stop", on_click=cb_until_stop, width="stretch")
            _timer(run)
        return
    if not run.get("refreshed"):                  # kosu bitti: tum sayfa bir kez tazelenir (tarih, gelen kutusu)
        run["refreshed"] = True
        st.rerun(scope="app")
    _result(run)


def _result(run: dict | None) -> None:
    """Son kosunun ozeti: NEDEN durdu ve durusa yol acan onemli mesajlar (CM: "Teklif geldi", "Oyuncun sakatlandı")."""
    if run is None or run.get("active") or not run.get("stop"):
        return
    stop = str(run.get("stop"))
    label = inbox.STOP_LABELS.get(stop, "Devam durdu")
    st.caption(f"{label} — {run.get('reason') or ''} · {int(run.get('done') or 0)} hafta "
               f"({float(run.get('seconds') or 0.0):.1f} sn)")
    for view in list(run.get("messages") or [])[:5]:
        st.caption(f"· {getattr(view, 'kind_label', '')}: {getattr(view, 'subject', '')}"
                   f" ({getattr(view, 'date_label', '')})")


def _control(cm) -> None:
    """Hedef secici + baslatma dugmesi (CM: Devam'in yanindaki kisa satir)."""
    ss = st.session_state
    if ss.get(TARGET_KEY) not in TARGET_OPTIONS:
        ss[TARGET_KEY] = inbox.TARGET_NEXT_MATCH
    blocked = bool(cm.season_finished) or live_fixture_pending()
    with st.container(horizontal=True, key="until_row", vertical_alignment="bottom"):
        st.selectbox("Şuna kadar devam", list(TARGET_OPTIONS), key=TARGET_KEY,
                     format_func=inbox.TARGET_LABELS.get, label_visibility="collapsed",
                     help="Hedefe kadar haftalar oynanır; önemli bir gelişmede (teklif, sakatlık, yönetim) durur.")
        if ss.get(TARGET_KEY) == inbox.TARGET_WEEKS:
            ss.setdefault(WEEKS_KEY, DEFAULT_WEEKS)
            st.number_input("Hafta", min_value=1, max_value=MAX_TOTAL_WEEKS, step=1, key=WEEKS_KEY,
                            label_visibility="collapsed", help="Kaç hafta oynansın?")
        st.button(START_LABEL, key="until_start", on_click=cb_until_start, disabled=blocked,
                  help=inbox.LIVE_TEXT if live_fixture_pending() else
                  ("Sezon tamamlandı; önce yeni sezonu başlat." if cm.season_finished else None))
        ss.setdefault(IMPORTANT_KEY, True)
        st.checkbox("Önemli gelişmede dur", key=IMPORTANT_KEY,
                    help="Teklif, sakatlık, ceza, yönetim ya da sözleşme uyarısı geldiğinde haftalar durur.")


def panel(cm) -> None:
    """Gelen Kutusu'nda Devam satirinin altindaki "şuna kadar devam" bolumu (paylasilan dunyada cizilmez)."""
    if getattr(cm.rules, "shared", False) or cm.game_mode is not GameMode.CAREER:
        return
    if running():
        _run_fragment()
        return
    _control(cm)
    _result(run_state())


__all__ = ["AREA", "CHUNK_WEEKS", "IMPORTANT_KEY", "RUN_KEY", "TARGET_KEY", "TARGET_OPTIONS", "WEEKS_KEY",
           "advance", "cb_until_start", "cb_until_stop", "cb_until_tick", "panel", "run_state", "running"]
