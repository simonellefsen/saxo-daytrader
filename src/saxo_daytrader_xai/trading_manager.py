from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import (
    append_audit_log,
    connect,
    has_trading_manager_run,
    init_db,
    record_trading_manager_run,
)
from saxo_daytrader_xai.execution_engine import queue_and_maybe_execute_latest_report
from saxo_daytrader_xai.market_schedule import get_market_status
from saxo_daytrader_xai.market_symbols import parse_exchange_code
from saxo_daytrader_xai.portfolio import fetch_goal_tracking, fetch_latest_batch_id, fetch_portfolio_positions
from saxo_daytrader_xai.swing_indicators import fetch_daily_swing_indicators
from saxo_daytrader_xai.watchlists import build_watchlists
from saxo_daytrader_xai.xai_decision import fetch_latest_decision_report


DEFAULT_EU_CODES = {"XCSE", "XSTO", "XOSL", "XHEL", "XLON", "XETR", "XAMS", "XMIL"}
DEFAULT_US_CODES = {"XNAS", "XNYS"}


TRADING_MANAGER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "approved_orders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "strategy_key": {"type": "string"},
                    "symbol": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "risk_notes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["strategy_key", "symbol", "approve", "confidence", "rationale", "risk_notes"],
                "additionalProperties": False,
            },
        },
        "execution_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "approved_orders", "execution_notes"],
    "additionalProperties": False,
}


def _load_default_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return load_config(root / "config.yaml")


def _manager_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("strategy", {}).get("swing", {}).get("trading_manager", {})


def _enabled(config: dict[str, Any]) -> bool:
    return bool(_manager_cfg(config).get("enabled", True))


def _due_window(config: dict[str, Any]) -> timedelta:
    minutes = int(_manager_cfg(config).get("due_window_minutes", 20) or 20)
    return timedelta(minutes=max(minutes, 1))


def _exchange_codes(config: dict[str, Any], section: str, defaults: set[str]) -> set[str]:
    cfg = _manager_cfg(config).get(section, {})
    return {str(code).upper() for code in cfg.get("exchange_codes", sorted(defaults))}


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC)
    except ValueError:
        return None


