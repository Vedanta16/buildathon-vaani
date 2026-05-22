/* Left pane — live transcript */

function Transcript({ calling, liveTranscript, partialText }) {
  // Use live transcript if provided, otherwise fall back to mock data
  const turns = liveTranscript && liveTranscript.length > 0
    ? liveTranscript.map(t => ({
        role: t.role,
        body: t.text,
        ts: new Date(t.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      }))
    : (window.MOCK_DATA ? window.MOCK_DATA.transcript : []);

  const scrollerRef = useRef(null);

  useEffect(() => {
    if (scrollerRef.current && calling) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [turns, partialText, calling]);

  return (
    <div className="pane" style={{ background: "var(--bg-base)" }}>
      <PanelHeader
        label="Transcript"
        right={
          calling ? (
            <span className="pulse">
              <span className="pulse-dot" /> live
            </span>
          ) : (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg-tertiary)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
              idle
            </span>
          )
        }
      />
      <div className="transcript" ref={scrollerRef}>
        <div className="transcript-inner">
          {turns.map((t, i) => (
            <TranscriptTurn key={i} turn={t} />
          ))}
          {partialText && (
            <div className="turn partial">
              <div className="role">User…</div>
              <div className="body" style={{ opacity: 0.6, fontStyle: 'italic' }}>{partialText}</div>
            </div>
          )}
          {!calling && turns.length > 0 && (
            <div style={{ color: "var(--fg-tertiary)", fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", paddingTop: 8 }}>
              — end of call —
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TranscriptTurn({ turn }) {
  const role =
    turn.role === "user" ? "User" :
    turn.role === "agent" ? "Agent" :
    turn.role === "partial" ? "User…" : turn.role;

  // Render agent body with memory-recall underlines if any
  let body;
  if (turn.memoryRefs && turn.memoryRefs.length > 0) {
    let remaining = turn.body;
    const parts = [];
    let key = 0;
    for (const ref of turn.memoryRefs) {
      const idx = remaining.indexOf(ref.phrase);
      if (idx === -1) { parts.push(remaining); remaining = ""; break; }
      if (idx > 0) parts.push(remaining.slice(0, idx));
      parts.push(
        <span className="memory-ref" key={key++} title={ref.note}>
          {ref.phrase}
        </span>
      );
      remaining = remaining.slice(idx + ref.phrase.length);
    }
    if (remaining) parts.push(remaining);
    body = parts;
  } else {
    body = turn.body;
  }

  return (
    <div className={"turn " + turn.role}>
      <div className="role">{role}</div>
      <div className="body">
        {body}
        {turn.bargeIn && <span className="barge-mark">barge-in</span>}
      </div>
      <div className="ts">{turn.ts}</div>
    </div>
  );
}

window.Transcript = Transcript;
