from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytz


@dataclass(frozen=True)
class ExchangeSchedule:
    code: str
    name: str
    timezone: str
    open_time: time
    close_time: time


DEFAULT_EXCHANGES: list[ExchangeSchedule] = [
    ExchangeSchedule("XCSE", "Copenhagen", "Europe/Copenhagen", time(9, 0), time(17, 0)),
    ExchangeSchedule("XLON", "London", "Europe/London", time(8, 0), time(16, 30)),
    ExchangeSchedule("XETR", "Frankfurt / Xetra", "Europe/Berlin", time(9, 0), time(17, 30)),
    ExchangeSchedule("XNAS", "Nasdaq US", "America/New_York", time(9, 30), time(16, 0)),
    ExchangeSchedule("XNYS", "NYSE", "America/New_York", time(9, 30), time(16, 0)),
    ExchangeSchedule("XSTO", "Stockholm", "Europe/Stockholm", time(9, 0), time(17, 30)),
    ExchangeSchedule("XOSL", "Oslo", "Europe/Oslo", time(9, 0), time(16, 30)),
    ExchangeSchedule("XHEL", "Helsinki", "Europe/Helsinki", time(10, 0), time(18, 30)),
    ExchangeSchedule("XMIL", "Milan", "Europe/Rome", time(9, 0), time(17, 30)),
]


def _next_open(local_now: datetime, exchange: ExchangeSchedule) -> datetime:
    candidate = local_now
    while True:
        if candidate.weekday() < 5:
            next_open = candidate.replace(
                hour=exchange.open_time.hour,
                minute=exchange.open_time.minute,
                second=0,
                microsecond=0,
            )
            if local_now < next_open:
                return next_open
        candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def get_market_status(config: dict[str, Any], reference_time: datetime | None = None) -> list[dict[str, Any]]:
    now_utc = (reference_time or datetime.now(UTC)).astimezone(UTC)
    offset_minutes = int(config["analysis_windows"]["offset_minutes_after_open"])
    duration_minutes = int(config["analysis_windows"]["duration_minutes"])
    rows: list[dict[str, Any]] = []

    for exchange in DEFAULT_EXCHANGES:
        tz = pytz.timezone(exchange.timezone)
        local_now = now_utc.astimezone(tz)
        open_dt = local_now.replace(
            hour=exchange.open_time.hour,
            minute=exchange.open_time.minute,
            second=0,
            microsecond=0,
        )
        close_dt = local_now.replace(
            hour=exchange.close_time.hour,
            minute=exchange.close_time.minute,
            second=0,
            microsecond=0,
        )
        is_weekday = local_now.weekday() < 5
        is_open = is_weekday and open_dt <= local_now <= close_dt
        analysis_start = open_dt + timedelta(minutes=offset_minutes)
        analysis_end = analysis_start + timedelta(minutes=duration_minutes)
        analysis_window_active = is_weekday and analysis_start <= local_now <= analysis_end
        next_open = _next_open(local_now, exchange)
        rows.append(
            {
                "code": exchange.code,
                "market": exchange.name,
                "timezone": exchange.timezone,
                "local_time": local_now.strftime("%Y-%m-%d %H:%M"),
                "is_open": is_open,
                "analysis_window_active": analysis_window_active,
                "analysis_window_start": analysis_start.strftime("%Y-%m-%d %H:%M"),
                "analysis_window_end": analysis_end.strftime("%Y-%m-%d %H:%M"),
                "next_open": next_open.strftime("%Y-%m-%d %H:%M"),
            }
        )
    return rows


def summarize_analysis_window(status_rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_markets = [row["market"] for row in status_rows if row["analysis_window_active"]]
    return {
        "analysis_window_active": bool(active_markets),
        "active_markets": active_markets,
    }
