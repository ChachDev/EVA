"""Exercise published checklist versions on the twelve development pilots only.

Usage from ml: python -B -m benchmarks.backend_replacements BASE_URL OLD_PILOT_DIR
OUTPUT_DIR CHECKLIST_V2. All records are local; no holdout archive is opened.
"""
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

from eva_ml.contracts import Contracts
from eva_ml.issai import plan_for


def variants(v1, v2):
    def replace(key, patch, extra=None):
        return [{**c, **(patch if c["id"] == key else (extra or {}).get(c["id"], {}))}
                for c in v1["criteria"]]
    copied = next(c for c in v2["criteria"] if c["id"] == "consultation")
    return [
        ("consultation", replace("consultation", copied)),
        ("callback", replace("empathy", dict(id="callback", title="Срок обратного звонка",
            instruction="Если клиент попросил перезвонить, оператор сообщил конкретный срок обратного звонка. Для «да» приведи просьбу клиента и последующий ответ оператора со сроком.",
            allow_na=True, na_condition="Клиент не просил об обратном телефонном звонке."))),
        ("identity", replace("identity", dict(instruction=
            "До раскрытия данных или защищённого действия оператор должен явно сослаться на успешную проверку личности либо система должна подтвердить её завершение. Запрос данных для проверки без подтверждения успеха недостаточен. Для «да» приведи реплику оператора или системы с подтверждением."))),
        ("plain_language", replace("empathy", dict(id="plain_language", weight=5, title="Избегает жаргона",
            instruction="Оператор объясняет решение понятным клиенту языком. Если использован профессиональный термин или аббревиатура, оператор поясняет их. При отсутствии терминов в содержательном ответе условие выполнено. Когда ответ есть, приведи его реплику; при отсутствии ответа — проверенный участок."),
            {"needs":{"weight":20}, "dialogue":{"weight":20}})),
    ]


def expected_total(cs, rows):
    if any(r["status"] == "needs_review" for r in rows): return "needs_review", None
    cs = {c["id"]:c for c in cs}
    weight, earned = Decimal(0), Decimal(0)
    for r in rows:
        c = cs[r["criterion_id"]]
        if c["type"] == "flag" or r["value"] is None: continue
        w = Decimal(str(c["weight"]))
        weight += w
        earned += w * (Decimal(int(r["value"])) if c["type"] == "boolean"
                       else Decimal(str(r["value"])) / Decimal(str(max(c["scale"]))))
    return (("complete", float((100*earned/weight).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)))
            if weight else ("not_calculable", None))


def author_check(target, row, messages):
    order = {m["id"]:i for i,m in enumerate(messages)}
    authors = {m["id"]:m["speaker"] for m in messages}
    ids = [i for e in row["evidence"] for i in e["message_ids"]]
    def times(role): return [order[i] for i in ids if authors[i] == role]
    op, cl, sy = times("operator"), times("client"), times("system")
    val = row["value"]
    if val is None: return None
    if target == "consultation":
        if val == 2: return any(o < c for o in op for c in cl)
        if val == 1: return bool(op)
    if target == "callback" and val is True: return any(c < o for o in op for c in cl)
    if target == "identity" and val is True: return bool(op or sy)
    if target == "plain_language" and any(m["speaker"]=="operator" for m in messages):
        return bool(op)
    return None


