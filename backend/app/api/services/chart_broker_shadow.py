from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.utils.log_utils import log
from core.ws.chart_events import LiveCandleEvent


BROKER_SCHEMA_VERSION = "chart_candle.v1"
DEFAULT_BROKER_KIND = "nats_jetstream"
DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_STREAM_PREFIX = "candles.bybit"
DEFAULT_NATS_URL = "nats://nats:4222"
DEFAULT_NATS_SUBJECT_PREFIX = "candles"
DEFAULT_NATS_PARTIAL_STREAM = "CHART_PARTIAL"
DEFAULT_NATS_CRITICAL_STREAM = "CHART_CRITICAL"
DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS = 6 * 60 * 60
DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS = 120


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def _interval_stream_token(tf: Any) -> str:
    value = str(int(tf))
    return "D" if value == "1440" else value


def stream_name_for_event(event: LiveCandleEvent, *, prefix: str = DEFAULT_STREAM_PREFIX) -> str:
    exchange = str(event.exchange or "bybit").lower()
    normalized_prefix = str(prefix or DEFAULT_STREAM_PREFIX).strip(".")
    if normalized_prefix.endswith(f".{exchange}"):
        base = normalized_prefix
    else:
        base = f"{normalized_prefix}.{exchange}"
    return f"{base}.{event.symbol}.{_interval_stream_token(event.tf)}"


def nats_lane_for_event_type(event_type: str) -> str:
    return "critical" if str(event_type) in {"final", "reconcile"} else "partial"


def nats_subject_for_event(
    event: LiveCandleEvent,
    *,
    subject_prefix: str = DEFAULT_NATS_SUBJECT_PREFIX,
) -> str:
    prefix = str(subject_prefix or DEFAULT_NATS_SUBJECT_PREFIX).strip(".")
    lane = nats_lane_for_event_type(event.event_type)
    exchange = str(event.exchange or "bybit").lower()
    return f"{prefix}.{lane}.{exchange}.{event.symbol}.{_interval_stream_token(event.tf)}"


def nats_subject_pattern_for_lane(
    lane: str,
    *,
    subject_prefix: str = DEFAULT_NATS_SUBJECT_PREFIX,
) -> str:
    prefix = str(subject_prefix or DEFAULT_NATS_SUBJECT_PREFIX).strip(".")
    return f"{prefix}.{lane}.*.*.*"


def nats_stream_for_event_type(
    event_type: str,
    *,
    partial_stream: str = DEFAULT_NATS_PARTIAL_STREAM,
    critical_stream: str = DEFAULT_NATS_CRITICAL_STREAM,
) -> str:
    return critical_stream if nats_lane_for_event_type(event_type) == "critical" else partial_stream


def nats_msg_id_for_payload(payload: dict[str, Any]) -> str:
    event_type = str(payload.get("event_type") or "")
    base = str(payload.get("idempotency_key") or payload.get("candle_key") or "")
    if event_type in {"final", "reconcile"} and base:
        return base
    if base:
        return ":".join(
            [
                base,
                str(payload.get("source_seq") or ""),
                str(payload.get("emitted_at_ms") or ""),
                str(payload.get("publish_ts_ms") or ""),
            ]
        )
    return str(payload.get("publish_ts_ms") or _now_ms())


async def ensure_nats_chart_streams(
    js: Any,
    *,
    subject_prefix: str = DEFAULT_NATS_SUBJECT_PREFIX,
    partial_stream: str = DEFAULT_NATS_PARTIAL_STREAM,
    critical_stream: str = DEFAULT_NATS_CRITICAL_STREAM,
    partial_max_age_seconds: float = DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS,
    critical_max_age_seconds: float = DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS,
    duplicate_window_seconds: float = DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS,
) -> None:
    from nats.js.api import RetentionPolicy, StorageType, StreamConfig  # type: ignore
    from nats.js.errors import NotFoundError  # type: ignore

    specs = [
        (
            partial_stream,
            nats_subject_pattern_for_lane("partial", subject_prefix=subject_prefix),
            partial_max_age_seconds,
            "Market Workbench chart partial shadow events",
        ),
        (
            critical_stream,
            nats_subject_pattern_for_lane("critical", subject_prefix=subject_prefix),
            critical_max_age_seconds,
            "Market Workbench chart final/reconcile shadow events",
        ),
    ]

    for stream_name, subject, max_age, description in specs:
        try:
            await js.stream_info(stream_name)
            continue
        except NotFoundError:
            pass

        await js.add_stream(
            config=StreamConfig(
                name=stream_name,
                description=description,
                subjects=[subject],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                max_age=max(1.0, float(max_age)),
                duplicate_window=max(1.0, float(duplicate_window_seconds)),
            )
        )


