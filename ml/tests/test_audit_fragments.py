import json
from eva_ml.audit_trace import Trace
from eva_ml.audit_fragments import scan,CHAR_BUDGET,MAX_READS

def test_bounded_fragment_recovery_carries_source_events_and_never_proves_absence():
    rows=[{"id":"s","speaker":"system","event":"identity_verified","text":"Verified","timestamp":None,"offset_seconds":None}]
    for i in range(MAX_READS+3):
        rows.append({"id":str(i),"speaker":"operator","text":"x"*2800,"timestamp":None,"offset_seconds":None})
    rows.insert(2,{"id":"huge","speaker":"client","text":"x"*(CHAR_BUDGET+1),"timestamp":None,"offset_seconds":None})
    trace=Trace({"messages":rows,"language":"EN"})
    seen=[]
    class Engine:
        def request(self,route,payload):
            data=json.loads(payload["messages"][1]["content"]);seen.append(data)
            candidate=next((m["id"] for m in data["fragment"] if m["role"]=="operator"),"none")
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"private_service":candidate})}}]}
    result=scan(Engine(),"test",trace,{"private_service":"Synthetic policy"})
    assert result["calls"]==MAX_READS and result["cross_fragment_review"]
    assert ["huge","huge"] in result["unread"]
    assert any("s" in d["prior_system_events"] for d in seen[1:])
    observed={m["id"] for d in seen for m in d["fragment"]}
    assert set(trace.records)==observed|{first for first,last in result["unread"] if first==last}
