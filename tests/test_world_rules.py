"""
Faz 12 / 14. Asama A3: dunya kurallari (world_rules.py) -- SAF testler (veritabani gerekmez).

Kapsam: eski kurallar ({}), paylasilan dunya varsayilanlari, JSON gidis-donus, hosgorulu okuma, butunluk denetimi
(Turkce metinler), yonetici formu icin siki guncelleme (with_changes) ve sezon kilidi (editable_changes).
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_rules import (  # noqa: E402
    FIELD_LABELS,
    GAMEPLAY_FIELDS,
    INT_BOUNDS,
    SETTINGS_FIELDS,
    RulesError,
    WorldRules,
)

ALL_FIELDS = {f.name for f in fields(WorldRules)}


def test_every_rule_is_labelled_and_classified_for_the_season_lock():
    assert set(FIELD_LABELS) == ALL_FIELDS
    classified = set(GAMEPLAY_FIELDS) | set(SETTINGS_FIELDS) | {"shared"}
    assert classified == ALL_FIELDS                        # yeni kural eklenirse kilit sinifi secilmeli
    assert not set(GAMEPLAY_FIELDS) & set(SETTINGS_FIELDS)
    assert set(INT_BOUNDS) == {f.name for f in fields(WorldRules)
                               if isinstance(f.default, int) and not isinstance(f.default, bool)}


def test_legacy_rules_are_the_empty_dict_and_single_manager():
    legacy = WorldRules.legacy()
    assert WorldRules.from_dict({}) == legacy == WorldRules()
    assert legacy.validate() == []
    assert (legacy.shared, legacy.max_seats, legacy.live_matches) == (False, 1, True)
    assert not (legacy.auto_advance or legacy.human_market or legacy.loans or legacy.internationals)
    for broken in (None, "bozuk", 42, ["shared"]):
        assert WorldRules.from_dict(broken) == legacy


def test_shared_defaults_are_valid_and_survive_a_json_round_trip():
    rules = WorldRules.shared_defaults()
    assert rules.validate() == []
    assert rules.shared and rules.max_seats == 8
    assert rules.ready_check and rules.auto_advance and rules.deadline_hours == 24
    assert (rules.max_missed_deadlines, rules.protection_weeks) == (3, 4)
    assert rules.club_offers_by_level and rules.human_market and rules.loans and not rules.internationals
    stored = json.loads(json.dumps(rules.to_dict()))        # GameState.world_rules JSONB
    assert WorldRules.from_dict(stored) == rules
    assert set(stored) == ALL_FIELDS


def test_from_dict_is_tolerant_field_by_field():
    rules = WorldRules.from_dict({
        "shared": "true", "max_seats": "12", "auto_advance": 1, "deadline_hours": 48.0,
        "fairness_strictness": " high ", "win_points": 99, "protection_weeks": -1, "loans": "belki",
        "offer_expiry_weeks": 2.5, "unknown": "yok sayilir", "ready_check": False,
    })
    assert rules.shared is True and rules.max_seats == 12 and rules.auto_advance is True
    assert rules.deadline_hours == 48 and rules.fairness_strictness == "HIGH" and rules.ready_check is False
    # bozuk alanlar yalnizca kendi varsayilanina doner
    assert rules.win_points == 3 and rules.protection_weeks == 4 and rules.loans is False
    assert rules.offer_expiry_weeks == 2
    assert WorldRules.from_dict({"max_seats": True}).max_seats == 1          # bool tamsayi sayilmaz


def test_validate_reports_consistency_problems_in_turkish():
    assert WorldRules(max_seats=4).validate() == [
        "Kişisel kariyerde tek menajer olur; birden çok koltuk için dünyayı paylaşıma aç."]
    assert WorldRules(shared=True, max_seats=1).validate() == ["Paylaşılan dünyada en az 2 menajer koltuğu olmalı."]
    assert WorldRules(shared=True, max_seats=4, ready_check=False, auto_advance=False).validate() == [
        "Hafta ilerlemesi için hazır kontrolü ya da süre dolunca otomatik ilerleme açık olmalı."]
    assert WorldRules(human_market=True).validate() == ["Menajerler arası pazar yalnızca paylaşılan dünyada açılır."]
    assert WorldRules(loans=True).validate() == ["Kiralık oyuncu sistemi yalnızca paylaşılan dünyada açılır."]
    assert WorldRules(shared=True, max_seats=2, ready_check=False, auto_advance=True).validate() == []


def test_validate_reports_type_and_range_errors_for_directly_built_rules():
    problems = WorldRules(shared="evet", deadline_hours=0, win_points=True, fairness_strictness="SERT").validate()
    assert "Paylaşılan dünya: açık/kapalı olmalı." in problems
    assert "Hafta süresi (saat): 1-168 arasında olmalı." in problems
    assert "Galibiyet puanı: 2-3 arasında olmalı." in problems
    assert "Adil oyun denetimi: düşük, orta ya da yüksek olmalı." in problems


def test_with_changes_is_strict_and_coerces_form_values():
    base = WorldRules.shared_defaults()
    updated = base.with_changes({"deadline_hours": "48", "auto_advance": "false", "fairness_strictness": "low"})
    assert (updated.deadline_hours, updated.auto_advance, updated.fairness_strictness) == (48, False, "LOW")
    assert updated.max_seats == base.max_seats and base.deadline_hours == 24          # asil nesne degismez
    assert base.changed_fields(updated) == {"deadline_hours": (24, 48), "auto_advance": (True, False),
                                            "fairness_strictness": ("MEDIUM", "LOW")}
    assert base.with_changes({}) == base and base.changed_fields(base) == {}

    with pytest.raises(RulesError, match="Bilinmeyen dünya kuralı: turbo"):
        base.with_changes({"turbo": True})
    with pytest.raises(RulesError, match=r"Hafta süresi \(saat\): 1-168 arasında olmalı."):
        base.with_changes({"deadline_hours": 500})
    with pytest.raises(RulesError, match="Canlı maç: geçersiz değer."):
        base.with_changes({"live_matches": "belki"})
    with pytest.raises(RulesError, match="Adil oyun denetimi: geçersiz değer."):
        base.with_changes({"fairness_strictness": "SERT"})
    with pytest.raises(RulesError):
        base.with_changes(["deadline_hours"])
    assert issubclass(RulesError, ValueError)


def test_gameplay_rules_lock_after_the_season_starts_but_settings_do_not():
    base = WorldRules.shared_defaults()
    gameplay = base.with_changes({"live_matches": False, "win_points": 2, "human_market": False, "loans": False,
                                  "internationals": True, "world_cup_every_seasons": 2,
                                  "board_confidence": True})          # 15C: yönetim kurulu da oyun kuralı
    assert base.editable_changes(gameplay, season_started=False) == []
    locked = base.editable_changes(gameplay, season_started=True)
    assert len(locked) == len(GAMEPLAY_FIELDS)
    assert locked[1] == "Galibiyet puanı: oyun kuralı; yalnızca sezon başında (ilk maçtan önce) değiştirilebilir."

    settings = base.with_changes({"max_seats": 16, "auto_advance": False, "deadline_hours": 72, "ready_check": True,
                                  "max_missed_deadlines": 5, "protection_weeks": 0, "club_offers_by_level": False,
                                  "offer_expiry_weeks": 4, "fairness_strictness": "HIGH"})
    assert base.editable_changes(settings, season_started=True) == []


def test_shared_world_never_turns_back_into_a_personal_career():
    shared = WorldRules.shared_defaults()
    personal = WorldRules()
    message = "Paylaşılan dünya kişisel kariyere geri çevrilemez."
    assert shared.editable_changes(personal, season_started=False)[0] == message
    assert message in shared.editable_changes(personal, season_started=True)
    # kisisel -> paylasilan: sezon basinda serbest, sezon icinde kilitli
    assert personal.editable_changes(shared, season_started=False) == []
    assert personal.editable_changes(WorldRules(shared=True, max_seats=4), season_started=True) == [
        "Paylaşılan dünya: yalnızca sezon başında (ilk maçtan önce) açılabilir."]
