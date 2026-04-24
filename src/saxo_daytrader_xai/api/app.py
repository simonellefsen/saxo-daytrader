from __future__ import annotations

import os
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
from saxo_daytrader_xai.xai_decision import fetch_latest_decision_report, generate_decision_report


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
                },
            }

    @app.get("/api/portfolio/positions")
    def portfolio_positions(limit: int = Query(default=25, ge=1, le=250)) -> dict[str, Any]:
        with runtime() as (config, connection):
            kwargs = portfolio_kwargs(config)
            positions = fetch_portfolio_positions(connection, **kwargs)
            return {"items": positions[:limit], "total": len(positions)}

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
        with runtime() as (_, connection):
            report = fetch_latest_decision_report(connection)
            return {"report": report}

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
