import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { api, ApiError, setTokenProvider } from '../api.js';
import Builder, { PayoffResult, TEMPLATES, validateLegs } from '../components/Builder.jsx';
import ChainViewer from '../components/ChainViewer.jsx';
import Coach, { servedByLabel } from '../components/Coach.jsx';
import Backtests from '../components/Backtests.jsx';
import { mockFetch } from './fetchMock.js';

const quote = (k, t) => ({ option_type: t, strike: k, bid: 1, ask: 1.2, volume: null, open_interest: null,
  implied_volatility: null, multiplier: 100, adjusted: false, quote_time: '1970-01-01T00:00:00+00:00' });
const chain = (page, strikes, { side = 'both', atm = null, basis = 'underlying', underlying = 100, total = 40 } = {}) => ({
  ticker: 'SPY', expiration: '2026-12-18', underlying_price: underlying, page, page_size: strikes.length, total_strikes: total,
  has_more: false, atm_strike: atm, atm_basis: atm == null ? null : basis, strikes_each_side: 10,
  provider: 'mock', data_status: 'synthetic', data_status_label: 'Synthetic demo data — not market prices',
  freshness: { as_of: null, stale: null }, units: { prices: 'USD per share' },
  chain: strikes.map((k) => ({
    strike: k,
    ...(side !== 'put' ? { call: quote(k, 'call') } : {}),
    ...(side !== 'call' ? { put: quote(k, 'put') } : {}),
  })),
});

describe('api client', () => {
  it('sends the bearer token and surfaces server error codes', async () => {
    const calls = mockFetch([['/api/x', { status: 402, body: { error: 'plan_required', detail: 'plan required' } }]]);
    setTokenProvider(async () => 'tok-123');
    await expect(api('/api/x')).rejects.toMatchObject({ status: 402, code: 'plan_required' });
    expect(calls[0].opts.headers.Authorization).toBe('Bearer tok-123');
    setTokenProvider(null);
    expect(new ApiError(503, { error: 'llm_unavailable', code: 'circuit_open' }).code).toBe('circuit_open');
  });
});

describe('T2 builder', () => {
  it('templates populate structurally correct legs', () => {
    const condor = TEMPLATES['iron condor'](100, '2026-12-18');
    expect(condor.map((l) => `${l.side} ${l.option_type} ${l.strike}`)).toEqual(['buy put 88', 'sell put 93', 'sell call 107', 'buy call 112']);
    const cc = TEMPLATES['covered call'](250, '2026-12-18');
    expect(cc[0]).toMatchObject({ instrument: 'stock', quantity: 100 });
    expect(cc[1].strike).toBe(265);
  });

  it('validates legs, including mixed expirations', () => {
    const ok = TEMPLATES['bull call spread'](100, '2026-12-18');
    expect(validateLegs(ok)).toEqual({});
    const bad = [{ ...ok[0], strike: '' }, { ...ok[1], expiration: '2027-01-15' }];
    const errs = validateLegs(bad);
    expect(errs[bad[0].id]).toContain('strike must be > 0');
    expect(errs.form).toMatch(/one expiration/);
    expect(validateLegs([{ ...ok[0], quantity: 1.5, premium: '-1' }])[ok[0].id]).toEqual(
      ['quantity must be a whole number ≥ 1', 'premium must be ≥ 0 or blank']);
  });

  it('adds, reorders and removes legs and blocks submit while invalid', async () => {
    mockFetch([['/api/positions', { body: { items: [] } }]]);
    const user = userEvent.setup();
    render(<Builder />);
    const submit = screen.getByRole('button', { name: 'Compute payoff' });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText('Ticker'), 'SPY');
    await user.type(screen.getByLabelText('Leg 1 strike'), '100');
    await user.type(screen.getByLabelText('Leg 1 expiration'), '2026-12-18');
    await user.click(screen.getByRole('button', { name: 'Add leg' }));
    expect(screen.getByLabelText('Leg 2 expiration')).toHaveValue('2026-12-18');
    await user.type(screen.getByLabelText('Leg 2 strike'), '110');
    await user.click(screen.getByRole('button', { name: 'Move leg 2 up' }));
    expect(screen.getByLabelText('Leg 1 strike')).toHaveValue('110');
    await user.click(screen.getByRole('button', { name: 'Remove leg 1' }));
    expect(screen.getByLabelText('Leg 1 strike')).toHaveValue('100');
    expect(submit).toBeEnabled();
  });

  it('renders exact T5 bounds, unlimited cases and premium provenance', () => {
    const computed = {
      net_debit: 0, net_credit: 500, max_profit: 500, max_loss: 9500, breakevens: [95],
      bounds: { max_profit: { at_price: 100 }, max_loss: { at_price: 0 }, method: 'analytic' },
      underlying_price: null, data_status: 'synthetic', quote_source: 'user_provided',
      premiums_used: [{ instrument: 'option', side: 'sell', option_type: 'put', strike: 100, premium: 5, premium_source: 'user_entered' }],
      conventions: { units: 'per position', excludes: ['fees'] }, payoff_curve: [{ price: 0, payoff: -9500 }, { price: 150, payoff: 500 }],
    };
    render(<PayoffResult result={{ computed }} />);
    expect(screen.getByTestId('max-loss')).toHaveTextContent('$9,500.00 at $0.00');
    expect(screen.getByText('not available')).toBeInTheDocument();
    expect(screen.getByTestId('data-status')).toHaveTextContent('Synthetic demo data');
    render(<PayoffResult result={{ computed: { ...computed, max_loss: 'unlimited', bounds: { max_loss: { at_price: null } } } }} />);
    expect(screen.getAllByTestId('max-loss')[1]).toHaveTextContent('Unlimited');
  });
});

