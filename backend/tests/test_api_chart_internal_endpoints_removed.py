from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "market_workbench_test")
os.environ.setdefault("DB_USER", "market_workbench_test")
os.environ.setdefault("DB_PASSWORD", "")
os.environ.setdefault("SYMBOL", "BTCUSDT")


class ApiChartInternalEndpointCleanupTests(unittest.TestCase):
    def test_bot_http_live_candle_endpoints_are_not_registered(self) -> None:
        from app import api_app

        route_paths = {getattr(route, "path", "") for route in api_app.app.routes}

        self.assertNotIn("/internal/candle-live-update", route_paths)
        self.assertNotIn("/internal/candle-live-reconcile", route_paths)
        self.assertIn("/internal/candle-rest-confirmed", route_paths)

    def test_removed_endpoints_are_not_in_otp_allowlist(self) -> None:
        from app import api_app
        from app.auth.otp.middleware import OTPAuthMiddleware

        middleware = next(
            item for item in api_app.app.user_middleware if item.cls is OTPAuthMiddleware
        )
        allow_paths = set(middleware.kwargs.get("allow_paths") or [])

        self.assertNotIn("/internal/candle-live-update", allow_paths)
        self.assertNotIn("/internal/candle-live-reconcile", allow_paths)
        self.assertIn("/internal/candle-rest-confirmed", allow_paths)


if __name__ == "__main__":
    unittest.main()
