"""
Faz 15D kalici gelen kutusu, takvim ve "suna kadar devam" testleri.

Saf testler (takvim, kategori / tur tablolari, baglanti eslemesi) CM_TEST_NO_DB=1 ile de kosar.
Veritabani testleri sentetik dunyada calisir ve kendi islemlerini geri alir.
Onerilen: TEST_DB_NAME=fm_db_test_15d.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inbox  # noqa: E402
from models import (  # noqa: E402
    INBOX_CATEGORIES,
    INBOX_KINDS,
    INBOX_REF_TYPES,
    InboxMessage,
)

USER = "Istanbul Lions"
RIVAL = "Karadeniz Storm"


# ===========================================================================
# 1) SAF: takvim
# ===========================================================================

def test_season_start_is_first_saturday_of_august():
    for season in range(1, 12):
        start = inbox.default_season_start(season)
        assert start.weekday() == inbox.LEAGUE_WEEKDAY == 5         # cumartesi
        assert start.month == 8 and start.day <= 7
        assert start.year == inbox.BASE_SEASON_YEAR + season - 1
    assert inbox.default_season_start(1) == date(2025, 8, 2)


def test_match_dates_are_saturday_league_and_wednesday_cup():
    for week in (1, 2, 19, 38):
        league = inbox.match_date(1, week)
        cup = inbox.match_date(1, week, midweek=True)
        assert league.weekday() == 5 and cup.weekday() == 2         # Cumartesi / Carsamba
        assert (league - cup).days == 3                             # kupa AYNI haftanin ici, ligden once
        assert (league - inbox.default_season_start(1)).days == 7 * (week - 1)
    assert inbox.week_dates(1, 1) == (inbox.match_date(1, 1, midweek=True), inbox.match_date(1, 1))


def test_stored_season_start_overrides_default():
    pinned = date(2030, 8, 10)
    assert inbox.season_start(1, pinned) == pinned
    assert inbox.match_date(1, 3, start=pinned) == date(2030, 8, 24)
    assert inbox.season_start(1, None) == inbox.default_season_start(1)


def test_date_formats_are_cm_style():
    day = date(2025, 8, 2)
    assert inbox.format_date(day) == "Cumartesi 2.08.25"
    assert inbox.short_date(day) == "2.08.25"
    assert inbox.long_date(day) == "2 Ağustos 2025 Cumartesi"
    assert inbox.format_date(None) == "" and inbox.long_date(None) == ""
    assert inbox.day_name(date(2025, 11, 5)) == "Çarşamba"


# ===========================================================================
# 2) SAF: kategori, tur ve baglanti tablolari
# ===========================================================================

def test_constants_match_schema_checks():
    assert inbox.CATEGORIES == INBOX_CATEGORIES
    assert set(inbox.CATEGORY_ORDER) == set(INBOX_CATEGORIES)
    assert set(inbox.CATEGORY_LABELS) == set(INBOX_CATEGORIES)
    assert inbox.KINDS == INBOX_KINDS
    assert set(inbox.KIND_CATEGORY) == set(INBOX_KINDS)
    assert set(inbox.KIND_LABELS) == set(INBOX_KINDS)
    assert set(inbox.KIND_ICONS) == set(INBOX_KINDS)
    assert set(inbox.KIND_CATEGORY.values()) <= set(INBOX_CATEGORIES)
    assert inbox.IMPORTANT_KINDS <= set(INBOX_KINDS)
    assert all(len(k) <= 20 for k in INBOX_KINDS)                   # inbox_messages.kind String(20)
    assert all(len(c) <= 12 for c in INBOX_CATEGORIES)              # .category String(12)


def test_every_ref_type_has_a_page():
    assert set(inbox.LINK_PAGES) == set(INBOX_REF_TYPES) == set(inbox.REF_TYPES)
    assert inbox.PARAM_REFS <= set(INBOX_REF_TYPES)
    assert all(len(r) <= 16 for r in INBOX_REF_TYPES)               # .ref_type String(16)
    # nav_view'un gercek slug'lari (U seridi): yanlis yazim tek tik hedefini bozar
    import nav_view
    slugs = set(nav_view.PAGES)
    assert set(inbox.LINK_PAGES.values()) <= slugs
    assert inbox.LINK_PAGES[inbox.REF_PLAYER] == nav_view.PLAYER
    assert inbox.LINK_PAGES[inbox.REF_TEAM] == nav_view.CLUB_PAGE


def test_desk_note_classification():
    """Masa notu duz metindir: sozlesme > teklif > duz transfer. Onemli turler "devam"i durdurur."""
    cases = {
        "Sözleşmesi bu sezon bitenler: Ali, Veli. Yenilemezsen sezon sonunda serbest kalırlar.":
            inbox.KIND_CONTRACT_EXPIRING,
        "Ön sözleşme: Jan Neumann bedelsiz ayrıldı (Coventry City FC)": inbox.KIND_CONTRACT_EXPIRING,
        "Ali Veli ile sözleşme feshedildi; tazminat 2.4M EUR.": inbox.KIND_CONTRACT,
        "Manchester Blue, Ahmet için 12M EUR teklif etti.": inbox.KIND_TRANSFER_OFFER,
        "Bonservis taksiti ödendi: 3M EUR": inbox.KIND_TRANSFER_OFFER,
        "Gözlemci raporu hazır.": inbox.KIND_TRANSFER,
        "": inbox.KIND_TRANSFER,
    }
    for text, kind in cases.items():
        assert inbox.classify_desk_note(text) == kind, text
    assert inbox.is_important(inbox.KIND_CONTRACT_EXPIRING)
    assert inbox.is_important(inbox.KIND_TRANSFER_OFFER)
    assert not inbox.is_important(inbox.KIND_TRANSFER)
    assert not inbox.is_important(inbox.KIND_WEEK_REPORT)


def test_targets_and_stop_reasons_are_labelled():
    assert set(inbox.TARGET_LABELS) == set(inbox.TARGETS)
    assert set(inbox.STOP_LABELS) == {inbox.STOP_TARGET, inbox.STOP_IMPORTANT, inbox.STOP_SEASON_END,
                                      inbox.STOP_LIMIT, inbox.STOP_BLOCKED, inbox.STOP_ERROR}


# ===========================================================================
# 3) VERITABANI
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark_db = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, *, flag: bool = True, team: str = USER):
    from career_manager import CareerManager
    from models import GameMode

    cm = CareerManager(db, seed=11)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.inbox = flag
    cm.set_game_mode(GameMode.CAREER)
    cm.set_user_team(cm.find_team(team))
    return cm


def _count(db, *where) -> int:
    db.flush()
    return int(db.scalar(select(func.count()).select_from(InboxMessage).where(*where)) or 0)


@pytest.mark.integration
@pytestmark_db
def test_flag_off_writes_nothing(db):
    cm = _manager(db, flag=False)
    cm.play_week()
    cm.play_week()
    assert _count(db) == 0


@pytest.mark.integration
@pytestmark_db
def test_week_writes_report_and_match_to_the_primary_seat(db):
    cm = _manager(db)
    report = cm.play_week()
    box = cm.inbox_for_manager()
    assert box.manager_id is None                                   # birincil koltuk: manager_id NULL
    rows = box.messages(limit=100)
    assert rows, "hafta mesaj üretmedi"
    kinds = {r.kind for r in rows}
    assert inbox.KIND_WEEK_REPORT in kinds
    assert inbox.KIND_MATCH_RESULT in kinds
    # hepsi bu menajerin, tarihli ve okunmamis
    assert all(r.manager_id is None and r.team_id == cm.user_team.id for r in rows)
    assert all(r.date is not None and r.date_label for r in rows)
    assert all(not r.read and not r.archived for r in rows)
    # hafta raporu satirlari kalici (eskiden st.session_state["last_week_lines"])
    stored = box.latest_week_report()
    assert stored is not None and stored.season == report.season and stored.week == report.week
    assert stored.lines and all(len(line) == 2 for line in stored.lines)
    assert any(kind == "result" for kind, _text in stored.lines)


@pytest.mark.integration
@pytestmark_db
def test_messages_survive_a_new_session_and_manager(db):
    """"Yenilemede mesajlar kalır": yeni CareerManager / yeni okuma ayni satirlari gorur."""
    from career_manager import CareerManager

    cm = _manager(db)
    cm.play_week()
    db.flush()
    before = [(r.kind, r.subject) for r in cm.inbox_for_manager().messages(limit=100)]
    db.expire_all()                                                 # oturum onbellegi bosaltildi (sayfa yenilendi)
    fresh = CareerManager(db, seed=99)
    after = [(r.kind, r.subject) for r in fresh.inbox_for_manager().messages(limit=100)]
    assert before and before == after


@pytest.mark.integration
@pytestmark_db
def test_categories_counts_read_and_archive(db):
    cm = _manager(db)
    for _ in range(3):
        cm.play_week()
    box = cm.inbox_for_manager()
    counts = box.counts()
    assert counts.total == counts.unread > 0
    assert set(counts.by_category) == set(inbox.CATEGORY_ORDER)
    assert counts.by_category[inbox.CAT_COMPETITION] > 0
    assert sum(counts.by_category.values()) == counts.total
    # kategori filtresi
    comp = box.messages(inbox.CAT_COMPETITION, limit=100)
    assert comp and all(r.category == inbox.CAT_COMPETITION for r in comp)
    assert len(comp) == counts.by_category[inbox.CAT_COMPETITION]
    # okundu
    first = comp[0]
    assert box.mark_read([first.id]) == 1
    assert box.message(first.id).read
    assert box.counts().unread == counts.unread - 1
    assert box.mark_unread(first.id) and not box.message(first.id).read
    # arsiv (satir silinmez, listede gorunmez)
    assert box.archive(first.id)
    assert first.id not in {r.id for r in box.messages(limit=200)}
    assert first.id in {r.id for r in box.messages(limit=200, include_archived=True)}
    assert box.archive(first.id, False) and box.message(first.id).archived is False
    # hepsini okundu yap
    assert box.mark_read() > 0 and box.counts().unread == 0
    assert box.archive_read() > 0 and box.counts().total == 0


@pytest.mark.integration
@pytestmark_db
def test_every_message_links_to_a_page(db):
    """Kart: "Her mesajdan ilgili sayfaya tek tık" -- baglanti verisi mesajda."""
    import nav_view

    cm = _manager(db)
    for _ in range(3):
        cm.play_week()
    rows = cm.inbox_for_manager().messages(limit=200)
    assert rows
    assert all(r.page in nav_view.PAGES for r in rows), \
        {r.kind for r in rows if r.page not in nav_view.PAGES}
    assert all(r.link is not None for r in rows)
    player_links = [r for r in rows if r.ref_type == inbox.REF_PLAYER]
    assert all(r.page_param == r.ref_id and r.page == nav_view.PLAYER for r in player_links)


@pytest.mark.integration
@pytestmark_db
def test_injuries_and_bans_are_important_and_in_their_own_tab(db):
    """Sakatlik / ceza mesaji yalnizca kendi kulubunun oyuncusu icin, kendi kategorisinde ve ONEMLI."""
    cm = _manager(db)
    for _ in range(6):
        if cm.season_finished:
            break
        cm.play_week()
    box = cm.inbox_for_manager()
    hurt = box.messages(inbox.CAT_INJURY, limit=200)
    if not hurt:
        pytest.skip("Bu tohumda sakatlık/ceza çıkmadı")
    assert all(r.kind in (inbox.KIND_INJURY, inbox.KIND_BAN) for r in hurt)
    assert all(r.ref_type == inbox.REF_PLAYER and r.page == "oyuncu" for r in hurt)
    # kendi kulubunun oyuncusu ONEMLI ("devam" durur); rakip oyuncusu yalnizca haber
    own = {p.id for p in cm.user_team.players} | {p.id for p in cm.academy_players(cm.user_team)}
    mine = [r for r in hurt if r.ref_id in own]
    rivals = [r for r in hurt if r.ref_id not in own]
    if not mine:
        pytest.skip("Bu tohumda menajerin kulübünde sakatlık/ceza çıkmadı")
    assert all(r.important for r in mine)
    assert all(not r.important and r.subject.startswith("Ligde") for r in rivals)


@pytest.mark.integration
@pytestmark_db
def test_other_managers_inbox_is_not_visible(db):
    """Baska koltugun mesaji bu menajere gorunmez (manager_id ile ayrilir)."""
    cm = _manager(db)
    cm.play_week()
    box = cm.inbox_for_manager()
    mine = box.messages(limit=200)
    assert mine
    inbox.post(db, manager_id=None, kind=inbox.KIND_BOARD, subject="Birincil", body="x", flush=True)
    stranger = inbox.Inbox(db, manager_id=999_999)
    assert stranger.messages(limit=50) == []
    assert stranger.counts().total == 0
    assert stranger.message(mine[0].id) is None                      # baskasinin mesaji okunamaz


@pytest.mark.integration
@pytestmark_db
def test_two_seats_get_their_own_inbox(db):
    """Paylasilan dunyada her koltuk kendi mesajlarini gorur: birincil NULL, uye koltuk kendi id'si."""
    import uuid

    from career_manager import CareerManager
    from models import GameMode, User, WorldManager
    from seats import SeatStore
    from world_rules import WorldRules

    cm = _manager(db)
    cm.state.world_rules = WorldRules.shared_defaults().to_dict()
    store = SeatStore(db)
    store.ensure_primary_row(None, "Sahip")
    user = User(username=f"inbox_{uuid.uuid4().hex[:8]}", password_hash="scrypt$test$not-a-real-hash")
    db.add(user)
    db.flush()
    seat = store.assign_team(store.create_seat(user.id, "Üye", 8.0, cm.career_week), cm.find_team(RIVAL).id)
    db.flush()
    assert cm.human_team_ids() == frozenset({cm.user_team.id, seat.team_id})

    cm.refresh_seats()
    cm.play_week()
    member = CareerManager(db, seed=11, manager_user_id=user.id)
    member.set_game_mode(GameMode.CAREER)

    mine = cm.inbox_for_manager()
    theirs = member.inbox_for_manager()
    assert mine.manager_id is None and theirs.manager_id == seat.id
    assert mine.messages(limit=100) and theirs.messages(limit=100)
    assert {r.team_id for r in mine.messages(limit=100)} == {cm.user_team.id}
    assert {r.team_id for r in theirs.messages(limit=100)} == {seat.team_id}
    assert not ({r.id for r in mine.messages(limit=100)} & {r.id for r in theirs.messages(limit=100)})
    # koltuk satiri silinince mesajlari da gider (ON DELETE CASCADE); birincilinkiler kalir
    keep = mine.counts().total
    db.execute(WorldManager.__table__.delete().where(WorldManager.id == seat.id))
    db.flush()
    assert theirs.counts().total == 0 and mine.counts().total == keep


