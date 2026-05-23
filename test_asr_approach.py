"""
test_asr_approach.py

Tests whether switching Gemini Live to response_modalities=["TEXT"] allows:
1. input_audio_transcription to still work
2. turn_complete to fire quickly (no 94-audio-chunk delay)
3. Session to stay alive across multiple turns (no reconnect needed)

Usage:
    .venv/bin/python test_asr_approach.py
"""

import asyncio
import time
import struct
import math
import sys
import os

# ---------------------------------------------------------------------------
# Ensure project root is on path so we can import backend.config
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from google import genai
from google.genai import types

# Pull API key from config
try:
    from backend.config import cfg
    GEMINI_API_KEY = cfg.gemini_api_key
    print("[setup] Loaded Gemini API key from backend.config")
except Exception as e:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    print(f"[setup] backend.config import failed ({e}), using env GEMINI_API_KEY")

# ---------------------------------------------------------------------------
# Audio helpers — generate fake 16kHz PCM (sine wave)
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 512  # ~32ms per chunk


def generate_sine_pcm(duration_sec: float, freq_hz: float = 440.0) -> bytes:
    """Generate raw 16-bit little-endian PCM at 16kHz."""
    n_samples = int(SAMPLE_RATE * duration_sec)
    samples = []
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        val = int(32767 * math.sin(2 * math.pi * freq_hz * t))
        samples.append(val)
    return struct.pack(f"<{n_samples}h", *samples)


# ---------------------------------------------------------------------------
# Models + configs to try
# ---------------------------------------------------------------------------
MODELS_TO_TRY = [
    # Newer live model - likely supports TEXT modality
    "gemini-3.1-flash-live-preview",
    # Current native audio model (used in production) - try with no audio output
    "gemini-2.5-flash-native-audio-latest",
]

MODALITIES_TO_TRY = [
    ["TEXT"],
    [],
    ["AUDIO"],  # fallback - the current production config
]


def build_config(modalities):
    return types.LiveConnectConfig(
        response_modalities=modalities,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        ),
    )


# ---------------------------------------------------------------------------
# Core test logic
# ---------------------------------------------------------------------------
async def run_turn(session, turn_num: int, pcm_data: bytes) -> dict:
    """
    Send one turn of audio, collect events until turn_complete.
    Returns dict with timing and transcription info.
    """
    print(f"\n--- Turn {turn_num} ---")

    # 1. ActivityStart
    print(f"  [turn {turn_num}] Sending ActivityStart...")
    await session.send_realtime_input(activity_start=types.ActivityStart())

    # 2. Stream PCM in chunks
    offset = 0
    chunk_count = 0
    bytes_per_chunk = CHUNK_SAMPLES * 2  # 16-bit = 2 bytes per sample
    while offset < len(pcm_data):
        chunk = pcm_data[offset:offset + bytes_per_chunk]
        await session.send_realtime_input(
            audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
        )
        offset += bytes_per_chunk
        chunk_count += 1
        # Small delay to simulate real-time streaming (~32ms per chunk)
        await asyncio.sleep(0.008)  # slightly faster than real-time to keep test snappy

    print(f"  [turn {turn_num}] Sent {chunk_count} audio chunks ({len(pcm_data)} bytes)")

    # 3. ActivityEnd — start the clock
    print(f"  [turn {turn_num}] Sending ActivityEnd (flush)...")
    t_flush = time.monotonic()
    await session.send_realtime_input(activity_end=types.ActivityEnd())

    # 4. Collect events until turn_complete
    transcription_text = ""
    msg_count = 0
    msg_types_seen = []
    t_turn_complete = None

    try:
        async with asyncio.timeout(15):  # 15s max wait per turn
            async for msg in session.receive():
                msg_count += 1
                elapsed_ms = (time.monotonic() - t_flush) * 1000

                server_content = getattr(msg, "server_content", None)
                setup_complete = getattr(msg, "setup_complete", None)

                if setup_complete is not None:
                    print(f"  [turn {turn_num}] msg#{msg_count} setup_complete (unexpected mid-turn)")
                    msg_types_seen.append("setup_complete")
                    continue

                if server_content is None:
                    msg_name = type(msg).__name__
                    print(f"  [turn {turn_num}] msg#{msg_count} no server_content, type={msg_name} (+{elapsed_ms:.0f}ms)")
                    msg_types_seen.append(f"raw:{msg_name}")
                    continue

                model_turn = getattr(server_content, "model_turn", None)
                input_trans = getattr(server_content, "input_transcription", None)
                output_trans = getattr(server_content, "output_transcription", None)
                turn_complete = getattr(server_content, "turn_complete", False)

                # Build a summary of what's in this message
                parts = []
                if model_turn:
                    n_parts = len(getattr(model_turn, "parts", []) or [])
                    parts.append(f"model_turn({n_parts} parts)")
                if input_trans:
                    text = getattr(input_trans, "text", "") or ""
                    finished = getattr(input_trans, "finished", False)
                    parts.append(f"input_trans(text={text!r}, finished={finished})")
                    if text:
                        transcription_text = text
                if output_trans:
                    text = getattr(output_trans, "text", "") or ""
                    parts.append(f"output_trans(text={text!r})")
                if turn_complete:
                    parts.append("TURN_COMPLETE")

                summary = ", ".join(parts) if parts else "empty"
                print(f"  [turn {turn_num}] msg#{msg_count} +{elapsed_ms:.0f}ms: {summary}")
                msg_types_seen.append(summary)

                if turn_complete:
                    t_turn_complete = time.monotonic()
                    break

    except asyncio.TimeoutError:
        print(f"  [turn {turn_num}] TIMEOUT: turn_complete never received after 15s")

    latency_ms = (t_turn_complete - t_flush) * 1000 if t_turn_complete else None
    return {
        "turn": turn_num,
        "msg_count": msg_count,
        "transcription": transcription_text,
        "latency_ms": latency_ms,
        "timed_out": t_turn_complete is None,
        "msg_types": msg_types_seen,
    }


