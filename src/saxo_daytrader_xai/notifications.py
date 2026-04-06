from __future__ import annotations

import json
import smtplib
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from typing import Any

import pytz
import requests

from saxo_daytrader_xai.db import append_audit_log
from saxo_daytrader_xai.portfolio import (
    fetch_latest_batch_id,
    fetch_portfolio_positions,
    fetch_portfolio_summary,
    fetch_realised_tax_summary,
)
from saxo_daytrader_xai.xai_decision import fetch_latest_decision_report


def _notification_timezone(config: dict[str, Any]):
    return pytz.timezone(config.get("notifications", {}).get("timezone", "Europe/Copenhagen"))


def _notification_now(config: dict[str, Any], reference_time: datetime | None = None) -> datetime:
    now_utc = (reference_time or datetime.now(UTC)).astimezone(UTC)
    return now_utc.astimezone(_notification_timezone(config))


def _day_bounds_utc(config: dict[str, Any], summary_date: date) -> tuple[str, str]:
    timezone = _notification_timezone(config)
    local_start = timezone.localize(datetime.combine(summary_date, time(0, 0)), is_dst=None)
    local_end = timezone.localize(datetime.combine(summary_date, time(23, 59, 59)), is_dst=None)
    return (
        local_start.astimezone(UTC).isoformat(timespec="seconds"),
        local_end.astimezone(UTC).isoformat(timespec="seconds"),
    )


def _period_bounds_utc(config: dict[str, Any], start_date: date, end_date: date) -> tuple[str, str]:
    timezone = _notification_timezone(config)
    local_start = timezone.localize(datetime.combine(start_date, time(0, 0)), is_dst=None)
    local_end = timezone.localize(datetime.combine(end_date, time(23, 59, 59)), is_dst=None)
    return (
        local_start.astimezone(UTC).isoformat(timespec="seconds"),
        local_end.astimezone(UTC).isoformat(timespec="seconds"),
    )


def _summary_trade_stats(connection, config: dict[str, Any], start_date: date, end_date: date) -> dict[str, Any]:
    start_utc, end_utc = _period_bounds_utc(config, start_date, end_date)
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS trade_count,
            COALESCE(SUM(net_amount_dkk), 0) AS net_amount_dkk,
            COALESCE(SUM(realised_gain_dkk), 0) AS realised_gain_dkk,
            COALESCE(SUM(tax_dkk), 0) AS tax_dkk,
            COALESCE(SUM(commission_dkk), 0) AS commission_dkk
        FROM trade_ledger
        WHERE created_at >= ? AND created_at <= ?
        """,
        (start_utc, end_utc),
    ).fetchone()
    return dict(row)


def _summary_execution_stats(connection, config: dict[str, Any], start_date: date, end_date: date) -> dict[str, Any]:
    start_utc, end_utc = _period_bounds_utc(config, start_date, end_date)
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count_rows
        FROM execution_orders
        WHERE created_at >= ? AND created_at <= ?
        GROUP BY status
        ORDER BY status
        """,
        (start_utc, end_utc),
    ).fetchall()
    return {row["status"]: row["count_rows"] for row in rows}


def _summary_top_positions(connection) -> list[dict[str, Any]]:
    batch_id = fetch_latest_batch_id(connection)
    positions = fetch_portfolio_positions(connection, batch_id=batch_id)
    return positions[:5]


def _period_descriptor(kind: str, local_now: datetime, config: dict[str, Any]) -> tuple[date, date, str]:
    current_date = local_now.date()
    if kind == "daily":
        start_date = current_date
        end_date = current_date
        label = current_date.isoformat()
    elif kind == "weekly":
        end_date = current_date - timedelta(days=current_date.weekday() + 1)
        start_date = end_date - timedelta(days=6)
        label = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    elif kind == "monthly":
        first_of_current_month = current_date.replace(day=1)
        end_date = first_of_current_month - timedelta(days=1)
        start_date = end_date.replace(day=1)
        label = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    else:
        raise ValueError(f"Unsupported summary kind '{kind}'")
    return start_date, end_date, label


