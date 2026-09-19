"""
tools/bench_week.py -- hafta isleme hizi olcum araci (Faz 14D). Gelistirici araci; oyun calisma zamaninda kullanilmaz.

YALNIZCA fm_db_test_14d* veritabanlarinda calisir (guvenlik blogu: database import'undan ONCE). Her kosu dunyayi
sifirdan kurar (database.reset_db + seed.seed): seed deterministik, dizi sayaclari sifirlanir -> her kosu ayni
baslangictan cikar. Ardindan web girisindeki gibi (accounts.py) altyapi / kulup / dunya kurulumu, kariyer modu ve
cm.teams()[0] kulubu. Her hafta ayri session_scope (web geri cagrisi gibi); sure with-blogunun tamamidir (commit
dahil). Hafta basina: sure, SQL sayisi (ifade turune gore; executemany satirlari ayrica), tum tablolarin ozeti
(DateTime sutunlari haric, birincil anahtar sirasiyla sha256) ve rapor ozeti (tests/test_multi_seat_career
._report_payload alanlari).

    python tools/bench_week.py --db fm_db_test_14d --source open --weeks 6 --seed 42 --career-seed 7 \
        --gc on|off|collect [--rollover] [--profile] --out .claude/phase14/kanit/14D/<ad>.json
    python tools/bench_week.py --compare A.json B.json      # ozetler ayni mi; hafta hafta fark tablosu
    python tools/bench_week.py --drop --db fm_db_test_14d    # yalnizca bu test veritabanini dusurur

--gc collect: CareerManager._post_match sarilir, her mactan sonra gc.collect() (dayaniklilik varyanti).
--rollover:   sezon bitene kadar hafta, sonra start_new_season() (sure + ozet), sonra yeni sezonun 1. haftasi.
--profile:    --profile-week haftasinda cProfile + SQL kaynak dokumu (her ifade E:\\cm icindeki en ic iki cagirana).
"""

from __future__ import annotations

import argparse
import hashlib
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

SELF = Path(__file__).resolve()
KINDS = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="fm_db_test_14d")
    ap.add_argument("--source", default="open", choices=("open", "synthetic"))
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--career-seed", type=int, default=7)
    ap.add_argument("--gc", default="on", choices=("on", "off", "collect"))
    ap.add_argument("--setup", default="web", choices=("web", "bare"),
                    help="web: accounts.py girisindeki ensure_youth/club/world_setup (varsayilan); bare: yalniz mod+kulup")
    ap.add_argument("--rollover", action="store_true")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--profile-week", type=int, default=1)
    ap.add_argument("--profile-out", default=None)
    ap.add_argument("--no-digest", action="store_true", help="tablo ozetleri alinmaz (yalniz sure)")
    ap.add_argument("--phases", action="store_true", help="hafta adimlarinin duvar saati (sarmalayici; sonucu degistirmez)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--drop", action="store_true")
    ap.add_argument("--base-dir", default=None,
                    help="A/B zamanlamasi: bu dizindeki moduller (orn. git show HEAD:career_manager.py) once yuklenir")
    ap.add_argument("--high-priority", action="store_true",
                    help="yalnizca bu olcum surecinin Windows onceligi YUKSEK (makinedeki diger yukun etkisini azaltir)")
    return ap.parse_args()


# ===========================================================================
# Karsilastirma (veritabani gerekmez)
# ===========================================================================

def _rows(data: dict) -> list[tuple[str, dict]]:
    rows = [(f"w{w['label']}", w) for w in data.get("weeks", [])]
    ro = data.get("rollover")
    if ro:
        rows.append(("devir", ro["new_season"]))
        rows += [(f"y{w['label']}", w) for w in ro.get("weeks", [])]
    return rows


