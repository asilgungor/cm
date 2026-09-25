"""
15G "Not modeli ve gizli ozellikler macta" testleri (EngineConfig.rating_model).

SAF testler (CM_TEST_NO_DB=1 ile calisir):
    * bayrak KAPALI: tek gizli ozellik okunmaz, gunun formu cekilmez, not eski formulle hesaplanir
      (altin tohumlar 14E ile birebir)
    * gunun formu: beklenen degeri 1.0, oyuncuya + tohuma gore deterministik, KENDI crc32 akisindan
      (macin RNG'si prepare sirasinda HIC tuketilmez)
    * tutarlilik -> sapma, mizac -> kart agirligi, onemli mac -> mac agirligi (ligde tam 1.0) monoton
    * macin agirliginin turetilmesi: eleme / tarafsiz sahada final / MatchTeam.rivals ile derbi
    * gizli ucluk: FM verisi varsa o, yoksa kimlikten crc32; her ozellik AYRI akis
    * not defteri: kurtaris netlikle agirlasir, net golde kalecinin sucu azalir, duello kazanan + kaybeden -
    * K12: uc gizli ozellik ve not defteri hicbir olaya / meta veriye / repr'e girmez
    * duman: ortalama not bandi, forvet-defans farki, tutarlilik sd orani (kucuk n)
Tam kabul olcumleri kanit kosusunda: .claude/phase14/kanit/15G_kabul.txt.
"""

from __future__ import annotations

import dataclasses
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import attribute_model as am  # noqa: E402
import match_engine  # noqa: E402
from match_engine import EngineConfig, KnockoutRule, MatchEngine  # noqa: E402
from models import Position  # noqa: E402
from tests.engine_stats import make_spread_team, make_team  # noqa: E402

RM = am.RatingModelConfig()
ON = EngineConfig(rating_model=True)
OFF = EngineConfig(rating_model=False)


def _play(seed=1, cfg=None, home_ovr=80, away_ovr=80, spread=None, **kw):
    if spread is None:
        home, away = make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr)
    else:
        home = make_spread_team(1, "Ev", home_ovr, spread=spread, rng_seed=seed)
        away = make_spread_team(2, "Dep", away_ovr, spread=spread, rng_seed=seed + 10007)
    return MatchEngine(home, away, seed=seed, config=cfg, **kw).simulate()


def _probe(team, position):
    return next(p for p in team.players if p.position is position)


# ---------------------------------------------------------------------------
# Bayrak kapali: 14E davranisi
# ---------------------------------------------------------------------------

# 14E motorunun notlari (rating_model=False). Bayrak cevrildikten SONRA da bu degerler
# EngineConfig(rating_model=False) ile birebir uretilmelidir.
GOLDEN_RATING_MODEL_OFF = [
    (0, 7.3, 5.7, 8.6), (1, 6.7, 6.3, 5.7), (2, 7.0, 6.5, 5.8),
    (3, 6.5, 5.7, 6.3), (4, 7.2, 6.8, 5.7),
]


def test_flag_is_on_by_default():
    """15G bayragi CEVRILDI (YENIDEN TEMELLENDIRME 7): varsayilan motor not modelini kullanir."""
    assert EngineConfig().rating_model is True


@pytest.mark.parametrize("seed,home_gk,home_def,away_fwd", GOLDEN_RATING_MODEL_OFF)
def test_ratings_off_match_the_14e_engine(seed, home_gk, home_def, away_fwd):
    r = _play(seed, OFF)
    assert _probe(r.home, Position.GK).rating == home_gk
    assert _probe(r.home, Position.DEF).rating == home_def
    assert _probe(r.away, Position.FWD).rating == away_fwd


def test_flag_off_reads_no_hidden_trait():
    r = _play(3, OFF)
    for team in (r.home, r.away):
        for p in team.players:
            assert p._trait is None
            assert p._day_form == 1.0
            assert p._big_game == 1.0
            assert p._duel_credit == p._chain_credit == p._keeper_credit == 0.0


