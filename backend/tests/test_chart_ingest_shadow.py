from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.services import chart_broker_shadow as broker_shadow
from app.api.services import chart_ingest_shadow as shadow
from app.api.services import chart_nats_shadow as nats_shadow


def _raw_kline(
    *,
    start: int = 1710000000000,
    interval: str = "15",
    confirm: bool = False,
    close: str = "100.5",
    volume: str = "12.3",
) -> dict:
    return {
        "topic": f"kline.{interval}.BTCUSDT",
        "ts": start + 100,
        "data": [
            {
                "start": start,
                "end": start + 15 * 60_000 - 1,
                "open": "100.0",
                "high": "101.0",
                "low": "99.0",
                "close": close,
                "volume": volume,
                "confirm": confirm,
            }
        ],
    }


def _store(max_events: int = 100) -> shadow.ChartIngestShadowCompareStore:
    return shadow.ChartIngestShadowCompareStore(
        max_events=max_events,
        price_tolerance=0.01,
        summary_interval_seconds=3600,
        logger=None,
    )


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class _FakeRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict, int, bool]] = []

    async def xadd(self, stream: str, fields: dict, *, maxlen: int, approximate: bool) -> str:
        self.messages.append((stream, fields, maxlen, approximate))
        return "1-0"


class _FailingRedis:
    async def xadd(self, stream: str, fields: dict, *, maxlen: int, approximate: bool) -> str:
        raise RuntimeError("redis unavailable")


class _FakeNatsJetStream:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float,
        stream: str,
        headers: dict,
    ) -> object:
        self.published.append(
            {
                "subject": subject,
                "payload": payload,
                "timeout": timeout,
                "stream": stream,
                "headers": headers,
            }
        )
        return object()


class _CaptureBrokerPublisher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, int, float | None]] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def enqueue_event(self, event, *, received_at_ms: int, volume: float | None = None) -> bool:
        self.enqueued.append((event.idempotency_key, received_at_ms, volume))
        return True

    def snapshot(self) -> dict:
        return {"count": len(self.enqueued)}


