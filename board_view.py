"""
board_view.py
=============
Faz 15C-U: **Yönetim** ekrani (CM 01/02 "Board" / "Job Centre"). Kural katmani 15C'dir (`board.py`); bu dosya
yalnizca onun `BoardDesk` API'sini cizer, tek bir kural hesaplamaz ve `board.py`'ye dokunmaz.

    render_board(db, cm, team)      nav_view.BOARD sayfasi (web_app.PAGE_RENDERERS)
        kulubu varken   Durum (sezon hedefi, lig sirasi, GUVEN CUBUGU + gerekce satirlari, uyari ve gecmisi),
                        Butce (kabul / reddet), Is ilanlari, Teklifler, Kariyer, en altta "Kulüpten ayrıl" (onayli)
        kulupsuzken     kovulan / istifa eden menajerin ekrani: tanınırlık, son kulubun kapanis satiri, acik
                        ilanlar (basvuru) ve gelen teklifler (kabul / ret). Kulup bulununca normal akisa doner.
    board_summary(cm)               Gelen Kutusu'nun ve Menajer ekraninin kisa yonetim satiri (BoardSummary)
    badge_count(db, cm)             menu rozeti: bekleyen is teklifi + acik uyari (iki ucuz sayim)
    confidence_html(...)            CM guven cubugu: cubuk + SOZCUK (Olumlu / Düşük / Görevin tehlikede)

**K12 / CM Klasik:** guven ekranda bir **cubuk ve bir sozcuktur**; ciplak sayi (58/100) yazilmaz. Kulup ilanlarinda
yalnizca 15C'nin acikca menajere verdigi alanlar gosterilir (itibar, kadro buyuklugu, kadro degeri, ilanin kalan
suresi); AI kulubunun sezon hedefi, kovulma zarinin olasiligi ve icerideki puanlar **gosterilmez** -- hedefi
menajer goreve baslayinca yonetim kurulunun mesajindan ogrenir. Gerekce satirlari (`BoardView.reasons`) 15C'nin
menajere yazdigi Turkce satirlardir, oldugu gibi cizilir.

Widget anahtarlari: bd_section (bolum), bd_budget_ok / bd_budget_no (butce), bd_job_q / bd_job_pick / bd_apply
(is ilanlari), bd_offer_ok_{id} / bd_offer_no_{id} (teklifler), bd_resign_ok / bd_resign (istifa),
lk_board_jobs / lk_board_history (tiklanabilir tablolar).
Sorgu butcesi: Durum bolumu ~3 sorgu (yonetim satiri + puan durumu + butce), Is ilanlari ~3 (acik kulupler +
kadro sayilari + basvurular), Teklifler ~2. Bolum acilmadan sorgu atilmaz.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st

import board
import links_view as lk
import nav_view
from database import session_scope
from finance import format_money
from ofm_theme import panel_title_html
from web_common import (
    flash,
    manager,
    md_escape,
    member_callback,
    requires_auth,
    reset_widgets,
    show_flash,
)

log = logging.getLogger(__name__)

AREA = "board"
MAIN_AREA = "main"                              # club_picker_view.WELCOME_AREA: sayfanin ustundeki genel mesaj
SECTION_KEY = "bd_section"
SEC_STATE, SEC_JOBS, SEC_OFFERS, SEC_HISTORY = "Durum", "İş ilanları", "Teklifler", "Kariyer"
CLUB_SECTIONS = [SEC_STATE, SEC_JOBS, SEC_OFFERS, SEC_HISTORY]
FREE_SECTIONS = [SEC_JOBS, SEC_OFFERS, SEC_HISTORY]

RESIGN_KEY = "bd_resign_ok"
JOB_QUERY_KEY = "bd_job_q"
JOB_PICK_KEY = "bd_job_pick"
BUDGET_HIDE_KEY = "bd_budget_hidden"            # "Şimdilik kalsın": öneri bu oturumda gizlenir (hiçbir şey yazılmaz)
JOB_LIMIT = 24
HISTORY_LIMIT = 20

OFF_TEXT = ("Bu kayıtta yönetim kurulu kuralı kapalı: sezon hedefi, güven, uyarı ve iş piyasası çalışmaz. "
            "Kişisel kariyerde kural varsayılan olarak açıktır.")
FREE_HINT = ("Kulüpsüzsün: yeni kulübü buradaki iş ilanlarına başvurarak ya da gelen bir teklifi kabul ederek "
             "bulursun. Hafta yine ilerler; her hafta piyasaya yeni ilanlar düşer.")
RESIGN_HINT = ("İstifa edersen kulüp menajer aramaya başlar ve sen kulüpsüz kalırsın: yeni kulübü iş "
               "piyasasından bulursun, serbestçe seçemezsin. Menajer tanınırlığın bir miktar düşer.")
BUDGET_KEPT_TEXT = ("Öneriyi şimdilik geri çevirdin: kasa ve maaş havuzu değişmedi. Öneri sezon boyunca burada "
                    "durur, istediğin an kabul edebilirsin.")
NO_JOBS_TEXT = "Şu anda menajer arayan kulüp yok. Her hafta yeni ilanlar açılır."
NO_OFFERS_TEXT = "Bekleyen iş teklifin yok."


# ===========================================================================
# ORTAK: masa, etiketler, HTML
# ===========================================================================

def desk_of(cm):
    """Menajerin yonetim masasi (board.BoardDesk). Kural kapaliyken de dondurulur: `enabled` sorulur."""
    return cm.board_desk()


def board_on(cm) -> bool:
    """Yonetim kurulu kurali bu kayitta acik mi (paylasilan dunyada dunya kuralina da bakar)."""
    try:
        return bool(cm.board_desk().enabled)
    except Exception:                                   # pragma: no cover - kural okunamazsa ekran cizilmez
        log.exception("Yönetim kurulu bayrağı okunamadı")
        return False


def _safe(fn, default=None):
    """Masa cagrisi: BoardError / veritabani hatasi sayfayi dusurmez."""
    try:
        return fn()
    except board.BoardError:
        return default
    except Exception:
        log.exception("Yönetim kurulu okunamadı")
        return default


def gap_text(view) -> str:
    """Hedefe gore durum -- SOZCUK (K12: kademe farkinin ham sayisi tek basina yazilmaz)."""
    if view is None or view.position is None:
        return "—"
    if view.gap < 0:
        return "Hedefin üstünde"
    if view.gap == 0:
        return "Hedefte"
    if view.gap == 1:
        return "Hedefin bir kademe altında"
    return f"Hedefin {view.gap} kademe altında"


def confidence_class(value: float) -> str:
    if value < board.FINAL_LEVEL:
        return "low"
    return "warn" if value < board.WARN_LEVEL else "ok"


def confidence_html(value: float, label: str, reasons: Sequence[str] = (), *,
                    title: str = "Yönetimin güveni") -> str:
    """CM guven cubugu: cubuk + sozcuk + (varsa) haftanin gerekce satirlari. Ciplak puan yazilmaz."""
    width = max(0.0, min(100.0, float(value)))
    why = "".join(f"<span>{escape(str(line))}</span>" for line in reasons if str(line).strip())
    return (f'<div class="ofm-conf {confidence_class(width)}">'
            f'<div class="h"><span>{escape(title)}</span><b>{escape(str(label))}</b></div>'
            f'<div class="track"><span class="fill" style="width:{width:.0f}%"></span></div>'
            + (f'<div class="why">{why}</div>' if why else "") + "</div>")


@dataclass(frozen=True)
class BoardSummary:
    """Gelen Kutusu / Menajer ekraninin kisa yonetim satiri (ekranin disinda hicbir sey hesaplamaz)."""
    enabled: bool
    unemployed: bool
    target: str = "—"
    position: str = "—"
    confidence: float = 0.0
    confidence_label: str = "—"
    warning: int = 0
    warning_label: str = ""
    status_label: str = ""
    gap: str = "—"
    offers: int = 0
    unemployed_weeks: int = 0

    @property
    def alert(self) -> int:
        """Menu rozeti: bekleyen teklif + acik uyari."""
        return int(self.offers) + (1 if self.warning else 0)


def board_summary(cm) -> BoardSummary | None:
    """Gelen Kutusu'nun ve Menajer ekraninin yonetim satiri; kural kapaliyken None."""
    desk = desk_of(cm)
    if not _safe(lambda: desk.enabled, False):
        return None
    free = bool(_safe(lambda: desk.unemployed, False))
    view = _safe(desk.state)
    offers = len(_safe(desk.offers, []) or [])
    if view is None:
        return BoardSummary(enabled=True, unemployed=free, offers=offers,
                            unemployed_weeks=_safe(desk.unemployed_weeks, 0) or 0)
    return BoardSummary(
        enabled=True, unemployed=free, target=view.target_label,
        position=f"{view.position}. / {view.league_size}" if view.position else "—",
        confidence=view.confidence, confidence_label=view.confidence_label,
        warning=view.warning, warning_label=view.warning_label, status_label=view.status_label,
        gap=gap_text(view), offers=offers,
        unemployed_weeks=_safe(desk.unemployed_weeks, 0) or 0,
    )


