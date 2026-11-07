"""Compare completed local predictions with the primary DEV annotations."""
from decimal import Decimal, ROUND_HALF_UP
import json
from statistics import mean, median

from openpyxl import load_workbook

from local_compare import OUTPUT, WORKBOOK, CHECKLIST


def read_reference(criteria):
    titles = {c["title"]: c["id"] for c in criteria}
    book = load_workbook(WORKBOOK, read_only=True, data_only=True)
    reference = {}
    try:
        for cid, lang, title, value, reason, first, second, other in book["Разметка"].iter_rows(min_row=6, values_only=True):
            value = {"Да": "true", "Нет": "false", "На проверку": "review"}.get(value, str(value))
            evidence = {mid.strip() for cell in (first, second, other) if cell for mid in cell.split(",")}
            reference[cid, titles[title]] = {"value": value, "evidence": evidence, "lang": lang, "reason": reason}
    finally:
        book.close()
    if len(reference) != 96:
        raise ValueError("Expected 96 complete annotations")
    return reference


def score(criteria, values):
    weight = 0
    points = Decimal(0)
    for c in criteria:
        value = values[c["id"]]
        if value == "review":
            return None
        if value == "N/A" or c["type"] == "flag":
            continue
        weight += c["weight"]
        ratio = Decimal(value) / max(c["scale"]) if c["type"] == "scale" else Decimal(value == "true")
        points += c["weight"] * ratio
    if not weight:
        return None
    return (100 * points / weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def display(v):
    return {"true": "Да", "false": "Нет", "review": "На проверку"}.get(v, str(v)) if v is not None else "—"


def main():
    criteria = json.loads(CHECKLIST.read_text(encoding="utf-8"))["criteria"]
    ref = read_reference(criteria)
    cids = list(dict.fromkeys(cid for cid, _ in ref))
    names = ("issai", "qwen")
    runs = {n:json.loads((OUTPUT/(n+".json")).read_text(encoding="utf-8")) for n in names}
    if len({r["config"]["experiment"] for r in runs.values()}) != 1:
        raise ValueError("Different benchmark configurations")
    if any(set(r["items"]) != set(cids) for r in runs.values()):
        raise ValueError("Incomplete benchmark")
    values = {n:{(cid,k):item for cid,d in runs[n]["items"].items() for k,item in d["criteria"].items()} for n in names}
    totals = {}
    for cid in cids:
        get = lambda data: score(criteria, {c["id"]:data[cid,c["id"]]["value"] for c in criteria})
        totals[cid]=[get(data) for data in (ref,values["issai"],values["qwen"])]
    lines=["# Сравнение локальных моделей на DEV", "",
           "12 синтетических чатов, по 4 RU/KZ/EN. Эталон — первичная разметка Codex в рабочей книге. "
           "В модель передавались только реплики и чек-лист freedom-cqa v1. Это выборка настройки. "
           "Во всех 12 эталонах флаг грубой ошибки отрицательный, поэтому на них можно проверить только ложные срабатывания.", "",
           "| Показатель | ISSAI | Qwen |","|---|---:|---:|"]
    def table_row(label, cells):
        lines.append("| " + " | ".join(map(str,(label,*cells))) + " |")
    def matched(n, keys):
        return sum(ref[k]["value"]==values[n][k]["value"] for k in keys)
    table_row("Точное совпадение", [f"{matched(n,ref)}/96" for n in names])
    for language in ("RU","KZ","EN"):
        keys=[k for k,v in ref.items() if v["lang"]==language]
        table_row(language,[f"{matched(n,keys)}/{len(keys)}" for n in names])
    for c in criteria:
        keys=[k for k in ref if k[1]==c["id"]]
        table_row(c["title"],[f"{matched(n,keys)}/12" for n in names])
    table_row("Технических неудачных вызовов",
              [sum(len(attempts) for attempts in runs[n].get("failures", {}).values()) for n in names])
    for lab,value in [("Ошибочно присвоено N/A","fp"),("Пропущено эталонное N/A","fn"),("Передано на проверку","review")]:
        table_row(lab,[sum((v["value"]=="N/A" and ref[k]["value"]!="N/A") if value=="fp" else
                           (v["value"]!="N/A" and ref[k]["value"]=="N/A") if value=="fn" else v["value"]=="review"
                           for k,v in values[n].items()) for n in names])
    na_keys=[k for k in ref if ref[k]["value"]=="N/A"]
    table_row("Верно распознано эталонных N/A", [f"{matched(n, na_keys)}/{len(na_keys)}" for n in names])
    table_row("Есть хотя бы один общий ID доказательства с разметкой", [f"{sum(bool(ref[k]['evidence'] & set(v['evidence'])) for k,v in values[n].items())}/96" for n in names])
    table_row("Обоснований, достигших ограничения в 160 символов", [sum(len(v["reason"])>=160 for v in values[n].values()) for n in names])
    table_row("Диалогов с итогом",[f"{sum(t[i] is not None for t in totals.values())}/12" for i in (1,2)])
    table_row("Диалогов с итогом 100", [sum(t[i]==100 for t in totals.values()) for i in (1,2)])
    common=[t for t in totals.values() if all(v is not None for v in t)]
    table_row(f"Средняя абсолютная ошибка итога на общих диалогах ({len(common)})",
              [f"{mean(abs(t[0]-t[i]) for t in common):.2f}" if common else "—" for i in (1,2)])
    table_row("Медиана времени на чат, с",[round(median(v["seconds"] for v in runs[n]["items"].values()),1) for n in names])
    table_row("Сумма времени успешных запросов, с",[round(sum(v["seconds"] for v in runs[n]["items"].values()),1) for n in names])
    if common:
        constant_mae = mean(abs(t[0]-100) for t in common)
        lines.extend(["", f"Для сравнения, постоянный итог 100 на этих же {len(common)} диалогах дал бы среднюю абсолютную ошибку {constant_mae:.2f}."])
    lines.extend(["","## Итоги диалогов","","| Диалог | Разметка | ISSAI | Qwen | Совпало у ISSAI (из 8) | Совпало у Qwen (из 8) |","|---|---:|---:|---:|---:|---:|"])
    for cid,t in totals.items():
        keys = [k for k in ref if k[0] == cid]
        table_row(cid,[display(v) for v in t]+[matched(n,keys) for n in names])
    lines.extend(["","## Все расхождения","","В колонках моделей указаны значения и ID, выбранные моделями. Совпадение ID показывает пересечение ссылок; смысловая достаточность проверяется по исходным репликам.",
                  "","| Диалог | Критерий | Эталон | ISSAI | Qwen |","|---|---|---|---|---|"])
    for k in ref:
        if all(ref[k]["value"]==values[n][k]["value"] for n in names):
            continue
        row=[k[1],display(ref[k]["value"])+" · "+", ".join(sorted(ref[k]["evidence"]))]
        for n in names:
            pred=values[n][k]
            row.append(display(pred["value"])+" · "+", ".join(pred["evidence"]))
        table_row(k[0],row)
    lines.extend(["","## Параметры","", "Q5_K_M, llama.cpp b12186 Vulkan, RTX 4070, контекст 8192, greedy, seed 42, thinking off. Одинаковая схема ответа для каждой пары запусков. "
                  "На восемь критериев выделено 3200 токенов, причина ограничена 160 символами. Время на чат включает генерацию и валидацию ответа, без холодной загрузки модели. "
                  "Разведочный запуск ISSAI с неограниченной длиной причины прервался на третьем чате из-за незавершённого ответа. Его результаты в таблицах не используются.",
                  "", "ISSAI — [Q5_K_M, ревизия b681071](https://huggingface.co/mradermacher/Qwen3.5-9B-Kazakh-GGUF/tree/b68107190e1e995f4d2576bccd8b4b1e27805956). "
                  "Qwen — [Q5_K_M, ревизия 3885219](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5). "
                  "[Сборка llama.cpp](https://github.com/ggml-org/llama.cpp/releases/tag/b12186).",
                  "","Отпечаток общих входов и промпта: `"+runs["issai"]["config"]["experiment"]+"`."])
    (OUTPUT/"comparison.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    details = ["# Расхождения с первичной разметкой", "",
               "Причины моделей воспроизведены из их ответов. ID относятся к указанному диалогу.", ""]
    for key, truth in ref.items():
        misses = [n for n in names if values[n][key]["value"] != truth["value"]]
        if not misses:
            continue
        details.extend([f"## {key[0]} · {key[1]}", "",
                        f"**Разметка — {display(truth['value'])}.** {truth['reason']} ({', '.join(sorted(truth['evidence']))})", ""])
        for name in names:
            v = values[name][key]
            details.extend([f"**{name.upper()} — {display(v['value'])}.** {v['reason']} ({', '.join(v['evidence'])})", ""])
    (OUTPUT/"disagreements.md").write_text("\n".join(details), encoding="utf-8")
    print("Compared",len(ref),"criteria per model on",len(cids),"chats.")
    for n in names:
        print(n,"matches",matched(n,ref),"of",len(ref))


if __name__=="__main__":
    main()
