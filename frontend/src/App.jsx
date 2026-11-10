// OptionsCoach shell: runtime config -> auth gate -> tabbed tools (P8).
// Tabs follow the WAI-ARIA tabs pattern (arrow keys move between tabs).
import { useRef, useState } from 'react';
import { AuthGate, useRuntimeConfig } from './auth.jsx';
import Analytics from './components/Analytics.jsx';
import Backtests from './components/Backtests.jsx';
import Builder from './components/Builder.jsx';
import ChainViewer from './components/ChainViewer.jsx';
import Coach from './components/Coach.jsx';
import Compare from './components/Compare.jsx';
import Screener from './components/Screener.jsx';
import StrategyFinder from './components/StrategyFinder.jsx';
import { DataStatus, ErrorNote } from './components/common.jsx';
import './index.css';

const TABS = [
  ['coach', 'Coach'],
  ['finder', 'Find a strategy'],
  ['chain', 'Chains'],
  ['builder', 'Builder & payoff'],
  ['compare', 'Compare'],
  ['analytics', 'Analytics'],
  ['screener', 'Screener'],
  ['backtests', 'Backtests'],
];

export function Shell({ config, signOut }) {
  const [tab, setTab] = useState('coach');
  const [seed, setSeed] = useState(null);
  const [position, setPosition] = useState(null); // a whole structure for the builder
  const [incoming, setIncoming] = useState(null); // a backtest request
  const [focus, setFocus] = useState(null); // a chain to open
  const openFromFinder = (kind, c) => {
    const nonce = Date.now();
    if (kind === 'builder') { setPosition({ ...c.links.builder, label: c.name, nonce }); setTab('builder'); }
    if (kind === 'backtest') { setIncoming({ request: c.links.backtest, label: c.name, nonce }); setTab('backtests'); }
    if (kind === 'chain') {
      setFocus({ ...c.links.chain, strikes: c.legs.filter((l) => l.strike).map((l) => l.strike), label: c.name, nonce });
      setTab('chain');
    }
    window.scrollTo?.({ top: 0, behavior: 'smooth' });
  };
  const refs = useRef({});
  const onKey = (e, i) => {
    const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!dir) return;
    const next = TABS[(i + dir + TABS.length) % TABS.length][0];
    setTab(next);
    refs.current[next]?.focus();
  };
  return (
    <div>
      <header>
        <div className="header-content">
          <div>
            <h1>Options Coach AI</h1>
            <p>Options strategies, explained simply. Educational only; not investment advice.</p>
          </div>
          <div className="header-actions">
            <DataStatus status={config.market_data.data_status} label={config.market_data.label} provider={config.market_data.provider} />
            {config.llm.labelled_demo ? <span className="status status-danger">Demo answers (no AI model)</span> : null}
            {signOut ? <button type="button" className="secondary inline-button" onClick={signOut}>Sign out</button> : null}
          </div>
        </div>
        <nav role="tablist" aria-label="Tools" className="tabs">
          {TABS.map(([id, label], i) => (
            <button key={id} role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls={`panel-${id}`}
                    tabIndex={tab === id ? 0 : -1} ref={(el) => { refs.current[id] = el; }}
                    className={tab === id ? 'tab active' : 'tab'} onClick={() => setTab(id)} onKeyDown={(e) => onKey(e, i)}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <main className="container">
        {TABS.map(([id]) => (
          <div key={id} role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} hidden={tab !== id}
               className={id === 'coach' ? '' : 'card'}>
            {id === 'coach' && <Coach />}
            {id === 'finder' && <StrategyFinder onOpen={openFromFinder} />}
            {id === 'chain' && tab === id && <ChainViewer focus={focus} onPick={(s) => { setSeed(s); setTab('builder'); }} />}
            {id === 'builder' && <Builder seed={seed} position={position} />}
            {id === 'compare' && tab === id && <Compare />}
            {id === 'analytics' && tab === id && <Analytics />}
            {id === 'screener' && tab === id && <Screener />}
            {id === 'backtests' && tab === id && <Backtests incoming={incoming} />}
          </div>
        ))}
      </main>
    </div>
  );
}

export default function App() {
  const { config, error } = useRuntimeConfig();
  if (error) return <div className="container"><ErrorNote error={error} /></div>;
  if (!config) return <p className="muted container">Loading…</p>;
  return <AuthGate config={config}>{({ signOut }) => <Shell config={config} signOut={signOut} />}</AuthGate>;
}
