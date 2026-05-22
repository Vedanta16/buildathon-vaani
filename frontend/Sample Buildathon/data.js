/* Mock data for the dashboard */

window.MOCK_DATA = (() => {

  const users = [
    { id: "u1", name: "User 1", label: "priya.s@acme.com" },
    { id: "u2", name: "User 2", label: "arjun.r@acme.com" },
    { id: "u3", name: "User 3", label: "kavita.m@acme.com" },
  ];

  const sessions = {
    u1: [
      { id: "s1-3", label: "Call 3 — May 22, 2:14 pm", at: "2:14 pm", dur: "3m 42s", live: true },
      { id: "s1-2", label: "Call 2 — May 21, 4:08 pm", at: "yesterday", dur: "2m 11s" },
      { id: "s1-1", label: "Call 1 — May 19, 11:22 am", at: "3 days ago", dur: "5m 04s" },
    ],
    u2: [
      { id: "s2-2", label: "Call 2 — May 20, 9:31 am", at: "2 days ago", dur: "4m 18s" },
      { id: "s2-1", label: "Call 1 — May 17, 6:42 pm", at: "5 days ago", dur: "1m 49s" },
    ],
    u3: [
      { id: "s3-1", label: "Call 1 — May 22, 12:01 pm", at: "today", dur: "2m 33s" },
    ],
  };

  const transcript = [
    { role: "agent", body: "Hey Priya, glad to have you back. What can I help with today?", ts: "00:00", phraseCache: true },
    { role: "user",  body: "Yeah, hey — I'm trying to renew my policy but the link in your email isn't working.", ts: "00:04" },
    { role: "agent", body: "Got it, one moment — let me pull that up.", ts: "00:07", phraseCache: true },
    { role: "agent", body: "I can see your auto policy ending June 4th. The link in the email points to our old portal; we migrated last week. I can text you a working renewal link right now to the number ending 4421.", ts: "00:09" },
    { role: "user",  body: "Wait actually before that — does it still include the roadside coverage we added last time?", ts: "00:18", bargeIn: true },
    { role: "agent", body: "Yes — roadside is on this policy, same $0 deductible we set up in March. Want me to keep that on the renewal?", ts: "00:21", memoryRefs: [{ phrase: "roadside is on this policy, same $0 deductible we set up in March", note: "from memory: user added roadside coverage on Mar 14 call" }] },
    { role: "user",  body: "Yeah keep it. And send the link.", ts: "00:28" },
    { role: "agent", body: "Sending now. You should see it in a few seconds. Anything else?", ts: "00:30" },
    { role: "partial", body: "no that's it thanks", ts: "00:34" },
  ];

  // Pipeline trace — most recent turn (in ms, total 1240ms)
  const pipeline = {
    totalMs: 1240,
    segs: [
      { kind: "vad",   label: "VAD",          ms: 80 },
      { kind: "asr-p", label: "ASR partial",  ms: 140, prov: "sarvam" },
      { kind: "asr-p", label: "partial ×3",   ms: 120, prov: "sarvam" },
      { kind: "asr",   label: "ASR final",    ms: 90,  prov: "sarvam" },
      { kind: "filler",label: "Filler",       ms: 60 },
      { kind: "llm",   label: "LLM",          ms: 360, prov: "gpt-4o" },
      { kind: "tts",   label: "TTS",          ms: 220, prov: "sarvam" },
      { kind: "playback", label: "Playback",  ms: 170 },
    ],
    bargeIn: false,
    speculative: true,
  };

  // Latency stacked bars — last 10 turns
  const latency = [
    { asr: 110, llm: 320, tts: 180, cache: false },
    { asr: 120, llm: 290, tts: 0,   cache: true  },
    { asr: 95,  llm: 410, tts: 200, cache: false },
    { asr: 105, llm: 280, tts: 180, cache: false },
    { asr: 115, llm: 0,   tts: 0,   cache: true, spec: true },
    { asr: 130, llm: 360, tts: 220, cache: false },
    { asr: 100, llm: 305, tts: 190, cache: false },
    { asr: 90,  llm: 250, tts: 0,   cache: true  },
    { asr: 125, llm: 340, tts: 210, cache: false },
    { asr: 110, llm: 310, tts: 190, cache: false },
  ];

  // Sparkline sample sequences
  const spark = {
    perceived: [820, 760, 740, 690, 710, 680, 650, 620, 640, 610, 590, 580],
    actual:    [1240,1180,1160,1090,1120,1090,1040,1020,1030,1010,990, 980],
    cost:      [0.02,0.04,0.05,0.07,0.07,0.08,0.10,0.11,0.12,0.13,0.14,0.15],
    hits:      [0,1,1,2,2,2,3,3,4,4,5,5],
  };

  const cost = {
    this: {
      promptCached:   1247,
      promptUncached: 386,
      completion:     412,
      dollars:        0.0142,
    },
    prev: {
      promptCached:   0,
      promptUncached: 1633,
      completion:     401,
      dollars:        0.0443,
    },
    savings: {
      tokens: 1247,
      dollars: 0.0301,
    },
  };

  const postCall = {
    ready: true,
    summary: "Priya called to renew her auto policy after the link in our renewal email failed. Confirmed she still wants the roadside-assist add-on from her March 14 call. Agent texted a working renewal link to the phone on file; call ended cleanly with no follow-up needed.",
    sentiment: ["neutral", "curious", "satisfied"],
    talk: { user: 38, agent: 62 },
    wins: [
      { text: "Phrase cache hit on opening greeting — TTS TTFB 0 ms.", meta: "turn 1" },
      { text: "Speculative gen committed on \"roadside coverage\" turn, saved ~410 ms.", meta: "turn 5" },
      { text: "Memory recall: roadside / $0 deductible from Mar 14 call surfaced correctly.", meta: "turn 6" },
      { text: "Barge-in handled cleanly mid-TTS; recovery in 240 ms.", meta: "turn 4" },
    ],
    issues: [
      { text: "Agent talked over user briefly at 00:18 before VAD canceled playback.", meta: "00:18" },
      { text: "LLM TTFT spiked to 410 ms on turn 3 — likely cache miss, prompt drifted by 2 tokens.", meta: "turn 3" },
    ],
    memorySuggestions: [
      { op: "add",    text: "Phone ending 4421 confirmed for SMS contact." },
      { op: "update", text: "Auto policy renewal cadence — annual, accepts SMS link flow." },
      { op: "add",    text: "Sensitive to broken links; prefers single-tap resolution." },
    ],
  };

  const costTurns = [
    { promptCached: 180, promptUncached: 60, completion: 45, tts: 0.0008, asr: 0.0005, phraseCache: true,  bargeIn: false, specHit: false },
    { promptCached: 185, promptUncached: 55, completion: 50, tts: 0.0012, asr: 0.0006, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 190, promptUncached: 50, completion: 80, tts: 0.0015, asr: 0.0007, phraseCache: false, bargeIn: true,  specHit: false },
    { promptCached: 195, promptUncached: 48, completion: 42, tts: 0.0000, asr: 0.0005, phraseCache: true,  bargeIn: false, specHit: false },
    { promptCached: 200, promptUncached: 45, completion: 38, tts: 0.0010, asr: 0.0006, phraseCache: false, bargeIn: false, specHit: true  },
    { promptCached: 205, promptUncached: 43, completion: 55, tts: 0.0013, asr: 0.0007, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 207, promptUncached: 40, completion: 48, tts: 0.0000, asr: 0.0005, phraseCache: true,  bargeIn: false, specHit: false },
    { promptCached: 210, promptUncached: 38, completion: 52, tts: 0.0011, asr: 0.0006, phraseCache: false, bargeIn: false, specHit: true  },
  ];

  const costPrevTurns = [
    { promptCached: 0, promptUncached: 240, completion: 45, tts: 0.0009, asr: 0.0005, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 0, promptUncached: 238, completion: 50, tts: 0.0013, asr: 0.0006, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 0, promptUncached: 235, completion: 80, tts: 0.0016, asr: 0.0007, phraseCache: false, bargeIn: true,  specHit: false },
    { promptCached: 0, promptUncached: 243, completion: 42, tts: 0.0011, asr: 0.0005, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 0, promptUncached: 245, completion: 38, tts: 0.0010, asr: 0.0006, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 0, promptUncached: 248, completion: 55, tts: 0.0014, asr: 0.0007, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 0, promptUncached: 247, completion: 48, tts: 0.0012, asr: 0.0005, phraseCache: false, bargeIn: false, specHit: false },
    { promptCached: 0, promptUncached: 250, completion: 52, tts: 0.0013, asr: 0.0006, phraseCache: false, bargeIn: false, specHit: false },
  ];

  return { users, sessions, transcript, pipeline, latency, spark, cost, postCall, costTurns, costPrevTurns };
})();
