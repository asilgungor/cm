"""
national_view.py
================
Milli Takim sekmesi (Faz 12 / 14. Asama, 12C; web_app.TAB_NATIONAL, dunya kurallarinda milli takimlar acikken).
Kurallar national_teams.NationalTeams'tedir (commit etmez); bu modul yalnizca SUNUM + callback. Callback'ler
web_common.member_callback ile ACIKCA sarilir (tests/test_world_schema.py denetler). Flash alani: national.

    national_tab(db, cm, team)   -> ust bilgi: Dunya Kupasi durumu (world_cup_status), milli gorev seridi (ulus, itibar
                                    sirasi, sozlesme, kadro, kampanya durumu) ya da "gorev yok".
        sezon arasi              -> kisisel dunyada milli mac gunleri bekliyorsa neden + nt_play_matchday (bir mac
                                    gunu oynatir: play_close_season_matchday); paylasilan dunyada yalnizca bilgi (tur
                                    motoru her turda bir gun oynatir)
        gorev yok                -> bekleyen is teklifleri: nt_accept_{offer_id}, nt_decline_{offer_id} (ulus, itibar,
                                    sozlesme suresi, kalan gecerlilik); nt_section: Fikstur / Gruplar / Dunya Kupasi /
                                    Uluslar
        gorev var (nt_section)   -> Ilk 11: nt_formation + nt_formation_save, taktik tahtasi, mevcut ilk 11 denetimi
                                    (LineupCheck), nt_xi (<= 11) + nt_bench (<= MAX_BENCH), gorev onizlemesi,
                                    nt_lineup_save, nt_auto_lineup
                                    Kadro: nt_query + nt_pos (aday tablosu, en fazla CANDIDATE_ROWS satir),
                                    nt_callups (cagri listesi; sinir sunucuda: MAX_CALLUPS) + nt_save_callups
                                    Fikstur (nt_fx_mine) / Gruplar (GroupRowView) / Dunya Kupasi (bracket_html,
                                    sampiyonlar arsivi) / Uluslar
                                    istifa: nt_resign_ok (onay) + nt_resign
    new_season_hint(cm)          -> web_app Lig / Devler Arenasi sekmesi (kisisel dunya): sezon bitti ama milli mac
                                    gunleri bekliyorsa yeni sezon dugmesinin yaninda neden ve nereden oynanacagi

Guvenlik: ulus her zaman sunucuda oturumun koltugundan (NationalTeams.my_nation / acting seat); widget'tan gelen
oyuncu / teklif id'leri yalnizca secim, yetki ve uyruk denetimi NationalTeams'te. NationalTeamError islem ICINDE
yakalanir (islem commit edilir: suresi dolan / geri cekilen teklif durumu kalici), Turkce mesaj flash'a kacisli yazilir.
Kullanici / veritabani metinleri md_escape ile duz metin; HTML yalnizca kacisli yardimcilarla (stat_strip_html,
group_tables_html, bracket_html, pitch.lineup_svg). Tablolar (st.dataframe) duz metin gosterir.
Cizim maliyeti: aday havuzu (tum A takim oyunculari) yalnizca Kadro bolumu aciksa okunur; varsayilan bolum Ilk 11
(yalnizca milli kadro satirlari).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st
from sqlalchemy import select

import pitch
import player_view
from bracket_view import GroupRowView, bracket_html, group_tables_html
from career_manager import WeekReport
from club_directory import plain_key
from database import session_scope
from extensions import rules_of
from models import GameMode, Nation, Position
from national_rules import MAX_CALLUPS
from national_teams import (
    CALLUP_NONE,
    COMPETITION_LABELS,
    COMPETITIONS,
    DISABLED_TEXT,
    NO_SEAT_TEXT,
    CallupRow,
    NationalJobOfferView,
    NationalTeamError,
    NationalTeams,
    NationView,
)
from ofm_theme import panel_title_html, stat_strip_html
from stars import FULL, GLYPH_FULL, GLYPH_HALF, HALF, UNKNOWN
from tactics import (
    DEFAULT_FORMATION,
    FORMATIONS,
    MAX_BENCH,
    ROLE_ORDER,
    formation_slots,
    role_counts,
    validate_lineup,
)
from web_common import (
    flash,
    is_shared_world,
    manager,
    md_escape,
    member_callback,
    reset_widgets,
    shared_page_world,
    show_flash,
)

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Team
    from tactics import LineupCheck

AREA = "national"
TAB_LABEL = "🌍 Milli Takım"

SEC_XI, SEC_SQUAD, SEC_FIXTURES = "⚽ İlk 11", "📋 Kadro", "📅 Fikstür"
SEC_GROUPS, SEC_WORLD_CUP, SEC_NATIONS = "📊 Gruplar", "🏆 Dünya Kupası", "🌐 Uluslar"
JOB_SECTIONS = [SEC_XI, SEC_SQUAD, SEC_FIXTURES, SEC_GROUPS, SEC_WORLD_CUP, SEC_NATIONS]
PUBLIC_SECTIONS = [SEC_FIXTURES, SEC_GROUPS, SEC_WORLD_CUP, SEC_NATIONS]

POS_ALL = "Tümü"
POS_OPTIONS = [POS_ALL] + [p.value for p in Position]
POSITION_VALUES = frozenset(p.value for p in Position)
CANDIDATE_ROWS = 80                 # Kadro bolumunde aday tablosunda en fazla bu kadar satir
XI_SIZE = 11
STATUS_LABELS = {"XI": "İlk 11", "BENCH": "Kulübe", "OUT": "Kadro dışı", CALLUP_NONE: "Çağrılmadı"}

# Senkron imzalari: veritabanindaki kadro degisince (kayit, asistan, mac gunu) secim kutulari yeniden doldurulur
CALLUPS_SIG, LINEUP_SIG, FORMATION_SIG = "nt_callups_sig", "nt_lineup_sig", "nt_formation_sig"
WIDGET_KEYS = ("nt_section", "nt_query", "nt_pos", "nt_callups", "nt_xi", "nt_bench", "nt_formation", "nt_fx_mine",
               "nt_resign_ok", CALLUPS_SIG, LINEUP_SIG, FORMATION_SIG)

CAREER_ONLY_TEXT = "Milli takımlar yalnızca kariyer modunda oynanır."
NO_JOB_TEXT = "Milli takım görevin yok."
SHARED_MATCHDAY_TEXT = ("Paylaşılan dünyada sezon arası milli maç günleri dünya ilerledikçe oynanır (her turda bir "
                        "maç günü).")
NO_MATCHDAY_TEXT = "Oynanacak sezon arası milli maç günü yok."
RESIGN_CONFIRM_TEXT = "İstifa etmek için önce onay kutusunu işaretle."
BAD_SELECTION_TEXT = "Geçersiz oyuncu seçimi."


# ===========================================================================
# YARDIMCILAR
# ===========================================================================

def _star_text(value: float | None, full: str = FULL, half: str = HALF) -> str:
    """Yildiz degeri (0.5-5.0) -> '⭐⭐⭐💫' (tablolar) ya da glyph (taktik tahtasi)."""
    if not value:
        return UNKNOWN
    whole = int(value)
    return full * whole + (half if value - whole >= 0.5 else "")


def _glyphs(value: float | None) -> str:
    return _star_text(value, GLYPH_FULL, GLYPH_HALF)


def _player_labels(rows: list[CallupRow]) -> dict[int, str]:
    """Secim kutusu etiketleri (duz metin). Streamlit secimi etikete gore saklar: etiketler benzersiz olmali."""
    labels: dict[int, str] = {}
    seen: set[str] = set()
    for r in rows:
        text = f"{r.name} · {r.position} · {_glyphs(r.stars)}" + (f" · {r.club}" if r.club else "")
        if text in seen:
            text = f"{text} · #{r.player_id}"
        seen.add(text)
        labels[r.player_id] = text
    return labels


def _id_list(raw) -> list[int] | None:
    """Widget durumundaki id listesi; tam sayi olmayan deger varsa None (sunucu reddeder)."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        return None
    ids: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        ids.append(value)
    return ids


