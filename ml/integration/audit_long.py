"""Verify an actual context overflow, retained ranges and zero fabricated clean verdicts."""
from pathlib import Path
import json
import sys
import urllib.request

root=Path(__file__).resolve().parents[2]
check=json.loads((root/"contracts/examples/checklist.json").read_text(encoding="utf8"))
conversation={"id":"synthetic-long-source","language":"EN","channel":"agent_chat","audio_url":None,"messages":[
    {"id":"m0001","speaker":"client","timestamp":None,"offset_seconds":None,"text":" 12345"*15000},
    {"id":"m0002","speaker":"system","timestamp":None,"offset_seconds":None,"text":"Identity verified.","event":"identity_verified"},
    {"id":"m0003","speaker":"operator","timestamp":None,"offset_seconds":None,"text":"Pay me personally for private financial advice."}]}
base=sys.argv[1]
if not base.startswith("http://127.0.0.1:"):raise ValueError("Local ML only")
req=urllib.request.Request(base+"/v1/audit",data=json.dumps({"schema_version":"1.0.0","checklist":check,"conversation":conversation}).encode(),
                           headers={"Content-Type":"application/json"})
with urllib.request.urlopen(req,timeout=120) as reply: result=json.load(reply)
audit=result["audit"]; manifest=audit["long_input"]
assert ["m0001","m0001"] in manifest["unread"] and manifest["message_count"]==3
assert manifest["checked"] and all(f["status"]=="needs_review" for f in audit["sentinel"])
print("PASS:",json.dumps({"checked":manifest["checked"],"unread":manifest["unread"],"calls":audit["calls"],
                         "seconds":audit["seconds"],"tokens":audit["tokens"]}))
