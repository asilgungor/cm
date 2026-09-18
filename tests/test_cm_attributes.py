"""
cm_attributes.py testleri (Faz 13I): CM 01/02 tarzi 31 ozellik (1-20) + tercih edilen ayak.

SAF testler: veritabani gerekmez (CM_TEST_NO_DB=1 ile calisir). Nufus: acik veri dunyasi (open_loader, canli
dunyanin ureticisi), akademi gencleri (youth), tum guc araligindan sentetik oyuncular (seed) ve gelisim /
gerileme gecirmis oyuncular (development) -- toplam ~9.000 oyuncu, dort mevki.

Kilitlenen sozlesme:
    * sayfa -> as_fm_attributes -> ratings.derive_engine_attributes: her motor ozelligi +-ENGINE_TOLERANCE (3),
      compute_overall +-OVERALL_TOLERANCE (2) -- 1-20 olceginin temsil edebildigi aralikta (24..99; saha
      oyuncusu kaleciligi 24..40)
    * determinizm (ayni surec, farkli surec + farkli PYTHONHASHSEED), 1-20 sinirlari, mevki anlami,
      bireysellik, yas etkisi, FM gecisi, gozlemci sisi (attribute_display)
"""

from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import sys
import timeit
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import cm_attributes as cm
import development
import fm_parser
import open_loader
import seed
import youth
from models import Player, Position
from ratings import (
    ENGINE_ATTRIBUTES,
    OUTFIELD_GK_CEILING,
    compute_overall,
    derive_engine_attributes,
    fm_scale,
    rate_fm_player,
)

ROOT = Path(__file__).resolve().parent.parent
WORLD_SEED = 2026


def _ns(pid: int, position: Position, age: int, overall: int, attrs: dict, fm: dict | None = None):
    return SimpleNamespace(id=pid, name=f"p{pid}", age=age, position=position, overall_rating=overall,
                           fm_attributes=fm or {}, **attrs)


def _engine(player) -> dict[str, int]:
    return {a: getattr(player, a) for a in ENGINE_ATTRIBUTES}


def _representable(position: Position, attr: str, value: int) -> int:
    """1-20 olceginin gosterebildigi motor degeri: taban fm_scale(1)=24, saha oyuncusu kaleciligi en fazla 40."""
    top = OUTFIELD_GK_CEILING if (attr == "goalkeeping" and position is not Position.GK) else 99
    return min(max(value, fm_scale(1)), top)


@pytest.fixture(scope="module")
def open_players() -> list[SimpleNamespace]:
    """Canli dunyanin ureticisi (acik veri) ile kurulan tam dunya: kulup kadrolari + akademi."""
    world = open_loader.build_open_world(12345)
    players, pid = [], 0
    for league in world.leagues:
        for club in league.clubs:
            for spec in club.players:
                pid += 1
                players.append(_ns(pid, spec.position, spec.age, spec.overall, spec.attributes))
    return players


@pytest.fixture(scope="module")
def population(open_players) -> list[SimpleNamespace]:
    rng = random.Random(7)
    players = list(open_players)
    pid = 100_000
    for _ in range(1500):                                    # akademi gencleri (guc 35-60, yas 16-19)
        spec = youth.make_youth(rng, "TR", rng.randint(16, 19), rng.uniform(-1, 1), set())
        pid += 1
        players.append(_ns(pid, spec.position, spec.age, spec.overall, spec.attributes))
    for _ in range(3000):                                    # tum guc araligi, tum mevkiler
        position = rng.choice(list(Position))
        target = rng.randint(30, 97)
        spec = seed.generate_player_spec(rng, "x", position, target, (max(1, target - 3), min(99, target + 3)))
        pid += 1
        players.append(_ns(pid, position, spec.age, spec.overall, spec.attributes))
    for _ in range(1500):                                    # gelisim / gerileme (overall ile ozellikler kayar)
        position = rng.choice(list(Position))
        target = rng.randint(45, 90)
        spec = seed.generate_player_spec(rng, "x", position, target, (target - 3, target + 3))
        steps = rng.randint(-12, 12)
        step = development.apply_progress(position, spec.overall, 99 if steps > 0 else None, spec.attributes,
                                          float(steps))
        pid += 1
        players.append(_ns(pid, position, rng.randint(17, 37), step.overall, step.attributes))
    return players


