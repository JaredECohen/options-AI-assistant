// Backtest form model: defaults, conversion to/from the BacktestRequest wire
// shape (docs/BACKTESTING.md), validation, and localStorage persistence.

export const STORAGE_KEY = 'oc.backtest.config.v1';
export const TERMINAL = ['succeeded', 'failed', 'cancelled', 'dead'];
export const EXIT_KEYS = ['profit_target_pct', 'stop_loss_pct', 'exit_dte', 'max_hold_days'];

export const FALLBACK_REQUEST = {
  underlying: 'SPY',
  start: '',
  end: '',
  strategy: 'short_put',
  legs: [{ right: 'put', side: 'sell', ratio: 1, strike: { method: 'delta', value: 0.3 } }],
  stock: null,
  entry: { dte_target: 45, dte_min: 30, dte_max: 60, expirations: 'any', frequency: 'weekly', weekday: 0,
    max_open: 3, min_iv: null, max_iv: null, min_credit: null, delta_tolerance: 0.05 },
  exit: { profit_target_pct: 50, stop_loss_pct: 200, exit_dte: 21, max_hold_days: null },
  costs: { commission_per_contract: 0.65, fill: 'conservative', spread_fraction: 0.25, assignment_fee: 0 },
  sizing: { capital: 100000, mode: 'fixed', contracts: 1, risk_pct: 5 },
};

// ---- dates (all ISO yyyy-mm-dd strings, computed in UTC to avoid DST drift) ----
const pad = (n) => String(n).padStart(2, '0');
export const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export function yesterday(now = new Date()) {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  return ymd(d);
}
const parse = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
};
const fmtUtc = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
export function addMonths(iso, months) {
  const d = parse(iso);
  const day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + months);
  const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return fmtUtc(d);
}
export function rangeStart(key, end) {
  if (key === 'YTD') return `${end.slice(0, 4)}-01-01`;
  const months = { '6M': 6, '1Y': 12, '2Y': 24, '3Y': 36 }[key];
  return addMonths(end, -months);
}
export const RANGES = ['6M', '1Y', '2Y', '3Y', 'YTD'];

// ---- legs ----
export const blankLeg = (index = 0) => ({ right: 'put', side: 'sell', ratio: '1', method: 'delta', value: '0.16', from_leg: String(Math.max(0, index - 1)) });

const str = (v) => (v === null || v === undefined ? '' : String(v));
export function legFromApi(l) {
  const s = l.strike || {};
  return { right: l.right, side: l.side, ratio: str(l.ratio ?? 1), method: s.method || 'delta', value: str(s.value),
    from_leg: str(s.from_leg ?? 0) };
}
function legToApi(l) {
  let strike;
  if (l.method === 'atm') strike = { method: 'atm' };
  else if (l.method === 'offset') strike = { method: 'offset', from_leg: Number(l.from_leg), value: Number(l.value) };
  else strike = { method: l.method, value: Number(l.value) };
  return { right: l.right, side: l.side, ratio: Number(l.ratio), strike };
}
// Compare legs loosely: ATM ignores value, only offset uses from_leg.
const sig = (l) => [l.right, l.side, Number(l.ratio), l.method,
  l.method === 'atm' ? '' : Number(l.value), l.method === 'offset' ? Number(l.from_leg) : ''].join('|');
export const legsEqual = (a, b) => a.length === b.length && a.every((l, i) => sig(l) === sig(b[i]));

const exitField = (v) => ({ on: v !== null && v !== undefined, val: str(v) });

