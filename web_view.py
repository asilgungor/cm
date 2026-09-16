"""
web_view.py
===========
Canli mac ekraninin HTML/CSS parcalari (6. Asama). SAF FONKSIYONLAR.

Streamlit'e bagimli degildir: web_app.py bu fonksiyonlarin urettigi HTML'i
ekrana basar. Bu ayrim sayesinde gorunum testleri Streamlit calistirmadan yazilir.

GUVENLIK: Oyuncu/kulup adlari dis kaynakli FM dosyalarindan gelebilir; HTML'e
yazilan her metin html.escape() ile kacirilir (bkz. test_web_view_escapes_html).

Eleme maclari (8. Asama): tabela uzatma oynandiysa "uzt." rozeti, seri penalti
basladiysa "(pen. 4-3)" gosterir; akista uzatma/penalti olaylari kendi simgesi ve
rengiyle listelenir.
"""

from __future__ import annotations

from html import escape

from match_feed import PHASE_FULL_TIME, PHASE_PRE_MATCH, Frame, MatchSummary, SideStats

CSS = """
<style>
.cm-board{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:1rem;
  padding:1.1rem 1.4rem;border-radius:16px;background:linear-gradient(135deg,#0b1f14,#123524);
  color:#f4f7f2;box-shadow:0 6px 24px rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.08)}
.cm-team{font-size:1.35rem;font-weight:700;letter-spacing:.2px;overflow-wrap:anywhere}
.cm-team.home{text-align:right}.cm-team.away{text-align:left}
.cm-score{font-size:2.9rem;font-weight:800;font-variant-numeric:tabular-nums;text-align:center;line-height:1}
.cm-clock{text-align:center;font-size:.95rem;opacity:.85;margin-top:.35rem;font-variant-numeric:tabular-nums}
.cm-phase{display:inline-block;margin-left:.4rem;padding:.05rem .5rem;border-radius:999px;
  background:rgba(255,255,255,.12);font-size:.8rem}
.cm-extra{text-align:center;margin-top:.3rem;font-size:.9rem;font-weight:700;font-variant-numeric:tabular-nums}
.cm-extra .aet{display:inline-block;padding:.02rem .45rem;border-radius:999px;background:rgba(255,255,255,.18);
  font-size:.75rem;letter-spacing:.3px}
.cm-extra .pens{color:#ffd60a;margin-left:.35rem}
.cm-flash-goal{animation:cmGoal 1.4s ease-out 1}
.cm-flash-red{animation:cmRed 1.2s ease-out 1}
@keyframes cmGoal{0%{box-shadow:0 0 0 0 rgba(255,214,10,.95);background:#3d6b1f}
  60%{box-shadow:0 0 0 22px rgba(255,214,10,0)}100%{box-shadow:0 6px 24px rgba(0,0,0,.25)}}
@keyframes cmRed{0%{box-shadow:0 0 0 0 rgba(229,57,53,.95);background:#5a1414}
  100%{box-shadow:0 6px 24px rgba(0,0,0,.25)}}
.cm-banner{margin:.7rem 0 0;padding:.6rem 1rem;border-radius:12px;font-weight:700;text-align:center}
.cm-banner.goal{background:#ffd60a;color:#1a1a1a;animation:cmPulse .9s ease-in-out 2}
.cm-banner.red{background:#e53935;color:#fff;animation:cmPulse .9s ease-in-out 2}
@keyframes cmPulse{50%{transform:scale(1.03)}}
.cm-feed{display:flex;flex-direction:column;gap:.45rem;max-height:520px;overflow:auto;padding-right:.25rem}
.cm-ev{display:grid;grid-template-columns:3.4rem 6.6rem 1fr;gap:.6rem;align-items:start;
  padding:.5rem .7rem;border-radius:10px;background:rgba(127,127,127,.08);border-left:4px solid transparent;
  font-size:.92rem}
.cm-ev .min{font-weight:700;font-variant-numeric:tabular-nums}
.cm-ev .tag{font-size:.72rem;font-weight:700;padding:.12rem .4rem;border-radius:6px;text-align:center;
  background:rgba(127,127,127,.18)}
.cm-ev.goal{border-left-color:#ffd60a;background:rgba(255,214,10,.14)}
.cm-ev.goal .tag{background:#ffd60a;color:#1a1a1a}
.cm-ev.red{border-left-color:#e53935;background:rgba(229,57,53,.12)}
.cm-ev.red .tag{background:#e53935;color:#fff}
.cm-ev.yellow{border-left-color:#fbc02d}.cm-ev.yellow .tag{background:#fbc02d;color:#1a1a1a}
.cm-ev.injury{border-left-color:#8e24aa}.cm-ev.injury .tag{background:#8e24aa;color:#fff}
.cm-ev.sub .tag{background:#1e88e5;color:#fff}
.cm-ev.whistle{background:rgba(127,127,127,.16);font-style:italic}
.cm-ev.pen_goal{border-left-color:#43a047;background:rgba(67,160,71,.12)}
.cm-ev.pen_goal .tag{background:#43a047;color:#fff}
.cm-ev.pen_miss{border-left-color:#e53935;background:rgba(229,57,53,.08)}
.cm-ev.pen_miss .tag{background:#6d4c41;color:#fff}
.cm-ev .ico{margin-right:.3rem}
.cm-ev.latest{outline:2px solid rgba(76,175,80,.55)}
.cm-scroll{max-width:100%;overflow-x:auto}
.cm-stats{width:100%;border-collapse:collapse;font-size:.93rem}
.cm-stats td{padding:.35rem .25rem;border-bottom:1px solid rgba(127,127,127,.18);font-variant-numeric:tabular-nums}
.cm-stats td.l{text-align:right;width:28%;font-weight:700}.cm-stats td.r{text-align:left;width:28%;font-weight:700}
.cm-stats td.c{text-align:center;opacity:.8}
.cm-bar{height:6px;border-radius:3px;background:rgba(127,127,127,.2);overflow:hidden;margin-top:.15rem}
.cm-bar > span{display:block;height:100%;background:#4caf50}
.cm-cond{display:flex;align-items:center;gap:.4rem;min-width:7.5rem}
.cm-cond .track{flex:1;height:8px;border-radius:4px;background:rgba(127,127,127,.22);overflow:hidden}
.cm-cond .fill{display:block;height:100%;border-radius:4px}
.cm-cond.good .fill{background:#43a047}.cm-cond.warn .fill{background:#fbc02d}.cm-cond.low .fill{background:#e53935}
.cm-cond .val{font-variant-numeric:tabular-nums;font-size:.82rem;width:2.6rem;text-align:right}
.cm-squad{width:100%;border-collapse:collapse;font-size:.88rem}
.cm-squad th{text-align:left;font-weight:600;opacity:.75;padding:.3rem .35rem;border-bottom:1px solid rgba(127,127,127,.3)}
.cm-squad td{padding:.28rem .35rem;border-bottom:1px solid rgba(127,127,127,.14);font-variant-numeric:tabular-nums}
.cm-squad tr.xi td:first-child{border-left:3px solid #43a047}
.cm-squad tr.bench td:first-child{border-left:3px solid #1e88e5}
.cm-squad tr.out td:first-child{border-left:3px solid transparent;opacity:.8}
.cm-badge{display:inline-block;padding:.05rem .45rem;border-radius:999px;font-size:.74rem;background:rgba(127,127,127,.2)}
.cm-badge.bad{background:#e53935;color:#fff}.cm-badge.xi{background:#43a047;color:#fff}
.cm-badge.bench{background:#1e88e5;color:#fff}
.cm-usage{height:14px;border-radius:7px;background:rgba(127,127,127,.2);overflow:hidden;margin:.25rem 0 .1rem}
.cm-usage > span{display:block;height:100%}
.cm-usage.ok > span{background:#43a047}.cm-usage.tight > span{background:#fbc02d}.cm-usage.over > span{background:#e53935}
.cm-log{display:flex;flex-direction:column;gap:.35rem}
.cm-log .m{padding:.45rem .7rem;border-radius:10px;background:rgba(127,127,127,.1);font-size:.9rem}
.cm-log .m.me{background:rgba(30,136,229,.14);border-left:3px solid #1e88e5}
.cm-log .m.him{background:rgba(67,160,71,.12);border-left:3px solid #43a047}
.cm-log .m.bad{background:rgba(229,57,53,.14);border-left:3px solid #e53935}
</style>
"""

