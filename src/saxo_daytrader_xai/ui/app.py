from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.db import connect, init_db
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

st.title("saxo-daytrader-xai")
st.caption("Phase 2 dashboard with live prices, curated watchlists, headlines, earnings, and exchange analysis windows.")

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

tab_portfolio, tab_watchlist, tab_news, tab_market = st.tabs(["Portfolio", "Watchlist", "News", "Market Status"])

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
            use_container_width=True,
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
        st.dataframe(trade_ledger, use_container_width=True, hide_index=True)
    else:
        st.caption("No trades recorded yet. Trade execution arrives in later phases.")

with tab_watchlist:
    st.subheader("Daily Refreshed Watchlists")
    st.caption(f"Generated at {watchlists['generated_at']}")

    st.markdown("**Nordic Top 25**")
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
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**US / Europe Top 50**")
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
        use_container_width=True,
        hide_index=True,
    )

with tab_news:
    st.subheader("News and Events")
    st.caption(f"Generated at {market_intelligence['generated_at']}")

    st.markdown("**Market News**")
    market_news_rows = market_intelligence["market_news"]
    if market_news_rows:
        st.dataframe(market_news_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No market headlines were available.")

    st.markdown("**Earnings Calendar**")
    earnings_rows = market_intelligence["earnings_calendar"]
    if earnings_rows:
        st.dataframe(earnings_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No upcoming earnings events were returned for the current focus list.")

    st.markdown("**Macro Events / Central Bank Headlines**")
    macro_rows = market_intelligence["macro_events"]
    if macro_rows:
        st.dataframe(macro_rows, use_container_width=True, hide_index=True)
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
                "Open": row["is_open"],
                "Analysis Window Active": row["analysis_window_active"],
                "Analysis Window Start": row["analysis_window_start"],
                "Analysis Window End": row["analysis_window_end"],
                "Next Open": row["next_open"],
            }
            for row in market_status_rows
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.write(
        "The system marks a market as analysis-active when the current local exchange time is "
        "between 60 and 90 minutes after the exchange opens."
    )

connection.close()
