from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Any

from fastapi import WebSocket


_SUPPORTED_TFS = {"15", "30", "60", "240", "1440"}

_CHART_CANDLE_CLIENTS: set[WebSocket] = set()
_CHART_CANDLE_SUBSCRIPTIONS: dict[WebSocket, tuple[str, str] | None] = {}
_CHART_CANDLE_LIVE_STATE: dict[tuple[str, str], dict[str, Any]] = {}
_CHART_CANDLE_SOURCE_SEQ: dict[tuple[str, str], int] = {}
_CHART_CANDLE_FINALIZED_BARS: set[tuple[str, str, int]] = set()
_CHART_CANDLE_FINALIZED_ORDER: deque[tuple[str, str, int]] = deque()
_STATE_LOCK = asyncio.Lock()
_EVENT_SEQ = 0
_FINALIZED_BAR_LIMIT = 10000
_METRIC_WINDOW_SIZE = 500
_SLOW_SEND_MS = 250
_VERY_SLOW_SEND_MS = 1000
_TEARDOWN_RACE_WINDOW_SECONDS = 10.0
_CLIENT_STATE_CONNECTED = "connected"
_CLIENT_STATE_SUBSCRIBED = "subscribed"
_CLIENT_STATE_CLOSING = "closing"
_CLIENT_STATE_DISCONNECTED = "disconnected"
_CLIENT_STATE_CLEANUP = "cleanup"
_ACTIVE_CLIENT_STATES = {_CLIENT_STATE_CONNECTED, _CLIENT_STATE_SUBSCRIBED}
_CLOSING_CLIENT_STATES = {
    _CLIENT_STATE_CLOSING,
    _CLIENT_STATE_DISCONNECTED,
    _CLIENT_STATE_CLEANUP,
}
_CLOSE_LIKE_ERROR_PATTERNS = (
    "close",
    "closed",
    "closing",
    "disconnect",
    "disconnected",
    "connectionreset",
    "brokenpipe",
    "clientdisconnected",
)


