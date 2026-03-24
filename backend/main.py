## backend/main.py

# 봇 프로세스 진입점
#
# 큰 흐름만 빠르게 파악하기 위함
# 1) 설정과 전략 runtime 준비
# 2) 이번 실행을 sessions 테이블에 기록
# 3) execution_data_store 복구와 포인터 정리
# 4) 실시간 태스크와 WebSocket 시작
# 5) 종료 시 태스크와 sessions 종료 시각 정리

import os, asyncio, signal, contextlib
from core.state import shared_state
from core.state.state_snapshot import start_state_snapshot_writer
from core.tools.simulated_price_feeder import simulated_price_loop
from core.ws.price_dispatcher import start_price_dispatcher
from core.ws.position_watcher import start_execution_ws
from core.ws.candle_detector import launch_candle_detectors
from core.ws.strategy_flag_push_listener import start_strategy_flag_push_listener
from core.utils.log_utils import log
from core.config.config_utils import start_config_watcher
from core.persistence.sessions_repo import start_session, end_session
from core.trading.funding_utils import start_funding_snapshot_poller
from core.config.config_utils import refresh_strategy_flags_cache_from_db
from core.operations.heartbeat import start_bot_heartbeat
from core.operations.event_loop_watchdog import start_event_loop_lag_watchdog
from strategies.base.loader import load_strategy_runtime
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

_tasks = None

# 실행 캐시 파일에는 포지션 dict와 meta 묶음만 남기기 위함
def _sanitize_execution_store_before_file_save(where: str, store: dict):
    bad = []
    for k, v in list(store.items()):
        if k == "meta":
            if not isinstance(v, dict):
                bad.append((k, type(v).__name__))
                store["meta"] = {}
            continue
        if not isinstance(v, dict):
            bad.append((k, type(v).__name__))
            store.pop(k, None)
    if bad:
        log(f"🧯 [INIT:{where}] non-dict top-level removed → " + ", ".join(f"{k}({t})" for k, t in bad))


# 취소 가능한 태스크를 안전하게 멈추기 위한 공통 처리
async def _stop_task(task):
    if not task:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

# simulation_mode 값에 맞는 가격 공급 루프를 하나만 유지
async def mode_switcher_loop():
    task = None
    last_mode = None

    try:
        while True:
            simulation_mode = shared_state.current_config.get("simulation_mode", False)

            if simulation_mode != last_mode:
                if task:
                    await _stop_task(task)
                    log("ℹ️ 이전 모드 작업이 정상적으로 취소되었습니다.")

                if simulation_mode:
                    log("🔁 [Mode Switch] 시뮬레이션 모드 활성화")
                    task = asyncio.create_task(simulated_price_loop())
                else:
                    log("🔁 [Mode Switch] 실매매 모드 활성화")
                    task = asyncio.create_task(start_price_dispatcher())

                last_mode = simulation_mode

            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        # 종료 신호가 오면 현재 모드용 가격 공급 루프도 같이 멈추기 위함
        if task:
            await _stop_task(task)
        raise

# SIGINT / SIGTERM 수신 시 메인 gather 태스크부터 취소
def _graceful_shutdown():
    log("🔚 [Main] 종료 신호 감지 — 작업 취소 중…")
    global _tasks
    try:
        if _tasks and not _tasks.done():
            _tasks.cancel()
    except Exception:
        pass


# 프로세스 1회 실행을 sessions 테이블의 한 행으로 기록
def _start_process_session():
    shared_state.session_id = start_session(
        account_id=getattr(shared_state, "account_id", 1),
        mode="live" if not shared_state.current_config.get("simulation_mode", False) else "simulation",
        config_snapshot=shared_state.current_config,
    )


# 현재 이벤트 루프에 종료 신호 핸들러 연결
def _attach_signal_handlers(loop: asyncio.AbstractEventLoop):
    try:
        loop.add_signal_handler(signal.SIGINT, _graceful_shutdown)
        loop.add_signal_handler(signal.SIGTERM, _graceful_shutdown)
    except NotImplementedError:
        # 일부 환경에서는 add_signal_handler를 지원하지 않음
        pass


