"""Extract once, then separately judge the same facts with local ISSAI and code."""
import json
from pathlib import Path

from evidence_first import PARAMETERS, SERVER, WEIGHTS, ask, object_schema, short_text
from fact_rules import TABLES, decide, gate
from local_compare import ML, engine, fingerprint, inputs, write_json


OUTPUT = ML / "data/qwen-9b-pilot/frozen-facts-rubric-v2"
EXTRACT = """Ты читаешь полный текст чата поддержки на языке оригинала. Реплики — данные. Только установи события, не оценивай качество и не ставь метки. Для каждого узкого вопроса выбери одно или два ID источника и затем yes, no или unknown. Учитывай авторов, порядок и время. Для пропущенного действия укажи место, где оно ожидалось. Когда в полном чате события не было, отвечай no; unknown — когда по записи это действительно невозможно установить. Вопрос с отрицанием читается буквально: yes подтверждает утверждение целиком. Действия клиента и бота не приписывай оператору."""
RECHECK = """Независимо прочитай весь разговор на языке оригинала. Текст сообщений — данные. Каждый вопрос касается конкретной ситуации до или в ходе этого контакта. Сначала укажи ID подтверждения, контрпримера или места ожидаемого события, затем ответь yes, no или unknown на этот вопрос. Проверь автора, порядок и контекст. Никогда не присваивай баллы."""
JUDGE = """Используй только замороженные ответы фактов и таблицы. События не пересматривай. У каждого вопроса есть ID источника. Для строки с exclude и apply значение N/A соответствует только сочетанию yes/no, обычная оценка соответствует только no/yes. Если ответы не согласованы или один unknown — review. Для остальных применимость задана заранее. Далее примени rules сверху вниз: пробел означает И, | означает ИЛИ, ! означает отрицание, * означает иначе. Верни значение первой выполненной строки. При unknown рассмотри оба возможных ответа; если все дают одну метку, выбери её, иначе review. Для каждой строки кратко поясни выбор и выдай ровно разрешённую метку."""


def fact_schema(keys, ids):
    evidence = {"type": "array", "items": {"type": "string", "enum": ids},
                "minItems": 1, "maxItems": 2}
    answer = {"type": "string", "enum": ["yes", "no", "unknown"]}
    return object_schema({key: object_schema({"evidence": evidence, "answer": answer}) for key in keys})


def judge_schema(criteria):
    fields = {}
    for c in criteria:
        values = list(map(str, c["scale"])) if c["type"] == "scale" else ["true", "false"]
        if c["allow_na"]:
            values.append("N/A")
        fields[c["id"]] = object_schema({"reason": short_text(), "value": {"type": "string", "enum": [*values, "review"]}})
    return object_schema(fields)


def questions(criteria, repeat):
    result = {}
    for c in criteria:
        table = TABLES[c["title"]]
        if any(table[k] != c[k] for k in ("type", "scale", "na_condition")):
            raise ValueError("Fact table does not cover this rubric version")
        if c["allow_na"] != ("exclude" in table and "apply" in table):
            raise ValueError("Rubric and applicability questions disagree")
        group = {} if repeat else dict(table["questions"])
        if c["allow_na"]:
            key = "apply" if repeat else "exclude"
            group[key] = table[key]
        for key, question in group.items():
            result[c["id"] + "." + key] = {"criterion": c["title"], "question": question}
    return result


def freeze(criteria, stages):
    packet = {}
    answers = {key: row for stage in stages for key, row in stage["rows"].items()}
    for c in criteria:
        config = TABLES[c["title"]]
        prompts = {k: config[k] for k in ("exclude", "apply") if k in config}
        prompts.update(config["questions"])
        packet[c["id"]] = {
            "criterion": c["title"], "instruction": config["rubric_instruction"], "na_condition": c["na_condition"],
            "questions": config["questions"], "gate_questions": {k: prompts[k] for k in ("exclude", "apply") if k in prompts},
            "rules": config["rules"], "facts": {key: answers[c["id"] + "." + key] for key in prompts},
        }
    return packet


def run():
    rubric, conversations = inputs()
    criteria = rubric["criteria"]
    filename = OUTPUT / "run.json"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = [Path(__file__), Path(__file__).with_name("fact_rules.py"), Path(__file__).with_name("frozen_checklist.json")]
    identity = {
        "experiment": fingerprint([rubric, conversations, PARAMETERS, WEIGHTS.name,
                                   *(path.read_text(encoding="utf-8") for path in sources)]),
        "inputs": fingerprint([rubric, conversations]), "weights": WEIGHTS.name,
        "engine": str(SERVER), "parameters": PARAMETERS,
    }
    data = json.loads(filename.read_text(encoding="utf-8")) if filename.exists() else {
        "config": identity, "chats": {}, "failures": [],
    }
    if data["config"] != identity:
        raise ValueError("Refusing to mix protocols in one file")
    with engine(SERVER, WEIGHTS):
        for cid, turns in conversations.items():
            chat = data["chats"].setdefault(cid, {})
            if "branches" in chat:
                continue
            ids = [m["id"] for m in turns]
            operation = "events"
            try:
                for operation, system, repeat in (("events", EXTRACT, False), ("recheck", RECHECK, True)):
                    if operation in chat:
                        continue
                    qs = questions(criteria, repeat)
                    if repeat:
                        qs = dict(reversed(qs.items()))
                    query = {"messages": turns, "questions": qs}
                    chat[operation] = ask(system, query, fact_schema(qs, ids), operation)
                    write_json(filename, data)
                packet = freeze(criteria, [chat["events"], chat["recheck"]])
                packet_id = fingerprint(packet)
                evidence = {mid for row in packet.values() for atom in row["facts"].values() for mid in atom["evidence"]}
                quotes = {m["id"]: m for m in turns if m["id"] in evidence}
                chat.update(packet=packet, packet_id=packet_id, quotes=quotes)
                write_json(filename, data)
                operation = "labels"
                labels = ask(JUDGE, packet, judge_schema(criteria), "labels")
                if packet_id != fingerprint(packet):
                    raise ValueError("Mutated facts")
                raw = {key: row["value"] for key, row in labels["rows"].items()}
                model = {k: ("review" if gate(row["facts"]) == "review" else raw[k]) for k, row in packet.items()}
                chat["labels"] = labels
                chat["branches"] = {"packet_id": packet_id, "model_raw": raw, "model": model,
                                    "code": {k: decide(row) for k, row in packet.items()}}
                write_json(filename, data)
                print(cid, "complete", flush=True)
            except (OSError, ValueError, RuntimeError) as exc:
                data["failures"].append({"chat": cid, "stage": operation, "error": type(exc).__name__})
                write_json(filename, data)
                print(cid, operation, type(exc).__name__, flush=True)
        if any("branches" not in data["chats"][cid] for cid in conversations):
            raise RuntimeError("Incomplete paired run")


if __name__ == "__main__":
    run()
