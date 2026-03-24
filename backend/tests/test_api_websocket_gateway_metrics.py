from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "market_workbench_test")
os.environ.setdefault("DB_USER", "market_workbench_test")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("SYMBOL", "BTCUSDT")

from app.api.ws import chart_candles  # noqa: E402


def _candle(start: int = 1710000000000) -> dict[str, object]:
    return {
        "start": start,
        "end": start + 899999,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "confirm": False,
    }


class _FakeWebSocket:
    def __init__(
        self,
        *,
        delay_sec: float = 0.0,
        fail_send: bool = False,
        send_exception: Exception | None = None,
    ) -> None:
        self.delay_sec = delay_sec
        self.fail_send = fail_send
        self.send_exception = send_exception or RuntimeError("send failed")
        self.accepted = False
        self.closed = False
        self.sent: list[dict[str, object]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, object]) -> None:
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        if self.fail_send and payload.get("type") != "candle_subscription_ack":
            raise self.send_exception
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


class ApiWebSocketGatewayMetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        chart_candles.reset_chart_websocket_metrics_for_tests()

    def tearDown(self) -> None:
        chart_candles.reset_chart_websocket_metrics_for_tests()

    def test_subscription_and_broadcast_metrics_update(self) -> None:
        async def run() -> None:
            ws = _FakeWebSocket(delay_sec=0.26)
            await chart_candles.register_chart_candle_client(ws)  # type: ignore[arg-type]
            await chart_candles.handle_chart_candle_client_message(
                ws, json.dumps({"type": "subscribe", "symbol": "BTCUSDT", "tf": "15"})
            )

            before = chart_candles.get_chart_websocket_metrics_snapshot()
            self.assertEqual(before["active_connections"], 1)
            self.assertEqual(before["subscribed_connections"], 1)
            self.assertEqual(before["subscriptions_by_tf"]["15"], 1)

            await chart_candles.upsert_chart_candle_live_event(
                symbol="BTCUSDT",
                tf=15,
                candle=_candle(),
                is_final=False,
                source="chart_ingest_active",
                source_seq=1,
            )

            after = chart_candles.get_chart_websocket_metrics_snapshot()
            self.assertEqual(after["broadcast_total"], 1)
            self.assertEqual(after["broadcast_last_matched_clients"], 1)
            self.assertEqual(after["send_total"], 1)
            self.assertEqual(after["send_failure_total"], 0)
            self.assertEqual(after["active_send_failure_total"], 0)
            self.assertEqual(after["closing_send_failure_total"], 0)
            self.assertEqual(after["broadcast_send_failure_total"], 0)
            self.assertEqual(after["teardown_close_race_total"], 0)
            self.assertEqual(after["slow_send_250ms_total"], 1)
            self.assertGreaterEqual(after["send_duration_p95_ms"], 250)
            self.assertGreater(after["last_success_epoch"], 0)

        asyncio.run(run())

    def test_send_failure_metrics_and_cleanup_update(self) -> None:
        async def run() -> None:
            ws = _FakeWebSocket(fail_send=True)
            await chart_candles.register_chart_candle_client(ws)  # type: ignore[arg-type]
            await chart_candles.handle_chart_candle_client_message(
                ws, json.dumps({"type": "subscribe", "symbol": "BTCUSDT", "tf": "15"})
            )

            await chart_candles.upsert_chart_candle_live_event(
                symbol="BTCUSDT",
                tf=15,
                candle=_candle(),
                is_final=False,
                source="chart_ingest_active",
                source_seq=1,
            )

            snapshot = chart_candles.get_chart_websocket_metrics_snapshot()
            self.assertEqual(snapshot["send_failure_total"], 1)
            self.assertEqual(snapshot["active_send_failure_total"], 1)
            self.assertEqual(snapshot["closing_send_failure_total"], 0)
            self.assertEqual(snapshot["broadcast_send_failure_total"], 1)
            self.assertEqual(snapshot["teardown_close_race_total"], 0)
            self.assertEqual(snapshot["server_close_total"], 1)
            self.assertEqual(snapshot["broadcast_error_total"], 1)
            self.assertEqual(snapshot["disconnect_cleanup_total"], 1)
            self.assertEqual(snapshot["active_connections"], 0)
            self.assertTrue(ws.closed)

        asyncio.run(run())

    def test_close_like_send_failure_is_classified_as_closing_race(self) -> None:
        async def run() -> None:
            ws = _FakeWebSocket(
                fail_send=True,
                send_exception=RuntimeError("cannot call send once close has started"),
            )
            await chart_candles.register_chart_candle_client(ws)  # type: ignore[arg-type]
            await chart_candles.handle_chart_candle_client_message(
                ws, json.dumps({"type": "subscribe", "symbol": "BTCUSDT", "tf": "15"})
            )

            await chart_candles.upsert_chart_candle_live_event(
                symbol="BTCUSDT",
                tf=15,
                candle=_candle(),
                is_final=False,
                source="chart_ingest_active",
                source_seq=1,
            )

            snapshot = chart_candles.get_chart_websocket_metrics_snapshot()
            self.assertEqual(snapshot["send_failure_total"], 1)
            self.assertEqual(snapshot["active_send_failure_total"], 0)
            self.assertEqual(snapshot["closing_send_failure_total"], 1)
            self.assertEqual(snapshot["broadcast_send_failure_total"], 1)
            self.assertEqual(snapshot["teardown_close_race_total"], 1)
            self.assertEqual(snapshot["last_send_failure_phase"], "closing")
            self.assertIn("RuntimeError", snapshot["last_send_failure_reason_code"])

        asyncio.run(run())

    def test_client_disconnect_marks_state_and_skips_closing_send(self) -> None:
        async def run() -> None:
            ws = _FakeWebSocket()
            await chart_candles.register_chart_candle_client(ws)  # type: ignore[arg-type]
            await chart_candles.handle_chart_candle_client_message(
                ws, json.dumps({"type": "subscribe", "symbol": "BTCUSDT", "tf": "15"})
            )

            chart_candles.record_chart_candle_client_receive_closed(
                ws, RuntimeError("client disconnected")
            )

            await chart_candles.upsert_chart_candle_live_event(
                symbol="BTCUSDT",
                tf=15,
                candle=_candle(),
                is_final=False,
                source="chart_ingest_active",
                source_seq=1,
            )

            snapshot = chart_candles.get_chart_websocket_metrics_snapshot()
            self.assertEqual(snapshot["client_disconnect_total"], 1)
            self.assertEqual(snapshot["send_skipped_closing_total"], 1)
            self.assertEqual(snapshot["send_failure_total"], 0)
            self.assertEqual(snapshot["disconnect_cleanup_total"], 1)
            self.assertEqual(snapshot["active_connections"], 0)

        asyncio.run(run())

    def test_metrics_endpoint_is_local_only_and_registered(self) -> None:
        from app import api_app

        paths = {getattr(route, "path", "") for route in api_app.app.routes}
        self.assertIn("/internal/chart-websocket-metrics", paths)

        allowed_request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
        proxied_external_request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-real-ip": "203.0.113.10"},
        )
        denied_request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={})

        self.assertTrue(api_app._is_local_metrics_request(allowed_request))  # type: ignore[arg-type]
        self.assertFalse(api_app._is_local_metrics_request(proxied_external_request))  # type: ignore[arg-type]
        self.assertFalse(api_app._is_local_metrics_request(denied_request))  # type: ignore[arg-type]

        payload = asyncio.run(api_app.internal_chart_websocket_metrics(allowed_request))  # type: ignore[arg-type]
        self.assertTrue(payload["ok"])
        self.assertIn("active_connections", payload)
        self.assertIn("broadcast_duration_p95_ms", payload)
        self.assertIn("send_duration_p99_ms", payload)
        self.assertIn("active_send_failure_total", payload)
        self.assertIn("closing_send_failure_total", payload)
        self.assertIn("client_disconnect_total", payload)
        self.assertIn("server_close_total", payload)
        self.assertIn("broadcast_send_failure_total", payload)
        self.assertIn("teardown_close_race_total", payload)
        self.assertIn("send_skipped_closing_total", payload)
        self.assertIn("last_send_failure_reason_code", payload)
        self.assertIn("last_send_failure_phase", payload)
        self.assertIn("last_close_reason_code", payload)


if __name__ == "__main__":
    unittest.main()
