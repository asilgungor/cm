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

import reputation
import staff as staff_rules
from career_manager import CareerManager, SeasonNotFinished, WeekReport
from database import session_scope, wait_for_db
from finance import (
    BudgetError,
    format_money,
    max_shiftable_to_transfer,
    max_shiftable_to_wages,
    weekly_to_transfer,
)
from match_engine import print_match_report
from models import Fixture, LineupStatus, Position, StaffRole, Team
from tactics import FORMATIONS, MAX_BENCH, arrange_slots, player_power
from transfers import ROLE_LABELS, ContractOffer, NegotiationStatus, TransferError

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
    rep = cm.manager_reputation
    lines.append(f"  Menajer tanınırlığı: {rep:.1f}/20 ({reputation.label(rep)})")
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
        summary = cm.wage_summary(team)
        warn = "  [MAAŞ BÜTÇESİ AŞILDI]" if summary.overspending else ""
        lines.append(
            f"  Transfer kasası: {format_money(team.transfer_budget)}  ·  "
            f"maaş havuzu: {format_money(team.wage_budget)}/hafta (%{summary.usage_pct:.0f} dolu){warn}"
        )
    lines.append(LINE)
    return "\n".join(lines)


def render_squad(cm: CareerManager, team: Team) -> str:
    week = cm.current_week
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    players = sorted(team.players, key=lambda p: (order[p.position], -p.overall_rating))
    labels = {LineupStatus.XI: "İlk 11", LineupStatus.BENCH: "Kulübe", LineupStatus.OUT: "Kadro dışı"}
    lines = [f"  KADRO — {team.name}  (ort. güç {team.squad_rating}, diziliş {team.formation})",
             f"  {'Mevki':<6}{'Oyuncu':<22}{'Yaş':>4}{'OVR':>5}{'Form':>6}{'Moral':>7}{'Not':>6}{'Ritim':>7}   Durum"]
    for p in players:
        avg = f"{p.average_rating:.2f}" if p.average_rating is not None else "-"
        idle = "—" if p.weeks_since_match == 0 else f"{p.weeks_since_match} hf"
        status = p.unavailability_reason(week) or labels[p.lineup_status]
        lines.append(
            f"  {p.position.value:<6}{p.name:<22}{p.age:>4}{p.overall_rating:>5}{p.form:>6}{p.morale:>7}"
            f"{avg:>6}{idle:>7}   {status}"
        )
    return "\n".join(lines)


def render_tactics(cm: CareerManager, team: Team) -> str:
    """Kadro ve taktik ekrani: diziliş slotları, kulübe, kadro dışı ve uyarılar."""
    week = cm.current_week
    xi, bench, out = cm.lineup_of(team)
    by_id = {p.id: p for p in team.players}
    lines = [
        THIN,
        f"  KADRO VE TAKTİK — {team.name}   Diziliş: {team.formation}   (hafta {week})",
        THIN,
        "  İLK 11",
        f"  {'#':>3} {'Slot':<5}{'Oyuncu':<22}{'Mv':<4}{'OVR':>4}{'Form':>6}{'Moral':>7}{'Güç':>7}",
    ]
    for i, (role, player) in enumerate(arrange_slots(team.players, team.formation, xi), start=1):
        if player is None:
            lines.append(f"  {i:>3} {role.value:<5}{'— boş —':<22}")
            continue
        flag = "" if player.position is role else "  (mevki dışı)"
        lines.append(
            f"  {i:>3} {role.value:<5}{player.name:<22}{player.position.value:<4}"
            f"{player.overall_rating:>4}{player.form:>6}{player.morale:>7}{player_power(player):>7.1f}{flag}"
        )
    if not xi:
        lines.append("       (ilk 11 belirlenmedi — maçta asistan kuracak)")

    lines.append(f"\n  YEDEK KULÜBESİ ({len(bench)}/{MAX_BENCH})")
    lines.append("    " + (", ".join(
        f"{by_id[i].name} ({by_id[i].position.value} {by_id[i].overall_rating})" for i in bench) or "—"))

    if out:
        lines.append("\n  KADRO DIŞI")
        lines.append("    " + ", ".join(
            f"{by_id[i].name} ({by_id[i].position.value} {by_id[i].overall_rating})" for i in out))

    blocked = [p for p in team.players if not p.is_available(week)]
    if blocked:
        lines.append("\n  SEÇİLEMEZ (sakat/cezalı)")
        for p in blocked:
            lines.append(f"    {p.name:<22}{p.position.value:<4}{p.overall_rating:>4}   {p.unavailability_reason(week)}")

    check = cm.lineup_check(team)
    for err in check.errors:
        lines.append(f"  [HATA] {err}")
    for warn in check.warnings:
        lines.append(f"  [UYARI] {warn}")
    if check.ok and not check.warnings:
        lines.append("  Kadro geçerli.")
    return "\n".join(lines)


