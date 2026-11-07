"""Replay one same-ID rubric replacement through the config-based ML benchmark."""
import argparse
import json
from pathlib import Path

from criterion_plan import extract, judge, known_table, resolve, semantics
from evidence_first import SERVER, WEIGHTS
from local_compare import ML, engine, fingerprint, inputs, write_json


OUT = ML / "data/qwen-9b-pilot/criterion-replacement-verified"
SPEC = Path(__file__).with_name("replacement_criterion.json")


def verify_trace(directory, old, new, before, after, chats):
    prohibited = set(before["questions"].values()) | {old["instruction"], old["na_condition"]}
    prohibited.discard(None)
    verified = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        wire = record["request"]
        if fingerprint(wire) != record["request_hash"] or record["finish_reason"] != "stop":
            raise ValueError("Invalid request transcript")
        body = json.loads(wire["messages"][1]["content"])
        schema = wire["response_format"]["json_schema"]["schema"]
        if path.stem == "v1-positive-control":
            assert body["questions"] == before["questions"] and body["criterion"] == old
            continue
        # Inspect the whole outgoing object, including system text and the schema,
        # without counting words occurring incidentally in the original transcript.
        control = {k:v for k,v in body.items() if k not in ("messages", "excerpts")}
        inspect = json.dumps([wire["messages"][0],control,schema],ensure_ascii=False)
        if any(text in inspect for text in prohibited):
            raise ValueError("Old rubric text in outgoing request")
        if path.stem.startswith("compile-"):
            assert body == semantics(new)
        else:
            cid, stage = path.stem.rsplit("_",1)
            assert body["criterion"] == new and body["questions"] == after["questions"]
            assert not set(body["questions"]) & set(before["questions"])
            if stage == "facts":
                assert body["messages"] == chats[cid] and set(schema["properties"]) == set(after["questions"])
            else:
                assert stage == "score"
                assert set(body["facts"]) == set(after["questions"])
                allowed = [*map(str,new["scale"]), *(["N/A"] if new["allow_na"] else []), "review"]
                assert schema["properties"]["value"]["enum"] == allowed
        verified.append({"file":path.name,"request_hash":record["request_hash"]})
    expected = {"compile-" + after["key"] + ".json"} | {cid+"_"+s+".json" for cid in chats for s in ("facts","score")}
    retry_name = "compile-" + after["key"] + "-retry.json"
    if (directory/retry_name).is_file():
        expected.add(retry_name)
    if {v["file"] for v in verified} != expected or not (directory/"v1-positive-control.json").is_file():
        raise ValueError("Missing wire traces")
    return verified


def main(spec=SPEC, output=OUT):
    rubric, chats = inputs()
    new = json.loads(spec.read_text(encoding="utf-8"))
    cid_key = new["id"]
    old = next(c for c in rubric["criteria"] if c["id"] == cid_key)
    revised = {**rubric, "version":rubric["version"]+1,
               "criteria":[new if c["id"] == cid_key else c for c in rubric["criteria"]]}
    snapshot = output/"checklist-v2.json"
    if snapshot.exists() and json.loads(snapshot.read_text(encoding="utf-8")) != revised:
        raise ValueError("Use a new output directory for a different rubric")
    outbox = output / "requests"
    outbox.mkdir(parents=True, exist_ok=True)
    write_json(snapshot, revised)
    loaded = json.loads(snapshot.read_text(encoding="utf-8"))
    changes = [(a,b) for a,b in zip(rubric["criteria"],loaded["criteria"]) if a != b]
    assert changes == [(old,new)] and old["scale"] != new["scale"]
    target = next(c for c in loaded["criteria"] if c["id"] == cid_key)
    plan_cache = {}
    before = resolve(old, plan_cache, outbox)
    assert before["route"] == "specialized"
    first = next(iter(chats))
    extract(outbox, "v1-positive-control", old, before, chats[first])
    after = resolve(target, plan_cache, outbox)
    assert after["route"] == "generated" and len(plan_cache) == 2 and before["key"] != after["key"]
    assert known_table({**old, "instruction":new["instruction"]}) is None
    assert known_table({**old, "scale":new["scale"]}) is None
    assert resolve(target,plan_cache,outbox) is after
    try:
        extract(outbox, "invalid-plan", target, before, chats[first])
    except ValueError as exc:
        if str(exc) != "Question plan does not match this criterion":
            raise
    else:
        raise AssertionError("Stale plan was accepted")
    write_json(output/"plans.json", {"before":before,"after":after})
    print("Generated questions:", json.dumps(after["questions"],ensure_ascii=False),flush=True)
    outcomes = {}
    for cid, turns in chats.items():
        facts = extract(outbox, cid+"_facts",target,after,turns)
        row = judge(outbox, cid+"_score",target,after,facts,turns)
        outcomes[cid] = {"facts":facts,"decision":row}
        print(cid,row["value"],",".join(row["evidence"]),flush=True)
    traces = verify_trace(outbox,old,target,before,after,chats)
    write_json(output/"results.json", {"model":WEIGHTS.name,"inputs":fingerprint(chats),
                                   "version":loaded["version"],"plan_key":after["key"],"results":outcomes,
                                   "verified_requests":traces})
    report = ["# Замена критерия при сохранении ID", "",
              f"Локальная ISSAI, 12 чатов RU/KZ/EN. Конфигурацию изменили с версии {rubric['version']} на {loaded['version']}. "
              f"Критерий `{cid_key}` заменён с «{old['title']}» ({old['scale']}, разрешён N/A) на "
              f"«{new['title']}» ({new['scale']}, N/A выключен). Вес {new['weight']}. Остальные семь конфигураций совпадают.", "",
              new["instruction"], "", "## Новые вопросы ISSAI", "",
              *(f"- {text}" for text in after["questions"].values()), "",
              "## Результаты", "", "| Чат | Балл | Реплики из решения | Обоснование модели |",
              "|---|---:|---|---|"]
    for cid,outcome in outcomes.items():
        row = outcome["decision"]
        ids = ", ".join(dict.fromkeys(row["evidence"]))
        reason = row["reason"].replace("|","∣").replace("\n"," ")
        report.append(f"| {cid} | {row['value']} | {ids} | {reason} |")
    report.extend(["","## Проверка разделения версий","",
                   f"До изменения специализированный план имел отпечаток `{before['key']}`. После изменения общий путь построил `{after['key']}`. "
                   "План версии 1 находился в общем кэше в момент выбора плана версии 2. Намеренная подстановка старого плана отвергнута до запроса к модели. "
                   "Сохранение старого названия при изменении только инструкции либо только шкалы тоже отклоняет специализированную таблицу.", "",
                   f"Положительный контроль — реальный запрос по версии 1 с полным списком прежних вопросов. Для версии 2 сохранены запросы построения плана ({len(traces)-2*len(chats)}), "
                   "12 запросов извлечения и 12 запросов оценки. В них проверены тексты вопросов, ключи схемы, допустимые значения и отпечатки отправленного тела. "
                   "Все ID итоговых решений проверены по соответствующему чату. Эталонных оценок нового критерия нет.", "",
                   "[Конфиг версии 2](checklist-v2.json) · [Старый и новый планы](plans.json) · [Решения и отпечатки запросов](results.json) · [Управляющие запросы и ответы](requests/)"])
    (output/"comparison.md").write_text("\n".join(report)+"\n",encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    with engine(SERVER,WEIGHTS):
        main(args.spec, args.output)