def _percentile(values: deque[float], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    index = max(0, min(index, len(ordered) - 1))
    return int(round(ordered[index]))


@dataclass
class _ChartWebSocketClientMeta:
    state: str = _CLIENT_STATE_CONNECTED
    connected_at: float = field(default_factory=time.monotonic)
    subscribed_at: float | None = None
    closing_started_at: float | None = None
    disconnected_at: float | None = None
    cleanup_started_at: float | None = None
    last_send_started_at: float | None = None
    last_send_failed_at: float | None = None
    last_send_failure_phase: str = ""
    client_disconnect_recorded: bool = False
    close_initiator: str = ""


@dataclass
class _ChartWebSocketMetrics:
    connect_total: int = 0
    disconnect_total: int = 0
    disconnect_cleanup_total: int = 0
    client_disconnect_total: int = 0
    server_close_total: int = 0
    close_error_total: int = 0
    broadcast_total: int = 0
    broadcast_no_match_total: int = 0
    broadcast_error_total: int = 0
    broadcast_send_failure_total: int = 0
    send_total: int = 0
    send_failure_total: int = 0
    active_send_failure_total: int = 0
    closing_send_failure_total: int = 0
    teardown_close_race_total: int = 0
    send_skipped_closing_total: int = 0
    slow_send_250ms_total: int = 0
    slow_send_1000ms_total: int = 0
    last_success_epoch: int = 0
    last_broadcast_duration_ms: int = 0
    last_broadcast_matched_clients: int = 0
    last_send_duration_max_ms: int = 0
    last_error_code: str = ""
    last_send_failure_reason_code: str = ""
    last_send_failure_phase: str = ""
    last_close_reason_code: str = ""
    broadcast_durations_ms: deque[float] = field(
        default_factory=lambda: deque(maxlen=_METRIC_WINDOW_SIZE)
    )
    broadcast_matched_clients: deque[int] = field(
        default_factory=lambda: deque(maxlen=_METRIC_WINDOW_SIZE)
    )
    send_durations_ms: deque[float] = field(
        default_factory=lambda: deque(maxlen=_METRIC_WINDOW_SIZE)
    )

    def reset(self) -> None:
        self.connect_total = 0
        self.disconnect_total = 0
        self.disconnect_cleanup_total = 0
        self.client_disconnect_total = 0
        self.server_close_total = 0
        self.close_error_total = 0
        self.broadcast_total = 0
        self.broadcast_no_match_total = 0
        self.broadcast_error_total = 0
        self.broadcast_send_failure_total = 0
        self.send_total = 0
        self.send_failure_total = 0
        self.active_send_failure_total = 0
        self.closing_send_failure_total = 0
        self.teardown_close_race_total = 0
        self.send_skipped_closing_total = 0
        self.slow_send_250ms_total = 0
        self.slow_send_1000ms_total = 0
        self.last_success_epoch = 0
        self.last_broadcast_duration_ms = 0
        self.last_broadcast_matched_clients = 0
        self.last_send_duration_max_ms = 0
        self.last_error_code = ""
        self.last_send_failure_reason_code = ""
        self.last_send_failure_phase = ""
        self.last_close_reason_code = ""
        self.broadcast_durations_ms.clear()
        self.broadcast_matched_clients.clear()
        self.send_durations_ms.clear()

    def record_connect(self) -> None:
        self.connect_total += 1

    def record_disconnect(self) -> None:
        self.disconnect_total += 1
        self.disconnect_cleanup_total += 1

    def record_client_disconnect(self, code: str) -> None:
        self.client_disconnect_total += 1
        self.last_close_reason_code = _sanitize_error_code(code or "client_disconnect")

    def record_server_close(self, code: str) -> None:
        self.server_close_total += 1
        self.last_close_reason_code = _sanitize_error_code(code or "server_close")

    def record_close_error(self, code: str) -> None:
        self.close_error_total += 1
        self.last_error_code = _sanitize_error_code(code)

    def record_broadcast(
        self,
        *,
        matched_clients: int,
        duration_ms: float,
        max_send_duration_ms: float,
        had_error: bool,
        error_code: str = "",
    ) -> None:
        duration_value = max(0, int(round(duration_ms)))
        max_send_value = max(0, int(round(max_send_duration_ms)))
        self.broadcast_total += 1
        self.last_broadcast_duration_ms = duration_value
        self.last_broadcast_matched_clients = max(0, int(matched_clients))
        self.last_send_duration_max_ms = max_send_value
        self.broadcast_durations_ms.append(duration_value)
        self.broadcast_matched_clients.append(self.last_broadcast_matched_clients)
        if matched_clients <= 0:
            self.broadcast_no_match_total += 1
        if had_error:
            self.broadcast_error_total += 1
            self.last_error_code = _sanitize_error_code(error_code or "send_failure")
        else:
            self.last_success_epoch = int(time.time())
            self.last_error_code = ""

    def record_send(
        self,
        *,
        duration_ms: float,
        ok: bool,
        error_code: str = "",
        failure_phase: str = "",
        from_broadcast: bool = False,
        teardown_race: bool = False,
    ) -> None:
        duration_value = max(0, int(round(duration_ms)))
        self.send_total += 1
        self.send_durations_ms.append(duration_value)
        if duration_value >= _SLOW_SEND_MS:
            self.slow_send_250ms_total += 1
        if duration_value >= _VERY_SLOW_SEND_MS:
            self.slow_send_1000ms_total += 1
        if not ok:
            phase = "closing" if failure_phase == "closing" else "active"
            reason = _sanitize_error_code(error_code or "send_failure")
            self.send_failure_total += 1
            if phase == "closing":
                self.closing_send_failure_total += 1
            else:
                self.active_send_failure_total += 1
            if from_broadcast:
                self.broadcast_send_failure_total += 1
            if teardown_race:
                self.teardown_close_race_total += 1
            self.last_error_code = reason
            self.last_send_failure_reason_code = reason
            self.last_send_failure_phase = phase

    def record_send_skipped_closing(self) -> None:
        self.send_skipped_closing_total += 1


_CHART_WS_METRICS = _ChartWebSocketMetrics()
_CHART_CANDLE_CLIENT_META: dict[WebSocket, _ChartWebSocketClientMeta] = {}


def _sanitize_error_code(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))
    return safe[:64] or "unknown"


