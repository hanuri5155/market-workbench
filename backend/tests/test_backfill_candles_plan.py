from __future__ import annotations

import io
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "market_workbench_test")
os.environ.setdefault("DB_USER", "market_workbench_test")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("SYMBOL", "BTCUSDT")

from core.tools import backfill_candles


class BackfillCandlesPlanTests(unittest.TestCase):
    def test_interval_map_includes_one_minute_without_changing_existing_values(self) -> None:
        self.assertEqual(backfill_candles.INTERVALS[1], "1")
        self.assertEqual(backfill_candles.INTERVALS[15], "15")
        self.assertEqual(backfill_candles.INTERVALS[1440], "D")
        self.assertNotIn(1, backfill_candles.DEFAULT_BACKFILL_INTERVALS)

    def test_one_minute_plan_calculates_one_day_window(self) -> None:
        plan = backfill_candles.build_backfill_plan(
            symbol="BTCUSDT",
            interval_min=1,
            start_dt=datetime(2026, 5, 1, tzinfo=timezone.utc),
            end_dt=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )

        self.assertEqual(plan["bybit_interval"], "1")
        self.assertEqual(plan["window_minutes"], 1440)
        self.assertEqual(plan["expected_bars"], 1440)
        self.assertEqual(plan["expected_pages"], 2)
        self.assertEqual(plan["estimated_requests"], 2)
        self.assertEqual(plan["source"], "bybit_rest_1m_backfill")
        self.assertIs(plan["dry_run_or_plan"], True)
        self.assertIs(plan["db_write"], False)
        self.assertIn("15m aggregate sanity", plan["validation_checks_to_run"])

    def test_one_minute_missing_date_window_fails_fast(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                backfill_candles.main(["--interval", "1"])

        self.assertEqual(raised.exception.code, 2)
        message = stderr.getvalue()
        self.assertIn("1m backfill requires explicit --start and --end", message)
        self.assertIn("Use --plan first", message)

    def test_one_minute_defaults_to_plan_without_execute(self) -> None:
        stdout = io.StringIO()
        with patch.object(backfill_candles, "_insert_candles") as insert:
            with patch.object(backfill_candles, "fetch_klines_page") as fetch:
                with patch("sys.stdout", stdout):
                    code = backfill_candles.main(
                        [
                            "--symbol",
                            "BTCUSDT",
                            "--interval",
                            "1",
                            "--start",
                            "2026-05-01T00:00:00",
                            "--end",
                            "2026-05-02T00:00:00",
                            "--json",
                        ]
                    )

        self.assertEqual(code, 0)
        insert.assert_not_called()
        fetch.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["interval_min"], 1)
        self.assertEqual(payload["expected_bars"], 1440)
        self.assertIs(payload["db_write"], False)


if __name__ == "__main__":
    unittest.main()
