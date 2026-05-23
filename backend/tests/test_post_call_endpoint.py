import pytest
from httpx import ASGITransport, AsyncClient

from backend import db
from backend.main import app


@pytest.mark.asyncio
async def test_post_call_report_endpoint_generates_and_stores(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()
    await db.create_session("s1", "u1", "mock", "mock")
    await db.append_turn("s1", "user", "The renewal link is still not working.", 1)
    await db.append_turn(
        "s1",
        "assistant",
        "I will send a new link.",
        2,
        {"lane": "FAST_LLM", "route": "policy_lookup"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/sessions/s1/post-call-report")
        second = await client.get("/sessions/s1/post-call-report")

    assert first.status_code == 200
    assert first.json()["source"] == "generated"
    assert first.json()["report"]["outcome"] == "unresolved"
    assert second.status_code == 200
    assert second.json()["source"] == "stored"


@pytest.mark.asyncio
async def test_post_call_report_endpoint_404s_without_turns(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/sessions/missing/post-call-report")

    assert response.status_code == 404