def _make_exception_code(exc: BaseException) -> str:
    reason = str(exc or "").lower()
    category = "close" if _is_close_like_error(exc) else "error"
    if "timeout" in reason:
        category = "timeout"
    return _sanitize_error_code(f"{exc.__class__.__name__}_{category}")


def _is_close_like_error(exc: BaseException) -> bool:
    haystack = f"{exc.__class__.__name__} {str(exc)}".lower()
    return any(pattern in haystack for pattern in _CLOSE_LIKE_ERROR_PATTERNS)


def _get_or_create_client_meta(ws: WebSocket) -> _ChartWebSocketClientMeta:
    meta = _CHART_CANDLE_CLIENT_META.get(ws)
    if meta is None:
        meta = _ChartWebSocketClientMeta()
        _CHART_CANDLE_CLIENT_META[ws] = meta
    return meta


def _mark_client_subscribed(ws: WebSocket) -> None:
    meta = _get_or_create_client_meta(ws)
    meta.state = _CLIENT_STATE_SUBSCRIBED
    meta.subscribed_at = time.monotonic()


def _mark_client_unsubscribed(ws: WebSocket) -> None:
    meta = _CHART_CANDLE_CLIENT_META.get(ws)
    if meta is not None and meta.state in _ACTIVE_CLIENT_STATES:
        meta.state = _CLIENT_STATE_CONNECTED


def _mark_client_send_started(ws: WebSocket, started_at: float) -> None:
    meta = _get_or_create_client_meta(ws)
    meta.last_send_started_at = started_at


def _mark_client_send_failed(ws: WebSocket, failed_at: float, phase: str) -> None:
    meta = _get_or_create_client_meta(ws)
    meta.last_send_failed_at = failed_at
    meta.last_send_failure_phase = phase


def _mark_client_closing(ws: WebSocket, reason_code: str) -> None:
    meta = _get_or_create_client_meta(ws)
    now = time.monotonic()
    if meta.closing_started_at is None:
        meta.closing_started_at = now
    meta.state = _CLIENT_STATE_CLOSING
    meta.close_initiator = meta.close_initiator or reason_code
    _CHART_WS_METRICS.last_close_reason_code = _sanitize_error_code(reason_code)


def _mark_client_cleanup(ws: WebSocket) -> _ChartWebSocketClientMeta | None:
    meta = _CHART_CANDLE_CLIENT_META.get(ws)
    if meta is None:
        return None
    meta.cleanup_started_at = time.monotonic()
    meta.state = _CLIENT_STATE_CLEANUP
    return meta


def _is_client_closing_or_closed(ws: WebSocket) -> bool:
    meta = _CHART_CANDLE_CLIENT_META.get(ws)
    if meta is None:
        return False
    return meta.state in _CLOSING_CLIENT_STATES or any(
        timestamp is not None
        for timestamp in (
            meta.closing_started_at,
            meta.disconnected_at,
            meta.cleanup_started_at,
        )
    )


def _classify_send_failure(
    ws: WebSocket,
    exc: BaseException,
    failed_at: float,
) -> tuple[str, bool, str]:
    meta = _CHART_CANDLE_CLIENT_META.get(ws)
    reason_code = _make_exception_code(exc)
    close_like = _is_close_like_error(exc)
    marker_times: list[float] = []
    if meta is not None:
        marker_times = [
            timestamp
            for timestamp in (
                meta.closing_started_at,
                meta.disconnected_at,
                meta.cleanup_started_at,
            )
            if timestamp is not None
        ]

    marker_race = any(
        0 <= failed_at - marker_time <= _TEARDOWN_RACE_WINDOW_SECONDS
        for marker_time in marker_times
    )
    state_is_closing = meta is not None and meta.state in _CLOSING_CLIENT_STATES
    phase = "closing" if close_like or marker_race or state_is_closing else "active"
    teardown_race = phase == "closing" and (close_like or marker_race)
    _mark_client_send_failed(ws, failed_at, phase)
    return phase, teardown_race, reason_code


