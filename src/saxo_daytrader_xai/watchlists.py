from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from saxo_daytrader_xai.market_data import fetch_live_prices
from saxo_daytrader_xai.market_symbols import SymbolSpec


NORDIC_UNIVERSE: list[SymbolSpec] = [
    SymbolSpec("ORSTED:xcse", "ORSTED.CO", "Orsted", "xcse", "Nordics", "DKK"),
    SymbolSpec("MAERSK-B:xcse", "MAERSK-B.CO", "A.P. Moller - Maersk B", "xcse", "Nordics", "DKK"),
    SymbolSpec("DSV:xcse", "DSV.CO", "DSV", "xcse", "Nordics", "DKK"),
    SymbolSpec("CARL-B:xcse", "CARL-B.CO", "Carlsberg B", "xcse", "Nordics", "DKK"),
    SymbolSpec("COLO-B:xcse", "COLO-B.CO", "Coloplast B", "xcse", "Nordics", "DKK"),
    SymbolSpec("DEMANT:xcse", "DEMANT.CO", "Demant", "xcse", "Nordics", "DKK"),
    SymbolSpec("VWS:xcse", "VWS.CO", "Vestas", "xcse", "Nordics", "DKK"),
    SymbolSpec("PNDORA:xcse", "PNDORA.CO", "Pandora", "xcse", "Nordics", "DKK"),
    SymbolSpec("GN:xcse", "GN.CO", "GN Store Nord", "xcse", "Nordics", "DKK"),
    SymbolSpec("NZYM-B:xcse", "NZYM-B.CO", "Novonesis B", "xcse", "Nordics", "DKK"),
    SymbolSpec("NETC:xcse", "NETC.CO", "Netcompany", "xcse", "Nordics", "DKK"),
    SymbolSpec("TRYG:xcse", "TRYG.CO", "Tryg", "xcse", "Nordics", "DKK"),
    SymbolSpec("ISS:xcse", "ISS.CO", "ISS", "xcse", "Nordics", "DKK"),
    SymbolSpec("BAVA:xcse", "BAVA.CO", "Bavarian Nordic", "xcse", "Nordics", "DKK"),
    SymbolSpec("ALK-B:xcse", "ALK-B.CO", "ALK-Abello B", "xcse", "Nordics", "DKK"),
    SymbolSpec("NNIT:xcse", "NNIT.CO", "NNIT", "xcse", "Nordics", "DKK"),
    SymbolSpec("DNB:xosl", "DNB.OL", "DNB Bank", "xosl", "Nordics", "NOK"),
    SymbolSpec("EQNR:xosl", "EQNR.OL", "Equinor", "xosl", "Nordics", "NOK"),
    SymbolSpec("SALM:xosl", "SALM.OL", "Salmar", "xosl", "Nordics", "NOK"),
    SymbolSpec("MOWI:xosl", "MOWI.OL", "Mowi", "xosl", "Nordics", "NOK"),
    SymbolSpec("AKRBP:xosl", "AKRBP.OL", "Aker BP", "xosl", "Nordics", "NOK"),
    SymbolSpec("VOLV-B:xsto", "VOLV-B.ST", "Volvo B", "xsto", "Nordics", "SEK"),
    SymbolSpec("ATCO-A:xsto", "ATCO-A.ST", "Atlas Copco A", "xsto", "Nordics", "SEK"),
    SymbolSpec("ABB:xsto", "ABB.ST", "ABB", "xsto", "Nordics", "SEK"),
    SymbolSpec("ERIC-B:xsto", "ERIC-B.ST", "Ericsson B", "xsto", "Nordics", "SEK"),
    SymbolSpec("HEXA-B:xsto", "HEXA-B.ST", "Hexagon B", "xsto", "Nordics", "SEK"),
    SymbolSpec("NDA-SE:xsto", "NDA-SE.ST", "Nordea", "xsto", "Nordics", "SEK"),
    SymbolSpec("SAND:xsto", "SAND.ST", "Sandvik", "xsto", "Nordics", "SEK"),
    SymbolSpec("KNEBV:xhel", "KNEBV.HE", "Kone", "xhel", "Nordics", "EUR"),
    SymbolSpec("NESTE:xhel", "NESTE.HE", "Neste", "xhel", "Nordics", "EUR"),
    SymbolSpec("NOKIA:xhel", "NOKIA.HE", "Nokia", "xhel", "Nordics", "EUR"),
    SymbolSpec("UPM:xhel", "UPM.HE", "UPM-Kymmene", "xhel", "Nordics", "EUR"),
]