def _role(value: str | None) -> Position | None:
    return Position(value) if value in POSITION_VALUES else None


def assign_roles(xi_ids: list[int], rows: list[CallupRow], formation: str) -> dict[int, Position]:
    """
    Secilen ilk 11'e gorev (mevki) dagitimi: once kayitli ilk 11 gorevi, yoksa dogal mevki; dizilisin istedigi sayidan
    fazlasi (en dusuk yildizlilar) bos kalan gorevlere (once dogal mevkii uyan, kaleci slotuna kaleci, saha slotuna
    saha oyuncusu) kaydirilir. Deterministik. Kadroda olmayan id'ler sunucunun reddetmesi icin gecirilir.
    """
    by_id = {r.player_id: r for r in rows}
    chosen = list(dict.fromkeys(xi_ids))
    unknown = [pid for pid in chosen if pid not in by_id]
    known = sorted((pid for pid in chosen if pid in by_id),
                   key=lambda pid: (-by_id[pid].stars, by_id[pid].name, pid))
    needs = role_counts(formation) if formation in FORMATIONS else {role: 0 for role in ROLE_ORDER}

    def preferred(pid: int) -> Position:
        row = by_id[pid]
        stored = _role(row.role) if row.status == "XI" else None
        return stored or Position(row.position)

    roles: dict[int, Position] = {}
    count = dict.fromkeys(ROLE_ORDER, 0)
    floaters: list[int] = []
    for pid in known:
        role = preferred(pid)
        if count[role] < needs[role]:
            roles[pid] = role
            count[role] += 1
        else:
            floaters.append(pid)
    for role in ROLE_ORDER:
        while count[role] < needs[role] and floaters:
            pick = next((pid for pid in floaters if by_id[pid].position == role.value), None)
            if pick is None:
                keeper_slot = role is Position.GK
                pick = next((pid for pid in floaters if (by_id[pid].position == Position.GK.value) == keeper_slot),
                            floaters[0])
            floaters.remove(pick)
            roles[pick] = role
            count[role] += 1
    for pid in floaters:                              # 11'den fazla secim: sunucu sayi hatasi verir
        roles[pid] = preferred(pid)
    for pid in unknown:
        roles[pid] = Position.MID
    return roles


