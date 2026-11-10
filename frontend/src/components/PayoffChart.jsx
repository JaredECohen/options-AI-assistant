import { useId } from 'react';

// Expiration payoff chart: profit area above zero, loss area below, with the current
// price, an optional target and the breakevens marked. Pure SVG, no dependencies.
// Exact expiration P&L of option/stock legs at price S (premiums per share, 100 shares/contract).
export function legsPayoff(legs, S) {
  return legs.reduce((sum, l) => {
    const prem = Number(l.premium) || 0;
    if (l.instrument === 'stock') return sum + (l.side === 'buy' ? S - prem : prem - S) * Number(l.quantity || 0);
    const k = Number(l.strike);
    const intrinsic = l.option_type === 'call' ? Math.max(S - k, 0) : Math.max(k - S, 0);
    const units = Number(l.quantity || 1) * Number(l.multiplier || 100);
    return sum + (l.side === 'buy' ? intrinsic - prem : prem - intrinsic) * units;
  }, 0);
}

// Points over [lo, hi] including every strike, so kinks are exact rather than sampled.
export function curveFromLegs(legs, lo, hi, steps = 120) {
  const xs = new Set();
  for (let i = 0; i <= steps; i += 1) xs.add(lo + ((hi - lo) * i) / steps);
  legs.forEach((l) => { if (l.strike >= lo && l.strike <= hi) xs.add(Number(l.strike)); });
  return [...xs].sort((a, b) => a - b).map((price) => ({ price, payoff: legsPayoff(legs, price) }));
}

const fmtUsd = (v) => `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export default function PayoffChart({ curve: given, legs, domain, spot, target, breakevens = [], height = 200, compact = false }) {
  const uid = useId().replace(/:/g, '');
  const curve = legs && domain ? curveFromLegs(legs, domain[0], domain[1]) : given;
  if (!curve || curve.length < 2) return null;
  const W = 560;
  const H = height;
  const padL = compact ? 8 : 56;
  const padR = 12;
  const padT = 14;
  const padB = compact ? 18 : 28;
  const xs = curve.map((p) => p.price);
  const ys = curve.map((p) => p.payoff);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(0, ...ys);
  const y1 = Math.max(0, ...ys);
  const ySpan = y1 - y0 || 1;
  const sx = (v) => padL + ((v - x0) / (x1 - x0 || 1)) * (W - padL - padR);
  const sy = (v) => padT + (1 - (v - y0) / ySpan) * (H - padT - padB);
  const zero = sy(0);
  const line = curve.map((p, i) => `${i ? 'L' : 'M'}${sx(p.price).toFixed(1)},${sy(p.payoff).toFixed(1)}`).join(' ');
  const area = `${line} L${sx(x1).toFixed(1)},${zero.toFixed(1)} L${sx(x0).toFixed(1)},${zero.toFixed(1)} Z`;
  const inRange = (v) => v != null && v >= x0 && v <= x1;
  const label = `Payoff at expiration from ${fmtUsd(x0)} to ${fmtUsd(x1)} underlying: max ${fmtUsd(y1)}, min ${fmtUsd(y0)}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="payoff-chart" role="img" aria-label={label}>
      <defs>
        <clipPath id={`${uid}-a`}><rect x="0" y="0" width={W} height={zero} /></clipPath>
        <clipPath id={`${uid}-b`}><rect x="0" y={zero} width={W} height={H - zero} /></clipPath>
      </defs>
      <path d={area} className="pc-profit" clipPath={`url(#${uid}-a)`} />
      <path d={area} className="pc-loss" clipPath={`url(#${uid}-b)`} />
      <line x1={padL} x2={W - padR} y1={zero} y2={zero} className="pc-zero" />
      {inRange(spot) ? (
        <g><line x1={sx(spot)} x2={sx(spot)} y1={padT} y2={H - padB} className="pc-spot" />
          {!compact ? <text x={sx(spot) + 4} y={padT + 10} className="pc-label">now {fmtUsd(spot)}</text> : null}</g>
      ) : null}
      {inRange(target) && Math.abs(target - spot) > (x1 - x0) * 0.01 ? (
        <g><line x1={sx(target)} x2={sx(target)} y1={padT} y2={H - padB} className="pc-target" />
          {!compact ? <text x={sx(target) + 4} y={padT + 22} className="pc-label pc-label-target">target {fmtUsd(target)}</text> : null}</g>
      ) : null}
      {breakevens.filter(inRange).map((b) => <circle key={b} cx={sx(b)} cy={zero} r={3.5} className="pc-be" />)}
      <path d={line} className="pc-line" fill="none" />
      {!compact ? (
        <g className="pc-axis">
          <text x={padL - 6} y={sy(y1) + 4} textAnchor="end">{fmtUsd(y1)}</text>
          {Math.abs(zero - sy(y1)) > 14 && Math.abs(zero - sy(y0)) > 14 ? <text x={padL - 6} y={zero + 4} textAnchor="end">$0</text> : null}
          {y0 < 0 ? <text x={padL - 6} y={sy(y0) + 4} textAnchor="end">{fmtUsd(y0)}</text> : null}
          <text x={padL} y={H - 8}>{fmtUsd(x0)}</text>
          <text x={W - padR} y={H - 8} textAnchor="end">{fmtUsd(x1)}</text>
        </g>
      ) : null}
    </svg>
  );
}
