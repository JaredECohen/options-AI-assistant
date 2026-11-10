// P4/P5/P8: volatility analytics, term structure and the IV surface.
// Observed and fitted values are always shown separately; unavailable metrics
// display their reason instead of a number.
import { useRef, useState } from 'react';
import { api, isAbort } from '../api.js';
import { DataStatus, Empty, ErrorNote, Loading, Unavailable, fmt, pct, reasonText } from './common.jsx';

export default function Analytics() {
  const [ticker, setTicker] = useState('');
  const [summary, setSummary] = useState(null);
  const [surface, setSurface] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const ctrl = useRef(null);

  const load = async (e) => {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!/^[A-Z.]{1,10}$/.test(t)) return setError({ message: 'Enter a valid ticker.' });
    if (ctrl.current) ctrl.current.abort();
    ctrl.current = new AbortController();
    setLoading(true);
    setError(null);
    try {
      const [a, s] = await Promise.all([
        api(`/api/analytics/${t}`, { signal: ctrl.current.signal }),
        api(`/api/analytics/${t}/surface`, { signal: ctrl.current.signal }),
      ]);
      setSummary(a);
      setSurface(s);
    } catch (err) {
      if (!isAbort(err)) setError(err);
    } finally {
      setLoading(false);
    }
  };

  const s = summary?.summary;
  return (
    <section aria-labelledby="an-title">
      <h2 id="an-title">Volatility analytics</h2>
      <form className="row" onSubmit={load}>
        <div>
          <label htmlFor="an-ticker">Ticker</label>
          <input id="an-ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="SPY" />
        </div>
        <button type="submit" disabled={loading}>Analyze</button>
      </form>
      <ErrorNote error={error} />
      {loading ? <Loading label="Computing analytics…" /> : null}
      {s && !loading ? (
        <>
          <p><DataStatus status={summary.data_status} label={summary.data_status_label} provider={summary.provider} /></p>
          {summary.iv_history && summary.iv_history.status !== 'ready' ? (
            <p className={`note note-${summary.iv_history.status}`} role="status">
              {summary.iv_history.detail} IV rank and percentile appear once it is done.
            </p>
          ) : null}
          <dl className="grid">
            <dt>30-day ATM IV</dt><dd><Unavailable metric={s.iv_30d} asPct /></dd>
            <dt>HV 10 / 20 / 60 / 252</dt>
            <dd>{['hv10', 'hv20', 'hv60', 'hv252'].map((k, i) => <span key={k}>{i ? ' / ' : ''}<Unavailable metric={s.hv[k]} asPct /></span>)}</dd>
            <dt>IV / HV20</dt><dd><Unavailable metric={s.iv_hv_ratio} /></dd>
            <dt>IV rank</dt><dd><Unavailable metric={s.iv_rank} digits={0} /></dd>
            <dt>IV percentile</dt><dd><Unavailable metric={s.iv_percentile} digits={0} /></dd>
            <dt>25Δ skew</dt><dd><Unavailable metric={s.skew_25d} asPct /></dd>
            <dt>Median spread</dt><dd>{s.liquidity?.median_spread_pct == null ? '—' : pct(s.liquidity.median_spread_pct)}</dd>
          </dl>
          <details>
            <summary>Definitions and conventions</summary>
            <ul>{Object.entries(s.definitions).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul>
            <p className="muted">
              Model {summary.conventions.model}; rate {summary.conventions.risk_free_rate} ({summary.conventions.risk_free_rate_source});
              dividend yield {summary.conventions.dividend_yield} ({summary.conventions.dividend_yield_source}). {summary.conventions.exercise_note}
            </p>
          </details>
          <h3>Term structure</h3>
          <table>
            <thead><tr><th scope="col">Expiration</th><th scope="col">DTE</th><th scope="col">ATM IV</th></tr></thead>
            <tbody>{s.term_structure.map((t) => (
              <tr key={t.expiration}><td>{t.expiration}</td><td>{fmt(t.dte, 1)}</td>
                <td>{t.atm_iv == null ? <span className="muted">n/a ({reasonText(t.unavailable_reason)})</span> : pct(t.atm_iv)}</td></tr>
            ))}</tbody>
          </table>
        </>
      ) : null}
      {surface && !loading ? (
        <>
          <h3>Surface</h3>
          <p className="muted">{surface.surface.method}</p>
          {surface.surface.fitted.length === 0 ? <Empty>Not enough quotes to fit a surface.</Empty> : (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Fitted implied volatility by expiry and log-moneyness</caption>
                <thead><tr><th scope="col">Expiry (fitted)</th>{surface.surface.k_grid.map((k) => <th key={k} scope="col">k={k}</th>)}</tr></thead>
                <tbody>{surface.surface.fitted.map((row) => (
                  <tr key={row.expiration}><th scope="row">{row.expiration}</th>{row.iv.map((v, i) => <td key={i}>{v == null ? '·' : pct(v)}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <h4>Dislocations vs local fit</h4>
          {surface.surface.dislocations.length === 0 ? <Empty>None flagged.</Empty> : (
            <ul>{surface.surface.dislocations.map((d) => (
              <li key={`${d.expiration}-${d.strike}-${d.option_type}`}>
                {d.expiration} {d.strike} {d.option_type}: observed {pct(d.iv_observed)} vs fitted {pct(d.iv_fitted)} ({d.direction.replaceAll('_', ' ')})
              </li>
            ))}</ul>
          )}
        </>
      ) : null}
    </section>
  );
}