STAT_ROWS: tuple[tuple[str, str], ...] = (
    ("goals", "Gol"),
    ("shots", "Şut"),
    ("on_target", "İsabetli şut"),
    ("yellow", "Sarı kart"),
    ("red", "Kırmızı kart"),
    ("injuries", "Sakatlık"),
    ("subs", "Değişiklik"),
)


# Yeni (eleme) olay turleri icin akis simgeleri; PENALTY_SHOOTOUT atis sonucuna gore
EVENT_ICONS: dict[str, str] = {
    "EXTRA_TIME_START": "⏱️",
    "EXTRA_TIME_HALF": "⏱️",
    "SHOOTOUT_START": "🥅",
}
KICK_ICONS: dict[str, str] = {"scored": "✅", "saved": "🧤", "missed": "❌"}


def _event_icon(frame: Frame) -> str:
    ev = frame.event
    if ev.type == "PENALTY_SHOOTOUT":
        return KICK_ICONS.get(ev.detail or "", "⚽")
    return EVENT_ICONS.get(ev.type, "")


def _extra_line(extra_time: bool, home_pens: int | None, away_pens: int | None) -> str:
    """Skorun altindaki 'uzt.' rozeti ve '(pen. 4-3)'. Ikisi de yoksa bos."""
    parts = []
    if extra_time:
        parts.append('<span class="aet">uzt.</span>')
    if home_pens is not None and away_pens is not None:
        parts.append(f'<span class="pens">(pen. {int(home_pens)}-{int(away_pens)})</span>')
    return f'<div class="cm-extra">{"".join(parts)}</div>' if parts else ""


