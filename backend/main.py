# backend/main.py
import asyncio
import json
import time
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.config import cfg
from backend.db import init_db, create_session, append_turn, close_session
from backend.events import AsrPartial, AsrFinal, TtsAudioChunk, TtsDone
from backend.vad import VAD
from backend.llm_openai import stream_response
from backend.phrase_cache import phrase_cache
from backend.filler import get_filler_chunk
from backend.barge_in import BargeInHandler
from backend.speculation import SpeculationManager
from backend.metrics import SessionMetrics
from backend.recording import Recorder
from backend.providers.asr.factory import create_asr
from backend.providers.tts.factory import create_tts

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


@app.on_event("startup")
async def startup():
    await init_db()
    phrase_cache.load()


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

    try:
        await create_session(session_id, user_id, asr_provider, tts_provider)
    except Exception:
        pass

    session_metrics = SessionMetrics()
    recorder = Recorder(session_id=session_id)
    vad = VAD(sample_rate=cfg.sample_rate)
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

    async def send_cancel():
        vad.agent_playing = False
        await send_json({"type": "playback.cancel"})

    barge_in_handler = BargeInHandler(on_cancel=send_cancel, vad=vad)

    # --- TTS event dispatcher ---
    async def handle_tts_event(event):
        if isinstance(event, TtsAudioChunk):
            recorder.append_agent(event.pcm_bytes, session_metrics.now_ms())
            if not turn_state.get("tts_first_audio_ms"):
                turn_state["tts_first_audio_ms"] = session_metrics.now_ms()
            vad.agent_playing = True
            await send_audio(event.pcm_bytes, {
                "type": "tts.audio_chunk",
                "source": event.source,
                "provider": event.provider,
                "sample_rate": event.sample_rate,
            })
        elif isinstance(event, TtsDone):
            vad.agent_playing = False
            await send_json({"type": "tts.done", "provider": event.provider})

    def on_tts_event_sync(event):
        asyncio.create_task(handle_tts_event(event))

    tts = create_tts(on_event=on_tts_event_sync, provider=tts_provider)

    # --- Sentence flush → TTS ---
    async def flush_sentence(sentence: str):
        sentence = sentence.strip()
        if not sentence:
            return
        turn_state["tts_start_ms"] = session_metrics.now_ms()
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

    # --- LLM + TTS pipeline ---
    async def run_llm_tts(user_text: str):
        import re
        turn_state["llm_start_ms"] = session_metrics.now_ms()
        sentence_buf = ""

        def on_token(token: str):
            nonlocal sentence_buf
            sentence_buf += token
            if not turn_state.get("llm_first_token_ms"):
                turn_state["llm_first_token_ms"] = session_metrics.now_ms()

        full_text, usage = await stream_response(
            messages=conversation_history,
            system_prompt=SYSTEM_PROMPT,
            on_token=on_token,
        )

        # Flush complete text as sentences
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+', full_text.strip())
        for sentence in sentences:
            if sentence.strip():
                await flush_sentence(sentence)

        conversation_history.append({"role": "assistant", "content": full_text})

        # Emit turn metrics
        turn_metrics = {
            **turn_state,
            **(usage or {}),
            "asr_provider": asr_provider,
            "tts_provider": tts_provider,
        }
        await append_turn(session_id, "assistant", full_text, session_metrics.now_ms())
        await send_json({"type": "metrics.turn", "turn": turn_metrics})

    # --- Speculation ---
    async def spec_llm_fn(text: str, on_token=None):
        return await stream_response(
            messages=conversation_history + [{"role": "user", "content": text}],
            system_prompt=SYSTEM_PROMPT,
            on_token=on_token or (lambda t: None),
        )

    spec_manager = SpeculationManager(
        llm_fn=spec_llm_fn,
        on_token=lambda t: None,
    ) if spec_enabled else None

    # --- ASR event handler ---
    async def handle_asr_event(event):
        nonlocal in_speech
        if isinstance(event, AsrPartial):
            await send_json({"type": "asr.partial", "text": event.text, "provider": event.provider})
            if spec_manager:
                await spec_manager.on_partial(event.text)

        elif isinstance(event, AsrFinal):
            if not event.text.strip():
                return
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

    def on_asr_event_sync(event):
        asyncio.create_task(handle_asr_event(event))

    asr = create_asr(on_event=on_asr_event_sync, provider=asr_provider)
    try:
        await asr.connect()
    except Exception as e:
        await send_json({"type": "error", "message": f"ASR connect failed: {e}"})
        await ws.close()
        return

    # --- Main receive loop ---
    try:
        async for message in ws.iter_bytes():
            recorder.append_user(message, session_metrics.now_ms())

            # Ensure turn_state is initialized
            if not turn_state.get("vad_start_ms"):
                turn_state["vad_start_ms"] = session_metrics.now_ms()

            is_speech = vad.process_and_check(message)

            if is_speech:
                if not in_speech:
                    in_speech = True
                    turn_state["vad_start_ms"] = session_metrics.now_ms()

                # Barge-in detection
                if vad.agent_playing:
                    await barge_in_handler.fire(ts_ms=session_metrics.now_ms())
                    await send_json({"type": "barge_in", "ts_ms": session_metrics.now_ms()})

                await asr.send_audio(message)

            else:
                if in_speech:
                    in_speech = False
                    await asr.flush()
                    # Reset turn state for next turn
                    turn_state.clear()

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
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
