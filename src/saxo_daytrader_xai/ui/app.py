from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import connect, init_db
from saxo_daytrader_xai.execution_engine import (
    execute_order,
    export_audit_bundle,
    fetch_execution_fills,
    fetch_execution_orders,
    queue_and_maybe_execute_latest_report,
    sync_broker_order_statuses,
)
from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.market_news import fetch_market_intelligence
from saxo_daytrader_xai.market_schedule import get_market_status, summarize_analysis_window
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
summary = fetch_portfolio_summary(connection, batch_id=batch_id)
positions = fetch_portfolio_positions(connection, batch_id=batch_id)
portfolio_symbols = fetch_portfolio_symbols(connection, batch_id=batch_id)
trade_ledger = fetch_trade_ledger(connection)
tax_summary = fetch_realised_tax_summary(connection, tax_year=2026)
watchlists = _load_watchlists(config_path)
watchlist_symbols = [row["symbol"] for row in watchlists["nordic"][:8]] + [row["symbol"] for row in watchlists["global"][:8]]
market_intelligence = _load_market_intelligence(config_path, tuple(portfolio_symbols[:10]), tuple(watchlist_symbols))
market_status_rows = get_market_status(config)
analysis_summary = summarize_analysis_window(market_status_rows)
latest_decision_report = fetch_latest_decision_report(connection)

if should_auto_run_decision_report(connection, config, analysis_summary["analysis_window_active"]):
    with st.spinner("Generating xAI decision report..."):
        generated_report = generate_decision_report(config=config, connection=connection)
        queue_and_maybe_execute_latest_report(config=config, connection=connection)
        latest_decision_report = fetch_latest_decision_report(connection)
        st.toast(f"Decision report generated with status: {generated_report['status']}")

st.title("saxo-daytrader-xai")
st.caption("Phase 7 dashboard with decision automation, simulation execution, Saxo live submission, broker sync, and audit exports.")

excluded_symbols = ", ".join(config.get("risk", {}).get("excluded_symbols", []))
st.info(f"Excluded symbols enforced globally: {excluded_symbols}")

if analysis_summary["analysis_window_active"]:
    st.success(f"Analysis window active: {', '.join(analysis_summary['active_markets'])}")
else:
    st.warning("Analysis window inactive right now.")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Positions", summary["position_count"])
col2.metric("Portfolio Value", _format_dkk(summary["total_market_value_dkk"]))
col3.metric("Cost Basis", _format_dkk(summary["total_cost_basis_dkk"]))
col4.metric("Unrealised P/L", _format_dkk(summary["total_unrealised_pnl_dkk"]))

tab_portfolio, tab_watchlist, tab_news, tab_market, tab_decision, tab_execution = st.tabs(
    ["Portfolio", "Watchlist", "News", "Market Status", "Decision Report", "Execution"]
)

with tab_portfolio:
    st.subheader("Portfolio Snapshot")
    col5, col6 = st.columns(2)
    col5.metric("Daily P/L", _format_dkk(summary["total_daily_pnl_dkk"]))
    col6.metric("Latest Batch", batch_id or "No imports yet")

    if positions:
        st.dataframe(
            [
                {
                    "Symbol": row["symbol"],
                    "Instrument": row["instrument_name"],
                    "ISIN": row["isin"],
                    "Qty": row["quantity"],
                    "Currency": row["currency"],
                    "Open Price": row["open_price_local"],
                    "Current Price": row["current_price_local"],
                    "Cost Basis DKK": _format_dkk(row["cost_basis_dkk"]),
                    "Market Value DKK": _format_dkk(row["market_value_dkk"]),
                    "Unrealised P/L DKK": _format_dkk(row["unrealised_pnl_dkk"]),
                    "Daily P/L DKK": _format_dkk(row["daily_pnl_dkk"]),
                    "Allocation": _format_pct(row["allocation_pct"]),
                    "Asset Class": row["asset_class"],
                    "Market": row["market_status"],
                    "Value Date": row["value_date"],
                }
                for row in positions
            ],
            width="stretch",
            hide_index=True,
        )
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
        st.dataframe(trade_ledger, width="stretch", hide_index=True)
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
            queue_and_maybe_execute_latest_report(config=config, connection=connection)
            latest_decision_report = fetch_latest_decision_report(connection)
        st.success(f"Decision report status: {result['status']}")
        st.rerun()
    if button_col2.button("Run Mock Decision"):
        with st.spinner("Generating mock decision report..."):
            result = generate_decision_report(config=config, connection=connection, force_mock=True)
            queue_and_maybe_execute_latest_report(config=config, connection=connection)
            latest_decision_report = fetch_latest_decision_report(connection)
        st.info(f"Mock decision report status: {result['status']}")
        st.rerun()
    if button_col3.button("Queue Latest Suggestions"):
        with st.spinner("Queuing latest report suggestions..."):
            queue_result = queue_and_maybe_execute_latest_report(config=config, connection=connection)
        st.info(f"Queue result: {queue_result['status']}")
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
    pending_approvals = [row for row in execution_orders if row["status"] == "pending_approval"]
    executed_orders = [row for row in execution_orders if row["status"] == "executed"]

    mode_col1, mode_col2, mode_col3, mode_col4 = st.columns(4)
    mode_col1.metric("Execution Mode", str(config["execution"]["mode"]).upper())
    mode_col2.metric("Queued Orders", len(execution_orders))
    mode_col3.metric("Pending Approval", len(pending_approvals))
    mode_col4.metric("Fill Records", len(execution_fills))

    st.caption(
        f"Adapter={config['execution']['adapter']} | dry_run={config['app']['dry_run']} | "
        f"max_daily_orders={config['execution']['max_daily_orders']}"
    )
    if config["execution"]["mode"] == "live":
        st.info("Approved live orders are submitted to Saxo and stored as broker submissions. They are not booked into the local trade ledger as executed fills yet.")

    action_col1, action_col2, action_col3 = st.columns(3)
    if action_col1.button("Run Queue Processor"):
        with st.spinner("Processing queued execution orders..."):
            queue_result = queue_and_maybe_execute_latest_report(config=config, connection=connection)
        st.success(f"Queue processor status: {queue_result['status']}")
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

    if config["execution"]["mode"] == "live" and pending_approvals:
        st.markdown("**Pending Live Approvals**")
        for order in pending_approvals:
            cols = st.columns([3, 2, 2, 2, 2])
            cols[0].write(f"{order['action']} {order['symbol']}")
            cols[1].write(f"Qty {order['quantity']:.4f}")
            cols[2].write(_format_money(order["price_local"], order["currency"]))
            cols[3].write(_format_dkk(order["estimated_value_dkk"]))
            if cols[4].button("Approve", key=f"approve-{order['id']}"):
                result = execute_order(order["id"], config=config, connection=connection, approved=True)
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
                    "Quantity": row["quantity"],
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
                    "Cumulative Qty": row["cumulative_quantity"],
                    "Delta Qty": row["delta_quantity"],
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

connection.close()
