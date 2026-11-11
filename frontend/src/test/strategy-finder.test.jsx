// Strategy finder page and its hand-offs to the builder and the backtester.
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import Builder from '../components/Builder.jsx';
import StrategyFinder, { legText, money } from '../components/StrategyFinder.jsx';
import { mockFetch } from './fetchMock.js';

const curve = [{ price: 700, payoff: -1534 }, { price: 752, payoff: -1534 }, { price: 790, payoff: 2266 }, { price: 820, payoff: 2266 }];
const candidate = (rank, key, name, extra = {}) => ({
  rank, key, name, type: 'debit', undefined_risk: false, fit_score: 1,
  legs: [
    { instrument: 'option', option_type: 'call', side: 'buy', strike: 752, expiration: '2026-12-18', quantity: 1, premium: 17.11, multiplier: 100 },
    { instrument: 'option', option_type: 'call', side: 'sell', strike: 790, expiration: '2026-12-18', quantity: 1, premium: 1.77, multiplier: 100 },
  ],
  metrics: { net_debit: 1534, net_credit: 0, max_profit: 2266, max_loss: 1534, breakevens: [767.34], pnl_at_view: 2266,
    scenario: 'SPY at $790.05 at expiry', probability_of_profit: 0.3773, return_on_risk: 1.4772, capital: 1534 },
  explanation: { how: 'Buy an at-the-money call and sell a call at your target.', wins: 'Earns its maximum at the target.',
    loses: 'Loses the debit below the lower strike.', fit: 'If your view plays out, this makes $2,266.00.' },
  payoff_curve: curve,
  links: { builder: { ticker: 'SPY', legs: [] }, backtest: { underlying: 'SPY' }, chain: { ticker: 'SPY', expiration: '2026-12-18' } },
  ...extra,
});
const response = (over = {}) => ({
  interpreted: { direction: 'bullish', target_price: null, move_pct: 5, horizon_days: 36, defined_risk_only: true, max_loss: null, owns_shares: false },
  parsed_by: 'rules', ticker: 'SPY', spot: 752.43, expiration: '2026-12-18', dte: 39, atm_iv: 0.164, expected_move: 40.34,
  target_price: 790.05, provider: 'thetadata', data_status: 'live', data_status_label: 'Real-time', needs_direction: false,
  candidates: [candidate(1, 'bull_call_spread', 'Bull call spread'), candidate(2, 'long_call', 'Long call')],
  excluded: [{ name: 'Short strangle', reason: 'undefined risk (you asked for defined risk)' }], method: 'How chosen.',
  ...over,
});

