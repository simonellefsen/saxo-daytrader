# saxo-daytrader-xai

Phase 33 foundation for a local Python day-trading assistant focused on a Danish SaxoInvestor portfolio.

## What Phase 23 includes

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
- Per-kind delivery routing so digests and broker alerts can target different Slack webhooks or email recipient lists
- Severity-based broker alert suppression so repeated low-signal events can be throttled without disabling higher-value alerts
- Named route profiles so several digest or alert kinds can share one delivery destination without repeated config
- Grouped broker alerts so several broker updates for the same order can be collapsed into one delivery
- Autonomous app launcher mode that starts the dashboard and background scheduler together for hands-off simulation trading
- Scheduler heartbeat and last-cycle status persisted to SQLite and shown in the dashboard
- One-click scheduler cycle controls in the dashboard for live or mock manual runs
- Route-profile formatting so subject prefixes, message preambles, and summary style can be shared across notification kinds
- Immutable scheduler cycle history with recent-cycle visibility in the dashboard
- Scheduler stale-worker detection with bounded auto-restart for launcher-managed autonomous mode
- Configurable scheduler cycle-history retention by age and row count
- Detection and repair of invalid legacy simulation trades that exceed available holdings
- Whole-share execution enforcement so queued and submitted equity orders use integer quantities only
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
.venv/bin/python main.py --with-scheduler
.venv/bin/python main.py --no-scheduler
.venv/bin/python main.py --sync-only
.venv/bin/python main.py --headless --port 8501 --no-browser
```

`main.py` imports the CSV into `ledger.db` before launching Streamlit.

By default, this project is now set up for autonomous simulation mode:

- `make run` starts both the Streamlit dashboard and the background scheduler
- the scheduler generates decisions during active analysis windows
- in simulation mode with `execution.auto_execute_simulation: true`, queued trades are executed automatically

If you only want the UI without autonomous execution, use:

```bash
make run-ui-only
```

## Config Reference

The project is driven by [config.yaml](/Users/lindau/codex/daytrader/config.yaml). Values written as `ENV:NAME` are loaded from `.env`.

### `app`

- `project_name`: display name used in the UI.
- `environment`: free-form environment label such as `local`.
- `dry_run`: when `true`, live broker submission and live broker management are blocked even if `execution.mode` is `live`.
- `simulation_mode`: legacy convenience flag; execution behavior is primarily controlled by `execution.mode`.
- `launch_scheduler_with_dashboard`: when `true`, `main.py` starts the background scheduler together with Streamlit.
- `scheduler_restart_on_failure`: if the launcher-managed scheduler dies or becomes stale, `main.py` may restart it.
- `scheduler_max_restarts`: maximum restart attempts per app run.
- `scheduler_restart_delay_seconds`: wait time before each restart attempt.

### `portfolio`

- `base_currency`: reporting currency. The project assumes `DKK`.
- `source_csv`: Saxo export used for the latest imported holdings baseline.
- `database_path`: SQLite database path, usually `ledger.db`.
- `initial_cash_dkk`: starting cash balance used for cash-aware portfolio value and buy-side limits. Buys reduce it, sells increase it through recorded `net_amount_dkk`.

### `market_data`

- `refresh_interval_seconds`: general UI/data refresh cadence for quote-oriented functions.
- `request_timeout_seconds`: timeout for market-data HTTP calls.
- `watchlists.nordic_limit`: number of Nordic names shown in the watchlist.
- `watchlists.global_limit`: number of US/Europe names shown in the watchlist.
- `rss.market_feeds`: RSS feeds for company/market headlines.
- `rss.macro_feeds`: RSS feeds for macro and central-bank headlines.

### `analysis_windows`

- `offset_minutes_after_open`: how long after an exchange open the system starts considering that market eligible for analysis.
- `duration_minutes`: how long the analysis window stays active after the offset.
- `calendar_refresh_interval_minutes`: how often exchange session calendars are refreshed.
- `calendar_lookback_days`: how much recent session history is cached.
- `calendar_lookahead_days`: how far future holiday/session data is cached.

Example: with `offset_minutes_after_open: 60` and `duration_minutes: 45`, a market that opens at `09:00` local will have an analysis window from `10:00` to `10:45` local.

### `scheduler`

- `enabled`: enables the scheduler worker.
- `poll_interval_minutes`: how often the worker wakes up and runs one scheduler cycle.
- `startup_run`: when `true`, a cycle runs immediately when the scheduler starts instead of waiting for the first interval boundary.
- `history_max_rows`: maximum scheduler-cycle history rows to retain.
- `history_retention_days`: maximum age of scheduler-cycle history rows.

### `execution`

- `mode`: `simulation` for local paper execution, `live` for Saxo broker submission/management.
- `adapter`: currently `saxo`.
- `auto_execute_simulation`: when `true`, simulation orders are executed automatically after queueing.
- `require_approval_live`: when `true`, live orders stay in approval state until manually approved.
- `min_trade_value_dkk`: ignores tiny orders below this estimated DKK size.
- `max_daily_orders`: daily cap on created execution orders.

### `risk`

- `excluded_symbols`: repo-safe list of blocked symbols.
- `excluded_symbols_csv`: optional comma-separated override loaded from environment.
- `max_position_weight`: maximum post-trade position weight as a fraction of total portfolio value.
- `allow_shorting`: should remain `false` for this project.

### `taxation`

- `share_income.currency`: tax reporting currency, expected to be `DKK`.
- `share_income.brackets`: Danish share-income brackets used by the sell calculator.

### `commissions`

- `default_rate`: percentage commission rate, e.g. `0.0008` for `0.08%`.
- `fx_conversion_rate`: FX conversion markup applied when trade currency is not `DKK`.
- `minimums`: per-exchange commission minima by currency and amount.

### `xai`

- `api_key`: xAI API key from `.env`.
- `base_url`: xAI API base URL.
- `model`: Grok model used for decision generation.
- `goal`: embedded objective included in every trading prompt.
- `timeout_seconds`: HTTP timeout for xAI calls.
- `auto_run_interval_minutes`: minimum spacing between automatic decision reports.
- `include_encrypted_reasoning`: when `true`, requests encrypted reasoning content from xAI.

### `saxo`

- `environment`: `SIM` or `LIVE`.
- `client_id`: Saxo app key.
- `client_secret`: Saxo app secret for secret-based auth flows.
- `client_key`: Saxo `ClientKey`, typically written by the OAuth helper.
- `account_key`: Saxo `AccountKey`, typically written by the OAuth helper.
- `session_path`: refreshable local Saxo session cache written by `--write-session`.

### `tradingview`

- `username`: TradingView username.
- `encrypted_password`: TradingView password secret.
- `totp_secret`: TOTP secret for 2FA automation.

### `notifications`

- `daily_summary_enabled`, `weekly_summary_enabled`, `monthly_summary_enabled`, `quarterly_summary_enabled`, `ytd_summary_enabled`: enable the corresponding digest types.
- `timezone`: local timezone for dispatch timing.
- `dispatch_hour_local`, `dispatch_minute_local`: local daily dispatch time.
- `weekly_dispatch_weekday_local`: weekday index for weekly digest dispatch.
- `monthly_dispatch_day_local`, `quarterly_dispatch_day_local`, `ytd_dispatch_day_local`: day-of-period dispatch points.
- `retry_backoff_minutes`: retry delay after a failed notification send.
- `max_attempts_per_day`: per-channel retry cap.
- `channel_cooldown_minutes`: minimum spacing between repeated sends of the same summary kind on the same channel.
- `summary_style`: default digest rendering style, e.g. `structured` or `compact`.
- `slack.enabled`: enable Slack delivery.
- `slack.webhook_url`: Slack webhook URL, normally from `.env`.
- `email.*`: SMTP configuration for email delivery.
- `alerts.broker_fill_enabled`: alerts for confirmed broker fills.
- `alerts.broker_reject_enabled`: alerts for broker rejections.
- `alerts.broker_cancel_enabled`: alerts for broker cancels/expirations.
- `alerts.execution_failure_enabled`: alerts when an execution order fails locally, e.g. session or lookup failure.
- `alerts.broker_management_failure_enabled`: alerts when a live cancel/replace request fails.
- `alert_suppression.*`: cooldown rules by severity.
- `alert_grouping.enabled`: group several broker updates for one order into one notification.
- `alert_grouping.max_items_per_group`: cap on grouped alert preview items.
- `route_profiles`: reusable delivery profiles.
- `routes`: per-summary-kind and per-alert-kind overrides, including:
  - `daily`, `weekly`, `monthly`, `quarterly`, `ytd`
  - `alert_broker_fill`, `alert_broker_reject`, `alert_broker_cancel`, `alert_broker_grouped`
  - `alert_execution_failed`, `alert_broker_management_failed`

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
- supports per-kind routing overrides for daily/weekly/monthly/quarterly/YTD digests and broker alert types
- suppresses repeated broker alerts per order scope using severity-specific cooldown windows
- supports named route profiles plus per-kind overrides for Slack webhooks and email recipients
- supports route-profile formatting for subject prefixes, message preambles, and compact vs structured summary rendering
- can group several broker updates for one execution order into a single notification payload
- records scheduler activity in `audit_log`
- exposes dead/stale worker detection in the dashboard using heartbeat age plus stored scheduler PID
- can be auto-restarted by `main.py` when launched in autonomous mode, using the configured restart budget in `app.scheduler_*`
- prunes old scheduler cycle-history rows automatically according to `scheduler.history_max_rows` and `scheduler.history_retention_days`
- flags impossible simulation trades in the Execution tab and can quarantine them from the effective portfolio state
- normalizes order quantities to whole shares before queueing, simulation execution, and Saxo order submission
- resolves Saxo instruments by Saxo's own symbol and exchange aliases, so `SBUX:xnas` and `MU:xnas` map correctly during broker submission
- pushes notification alerts when live execution fails, including session, lookup, and broker submission errors
- handles broker-side cancel/replace failures cleanly in the UI and pushes notifications for management failures without crashing Streamlit
- supports configurable starting cash in DKK, shows live cash balance in the portfolio summary, and adjusts cash automatically as trades execute

### What One Scheduler Cycle Does

Each scheduler cycle does the following in order:

1. Updates scheduler heartbeat and status in SQLite.
2. Refreshes exchange calendars if their refresh interval has elapsed.
3. Computes current market status and whether any analysis window is active.
4. Decides whether a new xAI decision report should be generated.
5. If eligible, generates a decision report.
6. Queues trades from the latest completed decision report.
7. If `execution.mode: simulation` and `execution.auto_execute_simulation: true`, executes queued simulation orders immediately.
8. Synchronizes broker order status for live orders.
9. Dispatches due digests and broker alerts.
10. Records cycle history and prunes old scheduler history rows.

### What `poll_interval_minutes: 10` Means

`scheduler.poll_interval_minutes: 10` means the background worker wakes up every 10 minutes and runs exactly one scheduler cycle.

It does not mean:

- the app waits 10 minutes before every single trade inside a cycle
- the app trades exactly every 10 minutes
- the app generates a new xAI report every 10 minutes regardless of market state

What it really means in practice:

- the worker checks conditions every 10 minutes
- if no analysis window is active, the cycle mostly updates status and exits
- if an analysis window is active, the cycle may generate a new decision report if `xai.auto_run_interval_minutes` also allows it
- if a completed report exists, orders may be queued and simulation orders may execute in that same cycle

### Poll Interval vs Analysis Window

These settings interact:

- `analysis_windows.offset_minutes_after_open`
- `analysis_windows.duration_minutes`
- `scheduler.poll_interval_minutes`
- `xai.auto_run_interval_minutes`

Example with the current defaults:

- exchange opens at `09:00`
- analysis window starts at `10:00`
- analysis window ends at `10:45`
- scheduler polls at `09:50`, `10:00`, `10:10`, `10:20`, `10:30`, `10:40`, `10:50`

In that case:

- `09:50`: no analysis yet
- `10:00`: eligible
- `10:10`: still eligible
- `10:20`: still eligible
- `10:30`: still eligible
- `10:40`: still eligible
- `10:50`: too late

So a 10-minute poll interval gives you roughly 5 chances to catch a 45-minute analysis window.

If you make `poll_interval_minutes` too large relative to `duration_minutes`, you can miss windows entirely. For example:

- `poll_interval_minutes: 30`
- `duration_minutes: 45`

would be much easier to miss if the scheduler happens to poll just before the window opens and then only again after it closes.

### Recommended Scheduler Settings

- `poll_interval_minutes: 10` is a reasonable default for a lightweight always-on process.
- Use `5` if you want tighter reaction time and are comfortable with more frequent API/database activity.
- Avoid setting it higher than the analysis window duration unless you are comfortable occasionally missing an opportunity window.
- Keep `startup_run: true` so a restart immediately re-evaluates the system instead of waiting for the next 15-minute boundary.

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

## Slack webhooks

Slack notifications use a Slack app with Incoming Webhooks.

Current setup flow, matching Slack's official documentation:

1. Go to [Slack apps](https://api.slack.com/apps).
2. Create a new app `From scratch` and select your Slack workspace.
3. In the app settings, open [Incoming Webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks).
4. Turn `Activate Incoming Webhooks` on.
5. Click `Add New Webhook to Workspace`.
6. Choose the Slack channel that should receive notifications and authorize the app.
7. Copy the generated webhook URL. It will look like `https://hooks.slack.com/services/...`.

