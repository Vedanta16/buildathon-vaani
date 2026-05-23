# Engineer B Handover: Half-Duplex Voice Runtime

Date: 2026-05-23

## Completed

- Added a backend `input.gate` WebSocket event for half-duplex turn-taking.
- The backend closes input after `asr.final`, keeps it closed through processing/speaking, and reopens it after TTS cooldown.
- Incoming audio frames are ignored by ASR while the gate is closed.
- Barge-in/interruption handling is disabled by default via `interruptions_enabled: false`.
- The frontend pauses mic PCM upload while `input.gate.state === "closed"` and resumes on `open`.
- Added integration coverage for the gate closing, reopening, ignored gated audio, and disabled interruption metrics.

## Files Touched

- `backend/main.py`
- `frontend/src/App.jsx`
- `backend/tests/test_integration.py`

## Validation

- Run targeted backend tests:
  `python -m pytest backend/tests/test_integration.py backend/tests/test_events.py backend/tests/test_vad.py backend/tests/test_tts_mock.py backend/tests/test_sentence_streaming.py`
- Run frontend build:
  `npm run build` from `frontend/`

## Known Limitations

- Full barge-in is intentionally deferred. Existing code remains, but the default client config sends `interruptions_enabled: false`.
- The current gate is session-local state in the WebSocket loop, not yet part of a shared `UserTurn -> AssistantPlan -> SpokenSegment[]` contract.
- Phrase cache is still text/fuzzy based; Engineer B B5 still needs phrase-ID keyed cache work.

## Recommended Next Checkpoint

Implement the shared runtime contract for `SpokenSegment[]` consumption so Engineer A can hand over structured assistant plans and Engineer B can speak only `should_speak=true` segments.
