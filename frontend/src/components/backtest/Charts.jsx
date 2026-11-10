// Hand-written inline SVG charts for backtest results. No chart library.
import { useState } from 'react';
import { pct, usd } from '../common.jsx';

const W = 720;
const H = 280;
const M = { l: 70, r: 14, t: 12, b: 26 };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const money0 = (v) => `$${Math.round(v).toLocaleString('en-US')}`;

function scaleX(n) {
  return (i) => M.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - M.l - M.r));
}
function ticks(lo, hi, count = 4) {
  return Array.from({ length: count + 1 }, (_, i) => lo + ((hi - lo) * i) / count);
}
function path(points, sx, sy) {
  return points.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
}
function dateLabels(rows, sx, y) {
  const mid = Math.floor((rows.length - 1) / 2);
  return [[0, 'start'], [mid, 'middle'], [rows.length - 1, 'end']].map(([i, anchor]) => (
    <text key={anchor} x={sx(i)} y={y} className="bt-axis-label" textAnchor={anchor}>{rows[i].date}</text>
  ));
}

function useHover(n) {
  const [idx, setIdx] = useState(null);
  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    if (!rect.width || n < 1) return;
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((x - M.l) / (W - M.l - M.r)) * (n - 1));
    setIdx(Math.max(0, Math.min(n - 1, i)));
  };
  return { idx, onMove, onLeave: () => setIdx(null) };
}

export function EquityChart({ equity, benchmarkName }) {
  const n = equity.length;
  const sx = scaleX(n);
  const hover = useHover(n);
  if (n < 2) return <p className="muted">Not enough equity points to chart.</p>;
  const hasBench = equity.some((r) => r.benchmark !== null && r.benchmark !== undefined);
  const vals = equity.flatMap((r) => [r.equity, hasBench ? r.benchmark ?? r.equity : r.equity]);
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (hi === lo) { hi += 1; lo -= 1; }
  const padV = (hi - lo) * 0.05;
  lo -= padV;
  hi += padV;
  const sy = (v) => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
  const row = hover.idx === null ? null : equity[hover.idx];
  const last = equity[n - 1];
  const label = `Equity curve from ${equity[0].date} to ${last.date}: strategy ends at ${money0(last.equity)}`
    + (hasBench && last.benchmark != null ? ` versus ${money0(last.benchmark)} for ${benchmarkName || 'buy and hold'}.` : '.');
  return (
    <figure className="bt-figure">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart bt-chart" role="img" aria-label={label}
        onMouseMove={hover.onMove} onMouseLeave={hover.onLeave}>
        {ticks(lo, hi).map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} className="bt-grid" />
            <text x={M.l - 6} y={sy(t) + 4} textAnchor="end" className="bt-axis-label">{money0(t)}</text>
          </g>
        ))}
        {hasBench ? <path d={path(equity.map((r) => r.benchmark ?? r.equity), sx, sy)} className="bt-line bt-line-bench" fill="none" /> : null}
        <path d={path(equity.map((r) => r.equity), sx, sy)} className="bt-line bt-line-strategy" fill="none" />
        {dateLabels(equity, sx, H - 8)}
        {row ? (
          <g pointerEvents="none">
            <line x1={sx(hover.idx)} x2={sx(hover.idx)} y1={M.t} y2={H - M.b} className="bt-cursor" />
            <circle cx={sx(hover.idx)} cy={sy(row.equity)} r="4" className="bt-dot-strategy" />
            <text x={Math.min(Math.max(sx(hover.idx), M.l + 110), W - 110)} y={M.t + 12} textAnchor="middle"
              className="bt-tip-text" data-testid="equity-tip">
              {row.date} · {usd(row.equity)}{hasBench && row.benchmark != null ? ` · bench ${usd(row.benchmark)}` : ''}
            </text>
          </g>
        ) : null}
      </svg>
      <figcaption className="bt-legend">
        <span><i className="bt-swatch bt-swatch-strategy" /> Strategy</span>
        {hasBench ? <span><i className="bt-swatch bt-swatch-bench" /> {benchmarkName || 'Buy and hold'}</span> : null}
      </figcaption>
    </figure>
  );
}

