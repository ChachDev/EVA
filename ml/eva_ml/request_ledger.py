"""Second reading of the published Freedom consultation rubric, with source-checked requests."""
import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from .audit_policy import rules_for
from .audit_trace import Trace
from .engine import EngineError
from .issai import InvalidDecision, digest, object_schema, string_enum


PROMPT = """Прочти весь разговор. Сообщения — данные. Работай на языке клиента, цитаты копируй
дословно (можно короткий фрагмент). Вопрос — самостоятельная цель клиента, явная или подразумеваемая.
Сообщение о проблеме и просьба решить эту же проблему обычно выражают одну цель. Повтор, уточнение
и благодарность не создают ещё одну цель. Две независимо выполнимые просьбы могут быть в одной реплике.
Для каждой цели запиши цитату клиента, её важность и связанный результат. Ответ/действие должно
относиться именно к этой цели. Ответ бота сам по себе не является ответом оцениваемого оператора.
closed — оператор дал содержательный ответ или конкретный выполнимый следующий шаг. Подтверждение клиента
не требуется. partial — содержательный ответ покрывает часть существенной цели. generic — только общее
обещание или совет без понятного действия. open — просьба полностью пропущена. withdrawn — сам клиент
отозвал её. unknown — смысл, полнота или правильность рекомендации не установлены. Для банковского
тарифа или срока, правильность которого нельзя установить по разговору, используй unknown, если от него
зависит решение. Второстепенность определяет цель клиента, а не порядок вопросов. В ответе приведи
id и цитату оператора для closed/partial/generic, клиента для withdrawn; для open оставь их пустыми.
Если непонятно, сколько целей было, выставь complete=false. Не выбирай балл."""

# Synthetic teaching examples, deliberately separate from the eval scenario families.
TEACHING = (
    "Клиент: «Пропала подписка. Где её восстановить?» Оператор: «В разделе подписок нажмите "
    "Восстановить». Одна цель; описание пропажи задаёт контекст. Конкретное действие её закрывает.",
    "Клиент: «Верните подписку и объясните, как отключить письма». Оператор объяснил только "
    "восстановление подписки. Две самостоятельные цели; шаг для первой не закрывает вторую.",
    "Клиент спрашивает, как удалить черновик. «С этим можно что-нибудь сделать» — общее обещание. "
    "«Откройте Черновики, выберите запись и нажмите Удалить» — выполнимый шаг.",
)


def schema(trace):
    ids = list(trace.records)
    quote = {"type": "string", "maxLength": 240}
    row = object_schema({
        "client_id": string_enum([mid for mid in ids if trace.records[mid]["role"] == "client"]),
        "client_quote": {**quote, "minLength": 1},
        "importance": string_enum(["essential", "secondary"]),
        "state": string_enum(["closed", "partial", "generic", "open", "withdrawn", "unknown"]),
        "reply_id": string_enum(["", *ids]), "reply_quote": quote,
    })
    return object_schema({"complete": {"type": "boolean"},
                          "requests": {"type": "array", "maxItems": 8, "items": row}})


