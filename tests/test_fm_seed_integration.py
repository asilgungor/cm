"""
FM dunyasi ve menajer tanınırlığı ENTEGRASYON testleri (6. Asama).

Test veritabaninda calisir (conftest). FM dunyasi yazma testi mevcut sentetik
dunyayi transaction icinde silip FM ornegini yazar, dogrular ve ROLLBACK eder.
Isim maskeleme testi tam seed yolunu (resolve_world -> write_world) ayni sekilde calistirir ve
veritabanina yalnizca maskeli oyuncu adinin gittigini dogrular.
"""

from __future__ import annotations

import enum
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fm_parser  # noqa: E402
import reputation  # noqa: E402
import seed  # noqa: E402
from career_manager import CareerManager  # noqa: E402
from club_directory import MASKED_LEAGUES, lookup_club, plain_key  # noqa: E402
from match_engine import MatchEngine, build_match_team  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    GameState,
    League,
    Player,
    PlayerMatchStat,
    Position,
    Staff,
    Team,
)
from name_masking import find_leaks, mask_player_name  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "fm" / "sample_fm_export.html"


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _wipe(db):
    for model in (PlayerMatchStat, Fixture, GameState, Staff, Player, Team, League):
        db.execute(delete(model))
    db.flush()


@pytest.fixture
def fm_world(db):
    """Sentetik dunyayi (transaction icinde) silip FM ornegini yazar."""
    _wipe(db)
    world = seed.build_fm_world(fm_parser.parse_files([SAMPLE]), rng_seed=2026)
    seed.write_world(db, world, rng_seed=2026)
    db.flush()
    db.expire_all()
    return world


def test_fm_world_is_persisted(db, fm_world):
    assert db.scalar(select(func.count()).select_from(League)) == 3
    assert db.scalar(select(func.count()).select_from(Team)) == 6
    # 10. Asama: A takimlar + kulup basina baslangic akademisi (akademi A takim sayisina dahil degil)
    assert db.scalar(select(func.count()).select_from(Player).where(Player.in_academy.is_(False))) \
        == fm_world.player_count
    assert db.scalar(select(func.count()).select_from(Player)) == fm_world.player_count + fm_world.academy_count
    assert fm_world.academy_count >= 6 * 4

    real = db.scalars(select(Player).where(Player.data_source == "fm")).all()
    assert len(real) == 42                                             # 6 kulup x 7 (Kuzey Yildizi atlandi)
    sample = next(p for p in real if p.team.name == "Istanbul Lions")
    assert sample.fm_attributes and 1 <= min(sample.fm_attributes.values()) <= max(sample.fm_attributes.values()) <= 20
    assert sample.nationality == "TUR" and sample.current_ability and sample.potential_ability
    assert any(ch in p.name for p in real for ch in "ıçğöşüé")           # UTF-8 kaliciligi

    bayern = db.scalar(select(Team).where(Team.name == "München Roten"))
    assert bayern.league.name == "Almanya Elit Ligi" and bayern.league.country == "Almanya"
    assert bayern.reputation == 94
    state = db.get(GameState, 1)
    assert state.manager_reputation == reputation.START_REPUTATION


def test_fm_squads_are_playable(db, fm_world):
    for team in db.scalars(select(Team)):
        assert len(team.players) >= seed.FM_MIN_SQUAD
        assert sum(p.position is Position.GK for p in team.players) >= 2
        assert team.free_wage >= 0 and team.staff
    real, barca = (db.scalar(select(Team).where(Team.name == n)) for n in ("Madrid Blancos", "Catalonia Blaugrana"))
    result = MatchEngine(build_match_team(real, True, 1), build_match_team(barca, False, 1), seed=9).simulate()
    assert result.events[-1].type.value == "FULL_TIME"
    assert sum(p.played for p in result.home.players) >= 11


# ---------------------------------------------------------------------------
# Isim maskeleme: veritabanina yalnizca maskeli oyuncu adi yazilir
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_fm_world(db):
    """seed.seed ile ayni yol (parser maskesi + mask_world + validate_world + write_world), commit YOK."""
    _wipe(db)
    world = seed.resolve_world(2026, source="fm", fm_paths=[SAMPLE], mask_level="light")
    seed.write_world(db, world, rng_seed=2026)
    db.flush()
    db.expire_all()
    return world