# ===========================================================================
# Anahtarlar, etiketler, gruplar
# ===========================================================================

def test_keys_labels_and_groups():
    assert cm.ATTRIBUTE_KEYS == (
        "acceleration", "aggression", "agility", "anticipation", "balance", "bravery", "creativity", "crossing",
        "decisions", "determination", "dribbling", "finishing", "flair", "handling", "heading", "influence",
        "jumping", "long_shots", "marking", "off_the_ball", "pace", "passing", "positioning", "reflexes",
        "set_pieces", "stamina", "strength", "tackling", "teamwork", "technique", "work_rate",
    )
    assert set(cm.ATTRIBUTE_LABELS) == set(cm.ATTRIBUTE_KEYS)
    assert cm.ATTRIBUTE_LABELS["acceleration"] == "Çabukluk"
    assert cm.ATTRIBUTE_LABELS["off_the_ball"] == "Topsuz oyun"
    assert len(set(cm.ATTRIBUTE_LABELS.values())) == 31                      # etiketler birbirinden ayri
    grouped = [k for _key, _label, keys in cm.ATTRIBUTE_GROUPS for k in keys]
    assert sorted(grouped) == sorted(cm.ATTRIBUTE_KEYS)                      # gruplar tam bolumleme
    assert cm.GOALKEEPER_KEYS == ("handling", "reflexes")
    assert set(cm.CHARACTER_KEYS) == {"determination", "influence", "flair", "aggression", "bravery", "work_rate",
                                      "teamwork"}


def test_reader_sets_document_what_the_game_reads():
    assert cm.ENGINE_BACKED_KEYS == {
        "pace", "acceleration", "finishing", "long_shots", "passing", "creativity", "technique", "tackling",
        "marking", "positioning", "dribbling", "agility", "handling", "reflexes"}
    assert cm.FM_READ_KEYS == {"stamina", "crossing", "heading", "jumping", "set_pieces", "influence"}
    assert cm.DISPLAY_ONLY_KEYS == {"aggression", "anticipation", "balance", "bravery", "decisions",
                                    "determination", "flair", "off_the_ball", "strength", "teamwork", "work_rate"}
    assert cm.UNREAD_FOR_GENERATED_KEYS == cm.FM_READ_KEYS | cm.DISPLAY_ONLY_KEYS
    assert len(cm.UNREAD_FOR_GENERATED_KEYS) == 17
    # FM_READ_KEYS gercekten team_roles / motor tarafindan FM adiyla okunuyor
    import inspect

    import match_engine
    import team_roles
    source = inspect.getsource(team_roles) + inspect.getsource(match_engine)
    for key in cm.FM_READ_KEYS:
        fm_names = {"jumping": ("jumping_reach",), "set_pieces": ("free_kicks", "corners"),
                    "influence": ("leadership",)}.get(key, (key,))
        assert any(f'"{name}"' in source for name in fm_names), key
    for key in cm.DISPLAY_ONLY_KEYS:                                          # ... bunlari kimse okumuyor
        assert f'"{key}"' not in inspect.getsource(team_roles)


# ===========================================================================
# Sinirlar, determinizm
# ===========================================================================

def test_bounds_order_and_fresh_dicts(population):
    for player in population[::7]:
        sheet = cm.player_attributes(player, WORLD_SEED)
        assert tuple(sheet) == cm.ATTRIBUTE_KEYS
        assert all(isinstance(v, int) and 1 <= v <= 20 for v in sheet.values()), sheet
    player = population[0]
    first = cm.player_attributes(player, WORLD_SEED)
    first["finishing"] = 99                                  # donen sozluk onbellege bagli degil
    assert cm.player_attributes(player, WORLD_SEED)["finishing"] != 99


