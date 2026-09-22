"""
tools/bench_retirement.py -- Faz 15B emeklilik + yeni jenerasyon kabul kosusu (gelistirici araci; oyun calisma
zamaninda kullanilmaz).

YALNIZCA fm_db_test_15b* veritabanlarinda calisir (guvenlik blogu: database import'undan ONCE; tools/bench_week.py
ve tools/bench_contracts.py ile ayni). Dunya sifirdan kurulur (reset_db + seed; varsayilan acik veri dunyasi,
114 kulup), web girisindeki gibi altyapi / kulup / dunya kurulumu, kariyer modu, cm.teams()[0] menajerin kulubu
(menajer hicbir sey yapmaz: dunya kendi kendine yasar). N sezon oynanir, her sezon sonunda start_new_season.

Sezon basina olculenler (devirden SONRA, yeni sezonun 1. haftasinda):
    nufus (toplam / A takim / akademi / kulupsuz), ortalama yas, ortalama guc, ortalama 6 motor ozelligi,
    yas dagilimi, emekli sayisi (transfer_log RETIRED), genc girisi sayisi, en kucuk / en buyuk A takim,
    en az kaleci, biten sezonun gol/mac degeri (lig ve kupa ayri), play_week medyani ve sezonun duvar saati.

    python tools/bench_retirement.py --db fm_db_test_15b_bench --seasons 20 --out .claude/phase14/kanit/15B/kabul.json
    python tools/bench_retirement.py --db fm_db_test_15b_bench --flag off --seasons 20 --out .../taban.json
    python tools/bench_retirement.py --db fm_db_test_15b_bench --drop
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
PREFIX = "fm_db_test_15b"
ATTRS = ("pace", "shooting", "passing", "defending", "dribbling", "goalkeeping")


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="fm_db_test_15b_bench")
    ap.add_argument("--source", default="open", choices=("open", "synthetic"))
    ap.add_argument("--seasons", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--career-seed", type=int, default=7)
    ap.add_argument("--flag", default="on", choices=("on", "off"), help="development.RETIREMENT")
    ap.add_argument("--contract-cycle", default="on", choices=("on", "off"))
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


def main() -> int:
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
    import development
    import seed
    from career_manager import CareerManager
    from models import GameMode

    development.RETIREMENT = args.flag == "on"
    contracts.CONTRACT_CYCLE = args.contract_cycle == "on"
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
        world = _world(database, season=1)
    print(f"[kabul] dunya {world['players']} oyuncu / {world['clubs']} kulup, menajer {user}, "
          f"emeklilik {args.flag}, sozlesme {args.contract_cycle}", flush=True)
    base = dict(world)

    seasons: list[dict] = []
    for _season in range(args.seasons):
        weeks: list[float] = []
        intake_total = 0
        t_season = time.perf_counter()
        for _ in range(80):
            with database.session_scope() as db:
                if CareerManager(db, seed=args.career_seed).season_finished:
                    break
            t0 = time.perf_counter()
            with database.session_scope() as db:
                report = CareerManager(db, seed=args.career_seed).play_week()
            weeks.append(time.perf_counter() - t0)
            intake_total += int(report.youth_intake_total or 0)
        t0 = time.perf_counter()
        with database.session_scope() as db:
            new_season = CareerManager(db, seed=args.career_seed).start_new_season()
        rollover = time.perf_counter() - t0
        row = _world(database, season=new_season)
        row.update(_season_stats(database, new_season - 1))
        row.update({
            "season": new_season - 1, "new_season": new_season, "weeks": len(weeks),
            "play_week_median_sn": round(statistics.median(weeks), 3) if weeks else None,
            "rollover_sn": round(rollover, 3),
            "season_wall_sn": round(time.perf_counter() - t_season, 1),
            "intake": intake_total,
        })
        seasons.append(row)
        print(f"[kabul] sezon {row['season']:>2} -> {new_season}: {row['season_wall_sn']:>6.1f} sn · "
              f"oyuncu {row['players']:>5} ({row['players'] / base['players'] * 100 - 100:+.1f}%) · "
              f"yas {row['avg_age']} · guc {row['avg_overall']} · gol/mac {row['goals_per_match']} · "
              f"emekli {row['retired']} · genc {intake_total} · kadro {row['min_squad']}-{row['max_squad']} "
              f"· kaleci>={row['min_keepers']} (%{row['gk_share']}, yas {row['gk_avg_age']}, "
              f"guc {row['gk_avg_overall']}) · serbest {row['free_agents']}", flush=True)

    counts = [s["players"] for s in seasons]
    ages = [s["avg_age"] for s in seasons]
    strengths = [s["avg_overall"] for s in seasons]
    goals = [s["goals_per_match"] for s in seasons if s["goals_per_match"] is not None]
    drift10 = None
    if len(strengths) >= 10:
        drift10 = round(max(abs(strengths[i + 9] - strengths[i]) for i in range(len(strengths) - 9)), 2)
    summary = {
        "seasons": len(seasons),
        "players_start": base["players"], "players_end": counts[-1] if counts else None,
        "players_min": min(counts) if counts else None, "players_max": max(counts) if counts else None,
        "players_band_pct": [round(min(counts) / base["players"] * 100 - 100, 2),
                             round(max(counts) / base["players"] * 100 - 100, 2)] if counts else None,
        "avg_age_min": min(ages) if ages else None, "avg_age_max": max(ages) if ages else None,
        "avg_overall_start": base["avg_overall"], "avg_overall_end": strengths[-1] if strengths else None,
        "avg_overall_min": min(strengths) if strengths else None,
        "avg_overall_max": max(strengths) if strengths else None,
        "strength_drift_per_10_seasons": drift10,
        "goals_per_match_min": min(goals) if goals else None, "goals_per_match_max": max(goals) if goals else None,
        "min_squad": min(s["min_squad"] for s in seasons) if seasons else None,
        "min_keepers": min(s["min_keepers"] for s in seasons) if seasons else None,
        "retired_total": sum(s["retired"] for s in seasons),
        "intake_total": sum(s["intake"] for s in seasons),
        "season_wall_median_sn": round(statistics.median([s["season_wall_sn"] for s in seasons]), 1)
        if seasons else None,
        "play_week_median_sn": round(statistics.median([s["play_week_median_sn"] for s in seasons
                                                        if s["play_week_median_sn"]]), 3) if seasons else None,
        "rollover_median_sn": round(statistics.median([s["rollover_sn"] for s in seasons]), 3) if seasons else None,
    }
    print("[kabul] OZET " + json.dumps(summary, ensure_ascii=False))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "meta": {"db": target, "source": args.source, "seed": args.seed, "career_seed": args.career_seed,
                     "seasons": args.seasons, "flag": args.flag, "contract_cycle": args.contract_cycle,
                     "user_team": user, "when": time.strftime("%Y-%m-%d %H:%M:%S")},
            "base": base, "summary": summary, "seasons": seasons,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[kabul] yazildi: {out}")
    return 0


def _world(database, season: int) -> dict:
    """Dunyanin anlik fotografi (oyuncu sayisi, yas / guc / ozellik ortalamalari, kadro tabanlari)."""
    attrs = ", ".join(f"avg(p.{a})" for a in ATTRS)
    with database.engine.connect() as c:
        row = c.execute(text(
            f"SELECT count(*), avg(p.age), avg(p.overall_rating), {attrs}, "
            "count(*) FILTER (WHERE p.team_id IS NULL), count(*) FILTER (WHERE p.in_academy), "
            "avg(p.age) FILTER (WHERE NOT p.in_academy AND p.team_id IS NOT NULL), "
            "avg(p.overall_rating) FILTER (WHERE NOT p.in_academy AND p.team_id IS NOT NULL), "
            "avg(p.age) FILTER (WHERE NOT p.in_academy AND p.team_id IS NOT NULL AND p.position = 'GK'), "
            "avg(p.overall_rating) FILTER (WHERE NOT p.in_academy AND p.team_id IS NOT NULL "
            "AND p.position = 'GK'), "
            "count(*) FILTER (WHERE NOT p.in_academy AND p.team_id IS NOT NULL AND p.position = 'GK'), "
            "stddev_pop(p.overall_rating) FILTER (WHERE NOT p.in_academy AND p.team_id IS NOT NULL) "
            "FROM players p")).one()
        squads = c.execute(text(
            "SELECT t.id, count(p.id) FILTER (WHERE p.id IS NOT NULL AND NOT p.in_academy), "
            "count(p.id) FILTER (WHERE p.position = 'GK' AND NOT p.in_academy) "
            "FROM teams t LEFT JOIN players p ON p.team_id = t.id GROUP BY t.id")).all()
        ages = dict(c.execute(text(
            "SELECT least(age, 40), count(*) FROM players GROUP BY 1 ORDER BY 1")).all())
    total, avg_age, avg_ovr = int(row[0]), row[1], row[2]
    attr_means = {a: round(float(v), 2) for a, v in zip(ATTRS, row[3:3 + len(ATTRS)], strict=True)}
    base = 3 + len(ATTRS)

    def num(index, digits=2):
        return round(float(row[index]), digits) if row[index] is not None else None

    return {
        "season": season, "players": total, "clubs": len(squads),
        "avg_age": round(float(avg_age), 2), "avg_overall": round(float(avg_ovr), 2),
        "senior_avg_age": num(base + 2), "senior_avg_overall": num(base + 3),
        "gk_avg_age": num(base + 4), "gk_avg_overall": num(base + 5),
        "gk_share": round(int(row[base + 6]) / max(1, sum(int(r[1]) for r in squads)) * 100, 2),
        "senior_overall_sd": num(base + 7),
        "attr_means": attr_means, "attr_mean": round(sum(attr_means.values()) / len(ATTRS), 2),
        "free_agents": int(row[base]), "academy": int(row[base + 1]),
        "min_squad": min(int(r[1]) for r in squads), "max_squad": max(int(r[1]) for r in squads),
        "min_keepers": min(int(r[2]) for r in squads),
        "age_hist": {int(k): int(v) for k, v in sorted(ages.items())},
    }


def _season_stats(database, season: int) -> dict:
    """Biten sezonun gol/mac degerleri ve devirde yazilan emeklilik kayitlari."""
    with database.engine.connect() as c:
        league = c.execute(text(
            "SELECT count(*), sum(home_score + away_score) FROM fixtures "
            "WHERE season = :s AND status = 'played' AND competition = 'LEAGUE'"), {"s": season}).one()
        cup = c.execute(text(
            "SELECT count(*), sum(home_score + away_score) FROM fixtures "
            "WHERE season = :s AND status = 'played' AND competition = 'CUP'"), {"s": season}).one()
        retired = c.scalar(text("SELECT count(*) FROM transfer_log WHERE season = :s AND kind = 'RETIRED'"),
                           {"s": season + 1})
        released = c.scalar(text("SELECT count(*) FROM transfer_log WHERE season = :s AND kind = 'RELEASED'"),
                            {"s": season + 1})
    def per_match(row):
        return round(float(row[1]) / int(row[0]), 3) if row and row[0] and row[1] is not None else None
    return {"league_matches": int(league[0] or 0), "goals_per_match": per_match(league),
            "cup_matches": int(cup[0] or 0), "cup_goals_per_match": per_match(cup),
            "retired": int(retired or 0), "released": int(released or 0)}


if __name__ == "__main__":
    raise SystemExit(main())