def test_flag_off_and_on_differ():
    off = [p.rating for p in _play(7, OFF).home.players]
    on = [p.rating for p in _play(7, ON).home.players]
    assert off != on


# ---------------------------------------------------------------------------
# Gunun formu (tutarlilik)
# ---------------------------------------------------------------------------

def test_day_form_is_deterministic_per_player_and_seed():
    sigma = am.form_sigma(10, RM)
    a = am.day_form(11, 501, sigma, RM)
    assert a == am.day_form(11, 501, sigma, RM)
    assert a != am.day_form(12, 501, sigma, RM)
    assert a != am.day_form(11, 502, sigma, RM)


def test_day_form_expected_value_is_one():
    """Butce notrlugu: gunun formunun beklenen degeri 1.0, yani takim gucu kalibrasyonu kaymaz."""
    sigma = am.form_sigma(10.5, RM)
    values = [am.day_form(s, 1, sigma, RM) for s in range(4000)]
    assert abs(statistics.fmean(values) - 1.0) < 0.006
    assert 0.5 * RM.form_sigma < statistics.pstdev(values) < 1.5 * RM.form_sigma


def test_day_form_zero_sigma_is_exactly_one():
    assert am.day_form(1, 1, 0.0, RM) == 1.0


def test_form_sigma_falls_with_consistency():
    sigmas = [am.form_sigma(c, RM) for c in range(1, 21)]
    assert sigmas == sorted(sigmas, reverse=True)
    assert am.form_sigma(10.5, RM) == pytest.approx(RM.form_sigma)
    # Kabul: tutarliligi 5 olan, 18 olandan belirgin genis sapar (not sd orani buradan gelir)
    assert am.form_sigma(5, RM) / am.form_sigma(18, RM) >= 4.0


def test_day_form_stays_in_range():
    sigma = am.form_sigma(1, RM)
    lo, hi = RM.form_range
    for s in range(2000):
        assert lo <= am.day_form(s, 7, sigma, RM) <= hi


def test_prepare_does_not_consume_the_match_rng():
    """Gunun formu AYRI akistan gelir: hazirliktan sonra macin RNG'si bayrak kapaliyla ayni yerdedir."""
    on = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=5, config=ON)
    off = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=5, config=OFF)
    assert [on.rng.random() for _ in range(5)] == [off.rng.random() for _ in range(5)]


def test_rating_ledger_never_draws_from_the_match_rng():
    class Boom:
        def random(self):
            raise AssertionError("not defteri sonuc RNG'sinden cekilis yapamaz")

        def choice(self, seq):
            raise AssertionError("not defteri sonuc RNG'sinden cekilis yapamaz")

    engine = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=5, config=ON)
    engine.rng = Boom()
    shooter = _probe(engine.home, Position.FWD)
    defender = _probe(engine.away, Position.DEF)
    engine._rating_chance(engine.home, engine.away, shooter, defender, 1.1, "save", 0.9)
    engine._rating_chain(engine.home, shooter, 0.5, goal=True)
    match_engine.rating_value(shooter, True, False, False, 90, RM)


def test_live_rating_is_the_engine_rating_at_full_time():
    """Canli not ile motorun yazdigi not TEK kaynaktan gelir: mac sonunda birebir esittir."""
    for seed in range(12):
        r = _play(seed, ON, spread=8)
        for team, other in ((r.home, r.away), (r.away, r.home)):
            for p in team.players:
                live = match_engine.live_rating(p, team.stats.goals, other.stats.goals, r.end_minute)
                assert live == (p.rating if p.played else None), (seed, p.name)


