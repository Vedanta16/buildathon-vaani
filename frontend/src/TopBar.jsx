import React from 'react';
import { Toggle, ProviderToggle } from './components.jsx';

export default function TopBar({ state, setState, onStartCall, onStopCall }) {
  const {
    calling, callTimer,
    asrProvider, ttsProvider,
    filler, phraseCache, promptCache, speculative, memory,
    systemPromptOpen, systemPrompt,
  } = state;

  const setKey = (k, v) => setState((s) => ({ ...s, [k]: v }));

  return (
    <React.Fragment>
      <div className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <div className="brand-mark" />
            <div className="brand-name">voice.console</div>
          </div>

          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg-secondary)", letterSpacing: "0.04em" }}>
            demo-user
          </span>

          <div className="spacer" />

          <button
            className={"call-btn" + (calling ? " active" : "")}
            onClick={() => calling ? onStopCall() : onStartCall()}
            type="button"
          >
            <span className="glyph" />
            {calling ? `End Call · ${callTimer}` : "Start Call"}
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
            options={["gemini_live", "openai_realtime"]}
            onChange={(v) => setKey("asrProvider", v)}
          />
          <ProviderToggle
            label="TTS"
            value={ttsProvider}
            options={["gemini", "openai"]}
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
