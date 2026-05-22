/* Main app */

function App() {
  const [state, setState] = useState({
    userId: "u1",
    sessionId: "s1-3",
    calling: true,
    asrProvider: "Sarvam",
    ttsProvider: "Sarvam",
    filler: true,
    phraseCache: true,
    promptCache: true,
    speculative: true,
    memory: true,
    systemPromptOpen: false,
    systemPrompt:
`You are a calm, concise voice agent for Northwind Auto Insurance.
- Speak in short sentences. Confirm actions before taking them.
- Never invent policy numbers or coverage details. If unsure, say so.
- Use memory only when it is directly relevant to the current turn.
- Match the user's language and tone; do not over-apologize.

Tools available: lookup_policy, send_sms_link, schedule_callback.`,
    activeTab: "live", // 'live' | 'report'
  });

  return (
    <div className="app">
      <TopBar state={state} setState={setState} />
      <div className="split">
        <Transcript calling={state.calling} />
        <RightPane state={state} setState={setState} />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
