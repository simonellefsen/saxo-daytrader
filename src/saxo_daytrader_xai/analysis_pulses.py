from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_EU_CLOSE_CODES = {"XCSE", "XSTO", "XOSL", "XHEL", "XLON", "XETR", "XFRA", "XMIL", "XAMS"}
DEFAULT_US_CLOSE_CODES = {"XNAS", "XNYS"}


def _swing_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("strategy", {}).get("swing", {})


def _pulse_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return _swing_cfg(config).get("analysis_pulses", {})


def _timezone(config: dict[str, Any]) -> ZoneInfo:
    timezone_name = str(_pulse_cfg(config).get("timezone") or "Europe/Copenhagen")
    return ZoneInfo(timezone_name)


def _parse_local_time(value: Any, default: str) -> time:
    raw = str(value or default).strip()
    hour_text, minute_text = raw.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def _due_window(config: dict[str, Any]) -> timedelta:
    minutes = int(_pulse_cfg(config).get("due_window_minutes", 20) or 20)
    return timedelta(minutes=max(minutes, 1))


def _pulse_key(kind: str, local_date: str) -> str:
    return f"{kind}:{local_date}"


def _pulse_row(
    *,
    kind: str,
    label: str,
    target_at: datetime,
    now: datetime,
    due_window: timedelta,
    source_markets: list[str],
) -> dict[str, Any]:
    target_utc = target_at.astimezone(UTC)
    window_end = target_utc + due_window
    local_target = target_at.isoformat(timespec="seconds")
    local_date = target_at.date().isoformat()
    return {
        "key": _pulse_key(kind, local_date),
        "kind": kind,
        "label": label,
        "target_at": local_target,
        "target_at_utc": target_utc.isoformat(timespec="seconds"),
        "window_end_at_utc": window_end.isoformat(timespec="seconds"),
        "due": target_utc <= now < window_end,
        "source_markets": source_markets,
    }


def _morning_pulse(config: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    cfg = _pulse_cfg(config).get("morning_macro", {})
    if not bool(cfg.get("enabled", True)):
        return None
    tz = _timezone(config)
    local_now = now.astimezone(tz)
    local_time = _parse_local_time(cfg.get("time"), "08:00")
    target_date = local_now.date()
    if target_date.weekday() >= 5:
        target_date = target_date + timedelta(days=7 - target_date.weekday())
    target_at = datetime.combine(target_date, local_time, tzinfo=tz)
    if now >= target_at.astimezone(UTC) + _due_window(config):
        target_at = target_at + timedelta(days=1)
        while target_at.weekday() >= 5:
            target_at = target_at + timedelta(days=1)
    return _pulse_row(
        kind="morning_macro",
        label="Morning Macro + Asia Pulse",
        target_at=target_at,
        now=now,
        due_window=_due_window(config),
        source_markets=["Shanghai", "Tokyo", "NSE India", "Hong Kong", "Shenzhen", "Taiwan"],
    )


def _close_pulse(
    config: dict[str, Any],
    *,
    now: datetime,
    market_status_rows: list[dict[str, Any]],
    cfg_key: str,
    kind: str,
    label: str,
    default_codes: set[str],
    default_minutes_before_close: int,
) -> dict[str, Any] | None:
    cfg = _pulse_cfg(config).get(cfg_key, {})
    if not bool(cfg.get("enabled", True)):
        return None
    codes = {str(code).upper() for code in cfg.get("exchange_codes", sorted(default_codes))}
    minutes_before_close = int(cfg.get("minutes_before_close", default_minutes_before_close) or default_minutes_before_close)
    candidates: list[tuple[datetime, str]] = []
    for row in market_status_rows:
        code = str(row.get("code") or "").upper()
        if code not in codes or not row.get("tradable_close_at_utc"):
            continue
        try:
            close_at = datetime.fromisoformat(str(row["tradable_close_at_utc"])).astimezone(UTC)
        except ValueError:
            continue
        target_at = close_at - timedelta(minutes=minutes_before_close)
        # Only build today's close pulses. If target already expired, the next pulse comes from
        # the next market-status refresh for the next trading session.
        if now < close_at + _due_window(config):
            candidates.append((target_at, str(row.get("market") or code)))
    if not candidates:
        return None
    target_at = min(target for target, _market in candidates)
    source_markets = sorted({market for target, market in candidates if target == target_at})
    return _pulse_row(
        kind=kind,
        label=label,
        target_at=target_at,
        now=now,
        due_window=_due_window(config),
        source_markets=source_markets,
    )


def analysis_pulse_status(
    config: dict[str, Any],
    market_status_rows: list[dict[str, Any]],
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    now = (reference_time or datetime.now(UTC)).astimezone(UTC)
    pulses = [
        _morning_pulse(config, now=now),
        _close_pulse(
            config,
            now=now,
            market_status_rows=market_status_rows,
            cfg_key="pre_eu_close",
            kind="pre_eu_close",
            label="Pre-EU/Nordic Close",
            default_codes=DEFAULT_EU_CLOSE_CODES,
            default_minutes_before_close=120,
        ),
        _close_pulse(
            config,
            now=now,
            market_status_rows=market_status_rows,
            cfg_key="pre_us_close",
            kind="pre_us_close",
            label="Pre-US Close Final Assessment",
            default_codes=DEFAULT_US_CLOSE_CODES,
            default_minutes_before_close=60,
        ),
    ]
    active_pulses = [pulse for pulse in pulses if pulse and bool(pulse["due"])]
    future_pulses = [
        pulse for pulse in pulses
        if pulse and datetime.fromisoformat(str(pulse["target_at_utc"])) > now
    ]
    next_pulse = min(
        future_pulses,
        key=lambda pulse: datetime.fromisoformat(str(pulse["target_at_utc"])),
        default=None,
    )
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": str(_timezone(config).key),
        "due": bool(active_pulses),
        "active_pulses": active_pulses,
        "pulses": [pulse for pulse in pulses if pulse is not None],
        "next_pulse_at": next_pulse["target_at_utc"] if next_pulse else None,
        "next_pulse_label": next_pulse["label"] if next_pulse else None,
    }
