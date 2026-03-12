## backend/core/utils/log_utils.py

import os, csv, json, datetime, logging 
from logging.handlers import RotatingFileHandler
from core.utils.file_utils import write_json_atomic

KST_TZ = datetime.timezone(datetime.timedelta(hours=9))

def _kst_now():
    return datetime.datetime.now(tz=KST_TZ)

def _kst_formatter(fmt: str, datefmt: str = None) -> logging.Formatter:
    formatter = logging.Formatter(fmt, datefmt)
    formatter.converter = lambda seconds: datetime.datetime.fromtimestamp(
        seconds, tz=KST_TZ
    ).timetuple()
    return formatter

# 1) 기본 로그 시스템 설정
# 기본 메인 로거:
#   - backend/logs/all.log 에만 기록
#   - run.log 는 별도의 헬퍼(log_to_run_file)로 '선택된 로그'만 append
def setup_logger(log_file: str = "logs/all.log"):
    # 이 파일 위치 기준으로 backend 디렉터리 계산
    base_dir = os.path.dirname(os.path.dirname(__file__))   # .../backend
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # 메인 로그 파일 (기존 all.log)
    if os.path.isabs(log_file):
        main_log_path = log_file
    else:
        # "logs/all.log" 같이 들어온 경우도 backend/logs/all.log 로 맞춤
        main_log_path = os.path.join(log_dir, os.path.basename(log_file))

    logger = logging.getLogger("trading_logger")
    logger.setLevel(logging.DEBUG)

    # 핸들러가 이미 설정되어 있으면 그대로 사용
    if logger.hasHandlers():
        return logger

    # 콘솔 출력 핸들러 (systemd journal 로도 들어감)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)

    # 파일 핸들러 1: all.log
    file_all = RotatingFileHandler(
        main_log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
    )
    file_all.setLevel(logging.DEBUG)

    formatter = _kst_formatter(
        "[%(asctime)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    console.setFormatter(formatter)
    file_all.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_all)
    return logger

# run.log 에 '한 줄'만 추가하고 싶을 때 사용
# - 기존 run.log(메인 봇 stdout, watch 스크립트 등) 흐름 + 전략 온/오프 로그용
def log_to_run_file(msg: str):
    try:
        base_dir = os.path.dirname(os.path.dirname(__file__))  # .../backend
        log_dir = os.path.join(base_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        run_path = os.path.join(log_dir, "run.log")

        ts = _kst_now().strftime("[%Y-%m-%d %H:%M:%S]")
        line = f"{ts} {msg}\n"

        with open(run_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # run.log 에 쓰다가 에러가 나더라도 메인 로깅은 죽지 않도록 조용히 무시
        pass


# 1-1) 펀딩 전용 로거 (파일만, 콘솔 출력 없음)
_funding_logger = None

def get_funding_logger(log_file="logs/funding.log"):
    global _funding_logger
    if _funding_logger is not None:
        return _funding_logger

    os.makedirs("logs", exist_ok=True)
    logger = logging.getLogger("funding_logger")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 상위(콘솔)로 전파 금지 → run.log에 안 찍힘

    # 파일 핸들러(로테이션)
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(_kst_formatter("%(asctime)s | %(message)s"))
    logger.addHandler(fh)

    _funding_logger = logger
    return logger

# 펀딩 전용 로그: logs/funding.log 에만 기록(콘솔 X)
def funding_log(msg: str):
    get_funding_logger().info(msg)

# 실시간 화면용 스냅샷 파일. 매번 덮어쓰기(append 아님)
def write_funding_snapshot(snapshot: dict, path: str = "logs/funding_snapshot.json"):
    # atomic write
    write_json_atomic(path, snapshot)


#  로거 객체 생성
logger = setup_logger()

#  심플 로깅 함수 (전략, 체결 등에서 사용)
def log(*args):
    logger.info(" ".join(str(arg) for arg in args))

#  진입 관련 트레이드 로깅
def log_entry_trade(data: dict, filename="logs/entry_log.csv"):
    os.makedirs("logs", exist_ok=True)
    fieldnames = [
        "timestamp", "symbol", "side", "entry_price", "sl", "tp", "qty",
        "wallet_balance", "leverage", "expected_loss_pct",
        "position_value", "avg_entry_price"
    ]
    write_header = not os.path.exists(filename)
    with open(filename, mode="a", newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(data)

# 손절선 생성, 터치, 삭제 로그
def log_level_event(event: str, price: float, side: str, context: str = "", filename="logs/levels_log.csv"):
    os.makedirs("logs", exist_ok=True)
    write_header = not os.path.exists(filename)
    with open(filename, mode="a", newline='') as file:
        writer = csv.writer(file)
        if write_header:
            writer.writerow(["timestamp", "event", "price", "side", "context"])
        writer.writerow([
            _kst_now().strftime("%Y-%m-%d %H:%M:%S"),
            event,
            price,
            side,
            context
        ])

# 레벨 저장 함수
# 레벨 저장 공통 함수
# - sort=True: 가격 기준 정렬
# - flush=True: flush + fsync 적용
# - verify=True: 저장 후 재열기
def save_levels(levels: list, path: str, *, call_from: str = "", sort: bool = False, verify: bool = False):
    try:
        if sort:
            levels.sort(key=lambda x: x.get("price", 0))

        write_json_atomic(path, levels, indent=2)

        if verify:
            with open(path, "r", encoding="utf-8") as fcheck:
                json.load(fcheck)
                #log(f" 저장된 파일 내용:\n{raw}")
        
        #log(f" 레벨 저장 완료 ← {call_from}")
    except Exception as e:
        log(f"❌ 레벨 저장 중 예외 발생 ({path}): {e}")

# JSON 기반 레벨 파일 로딩 공통 함수
def load_levels(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