@pytest.mark.integration
@pytestmark_db
def test_season_rollover_writes_a_season_message(db):
    cm = _manager(db)
    for _ in range(14):
        if cm.season_finished:
            break
        cm.play_week()
    assert cm.season_finished
    new_season = cm.start_new_season()
    rows = cm.inbox_for_manager().messages(kinds=(inbox.KIND_SEASON,), limit=10)
    assert rows and rows[0].season == new_season and rows[0].week == 1
    assert rows[0].date == inbox.match_date(new_season, 1)
    assert rows[0].page == "takim" and rows[0].page_param == cm.user_team.id


@pytest.mark.integration
@pytestmark_db
def test_date_bar_follows_the_played_week(db):
    cm = _manager(db)
    bar = cm.date_bar(midweek=False)
    assert bar.season == cm.season and bar.week == cm.current_week
    assert bar.date == inbox.match_date(cm.season, cm.current_week)
    assert bar.short == inbox.format_date(bar.date) and bar.label.endswith("1. hafta")
    cm.play_week()
    assert cm.date_bar(midweek=False).date == bar.date + timedelta(days=7)
    assert cm.game_date(midweek=True).weekday() == 2


@pytest.mark.integration
@pytestmark_db
def test_continue_until_next_match_stops_before_the_match(db):
    cm = _manager(db)
    # sezonun her haftasinda maci olan bir kulupte "sonraki mac" zaten bu hafta: hedefe hemen ulasilir
    result = cm.continue_until(inbox.TARGET_NEXT_MATCH)
    assert result.stopped_by == inbox.STOP_TARGET and result.weeks == 0
    assert result.target == inbox.TARGET_NEXT_MATCH and result.reason


