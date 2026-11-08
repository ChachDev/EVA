from uuid import uuid4

from starlette.testclient import TestClient

from app.public import COOKIE, public_app


def test_public_session_persists_after_restart_and_isolates_other_visitors(tmp_path, contracts):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>EVA</html>",encoding="utf-8")
    root = tmp_path / "sessions"
    target = {"base_url":"https://eva.example"}
    with TestClient(public_app(root,dist), **target) as client:
        root_response = client.get("/")
        token = client.cookies[COOKIE]
        assert all(v in root_response.headers["set-cookie"] for v in ("Secure","HttpOnly","SameSite=Lax"))
        assert "no-store" in root_response.headers["cache-control"]
        body = contracts.example("conversation-create")
        body["operation_id"] = str(uuid4())
        cid = client.post("/api/v1/conversations",json=body).json()["id"]
        path = "/api/v1/conversations/" + cid
        client.cookies.clear()
        assert client.get(path).status_code == 401
        client.get("/")
        assert client.get(path).status_code == client.delete(path).status_code == 404
    with TestClient(public_app(root,dist), **target) as client:
        client.cookies.set(COOKIE,token)
        assert client.get(path).json()["id"] == cid
        assert client.delete(path,headers={"Origin":"https://external.example"}).status_code == 403
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404
