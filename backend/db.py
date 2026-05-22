# backend/db.py
import aiosqlite
import json
import os
import time

DB_PATH = os.getenv("DB_PATH", "voice_agent.db")

CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    asr_provider TEXT,
    tts_provider TEXT,
    recording_path TEXT,
    post_call_eval_json TEXT,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS memory_blobs (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    blob_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SCHEMA)
        await db.commit()

async def get_or_create_user(user_id: str, name: str, email: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)",
            (user_id, name, email),
        )
        await db.commit()
        async with db.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return {"id": row[0], "name": row[1], "email": row[2]}

async def create_session(session_id: str, user_id: str, asr: str, tts: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        # Auto-create a default user if needed
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)",
            (user_id, "Demo User", "demo@example.com"),
        )
        await db.execute(
            "INSERT OR REPLACE INTO sessions (id, user_id, started_at, asr_provider, tts_provider) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, int(time.time() * 1000), asr, tts),
        )
        await db.commit()

async def append_turn(session_id: str, role: str, text: str, ts_ms: int, metrics: dict | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO turns (session_id, role, text, ts_ms, metrics_json) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, ts_ms, json.dumps(metrics) if metrics else None),
        )
        await db.commit()

async def close_session(session_id: str, recording_path: str | None, metrics_json: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ?, recording_path = ?, metrics_json = ? WHERE id = ?",
            (int(time.time() * 1000), recording_path, metrics_json, session_id),
        )
        await db.commit()
