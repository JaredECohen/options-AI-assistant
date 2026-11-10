// T2/T3: position builder with strategy templates and editable legs, and the
// deterministic payoff result (exact bounds from the server; nothing computed here).
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { DataStatus, Empty, ErrorNote, Loading, fmt, usd } from './common.jsx';

const MAX_LEGS = 8;
let nextId = 1;
const newLeg = (over = {}) => ({
  id: nextId++, instrument: 'option', option_type: 'call', side: 'buy', strike: '', expiration: '', quantity: 1, premium: '',
  ...over,
});

const step = (s) => (s >= 200 ? 5 : s >= 50 ? 1 : 0.5);
const near = (s, mult) => {
  if (!(s > 0)) return ''; // no price, no strike: never invent one
  const st = step(s);
  return Math.round((s * mult) / st) * st;
};

// The listed expiry closest to ~30 calendar days out (templates are monthly-style trades).
export function templateExpiration(list, now = new Date(), target = 30) {
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = (iso) => { const [y, m, d] = iso.split('-').map(Number); return (Date.UTC(y, m - 1, d) - today) / 86400000; };
  const future = list.filter((x) => days(x) >= 1);
  if (!future.length) return '';
  return future.reduce((best, x) => (Math.abs(days(x) - target) < Math.abs(days(best) - target) ? x : best));
}

// Templates express structure only; strikes are starting points to edit.
export const TEMPLATES = {
  'long call': (s, e) => [newLeg({ option_type: 'call', side: 'buy', strike: near(s, 1), expiration: e })],
  'long put': (s, e) => [newLeg({ option_type: 'put', side: 'buy', strike: near(s, 1), expiration: e })],
  'cash-secured put': (s, e) => [newLeg({ option_type: 'put', side: 'sell', strike: near(s, 0.95), expiration: e })],
  'covered call': (s, e) => [newLeg({ instrument: 'stock', side: 'buy', quantity: 100 }),
    newLeg({ option_type: 'call', side: 'sell', strike: near(s, 1.05), expiration: e })],
  'bull call spread': (s, e) => [newLeg({ option_type: 'call', side: 'buy', strike: near(s, 1), expiration: e }),
    newLeg({ option_type: 'call', side: 'sell', strike: near(s, 1.05), expiration: e })],
  'bear put spread': (s, e) => [newLeg({ option_type: 'put', side: 'buy', strike: near(s, 1), expiration: e }),
    newLeg({ option_type: 'put', side: 'sell', strike: near(s, 0.95), expiration: e })],
  'bull put spread': (s, e) => [newLeg({ option_type: 'put', side: 'sell', strike: near(s, 0.97), expiration: e }),
    newLeg({ option_type: 'put', side: 'buy', strike: near(s, 0.92), expiration: e })],
  'long straddle': (s, e) => [newLeg({ option_type: 'call', side: 'buy', strike: near(s, 1), expiration: e }),
    newLeg({ option_type: 'put', side: 'buy', strike: near(s, 1), expiration: e })],
  'short strangle': (s, e) => [newLeg({ option_type: 'call', side: 'sell', strike: near(s, 1.08), expiration: e }),
    newLeg({ option_type: 'put', side: 'sell', strike: near(s, 0.92), expiration: e })],
  'iron condor': (s, e) => [newLeg({ option_type: 'put', side: 'buy', strike: near(s, 0.88), expiration: e }),
    newLeg({ option_type: 'put', side: 'sell', strike: near(s, 0.93), expiration: e }),
    newLeg({ option_type: 'call', side: 'sell', strike: near(s, 1.07), expiration: e }),
    newLeg({ option_type: 'call', side: 'buy', strike: near(s, 1.12), expiration: e })],
};

