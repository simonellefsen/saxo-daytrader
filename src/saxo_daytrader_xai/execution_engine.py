from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import append_audit_log, connect, init_db
from saxo_daytrader_xai.fx_service import fetch_ecb_fx_rates, fx_rate_to_dkk
from saxo_daytrader_xai.identifier_lookup import resolve_instrument_identity
from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.market_schedule import get_market_status
from saxo_daytrader_xai.saxo_openapi import (
    SaxoOrderNotFoundError,
    SaxoSessionError,
    build_market_order_payload,
    cancel_order,
    change_order,
    ensure_access_token,
    get_balance_snapshot,
    get_open_order,
    get_order_activity_last,
    place_order,
    precheck_order,
)
from saxo_daytrader_xai.market_symbols import saxo_to_yahoo
from saxo_daytrader_xai.portfolio import (
    fetch_cash_summary,
    fetch_latest_batch_id,
    fetch_invalid_trade_ledger_rows,
    fetch_portfolio_positions,
    fetch_portfolio_summary,
)
from saxo_daytrader_xai.tax_engine import calculate_sell_outcome, update_ledger
from saxo_daytrader_xai.xai_decision import fetch_latest_decision_report


def _load_default_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return load_config(root / "config.yaml")


MANAGEABLE_LIVE_STATUSES = {
    "submitted_to_broker",
    "broker_working",
    "broker_amended",
    "broker_partially_filled",
    "broker_replace_requested",
    "broker_cancel_requested",
}


def _get_connection_and_config(config: dict[str, Any] | None, connection):
    resolved_config = config or _load_default_config()
    resolved_connection = connection or connect(resolved_config["portfolio"]["database_path"])
    init_db(resolved_connection)
    return resolved_config, resolved_connection, connection is None


def _current_position_map(connection, batch_id: str | None = None) -> dict[str, dict[str, Any]]:
    snapshot_positions = fetch_portfolio_positions(connection, batch_id=batch_id)
    positions = {}
    for row in snapshot_positions:
        positions[row["symbol"]] = {
            **row,
            "quantity_open": row["quantity"],
        }
    return positions