def test_live_rating_follows_the_ledger_during_the_match():
    """Not defteri doldukca canli not da oynar (gosterilen tek sayi notun kendisidir)."""
    r = _play(5, ON, spread=8)
    scorer = max((p for t in (r.home, r.away) for p in t.players), key=lambda p: p.goals)
    assert scorer.goals >= 1 and scorer.played
    before = match_engine.live_rating(scorer, 1, 1, 90)
    scorer.goals += 1                      # defter doldu: ayni oyuncu, bir gol daha
    assert match_engine.live_rating(scorer, 1, 1, 90) > before
    scorer.yellow_cards += 1
    assert match_engine.live_rating(scorer, 1, 1, 90) < match_engine.live_rating(
        scorer, 1, 1, 90) + RM.yellow


def test_low_consistency_swings_more_than_high_consistency():
    """Kabul: tutarliligi 5 olanin not standart sapmasi, 18 olanin >= 1.4 kati (duman, kucuk n)."""
    out = {}
    for value in (5, 18):
        ratings = []
        for seed in range(200):
            home = make_team(1, "Ev", 80)
            probe = _probe(home, Position.MID)
            probe.attributes = {am.CONSISTENCY_TRAIT: value}
            r = MatchEngine(home, make_team(2, "Dep", 80), seed=seed, config=ON).simulate()
            played = next(p for p in r.home.players if p.id == probe.id)
            if played.played:
                ratings.append(played.rating)
        out[value] = statistics.pstdev(ratings)
    # Duman esigi kabulden (1.4) dusuktur: tam olcum n = 1.200 / kol ile kanit kosusunda.
    assert out[5] >= 1.25 * out[18], out


# ---------------------------------------------------------------------------
# Mizac ve onemli mac
# ---------------------------------------------------------------------------

def test_temperament_card_factor_is_neutral_in_the_middle():
    assert am.temperament_card_factor(am.TRAIT_NEUTRAL, RM) == pytest.approx(1.0)
    assert am.temperament_card_factor(1, RM) > 1.0
    assert am.temperament_card_factor(20, RM) < 1.0
    lo, hi = RM.temperament_range
    assert all(lo <= am.temperament_card_factor(t, RM) <= hi for t in range(1, 21))


def test_hot_headed_player_carries_more_card_weight():
    home = make_team(1, "Ev", 80)
    defenders = [p for p in home.players if p.position is Position.DEF]
    calm, hot = defenders[0], defenders[1]
    calm.attributes = {am.TEMPERAMENT_TRAIT: 20}
    hot.attributes = {am.TEMPERAMENT_TRAIT: 1}
    MatchEngine(home, make_team(2, "Dep", 80), seed=4, config=ON)
    assert hot.aggression > calm.aggression


def test_big_game_factor_is_exactly_one_in_the_league():
    for trait in range(1, 21):
        assert am.big_game_factor(trait, am.occasion_weight(am.OCCASION_LEAGUE), RM) == 1.0


def test_big_game_factor_grows_with_the_occasion():
    weights = [am.occasion_weight(o) for o in
               (am.OCCASION_LEAGUE, am.OCCASION_DERBY, am.OCCASION_KNOCKOUT, am.OCCASION_FINAL)]
    assert weights == sorted(weights)
    big = [am.big_game_factor(18, w, RM) for w in weights]
    assert big == sorted(big)
    small = [am.big_game_factor(3, w, RM) for w in weights]
    assert small == sorted(small, reverse=True)
    lo, hi = RM.big_game_range
    assert all(lo <= am.big_game_factor(t, 1.4, RM) <= hi for t in range(1, 21))


def test_occasion_is_derived_from_what_the_engine_knows():
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
    assert MatchEngine(home, away, seed=1, config=ON).occasion == am.OCCASION_LEAGUE
    assert MatchEngine(home, away, seed=1, config=ON,
                       knockout=KnockoutRule()).occasion == am.OCCASION_KNOCKOUT
    assert MatchEngine(home, away, seed=1, config=ON, knockout=KnockoutRule(),
                       neutral_venue=True).occasion == am.OCCASION_FINAL
    home.rivals = frozenset({away.id})
    assert MatchEngine(home, away, seed=1, config=ON).occasion == am.OCCASION_DERBY
    # cagiran katman acikca da verebilir
    assert MatchEngine(home, away, seed=1, config=ON, occasion=am.OCCASION_FINAL).occasion == am.OCCASION_FINAL


