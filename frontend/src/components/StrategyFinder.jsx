// Strategy finder: describe a view in words (or fields) and get exact candidate
// structures priced from the live chain, each openable in the builder, the
// backtester and the chain. Server: POST /api/strategies/suggest.
import { useRef, useState } from 'react';
import { api, isAbort } from '../api.js';
import { DataStatus, ErrorNote, Loading, pct } from './common.jsx';
import PayoffChart from './PayoffChart.jsx';

const DIRECTIONS = [
  ['bullish', 'Rise'],
  ['bearish', 'Fall'],
  ['neutral', 'Stay in a range'],
  ['volatile', 'Big move, either way'],
  ['income', 'Earn income'],
];
const EXAMPLES = [
  'Up about 5% by mid-December, and I want my loss limited',
  'Stays in a range for the next month',
  'A big move after earnings, not sure which way',
  'I own 100 shares and want income from them',
];

export const money = (v, digits = 2) => {
  if (v === 'unlimited') return 'Unlimited';
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
};

export function legText(leg) {
  if (leg.instrument === 'stock') return `${leg.side === 'buy' ? 'Buy' : 'Sell'} ${leg.quantity} shares at ${money(leg.premium)}`;
  return `${leg.side === 'buy' ? 'Buy' : 'Sell'} ${leg.quantity} ${leg.strike} ${leg.option_type} at ${money(leg.premium)}`;
}

function viewToForm(v) {
  const s = (x) => (x === null || x === undefined ? '' : String(x));
  return {
    direction: v.direction || '', target_price: s(v.target_price), move_pct: s(v.move_pct),
    horizon_days: s(v.horizon_days), defined_risk_only: v.defined_risk_only !== false, max_loss: s(v.max_loss),
    owns_shares: Boolean(v.owns_shares),
  };
}

function formToView(f) {
  const n = (x) => (x === '' || x === null ? null : Number(x));
  return {
    direction: f.direction || null, target_price: n(f.target_price), move_pct: n(f.move_pct),
    horizon_days: Number(f.horizon_days) || 30, defined_risk_only: f.defined_risk_only, max_loss: n(f.max_loss),
    owns_shares: f.owns_shares,
  };
}

// Today's price +/- 2.5 expected moves, widened to show every strike and the target.
export function chartDomain(c, data) {
  const em = Math.max(data.expected_move || 0, data.spot * 0.02);
  const pts = [data.spot - 2.5 * em, data.spot + 2.5 * em, data.target_price,
    ...c.legs.filter((l) => l.strike).map((l) => Number(l.strike))].filter((v) => Number.isFinite(v));
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const pad = (hi - lo) * 0.04;
  return [Math.max(0, lo - pad), hi + pad];
}

const PARSED = { rules: 'Read from your description', model: 'Interpreted by the AI model', edited: 'Your edited settings',
  none: 'Could not tell the direction' };

