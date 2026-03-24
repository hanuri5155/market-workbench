from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class PublicCandidateObservation:
    """공개 연구 예제에서 쓰는 정리된 후보 row 형태.

    `candidate_id`는 summary 전 중복 제거를 위해서만 받는다. 반환값에는
    포함하지 않는다. 공개 output은 cohort 단위의 흐름을 설명해야 하며,
    raw 연구 row나 실전 전략 후보를 노출하면 안 된다.
    """

    candidate_id: str
    cohort: str
    diagnostic_bucket: str
    source_status: str
    label_status: str
    feature_cutoff_ms: int
    label_observed_after_ms: int
    quality_flags: tuple[str, ...] = field(default_factory=tuple)


def build_public_validation_summary(
    rows: Iterable[PublicCandidateObservation],
    *,
    min_group_size: int = 5,
) -> dict:
    """공개 문서용 aggregate-only 연구 summary를 만든다.

    비공개 workflow는 더 많은 source provider와 target metric을 사용한다.
    공개 helper는 일부러 좁은 책임만 가진다.

    - feature/label 시간 순서를 확인한 row만 summary에 반영한다.
    - 작은 group은 승격하지 않고 caveat로 남긴다.
    - candidate id, 가격, timestamp, raw row를 반환하지 않는다.
    - production rule이나 score model이 아니라는 경계를 output에 남긴다.
    """

    if min_group_size < 1:
        raise ValueError("min_group_size must be at least 1")

    seen_ids: set[str] = set()
    accepted_rows: list[PublicCandidateObservation] = []
    duplicate_rows = 0
    leakage_rejected_rows = 0

    for row in rows:
        if row.candidate_id in seen_ids:
            duplicate_rows += 1
            continue
        seen_ids.add(row.candidate_id)

        # label은 feature cutoff 이후에 관측되어야 한다. 순서가 뒤집힌 row를
        # 넣으면 미래 정보가 섞여 연구 결과가 실제보다 좋아 보일 수 있다.
        if row.label_observed_after_ms < row.feature_cutoff_ms:
            leakage_rejected_rows += 1
            continue

        accepted_rows.append(row)

    cohort_counts: Counter[str] = Counter()
    bucket_counts: Counter[tuple[str, str]] = Counter()
    source_status_counts: Counter[str] = Counter()
    label_status_counts: Counter[str] = Counter()
    quality_flag_counts: Counter[str] = Counter()

    for row in accepted_rows:
        cohort_counts[row.cohort] += 1
        bucket_counts[(row.cohort, row.diagnostic_bucket)] += 1
        source_status_counts[row.source_status] += 1
        label_status_counts[row.label_status] += 1
        quality_flag_counts.update(row.quality_flags)

    cohorts = {
        cohort: {
            "candidate_rows": count,
            "sample_gate": _sample_gate(count, min_group_size),
            "policy": "diagnostic_only" if count >= min_group_size else "caveat_only",
        }
        for cohort, count in sorted(cohort_counts.items())
    }

    diagnostic_buckets: dict[str, dict[str, dict[str, int | str]]] = defaultdict(dict)
    for (cohort, bucket), count in sorted(bucket_counts.items()):
        diagnostic_buckets[cohort][bucket] = {
            "candidate_rows": count,
            "sample_gate": _sample_gate(count, min_group_size),
            "policy": "diagnostic_only" if count >= min_group_size else "caveat_only",
        }

    return {
        "summary_version": "public_research_validation_summary_v1",
        "row_level_output": False,
        "production_rule": False,
        "score_threshold": False,
        "auto_entry_rule": False,
        "input_rows": len(seen_ids) + duplicate_rows,
        "accepted_rows": len(accepted_rows),
        "duplicate_rows": duplicate_rows,
        "leakage_rejected_rows": leakage_rejected_rows,
        "cohorts": cohorts,
        "diagnostic_buckets": dict(diagnostic_buckets),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "label_status_counts": dict(sorted(label_status_counts.items())),
        "quality_flag_counts": dict(sorted(quality_flag_counts.items())),
        "public_boundary": {
            "raw_rows_exported": False,
            "target_values_exported": False,
            "candidate_ids_exported": False,
            "strategy_conditions_exported": False,
        },
    }


def _sample_gate(count: int, min_group_size: int) -> str:
    if count >= min_group_size:
        return "sample_supported"
    if count > 0:
        return "sample_caveated"
    return "sample_empty"
