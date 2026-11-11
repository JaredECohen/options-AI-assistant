// T4: cross-ticker IV comparison at a common maturity and moneyness. Rows the
// server could not normalize are listed separately with the reason.
import { useState } from 'react';
import { api } from '../api.js';
import { Empty, ErrorNote, Loading, Unavailable, reasonText } from './common.jsx';

export default function Compare() {
  const [tickers, setTickers] = useState('SPY, QQQ');
  const [dte, setDte] = useState(30);
  const [sortBy, setSortBy] = useState('iv');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async (e) => {
    e.preventDefault();
    const list = tickers.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean);
    if (list.length < 2 || list.length > 10) return setError({ message: 'Enter 2 to 10 tickers.' });
    setLoading(true);
    setError(null);
    try {
      setData(await api('/api/compare', { method: 'POST', body: { tickers: list, target_dte: Number(dte), sort_by: sortBy } }));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  const rows = data?.rows || [];
  return (
    <section aria-labelledby="cmp-title">
      <h2 id="cmp-title">Compare implied volatility</h2>
      <form className="row" onSubmit={run}>
        <div><label htmlFor="cmp-t">Tickers</label><input id="cmp-t" value={tickers} onChange={(e) => setTickers(e.target.value)} /></div>
        <div><label htmlFor="cmp-dte">Maturity (days)</label>
          <input id="cmp-dte" type="number" min={7} max={180} value={dte} onChange={(e) => setDte(e.target.value)} /></div>
        <div><label htmlFor="cmp-sort">Sort by</label>
          <select id="cmp-sort" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            {['iv', 'hv20', 'iv_hv_ratio', 'iv_rank', 'skew_25d', 'ticker'].map((k) => <option key={k} value={k}>{k}</option>)}
          </select></div>
        <button type="submit" disabled={loading}>Compare</button>
      </form>
      <ErrorNote error={error} />
      {loading ? <Loading /> : null}
      {data && !loading ? (
        <>
          {rows.length ? (
            <p className="muted">{data.note} Maturity {data.target_dte}d, {data.moneyness}; reference status {data.reference_data_status}.</p>
          ) : null}
          {rows.length === 0 ? <Empty>No results.</Empty> : (
            <table>
              <thead><tr>{['Ticker', 'IV', 'HV20', 'IV/HV', 'IV rank', '25Δ skew', 'Status', 'Comparable'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.ticker} className={r.comparable ? '' : 'excluded'}>
                  <th scope="row">{r.ticker}</th>
                  <td><Unavailable metric={r.iv} asPct /></td><td><Unavailable metric={r.hv20} asPct /></td>
                  <td><Unavailable metric={r.iv_hv_ratio} /></td><td><Unavailable metric={r.iv_rank} digits={0} /></td>
                  <td><Unavailable metric={r.skew_25d} asPct /></td><td>{r.data_status}</td>
                  <td>{r.comparable ? 'yes' : `no: ${r.excluded_reasons.join('; ')}`}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {data.errors?.length ? <p className="error">Could not load: {data.errors.map((e) => `${e.ticker} (${reasonText(e.error)})`).join(', ')}</p> : null}
        </>
      ) : null}
    </section>
  );
}
