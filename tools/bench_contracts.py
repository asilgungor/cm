"""
tools/bench_contracts.py -- Faz 15A sozlesme dongusu kabul kosusu (gelistirici araci; oyun calisma zamaninda kullanilmaz).

YALNIZCA fm_db_test_15a* veritabanlarinda calisir (guvenlik blogu: database import'undan ONCE; tools/bench_week.py ile
ayni). Dunya sifirdan kurulur (reset_db + seed; varsayilan acik veri dunyasi, 114 kulup), web girisi gibi altyapi /
kulup / dunya kurulumu, kariyer modu, cm.teams()[0] menajerin kulubu. N sezon oynanir (her hafta ayri session_scope);
her sezon sonunda start_new_season. Menajer politikasi (--manager):
    active   ContractDesk API'siyle: 2. haftada sozlesmesi biten oyunculari yeniler (oyuncunun talebi, gerekirse butce
             kaydirma), on sozlesme doneminin ilk haftasinda bir on sozlesme dener, sezon basinda A takim 20'nin
             altindaysa serbest oyuncu imzalar
    passive  hicbir sey yapmaz (bosveren menajer: kadro guvencesi ve akademi yukseltmesi calisir)
Olculenler: sezon basina serbest kalan (RELEASED), on sozlesme (BOSMAN), havuzdan imza orani (serbest kalan oyuncunun
sonradan FREE_AGENT kaydi), AI yenileme kararlari; her hafta sonunda A takim en az / en cok, en az kaleci, en dusuk
kasa; play_week ve devir sureleri.

    python tools/bench_contracts.py --db fm_db_test_15a_bench --seasons 3 --out .claude/phase14/kanit/15A/kabul.json
    python tools/bench_contracts.py --db fm_db_test_15a_bench --drop
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PREFIX = "fm_db_test_15a"


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="fm_db_test_15a_bench")
    ap.add_argument("--source", default="open", choices=("open", "synthetic"))
    ap.add_argument("--seasons", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--career-seed", type=int, default=7)
    ap.add_argument("--manager", default="active", choices=("active", "passive"))
    ap.add_argument("--flag", default="on", choices=("on", "off"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--drop", action="store_true")
    return ap.parse_args()


def _admin(url: str, name: str, drop: bool) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    if not name.startswith(PREFIX):
        raise SystemExit(f"Guvenlik: yalnizca {PREFIX}* veritabanlari.")
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if drop:
                conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"),
                             {"n": name})
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                print(f"[kabul] {name} silindi")
            elif not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}):
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def main() -> int:  # noqa: C901 - tek akisli gelistirici araci
    args = _parse()
    # ---- guvenlik blogu (database import'undan ONCE) ----
    target = args.db
    if not target.startswith(PREFIX):
        raise SystemExit(f"Guvenlik: yalnizca {PREFIX}* veritabanlari.")
    from dotenv import load_dotenv

    from tests.db_urls import build_test_url
    load_dotenv(ROOT / ".env")
    url, main_db = build_test_url(os.environ["DATABASE_URL"], target)
    if not main_db or main_db == target or target == "fm_db":
        raise SystemExit("Guvenlik: hedef DB oyun DB'siyle ayni.")
    os.environ["DATABASE_URL"] = url
    os.environ["SEED_NAME_MASKING"] = "light"
    os.environ.pop("OFM_ALLOW_REAL_NAMES", None)
    os.environ["OFM_NEW_WORLD_SOURCE"] = "synthetic"
    if args.drop:
        _admin(url, target, drop=True)
        return 0
    _admin(url, target, drop=False)
    import database  # ANCAK simdi
    assert database.engine.url.database == target
    with database.engine.connect() as c:
        assert c.scalar(text("SELECT current_database()")) == target
    # ---- guvenlik blogu sonu ----

    import contracts
    import seed
    from career_manager import CareerManager
    from models import GameMode

    contracts.CONTRACT_CYCLE = args.flag == "on"
    database.reset_db()
    seed.seed(rng_seed=args.seed, source=args.source)
    with database.session_scope() as db:
        cm = CareerManager(db, seed=args.career_seed)
        cm.ensure_youth_setup()
        cm.ensure_club_setup()
        cm.ensure_world_setup()
        cm.set_game_mode(GameMode.CAREER)
        cm.set_user_team(cm.teams()[0])
        user = cm.state.user_team.name
        user_id = cm.state.user_team_id
        world = {"teams": len(cm.teams()), "players": db.scalar(text("SELECT count(*) FROM players"))}
    print(f"[kabul] dunya {world}, menajer kulubu {user}, bayrak {args.flag}, menajer {args.manager}", flush=True)

    weekly: list[dict] = []
    rollovers: list[dict] = []
    actions: list[str] = []
    for _season in range(args.seasons):
        for _ in range(80):
            with database.session_scope() as db:
                cm = CareerManager(db, seed=args.career_seed)
                if cm.season_finished:
                    break
                season, week = cm.season, cm.current_week
                if args.manager == "active" and args.flag == "on":
                    actions += _manager_week(cm, season, week, user_id)
            t0 = time.perf_counter()
            with database.session_scope() as db:
                report = CareerManager(db, seed=args.career_seed).play_week()
            elapsed = time.perf_counter() - t0
            row = {"season": season, "week": week, "sure_sn": round(elapsed, 3), "ai_transfers": len(report.transfers)}
            row.update(_world_metrics(database, user_id))
            weekly.append(row)
            if week % 10 == 0:
                print(f"[kabul] s{season} h{week:>2}: {elapsed:.2f} sn · kadro {row['min_squad']}-{row['max_squad']} "
                      f"· kaleci >= {row['min_keepers']} · havuz {row['free_agents']}", flush=True)
        t0 = time.perf_counter()
        with database.session_scope() as db:
            cm = CareerManager(db, seed=args.career_seed)
            new_season = cm.start_new_season()
            notes = list(cm.new_season_notes)
        elapsed = time.perf_counter() - t0
        metrics = _world_metrics(database, user_id)
        metrics.update(_season_counts(database, new_season))
        rollovers.append({"new_season": new_season, "sure_sn": round(elapsed, 3), **metrics,
                          "user_notes": [n for n in notes if "Ön sözleşme" in n or "serbest" in n or "güvence" in n
                                         or "akademiden" in n][:12]})
        print(f"[kabul] devir -> {new_season}: {elapsed:.2f} sn · {metrics}", flush=True)
        if args.manager == "active" and args.flag == "on":
            with database.session_scope() as db:
                cm = CareerManager(db, seed=args.career_seed)
                actions += _manager_preseason(cm, user_id)

    cohorts = _cohorts(database)
    times = [w["sure_sn"] for w in weekly]
    summary = {
        "weeks": len(weekly), "play_week_median_sn": round(statistics.median(times), 3), "play_week_worst_sn": max(times),
        "rollover_sn": [r["sure_sn"] for r in rollovers],
        "released_per_season": [r["released"] for r in rollovers],
        "bosman_per_season": [r["bosman"] for r in rollovers],
        "cohorts": cohorts,
        "min_squad": min(w["min_squad"] for w in weekly + rollovers),
        "max_squad": max(w["max_squad"] for w in weekly + rollovers),
        "min_squad_ai": min(w["min_squad_ai"] for w in weekly + rollovers),
        "max_squad_ai": max(w["max_squad_ai"] for w in weekly + rollovers),
        "min_keepers": min(w["min_keepers"] for w in weekly + rollovers),
        "min_budget": min(w["min_budget"] for w in weekly + rollovers),
        "user_min_squad": min(w["user_squad"] for w in weekly + rollovers),
    }
    print("[kabul] OZET " + json.dumps(summary, ensure_ascii=False))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"meta": {"db": target, "source": args.source, "seed": args.seed,
                                            "career_seed": args.career_seed, "seasons": args.seasons,
                                            "manager": args.manager, "flag": args.flag, "user_team": user,
                                            "world": world, "when": time.strftime("%Y-%m-%d %H:%M:%S")},
                                   "summary": summary, "rollovers": rollovers, "weekly": weekly,
                                   "manager_actions": actions}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[kabul] yazildi: {out}")
    return 0


def _world_metrics(database, user_id: int | None) -> dict:
    with database.engine.connect() as c:
        rows = c.execute(text(
            "SELECT t.id, count(p.id) FILTER (WHERE p.id IS NOT NULL AND NOT p.in_academy), "
            "count(p.id) FILTER (WHERE p.position = 'GK' AND NOT p.in_academy), min(t.transfer_budget) "
            "FROM teams t LEFT JOIN players p ON p.team_id = t.id GROUP BY t.id")).all()
        free = c.scalar(text("SELECT count(*) FROM players WHERE team_id IS NULL"))
    squads = {r[0]: int(r[1]) for r in rows}
    ai = [n for tid, n in squads.items() if tid != user_id]
    return {"min_squad": min(squads.values()), "max_squad": max(squads.values()), "min_squad_ai": min(ai),
            "max_squad_ai": max(ai), "avg_squad": round(sum(squads.values()) / len(squads), 2),
            "min_keepers": min(int(r[2]) for r in rows), "min_budget": min(int(r[3]) for r in rows),
            "free_agents": int(free), "user_squad": squads.get(user_id, 0)}


def _season_counts(database, new_season: int) -> dict:
    with database.engine.connect() as c:
        kinds = dict(c.execute(text("SELECT kind, count(*) FROM transfer_log WHERE season = :s AND week = 1 AND "
                                    "kind IN ('RELEASED', 'BOSMAN') GROUP BY kind"), {"s": new_season}).all())
        fa = c.scalar(text("SELECT count(*) FROM transfer_log WHERE season = :s AND week = 1 AND kind = 'FREE_AGENT'"),
                      {"s": new_season})
        talks = dict(c.execute(text("SELECT kind || ':' || status, count(*) FROM contract_talks WHERE season = :s "
                                    "GROUP BY 1"), {"s": new_season - 1}).all())
        expiring_ai = talks.get("RENEWAL:SIGNED", 0) + talks.get("RENEWAL:DECLINED", 0) + talks.get("RENEWAL:REFUSED", 0)
    return {"released": int(kinds.get("RELEASED", 0)), "bosman": int(kinds.get("BOSMAN", 0)),
            "preseason_free_agent_signings": int(fa or 0), "talks_prev_season": talks,
            "ai_renewal_rate": round(talks.get("RENEWAL:SIGNED", 0) / expiring_ai, 3) if expiring_ai else None}


def _cohorts(database) -> list[dict]:
    """Serbest kalan oyuncu (RELEASED / TERMINATED) sonradan FREE_AGENT kaydiyla imzaladi mi? (sezon kohortu)."""
    with database.engine.connect() as c:
        rows = c.execute(text(
            "SELECT r.season, count(*) AS released, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM transfer_log s "
            "WHERE s.player_id = r.player_id AND s.kind = 'FREE_AGENT' AND s.id > r.id)) AS signed, "
            "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM transfer_log s WHERE s.player_id = r.player_id AND "
            "s.kind = 'FREE_AGENT' AND s.id > r.id AND s.season = r.season)) AS signed_same_season "
            "FROM transfer_log r WHERE r.kind IN ('RELEASED', 'TERMINATED') GROUP BY r.season ORDER BY r.season")).all()
    return [{"season": int(s), "released": int(n), "signed": int(k), "signed_same_season": int(ks),
             "rate": round(k / n, 3) if n else None} for s, n, k, ks in rows]


def _manager_week(cm, season: int, week: int, user_id: int) -> list[str]:
    """Aktif menajer: 2. haftada yenilemeler, on sozlesme doneminin ilk haftasinda bir on sozlesme denemesi."""
    import contracts
    from transfer_desk import ContractDesk, DeskError
    from transfers import NegotiationStatus

    desk = ContractDesk(cm)
    done: list[str] = []
    if week == 2:
        for row in desk.contracts(expiring_only=True):
            if row.in_academy or not row.can_renew or row.status != contracts.ROW_EXPIRING:
                continue
            try:
                step = desk.open_renewal(row.player_id)
                if step.status is NegotiationStatus.OPEN and step.demand is not None:
                    step = desk.submit(step.talk_id, step.demand, shift_wage_room=True)
                done.append(f"s{season} h{week} yenileme {row.name}: {step.talk_status} ({step.message[:60]})")
            except DeskError as exc:
                done.append(f"s{season} h{week} yenileme {row.name}: HATA {exc}")
    window = desk.contract_window()
    if week == window.opens_week:
        for row in desk.pre_contract_targets(limit=40):
            if not row.can_approach:
                continue
            try:
                step = desk.open_pre_contract(row.player_id)
                if step.status is NegotiationStatus.OPEN and step.demand is not None:
                    step = desk.submit(step.talk_id, step.demand)
                done.append(f"s{season} h{week} ön sözleşme {row.name} ({row.team}): {step.talk_status}")
                if step.talk_status == contracts.AGREED:
                    break
            except DeskError as exc:
                done.append(f"s{season} h{week} ön sözleşme {row.name}: HATA {exc}")
    return done


def _manager_preseason(cm, user_id: int) -> list[str]:
    """Aktif menajer: sezon basinda A takim 20'nin altindaysa serbest oyuncu imzalar (en degerliden)."""
    import contracts
    from models import Team
    from transfer_desk import ContractDesk, DeskError
    from transfers import NegotiationStatus

    desk = ContractDesk(cm)
    team = cm.db.get(Team, user_id)
    done: list[str] = []
    tries = 0
    for row in desk.free_agents(limit=40):
        if len(team.players) >= contracts.SQUAD_MIN or tries >= 8:
            break
        tries += 1
        try:
            step = desk.open_free_agent(row.player_id)
            if step.status is NegotiationStatus.OPEN and step.demand is not None:
                step = desk.submit(step.talk_id, step.demand, shift_wage_room=True)
            done.append(f"s{cm.season} serbest {row.name}: {step.talk_status}")
            cm.db.expire(team, ["players"])
        except DeskError as exc:
            done.append(f"s{cm.season} serbest {row.name}: HATA {exc}")
    return done


if __name__ == "__main__":
    raise SystemExit(main())
