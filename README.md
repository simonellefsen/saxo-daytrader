# saxo-daytrader-xai

Phase 3 foundation for a local Python day-trading assistant focused on a Danish SaxoInvestor portfolio.

## What Phase 3 includes

- Python 3.11+ project scaffold
- Local SQLite database at `ledger.db`
- Configurable `config.yaml` with placeholders for API keys, Saxo credentials, exclusions, tax brackets, and commission settings
- CSV importer for the attached Saxo position export
- Strict exclusion of `NOVOb:xcse` and `TSLA:xnas`
- Tax-lot tracking based on imported holdings
- Danish share-income tax engine with 27% / 42% brackets
- Configurable commission and FX-conversion cost handling
- Immutable trade ledger and lot-realization records
- Streamlit dashboard with:
  - portfolio summary in DKK
  - holdings allocation table with live quote refresh support
  - daily refreshed Nordic and global watchlists
  - news, earnings, and macro headline tabs
  - market status and analysis-window detection
  - realised gain / tax summary from the trade ledger

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

Run the Phase 3 validation script:

```bash
.venv/bin/python scripts/validate_phase3.py
```

Expected output for the provided `Positioner_05-apr-2026_12_16_34.csv`:

```text
Phase 3 validation passed.
Imported source positions: 20
Excluded positions: 2
Active positions in DB: 18
Simulated sells: 18
Realised gains DKK: -1740.61
Tax impact DKK: 0.00
Commission DKK: 361.44
```

The script then prints exact per-position sell outcomes for a 10% simulation across all imported holdings.

Earlier validation scripts remain available:

```bash
.venv/bin/python scripts/validate_phase1.py
.venv/bin/python scripts/validate_phase2.py
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
└── src/
    └── saxo_daytrader_xai/
        ├── config.py
        ├── db.py
        ├── importer.py
        ├── market_data.py
        ├── market_news.py
        ├── market_schedule.py
        ├── market_symbols.py
        ├── portfolio.py
        ├── tax_engine.py
        ├── watchlists.py
        └── ui/
            └── app.py
```

## Next-phase todo

1. Add the xAI decision engine with the full internal trading prompt and JSON trade suggestions.
2. Store xAI prompts, responses, and decision reports in the immutable audit trail.
3. Add a decision-report page to the UI and wire the analysis window to run the xAI engine on schedule.
