import { existsSync } from 'node:fs';
import path from 'node:path';

import cookieParser from 'cookie-parser';
import express from 'express';
import rateLimit from 'express-rate-limit';
import helmet from 'helmet';
import jwt from 'jsonwebtoken';

import { verifyPassword } from './password.js';

const COOKIE = 'session';
const SESSION_SECONDS = 8 * 60 * 60;
// Compared even for unknown users, so response time doesn't reveal valid usernames.
const DUMMY_HASH = 'scrypt$AAAAAAAAAAAAAAAAAAAAAA$' + 'A'.repeat(86);

export function loadConfig(env = process.env) {
  const config = {
    port: Number(env.GATEWAY_PORT || 4000),
    backendUrl: (env.BACKEND_URL || 'http://127.0.0.1:5000').replace(/\/$/, ''),
    internalToken: env.INTERNAL_API_TOKEN || '',
    jwtSecret: env.JWT_SECRET || '',
    adminUsername: env.ADMIN_USERNAME || 'admin',
    adminPasswordHash: env.ADMIN_PASSWORD_HASH || '',
    secureCookies: env.COOKIE_SECURE === 'true',
    trustProxy: env.TRUST_PROXY === 'true',
    livePollMs: Number(env.LIVE_POLL_MS || 5000),
    loginRateLimit: Number(env.LOGIN_RATE_LIMIT || 10),
    staticDir: env.STATIC_DIR || '',
  };
  const problems = [];
  if (config.jwtSecret.length < 32) problems.push('JWT_SECRET must be at least 32 characters');
  if (config.internalToken.length < 32) problems.push('INTERNAL_API_TOKEN must be at least 32 characters');
  if (!config.adminPasswordHash.startsWith('scrypt$')) problems.push('ADMIN_PASSWORD_HASH is not set');
  if (problems.length) throw new Error(`${problems.join('; ')}. Run: npm run setup`);
  return config;
}

async function forward(config, req, res, target) {
  const headers = { 'X-Internal-Token': config.internalToken };
  const init = { method: req.method, headers, redirect: 'manual' };
  if (!['GET', 'HEAD'].includes(req.method)) {
    if (req.is('application/json')) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(req.body ?? {});
    } else {
      init.body = undefined;
    }
  }
  try {
    const upstream = await fetch(`${config.backendUrl}${target}`, init);
    res.status(upstream.status);
    for (const name of ['content-type', 'cache-control', 'location']) {
      const value = upstream.headers.get(name);
      if (value) res.setHeader(name, value);
    }
    res.send(Buffer.from(await upstream.arrayBuffer()));
  } catch {
    res.status(502).json({ error: 'The backend is not reachable. Check that Flask is running.' });
  }
}

/** One poller shared by all dashboards; it runs only while someone is connected. */
function createLiveHub(config) {
  const clients = new Set();
  let timer = null;
  let last = null;

  async function poll() {
    try {
      const response = await fetch(`${config.backendUrl}/api/overview`, {
        headers: { 'X-Internal-Token': config.internalToken },
      });
      if (!response.ok) throw new Error(String(response.status));
      const body = await response.text();
      if (body !== last) {
        last = body;
        for (const client of clients) client.write(`event: overview\ndata: ${body}\n\n`);
      } else {
        for (const client of clients) client.write(': keep-alive\n\n');
      }
    } catch {
      last = null;
      for (const client of clients) {
        client.write('event: unavailable\ndata: {}\n\n');
      }
    }
  }

  return {
    add(res) {
      clients.add(res);
      if (last) res.write(`event: overview\ndata: ${last}\n\n`);
      if (!timer) {
        poll();
        timer = setInterval(poll, config.livePollMs);
      }
      res.on('close', () => {
        clients.delete(res);
        if (!clients.size) {
          clearInterval(timer);
          timer = null;
          last = null;
        }
      });
    },
    close() {
      clearInterval(timer);
      timer = null;
      for (const client of clients) client.end();
      clients.clear();
    },
  };
}

