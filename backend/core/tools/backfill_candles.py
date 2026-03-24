## backend/core/tools/backfill_candles.py

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if __package__ in {None, ""}:
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

from core.utils.log_utils import log

# 환경 변수
SYMBOL = os.getenv('SYMBOL', 'BTCUSDT')
BYBIT_BASE_URL = os.getenv('BYBIT_BASE_URL', 'https://api.bybit.com')
KLINE_CATEGORY = os.getenv('BYBIT_KLINE_CATEGORY', 'linear')

# 분 단위 타임프레임과 Bybit interval 파라미터 매핑
INTERVALS = {
    1: '1',
    15: '15',
    30: '30',
    60: '60',
    240: '240',
    1440: 'D',
}

DEFAULT_BACKFILL_INTERVALS = (15, 30, 60, 240, 1440)
MAX_LIMIT = 1000  # Bybit Kline 최대 limit (공식 문서 기준)
DEFAULT_SOURCE = 'bybit_rest_v5'
ONE_MINUTE_BACKFILL_SOURCE = 'bybit_rest_1m_backfill'
ONE_MINUTE_WINDOW_ERROR = (
    "1m backfill requires explicit --start and --end. "
    "Use --plan first. "
    "Production write must be a separate approved ops window."
)

VALIDATION_CHECKLIST = (
    "row count by date",
    "expected vs actual minute rows",
    "duplicate natural key groups",
    "null OHLC count",
    "null volume/turnover count",
    "high >= low",
    "open/high/low/close > 0",
    "high >= open/close",
    "low <= open/close",
    "source distribution",
    "gap detection",
    "15m aggregate sanity",
)


