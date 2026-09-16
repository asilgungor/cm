"""
match_plan.py
=============
Durum bazli oyun plani (Soccer Manager "Substitutions and In-Game Instructions"). SAF MANTIK:
veritabani, ORM, Streamlit ve mac motorunu BILMEZ.

Menajer mactan once en fazla MAX_PLAN_RULES kural yazar; motor her oynanan dakikanin ardindan
kurallari sirayla yoklar:

    PlanTrigger   dakika >= minute VE skor durumu (ANY / WINNING / DRAWING / LOSING), istege bagli
                  gol farki (margin: en az N; exact_margin=True ise tam N). Skor farki eleme macinda
                  TOPLAM skora gore hesaplanir (motorun _deficit'i).
    PlanAction    istege bagli dizilis degisikligi (MATCH_FORMATIONS), istege bagli kismi talimat
                  (yalnizca verilen eksenler degisir: {"mentality": "ALL_OUT_ATTACK", "tempo": "FAST"})
                  ve istege bagli oyuncu degisikligi (sub_out_id -> sub_in_id)
    PlanRule      tetik + eylem (+ ad, acik/kapali). Her kural macta EN FAZLA BIR KEZ islenir:
                  kosul ilk saglandiginda tetiklenir (gecersiz eylemler motorda aciklamali olayla atlanir)
    MatchPlan     sirali kural listesi; kurulurken yapisal dogrulama (Turkce hata mesajlari, PlanError)

Motor uygulamasi (match_engine.MatchEngine._run_plan): eylemler menajer mudahaleleriyle AYNI kod
yolundan gecer (degisiklik hakki 3/5, pencere kurali, sakat / atilmis / sahada olmayan oyuncu, kaleci
kurali). Sira: once oyuncu degisikligi, sonra dizilis (giren oyuncu yeni hatlara dagitilsin), sonra
talimat. Rastgele sayi CEKILMEZ.

JSONB bicimi (to_dict):
    {"rules": [{"name": "", "enabled": true,
                "trigger": {"minute": 60, "situation": "LOSING", "margin": null, "exact_margin": false},
                "action": {"formation": "4-3-3", "instructions": {"mentality": "ALL_OUT_ATTACK"},
                           "sub_out_id": 105, "sub_in_id": 113}}]}
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from instructions import (
    COUNTER_ATTACK_LABEL,
    FOCUS_LABELS,
    MENTALITY_LABELS,
    OFFSIDE_TRAP_LABEL,
    PASSING_LABELS,
    PRESSING_LABELS,
    TACKLING_LABELS,
    TEMPO_LABELS,
    parse_instruction_changes,
)
from tactics import MATCH_FORMATIONS

MAX_PLAN_RULES = 5
MIN_TRIGGER_MINUTE = 1
MAX_TRIGGER_MINUTE = 120


class PlanError(ValueError):
    """Oyun plani gecersiz. errors: kullaniciya gosterilecek Turkce mesajlar."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__(" ".join(self.errors))


class ScoreSituation(str, Enum):
    ANY = "ANY"
    WINNING = "WINNING"
    DRAWING = "DRAWING"
    LOSING = "LOSING"


SITUATION_LABELS: dict[ScoreSituation, str] = {
    ScoreSituation.ANY: "Her durumda",
    ScoreSituation.WINNING: "Öndeyken",
    ScoreSituation.DRAWING: "Beraberken",
    ScoreSituation.LOSING: "Gerideyken",
}


