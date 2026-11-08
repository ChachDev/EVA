"""Verify actual asynchronous shadow completion against an existing synthetic integration case."""
import json
from pathlib import Path
import sys
import time
import urllib.request

receipt=json.loads(Path(sys.argv[2]).read_text(encoding="utf8"))
base=sys.argv[1]
if not base.startswith("http://127.0.0.1:") or base.endswith(":8000"):raise ValueError("Use an isolated loopback backend")
def call(path,body=None):
    req=urllib.request.Request(base+"/api/v1/evaluations"+path,data=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=120) as reply:return json.load(reply)
source=call("/"+receipt["evaluation"])["original"]
record=call("",{"conversation_id":source["conversation_id"],"checklist_id":source["checklist_id"],
                "checklist_version":source["checklist_version"]})
path="/"+record["id"]
for _ in range(120):
    export=call(path+"/audit")
    if export["audit"]["status"]!="pending":break
    time.sleep(1)
shadow=export["audit"]
assert shadow["status"]=="completed" and shadow["mode"]=="shadow" and shadow["candidate"]["audit"]["run_id"]
assert call(path)==record and export["review"]["original"]==record
print("PASS: shadow candidate persisted, primary original and score unchanged")