def _to_ms(dt: datetime) -> int:
    """UTC datetime -> epoch ms"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_to_utc(ms: int) -> datetime:
    """epoch ms -> UTC datetime (tz-aware)"""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _coerce_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_utc_datetime(value: str) -> datetime:
    """Parse YYYY-MM-DD or ISO-like datetime as UTC."""
    raw = value.strip()
    if not raw:
        raise ValueError("empty datetime")
    if len(raw) == 10:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    normalized = raw.replace("Z", "+00:00")
    return _coerce_utc(datetime.fromisoformat(normalized))


def _source_for_interval(interval_min: int) -> str:
    if interval_min == 1:
        return ONE_MINUTE_BACKFILL_SOURCE
    return DEFAULT_SOURCE


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _open_conn():
    from core.persistence.mysql_conn import _conn

    return _conn()


def build_backfill_plan(
    *,
    symbol: str,
    interval_min: int,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = MAX_LIMIT,
    category: str = KLINE_CATEGORY,
) -> dict[str, object]:
    if interval_min not in INTERVALS:
        raise ValueError(f"지원하지 않는 interval_min: {interval_min}")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    start_dt = _coerce_utc(start_dt)
    end_dt = _coerce_utc(end_dt)
    if end_dt <= start_dt:
        raise ValueError("--end must be later than --start")

    interval_seconds = interval_min * 60
    window_seconds = int((end_dt - start_dt).total_seconds())
    expected_bars = math.ceil(window_seconds / interval_seconds)
    expected_pages = _ceil_div(expected_bars, limit)
    start_ms = _to_ms(start_dt)
    end_ms = _to_ms(end_dt)
    last_start_ms = end_ms - (interval_seconds * 1000)

    return {
        "symbol": symbol,
        "interval_min": interval_min,
        "bybit_interval": INTERVALS[interval_min],
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "window_minutes": window_seconds // 60,
        "expected_bars": expected_bars,
        "page_limit": limit,
        "expected_pages": expected_pages,
        "category": category,
        "source": _source_for_interval(interval_min),
        "dry_run_or_plan": True,
        "db_write": False,
        "estimated_requests": expected_pages,
        "estimated_api_pages": expected_pages,
        "estimated_first_start_ms": start_ms,
        "estimated_last_end_ms": end_ms,
        "estimated_last_start_ms": last_start_ms,
        "validation_checks_to_run": list(VALIDATION_CHECKLIST),
        "next_safe_command_example": (
            "python backend/core/tools/backfill_candles.py "
            f"--symbol {symbol} --interval {interval_min} "
            f"--start {start_dt.strftime('%Y-%m-%dT%H:%M:%S')} "
            f"--end {end_dt.strftime('%Y-%m-%dT%H:%M:%S')} --plan"
        ),
    }


def print_backfill_plan(plan: dict[str, object]) -> None:
    print("1m REST candle backfill plan" if plan["interval_min"] == 1 else "REST candle backfill plan")
    print(f"symbol: {plan['symbol']}")
    print(f"interval_min: {plan['interval_min']}")
    print(f"bybit_interval: {plan['bybit_interval']}")
    print(f"start: {plan['start']}")
    print(f"end: {plan['end']}")
    print(f"window_minutes: {plan['window_minutes']}")
    print(f"expected_bars: {plan['expected_bars']}")
    print(f"page_limit: {plan['page_limit']}")
    print(f"expected_pages: {plan['expected_pages']}")
    print(f"estimated_requests: {plan['estimated_requests']}")
    print(f"category: {plan['category']}")
    print(f"source: {plan['source']}")
    print(f"dry_run_or_plan: {str(plan['dry_run_or_plan']).lower()}")
    print(f"db_write: {str(plan['db_write']).lower()}")
    print("validation_checks_to_run:")
    for check in plan["validation_checks_to_run"]:
        print(f"- {check}")
    print("next_safe_command_example:")
    print(plan["next_safe_command_example"])


def _get_latest_start(symbol: str, interval_min: int) -> datetime | None:
    """DB에 이미 저장된 가장 최신 start_time 조회 (없으면 None)"""
    sql = (
        'SELECT MAX(start_time) AS max_start '
        'FROM candles '
        'WHERE symbol=%s AND interval_min=%s'
    )
    with _open_conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (symbol, interval_min))
            row = cur.fetchone()
            max_start = row and row.get('max_start')
            return max_start  # naive datetime (UTC 기준으로 취급)


def _insert_candles(
    symbol: str,
    interval_min: int,
    klines: list[list],
    source: str = DEFAULT_SOURCE,
) -> int:
    """
    Bybit v5 /market/kline 응답(list[list])을 candles 테이블에 upsert.
    kline 포맷: [start, open, high, low, close, volume, turnover, ...]
    """
    if not klines:
        return 0

    rows = []
    for k in klines:
        try:
            start_ms = int(k[0])
            # UTC aware -> naive 로 변환 (DB DATETIME은 타임존 정보 없음, UTC 기준)
            start_dt = _ms_to_utc(start_ms).replace(tzinfo=None)
            open_, high, low, close = k[1], k[2], k[3], k[4]
            volume = k[5] if len(k) > 5 else None
            turnover = k[6] if len(k) > 6 else None
        except (IndexError, ValueError, TypeError):
            # 포맷 이상하면 그냥 스킵
            continue

        rows.append(
            (
                symbol,
                int(interval_min),
                start_dt,
                open_,
                high,
                low,
                close,
                volume,
                turnover,
                source,
            )
        )

    if not rows:
        return 0

    sql = """
    INSERT INTO candles
      (symbol, interval_min, start_time,
       open, high, low, close,
       volume, turnover, source)
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      open=VALUES(open),
      high=VALUES(high),
      low=VALUES(low),
      close=VALUES(close),
      volume=VALUES(volume),
      turnover=VALUES(turnover),
      source=VALUES(source)
    """
    with _open_conn() as cx:
        with cx.cursor() as cur:
            cur.executemany(sql, rows)
            return cur.rowcount


def fetch_klines_page(
    symbol: str,
    interval_str: str,
    start_ms: int,
    end_ms: int | None,
    limit: int = MAX_LIMIT,
) -> list[list]:
    """
    Bybit v5 /market/kline 한 페이지 호출.
    start_ms ~ end_ms 구간에서 최대 limit개의 캔들을 가져온다.
    end_ms 가 None이면 파라미터에서 생략.
    """
    url = f"{BYBIT_BASE_URL}/v5/market/kline"
    params: dict[str, object] = {
        'category': KLINE_CATEGORY,   # 예: 'linear' (USDT 무기한) 
        'symbol': symbol,
        'interval': interval_str,
        'start': start_ms,
        'limit': limit,
    }
    if end_ms is not None:
        params['end'] = end_ms

    try:
        resp = requests.get(url, params=params, timeout=10)
    except Exception as e:
        log(f"⚠️ [backfill] HTTP 오류: {e}")
        return []

    try:
        data = resp.json()
    except ValueError:
        log(f"⚠️ [backfill] JSON 파싱 실패 status={resp.status_code}")
        return []

    if resp.status_code != 200 or data.get('retCode') != 0:
        log(f"⚠️ [backfill] Bybit Kline 에러 retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
        return []

    result = data.get('result') or {}
    lst = result.get('list') or []
    if not isinstance(lst, list):
        return []

    # startTime(ms) 기준 오름차순 정렬
    try:
        lst.sort(key=lambda x: int(x[0]))
    except Exception:
        pass
    return lst


def backfill_interval(
    symbol: str,
    interval_min: int,
    *,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
    limit: int = MAX_LIMIT,
    source: str | None = None,
):
    """
    특정 심볼 + 타임프레임(분)을 대상으로
    candles 테이블을 최신까지 채움.
    """
    if interval_min not in INTERVALS:
        raise ValueError(f"지원하지 않는 interval_min: {interval_min}")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if interval_min == 1 and (start_dt is None or end_dt is None):
        raise ValueError(ONE_MINUTE_WINDOW_ERROR)

    interval_str = INTERVALS[interval_min]
    interval_ms = interval_min * 60 * 1000
    source = source or _source_for_interval(interval_min)
    # interval=1 execute mode is intentionally explicit. Before an approved
    # ops write window, add/verify source-overwrite guardrails for future
    # final/reconcile-owned rows.

    if start_dt is not None and end_dt is not None:
        start_dt = _coerce_utc(start_dt)
        end_dt = _coerce_utc(end_dt)
        if end_dt <= start_dt:
            raise ValueError("--end must be later than --start")
        # Explicit windows use [start, end), so the final candle start is end - interval.
        until_dt = end_dt - timedelta(minutes=interval_min)
        log(f"ℹ️ [backfill] {symbol} {interval_min}m 명시 구간 백필: {start_dt} <= start_time < {end_dt}")
    else:
        # DB에 이미 있는 가장 최신 시각 이후부터 시작
        latest = _get_latest_start(symbol, interval_min)
        if latest:
            start_dt = latest + timedelta(minutes=interval_min)
            log(f"ℹ️ [backfill] {symbol} {interval_min}m 기존 최대 start_time={latest} → 다음 캔들부터 백필 시작")
        else:
            # 기존 데이터가 없을 때의 기본 시작 시각(UTC): 2020-04-23
            start_dt = datetime(2020, 4, 23, tzinfo=timezone.utc)
            log(f"ℹ️ [backfill] {symbol} {interval_min}m 기존 데이터 없음 → {start_dt} 부터 백필 시작")

        # 미마감 캔들은 제외하기 위해 한 인터벌 정도 여유를 둠
        now_utc = datetime.now(timezone.utc)
        until_dt = now_utc - timedelta(minutes=interval_min)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

    if start_dt >= until_dt:
        log(f"ℹ️ [backfill] {symbol} {interval_min}m 이미 최신 상태 (start_dt={start_dt}, until_dt={until_dt})")
        return

    start_ms = _to_ms(start_dt)
    until_ms = _to_ms(until_dt)

    page = 0
    total_inserted = 0

    while start_ms <= until_ms:
        # 이번 페이지에서 가져올 end 상한 (limit * interval 만큼)
        approx_end_ms = start_ms + interval_ms * (limit - 1)
        end_ms = min(approx_end_ms, until_ms)

        page += 1
        log(
            f"➡️ [backfill] {symbol} {interval_min}m page={page} "
            f"range={_ms_to_utc(start_ms)} ~ {_ms_to_utc(end_ms)}",
        )

        klines = fetch_klines_page(symbol, interval_str, start_ms, end_ms, limit=limit)
        if not klines:
            log(f"ℹ️ [backfill] {symbol} {interval_min}m 더 이상 가져올 Kline 없음 (page={page})")
            break

        # 혹시 end_ms 이후 데이터가 섞여있으면 필터링
        filtered = [k for k in klines if int(k[0]) <= until_ms]
        inserted = _insert_candles(symbol, interval_min, filtered, source=source)
        total_inserted += inserted

        try:
            last_start_ms = max(int(k[0]) for k in klines)
        except (ValueError, TypeError):
            break

        if last_start_ms >= until_ms:
            log(f"✅ [backfill] {symbol} {interval_min}m 목표 시각까지 도달 (last_start={_ms_to_utc(last_start_ms)})")
            break

        # 다음 페이지 시작 시각: 마지막 캔들 다음 인터벌
        start_ms = last_start_ms + interval_ms

        # 레이트리밋 보호
        time.sleep(0.2)

    log(f"✅ [backfill] {symbol} {interval_min}m 백필 완료: 총 {total_inserted} row upsert")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill Bybit REST candles into the candles table.")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--interval", dest="intervals", action="append", type=int)
    parser.add_argument("--start", help="UTC start time, YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")
    parser.add_argument("--end", help="UTC exclusive end time, YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")
    parser.add_argument("--limit", type=int, default=MAX_LIMIT)
    parser.add_argument("--plan", action="store_true", help="Print a no-write plan without DB/API calls.")
    parser.add_argument("--execute", action="store_true", help="Execute DB writes. Required for interval=1 writes.")
    parser.add_argument("--json", action="store_true", help="Print plan output as JSON.")
    return parser


def _parse_window_or_error(parser: argparse.ArgumentParser, start: str | None, end: str | None) -> tuple[datetime, datetime]:
    if not start or not end:
        parser.error(ONE_MINUTE_WINDOW_ERROR)
    try:
        return parse_utc_datetime(start), parse_utc_datetime(end)
    except ValueError as exc:
        parser.error(f"invalid --start/--end datetime: {exc}")


def _run_default_backfill() -> int:
    log(f"🔰 [backfill] 시작 symbol={SYMBOL}, intervals={list(DEFAULT_BACKFILL_INTERVALS)}, base_url={BYBIT_BASE_URL}")
    for interval_min in DEFAULT_BACKFILL_INTERVALS:
        try:
            backfill_interval(SYMBOL, interval_min)
        except Exception as e:
            log(f"⚠️ [backfill] interval={interval_min}m 처리 중 예외: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _run_default_backfill()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.plan and args.execute:
        parser.error("--plan and --execute cannot be used together")
    if args.limit < 1 or args.limit > MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")

    intervals = args.intervals or list(DEFAULT_BACKFILL_INTERVALS)
    unsupported = [interval for interval in intervals if interval not in INTERVALS]
    if unsupported:
        parser.error(f"unsupported interval_min: {unsupported[0]}")

    has_one_minute = 1 in intervals
    if has_one_minute and (not args.start or not args.end):
        parser.error(ONE_MINUTE_WINDOW_ERROR)

    plan_mode = args.plan or (has_one_minute and not args.execute)
    if plan_mode:
        start_dt, end_dt = _parse_window_or_error(parser, args.start, args.end)
        plans = [
            build_backfill_plan(
                symbol=args.symbol,
                interval_min=interval,
                start_dt=start_dt,
                end_dt=end_dt,
                limit=args.limit,
            )
            for interval in intervals
        ]
        output: object = plans[0] if len(plans) == 1 else plans
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            for index, plan in enumerate(plans):
                if index:
                    print()
                print_backfill_plan(plan)
        return 0

    start_dt = end_dt = None
    if args.start or args.end:
        start_dt, end_dt = _parse_window_or_error(parser, args.start, args.end)

    log(f"🔰 [backfill] 시작 symbol={args.symbol}, intervals={intervals}, base_url={BYBIT_BASE_URL}")
    for interval_min in intervals:
        try:
            backfill_interval(
                args.symbol,
                interval_min,
                start_dt=start_dt,
                end_dt=end_dt,
                limit=args.limit,
            )
        except Exception as e:
            log(f"⚠️ [backfill] interval={interval_min}m 처리 중 예외: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
