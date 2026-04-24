from __future__ import annotations

import os
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import connect, fetch_scheduler_cycles, fetch_scheduler_status, init_db
from saxo_daytrader_xai.execution_engine import (
    fetch_execution_events,
    fetch_execution_fills,
    fetch_execution_orders,
    manage_live_order,
    queue_and_maybe_execute_latest_report,
    reconcile_portfolio_to_broker,
    retry_failed_execution_orders,
    sync_broker_order_statuses,
)
from saxo_daytrader_xai.market_schedule import get_market_status, summarize_analysis_window
from saxo_daytrader_xai.saxo_openapi import SaxoSessionError, ensure_access_token, get_chart_samples, lookup_instrument
from saxo_daytrader_xai.portfolio import (
    fetch_goal_tracking,
    fetch_portfolio_integrity_status,
    fetch_portfolio_positions,
    fetch_portfolio_summary,
    fetch_portfolio_value_history,
    fetch_trade_ledger,
    fetch_unrealised_after_tax_summary,
)
from saxo_daytrader_xai.scheduler_service import assess_scheduler_worker_health, run_manual_scheduler_cycle
from saxo_daytrader_xai.xai_decision import (
    estimate_next_decision_report,
    fetch_latest_decision_report,
    fetch_recent_decision_reports,
    generate_decision_report,
)


class SchedulerCycleRequest(BaseModel):
    mock: bool = False


class LiveOrderActionRequest(BaseModel):
    action: Literal["replace", "cancel"]
    quantity: float | None = None
    price: float | None = None


def _config_path(config_path: str | None = None) -> str:
    explicit = config_path or os.getenv("DAYTRADER_CONFIG") or "config.yaml"
    return str(Path(explicit).expanduser().resolve())


def _initial_cash_dkk(config: dict[str, Any]) -> float:
    return float(config.get("portfolio", {}).get("initial_cash_dkk", 0.0) or 0.0)


def _prefer_broker_state(config: dict[str, Any]) -> bool:
    execution_cfg = config.get("execution", {})
    return (
        str(execution_cfg.get("mode") or "").lower() == "live"
        and str(execution_cfg.get("adapter") or "").lower() == "saxo"
    )


def _daily_order_capacity(connection, config: dict[str, Any]) -> dict[str, int]:
    limit = int(config.get("execution", {}).get("max_daily_orders", 0) or 0)
    today = datetime.now(UTC).date().isoformat()
    used = int(
        connection.execute(
            """
            SELECT COUNT(*) AS count_orders
            FROM execution_orders
            WHERE substr(created_at, 1, 10) = ?
              AND status NOT IN ('error', 'cancelled')
            """,
            (today,),
        ).fetchone()["count_orders"]
    )
    remaining = max(limit - used, 0)
    return {"max": limit, "used": used, "remaining": remaining}


def _history_start_at(range_key: str, end_at: datetime) -> str | None:
    normalized = range_key.upper()
    if normalized == "1D":
        return (end_at - timedelta(days=1)).isoformat(timespec="seconds")
    if normalized == "1W":
        return (end_at - timedelta(days=7)).isoformat(timespec="seconds")
    if normalized == "1M":
        return (end_at - timedelta(days=31)).isoformat(timespec="seconds")
    if normalized == "3M":
        return (end_at - timedelta(days=93)).isoformat(timespec="seconds")
    if normalized == "1Y":
        return (end_at - timedelta(days=366)).isoformat(timespec="seconds")
    if normalized == "YTD":
        return datetime(end_at.year, 1, 1, tzinfo=UTC).isoformat(timespec="seconds")
    return None


