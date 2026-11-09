// Strategy explainer + chat (the original OptionsCoach UI). Conversations are
// server-owned; every answer shows who produced it (deterministic, model, demo or fallback).
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage } from '../api.js';

const viewToStrategies = {
  bullish: [
    'long call',
    'bull call spread',
    'bull put spread',
    'covered call',
    'cash-secured put',
    'collar',
    'protective put',
    'diagonal spread',
    'ratio spread',
  ],
  bearish: ['long put', 'bear put spread', 'bear call spread', 'diagonal spread', 'ratio spread'],
  neutral: [
    'iron condor',
    'iron butterfly',
    'short strangle',
    'short straddle',
    'calendar spread',
    'collar',
    'protective put',
    'diagonal spread',
    'ratio spread',
  ],
  volatile: ['long straddle', 'long strangle', 'backspread'],
};

const keyTerms = [
  { term: 'Call', def: 'Option that gives the right to buy shares at the strike price by expiration.' },
  { term: 'Put', def: 'Option that gives the right to sell shares at the strike price by expiration.' },
  { term: 'Premium', def: 'The price of the option contract.' },
  { term: 'Debit', def: 'Net premium paid to open a position.' },
  { term: 'Credit', def: 'Net premium received to open a position.' },
  { term: 'Strike Price', def: 'The fixed price where the option can be exercised.' },
  { term: 'Expiration', def: 'The date the option contract ends.' },
  { term: 'Intrinsic Value', def: 'Immediate exercise value (ITM amount); zero if OTM.' },
  { term: 'Extrinsic (Time) Value', def: 'Premium above intrinsic value, driven by time and volatility.' },
  { term: 'Moneyness (ITM/ATM/OTM)', def: 'Whether the strike is in, at, or out of the money relative to price.' },
  { term: 'Breakeven', def: 'Price where profit/loss is zero at expiration.' },
  { term: 'Assignment', def: 'Being obligated to buy/sell shares after a short option is exercised.' },
  { term: 'Exercise', def: 'Using the option to buy/sell shares at the strike.' },
  { term: 'Delta', def: 'Approximate change in option price for a $1 move in the underlying.' },
  { term: 'Gamma', def: 'Rate of change of delta as the underlying moves.' },
  { term: 'Convexity', def: "Curvature of a position's P/L; in options it is driven by gamma." },
  { term: 'Theta', def: 'Estimated time decay in option value per day.' },
  { term: 'Vega', def: 'Change in option price for a 1% change in implied volatility.' },
  { term: 'Rho', def: 'Change in option price for a 1% change in interest rates.' },
  { term: 'Implied Volatility', def: 'Market-implied estimate of future price movement.' },
  { term: 'Open Interest', def: 'Number of outstanding contracts that remain open for a strike and expiration.' },
];

const exampleQuestions = [
  'What is a bull call spread and when does it make sense?',
  'How do straddles differ from strangles?',
  'I am bullish for the next 6 months. What strategies fit?',
  'How should I think about picking strike prices?',
  'What does gamma tell me about convexity?',
  'Explain vega and how IV changes option pricing.',
  'When would I use a covered call vs a cash-secured put?',
  'How do debit and credit spreads differ in risk?',
  'What strategies generate income in a neutral market?',
  'How does a 10% OTM call behave vs a 20% OTM call?',
];

const strategyOptions = [
  'long call',
  'long put',
  'covered call',
  'cash-secured put',
  'bull call spread',
  'bear call spread',
  'bull put spread',
  'bear put spread',
  'long straddle',
  'long strangle',
  'short straddle',
  'short strangle',
  'iron condor',
  'iron butterfly',
  'collar',
  'protective put',
  'calendar spread',
  'diagonal spread',
  'ratio spread',
  'backspread',
];

const conversationKey = 'oc_conversation_id';

export function servedByLabel(sb) {
  if (!sb) return '';
  if (sb.mode === 'llm') return `AI answer · ${sb.model}${sb.escalated ? ' (escalated)' : ''}${sb.tools_used?.length ? ` · used ${[...new Set(sb.tools_used)].join(', ')}` : ''}`;
  if (sb.mode === 'heuristic_demo') return 'Demo mode · canned educational answer (no AI model)';
  if (sb.mode === 'heuristic_fallback') return `AI unavailable (${sb.fallback_reason}) · canned educational answer`;
  return 'Calculated / built-in answer';
}