def test_deterministic_within_process(population):
    sample = population[::97]
    before = [(cm.player_attributes(p, 11), cm.preferred_foot(p, 11), cm.player_role(p, 11)) for p in sample]
    cm.cache_clear()
    state = random.getstate()
    after = [(cm.player_attributes(p, 11), cm.preferred_foot(p, 11), cm.player_role(p, 11)) for p in sample]
    assert before == after
    assert random.getstate() == state                        # global RNG'ye dokunmaz


def test_identity_comes_from_the_player_not_the_world_seed(open_players):
    """14B §3.1: tohum sha256(f"{player.id}|{player.name}"). Dunya tohumu yok sayilir (motorda yoktur): profil
    sayfasi ile motorun okudugu sayfa ayni olmali."""
    player = open_players[3]
    assert cm.player_attributes(player, 1) == cm.player_attributes(player, 2) == cm.player_attributes(player)
    assert cm.identity_key(player) == f"{player.id}|{player.name}"
    renamed = SimpleNamespace(**{**vars(player), "name": player.name + "x"})
    other_id = SimpleNamespace(**{**vars(player), "id": player.id + 1_000_000})
    assert cm.player_attributes(renamed) != cm.player_attributes(player)
    assert cm.player_attributes(other_id) != cm.player_attributes(player)


_SUBPROCESS_CODE = r"""
import json, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import cm_attributes as cm
from models import Position
out = []
for pid, pos, age, ovr, eng in json.loads(sys.argv[2]):
    p = SimpleNamespace(id=pid, name=f"p{pid}", age=age, position=Position(pos), overall_rating=ovr, fm_attributes={},
                        **dict(zip(("pace", "shooting", "passing", "defending", "dribbling", "goalkeeping"), eng)))
    out.append([cm.player_attributes(p, 77), cm.preferred_foot(p, 77), cm.player_role(p, 77),
                [cm.attribute_display(v, 40, f"{pid}|{k}") for k, v in cm.player_attributes(p, 77).items()]])
out.append(sorted(m for m in sys.modules if m.split(".")[0] == "streamlit"))
print(json.dumps(out, ensure_ascii=False))
"""


