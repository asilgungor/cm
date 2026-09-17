"""
Cok koltuklu kariyer testleri (Faz 12 / 14. Asama, A2: CareerManager genellestirmesi).

Gercek PostgreSQL'e karsi calisir. Eski kariyer paritesi dunyayi yeniden kurar (commit); diger testler kendi
islemlerini rollback eder. Koltuk satirlari seats.SeatStore ile dogrudan eklenir (worlds.py kaydi A1'in).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transfers  # noqa: E402
from career_manager import CareerManager, ConcernError, LiveMatchError  # noqa: E402
from career_views import cup_report_lines, week_report_lines  # noqa: E402
from models import GameMode  # noqa: E402


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


# ===========================================================================
# 1) ESKI KARIYER PARITESI (senaryo: kurulum, transfer, sponsor, hazirlik maci, canli mac, tam sezon, sezon
#    devri ve yeni sezonun ilk haftasi). Beklenen ozetler HEAD cd1516f koduyla (A2 degisikliklerinden once)
#    ayni senaryodan uretildi.
# ===========================================================================

PARITY_SEED = 4242
PARITY_USER_TEAM = "Istanbul Lions"
# Parmak izine giren kariyer tablolari (Faz 12 tablolari -- world_managers, season_standings ... -- haric)
PARITY_TABLES = (
    "game_state", "leagues", "teams", "players", "staff", "fixtures", "player_match_stats", "tournaments",
    "tournament_entries", "cup_ties", "transfer_log", "season_honours", "news_items", "shortlist", "friendlies",
    "tactic_presets",
)


def _fresh_world() -> None:
    """conftest ile ayni temiz dunya (mod secilmemis): sira sayaclari da sifirlanir -> id'ler deterministik."""
    import database
    import seed

    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _score(result) -> str | None:
    return None if result is None else f"{result.home.name} {result.home_score}-{result.away_score} {result.away.name}"


def _report_payload(report) -> dict:
    notes = lambda items: [(n.player_id, n.player_name, n.team_name, n.detail) for n in items]  # noqa: E731
    return {
        "lines": week_report_lines(report), "cup_lines": cup_report_lines(report),
        "when": (report.season, report.week, report.midweek_only, report.season_finished),
        "results": [(fx.id, _score(r)) for fx, r in report.results],
        "cup_results": [(fx.id, _score(r)) for fx, r in report.cup_results],
        "user": (_score(report.user_result), _score(report.user_cup_result), report.lineup_notes),
        "reputation": (report.manager_reputation, report.season_reputation_delta),
        "money": (report.finance_note, report.sponsor_income, report.gate_income, report.tv_income,
                  report.prize_income, report.prize_notes),
        "notes": (notes(report.injuries), notes(report.suspensions), notes(report.development_notes),
                  notes(report.youth_intake), report.youth_intake_total, report.academy_notes,
                  notes(report.concern_notes), notes(report.wage_demands), report.honours_notes),
        "cup": (report.cup_label, report.cup_notes,
                report.cup_champion.name if report.cup_champion is not None else None),
        "transfers": [(t.describe(), t.player_id, t.from_team_id, t.to_team_id, t.kind) for t in report.transfers],
    }


def _table_digests(db) -> dict[str, str]:
    from sqlalchemy import DateTime

    from database import Base

    db.flush()
    digests: dict[str, str] = {}
    for name in PARITY_TABLES:
        table = Base.metadata.tables[name]
        cols = [c for c in table.columns if not isinstance(c.type, DateTime)]
        rows = db.execute(select(*cols).order_by(*table.primary_key.columns)).all()
        digests[name] = _digest([list(row) for row in rows])
    return digests


