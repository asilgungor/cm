"""
tactics_board.py
================
Surukle-birak taktik tahtasinin SAF mantigi (Faz 13G). Streamlit, veritabani ve HTML BILMEZ; oyuncu nesnelerine
duck typing ile bakar (id, name, position, overall_rating, form, morale, condition, unavailability_reason(week)).
Gorunum ve bilesen: tactics_board_view.py; kalici kayit: CareerManager.set_lineup / set_team_roles.

TAHTA = dizilis slotlari (tactics.formation_slots sirasi: GK, DEF..., MID..., FWD...) + kulube + kadro disi.
Veritabani yalnizca ilk 11'in ROL esini (oyuncu -> GK/DEF/MID/FWD) ve kulubeyi tutar; hat icindeki soldan saga sira
gorseldir (Layout.slots) ve oturumda kalir (layout_from_lineup onceki yerlesimi korur).

ISTEMCIDEN GELEN NIYET (bilesen -> Python, JSON; bilesen YALNIZCA niyet yollar, hicbir sey uygulamaz):
    {"n": 7, "rev": 3, "action": "move",    "player": 12, "to": "slot", "slot": 4}
    {"n": 7, "rev": 3, "action": "move",    "player": 12, "to": "bench" | "reserves"}
    {"n": 7, "rev": 3, "action": "swap",    "player": 12, "with": 31}
    {"n": 7, "rev": 3, "action": "role",    "player": 12, "role": "captain" | "penalty" | "free_kick" | "corner"}
    {"n": 7, "rev": 3, "action": "profile", "player": 12}
    n: istemci sayaci (ayni hareketin iki kez gelmesi fark edilsin), rev: istemcinin gordugu tahta surumu.
parse_intent semayi KATI denetler (bilinmeyen eylem, yanlis tip, bool'u sayi sanma, aralik disi slot -> IntentError).

KURALLAR (apply_intent; ihlal -> IntentError, yerlesim DEGISMEZ):
    * oyuncu (ve "with") kulubun A takim kadrosunda olmali (kadro sunucuda cm.user_team'den okunur, istemciden degil)
    * dolu slota / oyuncunun ustune birakma = YER DEGISTIRME (kulubeden slota: ilk 11'e girer, cikan kulubeye);
      bos slota / alana birakma = TASIMA
    * surukelenen oyuncu ilk 11'e ya da kulubeye giriyorsa oynayabilir olmali (sakat / cezali -> red)
    * kulube dolu (tactics.MAX_BENCH) iken kulubeye yeni oyuncu eklenemez (yer degistirme serbest)
    * yer degistirmede yerinden olan oyuncu oynayamiyorsa kadro disina alinir (not dusulur), red edilmez
Sonuc 11 slotu doluysa ve tactics.validate_lineup gecerse gorunum hemen kaydeder; degilse TASLAK kalir (oturumda,
nedeniyle gosterilir) -- ornegin bir oyuncuyu kulubeye alinca 10 kisi kalir, sonra baska oyuncu bos slota surukelenir.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

import fitness
from models import Position
from tactics import MAX_BENCH, ROLE_ORDER, formation_slots, player_power
from team_roles import SetPieceRoles

ACTIONS = frozenset({"move", "swap", "role", "profile"})
MOVE_TARGETS = frozenset({"slot", "bench", "reserves"})
ROLE_KEYS: dict[str, str] = {
    "captain": "captain_id",
    "penalty": "penalty_taker_id",
    "free_kick": "free_kick_taker_id",
    "corner": "corner_taker_id",
}
ROLE_BADGES: dict[str, str] = {"captain_id": "C", "penalty_taker_id": "P", "free_kick_taker_id": "F",
                               "corner_taker_id": "K"}
ROLE_DONE: dict[str, str] = {
    "captain_id": "©️ {name} artık kaptan.",
    "penalty_taker_id": "🎯 Penaltıları {name} kullanacak.",
    "free_kick_taker_id": "🎯 Serbest vuruşları {name} kullanacak.",
    "corner_taker_id": "🚩 Kornerleri {name} kullanacak.",
}
MAX_ID = 2**31 - 1

# Dikey saha (hucum yukari): hattin ust kenardan yuzdesi. Bes kisilik hatta oyuncular hafif zikzak dizilir.
LINE_Y: dict[Position, float] = {Position.GK: 89.0, Position.DEF: 70.0, Position.MID: 46.0, Position.FWD: 20.0}
PITCH_MARGIN_X = 8.0

# Bilesene giden sabit metinler (Turkce metin Python'da durur; bilesen yalnizca textContent ile yazar)
LABELS: dict[str, str] = {
    "bench": "Kulübe",
    "reserves": "Kadro dışı",
    "empty": "boş",
    "drop_bench": "Kulübeye almak için buraya bırak",
    "drop_reserves": "Kadro dışı bırakmak için buraya bırak",
    "menu_profile": "🔎 Profil",
    "menu_captain": "©️ Kaptan yap",
    "menu_penalty": "🎯 Penaltıcı yap",
    "menu_free_kick": "🎯 Serbest vuruşçu yap",
    "menu_corner": "🚩 Kornerci yap",
    "menu_bench": "🪑 Yedeğe al",
    "menu_reserves": "⛔ Kadro dışı bırak",
    "menu_close": "Kapat",
    "is_captain": "✓ Kaptan",
    "is_penalty": "✓ Penaltıcı",
    "is_free_kick": "✓ Serbest vuruşçu",
    "is_corner": "✓ Kornerci",
    "picked": "{name} seçildi: hedefe git (ok tuşları / dokun) ve Enter'a bas ya da dokun. İptal: Esc.",
    "cancelled": "Seçim iptal edildi.",
    "sent": "Gönderildi, tahta yenileniyor…",
    "locked": "Canlı maçın sürüyor: kadro maç kaydedilene kadar kilitli (profil açılabilir).",
    "hint": ("Sürükle-bırak: oyuncuyu slota, kulübeye ya da kadro dışına taşı; bir oyuncunun üstüne bırakırsan yer "
             "değiştirirler. Çift tık: profil. Sağ tık / basılı tut: menü. Klavye: Enter seç, oklar, Enter bırak."),
    "aria_board": "Taktik tahtası",
    "captain": "Kaptan",
    "penalty": "Penaltı atıcısı",
    "free_kick": "Serbest vuruş atıcısı",
    "corner": "Korner atıcısı",
    "offpos": "Mevki dışı",
    "condition": "Kondisyon",
    "unavailable": "Oynayamaz",
}


class IntentError(ValueError):
    """Niyet reddedildi (mesaj Turkce, kullaniciya flash olarak gosterilir)."""


# ===========================================================================
# YERLESIM
# ===========================================================================

@dataclass(frozen=True)
class Layout:
    formation: str
    slots: tuple[int | None, ...]           # formation_slots sirasiyla oyuncu id (bos slot: None)
    bench: tuple[int, ...]

    @property
    def roles(self) -> list[Position]:
        return formation_slots(self.formation)

    def xi(self) -> dict[int, Position]:
        return {pid: role for pid, role in zip(self.slots, self.roles, strict=True) if pid is not None}

    @property
    def complete(self) -> bool:
        return all(pid is not None for pid in self.slots)

    @property
    def filled(self) -> int:
        return sum(1 for pid in self.slots if pid is not None)

    def location(self, player_id: int) -> tuple[str, int | None]:
        """('slot', i) | ('bench', i) | ('reserves', None)"""
        for i, pid in enumerate(self.slots):
            if pid == player_id:
                return "slot", i
        for i, pid in enumerate(self.bench):
            if pid == player_id:
                return "bench", i
        return "reserves", None


def signature(formation: str, xi: Mapping[int, Position], bench: Iterable[int]) -> tuple:
    """Veritabanindaki kadronun ozeti: tahta cizildikten sonra kadro baska yoldan degisti mi?"""
    return (formation, tuple(sorted((int(pid), role.value) for pid, role in xi.items())),
            tuple(sorted(int(pid) for pid in bench)))


def layout_signature(layout: Layout) -> tuple:
    return signature(layout.formation, layout.xi(), layout.bench)


def layout_from_lineup(players: Iterable, formation: str, xi: Mapping[int, Position], bench: Iterable[int],
                       previous: Layout | None = None) -> Layout:
    """
    Kayitli kadrodan yerlesim. Ayni dizilisteki onceki yerlesimde ayni rolde duran oyuncu ayni slotta kalir;
    kalan slotlar rolun oyunculariyla secim gucu sirasiyla dolar. Dizilise sigmayan (rolu dolu) oyuncular bos kalan
    slotlara sirayla yerlesir (dizilis degisince taslak olur, gorunum "kaydet" ister).
    """
    by_id = {p.id: p for p in players}
    roles = formation_slots(formation)
    buckets: dict[Position, list[int]] = {role: [] for role in ROLE_ORDER}
    for pid, role in xi.items():
        if pid in by_id and role in buckets:
            buckets[role].append(pid)
    slots: list[int | None] = [None] * len(roles)
    if previous is not None and previous.formation == formation and len(previous.slots) == len(roles):
        for i, pid in enumerate(previous.slots):
            if pid is not None and pid in buckets[roles[i]]:
                slots[i] = pid
                buckets[roles[i]].remove(pid)
    for role in buckets:
        buckets[role].sort(key=lambda pid: (-player_power(by_id[pid]), pid))
    for i, role in enumerate(roles):
        if slots[i] is None and buckets[role]:
            slots[i] = buckets[role].pop(0)
    overflow = sorted((pid for role in ROLE_ORDER for pid in buckets[role]),
                      key=lambda pid: (-player_power(by_id[pid]), pid))
    for i in range(len(slots)):
        if slots[i] is None and overflow:
            slots[i] = overflow.pop(0)
    placed = {pid for pid in slots if pid is not None}
    bench_ids = tuple(pid for pid in dict.fromkeys(int(b) for b in bench) if pid in by_id and pid not in placed)
    return Layout(formation, tuple(slots), bench_ids)


def reserves(layout: Layout, players: Iterable) -> list:
    """Kadro disi: ilk 11'de ve kulubede olmayan A takim oyunculari (mevki, sonra guc sirasiyla)."""
    used = set(layout.bench) | {pid for pid in layout.slots if pid is not None}
    order = {role: i for i, role in enumerate(ROLE_ORDER)}
    return sorted((p for p in players if p.id not in used),
                  key=lambda p: (order.get(p.position, 9), -player_power(p), p.id))