def _get_live_price_map(symbols: list[str], config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    quotes = fetch_live_prices(symbols, timeout_seconds=config["market_data"]["request_timeout_seconds"])
    return {row["symbol"]: row for row in quotes}


def _symbol_exchange_code(symbol: str) -> str | None:
    if ":" not in symbol:
        return None
    return symbol.split(":", 1)[1].upper()


def _market_status_for_symbol(symbol: str, config: dict[str, Any]) -> dict[str, Any] | None:
    exchange_code = _symbol_exchange_code(symbol)
    if not exchange_code:
        return None
    rows = get_market_status(config)
    return next((row for row in rows if str(row.get("code")) == exchange_code), None)


def _should_auto_submit_live_orders(config: dict[str, Any]) -> bool:
    return (
        str(config["execution"]["mode"]) == "live"
        and not bool(config["execution"].get("require_approval_live", True))
        and not bool(config["app"].get("dry_run", True))
    )


def _approval_required_for_order(config: dict[str, Any]) -> bool:
    return str(config["execution"]["mode"]) == "live" and bool(config["execution"].get("require_approval_live", True))


def _cash_gate_enabled(config: dict[str, Any]) -> bool:
    return bool(config["execution"].get("require_settled_cash_for_live_buys", True))


def _to_dkk_amount(amount: float | None, currency: str | None, fx_snapshot: dict[str, Any]) -> float:
    if amount is None:
        return 0.0
    return float(amount) * fx_rate_to_dkk(currency or "DKK", fx_snapshot)


def _evaluate_live_buy_cash_gate(order: dict[str, Any], config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    balance = get_balance_snapshot(config, session)
    fx_snapshot = fetch_ecb_fx_rates()
    balance_currency = str(balance.get("Currency") or "DKK")
    required_dkk = float(order.get("estimated_value_dkk") or 0.0)
    cash_available_dkk = _to_dkk_amount(balance.get("CashAvailableForTrading"), balance_currency, fx_snapshot)
    funds_for_settlement_dkk = _to_dkk_amount(balance.get("FundsAvailableForSettlement"), balance_currency, fx_snapshot)
    transactions_not_booked_dkk = _to_dkk_amount(balance.get("TransactionsNotBooked"), balance_currency, fx_snapshot)

    has_cash = cash_available_dkk >= required_dkk
    settlement_ready = funds_for_settlement_dkk >= required_dkk
    pending_unbooked = abs(transactions_not_booked_dkk) > 1e-9

    allowed = has_cash and (settlement_ready or not pending_unbooked)
    return {
        "allowed": allowed,
        "required_dkk": required_dkk,
        "cash_available_dkk": cash_available_dkk,
        "funds_for_settlement_dkk": funds_for_settlement_dkk,
        "transactions_not_booked_dkk": transactions_not_booked_dkk,
        "balance_currency": balance_currency,
        "raw_balance": balance,
    }


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


def _whole_share_quantity(quantity: float) -> int:
    return max(int(math.floor(float(quantity))), 0)


def _initial_cash_dkk(config: dict[str, Any]) -> float:
    return float(config.get("portfolio", {}).get("initial_cash_dkk", 0.0) or 0.0)


def _max_affordable_buy_quantity(
    *,
    symbol: str,
    price_local: float,
    currency: str,
    fx_rate: float,
    available_cash_dkk: float,
    config: dict[str, Any],
) -> int:
    if available_cash_dkk <= 0 or price_local <= 0 or fx_rate <= 0:
        return 0
    gross_per_share_dkk = price_local * fx_rate
    quantity = _whole_share_quantity(available_cash_dkk / gross_per_share_dkk)
    while quantity > 0:
        gross_local = price_local * quantity
        gross_dkk = gross_local * fx_rate
        commission = _calculate_buy_commission(symbol, gross_local, gross_dkk, currency, fx_rate, config)
        total_spend_dkk = gross_dkk + commission["commission_dkk"]
        if total_spend_dkk <= available_cash_dkk + 1e-9:
            return quantity
        quantity -= 1
    return 0


def _dispatch_execution_alerts(connection, config: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from saxo_daytrader_xai.notifications import dispatch_broker_alerts_if_due

        return dispatch_broker_alerts_if_due(connection, config, force=False)
    except Exception:  # noqa: BLE001
        return None


def _dispatch_execution_failure_alerts(connection, config: dict[str, Any]) -> None:
    _dispatch_execution_alerts(connection, config)


def _mark_execution_failed(
    connection,
    *,
    order_id: int,
    approved: bool,
    adapter: str,
    error_text: str,
) -> dict[str, Any]:
    connection.execute(
        """
        UPDATE execution_orders
        SET status = ?, approved_at = ?, error_text = ?, execution_result_json = ?
        WHERE id = ?
        """,
        (
            "execution_failed",
            datetime.now(UTC).isoformat(timespec="seconds") if approved else None,
            error_text,
            json.dumps({"adapter": adapter, "error": error_text}, ensure_ascii=False, sort_keys=True),
            order_id,
        ),
    )
    connection.commit()
    return {"status": "execution_failed", "order_id": order_id, "error": error_text}


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
    initial_cash_dkk = _initial_cash_dkk(config)
    portfolio_summary = fetch_portfolio_summary(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk)
    position_map = {
        row["symbol"]: {**row, "quantity_open": row["quantity"]}
        for row in fetch_portfolio_positions(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk)
    }
    live_symbols = list({item.get("symbol") for item in suggestions if item.get("symbol")})
    live_symbols.extend(position_map.keys())
    live_price_map = _get_live_price_map([symbol for symbol in live_symbols if symbol], config)
    fx_snapshot = fetch_ecb_fx_rates()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    orders = []
    max_position_weight = float(config["risk"]["max_position_weight"])
    min_trade_value_dkk = float(config["execution"]["min_trade_value_dkk"])
    remaining_capacity = _remaining_daily_order_capacity(connection, config)
    remaining_cash_dkk = float(fetch_cash_summary(connection, initial_cash_dkk=initial_cash_dkk)["cash_balance_dkk"])
    approval_required = _approval_required_for_order(config)

    for suggestion in suggestions:
        if remaining_capacity <= 0:
            break
        action = suggestion["action"]
        symbol = suggestion["symbol"]
        if action not in {"BUY", "SELL"}:
            continue
        requested_weight_pct = float(suggestion["target_weight_pct"])
        if requested_weight_pct > 1.0:
            requested_weight_pct = requested_weight_pct / 100.0
        requested_weight_pct = min(requested_weight_pct, max_position_weight)
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
                    "approval_required": 1 if approval_required else 0,
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
            whole_quantity = _whole_share_quantity(quantity)
            estimated_value_dkk = whole_quantity * price_local * fx_rate
        else:
            capped_delta_value_dkk = min(delta_value_dkk, max(remaining_cash_dkk, 0.0))
            target_quantity = capped_delta_value_dkk / max(price_local * fx_rate, 1e-9)
            whole_quantity = min(
                _whole_share_quantity(quantity),
                _max_affordable_buy_quantity(
                    symbol=symbol,
                    price_local=price_local,
                    currency=currency,
                    fx_rate=fx_rate,
                    available_cash_dkk=max(remaining_cash_dkk, 0.0),
                    config=config,
                ),
                _whole_share_quantity(target_quantity),
            )
            gross_local = whole_quantity * price_local
            estimated_value_dkk = gross_local * fx_rate
        if whole_quantity <= 0 or estimated_value_dkk < min_trade_value_dkk:
            continue
        if action == "BUY":
            gross_local = whole_quantity * price_local
            gross_dkk = gross_local * fx_rate
            commission = _calculate_buy_commission(symbol, gross_local, gross_dkk, currency, fx_rate, config)
            remaining_cash_dkk -= gross_dkk + commission["commission_dkk"]
        else:
            try:
                sell_outcome = calculate_sell_outcome(
                    symbol,
                    float(whole_quantity),
                    float(price_local),
                    config=config,
                    connection=connection,
                    batch_id=batch_id,
                    tax_year=datetime.now(UTC).year,
                )
                remaining_cash_dkk += float(sell_outcome["net_DKK"])
            except ValueError:
                pass

        orders.append(
            {
                "symbol": symbol,
                "action": action,
                "mode": config["execution"]["mode"],
                "status": "pending_approval" if approval_required else "pending_execution",
                "adapter": config["execution"]["adapter"],
                "requested_weight_pct": requested_weight_pct,
                "quantity": float(whole_quantity),
                "price_local": price_local,
                "currency": currency,
                "estimated_value_dkk": estimated_value_dkk,
                "approval_required": 1 if approval_required else 0,
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
    quantity = float(_whole_share_quantity(float(order["quantity"])))
    currency = order["currency"]
    initial_cash_dkk = _initial_cash_dkk(config)
    portfolio_before = {
        "summary": fetch_portfolio_summary(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk),
        "positions": fetch_portfolio_positions(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk),
    }
    fx_snapshot = fetch_ecb_fx_rates()
    fx_rate = fx_rate_to_dkk(currency, fx_snapshot)
    gross_local = price_local * quantity
    gross_dkk = gross_local * fx_rate
    commission = _calculate_buy_commission(order["symbol"], gross_local, gross_dkk, currency, fx_rate, config)
    total_spend_dkk = gross_dkk + commission["commission_dkk"]
    identity = resolve_instrument_identity(order["symbol"], currency=currency, config=config)
    available_cash_dkk = float(portfolio_before["summary"]["cash_balance_dkk"])
    if total_spend_dkk > available_cash_dkk + 1e-9:
        raise ValueError(
            f"Insufficient cash to buy {int(quantity)} shares of {order['symbol']}; "
            f"need {total_spend_dkk:.2f} DKK, have {available_cash_dkk:.2f} DKK"
        )
    cursor = connection.execute(
        """
        INSERT INTO trade_ledger (
            created_at, symbol, isin, figi, instrument_name, side, quantity, price_local, currency,
            gross_amount_dkk, commission_dkk, commission_local, fx_conversion_dkk, tax_dkk,
            realised_gain_dkk, cost_basis_sold_dkk, net_amount_dkk, mode, status, notes,
            portfolio_before_json, portfolio_after_json, decision_context_json, tax_year, batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            order["symbol"],
            identity.isin,
            identity.figi,
            identity.instrument_name,
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
            -total_spend_dkk,
            order["mode"],
            "executed" if order["mode"] == "simulation" else "approved",
            "Phase 5 buy execution",
            json.dumps(portfolio_before, ensure_ascii=False, sort_keys=True),
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
            lot_id, batch_id, created_at, acquired_at, symbol, isin, figi, instrument_name,
            quantity_original, currency, cost_basis_total_local, cost_basis_total_dkk,
            fx_rate_to_dkk, source_type, source_reference, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lot_id,
            batch_id,
            created_at,
            created_at,
            order["symbol"],
            identity.isin,
            identity.figi,
            identity.instrument_name,
            quantity,
            currency,
            gross_local + commission["commission_local"],
            gross_dkk + commission["commission_dkk"],
            fx_rate,
            f"{order['mode']}_buy",
            f"execution_order:{order['id']}",
            json.dumps(
                {
                    "request": json.loads(order["request_json"]) if order.get("request_json") else {},
                    "identity_source": identity.source,
                    "figi": identity.figi,
                    "isin": identity.isin,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )
    connection.commit()
    portfolio_after = {
        "summary": fetch_portfolio_summary(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk),
        "positions": fetch_portfolio_positions(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk),
    }
    connection.execute(
        "UPDATE trade_ledger SET portfolio_after_json = ? WHERE id = ?",
        (json.dumps(portfolio_after, ensure_ascii=False, sort_keys=True), ledger_id),
    )
    connection.commit()
    return {"ledger_id": ledger_id, "lot_id": lot_id}


def _synced_fill_quantity(connection, execution_order_id: int) -> float:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(delta_quantity), 0) AS synced_quantity
        FROM execution_fills
        WHERE execution_order_id = ?
        """,
        (execution_order_id,),
    ).fetchone()
    return float(row["synced_quantity"]) if row else 0.0


def _record_execution_fill(
    connection,
    *,
    order: dict[str, Any],
    broker_order_id: str | None,
    fill_status: str,
    cumulative_quantity: float,
    delta_quantity: float,
    average_price_local: float,
    currency: str,
    ledger_id: int | None,
    payload: dict[str, Any],
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO execution_fills (
            created_at, execution_order_id, broker_order_id, symbol, side, fill_status,
            cumulative_quantity, delta_quantity, average_price_local, currency, ledger_id, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(timespec="seconds"),
            order["id"],
            broker_order_id,
            order["symbol"],
            order["action"],
            fill_status,
            cumulative_quantity,
            delta_quantity,
            average_price_local,
            currency,
            ledger_id,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    return int(cursor.lastrowid)


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_broker_quantity(payload: dict[str, Any]) -> float | None:
    for key in ("Amount", "CurrentAmount", "OrderAmount", "LeavesAmount", "OriginalAmount"):
        value = _coerce_float(payload.get(key))
        if value is not None:
            return value
    return None


def _execution_result(order: dict[str, Any]) -> dict[str, Any]:
    return json.loads(order["execution_result_json"]) if order.get("execution_result_json") else {}


def _broker_payload(order: dict[str, Any]) -> dict[str, Any]:
    return _execution_result(order).get("payload", {})


def _extract_broker_price(payload: dict[str, Any]) -> float | None:
    for key in ("OrderPrice", "Price", "OrderPriceDisplay"):
        value = _coerce_float(payload.get(key))
        if value is not None:
            return value
    return None


def _event_signature(order_id: int, event_type: str, payload: dict[str, Any]) -> str:
    def sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: sanitize(item)
                for key, item in value.items()
                if key not in {"last_sync_at"}
            }
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    serialized = json.dumps(
        sanitize(
            {
            "order_id": order_id,
            "event_type": event_type,
            "payload": payload,
            }
        ),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _record_execution_event(
    connection,
    *,
    order: dict[str, Any],
    broker_order_id: str | None,
    event_type: str,
    broker_status: str | None,
    broker_substatus: str | None,
    broker_quantity: float | None,
    broker_price_local: float | None,
    payload: dict[str, Any],
) -> int | None:
    signature = _event_signature(
        int(order["id"]),
        event_type,
        {
            "broker_order_id": broker_order_id,
            "broker_status": broker_status,
            "broker_substatus": broker_substatus,
            "broker_quantity": broker_quantity,
            "broker_price_local": broker_price_local,
        },
    )
    existing = connection.execute(
        "SELECT id FROM execution_order_events WHERE event_signature = ?",
        (signature,),
    ).fetchone()
    if existing:
        return None
    cursor = connection.execute(
        """
        INSERT INTO execution_order_events (
            created_at, execution_order_id, broker_order_id, event_type,
            broker_status, broker_substatus, broker_quantity, broker_price_local,
            event_signature, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(timespec="seconds"),
            order["id"],
            broker_order_id,
            event_type,
            broker_status,
            broker_substatus,
            broker_quantity,
            broker_price_local,
            signature,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    return int(cursor.lastrowid)


def _sync_incremental_live_fill(
    connection,
    config: dict[str, Any],
    order: dict[str, Any],
    activity: dict[str, Any],
    *,
    broker_order_id: str | None,
    fill_status: str,
) -> dict[str, Any]:
    batch_id = fetch_latest_batch_id(connection)
    filled_quantity = float(activity.get("FilledAmount") or order["quantity"])
    average_price = float(activity.get("AveragePrice") or order["price_local"])
    already_synced = _synced_fill_quantity(connection, int(order["id"]))
    delta_quantity = max(filled_quantity - already_synced, 0.0)
    if delta_quantity <= 1e-9:
        return {
            "ledger_id": None,
            "fill_id": None,
            "delta_quantity": 0.0,
            "cumulative_quantity": filled_quantity,
            "status": "no_new_fill",
        }

    synced_order = {**order, "quantity": delta_quantity, "price_local": average_price}

    if order["action"] == "SELL":
        trade = calculate_sell_outcome(
            order["symbol"],
            delta_quantity,
            average_price,
            config=config,
            connection=connection,
            batch_id=batch_id,
            tax_year=datetime.now(UTC).year,
        )
        trade["mode"] = order["mode"]
        trade["status"] = "executed"
        trade["notes"] = f"Saxo broker {fill_status.lower()} sync"
        trade["decision_context"] = activity
        result = update_ledger(trade, config=config, connection=connection)
    else:
        result = _record_buy_trade(connection, config, synced_order, batch_id)
        connection.execute(
            """
            UPDATE trade_ledger
            SET notes = ?, decision_context_json = ?
            WHERE id = ?
            """,
            (
                f"Saxo broker {fill_status.lower()} sync",
                json.dumps(activity, ensure_ascii=False, sort_keys=True),
                result["ledger_id"],
            ),
        )
        connection.commit()
    fill_id = _record_execution_fill(
        connection,
        order=order,
        broker_order_id=broker_order_id,
        fill_status=fill_status,
        cumulative_quantity=filled_quantity,
        delta_quantity=delta_quantity,
        average_price_local=average_price,
        currency=str(order["currency"]),
        ledger_id=result["ledger_id"],
        payload=activity,
    )
    connection.commit()
    return {
        **result,
        "fill_id": fill_id,
        "delta_quantity": delta_quantity,
        "cumulative_quantity": filled_quantity,
    }


def sync_broker_order_statuses(*, config: dict[str, Any] | None = None, connection=None, limit: int = 25) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        rows = resolved_connection.execute(
            """
            SELECT *
            FROM execution_orders
            WHERE mode = 'live'
              AND status IN (
                  'submitted_to_broker',
                  'broker_working',
                  'broker_partially_filled',
                  'broker_amended',
                  'broker_replace_requested',
                  'broker_cancel_requested'
              )
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        if not rows:
            return {"status": "ok", "updated": 0, "orders": []}

        session = ensure_access_token(resolved_config, resolved_config["saxo"].get("session_path"))
        updates: list[dict[str, Any]] = []

        for row in rows:
            order = dict(row)
            execution_result = json.loads(order["execution_result_json"]) if order.get("execution_result_json") else {}
            broker_result = execution_result.get("broker_result", {})
            broker_order_id = broker_result.get("OrderId")
            if not broker_order_id:
                updates.append({"order_id": order["id"], "status": order["status"], "skipped": "missing_order_id"})
                continue

            try:
                open_order = get_open_order(str(broker_order_id), resolved_config, session)
                broker_status = str(open_order.get("Status") or "Working")
                broker_quantity = _extract_broker_quantity(open_order)
                broker_price = _extract_broker_price(open_order)
                quantity_changed = broker_quantity is not None and abs(broker_quantity - float(order["quantity"])) > 1e-9
                price_changed = (
                    broker_price is not None
                    and order.get("price_local") is not None
                    and abs(broker_price - float(order["price_local"])) > 1e-9
                )
                if broker_status.lower() in {"working", "placed"}:
                    new_status = (
                        "broker_amended"
                        if quantity_changed or price_changed or order["status"] == "broker_amended"
                        else "broker_working"
                    )
                elif broker_status.lower() == "fill":
                    new_status = "broker_partially_filled"
                else:
                    new_status = order["status"]
                payload = {
                    **execution_result,
                    "open_order": open_order,
                    "broker_quantity": broker_quantity,
                    "broker_price_local": broker_price,
                    "quantity_changed": quantity_changed,
                    "price_changed": price_changed,
                    "last_sync_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                event_id = _record_execution_event(
                    resolved_connection,
                    order=order,
                    broker_order_id=str(broker_order_id),
                    event_type=new_status,
                    broker_status=broker_status,
                    broker_substatus=str(open_order.get("SubStatus") or ""),
                    broker_quantity=broker_quantity,
                    broker_price_local=broker_price,
                    payload=payload,
                )
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, quantity = COALESCE(?, quantity), price_local = COALESCE(?, price_local), execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        new_status,
                        broker_quantity,
                        broker_price,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        order["id"],
                    ),
                )
                if new_status == "broker_amended":
                    append_audit_log(
                        resolved_connection,
                        "execution_order_amended",
                        {
                            "order_id": order["id"],
                            "broker_order_id": broker_order_id,
                            "broker_quantity": broker_quantity,
                            "broker_price_local": broker_price,
                            "event_id": event_id,
                        },
                    )
                updates.append({"order_id": order["id"], "status": new_status})
                continue
            except SaxoOrderNotFoundError:
                activity = get_order_activity_last(str(broker_order_id), resolved_config, session)

            activity_status = str(activity.get("Status") or "")
            activity_substatus = str(activity.get("SubStatus") or "")
            broker_quantity = _extract_broker_quantity(activity)
            broker_price = _extract_broker_price(activity) or _coerce_float(activity.get("AveragePrice"))
            payload = {
                **execution_result,
                "last_activity": activity,
                "broker_quantity": broker_quantity,
                "broker_price_local": broker_price,
                "last_sync_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }

            if activity_status == "FinalFill" and activity_substatus == "Confirmed":
                result = _sync_incremental_live_fill(
                    resolved_connection,
                    resolved_config,
                    order,
                    activity,
                    broker_order_id=str(broker_order_id),
                    fill_status="FinalFill",
                )
                event_id = _record_execution_event(
                    resolved_connection,
                    order=order,
                    broker_order_id=str(broker_order_id),
                    event_type="broker_final_fill",
                    broker_status=activity_status,
                    broker_substatus=activity_substatus,
                    broker_quantity=broker_quantity,
                    broker_price_local=broker_price,
                    payload=payload,
                )
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, ledger_id = COALESCE(?, ledger_id), execution_result_json = ?
                    WHERE id = ?
                    """,
                    ("executed", result["ledger_id"], json.dumps(payload, ensure_ascii=False, sort_keys=True), order["id"]),
                )
                append_audit_log(
                    resolved_connection,
                    "execution_order_fill_synced",
                    {
                        "order_id": order["id"],
                        "ledger_id": result["ledger_id"],
                        "broker_order_id": broker_order_id,
                        "delta_quantity": result["delta_quantity"],
                        "cumulative_quantity": result["cumulative_quantity"],
                        "event_id": event_id,
                    },
                )
                updates.append({"order_id": order["id"], "status": "executed", "ledger_id": result["ledger_id"]})
            elif activity_status == "Fill" and activity_substatus == "Confirmed":
                result = _sync_incremental_live_fill(
                    resolved_connection,
                    resolved_config,
                    order,
                    activity,
                    broker_order_id=str(broker_order_id),
                    fill_status="Fill",
                )
                event_id = _record_execution_event(
                    resolved_connection,
                    order=order,
                    broker_order_id=str(broker_order_id),
                    event_type="broker_fill",
                    broker_status=activity_status,
                    broker_substatus=activity_substatus,
                    broker_quantity=broker_quantity,
                    broker_price_local=broker_price,
                    payload=payload,
                )
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, ledger_id = COALESCE(?, ledger_id), execution_result_json = ?
                    WHERE id = ?
                    """,
                    ("broker_partially_filled", result["ledger_id"], json.dumps(payload, ensure_ascii=False, sort_keys=True), order["id"]),
                )
                append_audit_log(
                    resolved_connection,
                    "execution_order_partial_fill_synced",
                    {
                        "order_id": order["id"],
                        "ledger_id": result["ledger_id"],
                        "broker_order_id": broker_order_id,
                        "delta_quantity": result["delta_quantity"],
                        "cumulative_quantity": result["cumulative_quantity"],
                        "event_id": event_id,
                    },
                )
                updates.append(
                    {
                        "order_id": order["id"],
                        "status": "broker_partially_filled",
                        "ledger_id": result["ledger_id"],
                        "delta_quantity": result["delta_quantity"],
                    }
                )
            elif activity_status in {"Changed", "Replaced", "Amended"} and activity_substatus == "Confirmed":
                event_id = _record_execution_event(
                    resolved_connection,
                    order=order,
                    broker_order_id=str(broker_order_id),
                    event_type="broker_amended",
                    broker_status=activity_status,
                    broker_substatus=activity_substatus,
                    broker_quantity=broker_quantity,
                    broker_price_local=broker_price,
                    payload=payload,
                )
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, quantity = COALESCE(?, quantity), price_local = COALESCE(?, price_local), execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        "broker_amended",
                        broker_quantity,
                        broker_price,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        order["id"],
                    ),
                )
                append_audit_log(
                    resolved_connection,
                    "execution_order_amended",
                    {
                        "order_id": order["id"],
                        "broker_order_id": broker_order_id,
                        "broker_quantity": broker_quantity,
                        "broker_price_local": broker_price,
                        "event_id": event_id,
                    },
                )
                updates.append({"order_id": order["id"], "status": "broker_amended"})
            elif activity_status in {"Cancelled", "Expired"} and activity_substatus == "Confirmed":
                new_status = "broker_cancelled" if activity_status == "Cancelled" else "broker_expired"
                event_id = _record_execution_event(
                    resolved_connection,
                    order=order,
                    broker_order_id=str(broker_order_id),
                    event_type=new_status,
                    broker_status=activity_status,
                    broker_substatus=activity_substatus,
                    broker_quantity=broker_quantity,
                    broker_price_local=broker_price,
                    payload=payload,
                )
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, execution_result_json = ?
                    WHERE id = ?
                    """,
                    (new_status, json.dumps(payload, ensure_ascii=False, sort_keys=True), order["id"]),
                )
                append_audit_log(
                    resolved_connection,
                    "execution_order_closed_without_fill",
                    {
                        "order_id": order["id"],
                        "broker_order_id": broker_order_id,
                        "status": new_status,
                        "event_id": event_id,
                    },
                )
                updates.append({"order_id": order["id"], "status": new_status})
            elif activity_status in {"Rejected", "Failed"}:
                event_id = _record_execution_event(
                    resolved_connection,
                    order=order,
                    broker_order_id=str(broker_order_id),
                    event_type="broker_rejected",
                    broker_status=activity_status,
                    broker_substatus=activity_substatus,
                    broker_quantity=broker_quantity,
                    broker_price_local=broker_price,
                    payload=payload,
                )
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, error_text = ?, execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        "broker_rejected",
                        json.dumps(activity, ensure_ascii=False, sort_keys=True),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        order["id"],
                    ),
                )
                append_audit_log(
                    resolved_connection,
                    "execution_order_rejected",
                    {
                        "order_id": order["id"],
                        "broker_order_id": broker_order_id,
                        "event_id": event_id,
                    },
                )
                updates.append({"order_id": order["id"], "status": "broker_rejected"})
            else:
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET execution_result_json = ?
                    WHERE id = ?
                    """,
                    (json.dumps(payload, ensure_ascii=False, sort_keys=True), order["id"]),
                )
                updates.append({"order_id": order["id"], "status": order["status"]})

        resolved_connection.commit()
        return {"status": "ok", "updated": len(updates), "orders": updates}
    finally:
        if should_close:
            resolved_connection.close()


def execute_order(order_id: int, *, config: dict[str, Any] | None = None, connection=None, approved: bool = False) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        order_row = resolved_connection.execute("SELECT * FROM execution_orders WHERE id = ?", (order_id,)).fetchone()
        if not order_row:
            raise ValueError(f"Unknown execution order {order_id}")
        order = dict(order_row)
        normalized_quantity = _whole_share_quantity(float(order["quantity"]))
        if normalized_quantity <= 0:
            resolved_connection.execute(
                """
                UPDATE execution_orders
                SET status = ?, error_text = ?
                WHERE id = ?
                """,
                ("invalid_quantity", "Order quantity must be at least 1 whole share", order_id),
            )
            resolved_connection.commit()
            return {"status": "invalid_quantity", "order_id": order_id}
        if abs(float(order["quantity"]) - normalized_quantity) > 1e-9:
            order["quantity"] = float(normalized_quantity)
            resolved_connection.execute(
                "UPDATE execution_orders SET quantity = ? WHERE id = ?",
                (float(normalized_quantity), order_id),
            )
            resolved_connection.commit()
        if order["status"] not in {"pending_execution", "pending_approval", "waiting_for_market_open"}:
            return {"status": order["status"], "order_id": order_id}
        if order["mode"] == "live" and order["approval_required"] and not approved:
            return {"status": "approval_required", "order_id": order_id}
        market_row = _market_status_for_symbol(str(order["symbol"]), resolved_config)
        if market_row is not None and not bool(market_row.get("is_open")):
            error_text = f"Exchange closed for {order['symbol']}: {market_row.get('status_reason')}"
            payload = {
                "symbol": order["symbol"],
                "exchange_code": market_row.get("code"),
                "market": market_row.get("market"),
                "status_reason": market_row.get("status_reason"),
                "next_open": market_row.get("next_open"),
                "next_open_at_utc": market_row.get("next_open_at_utc"),
            }
            resolved_connection.execute(
                """
                UPDATE execution_orders
                SET status = ?, error_text = ?, execution_result_json = ?
                WHERE id = ?
                """,
                (
                    "waiting_for_market_open",
                    error_text,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    order_id,
                ),
            )
            resolved_connection.commit()
            return {
                "status": "waiting_for_market_open",
                "order_id": order_id,
                "error": error_text,
                "market": payload,
            }
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
                _dispatch_execution_failure_alerts(resolved_connection, resolved_config)
                return {"status": "execution_failed", "order_id": order_id, "error": error_text}
            try:
                session = ensure_access_token(resolved_config, resolved_config["saxo"].get("session_path"))
                if order["action"] == "BUY" and _cash_gate_enabled(resolved_config):
                    cash_gate = _evaluate_live_buy_cash_gate(order, resolved_config, session)
                    if not cash_gate["allowed"]:
                        error_text = (
                            f"Waiting for settled cash before buying {order['symbol']}. "
                            f"Required {cash_gate['required_dkk']:.2f} DKK, "
                            f"cash available {cash_gate['cash_available_dkk']:.2f} DKK, "
                            f"funds for settlement {cash_gate['funds_for_settlement_dkk']:.2f} DKK, "
                            f"transactions not booked {cash_gate['transactions_not_booked_dkk']:.2f} DKK."
                        )
                        resolved_connection.execute(
                            """
                            UPDATE execution_orders
                            SET status = ?, error_text = ?, execution_result_json = ?
                            WHERE id = ?
                            """,
                            (
                                "waiting_for_cash_settlement",
                                error_text,
                                json.dumps({"cash_gate": cash_gate}, ensure_ascii=False, sort_keys=True),
                                order_id,
                            ),
                        )
                        resolved_connection.commit()
                        return {
                            "status": "waiting_for_cash_settlement",
                            "order_id": order_id,
                            "error": error_text,
                            "cash_gate": cash_gate,
                        }
                payload = build_market_order_payload(
                    symbol=order["symbol"],
                    action=order["action"],
                    quantity=float(_whole_share_quantity(float(order["quantity"]))),
                    external_reference=f"saxo-daytrader:{order_id}",
                    config=resolved_config,
                    session=session,
                )
                precheck = precheck_order(payload, resolved_config, session)
                broker_result = place_order(payload, resolved_config, session)
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, approved_at = ?, broker_order_id = ?, execution_result_json = ?
                    WHERE id = ?
                    """,
                    (
                        "submitted_to_broker",
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        str(broker_result.get("OrderId", "")) or None,
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
                _dispatch_execution_failure_alerts(resolved_connection, resolved_config)
                return {"status": "execution_failed", "order_id": order_id, "error": error_text}

        batch_id = fetch_latest_batch_id(resolved_connection)
        try:
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
        except ValueError as exc:
            error_text = str(exc)
            failed = _mark_execution_failed(
                resolved_connection,
                order_id=order_id,
                approved=True,
                adapter=order["adapter"],
                error_text=error_text,
            )
            append_audit_log(
                resolved_connection,
                "execution_order_failed",
                {"order_id": order_id, "mode": order["mode"], "adapter": order["adapter"], "error": error_text},
            )
            _dispatch_execution_failure_alerts(resolved_connection, resolved_config)
            return failed

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


def manage_live_order(
    order_id: int,
    *,
    management_action: str,
    config: dict[str, Any] | None = None,
    connection=None,
    new_quantity: float | None = None,
    new_price: float | None = None,
) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        row = resolved_connection.execute("SELECT * FROM execution_orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown execution order {order_id}")
        order = dict(row)
        if order["mode"] != "live":
            return {"status": "not_live_order", "order_id": order_id}
        if order["adapter"] != "saxo":
            return {"status": "unsupported_adapter", "order_id": order_id}
        if order["status"] not in MANAGEABLE_LIVE_STATUSES:
            return {"status": "not_manageable", "order_id": order_id, "current_status": order["status"]}
        if resolved_config["app"]["dry_run"]:
            return {"status": "blocked_by_dry_run", "order_id": order_id}

        session = ensure_access_token(resolved_config, resolved_config["saxo"].get("session_path"))
        execution_result = _execution_result(order)
        broker_result = execution_result.get("broker_result", {})
        broker_order_id = str(order.get("broker_order_id") or broker_result.get("OrderId") or "")
        if not broker_order_id:
            return {"status": "missing_broker_order_id", "order_id": order_id}

        now_iso = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            if management_action == "cancel":
                broker_response = cancel_order(broker_order_id, resolved_config, session)
                new_status = "broker_cancel_requested"
                payload = {
                    **execution_result,
                    "management": {
                        "action": "cancel",
                        "requested_at": now_iso,
                        "broker_response": broker_response,
                    },
                }
                event_type = "broker_cancel_requested"
            elif management_action == "replace":
                original_payload = _broker_payload(order)
                order_type = str(original_payload.get("OrderType") or "Market")
                effective_price = new_price if new_price is not None else _coerce_float(order.get("price_local"))
                normalized_replace_quantity = _whole_share_quantity(
                    new_quantity if new_quantity is not None else float(order["quantity"])
                )
                if normalized_replace_quantity <= 0:
                    return {"status": "invalid_quantity", "order_id": order_id}
                if new_price is not None and order_type == "Market":
                    order_type = "Limit"
                patch_payload: dict[str, Any] = {
                    "AccountKey": original_payload.get("AccountKey") or resolved_config["saxo"]["account_key"] or session.get("account_key"),
                    "OrderId": broker_order_id,
                    "Amount": float(normalized_replace_quantity),
                    "AssetType": original_payload.get("AssetType", "Stock"),
                    "OrderType": order_type,
                }
                if original_payload.get("OrderDuration"):
                    patch_payload["OrderDuration"] = original_payload["OrderDuration"]
                if effective_price is not None and order_type != "Market":
                    patch_payload["OrderPrice"] = effective_price
                broker_response = change_order(patch_payload, resolved_config, session)
                new_status = "broker_replace_requested"
                payload = {
                    **execution_result,
                    "management": {
                        "action": "replace",
                        "requested_at": now_iso,
                        "request_payload": patch_payload,
                        "broker_response": broker_response,
                    },
                }
                event_type = "broker_replace_requested"
            else:
                raise ValueError(f"Unsupported management action '{management_action}'")
        except (SaxoSessionError, SaxoOrderNotFoundError, requests.RequestException, ValueError) as exc:
            error_text = str(exc)
            failure_event_type = "broker_cancel_failed" if management_action == "cancel" else "broker_replace_failed"
            failure_payload = {
                **execution_result,
                "management": {
                    "action": management_action,
                    "requested_at": now_iso,
                    "error": error_text,
                },
            }
            event_id = _record_execution_event(
                resolved_connection,
                order=order,
                broker_order_id=broker_order_id,
                event_type=failure_event_type,
                broker_status=order["status"],
                broker_substatus="failed",
                broker_quantity=_coerce_float(order.get("quantity")),
                broker_price_local=_coerce_float(order.get("price_local")),
                payload=failure_payload,
            )
            resolved_connection.execute(
                """
                UPDATE execution_orders
                SET error_text = ?, execution_result_json = ?
                WHERE id = ?
                """,
                (
                    error_text,
                    json.dumps(failure_payload, ensure_ascii=False, sort_keys=True),
                    order_id,
                ),
            )
            resolved_connection.commit()
            append_audit_log(
                resolved_connection,
                "execution_order_management_failed",
                {
                    "order_id": order_id,
                    "broker_order_id": broker_order_id,
                    "action": management_action,
                    "event_id": event_id,
                    "error": error_text,
                },
            )
            _dispatch_execution_failure_alerts(resolved_connection, resolved_config)
            return {
                "status": "management_failed",
                "order_id": order_id,
                "broker_order_id": broker_order_id,
                "event_id": event_id,
                "error": error_text,
            }

        event_id = _record_execution_event(
            resolved_connection,
            order=order,
            broker_order_id=broker_order_id,
            event_type=event_type,
            broker_status=new_status,
            broker_substatus="requested",
            broker_quantity=float(normalized_replace_quantity) if management_action == "replace" else _coerce_float(order.get("quantity")),
            broker_price_local=new_price if management_action == "replace" else _coerce_float(order.get("price_local")),
            payload=payload,
        )
        resolved_connection.execute(
            """
            UPDATE execution_orders
            SET status = ?, execution_result_json = ?
            WHERE id = ?
            """,
            (new_status, json.dumps(payload, ensure_ascii=False, sort_keys=True), order_id),
        )
        resolved_connection.commit()
        append_audit_log(
            resolved_connection,
            "execution_order_management_requested",
            {
                "order_id": order_id,
                "broker_order_id": broker_order_id,
                "action": management_action,
                "event_id": event_id,
            },
        )
        return {
            "status": new_status,
            "order_id": order_id,
            "broker_order_id": broker_order_id,
            "event_id": event_id,
        }
    finally:
        if should_close:
            resolved_connection.close()


def queue_and_maybe_execute_latest_report(*, config: dict[str, Any] | None = None, connection=None) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        report = fetch_latest_decision_report(resolved_connection)
        orders = []
        if report and report["status"] == "completed":
            orders = _create_or_fetch_orders(resolved_connection, resolved_config, report)
        executed = []
        executable_statuses = {"pending_execution", "waiting_for_market_open", "waiting_for_cash_settlement"}
        auto_execute_queue = (
            (
                resolved_config["execution"]["mode"] == "simulation"
                and resolved_config["execution"]["auto_execute_simulation"]
            )
            or _should_auto_submit_live_orders(resolved_config)
        )
        if auto_execute_queue:
            if _should_auto_submit_live_orders(resolved_config):
                executable_statuses.add("pending_approval")
            queue_rows = resolved_connection.execute(
                f"""
                SELECT *
                FROM execution_orders
                WHERE mode = ?
                  AND status IN ({",".join("?" for _ in executable_statuses)})
                ORDER BY id ASC
                """,
                (str(resolved_config["execution"]["mode"]), *tuple(executable_statuses)),
            ).fetchall()
            for order in queue_rows:
                executed.append(
                    execute_order(
                        int(order["id"]),
                        config=resolved_config,
                        connection=resolved_connection,
                        approved=_should_auto_submit_live_orders(resolved_config),
                    )
                )
        broker_sync = sync_broker_order_statuses(config=resolved_config, connection=resolved_connection)
        alert_result = _dispatch_execution_alerts(resolved_connection, resolved_config)
        return {
            "status": "ok" if report and report["status"] == "completed" else "processed_existing_queue",
            "orders": orders,
            "executed": executed,
            "broker_sync": broker_sync,
            "alerts": alert_result,
        }
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


def fetch_execution_fills(connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM execution_fills
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_execution_events(connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM execution_order_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_invalid_simulation_trades(connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = fetch_invalid_trade_ledger_rows(connection, limit=limit)
    output: list[dict[str, Any]] = []
    for row in rows:
        related_order = connection.execute(
            """
            SELECT id, status, error_text
            FROM execution_orders
            WHERE ledger_id = ?
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        record = dict(row)
        if related_order:
            record["execution_order_id"] = related_order["id"]
            record["execution_order_status"] = related_order["status"]
            record["execution_order_error"] = related_order["error_text"]
        else:
            record["execution_order_id"] = None
            record["execution_order_status"] = None
            record["execution_order_error"] = None
        output.append(record)
    return output


def repair_invalid_simulation_trades(*, connection, config: dict[str, Any] | None = None, limit: int = 50) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    try:
        invalid_rows = fetch_invalid_simulation_trades(resolved_connection, limit=limit)
        repaired_ids: list[int] = []
        repaired_order_ids: list[int] = []
        for row in invalid_rows:
            if row["mode"] != "simulation" or row["status"] != "executed":
                continue
            note = str(row.get("notes") or "")
            appended_note = f"{note} | quarantined invalid simulation trade".strip(" |")
            resolved_connection.execute(
                """
                UPDATE trade_ledger
                SET status = ?, notes = ?
                WHERE id = ?
                """,
                ("ignored_invalid_simulation", appended_note, row["id"]),
            )
            repaired_ids.append(int(row["id"]))
            if row.get("execution_order_id") is not None:
                resolved_connection.execute(
                    """
                    UPDATE execution_orders
                    SET status = ?, error_text = ?
                    WHERE id = ?
                    """,
                    (
                        "invalid_repaired",
                        row["validation_note"],
                        int(row["execution_order_id"]),
                    ),
                )
                repaired_order_ids.append(int(row["execution_order_id"]))
            append_audit_log(
                resolved_connection,
                "invalid_simulation_trade_repaired",
                {
                    "ledger_id": int(row["id"]),
                    "execution_order_id": row.get("execution_order_id"),
                    "symbol": row["symbol"],
                    "validation_note": row["validation_note"],
                },
            )
        resolved_connection.commit()
        return {
            "status": "ok",
            "invalid_found": len(invalid_rows),
            "ledger_rows_repaired": repaired_ids,
            "execution_orders_repaired": repaired_order_ids,
        }
    finally:
        if should_close:
            resolved_connection.close()


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
            "execution_fills": out / "execution_fills.csv",
            "execution_order_events": out / "execution_order_events.csv",
            "notification_deliveries": out / "notification_deliveries.csv",
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