def _parity_run(prepare=None, tournament: bool = False) -> dict:
    """
    Temiz dunyada eski tek menajer senaryosu; islem sonunda GERI ALINIR. prepare(db): senaryodan once (orn.
    birincil koltuk satiri). tournament: turnuva modu (kupa + canli kupa maci + sezon devri). Donus:
    {"reports": ozet, "tables": tablo ozetleri, "log": okunur kayit}.

    Dongusel cop toplayici senaryo boyunca KAPALI: HEAD'de bile mac olaylari, kimlik haritasindan dusen Team
    nesnelerinin oyuncu koleksiyonunun ne zaman yeniden yuklendigine (kadro sirasi) baglidir; dongulerdeki Team
    nesnelerinin toplanma ani surec gecmisine gore degisir. gc kapaliyken sonuc yalnizca koda ve veriye baglidir.
    """
    import gc

    from database import SessionLocal

    _fresh_world()
    db = SessionLocal()
    log: list = []
    reports: list = []
    gc.collect()
    gc.disable()
    try:
        if prepare is not None:
            prepare(db)
            db.flush()
        cm = CareerManager(db, seed=PARITY_SEED)
        cm.set_game_mode(GameMode.TOURNAMENT if tournament else GameMode.CAREER)
        log.append(("setup", cm.ensure_youth_setup(), cm.ensure_club_setup()))
        if tournament:
            return _parity_tournament(cm, db, log, reports)
        user = cm.find_team(PARITY_USER_TEAM)
        cm.set_user_team(user)

        # --- 1. hafta menajer islemleri: izleme listesi, transfer, sponsor, taktik, hazirlik maci
        watch = max(cm.find_team("Madrid Blancos").players, key=lambda p: (p.overall_rating, p.id))
        cm.shortlist_add(watch, "izle")
        user.transfer_budget += 250_000_000
        db.flush()
        for seller_name in ("Vesuvio Azzurri", "Rhône Gones", "Karadeniz Storm", "Sachsen Bullen"):
            seller = cm.find_team(seller_name)
            target = sorted(seller.players, key=lambda p: (p.overall_rating, p.id))[len(seller.players) // 2]
            cm.shortlist_add(target, "hedef")
            fee = transfers.asking_price(target, seller, user.reputation) * 2
            decision = cm.offer_fee(user, target, fee)
            negotiation = cm.open_negotiation(user, target, fee)
            log.append(("offer", target.id, decision.accepted, decision.asking, negotiation.open))
            if not negotiation.open:
                continue
            response = negotiation.respond(negotiation.demand)
            log.append(("negotiation", response.status.value))
            if response.status is transfers.NegotiationStatus.ACCEPTED:
                room = negotiation.demand.wage - user.free_wage
                if room > 0:
                    cm.shift_budget(user, room)
                news = cm.complete_transfer(user, target, fee, negotiation.demand)
                log.append(("transfer", news.describe()))
                break
        log.append(("shortlist", [(r.player_id, r.team_name, r.asking_price, r.transfer_banned)
                                  for r in cm.shortlist()]))
        sponsor = cm.sign_sponsor(user, 0)
        log.append(("sponsor", sponsor.brand, sponsor.weekly, sponsor.seasons))
        cm.set_team_roles(user, cm.suggest_team_roles(user))
        cm.auto_lineup(user)
        # maas talepleri dogsun: sozlesme anindaki guc dusuk (kullanici + bir AI kulubu)
        for club in (user, cm.find_team("London Gunners")):
            for p in list(club.players)[:3]:
                p.contract_overall = max(1, p.overall_rating - 15)
        db.flush()
        friendly = cm.play_friendly(cm.find_team("Manchester Blue"))
        log.append(("friendly", friendly.score, friendly.goals))

        # --- sezon: 2. haftada canli mac, her hafta maas taleplerine sirayla evet/hayir
        live_done, answer = False, True
        for _ in range(40):
            if cm.season_finished:
                break
            if cm.current_week == 2 and not live_done:
                live_done = True
                try:
                    prep = cm.prepare_live_match()
                except LiveMatchError as exc:
                    log.append(("live-refused", str(exc)))
                else:
                    if prep.midweek_report is not None:
                        reports.append(_report_payload(prep.midweek_report))
                    log.append(("live", prep.title, prep.competition.value, prep.managed_team_id))
                    report = cm.save_live_result(prep.fixture_id, prep.engine.simulate())
                    reports.append(_report_payload(report))
                    continue
            reports.append(_report_payload(cm.play_week()))
            for player in list(user.players):
                if player.wage_demand is None:
                    continue
                try:
                    log.append(("wage", player.id, cm.respond_wage_demand(player, answer)))
                except ConcernError as exc:
                    log.append(("wage-error", player.id, str(exc)))
                answer = not answer

        # --- sezon devri ve yeni sezonun ilk haftasi
        new_season = cm.start_new_season()
        log.append(("new-season", new_season, cm.new_season_notes,
                    sorted((k, sorted(v.items())) for k, v in cm.season_payouts.items())))
        if cm.sponsor_offers(user):
            cm.sign_sponsor(user, len(cm.sponsor_offers(user)) - 1)
        reports.append(_report_payload(cm.play_week()))
        log.append(("state", cm.season, cm.current_week, cm.manager_reputation, cm.career_week,
                    [n.text for n in cm.world_news(limit=200)]))
        return {"reports": _digest(reports), "tables": _table_digests(db), "log": _digest(log),
                "weeks": len(reports), "raw_log": log}
    finally:
        gc.enable()
        db.rollback()
        db.close()


def _parity_tournament(cm, db, log: list, reports: list) -> dict:
    """Turnuva modu senaryosu (_parity_run icinden; islem cagiranda geri alinir)."""
    t = cm.tournaments.current()
    user = cm.tournaments.participants(t)[0]
    cm.set_user_team(user)
    cm.set_team_roles(user, cm.suggest_team_roles(user))
    cm.auto_lineup(user)
    live_done = False
    for _ in range(40):
        if cm.season_finished:
            break
        if not live_done and (cm.live_fixture() is not None or cm.live_cup_draw_pending()):
            live_done = True
            prep = cm.prepare_live_match()
            log.append(("live", prep.title, prep.competition.value, prep.managed_team_id))
            reports.append(_report_payload(cm.save_live_result(prep.fixture_id, prep.engine.simulate())))
            continue
        reports.append(_report_payload(cm.play_week()))
    new_season = cm.start_new_season()
    log.append(("new-season", new_season, cm.new_season_notes, cm.state.user_team_id))
    reports.append(_report_payload(cm.play_week()))
    log.append(("state", cm.season, cm.current_week, cm.manager_reputation, cm.career_week,
                [n.text for n in cm.world_news(limit=200)]))
    return {"reports": _digest(reports), "tables": _table_digests(db), "log": _digest(log),
            "weeks": len(reports), "raw_log": log}


def _schema_digest() -> str:
    """Parmak izine giren sutun adlari: model degisirse tablo ozetleri karsilastirilamaz (rapor + kayit yine)."""
    from sqlalchemy import DateTime

    from database import Base

    cols = {name: [c.name for c in Base.metadata.tables[name].columns if not isinstance(c.type, DateTime)]
            for name in PARITY_TABLES}
    return hashlib.sha256(json.dumps(cols, sort_keys=True).encode()).hexdigest()


# HEAD cd1516f (A2 oncesi) ile yakalanan ozetler. Yeniden uretmek: HEAD'in career_manager.py / tournament_manager.py
# kopyalari sys.path'in onunde iken _parity_run() / _parity_run(tournament=True) sonuclari.
HEAD_SCHEMA_DIGEST = "80bcc55a3008bb5274987877fbdaf0685009fd8cf2bc0d59019016c4c67d6f68"
HEAD_PARITY = {
    "career": {
        "weeks": 9,
        "reports": "e14e49621dd22724eaf87ceea5a548ebd949889cc7f519a82c3f1d13960ea667",
        "log": "b17118ebe58ecbe176e095665a8ab1a7985b1696cd9eed5d1d67a503cf87f058",
        "tables": {
            "game_state": "f180e6eb22a8079c2cd0e46550fb6177498298da5ab4e692e2e70e77388e83b4",
            "leagues": "0ef407c11272312c5dde8ea771e239cd2781cae7f62189adae431a14e6e4d31e",
            "teams": "8d67105cdf977857e7485bee3f452007ffbab885a6f46d5b8bb7ab995f295df8",
            "players": "8566979913342b0ec56e8be1b0667283c35191b46e4a224551bfd50a7beda0b2",
            "staff": "912cca9b92981c4ecad63de931be882319e1c050656dcfb97cd0c2d2ab37c251",
            "fixtures": "247e59c4df9a781dfe5e6c2e5932e85098d8efa3c7331542a5a74a51c5119fc5",
            "player_match_stats": "90baf9229a47e7599c0e5f3528f578bbce6ca07edad2369cd2032937f6e18f16",
            "tournaments": "2f1a52d52f30f66e39edabd4ea8daff44a69826b6a8924989c75b21d859dd7e9",
            "tournament_entries": "5af30104a3a85ae21ec37c09662d6c8af1ee45e6cc76b5c26367a240c46a3db0",
            "cup_ties": "35bc9ca89ffd0bf3221618fa3991ef077101678e08fe895ca43ad87ca035c794",
            "transfer_log": "3b0edc050fc10002920e93031554f1a45d100af7703954c891ec2b310a52de27",
            "season_honours": "7ad4650cd05d940ceed0b9cdc7c9379bb64e63e6af16a84ef56754b9b7c07f09",
            "news_items": "a45ab4e1a0e15ffb99c17645a54fb443e636c84b90c5b7305456e11d8644dccc",
            "shortlist": "9b954b6de147aca19ce66363c8ee13d61cb31351b002251ca5b315ed538d597a",
            "friendlies": "a015b400cbf47ec360d221359c583cec589358469e907b1ec1a97b8aa16ce608",
            "tactic_presets": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        },
    },
    "tournament": {
        "weeks": 8,
        "reports": "432d061607748226305337a4b02bbf50e25d2ada042e8d6973b509379a0a5732",
        "log": "4319b93510752506488f452c15fc6e87d7dffffc9a2af4ebe8c37bf0d22d2c21",
        "tables": {
            "game_state": "d034b0acca602dc9bd29b047e51ea3fe2faed1abf0fd1cd87466f79ad362d21b",
            "leagues": "0ef407c11272312c5dde8ea771e239cd2781cae7f62189adae431a14e6e4d31e",
            "teams": "2b6058ad3ccf6f22cec64c2c0f7255912dccd05840cc98aae036ccc83f2617ad",
            "players": "da02d592aaafec82de31547f7a7865ed7772940d3801b942eeb6fbf188c38ea0",
            "staff": "912cca9b92981c4ecad63de931be882319e1c050656dcfb97cd0c2d2ab37c251",
            "fixtures": "0c06d6b3873403e99aea3325e9fe1ebc1604ebeb2fc219eb9151e5f018295e41",
            "player_match_stats": "1d0fc305e8b6edc6af4eec1bf4e15e1b666ad9baf448b7a3dd39bf9635d63648",
            "tournaments": "5dffd7c42ee855ef64c0eec6549900232ff3320690b5bbba5386ef89f5f38858",
            "tournament_entries": "a199ead68e291deb88cff77b84d0ede9e8fddb91fb843361d7da080eecd551dd",
            "cup_ties": "f37ce5921f0272d085253f3d5d98bdab7f39af1623ec73cd0e2a35dabdf8b4bb",
            "transfer_log": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "season_honours": "632cbf9052d52550c0bbc95df83c0f1df6eac6ce407af70b3e1b9e333b348462",
            "news_items": "b6e24c172d47b6fb1ce0216092263f1902f6a9c241885c2ae5f567b7f795d790",
            "shortlist": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "friendlies": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "tactic_presets": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        },
    },
}


def _primary_row_only(db) -> None:
    from models import WorldManager

    db.execute(WorldManager.__table__.delete())
    db.add(WorldManager(user_id=None, display_name="Birincil", is_primary=True))


def _no_seat_rows(db) -> None:
    from models import WorldManager

    db.execute(WorldManager.__table__.delete())


def _assert_head_parity(mode: str, runs: dict[str, dict]) -> None:
    expected = HEAD_PARITY[mode]
    for variant, out in runs.items():
        assert out["weeks"] == expected["weeks"], (mode, variant)
        assert out["reports"] == expected["reports"], f"{mode}/{variant}: hafta raporlari HEAD'den farkli"
        assert out["log"] == expected["log"], f"{mode}/{variant}: menajer islemleri / haberler HEAD'den farkli"
        if _schema_digest() == HEAD_SCHEMA_DIGEST:
            differing = [t for t, d in expected["tables"].items() if out["tables"][t] != d]
            assert not differing, f"{mode}/{variant}: tablolar HEAD'den farkli: {differing}"
    first, second = runs.values()
    assert first["tables"] == second["tables"]


@pytest.fixture(scope="module", autouse=True)
def _clean_world_after_module():
    yield
    _fresh_world()                                     # modul sonunda conftest ile ayni temiz dunya


def test_legacy_parity_career_season_rollover_matches_head():
    """
    Eski kariyer (koltuk satiri yok / yalnizca birincil satir): tam sezon (canli mac, transfer, sponsor, hazirlik maci,
    maas talepleri) + sezon devri + yeni sezon haftasi HEAD cd1516f ile bit bit ayni: skorlar, butceler, tanınırlık,
    transfer kaydi, haberler, raporlar ve tum kariyer tablolari.
    """
    runs = {"no-rows": _parity_run(_no_seat_rows), "primary-row": _parity_run(_primary_row_only)}
    _assert_head_parity("career", runs)


def test_legacy_parity_tournament_mode_matches_head():
    runs = {"no-rows": _parity_run(_no_seat_rows, tournament=True),
            "primary-row": _parity_run(_primary_row_only, tournament=True)}
    _assert_head_parity("tournament", runs)


# ===========================================================================
# 2) COK KOLTUKLU DUNYA (islem sonunda geri alinir)
# ===========================================================================

PRIMARY_TEAM = "Istanbul Lions"
SEAT_TEAM = "Kadıköy Canaries"                 # ayni lig: insan -> insan maci


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _account(db, name: str) -> int:
    import uuid

    from models import User

    user = User(username=f"{name}_{uuid.uuid4().hex[:8]}"[:32], password_hash="scrypt$test$not-a-real-hash")
    db.add(user)
    db.flush()
    return user.id


class World:
    """Birincil koltuk (PRIMARY_TEAM) + bir menajer koltugu (SEAT_TEAM); paylasilan dunya kurallari."""

    def __init__(self, db, seed: int | None = 31, seat_team: str = SEAT_TEAM, primary_team: str = PRIMARY_TEAM):
        from seats import SeatStore
        from world_rules import WorldRules

        self.db = db
        self.cm = CareerManager(db, seed=seed)
        if self.cm.season_finished or self.cm.current_week != 1:
            pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
        self.cm.state.world_rules = WorldRules.shared_defaults().to_dict()
        self.primary_team = self.cm.find_team(primary_team)
        self.cm.set_user_team(self.primary_team)
        self.store = SeatStore(db)
        self.store.ensure_primary_row(None, "Sahip")
        self.user_id = _account(db, "uye")
        seat = self.store.create_seat(self.user_id, "Üye Menajer", 8.0, self.cm.career_week)
        self.seat_team = self.cm.find_team(seat_team)
        self.seat = self.store.assign_team(seat, self.seat_team.id)
        self.seed = seed

    def member(self, **kwargs) -> CareerManager:
        return CareerManager(self.db, seed=self.seed, manager_user_id=self.user_id, **kwargs)

    def seat_row(self):
        from models import WorldManager

        return self.db.get(WorldManager, self.seat.id)


def test_human_team_ids_legacy_and_multi_seat(db):
    from models import WorldManager
    from seats import SeatStore

    cm = CareerManager(db)
    db.execute(WorldManager.__table__.delete())
    cm.state.user_team_id = None
    db.flush()
    assert cm.human_team_ids() == frozenset()
    user = cm.find_team(PRIMARY_TEAM)
    cm.set_user_team(user)
    assert cm.human_team_ids() == frozenset({user.id})
    SeatStore(db).ensure_primary_row(None, "Sahip")                       # yalnizca birincil satir: ayni
    assert cm.human_team_ids() == frozenset({user.id}) and cm.live_allowed()

    world = World(db)
    assert world.cm.human_team_ids() == frozenset({world.primary_team.id, world.seat_team.id})
    assert not world.cm.live_allowed()
    released = world.store.release(world.seat, "RELEASED", world.cm.career_week + 4)
    assert released == world.seat_team.id
    assert world.cm.human_team_ids() == frozenset({world.primary_team.id})
    assert not world.cm.live_allowed()                                  # uye dunyada kaldi: canli yine kapali


def test_acting_seat_spectator_and_set_user_team_permissions(db):
    import reputation
    import worlds
    from career_manager import FriendlyError, ShortlistError
    from seats import SeatError

    world = World(db)
    member = world.member()
    assert member.acting_seat.id == world.seat.id and not member.acting_seat.is_primary
    assert member.user_team.id == world.seat_team.id and member.manager_reputation == 8.0
    with pytest.raises(worlds.WorldPermissionError):
        member.set_user_team(world.cm.find_team("Madrid Blancos"))
    with pytest.raises(SeatError, match="başka bir menajerin"):
        world.cm.set_user_team(world.seat_team)                         # birincil koltuk da alamaz

    owner = CareerManager(db, manager_user_id=None)
    assert owner.acting_seat.is_primary and owner.user_team.id == world.primary_team.id

    spectator = CareerManager(db, manager_user_id=_account(db, "izleyici"))
    assert spectator.acting_seat is None and spectator.user_team is None
    assert spectator.manager_reputation == reputation.START_REPUTATION
    with pytest.raises(ShortlistError):
        spectator.shortlist_add(world.seat_team.players[0])
    assert spectator.shortlist() == [] and not spectator.is_shortlisted(world.seat_team.players[0].id)
    with pytest.raises(FriendlyError, match="önce yöneteceğin takımı"):
        spectator.play_friendly(world.primary_team)
    with pytest.raises(worlds.WorldPermissionError):
        spectator.set_user_team(world.cm.find_team("Madrid Blancos"))
    assert spectator.live_fixture() is None


def test_shared_world_is_fixed_to_career_mode(db):
    world = World(db)
    cm = world.cm
    cm.state.game_mode = None
    db.flush()
    with pytest.raises(ValueError, match="yalnızca kariyer modunda"):
        cm.set_game_mode(GameMode.TOURNAMENT)
    cm.set_game_mode(GameMode.CAREER)
    assert not cm.can_change_mode()
    with pytest.raises(ValueError, match="yalnızca kariyer modunda"):
        cm.reset_game_mode()


def test_week_report_clubs_reputation_and_view_for_isolation(db):
    world = World(db, seed=57)
    cm = world.cm
    cm.set_game_mode(GameMode.CAREER)
    cm.ensure_club_setup()
    cm.run_ai_transfer_window = lambda: []
    primary_before = cm.state.manager_reputation
    report = cm.play_week()

    assert report.focus_team_id == world.primary_team.id
    club = report.clubs[world.seat_team.id]
    assert report.user_result is not None and world.primary_team.id in (report.user_result.home.id,
                                                                         report.user_result.away.id)
    assert club.user_result is not None and world.seat_team.id in (club.user_result.home.id, club.user_result.away.id)
    # tanınırlık: birincil GameState'te, koltuk kendi satirinda
    assert report.manager_reputation[0] == primary_before
    assert report.manager_reputation[1] == cm.state.manager_reputation
    assert club.manager_reputation[0] == 8.0 and club.manager_reputation[1] == world.seat_row().reputation
    assert "Maaşlar ödendi" in report.finance_note and "Maaşlar ödendi" in club.finance_note
    assert report.finance_note != club.finance_note

    seat_view = report.view_for(world.seat_team.id)
    assert seat_view.user_result is club.user_result and seat_view.finance_note == club.finance_note
    assert seat_view.manager_reputation == club.manager_reputation and seat_view.clubs == {}
    assert seat_view.results == report.results                           # dunya alanlari aynen
    assert report.view_for(world.primary_team.id).finance_note == report.finance_note
    ai_view = report.view_for(cm.find_team("Madrid Blancos").id)
    assert ai_view.user_result is None and ai_view.finance_note is None and ai_view.manager_reputation is None
    lines = [text for _kind, text in week_report_lines(seat_view)]
    assert club.finance_note in lines and report.finance_note not in lines

    member_view = world.member()                                         # uyenin tanınırlığı koltuktan
    assert member_view.manager_reputation == world.seat_row().reputation


def test_every_human_club_is_manager_controlled_with_stored_tactics(db):
    from instructions import Mentality, TeamInstructions
    from models import Competition, Fixture

    world = World(db)
    cm = world.cm
    stored = TeamInstructions(mentality=Mentality.ALL_OUT_ATTACK)
    cm.set_team_instructions(world.seat_team, stored)
    derby = db.scalar(select(Fixture).where(
        Fixture.season == cm.season, Fixture.competition == Competition.LEAGUE,
        Fixture.home_team_id.in_([world.primary_team.id, world.seat_team.id]),
        Fixture.away_team_id.in_([world.primary_team.id, world.seat_team.id])).order_by(Fixture.week))
    engine = cm._prepare_career_fixture(derby, derby.week)
    assert engine.home.manager_controlled and engine.away.manager_controlled
    seat_side = engine.home if engine.home.id == world.seat_team.id else engine.away
    assert seat_side.instructions == stored

    other = db.scalar(select(Fixture).where(
        Fixture.season == cm.season, Fixture.competition == Competition.LEAGUE,
        (Fixture.home_team_id == world.seat_team.id) | (Fixture.away_team_id == world.seat_team.id),
        Fixture.home_team_id != world.primary_team.id, Fixture.away_team_id != world.primary_team.id,
    ).order_by(Fixture.week))
    engine = cm._prepare_career_fixture(other, other.week)
    seat_side, ai_side = ((engine.home, engine.away) if engine.home.id == world.seat_team.id
                          else (engine.away, engine.home))
    assert seat_side.manager_controlled and seat_side.instructions == stored and not ai_side.manager_controlled


def test_match_seed_for_human_clubs_without_career_seed(db):
    import zlib

    from models import Competition, Fixture

    world = World(db, seed=None)
    cm = world.cm
    ids = [world.primary_team.id, world.seat_team.id]
    derby = db.scalar(select(Fixture).where(
        Fixture.season == cm.season, Fixture.competition == Competition.LEAGUE,
        Fixture.home_team_id.in_(ids), Fixture.away_team_id.in_(ids)))
    expected = zlib.crc32(f"{cm.season}|{derby.id}|{derby.home_team_id}".encode())
    assert cm.match_seed(derby) == expected == world.member().match_seed(derby) == CareerManager(db).match_seed(derby)
    seat_only = db.scalar(select(Fixture).where(
        Fixture.season == cm.season, Fixture.competition == Competition.LEAGUE,
        (Fixture.home_team_id == world.seat_team.id) | (Fixture.away_team_id == world.seat_team.id),
        Fixture.home_team_id != world.primary_team.id, Fixture.away_team_id != world.primary_team.id))
    assert cm.match_seed(seat_only) == zlib.crc32(f"{cm.season}|{seat_only.id}|{world.seat_team.id}".encode())
    ai_only = db.scalar(select(Fixture).where(
        Fixture.season == cm.season, Fixture.competition == Competition.LEAGUE,
        Fixture.home_team_id.notin_(ids), Fixture.away_team_id.notin_(ids)))
    assert cm.match_seed(ai_only) is None


def test_live_match_refused_with_two_seats(db):
    from career_manager import LIVE_SHARED_REFUSAL
    from models import Fixture, FixtureStatus

    world = World(db, seed=5)
    cm = world.cm
    cm.set_game_mode(GameMode.CAREER)
    for manager in (cm, world.member()):
        with pytest.raises(LiveMatchError, match="birlikte oynanır"):
            manager.prepare_live_match()
    fx = cm.fixtures_for_week(1)[0]
    result = cm._prepare_career_fixture(fx, 1).simulate()
    with pytest.raises(LiveMatchError) as exc:
        cm.play_week({fx.id: result})
    assert str(exc.value) == LIVE_SHARED_REFUSAL
    assert db.get(Fixture, fx.id).status is FixtureStatus.UNPLAYED and cm.current_week == 1


def test_wage_demands_concerns_finance_and_youth_notes_for_all_human_clubs(db):
    from career_manager import WeekReport

    world = World(db, seed=11)
    cm = world.cm
    cm.set_game_mode(GameMode.CAREER)
    ai_team = cm.find_team("Madrid Blancos")
    for team in (world.primary_team, world.seat_team, ai_team):
        for p in list(team.players)[:3]:
            p.contract_overall = max(1, p.overall_rating - 20)
    db.flush()
    report = WeekReport(cm.season, 1)
    cm._weekly_concerns(1, report)
    club = report.clubs[world.seat_team.id]
    assert report.wage_demands and club.wage_demands
    assert {n.team_name for n in report.wage_demands} == {world.primary_team.name}
    assert {n.team_name for n in club.wage_demands} == {world.seat_team.name}
    for team, notes in ((world.primary_team, report.wage_demands), (world.seat_team, club.wage_demands)):
        pending = {p.id for p in team.players if p.wage_demand is not None}     # insan kulubunde talep bekler
        assert pending and {n.player_id for n in notes} <= pending
    assert all(p.wage_demand is None for p in ai_team.players)                 # AI hemen karar verdi

    member = world.member()
    world.seat_team.wage_budget += 5_000_000
    db.flush()
    player = next(p for p in world.seat_team.players if p.wage_demand is not None)
    assert "yeni sözleşmeyi imzaladı" in member.respond_wage_demand(player, True)
    primary_player = next(p for p in world.primary_team.players if p.wage_demand is not None)
    with pytest.raises(ConcernError, match="senin oyuncun değil"):
        member.respond_wage_demand(primary_player, True)

    cm.ensure_club_setup()
    wages = WeekReport(cm.season, 1)
    cm._pay_weekly_wages(wages, 1)
    assert "Maaşlar ödendi" in wages.finance_note and "Maaşlar ödendi" in wages.clubs[world.seat_team.id].finance_note
    assert set(wages.clubs) == {world.seat_team.id}

    youth = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), youth)
    assert youth.youth_intake and youth.clubs[world.seat_team.id].youth_intake
    assert {n.team_name for n in youth.clubs[world.seat_team.id].youth_intake} == {world.seat_team.name}


