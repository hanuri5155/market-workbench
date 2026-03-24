from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ChartEventsHttpPublisherRemovedTest(unittest.TestCase):
    def test_http_publisher_symbols_are_removed(self) -> None:
        from core.ws import chart_events

        removed_symbols = (
            "InternalHttpChartEventPublisher",
            "AsyncHttpChartEventPublisher",
            "ChartEventPublisher",
            "ChartEventPublishResult",
        )

        for symbol in removed_symbols:
            with self.subTest(symbol=symbol):
                self.assertFalse(hasattr(chart_events, symbol))

    def test_shared_live_candle_event_contract_is_preserved(self) -> None:
        from core.ws.chart_events import LiveCandleEvent

        event = LiveCandleEvent(
            event_type="partial",
            exchange="BYBIT",
            symbol="btcusdt",
            tf=15,
            candle={
                "start": "1779732000000",
                "end": 1779732899999,
                "open": "100.1",
                "high": "101.2",
                "low": "99.9",
                "close": "100.5",
                "volume": "12.3",
                "confirm": False,
            },
            is_final=False,
            source="chart_ingest_active",
            source_seq=7,
        )

        self.assertEqual(event.exchange, "bybit")
        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.tf, 15)
        self.assertEqual(event.candle["start"], 1779732000000)
        self.assertEqual(event.candle["open"], 100.1)
        self.assertEqual(event.candle["volume"], 12.3)
        self.assertFalse(event.candle["confirm"])
        self.assertEqual(event.candle_key, "bybit:BTCUSDT:15:1779732000000")
        self.assertEqual(event.idempotency_key, "bybit:BTCUSDT:15:1779732000000:partial")

    def test_payload_normalization_and_builders_are_preserved(self) -> None:
        from core.ws.chart_events import (
            build_live_candle_event,
            build_reconcile_candle_event,
            normalize_chart_candle_payload,
        )

        candle = {
            "start": 1779732000000,
            "end": 1779732899999,
            "open": "100.1",
            "high": "101.2",
            "low": "99.9",
            "close": "100.5",
            "volume": "12.3",
            "confirm": False,
        }

        normalized = normalize_chart_candle_payload(candle, is_final=True)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertTrue(normalized["confirm"])
        self.assertEqual(normalized["volume"], 12.3)

        live_event = build_live_candle_event(
            symbol="BTCUSDT",
            tf=15,
            candle=candle,
            is_final=False,
            source="chart_ingest_active",
        )
        self.assertEqual(live_event.event_type, "partial")
        self.assertFalse(live_event.is_final)

        reconcile_event = build_reconcile_candle_event(
            symbol="BTCUSDT",
            tf=15,
            candle=candle,
            reason="unit_test",
            source="chart_ingest_active",
        )
        self.assertEqual(reconcile_event.event_type, "reconcile")
        self.assertTrue(reconcile_event.is_final)
        self.assertEqual(reconcile_event.reason, "unit_test")


if __name__ == "__main__":
    unittest.main()