def _pulse_row(
    *,
    kind: str,
    label: str,
    target_at_utc: datetime,
    now: datetime,
    source_markets: list[str],
    exchange_codes: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    window_end = target_at_utc + _due_window(config)
    return {
        "key": f"{kind}:{target_at_utc.date().isoformat()}:{target_at_utc.strftime('%H%M')}",
        "kind": kind,
        "label": label,
        "target_at_utc": target_at_utc.isoformat(timespec="seconds"),
        "window_end_at_utc": window_end.isoformat(timespec="seconds"),
        "due": target_at_utc <= now < window_end,
        "source_markets": source_markets,
        "exchange_codes": exchange_codes,
    }


def _group_targets(
    *,
    config: dict[str, Any],
    market_status_rows: list[dict[str, Any]],
    kind: str,
    label: str,
    codes: set[str],
    offset_minutes: int,
    anchor_key: str,
    now: datetime,
) -> list[dict[str, Any]]:
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    for row in market_status_rows:
        code = str(row.get("code") or "").upper()
        if code not in codes or row.get("holiday_name"):
            continue
        anchor = _parse_utc(row.get(anchor_key))
        tradable_close = _parse_utc(row.get("tradable_close_at_utc"))
        if anchor is None or tradable_close is None:
            continue
        target = anchor + timedelta(minutes=offset_minutes)
        if target >= tradable_close:
            continue
        if now >= target + _due_window(config):
            continue
        grouped.setdefault(target, []).append(row)
    pulses: list[dict[str, Any]] = []
    for target, rows in grouped.items():
        pulses.append(
            _pulse_row(
                kind=kind,
                label=label,
                target_at_utc=target,
                now=now,
                source_markets=sorted({str(row.get("market") or row.get("code")) for row in rows}),
                exchange_codes=sorted({str(row.get("code") or "").upper() for row in rows}),
                config=config,
            )
        )
    return pulses


def _close_targets(
    *,
    config: dict[str, Any],
    market_status_rows: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    cfg = _manager_cfg(config).get("close_rotation", {})
    if not bool(cfg.get("enabled", True)):
        return []
    minutes_before = int(cfg.get("minutes_before_tradable_close", 30) or 30)
    codes = _exchange_codes(config, "close_rotation", DEFAULT_EU_CODES)
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    for row in market_status_rows:
        code = str(row.get("code") or "").upper()
        if code not in codes or row.get("holiday_name"):
            continue
        tradable_close = _parse_utc(row.get("tradable_close_at_utc"))
        session_open = _parse_utc(row.get("session_open_at_utc"))
        if tradable_close is None or session_open is None:
            continue
        target = tradable_close - timedelta(minutes=minutes_before)
        if target <= session_open or now >= target + _due_window(config):
            continue
        grouped.setdefault(target, []).append(row)
    return [
        _pulse_row(
            kind="close_rotation",
            label="Pre-close Trading Manager",
            target_at_utc=target,
            now=now,
            source_markets=sorted({str(row.get("market") or row.get("code")) for row in rows}),
            exchange_codes=sorted({str(row.get("code") or "").upper() for row in rows}),
            config=config,
        )
        for target, rows in grouped.items()
    ]


def trading_manager_status(
    config: dict[str, Any],
    market_status_rows: list[dict[str, Any]] | None = None,
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    now = (reference_time or datetime.now(UTC)).astimezone(UTC)
    if not _enabled(config):
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "enabled": False,
            "due": False,
            "active_pulses": [],
            "pulses": [],
            "next_pulse_at": None,
            "next_pulse_label": None,
        }
    rows = market_status_rows or get_market_status(config, reference_time=now)
    pulses: list[dict[str, Any]] = []
    open_cfg = _manager_cfg(config).get("open_followup", {})
    if bool(open_cfg.get("enabled", True)):
        pulses.extend(
            _group_targets(
                config=config,
                market_status_rows=rows,
                kind="open_followup",
                label="Open +1h Trading Manager",
                codes=_exchange_codes(config, "open_followup", DEFAULT_EU_CODES),
                offset_minutes=int(open_cfg.get("minutes_after_open", 60) or 60),
                anchor_key="session_open_at_utc",
                now=now,
            )
        )
    pulses.extend(_close_targets(config=config, market_status_rows=rows, now=now))
    us_cfg = _manager_cfg(config).get("us_open_followup", {})
    if bool(us_cfg.get("enabled", True)):
        pulses.extend(
            _group_targets(
                config=config,
                market_status_rows=rows,
                kind="us_open_followup",
                label="US Open +1h Trading Manager",
                codes=_exchange_codes(config, "us_open_followup", DEFAULT_US_CODES),
                offset_minutes=int(us_cfg.get("minutes_after_open", 60) or 60),
                anchor_key="session_open_at_utc",
                now=now,
            )
        )
    pulses = sorted(pulses, key=lambda pulse: str(pulse["target_at_utc"]))
    active_pulses = [pulse for pulse in pulses if pulse["due"]]
    future_pulses = [
        pulse
        for pulse in pulses
        if datetime.fromisoformat(str(pulse["target_at_utc"])).astimezone(UTC) > now
    ]
    next_pulse = future_pulses[0] if future_pulses else None
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "enabled": True,
        "due": bool(active_pulses),
        "active_pulses": active_pulses,
        "pulses": pulses,
        "next_pulse_at": next_pulse["target_at_utc"] if next_pulse else None,
        "next_pulse_label": next_pulse["label"] if next_pulse else None,
    }


def should_auto_run_trading_manager(
    connection,
    config: dict[str, Any],
    market_status_rows: list[dict[str, Any]] | None = None,
    *,
    reference_time: datetime | None = None,
) -> bool:
    status = trading_manager_status(config, market_status_rows, reference_time=reference_time)
    return any(not has_trading_manager_run(connection, str(pulse["key"])) for pulse in status["active_pulses"])


def _extract_output_text(response_json: dict[str, Any]) -> str:
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    return ""


def _request_ai_manager(
    *,
    config: dict[str, Any],
    manager_pulse: dict[str, Any],
    report: dict[str, Any],
    candidate_orders: list[dict[str, Any]],
    technical_by_symbol: dict[str, dict[str, Any]],
    goal_tracking: dict[str, Any],
) -> dict[str, Any]:
    api_key = config.get("xai", {}).get("api_key")
    if not api_key:
        raise ValueError("XAI_API_KEY is missing")
    prompt = {
        "manager_pulse": manager_pulse,
        "decision_report": {
            "id": report.get("id"),
            "created_at": report.get("created_at"),
            "status": report.get("status"),
            "market_regime": (report.get("report_json") or {}).get("market_regime"),
            "portfolio_assessment": (report.get("report_json") or {}).get("portfolio_assessment"),
            "execution_notes": (report.get("report_json") or {}).get("execution_notes"),
        },
        "candidate_orders": candidate_orders,
        "daily_technicals": technical_by_symbol,
        "goal_tracking": goal_tracking,
        "instruction": (
            "Approve only trades that satisfy the swing rules: open exchange, watchlist-only, long-only, "
            "MACD/RSI/Bollinger/Stochastic/OBV confluence, 1:2 reward-risk, and no blacklist symbols. "
            "Account for progress versus the 5,000 DKK weekly and 20,000 DKK monthly pre-tax goals, "
            "but do not approve low-confluence trades just to chase the target. "
            "Reject marginal BUYs. SELL/FLATTEN is allowed when technicals warn risk is deteriorating."
        ),
    }
    request_json = {
        "model": config["xai"]["model"],
        "input": [
            {
                "role": "system",
                "content": "You are the Trading Manager execution gate. Return strict JSON only.",
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, indent=2)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "trading_manager_decision",
                "schema": TRADING_MANAGER_SCHEMA,
                "strict": True,
            }
        },
    }
    response = requests.post(
        f"{config['xai']['base_url'].rstrip('/')}/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=request_json,
        timeout=int(config["xai"].get("timeout_seconds", 120)),
    )
    response.raise_for_status()
    response_json = response.json()
    output_text = _extract_output_text(response_json)
    if not output_text:
        raise ValueError("Trading Manager AI response did not contain structured output text")
    parsed = json.loads(output_text)
    return {"status": "ok", "request_json": request_json, "response_json": response_json, "parsed": parsed}


