from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ChartActiveSourceCleanupTest(unittest.TestCase):
    def test_default_active_source_is_chart_ingest_active(self) -> None:
        from app.api.services import chart_active_source

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                chart_active_source.chart_event_active_source(),
                chart_active_source.CHART_INGEST_ACTIVE_SOURCE,
            )

        with patch.dict(os.environ, {"CHART_EVENT_ACTIVE_SOURCE": ""}, clear=True):
            self.assertEqual(
                chart_active_source.chart_event_active_source(),
                chart_active_source.CHART_INGEST_ACTIVE_SOURCE,
            )

    def test_only_chart_ingest_active_is_allowed(self) -> None:
        from app.api.services import chart_active_source

        self.assertEqual(
            chart_active_source.VALID_CHART_EVENT_ACTIVE_SOURCES,
            {chart_active_source.CHART_INGEST_ACTIVE_SOURCE},
        )

        with patch.dict(
            os.environ,
            {"CHART_EVENT_ACTIVE_SOURCE": chart_active_source.CHART_INGEST_ACTIVE_SOURCE},
            clear=True,
        ):
            self.assertEqual(
                chart_active_source.chart_event_active_source(),
                chart_active_source.CHART_INGEST_ACTIVE_SOURCE,
            )
            self.assertTrue(
                chart_active_source.is_chart_event_source_active(
                    chart_active_source.CHART_INGEST_ACTIVE_SOURCE
                )
            )

    def test_retired_bot_http_source_fails_fast(self) -> None:
        from app.api.services import chart_active_source

        with patch.dict(os.environ, {"CHART_EVENT_ACTIVE_SOURCE": "bot_http"}, clear=True):
            with self.assertRaises(chart_active_source.ChartActiveSourceConfigError) as ctx:
                chart_active_source.chart_event_active_source()

        self.assertIn("bot_http is retired", str(ctx.exception))
        self.assertIn("chart_ingest_active", str(ctx.exception))

    def test_unknown_source_fails_fast(self) -> None:
        from app.api.services import chart_active_source

        with patch.dict(os.environ, {"CHART_EVENT_ACTIVE_SOURCE": "unknown"}, clear=True):
            with self.assertRaises(chart_active_source.ChartActiveSourceConfigError) as ctx:
                chart_active_source.chart_event_active_source()

        self.assertIn("invalid CHART_EVENT_ACTIVE_SOURCE", str(ctx.exception))
        self.assertIn("chart_ingest_active", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
