// Regressions found by /qa on 2026-11-09 (local OptionsCoach against live ThetaData).
import { describe, expect, it } from 'vitest';
import { defaultExpiration, dte, expirationLabel } from '../components/ChainViewer.jsx';
import { reasonText } from '../components/common.jsx';

describe('chain expirations', () => {
  const now = new Date(2026, 10, 9, 20, 0); // Mon 9 Nov 2026, 8 PM local
  it('labels each expiration with its weekday and days to expiry', () => {
    expect(dte('2026-11-10', now)).toBe(1);
    expect(expirationLabel('2026-11-13', now)).toBe('2026-11-13 (Fri) · 4d');
  });
  it('opens on the first expiry a week or more out, not a 0-1 DTE chain', () => {
    // Regression: ISSUE-006. The viewer opened on that day's already-expired 0DTE chain.
    expect(defaultExpiration(['2026-11-09', '2026-11-10', '2026-11-13', '2026-11-16', '2026-11-20'], now)).toBe('2026-11-16');
    expect(defaultExpiration(['2026-11-10'], now)).toBe('2026-11-10');
    expect(defaultExpiration([], now)).toBe('');
  });
});

describe('reason codes', () => {
  it('words server reason codes instead of printing them raw', () => {
    // Regression: ISSUE-004. Compare and Screener printed Python class names.
    expect(reasonText('market_data_unavailable')).toBe('market data unavailable');
    expect(reasonText('insufficient_history')).toBe('needs about a year of IV history');
    expect(reasonText('some_new_code')).toBe('some new code');
    expect(reasonText(null)).toBe('unavailable');
  });
});