Add the webhook to your local `.env`:

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Then enable Slack delivery in `config.yaml`:

```yaml
notifications:
  slack:
    enabled: true
    webhook_url: ENV:SLACK_WEBHOOK_URL
```

If you want different Slack destinations for different digests or broker alerts, use either per-kind routes or route profiles in `config.yaml`:

```yaml
notifications:
  route_profiles:
    ops:
      slack_webhook_url: ENV:SLACK_WEBHOOK_URL
  routes:
    weekly:
      profile: ops
    alert_broker_reject:
      profile: ops
```

Route profiles can also share formatting across multiple delivery kinds:

```yaml
notifications:
  route_profiles:
    ops:
      slack_webhook_url: ENV:SLACK_WEBHOOK_URL
      subject_prefix: "[OPS]"
      message_preamble: "Shared profile preamble"
      summary_style: compact
  routes:
    weekly:
      profile: ops
    alert_broker_fill:
      profile: ops
```

Treat webhook URLs as secrets. Do not commit them to git. Slack's documentation notes that leaked webhook URLs are actively revoked.

## Repository safety

Before pushing this project to GitHub:

- Keep real credentials only in `.env`, never in tracked files.
- Use `.env.example` as the committed template.
- Do not commit Saxo exports, position CSV files, SQLite databases, or tax/audit exports.
- The included `.gitignore` blocks `.env`, `*.csv`, and local database files by default.
- If anything sensitive has already been pushed to the public repo, removing it in a new commit is not enough. Rewrite git history and rotate the affected credentials.