def _text_values(db) -> list[str]:
    """Yazilan tum satirlardaki metin, enum ve JSON degerleri."""
    values: list[str] = []
    for model in (League, Team, Player, Staff, GameState, Fixture):
        for row in db.execute(select(model.__table__)).all():
            for value in row:
                if isinstance(value, enum.Enum):
                    values.append(str(value.value))
                elif isinstance(value, str):
                    values.append(value)
                elif isinstance(value, dict | list):
                    values.append(json.dumps(value, ensure_ascii=False))
    return values


def test_fm_seed_writes_only_masked_player_names(db, seeded_fm_world):
    raw = fm_parser.parse_files([SAMPLE], mask_names=False).players
    masked = fm_parser.parse_files([SAMPLE]).players
    record_by_mask = {m.name: r for r, m in zip(raw, masked, strict=True)}
    assert seeded_fm_world.mask_summary.at_ingest and seeded_fm_world.mask_summary.level == "light"

    fm_rows = db.scalars(select(Player).where(Player.data_source == "fm")).all()
    assert len(fm_rows) == 42 and len({p.name for p in fm_rows}) == 42
    for player in fm_rows:
        record = record_by_mask[player.name]                              # DB adi = parser'in maskeli adi
        assert player.name == mask_player_name(record.name) != record.name
        assert "." not in player.name.split()[0]                            # ilk isim bas harfe inmez
        assert (player.age, player.position, player.nationality, player.fm_uid, player.current_ability,
                player.potential_ability, player.fm_attributes) == \
            (record.age, record.position, record.nationality, record.uid, record.current_ability,
             record.potential_ability, record.fm_attributes), record.name

    # Hicbir tablo/sutunda ozgun ad yok: tam yazim da, aksan/buyuk-kucuk harf duyarsiz kelime dizisi de
    values = _text_values(db)
    joined = "\n".join(values)
    padded = [f" {plain_key(v)} " for v in values]
    for record in raw:
        assert record.name not in joined, record.name
        key = f" {plain_key(record.name)} "
        assert not any(key in v for v in padded), record.name
    names = set(db.scalars(select(Player.name)))
    assert {"Egemen Kalaycıo", "Lennart Linde", "Unai Echever", "Görkem Çekırtaş"} <= names
    assert any(ch in n for n in names for ch in "ıçğöşü")                  # Turkce harfler korunur


# ---------------------------------------------------------------------------
# Maskeleme KAPALI (off): kisisel/yerel oyun -- gercek adlar veritabanina yazilir
# ---------------------------------------------------------------------------

def test_fm_seed_with_masking_off_writes_the_file_names(db, monkeypatch):
    """
    seed.seed ile ayni yol, --mask-level off: ornek dosyadaki GERCEK kulup/lig/oyuncu adlari
    veritabanina yazilir, sizinti kilidi tripmez ve seviye kayda islenir. Transaction ROLLBACK
    edilir (db fixture). Yalnizca depodaki kurgusal ornek dosya kullanilir.
    """
    _wipe(db)
    monkeypatch.setenv("OFM_ALLOW_REAL_NAMES", "1")                       # 'off' icin ikinci onay
    raw = fm_parser.parse_files([SAMPLE], mask_names=False)
    world = seed.resolve_world(2026, source="fm", fm_paths=[SAMPLE], mask_level="off")
    assert seed.masking_off(world) and world.mask_summary.level == "off"
    seed.write_world(db, world, rng_seed=2026)                            # kilit calismaz: hata yok
    db.flush()
    db.expire_all()
    assert db.get(GameState, 1).mask_level == "off"                       # dunyanin seviyesi kayitta

    file_clubs = {c for c in (p.club for p in raw.players) if c}
    expected_clubs = {(lookup_club(c).name if lookup_club(c) else c.strip()) for c in file_clubs}
    team_names = set(db.scalars(select(Team.name)))
    assert team_names and team_names <= expected_clubs                    # DB'de dosyadaki gercek adlar
    league_names = set(db.scalars(select(League.name)))
    assert league_names <= set(MASKED_LEAGUES)                            # gercek lig adlari
    assert not league_names & set(MASKED_LEAGUES.values())

    fm_rows = db.scalars(select(Player).where(Player.data_source == "fm")).all()
    raw_by_name = {p.name: p for p in raw.players}
    assert len(fm_rows) == 42
    for player in fm_rows:
        record = raw_by_name[player.name]                                 # DB adi = dosyadaki ad (birebir)
        assert (player.age, player.position, player.nationality, player.fm_uid,
                player.current_ability, player.potential_ability, player.fm_attributes) == \
            (record.age, record.position, record.nationality, record.uid,
             record.current_ability, record.potential_ability, record.fm_attributes), record.name

    # Gercek adla arama: maskeleme kapaliyken gercek ad kendisine cozulur
    cm = CareerManager(db, seed=4)
    for query in ("Galatasaray SK", "galatasaray"):
        assert cm.find_team(query) is not None
    assert find_leaks(list(league_names) + list(team_names))               # gercek adlar bilerek duruyor


