"""
extensions.py
=============
Kariyer eklentileri (Faz 12 / 14. Asama). CareerManager hafta / sezon donusumlerinde eklentileri cagirir;
boylece insan pazari (market_hub.MarketExtension, 12B) ve milli takimlar (national_teams.NationalExtension,
12C) career_manager.py'ye dokunmadan oyuna baglanir.

    CareerExtension  -> eklenti protokolu (tum kancalar; eklenti ilgilenmedigi kancada hicbir sey yapmaz)
    load(cm)         -> dunya kurallarinin actigi eklentiler. Modul yoksa, henuz iskeletse (kurucu
                        NotImplementedError) ya da kural kapaliysa eklenmez: eski kariyer icin her zaman [].

RNG kurali: eklentiler cm.rng'den ASLA cekmez (eski kariyerde mac/transfer sonuclari birebir ayni kalsin);
gerekirse zlib.crc32(f"tur|{tohum}|{sezon}|{id}") ile kendi Random'larini turetir (bkz. CareerManager._club_rng).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from world_rules import WorldRules

if TYPE_CHECKING:
    from career_manager import CareerManager


@runtime_checkable
class CareerExtension(Protocol):
    def on_week(self, week: int, report) -> None:
        """play_week sonunda (AI transfer penceresinden sonra, hafta sayaci artmadan once)."""

    def on_season_end(self) -> None:
        """start_new_season: sifirlamalardan once."""

    def on_season_start(self, new_season: int) -> None:
        """start_new_season: yeni sezonun turnuvasi kurulduktan sonra."""

    def new_season_blocker(self) -> str | None:
        """Yeni sezonu engelleyen durum (orn. Dunya Kupasi bitmedi): Turkce neden ya da None."""

    def on_player_moved(self, player, seller_id: int | None, buyer_id: int) -> None:
        """Oyuncu kulup degistirdi (kullanici ya da AI transferi)."""

    def on_club_released(self, team_id: int) -> None:
        """Insan menajer kulubu birakti / kulubu elinden alindi."""

    def transfer_block_reason(self, player) -> str | None:
        """Oyuncunun transferini engelleyen eklenti nedeni (orn. kiralikta) ya da None."""


# (modul, sinif, etkin mi?) -- sira CareerManager'in kancalari cagirma sirasidir
EXTENSIONS: tuple[tuple[str, str, Callable[[WorldRules], bool]], ...] = (
    ("market_hub", "MarketExtension", lambda rules: rules.shared and (rules.human_market or rules.loans)),
    ("national_teams", "NationalExtension", lambda rules: rules.internationals),
)


def rules_of(cm: Any) -> WorldRules:
    """cm.rules (Faz 12 A2) ya da GameState.world_rules'tan hosgorulu okuma; okunamazsa eski kurallar."""
    rules = getattr(cm, "rules", None)
    if isinstance(rules, WorldRules):
        return rules
    state = getattr(cm, "state", None)
    return WorldRules.from_dict(getattr(state, "world_rules", None))


def load(cm: CareerManager) -> list[CareerExtension]:
    """Kurallarin actigi ve kurulabilen eklentiler (iskelet modul / kapali kural -> eklenmez)."""
    rules = rules_of(cm)
    loaded: list[CareerExtension] = []
    for module_name, class_name, enabled in EXTENSIONS:
        if not enabled(rules):
            continue
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:        # modulun KENDI eksik bagimliligi gizlenmez
                raise
            continue
        factory = getattr(module, class_name, None)
        if factory is None:
            continue
        try:
            extension = factory(cm)
        except NotImplementedError:            # Faz 12 iskeleti: paket henuz doldurulmadi
            continue
        loaded.append(extension)
    return loaded