def record_chart_candle_client_receive_closed(ws: WebSocket, exc: BaseException) -> None:
    meta = _get_or_create_client_meta(ws)
    if meta.client_disconnect_recorded:
        return
    now = time.monotonic()
    meta.state = _CLIENT_STATE_DISCONNECTED
    meta.disconnected_at = now
    meta.client_disconnect_recorded = True
    meta.close_initiator = meta.close_initiator or "client"
    _CHART_WS_METRICS.record_client_disconnect(_make_exception_code(exc))


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").upper().strip()


def _normalize_tf(tf: Any) -> str:
    tf_str = str(tf or "").strip()
    return tf_str if tf_str in _SUPPORTED_TFS else ""


def _make_key(symbol: Any, tf: Any) -> tuple[str, str] | None:
    symbol_key = _normalize_symbol(symbol)
    tf_key = _normalize_tf(tf)
    if not symbol_key or not tf_key:
        return None
    return symbol_key, tf_key


def _next_event_seq() -> int:
    global _EVENT_SEQ
    _EVENT_SEQ += 1
    return _EVENT_SEQ


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _remember_finalized_bar(bar_key: tuple[str, str, int]) -> None:
    if bar_key in _CHART_CANDLE_FINALIZED_BARS:
        return
    _CHART_CANDLE_FINALIZED_BARS.add(bar_key)
    _CHART_CANDLE_FINALIZED_ORDER.append(bar_key)
    while len(_CHART_CANDLE_FINALIZED_ORDER) > _FINALIZED_BAR_LIMIT:
        old_key = _CHART_CANDLE_FINALIZED_ORDER.popleft()
        _CHART_CANDLE_FINALIZED_BARS.discard(old_key)


