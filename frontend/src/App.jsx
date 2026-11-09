import { useCallback, useEffect, useState } from 'react';

import { api, ApiError } from './api.js';
import CampaignPage from './CampaignPage.jsx';
import OverviewPage from './OverviewPage.jsx';

function useRoute() {
  const read = () => window.location.hash.match(/^#\/campaigns\/(\d+)$/)?.[1] ?? null;
  const [campaignId, setCampaignId] = useState(read);
  useEffect(() => {
    const onChange = () => {
      setCampaignId(read());
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return campaignId;
}

/** Keeps a live connection open. `version` changes whenever the numbers do. */
function useLive(enabled) {
  const [live, setLive] = useState({ state: 'connecting', version: 0, updatedAt: null });
  useEffect(() => {
    if (!enabled) return undefined;
    const source = new EventSource('/api/live');
    source.addEventListener('overview', () => {
      setLive((current) => ({ state: 'live', version: current.version + 1, updatedAt: new Date() }));
    });
    source.addEventListener('unavailable', () => setLive((c) => ({ ...c, state: 'lost' })));
    source.onerror = () => setLive((c) => ({ ...c, state: 'lost' }));
    return () => source.close();
  }, [enabled]);
  return live;
}

function LiveStatus({ live }) {
  if (live.state === 'lost') return <span className="live live-lost" role="status">Live updates paused, reconnecting</span>;
  if (live.state === 'connecting') return <span className="live live-lost">Connecting</span>;
  return (
    <span className="live" role="status">
      <span className="live-dot" aria-hidden="true" />
      Live, changed {live.updatedAt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' })}
    </span>
  );
}

function Login({ onSignedIn }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const user = await api.login(username, password);
      onSignedIn(user);
    } catch (problem) {
      setError(problem.message);
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Email Automation Intelligence</h1>
      <p>See what your campaigns delivered and who wrote back.</p>
      <form onSubmit={submit}>
        <label>Username
          <input value={username} autoComplete="username" required onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>Password
          <input type="password" value={password} autoComplete="current-password" required autoFocus onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>{busy ? 'Signing in' : 'Sign in'}</button>
      </form>
    </main>
  );
}

export default function App() {
  const [user, setUser] = useState(undefined);
  const campaignId = useRoute();
  const live = useLive(Boolean(user));

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null));
  }, []);

  const onApiError = useCallback((problem) => {
    if (problem instanceof ApiError && problem.status === 401) setUser(null);
  }, []);

  async function signOut() {
    await api.logout();
    setUser(null);
  }

  return (
    <>
      <div className="airmail" aria-hidden="true" />
      {user === undefined && <p className="page-loading">Loading</p>}
      {user === null && <Login onSignedIn={setUser} />}
      {user && (
        <>
          <header className="topbar">
            <a className="brand" href="#/">Email Automation Intelligence</a>
            <LiveStatus live={live} />
            <button type="button" className="text-button" onClick={signOut}>Sign out</button>
          </header>
          <main className="page">
            {campaignId
              ? <CampaignPage key={campaignId} id={campaignId} version={live.version} onApiError={onApiError} />
              : <OverviewPage version={live.version} onApiError={onApiError} />}
          </main>
        </>
      )}
    </>
  );
}
