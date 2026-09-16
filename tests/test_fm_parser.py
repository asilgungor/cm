"""
FM veri entegrasyonu testleri (6. Asama): fm_parser, club_directory, ratings,
seed'in FM dunyasi kurulumu (DB'siz).
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fm_parser  # noqa: E402
import ratings  # noqa: E402
import seed  # noqa: E402
from club_directory import (  # noqa: E402
    canonical_league,
    lookup_club,
    normalize,
    reputation_from_strength,
)
from fm_parser import (  # noqa: E402
    parse_attribute,
    parse_expiry_year,
    parse_money,
    parse_position,
    parse_rows,
    parse_wage,
)
from models import Position  # noqa: E402
from name_masking import find_leaks, mask_club_name, mask_player_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "fm" / "sample_fm_export.html"

HEADER = ["Name", "Position", "Nat", "Age", "Club", "Transfer Value", "Wage", "Expires",
          "CA", "Fin", "Pac", "Acc", "Pas", "Tck", "Mar", "Pos", "Dri", "Han", "Ref"]


def row(name="Deneme Oyuncu", position="ST (C)", nat="TUR", age="24", club="Galatasaray",
        value="€12M", wage="€45K p/w", expires="30/6/2029", ca="140", fin="15", pac="14",
        acc="15", pas="11", tck="5", mar="4", pos="9", dri="14", han="-", ref="-"):
    return [name, position, nat, age, club, value, wage, expires, ca, fin, pac, acc, pas, tck, mar, pos, dri, han, ref]


# ===========================================================================
# 1) Hucre ayristiricilari
# ===========================================================================

@pytest.mark.parametrize("text,expected", [
    ("€45M", 45_000_000),
    ("€850K", 850_000),
    ("€1.5M", 1_500_000),
    ("€1,5M", 1_500_000),
    ("£1.2M", 1_404_000),
    ("$10M", 9_200_000),
    ("€10M - €20M", 15_000_000),
    ("€1.250.000", 1_250_000),
    ("12,500", 12_500),
    ("Not for Sale", None),
    ("Satılık Değil", None),
    ("-", None),
    ("", None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("€150K p/w", 150_000),
    ("€150K", 150_000),                          # donem yok -> haftalik
    ("£40K p/m", round(40_000 * 1.17 * 12 / 52)),
    ("€5.2M p/a", 100_000),
    ("€8.500 pw", 8_500),
    ("-", None),
])
def test_parse_wage_normalizes_to_weekly(text, expected):
    assert parse_wage(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("GK", Position.GK), ("KL", Position.GK),
    ("D (C)", Position.DEF), ("D (RL), WB (R)", Position.DEF), ("WB (L)", Position.DEF),
    ("DM, M (C)", Position.MID), ("M/AM (C)", Position.MID), ("AM (RL), ST (C)", Position.MID),
    ("DOS", Position.MID), ("OOS", Position.MID),
    ("ST (C)", Position.FWD), ("F (C)", Position.FWD), ("FV", Position.FWD),
    ("", None), ("Menajer", None),
])
def test_parse_position(text, expected):
    assert parse_position(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("14", 14.0), ("12-15", 13.5), ("20", 20.0), ("-", None), ("25", None), ("0", None), ("abc", None),
])
def test_parse_attribute(text, expected):
    assert parse_attribute(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("30/6/2027", 2027), ("2028-06-30", 2028), ("30.06.2029", 2029), ("2030", 2030), ("-", None),
])
def test_parse_expiry_year(text, expected):
    assert parse_expiry_year(text) == expected


# ===========================================================================
# 2) Satir/baslik isleme
# ===========================================================================

def test_parse_rows_maps_fields_and_attributes():
    report = parse_rows([HEADER, row()])
    assert not report.warnings and not report.skipped
    p = report.players[0]
    assert (p.name, p.age, p.club, p.position) == ("Deneme Oyuncu", 24, "Galatasaray", Position.FWD)
    assert p.nationality == "TUR"
    assert (p.current_ability, p.value_eur, p.wage_eur_weekly, p.contract_expiry_year) == (140, 12_000_000, 45_000, 2029)
    assert p.fm_attributes["finishing"] == 15 and p.fm_attributes["positioning"] == 9
    assert "handling" not in p.fm_attributes                     # '-' atlandi


def test_ambiguous_nat_and_pos_are_resolved_by_values():
    # Nat sayisal -> Natural Fitness; Pos metin -> mevki (Position sutunu yokken)
    header = ["Name", "Pos", "Nat", "Age", "Club", "Fin"]
    data = [["A B", "ST (C)", "15", "22", "Galatasaray", "14"],
            ["C D", "D (C)", "12", "27", "Galatasaray", "6"]]
    report = parse_rows([header, *data])
    a, c = report.players
    assert a.position is Position.FWD and c.position is Position.DEF
    assert a.fm_attributes["natural_fitness"] == 15 and a.nationality is None


def test_best_pos_column_wins_over_position_list():
    header = ["Name", "Position", "Best Pos", "Age", "Club", "Fin"]
    report = parse_rows([header, ["X Y", "D (C), DM, M (C)", "M (C)", "25", "Arsenal", "10"]])
    assert report.players[0].position is Position.MID


def test_header_found_after_title_rows_and_repeated_headers_skipped():
    rows = [["Football Manager 2026"], ["Oyuncu Arama"], HEADER, row(), HEADER, row(name="İkinci Oyuncu")]
    report = parse_rows(rows)
    assert [p.name for p in report.players] == ["Deneme Oyuncu", "İkinci Oyuncu"]


def test_bad_rows_are_skipped_with_reasons():
    rows = [HEADER, row(name=""), row(position="Antrenör"), row(age="yaşsız"), row(name="Geçerli")]
    report = parse_rows(rows, source="t.csv")
    assert [p.name for p in report.players] == ["Geçerli"]
    reasons = [r for _, _, r in report.skipped]
    assert any("isim yok" in r for r in reasons)
    assert any("mevki" in r for r in reasons)
    assert any("yaş" in r for r in reasons)
    assert all(src == "t.csv" and line >= 2 for src, line, _ in report.skipped)


def test_missing_header_or_position_column_warns():
    assert parse_rows([["a", "b"], ["c", "d"]]).warnings
    no_position = parse_rows([["Name", "Age", "Club"], ["A", "20", "Inter"]])
    assert no_position.warnings and not no_position.players


def test_turkish_attribute_headers():
    header = ["Oyuncu", "Mevki", "Yaş", "Kulüp", "Bitiricilik", "Hız", "Top Kapma"]
    report = parse_rows([header, ["Ali Veli", "FV", "23", "Beşiktaş JK", "16", "15", "4"]])
    p = report.players[0]
    assert p.position is Position.FWD and p.club == "Beşiktaş JK"
    assert p.fm_attributes == {"finishing": 16, "pace": 15, "tackling": 4}


# ===========================================================================
# 3) Dosya bicimleri ve kodlamalar
# ===========================================================================

def test_sample_html_file():
    report = fm_parser.parse_file(SAMPLE, mask_names=False)                 # ham okuma
    assert report.files == [("sample_fm_export.html", "html")]
    assert len(report.players) == 49 and not report.skipped
    names = {p.name for p in report.players}
    assert any("ı" in n or "ğ" in n or "ü" in n for n in names)      # Turkce/Almanca karakterler korunur
    assert {p.club for p in report.players} >= {"Galatasaray", "Fenerbahçe", "FC Bayern München"}
    assert report.unknown_columns == {"Inf"}


def test_csv_semicolon_in_windows_1254(tmp_path):
    path = tmp_path / "liste.csv"
    lines = [";".join(HEADER), ";".join(row(name="Çağrı Şahin", club="Fenerbahçe"))]
    path.write_bytes("\n".join(lines).encode("cp1254"))
    report = fm_parser.parse_file(path, mask_names=False)
    assert report.files[0][1] == "csv"
    assert report.players[0].name == "Çağrı Şahin" and report.players[0].club == "Fenerbahçe"


def test_txt_pipe_export_utf16(tmp_path):
    path = tmp_path / "liste.txt"
    sep = "| " + " | ".join("-" * 3 for _ in HEADER) + " |"
    lines = ["| " + " | ".join(HEADER) + " |", sep, "| " + " | ".join(row()) + " |"]
    path.write_bytes("\n".join(lines).encode("utf-16"))
    report = fm_parser.parse_file(path)
    assert report.files[0][1] == "txt"
    assert report.players[0].fm_attributes["finishing"] == 15


def test_parse_files_deduplicates(tmp_path):
    for i in range(2):
        (tmp_path / f"p{i}.csv").write_text("\n".join([",".join(HEADER), ",".join(row())]), encoding="utf-8")
    report = fm_parser.parse_files(sorted(tmp_path.iterdir()))
    assert len(report.players) == 1 and report.duplicates == 1
    # tekrar ayiklama ham adla yapilir, ardindan adlar maskelenir
    assert report.masked and report.players[0].club == "Istanbul Lions"
    assert report.players[0].name == mask_player_name("Deneme Oyuncu") == "Deneme Oyancu"


def test_masked_read_changes_only_player_name_text(tmp_path):
    """Okuma sinirinda yalnizca ad (ve kulup/lig) metni degisir; PA/CA/yas/mevki/uyruk/ozellikler aynen kalir."""
    header = [*HEADER, "PA", "UID"]
    players = [
        ("Orkun Kökçü", "Beşiktaş", "M (C)", "TUR", "24", "150", "165", "1001"),
        ("Hakan Çalhanoğlu", "Galatasaray", "DM, M (C)", "TUR", "31", "160", "162", "1002"),
        ("Robert Lewandowski", "FC Barcelona", "ST (C)", "POL", "37", "158", "170", "1003"),
        ("Kylian Mbappé", "Real Madrid", "AM (L), ST (C)", "FRA", "27", "185", "192", "1004"),
        ("Erling Haaland", "Manchester City", "ST (C)", "NOR", "25", "183", "195", "1005"),
    ]
    lines = [",".join(header)]
    for name, club, position, nat, age, ca, pa, uid in players:
        cells = row(name=name, club=club, position=f'"{position}"', nat=nat, age=age, ca=ca)
        lines.append(",".join([*cells, pa, uid]))
    path = tmp_path / "gercek_ornek.csv"
    path.write_text("\n".join(lines), encoding="utf-8")

    raw = fm_parser.parse_file(path, mask_names=False)
    masked = fm_parser.parse_file(path)
    assert [p.name for p in raw.players] == [p[0] for p in players]
    assert [p.name for p in masked.players] == ["Orkan Kökçü", "Hakan Çalhano", "Robert Lewandow",
                                                "Kylian Mbeppe", "Erling Harland"]
    assert masked.masked and masked.masked_players == 5
    for before, after in zip(raw.players, masked.players, strict=True):
        a, b = dataclasses.asdict(before), dataclasses.asdict(after)
        for changed in ("name", "club", "league"):
            a.pop(changed), b.pop(changed)
        assert a == b, before.name                                            # PA, CA, yas, mevki, 1-20...
        assert after.potential_ability and after.current_ability and after.fm_attributes
        assert after.club == mask_club_name(before.club) and not find_leaks([after.club])


def test_discover_files_skips_samples_by_default(tmp_path):
    (tmp_path / "sample_demo.html").write_text("<table></table>", encoding="utf-8")
    (tmp_path / "gercek.csv").write_text("x", encoding="utf-8")
    (tmp_path / "notlar.md").write_text("x", encoding="utf-8")
    assert [p.name for p in fm_parser.discover_files(tmp_path)] == ["gercek.csv"]
    assert len(fm_parser.discover_files(tmp_path, include_samples=True)) == 2
    assert fm_parser.discover_files(tmp_path / "yok") == []


# ===========================================================================
# 4) Kulup rehberi
# ===========================================================================

@pytest.mark.parametrize("name,expected", [
    ("Bayern Münih", "Bayern München"),
    ("FC Bayern München", "Bayern München"),
    ("Bayern Munich", "Bayern München"),
    ("Man Utd", "Manchester United"),
    ("Beşiktaş JK", "Beşiktaş"),
    ("BESIKTAS", "Beşiktaş"),
    ("İstanbul Başakşehir", "Başakşehir"),
    ("Real Madrid CF", "Real Madrid"),
    ("AC Milan", "Milan"),
    ("Paris SG", "Paris Saint-Germain"),
])
def test_club_aliases(name, expected):
    assert lookup_club(name).name == expected


def test_unknown_club_and_league_mapping():
    assert lookup_club("Kuzey Yıldızı SK") is None
    assert normalize("FC Bayern München") == "bayern munchen"
    assert canonical_league("Turkish Super Lig") == ("Trendyol Süper Lig", "Türkiye")
    assert canonical_league("Spanish First Division") == ("LaLiga", "İspanya")
    assert canonical_league("2. Bundesliga")[0] == "2. Bundesliga"         # 1. lig ile karismaz
    assert canonical_league("Eredivisie") == ("Eredivisie", "Diğer")
    assert canonical_league("  ") is None
    assert reputation_from_strength(85) > reputation_from_strength(70)


# ===========================================================================
# 5) Guc donusumleri
# ===========================================================================

def test_fm_scale_and_ca_curves():
    assert ratings.fm_scale(1) == 24 and ratings.fm_scale(20) == 99
    assert ratings.fm_scale(10) < ratings.fm_scale(15) < ratings.fm_scale(18)
    assert ratings.ca_to_overall(100) < ratings.ca_to_overall(150) < ratings.ca_to_overall(180) <= 99


def test_rate_fm_player_uses_ca_and_derives_attributes():
    fm = {"finishing": 17, "long_shots": 15, "composure": 16, "pace": 16, "acceleration": 17,
          "passing": 12, "dribbling": 15, "technique": 14}
    overall, attrs, estimated = ratings.rate_fm_player(Position.FWD, fm, current_ability=160)
    assert overall == ratings.ca_to_overall(160)
    assert attrs["shooting"] > attrs["passing"] > attrs["goalkeeping"]
    assert attrs["goalkeeping"] <= ratings.OUTFIELD_GK_CEILING
    assert estimated == ["defending"]                              # tck/mar/pos yoktu


def test_rate_fm_player_without_ca_is_consistent():
    fm = {"reflexes": 17, "handling": 16, "one_on_ones": 15, "aerial_reach": 16, "passing": 9, "pace": 8}
    overall, attrs, _ = ratings.rate_fm_player(Position.GK, fm)
    assert overall == ratings.compute_overall(Position.GK, attrs)
    assert attrs["goalkeeping"] > 80


# ===========================================================================
# 6) FM dunyasi kurulumu (DB'siz)
# ===========================================================================

def test_build_fm_world_from_sample():
    report = fm_parser.parse_files([SAMPLE])
    world = seed.build_fm_world(report, rng_seed=1)
    assert world.source == "fm"
    assert {lg.name for lg in world.leagues} == {"Türkiye Elit Ligi", "İspanya Elit Ligi", "Almanya Elit Ligi"}
    clubs = {c.name: c for c in world.clubs}
    assert set(clubs) == {"Istanbul Lions", "Kadıköy Canaries", "Madrid Blancos", "Catalonia Blaugrana",
                          "München Roten", "Ruhr Schwarzgelb"}
    assert any(mask_club_name("Kuzey Yıldızı SK") in n for n in world.notes)
    assert not find_leaks(clubs) and not any("Kuzey Yıldızı" in n for n in world.notes)

    for club in world.clubs:
        positions = [p.position for p in club.players]
        assert len(club.players) >= seed.FM_MIN_SQUAD
        for position, minimum in seed.FM_MIN_PER_POSITION.items():
            assert positions.count(position) >= minimum, (club.name, position)
        fm_players = [p for p in club.players if p.data_source == "fm"]
        academy = [p for p in club.players if p.data_source == "academy"]
        assert len(academy) == club.academy_added
        assert fm_players and all(p.fm_attributes for p in fm_players)
        # Altyapi oyunculari gercek kadronun altinda kalir
        assert max(p.overall for p in academy) < max(p.overall for p in fm_players)

    assert clubs["Madrid Blancos"].reputation > clubs["Istanbul Lions"].reputation
    assert clubs["Madrid Blancos"].transfer_budget > clubs["Istanbul Lions"].transfer_budget


def test_build_fm_world_is_deterministic():
    report = fm_parser.parse_files([SAMPLE])
    a, b = seed.build_fm_world(report, rng_seed=7), seed.build_fm_world(report, rng_seed=7)
    assert [(p.name, p.overall) for p in a.clubs[0].players] == [(p.name, p.overall) for p in b.clubs[0].players]


def test_trim_squad_keeps_keepers():
    rng = __import__("random").Random(1)
    players = [seed.generate_player_spec(rng, f"O{i}", Position.MID, 85, (80, 90)) for i in range(30)]
    players += [seed.generate_player_spec(rng, f"K{i}", Position.GK, 60, (55, 65)) for i in range(3)]
    trimmed = seed.trim_squad(players)
    assert len(trimmed) == seed.FM_MAX_SQUAD
    assert sum(p.position is Position.GK for p in trimmed) == 3


def test_resolve_world_modes(tmp_path):
    empty = tmp_path / "bos"
    empty.mkdir()
    auto = seed.resolve_world(3, source="auto", fm_dir=empty)
    assert auto.source == "synthetic" and "dışa aktarımı yok" in auto.notes[0]
    with pytest.raises(seed.SeedError, match="bulunamadı"):
        seed.resolve_world(3, source="fm", fm_dir=empty)
    with pytest.raises(seed.SeedError, match="Dosya bulunamadı"):
        seed.resolve_world(3, source="fm", fm_paths=[tmp_path / "yok.html"])

    lonely = tmp_path / "tek.csv"
    lonely.write_text("\n".join([",".join(HEADER), ",".join(row())]), encoding="utf-8")
    with pytest.raises(seed.SeedError, match="oynanabilir lig"):
        seed.resolve_world(3, source="fm", fm_paths=[lonely])                  # tek kulup -> lig kurulamaz

    assert seed.resolve_world(3, source="synthetic").player_count == 360
