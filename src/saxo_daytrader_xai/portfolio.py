from __future__ import annotations

import sqlite3
from typing import Any


def fetch_latest_batch_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT batch_id FROM import_batches ORDER BY imported_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return row["batch_id"] if row else None


def fetch_portfolio_positions(connection: sqlite3.Connection, batch_id: str | None = None) -> list[dict[str, Any]]:
    batch_id = batch_id or fetch_latest_batch_id(connection)
    if not batch_id:
        return []
    rows = connection.execute(
        """
        SELECT
            instrument_name,
            symbol,
            isin,
            quantity,
            currency,
            open_price_local,
            current_price_local,
            cost_basis_dkk,
            market_value_dkk,
            unrealised_pnl_dkk,
            daily_pnl_dkk,
            allocation_pct,
            asset_class,
            market_status,
            value_date
        FROM position_snapshots
        WHERE batch_id = ? AND excluded = 0
        ORDER BY market_value_dkk DESC, symbol ASC
        """,
        (batch_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_portfolio_summary(connection: sqlite3.Connection, batch_id: str | None = None) -> dict[str, Any]:
    batch_id = batch_id or fetch_latest_batch_id(connection)
    if not batch_id:
        return {
            "batch_id": None,
            "position_count": 0,
            "total_market_value_dkk": 0.0,
            "total_cost_basis_dkk": 0.0,
            "total_unrealised_pnl_dkk": 0.0,
            "total_daily_pnl_dkk": 0.0,
        }
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS position_count,
            COALESCE(SUM(market_value_dkk), 0) AS total_market_value_dkk,
            COALESCE(SUM(cost_basis_dkk), 0) AS total_cost_basis_dkk,
            COALESCE(SUM(unrealised_pnl_dkk), 0) AS total_unrealised_pnl_dkk,
            COALESCE(SUM(daily_pnl_dkk), 0) AS total_daily_pnl_dkk
        FROM position_snapshots
        WHERE batch_id = ? AND excluded = 0
        """,
        (batch_id,),
    ).fetchone()
    summary = dict(row)
    summary["batch_id"] = batch_id
    return summary


def fetch_trade_ledger(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            created_at,
            symbol,
            side,
            quantity,
            price_local,
            currency,
            gross_amount_dkk,
            commission_dkk,
            tax_dkk,
            realised_gain_dkk,
            cost_basis_sold_dkk,
            net_amount_dkk,
            mode,
            status,
            notes
        FROM trade_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_portfolio_symbols(connection: sqlite3.Connection, batch_id: str | None = None) -> list[str]:
    batch_id = batch_id or fetch_latest_batch_id(connection)
    if not batch_id:
        return []
    rows = connection.execute(
        """
        SELECT symbol
        FROM position_snapshots
        WHERE batch_id = ? AND excluded = 0
        ORDER BY market_value_dkk DESC, symbol ASC
        """,
        (batch_id,),
    ).fetchall()
    return [row["symbol"] for row in rows]


def fetch_realised_tax_summary(connection: sqlite3.Connection, tax_year: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(realised_gain_dkk), 0) AS realised_gain_dkk,
            COALESCE(SUM(tax_dkk), 0) AS tax_dkk,
            COALESCE(SUM(commission_dkk), 0) AS commission_dkk,
            COUNT(*) AS trade_count
        FROM trade_ledger
        WHERE side = 'SELL' AND tax_year = ?
        """,
        (tax_year,),
    ).fetchone()
    return dict(row) if row else {"realised_gain_dkk": 0.0, "tax_dkk": 0.0, "commission_dkk": 0.0, "trade_count": 0}


def fetch_open_lot_summary(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            pl.symbol,
            pl.instrument_name,
            pl.currency,
            SUM(pl.quantity_original) AS quantity_original,
            COALESCE(SUM(lr.quantity_sold), 0) AS quantity_sold,
            SUM(pl.cost_basis_total_dkk) AS cost_basis_total_dkk
        FROM position_lots pl
        LEFT JOIN lot_realizations lr ON lr.lot_id = pl.lot_id
        GROUP BY pl.symbol, pl.instrument_name, pl.currency
        ORDER BY pl.symbol
        """
    ).fetchall()
    output = []
    for row in rows:
        record = dict(row)
        record["quantity_open"] = float(record["quantity_original"]) - float(record["quantity_sold"])
        output.append(record)
    return output
