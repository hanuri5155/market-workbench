from __future__ import annotations

import os

from core.utils.log_utils import log
from strategies.base.interfaces import StrategyRuntime
from strategies.demo_zone.strategy import build_demo_zone_runtime

_RUNTIME_FACTORIES = {
    "demo_zone": build_demo_zone_runtime,
}


def load_strategy_runtime(provider: str | None = None) -> StrategyRuntime:
    selected = str(provider or os.getenv("STRATEGY_PROVIDER", "demo_zone")).strip() or "demo_zone"
    factory = _RUNTIME_FACTORIES.get(selected)

    if factory is None:
        log(f"⚠️ [StrategyLoader] unknown provider='{selected}', fallback='demo_zone'")
        selected = "demo_zone"
        factory = _RUNTIME_FACTORIES[selected]

    runtime = factory()
    log(f"🧩 [StrategyLoader] provider={runtime.provider} display_name={runtime.display_name}")
    return runtime