def test_ai_window_never_trades_with_human_or_protected_clubs(db, monkeypatch):
    import career_manager as cmod
    from models import TransferLog

    world = World(db, seed=77)
    cm = world.cm
    cm.set_game_mode(GameMode.CAREER)
    protected = cm.find_team("Madrid Blancos")
    protected.ai_protected_until = cm.career_week + 30
    for team in cm.teams():
        team.transfer_budget = max(team.transfer_budget, 400_000_000)
    db.flush()
    monkeypatch.setattr(cmod, "AI_TRANSFER_CHANCE", 1.01)
    monkeypatch.setattr(cmod, "AI_MAX_DEALS_PER_WEEK", 12)
    blocked = {world.primary_team.id, world.seat_team.id, protected.id}
    deals = []
    for week in range(1, 11):
        cm.state.current_week = week
        db.flush()
        deals += cm.run_ai_transfer_window()
    assert deals, "senaryo hiç AI transferi üretmedi"
    assert all(d.from_team_id not in blocked and d.to_team_id not in blocked for d in deals)
    logged = db.scalars(select(TransferLog)).all()
    assert all(r.from_team_id not in blocked and r.to_team_id not in blocked for r in logged)
    assert "yönetim koruması" in cm.transfer_block_reason(protected.players[0])


