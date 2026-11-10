// T1: options chain. Ticker -> expirations -> a window of strikes centred on
// the money. The user picks how many strikes each side and which side(s) to
// show; both choices are remembered. A newer request aborts the previous one,
// so a slow stale response can never overwrite a fresh one. Data status and
// freshness come from the server and are shown as-is.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, isAbort } from '../api.js';
import { DataStatus, Empty, ErrorNote, Loading, fmt, pct } from './common.jsx';

export const DEPTHS = [5, 10, 15, 20, 30, 'all'];
const ALL_STRIKES = 500; // the server's cap; covers every listed chain
const SIDES = [
  { value: 'call', label: 'Calls' },
  { value: 'put', label: 'Puts' },
  { value: 'both', label: 'Both' },
];
const STORE_KEY = 'optionscoach.chain.view';
const COLUMNS = ['Bid', 'Ask', 'Vol', 'OI', 'IV', 'Mult'];
const BASIS = { underlying: 'from underlying', put_call_parity: 'from put-call parity' };

function loadView() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORE_KEY) || '{}');
    return {
      depth: DEPTHS.includes(saved.depth) ? saved.depth : 10,
      side: SIDES.some((s) => s.value === saved.side) ? saved.side : 'both',
    };
  } catch {
    return { depth: 10, side: 'both' };
  }
}

function saveView(view) {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(view));
  } catch {
    /* private mode or blocked storage: the choice just isn't remembered */
  }
}

function useLatest() {
  const ref = useRef(null);
  useEffect(() => () => ref.current && ref.current.abort(), []);
  return () => {
    if (ref.current) ref.current.abort();
    ref.current = new AbortController();
    return ref.current.signal;
  };
}

function Side({ q, itm }) {
  if (!q) return <td colSpan={COLUMNS.length} className="muted">—</td>;
  const cls = itm ? 'itm' : undefined;
  return (
    <>
      <td className={cls}>{fmt(q.bid)}</td>
      <td className={cls}>{fmt(q.ask)}</td>
      <td className={cls}>{q.volume ?? '—'}</td>
      <td className={cls}>{q.open_interest ?? '—'}</td>
      <td className={cls} title={q.iv_source || ''}>{q.implied_volatility == null ? '—' : pct(q.implied_volatility)}</td>
      <td className={cls}>{q.multiplier !== 100 || q.adjusted ? <span className="status status-warn">adj ×{q.multiplier}</span> : '100'}</td>
    </>
  );
}

function summary(data) {
  const shown = data.chain.length;
  const base = `Showing ${shown} of ${data.total_strikes} strikes`;
  if (data.atm_strike == null) return `${base} (at-the-money strike unknown)`;
  return `${base}, centred on ${data.atm_strike} (ATM, ${BASIS[data.atm_basis] || 'estimated'})`;
}

// Days to expiration counted in calendar days from today (local date).
export function dte(iso, now = new Date()) {
  const [y, m, d] = iso.split('-').map(Number);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((Date.UTC(y, m - 1, d) - today) / 86400000);
}
const WEEKDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// Short enough to fit the picker: "Mon Nov 16 · 7d", with the year only when it differs.
export function expirationLabel(iso, now = new Date()) {
  const [y, m, d] = iso.split('-').map(Number);
  const year = y === now.getFullYear() ? '' : ` '${String(y).slice(2)}`;
  return `${WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${MONTH[m - 1]} ${d}${year} · ${dte(iso, now)}d`;
}
// 0-1 DTE chains are mostly noise for learning; open on the first expiry a week or more out.
export function defaultExpiration(list, now = new Date()) {
  return list.find((x) => dte(x, now) >= 7) || list[0] || '';
}

