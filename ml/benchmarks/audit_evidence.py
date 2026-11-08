"""Score reference coverage, not an expert judgment of semantic adequacy (optional openpyxl)."""
from collections import Counter
import json
from pathlib import Path
import re
from zipfile import ZipFile
import sys
from openpyxl import load_workbook

root=Path(__file__).resolve().parents[1]
keys={c["title"]:c["id"] for c in json.loads((root.parent/"contracts/examples/checklist.json").read_text(encoding="utf8"))["criteria"]}
gold=json.loads((root/"data/blind15-comparison-20261108/reference.json").read_text(encoding="utf8"))
book=load_workbook(root/"data/blind15-comparison-20261108/DEV-blind-15-all-annotators.xlsx",data_only=True,read_only=True)
refs={}
for sheet in book:
    for row in sheet.iter_rows(min_row=6,max_col=5,values_only=True):
        cid,title,_,label,ids=row
        if title not in keys or not cid: continue
        refs[sheet.title,cid,keys[title]]=str(label),set(re.findall(r"m[0-9]+",str(ids)))
book.close()
new={r["cid"]:r["response"] for r in map(json.loads,Path(sys.argv[1]).read_text(encoding="utf8").splitlines())}
with ZipFile(root/"data/consultation-v1.1/sealed/blind15-consultation-v1.1.zip") as z:
    old={cid:json.loads(z.read(cid+".json"))["original"] for cid in new}
for name,outputs in [("baseline",old),("candidate",new)]:
    stats=Counter()
    for row in gold:
        cid,k=row["chat"],row["criterion"]; expected=str(row["gold"])
        # Use only annotators participating in the adjudicated answer.
        agreed=[refs.get((i,cid,k)) for i in re.findall(r"[1234]",str(row["source"]))]
        anchors=[ids for x in agreed if x for label,ids in [x] if label==expected and ids]
        if not anchors: continue
        stats["with_reference"]+=1
        decision=next(d for d in outputs[cid]["criteria"] if d["criterion_id"]==k)
        val=decision["value"]
        label=("review" if decision["status"]=="needs_review" else "N/A" if val is None else
               ("Да" if val else "Нет") if isinstance(val,bool) else str(val))
        ids={i for e in decision["evidence"] for i in e["message_ids"]}
        stats["same_label_and_full_reference"]+=int(label==expected and any(a<=ids for a in anchors))
        stats["same_label_and_any_reference"]+=int(label==expected and any(a&ids for a in anchors))
    print(name,dict(stats))