export function validateLegs(legs) {
  const errors = {};
  if (!legs.length) errors.form = 'Add at least one leg.';
  if (legs.length > MAX_LEGS) errors.form = `At most ${MAX_LEGS} legs.`;
  const exps = new Set();
  legs.forEach((l) => {
    const e = [];
    if (!Number.isInteger(Number(l.quantity)) || Number(l.quantity) < 1) e.push('quantity must be a whole number ≥ 1');
    if (l.premium !== '' && (Number.isNaN(Number(l.premium)) || Number(l.premium) < 0)) e.push('premium must be ≥ 0 or blank');
    if (l.instrument === 'option') {
      if (!(Number(l.strike) > 0)) e.push('strike must be > 0');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(l.expiration)) e.push('expiration must be YYYY-MM-DD');
      else exps.add(l.expiration);
    }
    if (e.length) errors[l.id] = e;
  });
  if (exps.size > 1) errors.form = 'All option legs must share one expiration for an expiration payoff.';
  return errors;
}

function toApiLeg(l) {
  const premium = l.premium === '' ? undefined : Number(l.premium);
  if (l.instrument === 'stock') return { instrument: 'stock', side: l.side, quantity: Number(l.quantity), premium };
  return { instrument: 'option', option_type: l.option_type, side: l.side, strike: Number(l.strike),
           expiration: l.expiration, quantity: Number(l.quantity), premium };
}

