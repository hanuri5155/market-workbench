from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from app.api.services.chart_broker_shadow import (
    BROKER_SCHEMA_VERSION,
    DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS,
    DEFAULT_NATS_CRITICAL_STREAM,
    DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS,
    DEFAULT_NATS_SUBJECT_PREFIX,
    DEFAULT_NATS_URL,
    _env_bool,
    _env_float,
    _env_int,
    ensure_nats_chart_streams,
    nats_subject_pattern_for_lane,
)
from core.utils.log_utils import log
from core.ws.chart_events import ChartCandlePayload, normalize_chart_candle_payload


DEFAULT_STORAGE_CONSUMER = "chart-storage-final-reconcile"
DEFAULT_FETCH_BATCH = 10
DEFAULT_FETCH_TIMEOUT_SECONDS = 1.0
DEFAULT_ACK_WAIT_SECONDS = 30.0
DEFAULT_MAX_DELIVER = 20
DEFAULT_MAX_ACK_PENDING = 100


CREATE_DEDUPE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chart_event_dedupe (
  event_id VARCHAR(190) NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  candle_key VARCHAR(190) NOT NULL,
  symbol VARCHAR(20) NOT NULL,
  interval_min INT NOT NULL,
  bar_time DATETIME(3) NOT NULL,
  nats_stream VARCHAR(128) NULL,
  nats_sequence BIGINT UNSIGNED NULL,
  nats_subject VARCHAR(255) NULL,
  processed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (event_id),
  KEY ix_chart_event_dedupe_candle (symbol, interval_min, bar_time),
  KEY ix_chart_event_dedupe_processed_at (processed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _default_db_cfg() -> dict[str, Any]:
    from core.persistence.mysql_conn import DB_CFG

    return dict(DB_CFG)


@dataclass(frozen=True)
class NatsMessageInfo:
    subject: str
    stream: str | None = None
    stream_sequence: int | None = None
    consumer_sequence: int | None = None
    num_delivered: int = 1


@dataclass(frozen=True)
class DurableFinalReconcileEvent:
    event_id: str
    event_type: str
    exchange: str
    symbol: str
    interval_min: int
    bar_time_ms: int
    candle_key: str
    idempotency_key: str
    candle: ChartCandlePayload
    volume: float | None = None
    turnover: float | None = None

    @property
    def start_time_utc(self) -> datetime:
        return datetime.fromtimestamp(self.bar_time_ms / 1000.0, tz=timezone.utc).replace(
            tzinfo=None
        )


@dataclass(frozen=True)
class DurableWriteResult:
    event_id: str
    duplicate: bool = False
    inserted: bool = False


class DurableEventValidationError(ValueError):
    pass


class DurableEventWriteError(RuntimeError):
    pass


class DurableFilterConfigError(ValueError):
    pass


class DurableConsumerConfigMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChartStorageFilterConfig:
    filter_subject: str = ""
    filter_subjects: tuple[str, ...] = ()

    @property
    def is_multi(self) -> bool:
        return bool(self.filter_subjects)

    @property
    def mode(self) -> str:
        return "multi" if self.is_multi else "single"

    @property
    def subjects(self) -> tuple[str, ...]:
        if self.filter_subjects:
            return self.filter_subjects
        return (self.filter_subject,)

    def consumer_config_filter_kwargs(self) -> dict[str, Any]:
        if self.filter_subjects:
            return {"filter_subjects": list(self.filter_subjects)}
        return {"filter_subject": self.filter_subject}

    def describe(self) -> str:
        return ",".join(self.subjects)


def _safe_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _interval_to_minutes(value: Any) -> int:
    token = str(value or "").strip().upper()
    if token == "D":
        return 1440
    return int(token)


def _bounded_id(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DurableEventValidationError(f"missing {field_name}")
    if len(text) > 190:
        raise DurableEventValidationError(f"{field_name} too long")
    return text


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != "":
            return str(value)
    return default


def _parse_filter_subjects_value(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if not value.strip():
            return ()
        raw_subjects = value.split(",")
    elif isinstance(value, Sequence):
        raw_subjects = [str(item) for item in value]
    else:
        raw_subjects = [str(value)]

    subjects: list[str] = []
    seen: set[str] = set()
    for raw in raw_subjects:
        subject = str(raw).strip()
        if not subject:
            raise DurableFilterConfigError("CHART_STORAGE_FILTER_SUBJECTS contains an empty subject")
        _validate_exact_filter_subject(subject)
        if subject in seen:
            raise DurableFilterConfigError(
                f"CHART_STORAGE_FILTER_SUBJECTS contains duplicate subject: {subject}"
            )
        seen.add(subject)
        subjects.append(subject)
    return tuple(subjects)


def _validate_exact_filter_subject(subject: str) -> None:
    if any(ch in subject for ch in {"*", ">"}):
        raise DurableFilterConfigError(
            f"multi-filter subject must be exact and cannot contain wildcards: {subject}"
        )
    if any(ch.isspace() for ch in subject):
        raise DurableFilterConfigError(f"multi-filter subject cannot contain whitespace: {subject}")
    parts = subject.split(".")
    if len(parts) != 5 or parts[0] != "candles" or parts[1] != "critical":
        raise DurableFilterConfigError(
            "multi-filter subject must match candles.critical.<exchange>.<symbol>.<interval>: "
            f"{subject}"
        )
    if any(not part for part in parts):
        raise DurableFilterConfigError(f"multi-filter subject contains an empty token: {subject}")


def build_chart_storage_filter_config(
    *,
    filter_subjects: Any = None,
    filter_subject: str = "",
    default_filter_subject: str = "",
) -> ChartStorageFilterConfig:
    exact_subjects = _parse_filter_subjects_value(filter_subjects)
    if exact_subjects:
        return ChartStorageFilterConfig(filter_subjects=exact_subjects)

    subject = str(filter_subject or default_filter_subject or "").strip()
    if not subject:
        raise DurableFilterConfigError("single filter subject is empty")
    return ChartStorageFilterConfig(filter_subject=subject)


def _filter_config_compare_key(config: ChartStorageFilterConfig) -> tuple[str, tuple[str, ...]]:
    if config.is_multi:
        return ("multi", tuple(sorted(config.filter_subjects)))
    return ("single", (config.filter_subject,))


def _filter_config_from_consumer_info(info: Any) -> ChartStorageFilterConfig:
    consumer_config = _get_attr_or_key(info, "config")
    filter_subjects = _get_attr_or_key(consumer_config, "filter_subjects")
    if filter_subjects:
        return ChartStorageFilterConfig(filter_subjects=tuple(str(item) for item in filter_subjects))
    filter_subject = str(_get_attr_or_key(consumer_config, "filter_subject") or "").strip()
    return ChartStorageFilterConfig(filter_subject=filter_subject)


def _get_attr_or_key(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def build_durable_final_reconcile_event_from_payload(
    payload: dict[str, Any],
) -> DurableFinalReconcileEvent:
    if not isinstance(payload, dict):
        raise DurableEventValidationError("payload must be an object")
    if str(payload.get("schema_version") or "") != BROKER_SCHEMA_VERSION:
        raise DurableEventValidationError("schema_version mismatch")

    event_type = str(payload.get("event_type") or "").strip().lower()
    if event_type not in {"final", "reconcile"}:
        raise DurableEventValidationError("event_type must be final or reconcile")
    if not _safe_bool(payload.get("is_final")):
        raise DurableEventValidationError("final/reconcile payload must be final")

    exchange = str(payload.get("exchange") or "bybit").strip().lower()
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        raise DurableEventValidationError("missing symbol")
    if len(symbol) > 20:
        raise DurableEventValidationError("symbol too long")

    interval_value = payload.get("tf", payload.get("interval"))
    try:
        interval_min = _interval_to_minutes(interval_value)
    except Exception as exc:
        raise DurableEventValidationError("invalid interval") from exc

    candle = payload.get("candle")
    if not isinstance(candle, dict):
        candle = {
            "start": payload.get("bar_time") or payload.get("start_time"),
            "end": payload.get("end_time"),
            "open": payload.get("open"),
            "high": payload.get("high"),
            "low": payload.get("low"),
            "close": payload.get("close"),
            "confirm": True,
        }
    normalized = normalize_chart_candle_payload(candle, is_final=True)
    if normalized is None:
        raise DurableEventValidationError("invalid candle")

    try:
        bar_time_ms = int(payload.get("bar_time") or payload.get("start_time") or normalized["start"])
    except Exception as exc:
        raise DurableEventValidationError("invalid bar_time") from exc

    candle_key = _bounded_id(
        payload.get("candle_key") or f"{exchange}:{symbol}:{interval_min}:{bar_time_ms}",
        field_name="candle_key",
    )
    idempotency_key = _bounded_id(
        payload.get("idempotency_key") or f"{candle_key}:{event_type}",
        field_name="idempotency_key",
    )
    event_id = _bounded_id(
        payload.get("event_id") or idempotency_key,
        field_name="event_id",
    )

    return DurableFinalReconcileEvent(
        event_id=event_id,
        event_type=event_type,
        exchange=exchange,
        symbol=symbol,
        interval_min=interval_min,
        bar_time_ms=bar_time_ms,
        candle_key=candle_key,
        idempotency_key=idempotency_key,
        candle=normalized,
        volume=_safe_float(payload.get("volume") or candle.get("volume")),
        turnover=_safe_float(payload.get("turnover") or candle.get("turnover")),
    )


class MySqlFinalReconcileStore:
    def __init__(
        self,
        *,
        db_cfg: dict[str, Any] | None = None,
        final_source: str = "chart_nats_final",
        reconcile_source: str = "chart_nats_reconcile",
        auto_create_schema: bool = False,
        fail_before_commit_event_id: str = "",
        fail_after_commit_event_id: str = "",
    ) -> None:
        self.db_cfg = dict(db_cfg) if db_cfg is not None else _default_db_cfg()
        self.final_source = str(final_source or "chart_nats_final")[:32]
        self.reconcile_source = str(reconcile_source or "chart_nats_reconcile")[:32]
        self.auto_create_schema = bool(auto_create_schema)
        self.fail_before_commit_event_id = str(fail_before_commit_event_id or "")
        self.fail_after_commit_event_id = str(fail_after_commit_event_id or "")

    def ensure_schema(self) -> None:
        if not self.auto_create_schema:
            return
        import pymysql

        cfg = {**self.db_cfg, "autocommit": True}
        with pymysql.connect(**cfg) as cx:
            with cx.cursor() as cur:
                cur.execute(CREATE_DEDUPE_TABLE_SQL)

    def write_event(
        self,
        event: DurableFinalReconcileEvent,
        *,
        message_info: NatsMessageInfo,
    ) -> DurableWriteResult:
        import pymysql

        cfg = {**self.db_cfg, "autocommit": False}
        cx = pymysql.connect(**cfg)
        try:
            with cx.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO chart_event_dedupe
                          (event_id, event_type, candle_key, symbol, interval_min, bar_time,
                           nats_stream, nats_sequence, nats_subject)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            event.event_id,
                            event.event_type,
                            event.candle_key,
                            event.symbol,
                            int(event.interval_min),
                            event.start_time_utc,
                            message_info.stream,
                            message_info.stream_sequence,
                            message_info.subject,
                        ),
                    )
                except pymysql.err.IntegrityError as exc:
                    if exc.args and int(exc.args[0]) == 1062:
                        cx.rollback()
                        return DurableWriteResult(event_id=event.event_id, duplicate=True)
                    raise

                if event.event_id == self.fail_before_commit_event_id:
                    raise DurableEventWriteError("test failure before commit")

                cur.execute(
                    """
                    INSERT INTO candles
                      (symbol, interval_min, start_time,
                       open, high, low, close,
                       volume, turnover, source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      open=VALUES(open),
                      high=VALUES(high),
                      low=VALUES(low),
                      close=VALUES(close),
                      volume=VALUES(volume),
                      turnover=VALUES(turnover),
                      source=VALUES(source)
                    """,
                    (
                        event.symbol,
                        int(event.interval_min),
                        event.start_time_utc,
                        float(event.candle["open"]),
                        float(event.candle["high"]),
                        float(event.candle["low"]),
                        float(event.candle["close"]),
                        event.volume,
                        event.turnover,
                        self._source_for_event(event),
                    ),
                )

            cx.commit()
        except Exception:
            with contextlib.suppress(Exception):
                cx.rollback()
            raise
        finally:
            with contextlib.suppress(Exception):
                cx.close()

        if event.event_id == self.fail_after_commit_event_id:
            raise DurableEventWriteError("test failure after commit")
        return DurableWriteResult(event_id=event.event_id, inserted=True)

    def _source_for_event(self, event: DurableFinalReconcileEvent) -> str:
        if event.event_type == "reconcile":
            return self.reconcile_source
        return self.final_source


@dataclass
class NatsJetStreamFinalDurableConsumer:
    store: MySqlFinalReconcileStore
    nats_url: str = DEFAULT_NATS_URL
    subject_prefix: str = DEFAULT_NATS_SUBJECT_PREFIX
    critical_stream: str = DEFAULT_NATS_CRITICAL_STREAM
    consumer: str = DEFAULT_STORAGE_CONSUMER
    filter_subject: str = ""
    filter_subjects: tuple[str, ...] = field(default_factory=tuple)
    critical_max_age_seconds: float = DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS
    duplicate_window_seconds: float = DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS
    fetch_batch: int = DEFAULT_FETCH_BATCH
    fetch_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS
    ack_wait_seconds: float = DEFAULT_ACK_WAIT_SECONDS
    max_deliver: int = DEFAULT_MAX_DELIVER
    max_ack_pending: int = DEFAULT_MAX_ACK_PENDING
    connect_timeout_seconds: float = 2.0
    nak_delay_seconds: float = 2.0
    term_invalid_messages: bool = True
    deliver_policy: str = "new"
    logger: Any = log
    _nc: Any | None = field(default=None, init=False)
    _js: Any | None = field(default=None, init=False)
    _sub: Any | None = field(default=None, init=False)
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "messages_seen_total": 0,
            "ack_total": 0,
            "nak_total": 0,
            "term_total": 0,
            "schema_drop_total": 0,
            "db_write_total": 0,
            "duplicate_total": 0,
            "redelivery_total": 0,
        },
        init=False,
    )

    async def setup(self) -> None:
        self.store.ensure_schema()
        await self._connect()
        await self._ensure_consumer()

    async def close(self) -> None:
        if self._nc is not None:
            close = getattr(self._nc, "drain", None) or getattr(self._nc, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        await result
        self._nc = None
        self._js = None
        self._sub = None

    async def run_forever(self) -> None:
        await self.setup()
        self._log(
            "[ChartNatsFinalDurable] worker started "
            f"stream={self.critical_stream}, consumer={self.consumer}, "
            f"filter_mode={self._filter_config().mode}, "
            f"filter={self._filter_config().describe()}, ack_policy=explicit"
        )
        try:
            while True:
                await self.consume_once()
        finally:
            await self.close()

    async def consume_until_idle(
        self,
        *,
        max_messages: int | None = None,
        idle_timeout_seconds: float = 2.0,
    ) -> int:
        await self.setup()
        consumed = 0
        idle_deadline = time.monotonic() + max(0.1, float(idle_timeout_seconds))
        try:
            while True:
                handled = await self.consume_once()
                consumed += handled
                if max_messages is not None and consumed >= max_messages:
                    return consumed
                if handled:
                    idle_deadline = time.monotonic() + max(0.1, float(idle_timeout_seconds))
                    continue
                if time.monotonic() >= idle_deadline:
                    return consumed
        finally:
            await self.close()

    async def consume_once(self) -> int:
        from nats.js.errors import FetchTimeoutError  # type: ignore

        if self._js is None:
            await self.setup()
        assert self._js is not None
        sub = await self._subscription()
        try:
            messages = await sub.fetch(
                batch=max(1, int(self.fetch_batch)),
                timeout=max(0.1, float(self.fetch_timeout_seconds)),
            )
        except FetchTimeoutError:
            return 0
        except Exception as exc:
            if _is_fetch_timeout(exc):
                return 0
            raise

        for msg in messages:
            await self._handle_msg(msg)
        return len(messages)

    async def _subscription(self) -> Any:
        if self._sub is not None:
            return self._sub
        assert self._js is not None
        self._sub = await self._js.pull_subscribe(
            self._subscription_subject(),
            durable=self.consumer,
            stream=self.critical_stream,
        )
        return self._sub

    async def _connect(self) -> None:
        if self._js is not None:
            return
        import nats  # type: ignore

        self._nc = await nats.connect(
            self.nats_url,
            name="market-workbench-chart-final-durable",
            connect_timeout=max(0.1, float(self.connect_timeout_seconds)),
            allow_reconnect=True,
            max_reconnect_attempts=-1,
        )
        self._js = self._nc.jetstream()
        await ensure_nats_chart_streams(
            self._js,
            subject_prefix=self.subject_prefix,
            critical_stream=self.critical_stream,
            critical_max_age_seconds=self.critical_max_age_seconds,
            duplicate_window_seconds=self.duplicate_window_seconds,
        )

    async def _ensure_consumer(self) -> None:
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, ReplayPolicy  # type: ignore
        from nats.js.errors import NotFoundError  # type: ignore

        assert self._js is not None
        desired_filter = self._filter_config()
        try:
            info = await self._js.consumer_info(self.critical_stream, self.consumer)
            self._validate_existing_consumer_config(info, desired_filter)
            return
        except NotFoundError:
            pass

        deliver_policy = (
            DeliverPolicy.ALL
            if str(self.deliver_policy).strip().lower() == "all"
            else DeliverPolicy.NEW
        )
        await self._js.add_consumer(
            self.critical_stream,
            config=ConsumerConfig(
                name=self.consumer,
                durable_name=self.consumer,
                deliver_policy=deliver_policy,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=max(1.0, float(self.ack_wait_seconds)),
                max_deliver=max(1, int(self.max_deliver)),
                max_ack_pending=max(1, int(self.max_ack_pending)),
                replay_policy=ReplayPolicy.INSTANT,
                **desired_filter.consumer_config_filter_kwargs(),
            ),
        )

    async def _handle_msg(self, msg: Any) -> None:
        self.stats["messages_seen_total"] += 1
        message_info = _message_info_from_msg(msg)
        if message_info.num_delivered > 1:
            self.stats["redelivery_total"] += 1

        try:
            payload = json.loads(msg.data.decode("utf-8"))
            event = build_durable_final_reconcile_event_from_payload(payload)
        except Exception as exc:
            await self._handle_invalid_message(msg, str(exc))
            return

        try:
            result = await asyncio.to_thread(
                self.store.write_event,
                event,
                message_info=message_info,
            )
            if result.duplicate:
                self.stats["duplicate_total"] += 1
            if result.inserted:
                self.stats["db_write_total"] += 1
            # Ack is intentionally after MySQL commit or duplicate confirmation.
            await msg.ack()
            self.stats["ack_total"] += 1
            self._log(
                "[ChartNatsFinalDurable] processed "
                f"event_id={event.event_id}, event_type={event.event_type}, "
                f"symbol={event.symbol}, tf={event.interval_min}, duplicate={result.duplicate}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats["nak_total"] += 1
            with contextlib.suppress(Exception):
                await msg.nak(delay=max(0.1, float(self.nak_delay_seconds)))
            self._log(
                "[ChartNatsFinalDurable] db write failed; message not acked "
                f"subject={message_info.subject}, stream_seq={message_info.stream_sequence}, "
                f"consumer_seq={message_info.consumer_sequence}, event_id={event.event_id}, "
                f"idempotency_key={event.idempotency_key}, error={exc}"
            )

    async def _handle_invalid_message(self, msg: Any, reason: str) -> None:
        self.stats["schema_drop_total"] += 1
        message_info = _message_info_from_msg(msg)
        payload_ids = _payload_ids_from_bytes(getattr(msg, "data", b""))
        if self.term_invalid_messages:
            self.stats["term_total"] += 1
            with contextlib.suppress(Exception):
                await msg.term()
            action = "term"
        else:
            self.stats["nak_total"] += 1
            with contextlib.suppress(Exception):
                await msg.nak(delay=max(0.1, float(self.nak_delay_seconds)))
            action = "nak"
        self._log(
            "[ChartNatsFinalDurable] invalid message "
            f"action={action}, subject={message_info.subject}, "
            f"stream_seq={message_info.stream_sequence}, "
            f"consumer_seq={message_info.consumer_sequence}, "
            f"deliveries={message_info.num_delivered}, "
            f"event_id={payload_ids.get('event_id', '')}, "
            f"idempotency_key={payload_ids.get('idempotency_key', '')}, "
            f"payload_sha256={_payload_sha256(getattr(msg, 'data', b''))}, "
            f"reason={reason}"
        )

    def _filter_subject(self) -> str:
        return self._filter_config().subjects[0]

    def _subscription_subject(self) -> str:
        return self._filter_config().subjects[0]

    def _filter_config(self) -> ChartStorageFilterConfig:
        return build_chart_storage_filter_config(
            filter_subjects=self.filter_subjects,
            filter_subject=self.filter_subject,
            default_filter_subject=nats_subject_pattern_for_lane(
                "critical",
                subject_prefix=self.subject_prefix,
            ),
        )

    def _validate_existing_consumer_config(
        self,
        info: Any,
        desired_filter: ChartStorageFilterConfig,
    ) -> None:
        current_filter = _filter_config_from_consumer_info(info)
        if _filter_config_compare_key(current_filter) == _filter_config_compare_key(desired_filter):
            return
        raise DurableConsumerConfigMismatchError(
            "[ChartNatsFinalDurable] existing consumer filter mismatch; "
            f"stream={self.critical_stream}, consumer={self.consumer}, "
            f"current_mode={current_filter.mode}, current_filter={current_filter.describe()}, "
            f"desired_mode={desired_filter.mode}, desired_filter={desired_filter.describe()}. "
            "Refusing to delete/recreate consumer automatically."
        )

    def _log(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger(message)
        except Exception:
            pass


_durable_consumer: NatsJetStreamFinalDurableConsumer | None = None
_durable_task: asyncio.Task | None = None


def is_chart_nats_final_durable_enabled() -> bool:
    return _env_bool("NATS_FINAL_DURABLE_API_EMBEDDED_ENABLED", False)


def is_chart_storage_worker_enabled() -> bool:
    return _env_bool("CHART_STORAGE_ENABLED", _env_bool("NATS_FINAL_DURABLE_ENABLED", False))


async def start_chart_nats_final_durable_from_env() -> NatsJetStreamFinalDurableConsumer | None:
    global _durable_consumer, _durable_task
    if not is_chart_nats_final_durable_enabled():
        return None
    if _durable_task and not _durable_task.done():
        return _durable_consumer

    _durable_consumer = build_consumer_from_env(logger=log)
    _durable_task = asyncio.create_task(
        _durable_consumer.run_forever(),
        name="chart-nats-final-durable",
    )
    return _durable_consumer


async def shutdown_chart_nats_final_durable() -> None:
    global _durable_consumer, _durable_task
    task = _durable_task
    consumer = _durable_consumer
    _durable_task = None
    _durable_consumer = None
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if consumer is not None:
        await consumer.close()


def build_consumer_from_env(*, logger: Any = log) -> NatsJetStreamFinalDurableConsumer:
    store = MySqlFinalReconcileStore(
        auto_create_schema=_env_bool("NATS_FINAL_DURABLE_AUTO_CREATE_SCHEMA", False),
        final_source=os.getenv("NATS_FINAL_DURABLE_DB_SOURCE_FINAL", "chart_nats_final"),
        reconcile_source=os.getenv(
            "NATS_FINAL_DURABLE_DB_SOURCE_RECONCILE",
            "chart_nats_reconcile",
        ),
        fail_before_commit_event_id=os.getenv(
            "NATS_FINAL_DURABLE_FAIL_BEFORE_COMMIT_EVENT_ID",
            "",
        ),
        fail_after_commit_event_id=os.getenv(
            "NATS_FINAL_DURABLE_FAIL_AFTER_COMMIT_EVENT_ID",
            "",
        ),
    )
    return NatsJetStreamFinalDurableConsumer(
        store=store,
        nats_url=os.getenv("NATS_URL", DEFAULT_NATS_URL),
        subject_prefix=os.getenv("NATS_SUBJECT_PREFIX", DEFAULT_NATS_SUBJECT_PREFIX),
        critical_stream=os.getenv("NATS_STREAM_CRITICAL", DEFAULT_NATS_CRITICAL_STREAM),
        consumer=_first_env(
            "CHART_STORAGE_CONSUMER_NAME",
            "NATS_CONSUMER_STORAGE_CRITICAL",
            default=DEFAULT_STORAGE_CONSUMER,
        ),
        filter_subject=_first_env(
            "CHART_STORAGE_FILTER_SUBJECT",
            "NATS_FINAL_DURABLE_FILTER_SUBJECT",
            default="",
        ),
        filter_subjects=_parse_filter_subjects_value(os.getenv("CHART_STORAGE_FILTER_SUBJECTS")),
        critical_max_age_seconds=max(
            1.0,
            _env_float("NATS_STREAM_CRITICAL_MAX_AGE_SEC", DEFAULT_NATS_CRITICAL_MAX_AGE_SECONDS),
        ),
        duplicate_window_seconds=max(
            1.0,
            _env_float("NATS_DUPLICATE_WINDOW_SEC", DEFAULT_NATS_DUPLICATE_WINDOW_SECONDS),
        ),
        fetch_batch=max(1, _env_int("NATS_FINAL_DURABLE_FETCH_BATCH", DEFAULT_FETCH_BATCH)),
        fetch_timeout_seconds=max(
            0.1,
            _env_float("NATS_FINAL_DURABLE_FETCH_TIMEOUT_SEC", DEFAULT_FETCH_TIMEOUT_SECONDS),
        ),
        ack_wait_seconds=max(
            1.0,
            _env_float("NATS_FINAL_DURABLE_ACK_WAIT_SEC", DEFAULT_ACK_WAIT_SECONDS),
        ),
        max_deliver=max(1, _env_int("NATS_FINAL_DURABLE_MAX_DELIVER", DEFAULT_MAX_DELIVER)),
        max_ack_pending=max(
            1,
            _env_int("NATS_FINAL_DURABLE_MAX_ACK_PENDING", DEFAULT_MAX_ACK_PENDING),
        ),
        connect_timeout_seconds=max(0.1, _env_float("NATS_CONNECT_TIMEOUT_SEC", 2.0)),
        nak_delay_seconds=max(0.1, _env_float("NATS_FINAL_DURABLE_NAK_DELAY_SEC", 2.0)),
        term_invalid_messages=_env_bool("NATS_FINAL_DURABLE_TERM_INVALID", True),
        deliver_policy=os.getenv("NATS_FINAL_DURABLE_DELIVER_POLICY", "new"),
        logger=logger,
    )


def _message_info_from_msg(msg: Any) -> NatsMessageInfo:
    stream_sequence = None
    consumer_sequence = None
    stream = None
    num_delivered = 1
    with contextlib.suppress(Exception):
        metadata = msg.metadata
        stream = str(getattr(metadata, "stream", "") or "") or None
        num_delivered = int(getattr(metadata, "num_delivered", 1) or 1)
        sequence = getattr(metadata, "sequence", None)
        stream_sequence = int(getattr(sequence, "stream", 0) or 0) or None
        consumer_sequence = int(getattr(sequence, "consumer", 0) or 0) or None
    return NatsMessageInfo(
        subject=str(getattr(msg, "subject", "") or ""),
        stream=stream,
        stream_sequence=stream_sequence,
        consumer_sequence=consumer_sequence,
        num_delivered=num_delivered,
    )


def _payload_sha256(data: Any) -> str:
    if isinstance(data, str):
        raw = data.encode("utf-8", errors="replace")
    elif isinstance(data, bytes):
        raw = data
    else:
        raw = str(data or "").encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def _payload_ids_from_bytes(data: Any) -> dict[str, str]:
    if isinstance(data, str):
        raw = data.encode("utf-8", errors="replace")
    elif isinstance(data, bytes):
        raw = data
    else:
        raw = str(data or "").encode("utf-8", errors="replace")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"event_id": "", "idempotency_key": ""}
    if not isinstance(payload, dict):
        return {"event_id": "", "idempotency_key": ""}
    return {
        "event_id": str(payload.get("event_id") or "")[:190],
        "idempotency_key": str(payload.get("idempotency_key") or "")[:190],
    }


def _is_fetch_timeout(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timeout" in message


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NATS final/reconcile durable storage worker")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-messages", type=int, default=None)
    parser.add_argument("--idle-timeout-sec", type=float, default=2.0)
    return parser.parse_args()


async def _amain() -> int:
    args = _parse_args()
    if not is_chart_storage_worker_enabled():
        log(
            "[ChartNatsFinalDurable] disabled: "
            "CHART_STORAGE_ENABLED/NATS_FINAL_DURABLE_ENABLED=false"
        )
        return 0
    consumer = build_consumer_from_env(logger=log)
    if args.setup_only:
        await consumer.setup()
        await consumer.close()
        log(
            "[ChartNatsFinalDurable] setup complete "
            f"stream={consumer.critical_stream}, consumer={consumer.consumer}, "
            f"filter_mode={consumer._filter_config().mode}, "
            f"filter={consumer._filter_config().describe()}"
        )
        return 0
    if args.once:
        count = await consumer.consume_until_idle(
            max_messages=args.max_messages,
            idle_timeout_seconds=args.idle_timeout_sec,
        )
        log(f"[ChartNatsFinalDurable] once complete consumed={count}, stats={consumer.stats}")
        return 0
    await consumer.run_forever()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
