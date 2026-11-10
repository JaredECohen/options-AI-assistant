// P7: options backtester. Configure a strategy, estimate the cost of the run,
// start a durable `backtest` job, poll it to completion and render the result.
// Contract: docs/BACKTESTING.md. Helpers live in ./backtest/.
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, isAbort } from '../api.js';
import { ErrorNote, Loading, pct } from './common.jsx';
import Results from './backtest/Results.jsx';
import {
  CostsSection, EntrySection, ExitSection, SizingSection, StrategySection, UnderlyingSection,
} from './backtest/FormSections.jsx';
import {
  FALLBACK_REQUEST, TERMINAL, applyPreset, formToRequest, loadStored, rangeStart, relabel, requestToForm,
  saveStored, validate, yesterday,
} from './backtest/config.js';

function initialForm() {
  const stored = loadStored();
  const end = yesterday();
  const base = requestToForm({ ...FALLBACK_REQUEST, start: rangeStart('1Y', end), end });
  if (!stored) return { form: base, fromStorage: false };
  // Merge so a stored form from an older build can never miss a field.
  return { form: { ...base, ...stored, exit: { ...base.exit, ...stored.exit } }, fromStorage: true };
}

function StatusLine({ job, progress }) {
  if (!job) return null;
  const p = progress || {};
  const running = !TERMINAL.includes(job.status);
  return (
    <div className="bt-run-status">
      <p role="status">Job {job.id}: {job.status}{job.cancel_requested && running ? ' (cancelling…)' : ''}</p>
      {running ? (
        <>
          <div className="bt-progress" role="progressbar" aria-label="Backtest progress" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={Math.round(p.pct ?? 0)}>
            <div className="bt-progress-fill" style={{ width: `${Math.min(100, Math.max(0, p.pct ?? 0))}%` }} />
          </div>
          <p className="muted" data-testid="bt-progress-text">
            {(p.pct ?? 0).toFixed(1)}%{p.day ? ` · day ${p.day}` : ''}
            {p.trades != null ? ` · ${p.trades} trades` : ''}{p.open != null ? ` · ${p.open} open` : ''}
            {p.requests != null ? ` · ${p.requests} requests` : ''}
          </p>
        </>
      ) : null}
    </div>
  );
}

