import asyncio
import os
from typing import List

from core.utils.log_utils import log


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def _label_env_key(label: str) -> str:
    s = str(label or "default").strip().upper()
    return "".join(ch if ch.isalnum() else "_" for ch in s)


def _task_summary(task: asyncio.Task, stack_limit: int = 6) -> str:
    try:
        coro = task.get_coro()
        coro_name = getattr(coro, "__qualname__", None) or getattr(coro, "__name__", None) or type(coro).__name__
    except Exception:
        coro_name = "unknown"

    where = "-"
    try:
        stack = task.get_stack(limit=max(1, int(stack_limit)))
        if stack:
            frame = stack[-1]
            where = f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}({frame.f_code.co_name})"
    except Exception:
        where = "-"

    name = ""
    try:
        name = task.get_name()
    except Exception:
        name = ""

    return f"name={name or '-'} coro={coro_name} at={where}"


def _collect_task_summaries(loop: asyncio.AbstractEventLoop, self_task: asyncio.Task | None, max_tasks: int, stack_limit: int) -> List[str]:
    tasks = []
    try:
        for t in asyncio.all_tasks(loop):
            if t is self_task:
                continue
            if t.done():
                continue
            tasks.append(t)
    except Exception:
        return []

    out = []
    for t in tasks[: max(1, int(max_tasks))]:
        out.append(_task_summary(t, stack_limit=stack_limit))
    return out


# 이벤트루프 지연(stall) 감지와 원인 추적용 로그 기록
# - 기본 1초 주기 체크
# - 기본 2초 이상 지연 시 경고 로그
# - 태스크 스냅샷(이름/코루틴/현재 프레임) 출력
async def start_event_loop_lag_watchdog(label: str = "default"):
    global_enabled = _env_bool("EVENT_LOOP_WATCHDOG_ENABLED", True)
    label_key = _label_env_key(label)
    enabled = _env_bool(f"EVENT_LOOP_WATCHDOG_{label_key}_ENABLED", global_enabled)
    if not enabled:
        log(f"ℹ️ [LoopWatchdog:{label}] disabled")
        return

    interval = max(0.1, _env_float("EVENT_LOOP_WATCHDOG_INTERVAL_SEC", 1.0))
    threshold = max(0.1, _env_float("EVENT_LOOP_WATCHDOG_LAG_THRESHOLD_SEC", 2.0))
    cooldown = max(0.0, _env_float("EVENT_LOOP_WATCHDOG_LOG_COOLDOWN_SEC", 20.0))
    max_tasks = max(1, _env_int("EVENT_LOOP_WATCHDOG_MAX_TASKS", 8))
    stack_limit = max(1, _env_int("EVENT_LOOP_WATCHDOG_STACK_LIMIT", 6))

    loop = asyncio.get_running_loop()
    self_task = asyncio.current_task()
    next_tick = loop.time() + interval
    last_logged_at = 0.0

    log(
        f"🛰️ [LoopWatchdog:{label}] start "
        f"(interval={interval:.2f}s, threshold={threshold:.2f}s, cooldown={cooldown:.2f}s)"
    )

    try:
        while True:
            sleep_for = max(0.0, next_tick - loop.time())
            await asyncio.sleep(sleep_for)

            now = loop.time()
            lag = now - next_tick
            next_tick += interval

            # 지연이 길게 발생한 경우 누적 오차를 현재 시점 기준으로 재정렬
            if lag > (interval * 4):
                next_tick = now + interval

            if lag < threshold:
                continue
            if cooldown > 0 and (now - last_logged_at) < cooldown:
                continue
            last_logged_at = now

            summaries = _collect_task_summaries(loop, self_task, max_tasks=max_tasks, stack_limit=stack_limit)
            log(
                f"⚠️ [LoopWatchdog:{label}] lag detected: "
                f"{lag:.3f}s (threshold={threshold:.3f}s, active_tasks~{len(summaries)})"
            )
            for idx, line in enumerate(summaries, start=1):
                log(f"   [LoopWatchdog:{label}] task#{idx} {line}")
    except asyncio.CancelledError:
        log(f"🛑 [LoopWatchdog:{label}] stopped")
        raise
    except Exception as e:
        log(f"❌ [LoopWatchdog:{label}] crashed: {e}")
        raise
