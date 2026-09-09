'use strict';

/**
 * BlazerTest MT5 <-> iOS bridge.
 *
 * The MT5 terminal can only make OUTBOUND HTTP requests (via MQL5's
 * WebRequest) and cannot accept inbound connections, so the EA and the
 * iOS app can never talk directly to each other. This tiny server sits
 * in between:
 *
 *   XAUUSD_Confluence_EA  --push heartbeat-->   bridge   <--poll status--  iOS app
 *   (running in an MT5      <--poll control--            --set control-->
 *    terminal on a VPS)
 *
 * State is per "accountTag" (one entry per EA/account instance reporting
 * in), held in memory and mirrored to a JSON file so a restart doesn't
 * lose the last-known control flags (trading enabled / flatten request).
 *
 * Auth: two shared-secret API keys via the `X-Api-Key` header.
 *   - EA_API_KEY  : used by the EA for POST /api/heartbeat and GET /api/control
 *   - APP_API_KEY : used by the iOS app for everything under /api/app/*
 * Keep them different — the EA key lives in a .set file on a VPS, the
 * app key lives on a phone. Compromise of one shouldn't hand over the
 * other's capabilities (the EA key alone can't disable/flatten trading).
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// Minimal .env loader (no dependency): if a .env file sits next to this
// script, load KEY=VALUE lines into process.env for values not already set.
// In production, just set real environment variables instead.
(function loadDotEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (key && !(key in process.env)) process.env[key] = value;
  }
})();

const PORT = process.env.PORT || 3000;
const EA_API_KEY = process.env.EA_API_KEY || '';
const APP_API_KEY = process.env.APP_API_KEY || '';
const DATA_FILE = process.env.DATA_FILE || path.join(__dirname, 'data.json');
const MAX_HISTORY = 500;
const STALE_AFTER_MS = 5 * 60 * 1000; // no heartbeat in 5 min => considered offline

if (!EA_API_KEY || !APP_API_KEY) {
  console.error(
    'FATAL: set EA_API_KEY and APP_API_KEY environment variables before starting the bridge ' +
      '(see .env.example). Refusing to start with empty/shared-by-default keys.'
  );
  process.exit(1);
}

// ---------------------------------------------------------------- storage

/** @type {Record<string, { accountTag: string, lastHeartbeat: any, control: {tradingEnabled: boolean, flattenAll: boolean}, history: any[] }>} */
let accounts = {};

function loadState() {
  try {
    const raw = fs.readFileSync(DATA_FILE, 'utf8');
    accounts = JSON.parse(raw);
  } catch (err) {
    if (err.code !== 'ENOENT') console.error('Failed to load state file, starting empty:', err.message);
    accounts = {};
  }
}

let saveTimer = null;
function saveStateSoon() {
  // Debounce writes; heartbeats can arrive every few seconds across accounts.
  if (saveTimer) return;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    fs.writeFile(DATA_FILE, JSON.stringify(accounts, null, 2), (err) => {
      if (err) console.error('Failed to persist state:', err.message);
    });
  }, 500);
}

function getOrCreateAccount(tag) {
  if (!accounts[tag]) {
    accounts[tag] = {
      accountTag: tag,
      lastHeartbeat: null,
      control: { tradingEnabled: true, flattenAll: false },
      history: [],
    };
  }
  return accounts[tag];
}

loadState();

// ------------------------------------------------------------------ auth

