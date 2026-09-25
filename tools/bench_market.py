"""
tools/bench_market.py -- Faz 15F canli pazar ve kiralik kabul kosusu (gelistirici araci; oyun calisma zamaninda
kullanilmaz).

YALNIZCA fm_db_test_15f* veritabanlarinda calisir (guvenlik blogu: database import'undan ONCE; tools/bench_week.py,
bench_contracts.py ve bench_retirement.py ile ayni desen). Dunya sifirdan kurulur (reset_db + seed; varsayilan acik
veri dunyasi, 114 kulup), web girisindeki gibi altyapi / kulup / dunya kurulumu, kariyer modu, cm.teams()[0]
menajerin kulubu. Menajer hicbir sey yapmaz: olculen dunyanin KENDI yasamidir (en zor durum).

Donem (transfer_rules.window_span) basina olculenler:
    AI <-> AI transfer sayisi (kabul bandi 40-120), toplam harcama, kiralik sayisi ve EN ZENGIN 5 KULUBUN
    harcama payi UC tanimla.

KABUL OLCUTU (koordinator karari, 2026-09-23): "en zengin 5 kulup" = SEZON BASINDA maas butcesi en buyuk 5
kulup (buyuk5); okuma SEZON BASINA, ayrica 3 sezon toplami; esik %35. Ozetteki rich_share_ok budur.
    buyuk5     kabul kumesi. Futbolda ve CM'de "en zengin 5 kulup" budur; sezon boyunca ayni bes kulubu
               gosterir ve olculen seyden (harcamadan) etkilenmez.
    kasa5      ARTEFAKT olarak kanitta kalir: donem acilisinda transfer_budget en buyuk 5. Kulup harcadikca
               kasasi kuculur ve kumeden duser -- yani EN COK HARCAYAN kendini olculen kumeden cikarir.
               Ayrica 3 sezon sonra transfer nakdi kulupleri ayirt etmez (ortanca 189M).
    harcayan5  DONGUSEL (kumeyi olctugu seyle tanimlar, her zaman yuksek cikar): yalnizca akliselim kontrolu.
Donem okumasi: YAZ penceresi %35 altina duserse KIRMIZI (arastirilir); OCAK penceresinin dusmesi normaldir
(kucuk, firsatci pazar).
Sezon basina olculenler:
    en kucuk / en buyuk A takim kadrosu (kabul 20-32), kasasi negatif kulup sayisi (kabul 0), menajere gelen
    kiralik teklifi sayisi (kabul >= 3), toplam kiralik, soylenti ve son gun haberi sayisi, play_week medyani.

    python tools/bench_market.py --db fm_db_test_15f_bench --seasons 3 --out .claude/phase14/kanit/15F/kabul.json
    python tools/bench_market.py --db fm_db_test_15f_bench --flag off --seasons 3 --out .../taban.json
    python tools/bench_market.py --db fm_db_test_15f_bench --drop
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
PREFIX = "fm_db_test_15f"
RICH_CLUBS = 5


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="fm_db_test_15f_bench")
    ap.add_argument("--source", default="open", choices=("open", "synthetic"))
    ap.add_argument("--seasons", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=5,
                    help="kac farkli yorunge (dunya ve kariyer tohumu birlikte artar). Kabul BES tohumun "
                         "MEDYANINDAN okunur (koordinator karari 2026-09-23): tek yorunge kaotik.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--career-seed", type=int, default=7)
    ap.add_argument("--flag", default="on", choices=("on", "off"), help="transfer_rules.LIVE_MARKET")
    ap.add_argument("--squad-floor", type=int, default=20,
                    help="menajerin A takimi bu sayinin altina duserse akademiden tamamlanir (0: kapali). "
                         "Hicbir sey yapmayan kabul menajerinin kadrosu 15A/15B ile erirse kiralik teklifi "
                         "olcumu anlamsizlasir.")
    ap.add_argument("--board", default="off", choices=("on", "off"),
                    help="15C yonetim kurulu (board.BOARD). VARSAYILAN KAPALI: menajer kovulmasin, "
                         "kiralik teklifi olcumu 3 sezon boyunca surebilsin.")
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
    if args.drop:
        _admin(url, target, drop=True)
        return 0
    _admin(url, target, drop=False)
    import database  # ANCAK simdi
    assert database.engine.url.database == target
    with database.engine.connect() as c:
        assert c.scalar(text("SELECT current_database()")) == target
    # ---- guvenlik blogu sonu ----

    import board
    import seed
    import transfer_rules
    from career_manager import CareerManager
    from models import GameMode

    transfer_rules.LIVE_MARKET = args.flag == "on"
    board.BOARD = args.board == "on"
    runs = [_trajectory(args, database, seed, transfer_rules, CareerManager, GameMode,
                        args.seed + i, args.career_seed + i) for i in range(max(1, int(args.seeds)))]
    return _report(args, runs, target)


def _trajectory(args, database, seed, transfer_rules, CareerManager, GameMode,
                world_seed: int, career_seed: int) -> dict:
    """Tek yorunge: dunyayi kurar, N sezon oynar, olcum satirlarini dondurur."""
    database.reset_db()
    seed.seed(rng_seed=world_seed, source=args.source)
    with database.session_scope() as db:
        cm = CareerManager(db, seed=career_seed)
        cm.ensure_youth_setup()
        cm.ensure_club_setup()
        cm.ensure_world_setup()
        cm.set_game_mode(GameMode.CAREER)
        cm.set_user_team(cm.teams()[0])
        user = cm.state.user_team.name
        season_weeks = int(cm._projected_season_weeks() or 38)
    elite_clubs, elite_names, elite_floor = _elite(database, transfer_rules)
    print(f"[kabul] tohum {world_seed}/{career_seed} · menajer {user} · bayrak {args.flag} · yonetim "
          f"{args.board} · {season_weeks} hafta · ELIT {elite_clubs} kulup (esik "
          f"{elite_floor/1e6:.2f}M/hafta): {', '.join(elite_names) or 'yok'}", flush=True)

    seasons: list[dict] = []
    for _ in range(args.seasons):
        weeks: list[float] = []
        rich: dict[tuple[int, int], list[int]] = {}
        season_no = None
        t_season = time.perf_counter()
        user_team_id = None
        size_ids: list[int] | None = None
        size_names: list[str] = []
        for _step in range(season_weeks + 4):
            with database.session_scope() as db:
                cm = CareerManager(db, seed=career_seed)
                if cm.season_finished:
                    break
                season_no = int(cm.season)
                week = int(cm.current_week)
                user_team_id = cm.state.user_team_id or user_team_id
                span = transfer_rules.window_span(week, season_weeks, False)
            if size_ids is None:                      # SEZON BASINDA sabitlenir (karar: maas butcesi kumesi)
                size_ids, size_names = _size_richest(database)
            if span is not None and week == int(span[0]):
                rich[(int(span[0]), int(span[1]))] = {"cash": _cash_richest(database), "size": size_ids}
            if args.squad_floor > 0:
                with database.session_scope() as db:
                    _top_up(CareerManager(db, seed=career_seed), args.squad_floor)
            t0 = time.perf_counter()
            with database.session_scope() as db:
                CareerManager(db, seed=career_seed).play_week()
            weeks.append(time.perf_counter() - t0)
        row = _season_stats(database, season_no, rich, user_team_id)
        row["size_five"] = size_names                 # kumenin sezon sezon sabit kaldigi kanitta gorunur
        t0 = time.perf_counter()
        with database.session_scope() as db:
            CareerManager(db, seed=career_seed).start_new_season()
        row.update({
            "season": season_no, "weeks": len(weeks),
            "play_week_median_sn": round(statistics.median(weeks), 3) if weeks else None,
            "rollover_sn": round(time.perf_counter() - t0, 3),
            "season_wall_sn": round(time.perf_counter() - t_season, 1),
        })
        seasons.append(row)
        windows = " · ".join(f"{w['label']}: {w['transfers']} transfer, BUYUK5 %{w['size_share_pct']} "
                             f"(kasa5 %{w['rich_share_pct']} / harcayan5 %{w['top5_spend_share_pct']})"
                             for w in row["windows"])
        print(f"[kabul]   sezon {season_no}: {row['season_wall_sn']:>6.1f} sn · AI kadro "
              f"{row['ai_min_squad']}-{row['ai_max_squad']} (menajer {row['manager_squad']}) · "
              f"negatif kasa {row['negative_budgets']} · "
              f"kiralik {row['loans']} (menajere teklif {row['manager_loan_offers']}) · "
              f"soylenti {row['rumours']} · {windows}", flush=True)

    def _agg(key: str) -> dict:
        """Ayni payi UC okumayla: donem basina, sezon basina ve 3 sezon toplaminda."""
        per_window, per_season, total_spend, total_share = [], [], 0, 0.0
        for row in seasons:
            spend = sum(w["spend"] for w in row["windows"])
            share = sum(w["spend"] * w[key] / 100 for w in row["windows"])
            total_spend += spend
            total_share += share
            if spend:
                per_season.append(round(share / spend * 100, 1))
            per_window += [w[key] for w in row["windows"] if w["transfers"]]
        return {"window_min": min(per_window) if per_window else None,
                "window_ok": all(v >= 35.0 for v in per_window) if per_window else None,
                "season": per_season,
                "season_ok": all(v >= 35.0 for v in per_season) if per_season else None,
                "total": round(total_share / total_spend * 100, 1) if total_spend else None,
                "total_ok": (total_share / total_spend * 100) >= 35.0 if total_spend else None}

    window_counts = [w["transfers"] for s in seasons for w in s["windows"]]
    summer_shares = [w["size_share_pct"] for s in seasons for w in s["windows"]
                     if w["transfers"] and int(w["first_week"]) == 1]
    winter_shares = [w["size_share_pct"] for s in seasons for w in s["windows"]
                     if w["transfers"] and int(w["first_week"]) != 1]
    summer_ok = all(v >= 35.0 for v in summer_shares) if summer_shares else None
    summary = {
        "seasons": len(seasons),
        "windows": len(window_counts),
        "window_transfers_min": min(window_counts) if window_counts else None,
        "window_transfers_max": max(window_counts) if window_counts else None,
        "window_transfers_in_band": all(40 <= n <= 120 for n in window_counts) if window_counts else None,
        # KABUL OLCUTU (koordinator karari 2026-09-23): "en zengin 5 kulup" = SEZON BASINDA maas butcesi en
        # buyuk 5 kulup; okuma SEZON BASINA (ayrica 3 sezon toplami). Esik 35, degismedi.
        "rich_share_ok": _agg("size_share_pct")["season_ok"],
        "rich_share_season_pct": _agg("size_share_pct")["season"],
        "rich_share_total_pct": _agg("size_share_pct")["total"],
        # Donem okumasi: YAZ penceresi 35'in altina duserse KIRMIZI (arastirilir); ocagin dusmesi normaldir
        # (kucuk, firsatci pazar).
        "summer_window_pct": summer_shares, "summer_window_ok": summer_ok,
        "winter_window_pct": winter_shares,
        # UC okuma (donem / sezon / 3 sezon toplami) x UC tanim -- kanitta hepsi kalir.
        # cash: ARTEFAKT (harcayan kume disina duser), top5_spend: DONGUSEL (yalnizca akliselim kontrolu).
        "size_share": _agg("size_share_pct"), "cash_share_artefact": _agg("rich_share_pct"),
        "top5_spend_share_circular": _agg("top5_spend_share_pct"),
        "size_five": seasons[-1].get("size_five") if seasons else None,
        # KUME karsilastirmasi (frozenset): siralama degisebilir, kume ayni kaldigi surece KARARLIDIR
        "size_five_stable": len({frozenset(s.get("size_five") or ()) for s in seasons}) == 1 if seasons else None,
        "size_five_order": [s.get("size_five") for s in seasons],
        "min_squad": min(s["min_squad"] for s in seasons) if seasons else None,
        "max_squad": max(s["max_squad"] for s in seasons) if seasons else None,
        "squad_band_ok": all(20 <= s["min_squad"] and s["max_squad"] <= 32 for s in seasons) if seasons else None,
        "ai_min_squad": min(s["ai_min_squad"] for s in seasons) if seasons else None,
        "ai_max_squad": max(s["ai_max_squad"] for s in seasons) if seasons else None,
        "ai_squad_band_ok": all(20 <= s["ai_min_squad"] and s["ai_max_squad"] <= 32 for s in seasons)
        if seasons else None,
        "manager_squad_min": min(s["manager_squad"] for s in seasons) if seasons else None,
        "negative_budgets": max(s["negative_budgets"] for s in seasons) if seasons else None,
        "manager_loan_offers_min": min(s["manager_loan_offers"] for s in seasons) if seasons else None,
        "manager_loan_offers_ok": all(s["manager_loan_offers"] >= 3 for s in seasons) if seasons else None,
        "loans_total": sum(s["loans"] for s in seasons),
        "rumours_total": sum(s["rumours"] for s in seasons),
        "deadline_stories": sum(s["deadline_stories"] for s in seasons),
        "play_week_median_sn": round(statistics.median([s["play_week_median_sn"] for s in seasons
                                                        if s["play_week_median_sn"]]), 3) if seasons else None,
        "season_wall_median_sn": round(statistics.median([s["season_wall_sn"] for s in seasons]), 1)
        if seasons else None,
        "rollover_median_sn": round(statistics.median([s["rollover_sn"] for s in seasons]), 3) if seasons else None,
    }
    print(f"[kabul]   tohum ozeti: sezon paylari {summary['rich_share_season_pct']} · "
          f"toplam %{summary['rich_share_total_pct']} · yaz {summary['summer_window_pct']}", flush=True)
    return {"world_seed": world_seed, "career_seed": career_seed, "user_team": user,
            "season_weeks": season_weeks, "elite_clubs": elite_clubs, "elite_names": elite_names,
            "elite_floor": elite_floor, "summary": summary, "seasons": seasons}


def _elite(database, transfer_rules) -> tuple[int, list[str], float]:
    """
    Dunyada kac kulup ELIT (maas butcesi >= ortanca x MARKET_ELITE_MEDIANS)? Koordinator karari geregi her
    kosuda kanita yazilir: DUZ bir dunyada elit kulup YOKTUR ve "ihtiyacim yok" kapisi kapali kalir -- bu
    dogru davranistir, kusur degil (16A lig piramidi gelince yeniden olculecek).
    """
    with database.engine.connect() as c:
        rows = c.execute(text("SELECT name, wage_budget FROM teams ORDER BY wage_budget DESC")).all()
    floor = transfer_rules.elite_wage_floor(int(r[1]) for r in rows)
    names = [str(r[0]) for r in rows if int(r[1]) >= floor]
    return len(names), names, float(floor)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _report(args, runs: list[dict], target: str) -> int:
    """
    Koordinator karari (2026-09-23): kabul TEK yorungeden degil, BES TOHUMUN DAGILIMINDAN okunur.
    Gecme olcutu: tohumlarin MEDYANI >= %35 (sezon okumasi, maas butcesi kumesi). En kotu tohum da yazilir:
    medyan geciyor ama tek tohum cok asagidaysa bu "gurultu" degil KIRILGAN'dir ve ayrica incelenir.
    Hicbir esik degismedi; tek kosunun yerine dagilim gecti.
    """
    totals = [r["summary"]["rich_share_total_pct"] for r in runs if r["summary"]["rich_share_total_pct"]]
    seasonal = [v for r in runs for v in (r["summary"]["rich_share_season_pct"] or [])]
    summers = [v for r in runs for v in (r["summary"]["summer_window_pct"] or [])]
    winters = [v for r in runs for v in (r["summary"]["winter_window_pct"] or [])]
    worst = min(runs, key=lambda r: r["summary"]["rich_share_total_pct"] or 0) if runs else None
    offers = [int(s["manager_loan_offers"]) for r in runs for s in r["seasons"]]
    worst_offer_seed = next((r["world_seed"] for r in runs
                             for s in r["seasons"] if int(s["manager_loan_offers"]) == min(offers)), None)         if offers else None
    bands = [r["summary"] for r in runs]
    dist = {
        "seeds": len(runs),
        "rich_share_seed_totals_pct": totals,
        "rich_share_median_pct": _median(totals),
        "rich_share_ok": (_median(totals) or 0) >= 35.0,
        "rich_share_worst_seed": worst["world_seed"] if worst else None,
        "rich_share_worst_pct": worst["summary"]["rich_share_total_pct"] if worst else None,
        "rich_share_season_values_pct": seasonal,
        "rich_share_season_median_pct": _median(seasonal),
        "summer_window_values_pct": summers, "summer_window_median_pct": _median(summers),
        "summer_window_ok": (_median(summers) or 0) >= 35.0,
        "winter_window_values_pct": winters, "winter_window_median_pct": _median(winters),
        "elite_clubs": [r["elite_clubs"] for r in runs],
        "window_transfers_in_band": all(b["window_transfers_in_band"] for b in bands),
        "window_transfers_min": min(b["window_transfers_min"] for b in bands),
        "window_transfers_max": max(b["window_transfers_max"] for b in bands),
        "ai_squad_band_ok": all(b["ai_squad_band_ok"] for b in bands),
        "ai_min_squad": min(b["ai_min_squad"] for b in bands),
        "ai_max_squad": max(b["ai_max_squad"] for b in bands),
        "negative_budgets": max(b["negative_budgets"] for b in bands),
        "play_week_median_sn": _median([b["play_week_median_sn"] for b in bands if b["play_week_median_sn"]]),
        # ELIT kume (ortancanin 3 kati; kabul kapisini kullanan kume) tohumlar arasi AYNI mi?
        "elite_set_stable": len({frozenset(r["elite_names"]) for r in runs}) == 1 if runs else None,
        "elite_set": sorted(runs[0]["elite_names"]) if runs else None,
        # En zengin BES kulup (olcum kumesi): tohum ICINDE kararli mi, tohumlar ARASI ayni mi? Alti kulup
        # birbirine cok yakin oldugu icin "ilk bes" kesimi tohuma gore degisebilir -- elit kume degismez.
        "size_five_stable_within_seed": all(b["size_five_stable"] for b in bands),
        "size_five_same_across_seeds": len({frozenset(b["size_five"] or ()) for b in bands}) == 1,
        "size_five_sets": sorted(sorted(f) for f in {frozenset(b["size_five"] or ()) for b in bands}),
        # Menajere kiralik teklifi: esik 3, okuma MEDYAN (digerleriyle tutarli; tek yorunge kaotik)
        "manager_loan_offers_values": offers,
        "manager_loan_offers_median": _median(offers),
        "manager_loan_offers_ok": (_median(offers) or 0) >= 3,
        "manager_loan_offers_worst": min(offers) if offers else None,
        "manager_loan_offers_worst_seed": worst_offer_seed,
    }
    print("[kabul] DAGILIM " + json.dumps(dist, ensure_ascii=False))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "meta": {"db": target, "source": args.source, "seeds": len(runs), "base_seed": args.seed,
                     "base_career_seed": args.career_seed, "seasons": args.seasons, "flag": args.flag,
                     "board": args.board, "squad_floor": args.squad_floor,
                     "when": time.strftime("%Y-%m-%d %H:%M:%S")},
            "distribution": dist, "runs": runs,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[kabul] yazildi: {out}")
    return 0


def _cash_richest(database) -> list[int]:
    """Donem acilisinda kasasi (transfer_budget) en buyuk 5 kulup. ARTEFAKT tanim: kulup harcadikca listeden
    duser, yani en cok harcayan olculen kumeden CIKAR. Kanitta gorunur kalsin diye olculmeye devam eder."""
    with database.engine.connect() as c:
        return [int(r[0]) for r in c.execute(text(
            "SELECT id FROM teams ORDER BY transfer_budget DESC, id LIMIT :n"), {"n": RICH_CLUBS}).all()]


def _size_richest(database) -> tuple[list[int], list[str]]:
    """
    SEZON BASINDA maas butcesi en buyuk 5 kulup -- koordinatorun 2026-09-23 karariyla "en zengin 5 kulup"
    tanimi budur: futbolda ve CM'de kulup buyuklugu budur, sezon boyunca ayni bes kulubu gosterir ve
    olculen seyden (harcamadan) etkilenmez. (id listesi, ad listesi)
    """
    with database.engine.connect() as c:
        rows = c.execute(text(
            "SELECT id, name FROM teams ORDER BY wage_budget DESC, id LIMIT :n"), {"n": RICH_CLUBS}).all()
    return [int(r[0]) for r in rows], [str(r[1]) for r in rows]


def _top_up(cm, floor: int) -> None:
    """Menajerin A takimini akademiden tabana tamamlar (gercek menajerin her sezon yaptigi en temel is)."""
    team = cm.user_team
    if team is None:
        return
    seniors = [p for p in team.players if not p.in_academy]
    missing = int(floor) - len(seniors)
    if missing <= 0:
        return
    ready = sorted((p for p in team.academy_players if p.loan_from_team_id is None),
                   key=lambda p: (-int(p.overall_rating), p.id))[:missing]
    for player in ready:
        try:
            cm.promote_to_senior(team, player)
        except Exception:                     # kadro dolu / kural engeli: sessizce gec
            break


def _pct(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _season_stats(database, season: int | None, rich: dict, user_team_id: int | None) -> dict:
    """Sezonun donem donem pazar ozeti ve dunya saglik olculeri (devirden ONCE okunur)."""
    windows = []
    with database.engine.connect() as c:
        for (first, last), ids in sorted(rich.items()):
            rows = c.execute(text(
                "SELECT to_team_id, fee FROM transfer_log WHERE season = :s AND week BETWEEN :a AND :b "
                "AND kind = 'TRANSFER'"), {"s": season, "a": first, "b": last}).all()
            loans = c.scalar(text(
                "SELECT count(*) FROM transfer_log WHERE season = :s AND week BETWEEN :a AND :b AND kind = 'LOAN'"),
                {"s": season, "a": first, "b": last}) or 0
            spend = sum(int(r[1] or 0) for r in rows)
            by_club: dict[int, int] = {}
            for team_id, fee in rows:
                if team_id is not None:
                    by_club[int(team_id)] = by_club.get(int(team_id), 0) + int(fee or 0)
            cash_spend = sum(by_club.get(i, 0) for i in ids["cash"])
            size_spend = sum(by_club.get(i, 0) for i in ids["size"])
            top5_spend = sum(sorted(by_club.values(), reverse=True)[:RICH_CLUBS])
            windows.append({
                "label": f"{first}-{last}", "first_week": first, "last_week": last,
                "transfers": len(rows), "loans": int(loans), "spend": spend,
                "rich_spend": cash_spend, "rich_share_pct": _pct(cash_spend, spend),
                "size_share_pct": _pct(size_spend, spend), "top5_spend_share_pct": _pct(top5_spend, spend),
            })
        squads = c.execute(text(
            "SELECT min(n), max(n) FROM (SELECT count(*) n FROM players WHERE NOT in_academy "
            "AND team_id IS NOT NULL GROUP BY team_id) s")).one()
        ai_squads = c.execute(text(
            "SELECT min(n), max(n) FROM (SELECT count(*) n FROM players WHERE NOT in_academy "
            "AND team_id IS NOT NULL AND team_id <> coalesce(:t, -1) GROUP BY team_id) s"),
            {"t": user_team_id}).one()
        manager_squad = c.scalar(text(
            "SELECT count(*) FROM players WHERE NOT in_academy AND team_id = :t"), {"t": user_team_id}) or 0
        negative = c.scalar(text("SELECT count(*) FROM teams WHERE transfer_budget < 0")) or 0
        loans_total = c.scalar(text("SELECT count(*) FROM transfer_log WHERE season = :s AND kind = 'LOAN'"),
                               {"s": season}) or 0
        offers = c.scalar(text(
            "SELECT count(*) FROM transfer_deals WHERE kind = 'LOAN' AND direction = 'OUT' AND season = :s "
            "AND human_team_id = :t"), {"s": season, "t": user_team_id}) or 0
        rumours = c.scalar(text(
            "SELECT count(*) FROM news_items WHERE season = :s AND kind = 'RUMOUR' AND text LIKE 'Söylenti%'"),
            {"s": season}) or 0
        deadline = c.scalar(text(
            "SELECT count(*) FROM news_items WHERE season = :s AND kind = 'RUMOUR' AND text LIKE 'Son gün%'"),
            {"s": season}) or 0
        transfers = c.scalar(text(
            "SELECT count(*) FROM transfer_log WHERE season = :s AND kind = 'TRANSFER'"), {"s": season}) or 0
        spend_all = c.scalar(text(
            "SELECT coalesce(sum(fee), 0) FROM transfer_log WHERE season = :s AND kind = 'TRANSFER'"),
            {"s": season}) or 0
    return {
        "windows": windows, "min_squad": int(squads[0] or 0), "max_squad": int(squads[1] or 0),
        "ai_min_squad": int(ai_squads[0] or 0), "ai_max_squad": int(ai_squads[1] or 0),
        "manager_squad": int(manager_squad),
        "negative_budgets": int(negative), "loans": int(loans_total), "manager_loan_offers": int(offers),
        "rumours": int(rumours), "deadline_stories": int(deadline), "transfers": int(transfers),
        "spend": int(spend_all),
    }


if __name__ == "__main__":
    raise SystemExit(main())