export default function Backtests({ pollMs = 2000 }) {
  const initial = useRef(null);
  if (initial.current === null) initial.current = initialForm();
  const [f, setF] = useState(initial.current.form);
  const [presetData, setPresetData] = useState(null);
  const [presetError, setPresetError] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [estimateFailed, setEstimateFailed] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState(null);
  const [pollId, setPollId] = useState(null);
  const [runError, setRunError] = useState(null);
  const [history, setHistory] = useState([]);
  const [loadingRun, setLoadingRun] = useState(false);
  const fromStorage = initial.current.fromStorage;

  const presets = presetData ? presetData.presets || [] : [];
  const errors = useMemo(() => validate(f), [f]);
  const request = useMemo(() => (errors.length ? null : formToRequest(f)), [f, errors]);
  const requestKey = request ? JSON.stringify(request) : '';

  const loadHistory = () => {
    api('/api/backtests')
      .then((res) => setHistory(res.backtests || []))
      .catch(() => {});
  };

  // Presets + history on mount.
  useEffect(() => {
    const ctl = new AbortController();
    api('/api/backtests/presets', { signal: ctl.signal })
      .then((res) => {
        setPresetData(res);
        if (!fromStorage && res.defaults) {
          const end = yesterday();
          setF((cur) => requestToForm({ ...res.defaults, start: rangeStart('1Y', end), end }, cur));
        }
      })
      .catch((err) => { if (!isAbort(err)) setPresetError(err); });
    loadHistory();
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { saveStored(f); }, [f]);

  // Debounced estimate; a newer config aborts the superseded request.
  useEffect(() => {
    if (!requestKey) { setEstimate(null); setEstimateFailed(false); return undefined; }
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      api('/api/backtests/estimate', { method: 'POST', body: JSON.parse(requestKey), signal: ctl.signal })
        .then((res) => { setEstimate(res); setEstimateFailed(false); })
        .catch((err) => { if (!isAbort(err)) { setEstimate(null); setEstimateFailed(true); } });
    }, 600);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [requestKey]);

  // Poll the job until terminal. Cleanup on unmount (or job change) stops it.
  useEffect(() => {
    if (!pollId) return undefined;
    let stopped = false;
    let timer;
    const ctl = new AbortController();
    const tick = async () => {
      try {
        const j = await api(`/api/jobs/${pollId}`, { signal: ctl.signal });
        if (stopped) return;
        setJob(j);
        if (TERMINAL.includes(j.status)) { setPollId(null); loadHistory(); return; }
      } catch (err) {
        if (stopped || isAbort(err)) return;
        if (err.status === 404) { setRunError(err); setPollId(null); return; }
      }
      timer = setTimeout(tick, pollMs);
    };
    timer = setTimeout(tick, pollMs);
    return () => { stopped = true; clearTimeout(timer); ctl.abort(); };
  }, [pollId, pollMs]);

  const set = (patch) => setF((cur) => ({ ...cur, ...patch }));
  const setLegs = (legs) => setF((cur) => relabel({ ...cur, legs }, presets));
  const onPreset = (key) => {
    const p = presets.find((x) => x.key === key);
    if (p) setF((cur) => applyPreset(cur, p));
  };
  const onRange = (key) => {
    const end = yesterday();
    set({ end, start: rangeStart(key, end) });
  };

  const trackJob = (j) => {
    setJob(j);
    setPollId(TERMINAL.includes(j.status) ? null : j.id);
    if (TERMINAL.includes(j.status)) loadHistory();
  };

  const run = async (e) => {
    e.preventDefault();
    setAttempted(true);
    if (errors.length) return;
    setRunError(null);
    setJob(null);
    setPollId(null);
    setStarting(true);
    try {
      trackJob(await api('/api/backtests', { method: 'POST', body: request }));
    } catch (err) {
      setRunError(err);
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      const j = await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
      if (j && j.status) setJob((cur) => ({ ...cur, ...j }));
      else setJob((cur) => ({ ...cur, cancel_requested: true }));
    } catch (err) {
      setRunError(err);
    }
  };

  const openRun = async (item) => {
    setRunError(null);
    setLoadingRun(true);
    setPollId(null);
    try {
      const j = await api(`/api/jobs/${item.id}`);
      trackJob(j);
      const config = j.result && j.result.config;
      if (config) {
        setF((cur) => requestToForm(config, cur));
      } else {
        setF((cur) => {
          const next = requestToForm({ underlying: item.underlying, start: item.start, end: item.end }, cur);
          const preset = presets.find((p) => p.key === item.strategy);
          return preset ? applyPreset(next, preset) : { ...next, strategy: item.strategy || next.strategy };
        });
      }
    } catch (err) {
      setRunError(err);
    } finally {
      setLoadingRun(false);
    }
  };

  const running = job && !TERMINAL.includes(job.status);
  const result = job && job.status === 'succeeded' && job.result && job.result.summary ? job.result : null;
  const progress = running && job.result ? job.result.progress : null;
  const blocked = presetData && presetData.available === false;

  return (
    <section aria-labelledby="bt-title" className="backtests">
      <h2 id="bt-title">Backtests</h2>
      <p className="muted">Replay an options strategy over end-of-day history with commissions, conservative fills and a buy-and-hold
        benchmark. Results are history, not a forecast.</p>

      {blocked ? (
        <div className="bt-notice status-warn" role="status" data-testid="bt-blocked">
          <strong>Backtesting is not available right now.</strong>
          <ul>{(presetData.blocked_by || []).map((b) => <li key={b}>{b}</li>)}</ul>
          <span className="muted">You can still explore the form; running will fail until this is resolved.</span>
        </div>
      ) : null}
      <ErrorNote error={presetError} />

      <form onSubmit={run} noValidate aria-label="Backtest configuration">
        <UnderlyingSection f={f} set={set} onRange={onRange} />
        <StrategySection f={f} presets={presets} onPreset={onPreset} setLegs={setLegs} set={set} />
        <EntrySection f={f} set={set} />
        <ExitSection f={f} set={set} />
        <CostsSection f={f} set={set} />
        <SizingSection f={f} set={set} />

        {attempted && errors.length ? (
          <div className="error bt-errors" role="alert" data-testid="bt-errors">
            <strong>Fix these before running:</strong>
            <ul>{errors.map((m) => <li key={m}>{m}</li>)}</ul>
          </div>
        ) : null}
        {!attempted && errors.length ? (
          <p className="muted bt-hint" data-testid="bt-inline-errors">{errors[0]}{errors.length > 1 ? ` (+${errors.length - 1} more)` : ''}</p>
        ) : null}

        <p className="muted bt-estimate" role="status" data-testid="bt-estimate">
          {estimate
            ? `~${estimate.entry_days} entries, ~${estimate.est_requests} data requests, ~${estimate.est_minutes} min (first run; cached reruns are fast)`
            : estimateFailed ? 'Estimate unavailable.' : request ? 'Estimating…' : ''}
        </p>

        <div className="bt-actions">
          <button type="submit" disabled={starting || Boolean(running)}>Run backtest</button>
          {running ? <button type="button" className="secondary" onClick={cancel} disabled={job.cancel_requested}>Cancel</button> : null}
        </div>
      </form>

      {starting ? <Loading label="Submitting…" /> : null}
      <ErrorNote error={runError} />
      <StatusLine job={job} progress={progress} />
      {job && ['failed', 'dead'].includes(job.status) ? (
        <div className="error" role="alert">Backtest {job.status}: {job.error || 'unknown error'}</div>
      ) : null}
      {job && job.status === 'cancelled' ? <p className="muted">Backtest cancelled.</p> : null}
      {loadingRun ? <Loading label="Loading run…" /> : null}

      {result ? <Results result={result} /> : null}

      <h3 className="bt-sub">Recent runs</h3>
      {history.length === 0 ? <p className="muted">No runs yet.</p> : (
        <ul className="bt-history" aria-label="Recent runs">
          {history.map((h) => (
            <li key={h.id}>
              <button type="button" className="secondary" onClick={() => openRun(h)}>
                <strong>{h.underlying}</strong> {String(h.strategy).replace(/_/g, ' ')} · {h.start} → {h.end}
                <span className="muted"> · {h.status}
                  {h.summary ? ` · ${pct(h.summary.total_return)} return · ${h.summary.trades} trades` : ''}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
