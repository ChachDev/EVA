"""Keep a disputed interaction change only if its source contains a corroborating observable action."""
from copy import deepcopy
import os
from pathlib import Path
import re

from .interaction_review import ALREADY_STATED, REPAIR, SOLVED, InteractionReviewer
from .issai import digest


PROFILE = "interaction-guard-v1"
confusion = re.compile(r"не (?:совсем )?(?:понял[аи]?|понятно)|это где|какой именно|вы (?:же )?(?:сказали|писали)|"
                       r"what (?:app|settings|page)\b|which one do you mean|I (?:still )?don.t understand|"
                       r"not what I (?:asked|meant)|қай жерден нақты|қайсысын айтасыз|қайта сұра|түсініксіз", re.I)
retraction = re.compile(r"не знаю|ошиб(?:ся|лась)|не (?:могу подтвердить|уверен)|білмеймін|қателес|"
                        r"I (?:don.t know|was wrong|can.t confirm)|not sure", re.I)
comprehension = re.compile(r"теперь понятно|понял[аи]?[, .!]|наш[её]л|нашла|получилось|"
                           r"таптым|түсінікті|түсіндім|енді түсіндім|that worked|I found|now I (?:see|understand)|makes sense", re.I)
pushback = re.compile(r"но я (?:просил|спрашивал)|я не (?:об этом|это имел)|я (?:только|лишь) (?:просил|спрашивал)|"
                      r"I (?:only asked|was only asking)|I.m only asking|маған (?:тек|басқа)|мен тек", re.I)
GROUNDS = {
    "needs": {None: "Клиент сообщил достаточно сведений для ответа. Уточнение потребности не требовалось.",
              True: "Оператор уточнил недостающую деталь и использовал ответ клиента.",
              False: "Оператор дал ответ без нужного уточнения или попросил заново описать известную проблему. Клиенту пришлось уточнить запрос."},
    "dialogue": {10: "Ответы последовательны; клиент подтвердил понимание.",
                 7: "В ходе разговора был отдельный сбой. Переписка осталась понятной.",
                 4: "Клиенту пришлось вернуться к запросу из-за формулировки или порядка ответа.",
                 0: "Противоречащие друг другу ответы оператора оставили ход разговора непонятным."},
}


def corroborated(key, old, proposal, conversation):
    if proposal["status"] != "scored":
        return False
    messages = {m["id"]: m for m in conversation["messages"]}
    refs = [messages[mid] for entry in proposal["evidence"] for mid in entry["message_ids"]]
    client = [m["text"] for m in refs if m["speaker"] == "client"]
    operator = [m["text"] for m in refs if m["speaker"] == "operator"]
    value = proposal["value"]
    if key == "needs":
        if value is True:
            return True  # The parent validated question, customer detail, and subsequent use in order.
        if value is None:
            if old["status"] != "scored" or old["value"] is not True or not operator:
                return False
            position = next(i for i, m in enumerate(conversation["messages"]) if m["id"] == refs[-1]["id"])
            return not any("?" in m["text"] for m in conversation["messages"][:position + 1] if m["speaker"] == "operator")
    if value is False or (key == "dialogue" and value == 4):
        response = client[-1] if client else ""
        return bool((ALREADY_STATED.search(response) or REPAIR.search(response) or confusion.search(response))
                    and not SOLVED.search(response))
    if key == "dialogue":
        if value == 0:
            return len(operator) >= 2 and bool(retraction.search(operator[-1]))
        if value == 7:
            return bool(client and pushback.search(client[-1]))
        if value == 10:
            return bool(client and comprehension.search(client[-1]))
    return False


class GuardedInteractionReviewer(InteractionReviewer):
    def __init__(self, engine, apply=None):
        super().__init__(engine)
        self.apply_mode = os.getenv("EVA_INTERACTION_APPLY", "both") if apply is None else apply
        if self.apply_mode not in {"both", "needs", "dialogue"}:
            raise ValueError("EVA_INTERACTION_APPLY must be both, needs, or dialogue")
        self.guarded_hash = digest([self.interaction_hash, PROFILE, Path(__file__).read_text("utf8"), self.apply_mode])
        self.experiment_hash = self.guarded_hash

    def gate(self, request, primary, candidate):
        result = deepcopy(candidate)
        original = {r["criterion_id"]: r for r in primary["criteria"]}
        for i, row in enumerate(result["criteria"]):
            key = row["criterion_id"]
            if key not in {"needs", "dialogue"} or row == original[key]:
                continue
            if self.apply_mode not in {"both", key} or not corroborated(key, original[key], row, request["conversation"]):
                result["criteria"][i] = deepcopy(original[key])
            else:
                source = {m["id"]: m["text"] for m in request["conversation"]["messages"]}
                refs = [mid for entry in row["evidence"] for mid in entry["message_ids"]]
                row["reason"] = GROUNDS[key][row["value"]] + " " + " ".join(f"{mid} «{source[mid]}»" for mid in refs)
        result["warnings"].append(f"Проверка оснований для изменения оценки {PROFILE} ({self.apply_mode}), SHA-256 {self.guarded_hash}.")
        return result

    def review_interaction(self, request, primary):
        candidate, state = super().review_interaction(request, primary)
        if state != "checked":
            return candidate, state
        return self.gate(request, primary, candidate), state
