from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import append_audit_log, connect, init_db
from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.market_news import fetch_market_intelligence
from saxo_daytrader_xai.market_schedule import get_market_status, summarize_analysis_window
from saxo_daytrader_xai.portfolio import (
    fetch_broker_account_summary,
    fetch_goal_tracking,
    fetch_latest_batch_id,
    fetch_portfolio_positions,
    fetch_portfolio_summary,
    fetch_portfolio_symbols,
)
from saxo_daytrader_xai.watchlists import build_watchlists


def _load_default_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return load_config(root / "config.yaml")


def _get_connection_and_config(config: dict[str, Any] | None, connection):
    resolved_config = config or _load_default_config()
    resolved_connection = connection or connect(resolved_config["portfolio"]["database_path"])
    init_db(resolved_connection)
    return resolved_config, resolved_connection, connection is None


DECISION_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "report_title": {"type": "string"},
        "analysis_window_active": {"type": "boolean"},
        "goal": {"type": "string"},
        "market_regime": {
            "type": "object",
            "properties": {
                "bias": {"type": "string", "enum": ["bullish", "neutral", "defensive"]},
                "summary": {"type": "string"},
                "key_drivers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bias", "summary", "key_drivers"],
            "additionalProperties": False,
        },
        "portfolio_assessment": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "main_risks": {"type": "array", "items": {"type": "string"}},
                "strengths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "main_risks", "strengths"],
            "additionalProperties": False,
        },
        "reasoning_steps": {"type": "array", "items": {"type": "string"}},
        "risk_rules_check": {"type": "array", "items": {"type": "string"}},
        "watchlist_focus": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "thesis": {"type": "string"},
                    "catalysts": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["symbol", "thesis", "catalysts", "risks"],
                "additionalProperties": False,
            },
        },
        "suggested_trades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD", "NO_ACTION"]},
                    "symbol": {"type": "string"},
                    "target_weight_pct": {"type": "number"},
                    "quantity_hint": {"type": "string"},
                    "confidence": {"type": "number"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "rationale": {"type": "string"},
                    "risk_notes": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "action",
                    "symbol",
                    "target_weight_pct",
                    "quantity_hint",
                    "confidence",
                    "priority",
                    "rationale",
                    "risk_notes",
                ],
                "additionalProperties": False,
            },
        },
        "execution_notes": {"type": "array", "items": {"type": "string"}},
        "daily_target_assessment": {"type": "string"},
    },
    "required": [
        "report_title",
        "analysis_window_active",
        "goal",
        "market_regime",
        "portfolio_assessment",
        "reasoning_steps",
        "risk_rules_check",
        "watchlist_focus",
        "suggested_trades",
        "execution_notes",
        "daily_target_assessment",
    ],
    "additionalProperties": False,
}


def _extract_output_text(response_json: dict[str, Any]) -> str:
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    return ""


