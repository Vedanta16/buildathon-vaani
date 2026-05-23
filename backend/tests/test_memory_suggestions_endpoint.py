import pytest
from httpx import ASGITransport, AsyncClient

from backend import db
from backend.main import app


async def seed_session_with_memory_candidate():
    await db.create_session("s1", "u1", "mock", "mock")
    await db.append_turn("s1", "user", "The renewal link was broken.", 1)
    await db.append_turn("s1", "assistant", "I sent a new link.", 2)
    await db.append_turn("s1", "user", "Great, please send links by SMS next time.", 3)


@pytest.mark.asyncio
async def test_memory_suggestions_endpoint_lists_pending_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()
    await seed_session_with_memory_candidate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/sessions/s1/memory-suggestions")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s1"
    assert body["user_id"] == "u1"
    assert len(body["suggestions"]) == 1
    suggestion = body["suggestions"][0]
    assert suggestion["status"] == "pending"
    assert suggestion["category"] == "user_preference"
    assert suggestion["requires_review"] is True


@pytest.mark.asyncio
async def test_accept_memory_suggestion_promotes_reviewed_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()
    await seed_session_with_memory_candidate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        suggestions_response = await client.get("/sessions/s1/memory-suggestions")
        suggestion_id = suggestions_response.json()["suggestions"][0]["id"]
        decision_response = await client.post(
            f"/memory-suggestions/{suggestion_id}/decision",
            json={"session_id": "s1", "decision": "accepted"},
        )
        suggestions_after = await client.get("/sessions/s1/memory-suggestions")
        memory_response = await client.get("/users/u1/memory")

    assert decision_response.status_code == 200
    assert decision_response.json()["status"] == "accepted"
    assert suggestions_after.json()["suggestions"][0]["status"] == "accepted"
    memory_items = memory_response.json()["items"]
    assert len(memory_items) == 1
    assert memory_items[0]["id"] == suggestion_id
    assert memory_items[0]["source"] == "reviewed"


@pytest.mark.asyncio
async def test_reject_memory_suggestion_does_not_promote_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()
    await seed_session_with_memory_candidate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        suggestions_response = await client.get("/sessions/s1/memory-suggestions")
        suggestion_id = suggestions_response.json()["suggestions"][0]["id"]
        decision_response = await client.post(
            f"/memory-suggestions/{suggestion_id}/decision",
            json={"session_id": "s1", "decision": "rejected"},
        )
        memory_response = await client.get("/users/u1/memory")

    assert decision_response.status_code == 200
    assert decision_response.json()["status"] == "rejected"
    assert memory_response.json()["items"] == []


@pytest.mark.asyncio
async def test_memory_suggestion_decision_validates_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()
    await seed_session_with_memory_candidate()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/memory-suggestions/does-not-matter/decision",
            json={"session_id": "s1", "decision": "maybe"},
        )

    assert response.status_code == 400
