import React, { useState, useRef, useMemo } from 'react';
import { Section, MetricBlock, Sparkline } from './components.jsx';
import { MOCK_DATA } from './data.js';

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
        {activeTab === "report" && <PostCallReport calling={calling} />}
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

  let segs, totalMs, specHit;
  if (latestTurn && latestTurn.vad_start_ms != null && latestTurn.tts_first_audio_ms) {
    const asrMs = Math.max(0, (latestTurn.asr_final_ms || 0) - latestTurn.vad_start_ms);
    const fillerMs = latestTurn.filler_played
      ? Math.max(0, (latestTurn.llm_start_ms || 0) - (latestTurn.asr_final_ms || 0))
      : 0;
    const llmMs = Math.max(0, (latestTurn.llm_first_token_ms || 0) - (latestTurn.llm_start_ms || 0));
    const ttsMs = latestTurn.phrase_cache_hit
      ? 0
      : Math.max(0, (latestTurn.tts_first_audio_ms || 0) - (latestTurn.tts_start_ms || 0));
    totalMs = Math.max(1, (latestTurn.tts_first_audio_ms || 0) - latestTurn.vad_start_ms);
    specHit = latestTurn.spec_hit;
    segs = [];
    if (asrMs > 0) segs.push({ kind: "asr", label: "ASR", ms: asrMs, prov: latestTurn.asr_provider || "" });
    if (fillerMs > 0) segs.push({ kind: "filler", label: "Filler", ms: fillerMs });
    if (llmMs > 0) segs.push({ kind: "llm", label: "LLM TTFT", ms: llmMs, prov: latestTurn.model || "gpt-4o" });
    if (ttsMs > 0) segs.push({ kind: "tts", label: latestTurn.phrase_cache_hit ? "TTS·cached" : "TTS TTFB", ms: ttsMs, prov: latestTurn.tts_provider || "" });
    if (segs.length === 0) {
      segs = MOCK_DATA.pipeline.segs;
      totalMs = MOCK_DATA.pipeline.totalMs;
    }
  } else {
    segs = MOCK_DATA.pipeline.segs;
    totalMs = MOCK_DATA.pipeline.totalMs;
    specHit = MOCK_DATA.pipeline.speculative;
  }

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
      </div>
    </Section>
  );
}

