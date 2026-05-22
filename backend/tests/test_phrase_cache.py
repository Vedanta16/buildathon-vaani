# backend/tests/test_phrase_cache.py
import pytest
from backend.phrase_cache import normalize, PhraseCache

def test_normalize_strips_punctuation_and_lowercases():
    assert normalize("Got it, one moment!") == "got it one moment"
    assert normalize("Let me pull that up.") == "let me pull that up"

def test_normalize_collapses_whitespace():
    assert normalize("  hello   world  ") == "hello world"

def test_exact_match_returns_audio():
    cache = PhraseCache()
    cache._data["got it one moment"] = b"fake_pcm"
    result = cache.lookup("Got it, one moment!")
    assert result == b"fake_pcm"

def test_fuzzy_match_handles_minor_punctuation_diff():
    cache = PhraseCache()
    cache._data["let me check that for you"] = b"audio_bytes"
    # Extra comma → should still match at 0.88+
    result = cache.lookup("Let me check that, for you.")
    assert result == b"audio_bytes"

def test_no_match_returns_none():
    cache = PhraseCache()
    cache._data["let me check that for you"] = b"audio_bytes"
    result = cache.lookup("Your policy ends on June fourth.")
    assert result is None

def test_semantically_different_does_not_match():
    cache = PhraseCache()
    cache._data["let me check that for you"] = b"audio_bytes"
    result = cache.lookup("Let me find another solution here.")
    assert result is None
