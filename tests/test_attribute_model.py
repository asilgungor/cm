"""
attribute_model.py testleri (14B "ozellikler motorda").

SAF testler (CM_TEST_NO_DB=1 ile calisir):
    * dirsek monoton ve doyumlu; tipik sayfada her carpan TAM 1.0 (butce notrlugu)
    * READERS 31 ozelligin hepsini (ve gizli sakatlik egilimini) kapsiyor; kanallar tanimli
    * bayrak KAPALI: MatchPlayer.sheet bos, attributes / stamina dokunulmamis; altin tohumlar 13B ile ayni
    * bayrak ACIK: sayfa = profil sayfasi (ayni tohum), team_roles icin attributes, deterministik, K12 (sayfa ve
      carpanlar repr / olay / meta veriye girmez), yeni rastgele sayi yok
    * grup ici ayrisma (bitiricilik yakin / uzaktan sut uzak sansta; refleks yakin sansta)
    * supurme dumani (n = 800 esli tohum, esiklerin yarisi): topsuz oyun, bitiricilik, elle kontrol, saldirganlik,
      dayaniklilik, sakatlik egilimi, takim oyunu; baskinlik tavani dumani (bir ozellik, 11 oyuncu)
Tam supurme (n >= 3.000 / kol) kanit kosusunda: .claude/phase14/kanit/14B_supurme.txt.

Veritabani (integration): profil sayfasi (ORM Player) = motorun okudugu sayfa (MatchPlayer.from_orm).
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import attribute_model as am  # noqa: E402
import cm_attributes as cm  # noqa: E402
import fitness  # noqa: E402
import match_engine  # noqa: E402
from match_engine import EngineConfig, MatchEngine, MatchPlayer  # noqa: E402
from models import Player, Position  # noqa: E402
from tests.engine_stats import attribute_sweep, make_team, sweep_config  # noqa: E402

CFG = am.AttributeModelConfig()
ON = EngineConfig(attribute_model=True)
OFF = EngineConfig(attribute_model=False)


# ===========================================================================
# Dirsek, sapma, butce notrlugu
# ===========================================================================

def test_elbowed_is_monotone_and_saturates_above_the_elbow():
    values = [am.elbowed(v, CFG) for v in range(1, 21)]
    assert all(b > a for a, b in zip(values, values[1:], strict=False))          # kesin artan
    below = [b - a for a, b in zip(values[:15], values[1:15], strict=False)]
    above = [b - a for a, b in zip(values[14:], values[15:], strict=False)]
    assert all(step == pytest.approx(1.0) for step in below)
    assert all(step == pytest.approx(CFG.elbow_slope) for step in above)
    assert am.elbowed(20, CFG) == pytest.approx(17.5)                              # 20, dirsekle 17.5 "etkiler"


def test_deviation_and_factor_are_relative_and_clipped():
    expected = {"finishing": 12, "long_shots": 12}
    assert am.deviation({"finishing": 12, "long_shots": 12}, expected, [("finishing", 1.0)], CFG) == 0.0
    assert am.deviation({"finishing": 6, "long_shots": 12}, expected, [("finishing", 0.5)], CFG) == pytest.approx(
        -0.25)
    assert am.factor({"finishing": 1, "long_shots": 12}, expected, [("finishing", 1.0)], CFG) == CFG.factor_range[0]
    assert am.factor({"finishing": 20, "long_shots": 12}, expected, [("finishing", 5.0)], CFG) == CFG.factor_range[1]


@pytest.mark.parametrize("ovr", [55, 80, 92])
def test_typical_sheet_gives_exactly_one(ovr):
    team = make_team(1, "Ev", ovr)
    for p in team.players:
        expected = am.expected_sheet(p)
        pf = am.player_factors(expected, expected, p.position, CFG.injury_mean, CFG)
        for name, value in pf.as_dict().items():
            if name == "fatigue":                # dayaniklilik mutlak okunur; tipik oyuncuda toplam carpan 1.0
                value *= fitness.stamina_decay_multiplier(float(expected["stamina"]))
                assert value == pytest.approx(1.0, abs=1e-12), (p.name, name, value)
                continue
            assert value == (0.0 if name in am._RAW_CHANNELS else 1.0), (p.name, name, value)
        for far in (0.0, 0.4, 1.0):
            assert am.finish_factor(pf, far, 0.7, CFG) == 1.0
            assert am.keeper_factor(pf, far, CFG) == 1.0
        p._am, p.role = pf, p.position
    assert am.team_factors(team.players, CFG) == am.TeamFactors()


def test_no_super_keeper_elbow_and_range_cap_the_upside():
    keeper = make_team(1, "Ev", 80).players[0]
    expected = am.expected_sheet(keeper)
    best = dict(expected, handling=20, reflexes=20, agility=20, positioning=20)
    pf = am.player_factors(best, expected, Position.GK, CFG.injury_mean, CFG)
    lo, hi = CFG.range_of("keeper")
    assert 1.0 < am.keeper_factor(pf, 0.0, CFG) <= hi
    worst = dict(expected, handling=1, reflexes=1)
    pf = am.player_factors(worst, expected, Position.GK, CFG.injury_mean, CFG)
    assert am.keeper_factor(pf, 0.0, CFG) == lo
    # doyum: 16 -> 20 kazanci, 6 -> 10 kazancinin yarisindan az
    def k(handling):
        f = am.player_factors(dict(expected, handling=handling), expected, Position.GK, CFG.injury_mean, CFG)
        return am.keeper_factor(f, 1.0, CFG)
    assert k(20) - k(16) <= 0.5 * (k(10) - k(6)) + 1e-12
    assert k(20) - 1.0 < 1.0 - k(6)                                           # iyi deger, kotu degerden az kazandirir


def test_within_group_split_finishing_near_long_shots_far():
    striker = make_team(1, "Ev", 80).players[-1]
    expected = am.expected_sheet(striker)
    sharp = dict(expected, finishing=expected["finishing"] + 3, long_shots=expected["long_shots"] - 4)
    pf = am.player_factors(sharp, expected, Position.FWD, CFG.injury_mean, CFG)
    near, far = am.finish_factor(pf, 0.0, 0.0, CFG), am.finish_factor(pf, 1.0, 0.0, CFG)
    assert near > 1.0 > far
    keeper = make_team(1, "Ev", 80).players[0]
    e = am.expected_sheet(keeper)
    reflex = dict(e, reflexes=e["reflexes"] + 3, handling=e["handling"] - 3)
    pf = am.player_factors(reflex, e, Position.GK, CFG.injury_mean, CFG)
    assert am.keeper_factor(pf, 0.0, CFG) > am.keeper_factor(pf, 1.0, CFG)       # refleks yakin sansta agirlasir
    assert am.far_share(Position.MID, 1.0, CFG) > am.far_share(Position.FWD, 1.0, CFG)
    assert am.far_share(Position.FWD, 0.8, CFG) > am.far_share(Position.FWD, 1.2, CFG)


# ===========================================================================
# Okuyucu tablosu
# ===========================================================================

def test_readers_cover_every_attribute_and_the_hidden_trait():
    assert set(am.READERS) >= set(cm.ATTRIBUTE_KEYS)
    assert am.INJURY_TRAIT in am.READERS
    assert am.ENGINE_READ_KEYS == frozenset(cm.ATTRIBUTE_KEYS)
    assert am.unread_keys(True) == frozenset()
    assert am.unread_keys(False) == cm.LEGACY_UNREAD_FOR_GENERATED_KEYS
    assert cm.ENGINE_READ_KEYS == am.ENGINE_READ_KEYS and cm.UNREAD_FOR_GENERATED_KEYS == am.unread_keys(True)
    from tests.engine_stats import SWEEP_METRICS

    for key, reader in am.READERS.items():
        assert reader.reads or reader.indirect, key                      # her ozellik en az bir yerde okunur
        assert reader.metric in SWEEP_METRICS, key
        assert reader.direction in (-1, 1) and reader.threshold > 0, key
        for read in reader.reads:
            assert read.channel in am.CHANNELS, (key, read.channel)
    # kanal disi okumalar gercekten o yoldan: team_roles'un okudugu FM adlari as_fm_attributes'ta var
    fm = cm.as_fm_attributes(dict.fromkeys(cm.ATTRIBUTE_KEYS, 10))
    for name in ("crossing", "heading", "jumping_reach", "free_kicks", "corners", "technique", "long_shots",
                 "acceleration", "leadership", "passing", "finishing"):
        assert name in fm, name
    # her ozellik, tipik sayfadan sapinca en az bir kanali oynatir (ya da team_roles / fitness yolundan okunur)
    striker = make_team(1, "Ev", 80).players[-1]
    for key in cm.ATTRIBUTE_KEYS:
        reader = am.READERS[key]
        moved = False
        for role in (Position.GK, Position.DEF, Position.MID, Position.FWD):
            player = SimpleNamespace(**{**vars(striker), "position": role})
            e = am.expected_sheet(player)
            base = am.player_factors(e, e, role, CFG.injury_mean, CFG).as_dict()
            low = am.player_factors(dict(e, **{key: 1}), e, role, CFG.injury_mean, CFG).as_dict()
            moved = moved or base != low
        assert moved or reader.indirect, key


# ===========================================================================
# Bayrak KAPALI: dokunulmaz, bit-bit ayni
# ===========================================================================

def test_flag_is_on_by_default():
    """14B §3.7: ozellik modeli varsayilan acik (YENIDEN TEMELLENDIRME 3)."""
    assert EngineConfig().attribute_model is True
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
    MatchEngine(home, away, seed=3)
    assert all(p.sheet and isinstance(p._am, am.PlayerFactors) for p in home.players)


def test_flag_off_leaves_players_untouched():
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
    MatchEngine(home, away, seed=3, config=OFF).simulate()
    for p in home.players + away.players:
        assert p.sheet == {} and p.attributes == {} and p.stamina is None and p._am is None


def test_flag_off_golden_seeds_unchanged():
    from tests.test_engine_golden_seeds import GOLDEN_ATTRIBUTE_MODEL_OFF, fingerprint

    # 13B altin listesi: 13B'den SONRAKI butun davranis bayraklari kapali olmalidir (15G dahil)
    off_13b = dataclasses.replace(OFF, rating_model=False)
    for seed, hg, ag, n_events, digest in GOLDEN_ATTRIBUTE_MODEL_OFF:
        r = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 78), seed=seed, config=off_13b).simulate()
        assert (r.home_score, r.away_score, len(r.events), fingerprint(r)) == (hg, ag, n_events, digest)


def test_stale_factors_are_ignored_by_a_flag_off_engine():
    """Onceki (bayrak acik) bir mactan kalmis carpan tasiyan MatchPlayer, bayrak kapali macta okunmaz."""
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 78)
    stale = am.PlayerFactors(attack=1.4, defense=1.4, card=1.4, fatigue=1.4, shooter=1.4, injury=1.4)
    with pytest.raises(AttributeError):
        stale.attack = 1.0                                      # salt okunur (onbellekte paylasilir)
    for p in home.players + away.players:
        p._am = stale
    r = MatchEngine(home, away, seed=0, config=OFF).simulate()
    fresh = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 78), seed=0, config=OFF).simulate()
    assert _digest(r) == _digest(fresh)
    assert all(p._am is None for p in home.players)


# ===========================================================================
# Bayrak ACIK
# ===========================================================================

def test_flag_on_prepares_sheet_attributes_stamina_and_factors():
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
    MatchEngine(home, away, seed=1, config=ON)
    for p in home.players:
        fresh = MatchPlayer(**{f: getattr(p, f) for f in ("id", "name", "position", "age", "overall", "pace",
                                                           "shooting", "passing", "defending", "dribbling",
                                                           "goalkeeping", "form", "morale")})
        assert p.sheet == cm.player_attributes(fresh)                  # profil sayfasiyla ayni (dunya tohumu yok)
        assert p.attributes == cm.as_fm_attributes(p.sheet)            # team_roles CM degerlerini okur
        assert p.stamina == float(p.sheet["stamina"])
        assert isinstance(p._am, am.PlayerFactors)


def test_injected_sheet_and_fm_values_are_kept():
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
    striker = home.players[-1]
    striker.sheet = dict.fromkeys(cm.ATTRIBUTE_KEYS, 11)
    fm_mid = home.players[7]
    fm_mid.attributes = {"crossing": 19, "stamina": 17, "injury_proneness": 20}
    fm_mid.stamina = 17.0
    MatchEngine(home, away, seed=1, config=ON)
    assert striker.sheet == dict.fromkeys(cm.ATTRIBUTE_KEYS, 11)
    assert fm_mid.sheet["crossing"] == 19 and fm_mid.sheet["stamina"] == 17
    assert fm_mid.attributes["crossing"] == 19 and fm_mid.attributes["injury_proneness"] == 20
    assert fm_mid.attributes["heading"] == fm_mid.sheet["heading"]      # FM'de olmayan sayfadan tamamlanir
    assert fm_mid._am.injury > 1.2                                       # gizli egilim 20 (FM) kurban agirligini artirir


def _digest(r) -> str:
    from tests.test_engine_golden_seeds import fingerprint

    return fingerprint(r)


def test_flag_on_is_deterministic_and_changes_outcomes():
    def sim(cfg):
        return MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 78), seed=5, config=cfg).simulate()

    assert _digest(sim(ON)) == _digest(sim(EngineConfig(attribute_model=True)))
    on = [_digest(MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 78), seed=s, config=ON).simulate())
          for s in range(6)]
    off = [_digest(MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 78), seed=s, config=OFF).simulate())
           for s in range(6)]
    assert on != off


def test_k12_sheet_and_factors_never_reach_repr_events_or_meta():
    home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
    r = MatchEngine(home, away, seed=2, config=ON).simulate()
    p = home.players[0]
    assert p.sheet and "sheet" not in repr(p) and "_am" not in repr(p)
    hidden = (am.PlayerFactors, am.TeamFactors, am.AttributeModelConfig, dict)
    names = [f.name for f in dataclasses.fields(match_engine.MatchEvent)]
    assert "sheet" not in names and not any(n.startswith("attr") for n in names)   # olay semasi 13B ile ayni
    for e in r.events:
        for name in names:
            value = getattr(e, name)
            assert not isinstance(value, hidden), (e.type, name)
        text = f"{e.description} {e.report or ''}"
        for label in cm.ATTRIBUTE_LABELS.values():                        # ozellik adi + sayi metne dokulmez
            assert f"{label} " not in text or not any(ch.isdigit() for ch in text.split(label, 1)[1][:4]), text


def test_attribute_model_draws_no_random_numbers():
    source = inspect.getsource(am)
    assert ".rng" not in source and "hash(" not in source      # macin RNG'si ve Python'un tuzlu hash()'i yok
    # 14B yolu: ozellik modelinin hicbir fonksiyonu rastgele sayi GORMEZ, yalnizca carpan uretir
    for fn in (am.player_factors, am.team_factors, am.prepare_player, am.factor,
               am.far_share, am.big_share, am.finish_factor, am.keeper_factor):
        assert "random" not in inspect.getsource(fn), fn.__name__
    # 15G: modulde `random` yalnizca TOHUMLU bir akis olarak kullanilabilir (crc32'den; gunun formu)
    for line in source.splitlines():
        if "random." in line and not line.lstrip().startswith("#"):
            assert "random.Random(zlib.crc32(" in line, line
    # motorun 14B kancalari: yalniz self._am kontrollu carpimlar; kancalarda rng cagrisi yok
    engine_src = inspect.getsource(match_engine)
    for line in engine_src.splitlines():
        if "_am" in line and "rng" in line:
            pytest.fail(f"14B kancasinda RNG: {line.strip()}")


# ===========================================================================
# Tutarlilik (§3.1): profil sayfasi = motorun okudugu sayfa
# ===========================================================================

def _orm_players(n: int = 50) -> list[Player]:
    import random

    rng = random.Random(14)
    out = []
    for i in range(n):
        position = rng.choice(list(Position))
        ovr = rng.randint(45, 92)
        engine = {a: max(20, min(99, ovr + rng.randint(-12, 12)))
                  for a in ("pace", "shooting", "passing", "defending", "dribbling")}
        engine["goalkeeping"] = ovr + 5 if position is Position.GK else rng.randint(10, 35)
        fm = {"crossing": rng.randint(1, 20), "stamina": rng.randint(1, 20)} if i % 5 == 0 else {}
        out.append(Player(id=5000 + i, name=f"Oyuncu {i}", age=rng.randint(17, 35), position=position,
                          overall_rating=ovr, form=50, morale=70, condition=100, fm_attributes=fm, **engine))
    return out


def test_profile_sheet_equals_engine_sheet_for_orm_players():
    players = _orm_players()
    for orm in players:
        profile = cm.player_attributes(orm, world_seed=2026)                # arayuz dunya tohumu gonderir: yok sayilir
        mp = MatchPlayer.from_orm(orm)
        assert profile == cm.player_attributes(mp)
        am.prepare_player(mp, CFG)
        assert mp.sheet == profile


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


@pytest.mark.integration
def test_profile_sheet_equals_engine_sheet_from_the_database():
    if os.getenv("CM_TEST_NO_DB") or not _db_available():
        pytest.skip("PostgreSQL erisilemiyor / CM_TEST_NO_DB (test dunyasi kurulmadi)")
    from sqlalchemy import select

    from database import SessionLocal

    with SessionLocal() as db:
        players = db.scalars(select(Player).order_by(Player.id).limit(50)).all()
        assert len(players) == 50
        for orm in players:
            profile = cm.player_attributes(orm, world_seed=12345)
            mp = MatchPlayer.from_orm(orm)
            am.prepare_player(mp, CFG)
            assert mp.sheet == profile, orm.id


# ===========================================================================
# Supurme dumani (n = 800 esli tohum, esiklerin yarisi). Tam supurme kanit kosusunda.
# ===========================================================================

SMOKE_N = 800
SMOKE = ("off_the_ball", "finishing", "handling", "aggression", "stamina", am.INJURY_TRAIT, "teamwork")


@pytest.mark.parametrize("attr", SMOKE)
def test_sweep_smoke_moves_the_target_metric(attr):
    reader = am.READERS[attr]
    # sakatlik nadir olay: dumanda taban oran 8 kat (kurban agirligi mekanizmasi ayni) -- kanit kosusu gercek oranla
    cfg = sweep_config(base_injury=0.036) if reader.metric == "injuries" else None
    r = attribute_sweep(attr, SMOKE_N, cfg=cfg)
    change = r.change(absolute=reader.absolute)
    assert change * reader.direction >= reader.threshold / 2, (attr, r.value("low"), r.value("high"), change)
    assert abs(r.points_delta) <= 0.20, (attr, r.points_delta)          # tek oyuncu baskinlik tavani


def test_dominance_ceiling_smoke_one_attribute_eleven_players():
    r = attribute_sweep("off_the_ball", SMOKE_N, squad=True, metric="points")
    assert r.points_delta <= 0.60, r.points_delta
