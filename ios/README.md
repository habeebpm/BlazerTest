# BlazerTest MT5 Monitor (iOS)

A SwiftUI app that **monitors and remotely controls** the `XAUUSD_Confluence_EA`
running in a MetaTrader 5 terminal — it shows live account/position status and
can remotely pause/resume new entries or flatten all positions. See
[`../README.md`](../README.md) for why it works this way instead of running
the EA "on" the phone.

This project was authored without access to Xcode/macOS, so it ships as
source + an [XcodeGen](https://github.com/yonaskolb/XcodeGen) spec rather than
a hand-crafted `.xcodeproj` (much less error-prone to generate than to
hand-edit `project.pbxproj` blind). You need a Mac with Xcode to build it.

## Prerequisites

- macOS with Xcode 15+ installed.
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- A running bridge server (see [`../bridge`](../bridge)) reachable over
  HTTPS from your phone.

## Generate and open the project

```bash
cd ios
xcodegen generate
open BlazerMT5Monitor.xcodeproj
```

Then in Xcode: pick a simulator or your device, set your own Team under
*Signing & Capabilities* (required to run on a physical device), and Run
(⌘R).

If `xcodegen generate` complains about the spec (versions of XcodeGen do
drift slightly), it's almost always a one-line fix in `project.yml` — the
app's Swift source under `BlazerMT5Monitor/Sources` doesn't depend on how
the project file itself is generated.

## First launch

1. Open the **Settings** tab.
2. Enter:
   - **Bridge URL** — e.g. `https://mt5-bridge.yourdomain.com` (must be
     HTTPS; the app's networking config disallows plain HTTP).
   - **App API key** — the bridge's `APP_API_KEY` (not the EA's key).
   - **Account tag** — must match the EA's `InpBridgeAccountTag` input (or
     its MT5 account login number, if that input was left blank).
3. Switch to **Dashboard** — it polls the bridge every 10 seconds for
   account/position status, and lets you toggle trading or flatten all
   positions.
4. **History** shows a simple equity sparkline built from the bridge's
   rolling history for that account.

## What this app is and isn't

- It **is** a live view into an EA that keeps trading autonomously on a VPS
  or desktop MT5 terminal, plus a remote kill-switch/pause control.
- It **is not** a trading engine itself — it never computes signals or
  places orders; all of that stays in the MQL5 EA, matching your backtested
  logic exactly.
- Pausing ("Trading Enabled" off) only blocks *new* entries — the EA keeps
  managing any already-open positions (breakeven/trailing) so they aren't
  left unmanaged. "Flatten All" closes everything immediately.

## Project layout

```
ios/
  project.yml                        # XcodeGen spec
  BlazerMT5Monitor/
    Sources/
      App.swift                      # @main entry point
      Models/BridgeModels.swift      # Codable types matching the bridge API
      Networking/BridgeClient.swift  # URLSession client for /api/app/*
      Networking/KeychainStore.swift # Keychain wrapper for the API key
      ViewModels/AppSettings.swift   # persisted connection settings
      ViewModels/DashboardViewModel.swift
      Views/                         # SwiftUI screens
```

## Extending it

Reasonable next steps, deliberately left out to keep this a small, auditable
starting point:

- **Push notifications** for daily-loss-limit hits or the EA going offline —
  add APNs to the bridge and register a device token from the app.
- **Multiple accounts** — `GET /api/app/accounts` on the bridge already
  supports it; add an account picker in place of the single `accountTag`
  setting.
- **Face ID / Touch ID gate** before revealing the flatten-all control.