def test_human_buyer_cannot_use_ai_flow_on_human_owned_player(db):
    from transfers import ContractOffer, TransferError

    world = World(db)
    cm, member = world.cm, world.member()
    seat_player = world.seat_team.players[0]
    primary_player = world.primary_team.players[0]
    for team in (world.primary_team, world.seat_team):
        team.transfer_budget, team.wage_budget = 500_000_000, team.wage_budget + 5_000_000
    db.flush()
    with pytest.raises(TransferError, match="Teklifler panelinden"):
        cm.offer_fee(world.primary_team, seat_player, 1_000_000)
    with pytest.raises(TransferError, match="Teklifler panelinden"):
        cm.open_negotiation(world.primary_team, seat_player, 1_000_000)
    with pytest.raises(TransferError, match="Teklifler panelinden"):
        member.offer_fee(world.seat_team, primary_player, 1_000_000)
    offer = ContractOffer(wage=1_000, years=2, role=seat_player.squad_role)
    with pytest.raises(TransferError, match="Teklifler panelinden"):
        cm.complete_transfer(world.primary_team, seat_player, 1_000_000, offer)
    with pytest.raises(TransferError, match="artık bu kulübün oyuncusu değil"):
        cm.complete_transfer(world.primary_team, seat_player, 1_000_000, offer,
                             expected_seller_id=cm.find_team("Madrid Blancos").id, human_deal=True)
    ai_player = cm.find_team("Madrid Blancos").players[5]
    assert member.offer_fee(world.seat_team, ai_player, 1_000).asking > 0        # AI kulubu: eski akis
    news = cm.complete_transfer(world.primary_team, seat_player, 1_000_000, offer,
                                expected_seller_id=world.seat_team.id, human_deal=True)
    assert news.to_team_id == world.primary_team.id and seat_player.team_id == world.primary_team.id