def test_league_match_leaves_the_big_game_factor_untouched():
    engine = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=2, config=ON)
    assert all(p._big_game == 1.0 for p in engine.home.players)
    knock = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=2, config=ON,
                        knockout=KnockoutRule())
    assert any(p._big_game != 1.0 for p in knock.home.players)


# ---------------------------------------------------------------------------
# Gizli ucluk
# ---------------------------------------------------------------------------

def test_fm_value_wins_over_the_identity_stream():
    player = make_team(1, "Ev", 80).players[0]
    player.attributes = {am.CONSISTENCY_TRAIT: 3, am.TEMPERAMENT_TRAIT: 17,
                         am.BIG_MATCH_TRAIT: 12}
    traits = am.hidden_traits(player, RM)
    assert (traits.consistency, traits.temperament, traits.big_match) == (3, 17, 12)


def test_each_trait_has_its_own_stream():
    """Ayni kimlikten uc ozellik AYRI crc32 akisiyla gelir: hepsi ayni sayi degildir."""
    values = []
    for pid in range(1, 60):
        player = make_team(1, "Ev", 80).players[0]
        player.id = pid
        player.attributes = {}
        t = am.hidden_traits(player, RM)
        assert 1 <= t.consistency <= 20 and 1 <= t.big_match <= 20 and 1 <= t.temperament <= 20
        values.append((t.consistency, t.big_match, t.temperament))
    assert sum(1 for c, b, t in values if c == b == t) < len(values) // 4


def test_hidden_traits_are_stable_for_the_same_identity():
    a, b = make_team(1, "Ev", 80).players[0], make_team(1, "Ev", 80).players[0]
    assert am.hidden_traits(a, RM) == am.hidden_traits(b, RM)


# ---------------------------------------------------------------------------
# Not defteri
# ---------------------------------------------------------------------------

def test_save_is_worth_more_when_the_chance_was_clear():
    assert am.save_value(1.4, RM) > am.save_value(1.0, RM) == am.save_value(0.6, RM)
    assert am.save_value(9.0, RM) <= RM.save + RM.save_clarity_cap


def test_keeper_is_blamed_less_for_a_clear_cut_goal():
    assert am.concede_value(1.4, RM) < am.concede_value(0.8, RM)
    assert am.concede_value(9.0, RM) >= RM.concede_floor


def test_duel_value_rewards_the_defender_who_holds_and_punishes_the_one_who_is_beaten():
    assert am.duel_value(1.0, conceded=False, cfg=RM) > 0
    assert am.duel_value(1.5, conceded=False, cfg=RM) > am.duel_value(0.8, conceded=False, cfg=RM)
    assert am.duel_value(1.0, conceded=True, cfg=RM) < 0
    # cok net sansta tek savunmacinin sucu azalir (ama sifirlanmaz)
    assert am.duel_value(1.8, conceded=True, cfg=RM) > am.duel_value(0.8, conceded=True, cfg=RM)
    assert am.duel_value(9.0, conceded=True, cfg=RM) < 0


def test_the_ledger_actually_fills_during_a_match():
    r = _play(11, ON, spread=8)
    assert any(p._duel_credit for t in (r.home, r.away) for p in t.players)
    assert any(p._chain_credit for t in (r.home, r.away) for p in t.players)
    assert any(p._keeper_credit for t in (r.home, r.away) for p in t.players)