def scoreboard_html(home: str, away: str, frame: Frame | None, flash: str | None = None,
                    summary: MatchSummary | None = None) -> str:
    """
    Skor tabelasi. flash: 'goal' / 'red' / None -> animasyon sinifi.
    frame yoksa summary (mac sonu ozeti) verilirse ondan beslenir.
    """
    if frame is not None:
        home_score, away_score = frame.home_score, frame.away_score
        clock, phase = frame.display_minute, frame.phase
        extra = _extra_line(frame.extra_time, frame.home_penalties, frame.away_penalties)
    elif summary is not None:
        home_score, away_score = summary.home_score, summary.away_score
        clock, phase = f"{summary.total_minutes}'", PHASE_FULL_TIME
        extra = _extra_line(summary.extra_time, summary.home_penalties, summary.away_penalties)
    else:
        home_score, away_score, clock, phase, extra = 0, 0, "0'", PHASE_PRE_MATCH, ""
    flash_cls = {"goal": " cm-flash-goal", "red": " cm-flash-red"}.get(flash or "", "")
    return (
        f'<div class="cm-board{flash_cls}">'
        f'<div class="cm-team home">{escape(home)}</div>'
        f'<div><div class="cm-score">{home_score} - {away_score}</div>{extra}'
        f'<div class="cm-clock">{escape(clock)}<span class="cm-phase">{escape(phase)}</span></div></div>'
        f'<div class="cm-team away">{escape(away)}</div>'
        f"</div>"
    )


def banner_html(frame: Frame | None) -> str:
    """Gol / kirmizi kart aninda tabelanin altinda parlayan uyari."""
    if frame is None or frame.event.highlight not in {"goal", "red"}:
        return ""
    ev = frame.event
    who = f"{escape(ev.player)} ({escape(ev.team or '')})" if ev.player else escape(ev.team or "")
    text = f"⚽ GOOOL! {who}" if ev.highlight == "goal" else f"🟥 KIRMIZI KART — {who}"
    return f'<div class="cm-banner {ev.highlight}">{frame.display_minute} · {text}</div>'


def event_html(frame: Frame, latest: bool = False) -> str:
    ev = frame.event
    cls = f"cm-ev {escape(ev.highlight)}" + (" latest" if latest else "")
    icon = _event_icon(frame)
    icon_html = f'<span class="ico">{icon}</span>' if icon else ""
    return (
        f'<div class="{cls}"><span class="min">{escape(frame.display_minute)}</span>'
        f'<span class="tag">{escape(ev.label)}</span>'
        f"<span>{icon_html}{escape(ev.description)}</span></div>"
    )


def feed_html(frames: list[Frame], limit: int = 60) -> str:
    """Olay akisi: en yeni ustte."""
    recent = list(reversed(frames[-limit:]))
    items = "".join(event_html(f, latest=(i == 0)) for i, f in enumerate(recent))
    return f'<div class="cm-feed">{items}</div>'


def stats_html(home: str, away: str, home_stats: SideStats, away_stats: SideStats,
               possession: tuple[int, int] | None = None) -> str:
    rows = [
        f'<tr><td class="l">{escape(home)}</td><td class="c"></td><td class="r">{escape(away)}</td></tr>'
    ]
    for key, label in STAT_ROWS:
        rows.append(
            f'<tr><td class="l">{getattr(home_stats, key)}</td><td class="c">{label}</td>'
            f'<td class="r">{getattr(away_stats, key)}</td></tr>'
        )
    if possession is not None:
        h, a = possession
        rows.append(
            f'<tr><td class="l">%{h}</td><td class="c">Topla oynama'
            f'<div class="cm-bar"><span style="width:{h}%"></span></div></td><td class="r">%{a}</td></tr>'
        )
    return f'<div class="cm-scroll"><table class="cm-stats">{"".join(rows)}</table></div>'