@dataclass(frozen=True)
class _RowPlayer:
    """tactics.validate_lineup icin milli kadro satiri (kondisyon yok: tam sayilir; kayitta sunucu denetler)."""
    id: int
    name: str
    position: Position
    reason: str

    def unavailability_reason(self, week: int | None, competition=None) -> str | None:
        return self.reason or None


def _lineup_check(rows: list[CallupRow], formation: str, week: int) -> LineupCheck | None:
    """Kayitli ilk 11'in denetimi (salt okuma). Ilk 11 kurulmadiysa None (macta asistan kurar)."""
    xi = {r.player_id: _role(r.role) for r in rows if r.status == "XI" and _role(r.role) is not None}
    if not xi:
        return None
    bench = [r.player_id for r in rows if r.status == "BENCH"]
    players = [_RowPlayer(r.player_id, r.name, Position(r.position), r.reason) for r in rows]
    return validate_lineup(players, formation, week, xi, bench)


def _formation(db, nation_id: int) -> str:
    return db.scalar(select(Nation.formation).where(Nation.id == nation_id)) or DEFAULT_FORMATION


def _sync(key: str, sig_key: str, sig, value) -> None:
    """Widget durumu veritabanindaki degerle eslenir: ilk cizimde ya da kayit degistiyse (kullanicinin kaydetmedigi
    secim veritabani degismedikce korunur). Widget'tan ONCE cagrilir."""
    ss = st.session_state
    if ss.get(sig_key) != sig or key not in ss:
        ss[key] = value
        ss[sig_key] = sig


def _is_shared(cm: CareerManager) -> bool:
    return rules_of(cm).shared or shared_page_world() is not None


def _report_notes(report: WeekReport) -> list[str]:
    """Sezon arasi raporundaki milli mac notlari (intl_notes; yoksa kupa alani ya da onur notlari)."""
    notes = [str(n) for n in (getattr(report, "intl_notes", None) or [])]
    if not notes:
        if report.cup_label:
            notes.append(report.cup_label)
        notes += [str(n) for n in report.cup_notes]
        notes += [str(n) for n in report.honours_notes]
    return [n for n in notes if n]


# ===========================================================================
# SEKME
# ===========================================================================

def national_tab(db, cm: CareerManager, team: Team | None) -> None:
    """Flash alani: national. Kulupsuz koltuk (team None) da gorur."""
    show_flash(AREA)
    st.markdown(panel_title_html(TAB_LABEL), unsafe_allow_html=True)
    nt = NationalTeams(cm)
    if not nt.enabled():
        st.info(DISABLED_TEXT)
        return
    if cm.game_mode is not GameMode.CAREER:
        st.info(CAREER_ONLY_TEXT)
        return
    try:
        mine = nt.my_nation()
        st.caption("🏆 " + md_escape(nt.world_cup_status()))
        offers = nt.job_offers() if mine is None else []
        if mine is None:
            st.markdown(stat_strip_html([("Milli takım", "Görev yok"), ("Bekleyen teklif", len(offers))]),
                        unsafe_allow_html=True)
        else:
            _job_strip(mine)
        _close_season_panel(nt, _is_shared(cm))
        if mine is None:
            _offers_panel(cm, offers)
            sections = PUBLIC_SECTIONS
        else:
            sections = JOB_SECTIONS
        if st.session_state.get("nt_section") not in sections:
            reset_widgets("nt_section")
        section = st.radio("Bölüm", sections, key="nt_section", horizontal=True, label_visibility="collapsed")
        if section == SEC_XI and mine is not None:
            _lineup_section(db, cm, nt, mine)
        elif section == SEC_SQUAD and mine is not None:
            _squad_section(cm, nt, mine)
        elif section == SEC_FIXTURES:
            _fixtures_section(nt, mine)
        elif section == SEC_GROUPS:
            _groups_section(nt)
        elif section == SEC_WORLD_CUP:
            _world_cup_section(cm, nt)
        elif section == SEC_NATIONS:
            _nations_section(nt, mine)
        # Faz 13E: milli kadro / aday listesinden secilen oyuncunun profili
        player_view.profile_panel(db, cm, team, player_view.AREA_NATIONAL)
        if mine is not None:
            _resign_panel(mine)
    except NationalTeamError as exc:                      # gorev cizim sirasinda dustu (baska oturum istifa etti)
        st.error(md_escape(str(exc)))