export function createApp(config) {
  const app = express();
  const live = createLiveHub(config);
  app.locals.live = live;
  app.disable('x-powered-by');
  if (config.trustProxy) app.set('trust proxy', 1);

  app.use(helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        imgSrc: ["'self'", 'data:'],
        styleSrc: ["'self'"],
        scriptSrc: ["'self'"],
        connectSrc: ["'self'"],
        frameAncestors: ["'none'"],
        // Without this, helmet asks browsers to force https even on localhost.
        upgradeInsecureRequests: config.secureCookies ? [] : null,
      },
    },
    strictTransportSecurity: config.secureCookies,
  }));

  app.get('/health', (_req, res) => res.json({ ok: true }));

  // Recipients' mail clients hit these with no session, so they stay public.
  // Flask checks the token or signature on each one.
  const trackingLimiter = rateLimit({ windowMs: 60_000, limit: 300, standardHeaders: true, legacyHeaders: false });
  app.all('/t/*path', trackingLimiter, (req, res) => forward(config, req, res, req.originalUrl));

  app.use(cookieParser());
  app.use(express.json({ limit: '256kb' }));

  const loginLimiter = rateLimit({
    windowMs: 15 * 60_000,
    limit: config.loginRateLimit,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Too many sign-in attempts. Try again in 15 minutes.' },
  });

  app.post('/auth/login', loginLimiter, async (req, res) => {
    const { username, password } = req.body ?? {};
    const known = typeof username === 'string' && username === config.adminUsername;
    const matches = typeof password === 'string'
      && await verifyPassword(password, known ? config.adminPasswordHash : DUMMY_HASH);
    if (!known || !matches) {
      return res.status(401).json({ error: 'That username or password is incorrect.' });
    }
    const token = jwt.sign({ sub: username }, config.jwtSecret, {
      algorithm: 'HS256',
      expiresIn: SESSION_SECONDS,
    });
    res.cookie(COOKIE, token, {
      httpOnly: true,
      sameSite: 'strict',
      secure: config.secureCookies,
      maxAge: SESSION_SECONDS * 1000,
      path: '/',
    });
    return res.json({ username });
  });

  app.post('/auth/logout', (_req, res) => {
    res.clearCookie(COOKIE, { path: '/' });
    res.status(204).end();
  });

  function requireSession(req, res, next) {
    try {
      req.user = jwt.verify(req.cookies?.[COOKIE] ?? '', config.jwtSecret, { algorithms: ['HS256'] });
      return next();
    } catch {
      return res.status(401).json({ error: 'Sign in to continue.' });
    }
  }

  // SameSite stops most cross-site posts. This header stops the rest, because
  // another origin can't set a custom header without passing a CORS check.
  function requireCsrfHeader(req, res, next) {
    if (['GET', 'HEAD', 'OPTIONS'].includes(req.method)) return next();
    if (req.get('X-Requested-With') === 'dashboard') return next();
    return res.status(403).json({ error: 'This request is missing its dashboard header.' });
  }

  app.get('/auth/me', requireSession, (req, res) => res.json({ username: req.user.sub }));

  app.get('/api/live', requireSession, (req, res) => {
    res.status(200).set({
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
    });
    res.flushHeaders();
    live.add(res);
  });

  const apiLimiter = rateLimit({ windowMs: 60_000, limit: 600, standardHeaders: true, legacyHeaders: false });
  app.use('/api', apiLimiter, requireSession, requireCsrfHeader, (req, res) =>
    forward(config, req, res, req.originalUrl));

  if (config.staticDir && existsSync(config.staticDir)) {
    const root = path.resolve(config.staticDir);
    app.use(express.static(root));
    app.get('/*path', (_req, res) => res.sendFile(path.join(root, 'index.html')));
  }

  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    const status = err.status && err.status < 500 ? err.status : 500;
    res.status(status).json({ error: status === 500 ? 'The gateway hit an unexpected error.' : 'That request could not be read.' });
  });

  return app;
}
