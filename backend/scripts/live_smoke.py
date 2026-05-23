import argparse
import asyncio
import json
import math
import sys
import uuid
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
import websockets


TARGET_SAMPLE_RATE = 16000
FRAME_SAMPLES = 512


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_wav_pcm16_mono_16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)

    if sample_width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) << 8
    elif sample_width == 2:
        samples = np.frombuffer(raw, dtype="<i2")
    elif sample_width == 4:
        samples = (np.frombuffer(raw, dtype="<i4") >> 16).astype(np.int16)
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width} bytes")

    if channels > 1:
        samples = samples.reshape(-1, channels).astype(np.int32).mean(axis=1)
    else:
        samples = samples.astype(np.int32)

    if sample_rate != TARGET_SAMPLE_RATE:
        gcd = math.gcd(sample_rate, TARGET_SAMPLE_RATE)
        up = TARGET_SAMPLE_RATE // gcd
        down = sample_rate // gcd
        samples = resample_poly(samples.astype(np.float32), up, down)

    samples = np.clip(np.rint(samples), -32768, 32767).astype("<i2")
    trailing_silence = np.zeros(TARGET_SAMPLE_RATE, dtype="<i2")
    return samples.tobytes() + trailing_silence.tobytes()


def _parse_binary_message(data: bytes) -> dict | None:
    header, sep, payload = data.partition(b"|")
    if not sep:
        return None
    try:
        msg = json.loads(header.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    msg["_payload_bytes"] = len(payload)
    return msg


async def _send_audio(ws, pcm: bytes, realtime: bool) -> None:
    frame_bytes = FRAME_SAMPLES * 2
    frame_delay = FRAME_SAMPLES / TARGET_SAMPLE_RATE
    for offset in range(0, len(pcm), frame_bytes):
        chunk = pcm[offset: offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk += b"\x00" * (frame_bytes - len(chunk))
        await ws.send(chunk)
        if realtime:
            await asyncio.sleep(frame_delay)


def _required_done(seen: dict) -> bool:
    if not seen["asr_final"]:
        return False
    if not any(text.strip() for text in seen["llm_response"]):
        return False
    if seen["tts_audio_chunk"] < 1 or not seen["tts_done"]:
        return False
    metrics = seen["metrics_turn"]
    if not metrics:
        return False
    return any(
        turn.get("asr_streaming") is True
        and turn.get("tts_streaming") is True
        and turn.get("vad_mode") == "local_manual"
        for turn in metrics
    )


def _missing_summary(seen: dict) -> list[str]:
    missing: list[str] = []
    if not seen["asr_final"]:
        missing.append("asr.final")
    if not any(text.strip() for text in seen["llm_response"]):
        missing.append("non-empty llm.response")
    if seen["tts_audio_chunk"] < 1:
        missing.append("tts.audio_chunk")
    if not seen["tts_done"]:
        missing.append("tts.done")
    if not seen["metrics_turn"]:
        missing.append("metrics.turn")
    elif not _required_done(seen):
        missing.append("metrics.turn with asr_streaming=true, tts_streaming=true, vad_mode=local_manual")
    return missing


async def run_smoke(args: argparse.Namespace) -> int:
    audio_path = Path(args.audio)
    if not audio_path.is_absolute():
        audio_path = _repo_root() / audio_path
    if not audio_path.exists():
        print(f"Audio fixture not found: {audio_path}", file=sys.stderr)
        return 2

    pcm = load_wav_pcm16_mono_16k(audio_path)
    session_id = args.session_id or f"smoke-{uuid.uuid4().hex[:10]}"
    ws_url = args.url.rstrip("/") + f"/ws/{session_id}"

    seen = {
        "asr_final": [],
        "llm_response": [],
        "tts_audio_chunk": 0,
        "tts_done": False,
        "metrics_turn": [],
        "errors": [],
    }
    done = asyncio.Event()

    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({
            "user_id": "live-smoke",
            "asr_provider": args.asr_provider,
            "tts_provider": args.tts_provider,
            "smart_routing": True,
            "spec_enabled": False,
            "system_prompt": (
                "You are a concise voice assistant. Reply to the user's test audio "
                "in one short sentence."
            ),
        }))

        async def receive_loop() -> None:
            async for raw in ws:
                msg = None
                if isinstance(raw, bytes):
                    msg = _parse_binary_message(raw)
                    if msg and msg.get("type") == "tts.audio_chunk":
                        seen["tts_audio_chunk"] += 1
                else:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                if not msg:
                    continue
                msg_type = msg.get("type")
                if msg_type == "error":
                    seen["errors"].append(msg.get("message", "unknown backend error"))
                    done.set()
                elif msg_type == "asr.final":
                    seen["asr_final"].append(msg.get("text", ""))
                elif msg_type == "llm.response":
                    seen["llm_response"].append(msg.get("text", ""))
                elif msg_type == "tts.done":
                    seen["tts_done"] = True
                elif msg_type == "metrics.turn":
                    seen["metrics_turn"].append(msg.get("turn", {}))

                if _required_done(seen):
                    done.set()

        receiver = asyncio.create_task(receive_loop())
        sender = asyncio.create_task(_send_audio(ws, pcm, realtime=not args.no_realtime))
        try:
            await asyncio.wait_for(done.wait(), timeout=args.timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

    if seen["errors"]:
        print("Live smoke failed with backend error:", file=sys.stderr)
        for error in seen["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1

    if not _required_done(seen):
        print("Live smoke timed out or missed required events.", file=sys.stderr)
        for item in _missing_summary(seen):
            print(f"- Missing {item}", file=sys.stderr)
        return 1

    turn = seen["metrics_turn"][-1]
    print("Live smoke passed")
    print(f"- session_id: {session_id}")
    print(f"- asr_provider: {args.asr_provider}")
    print(f"- tts_provider: {args.tts_provider}")
    print(f"- asr.final: {seen['asr_final'][0]!r}")
    print(f"- llm.response: {seen['llm_response'][-1]!r}")
    print(f"- tts.audio_chunk count: {seen['tts_audio_chunk']}")
    print(
        "- metrics: "
        f"asr_streaming={turn.get('asr_streaming')} "
        f"tts_streaming={turn.get('tts_streaming')} "
        f"vad_mode={turn.get('vad_mode')}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live WebSocket V2V smoke test")
    parser.add_argument("--url", default="ws://localhost:8000", help="Backend WebSocket base URL")
    parser.add_argument("--session-id", default="", help="Optional fixed session id")
    parser.add_argument("--audio", default="backend/harvard.wav", help="WAV fixture to stream")
    parser.add_argument("--asr-provider", default="gemini_live", help="ASR provider to request")
    parser.add_argument("--tts-provider", default="gemini", help="TTS provider to request")
    parser.add_argument("--timeout", type=float, default=90.0, help="Seconds to wait for required events")
    parser.add_argument("--no-realtime", action="store_true", help="Send frames as fast as possible")
    return parser.parse_args()


def main() -> None:
    raise SystemExit(asyncio.run(run_smoke(parse_args())))


if __name__ == "__main__":
    main()