def new_season_hint(cm: CareerManager) -> None:
    """Kisisel dunya, yeni sezon dugmesi: milli mac gunleri bekliyorsa neden (eski kariyer: sorgu yok)."""
    if not rules_of(cm).internationals or cm.game_mode is not GameMode.CAREER:
        return
    blocker = NationalTeams(cm).new_season_blocker()
    if blocker:
        st.warning(f"🌍 {md_escape(blocker)} Maç günlerini **{TAB_LABEL}** sekmesinden oyna.")


def _close_season_panel(nt: NationalTeams, shared: bool) -> None:
    blocker = nt.new_season_blocker()                     # None: sezon arasi milli mac gunu beklemiyor
    if blocker is None:
        return
    if shared:
        st.info(f"🌍 {SHARED_MATCHDAY_TEXT} {md_escape(blocker)}")
        return
    st.warning(f"🌍 {md_escape(blocker)}")
    st.button("⚽ Sıradaki milli maç gününü oyna", key="nt_play_matchday", on_click=cb_nt_play_matchday,
              type="primary", help="Kalan eleme maç günü ya da Dünya Kupası maç günü oynanır.")


def _job_strip(mine: NationView) -> None:
    st.markdown(stat_strip_html([
        ("Milli takım", mine.name),
        ("İtibar sırası", f"{mine.rank}."),
        ("İtibar", f"{mine.reputation}/100"),
        ("Sözleşme", f"{mine.contract_until_season}. sezon sonuna kadar" if mine.contract_until_season else "—"),
        ("Kadro", f"{mine.squad_size}/{MAX_CALLUPS}"),
        ("Durum", mine.stage_label),
    ]), unsafe_allow_html=True)


def _offers_panel(cm: CareerManager, offers: list[NationalJobOfferView]) -> None:
    st.markdown("#### 📨 Milli takım teklifleri")
    if not offers:
        seat = cm.acting_seat
        if seat is None or seat.id is None:
            st.info(NO_SEAT_TEXT)
        elif cm.user_team is None:
            st.info("Milli takım teklifleri kulübü olan menajerlere gelir.")
        else:
            st.info("Bekleyen milli takım teklifin yok. Teklifler her hafta menajer seviyene uygun, menajersiz "
                    "milli takımlardan gelir.")
        return
    for o in offers:
        with st.container(border=True):
            info, accept, decline = st.columns([4, 1, 1])
            info.markdown(f"**{md_escape(o.nation_name)}** · itibar {o.reputation}/100")
            if o.expires_in_weeks is None:
                expiry = "süresiz"
            elif o.expires_in_weeks <= 0:
                expiry = "bu hafta sona eriyor"
            else:
                expiry = f"{o.expires_in_weeks} hafta daha geçerli"
            info.caption(f"{o.seasons} sezonluk sözleşme · {expiry}")
            accept.button("✅ Kabul et", key=f"nt_accept_{o.id}", on_click=cb_nt_accept, args=(o.id,), type="primary",
                          width="stretch")
            decline.button("✖️ Reddet", key=f"nt_decline_{o.id}", on_click=cb_nt_decline, args=(o.id,),
                           width="stretch")
    st.caption("Bir menajer aynı anda tek milli takımı yönetebilir; kabul edince diğer tekliflerin geri çekilir.")


# ------------------------------------------------------------------ Ilk 11

