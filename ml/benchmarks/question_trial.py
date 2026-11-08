"""Paired local trial; existing annotated cohorts are retrospective. Synthetic pairs are not gold chats."""
import argparse,json
from pathlib import Path
from types import SimpleNamespace
from benchmarks.audit_run import inputs, CHECK, ROOT, Meter
from benchmarks.targeted_trial import split,read
from eva_ml.questions import QuestionsAuditor
from eva_ml.consultation_v11 import select_provider
from eva_ml.issai import digest

def examples():
    for lang,q,a,b in [
        ("RU","Как восстановить доступ и как сменить номер телефона?","Чтобы восстановить доступ, нажмите «Забыли пароль».","Для смены номера откройте форму смены номера в профиле."),
        ("EN","How do I recover access and change my phone number?","To recover access, select Forgot password.","To change the number, open the number change form in your profile."),
        ("KZ","Кіруді қалай қалпына келтіремін және телефон нөмірін қалай ауыстырамын?","Кіруді қалпына келтіру үшін «Құпиясөзді ұмыттым» түймесін басыңыз.","Нөмірді ауыстыру үшін профильдегі нөмірді өзгерту формасын ашыңыз.")]:
        for full in (False,True):
            yield {"id":f"qa-{lang}-{int(full)}","language":lang,"channel":"agent_chat","audio_url":None,
                "source":{"complete_start":True,"complete_end":True},
                "messages":[{"id":"m1","speaker":"client","timestamp":"00:00","text":q,"offset_seconds":0},
                {"id":"m2","speaker":"operator","timestamp":"00:10","text":a+(" "+b if full else ""),"offset_seconds":10}]},{"consultation":20 if full else 10}

def main():
    p=argparse.ArgumentParser();p.add_argument("--port",type=int);p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    if not args.out.resolve().is_relative_to((ROOT/"data").resolve()):p.error("Use ignored ml/data")
    if args.port is None:
        from eva_ml.engine import loaded_engine
        with loaded_engine() as worker:
            args.port=int(worker.base.rsplit(":",1)[1]);run(args)
    else:run(args)

def run(args):
    arc=ROOT/"data/baseline-plus-audit-v1"
    groups=split(read(arc/"human.jsonl"),read(arc/"pilot.jsonl"))
    dev=ROOT/".tmp/dev-ml-repair-2111/dev.json"
    conv={c["id"]:c for ref in [ROOT/"data/blind15-comparison-20261108/reference.json",ROOT/"data/consultation-v1.1/pilot-96.csv"]
          for c,_ in inputs(SimpleNamespace(pairs=False,voices=False,reference=ref,dev=dev))}
    engine=Meter(args.port,"Qwen3.5-9B-Kazakh.Q5_K_M");base=select_provider(engine,"consultation-v1.1")
    auditor=QuestionsAuditor(base,cache_size=0)
    cases=[("synthetic",c,g,None) for c,g in examples()]
    for name in ("reserved","pilot","develop"):
        cases.extend((name,conv[r["cid"]],r["gold"],r["audit"]) for r in groups[name])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open("x",encoding="utf8") as out:
        for cohort,c,g,old in cases:
            req={"schema_version":"1.0.0","conversation":c,"checklist":CHECK}
            engine.calls=engine.tokens=0;engine.token_known=True
            if old and (digest(c)!=old["versions"]["transcript"] or digest(CHECK)!=old["versions"]["checklist"] or
                old["versions"]["baseline_variant"]!=base.experiment_hash or not auditor.model_revision["declared"] or
                auditor.model_revision["declared"]!=old["versions"]["model_revision"]["declared"]):raise ValueError("Changed source/model")
            bundle=auditor.evaluate(req,saved_baseline=old["verification"]["baseline"] if old else None)
            out.write(json.dumps(dict(cohort=cohort,cid=c["id"],gold=g,language=c["language"],**bundle),ensure_ascii=False)+"\n");out.flush()

if __name__=="__main__":main()