def badge_count(db, cm) -> int:
    """
    Menu rozeti (web_app.nav_counts): bekleyen is teklifi + acik uyari. Iki ucuz sayim; kural kapaliyken sorgu
    atilmaz. Masanin `offers()` yolu kulup basina bir sorgu daha atardi, burada tek COUNT kullanilir.
    """
    from sqlalchemy import func, select

    from models import BoardOffer, BoardState

    try:
        if not cm.board_desk().enabled:
            return 0
        import inbox

        seat = inbox.manager_id_for(cm)
        owner = BoardOffer.manager_id.is_(None) if seat is None else BoardOffer.manager_id == int(seat)
        count = int(db.scalar(select(func.count()).select_from(BoardOffer).where(
            owner, BoardOffer.status == board.OFFER_PENDING)) or 0)
        team = cm.user_team
        if team is not None:
            owner_state = (BoardState.manager_id.is_(None) if seat is None
                           else BoardState.manager_id == int(seat))
            warning = db.scalar(select(BoardState.warning).where(
                owner_state, BoardState.season == cm.season, BoardState.team_id == team.id))
            count += 1 if int(warning or 0) else 0
        return count
    except Exception:                                   # pragma: no cover - rozet sayfayi dusurmez
        log.exception("Yönetim kurulu rozeti okunamadı")
        return 0