def _lineup_section(db, cm: CareerManager, nt: NationalTeams, mine: NationView) -> None:
    rows = nt.squad()
    formation = _formation(db, mine.id)
    names = list(FORMATIONS)
    _sync("nt_formation", FORMATION_SIG, (mine.id, formation), formation)
    if st.session_state.get("nt_formation") not in names:
        st.session_state["nt_formation"] = formation
    c1, c2, c3 = st.columns([2, 1, 1])
    chosen_formation = c1.selectbox("Diziliş", names, key="nt_formation")
    c2.button("💾 Dizilişi kaydet", key="nt_formation_save", on_click=cb_nt_formation_save, width="stretch",
              disabled=chosen_formation == formation)
    c3.button("🤖 Asistana bırak", key="nt_auto_lineup", on_click=cb_nt_auto_lineup, width="stretch",
              disabled=not rows)
    if chosen_formation != formation:
        st.caption(f"Kayıtlı diziliş {formation}; seçimini kaydetmeden ilk 11 {formation} ile denetlenir.")
    if not rows:
        st.info("Milli kadro boş: önce **📋 Kadro** bölümünden oyuncu çağır.")
        return

    check = _lineup_check(rows, formation, cm.current_week)
    if check is None:
        st.info("İlk 11 henüz kurulmadı: maçta asistan en iyi 11'i kuracak. **🤖 Asistana bırak** ile hemen "
                "kurabilir ya da aşağıdan seçebilirsin.")
    else:
        for err in check.errors:
            st.error(md_escape(err))
        for warning in check.warnings:
            st.warning(md_escape(warning))

    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown("#### Taktik tahtası")
        pool = {role: [r for r in rows if r.status == "XI" and r.role == role.value] for role in ROLE_ORDER}
        slots = []
        for role in formation_slots(formation):
            row = pool[role].pop(0) if pool[role] else None
            slots.append((role, row.name if row else None, row.stars if row else None, None))
        st.markdown(pitch.lineup_svg(slots, mine.name, formation_label=formation, rating_label=_glyphs),
                    unsafe_allow_html=True)
    with right:
        st.markdown("#### Milli kadro")
        st.dataframe(pd.DataFrame([
            {"Oyuncu": r.name, "Kulüp": r.club or "—", "Mv": r.position, "Yaş": r.age, "Güç": _star_text(r.stars),
             "Durum": STATUS_LABELS.get(r.status, r.status), "Görev": r.role or "", "Not": r.reason}
            for r in rows
        ]), hide_index=True, width="stretch")

    st.markdown("#### Kadro seçimi")
    st.caption(f"İlk 11'i ve en fazla {MAX_BENCH} yedeği seç, sonra kaydet. Görevler dizilişe göre dağıtılır: önce "
               "kayıtlı görev ya da doğal mevki, eksik kalan göreve en uygun oyuncu kaydırılır.")
    xi_db = [r.player_id for r in rows if r.status == "XI"][:XI_SIZE]
    bench_db = [r.player_id for r in rows if r.status == "BENCH"][:MAX_BENCH] if xi_db else []
    signature = (mine.id, tuple((r.player_id, r.status, r.role) for r in rows))
    ss = st.session_state
    if ss.get(LINEUP_SIG) != signature or "nt_xi" not in ss or "nt_bench" not in ss:
        ss["nt_xi"], ss["nt_bench"], ss[LINEUP_SIG] = xi_db, bench_db, signature
    labels = _player_labels(rows)
    options = [r.player_id for r in rows]
    x1, x2 = st.columns(2)
    xi = x1.multiselect("İlk 11", options, key="nt_xi", max_selections=XI_SIZE,
                        format_func=lambda pid: labels.get(pid, str(pid)), placeholder="Oyuncu seç")
    in_xi = set(xi)
    bench_options = [pid for pid in options if pid not in in_xi]
    bench = x2.multiselect("Kulübe", bench_options, key="nt_bench", max_selections=MAX_BENCH,
                           format_func=lambda pid: labels.get(pid, str(pid)), placeholder="Yedek seç")
    st.caption(f"İlk 11: {len(xi)}/{XI_SIZE} · Kulübe: {len(bench)}/{MAX_BENCH}")
    if xi:
        by_id = {r.player_id: r for r in rows}
        roles = assign_roles(list(xi), rows, formation)
        order = {role: i for i, role in enumerate(ROLE_ORDER)}
        preview = sorted(((pid, role) for pid, role in roles.items() if pid in by_id),
                         key=lambda item: (order[item[1]], -by_id[item[0]].stars, item[0]))
        st.dataframe(pd.DataFrame([
            {"Görev": role.value, "Oyuncu": by_id[pid].name, "Mv": by_id[pid].position,
             "Güç": _star_text(by_id[pid].stars),
             "Not": by_id[pid].reason or ("mevki dışı" if by_id[pid].position != role.value else "")}
            for pid, role in preview
        ]), hide_index=True, width="stretch")
    st.button("💾 İlk 11'i kaydet", key="nt_lineup_save", on_click=cb_nt_lineup_save, type="primary")