function PayoffChart({ curve }) {
  if (!curve || curve.length < 2) return null;
  const W = 520, H = 180, pad = 24;
  const xs = curve.map((p) => p.price), ys = curve.map((p) => p.payoff);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys, 0), Math.max(...ys, 0)];
  const sx = (x) => pad + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * pad);
  const sy = (y) => H - pad - ((y - y0) / (y1 - y0 || 1)) * (H - 2 * pad);
  const d = curve.map((p, i) => `${i ? 'L' : 'M'}${sx(p.price).toFixed(1)},${sy(p.payoff).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img"
         aria-label="Payoff at expiration across underlying prices (display only; bounds above are exact)">
      <line x1={pad} x2={W - pad} y1={sy(0)} y2={sy(0)} className="axis" />
      <path d={d} className="curve" fill="none" />
    </svg>
  );
}

export function PayoffResult({ result }) {
  if (!result) return null;
  const c = result.computed;
  const b = c.bounds || {};
  const at = (x) => (x && x.at_price !== null && x.at_price !== undefined ? ` at $${fmt(x.at_price)}` : '');
  return (
    <div className="result" aria-live="polite">
      <p>
        <DataStatus status={c.data_status} label={c.data_status && c.data_status === 'synthetic' ? 'Synthetic demo data — not market prices' : c.data_status} provider={c.quote_source} />
      </p>
      <dl className="grid">
        <dt>Max profit</dt><dd data-testid="max-profit">{usd(c.max_profit)}{at(b.max_profit)}</dd>
        <dt>Max loss</dt><dd data-testid="max-loss">{usd(c.max_loss)}{at(b.max_loss)}</dd>
        <dt>Breakevens</dt><dd>{c.breakevens.length ? c.breakevens.map((x) => `$${fmt(x)}`).join(', ') : 'none'}</dd>
        <dt>{c.net_debit > 0 ? 'Net debit' : 'Net credit'}</dt><dd>{usd(c.net_debit > 0 ? c.net_debit : c.net_credit)}</dd>
        <dt>Underlying</dt><dd>{c.underlying_price == null ? 'not available' : `$${fmt(c.underlying_price)}`}</dd>
      </dl>
      <PayoffChart curve={c.payoff_curve} />
      <details>
        <summary>Premiums used and conventions</summary>
        <ul>
          {c.premiums_used.map((p, i) => (
            <li key={i}>
              {p.instrument === 'stock' ? 'Stock' : `${p.side} ${p.option_type} ${p.strike}`}: ${fmt(p.premium, 4)} ({p.premium_source}
              {p.fill_convention ? `, ${p.fill_convention} fill` : ''}{p.contract_symbol ? `, ${p.contract_symbol}` : ''}
              {p.quote_time ? `, quoted ${p.quote_time}` : ''})
            </li>
          ))}
        </ul>
        <p className="muted">{b.method}. {c.conventions?.units}. Excludes {c.conventions?.excludes?.join(', ')}.</p>
      </details>
    </div>
  );
}

export default function Builder({ seed }) {
  const [ticker, setTicker] = useState('');
  const [legs, setLegs] = useState([newLeg()]);
  const [template, setTemplate] = useState('');
  const [templateNote, setTemplateNote] = useState('');
  const [fill, setFill] = useState('mid');
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState([]);
  const [name, setName] = useState('');
  const errors = validateLegs(legs);

  useEffect(() => {
    if (!seed) return;
    setTicker(seed.ticker);
    setLegs((prev) => [...prev.filter((l) => l.strike !== '' || l.instrument === 'stock'),
      newLeg({ strike: seed.row.strike, expiration: seed.expiration })].slice(0, MAX_LEGS));
  }, [seed]);

  useEffect(() => {
    const ctrl = new AbortController();
    api('/api/positions', { signal: ctrl.signal }).then((r) => setSaved(r.items || [])).catch(() => {});
    return () => ctrl.abort();
  }, []);

  const applyTemplate = async (name) => {
    setTemplate(name);
    setTemplateNote('');
    if (!name) return;
    // Strikes come only from a real price. A failed lookup used to fall back to $100,
    // which built a SPY iron condor at 88/93/107/112 against a $752 stock.
    let spot = null, exp = '';
    const t = ticker.trim().toUpperCase();
    if (!t) {
      setTemplateNote('Enter a ticker to fill strikes and the expiration from current prices.');
    } else {
      const [quote, exps] = await Promise.allSettled([api(`/api/quote?ticker=${t}`), api(`/api/expirations?ticker=${t}`)]);
      if (quote.status === 'fulfilled' && quote.value.price > 0) spot = quote.value.price;
      if (exps.status === 'fulfilled') exp = templateExpiration(exps.value.expirations || []);
      if (spot === null) {
        setTemplateNote(`No current price for ${t} right now, so strikes are left blank; enter them, or pick the template again.`);
      }
    }
    setLegs(TEMPLATES[name](spot, exp));
    setResult(null);
  };

  const update = (id, field, value) => setLegs((ls) => ls.map((l) => (l.id === id ? { ...l, [field]: value } : l)));
  const move = (i, dir) => setLegs((ls) => {
    const j = i + dir;
    if (j < 0 || j >= ls.length) return ls;
    const copy = [...ls];
    [copy[i], copy[j]] = [copy[j], copy[i]];
    return copy;
  });

  const compute = async (e) => {
    e.preventDefault();
    if (Object.keys(errors).length) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api('/api/payoff', { method: 'POST', body: { ticker: ticker.trim().toUpperCase(), legs: legs.map(toApiLeg), fill_convention: fill } }));
    } catch (err) {
      setError(err);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    try {
      const p = await api('/api/positions', { method: 'POST', body: { name, ticker: ticker.trim().toUpperCase(), legs: legs.map(toApiLeg) } });
      setSaved((s) => [p, ...s]);
      setName('');
    } catch (err) {
      setError(err);
    }
  };

  return (
    <section aria-labelledby="builder-title">
      <h2 id="builder-title">Position builder</h2>
      <form onSubmit={compute} noValidate>
        <div className="row">
          <div>
            <label htmlFor="b-ticker">Ticker</label>
            <input id="b-ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="SPY" required />
          </div>
          <div>
            <label htmlFor="b-template">Strategy template</label>
            <select id="b-template" value={template} onChange={(e) => applyTemplate(e.target.value)}>
              <option value="">Custom</option>
              {Object.keys(TEMPLATES).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="b-fill">Fill convention</label>
            <select id="b-fill" value={fill} onChange={(e) => setFill(e.target.value)}>
              <option value="mid">Mid</option>
              <option value="conservative">Conservative (pay ask / receive bid)</option>
            </select>
          </div>
        </div>
        {templateNote ? <p className="note" role="status">{templateNote}</p> : null}
        <table className="legs">
          <caption className="sr-only">Position legs</caption>
          <thead><tr><th>Type</th><th>Side</th><th>Call/Put</th><th>Strike</th><th>Expiration</th><th>Qty</th><th>Premium</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {legs.map((l, i) => (
              <tr key={l.id} aria-invalid={Boolean(errors[l.id])}>
                <td><select aria-label={`Leg ${i + 1} instrument`} value={l.instrument} onChange={(e) => update(l.id, 'instrument', e.target.value)}>
                  <option value="option">Option</option><option value="stock">Stock</option></select></td>
                <td><select aria-label={`Leg ${i + 1} side`} value={l.side} onChange={(e) => update(l.id, 'side', e.target.value)}>
                  <option value="buy">Buy</option><option value="sell">Sell</option></select></td>
                <td>{l.instrument === 'option' ? (
                  <select aria-label={`Leg ${i + 1} call or put`} value={l.option_type} onChange={(e) => update(l.id, 'option_type', e.target.value)}>
                    <option value="call">Call</option><option value="put">Put</option></select>) : '—'}</td>
                <td>{l.instrument === 'option' ? <input aria-label={`Leg ${i + 1} strike`} inputMode="decimal" value={l.strike} onChange={(e) => update(l.id, 'strike', e.target.value)} /> : '—'}</td>
                <td>{l.instrument === 'option' ? <input aria-label={`Leg ${i + 1} expiration`} placeholder="YYYY-MM-DD" value={l.expiration} onChange={(e) => update(l.id, 'expiration', e.target.value)} /> : '—'}</td>
                <td><input aria-label={`Leg ${i + 1} quantity`} inputMode="numeric" value={l.quantity} onChange={(e) => update(l.id, 'quantity', e.target.value)} /></td>
                <td><input aria-label={`Leg ${i + 1} premium`} placeholder="from quotes" inputMode="decimal" value={l.premium} onChange={(e) => update(l.id, 'premium', e.target.value)} /></td>
                <td className="actions">
                  <button type="button" className="secondary" aria-label={`Move leg ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                  <button type="button" className="secondary" aria-label={`Move leg ${i + 1} down`} disabled={i === legs.length - 1} onClick={() => move(i, 1)}>↓</button>
                  <button type="button" className="secondary" aria-label={`Remove leg ${i + 1}`} onClick={() => setLegs((ls) => ls.filter((x) => x.id !== l.id))}>✕</button>
                  {errors[l.id] ? <span className="error" role="alert">{errors[l.id].join('; ')}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {errors.form ? <p className="error" role="alert">{errors.form}</p> : null}
        <div className="row">
          <button type="button" className="secondary" disabled={legs.length >= MAX_LEGS} onClick={() => setLegs((ls) => [...ls, newLeg({ expiration: ls.find((x) => x.expiration)?.expiration || '' })])}>Add leg</button>
          <button type="submit" disabled={busy || Object.keys(errors).length > 0 || !ticker.trim()}>Compute payoff</button>
        </div>
      </form>
      <p className="muted">Leave a premium blank to price it from current quotes; the source and fill convention are shown with the result.</p>
      <ErrorNote error={error} />
      {busy ? <Loading label="Computing…" /> : <PayoffResult result={result} />}
      <div className="row">
        <div>
          <label htmlFor="b-name">Save as</label>
          <input id="b-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="My covered call" />
        </div>
        <button type="button" className="secondary" disabled={!name.trim() || !ticker.trim() || Object.keys(errors).length > 0} onClick={save}>Save position</button>
      </div>
      {saved.length ? (
        <ul aria-label="Saved positions">
          {saved.map((p) => <li key={p.id}>{p.name} · {p.ticker} · {p.legs.length} legs</li>)}
        </ul>
      ) : <Empty>No saved positions yet.</Empty>}
    </section>
  );
}