class ChartIngestShadowTests(unittest.TestCase):
    def test_normalize_bybit_message_to_shadow_event(self) -> None:
        observed = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(interval="D", confirm=True),
            default_symbol="BTCUSDT",
            source_seq=7,
            received_at_ms=1710000000200,
        )

        self.assertIsNotNone(observed)
        assert observed is not None
        self.assertEqual(observed.transport, "chart_ingest_shadow")
        self.assertEqual(observed.event.source, "chart_ingest_shadow")
        self.assertEqual(observed.event.exchange, "bybit")
        self.assertEqual(observed.event.tf, 1440)
        self.assertEqual(observed.event.event_type, "final")
        self.assertTrue(observed.event.is_final)
        self.assertEqual(observed.event.candle_key, "bybit:BTCUSDT:1440:1710000000000")
        self.assertEqual(
            observed.event.idempotency_key,
            "bybit:BTCUSDT:1440:1710000000000:final",
        )
        self.assertEqual(observed.volume, 12.3)

    def test_shadow_and_bot_http_match(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle=shadow_event.event.candle,
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000120,
        )
        assert bot_event is not None

        store.record_shadow_event(shadow_event)
        store.record_bot_http_event(bot_event)

        self.assertEqual(store.stats["shadow_events_total"], 1)
        self.assertEqual(store.stats["bot_http_events_seen_total"], 1)
        self.assertEqual(store.stats["compared_total"], 1)
        self.assertEqual(store.stats["match_total"], 1)
        self.assertEqual(store.stats["mismatch_total"], 0)
        self.assertEqual(store.stats["volume_missing_total"], 1)
        self.assertEqual(store.stats["reason_volume_missing_total"], 1)
        self.assertEqual(store.snapshot()["pending_missing_bot_http"], 0)

    def test_partial_ohlc_diff_is_drift_not_mismatch(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(close="100.5"),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                "start": 1710000000000,
                "end": 1710000899999,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.9,
                "confirm": False,
            },
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000120,
        )
        assert shadow_event is not None
        assert bot_event is not None

        store.record_bot_http_event(bot_event)
        store.record_shadow_event(shadow_event)

        self.assertEqual(store.stats["compared_total"], 1)
        self.assertEqual(store.stats["match_total"], 1)
        self.assertEqual(store.stats["mismatch_total"], 0)
        self.assertEqual(store.stats["final_mismatch_total"], 0)
        self.assertEqual(store.stats["partial_drift_total"], 1)
        self.assertEqual(store.stats["partial_ohlc_drift_total"], 1)
        self.assertEqual(store.stats["reason_partial_ohlc_drift_total"], 1)
        self.assertEqual(store.stats["reason_ohlc_diff_total"], 0)

    def test_final_ohlc_mismatch_is_counted(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(close="100.5", confirm=True),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                "start": 1710000000000,
                "end": 1710000899999,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.9,
                "confirm": True,
            },
            is_final=True,
            source="exchange_ws_final",
            received_at_ms=1710000000120,
        )
        assert shadow_event is not None
        assert bot_event is not None

        store.record_bot_http_event(bot_event)
        store.record_shadow_event(shadow_event)

        self.assertEqual(store.stats["compared_total"], 1)
        self.assertEqual(store.stats["match_total"], 0)
        self.assertEqual(store.stats["mismatch_total"], 1)
        self.assertEqual(store.stats["final_mismatch_total"], 1)
        self.assertEqual(store.stats["partial_drift_total"], 0)
        self.assertEqual(store.stats["reason_ohlc_diff_total"], 1)

    def test_missing_shadow_is_recorded(self) -> None:
        store = _store()
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                "start": 1710000000000,
                "end": 1710000899999,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "confirm": False,
            },
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000120,
        )
        assert bot_event is not None

        store.record_bot_http_event(bot_event)

        self.assertEqual(store.stats["missing_shadow_total"], 1)
        self.assertEqual(store.snapshot()["pending_missing_shadow"], 1)

    def test_missing_bot_http_final_is_recorded(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=True),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None

        store.record_shadow_event(shadow_event)

        self.assertEqual(store.stats["missing_bot_http_total"], 1)
        self.assertEqual(store.snapshot()["pending_missing_bot_http_final"], 1)

    def test_partial_volume_diff_is_drift_when_both_sides_have_volume(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(volume="12.3"),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                **shadow_event.event.candle,
                "volume": 13.0,
            },
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000120,
        )
        assert bot_event is not None

        store.record_shadow_event(shadow_event)
        store.record_bot_http_event(bot_event)

        self.assertEqual(store.stats["compared_total"], 1)
        self.assertEqual(store.stats["match_total"], 1)
        self.assertEqual(store.stats["mismatch_total"], 0)
        self.assertEqual(store.stats["volume_comparable_total"], 1)
        self.assertEqual(store.stats["volume_mismatch_total"], 0)
        self.assertEqual(store.stats["partial_drift_total"], 1)
        self.assertEqual(store.stats["partial_volume_drift_total"], 1)
        self.assertEqual(store.stats["reason_partial_volume_drift_total"], 1)
        self.assertEqual(store.stats["reason_volume_diff_total"], 0)

    def test_final_volume_mismatch_is_counted_when_both_sides_have_volume(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(volume="12.3", confirm=True),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                **shadow_event.event.candle,
                "volume": 13.0,
            },
            is_final=True,
            source="exchange_ws_final",
            received_at_ms=1710000000120,
        )
        assert bot_event is not None

        store.record_shadow_event(shadow_event)
        store.record_bot_http_event(bot_event)

        self.assertEqual(store.stats["compared_total"], 1)
        self.assertEqual(store.stats["mismatch_total"], 1)
        self.assertEqual(store.stats["final_mismatch_total"], 1)
        self.assertEqual(store.stats["volume_comparable_total"], 1)
        self.assertEqual(store.stats["volume_mismatch_total"], 1)
        self.assertEqual(store.stats["reason_volume_diff_total"], 1)

    def test_volume_missing_is_not_a_mismatch(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(volume="12.3"),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle=shadow_event.event.candle,
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000120,
        )
        assert bot_event is not None

        store.record_shadow_event(shadow_event)
        store.record_bot_http_event(bot_event)

        self.assertEqual(store.stats["mismatch_total"], 0)
        self.assertEqual(store.stats["volume_missing_total"], 1)
        self.assertEqual(store.stats["reason_volume_missing_total"], 1)

    def test_event_type_and_final_flag_diff_remain_critical(self) -> None:
        store = _store()
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=False),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                **shadow_event.event.candle,
                "confirm": True,
            },
            is_final=True,
            source="exchange_ws_final",
            received_at_ms=1710000000120,
        )
        assert bot_event is not None

        store._compare(bot_event, shadow_event)

        self.assertEqual(store.stats["compared_total"], 1)
        self.assertEqual(store.stats["mismatch_total"], 1)
        self.assertEqual(store.stats["final_mismatch_total"], 1)
        self.assertEqual(store.stats["reason_event_type_diff_total"], 1)
        self.assertEqual(store.stats["reason_final_flag_diff_total"], 1)

    def test_summary_log_includes_partial_drift_fields(self) -> None:
        logs: list[str] = []
        store = shadow.ChartIngestShadowCompareStore(
            max_events=100,
            price_tolerance=0.01,
            summary_interval_seconds=3600,
            logger=logs.append,
        )
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(close="100.5"),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle={
                "start": 1710000000000,
                "end": 1710000899999,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.9,
                "confirm": False,
            },
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000120,
        )
        assert shadow_event is not None
        assert bot_event is not None

        store.record_shadow_event(shadow_event)
        store.record_bot_http_event(bot_event)
        store.log_summary(reason="test")

        self.assertEqual(len(logs), 1)
        self.assertIn("mismatch_total=0", logs[0])
        self.assertIn("partial_drift_total=1", logs[0])
        self.assertIn("partial_ohlc_drift_total=1", logs[0])
        self.assertIn("partial_ohlc_drift:1", logs[0])

    def test_timing_lag_summary_is_counted(self) -> None:
        store = shadow.ChartIngestShadowCompareStore(
            max_events=100,
            price_tolerance=0.01,
            lag_warn_ms=50,
            summary_interval_seconds=3600,
            logger=None,
        )
        shadow_event = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert shadow_event is not None
        bot_event = shadow.build_bot_http_observed_event(
            symbol="BTCUSDT",
            tf=15,
            candle=shadow_event.event.candle,
            is_final=False,
            source="exchange_ws_partial",
            received_at_ms=1710000000200,
        )
        assert bot_event is not None

        store.record_shadow_event(shadow_event)
        store.record_bot_http_event(bot_event)

        snapshot = store.snapshot()
        self.assertEqual(snapshot["timing_lag_total"], 1)
        self.assertEqual(snapshot["reason_timing_lag_total"], 1)
        self.assertEqual(snapshot["lag_ms_min"], 100)
        self.assertEqual(snapshot["lag_ms_avg"], 100)
        self.assertEqual(snapshot["lag_ms_max"], 100)

    def test_duplicate_final_and_late_partial_after_final(self) -> None:
        store = _store()
        final_one = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=True),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        final_two = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=True),
            default_symbol="BTCUSDT",
            source_seq=2,
            received_at_ms=1710000000200,
        )
        partial_after_final = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=False),
            default_symbol="BTCUSDT",
            source_seq=3,
            received_at_ms=1710000000300,
        )
        assert final_one is not None
        assert final_two is not None
        assert partial_after_final is not None

        store.record_shadow_event(final_one)
        store.record_shadow_event(final_two)
        store.record_shadow_event(partial_after_final)

        self.assertEqual(store.stats["duplicate_final_total"], 1)
        self.assertEqual(store.stats["late_partial_after_final_total"], 1)

    def test_bounded_store_retention(self) -> None:
        store = _store(max_events=2)
        for index in range(3):
            bot_event = shadow.build_bot_http_observed_event(
                symbol="BTCUSDT",
                tf=15,
                candle={
                    "start": 1710000000000 + index * 900_000,
                    "end": 1710000899999 + index * 900_000,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "confirm": False,
                },
                is_final=False,
                source="exchange_ws_partial",
                received_at_ms=1710000000120 + index,
            )
            assert bot_event is not None
            store.record_bot_http_event(bot_event)

        snapshot = store.snapshot()
        self.assertEqual(snapshot["bot_store_size"], 2)
        self.assertEqual(snapshot["pending_missing_shadow"], 2)

    def test_disabled_capture_is_noop_without_store(self) -> None:
        previous = shadow._compare_store
        shadow._compare_store = None
        try:
            shadow.capture_bot_http_live_update(
                symbol="BTCUSDT",
                tf=15,
                candle={
                    "start": 1710000000000,
                    "end": 1710000899999,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "confirm": False,
                },
                is_final=False,
                source="exchange_ws_partial",
            )
        finally:
            shadow._compare_store = previous

    def test_all_tf_topics_use_bybit_interval_tokens(self) -> None:
        service = shadow.BybitChartIngestShadowService(
            symbol="BTCUSDT",
            intervals=["15", "30", "60", "240", "1440"],
            ws_url="wss://example.test",
            compare_store=_store(),
            logger=None,
        )

        self.assertEqual(
            service.topics,
            [
                "kline.15.BTCUSDT",
                "kline.30.BTCUSDT",
                "kline.60.BTCUSDT",
                "kline.240.BTCUSDT",
                "kline.D.BTCUSDT",
            ],
        )

    def test_disabled_start_from_env_is_noop(self) -> None:
        async def run() -> None:
            await shadow.shutdown_chart_ingest_shadow()
            with patch.dict("os.environ", {"CHART_INGEST_SHADOW_ENABLED": "false"}, clear=False):
                service = await shadow.start_chart_ingest_shadow_from_env()
                self.assertIsNone(service)
                self.assertIsNone(shadow.get_chart_ingest_shadow_compare_store())
            await shadow.shutdown_chart_ingest_shadow()

        asyncio.run(run())

    def test_shadow_enabled_broker_disabled_starts_compare_only(self) -> None:
        async def run() -> None:
            await shadow.shutdown_chart_ingest_shadow()

            async def fake_start(service) -> None:
                service._task = None

            env = {
                "CHART_INGEST_SHADOW_ENABLED": "true",
                "CHART_BROKER_PUBLISH_SHADOW_ENABLED": "false",
            }
            with patch.dict("os.environ", env, clear=False), patch.object(
                shadow.BybitChartIngestShadowService,
                "start",
                fake_start,
            ), patch.object(shadow, "log", lambda *_args, **_kwargs: None):
                service = await shadow.start_chart_ingest_shadow_from_env()
                self.assertIsNotNone(service)
                self.assertIsNotNone(shadow.get_chart_ingest_shadow_compare_store())
                self.assertIsNone(shadow.get_chart_broker_shadow_publisher())

            await shadow.shutdown_chart_ingest_shadow()

        asyncio.run(run())

    def test_service_start_stop_smoke_without_network(self) -> None:
        async def run() -> None:
            entered = asyncio.Event()

            async def fake_connect_once() -> None:
                entered.set()
                await asyncio.Event().wait()

            service = shadow.BybitChartIngestShadowService(
                symbol="BTCUSDT",
                intervals=["15"],
                ws_url="wss://example.test",
                compare_store=_store(),
                logger=None,
            )
            service._connect_once = fake_connect_once
            await service.start()
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.wait_for(service.close(), timeout=1)
            self.assertIsNone(service._task)

        asyncio.run(run())

    def test_broker_stream_name_uses_bybit_daily_token(self) -> None:
        observed = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(interval="D", confirm=True),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        assert observed is not None

        self.assertEqual(
            broker_shadow.stream_name_for_event(observed.event, prefix="candles.bybit"),
            "candles.bybit.BTCUSDT.D",
        )

    def test_nats_subject_name_splits_partial_and_critical(self) -> None:
        partial = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(interval="15", confirm=False),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        final = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(interval="D", confirm=True),
            default_symbol="BTCUSDT",
            source_seq=2,
            received_at_ms=1710000000200,
        )
        assert partial is not None
        assert final is not None

        self.assertEqual(
            broker_shadow.nats_subject_for_event(partial.event),
            "candles.partial.bybit.BTCUSDT.15",
        )
        self.assertEqual(
            broker_shadow.nats_subject_for_event(final.event),
            "candles.critical.bybit.BTCUSDT.D",
        )

    def test_broker_payload_schema_contains_shadow_contract_fields(self) -> None:
        observed = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=True, volume="12.3"),
            default_symbol="BTCUSDT",
            source_seq=11,
            received_at_ms=1710000000200,
        )
        assert observed is not None

        payload = broker_shadow.build_broker_shadow_payload(
            observed.event,
            received_at_ms=observed.received_at_ms,
            volume=observed.volume,
            publish_ts_ms=1710000000300,
        )
        fields = broker_shadow.build_redis_stream_fields(payload)
        decoded = json.loads(fields["payload"])

        self.assertEqual(payload["schema_version"], "chart_candle.v1")
        self.assertEqual(payload["source"], "chart_ingest_shadow")
        self.assertEqual(payload["exchange"], "bybit")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["tf"], 15)
        self.assertEqual(payload["event_type"], "final")
        self.assertTrue(payload["is_final"])
        self.assertEqual(payload["volume"], 12.3)
        self.assertEqual(payload["candle_key"], "bybit:BTCUSDT:15:1710000000000")
        self.assertEqual(payload["idempotency_key"], "bybit:BTCUSDT:15:1710000000000:final")
        self.assertEqual(payload["received_at_ms"], 1710000000200)
        self.assertEqual(payload["publish_ts_ms"], 1710000000300)
        self.assertEqual(fields["source"], "chart_ingest_shadow")
        self.assertEqual(fields["is_final"], "1")
        self.assertEqual(decoded["candle"]["volume"], 12.3)

    def test_broker_publish_disabled_from_env_is_noop(self) -> None:
        with patch.dict("os.environ", {"CHART_BROKER_PUBLISH_SHADOW_ENABLED": "false"}, clear=False):
            self.assertIsNone(broker_shadow.build_chart_broker_shadow_publisher_from_env(logger=None))

    def test_nats_broker_publish_success_with_fake_jetstream(self) -> None:
        async def run() -> None:
            nats_js = _FakeNatsJetStream()
            publisher = broker_shadow.NatsJetStreamChartEventPublisher(
                nats_js=nats_js,
                summary_interval_seconds=3600,
                drain_timeout_seconds=0.1,
                logger=None,
            )
            observed = shadow.build_shadow_observed_event_from_bybit_message(
                _raw_kline(volume="12.3"),
                default_symbol="BTCUSDT",
                source_seq=1,
                received_at_ms=1710000000100,
            )
            assert observed is not None

            await publisher._publish_item(
                broker_shadow.BrokerPublishItem(
                    event=observed.event,
                    received_at_ms=observed.received_at_ms,
                    volume=observed.volume,
                )
            )

            self.assertEqual(nats_js.published[0]["subject"], "candles.partial.bybit.BTCUSDT.15")
            self.assertEqual(nats_js.published[0]["stream"], "CHART_PARTIAL")
            self.assertIn("Nats-Msg-Id", nats_js.published[0]["headers"])
            payload = json.loads(nats_js.published[0]["payload"].decode("utf-8"))
            self.assertEqual(payload["schema_version"], "chart_candle.v1")
            self.assertEqual(payload["source"], "chart_ingest_shadow")
            self.assertEqual(publisher.stats["broker_publish_success_total"], 1)

        asyncio.run(run())

    def test_nats_broker_build_from_env(self) -> None:
        env = {
            "CHART_BROKER_PUBLISH_SHADOW_ENABLED": "true",
            "CHART_BROKER_KIND": "nats_jetstream",
            "NATS_URL": "nats://nats:4222",
        }
        with patch.dict("os.environ", env, clear=False):
            publisher = broker_shadow.build_chart_broker_shadow_publisher_from_env(logger=None)
        self.assertIsInstance(publisher, broker_shadow.NatsJetStreamChartEventPublisher)

    def test_nats_shadow_payload_is_compare_only_observed_event(self) -> None:
        observed = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(confirm=True, volume="12.3"),
            default_symbol="BTCUSDT",
            source_seq=11,
            received_at_ms=1710000000200,
        )
        assert observed is not None
        payload = broker_shadow.build_broker_shadow_payload(
            observed.event,
            received_at_ms=observed.received_at_ms,
            volume=observed.volume,
            publish_ts_ms=1710000000300,
        )

        nats_observed = nats_shadow.build_nats_shadow_observed_event_from_payload(
            payload,
            received_at_ms=1710000000400,
        )

        self.assertIsNotNone(nats_observed)
        assert nats_observed is not None
        self.assertEqual(nats_observed.transport, "nats_jetstream_shadow")
        self.assertEqual(nats_observed.event.source, "nats_jetstream_shadow")
        self.assertEqual(nats_observed.event.event_type, "final")
        self.assertTrue(nats_observed.event.is_final)
        self.assertEqual(nats_observed.volume, 12.3)

    def test_nats_shadow_payload_rejects_schema_mismatch(self) -> None:
        self.assertIsNone(
            nats_shadow.build_nats_shadow_observed_event_from_payload(
                {"schema_version": "unknown", "event_type": "partial"},
                received_at_ms=1710000000400,
            )
        )

    def test_shadow_service_enqueues_broker_publish_after_normalize(self) -> None:
        publisher = _CaptureBrokerPublisher()
        service = shadow.BybitChartIngestShadowService(
            symbol="BTCUSDT",
            intervals=["15"],
            ws_url="wss://example.test",
            compare_store=_store(),
            broker_publisher=publisher,
            logger=None,
        )

        service._handle_message(json.dumps(_raw_kline(volume="12.3")))

        self.assertEqual(len(publisher.enqueued), 1)
        self.assertEqual(publisher.enqueued[0][0], "bybit:BTCUSDT:15:1710000000000:partial")
        self.assertEqual(publisher.enqueued[0][2], 12.3)

    def test_redis_broker_publish_success_with_fake_client(self) -> None:
        async def run() -> None:
            redis = _FakeRedis()
            publisher = broker_shadow.RedisStreamChartEventPublisher(
                redis_client=redis,
                summary_interval_seconds=3600,
                drain_timeout_seconds=0.1,
                logger=None,
            )
            observed = shadow.build_shadow_observed_event_from_bybit_message(
                _raw_kline(volume="12.3"),
                default_symbol="BTCUSDT",
                source_seq=1,
                received_at_ms=1710000000100,
            )
            assert observed is not None

            await publisher.start()
            self.assertTrue(
                publisher.enqueue_event(
                    observed.event,
                    received_at_ms=observed.received_at_ms,
                    volume=observed.volume,
                )
            )
            await _wait_for(lambda: publisher.stats["broker_publish_success_total"] == 1)
            await publisher.close()

            self.assertEqual(redis.messages[0][0], "candles.bybit.BTCUSDT.15")
            self.assertEqual(publisher.stats["broker_publish_attempt_total"], 1)
            self.assertEqual(publisher.stats["broker_publish_failure_total"], 0)
            self.assertEqual(publisher.snapshot()["broker_publish_queue_depth"], 0)

        asyncio.run(run())

    def test_redis_broker_unavailable_records_failure_without_raising(self) -> None:
        async def run() -> None:
            publisher = broker_shadow.RedisStreamChartEventPublisher(
                redis_client=_FailingRedis(),
                final_retry_attempts=2,
                retry_backoff_seconds=0,
                summary_interval_seconds=3600,
                drain_timeout_seconds=0.1,
                logger=None,
            )
            observed = shadow.build_shadow_observed_event_from_bybit_message(
                _raw_kline(confirm=True),
                default_symbol="BTCUSDT",
                source_seq=1,
                received_at_ms=1710000000100,
            )
            assert observed is not None

            await publisher.start()
            self.assertTrue(
                publisher.enqueue_event(
                    observed.event,
                    received_at_ms=observed.received_at_ms,
                    volume=observed.volume,
                )
            )
            await _wait_for(lambda: publisher.stats["broker_publish_final_failure_total"] == 1)
            await publisher.close()

            self.assertEqual(publisher.stats["broker_publish_attempt_total"], 2)
            self.assertEqual(publisher.stats["broker_publish_failure_total"], 2)
            self.assertIn("redis unavailable", publisher.stats["last_broker_error"])

        asyncio.run(run())

    def test_partial_broker_queue_full_drops_partial_only(self) -> None:
        publisher = broker_shadow.RedisStreamChartEventPublisher(logger=None)
        publisher._partial_queue = asyncio.Queue(maxsize=1)
        publisher._critical_queue = asyncio.Queue(maxsize=1)
        first = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(),
            default_symbol="BTCUSDT",
            source_seq=1,
            received_at_ms=1710000000100,
        )
        second = shadow.build_shadow_observed_event_from_bybit_message(
            _raw_kline(close="100.6"),
            default_symbol="BTCUSDT",
            source_seq=2,
            received_at_ms=1710000000200,
        )
        assert first is not None
        assert second is not None

        self.assertTrue(
            publisher.enqueue_event(
                first.event,
                received_at_ms=first.received_at_ms,
                volume=first.volume,
            )
        )
        self.assertFalse(
            publisher.enqueue_event(
                second.event,
                received_at_ms=second.received_at_ms,
                volume=second.volume,
            )
        )
        self.assertEqual(publisher.stats["broker_publish_partial_dropped_total"], 1)
        self.assertEqual(publisher.stats["broker_publish_final_failure_total"], 0)


if __name__ == "__main__":
    unittest.main()