def compare(path_a: str, path_b: str) -> int:
    a = json.loads(Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(Path(path_b).read_text(encoding="utf-8"))
    rows_a, rows_b = dict(_rows(a)), dict(_rows(b))
    keys = [k for k, _ in _rows(a) if k in rows_b]
    bad = 0
    print(f"A = {path_a}\nB = {path_b}")
    print(f"{'hafta':>7} | {'A sn':>6} {'B sn':>6} {'fark':>6} | {'A sql':>6} {'B sql':>6} | ozet")
    for k in keys:
        wa, wb = rows_a[k], rows_b[k]
        tables_a, tables_b = wa.get("tables") or {}, wb.get("tables") or {}
        diff = sorted(t for t in set(tables_a) | set(tables_b) if tables_a.get(t) != tables_b.get(t))
        report_same = wa.get("report") == wb.get("report")
        same = not diff and report_same
        if not tables_a or not tables_b:
            verdict = "ozet yok"
        else:
            verdict = "AYNI" if same else "FARKLI: " + ", ".join((["rapor"] if not report_same else []) + diff)
        if tables_a and tables_b and not same:
            bad += 1
        print(f"{k:>7} | {wa['sure_sn']:6.2f} {wb['sure_sn']:6.2f} {wb['sure_sn'] - wa['sure_sn']:+6.2f} | "
              f"{wa['sql']['total']:6d} {wb['sql']['total']:6d} | {verdict}")
    only = sorted(set(rows_a) ^ set(rows_b))
    if only:
        print(f"yalniz bir tarafta: {only}")
    for name, data in (("A", a), ("B", b)):
        s = data.get("summary", {})
        print(f"{name}: medyan {s.get('median_sn')} sn, en kotu {s.get('worst_sn')} sn, SQL medyani {s.get('sql_median')}"
              + (f", sezon devri {data['rollover']['new_season']['sure_sn']} sn" if data.get("rollover") else ""))
    print("SONUC:", "tum ozetler AYNI" if not bad and keys else f"{bad} satirda FARK")
    return 1 if bad else 0


# ===========================================================================
# Olcum
# ===========================================================================

def main() -> int:  # noqa: C901 - tek akisli gelistirici araci
    args = _parse()
    if args.compare:
        return compare(*args.compare)

    if args.base_dir:
        sys.path.insert(0, str(Path(args.base_dir).resolve()))
    if args.high_priority and sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000080)   # HIGH_PRIORITY_CLASS

    # ---- 4.1 guvenlik blogu (database import'undan ONCE) ----
    TARGET = args.db
    if not TARGET.startswith("fm_db_test_14d"):
        raise SystemExit("Guvenlik: yalnizca fm_db_test_14d* veritabanlari.")
    from dotenv import load_dotenv

    from tests.db_urls import build_test_url
    load_dotenv(ROOT / ".env")
    url, main_db = build_test_url(os.environ["DATABASE_URL"], TARGET)
    if not main_db or main_db == TARGET or TARGET == "fm_db":
        raise SystemExit("Guvenlik: hedef DB oyun DB'siyle ayni.")
    os.environ["DATABASE_URL"] = url            # .env'deki fm_db'nin yerine
    os.environ["SEED_NAME_MASKING"] = "light"
    os.environ.pop("OFM_ALLOW_REAL_NAMES", None)
    os.environ["OFM_NEW_WORLD_SOURCE"] = "synthetic"   # yan yol kayit olursa kucuk dunya
    if args.drop:
        _admin(url, TARGET, drop=True)
        return 0
    _admin(url, TARGET, drop=False)             # veritabani yoksa 'postgres' uzerinden CREATE DATABASE
    import database  # ANCAK simdi
    assert database.engine.url.database == TARGET
    with database.engine.connect() as c:
        assert c.scalar(text("SELECT current_database()")) == TARGET
    # ---- guvenlik blogu sonu ----

    import gc

    import seed
    from career_manager import CareerManager
    from models import GameMode

    counter = _SqlCounter(database.engine)
    database.reset_db()
    seed.seed(rng_seed=args.seed, source=args.source)
    with database.session_scope() as db:
        cm = CareerManager(db, seed=args.career_seed)
        if args.setup == "web":                 # accounts.py girisi: altyapi, kulup ekonomisi, birincil koltuk
            cm.ensure_youth_setup()
            cm.ensure_club_setup()
            cm.ensure_world_setup()
        cm.set_game_mode(GameMode.CAREER)
        cm.set_user_team(cm.teams()[0])
        user_team = cm.state.user_team.name
        world = {"teams": len(cm.teams()),
                 "players": db.scalar(text("SELECT count(*) FROM players"))}

    if args.gc == "collect":
        original = CareerManager._post_match

        def _post_match_collect(self, *a, **kw):
            original(self, *a, **kw)
            gc.collect()

        CareerManager._post_match = _post_match_collect
    if args.gc == "off":
        gc.collect()
        gc.disable()
    phases = _PhaseTimer(CareerManager) if args.phases else None

    def one_week(label: str, profile: bool) -> dict:
        with database.session_scope() as probe:
            before = CareerManager(probe).state
            season, week = before.season, before.current_week
        prof = _Profiler(database.engine) if profile else None
        if phases is not None:
            phases.reset()
        counter.start()
        c0 = time.process_time()
        t0 = time.perf_counter()
        with database.session_scope() as db:
            cm = CareerManager(db, seed=args.career_seed)
            if prof is not None:
                prof.start()
            report = cm.play_week()
            if prof is not None:
                prof.stop()
            t_play = time.perf_counter() - t0
        elapsed = time.perf_counter() - t0
        cpu = time.process_time() - c0
        sql = counter.stop()
        out = {"label": label, "season": season, "week": week, "sure_sn": round(elapsed, 3),
               "play_sn": round(t_play, 3), "commit_sn": round(elapsed - t_play, 3), "cpu_sn": round(cpu, 3),
               "sql": sql,
               "transfers": len(report.transfers)}
        if phases is not None:
            out["phases"] = phases.snapshot()
        if not args.no_digest:
            out["report"] = _digest(_report_payload(report))
            out["tables"] = _table_digests(database)
        if prof is not None:
            out["profile_text"] = prof.render(label, elapsed, sql)
        return out

    weeks: list[dict] = []
    rollover: dict | None = None
    total = args.weeks
    try:
        if args.rollover:
            for n in range(1, 80):
                with database.session_scope() as probe:
                    if CareerManager(probe).season_finished:
                        break
                weeks.append(one_week(str(n), args.profile and n == args.profile_week))
                _progress(weeks[-1])
            counter.start()
            t0 = time.perf_counter()
            with database.session_scope() as db:
                new_season = CareerManager(db, seed=args.career_seed).start_new_season()
            elapsed = time.perf_counter() - t0
            sql = counter.stop()
            ns = {"label": "devir", "season": new_season, "week": 1, "sure_sn": round(elapsed, 3), "sql": sql}
            if not args.no_digest:
                ns["tables"] = _table_digests(database)
                ns["report"] = None
            print(f"[bench] sezon devri: {elapsed:.2f} sn, SQL {sql['total']} ({sql['rows']} satir)")
            rollover = {"new_season": ns, "weeks": [one_week("1", False)]}
            _progress(rollover["weeks"][-1])
        else:
            for n in range(1, total + 1):
                weeks.append(one_week(str(n), args.profile and n == args.profile_week))
                _progress(weeks[-1])
    finally:
        if args.gc == "off":
            gc.enable()

    times = [w["sure_sn"] for w in weeks]
    sqls = [w["sql"]["total"] for w in weeks]
    summary = {"weeks": len(weeks), "median_sn": round(statistics.median(times), 3), "worst_sn": max(times),
               "cpu_median_sn": round(statistics.median(w["cpu_sn"] for w in weeks), 3),
               "sql_median": statistics.median(sqls), "sql_rows_median": statistics.median(w["sql"]["rows"] for w in weeks)}
    profile_text = "\n".join(w.pop("profile_text") for w in weeks if "profile_text" in w)
    import career_manager

    data = {"meta": {"db": TARGET, "source": args.source, "seed": args.seed, "career_seed": args.career_seed,
                     "gc": args.gc, "setup": args.setup, "career_manager": career_manager.__file__,
                     "high_priority": bool(args.high_priority), "user_team": user_team, "world": world,
                     "python": sys.version.split()[0], "when": time.strftime("%Y-%m-%d %H:%M:%S")},
            "summary": summary, "weeks": weeks, "rollover": rollover}
    print(f"[bench] medyan {summary['median_sn']} sn, en kotu {summary['worst_sn']} sn, "
          f"SQL medyani {summary['sql_median']} (satir {summary['sql_rows_median']})")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[bench] yazildi: {out}")
    if profile_text:
        target = Path(args.profile_out) if args.profile_out else (
            Path(args.out).with_name(Path(args.out).stem + "_profil.txt") if args.out else None)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(profile_text, encoding="utf-8")
            print(f"[bench] profil: {target}")
        else:
            print(profile_text)
    return 0


