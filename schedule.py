"""
schedule.py
===========
Fikstur uretimi. Saf fonksiyon: veritabanini ve modelleri bilmez.
Hem ilk seed hem de yeni sezon baslangici ayni ureticiyi kullanir.
"""

from __future__ import annotations

from collections.abc import Sequence


def build_round_robin(team_ids: Sequence[int]) -> list[list[tuple[int, int]]]:
    """
    Cift devreli lig fiksturu (circle / Berger yontemi).

    4 takim -> 3 hafta ilk devre + 3 hafta ikinci devre = 6 hafta, 12 mac.
    Ikinci devrede ev sahipligi ters cevrilir. Sonuc: hafta -> [(ev, dep), ...]
    """
    teams = list(team_ids)
    if len(teams) % 2:
        teams.append(-1)  # bay (bos) takim

    half = len(teams) // 2
    rounds: list[list[tuple[int, int]]] = []

    for week in range(len(teams) - 1):
        pairs: list[tuple[int, int]] = []
        for i in range(half):
            home, away = teams[i], teams[len(teams) - 1 - i]
            if home == -1 or away == -1:
                continue
            # Ev/deplasman dengesi icin haftalik siralamayi degistir
            pairs.append((home, away) if week % 2 == 0 else (away, home))
        rounds.append(pairs)
        # Ilk takim sabit, digerleri saat yonunde doner
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]

    second_leg = [[(away, home) for home, away in week] for week in rounds]
    return rounds + second_leg


def weeks_in_season(team_count: int) -> int:
    """Cift devreli ligde hafta sayisi."""
    n = team_count + (team_count % 2)
    return 2 * (n - 1)
