"""
national_teams.py
=================
Milli takimlar ve Dunya Kupasi kontrolcusu (Faz 12 / 14. Asama, 12C). Commit ETMEZ.

Milli maclar match_engine'e dokunmadan oynanir: NationalSquad ordek nesnesi (.id = milli id, .players = ORM
Player uzerinde lineup_status / lineup_role'u milli kadrodan okuyan vekiller) build_match_team'e verilir;
uygun olmama yalnizca kulup sakatligidir, EngineConfig base_injury=0.0, tohum crc32("intl|..."). Kulup oyuncu
satirina YALNIZCA international_caps / international_goals yazilir (kondisyon, ceza, _post_match YOK); cm.rng
kullanilmaz (milli maclar acik/kapali lig sonuclari birebir ayni).

    NationalTeams     -> uluslar, is teklifleri, kadro cagrisi, ilk 11, fikstur, gruplar, agac
    NationalExtension -> kariyer eklentisi: haftasi gelen milli mac gununu oynatir; Dunya Kupasi bitmeden
                         yeni sezonu engeller (new_season_blocker)

Durum: iskelet (hata ve veri siniflari tanimli); Faz 12 C2 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from extensions import CareerExtension

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Position
    from tactics import LineupCheck

_PACKAGE = "Faz 12: national_teams (C2)"


class NationalTeamError(ValueError):
    """Milli takim islemi yapilamaz (mesaj Turkce)."""


@dataclass(frozen=True)
class NationView:
    id: int
    name: str
    reputation: int
    rank: int
    manager_name: str | None
    ai_managed: bool
    contract_until_season: int | None
    squad_size: int
    qualified: bool | None
    stage_label: str


@dataclass(frozen=True)
class NationalJobOfferView:
    id: int
    nation_id: int
    nation_name: str
    reputation: int
    seasons: int
    expires_in_weeks: int | None


@dataclass(frozen=True)
class CallupRow:
    player_id: int
    name: str
    club: str | None
    position: str
    age: int
    stars: float
    status: str
    role: str | None
    available: bool
    reason: str


@dataclass(frozen=True)
class IntlFixtureView:
    id: int
    competition: str
    stage_label: str
    week_label: str
    home: str
    away: str
    score: str | None
    penalties: str | None


class NationalTeams:
    def __init__(self, cm: CareerManager) -> None:
        raise NotImplementedError(_PACKAGE)

    def enabled(self) -> bool:
        raise NotImplementedError(_PACKAGE)

    def ensure_setup(self) -> list[str]:
        raise NotImplementedError(_PACKAGE)

    def nations(self) -> list[NationView]:
        raise NotImplementedError(_PACKAGE)

    def my_nation(self) -> NationView | None:
        raise NotImplementedError(_PACKAGE)

    def job_offers(self) -> list[NationalJobOfferView]:
        raise NotImplementedError(_PACKAGE)

    def accept_job(self, offer_id: int) -> NationView:
        raise NotImplementedError(_PACKAGE)

    def decline_job(self, offer_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def resign(self) -> None:
        raise NotImplementedError(_PACKAGE)

    def candidates(self, query: str = "", position: str | None = None) -> list[CallupRow]:
        raise NotImplementedError(_PACKAGE)

    def squad(self, nation_id: int | None = None) -> list[CallupRow]:
        raise NotImplementedError(_PACKAGE)

    def set_callups(self, player_ids: Sequence[int]) -> list[str]:
        raise NotImplementedError(_PACKAGE)

    def set_lineup(self, xi: Mapping[int, Position], bench: Sequence[int]) -> LineupCheck:
        raise NotImplementedError(_PACKAGE)

    def auto_lineup(self) -> None:
        raise NotImplementedError(_PACKAGE)

    def fixtures(self, competition: str | None = None) -> list[IntlFixtureView]:
        raise NotImplementedError(_PACKAGE)

    def group_tables(self, competition: str) -> list[tuple[str, list[dict]]]:
        raise NotImplementedError(_PACKAGE)

    def bracket(self) -> list:
        """arena_views.RoundView uyumlu: bracket_view.bracket_html ile cizilir."""
        raise NotImplementedError(_PACKAGE)

    def close_season_pending(self) -> bool:
        raise NotImplementedError(_PACKAGE)

    def play_close_season_matchday(self, report) -> bool:
        raise NotImplementedError(_PACKAGE)


class NationalExtension(CareerExtension):
    """Kurucu iskelette NotImplementedError: extensions.load bu eklentiyi paket dolana kadar atlar."""

    def __init__(self, cm: CareerManager) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_week(self, week: int, report) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_season_end(self) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_season_start(self, new_season: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def new_season_blocker(self) -> str | None:
        raise NotImplementedError(_PACKAGE)

    def on_player_moved(self, player, seller_id: int | None, buyer_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_club_released(self, team_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def transfer_block_reason(self, player) -> str | None:
        raise NotImplementedError(_PACKAGE)