def test_conceding_hurts_the_keeper_rating():
    """Ayni kadroda, cok gol yiyen kalecinin notu temiz kalan kalecininkinin altindadir."""
    beaten, clean = [], []
    for seed in range(60):
        r = _play(seed, ON, home_ovr=92, away_ovr=68)
        # Mac bittiginde kimse sahada degildir (MatchTeam.keeper None doner): kaleci MEVKIDEN bulunur.
        away_gk, home_gk = _probe(r.away, Position.GK), _probe(r.home, Position.GK)
        if r.home_score >= 3:
            beaten.append(away_gk.rating)
        if r.away_score == 0:
            clean.append(home_gk.rating)
    assert beaten and clean
    assert statistics.fmean(beaten) < statistics.fmean(clean)


def test_a_substitute_stays_near_the_base_rating():
    r = _play(13, ON, spread=8)
    subs = [p for t in (r.home, r.away) for p in t.players
            if p.played and (p.entered_minute or 0) > 80]
    for p in subs:
        assert abs(p.rating - RM.base) <= 1.3


def test_ratings_stay_inside_the_configured_range():
    lo, hi = RM.range
    for seed in range(25):
        r = _play(seed, ON, home_ovr=95, away_ovr=60, spread=8)
        for team in (r.home, r.away):
            for p in team.players:
                assert lo <= p.rating <= hi


def test_same_seed_gives_the_same_ratings():
    a, b = _play(21, ON, spread=8), _play(21, ON, spread=8)
    assert [p.rating for p in a.home.players] == [p.rating for p in b.home.players]


# ---------------------------------------------------------------------------
# K12: gizli sayi disari sizmaz
# ---------------------------------------------------------------------------

def test_hidden_state_is_not_in_repr_or_events():
    r = _play(9, ON, spread=8)
    player = r.home.players[0]
    text = repr(player)
    assert "trait" not in text and "day_form" not in text and "credit" not in text
    hidden = {f.name for f in dataclasses.fields(player)
              if f.name in ("_trait", "_day_form", "_big_game", "_duel_credit",
                            "_chain_credit", "_keeper_credit")}
    assert len(hidden) == 6
    assert all(not f.repr and not f.compare for f in dataclasses.fields(player)
               if f.name in hidden)
    for event in r.events:
        for word in ("tutarl", "mizac", "mizaç", "gunun formu", "günün formu", "consistency",
                     "temperament", "important"):
            assert word not in event.description.lower()
            assert word not in (event.report or "").lower()


def test_event_schema_gains_no_field_from_15g():
    """15G olay semasina alan EKLEMEZ (K5): not defteri yalnizca notun kendisine cikar."""
    names = {f.name for f in dataclasses.fields(match_engine.MatchEvent)}
    assert not {n for n in names if "trait" in n or "form" in n or "credit" in n}
    assert "chance_quality" in names          # K6: saklanan netlik zaten vardi, 15G onu OKUR


# ---------------------------------------------------------------------------
# Duman: kabul bantlari (kucuk n; tam olcum kanit kosusunda)
# ---------------------------------------------------------------------------

def _rating_sample(n=70, cfg=None):
    by_role: dict[str, list[float]] = {}
    every: list[float] = []
    for seed in range(n):
        r = _play(seed, cfg, spread=8)
        for team in (r.home, r.away):
            for p in team.players:
                if p.played:
                    by_role.setdefault((p.role or p.position).name, []).append(p.rating)
                    every.append(p.rating)
    return by_role, every


def test_average_rating_is_in_the_real_football_band():
    by_role, every = _rating_sample(cfg=ON)
    assert 6.6 <= statistics.fmean(every) <= 6.9, statistics.fmean(every)
    gap = statistics.fmean(by_role["FWD"]) - statistics.fmean(by_role["DEF"])
    assert abs(gap) <= 0.3, gap


def test_the_old_model_was_below_the_band():
    """Bayrak kapali motorun ortalamasi gercek futbol bandinin altindaydi (15G'nin sebebi)."""
    _, every = _rating_sample(cfg=OFF)
    assert statistics.fmean(every) < 6.6