function timingSafeEqual(a, b) {
  const bufA = Buffer.from(String(a));
  const bufB = Buffer.from(String(b));
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

function requireKey(expected) {
  return (req, res, next) => {
    const provided = req.header('X-Api-Key') || '';
    if (!provided || !timingSafeEqual(provided, expected)) {
      return res.status(401).json({ error: 'unauthorized' });
    }
    next();
  };
}

const requireEaAuth = requireKey(EA_API_KEY);
const requireAppAuth = requireKey(APP_API_KEY);

// ------------------------------------------------------------------- app

const app = express();
app.use(express.json({ limit: '256kb' }));

app.get('/api/health', (req, res) => {
  res.json({ ok: true, accounts: Object.keys(accounts).length });
});

// ---- EA endpoints (outbound-only from MT5, via WebRequest) ----

// EA pushes a status heartbeat.
app.post('/api/heartbeat', requireEaAuth, (req, res) => {
  const body = req.body || {};
  const tag = String(body.accountTag || 'default').slice(0, 128);
  const account = getOrCreateAccount(tag);

  const receivedAt = Date.now();
  account.lastHeartbeat = { ...body, receivedAt };
  account.history.push({
    ts: receivedAt,
    equity: body.equity,
    balance: body.balance,
    positionsCount: Array.isArray(body.positions) ? body.positions.length : 0,
    tradesToday: body.tradesToday,
  });
  if (account.history.length > MAX_HISTORY) {
    account.history.splice(0, account.history.length - MAX_HISTORY);
  }

  saveStateSoon();
  res.json({ ok: true });
});

// EA polls for remote-control flags. flattenAll is one-shot: it is
// cleared as soon as it has been delivered once, so the EA only ever
// executes it a single time per app request.
app.get('/api/control', requireEaAuth, (req, res) => {
  const tag = String(req.query.accountTag || 'default').slice(0, 128);
  const account = getOrCreateAccount(tag);

  const response = {
    tradingEnabled: account.control.tradingEnabled,
    flattenAll: account.control.flattenAll,
  };

  if (account.control.flattenAll) {
    account.control.flattenAll = false; // one-shot ack
    saveStateSoon();
  }

  res.json(response);
});

// ---- iOS app endpoints ----

app.get('/api/app/accounts', requireAppAuth, (req, res) => {
  const list = Object.values(accounts).map((a) => ({
    accountTag: a.accountTag,
    online: !!a.lastHeartbeat && Date.now() - a.lastHeartbeat.receivedAt < STALE_AFTER_MS,
    lastSeen: a.lastHeartbeat ? a.lastHeartbeat.receivedAt : null,
  }));
  res.json({ accounts: list });
});

app.get('/api/app/status', requireAppAuth, (req, res) => {
  const tag = String(req.query.accountTag || 'default').slice(0, 128);
  const account = accounts[tag];
  if (!account) return res.status(404).json({ error: 'unknown accountTag' });

  const online = !!account.lastHeartbeat && Date.now() - account.lastHeartbeat.receivedAt < STALE_AFTER_MS;
  res.json({
    accountTag: tag,
    online,
    lastHeartbeat: account.lastHeartbeat,
    control: account.control,
  });
});

app.get('/api/app/history', requireAppAuth, (req, res) => {
  const tag = String(req.query.accountTag || 'default').slice(0, 128);
  const account = accounts[tag];
  if (!account) return res.status(404).json({ error: 'unknown accountTag' });
  const limit = Math.min(parseInt(req.query.limit, 10) || 200, MAX_HISTORY);
  res.json({ accountTag: tag, history: account.history.slice(-limit) });
});

// App sets control flags: { tradingEnabled?: boolean, flattenAll?: boolean }
app.post('/api/app/control', requireAppAuth, (req, res) => {
  const body = req.body || {};
  const tag = String(body.accountTag || 'default').slice(0, 128);
  const account = getOrCreateAccount(tag);

  if (typeof body.tradingEnabled === 'boolean') {
    account.control.tradingEnabled = body.tradingEnabled;
  }
  if (typeof body.flattenAll === 'boolean' && body.flattenAll) {
    account.control.flattenAll = true; // latched until the EA polls and acks it
  }

  saveStateSoon();
  res.json({ ok: true, control: account.control });
});

app.use((req, res) => res.status(404).json({ error: 'not found' }));

app.listen(PORT, () => {
  console.log(`BlazerTest MT5 bridge listening on :${PORT}`);
});