def _summarize_market_regime(
    watchlists: dict[str, Any],
    portfolio_quotes: list[dict[str, Any]],
    market_news: dict[str, Any],
    market_status_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    quote_changes = [row["change_pct"] for row in portfolio_quotes if row.get("change_pct") is not None]
    avg_quote_change = sum(quote_changes) / len(quote_changes) if quote_changes else 0.0
    nordic_leaders = [row["symbol"] for row in watchlists["nordic"][:3]]
    global_leaders = [row["symbol"] for row in watchlists["global"][:5]]
    open_markets = [row["market"] for row in market_status_rows if row["is_open"]]
    headline_titles = [item["title"] for item in market_news["market_news"][:3]]
    if avg_quote_change > 0.01:
        bias = "bullish"
    elif avg_quote_change < -0.01:
        bias = "defensive"
    else:
        bias = "neutral"
    summary = (
        f"Average live change across tracked portfolio symbols is {avg_quote_change * 100:.2f}%. "
        f"Open exchanges: {', '.join(open_markets) if open_markets else 'none currently open'}. "
        f"Top Nordic watchlist symbols: {', '.join(nordic_leaders)}. "
        f"Top global watchlist symbols: {', '.join(global_leaders)}."
    )
    return {
        "bias": bias,
        "summary": summary,
        "key_drivers": headline_titles,
    }


def _build_context(config: dict[str, Any], connection) -> dict[str, Any]:
    batch_id = fetch_latest_batch_id(connection)
    initial_cash_dkk = float(config.get("portfolio", {}).get("initial_cash_dkk", 0.0) or 0.0)
    prefer_broker_cash = (
        str(config.get("execution", {}).get("mode")) == "live"
        and str(config.get("execution", {}).get("adapter")) == "saxo"
    )
    portfolio_summary = fetch_portfolio_summary(
        connection,
        batch_id=batch_id,
        initial_cash_dkk=initial_cash_dkk,
        prefer_broker_cash=prefer_broker_cash,
    )
    portfolio_positions = fetch_portfolio_positions(
        connection,
        batch_id=batch_id,
        initial_cash_dkk=initial_cash_dkk,
        prefer_broker_cash=prefer_broker_cash,
    )
    portfolio_symbols = fetch_portfolio_symbols(connection, batch_id=batch_id)
    goal_tracking = fetch_goal_tracking(connection, config)
    watchlists = build_watchlists(config)
    watchlist_symbols = [row["symbol"] for row in watchlists["nordic"][:5]] + [row["symbol"] for row in watchlists["global"][:10]]
    market_news = fetch_market_intelligence(config, portfolio_symbols[:8], watchlist_symbols[:8])
    market_status_rows = get_market_status(config)
    analysis_summary = summarize_analysis_window(market_status_rows)
    broker_account = fetch_broker_account_summary(connection)
    live_quotes = fetch_live_prices(
        portfolio_symbols[:10],
        timeout_seconds=config["market_data"]["request_timeout_seconds"],
    )
    quote_by_symbol = {row["symbol"]: row for row in live_quotes}
    enriched_positions = []
    for row in portfolio_positions:
        quote = quote_by_symbol.get(row["symbol"], {})
        enriched_positions.append(
            {
                "symbol": row["symbol"],
                "instrument_name": row["instrument_name"],
                "isin": row["isin"],
                "quantity": row["quantity"],
                "currency": row["currency"],
                "market_value_dkk": row["market_value_dkk"],
                "cost_basis_dkk": row["cost_basis_dkk"],
                "unrealised_pnl_dkk": row["unrealised_pnl_dkk"],
                "allocation_pct": row["allocation_pct"],
                "current_price_local": quote.get("current_price", row["current_price_local"]),
                "daily_change_pct": quote.get("change_pct"),
            }
        )

    market_regime = _summarize_market_regime(watchlists, live_quotes, market_news, market_status_rows)
    return {
        "batch_id": batch_id,
        "portfolio_summary": portfolio_summary,
        "portfolio_positions": enriched_positions,
        "watchlists": {
            "nordic": watchlists["nordic"][:10],
            "global": watchlists["global"][:15],
        },
        "goal_tracking": goal_tracking,
        "broker_account": broker_account,
        "market_news": market_news,
        "market_status": market_status_rows,
        "analysis_summary": analysis_summary,
        "market_regime": market_regime,
    }


def build_trading_prompt(context: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    excluded_symbols = config["risk"]["excluded_symbols"]
    excluded_symbols_text = ", ".join(excluded_symbols) if excluded_symbols else "none configured"
    system_prompt = f"""
You are the portfolio decision engine for a Danish SaxoInvestor day-trading system.

Core goal for every decision:
{config['xai']['goal']}

Hard rules:
- Never trade or recommend trading these excluded symbols: {excluded_symbols_text}.
- Never short. Long-only portfolio.
- No single position may exceed 15%% of portfolio value after the proposed trade.
- Treat all pnl, commission, and taxation impacts in DKK.
- Prefer liquid, high-conviction trades with limited execution complexity.
- If the best action is to do nothing, say so clearly.

Output requirements:
- Return only structured data conforming to the provided schema.
- Provide explicit step-by-step rationale in the reasoning_steps field.
- Suggested trades must be practical, risk-aware, and consistent with the supplied context.
""".strip()

    user_prompt = f"""
Current portfolio snapshot JSON:
{json.dumps({'summary': context['portfolio_summary'], 'positions': context['portfolio_positions']}, ensure_ascii=False, indent=2)}

Market regime summary JSON:
{json.dumps(context['market_regime'], ensure_ascii=False, indent=2)}

Watchlist opportunities JSON:
{json.dumps(context['watchlists'], ensure_ascii=False, indent=2)}

News and macro context JSON:
{json.dumps({'market_news': context['market_news']['market_news'][:8], 'macro_events': context['market_news']['macro_events'][:6], 'earnings_calendar': context['market_news']['earnings_calendar'][:8]}, ensure_ascii=False, indent=2)}

Market status JSON:
{json.dumps({'analysis_summary': context['analysis_summary'], 'markets': context['market_status']}, ensure_ascii=False, indent=2)}

Goal tracking JSON:
{json.dumps(context['goal_tracking'], ensure_ascii=False, indent=2)}

Broker account JSON:
{json.dumps(context['broker_account'], ensure_ascii=False, indent=2)}

Task:
1. Assess the current market regime for a day-trading horizon.
2. Evaluate the existing portfolio, including concentration and downside risks.
3. Evaluate whether the portfolio is currently on track versus the DKK 500/day and DKK 3,500/week goals using the provided day/week/month/year/all-time performance data.
4. Identify the highest-priority trade adjustments for today, if any.
5. Respect Danish tax drag, commission drag, and the long-only / exclusion constraints.
6. Produce a concise but concrete decision report for the operator.
""".strip()

    return {
        "system": system_prompt,
        "user": user_prompt,
    }


def _mock_decision_report(context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    top_position = context["portfolio_positions"][0]["symbol"] if context["portfolio_positions"] else "NO_HOLDINGS"
    top_watch = context["watchlists"]["global"][0]["symbol"] if context["watchlists"]["global"] else top_position
    return {
        "report_title": "Mock Decision Report",
        "analysis_window_active": bool(context["analysis_summary"]["analysis_window_active"]),
        "goal": config["xai"]["goal"],
        "market_regime": context["market_regime"],
        "portfolio_assessment": {
            "summary": "Mock mode: conservative stance because no live xAI call was made.",
            "main_risks": ["Decision engine ran in mock mode."],
            "strengths": ["Portfolio and market context assembled successfully."],
        },
        "reasoning_steps": [
            "Check whether the analysis window is active and review broad market tone.",
            "Review the largest portfolio exposures and recent price moves.",
            "Prefer no-action or small adjustments until the live xAI API is used.",
        ],
        "risk_rules_check": [
            "Excluded symbols remain blocked.",
            "No shorting allowed.",
            "Target weights must stay at or below 15 percent.",
        ],
        "watchlist_focus": [
            {
                "symbol": top_watch,
                "thesis": "Highest-ranked watchlist symbol from current inputs.",
                "catalysts": ["Watchlist ranking and live quote inputs"],
                "risks": ["Mock report only; requires live xAI confirmation"],
            }
        ],
        "suggested_trades": [
            {
                "action": "HOLD",
                "symbol": top_position,
                "target_weight_pct": 0.0,
                "quantity_hint": "No trade in mock mode",
                "confidence": 0.25,
                "priority": "low",
                "rationale": "Mock mode avoids generating real trade changes.",
                "risk_notes": ["Use a live xAI API key to obtain real suggestions."],
            }
        ],
        "execution_notes": ["Mock mode only. No live model response was requested."],
        "daily_target_assessment": (
            "Mock mode only. "
            f"Observed day pnl is {context['goal_tracking']['periods']['day']['pnl_dkk']:.2f} DKK "
            f"versus target {context['goal_tracking']['periods']['day']['target_dkk']:.2f} DKK."
        ),
    }


def _create_response_request(prompt: dict[str, str], config: dict[str, Any], include_encrypted_reasoning: bool) -> dict[str, Any]:
    request_json = {
        "model": config["xai"]["model"],
        "input": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "decision_report",
                "schema": DECISION_REPORT_SCHEMA,
                "strict": True,
            }
        },
    }
    if include_encrypted_reasoning:
        request_json["include"] = ["reasoning.encrypted_content"]
    return request_json


def request_decision_report(prompt: dict[str, str], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = config["xai"]["api_key"]
    if not api_key:
        raise ValueError("XAI_API_KEY is missing")
    request_json = _create_response_request(
        prompt,
        config,
        include_encrypted_reasoning=bool(config["xai"].get("include_encrypted_reasoning")),
    )
    response = requests.post(
        f"{config['xai']['base_url'].rstrip('/')}/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=request_json,
        timeout=config["xai"]["timeout_seconds"],
    )
    response.raise_for_status()
    response_json = response.json()
    report_text = _extract_output_text(response_json)
    if not report_text:
        raise ValueError("xAI response did not contain structured output text")
    return request_json, {"raw": response_json, "parsed": json.loads(report_text)}


def _latest_report_row(connection) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
        FROM decision_reports
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def fetch_latest_decision_report(connection) -> dict[str, Any] | None:
    row = _latest_report_row(connection)
    if not row:
        return None
    row["request_json"] = json.loads(row["request_json"]) if row.get("request_json") else None
    row["response_json"] = json.loads(row["response_json"]) if row.get("response_json") else None
    row["report_json"] = json.loads(row["report_json"]) if row.get("report_json") else None
    return row


def should_auto_run_decision_report(connection, config: dict[str, Any], analysis_window_active: bool) -> bool:
    if not analysis_window_active:
        return False
    latest = _latest_report_row(connection)
    if not latest or latest["status"] != "completed":
        if not latest:
            return True
        latest_time = datetime.fromisoformat(latest["created_at"])
        return datetime.now(UTC) - latest_time > timedelta(minutes=int(config["xai"]["auto_run_interval_minutes"]))
    latest_time = datetime.fromisoformat(latest["created_at"])
    return datetime.now(UTC) - latest_time > timedelta(minutes=int(config["xai"]["auto_run_interval_minutes"]))


def generate_decision_report(
    *,
    config: dict[str, Any] | None = None,
    connection=None,
    force_mock: bool = False,
) -> dict[str, Any]:
    resolved_config, resolved_connection, should_close = _get_connection_and_config(config, connection)
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        context = _build_context(resolved_config, resolved_connection)
        prompt = build_trading_prompt(context, resolved_config)
        batch_id = context["batch_id"]
        request_json: dict[str, Any] = {}
        response_json: dict[str, Any] | None = None
        report_json: dict[str, Any] | None = None
        error_text = None
        status = "completed"
        response_id = None

        try:
            if force_mock or not resolved_config["xai"]["api_key"]:
                report_json = _mock_decision_report(context, resolved_config)
                request_json = {"mode": "mock"}
                response_json = {"mode": "mock"}
            else:
                request_json, response_bundle = request_decision_report(prompt, resolved_config)
                response_json = response_bundle["raw"]
                report_json = response_bundle["parsed"]
                response_id = response_json.get("id")
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error_text = str(exc)
            report_json = _mock_decision_report(context, resolved_config)

        cursor = resolved_connection.execute(
            """
            INSERT INTO decision_reports (
                created_at,
                report_date,
                batch_id,
                model,
                status,
                analysis_window_active,
                response_id,
                prompt_text,
                request_json,
                response_json,
                report_json,
                error_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                created_at[:10],
                batch_id,
                resolved_config["xai"]["model"],
                status,
                1 if context["analysis_summary"]["analysis_window_active"] else 0,
                response_id,
                json.dumps(prompt, ensure_ascii=False, sort_keys=True),
                json.dumps(request_json, ensure_ascii=False, sort_keys=True),
                json.dumps(response_json, ensure_ascii=False, sort_keys=True) if response_json is not None else None,
                json.dumps(report_json, ensure_ascii=False, sort_keys=True),
                error_text,
            ),
        )
        report_id = int(cursor.lastrowid)
        resolved_connection.commit()

        append_audit_log(
            resolved_connection,
            "decision_report_generated",
            {
                "report_id": report_id,
                "batch_id": batch_id,
                "status": status,
                "response_id": response_id,
                "analysis_window_active": context["analysis_summary"]["analysis_window_active"],
            },
        )
        return {
            "id": report_id,
            "created_at": created_at,
            "status": status,
            "response_id": response_id,
            "report_json": report_json,
            "error_text": error_text,
            "context": context,
        }
    finally:
        if should_close:
            resolved_connection.close()
