"""
14C: yeni dunyanin kaynagi -- seed.new_world_source() cozucusu (SAF; veritabani gerekmez).

    CM_TEST_NO_DB=1 python -m pytest -q tests/test_new_world_source.py

Siralama: OFM_NEW_WORLD_SOURCE (gecerliyse) > data/open okunabiliyorsa 'open' > 'synthetic'.
16A-0 (sahip vetosu K-S11): data/fm'deki FM disa aktarimi ARTIK KENDILIGINDEN SECILMEZ; 'fm' yalnizca
ortam degiskeniyle acikca verilince doner (paylasilan dunyada SI verisi baskalarina acilmasin).
CLI'nin ve seed()'in varsayilani 'auto' degismez. tests/conftest.py ortam degiskenini 'synthetic' yapar;
bu dosyadaki testler onu gerekince monkeypatch ile kaldirir.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fm_parser  # noqa: E402
import open_loader  # noqa: E402
import seed  # noqa: E402


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(seed.NEW_WORLD_SOURCE_ENV, raising=False)


@pytest.fixture
def real_fm_export(monkeypatch, tmp_path) -> Path:
    """
    Gercek (sample_ olmayan) bir FM disa aktarimi 'bulunuyor': cozucu nereye bakarsa baksin
    fm_parser.discover_files onu dondurur. data/fm'ye DOKUNULMAZ.
    """
    export = tmp_path / "benim_ligim.html"
    export.write_text("<table></table>", encoding="utf-8")
    assert fm_parser.discover_files(tmp_path) == [export]                       # gercek disa aktarim sayilir
    monkeypatch.setattr(fm_parser, "discover_files", lambda *_args, **_kwargs: [export])
    return export


def test_valid_env_value_wins_and_is_read_at_call_time(monkeypatch, real_fm_export):
    for value in seed.NEW_WORLD_SOURCES:
        monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, value)
        assert seed.new_world_source() == value
    monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, "  Synthetic ")                 # bosluk / harf buyuklugu
    assert seed.new_world_source() == "synthetic"
    assert seed.NEW_WORLD_SOURCES == ("open", "synthetic", "auto", "fm")


def test_invalid_or_empty_env_value_falls_back_to_detection(monkeypatch, real_fm_export):
    for value in ("gercek", "strong", "", "   "):
        monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, value)
        assert seed.new_world_source() == "open"


def test_real_fm_export_is_never_picked_without_the_env_value(no_env, real_fm_export):
    """16A-0 / K-S11: disa aktarim var, ortam degiskeni yok -> 'open' (14C'de 'fm' idi)."""
    assert seed.new_world_source() == "open"
    assert seed.new_world_source(open_dir=open_loader.OPEN_DATA_DIR) == "open"
    assert "fm_dir" not in inspect.signature(seed.new_world_source).parameters   # data/fm'ye bakmaz


def test_explicit_env_fm_still_means_fm(monkeypatch, real_fm_export):
    """Sahibin acik secimi (kendi makinesi, kendi kariyeri): OFM_NEW_WORLD_SOURCE=fm -> 'fm'."""
    monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, "fm")
    assert seed.new_world_source() == "fm"
    monkeypatch.setenv(seed.NEW_WORLD_SOURCE_ENV, " FM ")
    assert seed.new_world_source() == "fm"


def test_empty_data_fm_means_open_world(no_env):
    assert seed.new_world_source() == "open"
    assert seed.new_world_source(open_dir=open_loader.OPEN_DATA_DIR) == "open"


def test_broken_or_missing_open_data_means_synthetic(no_env, real_fm_export, tmp_path):
    # FM disa aktarimi olsa bile acik veri yoksa 'synthetic' (FM'ye dusulmez)
    assert seed.new_world_source(open_dir=tmp_path / "yok") == "synthetic"

    broken = tmp_path / "open"
    broken.mkdir()
    (broken / open_loader.CLUBS_FILE).write_text("{bozuk json", encoding="utf-8")
    (broken / open_loader.LEAGUES_FILE).write_text("{}", encoding="utf-8")
    assert seed.new_world_source(open_dir=broken) == "synthetic"

    unlicensed = tmp_path / "lisanssiz"
    unlicensed.mkdir()
    for name, schema in ((open_loader.CLUBS_FILE, open_loader.CLUBS_SCHEMA),
                         (open_loader.LEAGUES_FILE, open_loader.LEAGUES_SCHEMA)):
        (unlicensed / name).write_text(json.dumps({"schema": schema, "leagues": [{}]}), encoding="utf-8")
    assert seed.new_world_source(open_dir=unlicensed) == "synthetic"


def test_cli_and_seed_defaults_stay_auto_while_web_entry_points_use_the_resolver():
    import accounts
    import worlds

    assert seed.build_arg_parser().parse_args([]).source == "auto"
    assert seed.build_arg_parser().parse_args(["--source", "fm"]).source == "fm"     # CLI yolu acik kalir
    assert inspect.signature(seed.seed).parameters["source"].default == "auto"
    assert inspect.signature(seed.resolve_world).parameters["source"].default == "auto"
    assert inspect.signature(accounts.register).parameters["source"].default is None
    assert inspect.signature(worlds.create_world).parameters["source"].default is None
    assert inspect.signature(accounts._build_world).parameters["open_sample"].default is False