def _technical_gate(order: dict[str, Any], technical: dict[str, Any] | None) -> tuple[bool, str]:
    if not technical or technical.get("status") != "ok":
        return False, "No usable daily technical indicator result."
    action = str(order.get("action") or "").upper()
    strategy_role = str(order.get("strategy_role") or action).upper()
    sentiment = str(technical.get("sentiment") or "HOLD").upper()
    trend_bias = str(technical.get("trend_bias") or "neutral").lower()
    confluences = int(technical.get("confluence_count") or 0)
    minimum = int(technical.get("min_confluences") or 3)
    if action == "BUY":
        if sentiment not in {"BUY", "OVERWEIGHT"}:
            return False, f"Technical sentiment is {sentiment}, not BUY/OVERWEIGHT."
        if trend_bias != "bullish":
            return False, f"Trend bias is {trend_bias}, not bullish."
        if confluences < minimum:
            return False, f"Only {confluences}/{minimum} indicator confluences."
        return True, "BUY approved by bullish technical confluence."
    if action == "SELL":
        if strategy_role == "FLATTEN" or sentiment in {"SELL", "UNDERWEIGHT"} or trend_bias == "bearish":
            return True, "SELL/FLATTEN approved by deteriorating technicals or explicit flatten role."
        return False, f"SELL not approved; technical sentiment is {sentiment} with {trend_bias} trend."
    return False, f"Unsupported manager action {action}."


