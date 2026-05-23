import pytest
from httpx import ASGITransport, AsyncClient

from backend import db
from backend.main import app


@pytest.mark.asyncio
async def test_memory_endpoint_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()

    payload = {
        "items": [
            {
                "id": "pref-sms",
                "category": "user_preference",
                "text": "Prefers SMS links for renewal.",
                "confidence": 0.91,
                "sensitivity": "normal",
                "source": "reviewed",
                "tags": ["sms", "renewal"],
            },
            {
                "id": "private-note",
                "category": "private_note",
                "text": "Internal-only escalation risk.",
                "confidence": 0.99,
                "sensitivity": "private",
                "source": "reviewed",
                "tags": ["renewal"],
            },
        ]
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_response = await client.put("/users/u1/memory", json=payload)
        get_response = await client.get("/users/u1/memory")

    assert put_response.status_code == 200
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["user_id"] == "u1"
    assert [item["id"] for item in data["items"]] == ["pref-sms", "private-note"]


@pytest.mark.asyncio
async def test_memory_endpoint_returns_empty_items_for_unknown_user(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice_agent.db"))
    await db.init_db()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/users/unknown/memory")

    assert response.status_code == 200
    assert response.json() == {"user_id": "unknown", "items": []}