# ===========================================================================
# CALLBACK'LER (veriyi degistiren tek yer)
# ===========================================================================

def _desk_in(db):
    return manager(db).board_desk()


@member_callback
def cb_board_budget() -> None:
    """Sezon basi butce onerisini kabul eder (kasa hedef seviyeye tamamlanir; kasadan para ALINMAZ)."""
    try:
        with session_scope() as db:
            view = _desk_in(db).accept_budget()
    except board.BoardError as exc:
        flash(AREA, "error", str(exc))
        return
    st.session_state.pop(BUDGET_HIDE_KEY, None)
    grant = f" Kasaya {format_money(view.transfer_grant)} eklendi." if view.transfer_grant else ""
    flash(AREA, "success", "Yönetimin bütçe önerisini kabul ettin." + grant)


@requires_auth
def cb_board_budget_decline() -> None:
    """"Şimdilik kalsın": hiçbir şey yazılmaz, öneri PENDING kalır; bu oturumda kart kapanır."""
    st.session_state[BUDGET_HIDE_KEY] = True
    flash(AREA, "info", BUDGET_KEPT_TEXT)


@member_callback
def cb_board_apply(team_id: int) -> None:
    """Is ilanina basvuru (kulubun karari 15C'nin tohumlu zariyla verilir)."""
    try:
        with session_scope() as db:
            result = _desk_in(db).apply(int(team_id))
    except board.BoardError as exc:
        flash(AREA, "error", str(exc))
        return
    flash(AREA, "success" if result.accepted else "warning", result.text)


