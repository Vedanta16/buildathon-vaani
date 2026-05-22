#!/usr/bin/env python3
"""
One-time script to pre-generate TTS audio for stock phrases.
Usage: python -m backend.scripts.pregen_phrases --tts-provider openai
"""
import asyncio
import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.phrase_cache import phrase_cache
from backend.providers.tts.factory import create_tts
from backend.events import TtsAudioChunk

PHRASES = [
    "Got it, one moment.",
    "Let me pull that up.",
    "Let me check that for you.",
    "One second.",
    "I see that here.",
    "Sure, I can help with that.",
    "Absolutely.",
    "Of course.",
    "Let me look into that.",
    "Is there anything else I can help you with?",
    "Have a great day.",
    "Thank you for calling.",
    "I understand.",
    "Got it.",
    "Sending that now.",
    "Let me find that for you.",
    "I'll take care of that.",
    "No problem at all.",
    "Right away.",
    "I'm looking into that now.",
]

async def generate(tts_provider: str):
    print(f"Generating {len(PHRASES)} phrases with {tts_provider} TTS...")
    for phrase in PHRASES:
        chunks = []
        def collect(event):
            if isinstance(event, TtsAudioChunk):
                chunks.append(event.pcm_bytes)

        tts = create_tts(on_event=collect, provider=tts_provider)
        await tts.synthesize(phrase)
        await tts.close()

        if chunks:
            phrase_cache.add(phrase, b"".join(chunks))
            print(f"  OK: {phrase}")
        else:
            print(f"  SKIP: {phrase} (no audio)")

    phrase_cache.save()
    count = len(phrase_cache._data)
    print(f"Saved {count} entries to phrase_cache/phrases.pkl")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tts-provider", default="openai", choices=["openai", "gemini", "mock"])
    args = parser.parse_args()
    asyncio.run(generate(args.tts_provider))