function Candidate({ c, data, onOpen }) {
  const m = c.metrics;
  const [open, setOpen] = useState(c.rank === 1);
  return (
    <article className={`sf-card${c.rank === 1 ? ' sf-best' : ''}`} aria-labelledby={`sf-${c.key}`}>
      <div className="sf-card-head">
        <span className="sf-rank" aria-label={`Rank ${c.rank}`}>{c.rank}</span>
        <div>
          <h3 id={`sf-${c.key}`}>{c.name}</h3>
          <p className="muted sf-sub">
            {c.type === 'credit' ? `Credit ${money(m.net_credit)}` : `Debit ${money(m.net_debit)}`} · {data.expiration} ({data.dte} days)
            {c.undefined_risk ? <span className="tag tag-warn">Undefined risk</span> : <span className="tag">Defined risk</span>}
            {c.rank === 1 ? <span className="tag tag-accent">Best fit</span> : null}
          </p>
        </div>
      </div>
      <p className="sf-fit">{c.explanation.fit}</p>
      <PayoffChart legs={c.legs} domain={chartDomain(c, data)} spot={data.spot} target={data.target_price}
        breakevens={m.breakevens} height={170} />
      <dl className="sf-metrics">
        <div><dt>Max profit</dt><dd className="pos">{money(m.max_profit)}</dd></div>
        <div><dt>Max loss</dt><dd className="neg">{money(m.max_loss)}</dd></div>
        <div><dt>Breakeven{m.breakevens.length > 1 ? 's' : ''}</dt><dd>{m.breakevens.map((b) => money(b)).join(' / ') || '—'}</dd></div>
        <div><dt>If your view plays out</dt><dd className={m.pnl_at_view >= 0 ? 'pos' : 'neg'}>{money(m.pnl_at_view)}</dd></div>
        <div><dt title="Risk-neutral estimate at the at-the-money IV">Chance of profit (model)</dt><dd>{m.probability_of_profit == null ? '—' : pct(m.probability_of_profit, 0)}</dd></div>
        <div><dt>Capital / margin</dt><dd>{money(m.capital, 0)}</dd></div>
      </dl>
      <ul className="sf-legs" aria-label="Legs">
        {c.legs.map((l, i) => <li key={i}>{legText(l)}</li>)}
      </ul>
      <button type="button" className="linklike sf-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Hide how it works' : 'How it works'}
      </button>
      {open ? (
        <dl className="sf-explain">
          <dt>How it works</dt><dd>{c.explanation.how}</dd>
          <dt>When it wins</dt><dd>{c.explanation.wins}</dd>
          <dt>How it loses</dt><dd>{c.explanation.loses}</dd>
        </dl>
      ) : null}
      <div className="sf-actions">
        <button type="button" onClick={() => onOpen('builder', c)}>Open in builder</button>
        <button type="button" className="secondary" onClick={() => onOpen('backtest', c)}>Backtest this</button>
        <button type="button" className="secondary" onClick={() => onOpen('chain', c)}>View in chain</button>
      </div>
    </article>
  );
}

