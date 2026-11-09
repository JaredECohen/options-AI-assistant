// Shared state rendering (loading / error / empty), data-status badge and formatters.
import { errorMessage } from '../api.js';

const TONE = { live: 'ok', delayed: 'warn', eod: 'warn', synthetic: 'danger' };

// Never labels anything "Real-time" unless the server reports status "live".
export function DataStatus({ status, label, provider, freshness }) {
  if (!status) return null;
  const stale = freshness && freshness.stale === true;
  return (
    <span className={`status status-${TONE[status] || 'warn'}`} data-testid="data-status">
      {label || status}
      {provider ? ` · ${provider}` : ''}
      {freshness && freshness.as_of ? ` · as of ${new Date(freshness.as_of).toLocaleString()}` : ''}
      {stale ? ' · STALE' : ''}
    </span>
  );
}

export function Loading({ label = 'Loading…' }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}
    </p>
  );
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      {errorMessage(error)}
      {error.body && error.body.blocked_by ? (
        <ul>
          {error.body.blocked_by.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Empty({ children }) {
  return <p className="muted">{children}</p>;
}

export const fmt = (v, digits = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(digits));
export const pct = (v, digits = 1) => (v === null || v === undefined ? '—' : `${(Number(v) * 100).toFixed(digits)}%`);
export const usd = (v) =>
  v === 'unlimited'
    ? 'Unlimited'
    : v === null || v === undefined
      ? '—'
      : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function Unavailable({ metric, digits = 2, asPct = false }) {
  if (!metric) return '—';
  if (metric.value === null || metric.value === undefined) {
    return <span className="muted" title={metric.unavailable_reason || ''}>n/a ({metric.unavailable_reason || 'unavailable'})</span>;
  }
  return asPct ? pct(metric.value) : fmt(metric.value, digits);
}
