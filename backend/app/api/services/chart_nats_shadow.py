from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.api.services.chart_broker_shadow import (
    BROKER_SCHEMA_VERSION,
    DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS,
    DEFAULT_NATS_CRITICAL_STREAM,
    DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS,
    DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS,
    DEFAULT_NATS_PARTIAL_STREAM,
    DEFAULT_NATS_SUBJECT_PREFIX,
    DEFAULT_NATS_URL,
    _env_bool,
    _env_float,
    _env_int,
    _now_ms,
    ensure_nats_chart_streams,
    nats_subject_pattern_for_lane,
)
from app.api.services.chart_ingest_shadow import (
    ChartIngestShadowCompareStore,
    ObservedCandleEvent,
    build_bot_http_observed_event,
)
from core.utils.log_utils import log
from core.ws.chart_events import LiveCandleEvent


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def build_nats_shadow_observed_event_from_payload(
    payload: dict[str, Any],
    *,
    received_at_ms: int | None = None,
) -> ObservedCandleEvent | None:
    if not isinstance(payload, dict):
        return None
    if str(payload.get("schema_version") or "") != BROKER_SCHEMA_VERSION:
        return None

    event_type = str(payload.get("event_type") or "")
    if event_type not in {"partial", "final", "reconcile"}:
        return None

    candle = payload.get("candle")
    if not isinstance(candle, dict):
        candle = {
            "start": payload.get("bar_time") or payload.get("start_time"),
            "end": payload.get("end_time"),
            "open": payload.get("open"),
            "high": payload.get("high"),
            "low": payload.get("low"),
            "close": payload.get("close"),
            "confirm": payload.get("confirm"),
        }

    try:
        event = LiveCandleEvent(
            event_type=event_type,
            exchange=str(payload.get("exchange") or "bybit"),
            symbol=str(payload.get("symbol") or ""),
            tf=_safe_int(payload.get("tf") or payload.get("interval")),
            candle=candle,
            is_final=bool(payload.get("is_final")),
            source="nats_jetstream_shadow",
            source_seq=_safe_int(payload.get("source_seq"), 0) or None,
            reason=str(payload.get("reason") or "broker_shadow")
            if event_type == "reconcile"
            else None,
            emitted_at_ms=_safe_int(payload.get("emitted_at_ms"), _now_ms()),
            exchange_ts=_safe_int(payload.get("exchange_ts"), 0) or None,
        )
    except Exception:
        return None

    return ObservedCandleEvent(
        transport="nats_jetstream_shadow",
        event=event,
        received_at_ms=int(received_at_ms or _now_ms()),
        origin_source=str(payload.get("source") or "unknown"),
        volume=_safe_float(payload.get("volume") or candle.get("volume")),
        raw_confirm=bool(payload.get("confirm") if "confirm" in payload else candle.get("confirm")),
    )


