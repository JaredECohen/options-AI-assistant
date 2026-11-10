import { render, screen, waitFor, within } from '@testing-library/react';
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

  it('backtests show blockers instead of results', async () => {
    mockFetch([['/api/backtests', { status: 503, body: { error: 'backtest_unavailable', detail: 'Backtesting is not available yet.',
      blocked_by: ['engine missing', 'no data'] } }]]);
    const user = userEvent.setup();
    render(<Backtests />);
    await user.click(screen.getByRole('button', { name: 'Run backtest' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('engine missing');
    expect(alert).toHaveTextContent('no data');
  });
});
