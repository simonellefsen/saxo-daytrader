from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests


ENVIRONMENT_SETTINGS = {
    "sim": {
        "auth_base_url": "https://sim.logonvalidation.net",
        "openapi_base_url": "https://gateway.saxobank.com/sim/openapi",
    },
    "live": {
        "auth_base_url": "https://live.logonvalidation.net",
        "openapi_base_url": "https://gateway.saxobank.com/openapi",
    },
}

TRADABLE_ASSET_TYPES = ("Stock", "Etf", "Etn", "Etc")
TOKEN_SAFETY_MARGIN_SECONDS = 60
EXCHANGE_ID_MAP = {
    "xnas": "XNAS",
    "xnys": "XNYS",
    "xcse": "XCSE",
    "xsto": "XSTO",
    "xosl": "XOSL",
    "xhel": "XHEL",
    "xlon": "XLON",
    "xetr": "XETR",
    "xfra": "XFRA",
    "xmil": "XMIL",
    "xpar": "XPAR",
    "xams": "XAMS",
    "xbru": "XBRU",
    "xlse": "XLIS",
}

EXCHANGE_ALIASES = {
    "xnas": {"XNAS", "NASDAQ"},
    "xnys": {"XNYS", "NYSE"},
    "xcse": {"XCSE", "CSE", "COP"},
    "xsto": {"XSTO", "STO", "STK"},
    "xosl": {"XOSL", "OSL", "OSE"},
    "xhel": {"XHEL", "HEL", "HEX"},
    "xlon": {"XLON", "LSE", "LON"},
    "xetr": {"XETR", "XTRA", "ETR"},
    "xfra": {"XFRA", "FSE", "FRA"},
    "xmil": {"XMIL", "MIL"},
    "xpar": {"XPAR", "PAR"},
    "xams": {"XAMS", "AMS"},
    "xbru": {"XBRU", "BRU"},
    "xlse": {"XLIS", "LIS"},
}


class SaxoSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SaxoInstrument:
    symbol: str
    uic: int
    asset_type: str
    exchange_id: str
    description: str
    tradable_as: list[str]
    currency_code: str | None
    isin_code: str | None


class SaxoOrderNotFoundError(SaxoSessionError):
    pass


def _response_json_or_none(response: requests.Response) -> dict[str, Any] | list[Any] | None:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_saxo_error(payload: Any) -> str | None:
    if isinstance(payload, dict):
        error_info = payload.get("ErrorInfo")
        if isinstance(error_info, dict):
            code = error_info.get("ErrorCode")
            message = error_info.get("Message")
            if code and message:
                return f"{code}: {message}"
            if message:
                return str(message)
            if code:
                return str(code)
        orders = payload.get("Orders")
        if isinstance(orders, list):
            for item in orders:
                nested = _extract_saxo_error(item)
                if nested:
                    return nested
        message = payload.get("Message") or payload.get("message") or payload.get("error_description")
        if message:
            return str(message)
    if isinstance(payload, list):
        for item in payload:
            nested = _extract_saxo_error(item)
            if nested:
                return nested
    return None


def _raise_for_saxo_response(response: requests.Response, *, action: str) -> dict[str, Any]:
    payload = _response_json_or_none(response)
    error_text = _extract_saxo_error(payload)
    status_code = int(getattr(response, "status_code", 200))
    response_text = str(getattr(response, "text", "") or "")
    if status_code >= 400:
        if status_code == 404 and error_text and "OrderNotFound" in error_text:
            raise SaxoOrderNotFoundError(error_text)
        if error_text:
            raise SaxoSessionError(f"{action} failed: {error_text}")
        snippet = response_text.strip()
        if snippet:
            raise SaxoSessionError(f"{action} failed: HTTP {status_code}: {snippet[:300]}")
        raise SaxoSessionError(f"{action} failed: HTTP {status_code}")
    if error_text:
        if "OrderNotFound" in error_text:
            raise SaxoOrderNotFoundError(error_text)
        raise SaxoSessionError(f"{action} failed: {error_text}")
    if isinstance(payload, dict):
        return payload
    return {}


