from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import connect, fetch_scheduler_cycles, fetch_scheduler_status, init_db
from saxo_daytrader_xai.execution_engine import (
    execute_order,
    export_audit_bundle,
    fetch_execution_events,
    fetch_execution_fills,
    fetch_execution_orders,
    fetch_invalid_simulation_trades,
    manage_live_order,
    queue_and_maybe_execute_latest_report,
    repair_invalid_simulation_trades,
    sync_broker_order_statuses,
)
from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.market_news import fetch_market_intelligence
from saxo_daytrader_xai.market_schedule import get_market_status, summarize_analysis_window
from saxo_daytrader_xai.notifications import (
    build_summary,
    dispatch_broker_alerts_if_due,
    dispatch_summaries_if_due,
    fetch_notification_deliveries,
)
from saxo_daytrader_xai.scheduler_service import (
    _scheduler_history_policy,
    assess_scheduler_worker_health,
    run_manual_scheduler_cycle,
)
from saxo_daytrader_xai.portfolio import (
    fetch_latest_batch_id,
    fetch_portfolio_positions,
    fetch_realised_tax_summary,
    fetch_portfolio_summary,
    fetch_portfolio_symbols,
    fetch_trade_ledger,
)
from saxo_daytrader_xai.watchlists import build_watchlists
from saxo_daytrader_xai.xai_decision import (
    fetch_latest_decision_report,
    generate_decision_report,
    should_auto_run_decision_report,
)


def _format_dkk(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f} DKK"


def _format_money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return "n/a"
    suffix = f" {currency}" if currency else ""
    return f"{value:,.2f}{suffix}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _format_qty(value: float | None) -> str:
    if value is None:
        return "n/a"
    rounded = round(float(value))
    if abs(float(value) - rounded) <= 1e-9:
        return str(int(rounded))
    return f"{float(value):.4f}"


def _signed_color(value: float | None) -> str:
    if value is None:
        return ""
    if float(value) > 0:
        return "color: #0a7f39; font-weight: 600;"
    if float(value) < 0:
        return "color: #b42318; font-weight: 600;"
    return ""


def _sent_alert_count(alert_result: dict | None) -> int:
    if not isinstance(alert_result, dict):
        return 0
    return sum(1 for row in alert_result.get("sent", []) if row.get("status") == "sent")


@st.cache_data(ttl=300, show_spinner=False)
def _load_watchlists(config_path: str) -> dict:
    return build_watchlists(load_config(config_path))


@st.cache_data(ttl=300, show_spinner=False)
def _load_market_intelligence(config_path: str, portfolio_symbols: tuple[str, ...], watchlist_symbols: tuple[str, ...]) -> dict:
    config = load_config(config_path)
    return fetch_market_intelligence(config, list(portfolio_symbols), list(watchlist_symbols))


@st.cache_data(ttl=60, show_spinner=False)
def _load_ticker_quote(config_path: str, symbol: str) -> list[dict]:
    config = load_config(config_path)
    return fetch_live_prices([symbol], timeout_seconds=config["market_data"]["request_timeout_seconds"])


st.set_page_config(page_title="saxo-daytrader-xai", layout="wide")
config_path = str(ROOT / "config.yaml")
config = load_config(config_path)
database_path = config["portfolio"]["database_path"]

connection = connect(database_path)
init_db(connection)

batch_id = fetch_latest_batch_id(connection)
initial_cash_dkk = float(config.get("portfolio", {}).get("initial_cash_dkk", 0.0) or 0.0)
summary = fetch_portfolio_summary(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk)
positions = fetch_portfolio_positions(connection, batch_id=batch_id, initial_cash_dkk=initial_cash_dkk)
portfolio_symbols = fetch_portfolio_symbols(connection, batch_id=batch_id)
trade_ledger = fetch_trade_ledger(connection)
tax_summary = fetch_realised_tax_summary(connection, tax_year=2026)
watchlists = _load_watchlists(config_path)
watchlist_symbols = [row["symbol"] for row in watchlists["nordic"][:8]] + [row["symbol"] for row in watchlists["global"][:8]]
market_intelligence = _load_market_intelligence(config_path, tuple(portfolio_symbols[:10]), tuple(watchlist_symbols))
market_status_rows = get_market_status(config)
analysis_summary = summarize_analysis_window(market_status_rows)
latest_decision_report = fetch_latest_decision_report(connection)
notification_deliveries = fetch_notification_deliveries(connection, limit=50)
scheduler_status = fetch_scheduler_status(connection)
scheduler_cycles = fetch_scheduler_cycles(connection, limit=15)
daily_summary_preview = build_summary(connection, config, summary_kind="daily")
weekly_summary_preview = build_summary(connection, config, summary_kind="weekly")
monthly_summary_preview = build_summary(connection, config, summary_kind="monthly")
quarterly_summary_preview = build_summary(connection, config, summary_kind="quarterly")
ytd_summary_preview = build_summary(connection, config, summary_kind="ytd")

