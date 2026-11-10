// Form fieldsets for the backtest config. `f` is the string-valued form state
// (see config.js); `set(patch)` merges top-level fields.
import { EXIT_KEYS, RANGES, blankLeg } from './config.js';

function Field({ id, label, children, hint }) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      {children}
      {hint ? <span className="muted bt-hint">{hint}</span> : null}
    </div>
  );
}

function Num({ id, label, value, onChange, step = 'any', min, max, hint, disabled }) {
  return (
    <Field id={id} label={label} hint={hint}>
      <input id={id} type="number" inputMode="decimal" step={step} min={min} max={max} value={value} disabled={disabled}
        onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

export function UnderlyingSection({ f, set, onRange }) {
  return (
    <fieldset>
      <legend>Underlying &amp; dates</legend>
      <div className="row">
        <Field id="bt-underlying" label="Underlying">
          <input id="bt-underlying" value={f.underlying} maxLength={10} autoCapitalize="characters"
            onChange={(e) => set({ underlying: e.target.value.toUpperCase() })} />
        </Field>
        <Field id="bt-start" label="Start date">
          <input id="bt-start" type="date" value={f.start} onChange={(e) => set({ start: e.target.value })} />
        </Field>
        <Field id="bt-end" label="End date">
          <input id="bt-end" type="date" value={f.end} onChange={(e) => set({ end: e.target.value })} />
        </Field>
      </div>
      <div className="bt-ranges" role="group" aria-label="Quick date ranges">
        {RANGES.map((r) => (
          <button key={r} type="button" className="secondary inline-button" onClick={() => onRange(r)}>{r}</button>
        ))}
      </div>
    </fieldset>
  );
}

const METHODS = [
  ['delta', 'Delta'], ['atm', 'ATM'], ['pct_otm', '% OTM'], ['offset', '$ offset from leg'],
];
const VALUE_HINT = { delta: '0.01–0.99', pct_otm: '-50 to 50', offset: '$ from leg', atm: 'n/a' };

export function StrategySection({ f, presets, onPreset, setLegs, set }) {
  const current = presets.find((p) => p.key === f.basePreset) || presets.find((p) => p.key === f.strategy);
  const updateLeg = (i, patch) => setLegs(f.legs.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const remove = (i) => setLegs(f.legs.filter((_, j) => j !== i).map((l) => {
    if (l.method !== 'offset') return l;
    const from = Number(l.from_leg);
    return { ...l, from_leg: String(from > i ? from - 1 : from === i ? 0 : from) };
  }));
  const changeMethod = (i, method) => {
    const patch = { method };
    if (method === 'offset') { patch.from_leg = String(Math.max(0, i - 1)); patch.value = f.legs[i].method === 'offset' ? f.legs[i].value : '5'; }
    if (method === 'delta') patch.value = '0.16';
    if (method === 'pct_otm') patch.value = '5';
    updateLeg(i, patch);
  };
  return (
    <fieldset>
      <legend>Strategy</legend>
      <Field id="bt-preset" label="Strategy preset">
        <select id="bt-preset" value={f.strategy === 'custom' && !presets.some((p) => p.key === 'custom') ? '' : f.strategy}
          onChange={(e) => onPreset(e.target.value)}>
          {presets.length === 0 ? <option value={f.strategy}>{f.strategy}</option> : null}
          {presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
      </Field>
      {current ? (
        <p className="muted bt-desc" data-testid="preset-desc">
          {current.description}{f.strategy === 'custom' && current.key !== 'custom' ? ' (legs edited: now a custom strategy)' : ''}
        </p>
      ) : null}
      <div className="table-wrap">
        <table className="legs bt-legs-editor">
          <caption className="sr-only">Strategy legs</caption>
          <thead>
            <tr>
              <th scope="col">Leg</th><th scope="col">Right</th><th scope="col">Side</th><th scope="col">Ratio</th>
              <th scope="col">Strike method</th><th scope="col">Value</th><th scope="col">From leg</th>
              <th scope="col"><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody>
            {f.legs.map((l, i) => {
              const n = i + 1;
              return (
                <tr key={i}>
                  <th scope="row">{n}</th>
                  <td>
                    <select aria-label={`Leg ${n} right`} value={l.right} onChange={(e) => updateLeg(i, { right: e.target.value })}>
                      <option value="call">Call</option><option value="put">Put</option>
                    </select>
                  </td>
                  <td>
                    <select aria-label={`Leg ${n} side`} value={l.side} onChange={(e) => updateLeg(i, { side: e.target.value })}>
                      <option value="buy">Buy</option><option value="sell">Sell</option>
                    </select>
                  </td>
                  <td>
                    <input aria-label={`Leg ${n} ratio`} type="number" min="1" max="10" step="1" value={l.ratio}
                      onChange={(e) => updateLeg(i, { ratio: e.target.value })} />
                  </td>
                  <td>
                    <select aria-label={`Leg ${n} strike method`} value={l.method} onChange={(e) => changeMethod(i, e.target.value)}>
                      {METHODS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                  </td>
                  <td>
                    <input aria-label={`Leg ${n} strike value`} type="number" step="any" placeholder={VALUE_HINT[l.method]}
                      disabled={l.method === 'atm'} value={l.method === 'atm' ? '' : l.value}
                      onChange={(e) => updateLeg(i, { value: e.target.value })} />
                  </td>
                  <td>
                    {l.method === 'offset' && i > 0 ? (
                      <select aria-label={`Leg ${n} offset from leg`} value={l.from_leg} onChange={(e) => updateLeg(i, { from_leg: e.target.value })}>
                        {f.legs.slice(0, i).map((_, j) => <option key={j} value={String(j)}>Leg {j + 1}</option>)}
                      </select>
                    ) : l.method === 'offset' ? <span className="muted">no earlier leg</span> : <span className="muted">—</span>}
                  </td>
                  <td>
                    <button type="button" className="secondary inline-button" aria-label={`Remove leg ${n}`} onClick={() => remove(i)}>Remove</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button type="button" className="secondary inline-button" disabled={f.legs.length >= 4}
        onClick={() => setLegs([...f.legs, blankLeg(f.legs.length)])}>Add leg</button>
      <div className="bt-stock">
        <label className="bt-check">
          <input type="checkbox" checked={f.stockOn} onChange={(e) => set({ stockOn: e.target.checked })} />
          Hold shares against the short call (covered call)
        </label>
        {f.stockOn ? (
          <Num id="bt-shares" label="Shares per contract" value={f.shares} step="1" min="1" onChange={(v) => set({ shares: v })} />
        ) : null}
      </div>
    </fieldset>
  );
}

export function EntrySection({ f, set }) {
  return (
    <fieldset>
      <legend>Entry</legend>
      <div className="row">
        <Num id="bt-dte-target" label="DTE target" value={f.dte_target} step="1" onChange={(v) => set({ dte_target: v })} />
        <Num id="bt-dte-min" label="DTE min" value={f.dte_min} step="1" onChange={(v) => set({ dte_min: v })} />
        <Num id="bt-dte-max" label="DTE max" value={f.dte_max} step="1" onChange={(v) => set({ dte_max: v })} />
        <Field id="bt-expirations" label="Expirations">
          <select id="bt-expirations" value={f.expirations} onChange={(e) => set({ expirations: e.target.value })}>
            <option value="any">Any</option><option value="fridays">Fridays</option><option value="monthly">Monthly (third Friday)</option>
          </select>
        </Field>
        <Field id="bt-frequency" label="Frequency">
          <select id="bt-frequency" value={f.frequency} onChange={(e) => set({ frequency: e.target.value })}>
            <option value="daily">Daily</option><option value="weekly">Weekly</option>
            <option value="monthly">Monthly (first trading day)</option><option value="when_flat">When flat</option>
          </select>
        </Field>
        {f.frequency === 'weekly' ? (
          <Field id="bt-weekday" label="Weekday">
            <select id="bt-weekday" value={f.weekday} onChange={(e) => set({ weekday: e.target.value })}>
              {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'].map((d, i) => <option key={d} value={String(i)}>{d}</option>)}
            </select>
          </Field>
        ) : null}
        <Num id="bt-max-open" label="Max open positions" value={f.max_open} step="1" min="1" onChange={(v) => set({ max_open: v })} />
        <Num id="bt-min-iv" label="Min IV (%)" value={f.min_iv} hint="Optional. ATM IV on entry day." onChange={(v) => set({ min_iv: v })} />
        <Num id="bt-max-iv" label="Max IV (%)" value={f.max_iv} hint="Optional." onChange={(v) => set({ max_iv: v })} />
        <Num id="bt-min-credit" label="Min credit ($/share)" value={f.min_credit} hint="Optional. Credit strategies only."
          onChange={(v) => set({ min_credit: v })} />
        <Num id="bt-delta-tol" label="Delta tolerance" value={f.delta_tolerance} step="0.01" onChange={(v) => set({ delta_tolerance: v })} />
      </div>
    </fieldset>
  );
}

const EXIT_META = {
  profit_target_pct: ['Profit target (%)', 'Credit: % of the credit captured. Debit: % gain on the debit.'],
  stop_loss_pct: ['Stop loss (%)', 'Credit: loss as % of the credit. Debit: % of the debit lost.'],
  exit_dte: ['Exit at DTE', 'Close when this many days remain.'],
  max_hold_days: ['Max hold days', 'Close after this many days in the trade.'],
};

export function ExitSection({ f, set }) {
  const setExit = (k, patch) => set({ exit: { ...f.exit, [k]: { ...f.exit[k], ...patch } } });
  return (
    <fieldset>
      <legend>Exit</legend>
      <p className="muted bt-desc">Unchecked rules are disabled. For credit trades the profit target is the share of the credit
        captured and the stop is the loss as a share of the credit; for debit trades the target is the gain on the debit and the
        stop is the share of the debit lost. Rules are checked on each close in this order: expiration, stop loss, profit target,
        exit DTE, max hold.</p>
      <div className="row">
        {EXIT_KEYS.map((k) => (
          <div key={k} className="bt-exit">
            <label className="bt-check" htmlFor={`bt-exit-${k}-on`}>
              <input id={`bt-exit-${k}-on`} type="checkbox" checked={f.exit[k].on} aria-label={`Enable ${EXIT_META[k][0]}`}
                onChange={(e) => setExit(k, { on: e.target.checked, val: e.target.checked && f.exit[k].val === '' ? '50' : f.exit[k].val })} />
              {EXIT_META[k][0]}
            </label>
            <input id={`bt-exit-${k}`} type="number" step="any" aria-label={`${EXIT_META[k][0]} value`} disabled={!f.exit[k].on}
              value={f.exit[k].val} onChange={(e) => setExit(k, { val: e.target.value })} />
            <span className="muted bt-hint">{EXIT_META[k][1]}</span>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

export function CostsSection({ f, set }) {
  return (
    <fieldset>
      <legend>Costs</legend>
      <div className="row">
        <Num id="bt-commission" label="Commission per contract ($)" value={f.commission} step="0.01" onChange={(v) => set({ commission: v })} />
        <Field id="bt-fill" label="Fill model">
          <select id="bt-fill" value={f.fill} onChange={(e) => set({ fill: e.target.value })}>
            <option value="conservative">Conservative (bid/ask)</option><option value="mid">Mid</option>
            <option value="spread_fraction">Spread fraction</option>
          </select>
        </Field>
        {f.fill === 'spread_fraction' ? (
          <Num id="bt-spread" label="Spread fraction (0–1)" value={f.spread_fraction} step="0.05"
            hint="Mid moved against you by fraction × half the spread. 0 = mid, 1 = the bid or ask." onChange={(v) => set({ spread_fraction: v })} />
        ) : null}
        <Num id="bt-assign" label="Assignment fee ($)" value={f.assignment_fee} step="0.01" onChange={(v) => set({ assignment_fee: v })} />
      </div>
    </fieldset>
  );
}

export function SizingSection({ f, set }) {
  return (
    <fieldset>
      <legend>Sizing</legend>
      <div className="row">
        <Num id="bt-capital" label="Starting capital ($)" value={f.capital} step="1000" onChange={(v) => set({ capital: v })} />
        <Field id="bt-sizing-mode" label="Sizing mode">
          <select id="bt-sizing-mode" value={f.sizing_mode} onChange={(e) => set({ sizing_mode: e.target.value })}>
            <option value="fixed">Fixed contracts</option><option value="risk_pct">Risk % of equity</option>
          </select>
        </Field>
        {f.sizing_mode === 'fixed' ? (
          <Num id="bt-contracts" label="Contracts per entry" value={f.contracts} step="1" min="1" max="100" onChange={(v) => set({ contracts: v })} />
        ) : (
          <Num id="bt-risk" label="Risk % of equity" value={f.risk_pct} step="0.5" onChange={(v) => set({ risk_pct: v })}
            hint="Undefined-risk shorts use the buying-power estimate as max loss." />
        )}
      </div>
    </fieldset>
  );
}
