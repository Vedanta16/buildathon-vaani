/* Main app */

function App() {
  const [state, setState] = React.useState({
    userId: "demo-user",
    sessionId: `session-${Date.now()}`,
    calling: false,
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
  });

  // WebSocket + audio refs (not React state — these are imperative)
  const wsRef = React.useRef(null);
  const streamRef = React.useRef(null);
  const audioCtxRef = React.useRef(null);
  const processorRef = React.useRef(null);
  const sourceRef = React.useRef(null);
  const playbackQueueRef = React.useRef([]);
  const isPlayingRef = React.useRef(false);

  // UI state for transcript and status
  const [transcript, setTranscript] = React.useState([]);
  const [partialText, setPartialText] = React.useState("");
  const [status, setStatus] = React.useState("idle");
  const [wsError, setWsError] = React.useState(null);

  // --- Audio playback queue ---
  async function playNextChunk() {
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
    src.onended = () => {
      isPlayingRef.current = false;
      playNextChunk();
    };
    src.start();
  }

  function enqueueAudio(pcmBytes, sampleRate) {
    const int16 = new Int16Array(pcmBytes);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768;
    }
    playbackQueueRef.current.push({ float32, sampleRate });
    playNextChunk();
  }

  function stopPlayback() {
    playbackQueueRef.current = [];
    isPlayingRef.current = false;
    // Recreate audio context to stop current playback
    if (audioCtxRef.current) {
      const oldCtx = audioCtxRef.current;
      const newCtx = new AudioContext({ sampleRate: 24000 });
      audioCtxRef.current = newCtx;
      oldCtx.close().catch(() => {});
    }
  }

  // --- Start/stop call ---
  async function startCall() {
    setWsError(null);
    setTranscript([]);
    setPartialText("");
    const sessionId = `session-${Date.now()}`;
    setState(s => ({ ...s, calling: true, sessionId }));

    try {
      // 1. Mic with echo cancellation
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
        video: false,
      });
      streamRef.current = stream;

      // 2. Audio context for mic capture
      const audioCtx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      sourceRef.current = source;

      // ScriptProcessor for PCM capture (deprecated but widely supported)
      const processor = audioCtx.createScriptProcessor(512, 1, 1);
      processorRef.current = processor;
      source.connect(processor);
      processor.connect(audioCtx.destination);

      // 3. WebSocket
      const wsUrl = `ws://localhost:8000/ws/${sessionId}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        setStatus("connecting");
        ws.send(JSON.stringify({
          user_id: state.userId,
          asr_provider: state.asrProvider,
          tts_provider: state.ttsProvider,
          smart_routing: state.smartRouting,
          spec_enabled: state.speculative,
        }));
        setStatus("live");
      };

      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          // Binary: JSON_HEADER|PCM_BYTES
          const buf = new Uint8Array(event.data);
          const pipeIdx = buf.indexOf(124); // '|' = 124
          if (pipeIdx >= 0) {
            const headerBytes = buf.slice(0, pipeIdx);
            const audioBytes = buf.slice(pipeIdx + 1).buffer;
            try {
              const header = JSON.parse(new TextDecoder().decode(headerBytes));
              enqueueAudio(audioBytes, header.sample_rate || 24000);
            } catch (_) {
              enqueueAudio(event.data, 24000);
            }
          } else {
            enqueueAudio(event.data, 24000);
          }
        } else {
          // Text: JSON control message
          try {
            const msg = JSON.parse(event.data);
            handleControlMessage(msg);
          } catch (_) {}
        }
      };

      ws.onerror = (e) => {
        setWsError("WebSocket error — is the backend running on port 8000?");
        setStatus("error");
      };

      ws.onclose = () => {
        setStatus("idle");
      };

      // 4. Stream PCM from mic to WS
      processor.onaudioprocess = (e) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        const float32 = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768));
        }
        ws.send(int16.buffer);
      };

    } catch (err) {
      setWsError(`Mic error: ${err.message}`);
      setState(s => ({ ...s, calling: false }));
    }
  }

  function handleControlMessage(msg) {
    switch (msg.type) {
      case "asr.partial":
        setPartialText(msg.text || "");
        break;
      case "asr.final":
        setTranscript(t => [...t, { role: "user", text: msg.text, ts: Date.now() }]);
        setPartialText("");
        break;
      case "tts.done":
        // nothing — audio is queued
        break;
      case "playback.cancel":
        stopPlayback();
        break;
      case "barge_in":
        stopPlayback();
        break;
      case "metrics.turn":
        // Could update metrics panel here
        break;
      case "error":
        setWsError(msg.message);
        break;
      default:
        break;
    }
  }

  async function stopCall() {
    setState(s => ({ ...s, calling: false }));
    setStatus("idle");
    stopPlayback();

    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (audioCtxRef.current) {
      await audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
  }

  return (
    <div className="app">
      <TopBar state={state} setState={setState} />
      <div className="split">
        <div style={{display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden'}}>
          {wsError && (
            <div style={{
              background: '#3a1a1a', color: '#ff6b6b', padding: '8px 16px',
              fontSize: '12px', borderBottom: '1px solid #5a2a2a'
            }}>
              {wsError}
            </div>
          )}
          {!state.calling ? (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flex: 1, flexDirection: 'column', gap: '16px'
            }}>
              <button
                onClick={startCall}
                style={{
                  padding: '16px 32px', fontSize: '16px', fontWeight: 600,
                  background: '#2563eb', color: 'white', border: 'none',
                  borderRadius: '8px', cursor: 'pointer'
                }}
              >
                Start Call
              </button>
              <div style={{color: '#888', fontSize: '12px'}}>
                Connects to ws://localhost:8000
              </div>
            </div>
          ) : (
            <div style={{display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden'}}>
              <div style={{
                padding: '8px 16px', background: '#1a2a1a',
                display: 'flex', alignItems: 'center', gap: '8px'
              }}>
                <div style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: status === 'live' ? '#22c55e' : '#f59e0b',
                  animation: status === 'live' ? 'pulse 1.5s infinite' : 'none'
                }} />
                <span style={{color: '#888', fontSize: '12px'}}>
                  {status === 'live' ? `Live · ${state.asrProvider} ASR · ${state.ttsProvider} TTS` : 'Connecting...'}
                </span>
                <button
                  onClick={stopCall}
                  style={{
                    marginLeft: 'auto', padding: '4px 12px', fontSize: '12px',
                    background: '#dc2626', color: 'white', border: 'none',
                    borderRadius: '4px', cursor: 'pointer'
                  }}
                >
                  End Call
                </button>
              </div>
              <Transcript
                calling={state.calling}
                liveTranscript={transcript}
                partialText={partialText}
              />
            </div>
          )}
        </div>
        <RightPane state={state} setState={setState} />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
