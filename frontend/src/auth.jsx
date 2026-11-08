// Auth bootstrap, mirroring MarketMosaic's frontend/src/auth/ClerkAuthProvider.tsx:
// the publishable key and JWT template come from /api/public/config at runtime,
// @clerk/react is import()ed only when auth is on, and API calls carry
// getToken({ template }) as a bearer. Never logs or stores the token.
import { useEffect, useState } from 'react';
import { api, setTokenProvider } from './api.js';

export function useRuntimeConfig() {
  const [state, setState] = useState({ config: null, error: null });
  useEffect(() => {
    const ctrl = new AbortController();
    api('/api/public/config', { signal: ctrl.signal })
      .then((config) => setState({ config, error: null }))
      .catch((error) => setState({ config: null, error }));
    return () => ctrl.abort();
  }, []);
  return state;
}

function ClerkBridge({ mod, template, children }) {
  const auth = mod.useAuth();
  useEffect(() => {
    setTokenProvider(async () => {
      if (!auth.isSignedIn) return null;
      try {
        return (await auth.getToken({ template })) ?? null;
      } catch {
        return null; // expired session or missing template: the API answers 401
      }
    });
    return () => setTokenProvider(null);
  }, [auth, template]);
  if (!auth.isLoaded) return <p className="muted container">Loading sign-in…</p>;
  if (!auth.isSignedIn) {
    const { SignIn } = mod;
    return (
      <div className="container signin">
        <p>Sign in with your MarketMosaic account to use OptionsCoach.</p>
        <SignIn routing="hash" />
      </div>
    );
  }
  return children({ signOut: () => auth.signOut() });
}

export function AuthGate({ config, children }) {
  const [mod, setMod] = useState(null);
  const [failed, setFailed] = useState(false);
  const enabled = config.auth_enabled;

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    import('@clerk/react')
      .then((m) => !cancelled && setMod(m))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  if (!enabled) return children({ signOut: null });
  if (!config.clerk_publishable_key) return <p className="error container" role="alert">Sign-in is not configured on this deployment.</p>;
  if (failed) return <p className="error container" role="alert">Sign-in could not be loaded. Please retry.</p>;
  if (!mod) return <p className="muted container">Loading sign-in…</p>;
  const { ClerkProvider } = mod;
  return (
    <ClerkProvider publishableKey={config.clerk_publishable_key} afterSignOutUrl="/">
      <ClerkBridge mod={mod} template={config.clerk_jwt_template}>{children}</ClerkBridge>
    </ClerkProvider>
  );
}
