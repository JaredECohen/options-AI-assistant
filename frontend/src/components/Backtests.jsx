// P7/P8: backtests. Until a backtesting engine and licensed historical options
// data exist, the server returns 503 with the blockers; this panel shows them
// instead of placeholder or synthetic results.
import { useState } from 'react';
import { api } from '../api.js';
import { ErrorNote, Loading } from './common.jsx';

export default function Backtests() {
  const [ticker, setTicker] = useState('SPY');
  const [kind, setKind] = useState('short_put');
  const [delta, setDelta] = useState(0.3);
  const [dte, setDte] = useState(45);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      setJob(await api('/api/backtests', { method: 'POST', body: { kind, underlying: ticker.toUpperCase(),
        entry: { target_abs_delta: Number(delta), target_dte: Number(dte) } } }));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-labelledby="bt-title">
      <h2 id="bt-title">Backtests</h2>
      <p className="muted">Phase 1 strategies: short put, cash-secured put, covered call. Results will include commissions,
        conservative fills, assignment, Sharpe, drawdown, win rate and CVaR once an engine and historical data are connected.</p>
      <form className="row" onSubmit={submit}>
        <div><label htmlFor="bt-t">Underlying</label><input id="bt-t" value={ticker} onChange={(e) => setTicker(e.target.value)} /></div>
        <div><label htmlFor="bt-k">Strategy</label>
          <select id="bt-k" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="short_put">Short put</option><option value="cash_secured_put">Cash-secured put</option>
            <option value="covered_call">Covered call</option>
          </select></div>
        <div><label htmlFor="bt-d">Entry |delta|</label><input id="bt-d" inputMode="decimal" value={delta} onChange={(e) => setDelta(e.target.value)} /></div>
        <div><label htmlFor="bt-dte">Entry DTE</label><input id="bt-dte" inputMode="numeric" value={dte} onChange={(e) => setDte(e.target.value)} /></div>
        <button type="submit" disabled={loading}>Run backtest</button>
      </form>
      {loading ? <Loading /> : null}
      <ErrorNote error={error} />
      {job ? <p role="status">Job {job.id}: {job.status}</p> : null}
    </section>
  );
}
