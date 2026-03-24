from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

import websockets

from app.api.services.chart_broker_shadow import (
    ChartBrokerShadowPublisher,
    build_chart_broker_shadow_publisher_from_env,
)
from core.utils.log_utils import log
from core.ws.chart_events import LiveCandleEvent, build_live_candle_event


ShadowTransport = Literal["bot_http", "chart_ingest_shadow", "nats_jetstream_shadow"]

SUPPORTED_INTERVALS = ("15", "30", "60", "240", "1440")
CANONICAL_TO_BYBIT_INTERVAL = {"1440": "D"}
BYBIT_TO_CANONICAL_INTERVAL = {"D": "1440"}
DEFAULT_BYBIT_PUBLIC_WS_URL = "wss://stream.bybit.com/v5/public/linear"


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


def _canonical_interval(interval: Any) -> str | None:
    raw = str(interval or "").strip()
    if not raw:
        return None

    if raw.lower().endswith("m") and raw[:-1].isdigit():
        raw = raw[:-1]

    upper = raw.upper()
    if upper in BYBIT_TO_CANONICAL_INTERVAL:
        return BYBIT_TO_CANONICAL_INTERVAL[upper]

    return raw if raw in SUPPORTED_INTERVALS else None


def _interval_to_bybit(interval: Any) -> str:
    canonical = _canonical_interval(interval)
    if canonical is None:
        return str(interval)
    return CANONICAL_TO_BYBIT_INTERVAL.get(canonical, canonical)


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip().lower() in {"", "none"}:
            return None
        return float(value)
    except Exception:
        return None


def _price_tolerance_from_env() -> float:
    default = _env_float("CHART_REST_VERIFY_PRICE_TOLERANCE", 0.01)
    return max(0.0, _env_float("CHART_INGEST_SHADOW_PRICE_TOLERANCE", default))


@dataclass(frozen=True)
class ObservedCandleEvent:
    transport: ShadowTransport
    event: LiveCandleEvent
    received_at_ms: int
    origin_source: str | None = None
    volume: float | None = None
    raw_confirm: bool | None = None

    @property
    def candle_key(self) -> str:
        return self.event.candle_key

    @property
    def compare_key(self) -> str:
        return self.event.idempotency_key

    @property
    def event_type(self) -> str:
        return self.event.event_type

    @property
    def is_final(self) -> bool:
        return bool(self.event.is_final)


