from copy import deepcopy
from uuid import uuid4
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

def test_manual_source_persists_without_inventing_times_or_roles(tmp_path):
    app=create_app(Settings(database_path=tmp_path/"trace.sqlite"),start_workers=False)
    payload={"operation_id":str(uuid4()),"channel":"agent_chat","language":"KZ",
             "source":{"target_operator_id":"A","complete_start":False},
             "messages":[{"speaker":"unknown","text":"Жоқ, нет.","language":"mixed",
                          "translation":{"language":"EN","text":"No."}},
                         {"speaker":"system","text":"System event.","event":"identity_verified"}]}
    with TestClient(app) as client:
        response=client.post("/api/v1/conversations",json=payload)
        assert response.status_code==201
        c=response.json();source=c["messages"][0]
        assert c["source"]==payload["source"] and source["id"]=="m0001"
        assert source["timestamp"] is source["offset_seconds"] is None and source["speaker"]=="unknown"
        assert source["translation"]==payload["messages"][0]["translation"] and source["text"]=="Жоқ, нет."
        assert client.post("/api/v1/conversations",json=payload).json()==c
        wrong=deepcopy(payload);wrong["messages"][1]["speaker"]="client"
        assert client.post("/api/v1/conversations",json=wrong).status_code==422
        wrong=deepcopy(payload);wrong["source"]["target_operator_id"]="B"
        assert client.post("/api/v1/conversations",json=wrong).status_code==409
