import React from 'react';
import { Section, MetricBlock } from './components.jsx';

const API_BASE = "http://localhost:8000";

export default function RightPane({ state, setState }) {
  const { activeTab, calling } = state;
  const setTab = (t) => setState((s) => ({ ...s, activeTab: t }));

  return (
    <div className="pane" style={{ background: "var(--bg-base)" }}>
      <div className="tabs">
        <button
          className={"tab" + (activeTab === "live" ? " active" : "")}
          onClick={() => setTab("live")}
          type="button"
        >
          Live Metrics
          {calling && <span className="count">●</span>}
        </button>
        <button
          className={"tab" + (activeTab === "report" ? " active" : "")}
          onClick={() => setTab("report")}
          type="button"
        >
          Post-Call Report
        </button>
      </div>

      <div className="right-pane-body">
        {activeTab === "live"   && <LiveMetrics state={state} />}
        {activeTab === "report" && <PostCallReport state={state} setState={setState} />}
      </div>
    </div>
  );
}

function LiveMetrics({ state }) {
  return (
    <React.Fragment>
      <PipelineTrace state={state} />
      <LatencyStats state={state} />
      <CostTokens state={state} />
    </React.Fragment>
  );
}

function PipelineTrace({ state }) {
  const metricsHistory = state && state.metricsHistory;
  const latestTurn = metricsHistory && metricsHistory.length > 0
    ? metricsHistory[metricsHistory.length - 1]
    : null;

  if (!latestTurn) {
    return (
      <Section label="Pipeline Trace · last turn" meta="waiting for live metrics">
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--fg-secondary)" }}>
          Start a call and complete one user turn to populate live ASR, route, LLM, TTS, and metrics data.
        </p>
      </Section>
    );
  }

  let segs = [];
  let specHit = latestTurn.spec_hit;
  let totalMs = 1;
  if (latestTurn.vad_start_ms != null) {
    const asrMs = Math.max(0, (latestTurn.asr_final_ms || 0) - latestTurn.vad_start_ms);
    const fillerMs = latestTurn.filler_played
      ? Math.max(0, (latestTurn.llm_start_ms || 0) - (latestTurn.asr_final_ms || 0))
      : 0;
    const llmMs = Math.max(0, (latestTurn.llm_first_token_ms || 0) - (latestTurn.llm_start_ms || 0));
    const ttsMs = latestTurn.phrase_cache_hit
      ? 0
      : Math.max(0, (latestTurn.tts_first_audio_ms || 0) - (latestTurn.tts_start_ms || 0));
    const endMs = Math.max(
      latestTurn.tts_first_audio_ms || 0,
      latestTurn.llm_first_token_ms || 0,
      latestTurn.asr_final_ms || 0,
      latestTurn.tts_start_ms || 0,
    );
    totalMs = Math.max(1, endMs - latestTurn.vad_start_ms);
    if (asrMs > 0) segs.push({ kind: "asr", label: latestTurn.asr_streaming ? "ASR stream" : "ASR", ms: asrMs, prov: latestTurn.asr_provider || "" });
    if (fillerMs > 0) segs.push({ kind: "filler", label: "Filler", ms: fillerMs });
    if (llmMs > 0) segs.push({ kind: "llm", label: "LLM TTFT", ms: llmMs, prov: latestTurn.model || "gpt-4o" });
    if (ttsMs > 0) segs.push({ kind: "tts", label: latestTurn.tts_streaming ? "TTS stream" : "TTS TTFB", ms: ttsMs, prov: latestTurn.tts_provider || "" });
  }
  if (segs.length === 0) segs.push({ kind: "llm", label: latestTurn.lane || "route", ms: totalMs, prov: latestTurn.route || "" });

  const ticks = [0, 250, 500, 750, 1000, 1250].filter((t) => t <= totalMs + 50);

  return (
    <Section
      label="Pipeline Trace · last turn"
      meta={
        <span>
          <span style={{ color: "var(--fg-secondary)" }}>{totalMs} ms</span>
          {" "}· {segs.length} stages
          {specHit && <span style={{ color: "var(--accent)", marginLeft: 8 }}>· spec ✓</span>}
          {latestTurn && <span style={{ color: "var(--accent)", marginLeft: 8 }}>· live</span>}
          {latestTurn?.asr_streaming && <span style={{ color: "var(--accent)", marginLeft: 8 }}>· ASR stream</span>}
          {latestTurn?.tts_streaming && <span style={{ color: "var(--accent)", marginLeft: 8 }}>· TTS stream</span>}
        </span>
      }
    >
      <div className="timeline">
        <div className="timeline-row">
          {segs.map((s, i) => (
            <div
              key={i}
              className={"tl-seg " + s.kind}
              style={{ width: `${(s.ms / totalMs) * 100}%` }}
              title={`${s.label} · ${s.ms}ms${s.prov ? ` · ${s.prov}` : ""}`}
            >
              <span>{s.label}</span>
              {s.prov && <span className="prov">[{s.prov}]</span>}
            </div>
          ))}
        </div>
      </div>
      <div className="timeline-ticks">
        {ticks.map((t) => (
          <span key={t}>{t}ms</span>
        ))}
      </div>
      <div className="legend">
        <span><span className="swatch" style={{ background: "#232830" }} />VAD</span>
        <span><span className="swatch" style={{ background: "#2C3640" }} />ASR final</span>
        <span><span className="swatch" style={{ background: "#232C34" }} />ASR partial</span>
        <span><span className="swatch" style={{ background: "#3A2E22" }} />Filler</span>
        <span><span className="swatch" style={{ background: "#3B3A2A" }} />LLM</span>
        <span><span className="swatch" style={{ background: "#2D4A38" }} />TTS</span>
        <span><span className="swatch" style={{ background: "var(--accent-dim)" }} />TTS · cached</span>
        {latestTurn && <span>VAD: {latestTurn.vad_mode || "unknown"}</span>}
        {latestTurn?.tts_sentence_count > 0 && <span>sentences: {latestTurn.tts_sentence_count}</span>}
      </div>
    </Section>
  );
}

