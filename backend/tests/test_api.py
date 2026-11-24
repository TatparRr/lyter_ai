import os
import tempfile

fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{path}"
os.environ["LLM_API_KEYS"] = ""
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"

from fastapi.testclient import TestClient
from app.main import app, init_db, response_cache

client = TestClient(app)
init_db()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_cache_and_history():
    response_cache.clear()
    first = client.post("/chat", json={"session_id":"test-cache","message":"What is the capital of France?"})
    assert first.status_code == 200
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

    second = client.post("/chat", json={"session_id":"other-session","message":"What is the capital of France?"})
    assert second.status_code == 200
    assert second.json()["reply"] == first.json()["reply"]
    assert second.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second.json()["metadata"]["key_id"] is None


def test_validation():
    r = client.post("/chat", json={"session_id":"","message":""})
    assert r.status_code == 422


def test_simulate_all_failure():
    r = client.post("/chat", headers={"X-Simulate-Failure":"*=429"}, json={"session_id":"failure-session","message":"hello"})
    assert r.status_code == 503
    assert r.json()["detail"] == "all providers unavailable"
    assert client.get("/sessions/failure-session/messages").status_code == 404


def test_unknown_session():
    r = client.get("/sessions/no-such-session/messages")
    assert r.status_code == 404
