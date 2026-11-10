// P6/P8: screener with presets, a custom filter editor, keyset paging and
// saved (user-scoped) screens. The server validates the DSL; errors are shown verbatim.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { Empty, ErrorNote, Loading, fmt, reasonText } from './common.jsx';

const OPS = ['<', '<=', '>', '>='];

export default function Screener() {
  const [meta, setMeta] = useState(null);
  const [universe, setUniverse] = useState('SPY, QQQ, IWM');
  const [preset, setPreset] = useState('high_iv_rank');
  const [filters, setFilters] = useState([]);
  const [result, setResult] = useState(null);
  const [saved, setSaved] = useState([]);
  const [name, setName] = useState('');
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    api('/api/screener/presets', { signal: ctrl.signal }).then(setMeta).catch(() => {});
    api('/api/screens', { signal: ctrl.signal }).then((r) => setSaved(r.items || [])).catch(() => {});
    return () => ctrl.abort();
  }, []);

  const definition = () => ({
    universe: universe.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean),
    filters: filters.map((f) => ({ field: f.field, op: f.op, value: Number(f.value) })),
  });

  const run = async (cursor = null) => {
    setLoading(true);
    setError(null);
    try {
      const body = { definition: definition(), preset: filters.length ? null : preset || null, cursor };
      const r = await api('/api/screener/run', { method: 'POST', body });
      setResult(cursor && result ? { ...r, matches: [...result.matches, ...r.matches] } : r);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    try {
      const s = await api('/api/screens', { method: 'POST', body: { name, definition: definition() } });
      setSaved((x) => [...x, s]);
      setName('');
    } catch (err) {
      setError(err);
    }
  };

  const fields = meta ? Object.keys(meta.fields) : [];
  return (
    <section aria-labelledby="scr-title">
      <h2 id="scr-title">Screener</h2>
      <div className="row">
        <div><label htmlFor="scr-u">Tickers (up to 10)</label><input id="scr-u" value={universe} onChange={(e) => setUniverse(e.target.value)} /></div>
        <div><label htmlFor="scr-p">Preset</label>
          <select id="scr-p" value={preset} onChange={(e) => setPreset(e.target.value)} disabled={filters.length > 0}>
            <option value="">None</option>
            {meta ? Object.keys(meta.presets).map((p) => <option key={p} value={p}>{p.replaceAll('_', ' ')}</option>) : null}
          </select></div>
      </div>
      <fieldset>
        <legend>Custom filters (override the preset)</legend>
        {filters.map((f, i) => (
          <div className="row" key={i}>
            <select aria-label={`Filter ${i + 1} field`} value={f.field} onChange={(e) => setFilters((fs) => fs.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)))}>
              {fields.map((k) => <option key={k} value={k} title={meta.fields[k]}>{k}</option>)}
            </select>
            <select aria-label={`Filter ${i + 1} operator`} value={f.op} onChange={(e) => setFilters((fs) => fs.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)))}>
              {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
            <input aria-label={`Filter ${i + 1} value`} inputMode="decimal" value={f.value} onChange={(e) => setFilters((fs) => fs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
            <button type="button" className="secondary" aria-label={`Remove filter ${i + 1}`} onClick={() => setFilters((fs) => fs.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button type="button" className="secondary" disabled={!fields.length || filters.length >= 12}
                onClick={() => setFilters((fs) => [...fs, { field: fields[0], op: '>=', value: '' }])}>Add filter</button>
      </fieldset>
      <div className="row">
        <button type="button" onClick={() => run(null)} disabled={loading}>Run screen</button>
        <input aria-label="Screen name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name to save" />
        <button type="button" className="secondary" disabled={!name.trim()} onClick={save}>Save screen</button>
      </div>
      <ErrorNote error={error} />
      {loading ? <Loading label="Screening…" /> : null}
      {result && !loading ? (
        <>
          <h3>Matches</h3>
          {result.matches.length === 0 ? <Empty>No tickers passed every filter.</Empty> : (
            <ul>{result.matches.map((m) => (
              <li key={m.ticker}>{m.ticker}: IV rank {fmt(m.metrics.iv_rank, 0)}, IV/HV {fmt(m.metrics.iv_hv_ratio)} · {m.meta.data_status}</li>
            ))}</ul>
          )}
          {result.next_cursor ? <button type="button" className="secondary" onClick={() => run(result.next_cursor)}>More</button> : null}
          <h3>Did not pass</h3>
          {result.rejected.length === 0 ? <Empty>None.</Empty> : (
            <ul>{result.rejected.map((r) => (
              <li key={r.ticker}>{r.ticker}: {r.failed.map((f) => `${f.field.replace(/_/g, ' ')} (${reasonText(f.reason)})`).join(', ')}</li>
            ))}</ul>
          )}
        </>
      ) : null}
      <h3>Saved screens</h3>
      {saved.length === 0 ? <Empty>No saved screens.</Empty> : (
        <ul>{saved.map((s) => (
          <li key={s.id}>
            <button type="button" className="link" onClick={() => {
              setUniverse(s.definition.universe.join(', '));
              setFilters(s.definition.filters.map((f) => ({ ...f, value: String(f.value) })));
            }}>{s.name}</button>
          </li>
        ))}</ul>
      )}
    </section>
  );
}