export default function Coach() {
  const [ticker, setTicker] = useState('');
  const [view, setView] = useState('');
  const [strategy, setStrategy] = useState('');
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState([]);
  const [showTerms, setShowTerms] = useState(false);
  const [showExamples, setShowExamples] = useState(false);
  const [loadingAction, setLoadingAction] = useState(null);
  const chatLogRef = useRef(null);

  const [conversationId, setConversationId] = useState(() => {
    try {
      return localStorage.getItem(conversationKey) || null;
    } catch {
      return null;
    }
  });
  const rememberConversation = (id) => {
    setConversationId(id);
    try {
      if (id) localStorage.setItem(conversationKey, id);
      else localStorage.removeItem(conversationKey);
    } catch {
      /* storage unavailable: conversation lasts for this page only */
    }
  };

  const strategyToViews = useMemo(() => {
    const map = {};
    Object.entries(viewToStrategies).forEach(([v, list]) => {
      list.forEach((strat) => {
        if (!map[strat]) map[strat] = new Set();
        map[strat].add(v);
      });
    });
    return map;
  }, []);


  const filteredStrategies = useMemo(() => {
    if (!view) return strategyOptions;
    return strategyOptions.filter((opt) => (viewToStrategies[view] || []).includes(opt));
  }, [view]);

  const filteredViews = useMemo(() => {
    if (!strategy) return ['bullish', 'bearish', 'neutral', 'volatile'];
    const allowed = strategyToViews[strategy] || new Set();
    return ['bullish', 'bearish', 'neutral', 'volatile'].filter((v) => allowed.has(v));
  }, [strategy, strategyToViews]);

  useEffect(() => {
    if (view && !filteredStrategies.includes(strategy)) {
      setStrategy('');
    }
  }, [view, filteredStrategies, strategy]);

  useEffect(() => {
    if (strategy && !filteredViews.includes(view)) {
      setView('');
    }
  }, [strategy, filteredViews, view]);

  useEffect(() => {
    if (!chatLogRef.current) {
      return;
    }
    chatLogRef.current.scrollTop = chatLogRef.current.scrollHeight;
  }, [messages]);

  const appendMessage = (text, meta = null) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setMessages((prev) => [...prev, { text: trimmed, meta }]);
  };

  const postChat = async (payload) => {
    const send = (id) => api('/api/chat', { method: 'POST', body: { ...payload, conversation_id: id } });
    try {
      return await send(conversationId);
    } catch (err) {
      if (err.status === 404 && conversationId) {
        rememberConversation(null); // not ours (or deleted): start a new conversation
        return send(null);
      }
      throw err;
    }
  };

  const deliver = async (payload) => {
    try {
      const data = await postChat(payload);
      rememberConversation(data.conversation_id);
      appendMessage(data.response_text || '', { servedBy: data.served_by });
    } catch (err) {
      appendMessage(errorMessage(err), { error: true });
    }
  };

  const sendChat = async (mode) => {
    if (mode === 'freeform' && !message.trim()) {
      return;
    }
    if (loadingAction) {
      return;
    }
    setLoadingAction(mode === 'freeform' ? 'send' : 'strategy');
    const payload = {
      message: message,
      ticker: ticker.trim() || null,
      view: view || null,
      strategy: strategy || null,
      mode,
    };
    try {
      if (mode === 'freeform' && message.trim()) {
        appendMessage(message, { user: true });
      }
      await deliver(payload);
      if (mode === 'freeform') {
        setMessage('');
      }
    } finally {
      setLoadingAction(null);
    }
  };

  const sendPreset = async (text) => {
    if (loadingAction) {
      return;
    }
    setLoadingAction('example');
    const payload = {
      message: text,
      ticker: ticker.trim() || null,
      view: view || null,
      strategy: strategy || null,
      mode: 'freeform',
    };
    try {
      appendMessage(text, { user: true });
      await deliver(payload);
      setMessage('');
    } finally {
      setLoadingAction(null);
    }
  };

  const clearChat = async () => {
    setMessages([]);
    rememberConversation(null); // next message starts a new conversation; old one stays in history
  };

  return (
    <div>
      <div className="subheader">
        <div className="header-content">
          <div className="header-actions">
            <button className="secondary inline-button" onClick={() => setShowExamples(true)}>Example Questions</button>
            <button className="secondary inline-button" onClick={() => setShowTerms(true)}>Key Terms</button>
          </div>
        </div>
      </div>

      <div className="container">
        <div className="card">
          <div className="pill">Strategy Builder</div>
          <div className="row">
            <div>
              <label htmlFor="ticker">Ticker</label>
              <input id="ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL" />
            </div>
            <div>
              <label htmlFor="view">Market View</label>
              <select id="view" value={view} onChange={(e) => setView(e.target.value)}>
                <option value="">Select view</option>
                {filteredViews.map((v) => (
                  <option key={v} value={v}>{v.charAt(0).toUpperCase() + v.slice(1)}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="strategy">Strategy</label>
              <select id="strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                <option value="">Select strategy</option>
                {filteredStrategies.map((s) => (
                  <option key={s} value={s}>{s.split(' ').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')}</option>
                ))}
              </select>
            </div>
          </div>
          <button
            id="strategyBtn"
            onClick={() => sendChat('structured')}
            disabled={loadingAction !== null}
          >
            {loadingAction === 'strategy' ? (
              <span className="button-loading"><span className="spinner" />Loading...</span>
            ) : (
              'Explain Strategy'
            )}
          </button>
        </div>

        <div className="card">
          <div className="pill">Chat</div>
          <div style={{ marginTop: '12px' }}>
            <div className="chat-log" ref={chatLogRef} aria-live="polite" aria-label="Conversation">
              {messages.map((msg, idx) => (
                <div key={`${idx}-${msg.text.slice(0, 8)}`} className={`message${msg.meta?.user ? ' user' : ''}${msg.meta?.error ? ' error' : ''}`}
                     role={msg.meta?.error ? 'alert' : undefined}>
                  {msg.text}
                  {msg.meta?.servedBy ? <div className="served-by">{servedByLabel(msg.meta.servedBy)}</div> : null}
                </div>
              ))}
            </div>
          </div>
          <label htmlFor="message">Message</label>
          <textarea
            id="message"
            rows={4}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Ask about a strategy, market view, or mechanics..."
          />
          <div className="row">
            <button id="send" onClick={() => sendChat('freeform')} disabled={loadingAction !== null}>
              {loadingAction === 'send' || loadingAction === 'example' ? (
                <span className="button-loading"><span className="spinner" />Sending...</span>
              ) : (
                'Send'
              )}
            </button>
            <button className="secondary" id="clearChat" onClick={clearChat} disabled={loadingAction !== null}>
              New Conversation
            </button>
          </div>
        </div>
      </div>

      <div className={`modal ${showTerms ? 'open' : ''}`} onClick={(e) => {
        if (e.target.classList.contains('modal')) setShowTerms(false);
      }}>
        <div className="modal-content" role="dialog" aria-modal="true" aria-labelledby="termsTitle">
          <div className="modal-header">
            <div className="modal-title" id="termsTitle">Key Terms</div>
            <button className="close-button" onClick={() => setShowTerms(false)}>Close</button>
          </div>
          <ul className="legend-list">
            {keyTerms.map((item) => (
              <li key={item.term}>
                <span className="legend-term">{item.term}</span>
                {item.def}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className={`modal ${showExamples ? 'open' : ''}`} onClick={(e) => {
        if (e.target.classList.contains('modal')) setShowExamples(false);
      }}>
        <div className="modal-content" role="dialog" aria-modal="true" aria-labelledby="examplesTitle">
          <div className="modal-header">
            <div className="modal-title" id="examplesTitle">Example Questions</div>
            <button className="close-button" onClick={() => setShowExamples(false)}>Close</button>
          </div>
          <ul className="example-list">
            {exampleQuestions.map((q) => (
              <li key={q}>
                <button
                  className="example-button"
                  type="button"
                  onClick={() => {
                    sendPreset(q);
                    setShowExamples(false);
                  }}
                >
                  {q}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