def _progress(w: dict) -> None:
    if "phases" in w:
        print("         adimlar: " + " | ".join(f"{k} {v:.2f}" for k, v in w["phases"].items()), flush=True)
    s = w["sql"]
    print(f"[bench] s{w['season']} h{w['week']:>2}: {w['sure_sn']:6.2f} sn (oyun {w['play_sn']:.2f} + commit "
          f"{w['commit_sn']:.2f}; cpu {w['cpu_sn']:.2f}) SQL {s['total']:5d} = S{s['SELECT']} I{s['INSERT']} U{s['UPDATE']} D{s['DELETE']} "
          f"diger {s['OTHER']} | satir {s['rows']} | AI transfer {w['transfers']}", flush=True)


def _admin(url: str, name: str, drop: bool) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    if not name.startswith("fm_db_test_14d"):
        raise SystemExit("Guvenlik: yalnizca fm_db_test_14d* veritabanlari.")
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if drop:
                conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"),
                             {"n": name})
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                print(f"[bench] {name} silindi")
            elif not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}):
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


# ===========================================================================
# Ozetler (tests/test_multi_seat_career deseni; kod oraya import edilmeden kopyalandi)
# ===========================================================================

def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _score(result) -> str | None:
    return None if result is None else f"{result.home.name} {result.home_score}-{result.away_score} {result.away.name}"