def _normalize_candle_payload(candle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(candle, dict):
        return None

    try:
        normalized = {
            "start": int(candle["start"]),
            "end": int(candle["end"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "confirm": bool(candle.get("confirm", False)),
        }
        volume = _optional_float(candle.get("volume"))
        if volume is not None:
            normalized["volume"] = volume
        return normalized
    except Exception:
        return None


async def register_chart_candle_client(ws: WebSocket) -> None:
    await ws.accept()
    _CHART_CANDLE_CLIENTS.add(ws)
    _CHART_CANDLE_SUBSCRIPTIONS[ws] = None
    _CHART_CANDLE_CLIENT_META[ws] = _ChartWebSocketClientMeta()
    _CHART_WS_METRICS.record_connect()


async def unregister_chart_candle_client(ws: WebSocket) -> None:
    existed = ws in _CHART_CANDLE_CLIENTS or ws in _CHART_CANDLE_SUBSCRIPTIONS
    _mark_client_cleanup(ws)
    _CHART_CANDLE_CLIENTS.discard(ws)
    _CHART_CANDLE_SUBSCRIPTIONS.pop(ws, None)
    if existed:
        _CHART_WS_METRICS.record_disconnect()
    _CHART_CANDLE_CLIENT_META.pop(ws, None)


async def _send_client_control_payload(ws: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception as exc:
        if _is_close_like_error(exc):
            record_chart_candle_client_receive_closed(ws, exc)
            return False
        raise


async def _send_snapshot_ack(ws: WebSocket, symbol: str, tf: str) -> bool:
    key = _make_key(symbol, tf)
    if key is None:
        return True

    async with _STATE_LOCK:
        latest = _CHART_CANDLE_LIVE_STATE.get(key)
        snapshot = dict(latest) if isinstance(latest, dict) else None

    return await _send_client_control_payload(
        ws,
        {
            "type": "candle_subscription_ack",
            "symbol": symbol,
            "tf": tf,
            "latest": snapshot,
            "serverTs": int(time.time() * 1000),
        }
    )


def get_chart_candle_live_candle(symbol: Any, tf: Any) -> dict[str, Any] | None:
    key = _make_key(symbol, tf)
    if key is None:
        return None

    latest = _CHART_CANDLE_LIVE_STATE.get(key)
    if not isinstance(latest, dict):
        return None

    candle = latest.get("candle")
    normalized_candle = _normalize_candle_payload(candle)
    if normalized_candle is None:
        return None

    return {
        **normalized_candle,
        "source": str(latest.get("source") or "chart_ws_live"),
    }


def get_chart_websocket_metrics_snapshot() -> dict[str, Any]:
    active_connections = len(_CHART_CANDLE_CLIENTS)
    subscription_counts: dict[str, int] = {}
    subscription_counts_by_tf: dict[str, int] = {tf: 0 for tf in sorted(_SUPPORTED_TFS)}
    subscribed_connections = 0

    for subscription in _CHART_CANDLE_SUBSCRIPTIONS.values():
        if subscription is None:
            continue
        subscribed_connections += 1
        symbol, tf = subscription
        subscription_counts[f"{symbol}:{tf}"] = subscription_counts.get(f"{symbol}:{tf}", 0) + 1
        subscription_counts_by_tf[tf] = subscription_counts_by_tf.get(tf, 0) + 1

    now = int(time.time())
    last_success_epoch = int(_CHART_WS_METRICS.last_success_epoch or 0)
    age_seconds = now - last_success_epoch if last_success_epoch and now >= last_success_epoch else 0

    return {
        "ok": True,
        "active_connections": active_connections,
        "subscribed_connections": subscribed_connections,
        "unsubscribed_connections": max(0, active_connections - subscribed_connections),
        "subscriptions_total": subscribed_connections,
        "subscriptions": dict(sorted(subscription_counts.items())),
        "subscriptions_by_tf": dict(sorted(subscription_counts_by_tf.items())),
        "connect_total": _CHART_WS_METRICS.connect_total,
        "disconnect_total": _CHART_WS_METRICS.disconnect_total,
        "disconnect_cleanup_total": _CHART_WS_METRICS.disconnect_cleanup_total,
        "client_disconnect_total": _CHART_WS_METRICS.client_disconnect_total,
        "server_close_total": _CHART_WS_METRICS.server_close_total,
        "close_error_total": _CHART_WS_METRICS.close_error_total,
        "broadcast_total": _CHART_WS_METRICS.broadcast_total,
        "broadcast_last_epoch": last_success_epoch,
        "broadcast_age_seconds": age_seconds,
        "broadcast_last_duration_ms": _CHART_WS_METRICS.last_broadcast_duration_ms,
        "broadcast_duration_p50_ms": _percentile(
            _CHART_WS_METRICS.broadcast_durations_ms, 0.50
        ),
        "broadcast_duration_p95_ms": _percentile(
            _CHART_WS_METRICS.broadcast_durations_ms, 0.95
        ),
        "broadcast_duration_p99_ms": _percentile(
            _CHART_WS_METRICS.broadcast_durations_ms, 0.99
        ),
        "broadcast_last_matched_clients": _CHART_WS_METRICS.last_broadcast_matched_clients,
        "broadcast_matched_clients_p95": _percentile(
            _CHART_WS_METRICS.broadcast_matched_clients, 0.95
        ),
        "broadcast_no_match_total": _CHART_WS_METRICS.broadcast_no_match_total,
        "broadcast_error_total": _CHART_WS_METRICS.broadcast_error_total,
        "broadcast_send_failure_total": _CHART_WS_METRICS.broadcast_send_failure_total,
        "send_total": _CHART_WS_METRICS.send_total,
        "send_failure_total": _CHART_WS_METRICS.send_failure_total,
        "active_send_failure_total": _CHART_WS_METRICS.active_send_failure_total,
        "closing_send_failure_total": _CHART_WS_METRICS.closing_send_failure_total,
        "teardown_close_race_total": _CHART_WS_METRICS.teardown_close_race_total,
        "send_skipped_closing_total": _CHART_WS_METRICS.send_skipped_closing_total,
        "send_last_duration_max_ms": _CHART_WS_METRICS.last_send_duration_max_ms,
        "send_duration_p95_ms": _percentile(_CHART_WS_METRICS.send_durations_ms, 0.95),
        "send_duration_p99_ms": _percentile(_CHART_WS_METRICS.send_durations_ms, 0.99),
        "slow_send_250ms_total": _CHART_WS_METRICS.slow_send_250ms_total,
        "slow_send_1000ms_total": _CHART_WS_METRICS.slow_send_1000ms_total,
        "last_success_epoch": last_success_epoch,
        "age_seconds": age_seconds,
        "error_total": _CHART_WS_METRICS.broadcast_error_total
        + _CHART_WS_METRICS.close_error_total,
        "last_error_code": _CHART_WS_METRICS.last_error_code,
        "last_send_failure_reason_code": _CHART_WS_METRICS.last_send_failure_reason_code,
        "last_send_failure_phase": _CHART_WS_METRICS.last_send_failure_phase,
        "last_close_reason_code": _CHART_WS_METRICS.last_close_reason_code,
        "teardown_race_window_seconds": int(_TEARDOWN_RACE_WINDOW_SECONDS),
        "window_size": _METRIC_WINDOW_SIZE,
    }


def reset_chart_websocket_metrics_for_tests() -> None:
    _CHART_CANDLE_CLIENTS.clear()
    _CHART_CANDLE_SUBSCRIPTIONS.clear()
    _CHART_CANDLE_LIVE_STATE.clear()
    _CHART_CANDLE_SOURCE_SEQ.clear()
    _CHART_CANDLE_FINALIZED_BARS.clear()
    _CHART_CANDLE_FINALIZED_ORDER.clear()
    _CHART_CANDLE_CLIENT_META.clear()
    _CHART_WS_METRICS.reset()


async def handle_chart_candle_client_message(ws: WebSocket, raw_message: str) -> bool:
    try:
        payload = json.loads(raw_message)
    except Exception:
        return True

    msg_type = str(payload.get("type") or "").strip()
    if msg_type == "ping":
        return await _send_client_control_payload(
            ws,
            {"type": "pong", "serverTs": int(time.time() * 1000)},
        )

    if msg_type not in {"subscribe", "unsubscribe"}:
        return True

    symbol = _normalize_symbol(payload.get("symbol"))
    tf = _normalize_tf(payload.get("tf"))
    if not symbol or not tf:
        return True

    if msg_type == "subscribe":
        _CHART_CANDLE_SUBSCRIPTIONS[ws] = (symbol, tf)
        _mark_client_subscribed(ws)
        return await _send_snapshot_ack(ws, symbol, tf)

    current = _CHART_CANDLE_SUBSCRIPTIONS.get(ws)
    if current == (symbol, tf):
        _CHART_CANDLE_SUBSCRIPTIONS[ws] = None
        _mark_client_unsubscribed(ws)
    return True


async def _broadcast_to_matching_clients(event: dict[str, Any], key: tuple[str, str]) -> None:
    dead_clients: list[tuple[WebSocket, bool]] = []
    matched_clients = 0
    max_send_duration_ms = 0.0
    had_error = False
    last_error_code = ""
    broadcast_started = time.monotonic()
    for client in list(_CHART_CANDLE_CLIENTS):
        if _CHART_CANDLE_SUBSCRIPTIONS.get(client) != key:
            continue
        if _is_client_closing_or_closed(client):
            _CHART_WS_METRICS.record_send_skipped_closing()
            dead_clients.append((client, False))
            continue
        matched_clients += 1
        send_started = time.monotonic()
        _mark_client_send_started(client, send_started)
        try:
            await client.send_json(event)
            send_duration_ms = (time.monotonic() - send_started) * 1000
            max_send_duration_ms = max(max_send_duration_ms, send_duration_ms)
            _CHART_WS_METRICS.record_send(duration_ms=send_duration_ms, ok=True)
        except Exception as exc:
            failed_at = time.monotonic()
            send_duration_ms = (failed_at - send_started) * 1000
            max_send_duration_ms = max(max_send_duration_ms, send_duration_ms)
            failure_phase, teardown_race, error_code = _classify_send_failure(
                client,
                exc,
                failed_at,
            )
            had_error = True
            last_error_code = error_code
            _CHART_WS_METRICS.record_send(
                duration_ms=send_duration_ms,
                ok=False,
                error_code=error_code,
                failure_phase=failure_phase,
                from_broadcast=True,
                teardown_race=teardown_race,
            )
            _mark_client_closing(client, "send_failure")
            dead_clients.append((client, True))

    for client, should_close in dead_clients:
        if should_close:
            try:
                if not _is_client_closing_or_closed(client):
                    _mark_client_closing(client, "server_close")
                _CHART_WS_METRICS.record_server_close("send_failure_cleanup")
                await client.close()
            except Exception as exc:
                _CHART_WS_METRICS.record_close_error(exc.__class__.__name__)
                pass
        await unregister_chart_candle_client(client)

    _CHART_WS_METRICS.record_broadcast(
        matched_clients=matched_clients,
        duration_ms=(time.monotonic() - broadcast_started) * 1000,
        max_send_duration_ms=max_send_duration_ms,
        had_error=had_error,
        error_code=last_error_code,
    )


async def upsert_chart_candle_live_event(
    *,
    symbol: Any,
    tf: Any,
    candle: dict[str, Any] | None,
    is_final: bool,
    source: str = "unknown",
    source_seq: int | None = None,
) -> bool:
    key = _make_key(symbol, tf)
    normalized_candle = _normalize_candle_payload(candle)
    if key is None or normalized_candle is None:
        return False

    if normalized_candle["confirm"] != bool(is_final):
        normalized_candle["confirm"] = bool(is_final)

    async with _STATE_LOCK:
        is_final_bool = bool(is_final)
        if source_seq is not None:
            try:
                source_seq = int(source_seq)
            except Exception:
                source_seq = None

        finalized_key = (key[0], key[1], int(normalized_candle["start"]))
        if finalized_key in _CHART_CANDLE_FINALIZED_BARS:
            return False

        current_latest = _CHART_CANDLE_LIVE_STATE.get(key)
        current_start = None
        if isinstance(current_latest, dict):
            current_candle = current_latest.get("candle")
            if isinstance(current_candle, dict):
                try:
                    current_start = int(current_candle.get("start"))
                except Exception:
                    current_start = None

        last_source_seq = _CHART_CANDLE_SOURCE_SEQ.get(key)
        if (
            source_seq is not None
            and last_source_seq is not None
            and source_seq <= last_source_seq
            and not is_final_bool
        ):
            return False

        event = {
            "type": "candle_update",
            "symbol": key[0],
            "tf": key[1],
            "candle": normalized_candle,
            "isFinal": is_final_bool,
            "source": str(source or "unknown"),
            "sourceSeq": source_seq,
            "seq": _next_event_seq(),
            "serverTs": int(time.time() * 1000),
        }
        if current_start is None or int(normalized_candle["start"]) >= current_start:
            _CHART_CANDLE_LIVE_STATE[key] = event
        if is_final_bool:
            _remember_finalized_bar(finalized_key)
        if source_seq is not None:
            _CHART_CANDLE_SOURCE_SEQ[key] = (
                max(last_source_seq, source_seq) if last_source_seq is not None else source_seq
            )

    await _broadcast_to_matching_clients(event, key)
    return True


async def broadcast_chart_candle_reconcile(
    *,
    symbol: Any,
    tf: Any,
    candle: dict[str, Any] | None,
    reason: str = "unknown",
    source: str = "rest_reconcile",
    source_seq: int | None = None,
) -> bool:
    key = _make_key(symbol, tf)
    normalized_candle = _normalize_candle_payload(candle)
    if key is None or normalized_candle is None:
        return False

    if source_seq is not None:
        try:
            source_seq = int(source_seq)
        except Exception:
            source_seq = None

    event = {
        "type": "candle_reconcile",
        "symbol": key[0],
        "tf": key[1],
        "candle": normalized_candle,
        "reason": str(reason or "unknown"),
        "source": str(source or "rest_reconcile"),
        "sourceSeq": source_seq,
        "seq": _next_event_seq(),
        "serverTs": int(time.time() * 1000),
    }

    await _broadcast_to_matching_clients(event, key)
    return True
