from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
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

from core.persistence import candles_repo


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, *_args, **_kwargs) -> None:
        return None

    def fetchall(self) -> list[dict]:
        return list(self.rows)

    def fetchone(self) -> dict | None:
        return self.rows[0] if self.rows else None


class _FakeConnection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.rows)


def _existing_row(*, source: str = "chart_nats_final", volume=123.45) -> dict:
    return {
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": volume,
        "turnover": None,
        "source": source,
    }


def _rest_verify_kwargs(**overrides) -> dict:
    base = {
        "symbol": "BTCUSDT",
        "interval_min": 15,
        "start_ms": 1710000000000,
        "open_": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": None,
        "turnover": None,
        "source": "bybit_rest_fix",
    }
    base.update(overrides)
    return base


class CandlesSourceGuardrailTests(unittest.TestCase):
    def test_same_ohlc_preserves_chart_storage_source(self) -> None:
        with patch.object(
            candles_repo,
            "_fetch_candle_for_key",
            return_value=_existing_row(source="chart_nats_final"),
        ):
            with patch.object(candles_repo, "upsert_candle") as upsert:
                result = candles_repo.upsert_candle_from_rest_verify(**_rest_verify_kwargs())

        self.assertEqual(result.action, "skip_source_overwrite")
        self.assertEqual(result.existing_source, "chart_nats_final")
        self.assertEqual(result.changed_fields, ())
        upsert.assert_not_called()

    def test_missing_row_inserts_bybit_rest_fix(self) -> None:
        with patch.object(candles_repo, "_fetch_candle_for_key", return_value=None):
            with patch.object(candles_repo, "upsert_candle") as upsert:
                result = candles_repo.upsert_candle_from_rest_verify(**_rest_verify_kwargs())

        self.assertEqual(result.action, "insert_bybit_rest_fix")
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["source"], "bybit_rest_fix")

    def test_ohlc_mismatch_repairs_and_marks_source(self) -> None:
        with patch.object(
            candles_repo,
            "_fetch_candle_for_key",
            return_value=_existing_row(source="chart_nats_final"),
        ):
            with patch.object(candles_repo, "upsert_candle") as upsert:
                result = candles_repo.upsert_candle_from_rest_verify(
                    **_rest_verify_kwargs(close=106.0)
                )

        self.assertEqual(result.action, "repair_source_bybit_rest_fix")
        self.assertEqual(result.existing_source, "chart_nats_final")
        self.assertEqual(result.changed_fields, ("close",))
        upsert.assert_called_once()

    def test_volume_is_compared_when_rest_verify_provides_volume(self) -> None:
        with patch.object(
            candles_repo,
            "_fetch_candle_for_key",
            return_value=_existing_row(source="chart_nats_final", volume=123.45),
        ):
            with patch.object(candles_repo, "upsert_candle") as upsert:
                result = candles_repo.upsert_candle_from_rest_verify(
                    **_rest_verify_kwargs(volume=124.45)
                )

        self.assertEqual(result.action, "repair_source_bybit_rest_fix")
        self.assertEqual(result.changed_fields, ("volume",))
        upsert.assert_called_once()

    def test_fetch_candles_for_chart_returns_volume(self) -> None:
        rows = [
            {
                "start_time": datetime(2024, 3, 9, 16, 0, 0),
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": "12.3",
            }
        ]
        with patch.object(candles_repo, "_conn", return_value=_FakeConnection(rows)):
            candles = candles_repo.fetch_candles_for_chart(
                symbol="BTCUSDT",
                interval_min=15,
                limit=1,
            )

        self.assertEqual(candles[0]["volume"], 12.3)

    def test_fetch_latest_candle_for_chart_returns_db_volume(self) -> None:
        rows = [
            {
                "start_time": datetime(2024, 3, 9, 16, 0, 0),
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": "12.3",
            }
        ]
        with patch.object(candles_repo, "_conn", return_value=_FakeConnection(rows)):
            candle = candles_repo.fetch_latest_candle_for_chart(
                symbol="BTCUSDT",
                interval_min=15,
            )

        assert candle is not None
        self.assertEqual(candle["volume"], 12.3)


if __name__ == "__main__":
    unittest.main()
