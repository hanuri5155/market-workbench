from __future__ import annotations

import os
from collections.abc import Callable


CHART_EVENT_ACTIVE_SOURCE_ENV = "CHART_EVENT_ACTIVE_SOURCE"
CHART_INGEST_ACTIVE_SOURCE = "chart_ingest_active"
RETIRED_BOT_HTTP_SOURCE = "bot_http"
VALID_CHART_EVENT_ACTIVE_SOURCES = {CHART_INGEST_ACTIVE_SOURCE}


class ChartActiveSourceConfigError(ValueError):
    """Raised when CHART_EVENT_ACTIVE_SOURCE is not a supported runtime source."""


def chart_event_active_source(
    getenv: Callable[[str, str | None], str | None] | None = None,
) -> str:
    env_getter = getenv or os.getenv
    raw = str(
        env_getter(CHART_EVENT_ACTIVE_SOURCE_ENV, CHART_INGEST_ACTIVE_SOURCE)
        or CHART_INGEST_ACTIVE_SOURCE
    ).strip()
    if raw in VALID_CHART_EVENT_ACTIVE_SOURCES:
        return raw
    if raw == RETIRED_BOT_HTTP_SOURCE:
        raise ChartActiveSourceConfigError(
            f"{CHART_EVENT_ACTIVE_SOURCE_ENV}=bot_http is retired; "
            f"use {CHART_INGEST_ACTIVE_SOURCE}"
        )
    valid_values = ", ".join(sorted(VALID_CHART_EVENT_ACTIVE_SOURCES))
    raise ChartActiveSourceConfigError(
        f"invalid {CHART_EVENT_ACTIVE_SOURCE_ENV}: {raw!r}; valid values: {valid_values}"
    )


def is_chart_event_source_active(source: str) -> bool:
    return chart_event_active_source() == str(source or "").strip()
