from __future__ import annotations

import sqlite3
from typing import Any


def fetch_latest_batch_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT batch_id FROM import_batches ORDER BY imported_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return row["batch_id"] if row else None


def _base_snapshot_rows(connection: sqlite3.Connection, batch_id: str) -> list[dict[str, Any]]:
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
            cost_basis_local,
            cost_basis_dkk,
            market_value_local,
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


def _trade_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
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
            cost_basis_sold_dkk
        FROM trade_ledger
        WHERE status IN ('executed', 'approved', 'recorded')
        ORDER BY created_at, id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _effective_positions(connection: sqlite3.Connection, batch_id: str) -> list[dict[str, Any]]:
    base_rows = _base_snapshot_rows(connection, batch_id)
    states: dict[str, dict[str, Any]] = {}
    for row in base_rows:
        base_quantity = float(row["quantity"] or 0.0)
        current_price_local = float(row["current_price_local"] or row["open_price_local"] or 0.0)
        fx_rate = (
            float(row["market_value_dkk"] or 0.0) / max(float(row["market_value_local"] or (base_quantity * current_price_local or 0.0)), 1e-9)
            if current_price_local > 0
            else 1.0
        )
        states[row["symbol"]] = {
            **row,
            "quantity": base_quantity,
            "cost_basis_dkk": float(row["cost_basis_dkk"] or 0.0),
            "current_price_local": current_price_local,
            "latest_fx_rate": fx_rate,
            "base_quantity": base_quantity,
            "base_daily_pnl_dkk": float(row["daily_pnl_dkk"] or 0.0),
        }

    for trade in _trade_rows(connection):
        symbol = trade["symbol"]
        quantity = float(trade["quantity"] or 0.0)
        gross_amount_dkk = float(trade["gross_amount_dkk"] or 0.0)
        price_local = float(trade["price_local"] or 0.0)
        fx_rate = gross_amount_dkk / max(quantity * price_local, 1e-9) if quantity > 0 and price_local > 0 else 1.0
        state = states.setdefault(
            symbol,
            {
                "instrument_name": symbol,
                "symbol": symbol,
                "isin": None,
                "quantity": 0.0,
                "currency": trade["currency"],
                "open_price_local": price_local,
                "current_price_local": price_local,
                "cost_basis_local": None,
                "cost_basis_dkk": 0.0,
                "market_value_local": 0.0,
                "market_value_dkk": 0.0,
                "unrealised_pnl_dkk": 0.0,
                "daily_pnl_dkk": 0.0,
                "allocation_pct": 0.0,
                "asset_class": "Equity",
                "market_status": "Local overlay",
                "value_date": None,
                "latest_fx_rate": fx_rate,
                "base_quantity": 0.0,
                "base_daily_pnl_dkk": 0.0,
            },
        )
        state["current_price_local"] = price_local or state["current_price_local"]
        state["latest_fx_rate"] = fx_rate or state["latest_fx_rate"]
        state["currency"] = trade["currency"] or state["currency"]

        if trade["side"] == "BUY":
            state["quantity"] = float(state["quantity"]) + quantity
            state["cost_basis_dkk"] = float(state["cost_basis_dkk"]) + gross_amount_dkk + float(trade["commission_dkk"] or 0.0)
        else:
            available_quantity = float(state["quantity"] or 0.0)
            if quantity > available_quantity + 1e-9:
                continue
            state["quantity"] = max(available_quantity - quantity, 0.0)
            state["cost_basis_dkk"] = max(
                float(state["cost_basis_dkk"]) - float(trade["cost_basis_sold_dkk"] or 0.0),
                0.0,
            )

    positions: list[dict[str, Any]] = []
    for state in states.values():
        effective_quantity = float(state["quantity"] or 0.0)
        if effective_quantity <= 1e-9:
            continue
        current_price_local = float(state["current_price_local"] or state["open_price_local"] or 0.0)
        fx_rate = float(state["latest_fx_rate"] or 1.0)
        effective_market_value_dkk = effective_quantity * current_price_local * fx_rate
        base_quantity = float(state.get("base_quantity") or 0.0)
        quantity_scale = effective_quantity / base_quantity if base_quantity > 0 else 0.0
        positions.append(
            {
                **state,
                "quantity": effective_quantity,
                "market_value_local": effective_quantity * current_price_local,
                "market_value_dkk": effective_market_value_dkk,
                "unrealised_pnl_dkk": effective_market_value_dkk - float(state["cost_basis_dkk"] or 0.0),
                "daily_pnl_dkk": float(state.get("base_daily_pnl_dkk") or 0.0) * quantity_scale,
            }
        )

    total_market_value_dkk = sum(float(row["market_value_dkk"] or 0.0) for row in positions)
    for row in positions:
        row["allocation_pct"] = (float(row["market_value_dkk"] or 0.0) / total_market_value_dkk) if total_market_value_dkk > 0 else 0.0
    positions.sort(key=lambda row: (-(float(row["market_value_dkk"] or 0.0)), row["symbol"]))
    return positions


def fetch_portfolio_positions(connection: sqlite3.Connection, batch_id: str | None = None) -> list[dict[str, Any]]:
    batch_id = batch_id or fetch_latest_batch_id(connection)
    if not batch_id:
        return []
    return _effective_positions(connection, batch_id)


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
    positions = _effective_positions(connection, batch_id)
    return {
        "batch_id": batch_id,
        "position_count": len(positions),
        "total_market_value_dkk": sum(float(row["market_value_dkk"] or 0.0) for row in positions),
        "total_cost_basis_dkk": sum(float(row["cost_basis_dkk"] or 0.0) for row in positions),
        "total_unrealised_pnl_dkk": sum(float(row["unrealised_pnl_dkk"] or 0.0) for row in positions),
        "total_daily_pnl_dkk": sum(float(row["daily_pnl_dkk"] or 0.0) for row in positions),
    }


def _annotated_trade_ledger_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    batch_id = fetch_latest_batch_id(connection)
    available_by_symbol = {
        row["symbol"]: float(row["quantity"] or 0.0)
        for row in (_base_snapshot_rows(connection, batch_id) if batch_id else [])
    }
    rows = connection.execute(
        """
        SELECT
            id,
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
        ORDER BY id ASC
        """,
        (),
    ).fetchall()
    annotated: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        symbol = record["symbol"]
        quantity = float(record["quantity"] or 0.0)
        validation_note = ""
        if record["side"] == "BUY":
            available_by_symbol[symbol] = available_by_symbol.get(symbol, 0.0) + quantity
        else:
            available = available_by_symbol.get(symbol, 0.0)
            if quantity > available + 1e-9:
                validation_note = f"Ignored by effective portfolio overlay: sell exceeds available quantity ({available:.4f})."
            else:
                available_by_symbol[symbol] = max(available - quantity, 0.0)
        record["validation_note"] = validation_note
        annotated.append(record)
    annotated.reverse()
    return annotated


def fetch_trade_ledger(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    annotated = _annotated_trade_ledger_rows(connection)
    return annotated[:limit]


def fetch_invalid_trade_ledger_rows(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    invalid_rows = [
        row
        for row in _annotated_trade_ledger_rows(connection)
        if row.get("validation_note") and row.get("status") in {"executed", "approved", "recorded"}
    ]
    return invalid_rows[:limit]


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