def _report_payload(report) -> dict:
    from career_views import cup_report_lines, week_report_lines

    def notes(items):
        return [(n.player_id, n.player_name, n.team_name, n.detail) for n in items]

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


def _table_digests(database) -> dict[str, str]:
    from sqlalchemy import DateTime, inspect, select

    digests: dict[str, str] = {}
    with database.engine.connect() as conn:
        insp = inspect(conn)
        for table in database.Base.metadata.sorted_tables:
            if not insp.has_table(table.name, schema=table.schema):
                continue
            cols = [c for c in table.columns if not isinstance(c.type, DateTime)]
            order = list(table.primary_key.columns) or cols
            rows = conn.execute(select(*cols).order_by(*order)).all()
            key = f"{table.schema}.{table.name}" if table.schema else table.name
            digests[key] = _digest([list(row) for row in rows])
    return digests


# ===========================================================================
# SQL sayaci, adim sureleri ve profil
# ===========================================================================

PHASES = (
    ("mac", "_prepare_career_fixture"), ("motor", None), ("mac_sonrasi", "_post_match"),
    ("arsiv_flush", "_archive_finished_leagues"), ("gelisim", "_weekly_development"), ("genc", "_youth_intake"),
    ("kaygi", "_weekly_concerns"), ("maas", "_pay_weekly_wages"), ("ai_transfer", "run_ai_transfer_window"),
    ("masa", "_run_transfer_desk"), ("toplu_okuma", "_load_teams"), ("toplu_yazim", "_bulk_write_dirty"),
)


class _PhaseTimer:
    """CareerManager adimlarini (ve MatchEngine.simulate'i) duvar saatiyle sarar; ic ice cagrilar ayrica sayilir."""

    def __init__(self, cm_cls) -> None:
        import match_engine

        self.totals: dict[str, float] = {}
        for label, name in PHASES:
            owner, attr = (match_engine.MatchEngine, "simulate") if name is None else (cm_cls, name)
            setattr(owner, attr, self._wrap(label, getattr(owner, attr)))

    def _wrap(self, label: str, fn):
        totals = self.totals

        def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                totals[label] = totals.get(label, 0.0) + time.perf_counter() - t0

        return wrapper

    def reset(self) -> None:
        self.totals.clear()

    def snapshot(self) -> dict[str, float]:
        return {label: round(self.totals.get(label, 0.0), 3) for label, _ in PHASES}

def _kind(statement: str) -> str:
    head = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
    if head == "WITH":
        for kind in KINDS:
            if kind in statement.upper():
                return kind
    return head if head in KINDS else "OTHER"