def render_selectable(cm: CareerManager, team: Team) -> tuple[str, list]:
    """Seçilebilir (sakat/cezalı olmayan) oyuncuları numaralı listeler."""
    week = cm.current_week
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    pool = sorted(
        (p for p in team.players if p.is_available(week)),
        key=lambda p: (order[p.position], -player_power(p)),
    )
    xi, bench, _ = cm.lineup_of(team)
    lines = [f"  {'No':>3} {'Mv':<4}{'Oyuncu':<22}{'OVR':>4}{'Form':>6}{'Moral':>7}{'Güç':>7}  Durum"]
    for i, p in enumerate(pool, start=1):
        where = "İLK 11" if p.id in xi else ("Kulübe" if p.id in bench else "Kadro dışı")
        lines.append(
            f"  {i:>3} {p.position.value:<4}{p.name:<22}{p.overall_rating:>4}"
            f"{p.form:>6}{p.morale:>7}{player_power(p):>7.1f}  {where}"
        )
    return "\n".join(lines), pool


def parse_money(raw: str) -> int | None:
    """'5M', '500K', '1.2m', '250000' -> tam sayi EUR. Gecersizse None."""
    text = raw.strip().lower().replace(" ", "").replace(",", "").replace(".", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    elif text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("-+")
    if not text.isdigit():
        return None
    return sign * int(text) * multiplier


def render_finance(cm: CareerManager, team: Team) -> str:
    """Finans ekrani: iki kalemli butce, maas dagilimi, kaydirma sinirlari."""
    s = cm.wage_summary(team)
    bar_len = 30
    filled = min(bar_len, int(bar_len * s.usage_pct / 100)) if team.wage_budget else 0
    bar = "█" * filled + "·" * (bar_len - filled)
    lines = [
        THIN,
        f"  FİNANS — {team.name}",
        THIN,
        f"  Transfer bütçesi (bonservis) : {format_money(team.transfer_budget):>14}",
        f"  Maaş havuzu (haftalık)       : {format_money(team.wage_budget):>14}",
        "",
        f"  Oyuncu maaşları  : {format_money(s.player_wages):>12} /hafta  ({len(team.players)} oyuncu)",
        f"  Personel maaşları: {format_money(s.staff_wages):>12} /hafta  ({len(team.staff)} kişi)",
        f"  TOPLAM YÜK       : {format_money(s.total):>12} /hafta",
        f"  Boş alan         : {format_money(s.free):>12} /hafta",
        f"  [{bar}] %{s.usage_pct:.0f}",
    ]
    if s.overspending:
        lines.append("  [UYARI] Maaş bütçesi aşılıyor — her hafta transfer kasandan düşecek.")
    lines += [
        "",
        f"  Kaydırma kuru: haftalık 1 EUR maaş alanı = {weekly_to_transfer(1)} EUR bonservis"
        f"  (örn. haftalık 10K açmak {format_money(weekly_to_transfer(10_000))} götürür)",
        "  Sınırlar:",
        f"    Transfer → Maaş : en çok {format_money(max_shiftable_to_wages(team.transfer_budget))}/hafta",
        f"    Maaş → Transfer : en çok {format_money(max_shiftable_to_transfer(team.wage_budget, team.wage_bill))}/hafta",
    ]
    return "\n".join(lines)


def render_staff(cm: CareerManager, team: Team) -> str:
    """Teknik heyet ekrani: roller, alt ozellikler ve oyuna etkileri."""
    lines = [THIN, f"  TEKNİK HEYET — {team.name}", THIN]
    if not team.staff:
        lines.append("  Kadroda personel yok.")
    for role in StaffRole:
        members = team.staff_by_role(role)
        limit = staff_rules.MAX_PER_ROLE[role]
        lines.append(f"  {staff_rules.ROLE_LABELS[role]} ({len(members)}/{limit})")
        if not members:
            lines.append("    —")
            continue
        for m in members:
            attrs = "  ".join(
                f"{staff_rules.ATTR_LABELS[a]} {getattr(m, a)}" for a in staff_rules.ROLE_ATTRIBUTES[role]
            )
            lines.append(f"    {m.name:<22} itibar {m.reputation:>3}  {format_money(m.wage)}/hf   {attrs}")

    physio = cm.physio_rating(team)
    coach_att = cm._staff_rating(team, StaffRole.COACH, "attacking")
    coach_def = cm._staff_rating(team, StaffRole.COACH, "defending")
    assistant = cm._staff_rating(team, StaffRole.ASSISTANT, "man_management")
    lines += [
        "",
        "  ETKİLER",
        f"    Sakatlık süresi çarpanı : ×{staff_rules.injury_multiplier(physio):.2f}"
        f"   (4 haftalık sakatlık → {staff_rules.apply_injury_multiplier(4, physio)} hafta)",
        f"    Gözlemci yanılma payı   : ±{cm.scout_margin(team)}",
        f"    Hücum formu çarpanı     : ×{staff_rules.training_multiplier(coach_att):.2f}",
        f"    Savunma formu çarpanı   : ×{staff_rules.training_multiplier(coach_def):.2f}",
        f"    Moral çarpanı (asistan) : ×{staff_rules.training_multiplier(assistant):.2f}",
    ]
    return "\n".join(lines)


def render_free_staff(cm: CareerManager, role: StaffRole | None = None) -> tuple[str, list]:
    """Bostaki personel listesi (numarali)."""
    pool = cm.free_agent_staff(role)
    lines = [f"  {'No':>3} {'İsim':<22}{'Rol':<18}{'İtb':>4}{'Maaş/hf':>12}   Öne çıkan"]
    for i, m in enumerate(pool, start=1):
        attrs = "  ".join(
            f"{staff_rules.ATTR_LABELS[a]} {getattr(m, a)}" for a in staff_rules.ROLE_ATTRIBUTES[m.role]
        )
        lines.append(
            f"  {i:>3} {m.name:<22}{staff_rules.ROLE_LABELS[m.role]:<18}{m.reputation:>4}"
            f"{format_money(m.wage):>12}   {attrs}"
        )
    return "\n".join(lines), pool


def render_scouted(cm: CareerManager, buyer: Team, player) -> str:
    """Gözlemci süzgecinden geçmiş oyuncu profili."""
    r = cm.scouted_report(buyer, player)
    margin = r["margin"]
    header = "kesin bilgi (kendi oyuncun)" if margin == 0 else f"gözlemci tahmini, ±{margin} yanılma payı"
    lines = [
        f"  {player.name} — {player.position.value}, {player.age} yaş, {player.team.name}",
        f"  ({header})",
        f"    Genel     : {r['overall_rating']}",
        f"    Hız {r['pace']}   Şut {r['shooting']}   Pas {r['passing']}",
        f"    Defans {r['defending']}   Dribling {r['dribbling']}   Kalecilik {r['goalkeeping']}",
        f"    Piyasa değeri : {format_money(r['market_value'].low)}"
        + ("" if r["market_value"].exact else f" - {format_money(r['market_value'].high)}"),
        f"    Sözleşme: {player.contract_years} yıl · rol {ROLE_LABELS[player.squad_role]}",
    ]
    if margin == 0:
        lines.append(f"    Maaş: {format_money(player.current_wage)}/hafta")
    return "\n".join(lines)


def render_transfer_list(cm: CareerManager, buyer: Team, players: list) -> str:
    """Arama sonuclari: gözlemci süzgecinden geçmiş özet satırlar."""
    margin = cm.scout_margin(buyer)
    lines = [f"  (gözlemci yanılma payı ±{margin})",
             f"  {'No':>3} {'Oyuncu':<22}{'Mv':<4}{'Yaş':>4}{'Takım':<20}{'Genel':>9}{'Değer':>18}"]
    for i, p in enumerate(players, start=1):
        r = cm.scouted_report(buyer, p)
        value = r["market_value"]
        value_txt = format_money(value.low) if value.exact else f"{format_money(value.low)}-{format_money(value.high)}"
        lines.append(
            f"  {i:>3} {p.name:<22}{p.position.value:<4}{p.age:>4}{p.team.name:<20}"
            f"{str(r['overall_rating']):>9}{value_txt:>18}"
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
    "  [5] Gol krallığı            [6] Takım değiştir    [7] Yeni sezon    [0] Çıkış\n"
    "  [T] Kadro ve Taktik   [F] Finans ve Bütçe   [R] Transfer Pazarı   [S] Teknik Heyet"
)

TACTICS_MENU = (
    "  [1] Asistana bırak (en iyi 11)   [2] Diziliş değiştir   [3] Oyuncu ilk 11'e al\n"
    "  [4] Oyuncuyu kulübeye al         [5] Oyuncuyu kadro dışı bırak   [6] Kadroyu temizle\n"
    "  [0] Geri"
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


def _pick_player(cm: CareerManager, team: Team, prompt: str):
    listing, pool = render_selectable(cm, team)
    print(listing)
    raw = ask(prompt)
    if raw.isdigit() and 1 <= int(raw) <= len(pool):
        return pool[int(raw) - 1]
    if raw != "0":
        print("  Geçersiz seçim.")
    return None


def tactics_screen(seed: int | None) -> None:
    """Kadro ve Taktik Yönetimi ekranı. Her işlem kendi transaction'ında kaydedilir."""
    while True:
        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team
            if team is None:
                print("  Önce bir takım seç.")
                return
            print()
            print(render_tactics(cm, team))
            print()
            print(TACTICS_MENU)
        choice = ask("  > ").upper()

        if choice == "0":
            return

        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team

            if choice == "1":
                xi = cm.auto_lineup(team)
                print(f"  Asistan {len(xi)} kişilik ilk 11'i ve kulübeyi kurdu.")

            elif choice == "2":
                names = list(FORMATIONS)
                print("  " + "   ".join(f"[{i}] {n}" for i, n in enumerate(names, start=1)))
                raw = ask("  Diziliş: ")
                if raw.isdigit() and 1 <= int(raw) <= len(names):
                    check = cm.set_formation(team, names[int(raw) - 1])
                    print(f"  Diziliş: {team.formation}")
                    for err in check.errors:
                        print(f"  [HATA] {err}")
                else:
                    print("  Geçersiz seçim.")

            elif choice in {"3", "4", "5"}:
                labels = {"3": "ilk 11'e alınacak", "4": "kulübeye alınacak", "5": "kadro dışı bırakılacak"}
                player = _pick_player(cm, team, f"  {labels[choice]} oyuncu no (0 = vazgeç): ")
                if player is None:
                    continue
                xi, bench, _ = cm.lineup_of(team)
                xi, bench = dict(xi), set(bench)
                xi.pop(player.id, None)
                bench.discard(player.id)

                if choice == "3":
                    roles = list(Position)
                    print("  " + "   ".join(f"[{i}] {r.value}" for i, r in enumerate(roles, start=1))
                          + f"   (öz mevki: {player.position.value})")
                    raw = ask("  Hangi slotta oynasın? (boş = kendi mevkisi): ")
                    role = roles[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(roles) else player.position
                    xi[player.id] = role
                elif choice == "4":
                    bench.add(player.id)

                check = cm.set_lineup(team, xi, bench)
                if check.ok:
                    print(f"  {player.name}: {labels[choice]} olarak güncellendi.")
                else:
                    print("  Değişiklik uygulanmadı:")
                    for err in check.errors:
                        print(f"  [HATA] {err}")
                for warn in check.warnings:
                    print(f"  [UYARI] {warn}")

            elif choice == "6":
                cm.clear_lineup(team)
                print("  Kadro temizlendi; maçta asistan kuracak.")
            else:
                print("  Geçersiz seçim.")


FINANCE_MENU = (
    "  [1] Transfer → Maaş bütçesine aktar    [2] Maaş → Transfer bütçesine aktar\n"
    "  [0] Geri"
)

STAFF_MENU = "  [1] Personel işe al    [2] Personel gönder    [0] Geri"


def finance_screen(seed: int | None) -> None:
    """Finans ve Bütçe Ayarla ekranı."""
    while True:
        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team
            if team is None:
                print("  Önce bir takım seç.")
                return
            print()
            print(render_finance(cm, team))
            print()
            print(FINANCE_MENU)
        choice = ask("  > ")
        if choice == "0":
            return
        if choice not in {"1", "2"}:
            print("  Geçersiz seçim.")
            continue

        raw = ask("  Haftalık tutar (örn. 25K veya 40000): ")
        weekly = parse_money(raw)
        if weekly is None or weekly <= 0:
            print("  Geçersiz tutar.")
            continue
        delta = weekly if choice == "1" else -weekly
        cost = weekly_to_transfer(weekly)
        direction = "maaş havuzuna" if choice == "1" else "transfer kasasına"
        print(f"  {format_money(weekly)}/hafta {direction} → bonservis karşılığı {format_money(cost)}")

        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            try:
                transfer, wage = cm.shift_budget(cm.user_team, delta)
                print(f"  Tamam. Transfer: {format_money(transfer)} · Maaş havuzu: {format_money(wage)}/hafta")
            except BudgetError as exc:
                print(f"  [HATA] {exc}")


def staff_screen(seed: int | None) -> None:
    """Teknik Heyet ekranı: görüntüle, işe al, gönder."""
    while True:
        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team
            if team is None:
                print("  Önce bir takım seç.")
                return
            print()
            print(render_staff(cm, team))
            print()
            print(STAFF_MENU)
        choice = ask("  > ")
        if choice == "0":
            return

        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team

            if choice == "1":
                listing, pool = render_free_staff(cm)
                if not pool:
                    print("  Boşta personel yok.")
                    continue
                print(f"\n  BOŞTAKİ PERSONEL   (maaş havuzunda {format_money(team.free_wage)}/hafta boş)")
                print(listing)
                raw = ask("  İşe alınacak no (0 = vazgeç): ")
                if not (raw.isdigit() and 1 <= int(raw) <= len(pool)):
                    continue
                member = pool[int(raw) - 1]
                try:
                    cm.hire_staff(team, member)
                    print(f"  {member.name} kadroya katıldı ({format_money(member.wage)}/hafta).")
                except TransferError as exc:
                    print(f"  [HATA] {exc}")

            elif choice == "2":
                if not team.staff:
                    print("  Kadroda personel yok.")
                    continue
                print()
                for i, m in enumerate(team.staff, start=1):
                    print(f"  {i:>3} {m.name:<22}{staff_rules.ROLE_LABELS[m.role]:<18}{format_money(m.wage)}/hf")
                raw = ask("  Gönderilecek no (0 = vazgeç): ")
                if not (raw.isdigit() and 1 <= int(raw) <= len(team.staff)):
                    continue
                member = team.staff[int(raw) - 1]
                try:
                    cm.release_staff(team, member)
                    print(f"  {member.name} gönderildi; maaş havuzunda {format_money(member.wage)}/hafta boşaldı.")
                except TransferError as exc:
                    print(f"  [HATA] {exc}")
            else:
                print("  Geçersiz seçim.")


def negotiate(cm: CareerManager, team: Team, player, fee: int) -> None:
    """2. Aşama: sözleşme masası. Menajer tur tur pazarlık eder."""
    negotiation = cm.open_negotiation(team, player, fee)
    demand = negotiation.demand
    print()
    print(THIN)
    print(f"  SÖZLEŞME MASASI — {player.name}")
    print(THIN)
    interest = negotiation.interest
    print(f"  Prestij (kulüp %40 + menajer %30): {interest.prestige:.1f}  ·  "
          f"oyuncunun beklentisi: {interest.required_prestige:.1f}")
    if not negotiation.open:
        print(f"\n  {negotiation.opening_message}")
        print("  Bonservis ödenmedi, transfer iptal.")
        return
    print(f"  Bonservis anlaşıldı: {format_money(fee)}")
    print(f"  Oyuncunun talebi   : {demand.describe()}")
    print(f"  Maaş havuzunda boş : {format_money(team.free_wage)}/hafta")

    while negotiation.open:
        current = negotiation.demand
        print(f"\n  Kalan pazarlık hakkı: {negotiation.rounds_left}")
        print(f"  [1] Talebi kabul et ({current.describe()})   [2] Karşı teklif   [0] Vazgeç")
        choice = ask("  > ")
        if choice == "0":
            print("  Görüşme sonlandırıldı, transfer iptal.")
            return
        if choice == "1":
            offer = current
        elif choice == "2":
            wage = parse_money(ask(f"  Haftalık maaş (şu an {current.wage:,}): "))
            if wage is None or wage < 0:
                print("  Geçersiz tutar.")
                continue
            years_raw = ask(f"  Sözleşme süresi yıl (şu an {current.years}): ")
            years = int(years_raw) if years_raw.isdigit() else current.years
            roles = list(ROLE_LABELS)
            print("  " + "   ".join(f"[{i}] {ROLE_LABELS[r]}" for i, r in enumerate(roles, start=1)))
            role_raw = ask(f"  Kadro rolü (şu an {ROLE_LABELS[current.role]}): ")
            role = roles[int(role_raw) - 1] if role_raw.isdigit() and 1 <= int(role_raw) <= len(roles) else current.role
            offer = ContractOffer(wage=wage, years=years, role=role)
        else:
            print("  Geçersiz seçim.")
            continue

        score = negotiation.persuasion(offer)
        response = negotiation.respond(offer)
        print(f"\n  İkna skoru: {score:.1f} / gereken {negotiation.required_persuasion:.1f}")
        print(f"  {response.message}")
        for c in response.complaints:
            print(f"    · {c}")

        if response.status is NegotiationStatus.ACCEPTED:
            try:
                news = cm.complete_transfer(team, player, fee, offer)
            except TransferError as exc:
                print(f"  [HATA] {exc}")
                print("  Transfer tamamlanamadı. Finans ekranından bütçe kaydırıp tekrar dene.")
                return
            print(f"\n  TRANSFER TAMAM: {news.describe()}")
            print(f"  Yeni transfer kasası: {format_money(team.transfer_budget)} · "
                  f"boş maaş alanı: {format_money(team.free_wage)}/hafta")
            return
        if response.status is NegotiationStatus.WALKED_AWAY:
            print("  Transfer iptal oldu.")
            return


def transfer_screen(seed: int | None) -> None:
    """Transfer Pazarı: oyuncu ara, profil gör, bonservis teklifi yap."""
    while True:
        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team
            if team is None:
                print("  Önce bir takım seç.")
                return
            print()
            print(THIN)
            print(f"  TRANSFER PAZARI — {team.name}")
            print(f"  Transfer kasası: {format_money(team.transfer_budget)}  ·  "
                  f"boş maaş alanı: {format_money(team.free_wage)}/hafta")
            print(THIN)
            print("  [1] Oyuncu ara    [2] En iyi hedefler    [0] Geri")
        choice = ask("  > ")
        if choice == "0":
            return
        if choice not in {"1", "2"}:
            print("  Geçersiz seçim.")
            continue

        query = ask("  İsim (boş = tümü): ") if choice == "1" else ""

        # Pazarlik boyunca tek oturum: ORM nesneleri canli kalmali
        with session_scope() as db:
            cm = CareerManager(db, seed=seed)
            team = cm.user_team
            players = cm.transfer_targets(team, query)
            if not players:
                print("  Sonuç yok.")
                continue
            print()
            print(render_transfer_list(cm, team, players))
            raw = ask("  İncelenecek no (0 = geri): ")
            if not (raw.isdigit() and 1 <= int(raw) <= len(players)):
                continue
            player = players[int(raw) - 1]
            print()
            print(render_scouted(cm, team, player))

            if ask("\n  Bonservis teklifi yapılsın mı? (e/h): ").lower() not in {"e", "evet"}:
                continue
            fee = parse_money(ask("  Teklif (örn. 12M): "))
            if fee is None or fee < 0:
                print("  Geçersiz tutar.")
                continue
            try:
                decision = cm.offer_fee(team, player, fee)
            except TransferError as exc:
                print(f"  [HATA] {exc}")
                continue
            print(f"\n  {player.team.name}: {decision.reason}")
            if not decision.accepted:
                print("  (Daha yüksek bir teklifle tekrar deneyebilirsin.)")
                continue
            negotiate(cm, team, player, fee)


def play_one_week(seed: int | None, commentary: bool) -> bool:
    """Bir hafta oynatir, raporu basar. Sezon bittiyse False doner."""
    with session_scope() as db:
        cm = CareerManager(db, seed=seed)
        highlight = cm.state.user_team_id
        report = cm.play_week()
        print(render_week_report(cm, report, highlight))
        for note in report.lineup_notes:
            print(f"  [Asistan] {note}")
        for news in report.transfers:
            print(f"  [Transfer] {news.describe()}")
        if report.manager_reputation is not None:
            before, after = report.manager_reputation
            sign = "+" if after >= before else ""
            print(f"  [Menajer] Tanınırlık {before:.2f} → {after:.2f} ({sign}{after - before:.2f}) · "
                  f"{reputation.label(after)}")
        if report.season_reputation_delta is not None:
            print(f"  [Menajer] Sezon sonu etkisi: {report.season_reputation_delta:+.1f}")
        if report.finance_note:
            print(f"  [Finans] {report.finance_note}")
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
        choice = ask("  > ").upper()

        if choice == "0":
            print("  Görüşürüz!")
            return
        if choice == "T":
            tactics_screen(seed)
        elif choice == "F":
            finance_screen(seed)
        elif choice == "R":
            transfer_screen(seed)
        elif choice == "S":
            staff_screen(seed)
        elif choice == "1":
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
    parser.add_argument("--formation", choices=list(FORMATIONS), help="Takımın dizilişini ayarla")
    parser.add_argument("--auto-lineup", action="store_true", help="Asistan en iyi 11'i kursun")
    parser.add_argument("--show-tactics", action="store_true", help="Kadro ve taktik ekranını bas ve çık")
    parser.add_argument("--show-finance", action="store_true", help="Finans ekranını bas ve çık")
    parser.add_argument("--show-staff", action="store_true", help="Teknik heyet ekranını bas ve çık")
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
        if args.formation:
            if cm.user_team is None:
                print("[main] Önce --team ile takım seç.")
                return 1
            cm.set_formation(cm.user_team, args.formation)
            print(f"[main] Diziliş: {args.formation}")
        if args.auto_lineup:
            if cm.user_team is None:
                print("[main] Önce --team ile takım seç.")
                return 1
            cm.auto_lineup(cm.user_team)
            print(f"[main] Asistan ilk 11'i kurdu ({cm.user_team.name}).")
        if args.new_season:
            try:
                print(f"[main] Yeni sezon başladı: Sezon {cm.start_new_season()}")
            except SeasonNotFinished as exc:
                print(f"[main] {exc}")
                return 1
        if args.show_tactics:
            if cm.user_team is None:
                print("[main] Önce --team ile takım seç.")
                return 1
            print(render_tactics(cm, cm.user_team))
            return 0
        if args.show_finance or args.show_staff:
            if cm.user_team is None:
                print("[main] Önce --team ile takım seç.")
                return 1
            if args.show_finance:
                print(render_finance(cm, cm.user_team))
            if args.show_staff:
                print(render_staff(cm, cm.user_team))
            return 0

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
