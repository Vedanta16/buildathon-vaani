/* Top bar: brand, user, session, call button, toggles, system prompt drawer */

function TopBar({
  state, setState,
}) {
  const {
    userId, sessionId, calling,
    asrProvider, ttsProvider,
    filler, phraseCache, promptCache, speculative, memory,
    systemPromptOpen, systemPrompt,
  } = state;

  const users = window.MOCK_DATA.users;
  const sessions = window.MOCK_DATA.sessions[userId] || [];

  const setKey = (k, v) => setState((s) => ({ ...s, [k]: v }));

  return (
    <React.Fragment>
      <div className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <div className="brand-mark" />
            <div className="brand-name">voice.console</div>
          </div>

          <select
            className="dd"
            value={userId}
            onChange={(e) => {
              const newSessions = window.MOCK_DATA.sessions[e.target.value] || [];
              setState((s) => ({
                ...s,
                userId: e.target.value,
                sessionId: newSessions[0]?.id || "",
              }));
            }}
          >
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.name} · {u.label}</option>
            ))}
          </select>

          <select
            className="dd"
            value={sessionId}
            onChange={(e) => setKey("sessionId", e.target.value)}
          >
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>

          <div className="spacer" />

          <button
            className={"call-btn" + (calling ? " active" : "")}
            onClick={() => setKey("calling", !calling)}
            type="button"
          >
            <span className="glyph" />
            {calling ? "End Call · 00:34" : "Start Call"}
          </button>

          <div className="spacer" />

          <button
            className={"sp-toggle" + (systemPromptOpen ? " expanded" : "")}
            onClick={() => setKey("systemPromptOpen", !systemPromptOpen)}
            type="button"
          >
            <span className="caret">{systemPromptOpen ? "▾" : "▸"}</span>
            System Prompt
          </button>
        </div>

        <div className="topbar-row row-2">
          <ProviderToggle
            label="ASR"
            value={asrProvider}
            options={["Sarvam", "OpenAI RT", "Gemini Live"]}
            onChange={(v) => setKey("asrProvider", v)}
          />
          <ProviderToggle
            label="TTS"
            value={ttsProvider}
            options={["Sarvam", "OpenAI", "Gemini"]}
            onChange={(v) => setKey("ttsProvider", v)}
          />

          <div className="divider" />

          <Toggle label="Filler"      on={filler}      onClick={() => setKey("filler", !filler)} />
          <Toggle label="PhraseCache" on={phraseCache} onClick={() => setKey("phraseCache", !phraseCache)} />
          <Toggle label="PromptCache" on={promptCache} onClick={() => setKey("promptCache", !promptCache)} />
          <Toggle label="SpecGen"     on={speculative} onClick={() => setKey("speculative", !speculative)} />
          <Toggle label="Memory"      on={memory}      onClick={() => setKey("memory", !memory)} />

          <div className="spacer" />

          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg-tertiary)", letterSpacing: "0.06em", textTransform: "uppercase", whiteSpace: "nowrap" }}>
            {Object.entries({ filler, phraseCache, promptCache, speculative, memory }).filter(([, v]) => v).length}/5 features active
          </span>
        </div>
      </div>

      {systemPromptOpen && (
        <div className="system-prompt">
          <div className="system-prompt-inner">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--fg-tertiary)" }}>
                System prompt · cached prefix · {systemPrompt.length} chars
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--accent)" }}>
                ● cache eligible
              </div>
            </div>
            <textarea
              value={systemPrompt}
              onChange={(e) => setKey("systemPrompt", e.target.value)}
              spellCheck={false}
            />
          </div>
        </div>
      )}
    </React.Fragment>
  );
}

window.TopBar = TopBar;
