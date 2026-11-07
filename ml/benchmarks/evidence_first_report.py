"""Paired ISSAI comparison. Read reference values only after inference has finished."""
import argparse
from decimal import Decimal
from statistics import mean, median
import json

from compare_report import display, read_reference, score
from evidence_first import OUTPUT
from local_compare import OUTPUT as BASE, inputs


def rate(num, den):
    return f"{num}/{den} ({100*num/den:.1f}%)" if den else "—"


def average(numbers):
    return f"{mean(numbers):.2f}" if numbers else "—"


def overview(output, criteria, truth, conversations, runs):
    """Use one common set of numeric dialogs for all three variants."""
    _, batched, isolated = runs
    if isolated["config"]["parent_experiment"] != batched["config"]["experiment"]:
        raise ValueError("Mismatched parent plans")
    rows_by_run=[{(cid,k):v for cid,d in r["items"].items() for k,v in d["criteria"].items()} for r in runs]
    all_rows=[truth,*rows_by_run]
    totals={cid:[score(criteria,{c["id"]:rows[cid,c["id"]]["value"] for c in criteria}) for rows in all_rows]
            for cid in conversations}
    comparable=[t for t in totals.values() if all(x is not None for x in t)]
    low=[t for t in totals.values() if t[0]<100]
    expected_na={k for k,v in truth.items() if v["value"]=="N/A"}
    lines=["# ISSAI на одних и тех же 12 диалогах", "",
           "A — один вызов. B — зафиксированные применимость и доказательства, затем оценка. "
           "C — те же доказательства, отдельный вопрос о каждом из четырёх разрешённых N/A и повторная оценка. "
           "C спроектирован после наблюдений на B, результаты относятся к настройке на DEV.",
           "", "| Метрика | A | B | C |","|---|---:|---:|---:|"]
    def line(label, values):
        lines.append("| "+" | ".join(map(str,[label,*values]))+" |")
    def correct(rows, keys):
        return sum(rows[k]["value"]==truth[k]["value"] for k in keys)
    indices=(1,2,3)
    line("Совпадения / 96",[correct(p,truth) for p in rows_by_run])
    for lang in ("RU","KZ","EN"):
        keys=[k for k in truth if truth[k]["lang"]==lang]
        line(lang+" / 32",[correct(p,keys) for p in rows_by_run])
    nonmax={k for k in truth for c in criteria if c["id"]==k[1] and c["weight"] and
            truth[k]["value"] not in ("N/A","review") and
            (truth[k]["value"]=="false" if c["type"]=="boolean" else int(truth[k]["value"])<max(c["scale"]))}
    line("Точные оценки ниже максимума / "+str(len(nonmax)),[correct(p,nonmax) for p in rows_by_run])
    sets=[{k for k,v in p.items() if v["value"]=="N/A"} for p in rows_by_run]
    line("Верные N/A / "+str(len(expected_na)),[len(s&expected_na) for s in sets])
    line("Ложные N/A",[len(s-expected_na) for s in sets])
    line("Пропущенные N/A",[len(expected_na-s) for s in sets])
    line("Ошибки N/A, всего",[len(s^expected_na) for s in sets])
    line("Автоматический итог / 12",[sum(t[i] is not None for t in totals.values()) for i in indices])
    line("Итог 100 / 12",[sum(t[i]==100 for t in totals.values()) for i in indices])
    line("Ложные 100 / "+str(len(low)),[sum(t[i]==100 for t in low) for i in indices])
    line("Без итога на эталонах ниже 100",[sum(t[i] is None for t in low) for i in indices])
    line("Review / 96",[sum(v["value"]=="review" for v in p.values()) for p in rows_by_run])
    line("Медиана суммарного времени стадий на чат, с",[f"{median(x['seconds'] for x in r['items'].values()):.1f}" for r in runs])
    for name,metric in [("Завышенные итоги",lambda x,y:int(y>x)),("Среднее завышение, баллы",lambda x,y:max(0,y-x)),("MAE, баллы",lambda x,y:abs(y-x))]:
        values=[[metric(t[0],t[i]) for t in comparable] for i in indices]
        line(f"{name}, общие {len(comparable)}",[sum(v) if name=="Завышенные итоги" else average(v) for v in values])
    lines.extend(["", "## Все диалоги", "", "| Диалог | Эталон | A | B | C |","|---|---:|---:|---:|---:|"])
    for cid,t in totals.items():line(cid,[display(v) for v in t])
    lines.extend(["","Стоимость C включает повторное использование плана B по его измеренной стоимости. "
                  "Для C сделаны ещё четыре вызова маршрутизации и один вызов оценки на разговор. "
                  "Все значения на review исключают автоматический итог. Общие метрики рассчитаны только по одинаковым диалогам.",
                  "", "[Два прохода](comparison.md) · [Отдельные вопросы N/A](../evidence-isolated-gate-v1/comparison.md) · [LLM-совет и источники](council.md)",
                  "", "[Доказательства двухпроходной оценки](trace.md) · [Доказательства отдельной проверки N/A](../evidence-isolated-gate-v1/trace.md)"])
    (output/"overview.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main(output=OUTPUT):
    rubric, conversations=inputs()
    criteria=rubric["criteria"]
    truth=read_reference(criteria)
    old=json.loads((BASE/"issai.json").read_text(encoding="utf-8"))
    new=json.loads((output/"issai.json").read_text(encoding="utf-8"))
    isolated = new["config"].get("protocol") == "isolated_gate"
    name = "Отдельный вопрос о N/A" if isolated else "Два прохода"
    if new["config"]["baseline_experiment"]!=old["config"]["experiment"]:
        raise ValueError("Unpaired inputs")
    modes=[old,new]
    if any(set(r["items"])!=set(conversations) for r in modes):
        raise ValueError("Incomplete results: report failures before comparing")
    predictions=[{(cid,k):v for cid,it in r["items"].items() for k,v in it["criteria"].items()} for r in modes]
    if any(set(v)!=set(truth) for v in predictions):
        raise ValueError("Missing decision")
    totals={}
    for cid,turns in conversations.items():
        keys={c["id"]:(cid,c["id"]) for c in criteria}
        totals[cid]=[score(criteria,{k:ref[p]["value"] for k,p in keys.items()})
                     for ref in (truth,*predictions)]
        plan=new["plans"][cid]["rows"]
        ids={m["id"] for m in turns}
        for k,p in keys.items():
            obs=plan[k]; result=predictions[1][p]
            if not obs["evidence"] or not set(obs["evidence"])<=ids or result["evidence"]!=obs["evidence"]:
                raise ValueError("Evidence changed across stages")
            gate=obs["condition"]
            if (result["value"]=="N/A")!=(gate=="holds") or (gate=="unknown" and result["value"]!="review"):
                raise ValueError("Inconsistent gate")
    cols=[1,2]
    common=[v for v in totals.values() if all(x is not None for x in v)]
    nonmax={p for p,t in truth.items() for c in criteria if p[1]==c["id"] and c["weight"] and
            t["value"] not in ("N/A","review") and
            (t["value"]=="false" if c["type"]=="boolean" else int(t["value"])<max(c["scale"]))}
    bound={p for p,t in truth.items() if t["value"]=="N/A"}
    def correct(p, keys):
        return sum(p[k]["value"]==truth[k]["value"] for k in keys)
    lines=["# ISSAI: " + ("независимая перепроверка условий N/A" if isolated else "один и два прохода") + " на DEV", "",
           "Одинаковые 12 синтетических чатов, 204 сообщения, 96 решений, модель ISSAI Qwen3.5-9B-Kazakh Q5_K_M. "
           "Эталон — первичная разметка Codex. В обеих конфигурациях одинаковы чек-лист, веса модели, движок llama.cpp b12186 Vulkan, "
           "greedy, seed 42, контекст 8192 и выключенный режим рассуждений. Исходный результат взят из сохранённого прогона bounded.",
           "", "В новом протоколе первый вызов сохраняет ID, наблюдение и статус условия N/A. "
           "Подтверждённое условие сразу даёт N/A, неясная применимость — review. Второй вызов читает полный диалог и зафиксированные факты, "
           "обосновывает и оценивает только применимые критерии. ID из первого прохода остаются доказательствами итоговой оценки. "
           "Промпты и число вызовов изменены вместе. Сравнение показывает эффект всей процедуры на выборке настройки.",
           "",f"| Метрика | Исходный ISSAI | {name} |","|---|---:|---:|"]
    if isolated:
        batched = json.loads((OUTPUT/"issai.json").read_text(encoding="utf-8"))
        for cid in conversations:
            for c in criteria:
                key=c["id"]
                if new["plans"][cid]["rows"][key]["evidence"]!=batched["plans"][cid]["rows"][key]["evidence"]:
                    raise ValueError("Diagnostic changed selected evidence")
        overview(OUTPUT,criteria,truth,conversations,[old,batched,new])
        lines[-2:-2] = ["Этот вариант выбран после просмотра двухпроходного прогона. Каждое условие N/A перепроверено отдельным запросом ISSAI по полному диалогу и прежним ID, без подсказки прежнего решения. "
                        "Время и токены включают переиспользованный первый этап, четыре независимых перепроверки и финальную оценку. Фактически дополнительно сделано пять вызовов на чат.", ""]
    def line(label, values):
        lines.append("| "+" | ".join(map(str,(label,*values)))+" |")
    line("Совпадения с эталоном", [rate(correct(p,truth),len(truth)) for p in predictions])
    for lang in ("RU","KZ","EN"):
        keys=[k for k in truth if truth[k]["lang"]==lang]
        line(lang,[rate(correct(p,keys),len(keys)) for p in predictions])
    for c in criteria:
        keys=[k for k in truth if k[1]==c["id"]]
        line(c["title"],[rate(correct(p,keys),len(keys)) for p in predictions])
    for label,which in [("Истинные N/A (TP)","tp"),("Ложные N/A (FP)","fp"),("Пропущенные N/A (FN)","fn"),
                        ("Precision N/A","precision"),("Recall N/A","recall"),("Ошибки N/A (FP + FN)","errors")]:
        vals=[]
        for pred in predictions:
            assigned={k for k,v in pred.items() if v["value"]=="N/A"}
            tp=len(assigned&bound); fp=len(assigned-bound); fn=len(bound-assigned)
            vals.append(dict(tp=tp,fp=fp,fn=fn,errors=fp+fn,precision=rate(tp,tp+fp),recall=rate(tp,tp+fn))[which])
        line(label,vals)
    line("Совпадения на применимых весовых решениях ниже максимума",[rate(correct(p,nonmax),len(nonmax)) for p in predictions])
    line("Отправлено на проверку (из 96)",[sum(v["value"]=="review" for v in p.values()) for p in predictions])
    line("Хотя бы один общий ID доказательства с эталоном (из 96)",
         [sum(bool(set(d["evidence"]) & truth[k]["evidence"]) for k,d in p.items()) for p in predictions])
    line("Итог выдан (из 12)",[sum(t[i] is not None for t in totals.values()) for i in cols])
    line("Итог 100 (из 12)",[sum(t[i]==100 for t in totals.values()) for i in cols])
    weak=[t for t in totals.values() if t[0]<100]
    for label,test in [("ложная сотня",lambda x:x==100),("выдано <100",lambda x:x is not None and x<100),("без итога",lambda x:x is None)]:
        line(f"Среди {len(weak)} эталонов ниже 100: {label}",[sum(test(t[i]) for t in weak) for i in cols])
    for label,func in [("Число завышений",lambda a,b:int(b>a)),("Число занижений",lambda a,b:int(b<a)),
                       ("Среднее завышение (нулевой штраф за занижение)",lambda a,b:max(Decimal(0),b-a)),
                       ("Среднее знаковое отклонение",lambda a,b:b-a),("MAE",lambda a,b:abs(b-a))]:
        line(f"{label}, общие {len(common)} диалогов",[
            sum(func(t[0],t[i]) for t in common) if label.startswith("Число") else average([func(t[0],t[i]) for t in common]) for i in cols])
    line("Медиана обработанного чата, с (без загрузки модели)",[f"{median(x['seconds'] for x in r['items'].values()):.1f}" for r in modes])
    line("Выходных токенов, всего",[
        sum(it["tokens"]["completion_tokens"] for it in old["items"].values()),
        sum(it["tokens"]["completion_tokens"] for it in new["plans"].values())+
        sum(it["grade_meta"]["tokens"].get("completion_tokens",0) for it in new["items"].values())+
        sum(call["tokens"]["completion_tokens"] for group in new.get("gates",{}).values() for call in group.values())])
    line("Технических неудачных вызовов", [sum(len(fs) for fs in r.get("failures",{}).values()) for r in modes])
    lines.extend(["", "## Итоги по диалогам", "", f"| Диалог | Эталон | Один проход | {name} | Совпадения до → после |","|---|---:|---:|---:|---:|"])
    for cid,t in totals.items():
        keys=[k for k in truth if k[0]==cid]
        line(cid,[*(display(x) for x in t)," → ".join(str(correct(p,keys)) for p in predictions)])
    lines.extend(["","## Где меняется N/A","","| Критерий | Эталон N/A | Верно до | Верно после | Ложных до | Ложных после |","|---|---:|---:|---:|---:|---:|"])
    for c in criteria:
        if not c["allow_na"]:continue
        keys={k for k in truth if k[1]==c["id"]}
        applicable_na=keys & bound
        line(c["title"],[len(applicable_na),*(correct(p,applicable_na) for p in predictions),
             *(sum(p[k]["value"]=="N/A" for k in keys-bound) for p in predictions)])
    optional=[c["id"] for c in criteria if c["allow_na"]]
    def all_optional_na(values):
        return sum(all(values[cid,k]["value"]=="N/A" for k in optional) for cid in conversations)
    lines.extend(["",f"Все {len(optional)} допускающих N/A критериев одновременно исключены в чатах: "
                  f"эталон {all_optional_na(truth)}, до {all_optional_na(predictions[0])}, после {all_optional_na(predictions[1])}."])
    lines.extend(["","## Изменения решений","","| Диалог | Критерий | Эталон | До | После | Эффект | ID первого этапа нового варианта |","|---|---|---|---|---|---|---|"])
    fixed=regressed=0
    for k,ref in truth.items():
        a,b=(p[k]["value"] for p in predictions)
        if a==b:continue
        effect="исправлено" if b==ref["value"] else ("регрессия" if a==ref["value"] else "изменена ошибка")
        fixed+=effect=="исправлено"; regressed+=effect=="регрессия"
        line(k[0],[k[1],display(ref["value"]),display(a),display(b),effect,", ".join(predictions[1][k]["evidence"])])
    lines.extend(["",f"Исправлено {fixed} решений, регрессий {regressed}. "
                  "Пропуск эталонного N/A учитывает и случаи review. Зависимые решения одного чата не являются независимыми наблюдениями.",
                  "", "### Воспроизводимость", "", f"Отпечатки исходного и нового запуска: `{old['config']['experiment']}` и `{new['config']['experiment']}`. "
                  "Сырые промежуточные решения сохранены в локальном issai.json, эталон читался только при сравнении."])
    (output/"comparison.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    provenance = ("ID сохранены с первого этапа. Для критериев с N/A состояние условия и его обоснование взяты из отдельного запроса о применимости."
                  if isolated else "Наблюдение и состояние условия N/A взяты из сохранённого первого вызова.")
    trace=["# Основания для изменений и оставшихся ошибок","", provenance,
           "В строке нового решения приведено основание, переданное в конечный результат.", ""]
    for k,ref in truth.items():
        before,after=(mode[k] for mode in predictions)
        if before["value"]==after["value"]==ref["value"]:continue
        cid,criterion=k
        plan=new["plans"][cid]["rows"][criterion]
        trace.extend([f"## {cid} · {criterion}", "",
                      f"**Разметка — {display(ref['value'])}.** {ref['reason']} ({', '.join(sorted(ref['evidence']))})", "",
                      f"**До — {display(before['value'])}.** {before['reason']} ({', '.join(before['evidence'])})", "",
                      f"**Применимость — {plan['condition']}.** {plan.get('gate_reason',plan['fact'])} ({', '.join(plan['evidence'])})", "",
                      f"**После — {display(after['value'])}.** {after['reason']}", ""])
    (output/"trace.md").write_text("\n".join(trace),encoding="utf-8")
    print(f"Verified {len(truth)} paired decisions, {len(common)} common totals; {fixed} fixes, {regressed} regressions.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated",action="store_true")
    main(OUTPUT.parent/"evidence-isolated-gate-v1" if parser.parse_args().isolated else OUTPUT)
