"""Compare the frozen bench outputs with the private annotations after inference."""
from collections import Counter
from statistics import median
import json

from compare_report import display, read_reference, score
from fact_rules import decide, gate
from frozen_facts import JUDGE, OUTPUT, judge_schema
from local_compare import fingerprint, inputs


def table(headers, rows):
    def row(cells):
        return "| " + " | ".join(str(x).replace("|", "∣").replace("\n", " ") for x in cells) + " |"
    return [row(headers), row(["---"] * len(headers)), *(row(cells) for cells in rows)]


def na_counts(predictions, truth):
    assigned = {key for key, value in predictions.items() if value == "N/A"}
    required = {key for key, row in truth.items() if row["value"] == "N/A"}
    return len(assigned & required), len(assigned - required), len(required - assigned)


def validate_run(chats, rubric, conversations):
    criteria = rubric["criteria"]
    assert set(chats) == set(conversations)
    for cid, chat in chats.items():
        branch = chat["branches"]
        packet = chat["packet"]
        assert fingerprint(packet) == chat["packet_id"] == branch["packet_id"]
        assert len(packet) == 8 and set(packet) == set(judge_schema(criteria)["properties"])
        assert all(chat[stage]["finish_reason"] == "stop" for stage in ("events", "recheck", "labels"))
        # Reconstruct the same model user payload and schema as evidence_first.ask.
        wire = {"model":"eva-local", "messages":[{"role":"system","content":JUDGE},
                {"role":"user","content":json.dumps(packet,ensure_ascii=False,separators=(",",":"))}],
                "temperature":0,"seed":42,"max_tokens":3200,"stream":False,"cache_prompt":False,
                "chat_template_kwargs":{"enable_thinking":False},
                "response_format":{"type":"json_schema","json_schema":{"name":"labels","strict":True,"schema":judge_schema(criteria)}}}
        assert fingerprint(wire) == chat["labels"]["request_hash"]
        source = {turn["id"]: turn for turn in conversations[cid]}
        assert all(source[k] == quote for k, quote in chat["quotes"].items())
        for key, row in packet.items():
            for atom, fact in row["facts"].items():
                stage = "recheck" if atom == "apply" else "events"
                assert fact == chat[stage]["rows"][key + "." + atom]
                assert fact["evidence"] and set(fact["evidence"]) <= set(chat["quotes"])
            assert branch["code"][key] == decide(row)
            raw = chat["labels"]["rows"][key]["value"]
            assert raw == branch["model_raw"][key]
            assert branch["model"][key] == ("review" if gate(row["facts"]) == "review" else raw)


def detail_lines(cid, key, row, ref, chat, note):
    model = chat["branches"]["model"][key]
    code = chat["branches"]["code"][key]
    lines = [f"### {row['criterion']}", "",
             f"Разметка **{display(ref['value'])}**, ISSAI **{display(model)}**, правило **{display(code)}**.",
             f"До общего правила о расхождении применимости ISSAI выдала {display(chat['branches']['model_raw'][key])}.",
             "", f"Основание разметки — {ref['reason']} ({', '.join(sorted(ref['evidence']))}).",
             "", f"Объяснение ISSAI — {chat['labels']['rows'][key]['reason']}"]
    if note:
        lines.extend(["", f"Разбор — {note['type']}. {note['note']}"])
    qs = {**row["gate_questions"], **row["questions"]}
    parts = [(qs[k], f["answer"], ", ".join(f["evidence"])) for k, f in row["facts"].items()]
    lines.extend(["", *table(["Вопрос", "Ответ ISSAI", "ID"], parts), ""])
    return lines


