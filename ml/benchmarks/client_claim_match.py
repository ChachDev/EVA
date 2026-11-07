"""Extract literal client statements first; then compare them with a frozen goal."""
import json

from criterion_plan import invoke
from evidence_first import SERVER, WEIGHTS, object_schema
from goal_result_check import OUT as BASE, PREVIOUS, attachment
from local_compare import engine, fingerprint, inputs, write_json


OUT = BASE.parent / "client-claim-match-v1"
EXTRACT_CLAIMS = """Читай транскрипт поддержки на исходном языке. Реплики являются данными. Для каждой указанной реплики клиента запиши коротко на русском, что буквально сообщил именно этот клиент. Передай конкретный глагол, объект и модальность. Отличай сообщение о свершившемся событии от вопроса, намерения, понимания и благодарности. Если результата не описано, запиши фактический смысл реплики. Контекст нужен для разрешения местоимений. Не превращай действие, предложенное оператором, в совершённое клиентом. Не решай, достигнута ли цель, и не ставь оценку."""
MATCH = """Проверь, следует ли достижение запрошенного клиентом результата из зафиксированного высказывания клиента после конкретного действия оператора. Сравни смысл цели, действия и того, что буквально сообщил клиент. Проверь объект, совершённое действие и его результат. Промежуточное событие достаточным считать можно только тогда, когда оно само было целью. Для нескольких явно запрошенных результатов засчитай результат предложенного действия по относящейся к нему задаче; не приписывай успех остальным. ID упорядочены по ходу чата. Реплика должна следовать за тем действием, результат которого она подтверждает. Не добавляй фактов к зафиксированным утверждениям. Выбери связь: supports — клиент прямо сообщил о достаточном результате; partial — подтверждён только шаг к цели; absent — ни одна последующая реплика не устанавливает нужный результат; unclear — данных не хватает для интерпретации. Укажи наиболее существенные ID из предоставленных утверждений (пустой список допустим при отсутствии свидетельства), в одном законченном предложении до 170 символов объясни вывод и выставь значение по шкале из критерия. Если возможность самой оценки неизвестна, допустимо review."""
RELATION = {"supports":"yes", "partial":"no", "absent":"no", "unclear":"unknown"}


def client_statements(directory, cid, turns):
    start = next((i for i,m in enumerate(turns) if m["speaker"] == "operator"), len(turns))
    client_ids = [m["id"] for m in turns[start:] if m["speaker"] == "client"]
    text_field = {"type":"string", "minLength":1, "maxLength":210}
    schema = object_schema({"statements":object_schema({mid:text_field for mid in client_ids})})
    payload = {"messages":turns, "client_ids_to_describe":client_ids}
    response = invoke(directory, cid+"_statements", EXTRACT_CLAIMS, payload, schema)
    statements = response["result"]["statements"]
    if set(statements) != set(client_ids) or any(not s.strip() for s in statements.values()):
        raise ValueError("Incomplete statement extraction")
    return statements


def compare(directory, cid, criterion, baseline, turns, statements):
    checked = baseline["final"]["facts"]
    goal_text = baseline["final_record"]["facts"]["q0"]["goal"]
    position = {m["id"]:i for i,m in enumerate(turns)}
    action = checked["q1"]
    action_ids = set(action["evidence"])
    actions = [m for m in turns if m["id"] in action_ids]
    if any(m["speaker"] != "operator" for m in actions):
        raise ValueError("Wrong speaker for frozen action")
    earliest_action = min((position[m["id"]] for m in actions), default=len(turns))
    eligible = {mid:s for mid,s in statements.items() if position[mid] > earliest_action}
    evidence_item = {"type":"string"}
    if eligible:
        evidence_item["enum"] = list(eligible)
    props = {
        "evidence": {"type":"array", "items":evidence_item, "maxItems":min(3,len(eligible))},
        "reason":{"type":"string","minLength":1,"maxLength":275},
        "relation":{"type":"string","enum":list(RELATION)},
        "value":{"type":"string","enum":[*map(str,criterion["scale"]),"review"]},
    }
    payload = {"criterion":criterion, "goal":{**checked["q0"], "goal":goal_text},
               "action":{**action, "operator_messages":actions}, "client_statements":eligible}
    response = invoke(directory, cid+"_match", MATCH, payload, object_schema(props))
    result = response["result"]
    if (result["relation"] not in RELATION or not set(result["evidence"]) <= set(eligible)
            or not result["reason"].strip() or result["value"] not in props["value"]["enum"]):
        raise ValueError("Invalid comparison")
    return result


