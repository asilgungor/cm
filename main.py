"""
main.py
=======
CLI arayuzu (VIEW katmani). Hicbir oyun kurali icermez: CareerManager'dan
veri alir, formatlar, kullanicidan komut alir. 2D arayuz bu dosyanin
karsiligi olacak; render_* fonksiyonlari string dondurdugu icin oradan da
dogrudan kullanilabilir.

Calistirma:
    python main.py                          # etkilesimli: takim sec, hafta oyna
    python main.py --team Galatasaray       # takimi komut satirindan sec
    python main.py --auto 6 --seed 7        # 6 haftayi sormadan oynat (test/demo)
    python main.py --new-season             # sezon bittiyse yenisini baslat
"""

from __future__ import annotations

import argparse
import sys

from career_manager import CareerManager, SeasonNotFinished, WeekReport
from database import session_scope, wait_for_db
from match_engine import print_match_report
from models import Fixture, Position, Team

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LINE = "=" * 78
THIN = "-" * 78


# ===========================================================================
# RENDER — saf string ureticiler
# ===========================================================================

def render_standings(cm: CareerManager, league_id: int, highlight_id: int | None = None) -> str:
    table = cm.standings(league_id)
    league = table[0].league.name if table else "?"
    lines = [f"  PUAN DURUMU — {league}",
             f"  {'#':>2}  {'Takım':<20}{'O':>3}{'G':>3}{'B':>3}{'M':>3}{'A':>4}{'Y':>4}{'Av':>5}{'P':>4}   Form"]
    for i, t in enumerate(table, start=1):
        mark = " ◄" if t.id == highlight_id else ""
        lines.append(
            f"  {i:>2}  {t.name:<20}{t.played:>3}{t.won:>3}{t.drawn:>3}{t.lost:>3}"
            f"{t.goals_for:>4}{t.goals_against:>4}{t.goal_difference:>+5}{t.points:>4}   "
            f"{cm.team_form(t.id) or '-'}{mark}"
        )
    return "\n".join(lines)


def render_header(cm: CareerManager) -> str:
    total = cm.total_weeks()
    week = cm.current_week
    lines = [LINE, f"  CM · Sezon {cm.season} · Hafta {min(week, total)} / {total}"
             + ("  · SEZON BİTTİ" if cm.season_finished else "")]
    team = cm.user_team
    if team is None:
        lines.append("  Takım seçilmedi.")
    else:
        pos = cm.position_of(team)
        form = cm.team_form(team.id) or "-"
        lines.append(f"  Takımın: {team.name} ({team.league.name}) — {pos}. sıra, {team.points} puan, form: {form}")
        nxt = cm.next_fixture(team.id)
        if nxt is None:
            lines.append("  Yaklaşan maç: yok (sezon tamamlandı)")
        else:
            home = nxt.home_team_id == team.id
            opp = nxt.away_team if home else nxt.home_team
            opp_form = cm.team_form(opp.id) or "-"
            where = "ev" if home else "deplasman"
            lines.append(f"  Yaklaşan maç: {nxt.week}. hafta  {team.name} vs {opp.name} ({where})  · rakip formu: {opp_form}")
        injured = [p for p in team.players if p.is_injured(week)]
        suspended = [p for p in team.players if p.is_suspended]
        inj = ", ".join(f"{p.name} ({p.injured_until_week}. hf)" for p in injured) or "—"
        sus = ", ".join(f"{p.name} ({p.suspended_matches} maç)" for p in suspended) or "—"
        lines.append(f"  Sakat: {inj}  ·  Cezalı: {sus}")
    lines.append(LINE)
    return "\n".join(lines)


def render_squad(cm: CareerManager, team: Team) -> str:
    week = cm.current_week
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    players = sorted(team.players, key=lambda p: (order[p.position], -p.overall_rating))
    lines = [f"  KADRO — {team.name}  (ort. güç {team.squad_rating})",
             f"  {'Mevki':<6}{'Oyuncu':<22}{'Yaş':>4}{'OVR':>5}{'Form':>6}{'Moral':>7}{'Not':>6}   Durum"]
    for p in players:
        avg = f"{p.average_rating:.2f}" if p.average_rating is not None else "-"
        status = p.unavailability_reason(week) or ""
        lines.append(
            f"  {p.position.value:<6}{p.name:<22}{p.age:>4}{p.overall_rating:>5}{p.form:>6}{p.morale:>7}{avg:>6}   {status}"
        )
    return "\n".join(lines)