class _SqlCounter:
    """before_cursor_execute: ifade turune gore sayim (executemany tek ifade; 'rows' satir sayisi)."""

    def __init__(self, engine) -> None:
        from sqlalchemy import event

        self.active = False
        self.counts: dict[str, int] = {}
        event.listen(engine, "before_cursor_execute", self._on)

    def _on(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if not self.active:
            return
        kind = _kind(statement)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        rows = len(parameters) if executemany and isinstance(parameters, (list, tuple)) else 1
        self.counts["rows"] = self.counts.get("rows", 0) + rows

    def start(self) -> None:
        self.counts = {}
        self.active = True

    def stop(self) -> dict[str, int]:
        self.active = False
        out = {k: self.counts.get(k, 0) for k in (*KINDS, "OTHER")}
        out["total"] = sum(out.values())
        out["rows"] = self.counts.get("rows", 0)
        return out


class _Profiler:
    """Bir hafta: cProfile + her ifadenin DB suresi, E:\\cm icindeki en ic iki cagirana bagli."""

    def __init__(self, engine) -> None:
        import cProfile

        from sqlalchemy import event

        self.engine = engine
        self.prof = cProfile.Profile()
        self.by_site: dict[tuple[str, str, str], list[float]] = {}
        self._pending: list[tuple[float, tuple[str, str, str]]] = []
        self._before = lambda *a: self._on_before(*a)
        self._after = lambda *a: self._on_after(*a)
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)

    _files: dict[str, str | None] = {}

    @classmethod
    def _project_name(cls, path: str) -> str | None:
        """Dosya proje kokunde (E:/cm) ise (bu arac haric) kisa adi, degilse None. Onbellekli (resolve pahali)."""
        if path not in cls._files:
            try:
                resolved = Path(path).resolve()
                inside = resolved != SELF and str(resolved).lower().startswith(str(ROOT).lower())
            except OSError:
                inside = False
            cls._files[path] = resolved.name if inside else None
        return cls._files[path]

    @classmethod
    def _site(cls) -> tuple[str, str]:
        frame = sys._getframe(2)
        found: list[str] = []
        while frame is not None and len(found) < 2:
            name = cls._project_name(frame.f_code.co_filename)
            if name is not None:
                found.append(f"{name}:{frame.f_code.co_name}")
            frame = frame.f_back
        found += ["-"] * (2 - len(found))
        return found[0], found[1]

    def _on_before(self, conn, cursor, statement, parameters, context, executemany) -> None:
        inner, outer = self._site()
        self._pending.append((time.perf_counter(), (inner, outer, _kind(statement))))

    def _on_after(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if not self._pending:
            return
        t0, key = self._pending.pop()
        self.by_site.setdefault(key, []).append(time.perf_counter() - t0)

    def start(self) -> None:
        self.prof.enable()

    def stop(self) -> None:
        from sqlalchemy import event

        self.prof.disable()
        event.remove(self.engine, "before_cursor_execute", self._before)
        event.remove(self.engine, "after_cursor_execute", self._after)

    def render(self, label: str, elapsed: float, sql: dict) -> str:
        import io
        import pstats

        lines = [f"=== Hafta {label}: {elapsed:.2f} sn, SQL {sql['total']} ifade ({sql['rows']} satir) ==="]
        db_total = sum(sum(v) for v in self.by_site.values())
        lines.append(f"DB suresi (ifade calistirma, cursor): {db_total:.2f} sn")
        lines.append(f"{'DB sn':>7} {'adet':>6}  {'tur':<7} en ic cagiran  <-  bir ustu")
        ranked = sorted(self.by_site.items(), key=lambda kv: -sum(kv[1]))
        for (inner, outer, kind), durations in ranked[:30]:
            lines.append(f"{sum(durations):7.3f} {len(durations):6d}  {kind:<7} {inner}  <-  {outer}")
        buf = io.StringIO()
        pstats.Stats(self.prof, stream=buf).sort_stats("cumulative").print_stats(45)
        lines.append("\n--- cProfile (kumulatif, ilk 45) ---")
        lines.append(buf.getvalue())
        buf = io.StringIO()
        pstats.Stats(self.prof, stream=buf).sort_stats("tottime").print_stats(30)
        lines.append("\n--- cProfile (kendi suresi, ilk 30) ---")
        lines.append(buf.getvalue())
        return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
