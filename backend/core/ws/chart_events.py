from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from typing_extensions import NotRequired


ChartEventType = Literal["partial", "final", "reconcile"]


class ChartCandlePayload(TypedDict):
    start: int
    end: int
    open: float
    high: float
    low: float
    close: float
    confirm: bool
    volume: NotRequired[float]


def _now_ms() -> int:
    return int(time.time() * 1000)


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


def normalize_chart_candle_payload(
    candle: dict[str, Any] | None,
    *,
    is_final: bool | None = None,
) -> ChartCandlePayload | None:
    if not isinstance(candle, dict):
        return None

    try:
        confirm = bool(candle.get("confirm", False) if is_final is None else is_final)
        normalized: ChartCandlePayload = {
            "start": int(candle["start"]),
            "end": int(candle["end"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "confirm": confirm,
        }
        volume = _optional_float(candle.get("volume"))
        if volume is not None:
            normalized["volume"] = volume
        return normalized
    except Exception:
        return None


@dataclass(frozen=True)
class LiveCandleEvent:
    event_type: ChartEventType
    exchange: str
    symbol: str
    tf: int
    candle: ChartCandlePayload
    is_final: bool
    source: str
    source_seq: int | None = None
    reason: str | None = None
    emitted_at_ms: int | None = None
    exchange_ts: int | None = None

    def __post_init__(self) -> None:
        normalized = normalize_chart_candle_payload(
            dict(self.candle),
            is_final=self.is_final,
        )
        if normalized is None:
            raise ValueError("invalid chart candle payload")

        if self.event_type == "partial" and self.is_final:
            raise ValueError("partial chart event cannot be final")
        if self.event_type in {"final", "reconcile"} and not self.is_final:
            raise ValueError(f"{self.event_type} chart event must be final")

        object.__setattr__(self, "exchange", str(self.exchange or "bybit").lower())
        object.__setattr__(self, "symbol", str(self.symbol or "").upper().strip())
        object.__setattr__(self, "tf", int(self.tf))
        object.__setattr__(self, "candle", normalized)
        object.__setattr__(self, "source", str(self.source or "chart_ingest_active"))
        if self.source_seq is not None:
            object.__setattr__(self, "source_seq", int(self.source_seq))
        if self.emitted_at_ms is None:
            object.__setattr__(self, "emitted_at_ms", _now_ms())

    @property
    def bar_time(self) -> int:
        return int(self.candle["start"])

    @property
    def candle_key(self) -> str:
        return f"{self.exchange}:{self.symbol}:{self.tf}:{self.bar_time}"

    @property
    def routing_key(self) -> str:
        return self.candle_key

    @property
    def idempotency_key(self) -> str:
        return f"{self.candle_key}:{self.event_type}"


def build_live_candle_event(
    *,
    symbol: str,
    tf: int,
    candle: dict[str, Any],
    is_final: bool,
    source: str = "chart_ingest_active",
    source_seq: int | None = None,
    exchange: str = "bybit",
    emitted_at_ms: int | None = None,
    exchange_ts: int | None = None,
) -> LiveCandleEvent:
    event_type: ChartEventType = "final" if is_final else "partial"
    normalized = normalize_chart_candle_payload(candle, is_final=is_final)
    if normalized is None:
        raise ValueError("invalid live candle payload")
    return LiveCandleEvent(
        event_type=event_type,
        exchange=exchange,
        symbol=symbol,
        tf=tf,
        candle=normalized,
        is_final=bool(is_final),
        source=source,
        source_seq=source_seq,
        emitted_at_ms=emitted_at_ms,
        exchange_ts=exchange_ts,
    )


def build_reconcile_candle_event(
    *,
    symbol: str,
    tf: int,
    candle: dict[str, Any],
    reason: str,
    source: str = "rest_reconcile",
    source_seq: int | None = None,
    exchange: str = "bybit",
    emitted_at_ms: int | None = None,
    exchange_ts: int | None = None,
) -> LiveCandleEvent:
    normalized = normalize_chart_candle_payload(candle, is_final=True)
    if normalized is None:
        raise ValueError("invalid reconcile candle payload")
    return LiveCandleEvent(
        event_type="reconcile",
        exchange=exchange,
        symbol=symbol,
        tf=tf,
        candle=normalized,
        is_final=True,
        source=source,
        source_seq=source_seq,
        reason=reason,
        emitted_at_ms=emitted_at_ms,
        exchange_ts=exchange_ts,
    )
