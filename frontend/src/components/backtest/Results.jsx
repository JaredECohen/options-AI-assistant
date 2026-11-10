// Backtest results: metric cards, charts, trades table, diagnostics, CSV export.
import { Fragment, useMemo, useState } from 'react';
import { fmt, pct, usd as baseUsd } from '../common.jsx';

// common.usd renders negatives as "$-300.00"; P&L reads better as "-$300.00".
const usd = (v) => (typeof v === 'number' && v < 0 ? `-${baseUsd(-v)}` : baseUsd(v));
import { DrawdownChart, EquityChart, ExitBars, MonthlyHeatmap } from './Charts.jsx';

const PAGE = 50;

const METRICS = [
  ['total_return', 'Total return', 'pct'], ['cagr', 'CAGR', 'pct'], ['sharpe', 'Sharpe', 'num'],
  ['sortino', 'Sortino', 'num'], ['max_drawdown', 'Max drawdown', 'pct'], ['win_rate', 'Win rate', 'pct'],
  ['cvar_95', 'CVaR 95', 'pct'], ['trades', 'Trades', 'int'], ['avg_pnl', 'Avg P&L', 'usd'],
  ['profit_factor', 'Profit factor', 'num'], ['avg_days_held', 'Avg days held', 'num1'],
  ['final_equity', 'Final equity', 'usd'], ['max_buying_power', 'Max buying power', 'usd'],
  ['total_commissions', 'Total commissions', 'usd'],
];
const BENCH_KEYS = ['total_return', 'cagr', 'sharpe', 'max_drawdown'];

function format(kind, v) {
  if (v === null || v === undefined) return '—';
  switch (kind) {
    case 'pct': return pct(v);
    case 'usd': return usd(v);
    case 'int': return String(v);
    case 'num1': return fmt(v, 1);
    default: return fmt(v, 2);
  }
}
const tone = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');

