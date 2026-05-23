# backend/main.py
import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import aiosqlite
from backend.db import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
logger = logging.getLogger("voice_session")

from backend.config import cfg
from backend.db import init_db, create_session, append_turn, close_session
from backend.events import AsrPartial, AsrFinal, TtsAudioChunk, TtsDone
from backend.vad import VAD
from backend.llm_gemini import stream_response
from backend.phrase_cache import phrase_cache
from backend.filler import get_filler_chunk
from backend.barge_in import BargeInHandler
from backend.speculation import SpeculationManager
from backend.metrics import SessionMetrics
from backend.recording import Recorder
from backend.providers.asr.factory import create_asr, registered_asr_providers
from backend.providers.tts.factory import create_tts, registered_tts_providers

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = (
    "You are a helpful voice assistant. Be concise — voice responses should be 1-3 sentences. "
    "Never use markdown, bullet points, or lists. Speak naturally."
)

SENTENCE_RE = re.compile(r"(.+?[.!?])(?:\s+|$)", re.DOTALL)


class SentenceAccumulator:
    """Incrementally extracts complete sentences from streamed LLM tokens."""

    def __init__(self) -> None:
        self._buffer = ""

    def push(self, token: str) -> list[str]:
        self._buffer += token
        sentences: list[str] = []
        consumed = 0
        for match in SENTENCE_RE.finditer(self._buffer):
            sentence = match.group(1).strip()
            if sentence:
                sentences.append(sentence)
            consumed = match.end()
        if consumed:
            self._buffer = self._buffer[consumed:]
        return sentences

    def drain(self) -> list[str]:
        text = self._buffer.strip()
        self._buffer = ""
        return [text] if text else []


@app.on_event("startup")
async def startup():
    await init_db()
    phrase_cache.load()


@app.get("/sessions")
async def get_sessions(limit: int = 20):
    """Return recent sessions with per-turn latency breakdown."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, asr_provider, tts_provider,
                   started_at, ended_at,
                   ROUND((ended_at - started_at) / 1000.0, 1) AS duration_s
            FROM sessions ORDER BY started_at DESC LIMIT ?
            """,
            (limit,),
        ) as cur:
            sessions = [dict(r) for r in await cur.fetchall()]

        for s in sessions:
            async with db.execute(
                """
                SELECT role, text, ts_ms, metrics_json
                FROM turns WHERE session_id = ? ORDER BY ts_ms
                """,
                (s["id"],),
            ) as cur:
                turns = []
                for row in await cur.fetchall():
                    t = dict(row)
                    m = json.loads(t.pop("metrics_json") or "{}")
                    # Compute derived latencies inline
                    if m.get("vad_start_ms") and m.get("asr_final_ms"):
                        m["asr_ms"] = m["asr_final_ms"] - m["vad_start_ms"]
                    if m.get("llm_start_ms") and m.get("llm_first_token_ms"):
                        m["llm_ttft_ms"] = m["llm_first_token_ms"] - m["llm_start_ms"]
                    if m.get("tts_start_ms") and m.get("tts_first_audio_ms"):
                        m["tts_ttfb_ms"] = m["tts_first_audio_ms"] - m["tts_start_ms"]
                    if m.get("vad_start_ms") and m.get("tts_first_audio_ms"):
                        m["total_ms"] = m["tts_first_audio_ms"] - m["vad_start_ms"]
                    t["metrics"] = m
                    turns.append(t)
                s["turns"] = turns
                # Session-level latency summary (assistant turns only)
                latencies = [
                    t["metrics"].get("total_ms")
                    for t in turns
                    if t["role"] == "assistant" and t["metrics"].get("total_ms")
                ]
                if latencies:
                    s["avg_total_ms"] = round(sum(latencies) / len(latencies))
                    s["min_total_ms"] = min(latencies)
                    s["max_total_ms"] = max(latencies)
    return {"sessions": sessions}