def test_shortlists_are_per_seat_and_purchase_clears_only_buyer_list(db):
    from transfers import ContractOffer

    world = World(db)
    cm, member = world.cm, world.member()
    madrid = cm.find_team("Madrid Blancos")
    x, y = madrid.players[3], madrid.players[4]
    cm.shortlist_add(x, "birincil")
    member.shortlist_add(y, "uye")
    member.shortlist_add(x)
    assert [r.player_id for r in cm.shortlist()] == [x.id]
    assert [r.player_id for r in member.shortlist()] == sorted([y.id, x.id])       # ayni hafta: oyuncu id sirasi
    assert member.is_shortlisted(y.id) and not cm.is_shortlisted(y.id)
    assert member.shortlist_remove(x.id) and cm.is_shortlisted(x.id)

    world.seat_team.transfer_budget, world.seat_team.wage_budget = 500_000_000, world.seat_team.wage_budget + 5_000_000
    db.flush()
    member.complete_transfer(world.seat_team, y, 1_000, ContractOffer(wage=1_000, years=2, role=y.squad_role))
    assert not member.is_shortlisted(y.id) and [r.player_id for r in cm.shortlist()] == [x.id]
    member.shortlist_add(x)
    world.primary_team.transfer_budget = 500_000_000
    world.primary_team.wage_budget += 5_000_000
    db.flush()
    cm.complete_transfer(world.primary_team, x, 1_000, ContractOffer(wage=1_000, years=2, role=x.squad_role))
    assert not cm.is_shortlisted(x.id) and member.is_shortlisted(x.id)