# ===========================================================================
# NIYET
# ===========================================================================

@dataclass(frozen=True)
class Intent:
    action: str
    player: int
    nonce: int
    rev: int
    to: str | None = None
    slot: int | None = None
    other: int | None = None
    role: str | None = None


def _int(value, *, low: int = 0, high: int = MAX_ID) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise IntentError("Geçersiz hareket: tahtayı yenileyip tekrar dene.")
    return value


def parse_intent(raw) -> Intent:
    """Bilesenden gelen ham sozluk -> Intent. Sema disi her sey IntentError (hicbir sey uygulanmaz)."""
    if not isinstance(raw, Mapping):
        raise IntentError("Geçersiz hareket: tahtayı yenileyip tekrar dene.")
    action = raw.get("action")
    if action not in ACTIONS:
        raise IntentError("Geçersiz hareket: tahtayı yenileyip tekrar dene.")
    intent = Intent(action=action, player=_int(raw.get("player"), low=1), nonce=_int(raw.get("n", 0)),
                    rev=_int(raw.get("rev", 0)))
    if action == "move":
        to = raw.get("to")
        if to not in MOVE_TARGETS:
            raise IntentError("Geçersiz hedef: tahtayı yenileyip tekrar dene.")
        slot = _int(raw.get("slot"), high=10) if to == "slot" else None
        return replace(intent, to=to, slot=slot)
    if action == "swap":
        return replace(intent, other=_int(raw.get("with"), low=1))
    if action == "role":
        role = raw.get("role")
        if role not in ROLE_KEYS:
            raise IntentError("Geçersiz görev: tahtayı yenileyip tekrar dene.")
        return replace(intent, role=role)
    return intent


