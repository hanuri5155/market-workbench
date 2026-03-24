import json
import os
import random
import time
from itertools import cycle
from urllib.parse import urlparse, urlunparse

import gevent
import websocket
from locust import User, between, events, task


DEFAULT_TFS = ("15", "30", "60", "240")
DEFAULT_EXPECTED_SOURCE = "chart_ingest_active"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") or ""
    if not path.endswith("/ws/chart-candles"):
        path = f"{path}/ws/chart-candles"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _resolve_ws_url(host: str | None) -> str:
    explicit = os.getenv("TARGET_WS_URL")
    if explicit:
        return explicit

    base = os.getenv("TARGET_BASE_URL") or host
    if base:
        return _to_ws_url(base)

    return "ws://127.0.0.1:8000/ws/chart-candles"


def _parse_subscriptions() -> list[tuple[str, str]]:
    explicit = os.getenv("CHART_SUBSCRIPTIONS", "").strip()
    if explicit:
        pairs: list[tuple[str, str]] = []
        for item in explicit.split(","):
            symbol, tf = item.strip().split(":", 1)
            pairs.append((symbol.upper(), tf))
        return pairs

    symbol = os.getenv("CHART_SYMBOL", "BTCUSDT").upper()
    tfs = tuple(tf.strip() for tf in os.getenv("CHART_TFS", ",".join(DEFAULT_TFS)).split(",") if tf.strip())
    return [(symbol, tf) for tf in tfs]


def _fire_request(name: str, response_time: float, response_length: int = 0, exception: Exception | None = None) -> None:
    events.request.fire(
        request_type="WS",
        name=name,
        response_time=response_time,
        response_length=response_length,
        exception=exception,
    )


def _message_latency_ms(received_at: int, message: dict) -> int:
    server_ts = message.get("serverTs")
    if not isinstance(server_ts, int):
        return 0
    return max(0, received_at - server_ts)