def parse_situation(value: ScoreSituation | str) -> ScoreSituation:
    if isinstance(value, ScoreSituation):
        return value
    for s, label in SITUATION_LABELS.items():
        if value in (s.value, label):
            return s
    raise ValueError(f"Bilinmeyen skor durumu: {value}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ===========================================================================
# Tetik
# ===========================================================================

@dataclass(frozen=True)
class PlanTrigger:
    minute: int
    situation: ScoreSituation = ScoreSituation.ANY
    margin: int | None = None           # WINNING / LOSING: en az bu kadar farkla (exact_margin: tam bu kadar)
    exact_margin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.situation, ScoreSituation):
            try:
                object.__setattr__(self, "situation", parse_situation(self.situation))
            except ValueError:
                pass                     # errors() raporlar

    def errors(self) -> list[str]:
        out = []
        if not _is_int(self.minute) or not MIN_TRIGGER_MINUTE <= self.minute <= MAX_TRIGGER_MINUTE:
            out.append(f"dakika {MIN_TRIGGER_MINUTE} ile {MAX_TRIGGER_MINUTE} arasında bir tam sayı olmalı "
                       f"({self.minute!r} verildi)")
        if not isinstance(self.situation, ScoreSituation):
            out.append(f"bilinmeyen skor durumu: {self.situation!r}")
        if self.margin is not None:
            if not _is_int(self.margin) or self.margin < 1:
                out.append(f"gol farkı en az 1 olan bir tam sayı olmalı ({self.margin!r} verildi)")
            elif self.situation not in (ScoreSituation.WINNING, ScoreSituation.LOSING):
                out.append("gol farkı yalnızca 'Öndeyken' ya da 'Gerideyken' durumunda kullanılabilir")
        elif self.exact_margin:
            out.append("'tam fark' seçeneği için gol farkı belirtilmeli")
        if not isinstance(self.exact_margin, bool):
            out.append("'tam fark' seçeneği açık/kapalı (True/False) olmalı")
        return out

    def matches(self, minute: int, goal_diff: int) -> bool:
        """minute: motor dakikasi (uzatma dakikalari 45 / 90'a yazilir); goal_diff: kendi - rakip."""
        if minute < self.minute:
            return False
        if self.situation is ScoreSituation.ANY:
            return True
        if self.situation is ScoreSituation.DRAWING:
            return goal_diff == 0
        lead = goal_diff if self.situation is ScoreSituation.WINNING else -goal_diff
        if lead <= 0:
            return False
        if self.margin is None:
            return True
        return lead == self.margin if self.exact_margin else lead >= self.margin

    def describe(self) -> str:
        """'60. dk sonrası, gerideyken' / '75. dk sonrası, en az 2 farkla öndeyken'."""
        text = f"{self.minute}. dk sonrası"
        if self.situation is ScoreSituation.ANY:
            return text
        if self.situation is ScoreSituation.DRAWING:
            return f"{text}, beraberken"
        state = "öndeyken" if self.situation is ScoreSituation.WINNING else "gerideyken"
        if self.margin is None:
            return f"{text}, {state}"
        amount = f"tam {self.margin}" if self.exact_margin else f"en az {self.margin}"
        return f"{text}, {amount} farkla {state}"

    def to_dict(self) -> dict[str, Any]:
        situation = self.situation.value if isinstance(self.situation, ScoreSituation) else self.situation
        return {"minute": self.minute, "situation": situation, "margin": self.margin,
                "exact_margin": self.exact_margin}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanTrigger:
        if not isinstance(data, Mapping):
            raise PlanError(["tetik bilgisi okunamadı"])
        return cls(minute=data.get("minute"), situation=data.get("situation") or ScoreSituation.ANY,
                   margin=data.get("margin"), exact_margin=bool(data.get("exact_margin", False)))


# ===========================================================================
# Eylem
# ===========================================================================