function LatencyStats({ state }) {
  const metricsHistory = state && state.metricsHistory;
  const hasLive = metricsHistory && metricsHistory.length > 0;

  if (!hasLive) {
    return (
      <Section label="Latency · live session" meta="waiting for live turns">
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--fg-secondary)" }}>
          No live turn metrics yet. The chart uses only backend <code>metrics.turn</code> events.
        </p>
      </Section>
    );
  }

  const latency = metricsHistory.map((t) => ({
        asr: Math.max(0, (t.asr_final_ms || 0) - (t.vad_start_ms || 0)),
        llm: Math.max(0, (t.llm_first_token_ms || 0) - (t.llm_start_ms || 0)),
        tts: t.phrase_cache_hit ? 0 : Math.max(0, (t.tts_first_audio_ms || 0) - (t.tts_start_ms || 0)),
        cache: !!(t.phrase_cache_hit || t.spec_hit),
        spec: !!t.spec_hit,
      }));

  const maxMs = Math.max(1, ...latency.map((l) => l.asr + l.llm + l.tts));

  const totalLatencies = metricsHistory.map((t) => Math.max(0, (t.tts_first_audio_ms || 0) - (t.vad_start_ms || 0))).filter((v) => v > 0);

  const median = totalLatencies.length > 0
    ? [...totalLatencies].sort((a, b) => a - b)[Math.floor(totalLatencies.length / 2)]
    : 0;

  const bargeInCount = metricsHistory.filter((t) => t.barge_in_ms).length;
  const specHitCount = metricsHistory.filter((t) => t.spec_hit).length;
  const specHitPct = metricsHistory.length > 0
    ? Math.round((specHitCount / metricsHistory.length) * 100)
    : 0;
  const sparkData = totalLatencies;

  return (
    <Section
      label="Latency · live session"
      meta={
        <span>
          median <span style={{ color: "var(--fg-primary)" }}>{median} ms</span>
          {"  "}· barge-in <span style={{ color: "var(--warn)" }}>{bargeInCount}</span>
          {"  "}· spec hit <span style={{ color: "var(--accent)" }}>{specHitPct}%</span>
          <span style={{ color: "var(--accent)", marginLeft: 8 }}>· live</span>
        </span>
      }
    >
      <div className="stack-chart-wrap">
        <div className="stack-chart">
          {latency.map((l, i) => {
            const asrPct = (l.asr / maxMs) * 100;
            const llmPct = (l.llm / maxMs) * 100;
            const ttsPct = (l.tts / maxMs) * 100;
            return (
              <div key={i} className={"stack-col" + (l.cache ? " cache" : "")}>
                {l.cache && <span className="cache-mark">⚡</span>}
                {l.tts > 0 && <div className="seg seg-tts" style={{ height: `${ttsPct}%` }} />}
                {l.llm > 0 && <div className="seg seg-llm" style={{ height: `${llmPct}%` }} />}
                {l.asr > 0 && <div className="seg seg-asr" style={{ height: `${asrPct}%` }} />}
                <span className="turn-no">t{i + 1}</span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="legend" style={{ marginBottom: "var(--space-3)" }}>
        <span><span className="swatch" style={{ background: "#4B6A82" }} />ASR</span>
        <span><span className="swatch" style={{ background: "#8A7E3A" }} />LLM TTFT</span>
        <span><span className="swatch" style={{ background: "#2D6B4E" }} />TTS TTFB</span>
        <span><span className="swatch" style={{ background: "var(--accent)" }} />⚡ phrase cache hit</span>
      </div>
      <div className="dual-num">
        <MetricBlock hero label="Perceived latency" value={median} unit="ms" spark={sparkData} />
        <MetricBlock hero label="Turns" value={metricsHistory.length} unit="turns" spark={sparkData} />
      </div>
    </Section>
  );
}

function CostTokens({ state }) {
  const metricsHistory = state && state.metricsHistory;
  const hasLive = metricsHistory && metricsHistory.length > 0;

  if (!hasLive) {
    return (
      <Section label="Cost & Tokens" meta="waiting for live usage">
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--fg-secondary)" }}>
          Token and cost data appears after backend <code>metrics.turn</code> events. No mock comparison is shown during real runs.
        </p>
      </Section>
    );
  }

  const liveSes = {
    promptCached: metricsHistory.reduce((a, t) => a + (t.cached_tokens || 0), 0),
    promptUncached: metricsHistory.reduce((a, t) => a + Math.max(0, (t.prompt_tokens || 0) - (t.cached_tokens || 0)), 0),
    completion: metricsHistory.reduce((a, t) => a + (t.completion_tokens || 0), 0),
    dollars: metricsHistory.reduce((a, t) => {
      return a + (t.cached_tokens || 0) * 0.00000125
               + Math.max(0, (t.prompt_tokens || 0) - (t.cached_tokens || 0)) * 0.000005
               + (t.completion_tokens || 0) * 0.000015;
    }, 0),
  };

  const ses = liveSes;
  const fmt = (n) => n.toLocaleString();
  const fmtUSD = (n) => "$" + n.toFixed(4);

  return (
    <Section
      label="Cost & Tokens"
      meta={<span style={{ color: "var(--accent)" }}>live · {metricsHistory.length} turns</span>}
    >
      <div className="cost-cmp">
        <div className="cost-col">
          <div className="col-head"><span>This session</span><span className="badge">backend metrics</span></div>
          <div className="cost-row"><span className="k">Prompt · cached</span><span className="v" style={{ color: "var(--accent)" }}>{fmt(ses.promptCached)}</span></div>
          <div className="cost-row"><span className="k">Prompt · uncached</span><span className="v">{fmt(ses.promptUncached)}</span></div>
          <div className="cost-row"><span className="k">Completion</span><span className="v">{fmt(ses.completion)}</span></div>
          <div className="cost-row"><span className="k">$ spent</span><span className="v">{fmtUSD(ses.dollars)}</span></div>
        </div>
        <div className="cost-col">
          <div className="col-head"><span>Route mix</span><span className="badge dim">live</span></div>
          {Object.entries(metricsHistory.reduce((acc, t) => {
            const key = t.lane || "unknown";
            acc[key] = (acc[key] || 0) + 1;
            return acc;
          }, {})).map(([lane, count]) => (
            <div className="cost-row dim" key={lane}><span className="k">{lane}</span><span className="v">{count}</span></div>
          ))}
        </div>
      </div>
      {ses.promptCached > 0 && (
        <div className="savings">
          <div className="label">Cached tokens · this session</div>
          <div className="row">
            <span className="big">{fmt(ses.promptCached)}<span className="unit">tokens</span></span>
            <span className="big">{fmtUSD(ses.promptCached * 0.00000125)}</span>
          </div>
          <div className="foot">
            {ses.promptCached + ses.promptUncached > 0 ? Math.round(ses.promptCached / (ses.promptCached + ses.promptUncached) * 100) : 0}% cache hit rate
          </div>
        </div>
      )}
    </Section>
  );
}

function PostCallReport({ state, setState }) {
  const {
    calling,
    sessionId,
    postCallReport,
    memorySuggestions,
    postCallLoading,
    postCallError,
  } = state;
  const dimmed = calling;
  const report = postCallReport;

  async function decideSuggestion(id, decision) {
    try {
      const response = await fetch(`${API_BASE}/memory-suggestions/${id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, decision }),
      });
      if (!response.ok) throw new Error(`decision failed: ${response.status}`);
      setState((s) => ({
        ...s,
        memorySuggestions: s.memorySuggestions.map((item) =>
          item.id === id ? { ...item, status: decision } : item
        ),
      }));
    } catch (err) {
      setState((s) => ({ ...s, postCallError: err.message || "Failed to update memory suggestion" }));
    }
  }

  return (
    <React.Fragment>
      {(dimmed || postCallLoading) && (
        <div className="status-banner">
          <span className="spinner" />
          <span>Analyzing call…</span>
          <span style={{ marginLeft: "auto", color: "var(--fg-tertiary)" }}>backend post-call report</span>
        </div>
      )}
      {postCallError && (
        <div className="status-banner" style={{ color: "var(--warn)" }}>
          <span>{postCallError}</span>
        </div>
      )}
      {!report && !postCallLoading && (
        <Section label="Post-Call Report" meta="no report loaded">
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--fg-secondary)" }}>
            End a call to generate a backend post-call report. This panel only renders backend data.
          </p>
        </Section>
      )}
      {report && (
      <div className={dimmed ? "report-dimmed" : ""}>
        <Section label="Summary" meta={`outcome · ${report.outcome || "unknown"}`}>
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--fg-primary)" }}>{report.summary}</p>
        </Section>
        <Section label="Sentiment" meta={report.sentiment_evidence?.length ? `${report.sentiment_evidence.length} evidence items` : "no evidence"}>
          <div className="pill-chain">
            <span className={"pill " + (report.user_sentiment || "neutral")}>{report.user_sentiment || "neutral"}</span>
          </div>
          <div className="tags-list" style={{ marginTop: 10 }}>
            {(report.sentiment_evidence || []).map((item, i) => (
              <div key={i} className="tag-item"><span className="tag">evidence</span><span className="text">{item}</span></div>
            ))}
          </div>
        </Section>
        <Section label={`Action items · ${(report.action_items || []).length}`}>
          <div className="tags-list">
            {(report.action_items || []).map((item, i) => (
              <div key={i} className="tag-item issue"><span className="tag">todo</span><span className="text">{item}</span></div>
            ))}
            {(report.action_items || []).length === 0 && <div className="tag-item"><span className="text">No action items generated.</span></div>}
          </div>
        </Section>
        <Section label={`Quality flags · ${(report.quality_flags || []).length}`}>
          <div className="tags-list">
            {(report.quality_flags || []).map((flag, i) => (
              <div key={i} className="tag-item issue"><span className="tag">flag</span><span className="text">{flag}</span></div>
            ))}
            {(report.quality_flags || []).length === 0 && <div className="tag-item win"><span className="text">No quality flags generated.</span></div>}
          </div>
        </Section>
        <Section label={`Memory suggestions · ${(memorySuggestions || []).length}`} meta="review required">
          <div className="memo-list">
            {(memorySuggestions || []).map((m) => (
              <div className="memo-row" key={m.id}>
                <span className={"op " + (m.status === "accepted" ? "update" : "")}>{m.status || "pending"}</span>
                <span className="text">{m.text}</span>
                {m.status === "pending" && (
                  <React.Fragment>
                    <button className="accept" type="button" onClick={() => decideSuggestion(m.id, "accepted")}>accept</button>
                    <button className="accept" type="button" onClick={() => decideSuggestion(m.id, "rejected")}>reject</button>
                  </React.Fragment>
                )}
              </div>
            ))}
            {(memorySuggestions || []).length === 0 && <div className="memo-row"><span className="text">No memory suggestions generated.</span></div>}
          </div>
        </Section>
      </div>
      )}
    </React.Fragment>
  );
}