@member_callback
def cb_board_accept(offer_id: int) -> None:
    """Is teklifini kabul eder: menajer yeni kulubun basina gecer."""
    try:
        with session_scope() as db:
            view = _desk_in(db).accept_offer(int(offer_id))
    except board.BoardError as exc:
        flash(AREA, "error", str(exc))
        return
    reset_widgets(JOB_PICK_KEY, JOB_QUERY_KEY, RESIGN_KEY, BUDGET_HIDE_KEY)
    st.session_state[SECTION_KEY] = SEC_STATE
    flash(MAIN_AREA, "success",
          f"{view.team_name} menajeri oldun. Sezon hedefini yönetim kurulunun mesajında bulursun.")
    nav_view.goto(nav_view.HOME)


@member_callback
def cb_board_decline(offer_id: int) -> None:
    try:
        with session_scope() as db:
            _desk_in(db).decline_offer(int(offer_id))
    except board.BoardError as exc:
        flash(AREA, "error", str(exc))
        return
    flash(AREA, "info", "Teklifi geri çevirdin.")


@member_callback
def cb_board_resign() -> None:
    """Istifa (onay kutusu isaretliyken): kulup ilan acar, menajer kulupsuz kalir."""
    try:
        with session_scope() as db:
            _desk_in(db).resign()
    except board.BoardError as exc:
        flash(AREA, "error", str(exc))
        return
    reset_widgets(RESIGN_KEY, JOB_PICK_KEY, BUDGET_HIDE_KEY)
    st.session_state[SECTION_KEY] = SEC_JOBS
    flash(AREA, "warning", "Kulübünden ayrıldın. Yeni kulübü iş ilanlarından bulacaksın.")


@requires_auth
def cb_board_open(section: str | None = None) -> None:
    """Baska ekranlardan Yonetim sayfasini acar (Gelen Kutusu satiri, Menajer ekrani)."""
    if section in CLUB_SECTIONS:
        st.session_state[SECTION_KEY] = section
    nav_view.goto(nav_view.BOARD, section=nav_view.SEC_CLUB)


# ===========================================================================
# BOLUM: DURUM (hedef, guven, uyari, butce, istifa)
# ===========================================================================

def state_strip(view) -> None:
    """Sezon karnesinin bilgi satiri: hedef, sira, hedefe gore durum, kupa beklentisi, gorevde gecen sure."""
    strip: list[tuple[str, object]] = [("Sezon hedefi", view.target_label)]
    strip.append(("Lig sırası", f"{view.position}. / {view.league_size}" if view.position else "—"))
    strip.append(("Hedefe göre", gap_text(view)))
    if view.cup_target_label:
        strip.append(("Kupa beklentisi", view.cup_target_label))
    strip.append(("Görevde", f"{view.weeks_in_charge} hafta"))
    strip.append(("Durum", view.warning_label if view.warning else view.status_label))
    st.markdown(nav_view.facts_html(strip), unsafe_allow_html=True)


def warning_block(view) -> None:
    """Uyari serididi: son uyari kirmizi, ilk uyari sari; uyari yoksa hicbir sey cizilmez."""
    if not view.warning:
        return
    if view.warning >= board.WARN_FINAL:
        st.error("Son uyarı: yönetim kurulu görevine son vermeyi görüşüyor. Sonuçlar düzelmezse ayrılman istenecek.")
    else:
        st.warning("Yönetim kurulu seni uyardı: sonuçlar beklentinin altında.")


def budget_panel(desk) -> None:
    """Sezon basi butce onerisi: seviye, kasaya eklenecek tutar, maas havuzu + Kabul et / Şimdilik kalsın."""
    view = _safe(desk.budget)
    if view is None:
        return
    st.markdown(panel_title_html("Sezon bütçesi"), unsafe_allow_html=True)
    st.markdown(nav_view.facts_html([
        ("Transfer kasası (hedef)", format_money(view.transfer_budget)),
        ("Kabul edilirse kasaya", format_money(view.transfer_grant) if view.transfer_grant else "—"),
        ("Haftalık maaş havuzu", format_money(view.wage_budget)),
        ("Durum", "Kabul edildi" if view.accepted else "Öneri bekliyor"),
    ]), unsafe_allow_html=True)
    st.caption(md_escape(view.text))
    if view.accepted or st.session_state.get(BUDGET_HIDE_KEY):
        if not view.accepted:
            st.caption(BUDGET_KEPT_TEXT)
        return
    with st.container(horizontal=True, key="bd_acts"):
        st.button("Bütçeyi kabul et", key="bd_budget_ok", on_click=cb_board_budget, type="primary",
                  help="Kasa bu seviyeye tamamlanır, maaş havuzu yukarı çekilir. Kasadan para alınmaz.")
        st.button("Şimdilik kalsın", key="bd_budget_no", on_click=cb_board_budget_decline,
                  help="Hiçbir şey değişmez; öneri sezon boyunca burada durur.")