async def test_with_config(model: str, modalities: list) -> bool:
    """Try a specific model+modalities combo. Returns True if both turns succeeded."""
    modality_str = str(modalities) if modalities else "[]"
    print(f"\n{'='*60}")
    print(f"TESTING: model={model!r}, response_modalities={modality_str}")
    print(f"{'='*60}")

    client = genai.Client(api_key=GEMINI_API_KEY)
    config = build_config(modalities)

    # Connect
    print("[connect] Connecting to Gemini Live...")
    t_connect_start = time.monotonic()
    ctx = client.aio.live.connect(model=model, config=config)

    try:
        session = await ctx.__aenter__()
    except Exception as e:
        print(f"[connect] FAILED: {e}")
        return False

    connect_ms = (time.monotonic() - t_connect_start) * 1000
    print(f"[connect] Connected in {connect_ms:.0f}ms")

    # Wait for setup_complete
    print("[connect] Waiting for setup_complete...")
    try:
        async with asyncio.timeout(10):
            async for msg in session.receive():
                setup_complete = getattr(msg, "setup_complete", None)
                if setup_complete is not None:
                    print("[connect] Got setup_complete — session ready")
                    break
                # Some SDKs don't send setup_complete separately; check server_content
                server_content = getattr(msg, "server_content", None)
                if server_content is not None:
                    print(f"[connect] Got server_content before setup_complete: {server_content}")
                    break
    except asyncio.TimeoutError:
        print("[connect] Timeout waiting for setup_complete — proceeding anyway")

    # Generate fake audio (2 seconds of sine at 440Hz)
    print("[audio] Generating 2s of synthetic 16kHz PCM audio...")
    pcm_2s = generate_sine_pcm(2.0, freq_hz=440.0)

    results = []

    # --- Turn 1 ---
    r1 = await run_turn(session, 1, pcm_2s)
    results.append(r1)

    if r1["timed_out"]:
        print("\n[result] Turn 1 timed out — aborting test for this config")
        await ctx.__aexit__(None, None, None)
        return False

    # --- Check session is still alive ---
    print(f"\n[session-check] Turn 1 complete. Checking if session is still open (no reconnect)...")
    session_alive = session is not None
    print(f"[session-check] session object still exists: {session_alive}")

    # Small pause to let any server-side cleanup settle
    await asyncio.sleep(0.2)

    # --- Turn 2 (same session, no reconnect) ---
    print("[turn2] Attempting Turn 2 on the SAME session...")
    try:
        r2 = await run_turn(session, 2, pcm_2s)
        results.append(r2)
        turn2_worked = not r2["timed_out"]
    except Exception as e:
        print(f"[turn2] Exception during Turn 2: {e}")
        turn2_worked = False
        results.append({"turn": 2, "latency_ms": None, "timed_out": True, "error": str(e)})

    # Clean up
    try:
        await ctx.__aexit__(None, None, None)
    except Exception:
        pass

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"RESULTS for model={model!r}, modalities={modality_str}")
    print(f"{'='*60}")
    for r in results:
        t = r.get("turn")
        lat = r.get("latency_ms")
        trans = r.get("transcription", "")
        timedout = r.get("timed_out", True)
        msgs = r.get("msg_count", 0)
        status = "OK" if not timedout else "TIMEOUT"
        lat_str = f"{lat:.0f}ms" if lat is not None else "N/A"
        print(f"  Turn {t}: {status}  latency={lat_str}  msgs={msgs}  transcription={trans!r}")

    print(f"\n  Turn 2 without reconnect: {'YES - SESSION STAYED ALIVE' if turn2_worked else 'NO - FAILED'}")

    return turn2_worked


async def main():
    print("=" * 60)
    print("Gemini Live ASR Approach Test")
    print("Testing TEXT modality to avoid 94-audio-chunk reconnect problem")
    print("=" * 60)
    print(f"Gemini API key configured: {bool(GEMINI_API_KEY)}")
    print()

    # Build ordered list: prefer TEXT for each model, then [], then AUDIO
    # But stop as soon as we find one that works (stays alive across turns)
    test_matrix = []
    for model in MODELS_TO_TRY:
        for modalities in MODALITIES_TO_TRY:
            test_matrix.append((model, modalities))

    found_working = False
    for model, modalities in test_matrix:
        try:
            success = await test_with_config(model, modalities)
            if success:
                print(f"\n{'*'*60}")
                print(f"*** SUCCESS ***")
                print(f"  Model: {model!r}")
                print(f"  response_modalities: {modalities}")
                print(f"  Session stayed alive across 2 turns without reconnect!")
                print(f"{'*'*60}")
                found_working = True
                break
        except Exception as e:
            print(f"\n[error] Unhandled exception for model={model!r}, modalities={modalities}: {e}")
            import traceback
            traceback.print_exc()

    if not found_working:
        print("\n*** All configurations tested. None kept the session fully alive across 2 turns. ***")


if __name__ == "__main__":
    asyncio.run(main())