export default function StrategyFinder({ onOpen = () => {} }) {
  const [ticker, setTicker] = useState('SPY');
  const [text, setText] = useState('');
  const [form, setForm] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const ctrl = useRef(null);

  const run = async (view) => {
    const t = ticker.trim().toUpperCase();
    if (!/^[A-Z.]{1,10}$/.test(t)) {
      setError({ message: 'Enter a valid ticker (letters only).' });
      return;
    }
    if (!view && !text.trim()) {
      setError({ message: 'Describe what you expect, or pick an example.' });
      return;
    }
    if (ctrl.current) ctrl.current.abort();
    ctrl.current = new AbortController();
    setLoading(true);
    setError(null);
    try {
      const res = await api('/api/strategies/suggest', { method: 'POST', signal: ctrl.current.signal,
        body: view ? { ticker: t, text, view } : { ticker: t, text } });
      setData(res);
      setForm(viewToForm(res.interpreted));
    } catch (err) {
      if (!isAbort(err)) setError(err);
    } finally {
      setLoading(false);
    }
  };

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const onSubmit = (e) => { e.preventDefault(); run(null); };

  return (
    <section aria-labelledby="sf-title" className="sf">
      <h2 id="sf-title">Find a strategy</h2>
      <p className="muted">Describe what you expect a stock to do. You get exact structures from today's options chain,
        with their payoff, what they risk, and how they work.</p>
      <form onSubmit={onSubmit} className="sf-form">
        <div className="sf-ticker">
          <label htmlFor="sf-ticker">Ticker</label>
          <input id="sf-ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="SPY" autoComplete="off" />
        </div>
        <div className="sf-text">
          <label htmlFor="sf-text">Your view</label>
          <textarea id="sf-text" rows={2} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Up about 5% by mid-December, and I want my loss limited" />
        </div>
        <button type="submit" disabled={loading}>{loading ? 'Finding…' : 'Find strategies'}</button>
      </form>
      <div className="starter-list" aria-label="Examples">
        {EXAMPLES.map((ex) => <button key={ex} type="button" className="starter" onClick={() => setText(ex)}>{ex}</button>)}
      </div>
      <ErrorNote error={error} />
      {loading ? <Loading label="Pricing candidates from the live chain…" /> : null}

      {form && data && !loading ? (
        <form className="sf-view" onSubmit={(e) => { e.preventDefault(); run(formToView(form)); }} aria-label="Interpreted view">
          <div className="sf-view-head">
            <h3>What you expect</h3>
            <span className="muted">{PARSED[data.parsed_by] || ''}</span>
          </div>
          <fieldset className="segmented sf-dir">
            <legend className="sr-only">Direction</legend>
            {DIRECTIONS.map(([v, l]) => (
              <label key={v} className={form.direction === v ? 'active' : ''}>
                <input type="radio" name="sf-dir" value={v} checked={form.direction === v} onChange={() => set('direction', v)} />
                {l}
              </label>
            ))}
          </fieldset>
          <div className="sf-fields">
            <div><label htmlFor="sf-target">Target price ($)</label>
              <input id="sf-target" inputMode="decimal" value={form.target_price} onChange={(e) => set('target_price', e.target.value)} placeholder="optional" /></div>
            <div><label htmlFor="sf-move">Or move (%)</label>
              <input id="sf-move" inputMode="decimal" value={form.move_pct} onChange={(e) => set('move_pct', e.target.value)} placeholder="optional" /></div>
            <div><label htmlFor="sf-horizon">Within (days)</label>
              <input id="sf-horizon" inputMode="numeric" value={form.horizon_days} onChange={(e) => set('horizon_days', e.target.value)} /></div>
            <div><label htmlFor="sf-maxloss">Max loss ($)</label>
              <input id="sf-maxloss" inputMode="decimal" value={form.max_loss} onChange={(e) => set('max_loss', e.target.value)} placeholder="no limit" /></div>
          </div>
          <div className="sf-checks">
            <label><input type="checkbox" checked={form.defined_risk_only} onChange={(e) => set('defined_risk_only', e.target.checked)} /> Defined risk only</label>
            <label><input type="checkbox" checked={form.owns_shares} onChange={(e) => set('owns_shares', e.target.checked)} /> I own 100 shares</label>
            <button type="submit" className="secondary">Update results</button>
          </div>
        </form>
      ) : null}

      {data && !loading && data.needs_direction ? <p className="note" role="status">{data.detail}</p> : null}

      {data && !loading && !data.needs_direction ? (
        <>
          <p className="sf-context status-line">
            <DataStatus status={data.data_status} label={data.data_status_label} provider={data.provider} />
            <span>{data.ticker} {money(data.spot)}</span>
            <span>Expiry {data.expiration} ({data.dte} days)</span>
            <span>ATM IV {pct(data.atm_iv)}</span>
            <span>Expected move ±{money(data.expected_move)}</span>
            {['bullish', 'bearish'].includes(data.interpreted.direction) ? <span>Your target {money(data.target_price)}</span> : null}
          </p>
          {data.candidates.length === 0 ? <p className="note">No structure fits these limits. Loosen the max loss, or allow undefined risk.</p> : (
            <div className="sf-grid">
              {data.candidates.map((c) => <Candidate key={c.key} c={c} data={data} onOpen={onOpen} />)}
            </div>
          )}
          {data.excluded?.length ? (
            <p className="muted sf-excluded">Not shown: {data.excluded.map((e) => `${e.name} (${e.reason})`).join('; ')}.</p>
          ) : null}
          <details className="sf-method"><summary>How these are chosen</summary><p className="muted">{data.method}</p></details>
        </>
      ) : null}
    </section>
  );
}