def resign_panel(cm) -> None:
    """"Kulüpten ayrıl": onay kutusu isaretlenmeden dugme pasiftir (15A-U fesih deseni)."""
    st.markdown(panel_title_html("Kulüpten ayrıl"), unsafe_allow_html=True)
    st.caption(RESIGN_HINT)
    ok = st.checkbox("İstifa etmek istediğimi onaylıyorum", key=RESIGN_KEY)
    st.button("Kulüpten ayrıl", key="bd_resign", on_click=cb_board_resign, disabled=not ok,
              help="Önce onay kutusunu işaretle." if not ok else "Görevinden ayrılırsın.")


def state_section(cm, desk, view) -> None:
    if view is None:
        st.markdown('<div class="cm-empty">Yönetim kurulunun bu sezon için henüz bir karnesi yok: sezon hedefi '
                    'ilk hafta oynandığında açılır.</div>', unsafe_allow_html=True)
    else:
        state_strip(view)
        st.markdown(confidence_html(view.confidence, view.confidence_label, view.reasons),
                    unsafe_allow_html=True)
        st.caption("Yönetimin güveni her hafta sonuç, hedefe uzaklık, mali durum ve kadro hareketleriyle değişir.")
        warning_block(view)
    budget_panel(desk)
    resign_panel(cm)


# ===========================================================================
# BOLUM: IS ILANLARI
# ===========================================================================

JOB_STATUS = {"applied": "Başvuruldu", "ok": "Uygun", "no": "Tanınırlık yetersiz"}


def job_rows(jobs: Sequence) -> list[dict]:
    return [{
        "Kulüp": job.team_name,
        "Lig": job.league_name or "—",
        "İtibar": int(job.reputation),
        "Kadro": int(job.squad_size),
        "Kadro değeri": format_money(job.squad_value),
        "İlan": f"{job.closes_in_weeks} hafta" if job.closes_in_weeks else "Son hafta",
        "Durum": JOB_STATUS["applied"] if job.applied else (JOB_STATUS["ok"] if job.eligible else JOB_STATUS["no"]),
        "_tid": int(job.team_id),
    } for job in jobs]


def jobs_section(desk) -> None:
    """Acik is ilanlari: yogun tablo (kulup adina tik -> kulup sayfasi) + basvuru."""
    with st.container(horizontal=True, key="bd_jobrow", vertical_alignment="bottom"):
        st.text_input("Kulüp ara", key=JOB_QUERY_KEY, placeholder="Kulüp adı", label_visibility="collapsed")
    query = str(st.session_state.get(JOB_QUERY_KEY) or "").strip()
    jobs = _safe(lambda: desk.jobs(query, limit=JOB_LIMIT), []) or []
    if not jobs:
        st.markdown(f'<div class="cm-empty">{escape(NO_JOBS_TEXT)}</div>', unsafe_allow_html=True)
        return
    frame, ids = lk.split_ids(job_rows(jobs))
    lk.link_table("lk_board_jobs", frame, clubs={"Kulüp": ids["_tid"]})
    open_jobs = [job for job in jobs if job.eligible and not job.applied]
    st.caption("Kulübün sezon hedefini göreve başladığında yönetim kurulunun mesajından öğrenirsin.")
    if not open_jobs:
        st.caption("Başvurabileceğin açık ilan yok: tanınırlığın yeterli değil ya da hepsine başvurdun. "
                   "Kulüpsüz geçen her hafta kulüplerin beklentisini bir miktar düşürür.")
        return
    names = {job.team_id: job.team_name for job in open_jobs}
    if st.session_state.get(JOB_PICK_KEY) not in names:
        reset_widgets(JOB_PICK_KEY)
    with st.container(horizontal=True, key="bd_jobacts", vertical_alignment="bottom"):
        pick = st.selectbox("Başvurulacak kulüp", list(names), key=JOB_PICK_KEY,
                            format_func=lambda i: names.get(i, str(i)), label_visibility="collapsed")
        st.button("Başvur", key="bd_apply", on_click=cb_board_apply, args=(pick,), type="primary",
                  disabled=pick is None,
                  help="Kulüp başvurunu değerlendirir; kabul ederse bekleyen bir iş teklifi olarak gelir.")