@dataclass(frozen=True)
class MoveResult:
    layout: Layout
    notes: tuple[str, ...] = ()
    changed: bool = True


def _player(by_id: Mapping[int, object], pid: int):
    player = by_id.get(pid)
    if player is None:
        raise IntentError("Bu oyuncu A takım kadronda değil (satılmış, akademiye inmiş ya da başka kulübün oyuncusu).")
    return player


def _enter_check(player, place: str, week: int) -> None:
    reason = player.unavailability_reason(week)
    if reason:
        where = "ilk 11'e" if place == "slot" else "kulübeye"
        raise IntentError(f"{player.name} {where} giremez: {reason}.")


def _put(slots: list, bench: list, place: tuple[str, int | None], pid: int | None) -> None:
    kind, index = place
    if kind == "slot":
        slots[index] = pid
    elif kind == "bench":
        if pid is None:
            bench.pop(index)
        else:
            bench[index] = pid


def apply_intent(layout: Layout, intent: Intent, by_id: Mapping[int, object], week: int) -> MoveResult:
    """move / swap niyetini yerlesime uygular (saf). Kurallar dosya basliginda; ihlal -> IntentError."""
    if intent.action not in ("move", "swap"):
        raise IntentError("Bu hareket tahtada uygulanmaz.")
    player = _player(by_id, intent.player)
    source = layout.location(player.id)
    if intent.action == "swap":
        other = _player(by_id, intent.other)
        if other.id == player.id:
            return MoveResult(layout, changed=False)
        return _exchange(layout, player, other, by_id, week)
    if intent.to == "slot":
        if intent.slot is None or not 0 <= intent.slot < len(layout.slots):
            raise IntentError("Bu dizilişte böyle bir slot yok: tahtayı yenileyip tekrar dene.")
        occupant = layout.slots[intent.slot]
        if occupant == player.id:
            return MoveResult(layout, changed=False)
        if occupant is not None:
            return _exchange(layout, player, _player(by_id, occupant), by_id, week)
        _enter_check(player, "slot", week)
        slots, bench = list(layout.slots), list(layout.bench)
        _put(slots, bench, source, None)
        slots[intent.slot] = player.id
        return MoveResult(Layout(layout.formation, tuple(slots), tuple(bench)))
    if intent.to == "bench":
        if source[0] == "bench":
            return MoveResult(layout, changed=False)
        if len(layout.bench) >= MAX_BENCH:
            raise IntentError(f"Kulübe dolu ({len(layout.bench)}/{MAX_BENCH}). Oyuncuyu kulübedeki birinin üstüne "
                              "bırak: yer değiştirirler.")
        _enter_check(player, "bench", week)
        slots, bench = list(layout.slots), list(layout.bench)
        _put(slots, bench, source, None)
        bench.append(player.id)
        return MoveResult(Layout(layout.formation, tuple(slots), tuple(bench)))
    # kadro disi
    if source[0] == "reserves":
        return MoveResult(layout, changed=False)
    slots, bench = list(layout.slots), list(layout.bench)
    _put(slots, bench, source, None)
    return MoveResult(Layout(layout.formation, tuple(slots), tuple(bench)))