def test_two_seats_play_friendlies_in_the_same_week(db):
    from career_manager import FriendlyError

    world = World(db, seed=9)
    cm, member = world.cm, world.member()
    cm.set_game_mode(GameMode.CAREER)
    madrid, london = cm.find_team("Madrid Blancos"), cm.find_team("London Gunners")
    first = cm.play_friendly(madrid)
    with pytest.raises(FriendlyError, match="bu hafta zaten bir hazırlık maçı oynadı"):
        member.play_friendly(madrid)                                     # rakip bu hafta oynadi
    second = member.play_friendly(london)
    assert (first.home_team_id, second.home_team_id) == (world.primary_team.id, world.seat_team.id)
    with pytest.raises(FriendlyError, match="Bu hafta zaten hazırlık maçı oynadın"):
        member.play_friendly(cm.find_team("Manchester Blue"))
    assert [f.id for f in cm.friendlies(team_id=world.seat_team.id)] == [second.friendly_id]
    assert second.match.home.manager_controlled


def test_season_archive_standings_prizes_and_new_season_notes_for_all_humans(db):
    from sqlalchemy import update

    from models import Fixture, FixtureStatus, SeasonStanding, TournamentStatus

    world = World(db, seed=13)
    cm = world.cm
    cm.set_game_mode(GameMode.CAREER)
    cm.ensure_club_setup()
    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    db.expire_all()
    world.seat_team.points = 99                                           # koltugun kulubu sampiyon
    cm.tournaments.ensure().status = TournamentStatus.FINISHED
    db.flush()
    assert cm.season_finished

    new_season = cm.start_new_season()
    assert new_season == 2
    rows = db.scalars(select(SeasonStanding).where(SeasonStanding.season == 1)).all()
    assert len(rows) == len(cm.teams())
    for league in cm.leagues():
        assert sorted(r.position for r in rows if r.league_id == league.id) == [1, 2, 3, 4]
    champion = next(r for r in rows if r.team_id == world.seat_team.id)
    assert (champion.position, champion.points) == (1, 99)

    by_team = cm.new_season_notes_by_team
    assert cm.new_season_notes == by_team[world.primary_team.id]
    seat_notes = by_team[world.seat_team.id]
    assert seat_notes and any("Lig ödülü" in n for n in seat_notes)
    assert any("şampiyonu oldun" in n for n in seat_notes)
    assert any("sponsor teklifi" in n for n in seat_notes)
    for team in (world.primary_team, world.seat_team):
        assert team.sponsor_offers                                        # insan kulubu teklifleri bekler
    assert all(not t.sponsor_offers for t in cm.teams() if t.id not in cm.human_team_ids())


