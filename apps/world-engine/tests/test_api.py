"""HTTP + WebSocket contract of the engine."""

from fastapi.testclient import TestClient

from app import main


def make_client() -> TestClient:
    return TestClient(main.app)


def test_healthz_is_ok():
    with make_client() as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["agents"] == 6
        assert "tick" in body


def test_state_endpoint_returns_snapshot():
    with make_client() as client:
        r = client.get("/api/world/state")
        assert r.status_code == 200
        body = r.json()
        assert len(body["agents"]) == 6
        assert {a["id"] for a in body["agents"]} == {
            "hakim",
            "layla",
            "maryam",
            "omar",
            "salim",
            "yassin",
        }
        assert body["meeting"] is None


def test_contract_endpoint_matches_frozen_vocabulary():
    with make_client() as client:
        r = client.get("/api/world/contract")
        body = r.json()
        assert body["states"] == ["idle", "working", "review", "break", "meeting"]
        assert "meeting_open" in body["events"]
        assert any(t["from"] == "working" and t["event"] == "task_done" for t in body["transitions"])


def test_ingest_event_moves_agent():
    with make_client() as client:
        r = client.post("/api/world/events", json={"agent": "layla", "type": "task_start", "ticket": "WORLD-3"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["event"]["to"] == "working"
        state = client.get("/api/world/state").json()
        layla = next(a for a in state["agents"] if a["id"] == "layla")
        assert layla["state"] == "working"
        assert layla["ticket"] == "WORLD-3"


def test_ingest_invalid_transition_is_refused():
    with make_client() as client:
        r = client.post("/api/world/events", json={"agent": "salim", "type": "task_done"})
        assert r.status_code == 409
        body = r.json()
        assert body["ok"] is False
        assert body["rejection"]["reason"] == "invalid_transition"


def test_ingest_unknown_agent_is_refused():
    with make_client() as client:
        r = client.post("/api/world/events", json={"agent": "ghost", "type": "task_start"})
        assert r.status_code == 409
        assert r.json()["rejection"]["reason"] == "unknown_agent"


def test_ingest_requires_fields():
    with make_client() as client:
        r = client.post("/api/world/events", json={"agent": "omar"})
        assert r.status_code == 422


def test_websocket_snapshot_then_delta():
    with make_client() as client:
        with client.websocket_connect("/ws/world") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert len(snapshot["world"]["agents"]) == 6

            ticked = client.post("/api/world/tick")
            assert ticked.status_code == 200

            delta = ws.receive_json()
            assert delta["type"] == "delta"
            assert delta["tick"] > snapshot["world"]["tick"]
            assert "counts" in delta
