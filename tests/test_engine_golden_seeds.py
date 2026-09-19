"""
tests/test_engine_golden_seeds.py
=================================
13A DETERMINIZM KAPISI (K11). Tohum 0-9 icin skor, olay sayisi ve parmak izi sabittir.

Amaci `test_extra_time.py` altin listesinden FARKLIDIR: orasi 8. Asama oncesi motoru
dondurur; burasi "rastgele cekis SIRASI kazara degisti mi" sorusunu aninda yanitlar.
Parmak izi TeamStats'in YENI sayaclarini (korner, faul, ofsayt) da acikca icerir, cunku
bunlar `TeamStats.__repr__` disinda tutulur (eski altin ozetler asama boyunca yesil kalsin diye).

Degerleri yeniden uretmek:
    python -m tests.test_engine_golden_seeds        # yeni GOLDEN listesini basar
Yeniden temellendirme ancak KASITLI bir davranis degisikliginden sonra yapilir ve
degisikligin nedeni buraya yazilir.

YENIDEN TEMELLENDIRME 1 (13A kapanisi): EngineConfig'teki 13A bayraklarinin tamami
tek adimda acildi (set_pieces, match_stats, discipline_v2, role_realism, weak_link,
flat_superiority, match_form, goal_timing, sub_timing, fatigue_balance). Kanit:
bayraklarin HEPSI False iken motor 5.500 macta (2.500 tohum x 2 guc senaryosu +
500 eleme maci) HEAD ile BIT-BIT ayni kaliyor -- yani buradaki fark yalnizca
kasitli varsayilan degisikligidir.

YENIDEN TEMELLENDIRME 2 (13B "anlatim"): olay SAYILARI ve METINLER degisti, SONUCLAR degismedi.
Motor artik korner / kartsiz faul / ofsayt / kurulus zinciri / ambiyans olaylarini da YAZIYOR
(akista cogu gizlenir, K7) ve anlatim commentary.py bankasindan, ayri bir RNG ile uretiliyor.
Kanit (.claude/phase13/scratch/b13/evidence.py, 2.200 mac = 1.000 tohum x 2 guc senaryosu +
200 eleme maci): skor, TUM oyuncu istatistikleri (gol, asist, sut, isabet, kurtaris, kart,
sakatlik, degisiklik, dakikalar, not, enerji serisi), takim sayaclari, uzatma dakikalari, macin
adami ve seri penaltilar 13A ile BIT-BIT ayni (outcome ozeti esit). 13B bayraklarinin hepsi False
iken olay listesi ve metin dahil TAM parmak izi 13A ile ayni. Asagidaki listede skorlar aynen
duruyor; yalnizca olay sayisi (ortalama ~41 -> ~88) ve parmak izi degisti.

YENIDEN TEMELLENDIRME 3 (14B "ozellikler motorda"): EngineConfig.attribute_model varsayilan olarak
ACILDI. CM 01/02 sayfasinin 31 ozelligi ve gizli sakatlik egilimi mevcut cekilislerin olasilik /
agirliklarinda okunuyor (yeni rastgele sayi YOK); skorlar, olay sayilari ve parmak izleri kasitli
olarak degisti. Kanit (.claude/phase14/kanit/14B_evidence.txt): bayrak False iken 2.200 macta
(1.000 tohum x 2 guc senaryosu + 200 eleme) outcome_sha VE full_sha 14B oncesiyle BIT-BIT ayni; ayrica
bayrak kapali 0-9 tohumlari eski listeyle (GOLDEN_ATTRIBUTE_MODEL_OFF, asagida testli) birebir ayni.
Bayrak acik dagilim kapisi (tests/test_engine_distribution.py) 18 / 18 bantta yesil.

YENIDEN TEMELLENDIRME 4 (14E "taktik etkisi ve karsi hamle"): EngineConfig.tactics_v2 varsayilan olarak ACILDI.
Bu listenin DEGERLERI DEGISMEDI: varsayilan talimatli macta 14E yalnizca taktik degisiklikte (gercek sekil normu,
hat sinirli en cok 3 hak) fark yaratir ve 0-9 tohumlarinda taktik degisiklik yok. Bayrak kapali yol ayni listeyle
kalici testli (test_golden_seed_with_tactics_v2_off_is_14b); 2.200 macta outcome_sha + full_sha 14E oncesiyle ayni
(.claude/phase14/kanit/14E_evidence.txt).
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from match_engine import EngineConfig, MatchEngine, MatchResult  # noqa: E402
from tests.engine_stats import make_team  # noqa: E402

SEEDS = range(10)
COUNTERS = ("goals", "shots", "shots_on_target", "saves", "possession_minutes", "yellow_cards",
            "red_cards", "injuries", "substitutions", "corners", "fouls", "offsides")


def _stats_line(team) -> str:
    return "|".join(f"{name}={getattr(team.stats, name, 0)}" for name in COUNTERS)


def fingerprint(r: MatchResult) -> str:
    ev = "\n".join(
        f"{e.minute}|{e.added_time}|{e.type.value}|{e.team_id}|{e.player_id}|{e.home_score}|"
        f"{e.away_score}|{e.detail}|{e.description}" for e in r.events)
    pl = "\n".join(
        f"{p.id}|{p.entered_minute}|{p.left_minute}|{p.rating}|{p.energy!r}|{p.energy_log}|{p.goals}|"
        f"{p.assists}|{p.shots}|{p.shots_on_target}|{p.saves}|{p.yellow_cards}|{p.sent_off}|{p.injured}|"
        f"{p.substituted}" for t in (r.home, r.away) for p in t.players)
    st = (f"{_stats_line(r.home)}#{_stats_line(r.away)}#{r.first_half_added}|{r.second_half_added}|"
          f"{r.total_minutes}|{r.man_of_the_match.id if r.man_of_the_match else None}")
    return hashlib.sha256((ev + "#" + pl + "#" + st).encode()).hexdigest()[:16]


def simulate(seed: int, config: EngineConfig | None = None) -> MatchResult:
    return MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 78), seed=seed, config=config).simulate()


# (tohum, ev golu, deplasman golu, olay sayisi, parmak izi) -- varsayilan motor (14B ozellik modeli + 14E taktik v2 acik)
GOLDEN = [
    (0, 1, 0, 78, '6ca1167284b7a2a0'),
    (1, 1, 0, 70, 'dcd48523f4de5aab'),
    (2, 1, 0, 85, 'a90e0d9368532f64'),
    (3, 1, 4, 93, '6c57eaa720978517'),
    (4, 3, 0, 98, '4e447fed65f8f10a'),
    (5, 1, 0, 91, 'd519c1cf810ebf55'),
    (6, 4, 0, 90, '8b669654f38788e5'),
    (7, 4, 1, 96, 'a9b0ed14697016f3'),
    (8, 2, 3, 87, 'c06b6648d88fb0bd'),
    (9, 2, 1, 89, 'faea9c0111e0bd12'),
]

# 14E oncesi liste = GOLDEN (degismedi): EngineConfig.tactics_v2=False iken motor bununla BIT-BIT ayni.
GOLDEN_PRE_14E = GOLDEN

# YENIDEN TEMELLENDIRME 2'nin listesi (13B): EngineConfig.attribute_model=False iken motor bununla BIT-BIT ayni.
GOLDEN_ATTRIBUTE_MODEL_OFF = [
    (0, 1, 0, 78, '87f2432ff2f76c04'),
    (1, 2, 1, 68, '5e52c8b7d9f74776'),
    (2, 1, 0, 86, '62197409c061739a'),
    (3, 2, 1, 90, '8114af722f74c479'),
    (4, 3, 0, 98, '0498e127d45e32a8'),
    (5, 0, 0, 93, 'c8de965ade4ed6af'),
    (6, 4, 0, 89, 'cb5fcf37d0a0e24c'),
    (7, 5, 1, 94, '5a35017a854e321e'),
    (8, 2, 2, 83, '50302f3e2617c8cd'),
    (9, 1, 3, 101, 'ddfe94c48a2dc63a'),
]


@pytest.mark.parametrize("seed,home_goals,away_goals,n_events,digest", GOLDEN)
def test_golden_seed_is_stable(seed, home_goals, away_goals, n_events, digest):
    r = simulate(seed)
    assert (r.home_score, r.away_score, len(r.events)) == (home_goals, away_goals, n_events)
    assert fingerprint(r) == digest


@pytest.mark.parametrize("seed,home_goals,away_goals,n_events,digest", GOLDEN_ATTRIBUTE_MODEL_OFF)
def test_golden_seed_with_attribute_model_off_is_13b(seed, home_goals, away_goals, n_events, digest):
    r = simulate(seed, EngineConfig(attribute_model=False, tactics_v2=False))
    assert (r.home_score, r.away_score, len(r.events)) == (home_goals, away_goals, n_events)
    assert fingerprint(r) == digest


@pytest.mark.parametrize("seed,home_goals,away_goals,n_events,digest", GOLDEN_PRE_14E)
def test_golden_seed_with_tactics_v2_off_is_14b(seed, home_goals, away_goals, n_events, digest):
    r = simulate(seed, EngineConfig(tactics_v2=False))
    assert (r.home_score, r.away_score, len(r.events)) == (home_goals, away_goals, n_events)
    assert fingerprint(r) == digest


def test_repeated_simulation_is_identical():
    for seed in SEEDS:
        assert fingerprint(simulate(seed)) == fingerprint(simulate(seed))


def _print_golden() -> None:
    print("GOLDEN = [")
    for seed in SEEDS:
        r = simulate(seed)
        print(f"    ({seed}, {r.home_score}, {r.away_score}, {len(r.events)}, '{fingerprint(r)}'),")
    print("]")


if __name__ == "__main__":
    _print_golden()
