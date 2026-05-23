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
CREATE TABLE IF NOT EXISTS memory_suggestion_decisions (
    suggestion_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    decision TEXT NOT NULL,
    suggestion_json TEXT NOT NULL,
    decided_at INTEGER NOT NULL
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

async def get_session(session_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, asr_provider, tts_provider,
                   started_at, ended_at, recording_path,
                   post_call_eval_json, metrics_json
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def append_turn(session_id: str, role: str, text: str, ts_ms: int, metrics: dict | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO turns (session_id, role, text, ts_ms, metrics_json) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, ts_ms, json.dumps(metrics) if metrics else None),
        )
        await db.commit()

async def get_session_turns(session_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT role, text, ts_ms, metrics_json
            FROM turns WHERE session_id = ? ORDER BY ts_ms
            """,
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    turns: list[dict] = []
    for row in rows:
        turn = dict(row)
        metrics_json = turn.pop("metrics_json", None)
        try:
            turn["metrics"] = json.loads(metrics_json) if metrics_json else {}
        except json.JSONDecodeError:
            turn["metrics"] = {}
        turns.append(turn)
    return turns

async def get_post_call_eval(session_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT post_call_eval_json FROM sessions WHERE id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None

async def save_post_call_eval(session_id: str, report: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET post_call_eval_json = ? WHERE id = ?",
            (json.dumps(report), session_id),
        )
        await db.commit()

async def get_user_memory_blob(user_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT blob_json FROM memory_blobs WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None

async def save_user_memory_blob(user_id: str, blob: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, email) VALUES (?, ?, ?)",
            (user_id, "Demo User", "demo@example.com"),
        )
        await db.execute(
            """
            INSERT INTO memory_blobs (user_id, blob_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                blob_json = excluded.blob_json,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(blob), int(time.time() * 1000)),
        )
        await db.commit()

async def get_memory_suggestion_decisions(session_id: str) -> dict[str, dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT suggestion_id, session_id, user_id, decision, suggestion_json, decided_at
            FROM memory_suggestion_decisions WHERE session_id = ?
            """,
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    decisions: dict[str, dict] = {}
    for row in rows:
        record = dict(row)
        try:
            record["suggestion"] = json.loads(record.pop("suggestion_json"))
        except json.JSONDecodeError:
            record["suggestion"] = {}
        decisions[record["suggestion_id"]] = record
    return decisions

async def save_memory_suggestion_decision(
    suggestion_id: str,
    session_id: str,
    user_id: str,
    decision: str,
    suggestion: dict,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO memory_suggestion_decisions
                (suggestion_id, session_id, user_id, decision, suggestion_json, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(suggestion_id) DO UPDATE SET
                decision = excluded.decision,
                suggestion_json = excluded.suggestion_json,
                decided_at = excluded.decided_at
            """,
            (
                suggestion_id,
                session_id,
                user_id,
                decision,
                json.dumps(suggestion),
                int(time.time() * 1000),
            ),
        )
        await db.commit()

async def close_session(session_id: str, recording_path: str | None, metrics_json: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ?, recording_path = ?, metrics_json = ? WHERE id = ?",
            (int(time.time() * 1000), recording_path, metrics_json, session_id),
        )
        await db.commit()
