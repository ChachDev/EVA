"""Rehearse an organizer file import + ten real evaluations on the disposable local stack."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

API = "http://127.0.0.1:18500"
ML = Path(__file__).resolve().parents[1]
CASES = [
    ("RU", "agent chat", "Где найти историю уведомлений?", "Здравствуйте, я Лена. В меню откройте «Уведомления», затем «История».", "Нашёл, спасибо.", "Рада помочь, до свидания!"),
    ("KZ", "agent chat", "Қолданбаның тілін қайдан ауыстырамын?", "Сәлеметсіз бе! Мен Айыммын. Профильден «Тіл» бөлімін ашыңыз.", "Ауыстырдым, рақмет.", "Сау болыңыз!"),
    ("EN", "agent chat", "Where are the notification settings?", "Hi, I'm John. Open Profile, then Notifications.", "I found the switch, thanks.", "Happy to help. Goodbye!"),
    ("RU", "voice call", "Как открыть раздел справки в браузере?", "Добрый день, я Анна. Нажмите на знак вопроса в верхнем меню.", "Вижу страницу справки.", "Спасибо за звонок, до свидания."),
    ("KZ", "voice call", "Байланыс нөмірін қайдан таба аламын?", "Сәлеметсіз бе, мен Дина. Сайттағы «Байланыстар» бөлімін ашыңыз.", "Телефон нөмірін көрдім.", "Сау болыңыз!"),
    ("EN", "voice call", "How do I download the public guide?", "Hello, I'm Jo. Open Help and choose Download guide.", "The guide opened.", "Glad it worked. Have a good day."),
    ("RU", "agent chat", "Не могу понять, где открыть настройки.", "Где-нибудь в меню.", "В каком разделе?", "Не знаю."),
    ("KZ", "agent chat", "Жаңа құпиясөзді өзім қалай орнатамын?", "Сәлеметсіз бе. Кіру бетінен «Құпиясөзді ұмыттым» тармағын ашыңыз.", "Хат келді, әлі ашпадым.", "Көмек керек болса, жазыңыз. Сау болыңыз."),
    ("EN", "agent chat", "Can you tell me the exact balance in my account?", "I cannot disclose a balance in this chat before verification. Please open Account in the signed-in app.", "I will try that.", "Thank you, goodbye."),
    ("RU", "agent chat", "Что означает значок звезды рядом с инструментом?", "Здравствуйте, я Ирина. Нажатие добавляет инструмент в избранное.", "Добавил, получилось.", "Хорошего дня, до свидания!"),
]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-dir", type=Path, required=True)
    parser.add_argument("--version",type=int,default=2)
    args=parser.parse_args()
    if os.getenv("EVA_VERIFY_ISOLATED") != "1":
        parser.error("Set EVA_VERIFY_ISOLATED=1 only for the disposable local stack.")
    backend=args.backend_dir.resolve()
    if not backend.is_relative_to(ML/".tmp") or not (backend/"app/cli.py").is_file():
        parser.error("Use a staged backend under ml/.tmp, never the team's DB.")
    run=uuid4().hex[:12]
    out=ML/"data/live-project-check"/("batch-"+run)
    out.mkdir(parents=True)
    prefix="qa-local-"+run
    records=[]
    for i,(lang,channel,*lines) in enumerate(CASES,1):
        transcript=[{"speaker":role,"text":line,
            "timestamp":(f"00:{n*5:02d}" if channel=="agent chat" else f"2026-11-08T12:00:{n*5:02d}+05:00")}
            for n,(role,line) in enumerate(zip(("client","operator","client","operator"),lines,strict=True))]
        records.append(dict(conversation_id=f"{prefix}-{i:02d}",language=lang,channel=channel,transcript=transcript,audio_url=None))
    envelope=backend/"data"/(prefix+".json")
    envelope.write_text(json.dumps(dict(error="",isSuccess=True,data=dict(dataset_group_id=prefix,total_records=10,records=records)),ensure_ascii=False),encoding="utf-8")
    http=build_opener(ProxyHandler({}))
    def api(path,payload=None):
        req=Request(API+path,data=json.dumps(payload).encode() if payload is not None else None,headers={"Content-Type":"application/json"})
        with http.open(req,timeout=150) as response:return response.status,json.load(response)
    versions=api("/api/v1/checklists")[1]
    assert any(v["id"]=="freedom-cqa" and v["version"]==args.version for v in versions)
    env={**os.environ,"DATABASE_URL":"sqlite:///"+(backend/"data/live-check.sqlite3").as_posix(),"PYTHONDONTWRITEBYTECODE":"1"}
    start=monotonic()
    subprocess.run([sys.executable,"-B","-m","app.cli","import-dataset","--file",str(envelope),"--group",prefix],check=True,cwd=backend,env=env)
    results=[]
    for row in records:
        cid=row["conversation_id"]
        t=monotonic()
        entry={"id":cid,"language":row["language"],"channel":row["channel"]}
        try:
            assert api("/api/v1/conversations/"+cid)[1]["id"]==cid, "Backend must see the staged import"
            status,rec=api("/api/v1/evaluations",dict(conversation_id=cid,checklist_id="freedom-cqa",checklist_version=args.version))
            original=rec["original"]
            snap=api("/api/v1/evaluations/"+rec["id"]+"/snapshot")[1]
            assert original["provider"]=="llama.cpp" and original["model"]=="Qwen3.5-9B-Kazakh.Q5_K_M"
            assert any("consultation-v1.1" in w for w in original["warnings"])
            assert snap["checklist"]["version"]==original["checklist_version"]==args.version
            assert snap["conversation"]["id"]==original["conversation_id"]==cid
            assert len(original["criteria"])==8
            assert {v["criterion_id"] for v in original["criteria"]}=={c["id"] for c in snap["checklist"]["criteria"]}
            ids={m["id"] for m in snap["conversation"]["messages"]}
            assert all(e["message_ids"] and set(e["message_ids"])<=ids for c in original["criteria"] for e in c["evidence"])
            reviews=sum(c["status"]=="needs_review" for c in original["criteria"])
            scoring=rec["scoring"]
            assert (scoring["status"]=="needs_review")==bool(reviews)
            assert (scoring["total"] is not None)==(scoring["status"]=="complete")
            entry.update(http=status,evaluation=rec["id"],total=rec["scoring"]["total"],review=reviews)
        except Exception as exc:
            entry["error"]=type(exc).__name__+": "+str(exc)
        entry["seconds"]=round(monotonic()-t,2)
        results.append(entry)
        print(cid,entry.get("http",entry.get("error")),entry["seconds"],flush=True)
    receipt=dict(results=results,seconds_import_to_last_snapshot=round(monotonic()-start,2),version=args.version,synthetic=True)
    (out/"batch.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(out)
    if not all(r.get("http")==201 for r in results):raise SystemExit(1)

if __name__=="__main__":
    main()