# ------------------------------------------------------------------ Kadro

def _squad_section(cm: CareerManager, nt: NationalTeams, mine: NationView) -> None:
    candidates = nt.candidates()                         # tum aday havuzu: yalnizca bu bolum aciksa
    called = [c for c in candidates if c.status != CALLUP_NONE]
    st.caption(f"{md_escape(mine.name)} için çağrılabilecek {len(candidates)} oyuncu (uyruk; uyruk yoksa kulübün "
               f"lig ülkesi). Milli kadroya en fazla {MAX_CALLUPS} oyuncu çağrılır; kaydedince yeni gelenler "
               "kulübeye yazılır.")
    f1, f2 = st.columns([3, 1])
    query = f1.text_input("Oyuncu ara", key="nt_query", max_chars=40)
    if st.session_state.get("nt_pos") not in POS_OPTIONS:
        reset_widgets("nt_pos")
    position = f2.selectbox("Mevki", POS_OPTIONS, key="nt_pos")
    key = plain_key(query or "")
    shown = [c for c in candidates
             if (not key or key in plain_key(c.name)) and (position == POS_ALL or c.position == position)]
    if shown:
        if len(shown) > CANDIDATE_ROWS:
            st.caption(f"{len(shown)} oyuncu bulundu; ilk {CANDIDATE_ROWS} gösteriliyor. Aramayı daralt.")
        st.dataframe(pd.DataFrame([
            {"Oyuncu": c.name, "Kulüp": c.club or "—", "Mv": c.position, "Yaş": c.age, "Güç": _star_text(c.stars),
             "Durum": STATUS_LABELS.get(c.status, c.status), "Not": c.reason}
            for c in shown[:CANDIDATE_ROWS]
        ]), hide_index=True, width="stretch")
        player_view.picker(player_view.AREA_NATIONAL, {
            c.player_id: player_view.option_label(c.name, c.position, c.club or "—")
            for c in shown[:CANDIDATE_ROWS]})
    else:
        st.info("Aramana uyan oyuncu yok.")

    st.markdown("#### Kadro çağrısı")
    _sync("nt_callups", CALLUPS_SIG, (mine.id, cm.season, tuple(sorted(c.player_id for c in called))),
          [c.player_id for c in called])
    labels = _player_labels(candidates)
    selected = st.multiselect(f"Milli kadro (en fazla {MAX_CALLUPS})", [c.player_id for c in candidates],
                              key="nt_callups", format_func=lambda pid: labels.get(pid, str(pid)),
                              placeholder="Oyuncu ara ve ekle")
    by_id = {c.player_id: c for c in candidates}
    keepers = sum(1 for pid in selected if pid in by_id and by_id[pid].position == Position.GK.value)
    unavailable = sum(1 for pid in selected if pid in by_id and not by_id[pid].available)
    st.caption(f"Seçili {len(selected)}/{MAX_CALLUPS} oyuncu · {keepers} kaleci · {unavailable} oynayamaz")
    if len(selected) > MAX_CALLUPS:
        st.warning(f"En fazla {MAX_CALLUPS} oyuncu çağırabilirsin; {len(selected) - MAX_CALLUPS} oyuncuyu çıkar.")
    st.button("💾 Kadroyu kaydet", key="nt_save_callups", on_click=cb_nt_save_callups, type="primary")


# ------------------------------------------------------------------ Fikstur, gruplar, Dunya Kupasi, uluslar

def _fixtures_section(nt: NationalTeams, mine: NationView | None) -> None:
    fixtures = nt.fixtures()
    if not fixtures:
        st.info("Bu sezon milli maç fikstürü yok.")
        return
    only_mine = mine is not None and st.toggle("Yalnızca milli takımımın maçları", key="nt_fx_mine")
    for label in dict.fromkeys(f.competition for f in fixtures):
        rows = [f for f in fixtures if f.competition == label
                and (not only_mine or mine.name in (f.home, f.away))]
        if not rows:
            continue
        played = sum(1 for f in rows if f.score is not None)
        st.markdown(f"#### {md_escape(label)} · {played}/{len(rows)} maç oynandı")
        st.dataframe(pd.DataFrame([
            {"Zaman": f.week_label, "Aşama": f.stage_label, "Ev sahibi": f.home, "Skor": f.score or "–",
             "Deplasman": f.away, "Penaltı": f.penalties or ""}
            for f in rows
        ]), hide_index=True, width="stretch")