@app.websocket("/ws/{session_id}")
async def voice_session(ws: WebSocket, session_id: str):
    await ws.accept()

    # First message: session config
    try:
        config = await ws.receive_json()
    except Exception:
        await ws.close()
        return

    user_id = config.get("user_id", "demo-user")
    asr_provider = config.get("asr_provider", cfg.asr_provider)
    tts_provider = config.get("tts_provider", cfg.tts_provider)
    smart_routing = config.get("smart_routing", True)
    spec_enabled = config.get("spec_enabled", True)
    system_prompt = config.get("system_prompt", SYSTEM_PROMPT) or SYSTEM_PROMPT

    async def send_startup_error(message: str):
        await ws.send_text(json.dumps({"type": "error", "message": message}))
        await ws.close(code=1008)

    if asr_provider not in registered_asr_providers():
        await send_startup_error(
            f"Unknown ASR provider '{asr_provider}'. Available: {', '.join(sorted(registered_asr_providers()))}"
        )
        return
    if tts_provider not in registered_tts_providers():
        await send_startup_error(
            f"Unknown TTS provider '{tts_provider}'. Available: {', '.join(sorted(registered_tts_providers()))}"
        )
        return

    missing_keys = cfg.missing_provider_keys(asr_provider, tts_provider)
    if asr_provider != "mock" or tts_provider != "mock":
        missing_keys.extend(cfg.missing_live_llm_keys())
    if missing_keys:
        await send_startup_error("; ".join(dict.fromkeys(missing_keys)))
        return

    try:
        await create_session(session_id, user_id, asr_provider, tts_provider)
    except Exception:
        pass

    try:
        session_metrics = SessionMetrics()
        recorder = Recorder(session_id=session_id)
        logger.info("Initializing VAD...")
        turn_vad = VAD(sample_rate=cfg.sample_rate)
        barge_in_vad = VAD(sample_rate=cfg.sample_rate, threshold=cfg.barge_in_threshold)
        logger.info("VAD ready")
    except Exception as e:
        logger.error("Session setup failed: %s", e, exc_info=True)
        await ws.close()
        return
    conversation_history: list[dict] = []

    # VAD state machine: avoid flushing on every silence frame
    in_speech = False

    # Per-turn tracking dict (mutable so inner functions can update it)
    turn_state: dict = {}

    async def send_json(msg: dict):
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            pass

    async def send_audio(pcm_bytes: bytes, meta: dict):
        """Send audio as binary frame: JSON_HEADER|PCM_BYTES"""
        try:
            header = json.dumps(meta).encode()
            await ws.send_bytes(header + b"|" + pcm_bytes)
        except Exception:
            pass

    playback_epoch = 0

    async def send_cancel():
        nonlocal playback_epoch
        playback_epoch += 1
        turn_vad.agent_playing = False
        await send_json({"type": "playback.cancel"})

    barge_in_handler = BargeInHandler(on_cancel=send_cancel, vad=turn_vad)

    # --- TTS event dispatcher ---
    async def handle_tts_event(event, epoch: int | None = None):
        if epoch is None:
            epoch = playback_epoch
        if epoch != playback_epoch:
            return
        if isinstance(event, TtsAudioChunk):
            recorder.append_agent(event.pcm_bytes, session_metrics.now_ms())
            if not turn_state.get("tts_first_audio_ms"):
                turn_state["tts_first_audio_ms"] = session_metrics.now_ms()
            turn_vad.agent_playing = True
            await send_audio(event.pcm_bytes, {
                "type": "tts.audio_chunk",
                "source": event.source,
                "provider": event.provider,
                "sample_rate": event.sample_rate,
            })
        elif isinstance(event, TtsDone):
            logger.info("TTS done — agent_playing reset to False, cooldown started")
            turn_vad.agent_playing = False
            turn_vad.set_cooldown(500)
            await send_json({"type": "tts.done", "provider": event.provider})

    def on_tts_event_sync(event):
        epoch = playback_epoch
        asyncio.create_task(handle_tts_event(event, epoch=epoch))

    tts = create_tts(on_event=on_tts_event_sync, provider=tts_provider)

    # --- Sentence flush → TTS ---
    async def flush_sentence(sentence: str):
        sentence = sentence.strip()
        if not sentence:
            return
        sentence_index = turn_state.get("tts_sentence_count", 0) + 1
        turn_state["tts_sentence_count"] = sentence_index
        now = session_metrics.now_ms()
        if not turn_state.get("tts_start_ms"):
            turn_state["tts_start_ms"] = now
        if not turn_state.get("first_sentence_tts_start_ms"):
            turn_state["first_sentence_tts_start_ms"] = now
        turn_state["last_sentence_tts_start_ms"] = now
        await send_json({
            "type": "tts.sentence_start",
            "sentence_index": sentence_index,
            "text": sentence,
        })
        cached_pcm = phrase_cache.lookup(sentence)
        if cached_pcm:
            turn_state["phrase_cache_hit"] = True
            await handle_tts_event(TtsAudioChunk(
                pcm_bytes=cached_pcm,
                sample_rate=cfg.sample_rate,
                provider="phrase_cache",
                source="phrase_cache",
            ))
            await handle_tts_event(TtsDone(provider="phrase_cache"))
        else:
            await tts.synthesize(sentence)

    class SentenceTTSStreamer:
        def __init__(self):
            self._queue: asyncio.Queue[str | None] = asyncio.Queue()
            self._task = asyncio.create_task(self._run())
            barge_in_handler.register_tts_task(self._task)

        def enqueue(self, sentence: str) -> None:
            sentence = sentence.strip()
            if sentence:
                self._queue.put_nowait(sentence)

        async def finish(self) -> None:
            self._queue.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        async def cancel(self) -> None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        async def _run(self) -> None:
            try:
                while True:
                    sentence = await self._queue.get()
                    if sentence is None:
                        return
                    await flush_sentence(sentence)
            finally:
                barge_in_handler.clear_tts_task(self._task)

    # --- LLM + TTS pipeline ---
    async def run_llm_tts(user_text: str):
        turn_state["llm_start_ms"] = session_metrics.now_ms()
        turn_state["llm_streaming"] = True
        turn_state["tts_streaming"] = True
        turn_state["tts_sentence_count"] = 0
        sentence_acc = SentenceAccumulator()
        sentence_tts = SentenceTTSStreamer()

        def on_token(token: str):
            if not turn_state.get("llm_first_token_ms"):
                turn_state["llm_first_token_ms"] = session_metrics.now_ms()
            for sentence in sentence_acc.push(token):
                sentence_tts.enqueue(sentence)

        try:
            full_text, usage = await stream_response(
                messages=conversation_history,
                system_prompt=system_prompt,
                on_token=on_token,
            )
            for sentence in sentence_acc.drain():
                sentence_tts.enqueue(sentence)
            conversation_history.append({"role": "assistant", "content": full_text})
            await send_json({"type": "llm.response", "text": full_text})
            await sentence_tts.finish()
        except asyncio.CancelledError:
            await sentence_tts.cancel()
            raise
        except Exception as e:
            await sentence_tts.cancel()
            logger.error("LLM error: %s", e)
            await send_json({"type": "error", "message": f"LLM error: {e}"})
            return

        # Emit turn metrics
        turn_metrics = {
            **turn_state,
            **(usage or {}),
            "asr_provider": asr_provider,
            "tts_provider": tts_provider,
            "vad_mode": "local_manual",
            "asr_streaming": True,
            "tts_streaming": True,
            "asr_transport": asr_provider,
            "tts_transport": tts_provider,
        }
        await append_turn(session_id, "assistant", full_text, session_metrics.now_ms(), turn_metrics)
        await send_json({"type": "metrics.turn", "turn": turn_metrics})

    # --- Speculation ---
    async def spec_llm_fn(text: str, on_token=None):
        return await stream_response(
            messages=conversation_history + [{"role": "user", "content": text}],
            system_prompt=system_prompt,
            on_token=on_token or (lambda t: None),
        )

    async def on_commit_async(committed_text: str, usage: dict):
        if not committed_text.strip():
            return
        turn_state["tts_streaming"] = True
        sentence_tts = SentenceTTSStreamer()
        sentence_acc = SentenceAccumulator()
        for sentence in sentence_acc.push(committed_text) + sentence_acc.drain():
            sentence_tts.enqueue(sentence)
        conversation_history.append({"role": "assistant", "content": committed_text})
        await send_json({"type": "llm.response", "text": committed_text})
        await sentence_tts.finish()
        turn_metrics = {
            **turn_state,
            **(usage or {}),
            "asr_provider": asr_provider,
            "tts_provider": tts_provider,
            "vad_mode": "local_manual",
            "asr_streaming": True,
            "tts_streaming": True,
            "asr_transport": asr_provider,
            "tts_transport": tts_provider,
        }
        await append_turn(session_id, "assistant", committed_text, session_metrics.now_ms(), turn_metrics)
        await send_json({"type": "metrics.turn", "turn": turn_metrics})

    def on_commit_sync(committed_text: str, usage: dict):
        asyncio.create_task(on_commit_async(committed_text, usage))

    spec_manager = SpeculationManager(
        llm_fn=spec_llm_fn,
        on_token=lambda t: None,
        on_commit=on_commit_sync,
    ) if spec_enabled else None

    # --- ASR event handler ---
    async def handle_asr_event(event):
        nonlocal in_speech
        if isinstance(event, AsrPartial):
            logger.info("ASR partial: %r", event.text)
            turn_state["asr_partial_count"] = turn_state.get("asr_partial_count", 0) + 1
            if not turn_state.get("asr_first_partial_ms"):
                turn_state["asr_first_partial_ms"] = session_metrics.now_ms()
            await send_json({"type": "asr.partial", "text": event.text, "provider": event.provider})
            if spec_manager:
                await spec_manager.on_partial(event.text)

        elif isinstance(event, AsrFinal):
            if not event.text.strip():
                return
            logger.info("ASR final: %r", event.text)
            turn_state["asr_final_ms"] = session_metrics.now_ms()
            conversation_history.append({"role": "user", "content": event.text})
            await append_turn(session_id, "user", event.text, session_metrics.now_ms())
            await send_json({"type": "asr.final", "text": event.text, "provider": event.provider})

            # Filler audio
            filler = get_filler_chunk()
            if filler:
                turn_state["filler_played"] = True
                await handle_tts_event(filler)

            if spec_manager:
                spec_result = await spec_manager.on_final(event.text)
                if spec_result == "commit":
                    turn_state["spec_hit"] = True
                    return

            llm_task = asyncio.create_task(run_llm_tts(event.text))
            barge_in_handler.register_llm_task(llm_task)
            llm_task.add_done_callback(barge_in_handler.clear_llm_task)

    def on_asr_event_sync(event):
        asyncio.create_task(handle_asr_event(event))

    asr = create_asr(on_event=on_asr_event_sync, provider=asr_provider)
    logger.info("Connecting to ASR provider: %s", asr_provider)
    try:
        await asyncio.wait_for(asr.connect(), timeout=15.0)
        logger.info("ASR connected successfully")
    except asyncio.TimeoutError:
        logger.error("ASR connect timed out after 15s")
        await send_json({"type": "error", "message": "ASR connect timed out"})
        await ws.close()
        return
    except Exception as e:
        logger.error("ASR connect failed: %s", e, exc_info=True)
        await send_json({"type": "error", "message": f"ASR connect failed: {e}"})
        await ws.close()
        return

    # --- Main receive loop ---
    # 10 frames × 32ms = 320ms of consecutive silence before flushing ASR.
    # Without this, any brief mid-sentence pause cuts the utterance in half.
    SILENCE_FLUSH_FRAMES = cfg.vad_silence_flush_frames
    _frame_count = 0
    _silence_frames = 0
    try:
        async for message in ws.iter_bytes():
            _frame_count += 1
            if _frame_count == 1:
                logger.info("First audio frame received (%d bytes)", len(message))
            elif _frame_count % 100 == 0:
                logger.info("Audio frames received: %d", _frame_count)
            recorder.append_user(message, session_metrics.now_ms())

            barge_detected = False
            if turn_vad.agent_playing and barge_in_vad.process_barge_in(message):
                barge_detected = True
                ts_ms = session_metrics.now_ms()
                await barge_in_handler.fire(ts_ms=ts_ms)
                await send_json({"type": "barge_in", "ts_ms": ts_ms})

            is_speech = barge_detected or (
                not turn_vad.agent_playing and turn_vad.process_and_check(message)
            )
            if _frame_count <= 5:
                logger.info(
                    "Frame %d: len=%d, vad_prob=%s, barge_prob=%s, agent_playing=%s",
                    _frame_count,
                    len(message),
                    turn_vad._last_prob,
                    barge_in_vad._last_prob,
                    turn_vad.agent_playing,
                )

            if is_speech:
                _silence_frames = 0
                if not in_speech:
                    in_speech = True
                    previous_barge_ms = turn_state.get("barge_in_ms") if barge_detected else None
                    turn_state.clear()
                    turn_state["vad_start_ms"] = session_metrics.now_ms()
                    turn_state["vad_mode"] = "local_manual"
                    turn_state["asr_streaming"] = True
                    turn_state["asr_transport"] = asr_provider
                    if barge_detected:
                        turn_state["barge_in_ms"] = previous_barge_ms or turn_state["vad_start_ms"]
                        turn_state["playback_cancelled"] = True
                    logger.info("VAD: speech started, sending ActivityStart")
                    await asr.activity_start()

                await asr.send_audio(message)

            else:
                if in_speech:
                    _silence_frames += 1
                    # Keep streaming audio during brief pauses so ASR gets full context
                    await asr.send_audio(message)

                    if _silence_frames >= SILENCE_FLUSH_FRAMES:
                        in_speech = False
                        _silence_frames = 0
                        logger.info("VAD: 320ms silence — flushing ASR")
                        await asr.flush()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WS receive loop error: %s", e, exc_info=True)
    finally:
        await asr.close()
        await tts.close()
        recording_path = recorder.stitch()
        try:
            await close_session(
                session_id,
                recording_path=recording_path,
                metrics_json=json.dumps({"turns": len(session_metrics.turns)}),
            )
        except Exception:
            pass
