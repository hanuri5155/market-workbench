from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

SyncHook = Callable[[], None]
AsyncHook = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class StrategyRuntime:
    provider: str
    display_name: str
    register_hooks: list[SyncHook] = field(default_factory=list)
    background_tasks: list[AsyncHook] = field(default_factory=list)
    notes: str = ""
