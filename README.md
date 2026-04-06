# saxo-daytrader-xai

Phase 16 foundation for a local Python day-trading assistant focused on a Danish SaxoInvestor portfolio.

## What Phase 16 includes

- Python 3.11+ project scaffold
- Local SQLite database at `ledger.db`
- Configurable `config.yaml` with placeholders for API keys, Saxo credentials, exclusions, tax brackets, and commission settings
- CSV importer for the attached Saxo position export
- Strict exclusion of `NOVOb:xcse` and `TSLA:xnas`
- Tax-lot tracking based on imported holdings
- Danish share-income tax engine with 27% / 42% brackets
- Configurable commission and FX-conversion cost handling
- Immutable trade ledger and lot-realization records
- xAI decision engine using the official Responses API
- Structured JSON decision reports with step-by-step rationale, watchlist focus, and suggested trades
- Decision report persistence with prompt, raw response, parsed report, and error tracking
- APScheduler-based background worker for recurring analysis cycles
- Exchange-calendar driven market hours, holiday closures, and daylight-saving aware session timing
- Simulation execution queue with immutable ledger updates
- Live-mode approval queue with dry-run protection
- Saxo OpenAPI session cache with refresh-token reuse
- Saxo instrument lookup, precheck, and order submission for approved live orders
- Saxo broker-status synchronization for submitted live orders
- Local ledger reconciliation when Saxo reports a confirmed final fill
- Incremental local ledger reconciliation for confirmed partial fills
- Immutable `execution_fills` records for broker fill history and deduplication
- Immutable `execution_order_events` records for broker-side amendments, cancellations, rejections, and working-order state changes
- Broker-side amendment reconciliation that updates local working order quantity and price from Saxo
- Scheduler-driven daily performance summary generation
- Optional Slack webhook and SMTP email delivery for daily, weekly, monthly, quarterly, and YTD summaries
- Immutable `notification_deliveries` records and notification history in the UI
- Renderable `systemd` and `launchd` service templates for unattended local deployment
- Live Saxo order-management actions for broker-side replace and cancel requests
- Notification throttling, retry backoff, and richer structured daily-summary formatting
- Weekly and monthly digest generation with independent scheduling and per-kind notification deduplication
- Quarterly and year-to-date digest generation using the same immutable notification pipeline
- Broker alert notifications for fills, rejections, and cancel confirmations
- Audit bundle CSV export for ledger, decisions, executions, and tax records
- Streamlit dashboard with:
  - portfolio summary in DKK
  - holdings allocation table with live quote refresh support
  - daily refreshed Nordic and global watchlists
  - news, earnings, and macro headline tabs
  - market status and analysis-window detection
  - realised gain / tax summary from the trade ledger
  - a Decision Report tab that can auto-run during analysis windows or run on demand
  - an Execution tab for queued orders, live approvals, Saxo submission status, broker sync, and audit export
  - live broker order replace/cancel controls for manageable Saxo orders
  - a Notifications tab with summary preview and delivery history

## Install

```bash
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python main.py
```

Useful options:

```bash
.venv/bin/python main.py --sync-only
.venv/bin/python main.py --headless --port 8501 --no-browser
```

`main.py` imports the CSV into `ledger.db` before launching Streamlit.

## Scheduler

Run the always-on background worker:

```bash
.venv/bin/python scripts/run_scheduler.py
```

Run one mock scheduler cycle for smoke testing:

```bash
.venv/bin/python scripts/run_scheduler.py --once --mock-decisions --force-decision
```

The scheduler:

- checks the configured exchange analysis windows
- refreshes the exchange-calendar cache on a recurring interval
- generates xAI decision reports during eligible windows
- queues suggested trades
- auto-executes queued trades in simulation mode
- sends one daily summary per configured channel after the local dispatch time
- sends optional weekly, monthly, quarterly, and YTD digests after their configured local dispatch windows
- sends optional broker event alerts when fills, rejections, or confirmed cancellations appear in the local broker sync tables
- records scheduler activity in `audit_log`

## Deployment

Render `systemd` and `launchd` service examples for the current workspace:

```bash
.venv/bin/python scripts/render_service_templates.py
```

Or:

```bash
make render-services
```

This writes rendered files into `deploy/rendered/` using the current repo path and `.venv/bin/python`.

Rendered files:

- `deploy/rendered/systemd/saxo-daytrader-scheduler.service`
- `deploy/rendered/systemd/saxo-daytrader-dashboard.service`
- `deploy/rendered/launchd/com.saxo-daytrader.scheduler.plist`
- `deploy/rendered/launchd/com.saxo-daytrader.dashboard.plist`

Typical install flow:

1. Render the templates.
2. Review the generated paths, user, and port.
3. Copy the chosen service file into your OS service directory.
4. Enable the scheduler service first.
5. Optionally enable the dashboard service if you want the Streamlit UI always running.

Example `systemd` commands:

```bash
sudo cp deploy/rendered/systemd/saxo-daytrader-scheduler.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now saxo-daytrader-scheduler.service
```

Example `launchd` commands:

```bash
cp deploy/rendered/launchd/com.saxo-daytrader.scheduler.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.saxo-daytrader.scheduler.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.saxo-daytrader.scheduler.plist
```

## Saxo OAuth helper

The project now auto-loads `.env` from the workspace root.

To discover your Saxo `ClientKey` and `AccountKey` after creating an OpenAPI app, use:

```bash
.venv/bin/python scripts/saxo_oauth_helper.py --environment sim --auth-mode pkce
```

Or with app secret:

```bash
.venv/bin/python scripts/saxo_oauth_helper.py --environment live --auth-mode secret
```

Notes:

- The same helper works for both `sim` and `live`.
- `sim` and `live` need separate Saxo app credentials.
- Your app must have a redirect URI that matches `SAXO_REDIRECT_URI` in `.env`.
- For local use, set a localhost redirect such as `http://localhost:8765/callback`.
- Use `--write-env` if you want the helper to write `SAXO_ENVIRONMENT`, `SAXO_CLIENT_KEY`, and `SAXO_ACCOUNT_KEY` back into `.env`.
- Use `--write-session` if you want the helper to persist a refreshable local session cache at `.secrets/saxo_session.json`.

Examples:

```bash
.venv/bin/python scripts/saxo_oauth_helper.py --environment sim --auth-mode pkce --write-env --write-session
.venv/bin/python scripts/saxo_oauth_helper.py --environment live --auth-mode secret --write-env --write-session
```

The session cache is ignored by git and used by the live Saxo adapter to refresh access tokens automatically.

## Repository safety

Before pushing this project to GitHub:

- Keep real credentials only in `.env`, never in tracked files.
- Use `.env.example` as the committed template.
- Do not commit Saxo exports, position CSV files, SQLite databases, or tax/audit exports.
- The included `.gitignore` blocks `.env`, `*.csv`, and local database files by default.
- If anything sensitive has already been pushed to the public repo, removing it in a new commit is not enough. Rewrite git history and rotate the affected credentials.

## Validation

Run the Phase 16 validation script:

```bash
.venv/bin/python scripts/validate_phase16.py
```

Earlier phase validations remain available. To validate against the live xAI API:

```bash
.venv/bin/python scripts/validate_phase4.py --live
```

Expected output shape:

```text
Phase 16 validation passed.
Imported source positions: 20
Excluded positions: 2
Alert deliveries sent: 3
Slack success payloads: 3
```

The exact order id values can vary slightly with the imported portfolio snapshot.

Earlier validation scripts remain available:

```bash
.venv/bin/python scripts/validate_phase1.py
.venv/bin/python scripts/validate_phase2.py
.venv/bin/python scripts/validate_phase3.py
.venv/bin/python scripts/validate_phase4.py
.venv/bin/python scripts/validate_phase5.py
.venv/bin/python scripts/validate_phase6.py
.venv/bin/python scripts/validate_phase7.py
.venv/bin/python scripts/validate_phase8.py
.venv/bin/python scripts/validate_phase9.py
.venv/bin/python scripts/validate_phase10.py
.venv/bin/python scripts/validate_phase11.py
.venv/bin/python scripts/validate_phase12.py
.venv/bin/python scripts/validate_phase13.py
.venv/bin/python scripts/validate_phase14.py
.venv/bin/python scripts/validate_phase15.py
.venv/bin/python scripts/validate_phase16.py
```

## Project layout

```text
.
├── config.yaml
├── main.py
├── requirements.txt
├── scripts/
│   └── validate_phase1.py
│   └── validate_phase2.py
│   └── validate_phase3.py
│   └── validate_phase4.py
│   └── validate_phase5.py
│   └── validate_phase6.py
│   └── validate_phase7.py
│   └── validate_phase8.py
│   └── validate_phase9.py
│   └── validate_phase10.py
│   └── validate_phase11.py
│   └── validate_phase12.py
│   └── validate_phase13.py
│   └── validate_phase14.py
│   └── validate_phase15.py
│   └── validate_phase16.py
└── src/
    └── saxo_daytrader_xai/
        ├── config.py
        ├── db.py
        ├── execution_engine.py
        ├── fx_service.py
        ├── importer.py
        ├── market_data.py
        ├── market_news.py
        ├── market_schedule.py
        ├── market_symbols.py
        ├── notifications.py
        ├── portfolio.py
        ├── saxo_openapi.py
        ├── scheduler_service.py
        ├── tax_engine.py
        ├── watchlists.py
        ├── xai_decision.py
        └── ui/
            └── app.py
```

## Next-phase todo

1. Add per-channel routing so digest kinds and broker alerts can target different Slack channels or email recipients.
2. Add severity-based alert suppression so repeated low-signal broker events do not spam the delivery channels.