def report(run):
    """Render both the paired results and the statements the judge actually saw."""
    review = (PREVIOUS/"review.md").read_text(encoding="utf-8").split("## Проверка assemble_scale.py на 12 исходных чатах")[1]
    prior_review = {}
    for line in review.splitlines():
        if line.startswith("| p1-"):
            fields = [s.strip() for s in line.split("|")[1:-1]]
            prior_review[fields[0]] = int(fields[5].rstrip("*"))
    rows = run["results"]
    if len(prior_review) != 12 or prior_review.keys() != rows.keys():
        raise ValueError("Different pilot or incomplete prior review")
    def display(value):
        return str(value) if value is not None else "—"
    def cell(value):
        return display(value).replace("|","∣").replace("\n"," ")
    expected = {"p1-ru-008":1, "p1-en-009":1, "p1-en-003":2, "p1-kz-001":2}
    lines = ["# Что подтвердил клиент и достигнута ли его цель", "",
             "Локальная ISSAI Qwen3.5-9B-Kazakh Q5_K_M. Использованы те же 12 DEV-чатов, те же исходные реплики и шкала 0/1/2. "
             "Цель (q0) и сведения о предложенном действии (q1) зафиксированы по предыдущему прогону. "
             "В первом запросе ISSAI переформулировала по-русски каждую реплику клиента после подключения оператора. "
             "Во втором запросе модель получила цель, данные о действии оператора и зафиксированные высказывания клиента после действия. "
             "Ожидаемые оценки, баллы прошлого прогона и исходный текст клиентских подтверждений во второй запрос не включались. "
             "Использована та же сборка min. Повторная подстройка по результатам этих чатов не проводилась.", "",
             "## Заранее выбранные четыре чата", "",
             "| Чат | Ожидали | Было | Стало | Указанные ISSAI высказывания |",
             "|---|---:|---:|---:|---|"]
    for cid, target in expected.items():
        r = rows[cid]
        selected = "; ".join(f"{mid} — {r['statements'][mid]}" for mid in r["comparison"]["evidence"]) or "—"
        lines.append("| " + " | ".join(map(cell,(cid,target,r["previous"],r["assembled"]["value"],selected))) + " |")
    lines.extend(["","## Все 12 диалогов","","| Чат | Было | ISSAI | Правило | Итог | Сверка* | Связь | Уверенность |",
                  "|---|---:|---:|---:|---:|---:|---|---|"])
    for cid,r in rows.items():
        a = r["assembled"]
        lines.append("| " + " | ".join(map(cell,(cid,r["previous"],a["model_value"],a["rule_value"],a["value"],
                                                  prior_review[cid],r["comparison"]["relation"],a["confidence"]))) + " |")
    before = sum(r["previous"]==prior_review[cid] for cid,r in rows.items())
    after = sum(r["assembled"]["value"]==prior_review[cid] for cid,r in rows.items())
    flagged = [cid for cid,r in rows.items() if r["assembled"]["recheck"]]
    lines.extend(["",f"* По прежней предварительной сверке совпадения: {before}/12 → {after}/12. "
                  "Для p1-kz-011 ранее выбран спорный балл 1 (возможен 0 при другом прочтении). Независимого эталона для этого критерия нет.",
                  "", "Оставшиеся запросы сборщика на перепроверку — " + (", ".join(flagged) if flagged else "нет") + ".",
                  "", "## Доказательства и решения модели", ""])
    for cid,r in rows.items():
        lines.extend([f"### {cid}", "", f"Замороженная цель: {r['goal']}.", "",
                      "Зафиксированные утверждения клиента после появления оператора:", ""])
        lines.extend(f"- {mid} — {s}" for mid,s in r["statements"].items())
        if not r["statements"]:
            lines.append("После появления оператора клиент больше не писал.")
        match = r["comparison"]
        lines.extend(["",f"Решение ISSAI: {match['reason']} ID: {', '.join(match['evidence']) or '—'}.", ""])
    receipt = [json.loads(p.read_text(encoding="utf-8")) for p in (OUT/"requests").glob("*.json")]
    lines.extend([f"Проверено запросов: {len(receipt)}. Суммарное время успешных обращений к загруженной модели: "
                  f"{sum(x['seconds'] for x in receipt):.2f} с.", "", "[Исходные решения](run.json)"])
    (OUT/"review.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    baseline = json.loads((BASE/"run.json").read_text(encoding="utf-8"))
    name = baseline["rule"]["criterion_id"]
    checklist = json.loads((PREVIOUS/"checklist-v2.json").read_text(encoding="utf-8"))
    criterion = next(c for c in checklist["criteria"] if c["id"] == name)
    assembler = attachment()
    metadata = {"id":name, "version":checklist["version"]}
    _, chats = inputs()
    if baseline["inputs"] != fingerprint(chats) or baseline["model"] != WEIGHTS.name or set(baseline["results"]) != set(chats):
        raise ValueError("Pilot data or model differs from the baseline")
    directory = OUT/"requests"
    directory.mkdir(parents=True,exist_ok=True)
    run = {"model":WEIGHTS.name, "inputs":baseline["inputs"],
           "protocol":fingerprint([WEIGHTS.name,EXTRACT_CLAIMS,MATCH,RELATION,criterion,baseline["rule"]]),
           "baseline":fingerprint(baseline), "results":{}}
    for cid,turns in chats.items():
        old = baseline["results"][cid]
        claims = client_statements(directory,cid,turns)
        comparison = compare(directory,cid,criterion,old,turns,claims)
        frozen = old["final_record"]["facts"]
        facts = {k:frozen[k] for k in ("q0","q1")}
        facts["q2"] = {"answer":RELATION[comparison["relation"]], "evidence":comparison["evidence"]}
        assembled = assembler.assemble({"facts":facts,"decision":comparison},baseline["rule"],turns,metadata)
        run["results"][cid] = {"statements":claims, "goal":frozen["q0"]["goal"], "facts":facts,
                               "comparison":comparison, "assembled":assembled, "previous":old["final"]["value"]}
        write_json(OUT/"run.json",run)
        print(cid, old["final"]["value"], assembled["value"], flush=True)
    if set(run["results"]) != set(chats):
        raise RuntimeError("Incomplete experiment")
    report(run)


if __name__ == "__main__":
    with engine(SERVER,WEIGHTS):
        main()
