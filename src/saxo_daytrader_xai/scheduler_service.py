from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import append_audit_log, connect, init_db
from saxo_daytrader_xai.execution_engine import queue_and_maybe_execute_latest_report
from saxo_daytrader_xai.market_schedule import get_market_status, refresh_market_calendars, summarize_analysis_window
from saxo_daytrader_xai.xai_decision import generate_decision_report, should_auto_run_decision_report


def _resolve_config(config: dict[str, Any] | None, config_path: str | Path) -> dict[str, Any]:
    if config is not None:
        return config
    return load_config(config_path)


def run_scheduler_cycle(
    *,
    config: dict[str, Any] | None = None,
    config_path: str | Path = "config.yaml",
    connection=None,
    force_mock: bool = False,
    force_decision: bool = False,
) -> dict[str, Any]:
    resolved_config = _resolve_config(config, config_path)
    resolved_connection = connection or connect(resolved_config["portfolio"]["database_path"])
    init_db(resolved_connection)
    should_close = connection is None

    try:
        calendar_refresh = refresh_market_calendars(resolved_config)
        market_status = get_market_status(resolved_config)
        analysis_summary = summarize_analysis_window(market_status)
        should_generate = force_decision or should_auto_run_decision_report(
            resolved_connection,
            resolved_config,
            analysis_summary["analysis_window_active"],
        )

        decision_result = None
        if should_generate:
            decision_result = generate_decision_report(
                config=resolved_config,
                connection=resolved_connection,
                force_mock=force_mock,
            )

        queue_result = queue_and_maybe_execute_latest_report(
            config=resolved_config,
            connection=resolved_connection,
        )

        outcome = {
            "status": "ok",
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "calendar_refresh": calendar_refresh,
            "analysis_window_active": analysis_summary["analysis_window_active"],
            "active_markets": analysis_summary["active_markets"],
            "generated_decision": decision_result is not None,
            "decision": decision_result,
            "queue": queue_result,
        }
        append_audit_log(resolved_connection, "scheduler_cycle_completed", outcome)
        return outcome
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "failed",
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "error": str(exc),
        }
        append_audit_log(resolved_connection, "scheduler_cycle_failed", payload)
        return payload
    finally:
        if should_close:
            resolved_connection.close()


def run_scheduler_forever(
    *,
    config_path: str | Path = "config.yaml",
    force_mock: bool = False,
) -> None:
    resolved_config = load_config(config_path)
    interval_minutes = int(resolved_config["scheduler"]["poll_interval_minutes"])
    scheduler = BlockingScheduler(timezone="Europe/Copenhagen")

    scheduler.add_job(
        run_scheduler_cycle,
        "interval",
        minutes=interval_minutes,
        max_instances=1,
        coalesce=True,
        kwargs={
            "config_path": str(config_path),
            "force_mock": force_mock,
        },
    )

    if resolved_config["scheduler"].get("startup_run", True):
        result = run_scheduler_cycle(config_path=config_path, force_mock=force_mock)
        print(result)

    print(
        f"Scheduler started. Poll interval={interval_minutes} minutes, "
        f"force_mock={force_mock}. Press Ctrl+C to stop."
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        time.sleep(0.1)
