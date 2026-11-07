"""Fresh local ISSAI fact extraction for the supplied goal/action/result assembler."""
import importlib.util
import json

from criterion_plan import invoke, judge
from evidence_first import SERVER, WEIGHTS, object_schema
from frozen_facts import EXTRACT, fact_schema
from local_compare import ML, engine, fingerprint, inputs, write_json


OUT = ML / "data/qwen-9b-pilot/goal-result-v2"
PREVIOUS = OUT.parent / "criterion-replacement-verified"
SYSTEM = EXTRACT + " Для открытого вопроса о цели заполни поле goal краткой фразой клиента по смыслу. Поле answer для этого вопроса означает, установлена ли цель."
RECHECK = SYSTEM + " Повторно прочитай весь чат, перепроверь спорные факты по реальным авторам и хронологии. Прежние ответы и оценки тебе неизвестны."


def attachment():
    spec = importlib.util.spec_from_file_location("reviewed_assembler", OUT/"assembly_source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_facts(outbox, name, turns, criterion, questions, goal_key, system, context=None):
    schema = fact_schema(questions, [m["id"] for m in turns])
    if goal_key in questions:
        row = schema["properties"][goal_key]
        row["properties"]["goal"] = {"type":"string","minLength":1,"maxLength":180}
        row["required"] = list(row["properties"])
    payload = {"criterion":criterion,"questions":questions,"messages":turns}
    if context:
        payload["already_extracted_goal"] = context
    facts = invoke(outbox, name, system, payload, schema)["result"]
    ids = {m["id"] for m in turns}
    if set(facts) != set(questions):
        raise ValueError("Incomplete response")
    for fact in facts.values():
        if fact["answer"] not in ("yes","no","unknown") or not 1 <= len(fact["evidence"]) <= 2 or not set(fact["evidence"]) <= ids:
            raise ValueError("Invalid response or message IDs")
    return facts


def main():
    asm = attachment()
    rule = asm.RULE_CONSULTATION_V2
    snapshot = json.loads((PREVIOUS/"checklist-v2.json").read_text(encoding="utf-8"))
    criterion = next(c for c in snapshot["criteria"] if c["id"] == rule["criterion_id"])
    identity = {"id":criterion["id"],"version":snapshot["version"]}
    assert identity["version"] == rule["criterion_version"] and rule["scale"] == criterion["scale"]
    questions = {key:row["question"] for key,row in rule["facts"].items()}
    goal_key = "q0"
    plan = {"questions":questions}
    _, chats = inputs()
    directory = OUT/"requests"
    directory.mkdir(parents=True, exist_ok=True)
    run = {"rule":rule,"model":WEIGHTS.name,"inputs":fingerprint(chats),"results":{}}
    for cid,turns in chats.items():
        facts = read_facts(directory,cid+"_facts",turns,criterion,questions,goal_key,SYSTEM)
        decision = judge(directory,cid+"_score",criterion,plan,facts,turns)
        record = {"facts":facts,"decision":decision}
        first = asm.assemble(record,rule,turns,identity)
        item = {"first_record":record,"first":first}
        if first["recheck"]:
            disputed = {entry["fact"] for entry in first["recheck"]}
            keys = {key for key in questions if any(key in name.split("/") for name in disputed)}
            if goal_key in keys:
                keys.update(questions)
            if "q1" in keys:
                keys.add("q2")
            selected = {k:q for k,q in questions.items() if k in keys}
            if not selected:
                raise ValueError("Unrecognized recheck request")
            confirmed = read_facts(directory,cid+"_recheck",turns,criterion,selected,goal_key,RECHECK,
                                   facts[goal_key] if goal_key not in selected else None)
            facts = {**facts, **confirmed}
            decision = judge(directory,cid+"_rescore",criterion,plan,facts,turns)
        record = {"facts":facts,"decision":decision}
        item.update(final_record=record, final=asm.assemble(record,rule,turns,identity))
        run["results"][cid] = item
        write_json(OUT/"run.json",run)
        print(cid, first["value"],item["final"]["value"],item["final"]["confidence"],flush=True)
    assert set(run["results"]) == set(chats)


if __name__ == "__main__":
    with engine(SERVER,WEIGHTS):
        main()
