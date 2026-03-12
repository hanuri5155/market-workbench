## backend/core/config/config_utils.py

# config.json 변경 감시와 전략 on/off 캐시를 담당

import os, json, time, hashlib
from core.state import shared_state
from core.utils.log_utils import log
from app.db import SessionLocal, models
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv
load_dotenv()

_last_file_hash = ""

CONFIG_PATH = os.getenv("CONFIG_PATH")

_current_config = {}

# strategy_flags 테이블에서 현재 주문 on/off 상태 조회
#
# enable_trading은 전체 주문 차단 스위치
# enable_zone_strategy는 Structure Zone 전략 스위치
def get_strategy_flags_from_db() -> dict[str, bool]:
    keys = ["enable_trading", "enable_zone_strategy"]
    result: dict[str, bool] = {k: False for k in keys}

    try:
        db = SessionLocal()
        rows = (
            db.query(models.StrategyFlag)
            .filter(models.StrategyFlag.key.in_(keys))
            .all()
        )
        for row in rows:
            if row.bool_value is not None:
                result[row.key] = bool(row.bool_value)
    except Exception as e:
        log(f"⚠️ [StrategyFlag] strategy_flags 조회 실패(DB 전략 플래그): {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass

    return result

# DB 값을 shared_state 캐시에 반영하고 최신 스냅샷 반환
def refresh_strategy_flags_cache_from_db() -> dict[str, bool]:
    flags = get_strategy_flags_from_db()
    shared_state.strategy_flags = flags
    shared_state.strategy_flags_updated_at = time.time()
    return flags

# DB 기준 전략 on/off 상태를 사람이 읽기 쉬운 로그로 출력
def log_strategy_flags_from_db(prefix: str = "") -> None:
    flags = get_strategy_flags_from_db()

    all_on = bool(flags.get("enable_trading", False))
    zone_on = all_on and bool(flags.get("enable_zone_strategy", False))

    status_all = "🟢 전체 주문" if all_on else "🔴 전체 주문"
    status_zone = "🟢 Structure Zone" if zone_on else "🔴 Structure Zone"

    if prefix:
        log(f"{prefix} {status_all} | {status_zone}")
    else:
        log(f"{status_all} | {status_zone}")


def get_file_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()

# config.json에서 실제로 바뀐 항목만 추려 로그 출력
def compare_and_log_changes(old_config: dict, new_config: dict):
    changed_items = []

    for key, new_value in new_config.items():
        old_value = old_config.get(key)
        if old_value != new_value:
            changed_items.append((key, old_value, new_value))

    if not changed_items:
        return

    strategy_keys = {"enable_trading", "enable_zone_strategy"}
    only_strategy_changes = all(key in strategy_keys for key, _, _ in changed_items)

    if only_strategy_changes:
        # 전략 on/off는 실제 운영 기준인 DB 값 재조회 후 로그 기록
        flags = get_strategy_flags_from_db()

        all_on = bool(flags.get("enable_trading", False))
        zone_on = all_on and bool(flags.get("enable_zone_strategy", False))

        status_all = '🟢 전체 주문' if all_on else '🔴 전체 주문'
        status_zone = '🟢 Structure Zone' if zone_on else '🔴 Structure Zone'

        log(f"{status_all} | {status_zone}")

    else:
        for key, old, new in changed_items:
            log(f"🔄 설정 변경: {key} → {old} → {new}")

class ConfigEventHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith("config.json"):
            # 저장 직후 빈 파일 노출 가능성 대응용 짧은 재읽기
            for attempt in range(2):
                try:
                    with open(CONFIG_PATH, "r") as f:
                        content = f.read()

                    if content.strip():
                        global _last_file_hash

                        content_hash = get_file_hash(content)
                        if content_hash == _last_file_hash:
                            return

                        _last_file_hash = content_hash
                        cfg = json.loads(content)
                        break

                    if attempt == 0:
                        log("⚠️ config.json이 비어있어 100ms 후 재시도합니다...")
                        time.sleep(0.1)

                except Exception as e:
                    log(f"❌ [설정] config.json 로딩 중 오류: {e}")
                    return

            else:
                log("❌ config.json이 두 번 시도 후에도 비어있어 무시합니다.")
                return

            global _current_config
            compare_and_log_changes(_current_config, cfg)
            _current_config = cfg
            shared_state.current_config = _current_config

# 프로세스 시작 시 config.json을 먼저 읽고 이후 변경도 계속 반영
def start_config_watcher():
    global _current_config
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"config.json 경로가 올바르지 않습니다: {CONFIG_PATH}")

    try:
        with open(CONFIG_PATH, "r") as f:
            _current_config = json.load(f)
    except Exception as e:
        log(f"❌ [설정] 초기 config.json 로딩 실패: {e}")
        _current_config = {}

    shared_state.current_config = _current_config

    observer = Observer()
    handler = ConfigEventHandler()
    observer.schedule(handler, path="config", recursive=False)
    observer.start()

# shared_state에 캐시된 전략 플래그 기준으로 주문 허용 여부 반환
def is_trading_enabled(strategy: str = None) -> bool:
    flags = getattr(shared_state, "strategy_flags", None) or {}

    if not bool(flags.get("enable_trading", False)):
        return False

    if strategy:
        strategy_key = f"enable_{strategy}"
        return bool(flags.get(strategy_key, False))

    return True

    