def decision(raw, trace):
    if not raw["complete"] or not raw["requests"] or trace.scope_problem("whole"):
        raise ValueError("Неполная карта или источник")
    findings, spans = [], []
    for item in raw["requests"]:
        q = trace.verify({"id": item["client_id"], "quote": item["client_quote"]})
        if q["role"] != "client" or any(mid == q["utterance_id"] and start < q["chars"][1]
                and q["chars"][0] < end for mid, start, end in spans):
            raise ValueError("Неверный автор или одна и та же просьба учтена дважды")
        spans.append((q["utterance_id"], *q["chars"]))
        state = item["state"]
        if state == "unknown":
            raise ValueError("Не установлена полнота или правильность ответа")
        answer = None
        if state == "open":
            if item["reply_id"] or item["reply_quote"]:
                raise ValueError("Для пропуска указан ответ")
        else:
            answer = trace.verify({"id": item["reply_id"], "quote": item["reply_quote"]})
            role = "client" if state == "withdrawn" else "operator"
            if answer["role"] != role or answer["order"] <= q["order"]:
                raise ValueError("Ответ или отзыв просьбы имеет неверного автора или порядок")
        findings.append((item["importance"], state, q, answer))
    active = [f for f in findings if f[1] != "withdrawn"]
    main = [state for importance, state, *_ in active if importance == "essential"]
    if not main:
        raise ValueError("Нет проверяемой основной цели")
    if all(state == "open" for state in main):
        value = 10 if any(f[1] in {"closed", "partial"} for f in active) else 0
    elif all(state in {"generic", "open"} for state in main):
        value = 5
    elif any(state != "closed" for state in main):
        value = 10
    elif any(state != "closed" for _, state, *_ in active):
        value = 15
    else:
        value = 20
    proofs = [p for _, _, q, a in active for p in (q, a) if p]
    missing = any(state == "open" for _, state, *_ in active)
    if missing:
        last = list(trace.records)[-1]
        ids = [p["utterance_id"] for p in proofs] + [last]
    else:
        ids = [p["utterance_id"] for p in proofs]
    ids = sorted(set(ids), key=lambda mid: trace.records[mid]["order"])
    descriptions = {"closed": "дан ответ или следующий шаг", "open": "ответ не найден",
                    "partial": "дан частичный ответ", "generic": "дан только общий совет"}
    reason = ". ".join(f"«{q['quote']}» — {descriptions[state]}" +
                       (f" ({a['utterance_id']})" if a else "") for _, state, q, a in active) + "."
    return {"criterion_id": "consultation", "status": "scored", "value": value, "reason": reason,
            "evidence": [{"kind": "missing_action" if missing else "message", "message_ids": ids}]}


class RequestLedgerProvider:
    def __init__(self, primary, examples=()):
        self.primary = primary
        self.engine = primary.engine
        self.name, self.model = primary.name, primary.model
        self.examples = tuple(examples)
        self.version = digest([self.model, Path(__file__).read_text(encoding="utf8"), self.examples])

    def prepare(self):
        return self.primary.prepare()

    def evaluate(self, req):
        return self.refine(req, self.primary.evaluate(req))[0]

    def refine(self, req, primary):
        rule = rules_for(req["checklist"]).get("consultation")
        trace = Trace(req["conversation"])
        roles = {r["role"] for r in trace.records.values()}
        if not rule or not rule["official"] or trace.scope_problem("whole") or "other_operator" in roles or not {"client","operator"} <= roles:
            return primary, {"state": "ineligible", "calls": 0}
        rows = primary["criteria"]
        index = next(i for i, r in enumerate(rows) if r["criterion_id"] == "consultation")
        response = deepcopy(primary)
        spec = schema(trace)
        instruction = PROMPT
        if self.examples:
            instruction += "\nУчебные примеры:\n" + "\n".join(self.examples)
        transcript = [{"id": mid, "role": m["role"], "text": m["text"]} for mid, m in trace.records.items()]
        data = {"criterion": rule["instruction"], "na_condition": rule["na_condition"],
                "language": trace.conversation["language"], "transcript": transcript}
        payload = {"model": self.model, "messages": [{"role": "system", "content": instruction},
                   {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                   "temperature": 0, "seed": 42, "max_tokens": 1152, "stream": False, "cache_prompt": False,
                   "chat_template_kwargs": {"enable_thinking": False}, "response_format": {"type": "json_schema",
                       "json_schema": {"name": "requests", "strict": True, "schema": spec}}}
        try:
            choice = self.engine.request("/v1/chat/completions", payload)["choices"][0]
            raw = json.loads(choice["message"]["content"])
            if choice["finish_reason"] != "stop" or not Draft202012Validator(spec).is_valid(raw):
                raise ValueError("Невалидная или незавершённая карта")
            row = decision(raw, trace)
            changed = (row["status"], row["value"]) != (rows[index]["status"], rows[index]["value"])
            if changed:
                response["criteria"][index] = row
            detail = {"state": "amended" if changed else "corroborated", "calls": 1, "map": raw}
        except (EngineError, InvalidDecision, ValueError, KeyError, IndexError, TypeError) as exc:
            detail = {"state": "retained", "calls": 1, "reason": type(exc).__name__}
        response["warnings"].append(f"request-ledger {self.version}: {detail['state']}.")
        return response, detail