def _exchange(layout: Layout, player, other, by_id: Mapping[int, object], week: int) -> MoveResult:
    """Iki oyuncu yer degistirir. Surukelenen hedefe girebilmeli; yerinden olan oynayamiyorsa kadro disina."""
    source, target = layout.location(player.id), layout.location(other.id)
    if target[0] in ("slot", "bench"):
        _enter_check(player, target[0], week)
    slots, bench = list(layout.slots), list(layout.bench)
    notes: list[str] = []
    _put(slots, bench, target, player.id)
    if source[0] in ("slot", "bench") and other.unavailability_reason(week):
        notes.append(f"{other.name} oynayamadığı için kadro dışına alındı ({other.unavailability_reason(week)}).")
        _put(slots, bench, source, None)
    else:
        _put(slots, bench, source, other.id)
    return MoveResult(Layout(layout.formation, tuple(slots), tuple(bench)), tuple(notes))


def apply_role(roles: SetPieceRoles, intent: Intent, by_id: Mapping[int, object]) -> tuple[SetPieceRoles, str]:
    """Kaptan / duran top gorevi: (yeni roller, basari metni). Oyuncu A takimda olmali."""
    player = _player(by_id, intent.player)
    field = ROLE_KEYS.get(intent.role or "")
    if field is None:
        raise IntentError("Geçersiz görev.")
    return replace(roles, **{field: player.id}), ROLE_DONE[field].format(name=player.name)