def build_summary(
    connection,
    config: dict[str, Any],
    *,
    summary_kind: str = "daily",
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    local_now = _notification_now(config, reference_time)
    start_date, end_date, summary_label = _period_descriptor(summary_kind, local_now, config)
    batch_id = fetch_latest_batch_id(connection)
    portfolio_summary = fetch_portfolio_summary(connection, batch_id=batch_id)
    tax_summary = fetch_realised_tax_summary(connection, tax_year=end_date.year)
    trade_stats = _summary_trade_stats(connection, config, start_date, end_date)
    execution_stats = _summary_execution_stats(connection, config, start_date, end_date)
    latest_report = fetch_latest_decision_report(connection)
    top_positions = _summary_top_positions(connection)

    suggested_trade_count = 0
    if latest_report and latest_report.get("report_json"):
        suggested_trade_count = len(latest_report["report_json"].get("suggested_trades", []))

    payload = {
        "summary_kind": summary_kind,
        "summary_date": summary_label,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "generated_at_local": local_now.isoformat(timespec="seconds"),
        "portfolio": portfolio_summary,
        "period": {
            "trade_count": int(trade_stats["trade_count"]),
            "net_amount_dkk": float(trade_stats["net_amount_dkk"]),
            "realised_gain_dkk": float(trade_stats["realised_gain_dkk"]),
            "tax_dkk": float(trade_stats["tax_dkk"]),
            "commission_dkk": float(trade_stats["commission_dkk"]),
            "execution_status_counts": execution_stats,
        },
        "year_to_date_tax": tax_summary,
        "latest_decision_report": {
            "created_at": latest_report.get("created_at") if latest_report else None,
            "status": latest_report.get("status") if latest_report else None,
            "model": latest_report.get("model") if latest_report else None,
            "suggested_trade_count": suggested_trade_count,
        },
        "top_positions": [
            {
                "symbol": row["symbol"],
                "market_value_dkk": row["market_value_dkk"],
                "allocation_pct": row["allocation_pct"],
                "daily_pnl_dkk": row["daily_pnl_dkk"],
            }
            for row in top_positions
        ],
    }
    subject = f"saxo-daytrader-xai {summary_kind} summary {summary_label}"
    style = str(config.get("notifications", {}).get("summary_style", "structured")).lower()
    if style == "compact":
        lines = [
            subject,
            f"Portfolio {portfolio_summary['total_market_value_dkk']:.2f} DKK | Daily P/L {portfolio_summary['total_daily_pnl_dkk']:.2f} DKK | Trades {int(trade_stats['trade_count'])}",
            f"Realised {float(trade_stats['realised_gain_dkk']):.2f} DKK | Tax {float(trade_stats['tax_dkk']):.2f} DKK | Commission {float(trade_stats['commission_dkk']):.2f} DKK",
            f"Decision report {payload['latest_decision_report']['status'] or 'none'} | Suggested trades {suggested_trade_count}",
        ]
    else:
        lines = [
            subject,
            "",
            f"Period: {start_date.isoformat()} to {end_date.isoformat()}",
            "",
            "Portfolio:",
            f"- Value: {portfolio_summary['total_market_value_dkk']:.2f} DKK",
            f"- Daily P/L: {portfolio_summary['total_daily_pnl_dkk']:.2f} DKK",
            f"- Unrealised P/L: {portfolio_summary['total_unrealised_pnl_dkk']:.2f} DKK",
            "",
            "Trading:",
            f"- Trades: {int(trade_stats['trade_count'])}",
            f"- Realised gain: {float(trade_stats['realised_gain_dkk']):.2f} DKK",
            f"- Net amount: {float(trade_stats['net_amount_dkk']):.2f} DKK",
            f"- Tax: {float(trade_stats['tax_dkk']):.2f} DKK",
            f"- Commission: {float(trade_stats['commission_dkk']):.2f} DKK",
            "",
            "Decision Engine:",
            f"- Latest report: {payload['latest_decision_report']['status'] or 'none'}",
            f"- Suggested trades: {suggested_trade_count}",
        ]
        if execution_stats:
            lines.append("- Execution status counts:")
            lines.extend(f"  - {status}: {count}" for status, count in sorted(execution_stats.items()))
        if top_positions:
            lines.extend(
                [
                    "",
                    "Top Positions:",
                    *[
                        f"- {row['symbol']}: {float(row['market_value_dkk']):.2f} DKK ({float(row['allocation_pct']) * 100:.2f}%), daily {float(row['daily_pnl_dkk'] or 0):.2f} DKK"
                        for row in top_positions[:3]
                    ],
                ]
            )
    return {
        "summary_date": payload["summary_date"],
        "subject": subject,
        "message_text": "\n".join(lines),
        "payload": payload,
    }


def build_daily_summary(connection, config: dict[str, Any], reference_time: datetime | None = None) -> dict[str, Any]:
    return build_summary(connection, config, summary_kind="daily", reference_time=reference_time)


def _record_notification_delivery(
    connection,
    *,
    summary_date: str,
    summary_kind: str,
    channel: str,
    status: str,
    subject: str,
    message_text: str,
    payload: dict[str, Any],
    error_text: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO notification_deliveries (
            created_at, summary_date, summary_kind, channel, status, subject, message_text, payload_json, error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(timespec="seconds"),
            summary_date,
            summary_kind,
            channel,
            status,
            subject,
            message_text,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            error_text,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def _already_sent(connection, summary_date: str, summary_kind: str, channel: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM notification_deliveries
        WHERE summary_date = ? AND summary_kind = ? AND channel = ? AND status = 'sent'
        ORDER BY id DESC
        LIMIT 1
        """,
        (summary_date, summary_kind, channel),
    ).fetchone()
    return row is not None


def _state_key(summary_kind: str, channel: str) -> str:
    return f"{summary_kind}:{channel}"


def _notification_state(connection, summary_kind: str, channel: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
        FROM notification_channel_state
        WHERE channel = ?
        """,
        (_state_key(summary_kind, channel),),
    ).fetchone()
    return dict(row) if row else None


def _upsert_notification_state(
    connection,
    *,
    summary_kind: str,
    channel: str,
    summary_date: str,
    last_attempt_at: str,
    next_attempt_after: str | None,
    attempt_count: int,
    last_status: str,
    last_error_text: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO notification_channel_state (
            channel, summary_date, last_attempt_at, next_attempt_after, attempt_count, last_status, last_error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel) DO UPDATE SET
            summary_date = excluded.summary_date,
            last_attempt_at = excluded.last_attempt_at,
            next_attempt_after = excluded.next_attempt_after,
            attempt_count = excluded.attempt_count,
            last_status = excluded.last_status,
            last_error_text = excluded.last_error_text
        """,
        (
            _state_key(summary_kind, channel),
            summary_date,
            last_attempt_at,
            next_attempt_after,
            attempt_count,
            last_status,
            last_error_text,
        ),
    )
    connection.commit()


def _channel_ready(
    connection,
    config: dict[str, Any],
    *,
    summary_kind: str,
    channel: str,
    summary_date: str,
    reference_time: datetime,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if _already_sent(connection, summary_date, summary_kind, channel):
        return False, "already_sent"
    state = _notification_state(connection, summary_kind, channel)
    if not state or state.get("summary_date") != summary_date:
        return True, "fresh"
    max_attempts = int(config.get("notifications", {}).get("max_attempts_per_day", 3))
    if int(state.get("attempt_count") or 0) >= max_attempts:
        return False, "max_attempts_reached"
    next_attempt_after = state.get("next_attempt_after")
    if next_attempt_after:
        next_dt = datetime.fromisoformat(str(next_attempt_after))
        if reference_time < next_dt:
            return False, "backoff_active"
    cooldown_minutes = int(config.get("notifications", {}).get("channel_cooldown_minutes", 240))
    last_attempt_at = state.get("last_attempt_at")
    if last_attempt_at and state.get("last_status") == "sent":
        last_dt = datetime.fromisoformat(str(last_attempt_at))
        if reference_time < last_dt + timedelta(minutes=cooldown_minutes):
            return False, "cooldown_active"
    return True, "ready"


def _send_slack(config: dict[str, Any], subject: str, message_text: str, payload: dict[str, Any]) -> dict[str, Any]:
    webhook_url = config.get("notifications", {}).get("slack", {}).get("webhook_url")
    if not webhook_url:
        raise ValueError("Slack webhook URL is missing")
    response = requests.post(
        webhook_url,
        json={
            "text": f"*{subject}*\n```{message_text}```",
            "metadata": {"event_type": "daily_summary", "event_payload": payload},
        },
        timeout=20,
    )
    response.raise_for_status()
    return {"status_code": response.status_code}


def _send_email(config: dict[str, Any], subject: str, message_text: str) -> dict[str, Any]:
    email_cfg = config.get("notifications", {}).get("email", {})
    host = str(email_cfg.get("smtp_host") or "")
    if not host:
        raise ValueError("SMTP host is missing")
    port = int(email_cfg.get("smtp_port") or 587)
    username = str(email_cfg.get("username") or "")
    password = str(email_cfg.get("password") or "")
    from_address = str(email_cfg.get("from_address") or "")
    to_addresses = [
        part.strip()
        for part in str(email_cfg.get("to_addresses_csv") or "").split(",")
        if part.strip()
    ]
    if not from_address or not to_addresses:
        raise ValueError("SMTP from/to addresses are missing")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = ", ".join(to_addresses)
    message.set_content(message_text)

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if bool(email_cfg.get("use_starttls", True)):
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
    return {"to": to_addresses}


def fetch_notification_deliveries(connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM notification_deliveries
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["payload_json"] = json.loads(record["payload_json"]) if record.get("payload_json") else None
        output.append(record)
    return output


def _summary_due(config: dict[str, Any], summary_kind: str, local_now: datetime, *, force: bool) -> bool:
    if force:
        return True
    notifications_cfg = config.get("notifications", {})
    if summary_kind == "daily":
        return bool(notifications_cfg.get("daily_summary_enabled", False))
    if summary_kind == "weekly":
        return bool(notifications_cfg.get("weekly_summary_enabled", False)) and local_now.weekday() == int(
            notifications_cfg.get("weekly_dispatch_weekday_local", 0)
        )
    if summary_kind == "monthly":
        return bool(notifications_cfg.get("monthly_summary_enabled", False)) and local_now.day == int(
            notifications_cfg.get("monthly_dispatch_day_local", 1)
        )
    return False


def dispatch_summary_if_due(
    connection,
    config: dict[str, Any],
    *,
    summary_kind: str = "daily",
    reference_time: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    notifications_cfg = config.get("notifications", {})
    local_now = _notification_now(config, reference_time)
    if not _summary_due(config, summary_kind, local_now, force=force):
        return {"status": "disabled" if summary_kind == "daily" and not notifications_cfg.get("daily_summary_enabled", False) and not force else "not_due", "sent": [], "summary_kind": summary_kind}

    dispatch_time = local_now.replace(
        hour=int(notifications_cfg.get("dispatch_hour_local", 18)),
        minute=int(notifications_cfg.get("dispatch_minute_local", 15)),
        second=0,
        microsecond=0,
    )
    if not force and local_now < dispatch_time:
        return {"status": "not_due", "sent": [], "summary_kind": summary_kind}

    summary = build_summary(connection, config, summary_kind=summary_kind, reference_time=reference_time)
    now_utc = (reference_time or datetime.now(UTC)).astimezone(UTC)
    channels: list[str] = []
    if notifications_cfg.get("slack", {}).get("enabled"):
        channels.append("slack")
    if notifications_cfg.get("email", {}).get("enabled"):
        channels.append("email")
    if not channels:
        channels.append("audit_log")

    sent: list[dict[str, Any]] = []
    for channel in channels:
        is_ready, reason = _channel_ready(
            connection,
            config,
            summary_kind=summary_kind,
            channel=channel,
            summary_date=summary["summary_date"],
            reference_time=now_utc,
            force=force,
        )
        if not is_ready:
            sent.append({"channel": channel, "status": "skipped", "reason": reason})
            continue
        previous_state = _notification_state(connection, summary_kind, channel) or {}
        attempt_count = int(previous_state.get("attempt_count") or 0) + 1
        try:
            if channel == "slack":
                delivery_meta = _send_slack(config, summary["subject"], summary["message_text"], summary["payload"])
            elif channel == "email":
                delivery_meta = _send_email(config, summary["subject"], summary["message_text"])
            else:
                delivery_meta = {"status": "stored_only"}
            delivery_id = _record_notification_delivery(
                connection,
                summary_date=summary["summary_date"],
                summary_kind=summary_kind,
                channel=channel,
                status="sent",
                subject=summary["subject"],
                message_text=summary["message_text"],
                payload={**summary["payload"], "delivery_meta": delivery_meta},
            )
            _upsert_notification_state(
                connection,
                summary_kind=summary_kind,
                channel=channel,
                summary_date=summary["summary_date"],
                last_attempt_at=now_utc.isoformat(timespec="seconds"),
                next_attempt_after=None,
                attempt_count=attempt_count,
                last_status="sent",
                last_error_text=None,
            )
            append_audit_log(
                connection,
                "daily_summary_sent",
                {"summary_date": summary["summary_date"], "summary_kind": summary_kind, "channel": channel, "delivery_id": delivery_id},
            )
            sent.append({"channel": channel, "status": "sent", "delivery_id": delivery_id})
        except Exception as exc:  # noqa: BLE001
            delivery_id = _record_notification_delivery(
                connection,
                summary_date=summary["summary_date"],
                summary_kind=summary_kind,
                channel=channel,
                status="failed",
                subject=summary["subject"],
                message_text=summary["message_text"],
                payload=summary["payload"],
                error_text=str(exc),
            )
            next_attempt = now_utc + timedelta(minutes=int(config.get("notifications", {}).get("retry_backoff_minutes", 30)))
            _upsert_notification_state(
                connection,
                summary_kind=summary_kind,
                channel=channel,
                summary_date=summary["summary_date"],
                last_attempt_at=now_utc.isoformat(timespec="seconds"),
                next_attempt_after=next_attempt.isoformat(timespec="seconds"),
                attempt_count=attempt_count,
                last_status="failed",
                last_error_text=str(exc),
            )
            append_audit_log(
                connection,
                "daily_summary_failed",
                {
                    "summary_date": summary["summary_date"],
                    "summary_kind": summary_kind,
                    "channel": channel,
                    "delivery_id": delivery_id,
                    "error": str(exc),
                },
            )
            sent.append({"channel": channel, "status": "failed", "delivery_id": delivery_id, "error": str(exc)})

    return {"status": "ok", "sent": sent, "summary": summary, "summary_kind": summary_kind}


def dispatch_summaries_if_due(
    connection,
    config: dict[str, Any],
    reference_time: datetime | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    results = []
    for summary_kind in ("daily", "weekly", "monthly"):
        results.append(
            dispatch_summary_if_due(
                connection,
                config,
                summary_kind=summary_kind,
                reference_time=reference_time,
                force=force,
            )
        )
    return {"status": "ok", "results": results}


def dispatch_daily_summary_if_due(
    connection,
    config: dict[str, Any],
    reference_time: datetime | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    return dispatch_summary_if_due(
        connection,
        config,
        summary_kind="daily",
        reference_time=reference_time,
        force=force,
    )
