import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { after, before, beforeEach, test } from 'node:test';

import jwt from 'jsonwebtoken';

import { createApp, loadConfig } from '../src/app.js';
import { hashPassword, verifyPassword } from '../src/password.js';

const JWT_SECRET = 'j'.repeat(40);
const INTERNAL = 'i'.repeat(40);
const CSRF = { 'X-Requested-With': 'dashboard', 'Content-Type': 'application/json' };

let backend, gateway, app, base, seen, overviewBody;

before(async () => {
  backend = createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      seen.push({ url: req.url, method: req.method, token: req.headers['x-internal-token'], body });
      if (req.url === '/api/overview') {
        res.setHeader('content-type', 'application/json');
        return res.end(overviewBody);
      }
      if (req.url.startsWith('/t/c/')) {
        res.writeHead(302, { location: 'https://example.org/landing' });
        return res.end();
      }
      res.setHeader('content-type', 'application/json');
      return res.end(JSON.stringify({ proxied: req.url }));
    });
  });
  await new Promise((resolve) => backend.listen(0, resolve));

  app = createApp(loadConfig({
    JWT_SECRET,
    INTERNAL_API_TOKEN: INTERNAL,
    ADMIN_PASSWORD_HASH: await hashPassword('correct horse'),
    BACKEND_URL: `http://127.0.0.1:${backend.address().port}`,
    LIVE_POLL_MS: '30',
    LOGIN_RATE_LIMIT: '5',
  }));
  gateway = app.listen(0);
  await new Promise((resolve) => gateway.once('listening', resolve));
  base = `http://127.0.0.1:${gateway.address().port}`;
});

after(() => {
  app.locals.live.close();
  gateway.closeAllConnections();
  gateway.close();
  backend.closeAllConnections();
  backend.close();
});

beforeEach(() => {
  seen = [];
  overviewBody = '{"sent":1}';
});

async function login() {
  const response = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'correct horse' }),
  });
  assert.equal(response.status, 200);
  return response.headers.get('set-cookie');
}

test('config refuses weak or missing secrets', () => {
  assert.throws(() => loadConfig({ JWT_SECRET: 'short' }), /JWT_SECRET/);
  assert.throws(() => loadConfig({ JWT_SECRET: JWT_SECRET, INTERNAL_API_TOKEN: INTERNAL }), /ADMIN_PASSWORD_HASH/);
});

test('password hashing verifies only the right password', async () => {
  const stored = await hashPassword('secret');
  assert.equal(await verifyPassword('secret', stored), true);
  assert.equal(await verifyPassword('wrong', stored), false);
  assert.equal(await verifyPassword('secret', 'garbage'), false);
});

test('session cookie is HttpOnly and SameSite=Strict', async () => {
  const cookie = await login();
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
});

test('API rejects requests with no session and never reaches Flask', async () => {
  const response = await fetch(`${base}/api/overview`);
  assert.equal(response.status, 401);
  assert.equal(seen.length, 0);
});

test('API rejects forged, expired and alg-none tokens', async () => {
  const forged = jwt.sign({ sub: 'admin' }, 'x'.repeat(40));
  const expired = jwt.sign({ sub: 'admin' }, JWT_SECRET, { expiresIn: -10 });
  const none = jwt.sign({ sub: 'admin' }, '', { algorithm: 'none' });
  for (const token of [forged, expired, none]) {
    const response = await fetch(`${base}/api/overview`, { headers: { cookie: `session=${token}` } });
    assert.equal(response.status, 401);
  }
});

test('API proxies with the internal token, which the browser never supplies', async () => {
  const cookie = await login();
  const response = await fetch(`${base}/api/campaigns?x=1`, {
    headers: { cookie, 'X-Internal-Token': 'attacker' },
  });
  assert.deepEqual(await response.json(), { proxied: '/api/campaigns?x=1' });
  assert.equal(seen[0].token, INTERNAL);
});

test('state-changing requests need the CSRF header', async () => {
  const cookie = await login();
  const blocked = await fetch(`${base}/api/campaigns`, {
    method: 'POST',
    headers: { cookie, 'Content-Type': 'application/json' },
    body: '{"name":"x"}',
  });
  assert.equal(blocked.status, 403);
  assert.equal(seen.length, 0);
  const allowed = await fetch(`${base}/api/campaigns`, {
    method: 'POST',
    headers: { cookie, ...CSRF },
    body: '{"name":"x"}',
  });
  assert.equal(allowed.status, 200);
  assert.equal(seen[0].body, '{"name":"x"}');
});

test('tracking routes are public and pass redirects through', async () => {
  const response = await fetch(`${base}/t/c/abc`, { redirect: 'manual' });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), 'https://example.org/landing');
});

test('live stream needs a session, then pushes changes', async () => {
  assert.equal((await fetch(`${base}/api/live`)).status, 401);
  const cookie = await login();
  const controller = new AbortController();
  const response = await fetch(`${base}/api/live`, { headers: { cookie }, signal: controller.signal });
  assert.match(response.headers.get('content-type'), /text\/event-stream/);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  const readUntil = async (needle) => {
    while (!text.includes(needle)) {
      const { value, done } = await reader.read();
      if (done) break;
      text += decoder.decode(value);
    }
  };
  await readUntil('"sent":1');
  overviewBody = '{"sent":2}';
  await readUntil('"sent":2');
  assert.match(text, /event: overview/);
  controller.abort();
});

test('security headers are set and framework header removed', async () => {
  const response = await fetch(`${base}/health`);
  assert.match(response.headers.get('content-security-policy'), /default-src 'self'/);
  assert.equal(response.headers.get('x-powered-by'), null);
});

test('wrong password is rejected, then login is rate limited', async () => {
  let last;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    last = await fetch(`${base}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password: 'nope' }),
    });
    if (attempt === 0 ) assert.ok([401, 429].includes(last.status));
  }
  assert.equal(last.status, 429);
});
