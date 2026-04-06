from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import append_audit_log, connect, init_db
from saxo_daytrader_xai.fx_service import fetch_ecb_fx_rates, fx_rate_to_dkk
from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.saxo_openapi import (
    SaxoSessionError,
    build_market_order_payload,
    ensure_access_token,
    place_order,
    precheck_order,
)
from saxo_daytrader_xai.market_symbols import saxo_to_yahoo
from saxo_daytrader_xai.portfolio import (
    fetch_latest_batch_id,
    fetch_open_lot_summary,
    fetch_portfolio_positions,
    fetch_portfolio_summary,
)
from saxo_daytrader_xai.tax_engine import calculate_sell_outcome, update_ledger
from saxo_daytrader_xai.xai_decision import fetch_latest_decision_report


def _load_default_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return load_config(root / "config.yaml")


def _get_connection_and_config(config: dict[str, Any] | None, connection):
    resolved_config = config or _load_default_config()
    resolved_connection = connection or connect(resolved_config["portfolio"]["database_path"])
    init_db(resolved_connection)
    return resolved_config, resolved_connection, connection is None


def _current_position_map(connection, batch_id: str | None = None) -> dict[str, dict[str, Any]]:
    snapshot_positions = fetch_portfolio_positions(connection, batch_id=batch_id)
    open_lots = fetch_open_lot_summary(connection)
    open_by_symbol = {row["symbol"]: row for row in open_lots}
    positions = {}
    for row in snapshot_positions:
        open_lot = open_by_symbol.get(row["symbol"], {})
        positions[row["symbol"]] = {
            **row,
            "quantity_open": open_lot.get("quantity_open", row["quantity"]),
        }
    return positions


