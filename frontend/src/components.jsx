import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';

/* -------- Panel header -------- */
export function PanelHeader({ label, right, children }) {
  return (
    <div className="panel-head">
      <div className="label">{label}</div>
      <div>{right || children}</div>
    </div>
  );
}

/* -------- Hard rectangular toggle -------- */
export function Toggle({ label, on, onClick }) {
  return (
    <button className={"toggle" + (on ? " on" : "")} onClick={onClick} type="button">
      <span className="lbl">{label}</span>
      <span className="state">
        <span className="dot" />
        {on ? "ON" : "OFF"}
      </span>
    </button>
  );
}

/* -------- Provider toggle (with dropdown caret) -------- */
export function ProviderToggle({ label, value, options, onChange }) {
  return (
    <label className="toggle on" style={{ cursor: "pointer", paddingRight: 0, position: "relative" }}>
      <span className="lbl">{label}:</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          appearance: "none",
          WebkitAppearance: "none",
          background: "transparent",
          border: 0,
          color: "var(--accent)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          paddingRight: 16,
          outline: "none",
          backgroundImage:
            "linear-gradient(45deg, transparent 50%, var(--accent) 50%), linear-gradient(135deg, var(--accent) 50%, transparent 50%)",
          backgroundPosition: "calc(100% - 8px) 8px, calc(100% - 4px) 8px",
          backgroundSize: "4px 4px, 4px 4px",
          backgroundRepeat: "no-repeat",
          cursor: "pointer",
        }}
      >
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}

/* -------- Sparkline -------- */
export function Sparkline({ data, width = 60, height = 18, color = "var(--accent)" }) {
  if (!data || data.length === 0) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1 || 1);
  const points = data.map((v, i) => [i * stepX, height - 2 - ((v - min) / range) * (height - 4)]);
  const d = points.map((p, i) => (i === 0 ? `M${p[0]},${p[1]}` : `L${p[0]},${p[1]}`)).join(" ");
  const last = points[points.length - 1];
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <path d={d} fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="2" fill={color} />
    </svg>
  );
}

/* -------- Metric block -------- */
export function MetricBlock({ label, value, unit, frac, delta, vs, hero, spark, sparkColor, flash }) {
  return (
    <div className={"metric" + (hero ? " hero" : "") + (flash ? " flashing" : "")}>
      <div className="label">{label}</div>
      <div className="value">
        <span>{value}</span>
        {frac && <span className="frac">/ {frac}</span>}
        {unit && <span className="unit">{unit}</span>}
        {spark && (
          <span style={{ marginLeft: "auto", paddingLeft: 8 }}>
            <Sparkline data={spark} color={sparkColor || "var(--accent)"} />
          </span>
        )}
      </div>
      {(delta || vs) && (
        <div className={"delta" + (delta?.good ? " good" : "") + (delta?.bad ? " bad" : "")}>
          {delta && (
            <span>
              <span className="arrow">{delta.arrow}</span>{" "}
              <span className="pct">{delta.pct}</span>
            </span>
          )}
          {vs && <span className="vs">{vs}</span>}
        </div>
      )}
    </div>
  );
}

/* -------- Section in right pane -------- */
export function Section({ label, meta, children }) {
  return (
    <div className="section">
      <div className="section-head">
        <div className="label">{label}</div>
        {meta && <div className="meta">{meta}</div>}
      </div>
      <div className="section-body">{children}</div>
    </div>
  );
}