# ===========================================================================
# BOLUM: TEKLIFLER
# ===========================================================================

def offer_card(offer) -> None:
    rows = [("Kulüp", offer.team_name), ("Lig", offer.league_name or "—"), ("Kulüp itibarı", offer.reputation),
            ("Teklif", offer.kind_label)]
    if offer.expires_in_weeks is not None:
        rows.append(("Geçerlilik", f"{offer.expires_in_weeks} hafta"))
    st.markdown(nav_view.name_title_html(offer.team_name), unsafe_allow_html=True)
    st.markdown(nav_view.pairs_html(rows), unsafe_allow_html=True)
    st.caption(md_escape(offer.text))
    with st.container(horizontal=True, key=f"bd_offer_{offer.id}"):
        st.button("Kabul et", key=f"bd_offer_ok_{offer.id}", on_click=cb_board_accept, args=(offer.id,),
                  type="primary", help="Göreve başlarsın; varsa şimdiki kulübünden ayrılırsın.")
        st.button("Reddet", key=f"bd_offer_no_{offer.id}", on_click=cb_board_decline, args=(offer.id,))
    lk.open_buttons(f"bd_of_{offer.id}", [("Kulüp sayfası", nav_view.CLUB_PAGE, offer.team_id)])


def offers_section(desk) -> None:
    offers = _safe(desk.offers, []) or []
    if not offers:
        st.markdown(f'<div class="cm-empty">{escape(NO_OFFERS_TEXT)}</div>', unsafe_allow_html=True)
    for offer in offers:
        offer_card(offer)
    closed = [o for o in (_safe(lambda: desk.offers(include_closed=True), []) or [])
              if o.status != board.OFFER_PENDING]
    if not closed:
        return
    with st.expander(f"Geçmiş başvurular ve teklifler ({len(closed)})"):
        st.markdown(nav_view.table_html(
            ["Sezon", "Hafta", "Kulüp", "Tür", "Sonuç"],
            [[o.season, o.week, o.team_name, o.kind_label, board.STATE_LABELS.get(o.status, o.status)]
             for o in closed[:20]], left=(2, 3, 4)), unsafe_allow_html=True)


# ===========================================================================
# BOLUM: KARIYER (yonetim karneleri ve uyarilar)
# ===========================================================================

OFFER_STATUS_LABELS = {
    board.OFFER_PENDING: "Bekliyor", board.OFFER_ACCEPTED: "Kabul edildi",
    board.OFFER_DECLINED: "Reddedildi", board.OFFER_EXPIRED: "Süresi doldu",
    board.OFFER_WITHDRAWN: "Geri çekildi",
}


def history_rows(views: Sequence) -> list[dict]:
    return [{
        "Sezon": view.season,
        "Kulüp": view.team_name,
        "Hedef": view.target_label,
        "Sıra": f"{view.position}." if view.position else "—",
        "Yönetim": view.confidence_label,
        "Uyarı": view.warning_label if view.warning else "—",
        "Durum": view.status_label,
        "_tid": view.team_id,
    } for view in views]