def _groups_section(nt: NationalTeams) -> None:
    shown = False
    for competition in COMPETITIONS:
        tables = nt.group_tables(competition)
        if not tables:
            continue
        shown = True
        st.markdown(f"#### {md_escape(COMPETITION_LABELS[competition])}")
        st.markdown(group_tables_html([(title, [GroupRowView(**row) for row in rows]) for title, rows in tables]),
                    unsafe_allow_html=True)
    if not shown:
        st.info("Bu sezon milli grup tablosu yok.")
        return
    st.caption("Yeşil şerit: Dünya Kupası'na katılan (elemeler) ya da bir üst tura çıkan (Dünya Kupası); "
               "vurgulu satır senin milli takımın.")


def _world_cup_section(cm: CareerManager, nt: NationalTeams) -> None:
    champion = nt.champion_name()
    if champion:
        st.success(f"🏆 Sezon {cm.season} Dünya Kupası şampiyonu: **{md_escape(champion)}**")
    st.markdown("#### Eleme turları")
    st.markdown(bracket_html(nt.bracket(), champion), unsafe_allow_html=True)
    champions = nt.champions()
    st.markdown("#### 🏅 Dünya Kupası şampiyonları")
    if champions:
        st.dataframe(pd.DataFrame([{"Sezon": season, "Şampiyon": name} for season, name in champions]),
                     hide_index=True, width="stretch")
    else:
        st.caption("Henüz Dünya Kupası şampiyonu yok.")


def _nations_section(nt: NationalTeams, mine: NationView | None) -> None:
    nations = nt.nations()
    if not nations:
        st.info("Bu dünyada henüz milli takım yok: milli takımlar ilk oynanan haftada kurulur.")
        return
    st.dataframe(pd.DataFrame([
        {"Sıra": n.rank, "Milli takım": ("⭐ " if mine is not None and n.id == mine.id else "") + n.name,
         "İtibar": n.reputation, "Menajer": n.manager_name or "Yapay zekâ",
         "Sözleşme": f"{n.contract_until_season}. sezon" if n.contract_until_season else "—",
         "Kadro": n.squad_size, "Durum": n.stage_label}
        for n in nations
    ]), hide_index=True, width="stretch")


def _resign_panel(mine: NationView) -> None:
    with st.expander("🚪 Milli takım görevinden istifa et"):
        st.caption(f"İstifa edersen {md_escape(mine.name)} yapay zekâya geçer; yeni milli takım teklifleri "
                   "haftalık olarak gelir.")
        confirmed = st.checkbox("İstifa etmeyi onaylıyorum", key="nt_resign_ok")
        st.button("İstifa et", key="nt_resign", on_click=cb_nt_resign, disabled=not confirmed,
                  help=None if confirmed else RESIGN_CONFIRM_TEXT)


# ===========================================================================
# CALLBACK'LER (member_callback: oturum + uyelik + paylasilan dunyada SHARED kilit)
# ===========================================================================

def _national_call(work) -> tuple[bool, object]:
    """
    work(NationalTeams) tek islemde calisir. NationalTeamError islem ICINDE yakalanir (islem commit edilir) ve kacisli
    Turkce mesaj flash'a yazilir. Basarida (True, sonuc).
    """
    error = None
    result = None
    with session_scope() as db:
        cm = manager(db)
        if cm.game_mode is not GameMode.CAREER:
            error = CAREER_ONLY_TEXT
        else:
            try:
                result = work(NationalTeams(cm))
            except NationalTeamError as exc:
                error = str(exc)
    if error is not None:
        flash(AREA, "error", md_escape(error))
        return False, None
    return True, result


def _reset_lineup_state() -> None:
    reset_widgets("nt_xi", "nt_bench", "nt_formation", LINEUP_SIG, FORMATION_SIG)


@member_callback
def cb_nt_accept(offer_id: int) -> None:
    ok, view = _national_call(lambda nt: nt.accept_job(offer_id))
    if ok:
        reset_widgets(*WIDGET_KEYS)
        until = f" ({view.contract_until_season}. sezon sonuna kadar)" if view.contract_until_season else ""
        flash(AREA, "success", f"🌍 {md_escape(view.name)} milli takımının menajeri oldun{md_escape(until)}.")


@member_callback
def cb_nt_decline(offer_id: int) -> None:
    ok, _ = _national_call(lambda nt: nt.decline_job(offer_id))
    if ok:
        flash(AREA, "info", "✖️ Milli takım teklifi reddedildi.")


