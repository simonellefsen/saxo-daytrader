from __future__ import annotations

import json
from calendar import monthrange
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from saxo_daytrader_xai.market_benchmarks import fetch_benchmark_index_snapshot
from saxo_daytrader_xai.portfolio import fetch_goal_tracking


def _journal_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("strategy", {}).get("swing", {}).get("journal", {})


def _timezone(config: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo(str(_journal_cfg(config).get("timezone") or "Europe/Copenhagen"))


def _parse_time(value: Any, default: str) -> time:
    raw = str(value or default)
    hour_text, minute_text = raw.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def _journal_exists(connection, *, journal_date: str, cadence: str) -> bool:
    row = connection.execute(
        """
        SELECT id
        FROM strategy_journal_entries
        WHERE journal_date = ?
          AND cadence = ?
        LIMIT 1
        """,
        (journal_date, cadence),
    ).fetchone()
    return row is not None


def fetch_recent_journal_learnings(connection, limit: int = 6) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM strategy_journal_entries
        ORDER BY journal_date DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metrics_json"] = json.loads(item["metrics_json"]) if item.get("metrics_json") else {}
        item["learnings_json"] = json.loads(item["learnings_json"]) if item.get("learnings_json") else []
        output.append(item)
    return output


def fetch_strategy_journal_entries(connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM strategy_journal_entries
        ORDER BY journal_date DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metrics_json"] = json.loads(item["metrics_json"]) if item.get("metrics_json") else {}
        item["learnings_json"] = json.loads(item["learnings_json"]) if item.get("learnings_json") else []
        output.append(item)
    return output


def _decision_metrics(connection, config: dict[str, Any], *, since_date: str, reference_time: datetime | None = None) -> dict[str, Any]:
    report_rows = connection.execute(
        """
        SELECT id, report_json
        FROM decision_reports
        WHERE report_date >= ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (since_date,),
    ).fetchall()
    suggested_trades = 0
    swing_orders = 0
    strategy_statuses: list[str] = []
    source_report_id = None
    for row in report_rows:
        source_report_id = source_report_id or int(row["id"])
        report_json = json.loads(row["report_json"]) if row.get("report_json") else {}
        suggested_trades += len(report_json.get("suggested_trades") or [])
        strategy_plan = dict(report_json.get("strategy_plan") or {})
        swing_orders += len(strategy_plan.get("swing_orders") or [])
        if strategy_plan.get("status"):
            strategy_statuses.append(str(strategy_plan["status"]))
    execution_rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM execution_orders
        WHERE created_at >= ?
        GROUP BY status
        """,
        (since_date,),
    ).fetchall()
    ledger_row = connection.execute(
        """
        SELECT
            COUNT(*) AS trade_count,
            COALESCE(SUM(realised_gain_dkk), 0) AS realised_gain_dkk
        FROM trade_ledger
        WHERE created_at >= ?
        """,
        (since_date,),
    ).fetchone()
    goal_tracking = fetch_goal_tracking(connection, config, reference_time=reference_time)
    benchmark_indices = fetch_benchmark_index_snapshot(
        config,
        timeout_seconds=int(config.get("market_data", {}).get("request_timeout_seconds", 10) or 10),
    )
    return {
        "report_count": len(report_rows),
        "suggested_trade_count": suggested_trades,
        "swing_order_count": swing_orders,
        "strategy_statuses": strategy_statuses[:5],
        "execution_status_counts": {str(row["status"]): int(row["count"]) for row in execution_rows},
        "trade_count": int(ledger_row["trade_count"] if ledger_row else 0),
        "realised_gain_dkk": float(ledger_row["realised_gain_dkk"] if ledger_row else 0.0),
        "goal_tracking": goal_tracking,
        "benchmark_indices": benchmark_indices,
        "source_report_id": source_report_id,
    }


def _learning_points(metrics: dict[str, Any]) -> list[str]:
    learnings: list[str] = []
    if metrics["report_count"] == 0:
        learnings.append("No decision reports were available for this journal period; keep the next analysis conservative.")
    if metrics["suggested_trade_count"] == 0:
        learnings.append("No high-conviction trades were suggested; market or portfolio constraints likely dominated.")
    if metrics["swing_order_count"] > 0:
        learnings.append("Review each swing order against daily confluence count and tomorrow-morning ownership quality.")
    if metrics["trade_count"] == 0:
        learnings.append("No closed trades were available for expectancy scoring; defer performance conclusions.")
    elif metrics["realised_gain_dkk"] < 0:
        learnings.append("Closed trades were net negative; inspect whether stop discipline or entry confluence failed.")
    else:
        learnings.append("Closed trades were non-negative; preserve the setup tags that worked.")
    goal_week = (metrics.get("goal_tracking") or {}).get("periods", {}).get("week", {})
    if goal_week:
        learnings.append(
            f"Weekly goal progress is {float(goal_week.get('pnl_dkk') or 0.0):.0f} DKK "
            f"versus {float(goal_week.get('target_dkk') or 0.0):.0f} DKK target-to-date."
        )
    benchmarks = (metrics.get("benchmark_indices") or {}).get("regions", {})
    if benchmarks:
        strongest = sorted(
            (
                (region, payload.get("average_change_pct"))
                for region, payload in benchmarks.items()
                if payload.get("average_change_pct") is not None
            ),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        if strongest:
            learnings.append(
                f"Benchmark context: strongest region was {strongest[0][0]} "
                f"at {float(strongest[0][1]) * 100:.2f}% average index move."
            )
    return learnings


def record_strategy_journal_entry(
    connection,
    *,
    journal_date: str,
    cadence: str,
    status: str,
    summary: str,
    metrics: dict[str, Any],
    learnings: list[str],
    source_report_id: int | None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO strategy_journal_entries (
            created_at, journal_date, cadence, status, summary,
            metrics_json, learnings_json, source_report_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(timespec="seconds"),
            journal_date,
            cadence,
            status,
            summary,
            json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            json.dumps(learnings, ensure_ascii=False, sort_keys=True),
            source_report_id,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def generate_strategy_journal_entry(
    connection,
    *,
    config: dict[str, Any],
    cadence: str,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    now = (reference_time or datetime.now(UTC)).astimezone(_timezone(config))
    journal_date = now.date().isoformat()
    if _journal_exists(connection, journal_date=journal_date, cadence=cadence):
        return {"status": "skipped", "reason": f"{cadence} journal already exists for {journal_date}"}
    metrics = _decision_metrics(connection, config, since_date=journal_date, reference_time=now)
    learnings = _learning_points(metrics)
    week = metrics.get("goal_tracking", {}).get("periods", {}).get("week", {})
    month = metrics.get("goal_tracking", {}).get("periods", {}).get("month", {})
    summary = (
        f"{cadence.title()} strategy journal: {metrics['report_count']} report(s), "
        f"{metrics['suggested_trade_count']} suggested trade(s), "
        f"{metrics['swing_order_count']} swing order(s), "
        f"{metrics['trade_count']} closed trade(s). "
        f"Week {float(week.get('pnl_dkk') or 0.0):.0f}/{float(week.get('target_dkk') or 0.0):.0f} DKK, "
        f"month {float(month.get('pnl_dkk') or 0.0):.0f}/{float(month.get('target_dkk') or 0.0):.0f} DKK before tax."
    )
    entry_id = record_strategy_journal_entry(
        connection,
        journal_date=journal_date,
        cadence=cadence,
        status="completed",
        summary=summary,
        metrics=metrics,
        learnings=learnings,
        source_report_id=metrics.get("source_report_id"),
    )
    return {"status": "completed", "id": entry_id, "cadence": cadence, "journal_date": journal_date}


def generate_due_strategy_journals(
    connection,
    config: dict[str, Any],
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    cfg = _journal_cfg(config)
    if not bool(cfg.get("enabled", True)):
        return {"status": "disabled", "entries": []}
    now = (reference_time or datetime.now(UTC)).astimezone(_timezone(config))
    entries: list[dict[str, Any]] = []
    daily_time = _parse_time(cfg.get("daily_time"), "22:30")
    if now.time() >= daily_time:
        entries.append(generate_strategy_journal_entry(connection, config=config, cadence="daily", reference_time=now))
    weekly_time = _parse_time(cfg.get("weekly_time"), "20:00")
    weekly_day = int(cfg.get("weekly_weekday", 6) or 6)
    if now.weekday() == weekly_day and now.time() >= weekly_time:
        entries.append(generate_strategy_journal_entry(connection, config=config, cadence="weekly", reference_time=now))
    monthly_time = _parse_time(cfg.get("monthly_time"), "22:45")
    if now.day == monthrange(now.year, now.month)[1] and now.time() >= monthly_time:
        entries.append(generate_strategy_journal_entry(connection, config=config, cadence="monthly", reference_time=now))
    return {"status": "ok", "entries": entries}