describe('strategy finder', () => {
  it('turns a sentence into ranked, explained candidates with exact numbers', async () => {
    const calls = mockFetch([['/api/strategies/suggest', { body: response() }]]);
    const user = userEvent.setup();
    render(<StrategyFinder />);
    await user.type(screen.getByLabelText('Your view'), 'Up about 5% by mid-December, loss limited');
    await user.click(screen.getByRole('button', { name: 'Find strategies' }));
    const best = await screen.findByRole('article', { name: 'Bull call spread' });
    expect(JSON.parse(calls[0].opts.body)).toEqual({ ticker: 'SPY', text: 'Up about 5% by mid-December, loss limited' });
    expect(within(best).getByText('Best fit')).toBeInTheDocument();
    expect(within(best).getAllByText('$2,266.00', { selector: '.sf-metrics dd' })).toHaveLength(2); // max profit = P&L at view
    expect(within(best).getByText('38%')).toBeInTheDocument();
    expect(within(best).getByText('Buy 1 752 call at $17.11')).toBeInTheDocument();
    expect(within(best).getByText(/Buy an at-the-money call/)).toBeInTheDocument(); // rank 1 opens its explanation
    expect(within(best).getByRole('img', { name: /Payoff at expiration/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Rise' })).toBeChecked();
    expect(screen.getByText(/Short strangle \(undefined risk/)).toBeInTheDocument();
  });

  it('re-runs with edited fields, which win over the text', async () => {
    const calls = mockFetch([['/api/strategies/suggest', { body: response() }]]);
    const user = userEvent.setup();
    render(<StrategyFinder />);
    await user.type(screen.getByLabelText('Your view'), 'bullish');
    await user.click(screen.getByRole('button', { name: 'Find strategies' }));
    await screen.findByRole('article', { name: 'Bull call spread' });
    await user.click(screen.getByRole('radio', { name: 'Stay in a range' }));
    await user.type(screen.getByLabelText('Max loss ($)'), '500');
    await user.click(screen.getByRole('button', { name: 'Update results' }));
    await waitFor(() => expect(calls).toHaveLength(2));
    const sent = JSON.parse(calls[1].opts.body).view;
    expect(sent).toMatchObject({ direction: 'neutral', max_loss: 500, move_pct: 5, horizon_days: 36, defined_risk_only: true });
  });

  it('asks for a direction when it cannot tell, and validates before calling', async () => {
    const calls = mockFetch([['/api/strategies/suggest', { body: response({ needs_direction: true, parsed_by: 'none',
      candidates: [], detail: 'Say whether you expect the price to rise or fall.',
      interpreted: { direction: null, horizon_days: 30, defined_risk_only: true, owns_shares: false } }) }]]);
    const user = userEvent.setup();
    render(<StrategyFinder />);
    await user.click(screen.getByRole('button', { name: 'Find strategies' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Describe what you expect');
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: /I own 100 shares and want income/ }));
    await user.click(screen.getByRole('button', { name: 'Find strategies' }));
    expect(await screen.findByText(/Say whether you expect/)).toBeInTheDocument();
    expect(screen.getByText('Could not tell the direction')).toBeInTheDocument();
  });

  it('hands a candidate to the builder, the backtester and the chain', async () => {
    mockFetch([['/api/strategies/suggest', { body: response() }]]);
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(<StrategyFinder onOpen={onOpen} />);
    await user.type(screen.getByLabelText('Your view'), 'bullish');
    await user.click(screen.getByRole('button', { name: 'Find strategies' }));
    const best = await screen.findByRole('article', { name: 'Bull call spread' });
    await user.click(within(best).getByRole('button', { name: 'Open in builder' }));
    await user.click(within(best).getByRole('button', { name: 'Backtest this' }));
    await user.click(within(best).getByRole('button', { name: 'View in chain' }));
    expect(onOpen.mock.calls.map((c) => [c[0], c[1].key])).toEqual([
      ['builder', 'bull_call_spread'], ['backtest', 'bull_call_spread'], ['chain', 'bull_call_spread']]);
  });

  it('the builder loads a whole structure with premiums left to reprice', async () => {
    mockFetch([['/api/positions', { body: { items: [] } }]]);
    const legs = candidate(1, 'k', 'n').legs;
    render(<Builder position={{ ticker: 'SPY', legs, label: 'Bull call spread', nonce: 1 }} />);
    expect(await screen.findByText(/Loaded "Bull call spread" from the strategy finder/)).toBeInTheDocument();
    expect(screen.getByLabelText('Leg 1 strike')).toHaveValue('752');
    expect(screen.getByLabelText('Leg 2 strike')).toHaveValue('790');
    expect(screen.getByLabelText('Leg 2 side')).toHaveValue('sell');
    expect(screen.getByLabelText('Leg 1 premium')).toHaveValue('');
  });

  it('formats money and legs', () => {
    expect(money('unlimited')).toBe('Unlimited');
    expect(money(-1534)).toBe('-$1,534.00');
    expect(legText({ instrument: 'stock', side: 'buy', quantity: 100, premium: 752.43 })).toBe('Buy 100 shares at $752.43');
  });
});

describe('payoff math', () => {
  it('computes exact expiration P&L from legs and keeps every strike as a point', async () => {
    const { legsPayoff, curveFromLegs } = await import('../components/PayoffChart.jsx');
    const legs = candidate(1, 'k', 'n').legs; // buy 752C @17.11, sell 790C @1.77: debit 15.34
    expect(legsPayoff(legs, 700)).toBeCloseTo(-1534, 6);
    expect(legsPayoff(legs, 767.34)).toBeCloseTo(0, 6);
    expect(legsPayoff(legs, 900)).toBeCloseTo(2266, 6);
    const curve = curveFromLegs(legs, 700, 820, 10);
    expect(curve.map((p) => p.price)).toEqual(expect.arrayContaining([752, 790]));
    expect(legsPayoff([{ instrument: 'stock', side: 'buy', quantity: 100, premium: 100 }], 110)).toBe(1000);
  });
});

describe('chain hand-off', () => {
  it('opens the structure expiry with every strike and both sides, and marks the legs', async () => {
    const { default: ChainViewer } = await import('../components/ChainViewer.jsx');
    const row = (k) => ({ strike: k, call: { bid: 1, ask: 1.1, multiplier: 100 }, put: { bid: 1, ask: 1.1, multiplier: 100 } });
    const calls = mockFetch([
      ['/api/expirations', { body: { expirations: ['2026-11-20', '2026-12-11'] } }],
      [/\/api\/chain/, { body: { ticker: 'SPY', expiration: '2026-12-11', underlying_price: 752, atm_strike: 752,
        chain: [710, 725, 752, 780, 795].map(row), total_strikes: 5, data_status: 'live', units: {} } }],
    ]);
    render(<ChainViewer focus={{ ticker: 'SPY', expiration: '2026-12-11', strikes: [710, 725, 780, 795], label: 'Iron condor', nonce: 1 }} />);
    expect(await screen.findByTestId('chain-picked')).toHaveTextContent('Iron condor, strikes 710 / 725 / 780 / 795');
    const chainCall = calls.find((c) => c.url.includes('/api/chain'));
    expect(chainCall.url).toContain('expiration=2026-12-11');
    expect(chainCall.url).toContain('side=both');
    expect(chainCall.url).toMatch(/strikes=(500|\d{3,})/); // every strike, not the saved depth
    expect(document.querySelectorAll('table.chain tr.picked')).toHaveLength(4);
  });
});