def build_broker_shadow_payload(
    event: LiveCandleEvent,
    *,
    received_at_ms: int,
    volume: float | None = None,
    publish_ts_ms: int | None = None,
    schema_version: str = BROKER_SCHEMA_VERSION,
) -> dict[str, Any]:
    publish_ts = int(publish_ts_ms or _now_ms())
    candle = dict(event.candle)
    if volume is not None:
        candle["volume"] = float(volume)

    return {
        "schema_version": schema_version,
        "source": "chart_ingest_shadow",
        "exchange": event.exchange,
        "symbol": event.symbol,
        "tf": int(event.tf),
        "interval": _interval_stream_token(event.tf),
        "bar_time": int(event.bar_time),
        "start_time": int(event.bar_time),
        "end_time": int(event.candle["end"]),
        "event_type": event.event_type,
        "is_final": bool(event.is_final),
        "open": float(event.candle["open"]),
        "high": float(event.candle["high"]),
        "low": float(event.candle["low"]),
        "close": float(event.candle["close"]),
        "volume": volume,
        "confirm": bool(event.candle.get("confirm")),
        "candle": candle,
        "candle_key": event.candle_key,
        "routing_key": event.routing_key,
        "idempotency_key": event.idempotency_key,
        "source_seq": event.source_seq,
        "emitted_at": event.emitted_at_ms,
        "emitted_at_ms": event.emitted_at_ms,
        "received_at": int(received_at_ms),
        "received_at_ms": int(received_at_ms),
        "exchange_ts": event.exchange_ts,
        "publish_ts": publish_ts,
        "publish_ts_ms": publish_ts,
    }


def build_redis_stream_fields(payload: dict[str, Any]) -> dict[str, str]:
    def scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    return {
        "schema_version": scalar(payload.get("schema_version")),
        "source": scalar(payload.get("source")),
        "exchange": scalar(payload.get("exchange")),
        "symbol": scalar(payload.get("symbol")),
        "tf": scalar(payload.get("tf")),
        "interval": scalar(payload.get("interval")),
        "bar_time": scalar(payload.get("bar_time")),
        "event_type": scalar(payload.get("event_type")),
        "is_final": scalar(payload.get("is_final")),
        "candle_key": scalar(payload.get("candle_key")),
        "idempotency_key": scalar(payload.get("idempotency_key")),
        "emitted_at_ms": scalar(payload.get("emitted_at_ms")),
        "received_at_ms": scalar(payload.get("received_at_ms")),
        "publish_ts_ms": scalar(payload.get("publish_ts_ms")),
        "payload": json.dumps(payload, separators=(",", ":"), sort_keys=True),
    }


@dataclass(frozen=True)
class BrokerPublishItem:
    event: LiveCandleEvent
    received_at_ms: int
    volume: float | None = None

    @property
    def is_final(self) -> bool:
        return bool(self.event.is_final)


class ChartBrokerShadowPublisher(Protocol):
    async def start(self) -> None:
        ...

    async def close(self) -> None:
        ...

    def enqueue_event(
        self,
        event: LiveCandleEvent,
        *,
        received_at_ms: int,
        volume: float | None = None,
    ) -> bool:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...