def condition_bar_html(condition: int | None, band: str | None) -> str:
    """Yesil/sari/kirmizi kondisyon cubugu. Deger yoksa tire."""
    if condition is None:
        return "—"
    value = max(0, min(100, int(condition)))
    cls = band if band in {"good", "warn", "low"} else "good"
    return (
        f'<div class="cm-cond {cls}"><div class="track"><span class="fill" style="width:{value}%"></span></div>'
        f'<span class="val">%{value}</span></div>'
    )


def squad_table_html(rows) -> str:
    """Kadro tablosu (career_views.SquadRow listesi): durum, OVR, form, moral, kondisyon cubugu."""
    status_cls = {"İlk 11": "xi", "Kulübe": "bench", "Kadro dışı": "out"}
    head = ("<tr><th>Oyuncu</th><th>Mv</th><th>Yaş</th><th>OVR</th><th>Form</th><th>Moral</th>"
            "<th>Kondisyon</th><th>Durum</th></tr>")
    body = []
    for r in rows:
        cls = status_cls.get(r.status, "out")
        badge = (f'<span class="cm-badge bad">{escape(r.unavailable)}</span>' if r.unavailable
                 else f'<span class="cm-badge {cls}">{escape(r.status)}'
                      f'{" · " + escape(r.slot) if r.slot else ""}</span>')
        body.append(
            f'<tr class="{cls}"><td>{escape(r.name)}</td><td>{escape(r.position)}</td><td>{r.age}</td>'
            f"<td>{r.overall}</td><td>{r.form}</td><td>{r.morale}</td>"
            f"<td>{condition_bar_html(r.condition, r.condition_band)}</td><td>{badge}</td></tr>"
        )
    return f'<div class="cm-scroll"><table class="cm-squad">{head}{"".join(body)}</table></div>'


def usage_bar_html(usage_pct: float) -> str:
    """Maas havuzu doluluk cubugu: yesil < %85, sari < %100, kirmizi = asim."""
    cls = "ok" if usage_pct < 85 else "tight" if usage_pct <= 100 else "over"
    width = max(0.0, min(100.0, usage_pct))
    return f'<div class="cm-usage {cls}"><span style="width:{width:.1f}%"></span></div>'


def negotiation_log_html(entries) -> str:
    """Sozlesme masasi konusma gecmisi: (kim, metin) ciftleri; kim = 'me' / 'him' / 'bad'."""
    items = "".join(
        f'<div class="m {who if who in {"me", "him", "bad"} else ""}">{escape(text)}</div>'
        for who, text in entries
    )
    return f'<div class="cm-log">{items}</div>'


def summary_lines(summary: MatchSummary) -> list[str]:
    """Mac sonu ozetinin duz metin satirlari (Streamlit markdown'a gider)."""
    def scorers(items):
        return ", ".join(f"{n} ({g})" if g > 1 else n for n, g in items) or "—"

    tail = " (uzt.)" if summary.extra_time else ""
    if summary.home_penalties is not None and summary.away_penalties is not None:
        tail += f" (pen. {summary.home_penalties}-{summary.away_penalties})"
    lines = [
        f"**{summary.home} {summary.home_score} - {summary.away_score} {summary.away}{tail}**",
        f"Golcüler — {summary.home}: {scorers(summary.home_scorers)} · {summary.away}: {scorers(summary.away_scorers)}",
        f"Topla oynama: %{summary.possession_home} - %{summary.possession_away} · Toplam süre: {summary.total_minutes} dk",
    ]
    if summary.man_of_the_match:
        lines.append(f"Maçın adamı: **{summary.man_of_the_match}** ({summary.motm_rating})")
    if summary.knockout:
        how = {"normal": "normal sürede", "extra_time": "uzatmalarda", "penalties": "penaltılarla"}.get(
            summary.decided_by, "normal sürede")
        agg = ""
        if summary.home_aggregate is not None and summary.away_aggregate is not None and (
                (summary.home_aggregate, summary.away_aggregate) != (summary.home_score, summary.away_score)):
            agg = f" · Toplam: {summary.home_aggregate}-{summary.away_aggregate}"
        winner = f"**{summary.advancing}** tur atladı ({how})" if summary.advancing else "Toplamda eşitlik bozulmadı"
        lines.append(f"Eleme: {winner}{agg}")
    return lines
