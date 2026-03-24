from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import requests


BYBIT_BASE_URL = (os.getenv("BYBIT_BASE_URL") or "https://api.bybit.com").rstrip("/")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY") or ""
BYBIT_API_SECRET = os.getenv("BYBIT_SECRET_KEY") or ""
RECV_WINDOW = str(os.getenv("RECV_WINDOW") or "5000")
REQUEST_TIMEOUT_SEC = 10


class BybitApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        ret_code: int | None = None,
        ret_msg: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        self.payload = payload or {}


def normalize_position_side(side: str) -> str:
    raw = str(side or "").upper()
    if raw in {"BUY", "LONG"}:
        return "LONG"
    if raw in {"SELL", "SHORT"}:
        return "SHORT"
    raise ValueError(f"unsupported side: {side}")


def side_to_bybit_position(side: str) -> tuple[str, int]:
    normalized = normalize_position_side(side)
    if normalized == "LONG":
        return "Buy", 1
    return "Sell", 2


def _ensure_private_api_keys() -> None:
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise BybitApiError("BYBIT API key/secret is not configured")


def _sign_get(timestamp: str, params: dict[str, Any]) -> str:
    sorted_params = sorted(params.items())
    query = "&".join(f"{k}={v}" for k, v in sorted_params)
    payload = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{query}"
    return hmac.new(BYBIT_API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _sign_post(timestamp: str, body: dict[str, Any]) -> tuple[str, str]:
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    payload = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
    sign = hmac.new(BYBIT_API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return sign, body_str


def _raise_if_bybit_error(data: dict[str, Any], *, context: str) -> None:
    try:
        ret_code = int(data.get("retCode", -1))
    except Exception:
        ret_code = -1
    if ret_code == 0:
        return

    ret_msg = str(data.get("retMsg") or "unknown bybit error")
    raise BybitApiError(
        f"{context}: {ret_msg}",
        ret_code=ret_code,
        ret_msg=ret_msg,
        payload=data,
    )


def _request_public_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{BYBIT_BASE_URL}{path}"
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SEC)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise BybitApiError(f"bybit public GET failed: {e}") from e
    except ValueError as e:
        raise BybitApiError("bybit public GET returned non-json response") from e

    if not isinstance(data, dict):
        raise BybitApiError("bybit public GET returned invalid payload")
    _raise_if_bybit_error(data, context=f"GET {path}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _request_private_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    _ensure_private_api_keys()
    timestamp = str(int(time.time() * 1000))
    sign = _sign_get(timestamp, params)

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
    }

    url = f"{BYBIT_BASE_URL}{path}"
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise BybitApiError(f"bybit private GET failed: {e}") from e
    except ValueError as e:
        raise BybitApiError("bybit private GET returned non-json response") from e

    if not isinstance(data, dict):
        raise BybitApiError("bybit private GET returned invalid payload")
    _raise_if_bybit_error(data, context=f"GET {path}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _request_private_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    _ensure_private_api_keys()
    timestamp = str(int(time.time() * 1000))
    sign, body_str = _sign_post(timestamp, body)

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json",
    }

    url = f"{BYBIT_BASE_URL}{path}"
    try:
        response = requests.post(
            url,
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise BybitApiError(f"bybit private POST failed: {e}") from e
    except ValueError as e:
        raise BybitApiError("bybit private POST returned non-json response") from e

    if not isinstance(data, dict):
        raise BybitApiError("bybit private POST returned invalid payload")
    _raise_if_bybit_error(data, context=f"POST {path}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _decimal_to_plain(value: Decimal) -> str:
    raw = format(value, "f")
    if "." in raw:
        raw = raw.rstrip("0").rstrip(".")
    return raw or "0"


def round_price_to_tick(price: float, tick_size: float) -> tuple[float, str]:
    try:
        price_dec = Decimal(str(price))
        tick_dec = Decimal(str(tick_size))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError("price/tick_size must be finite numbers") from e

    if tick_dec <= 0:
        raise ValueError("tick_size must be greater than 0")
    if price_dec <= 0:
        raise ValueError("price must be greater than 0")

    scaled = (price_dec / tick_dec).to_integral_value(rounding=ROUND_HALF_UP)
    rounded = scaled * tick_dec
    if rounded <= 0:
        rounded = tick_dec

    as_str = _decimal_to_plain(rounded)
    return float(rounded), as_str


def get_linear_tick_size(symbol: str) -> float:
    result = _request_public_get(
        "/v5/market/instruments-info",
        {"category": "linear", "symbol": symbol},
    )
    rows = result.get("list")
    if not isinstance(rows, list):
        raise BybitApiError("missing instruments list in bybit response")

    for row in rows:
        if str(row.get("symbol") or "") != str(symbol):
            continue
        price_filter = row.get("priceFilter") or {}
        tick_size_raw = price_filter.get("tickSize")
        try:
            tick_size = float(tick_size_raw)
        except Exception as e:
            raise BybitApiError("invalid tickSize in instruments-info response") from e
        if tick_size <= 0:
            raise BybitApiError("tickSize must be greater than 0")
        return tick_size

    raise BybitApiError(f"symbol not found in instruments-info: {symbol}")


def get_linear_last_price(symbol: str) -> float | None:
    result = _request_public_get(
        "/v5/market/tickers",
        {"category": "linear", "symbol": symbol},
    )
    rows = result.get("list")
    if not isinstance(rows, list):
        return None

    for row in rows:
        if str(row.get("symbol") or "") != str(symbol):
            continue
        try:
            last_price = float(row.get("lastPrice"))
        except Exception:
            return None
        if last_price > 0:
            return last_price
    return None


def get_open_linear_position(symbol: str, side: str) -> dict[str, Any] | None:
    bybit_side, bybit_position_idx = side_to_bybit_position(side)
    result = _request_private_get(
        "/v5/position/list",
        {"category": "linear", "symbol": symbol},
    )
    rows = result.get("list")
    if not isinstance(rows, list):
        return None

    for row in rows:
        if str(row.get("symbol") or "") != str(symbol):
            continue

        try:
            size = float(row.get("size") or 0)
        except Exception:
            size = 0.0
        if size <= 0:
            continue

        raw_side = str(row.get("side") or "")
        try:
            position_idx = int(row.get("positionIdx") or 0)
        except Exception:
            position_idx = 0

        if raw_side == bybit_side or position_idx == bybit_position_idx:
            return row

    return None


def list_open_linear_positions(symbol: str) -> list[dict[str, Any]]:
    result = _request_private_get(
        "/v5/position/list",
        {"category": "linear", "symbol": symbol},
    )
    rows = result.get("list")
    if not isinstance(rows, list):
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("symbol") or "") != str(symbol):
            continue
        try:
            size = float(row.get("size") or 0)
        except Exception:
            size = 0.0
        if size <= 0:
            continue
        out.append(row)
    return out


def update_linear_position_tpsl(
    *,
    symbol: str,
    side: str,
    tp_price_text: str | None = None,
    sl_price_text: str | None = None,
) -> dict[str, Any]:
    if tp_price_text is None and sl_price_text is None:
        raise ValueError("either tp_price_text or sl_price_text is required")

    _, position_idx = side_to_bybit_position(side)

    body: dict[str, Any] = {
        "category": "linear",
        "symbol": symbol,
        "tpslMode": "Full",
        "positionIdx": position_idx,
    }
    if tp_price_text is not None:
        body["takeProfit"] = tp_price_text
        body["tpTriggerBy"] = "LastPrice"
        body["tpOrderType"] = "Market"
    if sl_price_text is not None:
        body["stopLoss"] = sl_price_text
        body["slTriggerBy"] = "MarkPrice"
        body["slOrderType"] = "Market"

    return _request_private_post("/v5/position/trading-stop", body)