def _filter_candidate_orders(
    *,
    candidate_orders: list[dict[str, Any]],
    technical_by_symbol: dict[str, dict[str, Any]],
    ai_result: dict[str, Any] | None,
) -> dict[str, Any]:
    ai_by_key = {
        str(item.get("strategy_key") or ""): item
        for item in ((ai_result or {}).get("approved_orders") or [])
        if item.get("strategy_key")
    }
    approved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for order in candidate_orders:
        key = str(order.get("strategy_key") or "")
        symbol = str(order.get("symbol") or "")
        technical_ok, technical_reason = _technical_gate(order, technical_by_symbol.get(symbol))
        ai_item = ai_by_key.get(key)
        ai_approved = True if ai_result is None else bool(ai_item and ai_item.get("approve"))
        if technical_ok and ai_approved:
            enriched = dict(order)
            metadata = dict(enriched.get("strategy_metadata") or {})
            metadata["trading_manager"] = {
                "technical_gate": technical_reason,
                "ai_rationale": ai_item.get("rationale") if ai_item else None,
                "ai_confidence": ai_item.get("confidence") if ai_item else None,
            }
            enriched["strategy_metadata"] = metadata
            approved.append(enriched)
            continue
        skipped.append(
            {
                "strategy_key": key,
                "symbol": symbol,
                "action": order.get("action"),
                "technical_gate": technical_reason,
                "ai_approved": ai_approved,
                "ai_rationale": ai_item.get("rationale") if ai_item else None,
            }
        )
    return {"approved_orders": approved, "skipped_orders": skipped}


def _watchlist_symbols_for_exchanges(config: dict[str, Any], exchange_codes: set[str]) -> list[str]:
    symbols: list[str] = []
    watchlists = build_watchlists(config)
    for category in watchlists.get("categories", []) or []:
        for row in category.get("items", []) or []:
            symbol = str(row.get("symbol") or "")
            if parse_exchange_code(symbol).upper() in exchange_codes:
                symbols.append(symbol)
    return symbols


def _portfolio_symbols_for_exchanges(connection, config: dict[str, Any], exchange_codes: set[str]) -> list[str]:
    batch_id = fetch_latest_batch_id(connection)
    symbols: list[str] = []
    for row in fetch_portfolio_positions(connection, batch_id=batch_id, initial_cash_dkk=float(config["portfolio"]["initial_cash_dkk"])):
        symbol = str(row.get("symbol") or "")
        if parse_exchange_code(symbol).upper() in exchange_codes:
            symbols.append(symbol)
    return symbols


def _candidate_orders_for_pulse(report: dict[str, Any], manager_pulse: dict[str, Any]) -> list[dict[str, Any]]:
    exchange_codes = {str(code).upper() for code in manager_pulse.get("exchange_codes", [])}
    strategy_plan = (report.get("report_json") or {}).get("strategy_plan") or {}
    orders = []
    for order in strategy_plan.get("swing_orders", []) or []:
        symbol = str(order.get("symbol") or "")
        if parse_exchange_code(symbol).upper() in exchange_codes:
            orders.append(dict(order))
    return orders


