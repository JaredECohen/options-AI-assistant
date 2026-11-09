// API client. Every call carries the Clerk session token (when auth is on) in
// the Authorization header, accepts an AbortSignal so superseded requests are
// cancelled, and turns error bodies into ApiError with the server's code.

let tokenProvider = null;

export function setTokenProvider(fn) {
  tokenProvider = fn;
}

export class ApiError extends Error {
  constructor(status, body) {
    super((body && (body.detail || body.error)) || `HTTP ${status}`);
    this.status = status;
    this.code = (body && (body.code || body.error)) || 'http_error';
    this.body = body || {};
  }
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (tokenProvider) {
    const token = await tokenProvider();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(path, { method, headers, signal, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const isAbort = (err) => Boolean(err) && (err.name === 'AbortError' || err.code === 20);

const MESSAGES = {
  auth_required: 'Please sign in.',
  auth_invalid: 'Your session is no longer valid. Please sign in again.',
  beta_access_required: 'Your account does not have beta access yet.',
  account_suspended: 'This account is suspended.',
  plan_required: 'This feature needs a Pro plan.',
  market_data_unavailable: 'Market data is unavailable right now.',
  auth_unavailable: 'Sign-in checks are temporarily unavailable.',
  entitlements_unavailable: 'Access checks are temporarily unavailable.',
};

export function errorMessage(err) {
  if (!err) return '';
  return MESSAGES[err.code] || err.message || 'Something went wrong.';
}
