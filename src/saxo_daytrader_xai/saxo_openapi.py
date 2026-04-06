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


def _symbol_parts(symbol: str) -> tuple[str, str]:
    base, _, exchange = symbol.partition(":")
    return base.strip().upper(), exchange.strip().lower()


def lookup_instrument(symbol: str, config: dict[str, Any], session: dict[str, Any]) -> SaxoInstrument:
    base_symbol, exchange_code = _symbol_parts(symbol)
    exchange_id = EXCHANGE_ID_MAP.get(exchange_code, exchange_code.upper())
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.get(
        f"{base_url}/ref/v1/instruments",
        params={
            "$top": 20,
            "AccountKey": _account_key(config, session),
            "AssetTypes": ",".join(TRADABLE_ASSET_TYPES),
            "ExchangeId": exchange_id,
            "IncludeNonTradable": "false",
            "Keywords": base_symbol,
        },
        headers=_auth_headers(session["access_token"]),
        timeout=30,
    )
    response.raise_for_status()
    candidates = response.json().get("Data", [])
    exact_matches = [
        item
        for item in candidates
        if str(item.get("Symbol", "")).upper() == base_symbol and str(item.get("ExchangeId", "")).upper() == exchange_id
    ]
    selected = None
    if exact_matches:
        selected = exact_matches[0]
    elif candidates:
        selected = candidates[0]
    if not selected:
        raise SaxoSessionError(f"No tradable Saxo instrument match found for {symbol}")
    return SaxoInstrument(
        symbol=symbol,
        uic=int(selected["Identifier"]),
        asset_type=str(selected["AssetType"]),
        exchange_id=str(selected.get("ExchangeId", exchange_id)),
        description=str(selected.get("Description", symbol)),
        tradable_as=[str(value) for value in selected.get("TradableAs", [])],
        currency_code=selected.get("CurrencyCode"),
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
    return {
        "AccountKey": _account_key(config, session),
        "Amount": quantity,
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
    response.raise_for_status()
    return response.json()


def place_order(payload: dict[str, Any], config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    base_url = _openapi_base_url(str(session.get("environment") or config["saxo"]["environment"]))
    response = requests.post(
        f"{base_url}/trade/v2/orders",
        headers=_auth_headers(session["access_token"]),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