def history_section(desk) -> None:
    """Menajerin yonetim karneleri: sezon, kulup, hedef, sira, guven SOZCUGU, uyari ve kapanis durumu."""
    views = _safe(lambda: desk.history(limit=HISTORY_LIMIT), []) or []
    if not views:
        st.markdown('<div class="cm-empty">Yönetim kurulu karnen henüz boş.</div>', unsafe_allow_html=True)
        return
    frame, ids = lk.split_ids(history_rows(views))
    lk.link_table("lk_board_history", frame, clubs={"Kulüp": ids["_tid"]})
    st.caption("Uyarılar ve kovulma kararları gelen kutundaki yönetim mesajlarında ayrıntısıyla yazar.")


# ===========================================================================
# EKRAN
# ===========================================================================

def _section(options: list[str], counts: dict[str, int]) -> str:
    if st.session_state.get(SECTION_KEY) not in options:
        reset_widgets(SECTION_KEY)
    return st.radio("Bölüm", options, key=SECTION_KEY, horizontal=True, label_visibility="collapsed",
                    format_func=lambda s: f"{s} ({counts[s]})" if counts.get(s) else s)


def unemployed_screen(cm, desk) -> None:
    """Kovulan / istifa eden menajerin ekrani (CM: "The Manager" + is merkezi)."""
    import reputation

    rep = float(cm.manager_reputation)
    level = reputation.level(rep)
    weeks = _safe(desk.unemployed_weeks, 0) or 0
    last = _safe(desk.state)
    st.markdown(panel_title_html("Kulüpsüz menajer"), unsafe_allow_html=True)
    strip: list[tuple[str, object]] = [
        ("Unvan", level.title), ("Seviye", f"{level.level}/10"),
        ("Menajer tanınırlığı", reputation.label(rep)),
        ("Kulüpsüz", f"{weeks} hafta"),
    ]
    if last is not None:
        strip += [("Son kulüp", last.team_name), ("Ayrılış", last.status_label)]
    st.markdown(nav_view.facts_html(strip), unsafe_allow_html=True)
    st.caption(FREE_HINT)
    if last is not None and last.reasons:
        st.markdown(confidence_html(last.confidence, last.confidence_label, last.reasons,
                                    title=f"{last.team_name} · son hafta"), unsafe_allow_html=True)
    counts = {SEC_OFFERS: len(_safe(desk.offers, []) or [])}
    if st.session_state.get(SECTION_KEY) not in FREE_SECTIONS:
        st.session_state[SECTION_KEY] = SEC_JOBS
    section = _section(FREE_SECTIONS, counts)
    if section == SEC_OFFERS:
        offers_section(desk)
    elif section == SEC_HISTORY:
        history_section(desk)
    else:
        jobs_section(desk)


def club_screen(cm, desk, team) -> None:
    view = _safe(desk.state)
    counts = {SEC_OFFERS: len(_safe(desk.offers, []) or [])}
    section = _section(CLUB_SECTIONS, counts)
    if section == SEC_JOBS:
        jobs_section(desk)
    elif section == SEC_OFFERS:
        offers_section(desk)
    elif section == SEC_HISTORY:
        history_section(desk)
    else:
        state_section(cm, desk, view)


def render_board(db, cm, team) -> None:
    """Yonetim sayfasi (web_app.PAGE_RENDERERS[nav_view.BOARD]). `team` None olabilir (kovulma / istifa)."""
    show_flash(AREA)
    desk = desk_of(cm)
    if not _safe(lambda: desk.enabled, False):
        st.info(board.SHARED_OFF_TEXT if cm.rules.shared else OFF_TEXT)
        return
    if team is None or _safe(lambda: desk.unemployed, False):
        unemployed_screen(cm, desk)
        return
    club_screen(cm, desk, team)


__all__ = ["AREA", "BoardSummary", "CLUB_SECTIONS", "FREE_SECTIONS", "SECTION_KEY", "SEC_HISTORY", "SEC_JOBS",
           "SEC_OFFERS", "SEC_STATE", "badge_count", "board_on", "board_summary", "cb_board_accept",
           "cb_board_apply", "cb_board_budget", "cb_board_budget_decline", "cb_board_decline", "cb_board_open",
           "cb_board_resign", "confidence_html", "desk_of", "gap_text", "history_section", "jobs_section",
           "offers_section", "render_board", "state_section"]