@dataclass
class ChartIngestShadowCompareStore:
    max_events: int = 10000
    price_tolerance: float = 0.01
    volume_tolerance: float = 0.0
    lag_warn_ms: int = 2000
    summary_interval_seconds: float = 60.0
    logger: Any = log
    mismatch_log_sample_every: int = 10
    log_prefix: str = "[ChartIngestShadow]"
    _bot_events: OrderedDict[str, ObservedCandleEvent] = field(default_factory=OrderedDict, init=False)
    _shadow_events: OrderedDict[str, ObservedCandleEvent] = field(default_factory=OrderedDict, init=False)
    _missing_bot_http: OrderedDict[str, ObservedCandleEvent] = field(default_factory=OrderedDict, init=False)
    _missing_shadow: OrderedDict[str, ObservedCandleEvent] = field(default_factory=OrderedDict, init=False)
    _final_seen: dict[ShadowTransport, OrderedDict[str, bool]] = field(
        default_factory=lambda: {"bot_http": OrderedDict(), "chart_ingest_shadow": OrderedDict()},
        init=False,
    )
    _summary_task: asyncio.Task | None = field(default=None, init=False)
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "shadow_events_total": 0,
            "bot_http_events_seen_total": 0,
            "compared_total": 0,
            "match_total": 0,
            "mismatch_total": 0,
            "missing_bot_http_total": 0,
            "missing_shadow_total": 0,
            "final_match_total": 0,
            "final_mismatch_total": 0,
            "duplicate_final_total": 0,
            "late_partial_after_final_total": 0,
            "reconnect_count": 0,
            "shadow_ws_error_count": 0,
            "lag_samples_total": 0,
            "lag_ms_total": 0,
            "lag_ms_min": 0,
            "lag_ms_max": 0,
            "timing_lag_total": 0,
            "volume_comparable_total": 0,
            "volume_missing_total": 0,
            "volume_missing_bot_http_total": 0,
            "volume_missing_shadow_total": 0,
            "volume_mismatch_total": 0,
            "partial_drift_total": 0,
            "partial_ohlc_drift_total": 0,
            "partial_volume_drift_total": 0,
            "partial_drift_abs_max": 0.0,
            "reason_ohlc_diff_total": 0,
            "reason_volume_diff_total": 0,
            "reason_volume_missing_total": 0,
            "reason_partial_ohlc_drift_total": 0,
            "reason_partial_volume_drift_total": 0,
            "reason_timing_lag_total": 0,
            "reason_missing_bot_http_total": 0,
            "reason_missing_shadow_total": 0,
            "reason_event_type_diff_total": 0,
            "reason_final_flag_diff_total": 0,
            "reason_duplicate_final_total": 0,
        },
        init=False,
    )

    def start(self) -> None:
        if self._summary_task and not self._summary_task.done():
            return
        interval = max(1.0, float(self.summary_interval_seconds))
        self._summary_task = asyncio.create_task(
            self._summary_loop(interval),
            name="chart-ingest-shadow-compare-summary",
        )

    async def close(self) -> None:
        if self._summary_task and not self._summary_task.done():
            self._summary_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._summary_task
        self._summary_task = None
        self.log_summary(reason="shutdown")

    def record_bot_http_event(self, event: ObservedCandleEvent) -> None:
        self.stats["bot_http_events_seen_total"] += 1
        self._record_event(event, own_store=self._bot_events, other_store=self._shadow_events)

    def record_shadow_event(self, event: ObservedCandleEvent) -> None:
        self.stats["shadow_events_total"] += 1
        self._record_event(event, own_store=self._shadow_events, other_store=self._bot_events)

    def bump_reconnect(self) -> None:
        self.stats["reconnect_count"] += 1

    def bump_ws_error(self) -> None:
        self.stats["shadow_ws_error_count"] += 1

    def snapshot(self) -> dict[str, Any]:
        lag_count = int(self.stats.get("lag_samples_total", 0))
        lag_total = int(self.stats.get("lag_ms_total", 0))
        pending_bot_http_final = sum(1 for event in self._missing_bot_http.values() if event.is_final)
        pending_shadow_final = sum(1 for event in self._missing_shadow.values() if event.is_final)
        return {
            **self.stats,
            "lag_ms_avg": int(lag_total / lag_count) if lag_count else 0,
            "pending_missing_bot_http": len(self._missing_bot_http),
            "pending_missing_shadow": len(self._missing_shadow),
            "pending_missing_bot_http_final": pending_bot_http_final,
            "pending_missing_shadow_final": pending_shadow_final,
            "bot_store_size": len(self._bot_events),
            "shadow_store_size": len(self._shadow_events),
        }

    def log_summary(self, *, reason: str = "interval") -> None:
        snapshot = self.snapshot()
        self._log(
            f"{self.log_prefix} compare summary "
            f"reason={reason}, shadow_events_total={snapshot['shadow_events_total']}, "
            f"bot_http_events_seen_total={snapshot['bot_http_events_seen_total']}, "
            f"compared_total={snapshot['compared_total']}, match_total={snapshot['match_total']}, "
            f"mismatch_total={snapshot['mismatch_total']}, "
            f"missing_bot_http_total={snapshot['missing_bot_http_total']}, "
            f"missing_shadow_total={snapshot['missing_shadow_total']}, "
            f"final_match_total={snapshot['final_match_total']}, "
            f"final_mismatch_total={snapshot['final_mismatch_total']}, "
            f"duplicate_final_total={snapshot['duplicate_final_total']}, "
            f"late_partial_after_final_total={snapshot['late_partial_after_final_total']}, "
            f"volume_comparable_total={snapshot['volume_comparable_total']}, "
            f"volume_missing_total={snapshot['volume_missing_total']}, "
            f"volume_mismatch_total={snapshot['volume_mismatch_total']}, "
            f"partial_drift_total={snapshot['partial_drift_total']}, "
            f"partial_ohlc_drift_total={snapshot['partial_ohlc_drift_total']}, "
            f"partial_volume_drift_total={snapshot['partial_volume_drift_total']}, "
            f"partial_drift_abs_max={snapshot['partial_drift_abs_max']}, "
            f"lag_ms_min={snapshot['lag_ms_min']}, lag_ms_avg={snapshot['lag_ms_avg']}, "
            f"lag_ms_max={snapshot['lag_ms_max']}, timing_lag_total={snapshot['timing_lag_total']}, "
            f"pending_missing_bot_http={snapshot['pending_missing_bot_http']}, "
            f"pending_missing_shadow={snapshot['pending_missing_shadow']}, "
            f"pending_missing_bot_http_final={snapshot['pending_missing_bot_http_final']}, "
            f"pending_missing_shadow_final={snapshot['pending_missing_shadow_final']}, "
            f"reasons=ohlc_diff:{snapshot['reason_ohlc_diff_total']}/"
            f"volume_diff:{snapshot['reason_volume_diff_total']}/"
            f"volume_missing:{snapshot['reason_volume_missing_total']}/"
            f"partial_ohlc_drift:{snapshot['reason_partial_ohlc_drift_total']}/"
            f"partial_volume_drift:{snapshot['reason_partial_volume_drift_total']}/"
            f"timing_lag:{snapshot['reason_timing_lag_total']}/"
            f"missing_bot_http:{snapshot['reason_missing_bot_http_total']}/"
            f"missing_shadow:{snapshot['reason_missing_shadow_total']}/"
            f"event_type_diff:{snapshot['reason_event_type_diff_total']}/"
            f"final_flag_diff:{snapshot['reason_final_flag_diff_total']}/"
            f"duplicate_final:{snapshot['reason_duplicate_final_total']}, "
            f"reconnect_count={snapshot['reconnect_count']}, "
            f"shadow_ws_error_count={snapshot['shadow_ws_error_count']}"
        )

    def _record_event(
        self,
        event: ObservedCandleEvent,
        *,
        own_store: OrderedDict[str, ObservedCandleEvent],
        other_store: OrderedDict[str, ObservedCandleEvent],
    ) -> None:
        self._detect_final_ordering(event)
        self._remember(own_store, event.compare_key, event)

        counterpart = other_store.get(event.compare_key)
        if counterpart is None:
            self._mark_missing_counterpart(event)
            return

        self._missing_bot_http.pop(event.compare_key, None)
        self._missing_shadow.pop(event.compare_key, None)
        self._compare(counterpart, event)

    def _detect_final_ordering(self, event: ObservedCandleEvent) -> None:
        final_seen = self._final_seen.setdefault(event.transport, OrderedDict())
        if event.is_final:
            if event.candle_key in final_seen:
                self.stats["duplicate_final_total"] += 1
                self._bump_reason("duplicate_final")
                self._log(
                    f"{self.log_prefix} duplicate final observed "
                    f"source={event.transport}, key={event.candle_key}, "
                    f"count={self.stats['duplicate_final_total']}"
                )
            self._remember_final_key(final_seen, event.candle_key)
            return

        if event.event_type == "partial" and event.candle_key in final_seen:
            self.stats["late_partial_after_final_total"] += 1
            self._log(
                f"{self.log_prefix} late partial after final observed "
                f"source={event.transport}, key={event.candle_key}, "
                f"count={self.stats['late_partial_after_final_total']}"
            )

    def _mark_missing_counterpart(self, event: ObservedCandleEvent) -> None:
        if event.transport != "bot_http":
            pending = self._missing_bot_http
            stat = "missing_bot_http_total"
            reason = "missing_bot_http"
        else:
            pending = self._missing_shadow
            stat = "missing_shadow_total"
            reason = "missing_shadow"

        if event.compare_key not in pending:
            self.stats[stat] += 1
            self._bump_reason(reason)
        self._remember(pending, event.compare_key, event)

    def _compare(self, first: ObservedCandleEvent, second: ObservedCandleEvent) -> None:
        bot_event, shadow_event = (
            (first, second) if first.transport == "bot_http" else (second, first)
        )
        self.stats["compared_total"] += 1

        mismatches, reasons, drift_details, drift_reasons = self._compare_fields(
            bot_event,
            shadow_event,
        )
        lag_ms = abs(int(shadow_event.received_at_ms) - int(bot_event.received_at_ms))
        self._record_lag(lag_ms)
        if lag_ms > max(0, int(self.lag_warn_ms)):
            self.stats["timing_lag_total"] += 1
            reasons.add("timing_lag")
        if drift_details:
            self._record_partial_drift(drift_details, drift_reasons)
        for reason in reasons:
            self._bump_reason(reason)

        if mismatches:
            self.stats["mismatch_total"] += 1
            if bot_event.is_final or shadow_event.is_final:
                self.stats["final_mismatch_total"] += 1
            self._log_mismatch(bot_event, shadow_event, mismatches, reasons, lag_ms)
            return

        self.stats["match_total"] += 1
        if bot_event.is_final and shadow_event.is_final:
            self.stats["final_match_total"] += 1

    def _compare_fields(
        self,
        bot_event: ObservedCandleEvent,
        shadow_event: ObservedCandleEvent,
    ) -> tuple[list[str], set[str], list[str], set[str]]:
        mismatches: list[str] = []
        reasons: set[str] = set()
        drift_details: list[str] = []
        drift_reasons: set[str] = set()
        bot = bot_event.event
        shadow = shadow_event.event
        is_partial_pair = bot.event_type == "partial" and shadow.event_type == "partial"

        if bot.symbol != shadow.symbol:
            mismatches.append(f"symbol bot={bot.symbol} shadow={shadow.symbol}")
        if int(bot.tf) != int(shadow.tf):
            mismatches.append(f"tf bot={bot.tf} shadow={shadow.tf}")
        if int(bot.bar_time) != int(shadow.bar_time):
            mismatches.append(f"bar_time bot={bot.bar_time} shadow={shadow.bar_time}")
        if bot.event_type != shadow.event_type:
            mismatches.append(f"event_type bot={bot.event_type} shadow={shadow.event_type}")
            reasons.add("event_type_diff")
        if bool(bot.is_final) != bool(shadow.is_final):
            mismatches.append(f"is_final bot={bot.is_final} shadow={shadow.is_final}")
            reasons.add("final_flag_diff")
        if bool(bot.candle.get("confirm")) != bool(shadow.candle.get("confirm")):
            mismatches.append(
                f"confirm bot={bot.candle.get('confirm')} shadow={shadow.candle.get('confirm')}"
            )
            reasons.add("final_flag_diff")

        tolerance = max(0.0, float(self.price_tolerance))
        for field_name in ("open", "high", "low", "close"):
            bot_value = float(bot.candle[field_name])
            shadow_value = float(shadow.candle[field_name])
            delta = abs(bot_value - shadow_value)
            if delta > tolerance:
                detail = (
                    f"{field_name} bot={bot_value} shadow={shadow_value} "
                    f"delta={delta:.8f}"
                )
                if is_partial_pair:
                    drift_reasons.add("partial_ohlc_drift")
                    drift_details.append(detail)
                    continue
                reasons.add("ohlc_diff")
                mismatches.append(detail)

        if bot_event.volume is None or shadow_event.volume is None:
            self.stats["volume_missing_total"] += 1
            reasons.add("volume_missing")
            if bot_event.volume is None:
                self.stats["volume_missing_bot_http_total"] += 1
            if shadow_event.volume is None:
                self.stats["volume_missing_shadow_total"] += 1
        else:
            self.stats["volume_comparable_total"] += 1
            volume_delta = abs(float(bot_event.volume) - float(shadow_event.volume))
            if volume_delta > max(0.0, float(self.volume_tolerance)):
                detail = (
                    f"volume bot={bot_event.volume} shadow={shadow_event.volume} "
                    f"delta={volume_delta:.8f}"
                )
                if is_partial_pair:
                    drift_reasons.add("partial_volume_drift")
                    drift_details.append(detail)
                else:
                    self.stats["volume_mismatch_total"] += 1
                    mismatches.append(detail)
                    reasons.add("volume_diff")

        return mismatches, reasons, drift_details, drift_reasons

    def _record_partial_drift(self, drift_details: list[str], drift_reasons: set[str]) -> None:
        self.stats["partial_drift_total"] += 1
        if "partial_ohlc_drift" in drift_reasons:
            self.stats["partial_ohlc_drift_total"] += 1
        if "partial_volume_drift" in drift_reasons:
            self.stats["partial_volume_drift_total"] += 1
        for reason in drift_reasons:
            self._bump_reason(reason)

        max_delta = self._max_delta_from_details(drift_details)
        self.stats["partial_drift_abs_max"] = max(
            float(self.stats.get("partial_drift_abs_max", 0.0)),
            float(max_delta),
        )

    def _max_delta_from_details(self, details: list[str]) -> float:
        max_delta = 0.0
        for detail in details:
            try:
                token = str(detail).rsplit("delta=", 1)[1]
                max_delta = max(max_delta, float(token))
            except Exception:
                continue
        return max_delta

    def _record_lag(self, lag_ms: int) -> None:
        lag_ms = max(0, int(lag_ms))
        previous_count = int(self.stats.get("lag_samples_total", 0))
        self.stats["lag_samples_total"] += 1
        self.stats["lag_ms_total"] += lag_ms
        if previous_count == 0:
            self.stats["lag_ms_min"] = lag_ms
        else:
            self.stats["lag_ms_min"] = min(int(self.stats.get("lag_ms_min", lag_ms)), lag_ms)
        self.stats["lag_ms_max"] = max(int(self.stats.get("lag_ms_max", 0)), lag_ms)

    def _log_mismatch(
        self,
        bot_event: ObservedCandleEvent,
        shadow_event: ObservedCandleEvent,
        mismatches: list[str],
        reasons: set[str],
        lag_ms: int,
    ) -> None:
        count = int(self.stats.get("mismatch_total", 0))
        sample_every = max(1, int(self.mismatch_log_sample_every))
        if count > 5 and count % sample_every != 0:
            return
        self._log(
            f"{self.log_prefix} mismatch "
            f"key={bot_event.candle_key}, event_type={bot_event.event_type}, lag_ms={lag_ms}, "
            f"bot_origin={bot_event.origin_source}, reasons={','.join(sorted(reasons))}, "
            f"mismatches={'; '.join(mismatches)}"
        )

    def _bump_reason(self, reason: str) -> None:
        key = f"reason_{reason}_total"
        if key in self.stats:
            self.stats[key] += 1

    def _remember(
        self,
        store: OrderedDict[str, ObservedCandleEvent],
        key: str,
        event: ObservedCandleEvent,
    ) -> None:
        store[key] = event
        store.move_to_end(key)
        limit = max(1, int(self.max_events))
        while len(store) > limit:
            store.popitem(last=False)

    def _remember_final_key(self, store: OrderedDict[str, bool], key: str) -> None:
        store[key] = True
        store.move_to_end(key)
        limit = max(1, int(self.max_events))
        while len(store) > limit:
            store.popitem(last=False)

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


