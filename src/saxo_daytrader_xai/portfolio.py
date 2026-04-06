from __future__ import annotations

import json
import sqlite3
from typing import Any


ACTIVE_LEDGER_STATUSES = {"executed", "approved", "recorded"}


def _normalize_asset_class(value: str | None) -> str:
    text = (value or "").strip()
    normalized = text.casefold()
    mapping = {
        "aktie": "Equity",
        "aktier": "Equity",
        "equity": "Equity",
        "stock": "Equity",
        "stocks": "Equity",
    }
    return mapping.get(normalized, text)


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
            instrument_name,
            isin,
            figi,
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


def _cash_effect_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id,
            created_at,
            symbol,
            side,
            net_amount_dkk,
            status
        FROM trade_ledger
        ORDER BY created_at, id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _latest_price_state_by_symbol(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM portfolio_price_snapshots
        """
    ).fetchall()
    return {row["symbol"]: dict(row) for row in rows}


def fetch_cash_summary(connection: sqlite3.Connection, *, initial_cash_dkk: float = 0.0) -> dict[str, Any]:
    cash_from_trades = 0.0
    invalid_trade_ids: list[int] = []
    invalid_rows = {
        int(row["id"]): row
        for row in fetch_invalid_trade_ledger_rows(connection, limit=10_000)
    }
    for row in _cash_effect_rows(connection):
        if row["status"] not in ACTIVE_LEDGER_STATUSES:
            continue
        if int(row["id"]) in invalid_rows:
            invalid_trade_ids.append(int(row["id"]))
            continue
        cash_from_trades += float(row["net_amount_dkk"] or 0.0)
    return {
        "initial_cash_dkk": float(initial_cash_dkk or 0.0),
        "cash_from_trades_dkk": cash_from_trades,
        "cash_balance_dkk": float(initial_cash_dkk or 0.0) + cash_from_trades,
        "ignored_invalid_trade_ids": invalid_trade_ids,
    }


def _effective_positions(connection: sqlite3.Connection, batch_id: str, *, initial_cash_dkk: float = 0.0) -> list[dict[str, Any]]:
    base_rows = _base_snapshot_rows(connection, batch_id)
    latest_price_state = _latest_price_state_by_symbol(connection)
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
            "asset_class": _normalize_asset_class(row.get("asset_class")),
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
                "instrument_name": trade.get("instrument_name") or symbol,
                "symbol": symbol,
                "isin": trade.get("isin"),
                "figi": trade.get("figi"),
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
        state["asset_class"] = _normalize_asset_class(state.get("asset_class") or "Equity")
        if trade.get("instrument_name"):
            state["instrument_name"] = trade["instrument_name"]
        if trade.get("isin"):
            state["isin"] = trade["isin"]
        if trade.get("figi"):
            state["figi"] = trade["figi"]

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
        price_state = latest_price_state.get(state["symbol"], {})
        current_price_local = float(
            price_state.get("current_price_local")
            or state["current_price_local"]
            or state["open_price_local"]
            or 0.0
        )
        fx_rate = float(price_state.get("current_fx_rate_to_dkk") or state["latest_fx_rate"] or 1.0)
        baseline_price_local = price_state.get("baseline_price_local")
        baseline_fx_rate = price_state.get("baseline_fx_rate_to_dkk")
        if baseline_price_local not in (None, "") and baseline_fx_rate not in (None, ""):
            daily_pnl_dkk = effective_quantity * (
                current_price_local * fx_rate
                - float(baseline_price_local) * float(baseline_fx_rate)
            )
        else:
            base_quantity = float(state.get("base_quantity") or 0.0)
            quantity_scale = effective_quantity / base_quantity if base_quantity > 0 else 0.0
            daily_pnl_dkk = float(state.get("base_daily_pnl_dkk") or 0.0) * quantity_scale
        effective_market_value_dkk = effective_quantity * current_price_local * fx_rate
        positions.append(
            {
                **state,
                "quantity": effective_quantity,
                "market_value_local": effective_quantity * current_price_local,
                "market_value_dkk": effective_market_value_dkk,
                "unrealised_pnl_dkk": effective_market_value_dkk - float(state["cost_basis_dkk"] or 0.0),
                "daily_pnl_dkk": daily_pnl_dkk,
                "day_baseline_price_local": baseline_price_local,
                "day_baseline_fx_rate_to_dkk": baseline_fx_rate,
                "current_fx_rate_to_dkk": fx_rate,
                "latest_quote_updated_at": price_state.get("updated_at"),
                "baseline_session_date": price_state.get("baseline_session_date"),
                "quote_status": price_state.get("status"),
            }
        )

    invested_market_value_dkk = sum(float(row["market_value_dkk"] or 0.0) for row in positions)
    total_portfolio_value_dkk = invested_market_value_dkk + float(fetch_cash_summary(connection, initial_cash_dkk=initial_cash_dkk)["cash_balance_dkk"])
    for row in positions:
        row["allocation_pct"] = (
            float(row["market_value_dkk"] or 0.0) / total_portfolio_value_dkk
            if total_portfolio_value_dkk > 0
            else 0.0
        )
    positions.sort(key=lambda row: (-(float(row["market_value_dkk"] or 0.0)), row["symbol"]))
    return positions


def fetch_portfolio_positions(
    connection: sqlite3.Connection,
    batch_id: str | None = None,
    *,
    initial_cash_dkk: float = 0.0,
) -> list[dict[str, Any]]:
    batch_id = batch_id or fetch_latest_batch_id(connection)
    if not batch_id:
        return []
    return _effective_positions(connection, batch_id, initial_cash_dkk=initial_cash_dkk)


def fetch_portfolio_summary(
    connection: sqlite3.Connection,
    batch_id: str | None = None,
    *,
    initial_cash_dkk: float = 0.0,
) -> dict[str, Any]:
    batch_id = batch_id or fetch_latest_batch_id(connection)
    if not batch_id:
        return {
            "batch_id": None,
            "position_count": 0,
            "total_market_value_dkk": 0.0,
            "invested_market_value_dkk": 0.0,
            "cash_balance_dkk": float(initial_cash_dkk or 0.0),
            "initial_cash_dkk": float(initial_cash_dkk or 0.0),
            "cash_from_trades_dkk": 0.0,
            "total_cost_basis_dkk": 0.0,
            "total_unrealised_pnl_dkk": 0.0,
            "total_daily_pnl_dkk": 0.0,
        }
    positions = _effective_positions(connection, batch_id, initial_cash_dkk=initial_cash_dkk)
    invested_market_value_dkk = sum(float(row["market_value_dkk"] or 0.0) for row in positions)
    cash_summary = fetch_cash_summary(connection, initial_cash_dkk=initial_cash_dkk)
    return {
        "batch_id": batch_id,
        "position_count": len(positions),
        "total_market_value_dkk": invested_market_value_dkk + float(cash_summary["cash_balance_dkk"]),
        "invested_market_value_dkk": invested_market_value_dkk,
        "cash_balance_dkk": float(cash_summary["cash_balance_dkk"]),
        "initial_cash_dkk": float(cash_summary["initial_cash_dkk"]),
        "cash_from_trades_dkk": float(cash_summary["cash_from_trades_dkk"]),
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
            realised_gain_local,
            realised_gain_dkk,
            price_gain_dkk,
            fx_gain_dkk,
            cost_basis_sold_dkk,
            cost_basis_sold_local,
            sale_fx_rate_to_dkk,
            cost_basis_fx_rate_to_dkk,
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


def record_portfolio_value_snapshot(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
    snapshot_type: str,
    initial_cash_dkk: float = 0.0,
    batch_id: str | None = None,
    baseline_session_date: str | None = None,
    source: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> int:
    summary = fetch_portfolio_summary(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk)
    payload = {
        "summary": summary,
        "snapshot_type": snapshot_type,
        "baseline_session_date": baseline_session_date,
        "source": source,
    }
    if extra_payload:
        payload["extra"] = extra_payload
    cursor = connection.execute(
        """
        INSERT INTO portfolio_value_history (
            recorded_at,
            snapshot_type,
            baseline_session_date,
            batch_id,
            total_market_value_dkk,
            invested_market_value_dkk,
            cash_balance_dkk,
            total_cost_basis_dkk,
            total_unrealised_pnl_dkk,
            total_daily_pnl_dkk,
            position_count,
            source,
            raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recorded_at,
            snapshot_type,
            baseline_session_date,
            summary.get("batch_id"),
            float(summary["total_market_value_dkk"]),
            float(summary["invested_market_value_dkk"]),
            float(summary["cash_balance_dkk"]),
            float(summary["total_cost_basis_dkk"]),
            float(summary["total_unrealised_pnl_dkk"]),
            float(summary["total_daily_pnl_dkk"]),
            int(summary["position_count"]),
            source,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def fetch_portfolio_value_history(
    connection: sqlite3.Connection,
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int = 20_000,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if start_at:
        conditions.append("recorded_at >= ?")
        params.append(start_at)
    if end_at:
        conditions.append("recorded_at <= ?")
        params.append(end_at)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = connection.execute(
        f"""
        SELECT *
        FROM (
            SELECT *
            FROM portfolio_value_history
            {where_clause}
            ORDER BY recorded_at DESC, id DESC
            LIMIT ?
        )
        ORDER BY recorded_at ASC, id ASC
        """,
        (*params, int(limit)),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["raw_payload_json"] = json.loads(record["raw_payload_json"]) if record.get("raw_payload_json") else None
        output.append(record)
    return output


def prune_portfolio_value_history(
    connection: sqlite3.Connection,
    *,
    keep_max_rows: int | None = None,
    keep_since_recorded_at: str | None = None,
) -> int:
    deleted_rows = 0
    if keep_since_recorded_at:
        cursor = connection.execute(
            """
            DELETE FROM portfolio_value_history
            WHERE recorded_at < ?
            """,
            (keep_since_recorded_at,),
        )
        deleted_rows += int(cursor.rowcount or 0)
    if keep_max_rows is not None and keep_max_rows > 0:
        cursor = connection.execute(
            """
            DELETE FROM portfolio_value_history
            WHERE id NOT IN (
                SELECT id
                FROM portfolio_value_history
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
            )
            """,
            (keep_max_rows,),
        )
        deleted_rows += int(cursor.rowcount or 0)
    connection.commit()
    return deleted_rows


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