export function MetricCards({ summary, benchmark }) {
  return (
    <div className="bt-cards" role="list">
      {METRICS.map(([key, label, kind]) => {
        const v = summary[key];
        const b = benchmark && BENCH_KEYS.includes(key) ? benchmark[key] : undefined;
        return (
          <div className="bt-card" role="listitem" key={key} data-testid={`metric-${key}`}>
            <span className="bt-card-label">{label}</span>
            <strong className={['pct', 'usd'].includes(kind) && ['total_return', 'cagr', 'avg_pnl'].includes(key) ? tone(v) : ''}>
              {format(kind, v)}
            </strong>
            {b !== undefined && b !== null ? <span className="muted bt-card-bench">Benchmark {format(kind, b)}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

// ---- CSV ----
const csvCell = (v) => {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
export function toCsv(columns, rows) {
  return [columns.join(','), ...rows.map((r) => columns.map((c) => csvCell(r[c])).join(','))].join('\n');
}
export function tradesCsv(trades) {
  const cols = ['id', 'entry_date', 'exit_date', 'expiration', 'contracts', 'entry_value', 'exit_value', 'pnl', 'exit_reason',
    'days_held', 'underlying_entry', 'underlying_exit'];
  return toCsv(cols, trades);
}
export function equityCsv(equity) {
  return toCsv(['date', 'equity', 'benchmark', 'drawdown'], equity);
}
function download(name, text) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch {
    /* download unsupported */
  }
}

// ---- trades ----
const COLS = [
  ['id', '#'], ['entry_date', 'Entry'], ['exit_date', 'Exit'], ['expiration', 'Expiration'], ['contracts', 'Qty'],
  ['entry_value', 'Entry value'], ['exit_value', 'Exit value'], ['pnl', 'P&L'], ['exit_reason', 'Exit reason'],
  ['days_held', 'Days'],
];

export function TradesTable({ trades }) {
  const [sort, setSort] = useState({ key: 'entry_date', dir: 1 });
  const [reason, setReason] = useState('');
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState(() => new Set());
  const reasons = useMemo(() => [...new Set(trades.map((t) => t.exit_reason))].sort(), [trades]);
  const rows = useMemo(() => {
    const list = reason ? trades.filter((t) => t.exit_reason === reason) : trades.slice();
    const { key, dir } = sort;
    list.sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * dir;
    });
    return list;
  }, [trades, sort, reason]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const slice = rows.slice(cur * PAGE, cur * PAGE + PAGE);
  const toggleSort = (key) => {
    setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }));
    setPage(0);
  };
  const toggleOpen = (id) => setOpen((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  return (
    <div>
      <div className="row bt-trade-tools">
        <div>
          <label htmlFor="bt-reason">Filter by exit reason</label>
          <select id="bt-reason" value={reason} onChange={(e) => { setReason(e.target.value); setPage(0); }}>
            <option value="">All reasons</option>
            {reasons.map((r) => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
          </select>
        </div>
        <p className="muted" role="status">{rows.length} of {trades.length} trades</p>
      </div>
      <div className="table-wrap">
        <table className="bt-trades">
          <caption className="muted">Trades</caption>
          <thead>
            <tr>
              <th scope="col"><span className="sr-only">Legs</span></th>
              {COLS.map(([key, label]) => (
                <th key={key} scope="col" aria-sort={sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" className="linklike" onClick={() => toggleSort(key)}>
                    {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slice.map((t) => (
              <Fragment key={t.id}>
                <tr>
                  <td>
                    <button type="button" className="linklike" aria-expanded={open.has(t.id)}
                      aria-label={`${open.has(t.id) ? 'Hide' : 'Show'} legs for trade ${t.id}`} onClick={() => toggleOpen(t.id)}>
                      {open.has(t.id) ? '▾' : '▸'}
                    </button>
                  </td>
                  <td>{t.id}</td><td>{t.entry_date}</td><td>{t.exit_date}</td><td>{t.expiration}</td><td>{t.contracts}</td>
                  <td>{usd(t.entry_value)}</td><td>{usd(t.exit_value)}</td>
                  <td className={`bt-pnl ${tone(t.pnl)}`}>{usd(t.pnl)}</td>
                  <td>{String(t.exit_reason).replace(/_/g, ' ')}</td><td>{t.days_held}</td>
                </tr>
                {open.has(t.id) ? (
                  <tr className="bt-legs-row">
                    <td colSpan={COLS.length + 1}>
                      <table className="bt-legs">
                        <thead>
                          <tr><th scope="col">Side</th><th scope="col">Right</th><th scope="col">Strike</th><th scope="col">Ratio</th>
                            <th scope="col">Entry price</th><th scope="col">Exit price</th><th scope="col">Entry delta</th></tr>
                        </thead>
                        <tbody>
                          {(t.legs || []).map((l, i) => (
                            <tr key={i}>
                              <td>{l.side}</td><td>{l.right}</td><td>{fmt(l.strike)}</td><td>{l.ratio}</td>
                              <td>{fmt(l.entry_price)}</td><td>{fmt(l.exit_price)}</td><td>{fmt(l.entry_delta, 3)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="muted">Underlying {fmt(t.underlying_entry)} → {fmt(t.underlying_exit)}
                        {t.stock_pnl != null ? ` · Stock P&L ${usd(t.stock_pnl)}` : ''}
                        {t.commissions != null ? ` · Commissions ${usd(t.commissions)}` : ''}
                        {t.buying_power != null ? ` · Buying power ${usd(t.buying_power)}` : ''}</p>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
            {!slice.length ? <tr><td colSpan={COLS.length + 1} className="muted">No trades.</td></tr> : null}
          </tbody>
        </table>
      </div>
      {pages > 1 ? (
        <nav className="bt-pager" aria-label="Trades pages">
          <button type="button" className="secondary inline-button" disabled={cur === 0} onClick={() => setPage(cur - 1)}>Previous</button>
          <span className="muted">Page {cur + 1} of {pages}</span>
          <button type="button" className="secondary inline-button" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>Next</button>
        </nav>
      ) : null}
    </div>
  );
}

function Diagnostics({ d }) {
  if (!d) return null;
  const skipped = Object.entries(d.skipped_entries || {});
  return (
    <div>
      <dl className="grid bt-diag">
        {d.strategy_label ? <><dt>Strategy</dt><dd>{d.strategy_label}</dd></> : null}
        <dt>Data source</dt><dd>{d.data_source ?? '—'}</dd>
        <dt>Requests</dt><dd>{d.requests ?? '—'}</dd>
        <dt>Cache hits</dt><dd>{d.cache_hits ?? '—'}</dd>
        <dt>Stale marks</dt><dd>{d.stale_marks ?? '—'}</dd>
        <dt>Runtime</dt><dd>{d.runtime_seconds == null ? '—' : `${fmt(d.runtime_seconds, 1)} s`}</dd>
      </dl>
      <h4 className="bt-sub">Skipped entries</h4>
      {skipped.length ? (
        <ul className="bt-skips">
          {skipped.map(([k, c]) => <li key={k} className={c > 0 ? '' : 'muted'}>{k.replace(/_/g, ' ')}: <strong>{c}</strong></li>)}
        </ul>
      ) : <p className="muted">None.</p>}
    </div>
  );
}

export default function Results({ result }) {
  const { summary = {}, benchmark, equity = [], monthly_returns: monthly, trades = [], exit_reasons: reasons,
    diagnostics, caveats = [] } = result;
  return (
    <div className="bt-results" data-testid="bt-results">
      <h3>Results</h3>
      {result.synthetic ? <p><span className="status status-danger">Synthetic data — not market prices</span></p> : null}
      <MetricCards summary={summary} benchmark={benchmark} />
      <div className="bt-downloads row">
        <button type="button" className="secondary" onClick={() => download('backtest-trades.csv', tradesCsv(trades))}>Download trades CSV</button>
        <button type="button" className="secondary" onClick={() => download('backtest-equity.csv', equityCsv(equity))}>Download equity CSV</button>
      </div>
      <h4 className="bt-sub">Equity curve</h4>
      <EquityChart equity={equity} benchmarkName={benchmark && benchmark.name} />
      <h4 className="bt-sub">Drawdown</h4>
      <DrawdownChart equity={equity} />
      <h4 className="bt-sub">Monthly returns</h4>
      <MonthlyHeatmap rows={monthly} />
      <h4 className="bt-sub">Exit reasons</h4>
      <ExitBars reasons={reasons} />
      <h4 className="bt-sub">Trades</h4>
      <TradesTable trades={trades} />
      <h4 className="bt-sub">Diagnostics</h4>
      <Diagnostics d={diagnostics} />
      {caveats.length ? (
        <>
          <h4 className="bt-sub">Caveats</h4>
          <ul className="bt-caveats">{caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </>
      ) : null}
    </div>
  );
}