@dataclass
class RedisStreamChartEventPublisher:
    redis_url: str = DEFAULT_REDIS_URL
    stream_prefix: str = DEFAULT_STREAM_PREFIX
    queue_max: int = 2000
    critical_queue_max: int = 500
    publish_timeout_seconds: float = 1.0
    partial_retry_attempts: int = 1
    final_retry_attempts: int = 3
    retry_backoff_seconds: float = 0.25
    stream_maxlen: int = 50000
    summary_interval_seconds: float = 60.0
    drain_timeout_seconds: float = 2.0
    connect_retry_interval_seconds: float = 5.0
    failure_log_sample_every: int = 100
    logger: Any = log
    redis_client: Any | None = None
    _partial_queue: asyncio.Queue[BrokerPublishItem] | None = field(default=None, init=False)
    _critical_queue: asyncio.Queue[BrokerPublishItem] | None = field(default=None, init=False)
    _worker_task: asyncio.Task | None = field(default=None, init=False)
    _summary_task: asyncio.Task | None = field(default=None, init=False)
    _closing: bool = field(default=False, init=False)
    _owns_client: bool = field(default=False, init=False)
    _connect_retry_after_ms: int = field(default=0, init=False)
    _inflight_count: int = field(default=0, init=False)
    _stream_counts: Counter[str] = field(default_factory=Counter, init=False)
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "broker_publish_enabled": 1,
            "broker_publish_attempt_total": 0,
            "broker_publish_success_total": 0,
            "broker_publish_failure_total": 0,
            "broker_publish_partial_dropped_total": 0,
            "broker_publish_final_failure_total": 0,
            "broker_publish_latency_samples_total": 0,
            "broker_publish_latency_ms_total": 0,
            "broker_publish_latency_ms_min": 0,
            "broker_publish_latency_ms_max": 0,
            "broker_connect_failure_total": 0,
            "broker_publish_drain_timeout_total": 0,
            "last_broker_error": "",
        },
        init=False,
    )

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return

        self._closing = False
        if self._partial_queue is None:
            self._partial_queue = asyncio.Queue(maxsize=max(1, int(self.queue_max)))
        if self._critical_queue is None:
            self._critical_queue = asyncio.Queue(maxsize=max(1, int(self.critical_queue_max)))

        self._worker_task = asyncio.create_task(
            self._publisher_loop(),
            name="chart-broker-shadow-redis-publisher",
        )
        self._summary_task = asyncio.create_task(
            self._summary_loop(max(1.0, float(self.summary_interval_seconds))),
            name="chart-broker-shadow-summary",
        )
        self._log(
            "[ChartBrokerShadow] Redis Streams publisher started "
            f"url={self.redis_url}, stream_prefix={self.stream_prefix}, "
            f"queue_max={self.queue_max}, critical_queue_max={self.critical_queue_max}"
        )

    async def close(self) -> None:
        self._closing = True
        if self._summary_task and not self._summary_task.done():
            self._summary_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._summary_task
        self._summary_task = None

        deadline = time.monotonic() + max(0.0, float(self.drain_timeout_seconds))
        while self.queue_depth > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if self.queue_depth > 0:
            self.stats["broker_publish_drain_timeout_total"] += 1

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        self._worker_task = None

        if self.redis_client is not None and self._owns_client:
            close = getattr(self.redis_client, "aclose", None) or getattr(
                self.redis_client,
                "close",
                None,
            )
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        await result
        self.redis_client = None
        self._owns_client = False
        self.log_summary(reason="shutdown")

    @property
    def queue_depth(self) -> int:
        partial = self._partial_queue.qsize() if self._partial_queue is not None else 0
        critical = self._critical_queue.qsize() if self._critical_queue is not None else 0
        return partial + critical + max(0, int(self._inflight_count))

    def enqueue_event(
        self,
        event: LiveCandleEvent,
        *,
        received_at_ms: int,
        volume: float | None = None,
    ) -> bool:
        queue = self._critical_queue if event.is_final else self._partial_queue
        if queue is None:
            self.stats["broker_publish_failure_total"] += 1
            self.stats["last_broker_error"] = "publisher_not_started"
            if event.is_final:
                self.stats["broker_publish_final_failure_total"] += 1
            return False

        item = BrokerPublishItem(event=event, received_at_ms=received_at_ms, volume=volume)
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            if event.is_final:
                self.stats["broker_publish_failure_total"] += 1
                self.stats["broker_publish_final_failure_total"] += 1
                self.stats["last_broker_error"] = "critical_queue_full"
                self._log(
                    "[ChartBrokerShadow] final publish queue full "
                    f"key={event.candle_key}, final_failure_total="
                    f"{self.stats['broker_publish_final_failure_total']}"
                )
            else:
                self.stats["broker_publish_partial_dropped_total"] += 1
            return False

    def snapshot(self) -> dict[str, Any]:
        latency_count = int(self.stats.get("broker_publish_latency_samples_total", 0))
        latency_total = int(self.stats.get("broker_publish_latency_ms_total", 0))
        stream_counts = ",".join(
            f"{stream}:{count}" for stream, count in self._stream_counts.most_common(8)
        )
        return {
            **self.stats,
            "broker_publish_latency_ms_avg": int(latency_total / latency_count)
            if latency_count
            else 0,
            "broker_publish_queue_depth": self.queue_depth,
            "broker_publish_partial_queue_depth": self._partial_queue.qsize()
            if self._partial_queue is not None
            else 0,
            "broker_publish_critical_queue_depth": self._critical_queue.qsize()
            if self._critical_queue is not None
            else 0,
            "broker_publish_stream_counts": stream_counts,
        }

    def log_summary(self, *, reason: str = "interval") -> None:
        snapshot = self.snapshot()
        self._log(
            "[ChartBrokerShadow] publish summary "
            f"reason={reason}, broker_publish_enabled={snapshot['broker_publish_enabled']}, "
            f"broker_publish_attempt_total={snapshot['broker_publish_attempt_total']}, "
            f"broker_publish_success_total={snapshot['broker_publish_success_total']}, "
            f"broker_publish_failure_total={snapshot['broker_publish_failure_total']}, "
            f"broker_publish_partial_dropped_total="
            f"{snapshot['broker_publish_partial_dropped_total']}, "
            f"broker_publish_final_failure_total="
            f"{snapshot['broker_publish_final_failure_total']}, "
            f"broker_publish_latency_ms_min={snapshot['broker_publish_latency_ms_min']}, "
            f"broker_publish_latency_ms_avg={snapshot['broker_publish_latency_ms_avg']}, "
            f"broker_publish_latency_ms_max={snapshot['broker_publish_latency_ms_max']}, "
            f"broker_publish_queue_depth={snapshot['broker_publish_queue_depth']}, "
            f"broker_publish_partial_queue_depth="
            f"{snapshot['broker_publish_partial_queue_depth']}, "
            f"broker_publish_critical_queue_depth="
            f"{snapshot['broker_publish_critical_queue_depth']}, "
            f"broker_connect_failure_total={snapshot['broker_connect_failure_total']}, "
            f"last_broker_error={snapshot['last_broker_error']}, "
            f"stream_counts={snapshot['broker_publish_stream_counts']}"
        )

    async def _publisher_loop(self) -> None:
        while True:
            item = await self._next_item()
            self._inflight_count += 1
            try:
                await self._publish_item(item)
            finally:
                self._inflight_count = max(0, self._inflight_count - 1)

    async def _next_item(self) -> BrokerPublishItem:
        assert self._critical_queue is not None
        assert self._partial_queue is not None
        while True:
            try:
                return self._critical_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                return self._partial_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await asyncio.sleep(0.05)

    async def _publish_item(self, item: BrokerPublishItem) -> None:
        attempts = self.final_retry_attempts if item.is_final else self.partial_retry_attempts
        attempts = max(1, int(attempts))
        stream = stream_name_for_event(item.event, prefix=self.stream_prefix)
        last_error = ""

        for attempt in range(1, attempts + 1):
            self.stats["broker_publish_attempt_total"] += 1
            publish_ts = _now_ms()
            payload = build_broker_shadow_payload(
                item.event,
                received_at_ms=item.received_at_ms,
                volume=item.volume,
                publish_ts_ms=publish_ts,
            )
            fields = build_redis_stream_fields(payload)
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    self._xadd(stream, fields),
                    timeout=max(0.05, float(self.publish_timeout_seconds)),
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                self._record_latency(latency_ms)
                self.stats["broker_publish_success_total"] += 1
                self._stream_counts[stream] += 1
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)
                self.stats["broker_publish_failure_total"] += 1
                self.stats["last_broker_error"] = last_error[:240]
                self._log_publish_failure(item, stream, attempt, attempts, last_error)
                if attempt < attempts:
                    await asyncio.sleep(max(0.0, float(self.retry_backoff_seconds)))

        if item.is_final:
            self.stats["broker_publish_final_failure_total"] += 1

    async def _xadd(self, stream: str, fields: dict[str, str]) -> None:
        client = await self._redis()
        await client.xadd(
            stream,
            fields,
            maxlen=max(1, int(self.stream_maxlen)),
            approximate=True,
        )

    async def _redis(self) -> Any:
        if self.redis_client is not None:
            return self.redis_client

        now = _now_ms()
        if now < self._connect_retry_after_ms:
            raise RuntimeError(self.stats.get("last_broker_error") or "redis_connect_backoff")

        try:
            import redis.asyncio as redis_asyncio  # type: ignore

            client = redis_asyncio.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await client.ping()
            self.redis_client = client
            self._owns_client = True
            return client
        except Exception as exc:
            self.stats["broker_connect_failure_total"] += 1
            self.stats["last_broker_error"] = str(exc)[:240]
            self._connect_retry_after_ms = now + int(
                max(0.1, float(self.connect_retry_interval_seconds)) * 1000
            )
            raise

    def _record_latency(self, latency_ms: int) -> None:
        latency_ms = max(0, int(latency_ms))
        count = int(self.stats.get("broker_publish_latency_samples_total", 0))
        self.stats["broker_publish_latency_samples_total"] += 1
        self.stats["broker_publish_latency_ms_total"] += latency_ms
        if count == 0:
            self.stats["broker_publish_latency_ms_min"] = latency_ms
        else:
            self.stats["broker_publish_latency_ms_min"] = min(
                int(self.stats.get("broker_publish_latency_ms_min", latency_ms)),
                latency_ms,
            )
        self.stats["broker_publish_latency_ms_max"] = max(
            int(self.stats.get("broker_publish_latency_ms_max", 0)),
            latency_ms,
        )

    def _log_publish_failure(
        self,
        item: BrokerPublishItem,
        stream: str,
        attempt: int,
        attempts: int,
        error: str,
    ) -> None:
        failure_count = int(self.stats.get("broker_publish_failure_total", 0))
        sample_every = max(1, int(self.failure_log_sample_every))
        should_log = item.is_final or failure_count <= 5 or failure_count % sample_every == 0
        if not should_log:
            return
        self._log(
            "[ChartBrokerShadow] publish failure "
            f"stream={stream}, key={item.event.candle_key}, event_type={item.event.event_type}, "
            f"attempt={attempt}/{attempts}, error={error}"
        )

    async def _summary_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            self.log_summary()

    def _log(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger(message)
        except Exception:
            pass


@dataclass
class NatsJetStreamChartEventPublisher(RedisStreamChartEventPublisher):
    nats_url: str = DEFAULT_NATS_URL
    subject_prefix: str = DEFAULT_NATS_SUBJECT_PREFIX
    partial_stream: str = DEFAULT_NATS_PARTIAL_STREAM
    critical_stream: str = DEFAULT_NATS_CRITICAL_STREAM
    partial_max_age_seconds: float = DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS
    critical_max_age_seconds: float = DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS
    duplicate_window_seconds: float = DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS
    connect_timeout_seconds: float = 2.0
    nats_client: Any | None = None
    nats_js: Any | None = None
    _owns_nats_client: bool = field(default=False, init=False)

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return

        self._closing = False
        if self._partial_queue is None:
            self._partial_queue = asyncio.Queue(maxsize=max(1, int(self.queue_max)))
        if self._critical_queue is None:
            self._critical_queue = asyncio.Queue(maxsize=max(1, int(self.critical_queue_max)))

        self._worker_task = asyncio.create_task(
            self._publisher_loop(),
            name="chart-broker-shadow-nats-publisher",
        )
        self._summary_task = asyncio.create_task(
            self._summary_loop(max(1.0, float(self.summary_interval_seconds))),
            name="chart-broker-shadow-nats-summary",
        )
        self._log(
            "[ChartBrokerShadow] NATS JetStream publisher started "
            f"url={self.nats_url}, subject_prefix={self.subject_prefix}, "
            f"partial_stream={self.partial_stream}, critical_stream={self.critical_stream}, "
            f"queue_max={self.queue_max}, critical_queue_max={self.critical_queue_max}"
        )

    async def close(self) -> None:
        self._closing = True
        if self._summary_task and not self._summary_task.done():
            self._summary_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._summary_task
        self._summary_task = None

        deadline = time.monotonic() + max(0.0, float(self.drain_timeout_seconds))
        while self.queue_depth > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if self.queue_depth > 0:
            self.stats["broker_publish_drain_timeout_total"] += 1

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        self._worker_task = None

        if self.nats_client is not None and self._owns_nats_client:
            close = getattr(self.nats_client, "drain", None) or getattr(
                self.nats_client,
                "close",
                None,
            )
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        await result
        self.nats_client = None
        self.nats_js = None
        self._owns_nats_client = False
        self.log_summary(reason="shutdown")

    async def _publish_item(self, item: BrokerPublishItem) -> None:
        attempts = self.final_retry_attempts if item.is_final else self.partial_retry_attempts
        attempts = max(1, int(attempts))
        subject = nats_subject_for_event(item.event, subject_prefix=self.subject_prefix)
        stream = nats_stream_for_event_type(
            item.event.event_type,
            partial_stream=self.partial_stream,
            critical_stream=self.critical_stream,
        )
        last_error = ""

        for attempt in range(1, attempts + 1):
            self.stats["broker_publish_attempt_total"] += 1
            publish_ts = _now_ms()
            payload = build_broker_shadow_payload(
                item.event,
                received_at_ms=item.received_at_ms,
                volume=item.volume,
                publish_ts_ms=publish_ts,
            )
            headers = {"Nats-Msg-Id": nats_msg_id_for_payload(payload)}
            started = time.monotonic()
            try:
                js = await self._jetstream()
                await asyncio.wait_for(
                    js.publish(
                        subject,
                        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
                            "utf-8"
                        ),
                        timeout=max(0.05, float(self.publish_timeout_seconds)),
                        stream=stream,
                        headers=headers,
                    ),
                    timeout=max(0.05, float(self.publish_timeout_seconds)) + 0.2,
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                self._record_latency(latency_ms)
                self.stats["broker_publish_success_total"] += 1
                self._stream_counts[subject] += 1
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)
                self.stats["broker_publish_failure_total"] += 1
                self.stats["last_broker_error"] = last_error[:240]
                self._log_publish_failure(item, subject, attempt, attempts, last_error)
                if attempt < attempts:
                    await asyncio.sleep(max(0.0, float(self.retry_backoff_seconds)))

        if item.is_final:
            self.stats["broker_publish_final_failure_total"] += 1

    async def _jetstream(self) -> Any:
        if self.nats_js is not None:
            return self.nats_js

        now = _now_ms()
        if now < self._connect_retry_after_ms:
            raise RuntimeError(self.stats.get("last_broker_error") or "nats_connect_backoff")

        try:
            import nats  # type: ignore

            self.nats_client = await nats.connect(
                self.nats_url,
                name="market-workbench-chart-broker-shadow-publisher",
                connect_timeout=max(0.1, float(self.connect_timeout_seconds)),
                allow_reconnect=True,
                max_reconnect_attempts=-1,
                error_cb=self._nats_error_cb,
                disconnected_cb=self._nats_disconnected_cb,
                reconnected_cb=self._nats_reconnected_cb,
            )
            self._owns_nats_client = True
            self.nats_js = self.nats_client.jetstream()
            await ensure_nats_chart_streams(
                self.nats_js,
                subject_prefix=self.subject_prefix,
                partial_stream=self.partial_stream,
                critical_stream=self.critical_stream,
                partial_max_age_seconds=self.partial_max_age_seconds,
                critical_max_age_seconds=self.critical_max_age_seconds,
                duplicate_window_seconds=self.duplicate_window_seconds,
            )
            return self.nats_js
        except Exception as exc:
            self.stats["broker_connect_failure_total"] += 1
            self.stats["last_broker_error"] = str(exc)[:240]
            self._connect_retry_after_ms = now + int(
                max(0.1, float(self.connect_retry_interval_seconds)) * 1000
            )
            if self.nats_client is not None and self._owns_nats_client:
                close = getattr(self.nats_client, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        with contextlib.suppress(Exception):
                            await result
            self.nats_client = None
            self.nats_js = None
            self._owns_nats_client = False
            raise

    async def _nats_error_cb(self, exc: Exception) -> None:
        self.stats["last_broker_error"] = str(exc)[:240]
        self._log(f"[ChartBrokerShadow] NATS client error error={exc}")

    async def _nats_disconnected_cb(self) -> None:
        self._log("[ChartBrokerShadow] NATS client disconnected")

    async def _nats_reconnected_cb(self) -> None:
        self._log("[ChartBrokerShadow] NATS client reconnected")


def is_chart_broker_publish_shadow_enabled() -> bool:
    return _env_bool("CHART_BROKER_PUBLISH_SHADOW_ENABLED", False)


def build_chart_broker_shadow_publisher_from_env(
    *,
    logger: Any = log,
) -> ChartBrokerShadowPublisher | None:
    if not is_chart_broker_publish_shadow_enabled():
        return None

    kind = str(os.getenv("CHART_BROKER_KIND", DEFAULT_BROKER_KIND)).strip().lower()
    if kind in {"nats", "nats_jetstream", "jetstream"}:
        queue_max = max(1, _env_int("CHART_BROKER_PUBLISH_QUEUE_MAX", 2000))
        critical_queue_max = max(
            1,
            _env_int("CHART_BROKER_PUBLISH_CRITICAL_QUEUE_MAX", max(100, queue_max // 4)),
        )
        return NatsJetStreamChartEventPublisher(
            nats_url=os.getenv("NATS_URL", DEFAULT_NATS_URL),
            subject_prefix=os.getenv("NATS_SUBJECT_PREFIX", DEFAULT_NATS_SUBJECT_PREFIX),
            partial_stream=os.getenv("NATS_STREAM_PARTIAL", DEFAULT_NATS_PARTIAL_STREAM),
            critical_stream=os.getenv("NATS_STREAM_CRITICAL", DEFAULT_NATS_CRITICAL_STREAM),
            partial_max_age_seconds=max(
                1.0,
                _env_float("NATS_STREAM_PARTIAL_MAX_AGE_SEC", DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS),
            ),
            critical_max_age_seconds=max(
                1.0,
                _env_float(
                    "NATS_STREAM_CRITICAL_MAX_AGE_SEC",
                    DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS,
                ),
            ),
            duplicate_window_seconds=max(
                1.0,
                _env_float("NATS_DUPLICATE_WINDOW_SEC", DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS),
            ),
            queue_max=queue_max,
            critical_queue_max=critical_queue_max,
            publish_timeout_seconds=max(
                0.05,
                _env_float("CHART_BROKER_PUBLISH_TIMEOUT_SEC", 1.0),
            ),
            partial_retry_attempts=max(1, _env_int("CHART_BROKER_PUBLISH_PARTIAL_RETRY", 1)),
            final_retry_attempts=max(1, _env_int("CHART_BROKER_PUBLISH_FINAL_RETRY", 3)),
            retry_backoff_seconds=max(
                0.0,
                _env_float("CHART_BROKER_PUBLISH_RETRY_BACKOFF_SEC", 0.25),
            ),
            summary_interval_seconds=max(
                1.0,
                _env_float("CHART_BROKER_PUBLISH_SUMMARY_INTERVAL_SEC", 60.0),
            ),
            drain_timeout_seconds=max(
                0.0,
                _env_float("CHART_BROKER_PUBLISH_DRAIN_TIMEOUT_SEC", 2.0),
            ),
            connect_timeout_seconds=max(0.1, _env_float("NATS_CONNECT_TIMEOUT_SEC", 2.0)),
            logger=logger,
        )

    if kind not in {"redis_streams", "redis"}:
        if logger is not None:
            try:
                logger(
                    "[ChartBrokerShadow] unsupported broker kind "
                    f"kind={kind}; shadow broker publish disabled"
                )
            except Exception:
                pass
        return None

    queue_max = max(1, _env_int("CHART_BROKER_PUBLISH_QUEUE_MAX", 2000))
    critical_queue_max = max(
        1,
        _env_int("CHART_BROKER_PUBLISH_CRITICAL_QUEUE_MAX", max(100, queue_max // 4)),
    )
    return RedisStreamChartEventPublisher(
        redis_url=os.getenv("CHART_BROKER_URL", DEFAULT_REDIS_URL),
        stream_prefix=os.getenv("CHART_BROKER_STREAM_PREFIX", DEFAULT_STREAM_PREFIX),
        queue_max=queue_max,
        critical_queue_max=critical_queue_max,
        publish_timeout_seconds=max(
            0.05,
            _env_float("CHART_BROKER_PUBLISH_TIMEOUT_SEC", 1.0),
        ),
        partial_retry_attempts=max(1, _env_int("CHART_BROKER_PUBLISH_PARTIAL_RETRY", 1)),
        final_retry_attempts=max(1, _env_int("CHART_BROKER_PUBLISH_FINAL_RETRY", 3)),
        retry_backoff_seconds=max(
            0.0,
            _env_float("CHART_BROKER_PUBLISH_RETRY_BACKOFF_SEC", 0.25),
        ),
        stream_maxlen=max(1, _env_int("CHART_BROKER_STREAM_MAXLEN", 50000)),
        summary_interval_seconds=max(
            1.0,
            _env_float("CHART_BROKER_PUBLISH_SUMMARY_INTERVAL_SEC", 60.0),
        ),
        drain_timeout_seconds=max(
            0.0,
            _env_float("CHART_BROKER_PUBLISH_DRAIN_TIMEOUT_SEC", 2.0),
        ),
        logger=logger,
    )