// ---- request <-> form ----
export function requestToForm(req, base) {
  const r = req || {};
  const d = { ...FALLBACK_REQUEST, ...(base ? formToRequestLoose(base) : {}) };
  const entry = { ...d.entry, ...(r.entry || {}) };
  const exit = { ...d.exit, ...(r.exit || {}) };
  const costs = { ...d.costs, ...(r.costs || {}) };
  const sizing = { ...d.sizing, ...(r.sizing || {}) };
  const legs = (r.legs && r.legs.length ? r.legs : d.legs).map(legFromApi);
  const stock = r.stock !== undefined ? r.stock : d.stock;
  const iv = (v) => (v === null || v === undefined ? '' : String(Number((v * 100).toFixed(4))));
  return {
    underlying: r.underlying || d.underlying,
    start: r.start || d.start || '',
    end: r.end || d.end || '',
    strategy: r.strategy || d.strategy,
    basePreset: r.strategy || d.strategy,
    legs,
    stockOn: Boolean(stock),
    shares: str(stock ? stock.shares_per_contract : 100),
    dte_target: str(entry.dte_target), dte_min: str(entry.dte_min), dte_max: str(entry.dte_max),
    expirations: entry.expirations, frequency: entry.frequency, weekday: str(entry.weekday ?? 0),
    max_open: str(entry.max_open), min_iv: iv(entry.min_iv), max_iv: iv(entry.max_iv),
    min_credit: str(entry.min_credit), delta_tolerance: str(entry.delta_tolerance),
    exit: Object.fromEntries(EXIT_KEYS.map((k) => [k, exitField(exit[k])])),
    commission: str(costs.commission_per_contract), fill: costs.fill, spread_fraction: str(costs.spread_fraction),
    assignment_fee: str(costs.assignment_fee),
    capital: str(sizing.capital), sizing_mode: sizing.mode, contracts: str(sizing.contracts), risk_pct: str(sizing.risk_pct),
  };
}

const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v));

export function formToRequest(f) {
  const nullable = (v, scale = 1) => (v === '' ? null : Number((Number(v) / scale).toFixed(6)));
  return {
    underlying: f.underlying.trim().toUpperCase(),
    start: f.start,
    end: f.end,
    strategy: f.strategy,
    legs: f.legs.map(legToApi),
    stock: f.stockOn ? { shares_per_contract: Number(f.shares) } : null,
    entry: {
      dte_target: Number(f.dte_target), dte_min: Number(f.dte_min), dte_max: Number(f.dte_max),
      expirations: f.expirations, frequency: f.frequency, weekday: Number(f.weekday), max_open: Number(f.max_open),
      min_iv: nullable(f.min_iv, 100), max_iv: nullable(f.max_iv, 100), min_credit: nullable(f.min_credit),
      delta_tolerance: Number(f.delta_tolerance),
    },
    exit: Object.fromEntries(EXIT_KEYS.map((k) => [k, f.exit[k].on && f.exit[k].val !== '' ? Number(f.exit[k].val) : null])),
    costs: { commission_per_contract: Number(f.commission), fill: f.fill, spread_fraction: Number(f.spread_fraction),
      assignment_fee: Number(f.assignment_fee) },
    sizing: { capital: Number(f.capital), mode: f.sizing_mode, contracts: Number(f.contracts), risk_pct: Number(f.risk_pct) },
  };
}
// Tolerant version used only to seed defaults from a possibly odd stored form.
function formToRequestLoose(f) {
  try { return formToRequest(f); } catch { return {}; }
}

// Apply a server preset (legs, stock, exit) on top of the current form.
export function applyPreset(f, preset) {
  const legs = preset.legs && preset.legs.length ? preset.legs.map(legFromApi) : f.legs;
  const exit = preset.exit ? Object.fromEntries(EXIT_KEYS.map((k) => [k, exitField(preset.exit[k])])) : f.exit;
  return {
    ...f, strategy: preset.key, basePreset: preset.key, legs, exit,
    stockOn: Boolean(preset.stock), shares: preset.stock ? str(preset.stock.shares_per_contract) : f.shares,
  };
}

// Keep the strategy label honest after leg edits.
export function relabel(f, presets) {
  const base = (presets || []).find((p) => p.key === f.basePreset);
  const same = base && base.legs && base.legs.length && legsEqual(f.legs, base.legs.map(legFromApi));
  return { ...f, strategy: same ? base.key : 'custom' };
}

// ---- validation ----
const isInt = (v) => Number.isInteger(Number(v)) && v !== '';
const finite = (v) => v !== '' && Number.isFinite(Number(v));

