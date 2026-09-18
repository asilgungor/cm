"""
14C: yeni dunyanin kaynagi -- seed.new_world_source() cozucusu (SAF; veritabani gerekmez).

    CM_TEST_NO_DB=1 python -m pytest -q tests/test_new_world_source.py

Siralama: OFM_NEW_WORLD_SOURCE (gecerliyse) > data/fm disa aktarimi ('sample_' haric) > data/open okunabiliyorsa
'open' > 'synthetic'. CLI'nin ve seed()'in varsayilani 'auto' degismez. tests/conftest.py ortam degiskenini
'synthetic' yapar; bu dosyadaki testler onu gerekince monkeypatch ile kaldirir.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import open_loader  # noqa: E402
import seed  # noqa: E402


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(seed.NEW_WORLD_SOURCE_ENV, raising=False)


@pytest.fixture
def empty_fm(tmp_path) -> Path:
    fm_dir = tmp_path / "fm"
    fm_dir.mkdir()
    (fm_dir / "README.md").write_text("disa aktarim degil", encoding="utf-8")
    (fm_dir / "sample_fm_export.html").write_text("<table></table>", encoding="utf-8")   # ornek sayilmaz
    return fm_dir


def test_valid_env_value_wins_and_is_read_at_call_time(monkeypatch, empty_fm):
    for value in seed.NEW_WORLD_SOURCES:
        monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, value)
        assert seed.new_world_source(fm_dir=empty_fm) == value
    monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, "  Synthetic ")                 # bosluk / harf buyuklugu
    assert seed.new_world_source(fm_dir=empty_fm) == "synthetic"
    assert seed.NEW_WORLD_SOURCES == ("open", "synthetic", "auto", "fm")


def test_invalid_or_empty_env_value_falls_back_to_detection(monkeypatch, empty_fm):
    for value in ("gercek", "strong", "", "   "):
        monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, value)
        assert seed.new_world_source(fm_dir=empty_fm) == "open"


def test_real_fm_export_in_data_fm_means_fm(no_env, empty_fm):
    (empty_fm / "benim_ligim.html").write_text("<table></table>", encoding="utf-8")
    assert seed.new_world_source(fm_dir=empty_fm) == "fm"


def test_empty_data_fm_means_open_world(no_env, empty_fm, tmp_path):
    assert seed.new_world_source(fm_dir=empty_fm) == "open"
    assert seed.new_world_source(fm_dir=tmp_path / "yok") == "open"                # klasor hic yok
    assert seed.new_world_source(fm_dir=empty_fm, open_dir=open_loader.OPEN_DATA_DIR) == "open"


def test_broken_or_missing_open_data_means_synthetic(no_env, empty_fm, tmp_path):
    assert seed.new_world_source(fm_dir=empty_fm, open_dir=tmp_path / "yok") == "synthetic"

    broken = tmp_path / "open"
    broken.mkdir()
    (broken / open_loader.CLUBS_FILE).write_text("{bozuk json", encoding="utf-8")
    (broken / open_loader.LEAGUES_FILE).write_text("{}", encoding="utf-8")
    assert seed.new_world_source(fm_dir=empty_fm, open_dir=broken) == "synthetic"

    unlicensed = tmp_path / "lisanssiz"
    unlicensed.mkdir()
    for name, schema in ((open_loader.CLUBS_FILE, open_loader.CLUBS_SCHEMA),
                         (open_loader.LEAGUES_FILE, open_loader.LEAGUES_SCHEMA)):
        (unlicensed / name).write_text(json.dumps({"schema": schema, "leagues": [{}]}), encoding="utf-8")
    assert seed.new_world_source(fm_dir=empty_fm, open_dir=unlicensed) == "synthetic"


def test_cli_and_seed_defaults_stay_auto_while_web_entry_points_use_the_resolver():
    import accounts
    import worlds

    assert seed.build_arg_parser().parse_args([]).source == "auto"
    assert inspect.signature(seed.seed).parameters["source"].default == "auto"
    assert inspect.signature(seed.resolve_world).parameters["source"].default == "auto"
    assert inspect.signature(accounts.register).parameters["source"].default is None
    assert inspect.signature(worlds.create_world).parameters["source"].default is None
    assert inspect.signature(accounts._build_world).parameters["open_sample"].default is False
