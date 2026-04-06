from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import append_audit_log, connect, init_db
from saxo_daytrader_xai.fx_service import fetch_ecb_fx_rates, fx_rate_to_dkk
from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.portfolio import fetch_latest_batch_id, fetch_portfolio_positions


def _resolve_config(config: dict[str, Any] | None, config_path: str | Path) -> dict[str, Any]:
    if config is not None:
        return config
    return load_config(config_path)


def _monitor_timezone(config: dict[str, Any]):
    return pytz.timezone(config.get("price_monitor", {}).get("timezone", "Europe/Copenhagen"))


def _baseline_session_date(config: dict[str, Any], reference_time: datetime | None = None) -> str:
    timezone = _monitor_timezone(config)
    local_now = (reference_time or datetime.now(UTC)).astimezone(timezone)
    reset_hour = int(config.get("price_monitor", {}).get("reset_hour_local", 6))
    session_date = local_now.date()
    if local_now.hour < reset_hour:
        session_date = session_date - timedelta(days=1)
    return session_date.isoformat()


def refresh_portfolio_price_state(
    *,
    config: dict[str, Any] | None = None,
    config_path: str | Path = "config.yaml",
    connection=None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    resolved_config = _resolve_config(config, config_path)
    resolved_connection = connection or connect(resolved_config["portfolio"]["database_path"])
    init_db(resolved_connection)
    should_close = connection is None

    try:
        if not bool(resolved_config.get("price_monitor", {}).get("enabled", True)):
            return {"status": "disabled", "updated": 0, "baseline_session_date": None}

        batch_id = fetch_latest_batch_id(resolved_connection)
        initial_cash_dkk = float(resolved_config.get("portfolio", {}).get("initial_cash_dkk", 0.0) or 0.0)
        positions = fetch_portfolio_positions(
            resolved_connection,
            batch_id=batch_id,
            initial_cash_dkk=initial_cash_dkk,
        )
        if not positions:
            return {"status": "no_positions", "updated": 0, "baseline_session_date": None}

        symbols = [row["symbol"] for row in positions]
        quote_rows = fetch_live_prices(
            symbols,
            timeout_seconds=int(resolved_config["market_data"]["request_timeout_seconds"]),
        )
        fx_snapshot = fetch_ecb_fx_rates()
        quote_by_symbol = {row["symbol"]: row for row in quote_rows}
        position_by_symbol = {row["symbol"]: row for row in positions}
        existing_rows = {
            row["symbol"]: dict(row)
            for row in resolved_connection.execute("SELECT * FROM portfolio_price_snapshots").fetchall()
        }
        baseline_session_date = _baseline_session_date(resolved_config, reference_time=reference_time)
        updated_at = (reference_time or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")

        updated = 0
        for symbol in symbols:
            quote = quote_by_symbol.get(symbol, {})
            position = position_by_symbol[symbol]
            current_price_local = quote.get("current_price")
            previous_close_local = quote.get("previous_close")
            change_pct = quote.get("change_pct")
            currency = position.get("currency")
            current_fx_rate_to_dkk = fx_rate_to_dkk(currency, fx_snapshot)
            existing = existing_rows.get(symbol)

            baseline_price_local = existing.get("baseline_price_local") if existing else None
            baseline_fx_rate_to_dkk = existing.get("baseline_fx_rate_to_dkk") if existing else None
            baseline_at = existing.get("baseline_at") if existing else None
            existing_session_date = existing.get("baseline_session_date") if existing else None

            if (
                existing_session_date != baseline_session_date
                or baseline_price_local is None
                or baseline_fx_rate_to_dkk is None
            ) and current_price_local is not None:
                baseline_price_local = current_price_local
                baseline_fx_rate_to_dkk = current_fx_rate_to_dkk
                baseline_at = updated_at

            resolved_connection.execute(
                """
                INSERT INTO portfolio_price_snapshots (
                    symbol, updated_at, baseline_session_date, baseline_at,
                    current_price_local, current_fx_rate_to_dkk, previous_close_local, change_pct,
                    currency, source, status, baseline_price_local, baseline_fx_rate_to_dkk
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    baseline_session_date = excluded.baseline_session_date,
                    baseline_at = excluded.baseline_at,
                    current_price_local = excluded.current_price_local,
                    current_fx_rate_to_dkk = excluded.current_fx_rate_to_dkk,
                    previous_close_local = excluded.previous_close_local,
                    change_pct = excluded.change_pct,
                    currency = excluded.currency,
                    source = excluded.source,
                    status = excluded.status,
                    baseline_price_local = excluded.baseline_price_local,
                    baseline_fx_rate_to_dkk = excluded.baseline_fx_rate_to_dkk
                """,
                (
                    symbol,
                    updated_at,
                    baseline_session_date,
                    baseline_at,
                    current_price_local,
                    current_fx_rate_to_dkk,
                    previous_close_local,
                    change_pct,
                    currency,
                    quote.get("source"),
                    quote.get("status"),
                    baseline_price_local,
                    baseline_fx_rate_to_dkk,
                ),
            )
            updated += 1

        resolved_connection.commit()
        payload = {
            "status": "ok",
            "updated": updated,
            "baseline_session_date": baseline_session_date,
            "symbols": symbols,
        }
        append_audit_log(resolved_connection, "portfolio_price_state_refreshed", payload)
        return payload
    finally:
        if should_close:
            resolved_connection.close()