def build_bot_http_observed_event(
    *,
    symbol: Any,
    tf: Any,
    candle: dict[str, Any],
    is_final: bool,
    source: str | None = None,
    source_seq: Any = None,
    received_at_ms: int | None = None,
) -> ObservedCandleEvent | None:
    try:
        event = build_live_candle_event(
            symbol=str(symbol),
            tf=int(tf),
            candle=candle,
            is_final=bool(is_final),
            source="bot_http",
            source_seq=_safe_int(source_seq),
            emitted_at_ms=received_at_ms or _now_ms(),
        )
        return ObservedCandleEvent(
            transport="bot_http",
            event=event,
            received_at_ms=received_at_ms or _now_ms(),
            origin_source=str(source or "unknown"),
            volume=_safe_float(candle.get("volume")),
            raw_confirm=bool(candle.get("confirm", is_final)),
        )
    except Exception:
        return None


def build_shadow_observed_event_from_bybit_message(
    raw_message: str | dict[str, Any],
    *,
    default_symbol: str,
    source_seq: int,
    received_at_ms: int | None = None,
) -> ObservedCandleEvent | None:
    received_at_ms = received_at_ms or _now_ms()
    try:
        data = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    topic = str(data.get("topic") or "")
    if not topic.startswith("kline."):
        return None

    parts = topic.split(".")
    if len(parts) < 2:
        return None
    interval = _canonical_interval(parts[1])
    if interval is None:
        return None

    symbol = str(parts[2] if len(parts) >= 3 and parts[2] else default_symbol).upper().strip()
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    kline = rows[0]
    if not isinstance(kline, dict):
        return None

    start = _safe_int(kline.get("start"))
    end = _safe_int(kline.get("end"))
    open_ = _safe_float(kline.get("open"))
    high = _safe_float(kline.get("high"))
    low = _safe_float(kline.get("low"))
    close = _safe_float(kline.get("close"))
    if None in {start, end, open_, high, low, close}:
        return None

    confirm = bool(kline.get("confirm", False))
    candle = {
        "start": int(start),
        "end": int(end),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "confirm": confirm,
    }
    exchange_ts = _safe_int(data.get("ts") or kline.get("timestamp") or kline.get("ts"))

    try:
        event = LiveCandleEvent(
            event_type="final" if confirm else "partial",
            exchange="bybit",
            symbol=symbol,
            tf=int(interval),
            candle=candle,
            is_final=confirm,
            source="chart_ingest_shadow",
            source_seq=int(source_seq),
            emitted_at_ms=received_at_ms,
            exchange_ts=exchange_ts,
        )
        return ObservedCandleEvent(
            transport="chart_ingest_shadow",
            event=event,
            received_at_ms=received_at_ms,
            origin_source="chart_ingest_shadow",
            volume=_safe_float(kline.get("volume")),
            raw_confirm=confirm,
        )
    except Exception:
        return None


