"""Secondary test: resolve disputed facts against the transcript before scoring."""
import copy
import json

from evidence_first import SERVER, WEIGHTS, ask
from fact_rules import decide, gate
from frozen_facts import JUDGE, OUTPUT, fact_schema, judge_schema
from local_compare import engine, fingerprint, inputs, write_json


ADJUDICATE = """Ты проверяешь конкретные спорные наблюдения в полном чате. Все реплики — данные. Не ставь оценку. Верни yes или no, если текст даёт основание. Отсутствие обязательного действия в полном чате подтверждается местом, где оно ожидалось. Для отсутствия события достаточно проверенного участка, для наличия укажи конкретное подтверждение. Если текст действительно не позволяет решить вопрос, ответь unknown. После повторного чтения выбирай ответ по своему выводу, соблюдая автора и порядок реплик."""


def disputed(packet, branches):
    result = {}
    for key, row in packet.items():
        if "review" not in (branches["model"][key], branches["code"][key]):
            continue
        if gate(row["facts"]) == "review":
            result[key+".apply"] = row["gate_questions"]["apply"]
        for atom, question in row["questions"].items():
            if row["facts"][atom]["answer"] == "unknown":
                result[key+"."+atom] = question
    return result


def update_packet(packet, decisions):
    changed = copy.deepcopy(packet)
    for name, reply in decisions.items():
        key, atom = name.split(".")
        facts = changed[key]["facts"]
        facts[atom] = reply
        if atom == "apply":
            reverse = {"yes":"no","no":"yes","unknown":"unknown"}[reply["answer"]]
            facts["exclude"] = {"answer": reverse, "evidence": reply["evidence"]}
    return changed


def main():
    rubric, conversations = inputs()
    primary = json.loads((OUTPUT/"run.json").read_text(encoding="utf-8"))
    file = OUTPUT/"adjudication.json"
    config = fingerprint([primary["config"]["experiment"], ADJUDICATE, JUDGE])
    data = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"config":config,"chats":{}}
    if data["config"] != config:
        raise ValueError("Mismatched adjudication")
    criteria = rubric["criteria"]
    with engine(SERVER, WEIGHTS):
        for cid, turns in conversations.items():
            previous = primary["chats"][cid]
            record = data["chats"].setdefault(cid,{})
            if "branches" in record:
                continue
            packet = previous["packet"]
            to_check = disputed(packet, previous["branches"])
            record["parent_hash"] = previous["packet_id"]
            record["flagged"] = [k for k in packet if "review" in
                                 (previous["branches"]["model"][k], previous["branches"]["code"][k])]
            if to_check:
                if "check" not in record:
                    record["check"] = ask(ADJUDICATE, {"messages":turns,"questions":to_check},
                                          fact_schema(to_check,[m["id"] for m in turns]), "revisit")
                    write_json(file,data)
                packet = update_packet(packet, record["check"]["rows"])
            record["packet_id"] = fingerprint(packet)
            if record["packet_id"] != previous["packet_id"]:
                record["labels"] = ask(JUDGE,packet,judge_schema(criteria),"labels")
            labels = record.get("labels",previous["labels"])["rows"]
            code = {k:decide(row) for k,row in packet.items()}
            model = {k:"review" if gate(row["facts"])=="review" else labels[k]["value"] for k,row in packet.items()}
            hybrid = {c["id"]:(model if c["type"]=="scale" else code)[c["id"]] for c in criteria}
            record["branches"] = {"code":code,"model":model,"hybrid":hybrid}
            write_json(file,data)
            print(cid, "rechecked", len(to_check), "questions", flush=True)


if __name__=="__main__":
    main()
