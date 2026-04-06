# saxo-daytrader-xai

Phase 5 foundation for a local Python day-trading assistant focused on a Danish SaxoInvestor portfolio.

## What Phase 5 includes

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
- Simulation execution queue with immutable ledger updates
- Live-mode approval queue with dry-run protection
- Audit bundle CSV export for ledger, decisions, executions, and tax records
- Streamlit dashboard with:
  - portfolio summary in DKK
  - holdings allocation table with live quote refresh support
  - daily refreshed Nordic and global watchlists
  - news, earnings, and macro headline tabs
  - market status and analysis-window detection
  - realised gain / tax summary from the trade ledger
  - a Decision Report tab that can auto-run during analysis windows or run on demand
  - an Execution tab for queued orders, live approvals, and audit export

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
- generates xAI decision reports during eligible windows
- queues suggested trades
- auto-executes queued trades in simulation mode
- records scheduler activity in `audit_log`

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

## Repository safety

Before pushing this project to GitHub:

- Keep real credentials only in `.env`, never in tracked files.
- Use `.env.example` as the committed template.
- Do not commit Saxo exports, position CSV files, SQLite databases, or tax/audit exports.
- The included `.gitignore` blocks `.env`, `*.csv`, and local database files by default.
- If anything sensitive has already been pushed to the public repo, removing it in a new commit is not enough. Rewrite git history and rotate the affected credentials.

## Validation

Run the Phase 5 validation script:

```bash
.venv/bin/python scripts/validate_phase5.py
```

Phase 4 validation remains available. To validate against the live xAI API:

```bash
.venv/bin/python scripts/validate_phase4.py --live
```

Expected output shape:

```text
Phase 5 validation passed.
Imported source positions: 20
Excluded positions: 2
Execution orders created: 1
Executed simulation orders: 3
Trade ledger rows: 3
```

The exact queued-order count can vary slightly with the imported portfolio snapshot.

Earlier validation scripts remain available:

```bash
.venv/bin/python scripts/validate_phase1.py
.venv/bin/python scripts/validate_phase2.py
.venv/bin/python scripts/validate_phase3.py
.venv/bin/python scripts/validate_phase4.py
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
        ├── portfolio.py
        ├── scheduler_service.py
        ├── tax_engine.py
        ├── watchlists.py
        ├── xai_decision.py
        └── ui/
            └── app.py
```

## Next-phase todo

1. Wire approved live orders to the Saxo OpenAPI order endpoints once OAuth access-token storage is in place.
2. Add optional Slack or email daily summaries from the scheduler worker.
3. Add systemd and launchd service examples for unattended local deployment.