def main(base, old, out, v2_path, failed_log=None):
    if urlsplit(base).hostname not in ("127.0.0.1", "localhost"): raise ValueError("Loopback only")
    old, out = Path(old), Path(out)
    known = sorted(p.name.removeprefix("pilot-1-").removesuffix(".json") for p in old.glob("pilot-1-p1-*.json"))
    assert len(known)==12 and len(set(known))==12
    manifest = json.loads((old.parent/"manifest.json").read_bytes())
    assert set(known) == set(manifest["pilot"])
    assert set(known).isdisjoint(cid for ids in manifest["selected"].values() for cid in ids)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def http(path, body=None):
        req=urllib.request.Request(base+path, data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),
                                   headers={"Content-Type":"application/json"})
        start=time.perf_counter()
        try:
            with opener.open(req,timeout=180) as r: return r.status,json.load(r),time.perf_counter()-start
        except urllib.error.HTTPError as e: return e.code,json.loads(e.read()),time.perf_counter()-start
    versions=http("/api/v1/checklists")[1]
    v1=next(c for c in versions if c["id"]=="freedom-cqa" and c["version"]==1)
    changes=variants(v1,json.loads(Path(v2_path).read_bytes()))
    resume = failed_log == "--resume"
    failed = None if failed_log is None or resume else {(r["group"],r["cid"])
        for r in (json.loads(s) for s in Path(failed_log).read_text(encoding="utf8").splitlines())
        if r["event"]=="eval" and r["http"]!=201}
    checks=Contracts()
    def signature(r):return (r["status"],r["value"],r["evidence"])
    def emit(row):
        with (out/"runs.jsonl").open("a",encoding="utf8") as f: f.write(json.dumps(row,ensure_ascii=False)+"\n")
    history = [] if not resume else [json.loads(s) for s in (out/"runs.jsonl").read_text(encoding="utf8").splitlines()]
    assert resume or not (out/"runs.jsonl").exists()
    for group,version,target,cs in [("v1-first",1,None,None),("v1-repeat",1,None,None)]+[
            ("v"+str(i),i,target,cs) for i,(target,cs) in enumerate(changes,2)]:
        publish_start=time.perf_counter()
        done = {r["cid"] for r in history if r["event"]=="eval" and r["group"]==group}
        selected=[cid for cid in known if cid not in done and (failed is None or (group,cid) in failed)]
        if not selected: continue
        if failed is not None or (resume and any(r["event"]=="publish" and r["group"]==group for r in history)):
            stored=next(c for c in versions if c["id"]=="freedom-cqa" and c["version"]==version)
            assert cs is None or stored["criteria"]==cs
        elif cs is not None:
            code,stored,duration=http("/api/v1/checklists/freedom-cqa/versions",{
                "base_version":version-1,"name":v1["name"],"criteria":cs})
            assert code==201 and stored["version"]==version and stored["criteria"]==cs, (code,group)
            emit(dict(event="publish",group=group,seconds=duration))
        else: stored=v1
        assert sum(c["weight"] for c in stored["criteria"])==100
        (out/(group+"-checklist.json")).write_text(json.dumps(stored,ensure_ascii=False,indent=2),encoding="utf8")
        for idx,cid in enumerate(selected):
            status,response,elapsed=http("/api/v1/evaluations",dict(conversation_id=cid,checklist_id="freedom-cqa",checklist_version=version))
            line=dict(event="eval",group=group,cid=cid,http=status,seconds=elapsed)
            if idx==0 and failed is None and not done: line["first_ready_since_save"]=time.perf_counter()-publish_start
            if status==201:
                orig=response["original"]
                _,snapshot,_=http("/api/v1/evaluations/"+response["id"]+"/snapshot")
                assert snapshot["checklist"]==stored and snapshot["conversation"]["id"]==cid
                checks.validate_response(snapshot,orig)
                instructions,schema,fingerprint=plan_for(snapshot["checklist"],orig["model"])
                target_instruction=next((c for c in instructions if c["id"]==target),None)
                if target: assert "questions" not in target_instruction and "value" in schema["properties"][target]["properties"]
                line["plan"]=fingerprint
                rows=orig["criteria"]
                line.update(rows=len(rows),review=sum(r["status"]=="needs_review" for r in rows),
                            stub=orig["provider"]!="llama.cpp",total=response["scoring"]["total"])
                assert expected_total(stored["criteria"],rows)==(response["scoring"]["status"],line["total"])
                if target:
                    target_row=next(r for r in rows if r["criterion_id"]==target)
                    line.update(author_ok=author_check(target,target_row,snapshot["conversation"]["messages"]),
                                changed_review=target_row["status"]=="needs_review")
                else:
                    previous=json.loads((old/("pilot-1-"+cid+".json")).read_bytes())
                    def pairs(r):return {v["criterion_id"]:signature(v) for v in r["original"]["criteria"]}
                    prev,now=pairs(previous),pairs(response)
                    line["equal_labels"]=sum(v[:2]==prev[k][:2] for k,v in now.items())
                    line["equal_ids"]=sum([e["message_ids"] for e in v[2]]==[e["message_ids"] for e in prev[k][2]] for k,v in now.items())
                    line["equal_total"]=(response["scoring"]==previous["scoring"])
                (out/(group+"-"+cid+".json")).write_text(json.dumps(response,ensure_ascii=False),encoding="utf8")
            emit(line)
            print(group,idx+1,cid,status,round(elapsed,2),flush=True)


if __name__=="__main__": main(*sys.argv[1:])
