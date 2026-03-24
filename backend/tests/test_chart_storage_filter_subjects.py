from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.services import chart_nats_final_durable as final_durable


SUBJECT_15 = "candles.critical.bybit.BTCUSDT.15"
SUBJECT_30 = "candles.critical.bybit.BTCUSDT.30"
SUBJECT_60 = "candles.critical.bybit.BTCUSDT.60"
SUBJECT_240 = "candles.critical.bybit.BTCUSDT.240"


class _NoopStore:
    def ensure_schema(self) -> None:
        return None


class _FakeJetStream:
    def __init__(self, *, info: object | None = None) -> None:
        self.info = info
        self.added: list[object] = []

    async def consumer_info(self, stream: str, consumer: str) -> object:
        if self.info is None:
            from nats.js.errors import NotFoundError  # type: ignore

            raise NotFoundError()
        return self.info

    async def add_consumer(self, stream: str, *, config: object) -> None:
        self.added.append(config)


class ChartStorageFilterSubjectsTests(unittest.TestCase):
    def test_single_filter_backward_compatibility(self) -> None:
        cfg = final_durable.build_chart_storage_filter_config(
            filter_subject=SUBJECT_15,
            default_filter_subject="candles.critical.*.*.*",
        )

        self.assertEqual(cfg.mode, "single")
        self.assertEqual(cfg.filter_subject, SUBJECT_15)
        self.assertEqual(cfg.filter_subjects, ())
        self.assertEqual(cfg.consumer_config_filter_kwargs(), {"filter_subject": SUBJECT_15})

    def test_multi_filter_parses_comma_separated_exact_subjects(self) -> None:
        cfg = final_durable.build_chart_storage_filter_config(
            filter_subjects=f" {SUBJECT_15}, {SUBJECT_30},{SUBJECT_60},{SUBJECT_240} ",
            filter_subject="candles.critical.bybit.BTCUSDT.*",
        )

        self.assertEqual(cfg.mode, "multi")
        self.assertEqual(cfg.filter_subjects, (SUBJECT_15, SUBJECT_30, SUBJECT_60, SUBJECT_240))
        self.assertEqual(
            cfg.consumer_config_filter_kwargs(),
            {"filter_subjects": [SUBJECT_15, SUBJECT_30, SUBJECT_60, SUBJECT_240]},
        )

    def test_multi_filter_rejects_wildcard_subject(self) -> None:
        with self.assertRaises(final_durable.DurableFilterConfigError):
            final_durable.build_chart_storage_filter_config(
                filter_subjects="candles.critical.bybit.BTCUSDT.*",
                filter_subject=SUBJECT_15,
            )

    def test_multi_filter_rejects_duplicate_subject(self) -> None:
        with self.assertRaises(final_durable.DurableFilterConfigError):
            final_durable.build_chart_storage_filter_config(
                filter_subjects=f"{SUBJECT_15},{SUBJECT_15}",
                filter_subject=SUBJECT_15,
            )

    def test_multi_filter_rejects_blank_subject(self) -> None:
        with self.assertRaises(final_durable.DurableFilterConfigError):
            final_durable.build_chart_storage_filter_config(
                filter_subjects=f"{SUBJECT_15}, ,{SUBJECT_30}",
                filter_subject=SUBJECT_15,
            )

    def test_multi_filter_rejects_non_critical_subject_shape(self) -> None:
        with self.assertRaises(final_durable.DurableFilterConfigError):
            final_durable.build_chart_storage_filter_config(
                filter_subjects="candles.partial.bybit.BTCUSDT.15",
                filter_subject=SUBJECT_15,
            )

    def test_env_multi_filter_takes_precedence_over_single_filter(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CHART_STORAGE_FILTER_SUBJECTS": f"{SUBJECT_15},{SUBJECT_30}",
                "CHART_STORAGE_FILTER_SUBJECT": "candles.critical.bybit.BTCUSDT.*",
                "NATS_FINAL_DURABLE_FILTER_SUBJECT": "candles.critical.*.*.*",
            },
            clear=True,
        ), patch.object(final_durable, "_default_db_cfg", return_value={}):
            consumer = final_durable.build_consumer_from_env(logger=None)

        self.assertEqual(consumer._filter_config().mode, "multi")
        self.assertEqual(consumer.filter_subjects, (SUBJECT_15, SUBJECT_30))
        self.assertEqual(consumer._subscription_subject(), SUBJECT_15)

    def test_add_consumer_uses_filter_subject_for_single_mode(self) -> None:
        async def run() -> None:
            fake_js = _FakeJetStream()
            consumer = final_durable.NatsJetStreamFinalDurableConsumer(
                store=_NoopStore(),
                filter_subject=SUBJECT_15,
                logger=None,
            )
            consumer._js = fake_js

            await consumer._ensure_consumer()

            self.assertEqual(len(fake_js.added), 1)
            config = fake_js.added[0]
            self.assertEqual(config.filter_subject, SUBJECT_15)
            self.assertIsNone(config.filter_subjects)

        asyncio.run(run())

    def test_add_consumer_uses_filter_subjects_for_multi_mode(self) -> None:
        async def run() -> None:
            fake_js = _FakeJetStream()
            consumer = final_durable.NatsJetStreamFinalDurableConsumer(
                store=_NoopStore(),
                filter_subject=SUBJECT_15,
                filter_subjects=(SUBJECT_15, SUBJECT_30, SUBJECT_60, SUBJECT_240),
                logger=None,
            )
            consumer._js = fake_js

            await consumer._ensure_consumer()

            self.assertEqual(len(fake_js.added), 1)
            config = fake_js.added[0]
            self.assertIsNone(config.filter_subject)
            self.assertEqual(config.filter_subjects, [SUBJECT_15, SUBJECT_30, SUBJECT_60, SUBJECT_240])

        asyncio.run(run())

    def test_existing_consumer_matching_single_filter_is_ok(self) -> None:
        async def run() -> None:
            fake_js = _FakeJetStream(
                info=SimpleNamespace(config=SimpleNamespace(filter_subject=SUBJECT_15))
            )
            consumer = final_durable.NatsJetStreamFinalDurableConsumer(
                store=_NoopStore(),
                filter_subject=SUBJECT_15,
                logger=None,
            )
            consumer._js = fake_js

            await consumer._ensure_consumer()

            self.assertEqual(fake_js.added, [])

        asyncio.run(run())

    def test_existing_consumer_matching_multi_filter_is_ok(self) -> None:
        async def run() -> None:
            fake_js = _FakeJetStream(
                info=SimpleNamespace(
                    config=SimpleNamespace(filter_subjects=[SUBJECT_30, SUBJECT_15])
                )
            )
            consumer = final_durable.NatsJetStreamFinalDurableConsumer(
                store=_NoopStore(),
                filter_subjects=(SUBJECT_15, SUBJECT_30),
                logger=None,
            )
            consumer._js = fake_js

            await consumer._ensure_consumer()

            self.assertEqual(fake_js.added, [])

        asyncio.run(run())

    def test_existing_consumer_filter_mismatch_fails_without_recreate(self) -> None:
        async def run() -> None:
            fake_js = _FakeJetStream(
                info=SimpleNamespace(config=SimpleNamespace(filter_subject=SUBJECT_15))
            )
            consumer = final_durable.NatsJetStreamFinalDurableConsumer(
                store=_NoopStore(),
                filter_subjects=(SUBJECT_15, SUBJECT_30),
                logger=None,
            )
            consumer._js = fake_js

            with self.assertRaises(final_durable.DurableConsumerConfigMismatchError):
                await consumer._ensure_consumer()

            self.assertEqual(fake_js.added, [])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
