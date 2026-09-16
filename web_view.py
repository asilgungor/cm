"""
web_view.py
===========
Canli mac ekraninin HTML/CSS parcalari (6. Asama). SAF FONKSIYONLAR.

Streamlit'e bagimli degildir: web_app.py bu fonksiyonlarin urettigi HTML'i
ekrana basar. Bu ayrim sayesinde gorunum testleri Streamlit calistirmadan yazilir.

GUVENLIK: Oyuncu/kulup adlari dis kaynakli FM dosyalarindan gelebilir; HTML'e
yazilan her metin html.escape() ile kacirilir (bkz. test_web_view_escapes_html).
"""

from __future__ import annotations

from html import escape

from match_feed import Frame, MatchSummary, SideStats

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
.cm-ev.latest{outline:2px solid rgba(76,175,80,.55)}
.cm-stats{width:100%;border-collapse:collapse;font-size:.93rem}
.cm-stats td{padding:.35rem .25rem;border-bottom:1px solid rgba(127,127,127,.18);font-variant-numeric:tabular-nums}
.cm-stats td.l{text-align:right;width:28%;font-weight:700}.cm-stats td.r{text-align:left;width:28%;font-weight:700}
.cm-stats td.c{text-align:center;opacity:.8}
.cm-bar{height:6px;border-radius:3px;background:rgba(127,127,127,.2);overflow:hidden;margin-top:.15rem}
.cm-bar > span{display:block;height:100%;background:#4caf50}
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


def scoreboard_html(home: str, away: str, frame: Frame | None, flash: str | None = None) -> str:
    """Skor tabelasi. flash: 'goal' / 'red' / None -> animasyon sinifi."""
    home_score = frame.home_score if frame else 0
    away_score = frame.away_score if frame else 0
    clock = frame.display_minute if frame else "0'"
    phase = frame.phase if frame else "Başlama öncesi"
    flash_cls = {"goal": " cm-flash-goal", "red": " cm-flash-red"}.get(flash or "", "")
    return (
        f'<div class="cm-board{flash_cls}">'
        f'<div class="cm-team home">{escape(home)}</div>'
        f'<div><div class="cm-score">{home_score} - {away_score}</div>'
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
    cls = f"cm-ev {ev.highlight}" + (" latest" if latest else "")
    return (
        f'<div class="{cls}"><span class="min">{escape(frame.display_minute)}</span>'
        f'<span class="tag">{escape(ev.label)}</span>'
        f"<span>{escape(ev.description)}</span></div>"
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
    return f'<table class="cm-stats">{"".join(rows)}</table>'


def summary_lines(summary: MatchSummary) -> list[str]:
    """Mac sonu ozetinin duz metin satirlari (Streamlit markdown'a gider)."""
    def scorers(items):
        return ", ".join(f"{n} ({g})" if g > 1 else n for n, g in items) or "—"

    lines = [
        f"**{summary.home} {summary.home_score} - {summary.away_score} {summary.away}**",
        f"Golcüler — {summary.home}: {scorers(summary.home_scorers)} · {summary.away}: {scorers(summary.away_scorers)}",
        f"Topla oynama: %{summary.possession_home} - %{summary.possession_away} · Toplam süre: {summary.total_minutes} dk",
    ]
    if summary.man_of_the_match:
        lines.append(f"Maçın adamı: **{summary.man_of_the_match}** ({summary.motm_rating})")
    return lines
