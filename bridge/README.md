# BlazerTest MT5 Bridge

A small Node.js/Express server that sits between the `XAUUSD_Confluence_EA`
(MQL5, running inside an MT5 terminal) and the iOS monitor/control app,
because neither one can talk to the other directly:

- The MT5 terminal can only make **outbound** HTTP requests (`WebRequest`) —
  it cannot accept inbound connections from a phone.
- The iOS app has no way to reach a home/office MT5 terminal directly either
  (no public IP, no port forwarding you'd want to expose a trading terminal
  on).

So both sides talk to this bridge instead:

```
XAUUSD_Confluence_EA  --POST heartbeat-->   bridge   <--GET status---  iOS app
(MT5 terminal, VPS)   <--GET control----             ---POST control-->
```

State (last heartbeat + control flags, per account) is kept in memory and
mirrored to a JSON file (`data.json` by default) so a restart doesn't lose
the "trading enabled" flag.

## Run it

```bash
cd bridge
npm install
cp .env.example .env
# edit .env: set EA_API_KEY and APP_API_KEY to two DIFFERENT random secrets
#   openssl rand -hex 32
npm start
```

The server refuses to start if either key is unset — there is no insecure
default.

Deploy it anywhere that can run Node 18+ and give it a stable HTTPS URL
(a small VPS with a reverse proxy for TLS, Render, Fly.io, Railway, etc. all
work — this app has no database and one process is plenty). **Put it behind
HTTPS** — the iOS app's networking config refuses plain HTTP, and the EA's
`WebRequest` also expects the domain whitelisted in the terminal, which
should be the HTTPS origin.

## API

All endpoints require an `X-Api-Key` header. `EA_API_KEY` and `APP_API_KEY`
are deliberately separate — the EA's key lives in a `.set` file on a VPS,
the app's key lives on a phone; a leak of one shouldn't hand over the
other's capabilities (the EA's key alone can't disable trading or flatten
positions).

| Method | Path | Auth | Used by | Purpose |
|---|---|---|---|---|
| GET | `/api/health` | none | anyone | liveness check |
| POST | `/api/heartbeat` | `EA_API_KEY` | EA | push account/position status |
| GET | `/api/control?accountTag=` | `EA_API_KEY` | EA | poll remote-control flags |
| GET | `/api/app/accounts` | `APP_API_KEY` | app | list known accounts + online state |
| GET | `/api/app/status?accountTag=` | `APP_API_KEY` | app | latest heartbeat + control state |
| GET | `/api/app/history?accountTag=&limit=` | `APP_API_KEY` | app | rolling equity/balance history |
| POST | `/api/app/control` | `APP_API_KEY` | app | set `tradingEnabled` / request `flattenAll` |

`flattenAll` is one-shot: it's cleared the moment the EA's next `/api/control`
poll delivers it, so it only ever fires once per app request, not on every
poll thereafter.

## Multiple accounts / EAs

Every request is scoped by `accountTag`, an arbitrary string the EA sends
(its `InpBridgeAccountTag` input, or the MT5 account login number if that's
left blank). Point several EA instances at the same bridge with different
tags and they get independent state; the app's account picker (or a single
configured tag in Settings) selects which one to view.

## Wiring up the EA

In the EA's inputs (Tools > Options > Expert Advisors, add the bridge's
HTTPS origin to *"Allow WebRequest for listed URL"* first, or every call
fails with error 4060):

- `InpBridgeEnabled` = `true`
- `InpBridgeURL` = your bridge's base URL, e.g. `https://mt5-bridge.yourdomain.com`
- `InpBridgeApiKey` = the bridge's `EA_API_KEY`
- `InpBridgeAccountTag` = a label for this account (optional — defaults to
  the MT5 account login number)