def test_fm_career_week_runs(db, fm_world):
    cm = CareerManager(db, seed=4)
    cm.set_user_team(cm.find_team("Istanbul Lions"))
    report = cm.play_week()
    assert len(report.results) == 3                                     # 3 lig x 1 mac
    assert report.user_result is not None


# ---------------------------------------------------------------------------
# Menajer tanınırlığı (sentetik dunya uzerinde)
# ---------------------------------------------------------------------------

@pytest.fixture
def cm(db):
    manager = CareerManager(db, seed=5)
    if manager.season_finished:
        pytest.skip("Sezon bitmiş")
    return manager


def test_manager_reputation_moves_with_results(cm):
    team = cm.find_team("Istanbul Lions")
    cm.set_user_team(team)
    cm.run_ai_transfer_window = lambda: []
    before = cm.manager_reputation
    report = cm.play_week()
    assert report.manager_reputation is not None
    old, new = report.manager_reputation
    assert old == before and new == cm.manager_reputation
    # Ayni hafta Devler Arenasi maci da oynanabilir: yon ancak iki sonuc ayni yondeyse kesindir
    diffs = []
    for r in (report.user_result, getattr(report, "user_cup_result", None)):
        if r is not None:
            mine, theirs = (r.home, r.away) if r.home.id == team.id else (r.away, r.home)
            diffs.append(mine.stats.goals - theirs.stats.goals)
    assert diffs
    if all(d > 0 for d in diffs):
        assert new > old
    elif all(d < 0 for d in diffs):
        assert new < old
    elif all(d == 0 for d in diffs):
        assert new >= old


def test_no_reputation_change_without_user_team(cm):
    cm.state.user_team_id = None
    before = cm.manager_reputation
    report = cm.play_week()
    assert report.manager_reputation is None and cm.manager_reputation == before


def test_season_end_applies_position_bonus_once(cm):
    cm.set_user_team(cm.find_team("Manchester Blue"))
    last = None
    for _ in range(12):
        if cm.season_finished:
            break
        last = cm.play_week()
    assert last is not None and last.season_finished
    assert last.season_reputation_delta is not None
    team = cm.user_team
    position = cm.position_of(team)
    assert last.season_reputation_delta == reputation.season_delta(position, len(cm.standings(team.league_id)))
    after = cm.manager_reputation
    extra = cm.play_week()                                              # sezon bitti: bir sey degismemeli
    assert not extra.played_any and cm.manager_reputation == after


def test_user_negotiation_uses_career_manager_reputation(cm):
    buyer = cm.find_team("Karadeniz Storm")
    cm.set_user_team(buyer)
    star = max(cm.find_team("Manchester Blue").players, key=lambda p: p.overall_rating)

    cm.state.manager_reputation = 1.0
    closed = cm.open_negotiation(buyer, star, 10_000_000)
    assert not closed.open and closed.manager_reputation == 1.0
    assert "masasına oturmadı" in closed.opening_message

    cm.state.manager_reputation = 20.0
    assert cm.open_negotiation(buyer, star, 10_000_000).manager_reputation == 20.0


def test_ai_clubs_use_reputation_derived_manager(cm):
    city, trabzon = cm.find_team("Manchester Blue"), cm.find_team("Karadeniz Storm")
    cm.state.user_team_id = None
    target = max(cm.find_team("Milano Nerazzurri").players, key=lambda p: p.overall_rating)
    assert cm.open_negotiation(city, target, 1).manager_reputation == reputation.ai_manager_reputation(city.reputation)
    assert cm.manager_reputation_for(city) > cm.manager_reputation_for(trabzon)
