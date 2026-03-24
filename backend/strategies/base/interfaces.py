from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

SyncHook = Callable[[], None]
AsyncHook = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class StrategyRuntime:
    """공개 레포에서는 실제 전략 대신 runtime 연결 형태만 보여준다."""

    provider: str
    display_name: str
    register_hooks: list[SyncHook] = field(default_factory=list)
    background_tasks: list[AsyncHook] = field(default_factory=list)
    notes: str = ""