def test_cup_results_and_round_reputation_per_seat(db, monkeypatch):
    import reputation

    world_cm = CareerManager(db, seed=21)
    if world_cm.season_finished or world_cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil")
    world_cm.set_game_mode(GameMode.CAREER)
    participants = world_cm.tournaments.participants(world_cm.tournaments.ensure())
    world = World(db, seed=21, primary_team=participants[0].name, seat_team=participants[1].name)
    cm = world.cm
    cm.run_ai_transfer_window = lambda: []
    monkeypatch.setattr(reputation, "match_delta", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(reputation, "season_delta", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(reputation, "cup_round_delta", lambda stage, won: 1.0 if won else -0.5)
    primary_before = cm.state.manager_reputation

    first = cm.play_week()
    assert first.user_cup_result is not None and first.clubs[world.seat_team.id].user_cup_result is not None
    second = cm.play_week()
    ties = cm.tournaments.ties(cm.tournaments.current())
    outcomes = {}
    for team in (world.primary_team, world.seat_team):
        tie = next(t for t in ties if team.id in (t.first_team_id, t.second_team_id))
        assert tie.decided
        outcomes[team.id] = 1.0 if tie.winner_team_id == team.id else -0.5
    assert cm.state.manager_reputation == reputation.apply(primary_before, outcomes[world.primary_team.id])
    assert world.seat_row().reputation == reputation.apply(8.0, outcomes[world.seat_team.id])
    seat_rep = second.clubs[world.seat_team.id].manager_reputation
    assert seat_rep == (8.0, world.seat_row().reputation)


class _RecorderExtension:
    def __init__(self, calls: list, blocker: dict, blocked_player: dict):
        self.calls, self.blocker, self.blocked_player = calls, blocker, blocked_player

    def on_week(self, week, report):
        self.calls.append(("on_week", week, type(report).__name__))

    def on_season_end(self):
        self.calls.append(("on_season_end",))

    def on_season_start(self, new_season):
        self.calls.append(("on_season_start", new_season))

    def new_season_blocker(self):
        return self.blocker.get("reason")

    def on_player_moved(self, player, seller_id, buyer_id):
        self.calls.append(("on_player_moved", player.id, seller_id, buyer_id))

    def on_club_released(self, team_id):
        self.calls.append(("on_club_released", team_id))

    def transfer_block_reason(self, player):
        return "Kiralık listesinde" if player.id == self.blocked_player.get("id") else None


def test_extension_hooks_are_called_in_order(db, monkeypatch):
    from sqlalchemy import update

    import extensions
    from career_manager import SeasonNotFinished
    from models import Fixture, FixtureStatus, TournamentStatus
    from transfers import ContractOffer, TransferError

    calls: list = []
    blocker: dict = {}
    blocked_player: dict = {}
    monkeypatch.setattr(extensions, "load", lambda cm: [_RecorderExtension(calls, blocker, blocked_player)])
    world = World(db, seed=3)
    cm = world.cm
    cm.set_game_mode(GameMode.CAREER)
    cm.run_ai_transfer_window = lambda: calls.append(("ai_window",)) or []
    cm.play_week()
    assert calls[-2:] == [("ai_window",), ("on_week", 1, "WeekReport")]

    madrid = cm.find_team("Madrid Blancos")
    target = madrid.players[2]
    blocked_player["id"] = target.id
    with pytest.raises(TransferError, match="Kiralık listesinde"):
        cm.offer_fee(world.primary_team, target, 1_000)
    blocked_player.clear()
    world.primary_team.transfer_budget = 500_000_000
    world.primary_team.wage_budget += 5_000_000
    db.flush()
    cm.complete_transfer(world.primary_team, target, 1_000, ContractOffer(wage=1_000, years=2, role=target.squad_role))
    assert calls[-1] == ("on_player_moved", target.id, madrid.id, world.primary_team.id)
    cm.run_extensions("on_club_released", world.seat_team.id)
    assert calls[-1] == ("on_club_released", world.seat_team.id)

    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=1, away_score=0))
    db.flush()
    db.expire_all()
    cm.tournaments.ensure().status = TournamentStatus.FINISHED
    db.flush()
    blocker["reason"] = "Dünya Kupası sürüyor."
    with pytest.raises(SeasonNotFinished, match="Dünya Kupası sürüyor"):
        cm.start_new_season()
    assert cm.season == 1
    blocker.clear()
    del calls[:]
    assert cm.start_new_season() == 2
    assert calls[0] == ("on_season_end",) and calls[-1] == ("on_season_start", 2)


