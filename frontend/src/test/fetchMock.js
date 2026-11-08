// Minimal fetch router for component tests. Honors AbortSignal like real fetch.
import { vi } from 'vitest';

export function mockFetch(routes) {
  const calls = [];
  const fn = vi.fn((url, opts = {}) => {
    calls.push({ url, opts });
    const route = routes.find(([match]) => (typeof match === 'string' ? url.startsWith(match) : match.test(url)));
    if (!route) return Promise.resolve(new Response(JSON.stringify({ error: 'not_found' }), { status: 404 }));
    const [, handler] = route;
    return new Promise((resolve, reject) => {
      const respond = () => {
        const { status = 200, body } = typeof handler === 'function' ? handler(url, opts) : handler;
        resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
      };
      if (opts.signal) {
        if (opts.signal.aborted) return reject(new DOMException('aborted', 'AbortError'));
        opts.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }
      const delay = typeof handler === 'object' && handler.delay ? handler.delay : 0;
      setTimeout(respond, delay);
    });
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}
