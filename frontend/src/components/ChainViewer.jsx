// T1: options chain. Ticker -> expirations -> paged chain. A newer request
// aborts the previous one, so a slow stale response can never overwrite a
// fresh one. Data status and freshness come from the server and are shown as-is.
import { useEffect, useRef, useState } from 'react';
import { api, isAbort } from '../api.js';
import { DataStatus, Empty, ErrorNote, Loading, fmt, pct } from './common.jsx';

const PAGE_SIZE = 20;

function useLatest() {
  const ref = useRef(null);
  useEffect(() => () => ref.current && ref.current.abort(), []);
  return () => {
    if (ref.current) ref.current.abort();
    ref.current = new AbortController();
    return ref.current.signal;
  };
}

function Side({ q }) {
  if (!q) return <td colSpan={6} className="muted">—</td>;
  return (
    <>
      <td>{fmt(q.bid)}</td>
      <td>{fmt(q.ask)}</td>
      <td>{q.volume ?? '—'}</td>
      <td>{q.open_interest ?? '—'}</td>
      <td title={q.iv_source || ''}>{q.implied_volatility == null ? '—' : pct(q.implied_volatility)}</td>
      <td>{q.multiplier !== 100 || q.adjusted ? <span className="status status-warn">adj ×{q.multiplier}</span> : '100'}</td>
    </>
  );
}

export default function ChainViewer({ onPick }) {
  const [ticker, setTicker] = useState('');
  const [expirations, setExpirations] = useState([]);
  const [expiration, setExpiration] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const nextExpSignal = useLatest();
  const nextChainSignal = useLatest();

  const loadExpirations = async (e) => {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!/^[A-Z.]{1,10}$/.test(t)) {
      setError({ message: 'Enter a valid ticker (letters only).' });
      return;
    }
    setError(null);
    setData(null);
    setLoading(true);
    try {
      const res = await api(`/api/expirations?ticker=${encodeURIComponent(t)}`, { signal: nextExpSignal() });
      setExpirations(res.expirations || []);
      setExpiration(res.expirations?.[0] || '');
      setPage(1);
    } catch (err) {
      if (!isAbort(err)) setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!expiration) return;
    const t = ticker.trim().toUpperCase();
    setLoading(true);
    setError(null);
    api(`/api/chain?ticker=${encodeURIComponent(t)}&expiration=${expiration}&page=${page}&page_size=${PAGE_SIZE}`, {
      signal: nextChainSignal(),
    })
      .then((res) => {
        setData(res);
        setLoading(false);
      })
      .catch((err) => {
        if (isAbort(err)) return;
        setError(err);
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiration, page]);

  return (
    <section aria-labelledby="chain-title">
      <h2 id="chain-title">Options chain</h2>
      <form className="row" onSubmit={loadExpirations}>
        <div>
          <label htmlFor="chain-ticker">Ticker</label>
          <input id="chain-ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="SPY" />
        </div>
        <div>
          <label htmlFor="chain-exp">Expiration</label>
          <select id="chain-exp" value={expiration} onChange={(e) => { setExpiration(e.target.value); setPage(1); }}
                  disabled={!expirations.length}>
            {expirations.length ? null : <option value="">Load a ticker first</option>}
            {expirations.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </div>
        <button type="submit" disabled={loading}>Load</button>
      </form>
      <ErrorNote error={error} />
      {loading ? <Loading label="Loading chain…" /> : null}
      {data && !loading ? (
        <>
          <p>
            <DataStatus status={data.data_status} label={data.data_status_label} provider={data.provider} freshness={data.freshness} />{' '}
            Underlying {data.underlying_price == null ? 'n/a' : `$${fmt(data.underlying_price)}`} · prices in {data.units?.prices}
          </p>
          {data.chain.length === 0 ? <Empty>No contracts for this expiration.</Empty> : (
            <div className="table-wrap">
              <table className="chain">
                <caption className="sr-only">Calls and puts by strike for {data.ticker} {data.expiration}</caption>
                <thead>
                  <tr><th colSpan={6}>Calls</th><th>Strike</th><th colSpan={6}>Puts</th></tr>
                  <tr>
                    {['Bid', 'Ask', 'Vol', 'OI', 'IV', 'Mult'].map((h) => <th key={`c${h}`} scope="col">{h}</th>)}
                    <th scope="col">$</th>
                    {['Bid', 'Ask', 'Vol', 'OI', 'IV', 'Mult'].map((h) => <th key={`p${h}`} scope="col">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {data.chain.map((row) => (
                    <tr key={row.strike}>
                      <Side q={row.call} />
                      <th scope="row">
                        {onPick ? (
                          <button type="button" className="link" onClick={() => onPick({ ticker: data.ticker, expiration: data.expiration, row })}
                                  aria-label={`Use strike ${row.strike} in builder`}>{row.strike}</button>
                        ) : row.strike}
                      </th>
                      <Side q={row.put} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="row">
            <button type="button" className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous strikes</button>
            <span className="muted">Page {data.page} · {data.total_strikes} strikes</span>
            <button type="button" className="secondary" disabled={!data.has_more} onClick={() => setPage(page + 1)}>More strikes</button>
          </div>
        </>
      ) : null}
    </section>
  );
}