# 봇 시작 시점의 전략 on/off 상태와 runtime 메모 출력
def _log_startup_status(strategy_runtime):
    strategy_flags = refresh_strategy_flags_cache_from_db()

    all_on = bool(strategy_flags.get("enable_trading", False))
    zone_on = all_on and bool(strategy_flags.get("enable_zone_strategy", False))

    status_all = "🟢 전체 주문" if all_on else "🔴 전체 주문"
    status_zone = "🟢 Structure Zone" if zone_on else "🔴 Structure Zone"

    log(f"{status_all} | {status_zone}")
    if strategy_runtime.notes:
        log(f"ℹ️ [StrategyLoader] {strategy_runtime.notes}")


# 마지막 청산 fill 시각을 우선 사용하고 없으면 현재 시각으로 채움
def _resolve_exit_time_from_fills(position: dict) -> str:
    fills = position.get("position_fills") or {}
    if not fills:
        return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    last_fill = max(
        fills.values(),
        key=lambda fill_row: fill_row.get("fill_time", ""),
        default=None,
    )
    if last_fill and last_fill.get("fill_time"):
        return last_fill["fill_time"]
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


# position_fills에 기록된 청산 수량 합계를 계산
def _sum_closed_qty(position: dict) -> float:
    closed_qty = 0.0
    for fill_row in (position.get("position_fills") or {}).values():
        try:
            qty = float(fill_row.get("qty", 0.0) or 0.0)
        except Exception:
            qty = 0.0
        if qty > 0:
            closed_qty += qty
    return closed_qty


# 재시작 직후 fills 기준으로 이미 끝난 포지션 상태를 execution_data_store에 반영
def _reconcile_execution_store_from_fills():
    cfg = getattr(shared_state, "current_config", {}) or {}
    if not bool(cfg.get("reconcile_closed_from_fills_on_start", True)):
        return

    store = shared_state.execution_data_store
    fixed_count = 0
    dust_qty_threshold = 5e-4  # 0.001 스텝의 절반

    for position_key, position in list(store.items()):
        if position_key == "meta" or not isinstance(position, dict):
            continue

        entry_size = float(position.get("entry_size") or 0.0)
        closed_qty = _sum_closed_qty(position)

        # fills 기준으로 이미 끝난 포지션이면 현재 수량, 종료 여부, 종료 시각을 함께 맞춤
        if (entry_size - closed_qty) > dust_qty_threshold:
            continue

        changed = False
        current_size = float(position.get("current_size", 0.0) or 0.0)
        if current_size != 0.0:
            position["current_size"] = 0.0
            changed = True

        if not position.get("closed"):
            position["closed"] = True
            changed = True

        position.setdefault("last_exit_reason", "init_reconciled_zero")

        if not position.get("exit_time"):
            position["exit_time"] = _resolve_exit_time_from_fills(position)
            changed = True

        if changed:
            fixed_count += 1

    # meta는 실행 캐시 안에서 현재 활성 포지션을 가리키는 보조 인덱스 묶음
    # last_active_order가 이미 닫힌 포지션을 가리키면 이후 체결 해석이 꼬일 수 있어 비움
    meta_state = store.get("meta") or {}
    active_order_key = meta_state.get("last_active_order")
    if active_order_key and isinstance(store.get(active_order_key), dict) and store[active_order_key].get("closed"):
        meta_state["last_active_order"] = None
        store["meta"] = meta_state

    # current_position_link_id는 런타임이 "지금 열린 포지션"으로 보는 포인터
    # 재시작 직후 닫힌 주문 키를 들고 있으면 WebSocket 체결 해석이 어긋나므로 함께 비움
    current_position_key = getattr(shared_state, "current_position_link_id", None)
    if current_position_key and isinstance(store.get(current_position_key), dict) and store[current_position_key].get("closed"):
        shared_state.current_position_link_id = None

    _sanitize_execution_store_before_file_save("reconcile", store)
    shared_state.save_execution_data_store(store)
    log(f"🧽 [INIT] Reconciled from fills → fixed={fixed_count}")