def default_session_path(config: dict[str, Any]) -> Path:
    config_dir = Path(config["_meta"]["config_dir"])
    return config_dir / ".secrets" / "saxo_session.json"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def load_session(session_path: str | Path) -> dict[str, Any]:
    path = Path(session_path)
    if not path.exists():
        raise SaxoSessionError(f"Saxo session file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(session_path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(session_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def session_is_access_token_valid(session: dict[str, Any]) -> bool:
    expires_at = _parse_iso(session.get("access_token_expires_at"))
    return bool(session.get("access_token")) and expires_at is not None and expires_at > _now_utc() + timedelta(seconds=TOKEN_SAFETY_MARGIN_SECONDS)


def session_can_refresh(session: dict[str, Any]) -> bool:
    refresh_expires_at = _parse_iso(session.get("refresh_token_expires_at"))
    return bool(session.get("refresh_token")) and refresh_expires_at is not None and refresh_expires_at > _now_utc() + timedelta(seconds=TOKEN_SAFETY_MARGIN_SECONDS)


def _refresh_access_token(session: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    environment = str(session.get("environment") or config["saxo"]["environment"]).lower()
    client_id = config["saxo"]["client_id"]
    client_secret = config["saxo"].get("client_secret", "")
    if not client_id:
        raise SaxoSessionError("SAXO_CLIENT_ID is missing")
    if not session_can_refresh(session):
        raise SaxoSessionError("No valid refresh token is available. Re-run the Saxo OAuth helper.")

    token_url = f"{ENVIRONMENT_SETTINGS[environment]['auth_base_url']}/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": session["refresh_token"],
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = None

    # PKCE apps use client_id in-body and may require the original code_verifier.
    if session.get("auth_mode") == "pkce":
        data["client_id"] = client_id
        if session.get("code_verifier"):
            data["code_verifier"] = session["code_verifier"]
        if session.get("redirect_uri"):
            data["redirect_uri"] = session["redirect_uri"]
    else:
        if not client_secret:
            raise SaxoSessionError("SAXO_CLIENT_SECRET is missing for secret-based token refresh")
        auth = (client_id, client_secret)
        if session.get("redirect_uri"):
            data["redirect_uri"] = session["redirect_uri"]

    response = requests.post(token_url, data=data, headers=headers, auth=auth, timeout=30)
    response.raise_for_status()
    token_response = response.json()
    refreshed = {
        **session,
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token", session.get("refresh_token")),
        "token_type": token_response.get("token_type", "Bearer"),
        "access_token_expires_at": _to_iso(_now_utc() + timedelta(seconds=int(token_response.get("expires_in", 0)))),
        "refresh_token_expires_at": _to_iso(_now_utc() + timedelta(seconds=int(token_response.get("refresh_token_expires_in", 0)))),
        "last_refreshed_at": _to_iso(_now_utc()),
    }
    return refreshed


def ensure_access_token(config: dict[str, Any], session_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(session_path or default_session_path(config))
    session = load_session(path)
    if session_is_access_token_valid(session):
        return session
    refreshed = _refresh_access_token(session, config)
    save_session(path, refreshed)
    return refreshed


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _openapi_base_url(environment: str) -> str:
    return ENVIRONMENT_SETTINGS[environment.lower()]["openapi_base_url"]


def _account_key(config: dict[str, Any], session: dict[str, Any]) -> str:
    account_key = config["saxo"].get("account_key") or session.get("account_key")
    if not account_key:
        raise SaxoSessionError("SAXO_ACCOUNT_KEY is missing. Re-run the Saxo OAuth helper with --write-env or --write-session.")
    return str(account_key)


def _client_key(config: dict[str, Any], session: dict[str, Any]) -> str:
    client_key = config["saxo"].get("client_key") or session.get("client_key")
    if not client_key:
        raise SaxoSessionError("SAXO_CLIENT_KEY is missing. Re-run the Saxo OAuth helper with --write-env or --write-session.")
    return str(client_key)


def _symbol_parts(symbol: str) -> tuple[str, str]:
    base, _, exchange = symbol.partition(":")
    return base.strip().upper(), exchange.strip().lower()


def _symbol_with_suffix(symbol: str) -> str:
    base_symbol, exchange_code = _symbol_parts(symbol)
    return f"{base_symbol}:{exchange_code}" if exchange_code else base_symbol


def _exchange_aliases(exchange_code: str) -> set[str]:
    aliases = EXCHANGE_ALIASES.get(exchange_code, {exchange_code.upper()})
    return {value.upper() for value in aliases if value}


def _candidate_score(candidate: dict[str, Any], *, requested_symbol: str, base_symbol: str, exchange_code: str) -> tuple[int, int, int]:
    candidate_symbol = str(candidate.get("Symbol", "")).strip().upper()
    candidate_exchange = str(candidate.get("ExchangeId", "")).strip().upper()
    aliases = _exchange_aliases(exchange_code)
    exact_symbol = int(candidate_symbol == requested_symbol.upper())
    exact_base = int(candidate_symbol.split(":", 1)[0] == base_symbol)
    exchange_match = int(candidate_exchange in aliases or candidate_symbol.endswith(f":{exchange_code.upper()}"))
    tradable_as = candidate.get("TradableAs", []) or []
    stock_preferred = int("Stock" in {str(value) for value in tradable_as})
    return (exact_symbol, exchange_match, exact_base + stock_preferred)


def lookup_instrument(symbol: str, config: dict[str, Any], session: dict[str, Any]) -> SaxoInstrument:
    base_symbol, exchange_code = _symbol_parts(symbol)
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/ref/v1/instruments",
        params={
            "$top": 50,
            "AccountKey": _account_key(config, session),
            "AssetTypes": ",".join(TRADABLE_ASSET_TYPES),
            "IncludeNonTradable": "false",
            "Keywords": base_symbol,
        },
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    response.raise_for_status()
    candidates = response.json().get("Data", [])
    selected = None
    if candidates:
        requested_symbol = _symbol_with_suffix(symbol)
        ranked = sorted(
            candidates,
            key=lambda item: _candidate_score(
                item,
                requested_symbol=requested_symbol,
                base_symbol=base_symbol,
                exchange_code=exchange_code,
            ),
            reverse=True,
        )
        best = ranked[0]
        if _candidate_score(best, requested_symbol=requested_symbol, base_symbol=base_symbol, exchange_code=exchange_code) > (0, 0, 0):
            selected = best
    if not selected:
        raise SaxoSessionError(f"No tradable Saxo instrument match found for {symbol}")
    return SaxoInstrument(
        symbol=symbol,
        uic=int(selected["Identifier"]),
        asset_type=str(selected["AssetType"]),
        exchange_id=str(selected.get("ExchangeId", EXCHANGE_ID_MAP.get(exchange_code, exchange_code.upper()))),
        description=str(selected.get("Description", symbol)),
        tradable_as=[str(value) for value in selected.get("TradableAs", [])],
        currency_code=selected.get("CurrencyCode"),
        isin_code=(
            selected.get("IsinCode")
            or (selected.get("DisplayAndFormat", {}) or {}).get("IsinCode")
        ),
    )


def build_market_order_payload(
    *,
    symbol: str,
    action: str,
    quantity: float,
    external_reference: str,
    config: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    instrument = lookup_instrument(symbol, config, session)
    whole_quantity = int(quantity)
    if whole_quantity <= 0:
        raise SaxoSessionError("Order quantity must be at least 1 whole share")
    return {
        "AccountKey": _account_key(config, session),
        "Amount": whole_quantity,
        "AssetType": instrument.asset_type,
        "BuySell": "Buy" if action == "BUY" else "Sell",
        "ExternalReference": external_reference[:50],
        "ManualOrder": True,
        "OrderDuration": {"DurationType": "DayOrder"},
        "OrderType": "Market",
        "Uic": instrument.uic,
    }


def precheck_order(payload: dict[str, Any], config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    request_payload = {
        **payload,
        "FieldGroups": ["Costs", "MarginImpactBuySell"],
    }
    response = requests.post(
        f"{base_url}/trade/v2/orders/precheck",
        headers=_auth_headers(session["access_token"]),
        json=request_payload,
        timeout=30,
    )
    return _raise_for_saxo_response(response, action="Order precheck")


def place_order(payload: dict[str, Any], config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.post(
        f"{base_url}/trade/v2/orders",
        headers=_auth_headers(session["access_token"]),
        json=payload,
        timeout=30,
    )
    return _raise_for_saxo_response(response, action="Order placement")


def get_balance_snapshot(config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/port/v1/balances/me",
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    return _raise_for_saxo_response(response, action="Balance snapshot")


def get_positions_snapshot(
    config: dict[str, Any],
    session: dict[str, Any],
    *,
    top: int = 200,
) -> list[dict[str, Any]]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/port/v1/positions/me",
        params={
            "$top": int(top),
            "FieldGroups": "DisplayAndFormat,PositionBase,PositionView",
        },
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    payload = _raise_for_saxo_response(response, action="Positions snapshot")
    return [dict(row) for row in payload.get("Data", [])]


def get_accounts_snapshot(
    config: dict[str, Any],
    session: dict[str, Any],
    *,
    top: int = 100,
) -> list[dict[str, Any]]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/port/v1/accounts/me",
        params={"$top": int(top)},
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    payload = _raise_for_saxo_response(response, action="Accounts snapshot")
    return [dict(row) for row in payload.get("Data", [])]


def get_instrument_exposures(
    config: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/port/v1/exposure/instruments/me",
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    payload = _response_json_or_none(response)
    status_code = int(getattr(response, "status_code", 200))
    if status_code >= 400:
        error_text = _extract_saxo_error(payload)
        if error_text:
            raise SaxoSessionError(f"Instrument exposures failed: {error_text}")
        raise SaxoSessionError(f"Instrument exposures failed: HTTP {status_code}")
    if isinstance(payload, list):
        return [dict(row) for row in payload]
    return [dict(row) for row in (payload or {}).get("Data", [])]


def change_order(payload: dict[str, Any], config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.patch(
        f"{base_url}/trade/v2/orders",
        headers=_auth_headers(session["access_token"]),
        json=payload,
        timeout=30,
    )
    return _raise_for_saxo_response(response, action="Order replace")


def cancel_order(order_id: str, config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.delete(
        f"{base_url}/trade/v2/orders/{order_id}",
        params={"AccountKey": _account_key(config, session)},
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    return _raise_for_saxo_response(response, action="Order cancel")


def get_open_order(order_id: str, config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/port/v1/orders/{_client_key(config, session)}/{order_id}",
        params={"FieldGroups": "DisplayAndFormat"},
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    if response.status_code == 404:
        raise SaxoOrderNotFoundError(f"Saxo open order {order_id} was not found in open orders")
    response.raise_for_status()
    payload = response.json()
    data = payload.get("Data", [])
    if not data:
        raise SaxoOrderNotFoundError(f"Saxo open order {order_id} returned no rows")
    return data[0]


def get_order_activity_last(order_id: str, config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/cs/v1/audit/orderactivities",
        params={
            "AccountKey": _account_key(config, session),
            "ClientKey": _client_key(config, session),
            "EntryType": "Last",
            "OrderId": order_id,
        },
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("Data", [])
    if not data:
        raise SaxoOrderNotFoundError(f"Saxo order activity {order_id} returned no rows")
    return data[0]