@dataclass(frozen=True)
class PlanAction:
    formation: str | None = None
    instructions: Mapping[str, Any] = field(default_factory=dict)     # kismi talimat: alan -> deger
    sub_out_id: int | None = None
    sub_in_id: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.formation, tuple):
            object.__setattr__(self, "formation", "-".join(str(n) for n in self.formation))
        raw = self.instructions if isinstance(self.instructions, Mapping) else None
        if raw is not None:
            try:
                object.__setattr__(self, "instructions", parse_instruction_changes(raw))
            except ValueError:
                object.__setattr__(self, "instructions", dict(raw))     # errors() raporlar

    @property
    def has_substitution(self) -> bool:
        return self.sub_out_id is not None or self.sub_in_id is not None

    @property
    def is_empty(self) -> bool:
        return self.formation is None and not self.instructions and not self.has_substitution

    def errors(self) -> list[str]:
        out = []
        if self.is_empty:
            out.append("en az bir eylem (diziliş, talimat ya da oyuncu değişikliği) seçilmeli")
        if self.formation is not None and self.formation not in MATCH_FORMATIONS:
            out.append(f"bilinmeyen diziliş: {self.formation} (seçenekler: {', '.join(MATCH_FORMATIONS)})")
        if not isinstance(self.instructions, Mapping):
            out.append("talimatlar okunamadı")
        else:
            try:
                parse_instruction_changes(self.instructions)
            except ValueError as exc:
                out.append(str(exc)[0].lower() + str(exc)[1:])
        if self.has_substitution:
            if self.sub_out_id is None or self.sub_in_id is None:
                out.append("oyuncu değişikliği için çıkan ve giren oyuncu birlikte seçilmeli")
            elif not _is_int(self.sub_out_id) or not _is_int(self.sub_in_id):
                out.append("oyuncu numaraları tam sayı olmalı")
            elif self.sub_out_id == self.sub_in_id:
                out.append("çıkan ve giren oyuncu aynı olamaz")
        return out

    def describe(self, names: Mapping[int, str] | None = None) -> str:
        names = names or {}
        parts = []
        if self.has_substitution:
            out = names.get(self.sub_out_id, f"#{self.sub_out_id}")
            inn = names.get(self.sub_in_id, f"#{self.sub_in_id}")
            parts.append(f"{out} çıkar, {inn} girer")
        if self.formation is not None:
            parts.append(f"diziliş {self.formation}")
        if isinstance(self.instructions, Mapping) and self.instructions:
            parts.append(describe_instruction_changes(self.instructions))
        return "; ".join(parts) if parts else "eylem yok"

    def to_dict(self) -> dict[str, Any]:
        instructions = {}
        if isinstance(self.instructions, Mapping):
            for name, value in self.instructions.items():
                instructions[name] = value.value if isinstance(value, Enum) else value
        return {"formation": self.formation, "instructions": instructions,
                "sub_out_id": self.sub_out_id, "sub_in_id": self.sub_in_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanAction:
        if not isinstance(data, Mapping):
            raise PlanError(["eylem bilgisi okunamadı"])
        return cls(formation=data.get("formation") or None, instructions=data.get("instructions") or {},
                   sub_out_id=data.get("sub_out_id"), sub_in_id=data.get("sub_in_id"))


_VALUE_LABELS: dict[str, Mapping[Any, str]] = {
    "mentality": MENTALITY_LABELS, "tackling": TACKLING_LABELS, "passing_style": PASSING_LABELS,
    "tempo": TEMPO_LABELS, "pressing": PRESSING_LABELS, "attacking_focus": FOCUS_LABELS,
}
_CHANGE_NAMES: dict[str, str] = {
    "mentality": "zihniyet", "tackling": "sertlik", "passing_style": "pas stili", "tempo": "tempo",
    "pressing": "pres", "attacking_focus": "hücum yönü",
    "offside_trap": OFFSIDE_TRAP_LABEL.lower(), "counter_attack": COUNTER_ATTACK_LABEL.lower(),
}


def describe_instruction_changes(changes: Mapping[str, Any]) -> str:
    """{'mentality': 'ALL_OUT_ATTACK', 'tempo': 'FAST'} -> 'zihniyet Çok Ofansif (...), tempo Hızlı'."""
    try:
        parsed = parse_instruction_changes(changes)
    except ValueError:
        return "talimat (geçersiz)"
    parts = []
    for name, value in parsed.items():
        if name in _VALUE_LABELS:
            parts.append(f"{_CHANGE_NAMES[name]} {_VALUE_LABELS[name][value]}")
        else:
            parts.append(f"{_CHANGE_NAMES[name]} {'açık' if value else 'kapalı'}")
    return ", ".join(parts)


# ===========================================================================
# Kural ve plan
# ===========================================================================

@dataclass(frozen=True)
class PlanRule:
    trigger: PlanTrigger
    action: PlanAction
    name: str = ""
    enabled: bool = True

    def errors(self) -> list[str]:
        out = []
        if not isinstance(self.trigger, PlanTrigger):
            out.append("tetik eksik")
        else:
            out.extend(self.trigger.errors())
        if not isinstance(self.action, PlanAction):
            out.append("eylem eksik")
        else:
            out.extend(self.action.errors())
        if not isinstance(self.name, str):
            out.append("kural adı metin olmalı")
        if not isinstance(self.enabled, bool):
            out.append("kural açık/kapalı (True/False) olmalı")
        return out

    def describe(self, names: Mapping[int, str] | None = None) -> str:
        head = f"{self.name}: " if self.name else ""
        return f"{head}{self.trigger.describe()} → {self.action.describe(names)}"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "enabled": self.enabled,
                "trigger": self.trigger.to_dict(), "action": self.action.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanRule:
        if not isinstance(data, Mapping):
            raise PlanError(["kural okunamadı"])
        return cls(trigger=PlanTrigger.from_dict(data.get("trigger") or {}),
                   action=PlanAction.from_dict(data.get("action") or {}),
                   name=str(data.get("name") or ""), enabled=bool(data.get("enabled", True)))


@dataclass(frozen=True)
class MatchPlan:
    rules: tuple[PlanRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.rules, tuple):
            object.__setattr__(self, "rules", tuple(self.rules))
        errors = self.errors()
        if errors:
            raise PlanError(errors)

    def errors(self) -> list[str]:
        out = []
        if len(self.rules) > MAX_PLAN_RULES:
            out.append(f"Oyun planında en fazla {MAX_PLAN_RULES} kural olabilir ({len(self.rules)} verildi).")
        for index, rule in enumerate(self.rules, start=1):
            if not isinstance(rule, PlanRule):
                out.append(f"Kural {index}: kural okunamadı.")
                continue
            out.extend(f"Kural {index}: {message}." for message in rule.errors())
        return out

    @property
    def is_empty(self) -> bool:
        return not self.rules

    def __len__(self) -> int:
        return len(self.rules)

    def squad_errors(self, squad_ids: Iterable[int]) -> list[str]:
        """Kadroya gore kontrol (arayuz uyarisi): degisiklikteki oyuncular kadroda mi?"""
        squad = set(squad_ids)
        out = []
        for index, rule in enumerate(self.rules, start=1):
            action = rule.action
            if not action.has_substitution:
                continue
            for pid, role in ((action.sub_out_id, "çıkacak"), (action.sub_in_id, "girecek")):
                if pid is not None and pid not in squad:
                    out.append(f"Kural {index}: {role} oyuncu (#{pid}) kadroda değil.")
        return out

    def describe(self, names: Mapping[int, str] | None = None) -> list[str]:
        return [f"{i}. {rule.describe(names)}" for i, rule in enumerate(self.rules, start=1)]

    def to_dict(self) -> dict[str, Any]:
        return {"rules": [rule.to_dict() for rule in self.rules]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, strict: bool = True) -> MatchPlan:
        """
        strict=True: gecersiz icerik PlanError. strict=False (JSONB'den hosgorulu okuma): okunamayan
        ya da gecersiz kurallar atlanir, fazlasi kesilir. Eksik anahtarlar her iki kipte varsayilandir.
        """
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            if strict:
                raise PlanError(["Oyun planı okunamadı."])
            return cls()
        raw = data.get("rules") or []
        if not isinstance(raw, (list, tuple)):
            if strict:
                raise PlanError(["Oyun planı kuralları liste olmalı."])
            return cls()
        rules = []
        for index, item in enumerate(raw, start=1):
            try:
                rule = PlanRule.from_dict(item)
                problems = rule.errors()
            except PlanError as exc:
                problems, rule = exc.errors, None
            if problems:
                if strict:
                    raise PlanError([f"Kural {index}: {message}." for message in problems])
                continue
            rules.append(rule)
        if not strict:
            rules = rules[:MAX_PLAN_RULES]
        return cls(tuple(rules))