export default function ChainViewer({ onPick, focus = null }) {
  const initial = useRef(loadView()).current;
  const [ticker, setTicker] = useState('');
  const [expirations, setExpirations] = useState([]);
  const [expiration, setExpiration] = useState('');
  const [depth, setDepth] = useState(initial.depth);
  const [side, setSide] = useState(initial.side);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const nextExpSignal = useLatest();
  const nextChainSignal = useLatest();
  const wrapRef = useRef(null);
  const atmRef = useRef(null);

  useEffect(() => saveView({ depth, side }), [depth, side]);

  // Opened from the strategy finder: that ticker and expiry, with the structure's strikes marked.
  useEffect(() => {
    if (!focus) return;
    setTicker(focus.ticker);
    fetchExpirations(focus.ticker, focus.expiration);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce]);
  const picked = new Set(focus && data && data.expiration === focus.expiration ? focus.strikes || [] : []);

  const loadExpirations = async (e) => {
    e.preventDefault();
    await fetchExpirations(ticker.trim().toUpperCase());
  };

  const fetchExpirations = async (t, preferred = null) => {
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
      const list = res.expirations || [];
      setExpiration(preferred && list.includes(preferred) ? preferred : defaultExpiration(list));
    } catch (err) {
      if (!isAbort(err)) setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!expiration) return;
    const t = ticker.trim().toUpperCase();
    const strikes = depth === 'all' ? ALL_STRIKES : depth;
    setLoading(true);
    setError(null);
    api(`/api/chain?ticker=${encodeURIComponent(t)}&expiration=${expiration}&side=${side}&strikes=${strikes}`, {
      signal: nextChainSignal(),
    })
      .then((res) => {
        setData({ ...res, requestedSide: side });
        setLoading(false);
      })
      .catch((err) => {
        if (isAbort(err)) return;
        setError(err);
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiration, depth, side]);

  // Centre the at-the-money row inside the scrolling table (not the page).
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const row = atmRef.current;
    if (!wrap || !row) return;
    // centre within the part of the box the sticky header does not cover
    const header = wrap.querySelector('thead')?.offsetHeight || 0;
    const visible = wrap.clientHeight - header;
    wrap.scrollTop = Math.max(0, row.offsetTop - header - visible / 2 + row.offsetHeight / 2);
  }, [data, loading]);

  const shownSide = data?.requestedSide || 'both'; // what the rows on screen were fetched with
  const showCalls = shownSide !== 'put';
  const showPuts = shownSide !== 'call';
  const spot = data?.underlying_price ?? data?.atm_strike ?? null;

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
          <select id="chain-exp" value={expiration} onChange={(e) => setExpiration(e.target.value)}
                  disabled={!expirations.length}>
            {expirations.length ? null : <option value="">Load a ticker first</option>}
            {expirations.map((x) => <option key={x} value={x}>{expirationLabel(x)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="chain-depth">Strikes each side</label>
          <select id="chain-depth" value={String(depth)}
                  onChange={(e) => setDepth(e.target.value === 'all' ? 'all' : Number(e.target.value))}>
            {DEPTHS.map((d) => <option key={d} value={String(d)}>{d === 'all' ? 'All' : d}</option>)}
          </select>
        </div>
        <fieldset className="segmented">
          <legend>Show</legend>
          {SIDES.map((s) => (
            <label key={s.value} className={side === s.value ? 'active' : undefined}>
              <input type="radio" name="chain-side" value={s.value} checked={side === s.value}
                     onChange={() => setSide(s.value)} />
              {s.label}
            </label>
          ))}
        </fieldset>
        <button type="submit" disabled={loading}>Load</button>
      </form>
      <ErrorNote error={error} />
      {loading ? <Loading label="Loading chain…" /> : null}
      {data && !loading ? (
        <>
          <p className="status-line">
            <DataStatus status={data.data_status} label={data.data_status_label} provider={data.provider} freshness={data.freshness} />{' '}
            Underlying {data.underlying_price == null ? 'n/a' : `$${fmt(data.underlying_price)}`} · prices in {data.units?.prices}
          </p>
          {data.chain.length === 0 ? <Empty>No contracts for this expiration.</Empty> : (
            <>
              <p className="muted" data-testid="chain-summary">{summary(data)}</p>
              <div className="table-wrap chain-wrap" ref={wrapRef}>
                <table className="chain">
                  <caption className="sr-only">
                    {shownSide === 'both' ? 'Calls and puts' : shownSide === 'call' ? 'Calls' : 'Puts'} by strike for {data.ticker} {data.expiration}
                  </caption>
                  <thead>
                    <tr>
                      {showCalls ? <th colSpan={COLUMNS.length}>Calls</th> : null}
                      <th>Strike</th>
                      {showPuts ? <th colSpan={COLUMNS.length}>Puts</th> : null}
                    </tr>
                    <tr>
                      {showCalls ? COLUMNS.map((h) => <th key={`c${h}`} scope="col">{h}</th>) : null}
                      <th scope="col">$</th>
                      {showPuts ? COLUMNS.map((h) => <th key={`p${h}`} scope="col">{h}</th>) : null}
                    </tr>
                  </thead>
                  <tbody>
                    {data.chain.map((row) => {
                      const atm = row.strike === data.atm_strike;
                      return (
                        <tr key={row.strike} className={[atm ? 'atm' : '', picked.has(row.strike) ? 'picked' : ''].join(' ').trim() || undefined}
                            ref={atm ? atmRef : undefined}
                            aria-current={atm ? 'true' : undefined}>
                          {showCalls ? <Side q={row.call} itm={spot != null && row.strike < spot} /> : null}
                          <th scope="row">
                            {onPick ? (
                              <button type="button" className="link" onClick={() => onPick({ ticker: data.ticker, expiration: data.expiration, row })}
                                      aria-label={`Use strike ${row.strike} in builder`}>{row.strike}</button>
                            ) : row.strike}
                            {atm ? <span className="atm-badge">ATM</span> : null}
                          </th>
                          {showPuts ? <Side q={row.put} itm={spot != null && row.strike > spot} /> : null}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      ) : null}
    </section>
  );
}