class ChartWebSocketUser(User):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.ws_url = _resolve_ws_url(getattr(self, "host", None))
        self.subscriptions_per_user = max(1, _env_int("SUBS_PER_USER", 1))
        self.connect_timeout = _env_float("WS_CONNECT_TIMEOUT_SEC", 5.0)
        self.recv_timeout = _env_float("WS_RECV_TIMEOUT_SEC", 1.0)
        self.ping_interval = _env_float("PING_INTERVAL_SEC", 20.0)
        self.check_source_seq_gaps = os.getenv("CHECK_SOURCE_SEQ_GAPS", "true").lower() in {"1", "true", "yes"}
        self.expected_source = os.getenv("EXPECTED_CHART_SOURCE", DEFAULT_EXPECTED_SOURCE)
        self._stopped = False
        self._sockets: list[websocket.WebSocket] = []
        self._greenlets: list[gevent.Greenlet] = []

        choices = _parse_subscriptions()
        if not choices:
            raise RuntimeError("No chart subscriptions configured")

        selected: list[tuple[str, str]] = []
        if self.subscriptions_per_user <= len(choices):
            selected = random.sample(choices, self.subscriptions_per_user)
        else:
            for _, item in zip(range(self.subscriptions_per_user), cycle(choices)):
                selected.append(item)

        for index, (symbol, tf) in enumerate(selected, start=1):
            self._greenlets.append(gevent.spawn(self._socket_loop, index, symbol, tf))

    @task
    def keep_user_alive(self) -> None:
        gevent.sleep(1)

    def on_stop(self) -> None:
        self._stopped = True
        for sock in list(self._sockets):
            try:
                sock.close()
            except Exception:
                pass
        for greenlet in list(self._greenlets):
            greenlet.kill(block=False)

    def _socket_loop(self, index: int, symbol: str, tf: str) -> None:
        reconnects = 0
        last_source_seq: int | None = None
        last_event_seq: int | None = None
        socket_name = f"{symbol}:{tf}:socket-{index}"

        while not self._stopped:
            sock = None
            connected = False
            try:
                start = time.perf_counter()
                sock = websocket.create_connection(self.ws_url, timeout=self.connect_timeout)
                sock.settimeout(self.recv_timeout)
                self._sockets.append(sock)
                connected = True
                _fire_request("connect", (time.perf_counter() - start) * 1000)

                if reconnects > 0:
                    _fire_request("reconnect", 0)
                reconnects += 1

                subscribe = {"type": "subscribe", "symbol": symbol, "tf": tf}
                send_start = time.perf_counter()
                sock.send(json.dumps(subscribe, separators=(",", ":")))
                _fire_request("subscribe", (time.perf_counter() - send_start) * 1000, len(json.dumps(subscribe)))

                last_ping = time.monotonic()
                while not self._stopped:
                    try:
                        raw = sock.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_ping >= self.ping_interval:
                            ping = {"type": "ping", "clientTs": _now_ms()}
                            sock.send(json.dumps(ping, separators=(",", ":")))
                            last_ping = time.monotonic()
                        continue

                    if not raw:
                        raise RuntimeError("empty websocket frame")

                    message = self._handle_message(raw, socket_name, symbol, tf, last_source_seq, last_event_seq)
                    if message is None:
                        continue
                    if isinstance(message.get("sourceSeq"), int):
                        last_source_seq = message["sourceSeq"]
                    if isinstance(message.get("seq"), int):
                        last_event_seq = message["seq"]
            except Exception as exc:
                if connected and not self._stopped:
                    _fire_request("disconnect", 0, exception=exc)
                elif not self._stopped:
                    _fire_request("connect", 0, exception=exc)
                gevent.sleep(min(5, 0.5 + reconnects * 0.2))
            finally:
                if sock is not None:
                    try:
                        if connected:
                            sock.send(json.dumps({"type": "unsubscribe", "symbol": symbol, "tf": tf}, separators=(",", ":")))
                    except Exception:
                        pass
                    try:
                        sock.close()
                    except Exception:
                        pass
                    if sock in self._sockets:
                        self._sockets.remove(sock)

    def _handle_message(
        self,
        raw: str | bytes,
        socket_name: str,
        expected_symbol: str,
        expected_tf: str,
        last_source_seq: int | None,
        last_event_seq: int | None,
    ) -> dict | None:
        received_at = _now_ms()
        response_length = len(raw)

        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            _fire_request("message_parse", 0, response_length, exception=exc)
            return None

        message_type = message.get("type", "unknown")
        metric_name = f"message:{message_type}"
        latency_ms = _message_latency_ms(received_at, message)
        _fire_request(metric_name, latency_ms, response_length)

        event_message = self._validate_subscription_ack(message, expected_symbol, expected_tf, received_at, response_length)
        if event_message is None:
            event_message = message

        self._validate_source(event_message, socket_name, response_length)

        source_seq = event_message.get("sourceSeq")
        if isinstance(source_seq, int) and last_source_seq is not None:
            if source_seq <= last_source_seq:
                _fire_request(
                    f"source_seq_non_monotonic:{socket_name}",
                    0,
                    response_length,
                    exception=RuntimeError(f"sourceSeq moved from {last_source_seq} to {source_seq}"),
                )
            elif self.check_source_seq_gaps and source_seq > last_source_seq + 1:
                _fire_request(
                    f"source_seq_gap:{socket_name}",
                    0,
                    response_length,
                    exception=RuntimeError(f"sourceSeq gap from {last_source_seq} to {source_seq}"),
                )

        event_seq = event_message.get("seq")
        if isinstance(event_seq, int) and last_event_seq is not None and event_seq <= last_event_seq:
            _fire_request(
                f"event_seq_non_monotonic:{socket_name}",
                0,
                response_length,
                exception=RuntimeError(f"seq moved from {last_event_seq} to {event_seq}"),
            )

        return event_message

    def _validate_subscription_ack(
        self,
        message: dict,
        expected_symbol: str,
        expected_tf: str,
        received_at: int,
        response_length: int,
    ) -> dict | None:
        if message.get("type") != "candle_subscription_ack":
            return None

        if message.get("symbol") != expected_symbol or str(message.get("tf")) != expected_tf:
            _fire_request(
                "subscription_ack_mismatch",
                0,
                response_length,
                exception=RuntimeError(
                    f"ack mismatch expected {expected_symbol}:{expected_tf}, "
                    f"got {message.get('symbol')}:{message.get('tf')}"
                ),
            )

        latest = message.get("latest")
        if not isinstance(latest, dict):
            _fire_request(
                "subscription_latest_missing",
                0,
                response_length,
                exception=RuntimeError(f"missing latest for {expected_symbol}:{expected_tf}"),
            )
            return None

        latest_latency_ms = _message_latency_ms(received_at, latest)
        _fire_request("subscription_latest_present", latest_latency_ms, response_length)
        return latest

    def _validate_source(self, message: dict, socket_name: str, response_length: int) -> None:
        message_type = message.get("type")
        if message_type not in {"candle_update", "candle_reconcile"}:
            return

        source = message.get("source")
        if source != self.expected_source:
            _fire_request(
                f"source_mismatch:{socket_name}",
                0,
                response_length,
                exception=RuntimeError(f"expected source {self.expected_source}, got {source}"),
            )