describe('T1 chain viewer', () => {
  beforeEach(() => window.localStorage.clear());

  const loadSpy = async (user) => {
    await user.type(screen.getByLabelText('Ticker'), 'spy');
    await user.click(screen.getByRole('button', { name: 'Load' }));
    return screen.findByRole('table');
  };

  it('labels synthetic data, centres on the money and never says real-time', async () => {
    const calls = mockFetch([
      ['/api/expirations', { body: { expirations: ['2026-12-18'], data_status: 'synthetic' } }],
      [/\/api\/chain/, { body: chain(1, [95, 100, 105], { atm: 100, total: 21 }) }],
    ]);
    const user = userEvent.setup();
    render(<ChainViewer />);
    const table = await loadSpy(user);
    expect(screen.getByTestId('data-status')).toHaveTextContent('Synthetic demo data');
    expect(document.body.textContent).not.toMatch(/real-time/i);
    const chainCall = calls.find((c) => c.url.includes('/api/chain'));
    expect(chainCall.url).toMatch(/strikes=10/);
    expect(chainCall.url).toMatch(/side=both/);
    const atmRow = within(table).getAllByRole('row').find((r) => r.classList.contains('atm'));
    expect(atmRow).toHaveTextContent('100');
    expect(within(atmRow).getByText('ATM')).toBeInTheDocument();
    expect(screen.getByTestId('chain-summary')).toHaveTextContent('Showing 3 of 21 strikes, centred on 100 (ATM, from underlying)');
    expect(screen.queryByRole('button', { name: 'More strikes' })).toBeNull();
  });

  it('shades in-the-money cells on each side of the strike', async () => {
    mockFetch([
      ['/api/expirations', { body: { expirations: ['2026-12-18'] } }],
      [/\/api\/chain/, { body: chain(1, [95, 100, 105], { atm: 100, underlying: 101 }) }],
    ]);
    const user = userEvent.setup();
    render(<ChainViewer />);
    const table = await loadSpy(user);
    const [, , low, mid, high] = within(table).getAllByRole('row');
    const cells = (row) => within(row).getAllByRole('cell');
    // calls left of the strike are ITM below the underlying, puts right of it above
    expect(cells(low)[0]).toHaveClass('itm');
    expect(cells(low)[6]).not.toHaveClass('itm');
    expect(cells(mid)[0]).toHaveClass('itm');
    expect(cells(high)[0]).not.toHaveClass('itm');
    expect(cells(high)[6]).toHaveClass('itm');
  });

  it('shows only the chosen side and asks the server for it', async () => {
    const calls = mockFetch([
      ['/api/expirations', { body: { expirations: ['2026-12-18'] } }],
      [/\/api\/chain/, (url) => ({ body: chain(1, [95, 100, 105], { atm: 100, side: new URL(url, 'http://x').searchParams.get('side') }) })],
    ]);
    const user = userEvent.setup();
    render(<ChainViewer />);
    await loadSpy(user);
    expect(screen.getByRole('columnheader', { name: 'Calls' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Puts' })).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Puts' }));
    await waitFor(() => expect(calls.at(-1).url).toMatch(/side=put/));
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: 'Calls' })).toBeNull());
    expect(screen.getByRole('columnheader', { name: 'Puts' })).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Calls' }));
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: 'Puts' })).toBeNull());
    expect(calls.at(-1).url).toMatch(/side=call/);
    expect(screen.getByRole('columnheader', { name: 'Calls' })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem('optionscoach.chain.view')).side).toBe('call');
  });

  it('asks for the chosen depth, "All" included, and remembers it', async () => {
    const calls = mockFetch([
      ['/api/expirations', { body: { expirations: ['2026-12-18'] } }],
      [/\/api\/chain/, { body: chain(1, [100], { atm: 100 }) }],
    ]);
    const user = userEvent.setup();
    const { unmount } = render(<ChainViewer />);
    await loadSpy(user);
    await user.selectOptions(screen.getByLabelText('Strikes each side'), '20');
    await waitFor(() => expect(calls.at(-1).url).toMatch(/strikes=20/));
    await user.selectOptions(screen.getByLabelText('Strikes each side'), 'all');
    await waitFor(() => expect(calls.at(-1).url).toMatch(/strikes=500/));
    unmount();
    render(<ChainViewer />);
    expect(screen.getByLabelText('Strikes each side')).toHaveValue('all');
  });

  it('survives storage that throws', async () => {
    const getItem = Storage.prototype.getItem;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.getItem = () => { throw new Error('blocked'); };
    Storage.prototype.setItem = () => { throw new Error('blocked'); };
    try {
      render(<ChainViewer />);
      expect(screen.getByLabelText('Strikes each side')).toHaveValue('10');
      expect(screen.getByRole('radio', { name: 'Both' })).toBeChecked();
    } finally {
      Storage.prototype.getItem = getItem;
      Storage.prototype.setItem = setItem;
    }
  });

  it('shows provider errors and validates input before calling', async () => {
    const calls = mockFetch([['/api/expirations', { status: 503, body: { error: 'market_data_unavailable', detail: 'x' } }]]);
    const user = userEvent.setup();
    render(<ChainViewer />);
    await user.type(screen.getByLabelText('Ticker'), '12$');
    await user.click(screen.getByRole('button', { name: 'Load' }));
    expect(screen.getByRole('alert')).toHaveTextContent('valid ticker');
    expect(calls).toHaveLength(0);
    await user.clear(screen.getByLabelText('Ticker'));
    await user.type(screen.getByLabelText('Ticker'), 'SPY');
    await user.click(screen.getByRole('button', { name: 'Load' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Market data is unavailable');
  });

  it('aborts a superseded chain request', async () => {
    const calls = mockFetch([
      ['/api/expirations', { body: { expirations: ['2026-12-18', '2027-01-15'] } }],
      [/2026-12-18/, { body: chain(1, [1]), delay: 200 }],
      [/2027-01-15/, { body: chain(1, [2]) }],
    ]);
    const user = userEvent.setup();
    render(<ChainViewer />);
    await user.type(screen.getByLabelText('Ticker'), 'SPY');
    await user.click(screen.getByRole('button', { name: 'Load' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('2026-12-18'))).toBe(true));
    await user.selectOptions(screen.getByLabelText('Expiration'), '2027-01-15');
    const first = calls.find((c) => c.url.includes('2026-12-18'));
    expect(first.opts.signal.aborted).toBe(true);
    await screen.findByRole('table');
  });
});

describe('chat and blocked features', () => {
  it('labels who answered and keeps the server conversation id', async () => {
    expect(servedByLabel({ mode: 'heuristic_demo' })).toMatch(/no AI model/);
    expect(servedByLabel({ mode: 'llm', model: 'claude-haiku-5-5', tools_used: ['compute_payoff', 'compute_payoff'] }))
      .toBe('AI answer · claude-haiku-5-5 · used compute_payoff');
    expect(servedByLabel({ mode: 'heuristic_fallback', fallback_reason: 'timeout' })).toMatch(/AI unavailable \(timeout\)/);
    localStorage.clear();
    const calls = mockFetch([['/api/chat', { body: { response_text: 'Summary: x', conversation_id: 'c-1', served_by: { mode: 'deterministic' } } }]]);
    const user = userEvent.setup();
    render(<Coach />);
    await user.type(screen.getByLabelText('Message'), 'what is theta');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Calculated / built-in answer')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'and vega?');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(JSON.parse(calls[1].opts.body)).toMatchObject({ conversation_id: 'c-1' });
    expect(JSON.parse(calls[1].opts.body).session_id).toBeUndefined();
  });

  it('shows model failures as errors, not answers', async () => {
    localStorage.clear();
    mockFetch([['/api/chat', { status: 503, body: { error: 'llm_unavailable', code: 'circuit_open', detail: 'The AI assistant is temporarily paused.' } }]]);
    const user = userEvent.setup();
    render(<Coach />);
    await user.type(screen.getByLabelText('Message'), 'hello');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('temporarily paused');
  });
});

// ---- Backtests (docs/BACKTESTING.md) ----
const leg = (right, side, strike) => ({ right, side, ratio: 1, strike });
const PRESETS = [
  { key: 'short_put', label: 'Short put', kind: 'credit', description: 'Sell one put for premium.',
    legs: [leg('put', 'sell', { method: 'delta', value: 0.3 })], stock: null,
    exit: { profit_target_pct: 50, stop_loss_pct: 200, exit_dte: 21, max_hold_days: null } },
  { key: 'iron_condor', label: 'Iron condor', kind: 'credit', description: 'Sell a put spread and a call spread.',
    legs: [leg('put', 'sell', { method: 'delta', value: 0.16 }), leg('put', 'buy', { method: 'offset', from_leg: 0, value: -5 }),
      leg('call', 'sell', { method: 'delta', value: 0.16 }), leg('call', 'buy', { method: 'offset', from_leg: 2, value: 5 })],
    stock: null, exit: { profit_target_pct: 50, stop_loss_pct: 200, exit_dte: 21, max_hold_days: null } },
  { key: 'covered_call', label: 'Covered call', kind: 'credit', description: 'Own shares, sell a call.',
    legs: [leg('call', 'sell', { method: 'delta', value: 0.3 })], stock: { shares_per_contract: 100 },
    exit: { profit_target_pct: 50, stop_loss_pct: null, exit_dte: 7, max_hold_days: null } },
  { key: 'custom', label: 'Custom', kind: 'credit', description: 'Build your own.', legs: [], stock: null,
    exit: { profit_target_pct: null, stop_loss_pct: null, exit_dte: null, max_hold_days: null } },
];
const DEFAULTS = {
  underlying: 'SPY', start: '2025-01-02', end: '2025-12-31', strategy: 'short_put', legs: [], stock: null,
  entry: { dte_target: 45, dte_min: 30, dte_max: 60, expirations: 'any', frequency: 'weekly', weekday: 0, max_open: 3,
    min_iv: null, max_iv: null, min_credit: null, delta_tolerance: 0.05 },
  exit: { profit_target_pct: 50, stop_loss_pct: 200, exit_dte: 21, max_hold_days: null },
  costs: { commission_per_contract: 0.65, fill: 'conservative', spread_fraction: 0.25, assignment_fee: 0 },
  sizing: { capital: 100000, mode: 'fixed', contracts: 1, risk_pct: 5 },
};
const presetsBody = (over = {}) => ({ presets: PRESETS, defaults: DEFAULTS, available: true, blocked_by: [], ...over });
const trade = (id, pnl, reason) => ({ id, entry_date: `2025-01-${String(id + 5).padStart(2, '0')}`, exit_date: '2025-01-28',
  expiration: '2025-02-21', contracts: 1, entry_value: 210, exit_value: -95, pnl, exit_reason: reason, days_held: 17 + id,
  underlying_entry: 590.1, underlying_exit: 601.3,
  legs: [{ right: 'put', side: 'sell', strike: 560 + id, ratio: 1, entry_price: 2.1, exit_price: 0.95, entry_delta: -0.16 }] });
const RESULT = {
  config: { ...DEFAULTS, strategy: 'short_put', legs: PRESETS[0].legs },
  summary: { total_return: 0.12, cagr: 0.11, sharpe: 1.23, sortino: 1.6, max_drawdown: 0.08, win_rate: 0.8, cvar_95: 0.01,
    trades: 3, avg_pnl: 120.5, median_pnl: 140, best_trade: 410, worst_trade: -880, profit_factor: 2.1, avg_days_held: 21.3,
    final_equity: 112000, time_in_market: 0.9, max_buying_power: 12000, total_commissions: 270.4 },
  benchmark: { name: 'Buy and hold SPY', total_return: 0.15, cagr: 0.14, sharpe: 1.0, max_drawdown: 0.12 },
  equity: [
    { date: '2025-01-02', equity: 100000, benchmark: 100000, drawdown: 0 },
    { date: '2025-06-02', equity: 106000, benchmark: 104000, drawdown: 0.02 },
    { date: '2025-12-31', equity: 112000, benchmark: 115000, drawdown: 0 }],
  monthly_returns: [{ month: '2025-01', return: 0.012 }, { month: '2025-02', return: -0.02 }, { month: '2025-03', return: null }],
  trades: [trade(1, 112.4, 'profit_target'), trade(2, -300, 'stop_loss'), trade(3, 50, 'profit_target')],
  exit_reasons: { profit_target: 2, stop_loss: 1 },
  diagnostics: { skipped_entries: { no_chain: 1, iv_filter: 5 }, stale_marks: 4, requests: 120, cache_hits: 30,
    data_source: 'ThetaData end-of-day', runtime_seconds: 95.2 },
  caveats: ['End-of-day data means intraday stop breaches are not seen.'], synthetic: false,
};
const job = (status, extra = {}) => ({ id: 'job1', kind: 'backtest', status, attempts: 1, max_attempts: 3, error: null,
  result: null, cancel_requested: false, ...extra });
const ESTIMATE = { entry_days: 52, est_requests: 260, est_minutes: 2.5, trading_days: 250 };

// Order matters: first match wins and string routes match by prefix.
function btRoutes({ presets = presetsBody(), jobs, post, history = [] } = {}) {
  return [
    ['/api/backtests/presets', { body: presets }],
    ['/api/backtests/estimate', { body: ESTIMATE }],
    [/\/api\/jobs\/job1\/cancel$/, () => ({ body: job('running', { cancel_requested: true }) })],
    [/\/api\/jobs\/job1$/, jobs || (() => ({ body: job('succeeded', { result: RESULT }) }))],
    ['/api/backtests', (url, opts) => (opts.method === 'POST'
      ? (post ? post() : { body: job('queued') }) : { body: { backtests: history } })],
  ];
}
const posts = (calls) => calls.filter((c) => c.opts.method === 'POST' && c.url === '/api/backtests');

describe('Backtests', () => {
  beforeEach(() => { try { window.localStorage.clear(); } catch { /* ignore */ } });

  it('loads presets and choosing a preset fills the legs', async () => {
    mockFetch(btRoutes());
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    const select = await screen.findByLabelText('Strategy preset');
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(PRESETS.length));
    await user.selectOptions(select, 'iron_condor');
    expect(screen.getByTestId('preset-desc')).toHaveTextContent('Sell a put spread and a call spread.');
    expect(screen.getByLabelText('Leg 4 right')).toHaveValue('call');
    expect(screen.getByLabelText('Leg 4 side')).toHaveValue('buy');
    expect(screen.getByLabelText('Leg 2 strike method')).toHaveValue('offset');
    expect(screen.getByLabelText('Leg 2 offset from leg')).toHaveValue('0');
    expect(screen.getByLabelText('Leg 1 strike value')).toHaveValue(0.16);
    // Editing a leg turns the strategy into a custom one.
    await user.selectOptions(screen.getByLabelText('Leg 1 side'), 'buy');
    expect(screen.getByLabelText('Strategy preset')).toHaveValue('custom');
    // Restoring it restores the label.
    await user.selectOptions(screen.getByLabelText('Leg 1 side'), 'sell');
    expect(screen.getByLabelText('Strategy preset')).toHaveValue('iron_condor');
  });

  it('shows the blocked notice but keeps the form usable', async () => {
    mockFetch(btRoutes({ presets: presetsBody({ available: false, blocked_by: ['No history provider configured'] }) }));
    render(<Backtests pollMs={10} />);
    expect(await screen.findByTestId('bt-blocked')).toHaveTextContent('No history provider configured');
    expect(screen.getByLabelText('Underlying')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeEnabled();
  });

  it('blocks submit when the dates are out of order and sends no request', async () => {
    const calls = mockFetch(btRoutes());
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2025-06-01' } });
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2025-01-01' } });
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    expect(await screen.findByTestId('bt-errors')).toHaveTextContent('Start date must be before the end date.');
    expect(posts(calls)).toHaveLength(0);
    expect(calls.some((c) => c.url === '/api/backtests/estimate')).toBe(false);
  });

  it('rejects DTE out of order', async () => {
    const calls = mockFetch(btRoutes());
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    fireEvent.change(screen.getByLabelText('DTE min'), { target: { value: '70' } });
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    expect(await screen.findByTestId('bt-errors')).toHaveTextContent('min ≤ target ≤ max');
    expect(posts(calls)).toHaveLength(0);
  });

  it('rejects an offset leg that no longer has an earlier leg to reference', async () => {
    const calls = mockFetch(btRoutes());
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    const select = await screen.findByLabelText('Strategy preset');
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(PRESETS.length));
    await user.selectOptions(select, 'iron_condor');
    await user.click(screen.getByRole('button', { name: 'Remove leg 1' })); // leg 2 (offset from leg 1) becomes leg 1
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    expect(await screen.findByTestId('bt-errors')).toHaveTextContent('Leg 1: an offset must reference an earlier leg.');
    expect(posts(calls)).toHaveLength(0);
    // Only earlier legs are offered as references.
    expect(within(screen.getByLabelText('Leg 3 offset from leg')).getAllByRole('option')).toHaveLength(2);
  });

  it('sends a normalized request, shows the estimate, polls until success and renders results', async () => {
    let polls = 0;
    const calls = mockFetch(btRoutes({
      jobs: () => {
        polls += 1;
        return polls < 3
          ? { body: job('running', { result: { progress: { pct: 23.5, day: '2025-03-04', trades: 4, open: 2, requests: 40 } } }) }
          : { body: job('succeeded', { result: RESULT }) };
      },
    }));
    const user = userEvent.setup();
    render(<Backtests pollMs={15} />);
    const select = await screen.findByLabelText('Strategy preset');
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(PRESETS.length));
    await user.selectOptions(select, 'short_put');
    fireEvent.change(screen.getByLabelText('Min IV (%)'), { target: { value: '15' } });
    await waitFor(() => expect(screen.getByTestId('bt-estimate')).toHaveTextContent('~52 entries, ~260 data requests, ~2.5 min'),
      { timeout: 2000 });
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));

    await waitFor(() => expect(screen.getByTestId('bt-progress-text')).toHaveTextContent('23.5%'));
    expect(screen.getByTestId('bt-progress-text')).toHaveTextContent('day 2025-03-04');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();

    const results = await screen.findByTestId('bt-results');
    expect(within(results).getByTestId('metric-total_return')).toHaveTextContent('12.0%');
    expect(within(results).getByTestId('metric-total_return')).toHaveTextContent('Benchmark 15.0%');
    expect(within(results).getByTestId('metric-sharpe')).toHaveTextContent('1.23');
    expect(within(results).getByTestId('metric-total_commissions')).toHaveTextContent('$270.40');
    expect(within(results).getByRole('img', { name: /Equity curve from 2025-01-02 to 2025-12-31/ })).toBeInTheDocument();
    const table = within(results).getByRole('table', { name: 'Trades' });
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(screen.getByText('End-of-day data means intraday stop breaches are not seen.')).toBeInTheDocument();

    const body = JSON.parse(posts(calls)[0].opts.body);
    expect(body).toMatchObject({ underlying: 'SPY', strategy: 'short_put', stock: null,
      legs: [{ right: 'put', side: 'sell', ratio: 1, strike: { method: 'delta', value: 0.3 } }],
      entry: { dte_target: 45, min_iv: 0.15, max_iv: null }, exit: { profit_target_pct: 50, max_hold_days: null },
      sizing: { mode: 'fixed', contracts: 1, capital: 100000 } });
    expect(polls).toBeGreaterThanOrEqual(3);
  });

  it('sorts, filters and expands trades', async () => {
    mockFetch(btRoutes());
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    // Re-query the table every time: the results can re-render (the recent-runs list refreshes after a
    // run), and a held reference would then read a detached node. Generous timeout for loaded CI hosts.
    await screen.findByRole('table', { name: 'Trades' }, { timeout: 5000 });
    const table = () => screen.getByRole('table', { name: 'Trades' });
    const pnls = () => within(table()).getAllByRole('row').slice(1).map((r) => r.querySelector('.bt-pnl').textContent);
    await user.click(within(table()).getByRole('button', { name: /^P&L/ }));
    await waitFor(() => expect(pnls()).toEqual(['-$300.00', '$50.00', '$112.40']));
    await user.click(within(table()).getByRole('button', { name: /^P&L/ }));
    await waitFor(() => expect(pnls()).toEqual(['$112.40', '$50.00', '-$300.00']));
    await user.selectOptions(screen.getByLabelText('Filter by exit reason'), 'stop_loss');
    await waitFor(() => expect(pnls()).toEqual(['-$300.00']));
    await user.click(screen.getByRole('button', { name: 'Show legs for trade 2' }));
    expect(screen.getByText('562.00')).toBeInTheDocument();
    expect(screen.getByText('-0.160')).toBeInTheDocument();
  });

  it('shows the error of a failed job', async () => {
    mockFetch(btRoutes({ jobs: () => ({ body: job('failed', { error: 'ThetaData terminal unreachable' }) }) }));
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    expect(await screen.findByText(/ThetaData terminal unreachable/)).toBeInTheDocument();
    expect(screen.queryByTestId('bt-results')).not.toBeInTheDocument();
  });

  it('cancels a running job', async () => {
    const calls = mockFetch(btRoutes({ jobs: () => ({ body: job('running', { result: { progress: { pct: 5 } } }) }) }));
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/job1/cancel' && c.opts.method === 'POST')).toBe(true));
  });

  it('surfaces a 503 from the server and stops polling on unmount', async () => {
    const calls = mockFetch(btRoutes({ post: () => ({ status: 503, body: { error: 'backtest_unavailable',
      detail: 'Backtesting is not available.', blocked_by: ['no history provider'] } }) }));
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no history provider');
    expect(calls.some((c) => c.url.startsWith('/api/jobs/'))).toBe(false);
  });

  it('stops polling when unmounted', async () => {
    const calls = mockFetch(btRoutes({ jobs: () => ({ body: job('running', { result: { progress: { pct: 5 } } }) }) }));
    const user = userEvent.setup();
    const { unmount } = render(<Backtests pollMs={10} />);
    await screen.findByLabelText('Strategy preset');
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/jobs/job1').length).toBeGreaterThan(0));
    unmount();
    const n = calls.length;
    await new Promise((r) => setTimeout(r, 80));
    expect(calls.length).toBe(n);
  });

  it('loads a past run and its config back into the form', async () => {
    const config = { ...DEFAULTS, underlying: 'QQQ', strategy: 'iron_condor', legs: PRESETS[1].legs,
      entry: { ...DEFAULTS.entry, min_iv: 0.2 } };
    mockFetch(btRoutes({
      history: [{ id: 'job1', status: 'succeeded', created_at: '2025-02-01T00:00:00Z', underlying: 'QQQ',
        strategy: 'iron_condor', start: '2025-01-02', end: '2025-12-31', summary: { total_return: 0.08, trades: 52 } }],
      jobs: () => ({ body: job('succeeded', { result: { ...RESULT, config } }) }),
    }));
    const user = userEvent.setup();
    render(<Backtests pollMs={10} />);
    await user.click(await screen.findByRole('button', { name: /QQQ iron condor/ }));
    expect(await screen.findByTestId('bt-results')).toBeInTheDocument();
    expect(screen.getByLabelText('Underlying')).toHaveValue('QQQ');
    expect(screen.getByLabelText('Min IV (%)')).toHaveValue(20);
    expect(screen.getByLabelText('Leg 4 side')).toHaveValue('buy');
  });
});