def render_results(fixtures: list[Fixture], highlight_id: int | None = None) -> str:
    if not fixtures:
        return "  (bu hafta oynanmış maç yok)"
    lines: list[str] = []
    current_league = None
    for fx in fixtures:
        if fx.league_id != current_league:
            current_league = fx.league_id
            lines.append(f"  {fx.league.name}")
        mark = " ◄" if highlight_id in (fx.home_team_id, fx.away_team_id) else ""
        lines.append(f"    {fx.home_team.name:>20} {fx.home_score} - {fx.away_score} {fx.away_team.name:<20}{mark}")
    return "\n".join(lines)


def render_week_report(cm: CareerManager, report: WeekReport, highlight_id: int | None) -> str:
    if not report.played_any:
        return "  Oynanacak maç yok — sezon tamamlandı."
    lines = [THIN, f"  Sezon {report.season}, {report.week}. hafta sonuçları", THIN]
    current_league = None
    for fx, result in report.results:
        if fx.league_id != current_league:
            current_league = fx.league_id
            lines.append(f"  {fx.league.name}")
        h_scorers = ", ".join(f"{n} {g}" if g > 1 else n for n, g in result.scorers(result.home))
        a_scorers = ", ".join(f"{n} {g}" if g > 1 else n for n, g in result.scorers(result.away))
        scorers = f"   ({h_scorers or '-'} | {a_scorers or '-'})" if result.home_score + result.away_score else ""
        mark = " ◄" if highlight_id in (fx.home_team_id, fx.away_team_id) else ""
        lines.append(f"    {result.home.name:>20} {result.home_score} - {result.away_score} {result.away.name:<20}{mark}{scorers}")
    if report.injuries:
        lines.append("  Sakatlıklar: " + "; ".join(f"{n.player_name} ({n.team_name}) — {n.detail}" for n in report.injuries))
    if report.suspensions:
        lines.append("  Cezalar: " + "; ".join(f"{n.player_name} ({n.team_name}) — {n.detail}" for n in report.suspensions))
    if report.season_finished:
        lines.append(THIN)
        lines.append("  SEZON TAMAMLANDI! Şampiyonlar:")
        for league in cm.leagues():
            champ = cm.champion(league.id)
            if champ:
                lines.append(f"    {league.name}: {champ.name} ({champ.points} puan)")
    return "\n".join(lines)


def render_top_scorers(cm: CareerManager, league_id: int | None = None) -> str:
    rows = cm.top_scorers(league_id)
    if not rows:
        return "  Henüz gol atılmadı."
    lines = [f"  GOL KRALLIĞI{'' if league_id is None else ' — ' + rows[0].team.league.name}",
             f"  {'#':>2}  {'Oyuncu':<22}{'Takım':<20}{'Gol':>4}{'Ast':>4}{'Maç':>4}{'Not':>6}"]
    for i, r in enumerate(rows, start=1):
        lines.append(f"  {i:>2}  {r.player.name:<22}{r.team.name:<20}{r.goals:>4}{r.assists:>4}{r.appearances:>4}{r.avg_rating:>6.2f}")
    return "\n".join(lines)


def render_team_list(cm: CareerManager) -> tuple[str, list[Team]]:
    lines, teams = [], []
    for league in cm.leagues():
        lines.append(f"  {league.name} ({league.country})")
        for t in league.teams:
            teams.append(t)
            lines.append(f"    [{len(teams):>2}] {t.name:<20} itibar {t.reputation}  kadro {t.squad_rating}")
    return "\n".join(lines), teams


# ===========================================================================
# KONTROL AKISI
# ===========================================================================

MENU = (
    "  [1] Sonraki haftayı oyna    [2] Puan durumları    [3] Kadrom    [4] Son sonuçlar\n"
    "  [5] Gol krallığı            [6] Takım değiştir    [7] Yeni sezon    [0] Çıkış"
)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "0"


def choose_team(cm: CareerManager) -> Team | None:
    listing, teams = render_team_list(cm)
    print("\n  Hangi takımı yöneteceksin?\n" + listing)
    while True:
        raw = ask("  Takım numarası (0 = vazgeç): ")
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(teams):
            team = teams[int(raw) - 1]
            cm.set_user_team(team)
            print(f"  Seçildi: {team.name}")
            return team
        print("  Geçersiz seçim.")