function LatencyStats({ state }) {
  const metricsHistory = state && state.metricsHistory;
  const hasLive = metricsHistory && metricsHistory.length > 0;

  const latency = hasLive
    ? metricsHistory.map((t) => ({
        asr: Math.max(0, (t.asr_final_ms || 0) - (t.vad_start_ms || 0)),
        llm: Math.max(0, (t.llm_first_token_ms || 0) - (t.llm_start_ms || 0)),
        tts: t.phrase_cache_hit ? 0 : Math.max(0, (t.tts_first_audio_ms || 0) - (t.tts_start_ms || 0)),
        cache: !!(t.phrase_cache_hit || t.spec_hit),
        spec: !!t.spec_hit,
      }))
    : MOCK_DATA.latency;

  const maxMs = Math.max(1, ...latency.map((l) => l.asr + l.llm + l.tts));

  const totalLatencies = hasLive
    ? metricsHistory.map((t) => Math.max(0, (t.tts_first_audio_ms || 0) - (t.vad_start_ms || 0))).filter((v) => v > 0)
    : [820, 760, 740, 690, 710, 680, 650, 620, 640, 610, 590, 580];

  const median = totalLatencies.length > 0
    ? [...totalLatencies].sort((a, b) => a - b)[Math.floor(totalLatencies.length / 2)]
    : 0;

  const bargeInCount = hasLive ? metricsHistory.filter((t) => t.barge_in_ms).length : 1;
  const specHitCount = hasLive ? metricsHistory.filter((t) => t.spec_hit).length : 0;
  const specHitPct = hasLive && metricsHistory.length > 0
    ? Math.round((specHitCount / metricsHistory.length) * 100)
    : 71;
  const sparkData = hasLive ? totalLatencies : MOCK_DATA.spark.perceived;

  return (
    <Section
      label="Latency · live session"
      meta={
        <span>
          median <span style={{ color: "var(--fg-primary)" }}>{hasLive ? median : 610} ms</span>
          {"  "}· barge-in <span style={{ color: "var(--warn)" }}>{bargeInCount}</span>
          {"  "}· spec hit <span style={{ color: "var(--accent)" }}>{specHitPct}%</span>
          {hasLive && <span style={{ color: "var(--accent)", marginLeft: 8 }}>· live</span>}
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
        <MetricBlock hero label="Perceived latency" value={hasLive ? median : 590} unit="ms" spark={sparkData} />
        <MetricBlock hero label="Turns" value={hasLive ? metricsHistory.length : "—"} unit={hasLive ? "turns" : ""} spark={sparkData} />
      </div>
    </Section>
  );
}

function LiveCostChart() {
  const { costTurns, costPrevTurns } = MOCK_DATA;
  const CACHED_RATE = 0.000001250;
  const UNCACHED_RATE = 0.000005000;
  const COMPLETION_RATE = 0.000015000;

  const turnCost = (t) =>
    t.promptCached * CACHED_RATE + t.promptUncached * UNCACHED_RATE + t.completion * COMPLETION_RATE + t.tts + t.asr;
  const segCosts = (t) => ({
    cached: t.promptCached * CACHED_RATE,
    uncached: t.promptUncached * UNCACHED_RATE,
    completion: t.completion * COMPLETION_RATE,
    tts: t.phraseCache ? 0 : t.tts,
    asr: t.asr,
  });

  const allCosts = costTurns.map(turnCost);
  const maxCost = Math.max(...allCosts, ...costPrevTurns.map(turnCost));
  const thisTotalUSD = allCosts.reduce((a, b) => a + b, 0);
  const prevTotalUSD = costPrevTurns.map(turnCost).reduce((a, b) => a + b, 0);
  const fmtUSD = (n) => "$" + n.toFixed(4);

  const yTicks = useMemo(() => {
    const step = maxCost / 3;
    return [step, step * 2, step * 3].map((v) => ({ val: v, pct: (v / maxCost) * 100 }));
  }, [maxCost]);

  const prevCumulative = useMemo(() => {
    let acc = 0;
    return costPrevTurns.map((t) => { acc += turnCost(t); return acc; });
  }, []);

  const CHART_COLS = costTurns.length;

  return (
    <div className="cost-chart-wrap">
      <div className="cost-chart-header">
        <span className="cost-sess-this">This session: <span className="cost-sess-val">{fmtUSD(thisTotalUSD)}</span></span>
        <span className="spacer" />
        <span className="cost-sess-prev">Previous session: <span className="cost-sess-val-dim">{fmtUSD(prevTotalUSD)}</span></span>
      </div>
      <div className="cost-chart-outer">
        <div className="cost-yaxis">
          {[...yTicks].reverse().map((tick, i) => (
            <span key={i} className="cost-ytick" style={{ bottom: `${tick.pct}%` }}>
              {tick.val < 0.001 ? (tick.val * 1000).toFixed(3) + "m" : fmtUSD(tick.val)}
            </span>
          ))}
        </div>
        <div className="cost-chart-area">
          <div className="cost-chart">
            {costTurns.map((t, i) => {
              const segs = segCosts(t);
              const total = turnCost(t);
              const totalPct = (total / maxCost) * 100;
              const cachedPct = (segs.cached / total) * totalPct;
              const uncachedPct = (segs.uncached / total) * totalPct;
              const completionPct = (segs.completion / total) * totalPct;
              const ttsPct = (segs.tts / total) * totalPct;
              const asrPct = (segs.asr / total) * totalPct;
              return (
                <div key={i} className="cost-chart-col" style={{ height: `${totalPct}%` }}>
                  {t.phraseCache && <span className="cost-col-flash" title="Phrase cache hit — TTS skipped">⚡</span>}
                  {t.bargeIn && <div className="cost-barge-notch" title="Barge-in on this turn" />}
                  <div className="seg-cost-asr" style={{ flex: `${asrPct} 0 0` }} />
                  <div className={`seg-cost-completion${t.specHit ? " spec-glow" : ""}`} style={{ flex: `${completionPct} 0 0` }} />
                  {!t.phraseCache && segs.tts > 0 && <div className="seg-cost-tts" style={{ flex: `${ttsPct} 0 0` }} />}
                  <div className="seg-cost-uncached" style={{ flex: `${uncachedPct} 0 0` }} />
                  <div className="seg-cost-cached" style={{ flex: `${cachedPct} 0 0` }} />
                </div>
              );
            })}
          </div>
          <svg className="cost-prev-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline
              points={prevCumulative.map((v, i) => {
                const x = ((i + 0.5) / CHART_COLS) * 100;
                const y = 100 - (v / (maxCost * CHART_COLS)) * 100 * 0.85;
                return `${x},${y}`;
              }).join(" ")}
              fill="none" stroke="var(--fg-tertiary)" strokeWidth="0.8" strokeDasharray="3,2" opacity="0.5"
            />
          </svg>
        </div>
      </div>
      <div className="cost-xaxis">
        {costTurns.map((_, i) => <span key={i} className="cost-xtick">t{i + 1}</span>)}
      </div>
      <div className="legend" style={{ marginTop: "var(--space-3)" }}>
        <span><span className="swatch" style={{ background: "#1A3D28" }} />LLM cached</span>
        <span><span className="swatch" style={{ background: "#2A3A50" }} />LLM uncached</span>
        <span><span className="swatch" style={{ background: "#4A3E1A" }} />Completion</span>
        <span><span className="swatch" style={{ background: "#3A2A50" }} />TTS</span>
        <span><span className="swatch" style={{ background: "#1A3A3A" }} />ASR</span>
        <span>⚡ phrase cache hit</span>
      </div>
    </div>
  );
}

function CostTokens({ state }) {
  const metricsHistory = state && state.metricsHistory;
  const hasLive = metricsHistory && metricsHistory.length > 0;
  const { cost } = MOCK_DATA;
  const mockSes = cost.this;
  const prev = cost.prev;
  const savings = cost.savings;

  const liveSes = hasLive ? {
    promptCached: metricsHistory.reduce((a, t) => a + (t.cached_tokens || 0), 0),
    promptUncached: metricsHistory.reduce((a, t) => a + Math.max(0, (t.prompt_tokens || 0) - (t.cached_tokens || 0)), 0),
    completion: metricsHistory.reduce((a, t) => a + (t.completion_tokens || 0), 0),
    dollars: metricsHistory.reduce((a, t) => {
      return a + (t.cached_tokens || 0) * 0.00000125
               + Math.max(0, (t.prompt_tokens || 0) - (t.cached_tokens || 0)) * 0.000005
               + (t.completion_tokens || 0) * 0.000015;
    }, 0),
  } : null;

  const ses = liveSes || mockSes;
  const fmt = (n) => n.toLocaleString();
  const fmtUSD = (n) => "$" + n.toFixed(4);

  return (
    <Section
      label="Cost & Tokens"
      meta={
        hasLive
          ? <span style={{ color: "var(--accent)" }}>live · {metricsHistory.length} turns</span>
          : <span>A: cache <span style={{ color: "var(--accent)" }}>ON</span>{"  "}· B: cache <span style={{ color: "var(--fg-secondary)" }}>OFF</span></span>
      }
    >
      <div className="cost-cmp">
        <div className="cost-col">
          <div className="col-head"><span>This session</span><span className="badge">cache · on</span></div>
          <div className="cost-row"><span className="k">Prompt · cached</span><span className="v" style={{ color: "var(--accent)" }}>{fmt(ses.promptCached)}</span></div>
          <div className="cost-row"><span className="k">Prompt · uncached</span><span className="v">{fmt(ses.promptUncached)}</span></div>
          <div className="cost-row"><span className="k">Completion</span><span className="v">{fmt(ses.completion)}</span></div>
          <div className="cost-row"><span className="k">$ spent</span><span className="v">{fmtUSD(ses.dollars)}</span></div>
        </div>
        <div className="sep" />
        <div className="cost-col">
          <div className="col-head"><span>Previous session</span><span className="badge dim">cache · off</span></div>
          <div className="cost-row dim"><span className="k">Prompt · cached</span><span className="v"><span className="sub">{fmt(prev.promptCached)}</span></span></div>
          <div className="cost-row dim"><span className="k">Prompt · uncached</span><span className="v">{fmt(prev.promptUncached)}</span></div>
          <div className="cost-row dim"><span className="k">Completion</span><span className="v">{fmt(prev.completion)}</span></div>
          <div className="cost-row dim"><span className="k">$ spent</span><span className="v">{fmtUSD(prev.dollars)}</span></div>
        </div>
      </div>
      <LiveCostChart />
      {!hasLive && (
        <div className="savings">
          <div className="label">Saved by caching · this session</div>
          <div className="row">
            <span className="big">{fmt(savings.tokens)}<span className="unit">tokens</span></span>
            <span className="big">{fmtUSD(savings.dollars)}</span>
          </div>
          <div className="foot">
            76.4% prompt-token cache hit · projected ${(savings.dollars * 30 * 30).toFixed(2)}/mo at 30 calls/day
          </div>
        </div>
      )}
      {hasLive && ses.promptCached > 0 && (
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

function PostCallReport({ calling }) {
  const { postCall } = MOCK_DATA;
  const dimmed = calling;
  return (
    <React.Fragment>
      {dimmed && (
        <div className="status-banner">
          <span className="spinner" />
          <span>Analyzing call…</span>
          <span style={{ marginLeft: "auto", color: "var(--fg-tertiary)" }}>populates on End Call</span>
        </div>
      )}
      <div className={dimmed ? "report-dimmed" : ""}>
        <Section label="Summary" meta="2 sentences · 1 LLM call · 312 ms">
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--fg-primary)" }}>{postCall.summary}</p>
        </Section>
        <Section label="Sentiment arc · 3 segments">
          <div className="pill-chain">
            {postCall.sentiment.map((p, i) => (
              <React.Fragment key={i}>
                <span className={"pill " + p}>{p}</span>
                {i < postCall.sentiment.length - 1 && <span className="pill-arrow">›</span>}
              </React.Fragment>
            ))}
          </div>
        </Section>
        <Section label="Talk ratio" meta="9 turns · 34s user · 56s agent">
          <div className="ratio-bars">
            <div className="ratio-row user"><span className="who">User</span><div className="bar"><div className="bar-fill" style={{ width: postCall.talk.user + "%" }} /></div><span className="pct">{postCall.talk.user}%</span></div>
            <div className="ratio-row agent"><span className="who">Agent</span><div className="bar"><div className="bar-fill" style={{ width: postCall.talk.agent + "%" }} /></div><span className="pct">{postCall.talk.agent}%</span></div>
          </div>
        </Section>
        <Section label={`Wins · ${postCall.wins.length}`}>
          <div className="tags-list">
            {postCall.wins.map((w, i) => (
              <div key={i} className="tag-item win"><span className="tag">win</span><span className="text">{w.text}</span><span className="meta">{w.meta}</span></div>
            ))}
          </div>
        </Section>
        <Section label={`Issues · ${postCall.issues.length}`}>
          <div className="tags-list">
            {postCall.issues.map((w, i) => (
              <div key={i} className="tag-item issue"><span className="tag">issue</span><span className="text">{w.text}</span><span className="meta">{w.meta}</span></div>
            ))}
          </div>
        </Section>
        <RecordingSection />
        <Section label={`Memory suggestions · ${postCall.memorySuggestions.length}`} meta="apply to user profile">
          <div className="memo-list">
            {postCall.memorySuggestions.map((m, i) => (
              <div className="memo-row" key={i}>
                <span className={"op " + (m.op === "update" ? "update" : "")}>{m.op}</span>
                <span className="text">{m.text}</span>
                <button className="accept" type="button">accept ▸</button>
              </div>
            ))}
          </div>
        </Section>
      </div>
    </React.Fragment>
  );
}

function RecordingSection() {
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0.32);
  const waveRef = useRef(null);
  const bars = useMemo(() => {
    const seed = 17; const N = 240; const out = [];
    for (let i = 0; i < N; i++) {
      const x = i / N;
      const env = 0.6 + 0.4 * Math.sin(x * Math.PI * 2.3);
      const noise = Math.sin(i * 13.37 + seed) * 0.5 + Math.sin(i * 2.7 + seed) * 0.5;
      out.push(Math.max(0.05, Math.min(1, Math.abs(noise) * env)));
    }
    return out;
  }, []);
  const handleClick = (e) => {
    if (!waveRef.current) return;
    const r = waveRef.current.getBoundingClientRect();
    setPos(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)));
  };
  return (
    <Section label="Recording · call.wav" meta="01:34 · 16 kHz · 1.8 MB">
      <div className="waveform" ref={waveRef} onClick={handleClick} style={{ cursor: "pointer" }}>
        <svg className="wave-svg" viewBox={`0 0 ${bars.length} 60`} preserveAspectRatio="none">
          {bars.map((v, i) => {
            const h = v * 50; const y = 30 - h / 2; const past = i / bars.length < pos;
            return <rect key={i} x={i + 0.2} y={y} width="0.6" height={h} fill={past ? "var(--accent)" : "var(--fg-tertiary)"} opacity={past ? 0.85 : 0.5} />;
          })}
        </svg>
        <div className="wave-cursor" style={{ left: `${pos * 100}%` }} />
      </div>
      <div className="player">
        <button className="player-btn" onClick={() => setPlaying((p) => !p)} type="button">
          {playing
            ? <span style={{ width: 8, height: 8, background: "var(--fg-primary)" }} />
            : <span style={{ width: 0, height: 0, borderStyle: "solid", borderWidth: "4px 0 4px 6px", borderColor: "transparent transparent transparent var(--fg-primary)" }} />}
        </button>
        <span className="player-time">{Math.floor(pos * 94).toString().padStart(2, "0")}:{(Math.floor(pos * 94 * 10) % 10).toString()}{Math.floor((pos*94 % 1)*10)}{" / 01:34"}</span>
        <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg-tertiary)" }}>L=user · R=agent · download ↓</span>
      </div>
    </Section>
  );
}