class BybitChartIngestShadowService:
    def __init__(
        self,
        *,
        symbol: str,
        intervals: list[str],
        ws_url: str,
        compare_store: ChartIngestShadowCompareStore,
        reconnect_base_delay_seconds: float = 5.0,
        reconnect_max_delay_seconds: float = 60.0,
        broker_publisher: ChartBrokerShadowPublisher | None = None,
        logger: Any = log,
    ) -> None:
        self.symbol = str(symbol or "BTCUSDT").upper().strip()
        self.intervals = [
            interval for interval in (_canonical_interval(value) for value in intervals) if interval
        ] or list(SUPPORTED_INTERVALS)
        self.ws_url = str(ws_url or DEFAULT_BYBIT_PUBLIC_WS_URL)
        self.compare_store = compare_store
        self.reconnect_base_delay_seconds = max(1.0, float(reconnect_base_delay_seconds))
        self.reconnect_max_delay_seconds = max(
            self.reconnect_base_delay_seconds,
            float(reconnect_max_delay_seconds),
        )
        self.broker_publisher = broker_publisher
        self.logger = logger
        self._task: asyncio.Task | None = None
        self._closing = False
        self._source_seq = 0

    @property
    def topics(self) -> list[str]:
        return [
            f"kline.{_interval_to_bybit(interval)}.{self.symbol}"
            for interval in self.intervals
        ]

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._closing = False
        self._task = asyncio.create_task(self._run_loop(), name="chart-ingest-shadow-bybit")
        self._log(
            "[ChartIngestShadow] service started "
            f"symbol={self.symbol}, intervals={','.join(self.intervals)}, ws_url={self.ws_url}"
        )

    async def close(self) -> None:
        self._closing = True
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._log("[ChartIngestShadow] service stopped")

    async def _run_loop(self) -> None:
        attempt = 0
        while not self._closing:
            try:
                attempt += 1
                if attempt > 1:
                    self.compare_store.bump_reconnect()
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.compare_store.bump_ws_error()
                self._log(
                    "[ChartIngestShadow] ws loop error "
                    f"error={exc}, shadow_ws_error_count={self.compare_store.stats['shadow_ws_error_count']}"
                )

            if self._closing:
                return

            delay = min(
                self.reconnect_max_delay_seconds,
                self.reconnect_base_delay_seconds * max(1, min(attempt, 6)),
            )
            await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        async with websockets.connect(
            self.ws_url,
            ping_interval=None,
            open_timeout=30,
        ) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": self.topics}))
            self._log(
                "[ChartIngestShadow] Bybit public WS subscribed "
                f"topics={','.join(self.topics)}"
            )
            keep_alive_task = asyncio.create_task(self._keep_alive(ws), name="chart-ingest-shadow-ping")
            try:
                async for message in ws:
                    self._handle_message(message)
            finally:
                if not keep_alive_task.done():
                    keep_alive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await keep_alive_task

    async def _keep_alive(self, ws: Any) -> None:
        try:
            while True:
                await ws.send(json.dumps({"op": "ping"}))
                pong_waiter = ws.ping()
                await pong_waiter
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.compare_store.bump_ws_error()
            self._log(
                "[ChartIngestShadow] keepalive error "
                f"error={exc}, shadow_ws_error_count={self.compare_store.stats['shadow_ws_error_count']}"
            )
            with contextlib.suppress(Exception):
                await ws.close()

    def _handle_message(self, message: str) -> None:
        self._source_seq += 1
        observed = build_shadow_observed_event_from_bybit_message(
            message,
            default_symbol=self.symbol,
            source_seq=self._source_seq,
            received_at_ms=_now_ms(),
        )
        if observed is None:
            return
        self.compare_store.record_shadow_event(observed)
        if self.broker_publisher is not None:
            self.broker_publisher.enqueue_event(
                observed.event,
                received_at_ms=observed.received_at_ms,
                volume=observed.volume,
            )

    def _log(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger(message)
        except Exception:
            pass


_compare_store: ChartIngestShadowCompareStore | None = None
_shadow_service: BybitChartIngestShadowService | None = None
_broker_publisher: ChartBrokerShadowPublisher | None = None


def is_chart_ingest_shadow_enabled() -> bool:
    return _env_bool("CHART_INGEST_SHADOW_ENABLED", False)


def _intervals_from_env() -> list[str]:
    raw = os.getenv("CHART_INGEST_SHADOW_INTERVALS")
    if raw:
        parsed = [
            interval
            for interval in (_canonical_interval(part) for part in raw.split(","))
            if interval
        ]
        if parsed:
            return parsed
    return list(SUPPORTED_INTERVALS)


def get_chart_ingest_shadow_compare_store() -> ChartIngestShadowCompareStore | None:
    return _compare_store


def get_chart_broker_shadow_publisher() -> ChartBrokerShadowPublisher | None:
    return _broker_publisher


async def start_chart_ingest_shadow_from_env() -> BybitChartIngestShadowService | None:
    global _broker_publisher, _compare_store, _shadow_service
    if not is_chart_ingest_shadow_enabled():
        return None

    if _shadow_service and _shadow_service._task and not _shadow_service._task.done():
        return _shadow_service

    _compare_store = ChartIngestShadowCompareStore(
        max_events=max(100, _env_int("CHART_INGEST_SHADOW_COMPARE_MAX_EVENTS", 10000)),
        price_tolerance=_price_tolerance_from_env(),
        volume_tolerance=max(0.0, _env_float("CHART_INGEST_SHADOW_VOLUME_TOLERANCE", 0.0)),
        lag_warn_ms=max(0, _env_int("CHART_INGEST_SHADOW_LAG_WARN_MS", 2000)),
        summary_interval_seconds=max(
            1.0,
            _env_float("CHART_INGEST_SHADOW_SUMMARY_INTERVAL_SEC", 60.0),
        ),
        mismatch_log_sample_every=max(
            1,
            _env_int("CHART_INGEST_SHADOW_MISMATCH_LOG_SAMPLE_EVERY", 10),
        ),
        logger=log,
    )
    _compare_store.start()
    _broker_publisher = build_chart_broker_shadow_publisher_from_env(logger=log)
    if _broker_publisher is not None:
        await _broker_publisher.start()

    symbol = os.getenv("SYMBOL", "BTCUSDT")
    ws_url = (
        os.getenv("CHART_INGEST_SHADOW_BYBIT_WS_URL")
        or os.getenv("BYBIT_WS_URL")
        or DEFAULT_BYBIT_PUBLIC_WS_URL
    )
    _shadow_service = BybitChartIngestShadowService(
        symbol=symbol,
        intervals=_intervals_from_env(),
        ws_url=ws_url,
        compare_store=_compare_store,
        reconnect_base_delay_seconds=max(
            1.0,
            _env_float("CHART_INGEST_SHADOW_RECONNECT_BASE_SEC", 5.0),
        ),
        reconnect_max_delay_seconds=max(
            1.0,
            _env_float("CHART_INGEST_SHADOW_RECONNECT_MAX_SEC", 60.0),
        ),
        broker_publisher=_broker_publisher,
        logger=log,
    )
    await _shadow_service.start()
    return _shadow_service


async def shutdown_chart_ingest_shadow() -> None:
    global _broker_publisher, _compare_store, _shadow_service
    service = _shadow_service
    broker_publisher = _broker_publisher
    store = _compare_store
    _shadow_service = None
    _broker_publisher = None
    _compare_store = None

    if service is not None:
        await service.close()
    if broker_publisher is not None:
        await broker_publisher.close()
    if store is not None:
        await store.close()


def capture_bot_http_live_update(
    *,
    symbol: Any,
    tf: Any,
    candle: dict[str, Any],
    is_final: bool,
    source: str | None = None,
    source_seq: Any = None,
) -> None:
    store = _compare_store
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
            received_at_ms=_now_ms(),
        )
        if observed is not None:
            store.record_bot_http_event(observed)
    except Exception as exc:
        log(f"[ChartIngestShadow] bot_http capture failed: {exc}")
