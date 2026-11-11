// Minimal Markdown for chat answers, rendered as React elements (never innerHTML),
// so model output cannot inject markup. Supports paragraphs, #-headings, - / * / 1.
// lists, **bold**, *italic*, `code`, and "Label: value" lines (the built-in answers
// are written that way), which get a bold label.
import { Fragment } from 'react';

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;

export function inline(text, keyBase = 'i') {
  return text.split(INLINE).filter((part) => part !== '').map((part, i) => {
    const key = `${keyBase}-${i}`;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) return <code key={key}>{part.slice(1, -1)}</code>;
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={key}>{part.slice(1, -1)}</em>;
    return <Fragment key={key}>{part}</Fragment>;
  });
}

const LABEL = /^([A-Z][A-Za-z0-9 /()'’-]{1,40}):\s+(.+)$/;

function line(text, key) {
  const m = text.match(LABEL);
  if (m && !text.startsWith('http')) {
    return <><strong key={`${key}-l`}>{m[1]}:</strong> {inline(m[2], key)}</>;
  }
  return inline(text, key);
}

export function blocks(text) {
  const out = [];
  let list = null;
  let para = [];
  const flushPara = () => {
    if (para.length) out.push({ type: 'p', lines: para });
    para = [];
  };
  const flushList = () => {
    if (list) out.push(list);
    list = null;
  };
  for (const raw of String(text || '').split('\n')) {
    const t = raw.trim();
    const bullet = t.match(/^[-*•]\s+(.*)$/);
    const numbered = t.match(/^(\d+)[.)]\s+(.*)$/);
    const heading = t.match(/^#{1,4}\s+(.*)$/);
    if (!t) { flushPara(); flushList(); continue; }
    if (heading) { flushPara(); flushList(); out.push({ type: 'h', text: heading[1] }); continue; }
    if (bullet || numbered) {
      flushPara();
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) { flushList(); list = { type: 'list', ordered, items: [] }; }
      list.items.push(bullet ? bullet[1] : numbered[2]);
      continue;
    }
    flushList();
    para.push(t);
  }
  flushPara();
  flushList();
  return out;
}

export default function Markdown({ text }) {
  return (
    <div className="md">
      {blocks(text).map((b, i) => {
        if (b.type === 'h') return <h4 key={i}>{inline(b.text, `h${i}`)}</h4>;
        if (b.type === 'list') {
          const items = b.items.map((it, j) => <li key={j}>{line(it, `l${i}-${j}`)}</li>);
          return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
        }
        return (
          <p key={i}>
            {b.lines.map((l, j) => <Fragment key={j}>{j ? <br /> : null}{line(l, `p${i}-${j}`)}</Fragment>)}
          </p>
        );
      })}
    </div>
  );
}