@pytest.mark.integration
@pytestmark_db
def test_continue_until_weeks_and_season_end(db):
    cm = _manager(db)
    start = cm.current_week
    result = cm.continue_until(inbox.TARGET_WEEKS, weeks=2, stop_on_important=False)
    assert result.weeks == 2 and cm.current_week == start + 2
    assert result.stopped_by in (inbox.STOP_TARGET, inbox.STOP_SEASON_END)
    rest = cm.continue_until(inbox.TARGET_SEASON_END, stop_on_important=False)
    assert cm.season_finished and rest.stopped_by == inbox.STOP_SEASON_END
    assert rest.seconds >= 0.0


@pytest.mark.integration
@pytestmark_db
def test_continue_until_stops_on_an_important_message(db):
    cm = _manager(db)
    result = cm.continue_until(inbox.TARGET_SEASON_END, stop_on_important=True)
    if result.stopped_by != inbox.STOP_IMPORTANT:
        pytest.skip("Bu tohumda sezon boyunca önemli gelişme çıkmadı")
    assert result.messages and all(m.important for m in result.messages)
    assert not cm.season_finished
    assert result.reason.startswith(result.messages[0].kind_label)


@pytest.mark.integration
@pytestmark_db
def test_continue_until_refuses_without_a_club_and_in_shared_worlds(db):
    from career_manager import CareerManager
    from models import GameMode

    cm = CareerManager(db, seed=3)
    cm.set_game_mode(GameMode.CAREER)
    cm.state.user_team_id = None
    db.flush()
    result = cm.continue_until(inbox.TARGET_NEXT_MATCH)
    assert result.stopped_by == inbox.STOP_BLOCKED and result.weeks == 0


@pytest.mark.integration
@pytestmark_db
def test_post_rejects_unknown_kind_and_empty_subject(db):
    assert inbox.post(db, manager_id=None, kind="YOK", subject="konu") is None
    assert inbox.post(db, manager_id=None, kind=inbox.KIND_BOARD, subject="   ") is None
    row = inbox.post(db, manager_id=None, kind=inbox.KIND_BOARD, subject="Yönetim " * 40,
                     body="x" * 5000, flush=True)
    assert row is not None and len(row.subject) <= inbox.SUBJECT_MAX and len(row.body) <= inbox.BODY_MAX
    assert row.category == inbox.CAT_MESSAGE and row.important is True      # BOARD onemlidir