if should_auto_run_decision_report(connection, config, analysis_summary["analysis_window_active"]):
    with st.spinner("Generating xAI decision report..."):
        generated_report = generate_decision_report(config=config, connection=connection)
        queue_and_maybe_execute_latest_report(config=config, connection=connection)
        latest_decision_report = fetch_latest_decision_report(connection)
        st.toast(f"Decision report generated with status: {generated_report['status']}")

st.title("saxo-daytrader-xai")
st.caption("Phase 34 dashboard with autonomous simulation support, live broker workflow, quote-aware daily P/L tracking, scheduler controls, route-aware notifications, scheduler cycle history, stale-worker detection, automatic history retention, and invalid simulation trade repair.")

autonomous_scheduler = bool(config.get("app", {}).get("launch_scheduler_with_dashboard", False)) and bool(
    config.get("scheduler", {}).get("enabled", True)
)
if config["execution"]["mode"] == "simulation" and config["execution"].get("auto_execute_simulation", False):
    if autonomous_scheduler:
        st.success("Autonomous simulation is enabled. Running the app normally also launches the scheduler that makes decisions and executes simulation trades.")
    else:
        st.warning("Simulation auto-execution is enabled, but autonomous scheduling is off. Run `make scheduler` or enable `app.launch_scheduler_with_dashboard` to execute trades without the manual queue button.")

excluded_symbols = config.get("risk", {}).get("excluded_symbols", [])
if excluded_symbols:
    st.info(f"Excluded symbols enforced globally: {', '.join(excluded_symbols)}")
else:
    st.info("No globally excluded symbols are configured.")

if analysis_summary["analysis_window_active"]:
    st.success(f"Analysis window active: {', '.join(analysis_summary['active_markets'])}")
else:
    st.warning("Analysis window inactive right now.")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Positions", summary["position_count"])
col2.metric("Portfolio Value", _format_dkk(summary["total_market_value_dkk"]))
col3.metric("Cash", _format_dkk(summary["cash_balance_dkk"]))
col4.metric("Cost Basis", _format_dkk(summary["total_cost_basis_dkk"]))
col5.metric("Unrealised P/L", _format_dkk(summary["total_unrealised_pnl_dkk"]))

tab_portfolio, tab_watchlist, tab_news, tab_market, tab_decision, tab_execution, tab_notifications = st.tabs(
    ["Portfolio", "Watchlist", "News", "Market Status", "Decision Report", "Execution", "Notifications"]
)

