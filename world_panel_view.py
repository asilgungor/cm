"""
world_panel_view.py
===================
Paylasilan dunya kenar cubugu paneli (Faz 12 / 14. Asama, 12A): dunya adi, sezon/hafta, sure sayaci,
"3/5 hazir" ve bekleyenler, hazir / zorla ilerlet dugmeleri, bildirim rozeti, "Dunyalar" (sb_worlds).
Paylasilan dunyada takim secicinin (sb_team / sb_set_team / sb_change_mode) ve kariyer tohumunun yerini alir.

    sidebar_panel(ctx, team_name, counts=None) -> kenar cubugu (flash alani: world)
    inbox_counts(db, ctx)          -> rozet sayilari (MarketHub.counts); Faz 13I menu sayaclari da bunu kullanir
    ready_panel(db, ctx)           -> Lig sekmesi: "Sonraki haftayi oyna" yerine hazir paneli (lg_ready)
    latest_report(db, ctx)         -> menajerin son hafta raporu (manager_week_reports; session_state degil)
    advance_if_due(ctx)            -> sayfa yuklenirken suresi dolan / herkesin hazir oldugu haftayi ilerletir
                                      (world_manager.maybe_advance_due: beklemez, mesgulse hicbir sey yapmaz)
    advance_world(ctx, trigger, expected) -> hazir / zorla callback'lerinin ortak ilerletmesi

Hazir akisi (plan 1.5 "kilit yukseltme yok"): cb_world_ready once hazir bayragini SHARED kilit altinda kendi
isleminde commit eder; herkes hazirsa world_manager.try_advance(ctx, READY, expected=(sezon, hafta)) YENI islemde
(try_exclusive) denenir. Mesgulse (AdvanceInProgress) bir sonraki sayfa yuklemesi (maybe_advance_due) yeniden dener.
Callback'ler member_callback / admin_callback ile ACIKCA sarilir.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import streamlit as st
from sqlalchemy.exc import OperationalError, SQLAlchemyError

import world_manager
import worlds
from database import session_scope
from ofm_theme import panel_title_html
from turn_rules import AdvanceTrigger
from web_common import (
    WORLD_KIND_SHARED,
    admin_callback,
    callback_world,
    flash,
    is_lock_timeout,
    md_escape,
    member_callback,
    reset_widgets,
    show_flash,
)

if TYPE_CHECKING:
    from world_manager import ManagerReport, TurnStatus
    from worlds import WorldContext

log = logging.getLogger(__name__)

PHASE_LABELS = {
    world_manager.PHASE_SEASON: "",
    world_manager.PHASE_SEASON_END: "sezon bitti: sıradaki ilerleme yeni sezonu başlatır",
    world_manager.PHASE_CLOSE_SEASON: "sezon arası milli maç günleri",
}
READY_OFF_TEXT = "Bu dünyada hazır kontrolü kapalı; hafta süre dolunca ilerler."
NO_CLUB_TEXT = "Hazır bildirmek için önce bir kulüp seç."
BUSY_ADVANCE_TEXT = "Hafta şu an başka bir istekle oynatılıyor; birkaç saniye sonra güncel durumu göreceksin."
# Hafta ilerleyince eski haftaya ait ekran durumu atilir (cb_play_week ile ayni anahtarlar)
WEEK_WIDGETS = ("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee", "hneg")


# ===========================================================================
# METIN YARDIMCILARI
# ===========================================================================

def deadline_text(status: TurnStatus) -> str:
    """Tur bitisi: kalan sure / doldu / sure siniri yok."""
    if status.deadline_at is None or status.seconds_left is None:
        return "⏳ Süre sınırı yok: herkes hazır olunca ya da yönetici oynatınca hafta ilerler."
    if status.seconds_left <= 0:
        return "⌛ Süre doldu: hafta ilk fırsatta oynatılacak."
    minutes = status.seconds_left // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = ([f"{days} gün"] if days else []) + ([f"{hours} sa"] if hours or days else []) + [f"{mins} dk"]
    return "⏳ Kalan süre: " + " ".join(parts)


def ready_text(status: TurnStatus) -> str:
    """'1/2 hazır' + bekleyenler (adlar kacisli)."""
    text = f"✅ {status.ready}/{status.active} hazır"
    if status.waiting_names:
        text += " · Bekleniyor: " + ", ".join(md_escape(name) for name in status.waiting_names)
    return text


def season_text(status: TurnStatus) -> str:
    phase = PHASE_LABELS.get(status.phase, "")
    return f"Sezon {status.season} · Hafta {status.week}" + (f" · {phase}" if phase else "")


# ===========================================================================
# KENAR CUBUGU
# ===========================================================================

def inbox_counts(db, ctx: WorldContext):
    """
    Rozet sayilari (market_hub.InboxCounts: yanit bekleyen teklif, okunmamis mesaj / bildirim, inceleme). Koltugu
    olmayan izleyici ya da okuma hatasi -> None (cizimi bozmaz; savepoint).
    """
    try:
        import market_hub
        from career_manager import CareerManager

        with db.begin_nested():
            cm = CareerManager(db, seed=ctx.world_seed, manager_user_id=ctx.user_id)
            if cm.acting_seat is None:
                return None
            return market_hub.MarketHub(cm).counts()
    except (NotImplementedError, ImportError, AttributeError, TypeError, ValueError, SQLAlchemyError) as exc:
        log.warning("Dünya rozeti okunamadı: %s", exc)
        return None


def badge_lines(counts, admin: bool) -> list[str]:
    """Kenar cubugu rozeti: her sayac kendi etiketiyle (sekme etiketlerinde sayac yok)."""
    lines = [f"📨 Yanıt bekleyen teklif: {counts.offers_action}",
             f"✉️ Okunmamış mesaj: {counts.messages}",
             f"🔔 Okunmamış bildirim: {counts.notifications}"]
    if admin:
        lines.append(f"⚖️ İnceleme bekleyen anlaşma: {counts.reviews}")
    return lines


def sidebar_panel(ctx: WorldContext | None, team_name: str | None = None, counts=None) -> None:
    """
    Kenar cubugu (st.sidebar icinde cagrilir; flash alani: world). counts: cagiranin okudugu rozet sayilari (Faz 13I
    menusu ayni sayilari menu etiketlerinde kullanir; ikinci kez sorgulanmaz). None ise burada okunur.
    """
    from world_lobby_view import cb_open_lobby

    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        show_flash("world")
        st.button("🌍 Dünyalar", key="sb_worlds", on_click=cb_open_lobby, width="stretch")
        return
    with session_scope() as db:
        wc = world_manager.WorldController(db, ctx)
        status = wc.turn_status()
        rules = wc.rules
        admin = wc.is_admin()
        if counts is None:
            counts = inbox_counts(db, ctx)

    st.markdown(panel_title_html(f"🌍 {ctx.name}"), unsafe_allow_html=True)
    show_flash("world")
    st.caption(season_text(status))
    st.caption(f"🏟️ Kulübün: **{md_escape(team_name)}**" if team_name else "🏟️ Henüz kulübün yok.")
    st.caption(deadline_text(status))
    st.caption(ready_text(status))
    ready_button(status, rules.ready_check, key="wp_ready")
    if status.can_force:
        st.button("⏩ Haftayı şimdi oynat", key="wp_force", on_click=cb_world_force,
                  args=(status.season, status.week), width="stretch",
                  help="Yönetici: hazır olmayanları beklemeden haftayı oynatır (hazır olmayanlar kaçırmış sayılabilir).")
    if counts is not None:
        for line in badge_lines(counts, admin):
            st.caption(line)
        st.button("🔔 Bildirimleri okundu say", key="wp_mark_read", on_click=cb_world_mark_read,
                  disabled=counts.notifications == 0, width="stretch",
                  help="Yalnızca bildirimleri okundu sayar; mesajlar açılınca okunur, teklif sayacı yanıt verince "
                       "düşer (📨 Teklifler & Mesajlar).")
    st.button("🌍 Dünyalar", key="sb_worlds", on_click=cb_open_lobby, width="stretch",
              help="Dünyalarım, dünya kur, davet koduyla katıl, açık dünyalar.")


def ready_button(status: TurnStatus, ready_check: bool, *, key: str) -> None:
    """Hazir / hazir degil dugmesi (wp_ready, lg_ready): hafta beklentisi dugmeye baglanir (bayat tik reddedilir)."""
    if not ready_check:
        st.caption(READY_OFF_TEXT)
        return
    if status.me_ready:
        st.button("↩️ Hazır değilim", key=key, on_click=cb_world_ready, args=(False, status.career_week),
                  width="stretch", help="Hazır bildirimini geri al (hafta henüz oynatılmadı).")
        return
    st.button("✅ Hazırım", key=key, on_click=cb_world_ready, args=(True, status.career_week), type="primary",
              width="stretch", disabled=not status.can_ready, help=None if status.can_ready else NO_CLUB_TEXT)


def ready_panel(db, ctx: WorldContext) -> None:
    """Lig sekmesi (paylasilan dunya): tur durumu + hazir dugmesi (lg_ready); yonetici icin zorla oynat (lg_force)."""
    wc = world_manager.WorldController(db, ctx)
    status = wc.turn_status()
    rules = wc.rules
    st.caption(f"🌍 {md_escape(ctx.name)} · {season_text(status)} · {deadline_text(status)} · {ready_text(status)}")
    b1, b2 = st.columns(2)
    with b1:
        ready_button(status, rules.ready_check, key="lg_ready")
    if status.can_force:
        b2.button("⏩ Haftayı şimdi oynat", key="lg_force", on_click=cb_world_force, args=(status.season, status.week),
                  width="stretch")
    if status.phase != world_manager.PHASE_SEASON:
        st.info("Sezon tamamlandı: herkes hazır olunca (ya da yönetici oynatınca) yeni sezon başlar.")


def latest_report(db, ctx: WorldContext) -> ManagerReport | None:
    """Menajerin kaydedilmis en son hafta raporu (DB; sayfa yenilense de kaybolmaz)."""
    reports = world_manager.WorldController(db, ctx).my_reports(limit=1)
    return reports[0] if reports else None


# ===========================================================================
# ILERLETME
# ===========================================================================

def advance_world(ctx: WorldContext, trigger: AdvanceTrigger, expected: tuple[int, int]):
    """
    try_advance (kendi islemi, try_exclusive). Acik oturum YOKKEN cagrilir. Sonuc mesaji 'world' alanina yazilir.
    Mesgul -> bilgi mesaji (sayfa yuklemesi yeniden dener); yetkisiz zorlama -> hata.
    """
    try:
        result = world_manager.try_advance(ctx, trigger, expected)
    except world_manager.AdvanceInProgress:
        flash("world", "info", BUSY_ADVANCE_TEXT)
        return None
    except world_manager.TurnError as exc:
        flash("world", "error", str(exc))
        return None
    if result.advanced:
        flash("world", "success", f"⏩ {result.message}")
        reset_widgets(*WEEK_WIDGETS)
    elif trigger is AdvanceTrigger.FORCED or result.message != world_manager.MOVED_TEXT:
        flash("world", "info", result.message)
    return result


def advance_if_due(ctx: WorldContext | None) -> None:
    """Sayfa yuklenirken (cizim oturumu acilmadan): suresi dolan / herkesin hazir oldugu tur. Beklemez."""
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        return
    try:
        result = world_manager.maybe_advance_due(ctx)
    except (world_manager.TurnError, worlds.WorldError) as exc:
        log.warning("Dünya %s ilerleme denetimi: %s", ctx.world_id, exc)
        return
    except OperationalError as exc:
        if not is_lock_timeout(exc):
            raise
        return
    if result is not None and result.advanced:
        flash("world", "info", f"⏩ {result.message}")
        reset_widgets(*WEEK_WIDGETS)


# ===========================================================================
# CALLBACK'LER
# ===========================================================================

@member_callback
def cb_world_ready(ready: bool, expected_career_week: int) -> None:
    """Hazir bayragi (SHARED kilit, kendi islemi, commit) -> herkes hazirsa YENI islemde try_advance(READY)."""
    ctx = callback_world()
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        return
    try:
        with session_scope() as db:
            status = world_manager.WorldController(db, ctx).set_ready(bool(ready), int(expected_career_week))
    except world_manager.StaleTurnError as exc:
        flash("world", "warning", str(exc))
        return
    except (world_manager.TurnError, worlds.WorldError) as exc:
        flash("world", "error", str(exc))
        return
    if not ready:
        flash("world", "info", "Hazır bildirimin geri alındı.")
        return
    flash("world", "success", f"✅ Hazırsın ({status.ready}/{status.active} hazır).")
    if status.active and status.ready >= status.active:
        advance_world(ctx, AdvanceTrigger.READY, (status.season, status.week))


@admin_callback
def cb_world_force(expected_season: int, expected_week: int) -> None:
    """Yonetici: haftayi hazir beklemeden oynatir (rol try_advance icinde yeniden okunur)."""
    ctx = callback_world()
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        return
    advance_world(ctx, AdvanceTrigger.FORCED, (int(expected_season), int(expected_week)))


@member_callback
def cb_world_mark_read() -> None:
    """Yalnizca BILDIRIMLER okundu sayilir (mesajlar ve teklifler etkilenmez)."""
    ctx = callback_world()
    if ctx is None or ctx.kind != WORLD_KIND_SHARED:
        return
    import messaging

    with session_scope() as db:
        wc = world_manager.WorldController(db, ctx)
        seat = wc.my_seat()
        if seat is None or seat.id is None:
            return
        count = messaging.Messaging(db, seat, wc.is_admin()).mark_notifications_read()
    flash("world", "info", f"🔔 {count} bildirim okundu olarak işaretlendi.")
