"""
Hesaplar ve kariyer izolasyonu (accounts.py, 10. Asama) entegrasyon testleri.

Kendi test veritabaninda calistirilmasi onerilir (conftest olusturur ve sentetik dunyayi kurar):
    TEST_DB_NAME=fm_db_test_auth python -m pytest -q -p no:cacheprovider tests/test_accounts.py

Temizlik: accounts.users ve career_% semalari modul BASINDA ve SONUNDA silinir (conftest'teki
reset_db hesaplari sifirlamaz). 'public' dunyasina kalici mac yazilmaz: public'te oynanan hafta
geri alinir; public'e yalnizca sahiplik isareti (game_state.user_id) yazilir, o da temizlikte
kullanicilarla birlikte bosalir (ON DELETE SET NULL).

Pahali gercek dunya kurulumu yalnizca ikinci kullanici icin yapilir; kilit/yaris/eski hesap
senaryolarinda seed.seed yalnizca game_state satiri yazan hafif bir sahteyle degistirilir.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _db_available() -> bool:
    if os.getenv("CM_TEST_NO_DB"):
        return False
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

PASSWORD = "Gizli.Parola42"
DIGEST_TABLES = ("game_state", "leagues", "teams", "players", "fixtures", "player_match_stats",
                 "staff", "tournaments", "tournament_entries", "cup_ties")


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _assert_test_database() -> None:
    import database

    expected = os.getenv("TEST_DB_NAME", "fm_db_test")
    actual = database.engine.url.database
    if actual != expected or actual == os.getenv("DB_NAME", "fm_db"):
        raise RuntimeError(f"Hesap testleri yalnızca test veritabanında çalışır (bağlantı: {actual}).")


def _career_schemas() -> set[str]:
    from sqlalchemy import text

    import database

    with database.engine.connect() as conn:
        return set(conn.scalars(text(r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 'career\_%'")))


def _wipe_accounts() -> None:
    """Test kullanicilari ve kariyer semalari silinir; public dunyasi korunur."""
    from sqlalchemy import text

    import database

    _assert_test_database()
    for schema in _career_schemas():
        database.drop_career_schema(schema)
    database.init_accounts()
    with database.engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts.worlds"))           # Faz 12: uyelikler CASCADE ile duser
        conn.execute(text("DELETE FROM accounts.users"))
        if conn.scalar(text("SELECT to_regclass('public.game_state') IS NOT NULL")):
            conn.execute(text("UPDATE public.game_state SET user_id = NULL"))


def _digest(db, schema: str) -> dict[str, str]:
    """Semadaki oyun tablolarinin icerik ozeti (tablo adlari semayla nitelenir: baglamdan bagimsiz)."""
    from sqlalchemy import text

    return {
        table: db.scalar(text(
            f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM \"{schema}\".\"{table}\" t"
        ))
        for table in DIGEST_TABLES
    }


def _read_digest(schema: str) -> dict[str, str]:
    import database

    with database.SessionLocal() as db:
        return _digest(db, schema)


def _game_state(schema: str | None):
    import database
    from models import GameState

    with database.career_context(schema), database.session_scope() as db:
        state = db.get(GameState, 1)
        return SimpleNamespace(week=state.current_week, season=state.season,
                               user_team_id=state.user_team_id, user_id=state.user_id)


def _counts(schema: str | None) -> dict[str, int]:
    from sqlalchemy import func, select

    import database
    from models import Fixture, FixtureStatus, League, Player, Team

    with database.career_context(schema), database.session_scope() as db:
        count = lambda q: db.scalar(q) or 0  # noqa: E731
        return {
            "leagues": count(select(func.count()).select_from(League)),
            "teams": count(select(func.count()).select_from(Team)),
            "players": count(select(func.count()).select_from(Player)),
            "fixtures": count(select(func.count()).select_from(Fixture)),
            "played": count(select(func.count()).select_from(Fixture)
                            .where(Fixture.status == FixtureStatus.PLAYED)),
        }


def _user_row(user_id: int):
    import database
    from models import User

    with database.SessionLocal() as db:
        user = db.get(User, user_id)
        return SimpleNamespace(username=user.username, password_hash=user.password_hash,
                               last_login_at=user.last_login_at, career_schema=user.career_schema)


def _insert_user(username: str, password: str, career_schema: str | None = None) -> int:
    import auth
    import database
    from models import User

    with database.session_scope() as db:
        user = User(username=username, password_hash=auth.hash_password(password),
                    career_schema=career_schema)
        db.add(user)
        db.flush()
        return user.id


def _fake_seed(calls: list, delay: float = 0.0):
    """Hafif seed.seed: yalnizca game_state satiri (sahiplik baglama adimi icin yeterli)."""
    import database
    from models import GameState

    def fake(rng_seed, source="auto", **_kwargs):
        calls.append(SimpleNamespace(schema=database.current_career_schema(), source=source,
                                     rng_seed=rng_seed))
        if delay:
            time.sleep(delay)
        with database.session_scope() as db:
            db.add(GameState(id=1, season=1, current_week=1))
    return fake


def _schema_exists(schema: str) -> bool:
    from sqlalchemy import text

    import database

    with database.engine.connect() as conn:
        return bool(conn.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)"),
                                {"s": schema}))


# ---------------------------------------------------------------------------
# Ortak dunya: A eski 'public' kariyeri devralir, B kendi semasini alir
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    import accounts

    _wipe_accounts()
    public_before = _read_digest("public")
    counts_before = _counts(None)
    state_before = _game_state(None)
    a = accounts.register("MenajerA", PASSWORD, source="synthetic")
    b = accounts.register("menajer_b", PASSWORD, world_seed=7, source="synthetic")
    try:
        yield SimpleNamespace(a=a, b=b, public_before=public_before, counts_before=counts_before,
                              state_before=state_before)
    finally:
        _wipe_accounts()


def test_first_user_claims_legacy_public_career(world):
    import accounts

    a = world.a
    assert isinstance(a, accounts.AuthSession)
    assert (a.username, a.career_schema) == ("MenajerA", "public")
    assert accounts.career_owner("public") == a.user_id

    state = _game_state(None)
    assert state.user_id == a.user_id
    assert (state.week, state.season, state.user_team_id) == (
        world.state_before.week, world.state_before.season, world.state_before.user_team_id)
    # Dunya aynen korunur: yalnizca game_state'teki sahiplik isareti degisti
    after = _read_digest("public")
    changed = {t for t in DIGEST_TABLES if after[t] != world.public_before[t]}
    assert changed <= {"game_state"}
    assert _counts(None) == world.counts_before


def test_second_user_gets_own_fully_seeded_career(world):
    import accounts
    import database

    a, b = world.a, world.b
    assert b.user_id != a.user_id
    assert b.career_schema == f"career_{b.user_id}" and _schema_exists(b.career_schema)
    assert accounts.career_owner(b.career_schema) == b.user_id

    counts = _counts(b.career_schema)
    assert counts["leagues"] > 0 and counts["teams"] > 0 and counts["players"] > 0
    assert counts["fixtures"] > 0 and counts["played"] == 0
    state = _game_state(b.career_schema)
    assert (state.user_id, state.week, state.season) == (b.user_id, 1, 1)
    assert _game_state(None).user_id == a.user_id

    with database.career_context(b.career_schema):
        assert database.schema_problems() == []
    user = _user_row(b.user_id)
    assert user.career_schema == b.career_schema and user.last_login_at is not None
    assert PASSWORD not in user.password_hash and user.password_hash.startswith("scrypt$")


def test_duplicate_username_is_rejected_case_insensitively(world):
    import accounts

    users_before, schemas_before = accounts.user_count(), _career_schemas()
    for name in ("menajera", "MENAJERA", "  MenajerA  ", "MENAJER_B"):
        with pytest.raises(accounts.AccountError) as err:
            accounts.register(name, "Baska.Parola77", source="synthetic")
        assert str(err.value) == "Bu kullanıcı adı zaten alınmış."
    assert accounts.user_count() == users_before
    assert _career_schemas() == schemas_before


def test_register_validation_errors_are_account_and_auth_errors():
    import accounts
    import auth

    cases = [
        ("ab", PASSWORD, "3-32"),
        ("x';DROP SCHEMA public--", PASSWORD, "yalnızca İngilizce harf"),
        ("MenajerX", "menajerx123", "kullanıcı adını"),
        ("MenajerX", "kisa1", "en az 8"),
    ]
    for username, password, fragment in cases:
        with pytest.raises(accounts.AccountError) as err:
            accounts.register(username, password, source="synthetic")
        assert isinstance(err.value, auth.AuthError) and fragment in str(err.value)
    assert _schema_exists("public")


def test_authenticate_success_updates_last_login(world):
    import accounts

    a = world.a
    before = _user_row(a.user_id).last_login_at
    session = accounts.authenticate("  menajera ", PASSWORD)
    assert session == accounts.AuthSession(user_id=a.user_id, username="MenajerA", career_schema="public")
    after = _user_row(a.user_id).last_login_at
    assert after is not None and (before is None or after > before)

    session_b = accounts.authenticate("MENAJER_B", PASSWORD)
    assert session_b == world.b


def test_wrong_password_and_unknown_user_give_same_generic_error(world, monkeypatch):
    import accounts
    import auth

    verified: list[str] = []
    real_verify = auth.verify_password
    monkeypatch.setattr(auth, "verify_password",
                        lambda pw, stored: verified.append(stored) or real_verify(pw, stored))
    before = _user_row(world.a.user_id)

    attempts = [
        ("MenajerA", "Yanlis.Parola1"),
        ("MenajerA", ""),
        ("MenajerA", None),
        ("yok_boyle_biri", PASSWORD),
        ("", PASSWORD),
        ("x" * 500, PASSWORD),
        (None, None),
    ]
    messages = set()
    for username, password in attempts:
        with pytest.raises(accounts.AccountError) as err:
            accounts.authenticate(username, password)
        messages.add(str(err.value))
    assert messages == {"Kullanıcı adı veya parola hatalı."}
    # Her denemede tam olarak bir ozet dogrulamasi; olmayan kullanicida DUMMY_HASH ile
    assert len(verified) == len(attempts)
    assert verified.count(auth.DUMMY_HASH) == 4
    after = _user_row(world.a.user_id)
    assert (after.last_login_at, after.password_hash) == (before.last_login_at, before.password_hash)


def test_login_transparently_rehashes_weak_hash(world):
    import base64
    import hashlib

    from sqlalchemy import update

    import accounts
    import auth
    import database
    from models import User

    salt = b"eski-tuz-16bayt!"
    weak_digest = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=2 ** 12, r=8, p=1, dklen=64)
    weak = f"scrypt$4096$8$1${base64.b64encode(salt).decode()}${base64.b64encode(weak_digest).decode()}"
    with database.session_scope() as db:
        db.execute(update(User).where(User.id == world.b.user_id).values(password_hash=weak))

    assert accounts.authenticate("menajer_b", PASSWORD) == world.b
    upgraded = _user_row(world.b.user_id).password_hash
    assert upgraded != weak and not auth.needs_rehash(upgraded)
    assert auth.verify_password(PASSWORD, upgraded)
    assert accounts.authenticate("menajer_b", PASSWORD) == world.b
    assert _user_row(world.b.user_id).password_hash == upgraded      # tekrar ozetlenmez


# ---------------------------------------------------------------------------
# Izolasyon
# ---------------------------------------------------------------------------

def test_week_in_user_b_career_leaves_public_untouched(world):
    import database
    from career_manager import CareerManager

    b = world.b
    public_before = _read_digest("public")
    b_before = _game_state(b.career_schema)

    with database.career_context(b.career_schema), database.session_scope() as db:
        cm = CareerManager(db, seed=3)
        team = cm.teams()[0]
        cm.set_user_team(team)
        report = cm.play_week()
        assert report.results
        chosen = team.id

    b_after = _game_state(b.career_schema)
    assert b_after.week == b_before.week + 1 and b_after.user_team_id == chosen
    assert _counts(b.career_schema)["played"] > 0
    assert _read_digest("public") == public_before
    assert _game_state(None).user_id == world.a.user_id


def test_week_in_public_career_leaves_user_b_untouched(world):
    from sqlalchemy import text

    import database
    from career_manager import CareerManager
    from models import GameState

    b = world.b
    b_before = _read_digest(b.career_schema)
    with database.career_context("public"):
        db = database.SessionLocal()
        try:
            cm = CareerManager(db, seed=5)
            week = cm.current_week
            if cm.season_finished:
                pytest.skip("public sezonu bitmiş")
            cm.set_user_team(cm.teams()[1])
            cm.play_week()
            db.flush()
            assert db.get(GameState, 1).current_week == week + 1
            assert db.scalar(text('SELECT current_week FROM "public".game_state WHERE id = 1')) == week + 1
            # Ayni islem kendi yazdiklarini gorur: yazilar B'ye gitseydi burada gorunurdu
            assert _digest(db, b.career_schema) == b_before
        finally:
            db.rollback()                    # public dunyasi diger test dosyalari icin korunur
            db.close()
    assert _read_digest(b.career_schema) == b_before


def test_session_without_context_still_sees_public(world):
    from sqlalchemy import text

    import accounts
    import database
    from models import GameState

    a, b = world.a, world.b
    with database.SessionLocal() as db:
        assert db.scalar(text("SELECT current_schema()")) == "public"
        assert db.get(GameState, 1).user_id == a.user_id

    with database.career_context(b.career_schema):
        with database.SessionLocal() as db:
            assert db.scalar(text("SELECT current_schema()")) == b.career_schema
            assert db.get(GameState, 1).user_id == b.user_id
        with database.career_context(None), database.SessionLocal() as db:
            assert db.scalar(text("SELECT current_schema()")) == "public"

    # Web cozucusu: baglam yoksa cozucunun semasi; hesap islemleri cozucuyu hic cagirmaz
    def exploding_resolver():
        raise RuntimeError("çözücü çağrıldı")

    try:
        database.set_career_schema_resolver(lambda: b.career_schema)
        with database.SessionLocal() as db:
            assert db.get(GameState, 1).user_id == b.user_id
        database.set_career_schema_resolver(exploding_resolver)
        assert accounts.user_count() >= 2
        assert accounts.career_owner("public") == a.user_id
        assert accounts.authenticate("MenajerA", PASSWORD).career_schema == "public"
    finally:
        database.set_career_schema_resolver(None)
    with database.SessionLocal() as db:
        assert db.scalar(text("SELECT current_schema()")) == "public"


def test_schema_names_are_validated():
    import accounts
    import database

    bad = [
        'public"; DROP SCHEMA public CASCADE; --',
        "career_1; DROP SCHEMA public",
        "Career_1",
        "career-1",
        "1career",
        "accounts",
        "c" * 64,
        "pg_temp public",
        "şema",
    ]
    for name in bad:
        with pytest.raises(ValueError):
            database.valid_schema_name(name)
        with pytest.raises(ValueError), database.career_context(name):
            pass
        with pytest.raises(ValueError):
            accounts.career_owner(name)
        with pytest.raises(ValueError):
            database.drop_career_schema(name)
        with pytest.raises(ValueError):
            accounts.ensure_career_ready(accounts.AuthSession(user_id=1, username="x", career_schema=name))
    with pytest.raises(ValueError):
        database.drop_career_schema("public")
    assert _schema_exists("public") and _counts(None)["teams"] > 0


# ---------------------------------------------------------------------------
# Kurulum hatalari, sahiplik onarimi, eski hesaplar
# ---------------------------------------------------------------------------

def test_failed_provisioning_leaves_no_user_and_no_schema(world, monkeypatch):
    import accounts
    import database
    import seed

    users_before, schemas_before = accounts.user_count(), _career_schemas()
    seen: list[str] = []

    def broken_seed(rng_seed, source="auto", **_kwargs):
        schema = database.current_career_schema()
        seen.append(schema)
        assert _schema_exists(schema)            # init_db semayi kurmustu
        raise RuntimeError("disk dolu")

    monkeypatch.setattr(seed, "seed", broken_seed)
    with pytest.raises(accounts.AccountError) as err:
        accounts.register("MenajerC", PASSWORD, source="synthetic")
    assert "Kariyer hazırlanamadı" in str(err.value) and "disk" not in str(err.value)
    assert len(seen) == 1 and seen[0].startswith("career_") and not _schema_exists(seen[0])

    def seed_error(rng_seed, source="auto", **_kwargs):
        raise seed.SeedError("FM verisinden oynanabilir lig kurulamadı.")

    monkeypatch.setattr(seed, "seed", seed_error)
    with pytest.raises(accounts.AccountError) as err:
        accounts.register("MenajerC", PASSWORD, source="fm")
    assert "oynanabilir lig kurulamadı" in str(err.value)

    assert accounts.user_count() == users_before and _career_schemas() == schemas_before
    with pytest.raises(accounts.AccountError, match="Kullanıcı adı veya parola hatalı."):
        accounts.authenticate("MenajerC", PASSWORD)


def test_username_race_during_provisioning_cleans_up(world, monkeypatch):
    """Kurulum surerken ayni ad baska yoldan alinirsa: yeni sema silinir, hesap olusmaz."""
    from sqlalchemy import text

    import accounts
    import database
    import seed

    calls: list = []
    fake = _fake_seed(calls)

    def racing_seed(rng_seed, source="auto", **kwargs):
        fake(rng_seed, source, **kwargs)
        _insert_user("menajerd", "Diger.Parola55")

    monkeypatch.setattr(seed, "seed", racing_seed)
    users_before, schemas_before = accounts.user_count(), _career_schemas()
    with pytest.raises(accounts.AccountError, match="Bu kullanıcı adı zaten alınmış."):
        accounts.register("MenajerD", PASSWORD, source="synthetic")
    assert len(calls) == 1 and not _schema_exists(calls[0].schema)
    assert _career_schemas() == schemas_before
    assert accounts.user_count() == users_before + 1                 # yalnizca yarisi kazanan
    with database.engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts.users WHERE lower(username) = 'menajerd'"))


def test_rebuilt_public_world_is_not_claimed_and_owner_is_relinked(world, monkeypatch):
    from sqlalchemy import text

    import accounts
    import database
    import seed

    a = world.a
    # 'python seed.py' public dunyasini yeniden kurunca sahiplik isareti bosalir
    with database.engine.begin() as conn:
        conn.execute(text("UPDATE public.game_state SET user_id = NULL"))
    calls: list = []
    monkeypatch.setattr(seed, "seed", _fake_seed(calls))
    _youth_setup_spy(monkeypatch, [])

    newcomer = accounts.register("MenajerE", PASSWORD, source="synthetic")
    assert newcomer.career_schema == f"career_{newcomer.user_id}"
    assert len(calls) == 1 and calls[0].schema == newcomer.career_schema
    assert accounts.career_owner("public") == a.user_id
    assert _game_state(None).user_id is None

    # Giris sahipligi geri baglar
    assert accounts.authenticate("MenajerA", PASSWORD) == a
    assert _game_state(None).user_id == a.user_id

    # ensure_career_ready de onarir (ve bunu bildirir)
    with database.engine.begin() as conn:
        conn.execute(text("UPDATE public.game_state SET user_id = NULL"))
    messages = accounts.ensure_career_ready(a)
    assert any("sahipliği onarıldı" in m for m in messages)
    assert _game_state(None).user_id == a.user_id
    assert not any("sahipliği" in m for m in accounts.ensure_career_ready(a))

    # Baskasinin kariyerini sahte oturumla "onarmak" mumkun degil
    with database.engine.begin() as conn:
        conn.execute(text("UPDATE public.game_state SET user_id = NULL"))
    accounts.ensure_career_ready(accounts.AuthSession(user_id=newcomer.user_id, username="MenajerE",
                                                      career_schema="public"))
    assert _game_state(None).user_id is None
    accounts.authenticate("MenajerA", PASSWORD)
    assert _game_state(None).user_id == a.user_id


def _youth_setup_spy(monkeypatch, calls: list):
    """
    CareerManager.ensure_youth_setup yerine: cagrildigi semayi kaydeder, gercegini (varsa) calistirir
    ve bir isaret mesaji ekler. 'public'te gercegi CALISTIRILMAZ: ortak test dunyasina akademi yazilmaz.
    """
    import database
    from career_manager import CareerManager

    real = getattr(CareerManager, "ensure_youth_setup", None)
    real_club = getattr(CareerManager, "ensure_club_setup", None)

    def spy(self):
        schema = database.current_career_schema()
        calls.append(schema)
        done = list(real(self)) if real is not None and schema != "public" else []
        return done + ["altyapı kontrolü yapıldı"]

    def club_spy(self):
        # 11. Asama: seed tesis/sponsor doldurmaz; 'public' test dunyasina kalici sponsor/gelir verisi yazilmasin
        schema = database.current_career_schema()
        return list(real_club(self)) if real_club is not None and schema != "public" else []

    monkeypatch.setattr(CareerManager, "ensure_youth_setup", spy, raising=False)
    monkeypatch.setattr(CareerManager, "ensure_club_setup", club_spy, raising=False)


def test_ensure_career_ready_upgrades_only_that_career(world, monkeypatch):
    from sqlalchemy import text

    import accounts
    import database

    b = world.b
    youth_calls: list = []
    _youth_setup_spy(monkeypatch, youth_calls)
    first = accounts.ensure_career_ready(b)
    assert isinstance(first, list) and all(isinstance(m, str) for m in first)
    assert "altyapı kontrolü yapıldı" in first and youth_calls == [b.career_schema]

    # B'nin eski semali oldugunu varsay: bilinen ek sutun eksik
    with database.engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{b.career_schema}".players DROP COLUMN development_progress'))
    messages = accounts.ensure_career_ready(b)
    assert "sütun eklendi: players.development_progress" in messages
    again = accounts.ensure_career_ready(b)
    assert not any(m.startswith(("tablo eklendi", "sütun eklendi")) for m in again)
    with database.career_context(b.career_schema):
        assert database.schema_problems() == []
    assert database.schema_problems() == []                         # public etkilenmedi

    # Web testleri sahte oturumla cagirir: kullanici satiri olmasa da calisir, sahiplige dokunmaz
    fake = accounts.AuthSession(user_id=0, username="test_menajer", career_schema="public")
    assert isinstance(accounts.ensure_career_ready(fake), list)
    assert _game_state(None).user_id == world.a.user_id
    assert youth_calls[-1] == "public"


def test_legacy_user_without_career_gets_one_once(world, monkeypatch):
    from sqlalchemy import text

    import accounts
    import database
    import seed

    calls: list = []
    monkeypatch.setattr(seed, "seed", _fake_seed(calls, delay=0.4))
    user_id = _insert_user("EskiMenajer", PASSWORD, career_schema=None)

    # Ayni anda iki giris: kurulum bir kez yapilir, ikisi de ayni kariyeri alir
    results: list = []
    barrier = threading.Barrier(2)

    def login():
        barrier.wait()
        try:
            results.append(accounts.authenticate("eskimenajer", PASSWORD))
        except Exception as exc:                 # pragma: no cover - hata raporu icin
            results.append(exc)

    threads = [threading.Thread(target=login) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 2 and all(isinstance(r, accounts.AuthSession) for r in results), results
    assert results[0] == results[1]
    session = results[0]
    assert session.user_id == user_id and session.career_schema == f"career_{user_id}"
    # 14C: kaynak verilmez -> seed.new_world_source() (conftest: OFM_NEW_WORLD_SOURCE=synthetic)
    assert len(calls) == 1 and calls[0].source == "synthetic" and calls[0].schema == session.career_schema
    assert _user_row(user_id).career_schema == session.career_schema
    assert _game_state(session.career_schema).user_id == user_id

    # Semasi kaybolmus hesap (elle silinmis): giriste yeniden kurulur
    database.drop_career_schema(session.career_schema)
    with database.engine.begin() as conn:
        conn.execute(text("UPDATE accounts.users SET career_schema = 'career_999999' WHERE id = :id"),
                     {"id": user_id})
    again = accounts.authenticate("EskiMenajer", PASSWORD)
    assert again.career_schema == f"career_{user_id}" and _schema_exists(again.career_schema)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 14C: yeni kariyerin kaynagi (seed.new_world_source) ve acik veri dunyasi
# ---------------------------------------------------------------------------

def _ensure_public_owned() -> None:
    """Yeni kayit 'public'i devralmasin: sahibi yoksa sahte bir sahip yazilir."""
    from sqlalchemy import text

    import database

    with database.engine.connect() as conn:
        owned = conn.scalar(text("SELECT count(*) FROM accounts.users WHERE career_schema = 'public'"))
    if not owned:
        _insert_user("PublicSahibi", PASSWORD, career_schema="public")


def _drop_registered(*sessions) -> None:
    from sqlalchemy import text

    import database

    for session in sessions:
        database.drop_career_schema(session.career_schema)
        with database.engine.begin() as conn:
            conn.execute(text("DELETE FROM accounts.users WHERE id = :id"), {"id": session.user_id})


def test_register_without_source_follows_the_new_world_resolver(world, monkeypatch):
    """Kaynaksiz kayit ortam degiskenini cagri aninda izler; acikca verilen kaynak her zaman kazanir."""
    import accounts
    import seed

    _ensure_public_owned()
    calls: list = []
    monkeypatch.setattr(seed, "seed", _fake_seed(calls))
    sessions = [accounts.register("KaynakKurgu", PASSWORD)]                    # conftest: synthetic (bugunku dunya)
    monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, "open")
    sessions.append(accounts.register("KaynakAcik", PASSWORD))
    sessions.append(accounts.register("KaynakAcik2", PASSWORD, source="synthetic"))
    try:
        assert [c.source for c in calls] == ["synthetic", "open", "synthetic"]
        assert [c.schema for c in calls] == [s.career_schema for s in sessions]
    finally:
        _drop_registered(*sessions)


def test_register_with_open_source_builds_the_real_club_world(world, monkeypatch):
    """register(source='open'): 6 gercek lig, kulup adlari acik veriden, maske seviyesi light (paylasilabilir)."""
    import functools

    from sqlalchemy import func, select

    import accounts
    import database
    import open_loader
    import worlds
    from models import GameState, League, Player, Team

    _ensure_public_owned()
    # Hiz icin ornek boyut (lig basina 6 kulup; --open-sample karsiligi). Itibarlar tam dunyayla aynidir.
    monkeypatch.setattr(accounts, "_build_world", functools.partial(accounts._build_world, open_sample=True))
    session = accounts.register("AcikMenajer", PASSWORD, source="open")
    try:
        assert session.career_schema == f"career_{session.user_id}"
        data = open_loader.load_open_data()
        top = [lg for lg in data.leagues["leagues"] if lg["tier"] == 1]      # 16A-0: 2. kademeler kurulmaz
        open_leagues = {lg["name"] for lg in top}
        open_clubs = {c["name"] for lg in top for c in lg["clubs"]}
        with database.career_context(session.career_schema), database.session_scope() as db:
            leagues = set(db.scalars(select(League.name)))
            teams = set(db.scalars(select(Team.name)))
            state = db.get(GameState, 1)
            sources = dict(db.execute(select(Player.data_source, func.count()).group_by(Player.data_source)).all())
            assert len(leagues) == 6 and leagues == open_leagues
            assert len(teams) == 6 * open_loader.SAMPLE_CLUBS_PER_LEAGUE and teams <= open_clubs
            assert {"Galatasaray", "Fenerbahçe"} <= teams
            assert set(sources) <= {"open", "academy"} and sources["open"] > 0
            assert state.mask_level == "light" and not worlds.world_has_real_names(state)
            assert state.user_id == session.user_id
    finally:
        _drop_registered(session)


def test_concurrent_same_username_registration(world, monkeypatch):
    import accounts
    import seed

    calls: list = []
    monkeypatch.setattr(seed, "seed", _fake_seed(calls, delay=0.4))
    users_before = accounts.user_count()
    results: list = []
    barrier = threading.Barrier(2)

    def register(name):
        barrier.wait()
        try:
            results.append(accounts.register(name, PASSWORD, source="synthetic"))
        except Exception as exc:
            results.append(exc)

    threads = [threading.Thread(target=register, args=(n,)) for n in ("Ikiz", "IKIZ")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    sessions = [r for r in results if isinstance(r, accounts.AuthSession)]
    errors = [r for r in results if not isinstance(r, accounts.AuthSession)]
    assert len(sessions) == 1 and len(errors) == 1, results
    assert isinstance(errors[0], accounts.AccountError) and str(errors[0]) == "Bu kullanıcı adı zaten alınmış."
    assert len(calls) == 1                         # kaybeden taraf pahali kurulumu hic yapmadi
    assert accounts.user_count() == users_before + 1


# ---------------------------------------------------------------------------
# Eski kariyeri devralma kilidi (SON test: 'world' kullanicilarini siler, kendi temizligini yapar)
# ---------------------------------------------------------------------------

def test_public_claim_waits_for_lock_and_rechecks_owner(monkeypatch):
    from sqlalchemy import text

    import accounts
    import database
    import seed

    _wipe_accounts()
    calls: list = []
    monkeypatch.setattr(seed, "seed", _fake_seed(calls))
    rival = _insert_user("Rakip", "Rakip.Parola9", career_schema=None)
    holder = database.engine.connect()
    worker = None
    try:
        holder.execute(text("SELECT user_id FROM public.game_state WHERE id = 1 FOR UPDATE"))
        results: list = []

        def run():
            try:
                results.append(accounts.register("MenajerZ", PASSWORD, source="synthetic"))
            except Exception as exc:
                results.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 15
        waiting = 0
        while time.monotonic() < deadline and not waiting:
            waiting = holder.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            ))
            time.sleep(0.05)
        assert waiting, "kayıt game_state kilidinde beklemedi"
        assert not results

        # Kilidi tutan islem kariyeri baskasina verir; bekleyen kayit bunu gorup kendi kariyerini kurar
        holder.execute(text("UPDATE public.game_state SET user_id = :uid WHERE id = 1"), {"uid": rival})
        holder.commit()
        worker.join(timeout=60)
        assert len(results) == 1 and isinstance(results[0], accounts.AuthSession), results
        assert results[0].career_schema == f"career_{results[0].user_id}"
        assert _game_state(None).user_id == rival and len(calls) == 1
    finally:
        holder.rollback()
        holder.close()
        if worker is not None:
            worker.join(timeout=60)

    # Kilit yarisinda ayni anda iki yeni kayit: tam olarak biri devralir
    _wipe_accounts()
    results = []
    barrier = threading.Barrier(2)

    def register(name):
        barrier.wait()
        try:
            results.append(accounts.register(name, PASSWORD, source="synthetic"))
        except Exception as exc:
            results.append(exc)

    try:
        threads = [threading.Thread(target=register, args=(n,)) for n in ("Birinci", "Ikinci")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert len(results) == 2 and all(isinstance(r, accounts.AuthSession) for r in results), results
        assert sorted(r.career_schema == "public" for r in results) == [False, True]
        owner = next(r for r in results if r.career_schema == "public")
        assert _game_state(None).user_id == owner.user_id == accounts.career_owner("public")
    finally:
        _wipe_accounts()


def test_server_side_lockout_after_repeated_wrong_passwords(world, monkeypatch):
    """Tarayici oturumu degistirmek kilidi asmaz: kilit hesaptadir; basarili giris sayaci sifirlar."""
    import accounts
    import database
    import seed
    from models import User

    monkeypatch.setattr(seed, "seed", _fake_seed([]))                     # kariyersiz hesabin ilk girisi hafif

    user_id = _insert_user("KilitliMenajer", PASSWORD)
    for _ in range(accounts.MAX_FAILED_LOGINS - 1):
        with pytest.raises(accounts.AccountError, match="hatalı"):
            accounts.authenticate("KilitliMenajer", "Yanlis.Parola1")
    with database.SessionLocal() as db:
        assert db.get(User, user_id).failed_logins == accounts.MAX_FAILED_LOGINS - 1

    with pytest.raises(accounts.AccountError, match="hatalı"):
        accounts.authenticate("KilitliMenajer", "Yanlis.Parola1")          # 5. hata: kilit
    with pytest.raises(accounts.AccountError, match="kilitlendi"):
        accounts.authenticate("KilitliMenajer", PASSWORD)                   # dogru parola bile reddedilir
    with database.session_scope() as db:
        db.get(User, user_id).locked_until = None                          # kilit suresi doldu
    session = accounts.authenticate("KilitliMenajer", PASSWORD)
    with database.SessionLocal() as db:
        user = db.get(User, user_id)
        assert session.user_id == user_id and user.failed_logins == 0 and user.locked_until is None



def test_concurrent_wrong_passwords_cannot_exceed_the_attempt_budget(world, monkeypatch):
    """Paralel hatali denemeler de en fazla MAX_FAILED_LOGINS gercek parola dogrulamasi yaptirir, sonra kilit."""
    import threading

    import accounts
    import auth
    import database
    import seed
    from models import User

    monkeypatch.setattr(seed, "seed", _fake_seed([]))
    user_id = _insert_user("ParalelMenajer", PASSWORD)
    real_checks, lock = [], threading.Lock()
    real_verify = auth.verify_password

    def spy(password, stored):
        if stored != auth.DUMMY_HASH:
            with lock:
                real_checks.append(password)
        return real_verify(password, stored)

    monkeypatch.setattr(auth, "verify_password", spy)
    barrier = threading.Barrier(16)
    outcomes: list[str] = []

    def attempt():
        barrier.wait()
        try:
            accounts.authenticate("ParalelMenajer", "Yanlis.Parola1")
            outcomes.append("ok")
        except accounts.AccountError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(outcomes) == 16 and "ok" not in outcomes
    assert len(real_checks) <= accounts.MAX_FAILED_LOGINS
    with database.SessionLocal() as db:
        assert db.get(User, user_id).locked_until is not None
    with pytest.raises(accounts.AccountError, match="kilitlendi"):
        accounts.authenticate("ParalelMenajer", PASSWORD)


def test_schema_name_validation_rejects_newline_and_system_schemas():
    import database

    for bad in ("career_2\n", "pg_catalog", "pg_toast", "information_schema", "accounts", "Career_2", "a;b"):
        with pytest.raises(ValueError):
            database.valid_schema_name(bad)
    assert database.valid_schema_name("career_12_2") == "career_12_2"
    assert database.valid_schema_name("public") == "public"