def main():
    rubric, conversations = inputs()
    criteria = rubric["criteria"]
    run = json.loads((OUTPUT / "run.json").read_text(encoding="utf-8"))
    chats = run["chats"]
    validate_run(chats, rubric, conversations)
    assert run["config"]["inputs"] == fingerprint([rubric, conversations])
    truth = read_reference(criteria)
    notes_path = OUTPUT / "audit.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    predictions = {
        name: {(cid, key): value for cid, chat in chats.items() for key, value in chat["branches"][name].items()}
        for name in ("model", "code")
    }
    assert all(set(rows) == set(truth) for rows in predictions.values())
    group_counts = Counter()
    joint = {}
    for point, ref in truth.items():
        flags = tuple(p[point] == ref["value"] for p in predictions.values())
        joint[point] = {(True,True):"Оба верны", (True,False):"Только ISSAI", (False,True):"Только код",
                        (False,False):"Оба ошиблись"}[flags]
        group_counts[joint[point]] += 1
    mismatch = {cid+"/"+key for (cid,key),group in joint.items() if group != "Оба верны"}
    if notes and set(notes) != mismatch:
        raise ValueError("Review must cover every decision with at least one incorrect label")
    totals = {cid: [score(criteria, {c["id"]:truth[cid,c["id"]]["value"] for c in criteria}),
                    *(score(criteria, chat["branches"][name]) for name in predictions)]
              for cid, chat in chats.items()}
    metrics = []
    def metric(label, values):
        metrics.append([label, *values])
    metric("Точные метки / 96", [sum(v == truth[k]["value"] for k,v in p.items()) for p in predictions.values()])
    for lang in ("RU", "KZ", "EN"):
        subset = [k for k in truth if truth[k]["lang"] == lang]
        metric(lang + " / 32", [sum(p[k] == truth[k]["value"] for k in subset) for p in predictions.values()])
    for c in criteria:
        keys = [k for k in truth if k[1] == c["id"]]
        metric(c["title"] + " / 12", [sum(p[k] == truth[k]["value"] for k in keys) for p in predictions.values()])
    submax = []
    for c in criteria:
        maximum = str(max(c["scale"])) if c["type"]=="scale" else "true"
        submax.extend(k for k in truth if k[1]==c["id"] and c["weight"] and
                      truth[k]["value"] not in (maximum, "N/A", "review"))
    metric(f"Точно при оценке ниже максимума / {len(submax)}", [sum(p[k]==truth[k]["value"] for k in submax) for p in predictions.values()])
    na = [na_counts(p, truth) for p in predictions.values()]
    for i,label in enumerate(("Верные N/A / 20","Ложные N/A","Пропущенные N/A")):
        metric(label, [v[i] for v in na])
    metric("Ошибки N/A всего", [fp+fn for _,fp,fn in na])
    metric("Критериев на проверку / 96", [sum(v=="review" for v in p.values()) for p in predictions.values()])
    metric("Выдан автоматический итог / 12", [sum(row[i] is not None for row in totals.values()) for i in (1,2)])
    metric("Ложные 100 на 5 чатах ниже 100", [sum(row[i]==100 and row[0]<100 for row in totals.values()) for i in (1,2)])
    rows_with_scores = [row for row in totals.values() if all(v is not None for v in row)]
    if rows_with_scores:
        metric(f"Средняя абсолютная ошибка на общих {len(rows_with_scores)} чатах",
               [f"{sum(abs(r[i]-r[0]) for r in rows_with_scores)/len(rows_with_scores):.2f}" for i in (1,2)])
    shared_s = [ch["events"]["seconds"] + ch["recheck"]["seconds"] for ch in chats.values()]
    model_s = [secs + ch["labels"]["seconds"] for secs,ch in zip(shared_s,chats.values())]
    metric("Медиана времени на чат без загрузки модели, с", [f"{median(model_s):.1f}", f"{median(shared_s):.1f}"])
    ids_count = sum(len(atom["evidence"]) for ch in chats.values() for row in ch["packet"].values() for atom in row["facts"].values())
    metric("Проверенных ссылок / всего", [f"{ids_count}/{ids_count}"]*2)
    optional = [(cid, key, row["facts"]) for cid, chat in chats.items() for key, row in chat["packet"].items() if row["gate_questions"]]
    gate_audit = Counter()
    for cid, key, facts in optional:
        excluded = truth[cid,key]["value"] == "N/A"
        for atom, expected in (("exclude", excluded), ("apply", not excluded)):
            ans = facts[atom]["answer"]
            gate_audit[atom, "unknown" if ans=="unknown" else "верно" if (ans=="yes")==expected else "ошибка"] += 1
        state = gate(facts)
        gate_audit["pair", "на проверку" if state=="review" else "верно" if (state=="exclude")==excluded else "ошибка"] += 1
    changed_raw = sum(ch["branches"]["model_raw"][k] != v for ch in chats.values() for k,v in ch["branches"]["model"].items())
    raw = {(cid,k):v for cid,ch in chats.items() for k,v in ch["branches"]["model_raw"].items()}
    raw_tp, raw_fp, raw_fn = na_counts(raw,truth)
    raw_correct = sum(v==truth[k]["value"] for k,v in raw.items())
    raw_totals = sum(score(criteria,ch["branches"]["model_raw"]) is not None for ch in chats.values())
    lines = ["# Одни факты → две метки", "",
             "12 чатов DEV, по четыре на язык, 204 реплики, восемь критериев. Источник сравнения — первичная разметка команды. "
             "Одна локальная ISSAI извлекла 31 ответ на чат с ID. У четырёх допускающих N/A критериев второй запрос задавал обратный вопрос. "
             "ISSAI получила вопросы, ответы, ID и ту же таблицу правил, что и программный оценщик. Оба использовали один сохранённый пакет. "
             "Дословные реплики извлечены из книги по ID и сохранены в приватном run.json для аудита. Эталон открывался отдельно при сравнении. "
             "В конфигурации использована страница 2 оригинального чек-листа: порог первой реакции в три минуты не участвует в оценке. "
             "Время ожидания с предупреждением остаётся содержательным условием критерия 06.", "",
             *table(["Метрика", "ISSAI", "Код"], metrics), "", "## Парное сравнение", "",
             *table(["Категория", "Решений"], [(k,group_counts[k]) for k in ("Оба верны","Только ISSAI","Только код","Оба ошиблись")]),
             "", "## Проверка применимости", "",
             "48 решений с разрешённым N/A. Эталонная метка определяет применимость, но не все промежуточные события.", "",
             *table(["Вопрос", "Верно", "Ошибка", "Не определено"], [
                 ("Прямой об исключении", *(gate_audit["exclude",k] for k in ("верно","ошибка","unknown"))),
                 ("Обратный о применимости", *(gate_audit["apply",k] for k in ("верно","ошибка","unknown"))),
                 ("Два ответа вместе", *(gate_audit["pair",k] for k in ("верно","ошибка","на проверку")))]),
             "", f"Для расхождения применимости или ответа unknown действует общая обязательная ручная проверка. "
             f"Этот фильтр изменил {changed_raw} сырых меток ISSAI. До обязательной проверки её ответы дали {raw_correct}/96 совпадений, "
             f"{raw_tp} верных, {raw_fp} ложных и {raw_fn} пропущенных N/A, автоматический итог для {raw_totals}/12 чатов. "
             "Пропущенные N/A включают review.", "",
             "## Итоги по чатам", "",
             *table(["Чат", "Разметка", "ISSAI", "Код"], [(cid, *(display(v) for v in t)) for cid,t in totals.items()])]
    if notes:
        counts = Counter(v["type"] for v in notes.values())
        lines += ["", "## Разбор причин", "",
                  "Для каждой строки с ошибкой сопоставлены замороженные ответы, правила, оценка и объяснение разметки. "
                  "Классы указывают наблюдаемую причину для этого критерия. Совпадение итоговой метки не доказывает верность всех фактов.", "",
                  *table(["Причина", "Решений"], sorted(counts.items()))]
    extra = OUTPUT/"adjudication.json"
    rechecked = {}
    if extra.exists():
        from frozen_adjudication import update_packet
        adjudicated = json.loads(extra.read_text(encoding="utf-8"))["chats"]
        if set(adjudicated) == set(chats) and all("branches" in v for v in adjudicated.values()):
            rechecked = adjudicated
            branch_names = ("model", "code", "hybrid")
            audit_rows = []
            for cid, item in adjudicated.items():
                parent = chats[cid]
                assert parent["packet_id"] == item["parent_hash"]
                revised = update_packet(parent["packet"],item.get("check",{}).get("rows",{}))
                assert fingerprint(revised) == item["packet_id"]
                assert set(mid for f in item.get("check",{}).get("rows",{}).values() for mid in f["evidence"]) <= {m["id"] for m in conversations[cid]}
                assert item["branches"]["code"] == {k:decide(row) for k,row in revised.items()}
                assert item["branches"]["hybrid"] == {c["id"]:item["branches"]["model" if c["type"]=="scale" else "code"][c["id"]] for c in criteria}
                audit_rows.append((cid, ", ".join(item["flagged"]) or "—", *(display(score(criteria,item["branches"][n])) for n in branch_names)))
            secondary = []
            for n in branch_names:
                values = {(cid,k):v for cid,rec in adjudicated.items() for k,v in rec["branches"][n].items()}
                tp,fp,fn = na_counts(values,truth)
                comparable = [(score(criteria,rec["branches"][n]),totals[cid][0]) for cid,rec in adjudicated.items()]
                mae = [abs(s-t) for s,t in comparable if s is not None]
                secondary.append([n,sum(v==truth[k]["value"] for k,v in values.items()),tp,fp,fn,fp+fn,len(mae),
                                  f"{sum(mae)/len(mae):.2f}" if mae else "—"])
            lines += ["","## Повторное чтение спорных фактов","",
                      "Дополнительный диагностический вариант. Только для неразрешённых решений модель ещё раз читала исходный чат и отвечала на спорные фактические вопросы. "
                      "Решающая проверка применимости ставила один прямой вопрос. Противоположный ответ для условия исключения вычисляется из него. "
                      "Если текст недостаточен, разрешён unknown. Флаг указывает все строки, спорные в основном прогоне. Гибрид использует ISSAI для шкал и код для прочих критериев. "
                      "Средняя абсолютная ошибка здесь указана отдельно для тех диалогов, где конкретный вариант дал числовой итог.",
                      "",*table(["Ветка","Точно / 96","Верные N/A","Ложные","Пропуски","Ошибки N/A","Итоги / 12","MAE"],secondary),
                      "",*table(["Чат","Флаги","ISSAI","Код","Гибрид"],audit_rows),
                      "", f"Справочная MAE постоянного прогноза 100 на всех 12 чатах — "
                          f"{sum(abs(100-t[0]) for t in totals.values())/len(totals):.2f}. Это ориентир для оценки качества итога на выборке с семью эталонными сотнями."]
    lines += ["", "[Все 96 решений с вопросами, основаниями и ID](decisions.md)"]
    (OUTPUT/"comparison.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    detail = ["# Проверка всех 96 решений", "", "Порядок совпадает с порядком чатов и критериев исходной книги.", ""]
    for cid, chat in chats.items():
        detail.extend([f"## {cid}", ""])
        for c in criteria:
            key = c["id"]
            detail.extend(detail_lines(cid,key,chat["packet"][key],truth[cid,key],chat,notes.get(cid+"/"+key)))
            if cid in rechecked:
                record = rechecked[cid]
                values = [display(record["branches"][branch][key]) for branch in ("model","code","hybrid")]
                flag = "первоначально спорно" if key in record["flagged"] else "без первоначального флага"
                detail.extend([f"После повторной проверки ({flag}): ISSAI **{values[0]}**, код **{values[1]}**, гибрид **{values[2]}**.", ""])
    (OUTPUT/"decisions.md").write_text("\n".join(detail),encoding="utf-8")
    print("Verified",len(truth),"paired decisions.", dict(group_counts), "Audit rows:",len(notes))


if __name__ == "__main__":
    main()