@member_callback
def cb_nt_resign() -> None:
    if not st.session_state.get("nt_resign_ok"):
        flash(AREA, "error", RESIGN_CONFIRM_TEXT)
        return

    def work(nt: NationalTeams):
        mine = nt.my_nation()
        if mine is None:
            raise NationalTeamError(NO_JOB_TEXT)
        nt.resign()
        return mine

    ok, mine = _national_call(work)
    if ok:
        reset_widgets(*WIDGET_KEYS)
        flash(AREA, "info", f"🚪 {md_escape(mine.name)} milli takımındaki görevinden istifa ettin.")


@member_callback
def cb_nt_save_callups() -> None:
    ids = _id_list(st.session_state.get("nt_callups"))
    if ids is None:
        flash(AREA, "error", md_escape(BAD_SELECTION_TEXT))
        return
    ok, warnings = _national_call(lambda nt: nt.set_callups(ids))
    if ok:
        flash(AREA, "success", f"📋 Milli kadro kaydedildi ({len(set(ids))} oyuncu).")
        for warning in warnings or []:
            flash(AREA, "warning", md_escape(warning))


@member_callback
def cb_nt_formation_save() -> None:
    name = st.session_state.get("nt_formation")
    ok, check = _national_call(lambda nt: nt.set_formation(str(name)))
    if ok:
        _reset_lineup_state()
        flash(AREA, "success", f"📐 Milli takım dizilişi {md_escape(name)} olarak kaydedildi.")
        if check.errors:
            flash(AREA, "warning", "Kayıtlı ilk 11 yeni dizilişe uymuyor; ilk 11'i yeniden kaydet ya da asistana "
                                   "bırak: " + md_escape(" ".join(check.errors)))


@member_callback
def cb_nt_lineup_save() -> None:
    xi_ids = _id_list(st.session_state.get("nt_xi"))
    bench_ids = _id_list(st.session_state.get("nt_bench"))
    if xi_ids is None or bench_ids is None:
        flash(AREA, "error", md_escape(BAD_SELECTION_TEXT))
        return

    def work(nt: NationalTeams):
        rows = nt.squad()                                 # gorev yoksa NationalTeamError (ulus sunucuda)
        seat = nt.cm.acting_seat
        formation = nt.db.scalar(select(Nation.formation).where(Nation.manager_id == seat.id))
        return nt.set_lineup(assign_roles(xi_ids, rows, formation or ""), bench_ids)

    ok, check = _national_call(work)
    if not ok:
        return
    if not check.ok:
        flash(AREA, "error", "İlk 11 kaydedilmedi: " + md_escape(" ".join(check.errors)))
        return
    _reset_lineup_state()
    flash(AREA, "success", "💾 Milli takımın ilk 11'i ve kulübesi kaydedildi.")
    for warning in check.warnings:
        flash(AREA, "warning", md_escape(warning))


@member_callback
def cb_nt_auto_lineup() -> None:
    ok, _ = _national_call(lambda nt: nt.auto_lineup())
    if ok:
        _reset_lineup_state()
        flash(AREA, "success", "🤖 Asistan milli takımın ilk 11'ini ve kulübesini kurdu.")


@member_callback
def cb_nt_play_matchday() -> None:
    """Kisisel dunya: sezon arasi bir milli mac gunu (paylasilan dunyada tur motoru oynatir)."""
    auth = st.session_state.get("auth")
    with session_scope() as db:
        cm = manager(db)
        if rules_of(cm).shared or is_shared_world(auth):
            flash(AREA, "error", SHARED_MATCHDAY_TEXT)
            return
        nt = NationalTeams(cm)
        if cm.game_mode is not GameMode.CAREER or not nt.close_season_pending():
            flash(AREA, "info", NO_MATCHDAY_TEXT)
            return
        report = WeekReport(season=cm.season, week=cm.current_week)
        played = nt.play_close_season_matchday(report)
        notes = _report_notes(report)
        finished = not nt.close_season_pending()
        champion = nt.champion_name() if finished else None
    if not played:
        flash(AREA, "info", NO_MATCHDAY_TEXT)
        return
    flash(AREA, "success", "⚽ Milli maç günü oynandı.")
    if notes:
        flash(AREA, "info", "  \n".join(md_escape(note) for note in notes))
    if finished:
        text = "🏆 Sezon arası milli maçlar tamamlandı"
        if champion:
            text += f"; Dünya Kupası şampiyonu {md_escape(champion)}"
        flash(AREA, "success", text + ". Yeni sezonu **🏆 Lig** sekmesinden başlatabilirsin.")