def _get_live_price_map(symbols: list[str], config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    quotes = fetch_live_prices(symbols, timeout_seconds=config["market_data"]["request_timeout_seconds"])
    return {row["symbol"]: row for row in quotes}


def _estimate_price_and_fx(
    symbol: str,
    position_map: dict[str, dict[str, Any]],
    live_price_map: dict[str, dict[str, Any]],
    fx_snapshot: dict[str, Any],
) -> tuple[float, str, float]:
    if symbol in position_map:
        position = position_map[symbol]
        price_local = live_price_map.get(symbol, {}).get("current_price") or position["current_price_local"]
        currency = position["currency"]
        market_value_local = position.get("market_value_local")
        market_value_dkk = position.get("market_value_dkk")
        if currency == "DKK":
            fx_rate = 1.0
        elif market_value_local not in (None, 0) and market_value_dkk not in (None, 0):
            fx_rate = float(market_value_dkk) / float(market_value_local)
        else:
            fx_rate = fx_rate_to_dkk(currency, fx_snapshot)
        return float(price_local), currency, fx_rate

    quote = live_price_map.get(symbol)
    if not quote or quote.get("current_price") is None:
        raise ValueError(f"No live price available for {symbol}")
    yahoo_symbol = saxo_to_yahoo(symbol)
    suffix = yahoo_symbol.split(".")[-1] if "." in yahoo_symbol else ""
    currency = {
        "": "USD",
        "CO": "DKK",
        "ST": "SEK",
        "OL": "NOK",
        "HE": "EUR",
        "DE": "EUR",
        "PA": "EUR",
        "AS": "EUR",
        "BR": "EUR",
        "L": "GBP",
        "MI": "EUR",
    }.get(suffix, "USD")
    fx_rate = fx_rate_to_dkk(currency, fx_snapshot)
    return float(quote["current_price"]), currency, fx_rate


def _calculate_buy_commission(symbol: str, gross_local: float, gross_dkk: float, currency: str, fx_rate: float, config: dict[str, Any]) -> dict[str, float]:
    commissions_cfg = config["commissions"]
    trade_commission_local = gross_local * float(commissions_cfg["default_rate"])
    market_code = symbol.split(":", 1)[1].upper() if ":" in symbol else ""
    minimum_cfg = commissions_cfg.get("minimums", {}).get(market_code)
    if minimum_cfg:
        minimum_amount = float(minimum_cfg["amount"])
        minimum_currency = minimum_cfg["currency"]
        if minimum_currency == currency:
            trade_commission_local = max(trade_commission_local, minimum_amount)
        elif minimum_currency == "DKK":
            trade_commission_local = max(trade_commission_local, minimum_amount / max(fx_rate, 1e-9))
    fx_conversion_dkk = 0.0 if currency == "DKK" else gross_dkk * float(commissions_cfg["fx_conversion_rate"])
    trade_commission_dkk = trade_commission_local * fx_rate
    return {
        "commission_local": trade_commission_local,
        "commission_dkk": trade_commission_dkk + fx_conversion_dkk,
        "fx_conversion_dkk": fx_conversion_dkk,
    }


def _remaining_daily_order_capacity(connection, config: dict[str, Any]) -> int:
    limit = int(config["execution"]["max_daily_orders"])
    today = datetime.now(UTC).date().isoformat()
    used = connection.execute(
        """
        SELECT COUNT(*) AS count_orders
        FROM execution_orders
        WHERE substr(created_at, 1, 10) = ?
          AND status NOT IN ('error', 'cancelled')
        """,
        (today,),
    ).fetchone()["count_orders"]
    return max(limit - int(used), 0)


def _create_or_fetch_orders(connection, config: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    existing = connection.execute(
        "SELECT * FROM execution_orders WHERE report_id = ? ORDER BY id",
        (report["id"],),
    ).fetchall()
    if existing:
        return [dict(row) for row in existing]

    report_json = report["report_json"] or {}
    suggestions = report_json.get("suggested_trades", [])
    batch_id = fetch_latest_batch_id(connection)
    portfolio_summary = fetch_portfolio_summary(connection, batch_id=batch_id)
    position_map = _current_position_map(connection, batch_id=batch_id)
    live_symbols = list({item.get("symbol") for item in suggestions if item.get("symbol")})
    live_symbols.extend(position_map.keys())
    live_price_map = _get_live_price_map([symbol for symbol in live_symbols if symbol], config)
    fx_snapshot = fetch_ecb_fx_rates()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    orders = []
    max_position_weight = float(config["risk"]["max_position_weight"])
    min_trade_value_dkk = float(config["execution"]["min_trade_value_dkk"])
    remaining_capacity = _remaining_daily_order_capacity(connection, config)

    for suggestion in suggestions:
        if remaining_capacity <= 0:
            break
        action = suggestion["action"]
        symbol = suggestion["symbol"]
        if action not in {"BUY", "SELL"}:
            continue
        requested_weight_pct = min(float(suggestion["target_weight_pct"]), max_position_weight)
        try:
            price_local, currency, fx_rate = _estimate_price_and_fx(symbol, position_map, live_price_map, fx_snapshot)
        except Exception as exc:  # noqa: BLE001
            orders.append(
                {
                    "symbol": symbol,
                    "action": action,
                    "mode": config["execution"]["mode"],
                    "status": "error",
                    "adapter": config["execution"]["adapter"],
                    "requested_weight_pct": requested_weight_pct,
                    "quantity": 0.0,
                    "price_local": None,
                    "currency": None,
                    "estimated_value_dkk": 0.0,
                    "approval_required": 1 if config["execution"]["mode"] == "live" else 0,
                    "request_json": json.dumps(suggestion, ensure_ascii=False, sort_keys=True),
                    "execution_result_json": None,
                    "error_text": str(exc),
                }
            )
            continue

        current_value_dkk = 0.0
        current_quantity = 0.0
        if symbol in position_map:
            current_quantity = float(position_map[symbol]["quantity_open"])
            current_value_dkk = current_quantity * price_local * fx_rate
        target_value_dkk = float(portfolio_summary["total_market_value_dkk"]) * requested_weight_pct
        delta_value_dkk = target_value_dkk - current_value_dkk
        if action == "BUY" and delta_value_dkk <= 0:
            continue
        if action == "SELL" and delta_value_dkk >= 0:
            continue
        quantity = abs(delta_value_dkk) / max(price_local * fx_rate, 1e-9)
        if action == "SELL":
            quantity = min(quantity, current_quantity)
        if quantity <= 1e-9 or abs(delta_value_dkk) < min_trade_value_dkk:
            continue

        orders.append(
            {
                "symbol": symbol,
                "action": action,
                "mode": config["execution"]["mode"],
                "status": "pending_approval" if config["execution"]["mode"] == "live" else "pending_execution",
                "adapter": config["execution"]["adapter"],
                "requested_weight_pct": requested_weight_pct,
                "quantity": quantity,
                "price_local": price_local,
                "currency": currency,
                "estimated_value_dkk": abs(delta_value_dkk),
                "approval_required": 1 if config["execution"]["mode"] == "live" else 0,
                "request_json": json.dumps(suggestion, ensure_ascii=False, sort_keys=True),
                "execution_result_json": None,
                "error_text": None,
            }
        )
        remaining_capacity -= 1

    connection.executemany(
        """
        INSERT INTO execution_orders (
            created_at, report_id, symbol, action, mode, status, adapter,
            requested_weight_pct, quantity, price_local, currency, estimated_value_dkk,
            approval_required, request_json, execution_result_json, error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                created_at,
                report["id"],
                order["symbol"],
                order["action"],
                order["mode"],
                order["status"],
                order["adapter"],
                order["requested_weight_pct"],
                order["quantity"],
                order["price_local"],
                order["currency"],
                order["estimated_value_dkk"],
                order["approval_required"],
                order["request_json"],
                order["execution_result_json"],
                order["error_text"],
            )
            for order in orders
        ],
    )
    connection.commit()
    created = connection.execute(
        "SELECT * FROM execution_orders WHERE report_id = ? ORDER BY id",
        (report["id"],),
    ).fetchall()
    return [dict(row) for row in created]


def _record_buy_trade(connection, config: dict[str, Any], order: dict[str, Any], batch_id: str) -> dict[str, Any]:
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    price_local = float(order["price_local"])
    quantity = float(order["quantity"])
    currency = order["currency"]
    fx_snapshot = fetch_ecb_fx_rates()
    fx_rate = fx_rate_to_dkk(currency, fx_snapshot)
    gross_local = price_local * quantity
    gross_dkk = gross_local * fx_rate
    commission = _calculate_buy_commission(order["symbol"], gross_local, gross_dkk, currency, fx_rate, config)
    cursor = connection.execute(
        """
        INSERT INTO trade_ledger (
            created_at, symbol, isin, side, quantity, price_local, currency,
            gross_amount_dkk, commission_dkk, commission_local, fx_conversion_dkk, tax_dkk,
            realised_gain_dkk, cost_basis_sold_dkk, net_amount_dkk, mode, status, notes,
            portfolio_before_json, portfolio_after_json, decision_context_json, tax_year, batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            order["symbol"],
            None,
            "BUY",
            quantity,
            price_local,
            currency,
            gross_dkk,
            commission["commission_dkk"],
            commission["commission_local"],
            commission["fx_conversion_dkk"],
            0.0,
            0.0,
            0.0,
            -(gross_dkk + commission["commission_dkk"]),
            order["mode"],
            "executed" if order["mode"] == "simulation" else "approved",
            "Phase 5 buy execution",
            json.dumps({}, ensure_ascii=False, sort_keys=True),
            json.dumps({}, ensure_ascii=False, sort_keys=True),
            order["request_json"],
            datetime.now(UTC).year,
            batch_id,
        ),
    )
    ledger_id = int(cursor.lastrowid)
    lot_id = f"buy:{ledger_id}"
    connection.execute(
        """
        INSERT INTO position_lots (
            lot_id, batch_id, created_at, acquired_at, symbol, isin, instrument_name,
            quantity_original, currency, cost_basis_total_local, cost_basis_total_dkk,
            fx_rate_to_dkk, source_type, source_reference, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lot_id,
            batch_id,
            created_at,
            created_at,
            order["symbol"],
            None,
            order["symbol"],
            quantity,
            currency,
            gross_local + commission["commission_local"],
            gross_dkk + commission["commission_dkk"],
            fx_rate,
            f"{order['mode']}_buy",
            f"execution_order:{order['id']}",
            order["request_json"],
        ),
    )
    connection.commit()
    return {"ledger_id": ledger_id, "lot_id": lot_id}


def execute_order(order_id: int, *, config: dict[str, Any] | None = None, connection=None, approved: bool = False) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        order_row = resolved_connection.execute("SELECT * FROM execution_orders WHERE id = ?", (order_id,)).fetchone()
        if not order_row:
            raise ValueError(f"Unknown execution order {order_id}")
        order = dict(order_row)
        if order["status"] not in {"pending_execution", "pending_approval"}:
            return {"status": order["status"], "order_id": order_id}
        if order["mode"] == "live" and order["approval_required"] and not approved:
            return {"status": "approval_required", "order_id": order_id}
        if order["mode"] == "live" and resolved_config["app"]["dry_run"]:
            resolved_connection.execute(
                """
                UPDATE execution_orders
                SET status = ?, approved_at = ?, error_text = ?, execution_result_json = ?
                WHERE id = ?
                """,
                (
                    "blocked_by_dry_run",
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    "Live execution blocked because app.dry_run is true",
                    json.dumps({"dry_run": True}, ensure_ascii=False, sort_keys=True),
                    order_id,
                ),
            )
            resolved_connection.commit()
            return {"status": "blocked_by_dry_run", "order_id": order_id}
        if order["mode"] == "live":
            if order["adapter"] != "saxo":
                error_text = f"Unsupported live adapter '{order['adapter']}'"
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, approved_at = ?, error_text = ?, execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        "execution_failed",
                        datetime.now(UTC).isoformat(timespec="seconds") if approved else None,
                        error_text,
                        json.dumps({"adapter": order["adapter"]}, ensure_ascii=False, sort_keys=True),
                        order_id,
                    ),
                )
                resolved_connection.commit()
                return {"status": "execution_failed", "order_id": order_id, "error": error_text}
            try:
                session = ensure_access_token(resolved_config, resolved_config["saxo"].get("session_path"))
                payload = build_market_order_payload(
                    symbol=order["symbol"],
                    action=order["action"],
                    quantity=float(order["quantity"]),
                    external_reference=f"saxo-daytrader:{order_id}",
                    config=resolved_config,
                    session=session,
                )
                precheck = precheck_order(payload, resolved_config, session)
                broker_result = place_order(payload, resolved_config, session)
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, approved_at = ?, execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        "submitted_to_broker",
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        json.dumps(
                            {"precheck": precheck, "payload": payload, "broker_result": broker_result},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        order_id,
                    ),
                )
                resolved_connection.commit()
                append_audit_log(
                    resolved_connection,
                    "execution_order_submitted",
                    {"order_id": order_id, "mode": order["mode"], "adapter": order["adapter"], "payload": payload},
                )
                return {
                    "status": "submitted_to_broker",
                    "order_id": order_id,
                    "broker_result": broker_result,
                    "precheck": precheck,
                }
            except (SaxoSessionError, requests.RequestException, ValueError) as exc:  # type: ignore[name-defined]
                error_text = str(exc)
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, approved_at = ?, error_text = ?, execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        "execution_failed",
                        datetime.now(UTC).isoformat(timespec="seconds") if approved else None,
                        error_text,
                        json.dumps({"adapter": order["adapter"], "error": error_text}, ensure_ascii=False, sort_keys=True),
                        order_id,
                    ),
                )
                resolved_connection.commit()
                append_audit_log(
                    resolved_connection,
                    "execution_order_failed",
                    {"order_id": order_id, "mode": order["mode"], "adapter": order["adapter"], "error": error_text},
                )
                return {"status": "execution_failed", "order_id": order_id, "error": error_text}

        batch_id = fetch_latest_batch_id(resolved_connection)
        if order["action"] == "SELL":
            trade = calculate_sell_outcome(
                order["symbol"],
                float(order["quantity"]),
                float(order["price_local"]),
                config=resolved_config,
                connection=resolved_connection,
                batch_id=batch_id,
                tax_year=datetime.now(UTC).year,
            )
            trade["mode"] = order["mode"]
            trade["status"] = "executed"
            trade["notes"] = "Phase 5 automated execution"
            result = update_ledger(trade, config=resolved_config, connection=resolved_connection)
            ledger_id = result["ledger_id"]
        else:
            result = _record_buy_trade(resolved_connection, resolved_config, order, batch_id)
            ledger_id = result["ledger_id"]

        resolved_connection.execute(
            """
            UPDATE execution_orders
            SET status = ?, approved_at = ?, ledger_id = ?, execution_result_json = ?
            WHERE id = ?
            """,
            (
                "executed",
                datetime.now(UTC).isoformat(timespec="seconds"),
                ledger_id,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                order_id,
            ),
        )
        resolved_connection.commit()
        append_audit_log(
            resolved_connection,
            "execution_order_executed",
            {"order_id": order_id, "ledger_id": ledger_id, "mode": order["mode"], "action": order["action"]},
        )
        return {"status": "executed", "order_id": order_id, "ledger_id": ledger_id}
    finally:
        if should_close:
            resolved_connection.close()


def queue_and_maybe_execute_latest_report(*, config: dict[str, Any] | None = None, connection=None) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        report = fetch_latest_decision_report(resolved_connection)
        if not report or report["status"] != "completed":
            return {"status": "no_completed_report"}
        orders = _create_or_fetch_orders(resolved_connection, resolved_config, report)
        executed = []
        if resolved_config["execution"]["mode"] == "simulation" and resolved_config["execution"]["auto_execute_simulation"]:
            for order in orders:
                if order["status"] == "pending_execution":
                    executed.append(execute_order(order["id"], config=resolved_config, connection=resolved_connection))
        return {"status": "ok", "orders": orders, "executed": executed}
    finally:
        if should_close:
            resolved_connection.close()


def fetch_execution_orders(connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM execution_orders
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def export_audit_bundle(output_dir: str, *, config: dict[str, Any] | None = None, connection=None) -> dict[str, Any]:
    import csv

    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        exports = {
            "trade_ledger": out / "trade_ledger.csv",
            "lot_realizations": out / "lot_realizations.csv",
            "decision_reports": out / "decision_reports.csv",
            "execution_orders": out / "execution_orders.csv",
            "audit_log": out / "audit_log.csv",
        }
        for table_name, path in exports.items():
            rows = resolved_connection.execute(f"SELECT * FROM {table_name}").fetchall()
            if rows:
                fieldnames = list(rows[0].keys())
            else:
                fieldnames = [
                    row["name"]
                    for row in resolved_connection.execute(f"PRAGMA table_info({table_name})").fetchall()
                ]
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                if fieldnames:
                    writer.writeheader()
                    writer.writerows([dict(row) for row in rows])
        return {"status": "ok", "output_dir": str(out), "files": {k: str(v) for k, v in exports.items()}}
    finally:
        if should_close:
            resolved_connection.close()
