"""Exercise two fresh public sessions using only synthetic dialogue and optional synthetic speech."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"
API = "/api/v1"


def request(client, method, path, code=200, **kwargs):
    response = client.request(method, API + path, **kwargs)
    if response.status_code != code:
        raise RuntimeError(f"{method} {path}: expected {code}, received {response.status_code}: {response.text[:160]}")
    return response.json() if code != 204 else None


def start(client):
    home = client.get("/")
    assert home.status_code == 200 and "EVA" in home.text
    assert "HttpOnly" in home.headers["set-cookie"] and "Secure" in home.headers["set-cookie"]
    if client.base_url.scheme == "http":
        # HTTP transport is used only to exercise the loopback gateway before opening a tunnel.
        client.headers["Cookie"] = "__Host-eva_workspace=" + client.cookies["__Host-eva_workspace"]
    assert request(client, "GET", "/conversations") == []
    assert request(client, "GET", "/checklists")[0]["id"] == "freedom-cqa"


def evaluate(client, cid):
    start_time = monotonic()
    result = request(client, "POST", "/evaluations", 201, json={
        "conversation_id":cid, "checklist_id":"freedom-cqa", "checklist_version":1})
    count = len(result["original"]["criteria"])
    print(f"Persisted evaluation with {count} criteria in {monotonic() - start_time:.1f}s", flush=True)
    assert count > 0
    request(client, "GET", f"/evaluations/{result['id']}/snapshot")
    return result["id"]


def speech(client, other, path):
    asset = request(client, "POST", "/audio-assets", 201, content=path.read_bytes(),
        headers={"Content-Type":"audio/wav", "X-Audio-Filename":"synthetic.wav", "Idempotency-Key":str(uuid4())})
    job = request(client, "POST", "/transcriptions", 202, json={
        "asset_id":asset["id"], "operation_id":str(uuid4()), "language":"EN"})
    job_path = "/transcriptions/" + job["id"]
    start_time = monotonic()
    while job["status"] in ("queued","running") and monotonic() - start_time < 150:
        sleep(1)
        job = request(client, "GET", job_path)
    assert job["status"] in ("completed","needs_review"), (job["status"], job.get("error_code"))
    segments = job["result"]["segments"]
    assert segments
    print(f"Whisper returned {len(segments)} segments in {monotonic() - start_time:.1f}s", flush=True)
    if job["status"] == "needs_review":
        job = request(client, "POST", job_path + "/review", json={"roles":["operator"] * len(segments),"operation_id":str(uuid4())})
    source = request(client, "GET", job_path + "/export")
    assert source["messages"][0]["offset_seconds"] == segments[0]["start_seconds"]
    first = request(client, "GET", "/conversations/" + job["conversation_id"])
    assert first["audio_url"] == f"/api/v1/audio-assets/{asset['id']}/content"
    assert client.get(first["audio_url"], headers={"Range":"bytes=0-15"}).status_code == 206
    assert other.get(first["audio_url"]).status_code == 404
    copied = request(client, "POST", "/conversations", 201, json=dict(source,operation_id=str(uuid4())))
    assert copied["id"] != first["id"] and copied["messages"] == first["messages"]
    evaluate(client, copied["id"])
    for cid in (first["id"], copied["id"]):
        request(client, "DELETE", "/conversations/" + cid, 204)
    assert request(client, "GET", "/transcriptions")["items"][0]["display_name"] == "synthetic.wav"
    assert request(client, "GET", job_path + "/export") == source
    print("Audio history, JSON reimport, playback and role preservation passed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--audio", type=Path, help="Synthetic WAV, spoken in English by an operator")
    parser.add_argument("--isolation-only", action="store_true")
    args = parser.parse_args()
    if urlsplit(args.url).scheme != "https" and urlsplit(args.url).hostname != "127.0.0.1":
        parser.error("Use HTTPS for public workspaces.")
    with httpx.Client(base_url=args.url, timeout=125) as client, httpx.Client(base_url=args.url, timeout=125) as other:
        start(client)
        start(other)
        chat = json.loads((EXAMPLES / "conversation-create.json").read_text(encoding="utf-8"))
        chat["operation_id"] = str(uuid4())
        conversation = request(client, "POST", "/conversations", 201, json=chat)
        path = "/conversations/" + conversation["id"]
        assert other.get(API + path).status_code == other.delete(API + path).status_code == 404
        assert client.delete(API + path, headers={"Origin":"https://another.example"}).status_code == 403
        assert request(other, "GET", "/conversations") == []
        print("Independent sessions, cross-session read/delete and cross-origin checks passed.", flush=True)
        if not args.isolation_only:
            eid = evaluate(client, conversation["id"])
            request(client, "DELETE", "/evaluations/" + eid, 204)
            assert client.get(API + "/evaluations/" + eid).status_code == 404
        request(client, "DELETE", path, 204)
        check = json.loads((EXAMPLES / "checklist-publish.json").read_text(encoding="utf-8"))
        version_path = "/checklists/freedom-cqa/versions"
        version = request(client, "POST", version_path, 201, json=check)["version"]
        request(client, "DELETE", version_path + "/" + str(version), 204)
        assert request(client, "GET", "/checklists")[0]["version"] == 1
        assert request(client, "POST", version_path, 201, json=check)["version"] > version
        if args.audio:
            speech(client, other, args.audio)
        print("Public smoke passed.", flush=True)


if __name__ == "__main__":
    main()