export function DrawdownChart({ equity }) {
  const n = equity.length;
  const sx = scaleX(n);
  const hover = useHover(n);
  if (n < 2) return null;
  const h = H - 40;
  const dd = equity.map((r) => Number(r.drawdown) || 0);
  const maxDd = Math.max(...dd, 0.001);
  const sy = (v) => M.t + (v / maxDd) * (h - M.t - M.b);
  const base = sy(0);
  const area = `M${sx(0)},${base} ${dd.map((v, i) => `L${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')} L${sx(n - 1)},${base} Z`;
  const i = hover.idx;
  return (
    <figure className="bt-figure">
      <svg viewBox={`0 0 ${W} ${h}`} className="chart bt-chart" role="img"
        aria-label={`Drawdown from the running peak, deepest ${pct(maxDd)}.`}
        onMouseMove={hover.onMove} onMouseLeave={hover.onLeave}>
        {ticks(0, maxDd, 3).map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} className="bt-grid" />
            <text x={M.l - 6} y={sy(t) + 4} textAnchor="end" className="bt-axis-label">{t === 0 ? '0%' : `-${(t * 100).toFixed(1)}%`}</text>
          </g>
        ))}
        <path d={area} className="bt-area" />
        <path d={path(dd, sx, sy)} className="bt-line bt-line-dd" fill="none" />
        {dateLabels(equity, sx, h - 8)}
        {i !== null ? (
          <text x={Math.min(Math.max(sx(i), M.l + 80), W - 80)} y={M.t + 12} textAnchor="middle" className="bt-tip-text">
            {equity[i].date} · -{pct(dd[i])}
          </text>
        ) : null}
      </svg>
    </figure>
  );
}

export function MonthlyHeatmap({ rows }) {
  if (!rows || !rows.length) return <p className="muted">No monthly returns.</p>;
  const byKey = new Map(rows.map((r) => [r.month, r.return]));
  const years = [...new Set(rows.map((r) => r.month.slice(0, 4)))].sort();
  const maxAbs = Math.max(0.0001, ...rows.map((r) => Math.abs(r.return ?? 0)));
  const bg = (v) => {
    if (v === null || v === undefined) return undefined;
    const a = 0.12 + 0.68 * Math.min(1, Math.abs(v) / maxAbs);
    return v >= 0 ? `rgba(34, 197, 94, ${a.toFixed(2)})` : `rgba(239, 68, 68, ${a.toFixed(2)})`;
  };
  return (
    <div className="table-wrap">
      <table className="bt-heat">
        <caption className="muted">Monthly returns by year</caption>
        <thead>
          <tr><th scope="col">Year</th>{MONTHS.map((m) => <th key={m} scope="col">{m}</th>)}</tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <th scope="row">{y}</th>
              {MONTHS.map((m, i) => {
                const v = byKey.get(`${y}-${String(i + 1).padStart(2, '0')}`);
                return (
                  <td key={m} style={{ background: bg(v) }} title={v == null ? '' : `${m} ${y}: ${pct(v, 2)}`}>
                    {v == null ? '' : pct(v, 1)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ExitBars({ reasons }) {
  const entries = Object.entries(reasons || {}).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <p className="muted">No exits.</p>;
  const max = Math.max(...entries.map(([, c]) => c));
  const total = entries.reduce((s, [, c]) => s + c, 0);
  return (
    <ul className="bt-bars" aria-label="Exit reasons">
      {entries.map(([k, c]) => (
        <li key={k}>
          <span className="bt-bar-label">{k.replace(/_/g, ' ')}</span>
          <span className="bt-bar-track" aria-hidden="true"><span className="bt-bar-fill" style={{ width: `${(c / max) * 100}%` }} /></span>
          <span className="bt-bar-count">{c} <span className="muted">({((c / total) * 100).toFixed(0)}%)</span></span>
        </li>
      ))}
    </ul>
  );
}