with tab_portfolio:
    st.subheader("Portfolio Snapshot")
    cash_col1, cash_col2, cash_col3, cash_col4 = st.columns(4)
    daily_delta = summary["total_daily_pnl_dkk"]
    cash_col1.metric("Daily P/L Since 06:00", _format_dkk(daily_delta), delta=f"{daily_delta:+,.2f} DKK")
    cash_col2.metric("Latest Batch", batch_id or "No imports yet")
    cash_col3.metric("Initial Cash", _format_dkk(summary["initial_cash_dkk"]))
    cash_col4.metric("Cash From Trades", _format_dkk(summary["cash_from_trades_dkk"]))
    baseline_session_date = next((row.get("baseline_session_date") for row in positions if row.get("baseline_session_date")), None)
    if baseline_session_date:
        st.caption(f"Intraday baseline session: {baseline_session_date} at 06:00 Europe/Copenhagen.")

    if positions:
        position_df = pd.DataFrame(
            [
                {
                    "Symbol": row["symbol"],
                    "Instrument": row["instrument_name"],
                    "ISIN": row["isin"],
                    "Qty": row["quantity"],
                    "Currency": row["currency"],
                    "Open Price": row["open_price_local"],
                    "Current Price": row["current_price_local"],
                    "Cost Basis DKK": row["cost_basis_dkk"],
                    "Market Value DKK": row["market_value_dkk"],
                    "Unrealised P/L DKK": row["unrealised_pnl_dkk"],
                    "Daily P/L DKK": row["daily_pnl_dkk"],
                    "Allocation": row["allocation_pct"],
                    "Asset Class": row["asset_class"],
                    "Market": row["market_status"],
                    "Value Date": row["value_date"],
                }
                for row in positions
            ]
        )
        styled_positions = (
            position_df.style.format(
                {
                    "Qty": lambda value: _format_qty(value),
                    "Open Price": lambda value: _format_money(value),
                    "Current Price": lambda value: _format_money(value),
                    "Cost Basis DKK": lambda value: _format_dkk(value),
                    "Market Value DKK": lambda value: _format_dkk(value),
                    "Unrealised P/L DKK": lambda value: _format_dkk(value),
                    "Daily P/L DKK": lambda value: _format_dkk(value),
                    "Allocation": lambda value: _format_pct(value),
                }
            )
            .map(_signed_color, subset=["Daily P/L DKK", "Unrealised P/L DKK"])
        )
        st.dataframe(styled_positions, width="stretch", hide_index=True)
    else:
        st.warning("No portfolio positions are available in the database yet.")

    st.subheader("Ticker Lookup")
    ticker_input = st.text_input("Lookup live price for a ticker", value=portfolio_symbols[0] if portfolio_symbols else "AMD:xnas")
    if ticker_input:
        quote_rows = _load_ticker_quote(config_path, ticker_input.strip())
        if quote_rows:
            quote = quote_rows[0]
            st.write(
                {
                    "symbol": quote["symbol"],
                    "yahoo_symbol": quote["yahoo_symbol"],
                    "current_price": quote["current_price"],
                    "previous_close": quote["previous_close"],
                    "change_pct": _format_pct(quote["change_pct"]),
                    "status": quote["status"],
                    "source": quote["source"],
                }
            )

    st.subheader("Trade Ledger")
    st.caption(
        f"2026 realised gains: {_format_dkk(tax_summary['realised_gain_dkk'])} | "
        f"tax impact: {_format_dkk(tax_summary['tax_dkk'])} | "
        f"commission: {_format_dkk(tax_summary['commission_dkk'])}"
    )
    if trade_ledger:
        st.dataframe(
            [
                {
                    "ID": row["id"],
                    "Created": row["created_at"],
                    "Symbol": row["symbol"],
                    "Side": row["side"],
                    "Qty": _format_qty(row["quantity"]),
                    "Price Local": row["price_local"],
                    "Currency": row["currency"],
                    "Gross DKK": _format_dkk(row["gross_amount_dkk"]),
                    "Commission DKK": _format_dkk(row["commission_dkk"]),
                    "Tax DKK": _format_dkk(row["tax_dkk"]),
                    "Realised Gain Local": row.get("realised_gain_local"),
                    "Price Gain DKK": _format_dkk(row.get("price_gain_dkk")),
                    "FX Gain DKK": _format_dkk(row.get("fx_gain_dkk")),
                    "Realised Gain DKK": _format_dkk(row["realised_gain_dkk"]),
                    "Cost Basis Sold DKK": _format_dkk(row["cost_basis_sold_dkk"]),
                    "Cost Basis Sold Local": row.get("cost_basis_sold_local"),
                    "Sale FX": row.get("sale_fx_rate_to_dkk"),
                    "Cost FX": row.get("cost_basis_fx_rate_to_dkk"),
                    "Net DKK": _format_dkk(row["net_amount_dkk"]),
                    "Mode": row["mode"],
                    "Status": row["status"],
                    "Notes": row["notes"],
                    "Validation": row.get("validation_note", ""),
                }
                for row in trade_ledger
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No trades recorded yet. Trade execution arrives in later phases.")

with tab_watchlist:
    st.subheader("Daily Refreshed Watchlists")
    st.caption(f"Generated at {watchlists['generated_at']}")

    st.markdown(f"**Nordic Top {len(watchlists['nordic'])}**")
    st.dataframe(
        [
            {
                "Symbol": row["symbol"],
                "Name": row["name"],
                "Exchange": row["exchange"],
                "Currency": row["currency"],
                "Last Price": _format_money(row["current_price"], row["currency"]),
                "Change": _format_pct(row["change_pct"]),
                "Quote Source": row["quote_source"],
                "Status": row["quote_status"],
            }
            for row in watchlists["nordic"]
        ],
        width="stretch",
        hide_index=True,
    )

    st.markdown(f"**US / Europe Top {len(watchlists['global'])}**")
    st.dataframe(
        [
            {
                "Symbol": row["symbol"],
                "Name": row["name"],
                "Exchange": row["exchange"],
                "Currency": row["currency"],
                "Last Price": _format_money(row["current_price"], row["currency"]),
                "Change": _format_pct(row["change_pct"]),
                "Quote Source": row["quote_source"],
                "Status": row["quote_status"],
            }
            for row in watchlists["global"]
        ],
        width="stretch",
        hide_index=True,
    )

with tab_news:
    st.subheader("News and Events")
    st.caption(f"Generated at {market_intelligence['generated_at']}")

    st.markdown("**Market News**")
    market_news_rows = market_intelligence["market_news"]
    if market_news_rows:
        st.dataframe(market_news_rows, width="stretch", hide_index=True)
    else:
        st.caption("No market headlines were available.")

    st.markdown("**Earnings Calendar**")
    earnings_rows = market_intelligence["earnings_calendar"]
    if earnings_rows:
        st.dataframe(earnings_rows, width="stretch", hide_index=True)
    else:
        st.caption("No upcoming earnings events were returned for the current focus list.")

    st.markdown("**Macro Events / Central Bank Headlines**")
    macro_rows = market_intelligence["macro_events"]
    if macro_rows:
        st.dataframe(macro_rows, width="stretch", hide_index=True)
    else:
        st.caption("No macro feeds were available.")

with tab_market:
    st.subheader("Exchange Status")
    scheduler_health = assess_scheduler_worker_health(
        scheduler_status,
        poll_interval_minutes=int(config.get("scheduler", {}).get("poll_interval_minutes", 15)),
    )
    sched_col1, sched_col2, sched_col3, sched_col4 = st.columns(4)
    sched_col1.metric("Scheduler Health", str(scheduler_health["status"]).upper())
    sched_col2.metric("Scheduler PID", scheduler_status.get("scheduler_pid") if scheduler_status else "n/a")
    sched_col3.metric("Last Cycle Status", scheduler_status.get("last_cycle_status") if scheduler_status else "n/a")
    sched_col4.metric("Last Completed", scheduler_status.get("last_cycle_completed_at") if scheduler_status else "n/a")
    st.caption(scheduler_health["message"])
    if scheduler_health.get("restart_recommended"):
        st.warning("Scheduler restart is recommended. If the app launched the scheduler child, it will auto-restart within the configured restart budget.")
    history_policy = _scheduler_history_policy(config)
    st.caption(
        f"Scheduler history retention: keep last {history_policy['history_max_rows']} rows "
        f"and {history_policy['history_retention_days']} days."
    )
    if "manual_scheduler_result" in st.session_state:
        last_manual_result = st.session_state["manual_scheduler_result"]
        if last_manual_result.get("status") == "ok":
            st.success(
                f"Manual scheduler cycle completed. generated_decision={last_manual_result.get('generated_decision')} "
                f"queue_status={last_manual_result.get('queue', {}).get('status')}"
            )
        else:
            st.error(f"Manual scheduler cycle failed: {last_manual_result.get('error', 'unknown error')}")
    cycle_col1, cycle_col2 = st.columns(2)
    if cycle_col1.button("Run Scheduler Cycle Now"):
        with st.spinner("Running scheduler cycle..."):
            st.session_state["manual_scheduler_result"] = run_manual_scheduler_cycle(
                config=config,
                connection=connection,
                mock=False,
            )
        st.rerun()
    if cycle_col2.button("Run Mock Scheduler Cycle"):
        with st.spinner("Running mock scheduler cycle..."):
            st.session_state["manual_scheduler_result"] = run_manual_scheduler_cycle(
                config=config,
                connection=connection,
                mock=True,
            )
        st.rerun()
    if scheduler_status and scheduler_status.get("last_cycle_json"):
        with st.expander("Latest Scheduler Cycle"):
            st.json(scheduler_status["last_cycle_json"])

    st.markdown("**Recent Scheduler Cycles**")
    if scheduler_cycles:
        st.dataframe(
            [
                {
                    "ID": row["id"],
                    "Started": row["started_at"],
                    "Completed": row["completed_at"],
                    "Status": row["status"],
                    "Analysis Window": bool(row["analysis_window_active"]),
                    "Generated Decision": bool(row["generated_decision"]),
                    "Queue": row["queue_status"] or "",
                    "Notifications": row["notifications_status"] or "",
                    "Broker Alerts": row["broker_alerts_status"] or "",
                }
                for row in scheduler_cycles
            ],
            width="stretch",
            hide_index=True,
        )
        with st.expander("Most Recent Scheduler Cycle Payload"):
            st.json(scheduler_cycles[0]["cycle_json"])
    else:
        st.caption("No scheduler cycles have been recorded yet.")

    st.dataframe(
        [
            {
                "Code": row["code"],
                "Market": row["market"],
                "Timezone": row["timezone"],
                "Local Time": row["local_time"],
                "Status": row["status_reason"],
                "Holiday": row["holiday_name"] or "",
                "Session Open": row["session_open_local"],
                "Session Close": row["session_close_local"],
                "Open": row["is_open"],
                "Analysis Window Active": row["analysis_window_active"],
                "Analysis Window Start": row["analysis_window_start"],
                "Analysis Window End": row["analysis_window_end"],
                "Next Open": row["next_open"],
                "Calendar Source": row["calendar_source"],
                "Last Checked": row["calendar_last_checked"],
            }
            for row in market_status_rows
        ],
        width="stretch",
        hide_index=True,
    )
    st.write(
        "The system checks refreshed exchange calendars for each market, including holiday closures and daylight-saving shifts, "
        "and marks a market as analysis-active when the current local exchange time is between 60 and 90 minutes after that market's actual session open."
    )

with tab_decision:
    st.subheader("xAI Decision Report")
    button_col1, button_col2, button_col3 = st.columns(3)
    if button_col1.button("Run Decision Now", type="primary"):
        with st.spinner("Calling xAI decision engine..."):
            result = generate_decision_report(config=config, connection=connection)
            queue_result = queue_and_maybe_execute_latest_report(config=config, connection=connection)
            latest_decision_report = fetch_latest_decision_report(connection)
        st.success(
            f"Decision report status: {result['status']} | "
            f"trade alerts sent: {_sent_alert_count(queue_result.get('alerts'))}"
        )
        st.rerun()
    if button_col2.button("Run Mock Decision"):
        with st.spinner("Generating mock decision report..."):
            result = generate_decision_report(config=config, connection=connection, force_mock=True)
            queue_result = queue_and_maybe_execute_latest_report(config=config, connection=connection)
            latest_decision_report = fetch_latest_decision_report(connection)
        st.info(
            f"Mock decision report status: {result['status']} | "
            f"trade alerts sent: {_sent_alert_count(queue_result.get('alerts'))}"
        )
        st.rerun()
    if button_col3.button("Queue Latest Suggestions"):
        with st.spinner("Queuing latest report suggestions..."):
            queue_result = queue_and_maybe_execute_latest_report(config=config, connection=connection)
        st.info(
            f"Queue result: {queue_result['status']} | "
            f"trade alerts sent: {_sent_alert_count(queue_result.get('alerts'))}"
        )
        st.rerun()

    if latest_decision_report:
        report = latest_decision_report["report_json"] or {}
        st.caption(
            f"Created at {latest_decision_report['created_at']} | "
            f"status={latest_decision_report['status']} | "
            f"model={latest_decision_report['model']}"
        )
        if latest_decision_report.get("error_text"):
            st.warning(latest_decision_report["error_text"])

        st.markdown(f"**{report.get('report_title', 'Decision Report')}**")
        st.write(report.get("daily_target_assessment", ""))

        regime = report.get("market_regime", {})
        portfolio_assessment = report.get("portfolio_assessment", {})
        st.json(
            {
                "analysis_window_active": report.get("analysis_window_active"),
                "goal": report.get("goal"),
                "market_regime": regime,
                "portfolio_assessment": portfolio_assessment,
            }
        )

        st.markdown("**Reasoning Steps**")
        for step in report.get("reasoning_steps", []):
            st.write(f"- {step}")

        st.markdown("**Risk Rules Check**")
        for item in report.get("risk_rules_check", []):
            st.write(f"- {item}")

        st.markdown("**Suggested Trades**")
        suggested_trades = report.get("suggested_trades", [])
        if suggested_trades:
            st.dataframe(suggested_trades, width="stretch", hide_index=True)
        else:
            st.caption("No suggested trades in the latest report.")

        st.markdown("**Watchlist Focus**")
        watchlist_focus = report.get("watchlist_focus", [])
        if watchlist_focus:
            st.dataframe(watchlist_focus, width="stretch", hide_index=True)
        else:
            st.caption("No watchlist focus items in the latest report.")

        with st.expander("Prompt and Raw Report"):
            st.json(
                {
                    "prompt": latest_decision_report.get("request_json"),
                    "report": latest_decision_report.get("report_json"),
                }
            )
    else:
        st.caption("No decision report has been generated yet.")

with tab_execution:
    st.subheader("Execution Queue")
    execution_orders = fetch_execution_orders(connection, limit=100)
    execution_fills = fetch_execution_fills(connection, limit=100)
    execution_events = fetch_execution_events(connection, limit=100)
    invalid_simulation_trades = fetch_invalid_simulation_trades(connection, limit=25)
    pending_approvals = [row for row in execution_orders if row["status"] == "pending_approval"]
    executed_orders = [row for row in execution_orders if row["status"] == "executed"]

    mode_col1, mode_col2, mode_col3, mode_col4, mode_col5 = st.columns(5)
    mode_col1.metric("Execution Mode", str(config["execution"]["mode"]).upper())
    mode_col2.metric("Queued Orders", len(execution_orders))
    mode_col3.metric("Pending Approval", len(pending_approvals))
    mode_col4.metric("Broker Events", len(execution_events))
    mode_col5.metric("Invalid Sim Trades", len(invalid_simulation_trades))

    st.caption(
        f"Adapter={config['execution']['adapter']} | dry_run={config['app']['dry_run']} | "
        f"max_daily_orders={config['execution']['max_daily_orders']}"
    )
    if config["execution"]["mode"] == "live":
        st.info("Approved live orders are submitted to Saxo and stored as broker submissions. They are not booked into the local trade ledger as executed fills yet.")

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    if action_col1.button("Run Queue Processor"):
        with st.spinner("Processing queued execution orders..."):
            queue_result = queue_and_maybe_execute_latest_report(config=config, connection=connection)
        st.success(
            f"Queue processor status: {queue_result['status']} | "
            f"trade alerts sent: {_sent_alert_count(queue_result.get('alerts'))}"
        )
        st.rerun()

    if action_col2.button("Export Audit Bundle"):
        export_dir = ROOT / "exports" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        export_result = export_audit_bundle(str(export_dir), config=config, connection=connection)
        st.success(f"Audit bundle exported to {export_result['output_dir']}")

    if action_col3.button("Sync Broker Status"):
        with st.spinner("Synchronizing Saxo broker order statuses..."):
            sync_result = sync_broker_order_statuses(config=config, connection=connection)
        st.success(f"Broker sync updated {sync_result['updated']} orders")
        st.rerun()
    if action_col4.button("Repair Invalid Simulation Trades"):
        with st.spinner("Repairing invalid simulation trades..."):
            repair_result = repair_invalid_simulation_trades(config=config, connection=connection)
        st.success(
            f"Repaired {len(repair_result['ledger_rows_repaired'])} ledger rows and "
            f"{len(repair_result['execution_orders_repaired'])} execution orders"
        )
        st.rerun()

    if invalid_simulation_trades:
        st.markdown("**Invalid Simulation Trades**")
        st.warning("These ledger rows exceed the available imported quantity and are ignored by the effective portfolio overlay until repaired.")
        st.dataframe(
            [
                {
                    "Ledger ID": row["id"],
                    "Created": row["created_at"],
                    "Symbol": row["symbol"],
                    "Side": row["side"],
                    "Quantity": row["quantity"],
                    "Execution Order ID": row["execution_order_id"],
                    "Execution Order Status": row["execution_order_status"] or "",
                    "Validation": row["validation_note"],
                }
                for row in invalid_simulation_trades
            ],
            width="stretch",
            hide_index=True,
        )

    if config["execution"]["mode"] == "live" and pending_approvals:
        st.markdown("**Pending Live Approvals**")
        for order in pending_approvals:
            cols = st.columns([3, 2, 2, 2, 2])
            cols[0].write(f"{order['action']} {order['symbol']}")
            cols[1].write(f"Qty {_format_qty(order['quantity'])}")
            cols[2].write(_format_money(order["price_local"], order["currency"]))
            cols[3].write(_format_dkk(order["estimated_value_dkk"]))
            if cols[4].button("Approve", key=f"approve-{order['id']}"):
                result = execute_order(order["id"], config=config, connection=connection, approved=True)
                st.success(f"Order {order['id']} status: {result['status']}")
                st.rerun()

    manageable_orders = [
        row
        for row in execution_orders
        if row["mode"] == "live"
        and row["status"] in {
            "submitted_to_broker",
            "broker_working",
            "broker_amended",
            "broker_partially_filled",
            "broker_replace_requested",
            "broker_cancel_requested",
        }
    ]
    if config["execution"]["mode"] == "live" and manageable_orders:
        st.markdown("**Manage Live Broker Orders**")
        for order in manageable_orders[:20]:
            cols = st.columns([3, 2, 2, 2, 2, 2])
            cols[0].write(f"{order['id']} {order['action']} {order['symbol']}")
            cols[1].write(f"Status {order['status']}")
            replace_qty = cols[2].number_input(
                "Qty",
                min_value=0.0,
                value=float(order["quantity"] or 0.0),
                step=1.0,
                key=f"replace-qty-{order['id']}",
            )
            replace_price = cols[3].number_input(
                "Price",
                min_value=0.0,
                value=float(order["price_local"] or 0.0),
                step=0.01,
                key=f"replace-price-{order['id']}",
            )
            if cols[4].button("Replace", key=f"replace-btn-{order['id']}"):
                result = manage_live_order(
                    order["id"],
                    management_action="replace",
                    config=config,
                    connection=connection,
                    new_quantity=replace_qty,
                    new_price=replace_price if replace_price > 0 else None,
                )
                st.success(f"Order {order['id']} status: {result['status']}")
                st.rerun()
            if cols[5].button("Cancel", key=f"cancel-btn-{order['id']}"):
                result = manage_live_order(
                    order["id"],
                    management_action="cancel",
                    config=config,
                    connection=connection,
                )
                st.success(f"Order {order['id']} status: {result['status']}")
                st.rerun()

    if execution_orders:
        st.dataframe(
            [
                {
                    "ID": row["id"],
                    "Created": row["created_at"],
                    "Symbol": row["symbol"],
                    "Action": row["action"],
                    "Mode": row["mode"],
                    "Status": row["status"],
                    "Adapter": row["adapter"],
                    "Target Weight": _format_pct(row["requested_weight_pct"]),
                    "Quantity": _format_qty(row["quantity"]),
                    "Price": _format_money(row["price_local"], row["currency"]),
                    "Estimated Value DKK": _format_dkk(row["estimated_value_dkk"]),
                    "Broker Order ID": row["broker_order_id"],
                    "Ledger ID": row["ledger_id"],
                    "Error": row["error_text"],
                }
                for row in execution_orders
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No execution orders have been created yet.")

    st.markdown("**Broker Fill Records**")
    if execution_fills:
        st.dataframe(
            [
                {
                    "ID": row["id"],
                    "Order ID": row["execution_order_id"],
                    "Broker Order ID": row["broker_order_id"],
                    "Symbol": row["symbol"],
                    "Side": row["side"],
                    "Fill Status": row["fill_status"],
                    "Cumulative Qty": _format_qty(row["cumulative_quantity"]),
                    "Delta Qty": _format_qty(row["delta_quantity"]),
                    "Avg Price": _format_money(row["average_price_local"], row["currency"]),
                    "Ledger ID": row["ledger_id"],
                }
                for row in execution_fills
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No broker fill records have been synchronized yet.")

    st.markdown("**Broker Order Events**")
    if execution_events:
        st.dataframe(
            [
                {
                    "ID": row["id"],
                    "Created": row["created_at"],
                    "Order ID": row["execution_order_id"],
                    "Broker Order ID": row["broker_order_id"],
                    "Event": row["event_type"],
                    "Broker Status": row["broker_status"],
                    "Broker Substatus": row["broker_substatus"],
                    "Broker Qty": _format_qty(row["broker_quantity"]),
                    "Broker Price": row["broker_price_local"],
                }
                for row in execution_events
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No broker lifecycle events have been synchronized yet.")

with tab_notifications:
    st.subheader("Notifications")
    notif_col1, notif_col2, notif_col3, notif_col4, notif_col5 = st.columns(5)
    notif_col1.metric("Daily Enabled", "Yes" if config["notifications"]["daily_summary_enabled"] else "No")
    notif_col2.metric("Weekly Enabled", "Yes" if config["notifications"]["weekly_summary_enabled"] else "No")
    notif_col3.metric("Monthly Enabled", "Yes" if config["notifications"]["monthly_summary_enabled"] else "No")
    notif_col4.metric("Quarterly Enabled", "Yes" if config["notifications"].get("quarterly_summary_enabled") else "No")
    notif_col5.metric("YTD Enabled", "Yes" if config["notifications"].get("ytd_summary_enabled") else "No")

    alert_col1, alert_col2, alert_col3, alert_col4, alert_col5, alert_col6, alert_col7 = st.columns(7)
    alerts_cfg = config["notifications"].get("alerts", {})
    alert_col1.metric("Exec Success Alerts", "Yes" if alerts_cfg.get("execution_success_enabled") else "No")
    alert_col2.metric("Exec Warning Alerts", "Yes" if alerts_cfg.get("execution_warning_enabled") else "No")
    alert_col3.metric("Fill Alerts", "Yes" if alerts_cfg.get("broker_fill_enabled") else "No")
    alert_col4.metric("Reject Alerts", "Yes" if alerts_cfg.get("broker_reject_enabled") else "No")
    alert_col5.metric("Cancel Alerts", "Yes" if alerts_cfg.get("broker_cancel_enabled") else "No")
    alert_col6.metric("Execution Failure Alerts", "Yes" if alerts_cfg.get("execution_failure_enabled") else "No")
    alert_col7.metric("Mgmt Failure Alerts", "Yes" if alerts_cfg.get("broker_management_failure_enabled") else "No")

    suppression_cfg = config["notifications"].get("alert_suppression", {})
    suppress_col1, suppress_col2, suppress_col3, suppress_col4 = st.columns(4)
    suppress_col1.metric("Suppression Enabled", "Yes" if suppression_cfg.get("enabled", True) else "No")
    suppress_col2.metric("Low Cooldown", f"{int(suppression_cfg.get('low_cooldown_minutes', 240))}m")
    suppress_col3.metric("Medium Cooldown", f"{int(suppression_cfg.get('medium_cooldown_minutes', 60))}m")
    suppress_col4.metric("High Cooldown", f"{int(suppression_cfg.get('high_cooldown_minutes', 0))}m")

    grouping_cfg = config["notifications"].get("alert_grouping", {})
    grouping_col1, grouping_col2 = st.columns(2)
    grouping_col1.metric("Grouping Enabled", "Yes" if grouping_cfg.get("enabled", True) else "No")
    grouping_col2.metric("Max Items Per Group", int(grouping_cfg.get("max_items_per_group", 5)))

    action_col1, action_col2 = st.columns(2)
    if action_col1.button("Send All Digests Now"):
        with st.spinner("Dispatching summaries..."):
            summary_result = dispatch_summaries_if_due(connection, config, force=True)
        st.success(f"Summary dispatch status: {summary_result['status']}")
        st.rerun()
    if action_col2.button("Send Broker Alerts Now"):
        with st.spinner("Dispatching broker alerts..."):
            alert_result = dispatch_broker_alerts_if_due(connection, config, force=True)
        st.success(f"Broker alert dispatch status: {alert_result['status']}")
        st.rerun()

    st.markdown("**Daily Summary Preview**")
    st.caption(daily_summary_preview["subject"])
    st.code(daily_summary_preview["message_text"], language="text")

    preview_col1, preview_col2 = st.columns(2)
    with preview_col1:
        st.markdown("**Weekly Digest Preview**")
        st.caption(weekly_summary_preview["subject"])
        st.code(weekly_summary_preview["message_text"], language="text")
    with preview_col2:
        st.markdown("**Monthly Digest Preview**")
        st.caption(monthly_summary_preview["subject"])
        st.code(monthly_summary_preview["message_text"], language="text")

    preview_col3, preview_col4 = st.columns(2)
    with preview_col3:
        st.markdown("**Quarterly Digest Preview**")
        st.caption(quarterly_summary_preview["subject"])
        st.code(quarterly_summary_preview["message_text"], language="text")
    with preview_col4:
        st.markdown("**Year-to-Date Digest Preview**")
        st.caption(ytd_summary_preview["subject"])
        st.code(ytd_summary_preview["message_text"], language="text")

    route_rows = []
    for summary_kind, route_cfg in sorted(config["notifications"].get("routes", {}).items()):
        if route_cfg:
            route_rows.append(
                {
                    "Kind": summary_kind,
                    "Profile": route_cfg.get("profile") or "",
                    "Slack Webhook Override": "Yes" if route_cfg.get("slack_webhook_url") else "No",
                    "Email Recipients Override": "Yes" if route_cfg.get("email_to_addresses_csv") else "No",
                    "Subject Prefix": route_cfg.get("subject_prefix") or "",
                    "Message Preamble": "Yes" if route_cfg.get("message_preamble") else "No",
                    "Summary Style": route_cfg.get("summary_style") or "",
                }
            )
    st.markdown("**Route Overrides**")
    if route_rows:
        st.dataframe(route_rows, width="stretch", hide_index=True)
    else:
        st.caption("No per-kind delivery route overrides are configured.")

    profile_rows = []
    for profile_name, profile_cfg in sorted(config["notifications"].get("route_profiles", {}).items()):
        if profile_cfg:
            profile_rows.append(
                {
                    "Profile": profile_name,
                    "Slack Webhook": "Yes" if profile_cfg.get("slack_webhook_url") else "No",
                    "Email Recipients": "Yes" if profile_cfg.get("email_to_addresses_csv") else "No",
                    "Subject Prefix": profile_cfg.get("subject_prefix") or "",
                    "Message Preamble": "Yes" if profile_cfg.get("message_preamble") else "No",
                    "Summary Style": profile_cfg.get("summary_style") or "",
                }
            )
    st.markdown("**Route Profiles**")
    if profile_rows:
        st.dataframe(profile_rows, width="stretch", hide_index=True)
    else:
        st.caption("No named route profiles are configured.")

    st.markdown("**Delivery History**")
    if notification_deliveries:
        st.dataframe(
            [
                {
                    "ID": row["id"],
                    "Created": row["created_at"],
                    "Summary Date": row["summary_date"],
                    "Kind": row.get("summary_kind", "daily"),
                    "Channel": row["channel"],
                    "Status": row["status"],
                    "Subject": row["subject"],
                    "Error": row["error_text"],
                }
                for row in notification_deliveries
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No notification deliveries have been recorded yet.")

connection.close()