## Validation

Run the Phase 33 validation script:

```bash
.venv/bin/python scripts/validate_phase33.py
```

Earlier phase validations remain available. To validate against the live xAI API:

```bash
.venv/bin/python scripts/validate_phase4.py --live
```

Expected output shape:

```text
Phase 33 validation passed.
Starting cash DKK: 10000.00
Cash after buy DKK: 9xxx.xx
Insufficient-cash status: execution_failed
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
.venv/bin/python scripts/validate_phase17.py
.venv/bin/python scripts/validate_phase18.py
.venv/bin/python scripts/validate_phase19.py
.venv/bin/python scripts/validate_phase20.py
.venv/bin/python scripts/validate_phase21.py
.venv/bin/python scripts/validate_phase22.py
.venv/bin/python scripts/validate_phase23.py
.venv/bin/python scripts/validate_phase24.py
.venv/bin/python scripts/validate_phase25.py
.venv/bin/python scripts/validate_phase26.py
.venv/bin/python scripts/validate_phase27.py
.venv/bin/python scripts/validate_phase28.py
.venv/bin/python scripts/validate_phase29.py
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
│   └── validate_phase17.py
│   └── validate_phase18.py
│   └── validate_phase19.py
│   └── validate_phase20.py
│   └── validate_phase21.py
│   └── validate_phase22.py
│   └── validate_phase23.py
│   └── validate_phase24.py
│   └── validate_phase25.py
│   └── validate_phase26.py
│   └── validate_phase27.py
│   └── validate_phase28.py
│   └── validate_phase29.py
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

1. Add a launcher-visible incident counter and cooldown so repeated scheduler crashes can be surfaced more clearly in the dashboard.
2. Add a one-click dashboard action to prune scheduler history immediately using the current retention policy.