# ===========================================================================
# BILESEN VERISI
# ===========================================================================

def short_name(name: str, limit: int = 14) -> str:
    """'Mauro Icardi' -> 'M. Icardi' (tek kelime oldugu gibi), gerekirse kirpilir."""
    parts = (name or "").split()
    text = f"{parts[0][0]}. {parts[-1]}" if len(parts) >= 2 else (name or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    letters = parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")
    return letters.upper()


def slot_points(formation: str) -> list[tuple[str, float, float]]:
    """Her slotun (rol, x %, y %) konumu; dikey saha, hucum yukari."""
    roles = formation_slots(formation)
    counts = {role: roles.count(role) for role in set(roles)}
    seen: dict[Position, int] = {}
    points = []
    for role in roles:
        i = seen.get(role, 0)
        seen[role] = i + 1
        n = counts[role]
        x = PITCH_MARGIN_X + (100 - 2 * PITCH_MARGIN_X) * (i + 0.5) / n
        y = LINE_Y[role] + (3.0 if n >= 5 and i % 2 else 0.0)
        points.append((role.value, round(x, 2), round(y, 2)))
    return points


def token(player, week: int, roles: SetPieceRoles, stars_of, slot_role: str | None = None) -> dict:
    """Bir oyuncunun bilesen verisi. SAYISAL GUC YOK: guc yildiz metni (stars_of), kondisyon kendi kadronda %."""
    condition = int(fitness.condition_of(player))
    badges = [ROLE_BADGES[field] for field in ROLE_BADGES if getattr(roles, field) == player.id]
    reason = player.unavailability_reason(week)
    position = player.position.value if isinstance(player.position, Position) else str(player.position)
    return {
        "id": int(player.id),
        "name": player.name,
        "short": short_name(player.name),
        "ini": initials(player.name),
        "pos": position,
        "cond": condition,
        "band": fitness.condition_band(condition),
        "stars": stars_of(player.overall_rating),
        "out": reason or None,
        "badges": badges,
        "offpos": slot_role is not None and position != slot_role,
    }


def board_payload(layout: Layout, players: Sequence, week: int, roles: SetPieceRoles, stars_of, *,
                  rev: int, locked: bool, team_name: str) -> dict:
    """Bilesene giden JSON: slotlar (konum + oyuncu), kulube, kadro disi, sabit metinler."""
    by_id = {p.id: p for p in players}
    slots = []
    for i, ((role, x, y), pid) in enumerate(zip(slot_points(layout.formation), layout.slots, strict=True)):
        player = by_id.get(pid) if pid is not None else None
        slots.append({"slot": i, "role": role, "x": x, "y": y,
                      "player": token(player, week, roles, stars_of, role) if player is not None else None})
    return {
        "rev": int(rev),
        "formation": layout.formation,
        "team": team_name,
        "locked": bool(locked),
        "max_bench": MAX_BENCH,
        "slots": slots,
        "bench": [token(by_id[pid], week, roles, stars_of) for pid in layout.bench if pid in by_id],
        "reserves": [token(p, week, roles, stars_of) for p in reserves(layout, players)],
        "labels": LABELS,
    }
