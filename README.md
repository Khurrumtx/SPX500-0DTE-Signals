# SPX500-0DTE-Signals

A mobile-first **PWA** dashboard for SPX500 0DTE signals that runs on iPhone
(installable to the Home Screen).

## Layout

- `dashboard/` — the PWA (HTML/CSS/JS, service worker, manifest, icons)
- `dashboard/signals/signal.json` — signal data the dashboard polls
- `signals/signal.json` — source-of-truth signal file (mirror)
- `scripts/` — signal generators (TBD)

## Run locally

```bash
cd dashboard
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy to GitHub Pages

1. Push to `main`.
2. In the repo on GitHub: **Settings → Pages → Source: Deploy from a branch**.
3. Branch: `main`, Folder: `/dashboard`. Save.
4. After a minute the site is live at `https://<user>.github.io/SPX500-0DTE-Signals/`.

## Install on iPhone

1. Open the URL above in **Safari** (not Chrome — only Safari can install PWAs on iOS).
2. Tap the Share icon → **Add to Home Screen** → Add.
3. Launch the app from your Home Screen.
4. Open Settings (gear icon) → toggle **Signal notifications** on and allow when prompted.

Notification support requires **iOS 16.4+** and only works once the PWA has
been installed to the Home Screen.

## Updating signals

The dashboard fetches `dashboard/signals/signal.json` every 15s. Update that
file (e.g. from a scheduled GitHub Action that runs your signal script) and
the app picks it up on the next poll. Expected schema:

```json
{ "signal": "CALL" | "PUT" | "WAIT" | "NONE", "timestamp": "2026-05-23T14:30:00Z", "confidence": 0-100 }
```

When `signal` or `timestamp` changes to a CALL/PUT, the app fires a local
notification (if notifications are enabled).