def test_deterministic_across_processes(open_players):
    sample = open_players[:: max(1, len(open_players) // 12)][:12]
    spec = [[p.id, p.position.value, p.age, p.overall_rating, [getattr(p, a) for a in ENGINE_ATTRIBUTES]]
            for p in sample]
    results = []
    for hash_seed in ("0", "12345"):                         # tuzlu hash() kullanilsaydi burada ayrisirdi
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONIOENCODING="utf-8")
        done = subprocess.run([sys.executable, "-c", _SUBPROCESS_CODE, str(ROOT), json.dumps(spec)],
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=120, check=True)
        results.append(json.loads(done.stdout))
    assert results[0] == results[1]
    local = [[cm.player_attributes(p, 77), cm.preferred_foot(p, 77), cm.player_role(p, 77),
              [cm.attribute_display(v, 40, f"{p.id}|{k}") for k, v in cm.player_attributes(p, 77).items()]]
             for p in sample]
    assert results[0][:-1] == local
    assert results[0][-1] == []                              # saf modul: Streamlit yuklenmez


# ===========================================================================
# Tutarlilik: motorun alti ozelligi ve overall
# ===========================================================================

def test_consistency_with_engine_attributes_and_overall(population):
    attr_errors: Counter[int] = Counter()
    overall_errors: Counter[int] = Counter()
    for player in population:
        sheet = cm.player_attributes(player, WORLD_SEED)
        derived, estimated = derive_engine_attributes(player.position, cm.as_fm_attributes(sheet))
        assert estimated == []                               # her motor ozelligi sayfadan turetilebiliyor
        for attr in ENGINE_ATTRIBUTES:
            error = derived[attr] - _representable(player.position, attr, getattr(player, attr))
            assert abs(error) <= cm.ENGINE_TOLERANCE, (player, attr, derived, sheet)
            attr_errors[abs(error)] += 1
        overall_error = compute_overall(player.position, derived) - player.overall_rating
        assert abs(overall_error) <= cm.OVERALL_TOLERANCE, (player, derived)
        overall_errors[abs(overall_error)] += 1
    assert len(population) > 8000
    # Nitelik korumasi: tolerans istisna, kural degil
    total = sum(attr_errors.values())
    assert attr_errors[0] / total > 0.70 and (attr_errors[0] + attr_errors[1]) / total > 0.98
    assert overall_errors[0] / len(population) > 0.65


def test_engine_values_below_the_scale_floor_show_as_one():
    keeper = _ns(1, Position.GK, 17, 38, {"pace": 20, "shooting": 10, "passing": 28, "defending": 18,
                                          "dribbling": 13, "goalkeeping": 45})
    sheet = cm.player_attributes(keeper, 1)
    assert sheet["finishing"] == sheet["long_shots"] == 1                 # sut 10 < 24 (fm_scale(1))
    striker = _ns(2, Position.FWD, 25, 80, {"pace": 82, "shooting": 84, "passing": 72, "defending": 55,
                                            "dribbling": 82, "goalkeeping": 8})
    sheet = cm.player_attributes(striker, 1)
    assert sheet["handling"] == sheet["reflexes"] == 1


def test_consistency_holds_for_player_capped_at_99():
    """Gelisimde kalecilik 99 tavanina dayanip overall'i 99'a cikmis kaleci: agirlikli ortalama 96."""
    keeper = _ns(7965, Position.GK, 36, 99, {"pace": 87, "shooting": 54, "passing": 95, "defending": 86,
                                             "dribbling": 74, "goalkeeping": 99})
    derived, _ = derive_engine_attributes(Position.GK, cm.as_fm_attributes(cm.player_attributes(keeper, 99)))
    assert all(abs(derived[a] - getattr(keeper, a)) <= 3 for a in ENGINE_ATTRIBUTES)
    assert abs(compute_overall(Position.GK, derived) - 99) <= 2


# ===========================================================================
# Mevki anlami, bireysellik, yas
# ===========================================================================

@pytest.fixture(scope="module")
def by_role(open_players) -> dict[str, list[dict[str, int]]]:
    """Rol -> sayfalar; ayrica FB (LB+RB), W (LM/RM/LW/RW), outfield, keepers birlesik gruplari."""
    families = {"LB": "FB", "RB": "FB", "LM": "W", "RM": "W", "LW": "W", "RW": "W"}
    groups: dict[str, list[dict[str, int]]] = defaultdict(list)
    for player in open_players:
        role = cm.player_role(player, WORLD_SEED)
        sheet = cm.player_attributes(player, WORLD_SEED)
        groups[role].append(sheet)
        if role in families:
            groups[families[role]].append(sheet)
        groups["keepers" if role == "GK" else "outfield"].append(sheet)
    return groups


def _mean(sheets: list[dict[str, int]], key: str) -> float:
    return statistics.mean(s[key] for s in sheets)


def test_roles_cover_the_squad(by_role):
    assert set(by_role) >= {"GK", "CB", "LB", "RB", "DM", "CM", "AM", "LM", "RM", "LW", "RW", "ST"}
    assert all(code in cm.ROLE_LABELS for code in by_role if code not in ("FB", "W", "outfield", "keepers"))


def test_position_sense(by_role):
    cb, fb, dm, am, cmid, w, st, gk = (by_role[r] for r in ("CB", "FB", "DM", "AM", "CM", "W", "ST", "GK"))
    outfield = by_role["outfield"]
    # stoper: markaj / top kapma / kafa / pozisyon / guc / ziplama
    for key in ("marking", "tackling", "heading", "positioning", "strength", "jumping"):
        assert _mean(cb, key) > _mean(w, key) + 1.5, key
    assert _mean(cb, "marking") > _mean(w, "marking") + 3
    # bek: dayaniklilik / orta / top kapma / cabukluk (hiz motorda stoperle ayni dagilim: DEF ofseti)
    assert _mean(fb, "stamina") > _mean(cb, "stamina") + 1.5
    assert _mean(fb, "crossing") > _mean(cb, "crossing") + 3
    assert _mean(fb, "acceleration") > _mean(cb, "acceleration")
    assert _mean(fb, "tackling") > _mean(w, "tackling") + 2
    # on libero: top kapma / pozisyon / takim oyunu / calismak. DM ve AM ikisi de MID: motor ikisine AYNI defans
    # dagilimini verir, top kapma / markaj / pozisyon ortalamasi ona sabitlenir; fark grup ici sekilde ve motor
    # disi ozelliklerde gorunur.
    for key in ("teamwork", "work_rate", "aggression"):
        assert _mean(dm, key) > _mean(am, key) + 1, key
    for key in ("tackling", "positioning"):
        assert _mean(dm, key) > _mean(am, key) + 0.3, key
        assert _mean(dm, key) > _mean(dm, "marking"), key
        assert _mean(dm, key) > _mean(st, key) + 3, key
    # oyun kurucu: pas / yaraticilik / teknik / karar
    for key in ("creativity", "technique", "decisions"):
        assert _mean(am, key) > _mean(cb, key) + 1, key
    assert _mean(cmid, "passing") > _mean(st, "passing")
    # kanat: cabukluk / top surme / orta / fantezi. Motorun hiz ofsetleri DEF +1, MID 0, FWD +2: kanat oyuncusu
    # motorda stoperden hizli DEGILDIR; fark cabuklugun hiz grubundaki payinda gorunur.
    for key in ("dribbling", "crossing", "flair"):
        assert _mean(w, key) > _mean(cb, key) + 1, key
    assert _mean(w, "acceleration") > _mean(cb, "acceleration") + 1
    assert _mean(w, "acceleration") - _mean(w, "pace") > 0.5 > _mean(cb, "acceleration") - _mean(cb, "pace")
    assert _mean(w, "crossing") > _mean(st, "crossing") + 2
    assert _mean(w, "flair") > _mean(dm, "flair") + 3
    # santrfor: bitiricilik / topsuz oyun / kafa / karar
    for key in ("finishing", "off_the_ball"):
        assert _mean(st, key) > max(_mean(cb, key), _mean(dm, key)) + 3, key
    assert _mean(st, "heading") > _mean(w, "heading") + 2
    assert _mean(st, "decisions") > _mean(w, "decisions")
    # kaleci: elle kontrol / refleks / pozisyon / ziplama; saha becerileri dusuk
    assert _mean(gk, "handling") > 14 and _mean(gk, "reflexes") > 14
    assert all(s["handling"] <= 5 and s["reflexes"] <= 5 for s in outfield)
    assert _mean(gk, "jumping") > _mean(outfield, "jumping")
    assert _mean(gk, "positioning") > _mean(w, "positioning") + 3
    for key in ("finishing", "crossing", "off_the_ball", "heading", "dribbling", "set_pieces"):
        assert _mean(gk, key) < 10 and _mean(gk, key) < _mean(outfield, key) - 3, key


def test_individuality_identical_engine_values_differ():
    engine = {"pace": 80, "shooting": 74, "passing": 78, "defending": 70, "dribbling": 81, "goalkeeping": 30}
    sheets = [cm.player_attributes(_ns(pid, Position.MID, 25, 78, engine), 5) for pid in range(1, 41)]
    for a, b in zip(sheets, sheets[1:], strict=False):
        assert sum(a[k] != b[k] for k in cm.ATTRIBUTE_KEYS) >= 5
    # ... ama hepsi ayni motor degerlerine geri doner
    for sheet in sheets:
        derived, _ = derive_engine_attributes(Position.MID, cm.as_fm_attributes(sheet))
        assert all(abs(derived[a] - engine[a]) <= cm.ENGINE_TOLERANCE for a in ENGINE_ATTRIBUTES)


def test_two_right_wingers_flair_heavy_and_crossing_heavy():
    engine = {"pace": 84, "shooting": 83, "passing": 72, "defending": 56, "dribbling": 83, "goalkeeping": 30}
    wingers = [p for p in (_ns(pid, Position.FWD, 24, 80, engine) for pid in range(1, 800))
               if cm.player_role(p, 9) == "RW"]
    assert len(wingers) >= 50
    sheets = [cm.player_attributes(p, 9) for p in wingers]
    flair_heavy = [s for s in sheets if s["flair"] >= s["crossing"] + 3]
    crossing_heavy = [s for s in sheets if s["crossing"] >= s["flair"] + 5]
    assert flair_heavy and crossing_heavy


def test_age_moves_physical_to_youth_and_mental_to_experience():
    engine = {"pace": 78, "shooting": 70, "passing": 80, "defending": 72, "dribbling": 78, "goalkeeping": 30}
    young = [cm.player_attributes(_ns(pid, Position.MID, 19, 77, engine), 3) for pid in range(400)]
    old = [cm.player_attributes(_ns(pid, Position.MID, 33, 77, engine), 3) for pid in range(400)]
    for key in ("stamina", "agility"):
        assert _mean(young, key) > _mean(old, key) + 0.5, key
    assert _mean(young, "acceleration") - _mean(young, "pace") > _mean(old, "acceleration") - _mean(old, "pace")
    for key in ("decisions", "anticipation", "teamwork", "influence"):
        assert _mean(old, key) > _mean(young, key) + 0.8, key
    assert _mean(old, "positioning") > _mean(young, "positioning")


def test_character_attributes_have_a_sensible_spread(open_players):
    sheets = [cm.player_attributes(p, WORLD_SEED) for p in open_players]
    for key in cm.CHARACTER_KEYS:
        values = [s[key] for s in sheets]
        assert 7.5 <= statistics.mean(values) <= 14.5, key
        assert statistics.pstdev(values) >= 2.0, key
        assert min(values) <= 6 and max(values) >= 18, key


def test_sheet_is_stable_under_development():
    """Gelisim adimi (overall +1) sayfayi azar azar oynatir; rol ve ayak kimlikten gelir, degismez."""
    rng = random.Random(5)
    changes: Counter[int] = Counter()
    for pid in range(400):
        position = rng.choice(list(Position))
        target = rng.randint(45, 88)
        spec = seed.generate_player_spec(rng, "x", position, target, (target - 3, target + 3))
        before = _ns(pid, position, spec.age, spec.overall, spec.attributes)
        step = development.apply_progress(position, spec.overall, 99, spec.attributes, 1.0)
        after = _ns(pid, position, spec.age, step.overall, step.attributes)
        a, b = cm.player_attributes(before, 3), cm.player_attributes(after, 3)
        for key in cm.ATTRIBUTE_KEYS:
            changes[b[key] - a[key]] += 1
        assert cm.player_role(before, 3) == cm.player_role(after, 3)
        assert cm.preferred_foot(before, 3) == cm.preferred_foot(after, 3)
    assert max(abs(d) for d in changes) <= 3
    assert sum(d * n for d, n in changes.items()) > 0                        # gelisim sayfada gorunur
    assert changes[0] / sum(changes.values()) > 0.7


# ===========================================================================
# Tercih edilen ayak
# ===========================================================================

def test_preferred_foot_split_and_sides(population):
    feet = Counter(cm.preferred_foot(p, WORLD_SEED) for p in population)
    total = sum(feet.values())
    assert set(feet) == {"Sağ", "Sol", "Her iki ayak"}
    assert 0.70 <= feet["Sağ"] / total <= 0.80
    assert 0.15 <= feet["Sol"] / total <= 0.25
    assert 0.03 <= feet["Her iki ayak"] / total <= 0.08
    by_side: dict[str, Counter] = defaultdict(Counter)
    for p in population:
        role = cm.player_role(p, WORLD_SEED)
        side = "left" if role in ("LB", "LM", "LW") else "right" if role in ("RB", "RM", "RW") else "centre"
        by_side[side][cm.preferred_foot(p, WORLD_SEED)] += 1
    left = by_side["left"]
    assert left["Sol"] / sum(left.values()) > 0.55
    assert by_side["right"]["Sağ"] / sum(by_side["right"].values()) > 0.80


def test_fm_foot_values_win():
    engine = {"pace": 70, "shooting": 70, "passing": 70, "defending": 70, "dribbling": 70, "goalkeeping": 30}
    lefty = _ns(1, Position.MID, 25, 70, engine, {"left_foot": 20, "right_foot": 8})
    both = _ns(2, Position.MID, 25, 70, engine, {"left_foot": 16, "right_foot": 18})
    assert cm.preferred_foot(lefty, 1) == "Sol" and cm.preferred_foot(both, 1) == "Her iki ayak"


# ===========================================================================
# FM oyunculari
# ===========================================================================

def _fm_player(pid: int, position: Position, fm: dict[str, float], ca: int | None = None):
    overall, attrs, _estimated = rate_fm_player(position, fm, ca)
    return _ns(pid, position, 27, overall, attrs, fm)


def test_fm_players_pass_their_real_values_through():
    rng = random.Random(11)
    for pid in range(60):
        position = rng.choice(list(Position))
        fm = {key: rng.randint(1, 20) for key in fm_parser.ATTRIBUTE_ALIASES}
        sheet = cm.player_attributes(_fm_player(pid, position, fm, rng.randint(60, 180)), 4)
        for key in cm.ATTRIBUTE_KEYS:
            expected = {"creativity": fm["vision"], "influence": fm["leadership"], "jumping": fm["jumping_reach"],
                        "set_pieces": int((fm["free_kicks"] + fm["corners"]) / 2 + 0.5)}.get(key, fm.get(key))
            assert sheet[key] == expected, (key, sheet[key], expected)


def test_fm_partial_export_fills_only_the_gaps():
    fm = {"finishing": 16, "pace": 15, "tackling": 4, "vision": 13.5, "stamina": 12}
    player = _fm_player(1, Position.FWD, fm)
    sheet = cm.player_attributes(player, 4)
    assert sheet["finishing"] == 16 and sheet["pace"] == 15 and sheet["tackling"] == 4 and sheet["stamina"] == 12
    assert sheet["creativity"] == 14                                        # gozlemci araligi 13.5 -> 14
    assert all(1 <= v <= 20 for v in sheet.values())
    derived, _ = derive_engine_attributes(Position.FWD, cm.as_fm_attributes(sheet))
    # FM'de hic kaynagi olmayan grup (dribbling) motor degerine oturur
    assert abs(derived["dribbling"] - player.dribbling) <= cm.ENGINE_TOLERANCE


def test_as_fm_attributes_maps_names_back():
    sheet = dict.fromkeys(cm.ATTRIBUTE_KEYS, 10) | {"creativity": 17, "influence": 15, "jumping": 12,
                                                    "set_pieces": 18}
    fm = cm.as_fm_attributes(sheet)
    assert fm["vision"] == 17 and fm["leadership"] == 15 and fm["jumping_reach"] == 12
    assert fm["free_kicks"] == fm["corners"] == 18
    assert "creativity" not in fm and "set_pieces" not in fm


# ===========================================================================
# Oyuncu nesneleri (duck typing)
# ===========================================================================

def test_orm_player_spec_and_match_player_agree():
    import match_engine

    engine = {"pace": 81, "shooting": 64, "passing": 76, "defending": 84, "dribbling": 70, "goalkeeping": 33}
    orm = Player(id=42, name="Orm Oyuncu", age=28, position=Position.DEF, overall_rating=80, form=55, morale=70,
                 condition=100, fm_attributes={}, **engine)
    plain = SimpleNamespace(**{**vars(_ns(42, Position.DEF, 28, 80, engine)), "name": "Orm Oyuncu"})
    match_player = match_engine.MatchPlayer.from_orm(orm)
    assert cm.player_attributes(orm, 8) == cm.player_attributes(plain, 8) == cm.player_attributes(match_player, 8)
    assert cm.preferred_foot(orm, 8) == cm.preferred_foot(match_player, 8)
    spec = seed.PlayerSpec(name="Spec", age=28, position=Position.DEF, overall=80, attributes=dict(engine),
                           form=50, morale=70, contract_years=3, market_value=0)
    sheet = cm.player_attributes(spec, 8)                                   # kimliksiz: ad kullanilir
    derived, _ = derive_engine_attributes(Position.DEF, cm.as_fm_attributes(sheet))
    assert all(abs(derived[a] - engine[a]) <= cm.ENGINE_TOLERANCE for a in ENGINE_ATTRIBUTES)


# ===========================================================================
# Gozlemci sisi
# ===========================================================================

def test_attribute_display_exact_unknown_and_edges():
    assert cm.attribute_display(15, 70, "1|pace") == "15"
    assert cm.attribute_display(15, 100, "1|pace") == "15"
    assert cm.attribute_display(15, 0, "1|pace") == "?"
    assert cm.attribute_display(15, 24.9, "1|pace") == "?"
    assert cm.attribute_display(15, None, "1|pace") == "?"
    assert cm.attribute_display(None, 90, "1|pace") == "?"
    assert cm.attribute_display(1, 25, "1|pace").startswith("1-")          # sinirda pencere kayar
    assert cm.attribute_display(20, 25, "1|pace").endswith("-20")


def test_attribute_display_ranges_contain_truth_and_narrow():
    keys = [f"{pid}|{key}" for pid in range(1, 26) for key in cm.ATTRIBUTE_KEYS]
    knowledge_levels = list(range(25, 70)) + [69.5]
    offsets = set()
    for player_key in keys:
        for value in range(1, 21):
            previous = None
            for knowledge in knowledge_levels:
                text = cm.attribute_display(value, knowledge, player_key)
                low, high = (int(t) for t in text.split("-"))
                assert 1 <= low <= value <= high <= 20
                assert high - low == cm.range_width(knowledge) >= 1
                if previous is not None:                                    # bilgi arttikca ic ice daralir
                    assert previous[0] <= low and high <= previous[1]
                previous = (low, high)
            assert cm.attribute_display(value, 70, player_key) == str(value)
        offsets.add(cm.attribute_range(10, 25, player_key)[0])
    assert len(offsets) == cm.range_width(25) + 1                          # kayma anahtara gore degisir
    assert cm.range_width(25) == 4 and cm.range_width(69) == 1


def test_attribute_display_is_stable_between_calls():
    first = [cm.attribute_display(v, k, f"9|{key}") for v in range(1, 21) for k in (30, 45, 60)
             for key in cm.ATTRIBUTE_KEYS]
    second = [cm.attribute_display(v, k, f"9|{key}") for v in range(1, 21) for k in (30, 45, 60)
              for key in cm.ATTRIBUTE_KEYS]
    assert first == second
    assert cm.attribute_display(12, 40, (9, "pace")) == cm.attribute_display(12, 40, (9, "pace"))


# ===========================================================================
# Hiz
# ===========================================================================

def test_squad_sheet_is_cheap(open_players):
    squad = [_ns(900_000 + i, p.position, p.age, p.overall_rating, _engine(p)) for i, p in enumerate(open_players[:30])]

    def run():
        for p in squad:
            cm.player_attributes(p, 31)
            cm.preferred_foot(p, 31)

    def cold():
        cm.cache_clear()
        run()

    cold_ms = min(timeit.repeat(cold, number=1, repeat=5)) * 1000
    run()
    warm_ms = min(timeit.repeat(run, number=1, repeat=5)) * 1000
    # Olcum makinesinde (yuklu) ~3 ms / ~0.3 ms; esikler CI gurultusu icin genis
    assert cold_ms < 25, cold_ms
    assert warm_ms < 3, warm_ms