# 전략별 hook을 연결하고 shared_state 스냅샷 기록기를 시작
def _register_strategy_runtime(strategy_runtime):
    for hook in strategy_runtime.register_hooks:
        hook()

    # /tmp/shared_state.json은 현재 런타임 상태를 외부에서 빠르게 읽기 위한 스냅샷 파일
    start_state_snapshot_writer("/tmp/shared_state.json", 0.25)


# 봇이 켜져 있는 동안 계속 돌아가야 하는 백그라운드 태스크 묶음
def _build_runtime_tasks(strategy_runtime):
    funding_poll_sec = int(os.getenv("FUNDING_POLL_SEC", "60"))
    return asyncio.gather(
        start_event_loop_lag_watchdog("bot"),
        start_bot_heartbeat(),
        mode_switcher_loop(),
        start_execution_ws(),
        start_funding_snapshot_poller(funding_poll_sec),
        start_strategy_flag_push_listener(),
        *[task() for task in strategy_runtime.background_tasks],
    )


# 남은 태스크, async generator, 세션 기록까지 순서대로 정리
def _shutdown_runtime(loop: asyncio.AbstractEventLoop):
    global _tasks

    try:
        if _tasks and not _tasks.done():
            _tasks.cancel()
            with contextlib.suppress(Exception):
                loop.run_until_complete(_tasks)
    except Exception:
        pass

    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            with contextlib.suppress(Exception):
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass

    try:
        with contextlib.suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass

    try:
        with contextlib.suppress(Exception):
            loop.run_until_complete(asyncio.sleep(0))
    except Exception:
        pass

    try:
        if getattr(shared_state, "session_id", None):
            end_session(shared_state.session_id)
            log(f"✅ [Main] 세션 종료 기록 완료 (session_id={shared_state.session_id})")
    except Exception as e:
        log(f"⚠️ [Main] 세션 종료 기록 중 예외: {e}")

    try:
        loop.stop()
    except Exception:
        pass
    try:
        loop.close()
    except Exception:
        pass


# 봇 프로세스 시작부터 종료 정리까지 담당
def main():
    global _tasks

    # 1) 실행 설정과 전략 runtime 준비
    strategy_runtime = load_strategy_runtime()
    start_config_watcher()

    if not shared_state.current_config:
        raise RuntimeError("❌ config.json 로드 실패: current_config가 비어 있습니다.")

    # 2) 이번 프로세스 실행을 sessions 테이블에 기록
    _start_process_session()

    # 3) 메인 이벤트 루프 준비
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    shared_state.main_event_loop = loop
    _attach_signal_handlers(loop)
    _log_startup_status(strategy_runtime)

    # 4) 재시작 직후 복구 상태 정리
    try:
        _reconcile_execution_store_from_fills()
    except Exception as e:
        log(f"⚠️ [INIT] reconcile-from-fills 실패: {e}")

    # 5) 전략 runtime과 관찰용 상태 스냅샷 시작
    _register_strategy_runtime(strategy_runtime)

    try:
        # 6) 캔들 감지기와 상시 백그라운드 태스크 시작
        # candle detector 초기 기동 1회, 이후 재시작은 config watcher 담당
        loop.run_until_complete(launch_candle_detectors())
        _tasks = _build_runtime_tasks(strategy_runtime)

        try:
            loop.run_until_complete(_tasks)
        except asyncio.CancelledError:
            log("🛑 [Main] 작업이 취소되었습니다(Cancelled). 정상 종료 처리로 이동합니다.")
    finally:
        _shutdown_runtime(loop)


if __name__ == "__main__":
    main()
