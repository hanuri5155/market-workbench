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

from core.research.public_validation_summary import (
    PublicCandidateObservation,
    build_public_validation_summary,
)


class PublicValidationSummaryTests(unittest.TestCase):
    def test_summary_is_aggregate_only_and_keeps_candidate_ids_private(self) -> None:
        rows = [
            PublicCandidateObservation(
                candidate_id=f"candidate-{idx}",
                cohort="q4_public_example",
                diagnostic_bucket="high_potential_high_risk",
                source_status="source_supported",
                label_status="labeled",
                feature_cutoff_ms=1000,
                label_observed_after_ms=2000,
            )
            for idx in range(5)
        ]

        summary = build_public_validation_summary(rows, min_group_size=5)

        self.assertEqual(summary["accepted_rows"], 5)
        self.assertFalse(summary["row_level_output"])
        self.assertFalse(summary["production_rule"])
        self.assertFalse(summary["public_boundary"]["candidate_ids_exported"])
        self.assertNotIn("candidate-0", repr(summary))
        self.assertEqual(
            summary["cohorts"]["q4_public_example"]["sample_gate"],
            "sample_supported",
        )

    def test_rejects_rows_that_observe_label_before_feature_cutoff(self) -> None:
        rows = [
            PublicCandidateObservation(
                candidate_id="safe-row",
                cohort="ma_regime_public_example",
                diagnostic_bucket="aligned",
                source_status="source_supported",
                label_status="labeled",
                feature_cutoff_ms=1000,
                label_observed_after_ms=1500,
            ),
            PublicCandidateObservation(
                candidate_id="leaky-row",
                cohort="ma_regime_public_example",
                diagnostic_bucket="aligned",
                source_status="source_supported",
                label_status="labeled",
                feature_cutoff_ms=2000,
                label_observed_after_ms=1500,
            ),
        ]

        summary = build_public_validation_summary(rows, min_group_size=2)

        self.assertEqual(summary["accepted_rows"], 1)
        self.assertEqual(summary["leakage_rejected_rows"], 1)
        self.assertEqual(
            summary["cohorts"]["ma_regime_public_example"]["policy"],
            "caveat_only",
        )

    def test_counts_quality_flags_without_promoting_small_groups(self) -> None:
        rows = [
            PublicCandidateObservation(
                candidate_id="small-1",
                cohort="short_compression_public_example",
                diagnostic_bucket="fast_breakdown",
                source_status="source_supported",
                label_status="labeled",
                feature_cutoff_ms=1000,
                label_observed_after_ms=2000,
                quality_flags=("sample_small", "external_source_missing"),
            )
        ]

        summary = build_public_validation_summary(rows, min_group_size=5)

        self.assertEqual(summary["quality_flag_counts"]["sample_small"], 1)
        self.assertEqual(summary["quality_flag_counts"]["external_source_missing"], 1)
        self.assertEqual(
            summary["diagnostic_buckets"]["short_compression_public_example"][
                "fast_breakdown"
            ]["policy"],
            "caveat_only",
        )

    def test_duplicate_candidate_rows_are_not_counted_twice(self) -> None:
        row = PublicCandidateObservation(
            candidate_id="duplicate",
            cohort="q4_public_example",
            diagnostic_bucket="unclassified",
            source_status="source_supported",
            label_status="labeled",
            feature_cutoff_ms=1000,
            label_observed_after_ms=2000,
        )

        summary = build_public_validation_summary([row, row], min_group_size=1)

        self.assertEqual(summary["accepted_rows"], 1)
        self.assertEqual(summary["duplicate_rows"], 1)
        self.assertEqual(summary["input_rows"], 2)


if __name__ == "__main__":
    unittest.main()