GLOBAL_UNIVERSE: list[SymbolSpec] = [
    SymbolSpec("AAPL:xnas", "AAPL", "Apple", "xnas", "Global", "USD"),
    SymbolSpec("MSFT:xnas", "MSFT", "Microsoft", "xnas", "Global", "USD"),
    SymbolSpec("NVDA:xnas", "NVDA", "NVIDIA", "xnas", "Global", "USD"),
    SymbolSpec("AMD:xnas", "AMD", "Advanced Micro Devices", "xnas", "Global", "USD"),
    SymbolSpec("GOOGL:xnas", "GOOGL", "Alphabet Class A", "xnas", "Global", "USD"),
    SymbolSpec("AMZN:xnas", "AMZN", "Amazon", "xnas", "Global", "USD"),
    SymbolSpec("META:xnas", "META", "Meta Platforms", "xnas", "Global", "USD"),
    SymbolSpec("PLTR:xnas", "PLTR", "Palantir", "xnas", "Global", "USD"),
    SymbolSpec("MSTR:xnas", "MSTR", "MicroStrategy", "xnas", "Global", "USD"),
    SymbolSpec("NFLX:xnas", "NFLX", "Netflix", "xnas", "Global", "USD"),
    SymbolSpec("AVGO:xnas", "AVGO", "Broadcom", "xnas", "Global", "USD"),
    SymbolSpec("ASML:xnas", "ASML", "ASML ADR", "xnas", "Global", "USD"),
    SymbolSpec("ADBE:xnas", "ADBE", "Adobe", "xnas", "Global", "USD"),
    SymbolSpec("QCOM:xnas", "QCOM", "Qualcomm", "xnas", "Global", "USD"),
    SymbolSpec("INTC:xnas", "INTC", "Intel", "xnas", "Global", "USD"),
    SymbolSpec("CSCO:xnas", "CSCO", "Cisco", "xnas", "Global", "USD"),
    SymbolSpec("AMAT:xnas", "AMAT", "Applied Materials", "xnas", "Global", "USD"),
    SymbolSpec("ARM:xnas", "ARM", "Arm Holdings ADR", "xnas", "Global", "USD"),
    SymbolSpec("FIGR:xnas", "FIGR", "FIGS", "xnas", "Global", "USD"),
    SymbolSpec("RIVN:xnas", "RIVN", "Rivian", "xnas", "Global", "USD"),
    SymbolSpec("JNJ:xnys", "JNJ", "Johnson & Johnson", "xnys", "Global", "USD"),
    SymbolSpec("JPM:xnys", "JPM", "JPMorgan Chase", "xnys", "Global", "USD"),
    SymbolSpec("V:xnys", "V", "Visa", "xnys", "Global", "USD"),
    SymbolSpec("MA:xnys", "MA", "Mastercard", "xnys", "Global", "USD"),
    SymbolSpec("AJG:xnys", "AJG", "Arthur J. Gallagher", "xnys", "Global", "USD"),
    SymbolSpec("ZTS:xnys", "ZTS", "Zoetis", "xnys", "Global", "USD"),
    SymbolSpec("LLY:xnys", "LLY", "Eli Lilly", "xnys", "Global", "USD"),
    SymbolSpec("GS:xnys", "GS", "Goldman Sachs", "xnys", "Global", "USD"),
    SymbolSpec("CAT:xnys", "CAT", "Caterpillar", "xnys", "Global", "USD"),
    SymbolSpec("GE:xnys", "GE", "GE Aerospace", "xnys", "Global", "USD"),
    SymbolSpec("UBER:xnys", "UBER", "Uber", "xnys", "Global", "USD"),
    SymbolSpec("SHOP:xnys", "SHOP", "Shopify", "xnys", "Global", "USD"),
    SymbolSpec("SAP:xetr", "SAP.DE", "SAP", "xetr", "Global", "EUR"),
    SymbolSpec("SIE:xetr", "SIE.DE", "Siemens", "xetr", "Global", "EUR"),
    SymbolSpec("AIR:xpar", "AIR.PA", "Airbus", "xpar", "Global", "EUR"),
    SymbolSpec("MC:xpar", "MC.PA", "LVMH", "xpar", "Global", "EUR"),
    SymbolSpec("OR:xpar", "OR.PA", "L'Oreal", "xpar", "Global", "EUR"),
    SymbolSpec("SAN:xpar", "SAN.PA", "Sanofi", "xpar", "Global", "EUR"),
    SymbolSpec("SU:xpar", "SU.PA", "Schneider Electric", "xpar", "Global", "EUR"),
    SymbolSpec("ABI:xbru", "ABI.BR", "AB InBev", "xbru", "Global", "EUR"),
    SymbolSpec("ASML:xams", "ASML.AS", "ASML Amsterdam", "xams", "Global", "EUR"),
    SymbolSpec("ADYEN:xams", "ADYEN.AS", "Adyen", "xams", "Global", "EUR"),
    SymbolSpec("RMS:xpar", "RMS.PA", "Hermes", "xpar", "Global", "EUR"),
    SymbolSpec("SHELL:xlon", "SHEL.L", "Shell", "xlon", "Global", "GBP"),
    SymbolSpec("AZN:xlon", "AZN.L", "AstraZeneca", "xlon", "Global", "GBP"),
    SymbolSpec("ULVR:xlon", "ULVR.L", "Unilever", "xlon", "Global", "GBP"),
    SymbolSpec("HSBA:xlon", "HSBA.L", "HSBC", "xlon", "Global", "GBP"),
    SymbolSpec("RIO:xlon", "RIO.L", "Rio Tinto", "xlon", "Global", "GBP"),
    SymbolSpec("RR:xlon", "RR.L", "Rolls-Royce", "xlon", "Global", "GBP"),
    SymbolSpec("QOMP:xetr", "QOMP.DE", "iShares MSCI World Quality Factor", "xetr", "Global", "EUR"),
    SymbolSpec("ARKI:xlon", "ARKI.L", "ARK AI & Robotics UCITS", "xlon", "Global", "USD"),
    SymbolSpec("ARKK:xmil", "ARKK.MI", "ARK Innovation UCITS", "xmil", "Global", "EUR"),
    SymbolSpec("ADI:xnas", "ADI", "Analog Devices", "xnas", "Global", "USD"),
    SymbolSpec("LMND:xnys", "LMND", "Lemonade", "xnys", "Global", "USD"),
]


