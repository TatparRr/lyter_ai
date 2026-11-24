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


def test_demo_key_fallback():
    r = client.post(
        "/chat",
        headers={"X-Simulate-Failure":"key_1=429"},
        json={"session_id":"fallback-cache-bypass","message":"Tell me a unique fallback answer"},
    )
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    assert r.json()["metadata"]["fallback_attempts"] == 1


def test_multiturn_history():
    client.post("/chat", json={"session_id":"turns","message":"My favorite color is blue"})
    r = client.post("/chat", json={"session_id":"turns","message":"What is my favorite color?"})
    history = client.get("/sessions/turns/messages")
    assert r.status_code == 200
    assert history.status_code == 200
    assert len(history.json()["messages"]) == 4


def test_failed_request_not_added_to_history():
    r = client.post(
        "/chat",
        headers={"X-Simulate-Failure":"*=503"},
        json={"session_id":"no-history","message":"This should fail"},
    )
    assert r.status_code == 503
    assert client.get("/sessions/no-history/messages").status_code == 404


def test_key_two_simulated_after_key_one():
    r = client.post(
        "/chat",
        headers={"X-Simulate-Failure":"key_1=429,key_2=500"},
        json={"session_id":"all-simulated","message":"A question that should fail both demo keys"},
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "all providers unavailable"


def test_cache_latency_is_small():
    response_cache.clear()
    live = client.post("/chat", json={"session_id":"latency-live","message":"Latency benchmark question"})
    cached = client.post("/chat", json={"session_id":"latency-cache","message":"Latency benchmark question"})
    assert live.status_code == cached.status_code == 200
    assert live.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert cached.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert cached.json()["metadata"]["latency_ms"] <= live.json()["metadata"]["latency_ms"]
