import React from 'react';
import TopBar from './TopBar.jsx';
import Transcript from './Transcript.jsx';
import RightPane from './RightPane.jsx';

const API_BASE = "http://localhost:8000";

export default function App() {
  const [state, setState] = React.useState({
    userId: "demo-user",
    sessionId: `session-${Date.now()}`,
    calling: false,
    callTimer: "00:00",
    asrProvider: "gemini_live",
    ttsProvider: "gemini",
    filler: true,
    phraseCache: true,
    promptCache: true,
    speculative: true,
    smartRouting: true,
    memory: false,
    systemPromptOpen: false,
    systemPrompt:
`You are a calm, concise voice assistant for Northwind Auto Insurance.
Speak in short sentences. Never use markdown or lists.
If unsure about policy details, say so.`,
    activeTab: "live",
    metricsHistory: [],
    routeHistory: [],
    postCallReport: null,
    memorySuggestions: [],
    postCallLoading: false,
    postCallError: null,
  });

  const wsRef = React.useRef(null);
  const streamRef = React.useRef(null);
  const audioCtxRef = React.useRef(null);
  const processorRef = React.useRef(null);
  const sourceRef = React.useRef(null);
  const playbackQueueRef = React.useRef([]);
  const isPlayingRef = React.useRef(false);
  const inputGateOpenRef = React.useRef(true);
  const timerRef = React.useRef(null);
  const callStartRef = React.useRef(null);

  const [transcript, setTranscript] = React.useState([]);
  const [partialText, setPartialText] = React.useState("");
  const [status, setStatus] = React.useState("idle");
  const [inputGate, setInputGate] = React.useState({ state: "open", reason: "ready" });
  const [wsError, setWsError] = React.useState(null);

  async function loadPostCallArtifacts(sessionId) {
    if (!sessionId) return;
    setState(s => ({ ...s, postCallLoading: true, postCallError: null }));
    try {
      const reportRes = await fetch(`${API_BASE}/sessions/${sessionId}/post-call-report`);
      if (!reportRes.ok) throw new Error(`post-call report failed: ${reportRes.status}`);
      const reportBody = await reportRes.json();
      const suggestionsRes = await fetch(`${API_BASE}/sessions/${sessionId}/memory-suggestions`);
      const suggestionsBody = suggestionsRes.ok ? await suggestionsRes.json() : { suggestions: [] };
      setState(s => ({
        ...s,
        postCallReport: reportBody.report,
        memorySuggestions: suggestionsBody.suggestions || [],
        postCallLoading: false,
        postCallError: null,
        activeTab: "report",
      }));
    } catch (err) {
      setState(s => ({
        ...s,
        postCallLoading: false,
        postCallError: err.message || "Failed to load post-call report",
      }));
    }
  }

  function playNextChunk() {
    if (isPlayingRef.current || playbackQueueRef.current.length === 0) return;
    const audioCtx = audioCtxRef.current;
    if (!audioCtx) return;
    isPlayingRef.current = true;
    const { float32, sampleRate } = playbackQueueRef.current.shift();
    const buffer = audioCtx.createBuffer(1, float32.length, sampleRate);
    buffer.copyToChannel(float32, 0);
    const src = audioCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(audioCtx.destination);
    src.onended = () => { isPlayingRef.current = false; playNextChunk(); };
    src.start();
  }

  function enqueueAudio(pcmBytes, sampleRate) {
    const int16 = new Int16Array(pcmBytes);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
    playbackQueueRef.current.push({ float32, sampleRate });
    playNextChunk();
  }

  function stopPlayback() {
    playbackQueueRef.current = [];
    isPlayingRef.current = false;
    if (audioCtxRef.current) {
      const oldCtx = audioCtxRef.current;
      audioCtxRef.current = new AudioContext({ sampleRate: 24000 });
      oldCtx.close().catch(() => {});
    }
  }

  function handleControlMessage(msg) {
    switch (msg.type) {
      case "asr.partial":   setPartialText(msg.text || ""); break;
      case "asr.final":
        setTranscript(t => [...t, { role: "user", text: msg.text, ts: Date.now() }]);
        setPartialText("");
        break;
      case "llm.response":
        setTranscript(t => [...t, { role: "agent", text: msg.text, ts: Date.now() }]);
        break;
      case "turn.route":
        setState(s => ({ ...s, routeHistory: [...s.routeHistory, msg.route] }));
        break;
      case "metrics.turn":
        setState(s => ({ ...s, metricsHistory: [...s.metricsHistory, msg.turn] }));
        break;
      case "post_call_eval.started":
        setState(s => ({ ...s, postCallLoading: true, postCallError: null }));
        break;
      case "post_call_eval.completed":
        setState(s => ({
          ...s,
          postCallReport: msg.report,
          postCallLoading: false,
          postCallError: null,
        }));
        loadPostCallArtifacts(msg.session_id);
        break;
      case "post_call_eval.error":
        setState(s => ({ ...s, postCallLoading: false, postCallError: msg.message }));
        break;
      case "input.gate":
        inputGateOpenRef.current = msg.state !== "closed";
        setInputGate({ state: msg.state || "open", reason: msg.reason || "ready" });
        break;
      case "playback.cancel":
      case "barge_in":
        stopPlayback();
        break;
      case "error":
        setWsError(msg.message);
        break;
      default: break;
    }
  }

  async function startCall() {
    setWsError(null);
    setTranscript([]);
    setPartialText("");
    inputGateOpenRef.current = true;
    setInputGate({ state: "open", reason: "ready" });

    const sessionId = `session-${Date.now()}`;
    // Capture current settings before any async gaps
    const {
      asrProvider,
      ttsProvider,
      smartRouting,
      speculative,
      filler,
      phraseCache,
      memory,
      userId,
      systemPrompt,
    } = state;

    setState(s => ({
      ...s,
      calling: true,
      sessionId,
      metricsHistory: [],
      routeHistory: [],
      postCallReport: null,
      memorySuggestions: [],
      postCallLoading: false,
      postCallError: null,
      activeTab: "live",
    }));

    callStartRef.current = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - callStartRef.current) / 1000);
      const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const ss = String(elapsed % 60).padStart(2, '0');
      setState(s => ({ ...s, callTimer: `${mm}:${ss}` }));
    }, 1000);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        video: false,
      });
      streamRef.current = stream;

      const audioCtx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      sourceRef.current = source;
      const processor = audioCtx.createScriptProcessor(512, 1, 1);
      processorRef.current = processor;
      source.connect(processor);
      processor.connect(audioCtx.destination);

      const wsUrl = `ws://localhost:8000/ws/${sessionId}`;
      console.log('[WS] connecting to', wsUrl);
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        const cfg = {
          user_id: userId,
          asr_provider: asrProvider,
          tts_provider: ttsProvider,
          smart_routing: smartRouting,
          spec_enabled: speculative,
          interruptions_enabled: false,
          filler,
          phrase_cache: phraseCache,
          memory,
          system_prompt: systemPrompt,
        };
        console.log('[WS] open, sending config', cfg);
        ws.send(JSON.stringify(cfg));
        setStatus("live");
      };

      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          const buf = new Uint8Array(event.data);
          const pipeIdx = buf.indexOf(124); // '|'
          if (pipeIdx >= 0) {
            const header = JSON.parse(new TextDecoder().decode(buf.slice(0, pipeIdx)));
            enqueueAudio(buf.slice(pipeIdx + 1).buffer, header.sample_rate || 24000);
          } else {
            enqueueAudio(event.data, 24000);
          }
        } else {
          try { handleControlMessage(JSON.parse(event.data)); } catch (_) {}
        }
      };

      ws.onerror = (e) => {
        console.error('[WS] error', e);
        setWsError("WebSocket error — is the backend running on port 8000?");
        setStatus("error");
      };

      ws.onclose = (e) => {
        console.log('[WS] closed', e.code, e.reason);
        setStatus("idle");
        setState(s => {
          if (s.calling) {
            if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
            return { ...s, calling: false, callTimer: "00:00" };
          }
          return s;
        });
      };

      processor.onaudioprocess = (e) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        if (!inputGateOpenRef.current) return;
        const float32 = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++)
          int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768));
        ws.send(int16.buffer);
      };

    } catch (err) {
      console.error('[startCall] error', err);
      setWsError(`Mic error: ${err.message}`);
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      setState(s => ({ ...s, calling: false, callTimer: "00:00" }));
    }
  }

  async function stopCall() {
    const sessionId = state.sessionId;
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setState(s => ({ ...s, calling: false, callTimer: "00:00" }));
    setStatus("idle");
    inputGateOpenRef.current = true;
    setInputGate({ state: "open", reason: "ready" });
    stopPlayback();
    processorRef.current?.disconnect(); processorRef.current = null;
    sourceRef.current?.disconnect(); sourceRef.current = null;
    streamRef.current?.getTracks().forEach(t => t.stop()); streamRef.current = null;
    wsRef.current?.close(); wsRef.current = null;
    if (audioCtxRef.current) { await audioCtxRef.current.close().catch(() => {}); audioCtxRef.current = null; }
    await loadPostCallArtifacts(sessionId);
  }

  return (
    <div className="app">
      <TopBar state={state} setState={setState} onStartCall={startCall} onStopCall={stopCall} />
      <div className="split">
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
          {wsError && (
            <div style={{ background: '#3a1a1a', color: '#ff6b6b', padding: '8px 16px', fontSize: '12px', borderBottom: '1px solid #5a2a2a' }}>
              {wsError}
            </div>
          )}
          {!state.calling ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, flexDirection: 'column', gap: '16px' }}>
              <button
                onClick={startCall}
                style={{ padding: '16px 32px', fontSize: '16px', fontWeight: 600, background: '#2563eb', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer' }}
              >
                Start Call
              </button>
              <div style={{ color: '#888', fontSize: '12px' }}>Connects to ws://localhost:8000</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
              <div style={{ padding: '8px 16px', background: '#1a2a1a', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: status === 'live' ? '#22c55e' : '#f59e0b', animation: status === 'live' ? 'pulse 1.5s infinite' : 'none' }} />
                <span style={{ color: '#888', fontSize: '12px' }}>
                  {status === 'live' ? `Live · ${state.asrProvider} ASR · ${state.ttsProvider} TTS` : 'Connecting...'}
                  {status === 'live' && inputGate.state === 'closed' ? ` · ${inputGate.reason}` : ''}
                </span>
                <button onClick={stopCall} style={{ marginLeft: 'auto', padding: '4px 12px', fontSize: '12px', background: '#dc2626', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                  End Call
                </button>
              </div>
              <Transcript calling={state.calling} liveTranscript={transcript} partialText={partialText} />
            </div>
          )}
        </div>
        <RightPane state={state} setState={setState} />
      </div>
    </div>
  );
}