def play_one_week(seed: int | None, commentary: bool) -> bool:
    """Bir hafta oynatir, raporu basar. Sezon bittiyse False doner."""
    with session_scope() as db:
        cm = CareerManager(db, seed=seed)
        highlight = cm.state.user_team_id
        report = cm.play_week()
        print(render_week_report(cm, report, highlight))
        if commentary and report.user_result is not None:
            print()
            print_match_report(report.user_result, show_lineups=False)
        return report.played_any and not report.season_finished


def interactive_loop(seed: int | None, commentary: bool) -> None:
    while True:
        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            if cm.user_team is None:
                choose_team(cm)
            print()
            print(render_header(cm))
            if cm.user_team is not None:
                print(render_standings(cm, cm.user_team.league_id, cm.user_team.id))
            print()
            print(MENU)
        choice = ask("  > ")

        if choice == "0":
            print("  Görüşürüz!")
            return
        if choice == "1":
            play_one_week(seed, commentary)
        elif choice == "2":
            with session_scope() as db:
                cm = CareerManager(db, seed=seed)
                hl = cm.state.user_team_id
                for league in cm.leagues():
                    print()
                    print(render_standings(cm, league.id, hl))
        elif choice == "3":
            with session_scope() as db:
                cm = CareerManager(db, seed=seed)
                if cm.user_team:
                    print()
                    print(render_squad(cm, cm.user_team))
        elif choice == "4":
            with session_scope() as db:
                cm = CareerManager(db, seed=seed)
                week = cm.last_played_week()
                print()
                if week is None:
                    print("  Henüz maç oynanmadı.")
                else:
                    print(f"  {week}. hafta sonuçları")
                    print(render_results(cm.results_for_week(week), cm.state.user_team_id))
        elif choice == "5":
            with session_scope() as db:
                cm = CareerManager(db, seed=seed)
                print()
                print(render_top_scorers(cm))
        elif choice == "6":
            with session_scope() as db:
                choose_team(CareerManager(db, seed=seed))
        elif choice == "7":
            with session_scope() as db:
                cm = CareerManager(db, seed=seed)
                try:
                    season = cm.start_new_season()
                    print(f"  Yeni sezon başladı: Sezon {season}")
                except SeasonNotFinished as exc:
                    print(f"  {exc}")
        else:
            print("  Geçersiz seçim.")


def main() -> int:
    parser = argparse.ArgumentParser(description="CM — kariyer modu CLI")
    parser.add_argument("--team", help="Yönetilecek takım adı (örn. Galatasaray)")
    parser.add_argument("--auto", type=int, metavar="N", help="N haftayı sormadan oynat ve çık")
    parser.add_argument("--seed", type=int, default=None, help="Tekrar üretilebilir sonuçlar için tohum")
    parser.add_argument("--no-commentary", action="store_true", help="Kendi maçının spiker akışını basma")
    parser.add_argument("--new-season", action="store_true", help="Sezon bittiyse yeni sezon başlat")
    args = parser.parse_args()

    if not wait_for_db(retries=3, delay=1.0, verbose=False):
        print("[main] Veritabanına bağlanılamadı. 'docker compose up -d' çalıştı mı?")
        return 1

    with session_scope() as db:
        cm = CareerManager(db, seed=args.seed)
        if args.team:
            team = cm.find_team(args.team)
            if team is None:
                print(f"[main] Takım bulunamadı: {args.team}")
                return 1
            cm.set_user_team(team)
        if args.new_season:
            try:
                print(f"[main] Yeni sezon başladı: Sezon {cm.start_new_season()}")
            except SeasonNotFinished as exc:
                print(f"[main] {exc}")
                return 1

    commentary = not args.no_commentary

    if args.auto:
        for _ in range(args.auto):
            if not play_one_week(args.seed, commentary):
                break
        with session_scope() as db:
            cm = CareerManager(db, seed=args.seed)
            print()
            print(render_header(cm))
            for league in cm.leagues():
                print(render_standings(cm, league.id, cm.state.user_team_id))
                print()
            print(render_top_scorers(cm))
        return 0

    interactive_loop(args.seed, commentary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