def run_trading_manager_cycle(
    *,
    config: dict[str, Any] | None = None,
    connection=None,
    market_status_rows: list[dict[str, Any]] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    resolved_config = config or _load_default_config()
    resolved_connection = connection or connect(resolved_config["portfolio"]["database_path"])
    init_db(resolved_connection)
    should_close = connection is None
    try:
        market_rows = market_status_rows or get_market_status(resolved_config)
        status = trading_manager_status(resolved_config, market_rows)
        due_pulses = status["active_pulses"] if not force else (status["active_pulses"] or status["pulses"][:1])
        runnable = [pulse for pulse in due_pulses if force or not has_trading_manager_run(resolved_connection, str(pulse["key"]))]
        if not runnable:
            return {"status": "not_due", "manager_status": status}

        report = fetch_latest_decision_report(resolved_connection)
        if not report or report.get("status") != "completed":
            return {"status": "skipped_no_completed_report", "manager_status": status}

        results: list[dict[str, Any]] = []
        for pulse in runnable:
            exchange_codes = {str(code).upper() for code in pulse.get("exchange_codes", [])}
            open_codes = {
                str(row.get("code") or "").upper()
                for row in market_rows
                if str(row.get("code") or "").upper() in exchange_codes and bool(row.get("is_tradable"))
            }
            if not open_codes:
                manager_payload = {"summary": "No source exchanges are currently tradable.", "approved_orders": [], "execution_notes": []}
                run_id = record_trading_manager_run(
                    resolved_connection,
                    manager_pulse=pulse,
                    report_id=int(report["id"]),
                    status="skipped_market_closed",
                    open_exchange_codes=[],
                    technical={},
                    manager=manager_payload,
                )
                results.append({"status": "skipped_market_closed", "id": run_id, "pulse": pulse})
                continue

            candidate_orders = [
                order
                for order in _candidate_orders_for_pulse(report, pulse)
                if parse_exchange_code(str(order.get("symbol") or "")).upper() in open_codes
            ]
            symbol_pool = set(order["symbol"] for order in candidate_orders if order.get("symbol"))
            symbol_pool.update(_portfolio_symbols_for_exchanges(resolved_connection, resolved_config, open_codes))
            symbol_pool.update(_watchlist_symbols_for_exchanges(resolved_config, open_codes))
            max_symbols = int(_manager_cfg(resolved_config).get("max_symbols", 30) or 30)
            technical_symbols = sorted(symbol_pool)[:max_symbols]
            indicator_config = copy.deepcopy(resolved_config)
            indicator_config.setdefault("strategy", {}).setdefault("swing", {}).setdefault("daily_indicators", {})["max_symbols"] = max_symbols
            technical_by_symbol = fetch_daily_swing_indicators(technical_symbols, indicator_config)

            ai_payload = None
            ai_error = None
            if bool(_manager_cfg(resolved_config).get("use_ai", True)) and candidate_orders:
                try:
                    ai_response = _request_ai_manager(
                        config=resolved_config,
                        manager_pulse=pulse,
                        report=report,
                        candidate_orders=candidate_orders,
                        technical_by_symbol=technical_by_symbol,
                        goal_tracking=fetch_goal_tracking(resolved_connection, resolved_config),
                    )
                    ai_payload = ai_response["parsed"]
                except Exception as exc:  # noqa: BLE001
                    ai_error = str(exc)
                    append_audit_log(
                        resolved_connection,
                        "trading_manager_ai_failed",
                        {"manager_key": pulse["key"], "report_id": report["id"], "error": ai_error},
                    )

            manager_decision = _filter_candidate_orders(
                candidate_orders=candidate_orders,
                technical_by_symbol=technical_by_symbol,
                ai_result=ai_payload,
            )
            queue_result = queue_and_maybe_execute_latest_report(
                config=resolved_config,
                connection=resolved_connection,
                create_report_orders=True,
                strategy_orders_override=manager_decision["approved_orders"],
            )
            manager_payload = {
                "summary": (ai_payload or {}).get("summary") or "Trading Manager used deterministic technical execution gates.",
                "ai_error": ai_error,
                "approved_order_count": len(manager_decision["approved_orders"]),
                "skipped_order_count": len(manager_decision["skipped_orders"]),
                "approved_orders": [
                    {"strategy_key": order.get("strategy_key"), "symbol": order.get("symbol"), "action": order.get("action")}
                    for order in manager_decision["approved_orders"]
                ],
                "skipped_orders": manager_decision["skipped_orders"],
                "execution_notes": (ai_payload or {}).get("execution_notes") or [],
            }
            run_status = "completed" if manager_decision["approved_orders"] else "completed_no_orders"
            run_id = record_trading_manager_run(
                resolved_connection,
                manager_pulse=pulse,
                report_id=int(report["id"]),
                status=run_status,
                open_exchange_codes=sorted(open_codes),
                technical=technical_by_symbol,
                manager=manager_payload,
                queue_result=queue_result,
            )
            results.append(
                {
                    "status": run_status,
                    "id": run_id,
                    "pulse": pulse,
                    "open_exchange_codes": sorted(open_codes),
                    "approved_orders": manager_decision["approved_orders"],
                    "skipped_orders": manager_decision["skipped_orders"],
                    "queue": queue_result,
                }
            )
        return {"status": "ok", "manager_status": status, "runs": results}
    finally:
        if should_close:
            resolved_connection.close()