export function validate(f, now = new Date()) {
  const errors = [];
  const add = (msg) => errors.push(msg);
  if (!/^[A-Za-z.]{1,10}$/.test(f.underlying.trim())) add('Underlying must be 1-10 letters.');
  if (!f.start || !f.end) add('Start and end dates are required.');
  else {
    if (f.start >= f.end) add('Start date must be before the end date.');
    if (f.end > yesterday(now)) add('End date cannot be later than yesterday.');
    if (f.start < f.end && addMonths(f.start, 60) < f.end) add('The date range cannot exceed 5 years.');
  }
  if (f.legs.length < 1) add('Add at least one leg.');
  if (f.legs.length > 4) add('A strategy can have at most 4 legs.');
  f.legs.forEach((l, i) => {
    const n = i + 1;
    if (!isInt(l.ratio) || Number(l.ratio) < 1 || Number(l.ratio) > 10) add(`Leg ${n}: ratio must be a whole number from 1 to 10.`);
    if (l.method === 'atm') return;
    if (!finite(l.value)) { add(`Leg ${n}: enter a strike value.`); return; }
    const v = Number(l.value);
    if (l.method === 'delta' && (v < 0.01 || v > 0.99)) add(`Leg ${n}: delta must be between 0.01 and 0.99.`);
    if (l.method === 'pct_otm' && (v < -50 || v > 50)) add(`Leg ${n}: % OTM must be between -50 and 50.`);
    if (l.method === 'offset') {
      if (v < -500 || v > 500) add(`Leg ${n}: offset must be between -500 and 500 dollars.`);
      if (!isInt(l.from_leg) || Number(l.from_leg) >= i || Number(l.from_leg) < 0) {
        add(`Leg ${n}: an offset must reference an earlier leg.`);
      }
    }
  });
  if (f.stockOn && (!isInt(f.shares) || Number(f.shares) < 1)) add('Shares per contract must be a positive whole number.');
  const [t, lo, hi] = [f.dte_target, f.dte_min, f.dte_max];
  if (![t, lo, hi].every(finite)) add('DTE target, min and max are required.');
  else {
    if (!(Number(lo) <= Number(t) && Number(t) <= Number(hi))) add('DTE must satisfy min ≤ target ≤ max.');
    if (Number(hi) > 365) add('DTE max cannot exceed 365.');
    if (Number(lo) < 0) add('DTE min cannot be negative.');
  }
  if (!isInt(f.max_open) || Number(f.max_open) < 1) add('Max open positions must be at least 1.');
  if (f.min_iv !== '' && f.max_iv !== '' && Number(f.min_iv) > Number(f.max_iv)) add('Min IV cannot exceed max IV.');
  if (!finite(f.delta_tolerance) || Number(f.delta_tolerance) <= 0) add('Delta tolerance must be positive.');
  EXIT_KEYS.forEach((k) => {
    if (f.exit[k].on && !(finite(f.exit[k].val) && Number(f.exit[k].val) >= 0)) add(`Enter a value for ${EXIT_LABELS[k]} or turn it off.`);
  });
  if (!finite(f.commission) || Number(f.commission) < 0) add('Commission must be zero or more.');
  if (f.fill === 'spread_fraction' && !(finite(f.spread_fraction) && Number(f.spread_fraction) >= 0 && Number(f.spread_fraction) <= 1)) {
    add('Spread fraction must be between 0 and 1.');
  }
  if (!finite(f.assignment_fee) || Number(f.assignment_fee) < 0) add('Assignment fee must be zero or more.');
  if (!finite(f.capital) || Number(f.capital) < 1000 || Number(f.capital) > 100000000) add('Capital must be between 1,000 and 100,000,000.');
  if (f.sizing_mode === 'fixed' && (!isInt(f.contracts) || Number(f.contracts) < 1 || Number(f.contracts) > 100)) {
    add('Contracts must be a whole number from 1 to 100.');
  }
  if (f.sizing_mode === 'risk_pct' && !(finite(f.risk_pct) && Number(f.risk_pct) > 0 && Number(f.risk_pct) <= 100)) {
    add('Risk % must be greater than 0 and at most 100.');
  }
  return errors;
}

export const EXIT_LABELS = {
  profit_target_pct: 'profit target', stop_loss_pct: 'stop loss', exit_dte: 'exit at DTE', max_hold_days: 'max hold days',
};

// ---- persistence (every access guarded) ----
export function loadStored() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const f = JSON.parse(raw);
    if (!f || !Array.isArray(f.legs) || !f.exit) return null;
    return f;
  } catch {
    return null;
  }
}
export function saveStored(f) {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(f)); } catch { /* storage unavailable */ }
}

export { num };