def test_ensure_world_setup_creates_primary_row_once(db):
    from models import GameState, WorldManager

    cm = CareerManager(db)
    db.execute(WorldManager.__table__.delete())
    owner = _account(db, "sahip")
    db.get(GameState, 1).user_id = owner
    db.flush()
    assert cm.ensure_world_setup() == ["menajer koltuğu kaydedildi"]
    rows = db.scalars(select(WorldManager)).all()
    assert len(rows) == 1 and rows[0].is_primary and rows[0].user_id == owner
    assert rows[0].display_name.startswith("sahip_") and rows[0].team_id is None and rows[0].reputation is None
    assert cm.ensure_world_setup() == []
    assert CareerManager(db, manager_user_id=owner).acting_seat.is_primary


def test_lock_rows_flushes_pending_changes_and_locks_in_id_order(db):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    import database
    from models import Team

    cm = CareerManager(db)
    a, b = cm.find_team("Madrid Blancos"), cm.find_team("London Gunners")
    a.transfer_budget = 123_456
    rows = cm.lock_rows(Team, [b.id, a.id, a.id])
    assert [t.id for t in rows] == sorted({a.id, b.id}) and a.transfer_budget == 123_456
    assert cm.lock_rows(Team, []) == []
    with database.engine.connect() as other:
        with pytest.raises(OperationalError):
            other.execute(text("SELECT id FROM teams WHERE id = :id FOR UPDATE NOWAIT"), {"id": b.id})
        other.rollback()


def test_shared_world_helper_members_resolve_their_seat():
    """tests/world_helpers.make_shared_public ile kurulan (commit edilmis) dunyada koltuk cozumlemesi."""
    import worlds
    from database import session_scope
    from tests.world_helpers import cleanup_shared, make_shared_public

    world = None
    try:
        world = make_shared_public("A2Sahip", [("A2Uye", "Madrid Blancos"), "A2Kulupsuz"], owner_team=PRIMARY_TEAM)
        with session_scope() as session:
            owner = CareerManager(session, manager_user_id=world.owner_id)
            member = CareerManager(session, manager_user_id=world.user_ids["A2Uye"])
            clubless = CareerManager(session, manager_user_id=world.user_ids["A2Kulupsuz"])
            assert owner.acting_seat.is_primary and owner.user_team.name == PRIMARY_TEAM
            assert member.acting_seat.id == world.seat_ids["A2Uye"] and member.user_team.name == "Madrid Blancos"
            assert clubless.acting_seat is not None and clubless.user_team is None
            assert owner.human_team_ids() == {owner.user_team.id, member.user_team.id}
            assert not owner.live_allowed() and owner.rules.shared
            with pytest.raises(worlds.WorldPermissionError):
                member.set_user_team(owner.find_team("London Gunners"))
            assert owner.ensure_world_setup() == []
    finally:
        cleanup_shared(world)
