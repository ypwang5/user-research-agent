import pytest
from fastapi.testclient import TestClient

import memory_service.main as api
from memory_service.config import settings


@pytest.fixture
def client(store, monkeypatch):
    # 不受本机 .env 里 API_KEY 的影响；鉴权由 test_auth 单独覆盖
    monkeypatch.setattr(settings, "api_key", "")
    api.app.dependency_overrides[api.get_store] = lambda: store
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def _ingest(client, uid, content, summary="每月手工对账要花两天时间，经常对错"):
    return client.post("/v1/ingest", json={
        "project": "p", "interviewee_id": uid, "content": content,
        "insights": [{"type": "pain_point", "summary": summary, "quote": "q-" + uid}],
    })


def test_ingest_check_search_overview(client):
    r = _ingest(client, "U1", "c1")
    assert r.status_code == 200 and r.json()["stats"] == {"created": 1, "merged": 0, "same_interviewee": 0}
    r = _ingest(client, "U2", "c2")
    assert r.json()["stats"]["merged"] == 1

    assert client.post("/v1/sessions/check", json={"project": "p", "content": "c1"}).json()["duplicate"] is True
    assert client.post("/v1/sessions/check", json={"project": "p", "content": "new"}).json()["duplicate"] is False

    hits = client.post("/v1/search", json={"project": "p", "query": "对账"}).json()["results"]
    assert hits and hits[0]["mention_count"] == 2

    ov = client.get("/v1/overview", params={"project": "p"}).json()
    assert ov["total_insights"] == 1 and ov["total_interviewees"] == 2


def test_validation(client):
    r = client.post("/v1/ingest", json={"project": "p", "interviewee_id": "U1", "content": "x",
                                        "insights": [{"type": "pain_point", "summary": ""}]})
    assert r.status_code == 422
    r = client.post("/v1/ingest", json={"project": "p", "interviewee_id": "U1", "content": "x",
                                        "insights": [{"type": "bogus", "summary": "s"}]})
    assert r.status_code == 422


def test_redact_endpoint(client):
    r = client.post("/v1/redact", json={"text": "张三 13812345678", "names": ["张三"]}).json()
    assert r["text"] == "[受访者] [手机号]"


def test_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret")
    assert client.get("/health").status_code == 200
    assert client.post("/v1/redact", json={"text": "x"}).status_code == 401
    ok = client.post("/v1/redact", json={"text": "x"}, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