def _parse_json_text(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return None


def create_app(config_path: str | None = None) -> FastAPI:
    resolved_config_path = _config_path(config_path)
    app = FastAPI(
        title="saxo-daytrader-xai API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.config_path = resolved_config_path

    @contextmanager
    def runtime():
        config = load_config(app.state.config_path)
        connection = connect(config["portfolio"]["database_path"])
        init_db(connection)
        try:
            yield config, connection
        finally:
            connection.close()

    def portfolio_kwargs(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "initial_cash_dkk": _initial_cash_dkk(config),
            "prefer_broker_cash": _prefer_broker_state(config),
        }

    def execution_counts(orders: list[dict[str, Any]]) -> dict[str, int]:
        queued_statuses = {
            "pending_execution",
            "pending_approval",
            "waiting_for_market_open",
            "waiting_for_cash_settlement",
            "waiting_for_virtual_cash_budget",
        }
        return {
            "queued": sum(1 for row in orders if row.get("status") in queued_statuses),
            "pending_approval": sum(1 for row in orders if row.get("status") == "pending_approval"),
            "broker_live": sum(
                1
                for row in orders
                if row.get("status")
                in {
                    "submitted_to_broker",
                    "broker_working",
                    "broker_amended",
                    "broker_partially_filled",
                    "broker_replace_requested",
                    "broker_cancel_requested",
                }
            ),
            "failed": sum(1 for row in orders if row.get("status") == "execution_failed"),
        }

    active_order_statuses = {
        "pending_execution",
        "pending_approval",
        "waiting_for_market_open",
        "waiting_for_cash_settlement",
        "waiting_for_virtual_cash_budget",
        "submitted_to_broker",
        "broker_working",
        "broker_amended",
        "broker_partially_filled",
        "broker_replace_requested",
        "broker_cancel_requested",
    }

    def ladder_status_by_symbol(connection) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT symbol, status, strategy_type, strategy_role
            FROM execution_orders
            ORDER BY id ASC
            """
        ).fetchall()
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = output.setdefault(
                str(row["symbol"]),
                {
                    "active_orders": 0,
                    "active_stop_orders": 0,
                    "active_take_profit_orders": 0,
                    "filled_entry_rungs": 0,
                    "total_entry_rungs": 0,
                    "latest_strategy_type": None,
                },
            )
            status = str(row["status"] or "")
            strategy_type = str(row["strategy_type"] or "") or None
            strategy_role = str(row["strategy_role"] or "") or None
            if strategy_type:
                record["latest_strategy_type"] = strategy_type
            if status in active_order_statuses:
                record["active_orders"] += 1
                if strategy_role == "stop_loss":
                    record["active_stop_orders"] += 1
                elif strategy_role == "take_profit":
                    record["active_take_profit_orders"] += 1
            if strategy_role == "entry":
                record["total_entry_rungs"] += 1
            if status == "executed" and strategy_role == "entry":
                record["filled_entry_rungs"] += 1
        for symbol, record in output.items():
            trailing = bool(record["active_stop_orders"])
            filled = int(record["filled_entry_rungs"])
            total = max(int(record["total_entry_rungs"]), filled, 0)
            if record["active_orders"]:
                if total:
                    status_text = f"{filled}/{total} filled"
                else:
                    status_text = f"{record['active_orders']} active"
                if trailing:
                    status_text += " • trailing"
            elif filled:
                status_text = f"{filled}/{total or filled} filled"
            else:
                status_text = "idle"
            record["text"] = status_text
            record["trailing"] = trailing
            record["progress_pct"] = (filled / total) if total else 0.0
        return output

    def _ladder_parameters(order_rows: list[dict[str, Any]], position: dict[str, Any] | None) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for row in order_rows:
            request_payload = _parse_json_text(row.get("request_json")) or {}
            strategy_metadata = request_payload.get("strategy_metadata") if isinstance(request_payload, dict) else None
            if isinstance(strategy_metadata, dict):
                metadata = strategy_metadata
                break
        if not metadata:
            return {}
        current_price = float(position.get("current_price_local") or 0.0) if isinstance(position, dict) else 0.0
        ceiling = metadata.get("take_profit_price_local")
        stop = metadata.get("stop_price_local")
        entry = metadata.get("entry_price_local")
        return {
            "atr_1m": metadata.get("atr_1m"),
            "rung_spacing_local": metadata.get("rung_spacing_local"),
            "ladder_rung_id": metadata.get("ladder_rung_id"),
            "max_position_weight_pct": float(metadata.get("max_position_weight_pct") or 0.0),
            "entry_price_local": entry,
            "take_profit_price_local": ceiling,
            "stop_price_local": stop,
            "current_price_local": current_price if current_price else None,
            "ceiling_gap_local": (float(ceiling) - current_price) if ceiling not in (None, "") and current_price else None,
            "stop_gap_local": (current_price - float(stop)) if stop not in (None, "") and current_price else None,
        }

    def _chart_series_for_symbol(config: dict[str, Any], symbol: str, *, range_key: str, first_event_at: datetime | None) -> tuple[list[dict[str, Any]], str | None]:
        execution_cfg = config.get("execution", {})
        if str(execution_cfg.get("adapter") or "").lower() != "saxo":
            return [], "Chart unavailable: non-Saxo adapter."
        try:
            session = ensure_access_token(config, config["saxo"].get("session_path"))
            instrument = lookup_instrument(symbol, config, session)
        except Exception as exc:  # noqa: BLE001
            return [], str(exc)

        now = datetime.now(UTC)
        normalized = str(range_key or "SESSION").upper()
        if normalized == "1H":
            horizon_minutes = 1
            count = 60
        elif normalized == "4H":
            horizon_minutes = 1
            count = 240
        elif normalized == "SESSION":
            age_minutes = 240
            if first_event_at is not None:
                age_minutes = max(int((now - first_event_at).total_seconds() // 60) + 30, 60)
            if age_minutes <= 360:
                horizon_minutes = 1
            elif age_minutes <= 1440:
                horizon_minutes = 5
            elif age_minutes <= 10080:
                horizon_minutes = 30
            else:
                horizon_minutes = 60
            count = min(max(age_minutes // max(horizon_minutes, 1), 60), 1000)
        else:
            horizon_minutes = 5
            count = 288

        try:
            payload = get_chart_samples(
                uic=instrument.uic,
                asset_type=instrument.asset_type,
                config=config,
                session=session,
                horizon_minutes=horizon_minutes,
                count=count,
                mode="UpTo",
            )
        except SaxoSessionError as exc:
            return [], str(exc)
        except Exception as exc:  # noqa: BLE001
            return [], f"Chart fetch failed: {exc}"

        data = payload.get("Data", []) or []
        output: list[dict[str, Any]] = []
        for item in data:
            output.append(
                {
                    "time": item.get("Time"),
                    "open": float(item.get("Open") or 0.0),
                    "high": float(item.get("High") or 0.0),
                    "low": float(item.get("Low") or 0.0),
                    "close": float(item.get("Close") or 0.0),
                    "volume": float(item.get("Volume") or 0.0),
                }
            )
        return output, None

    def asset_ladder_history_payload(config: dict[str, Any], connection, symbol: str, *, range_key: str = "SESSION") -> dict[str, Any]:
        kwargs = portfolio_kwargs(config)
        positions = fetch_portfolio_positions(connection, **kwargs)
        position = next((row for row in positions if str(row.get("symbol")) == symbol), None)
        order_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM execution_orders
                WHERE symbol = ?
                ORDER BY id ASC
                """,
                (symbol,),
            ).fetchall()
        ]
        fill_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM execution_fills
                WHERE symbol = ?
                ORDER BY id ASC
                """,
                (symbol,),
            ).fetchall()
        ]
        event_rows = [
            {
                **dict(row),
                "request_json": _parse_json_text(row["request_json"]),
            }
            for row in connection.execute(
                """
                SELECT e.*, o.request_json, o.strategy_role, o.strategy_type
                FROM execution_order_events e
                JOIN execution_orders o ON o.id = e.execution_order_id
                WHERE o.symbol = ?
                ORDER BY e.id ASC
                """,
                (symbol,),
            ).fetchall()
        ]

        first_event_at: datetime | None = None
        for row in fill_rows:
            if str(row.get("side") or "").upper() == "BUY" and row.get("created_at"):
                first_event_at = datetime.fromisoformat(str(row["created_at"])).astimezone(UTC)
                break
        if first_event_at is None:
            for row in order_rows:
                if str(row.get("action") or "").upper() == "BUY" and row.get("created_at"):
                    first_event_at = datetime.fromisoformat(str(row["created_at"])).astimezone(UTC)
                    break

        chart_points, chart_error = _chart_series_for_symbol(config, symbol, range_key=range_key, first_event_at=first_event_at)
        chart_source = "saxo" if chart_points else "fallback"

        markers: list[dict[str, Any]] = []
        for row in fill_rows:
            side = str(row.get("side") or "").upper()
            fill_price = float(row.get("average_price_local") or 0.0)
            fill_payload = _parse_json_text(row.get("raw_payload_json")) or {}
            last_activity = fill_payload.get("last_activity") if isinstance(fill_payload, dict) else None
            commission_dkk = None
            if isinstance(last_activity, dict):
                commission_dkk = (last_activity.get("Cost") or {}).get("Commission") or (last_activity.get("CostInAccountCurrency") or {}).get("Commission")
            markers.append(
                {
                    "id": f"fill-{row['id']}",
                    "time": row.get("created_at"),
                    "price": fill_price,
                    "kind": "buy_fill" if side == "BUY" else "sell_fill",
                    "label": f"{side.title()} fill",
                    "quantity": float(row.get("delta_quantity") or 0.0),
                    "commission_dkk": commission_dkk,
                    "strategy_role": None,
                    "ladder_rung_id": None,
                    "amendment_reason": None,
                    "strategy_reason": None,
                    "confidence": None,
                    "details": f"{side.title()} fill at {fill_price:.2f}",
                    "payload": fill_payload,
                }
            )
        for row in order_rows:
            request_payload = _parse_json_text(row.get("request_json")) or {}
            strategy_metadata = request_payload.get("strategy_metadata") if isinstance(request_payload, dict) else None
            if row.get("status") in active_order_statuses or row.get("status") == "executed":
                price = row.get("limit_price_local") or row.get("stop_price_local") or row.get("price_local")
                kind = "order"
                label = f"{str(row.get('action') or '').title()} {str(row.get('order_type') or 'Order')}"
                if str(row.get("strategy_role") or "") == "flatten_close":
                    kind = "flatten"
                    label = "Flatten"
                markers.append(
                    {
                        "id": f"order-{row['id']}",
                        "time": row.get("created_at"),
                        "price": float(price or 0.0),
                        "kind": kind,
                        "label": label,
                        "quantity": float(row.get("quantity") or 0.0),
                        "commission_dkk": None,
                        "strategy_role": row.get("strategy_role"),
                        "ladder_rung_id": strategy_metadata.get("ladder_rung_id") if isinstance(strategy_metadata, dict) else None,
                        "amendment_reason": strategy_metadata.get("amendment_reason") if isinstance(strategy_metadata, dict) else None,
                        "strategy_reason": request_payload.get("rationale") if isinstance(request_payload, dict) else None,
                        "confidence": request_payload.get("confidence") if isinstance(request_payload, dict) else None,
                        "details": f"{label} · {row.get('status')}",
                        "payload": {
                            "request_json": request_payload,
                            "execution_result_json": _parse_json_text(row.get("execution_result_json")),
                            "order": row,
                        },
                    }
                )
        for row in event_rows:
            if str(row.get("event_type") or "").startswith("broker_") and "amend" not in str(row.get("event_type") or ""):
                continue
            payload = _parse_json_text(row.get("raw_payload_json")) or {}
            markers.append(
                {
                    "id": f"event-{row['id']}",
                    "time": row.get("created_at"),
                    "price": float(row.get("broker_price_local") or 0.0),
                    "kind": "amendment",
                    "label": "Order update",
                    "quantity": float(row.get("broker_quantity") or 0.0),
                    "commission_dkk": None,
                    "strategy_role": row.get("strategy_role"),
                    "ladder_rung_id": payload.get("strategy_metadata", {}).get("ladder_rung_id") if isinstance(payload.get("strategy_metadata"), dict) else None,
                    "amendment_reason": payload.get("amendment_reason") if isinstance(payload, dict) else None,
                    "strategy_reason": None,
                    "confidence": None,
                    "details": f"{row.get('event_type')} · {row.get('broker_status') or ''}",
                    "payload": payload,
                }
            )
        markers.sort(key=lambda item: str(item.get("time") or ""))

        if not chart_points:
            fallback_points = []
            for marker in markers:
                marker_time = marker.get("time")
                marker_price = marker.get("price")
                if not marker_time or marker_price in (None, ""):
                    continue
                price_value = float(marker_price)
                fallback_points.append(
                    {
                        "time": marker_time,
                        "open": price_value,
                        "high": price_value,
                        "low": price_value,
                        "close": price_value,
                        "volume": 0.0,
                    }
                )
            if position and position.get("current_price_local") not in (None, ""):
                fallback_points.append(
                    {
                        "time": datetime.now(UTC).isoformat(timespec="seconds"),
                        "open": float(position["current_price_local"]),
                        "high": float(position["current_price_local"]),
                        "low": float(position["current_price_local"]),
                        "close": float(position["current_price_local"]),
                        "volume": 0.0,
                    }
                )
            if fallback_points:
                chart_points = sorted(fallback_points, key=lambda item: str(item["time"]))
                chart_error = chart_error or "Saxo chart samples unavailable; showing execution-price fallback."
                chart_source = "fallback"

        active_lines: list[dict[str, Any]] = []
        ladder_levels: list[dict[str, Any]] = []
        for row in order_rows:
            status = str(row.get("status") or "")
            if status not in active_order_statuses:
                continue
            strategy_role = str(row.get("strategy_role") or "")
            if row.get("stop_price_local") is not None:
                active_lines.append(
                    {
                        "label": "Stop loss",
                        "price": float(row["stop_price_local"]),
                        "color": "#b42318",
                        "kind": strategy_role or "stop",
                        "dashed": True,
                    }
                )
            if row.get("limit_price_local") is not None:
                active_lines.append(
                    {
                        "label": "Take profit" if strategy_role == "take_profit" else "Limit",
                        "price": float(row["limit_price_local"]),
                        "color": "#0f8a4b" if strategy_role == "take_profit" else "#6b7280",
                        "kind": strategy_role or "limit",
                        "dashed": True,
                    }
                )
            if str(row.get("strategy_type") or "") == "ladder":
                request_payload = _parse_json_text(row.get("request_json")) or {}
                metadata = request_payload.get("strategy_metadata") if isinstance(request_payload, dict) else None
                if isinstance(metadata, dict):
                    for key, label, color in (
                        ("entry_price_local", "Entry rung", "#9ca3af"),
                        ("take_profit_price_local", "Take-profit rung", "#0f8a4b"),
                        ("stop_price_local", "Stop rung", "#b42318"),
                    ):
                        value = metadata.get(key)
                        if value not in (None, ""):
                            ladder_levels.append(
                                {
                                    "label": label,
                                    "price": float(value),
                                    "color": color,
                                    "kind": key,
                                }
                            )

        ladder_summary = ladder_status_by_symbol(connection).get(symbol, {"text": "idle", "active_orders": 0, "filled_entry_rungs": 0, "trailing": False, "progress_pct": 0.0})
        ladder_parameters = _ladder_parameters(order_rows, position)
        return {
            "symbol": symbol,
            "range_key": range_key,
            "position": position,
            "ladder_summary": ladder_summary,
            "chart": {
                "points": chart_points,
                "error": chart_error,
                "source": chart_source,
                "has_real_data": chart_source == "saxo" and bool(chart_points),
                "first_event_at": first_event_at.isoformat(timespec="seconds") if first_event_at else None,
            },
            "markers": markers,
            "active_lines": active_lines,
            "ladder_levels": ladder_levels,
            "ladder_parameters": ladder_parameters,
            "legend": [
                {"key": "buy_fill", "label": "Buy fill", "color": "#0f8a4b"},
                {"key": "sell_fill", "label": "Sell fill", "color": "#b42318"},
                {"key": "order", "label": "Order / rung", "color": "#2563eb"},
                {"key": "amendment", "label": "Amendment", "color": "#38bdf8"},
                {"key": "stop_loss", "label": "Stop line", "color": "#b42318"},
                {"key": "take_profit", "label": "Ceiling / take-profit", "color": "#0f8a4b"},
                {"key": "current_price", "label": "Current price", "color": "#2563eb"},
            ],
        }

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        with runtime() as (config, connection):
            kwargs = portfolio_kwargs(config)
            summary = fetch_portfolio_summary(connection, **kwargs)
            after_tax = fetch_unrealised_after_tax_summary(connection, config, initial_cash_dkk=kwargs["initial_cash_dkk"])
            integrity = fetch_portfolio_integrity_status(connection, initial_cash_dkk=kwargs["initial_cash_dkk"])
            market_status = get_market_status(config)
            analysis_summary = summarize_analysis_window(market_status)
            latest_decision = fetch_latest_decision_report(connection)
            scheduler_status = fetch_scheduler_status(connection)
            scheduler_health = assess_scheduler_worker_health(
                scheduler_status,
                poll_interval_minutes=int(config.get("scheduler", {}).get("poll_interval_minutes", 10)),
            )
            orders = fetch_execution_orders(connection, limit=250)
            return {
                "app": {
                    "project_name": config.get("app", {}).get("project_name"),
                    "environment": config.get("app", {}).get("environment"),
                    "config_path": app.state.config_path,
                },
                "execution": {
                    "mode": config.get("execution", {}).get("mode"),
                    "adapter": config.get("execution", {}).get("adapter"),
                    "require_approval_live": bool(config.get("execution", {}).get("require_approval_live", True)),
                    "max_daily_orders": int(config.get("execution", {}).get("max_daily_orders", 0)),
                    "daily_order_capacity": _daily_order_capacity(connection, config),
                    "counts": execution_counts(orders),
                },
                "portfolio_summary": summary,
                "after_tax_summary": after_tax,
                "integrity": integrity,
                "analysis_summary": analysis_summary,
                "latest_decision": {
                    "id": latest_decision.get("id") if latest_decision else None,
                    "created_at": latest_decision.get("created_at") if latest_decision else None,
                    "status": latest_decision.get("status") if latest_decision else None,
                },
                "scheduler_status": scheduler_status,
                "scheduler_health": scheduler_health,
                "refresh": {
                    "price_poll_interval_minutes": int(config.get("price_monitor", {}).get("poll_interval_minutes", 1)),
                    "scheduler_poll_interval_minutes": int(config.get("scheduler", {}).get("poll_interval_minutes", 10)),
                    "decision_interval_minutes": int(config.get("strategy", {}).get("selection_interval_minutes", 15)),
                },
            }

    @app.get("/api/portfolio/positions")
    def portfolio_positions(limit: int = Query(default=25, ge=1, le=250)) -> dict[str, Any]:
        with runtime() as (config, connection):
            kwargs = portfolio_kwargs(config)
            positions = fetch_portfolio_positions(connection, **kwargs)
            ladder_status_map = ladder_status_by_symbol(connection)
            items = []
            for row in positions[:limit]:
                enriched = dict(row)
                enriched["ladder_status"] = ladder_status_map.get(
                    str(row.get("symbol") or ""),
                    {"text": "idle", "active_orders": 0, "filled_entry_rungs": 0, "trailing": False},
                )
                items.append(enriched)
            return {"items": items, "total": len(positions)}

    @app.get("/api/asset-ladder-history/{symbol}")
    def asset_ladder_history(symbol: str, range_key: str = Query(default="SESSION")) -> dict[str, Any]:
        with runtime() as (config, connection):
            return asset_ladder_history_payload(config, connection, symbol, range_key=range_key)

    @app.get("/api/ladder-chart/{symbol}")
    def ladder_chart(symbol: str, range_key: str = Query(default="SESSION")) -> dict[str, Any]:
        with runtime() as (config, connection):
            return asset_ladder_history_payload(config, connection, symbol, range_key=range_key)

    @app.get("/api/portfolio/trades")
    def portfolio_trades(limit: int = Query(default=50, ge=1, le=250)) -> dict[str, Any]:
        with runtime() as (_, connection):
            return {"items": fetch_trade_ledger(connection, limit=limit)}

    @app.get("/api/performance")
    def performance(
        range_key: str = Query(default="1D"),
        start_at: str | None = Query(default=None),
        end_at: str | None = Query(default=None),
    ) -> dict[str, Any]:
        with runtime() as (config, connection):
            end_dt = datetime.fromisoformat(end_at).astimezone(UTC) if end_at else datetime.now(UTC)
            effective_start_at = start_at or _history_start_at(range_key, end_dt)
            history = fetch_portfolio_value_history(
                connection,
                start_at=effective_start_at,
                end_at=end_dt.isoformat(timespec="seconds"),
                limit=5000,
            )
            return {
                "range_key": range_key,
                "history": history,
                "goal_tracking": fetch_goal_tracking(connection, config),
            }

    @app.get("/api/market/status")
    def market_status() -> dict[str, Any]:
        with runtime() as (config, _):
            rows = get_market_status(config)
            return {
                "items": rows,
                "summary": summarize_analysis_window(rows),
            }

    @app.get("/api/decision/latest")
    def decision_latest() -> dict[str, Any]:
        with runtime() as (config, connection):
            report = fetch_latest_decision_report(connection)
            next_report = estimate_next_decision_report(connection, config)
            return {"report": report, "next_report": next_report}

    @app.get("/api/decision/reports")
    def decision_reports(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        with runtime() as (_, connection):
            return {"items": fetch_recent_decision_reports(connection, limit=limit)}

    @app.get("/api/execution")
    def execution(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        with runtime() as (_, connection):
            orders = fetch_execution_orders(connection, limit=limit)
            return {
                "orders": orders,
                "fills": fetch_execution_fills(connection, limit=limit),
                "events": fetch_execution_events(connection, limit=limit),
            }

    @app.get("/api/scheduler")
    def scheduler(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        with runtime() as (_, connection):
            return {
                "status": fetch_scheduler_status(connection),
                "cycles": fetch_scheduler_cycles(connection, limit=limit),
            }

    def _run_action(func, *args, **kwargs) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/actions/decision-report")
    def action_generate_decision_report() -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(generate_decision_report, config=config, connection=connection)

    @app.post("/api/actions/queue-process")
    def action_queue_process() -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(queue_and_maybe_execute_latest_report, config=config, connection=connection)

    @app.post("/api/actions/sync-broker")
    def action_sync_broker() -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(sync_broker_order_statuses, config=config, connection=connection)

    @app.post("/api/actions/retry-failed")
    def action_retry_failed() -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(
                retry_failed_execution_orders,
                config=config,
                connection=connection,
                recoverable_only=True,
            )

    @app.post("/api/actions/reconcile-broker")
    def action_reconcile_broker() -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(reconcile_portfolio_to_broker, config=config, connection=connection)

    @app.post("/api/actions/scheduler-cycle")
    def action_scheduler_cycle(request: SchedulerCycleRequest) -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(
                run_manual_scheduler_cycle,
                config=config,
                connection=connection,
                mock=bool(request.mock),
            )

    @app.post("/api/orders/{order_id}/manage")
    def action_manage_order(order_id: int, request: LiveOrderActionRequest) -> dict[str, Any]:
        with runtime() as (config, connection):
            return _run_action(
                manage_live_order,
                order_id,
                management_action=request.action,
                config=config,
                connection=connection,
                new_quantity=request.quantity,
                new_price=request.price,
            )

    return app


app = create_app()