def _rank_watchlist_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row["change_pct"] is None,
            -(row["change_pct"] or -999.0),
            -float(row["current_price"] or 0.0),
            row["symbol"],
        ),
    )


def _build_watchlist_rows(
    entries: list[SymbolSpec],
    config: dict[str, Any],
    excluded_symbols: set[str],
) -> list[dict[str, Any]]:
    filtered_entries = [entry for entry in entries if entry.symbol not in excluded_symbols]
    quotes = fetch_live_prices(
        [entry.symbol for entry in filtered_entries],
        timeout_seconds=config["market_data"]["request_timeout_seconds"],
        symbol_to_yahoo={entry.symbol: entry.yahoo_symbol for entry in filtered_entries},
    )
    quote_by_symbol = {row["symbol"]: row for row in quotes}
    rows = []
    for entry in filtered_entries:
        quote = quote_by_symbol.get(entry.symbol, {})
        rows.append(
            {
                "symbol": entry.symbol,
                "name": entry.name,
                "yahoo_symbol": entry.yahoo_symbol,
                "exchange": entry.exchange_code.upper(),
                "region": entry.region,
                "currency": entry.currency,
                "current_price": quote.get("current_price"),
                "change_pct": quote.get("change_pct"),
                "quote_source": quote.get("source", "unavailable"),
                "quote_status": quote.get("status", "No quote"),
            }
        )
    return _rank_watchlist_rows(rows)


def build_watchlists(config: dict[str, Any]) -> dict[str, Any]:
    excluded_symbols = set(config.get("risk", {}).get("excluded_symbols", []))
    nordic_rows = _build_watchlist_rows(NORDIC_UNIVERSE, config, excluded_symbols)
    global_rows = _build_watchlist_rows(GLOBAL_UNIVERSE, config, excluded_symbols)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "nordic": nordic_rows[: config["market_data"]["watchlists"]["nordic_limit"]],
        "global": global_rows[: config["market_data"]["watchlists"]["global_limit"]],
    }