@dataclass
class NatsJetStreamGatewayShadowSubscriber:
    compare_store: ChartIngestShadowCompareStore
    nats_url: str = DEFAULT_NATS_URL
    subject_prefix: str = DEFAULT_NATS_SUBJECT_PREFIX
    partial_stream: str = DEFAULT_NATS_PARTIAL_STREAM
    critical_stream: str = DEFAULT_NATS_CRITICAL_STREAM
    partial_consumer: str = "chart-api-gateway-shadow-partial"
    critical_consumer: str = "chart-api-gateway-shadow-critical"
    partial_max_age_seconds: float = DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS
    critical_max_age_seconds: float = DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS
    duplicate_window_seconds: float = DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS
    fetch_batch: int = 50
    fetch_timeout_seconds: float = 1.0
    reconnect_delay_seconds: float = 2.0
    summary_interval_seconds: float = 60.0
    ack_wait_seconds: float = 30.0
    max_deliver: int = 5
    max_ack_pending: int = 1000
    connect_timeout_seconds: float = 2.0
    logger: Any = log
    _task: asyncio.Task | None = field(default=None, init=False)
    _summary_task: asyncio.Task | None = field(default=None, init=False)
    _closing: bool = field(default=False, init=False)
    _nc: Any | None = field(default=None, init=False)
    _js: Any | None = field(default=None, init=False)
    _consumer_pending: dict[str, int] = field(default_factory=dict, init=False)
    _consumer_ack_pending: dict[str, int] = field(default_factory=dict, init=False)
    _consumer_redelivered: dict[str, int] = field(default_factory=dict, init=False)
    _subject_counts: Counter[str] = field(default_factory=Counter, init=False)
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "nats_shadow_subscribe_enabled": 1,
            "connect_total": 0,
            "reconnect_total": 0,
            "fetch_timeout_total": 0,
            "messages_seen_total": 0,
            "ack_total": 0,
            "nak_total": 0,
            "term_total": 0,
            "schema_drop_total": 0,
            "redelivery_total": 0,
            "consumer_info_failure_total": 0,
            "last_error": "",
        },
        init=False,
    )

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._closing = False
        self._task = asyncio.create_task(self._run_loop(), name="chart-nats-shadow-subscriber")
        self._summary_task = asyncio.create_task(
            self._summary_loop(max(1.0, float(self.summary_interval_seconds))),
            name="chart-nats-shadow-summary",
        )
        self._log(
            "[ChartNatsShadow] subscriber starting "
            f"url={self.nats_url}, partial_stream={self.partial_stream}, "
            f"critical_stream={self.critical_stream}, subject_prefix={self.subject_prefix}"
        )

    async def close(self) -> None:
        self._closing = True
        for task in (self._summary_task, self._task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._summary_task = None
        self._task = None
        await self._close_client()
        await self._log_summary(reason="shutdown")

    def snapshot(self) -> dict[str, Any]:
        subject_counts = ",".join(
            f"{subject}:{count}" for subject, count in self._subject_counts.most_common(8)
        )
        return {
            **self.stats,
            "partial_pending": self._consumer_pending.get(self.partial_consumer, 0),
            "critical_pending": self._consumer_pending.get(self.critical_consumer, 0),
            "partial_ack_pending": self._consumer_ack_pending.get(self.partial_consumer, 0),
            "critical_ack_pending": self._consumer_ack_pending.get(self.critical_consumer, 0),
            "partial_redelivered": self._consumer_redelivered.get(self.partial_consumer, 0),
            "critical_redelivered": self._consumer_redelivered.get(self.critical_consumer, 0),
            "subject_counts": subject_counts,
        }

    async def _run_loop(self) -> None:
        while not self._closing:
            try:
                await self._connect()
                partial_sub = await self._js.pull_subscribe(
                    nats_subject_pattern_for_lane("partial", subject_prefix=self.subject_prefix),
                    durable=self.partial_consumer,
                    stream=self.partial_stream,
                )
                critical_sub = await self._js.pull_subscribe(
                    nats_subject_pattern_for_lane("critical", subject_prefix=self.subject_prefix),
                    durable=self.critical_consumer,
                    stream=self.critical_stream,
                )
                await asyncio.gather(
                    self._consume_loop(partial_sub, self.partial_consumer),
                    self._consume_loop(critical_sub, self.critical_consumer),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closing:
                    return
                self.stats["reconnect_total"] += 1
                self.stats["last_error"] = str(exc)[:240]
                self.compare_store.bump_reconnect()
                self._log(
                    "[ChartNatsShadow] subscriber reconnect "
                    f"count={self.stats['reconnect_total']}, error={exc}"
                )
                await self._close_client()
                await asyncio.sleep(max(0.1, float(self.reconnect_delay_seconds)))

    async def _connect(self) -> None:
        import nats  # type: ignore
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, ReplayPolicy  # type: ignore
        from nats.js.errors import NotFoundError  # type: ignore

        self._nc = await nats.connect(
            self.nats_url,
            name="market-workbench-chart-api-gateway-shadow",
            connect_timeout=max(0.1, float(self.connect_timeout_seconds)),
            allow_reconnect=True,
            max_reconnect_attempts=-1,
            error_cb=self._nats_error_cb,
            disconnected_cb=self._nats_disconnected_cb,
            reconnected_cb=self._nats_reconnected_cb,
        )
        self._js = self._nc.jetstream()
        await ensure_nats_chart_streams(
            self._js,
            subject_prefix=self.subject_prefix,
            partial_stream=self.partial_stream,
            critical_stream=self.critical_stream,
            partial_max_age_seconds=self.partial_max_age_seconds,
            critical_max_age_seconds=self.critical_max_age_seconds,
            duplicate_window_seconds=self.duplicate_window_seconds,
        )
        for stream, consumer, lane in (
            (self.partial_stream, self.partial_consumer, "partial"),
            (self.critical_stream, self.critical_consumer, "critical"),
        ):
            try:
                await self._js.consumer_info(stream, consumer)
                continue
            except NotFoundError:
                pass
            await self._js.add_consumer(
                stream,
                config=ConsumerConfig(
                    name=consumer,
                    durable_name=consumer,
                    deliver_policy=DeliverPolicy.ALL,
                    ack_policy=AckPolicy.EXPLICIT,
                    ack_wait=max(1.0, float(self.ack_wait_seconds)),
                    max_deliver=max(1, int(self.max_deliver)),
                    max_ack_pending=max(1, int(self.max_ack_pending)),
                    replay_policy=ReplayPolicy.INSTANT,
                    filter_subject=nats_subject_pattern_for_lane(
                        lane,
                        subject_prefix=self.subject_prefix,
                    ),
                ),
            )
        self.stats["connect_total"] += 1
        self._log(
            "[ChartNatsShadow] subscriber connected "
            f"connect_total={self.stats['connect_total']}, ack_policy=explicit, "
            f"partial_consumer={self.partial_consumer}, critical_consumer={self.critical_consumer}"
        )

    async def _consume_loop(self, sub: Any, consumer_name: str) -> None:
        from nats.js.errors import FetchTimeoutError  # type: ignore

        while not self._closing:
            try:
                messages = await sub.fetch(
                    batch=max(1, int(self.fetch_batch)),
                    timeout=max(0.1, float(self.fetch_timeout_seconds)),
                )
            except FetchTimeoutError:
                self.stats["fetch_timeout_total"] += 1
                continue
            except Exception as exc:
                if _is_nats_fetch_timeout(exc):
                    self.stats["fetch_timeout_total"] += 1
                    continue
                raise
            for msg in messages:
                await self._handle_msg(msg, consumer_name)

    async def _handle_msg(self, msg: Any, consumer_name: str) -> None:
        self.stats["messages_seen_total"] += 1
        self._subject_counts[str(getattr(msg, "subject", ""))] += 1
        received_at_ms = _now_ms()
        try:
            metadata = msg.metadata
            if int(getattr(metadata, "num_delivered", 1)) > 1:
                self.stats["redelivery_total"] += 1
        except Exception:
            pass

        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except Exception as exc:
            await self._term_schema_drop(msg, f"invalid_json:{exc}")
            return

        observed = build_nats_shadow_observed_event_from_payload(
            payload,
            received_at_ms=received_at_ms,
        )
        if observed is None:
            await self._term_schema_drop(
                msg,
                "schema_mismatch",
                payload=payload if isinstance(payload, dict) else None,
            )
            return

        try:
            self.compare_store.record_shadow_event(observed)
            await msg.ack()
            self.stats["ack_total"] += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats["nak_total"] += 1
            self.stats["last_error"] = str(exc)[:240]
            with contextlib.suppress(Exception):
                await msg.nak(delay=max(0.1, float(self.reconnect_delay_seconds)))
            self._log(
                "[ChartNatsShadow] message processing failed "
                f"consumer={consumer_name}, subject={getattr(msg, 'subject', '')}, error={exc}"
            )

    async def _term_schema_drop(
        self,
        msg: Any,
        reason: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.stats["schema_drop_total"] += 1
        self.stats["term_total"] += 1
        fields = []
        if payload:
            for key in ("schema_version", "event_type", "symbol", "tf", "bar_time"):
                fields.append(f"{key}={payload.get(key)}")
        self._log(
            "[ChartNatsShadow] schema drop "
            f"subject={getattr(msg, 'subject', '')}, reason={reason}, "
            f"schema_drop_total={self.stats['schema_drop_total']}, {', '.join(fields)}"
        )
        with contextlib.suppress(Exception):
            await msg.term()

    async def _summary_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await self._refresh_consumer_info()
            await self._log_summary()

    async def _refresh_consumer_info(self) -> None:
        if self._js is None:
            return
        for stream, consumer in (
            (self.partial_stream, self.partial_consumer),
            (self.critical_stream, self.critical_consumer),
        ):
            try:
                info = await self._js.consumer_info(stream, consumer)
                self._consumer_pending[consumer] = int(info.num_pending or 0)
                self._consumer_ack_pending[consumer] = int(info.num_ack_pending or 0)
                self._consumer_redelivered[consumer] = int(info.num_redelivered or 0)
            except Exception as exc:
                self.stats["consumer_info_failure_total"] += 1
                self.stats["last_error"] = str(exc)[:240]

    async def _log_summary(self, *, reason: str = "interval") -> None:
        snapshot = self.snapshot()
        self._log(
            "[ChartNatsShadow] subscriber summary "
            f"reason={reason}, messages_seen_total={snapshot['messages_seen_total']}, "
            f"ack_total={snapshot['ack_total']}, nak_total={snapshot['nak_total']}, "
            f"term_total={snapshot['term_total']}, schema_drop_total={snapshot['schema_drop_total']}, "
            f"redelivery_total={snapshot['redelivery_total']}, "
            f"partial_pending={snapshot['partial_pending']}, "
            f"critical_pending={snapshot['critical_pending']}, "
            f"partial_ack_pending={snapshot['partial_ack_pending']}, "
            f"critical_ack_pending={snapshot['critical_ack_pending']}, "
            f"partial_redelivered={snapshot['partial_redelivered']}, "
            f"critical_redelivered={snapshot['critical_redelivered']}, "
            f"connect_total={snapshot['connect_total']}, reconnect_total={snapshot['reconnect_total']}, "
            f"last_error={snapshot['last_error']}, subject_counts={snapshot['subject_counts']}"
        )

    async def _close_client(self) -> None:
        if self._nc is not None:
            close = getattr(self._nc, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        await result
        self._nc = None
        self._js = None

    def _log(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger(message)
        except Exception:
            pass

    async def _nats_error_cb(self, exc: Exception) -> None:
        self.stats["last_error"] = str(exc)[:240]
        self._log(f"[ChartNatsShadow] NATS client error error={exc}")

    async def _nats_disconnected_cb(self) -> None:
        self._log("[ChartNatsShadow] NATS client disconnected")

    async def _nats_reconnected_cb(self) -> None:
        self._log("[ChartNatsShadow] NATS client reconnected")


_nats_shadow_compare_store: ChartIngestShadowCompareStore | None = None
_nats_shadow_subscriber: NatsJetStreamGatewayShadowSubscriber | None = None


def is_chart_nats_shadow_subscribe_enabled() -> bool:
    return _env_bool("NATS_SHADOW_SUBSCRIBE_ENABLED", False)


def _is_nats_fetch_timeout(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timeout" in message


def get_chart_nats_shadow_compare_store() -> ChartIngestShadowCompareStore | None:
    return _nats_shadow_compare_store


def get_chart_nats_shadow_subscriber() -> NatsJetStreamGatewayShadowSubscriber | None:
    return _nats_shadow_subscriber


async def start_chart_nats_shadow_from_env() -> NatsJetStreamGatewayShadowSubscriber | None:
    global _nats_shadow_compare_store, _nats_shadow_subscriber
    if not is_chart_nats_shadow_subscribe_enabled():
        return None
    if _nats_shadow_subscriber and _nats_shadow_subscriber._task and not _nats_shadow_subscriber._task.done():
        return _nats_shadow_subscriber

    _nats_shadow_compare_store = ChartIngestShadowCompareStore(
        max_events=max(100, _env_int("NATS_SHADOW_COMPARE_MAX_EVENTS", 10000)),
        price_tolerance=max(0.0, _env_float("CHART_INGEST_SHADOW_PRICE_TOLERANCE", 0.01)),
        volume_tolerance=max(0.0, _env_float("CHART_INGEST_SHADOW_VOLUME_TOLERANCE", 0.0)),
        lag_warn_ms=max(0, _env_int("NATS_SHADOW_LAG_WARN_MS", _env_int("CHART_INGEST_SHADOW_LAG_WARN_MS", 2000))),
        summary_interval_seconds=max(1.0, _env_float("NATS_SHADOW_COMPARE_SUMMARY_INTERVAL_SEC", 60.0)),
        mismatch_log_sample_every=max(1, _env_int("NATS_SHADOW_MISMATCH_LOG_SAMPLE_EVERY", 10)),
        log_prefix="[ChartNatsShadow]",
        logger=log,
    )
    _nats_shadow_compare_store.start()
    _nats_shadow_subscriber = NatsJetStreamGatewayShadowSubscriber(
        compare_store=_nats_shadow_compare_store,
        nats_url=os.getenv("NATS_URL", DEFAULT_NATS_URL),
        subject_prefix=os.getenv("NATS_SUBJECT_PREFIX", DEFAULT_NATS_SUBJECT_PREFIX),
        partial_stream=os.getenv("NATS_STREAM_PARTIAL", DEFAULT_NATS_PARTIAL_STREAM),
        critical_stream=os.getenv("NATS_STREAM_CRITICAL", DEFAULT_NATS_CRITICAL_STREAM),
        partial_consumer=os.getenv(
            "NATS_CONSUMER_API_GATEWAY_PARTIAL",
            "chart-api-gateway-shadow-partial",
        ),
        critical_consumer=os.getenv(
            "NATS_CONSUMER_API_GATEWAY_CRITICAL",
            "chart-api-gateway-shadow-critical",
        ),
        partial_max_age_seconds=max(
            1.0,
            _env_float("NATS_STREAM_PARTIAL_MAX_AGE_SEC", DEFAULT_NATS_PARTIAL_MAX_AGE_SECONDS),
        ),
        critical_max_age_seconds=max(
            1.0,
            _env_float("NATS_STREAM_CRITICAL_MAX_AGE_SEC", DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS),
        ),
        duplicate_window_seconds=max(
            1.0,
            _env_float("NATS_DUPLICATE_WINDOW_SEC", DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS),
        ),
        fetch_batch=max(1, _env_int("NATS_SHADOW_FETCH_BATCH", 50)),
        fetch_timeout_seconds=max(0.1, _env_float("NATS_SHADOW_FETCH_TIMEOUT_SEC", 1.0)),
        reconnect_delay_seconds=max(0.1, _env_float("NATS_SHADOW_RECONNECT_DELAY_SEC", 2.0)),
        summary_interval_seconds=max(1.0, _env_float("NATS_SHADOW_SUMMARY_INTERVAL_SEC", 60.0)),
        ack_wait_seconds=max(1.0, _env_float("NATS_SHADOW_ACK_WAIT_SEC", 30.0)),
        max_deliver=max(1, _env_int("NATS_SHADOW_MAX_DELIVER", 5)),
        max_ack_pending=max(1, _env_int("NATS_SHADOW_MAX_ACK_PENDING", 1000)),
        connect_timeout_seconds=max(0.1, _env_float("NATS_CONNECT_TIMEOUT_SEC", 2.0)),
        logger=log,
    )
    await _nats_shadow_subscriber.start()
    return _nats_shadow_subscriber


async def shutdown_chart_nats_shadow() -> None:
    global _nats_shadow_compare_store, _nats_shadow_subscriber
    subscriber = _nats_shadow_subscriber
    store = _nats_shadow_compare_store
    _nats_shadow_subscriber = None
    _nats_shadow_compare_store = None
    if subscriber is not None:
        await subscriber.close()
    if store is not None:
        await store.close()


def capture_bot_http_for_nats_shadow(
    *,
    symbol: Any,
    tf: Any,
    candle: dict[str, Any],
    is_final: bool,
    source: str | None = None,
    source_seq: Any = None,
) -> None:
    store = _nats_shadow_compare_store
    if store is None:
        return

    try:
        observed = build_bot_http_observed_event(
            symbol=symbol,
            tf=tf,
            candle=candle,
            is_final=bool(is_final),
            source=source,
            source_seq=source_seq,
            received_at_ms=int(time.time() * 1000),
        )
        if observed is not None:
            store.record_bot_http_event(observed)
    except Exception as exc:
        log(f"[ChartNatsShadow] bot_http capture failed: {exc}")
