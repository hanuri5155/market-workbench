## backend/core/tools/backfill_candles.py

import os, time, requests
from datetime import datetime, timedelta, timezone
from core.utils.log_utils import log
from core.persistence.mysql_conn import _conn

# 환경 변수
SYMBOL = os.getenv('SYMBOL', 'BTCUSDT')
BYBIT_BASE_URL = os.getenv('BYBIT_BASE_URL', 'https://api.bybit.com')
KLINE_CATEGORY = os.getenv('BYBIT_KLINE_CATEGORY', 'linear')

# 분 단위 타임프레임과 Bybit interval 파라미터 매핑
INTERVALS = {
    15: '15',
    30: '30',
    60: '60',
    240: '240',
    1440: 'D',
}

MAX_LIMIT = 1000  # Bybit Kline 최대 limit (공식 문서 기준) 


# UTC datetime -> epoch ms
def _to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


# epoch ms -> UTC datetime (tz-aware)
def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


# DB에 이미 저장된 가장 최신 start_time 조회 (없으면 None)
def _get_latest_start(symbol: str, interval_min: int) -> datetime | None:
    sql = (
        'SELECT MAX(start_time) AS max_start '
        'FROM candles '
        'WHERE symbol=%s AND interval_min=%s'
    )
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (symbol, interval_min))
            row = cur.fetchone()
            max_start = row and row.get('max_start')
            return max_start  # naive datetime (UTC 기준으로 취급)


# Bybit v5 /market/kline 응답(list[list])을 candles 테이블에 upsert
# kline 포맷: [start, open, high, low, close, volume, turnover, ...] 
def _insert_candles(
    symbol: str,
    interval_min: int,
    klines: list[list],
    source: str = 'bybit_rest_v5',
) -> int:
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
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.executemany(sql, rows)
            return cur.rowcount


# Bybit v5 /market/kline 한 페이지 호출
# start_ms ~ end_ms 구간에서 최대 limit개 캔들 조회
# end_ms 가 None이면 파라미터에서 생략
def fetch_klines_page(
    symbol: str,
    interval_str: str,
    start_ms: int,
    end_ms: int | None,
    limit: int = MAX_LIMIT,
) -> list[list]:
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


# 특정 심볼 + 타임프레임(분)을 대상으로
# candles 테이블을 최신까지 채움
def backfill_interval(symbol: str, interval_min: int):
    if interval_min not in INTERVALS:
        raise ValueError(f"지원하지 않는 interval_min: {interval_min}")

    interval_str = INTERVALS[interval_min]
    interval_ms = interval_min * 60 * 1000

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
        approx_end_ms = start_ms + interval_ms * (MAX_LIMIT - 1)
        end_ms = min(approx_end_ms, until_ms)

        page += 1
        log(
            f"➡️ [backfill] {symbol} {interval_min}m page={page} "
            f"range={_ms_to_utc(start_ms)} ~ {_ms_to_utc(end_ms)}",
        )

        klines = fetch_klines_page(symbol, interval_str, start_ms, end_ms, limit=MAX_LIMIT)
        if not klines:
            log(f"ℹ️ [backfill] {symbol} {interval_min}m 더 이상 가져올 Kline 없음 (page={page})")
            break

        # 혹시 end_ms 이후 데이터가 섞여있으면 필터링
        filtered = [k for k in klines if int(k[0]) <= until_ms]
        inserted = _insert_candles(symbol, interval_min, filtered)
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


def main():
    log(f"🔰 [backfill] 시작 symbol={SYMBOL}, intervals={list(INTERVALS.keys())}, base_url={BYBIT_BASE_URL}")
    for interval_min in sorted(INTERVALS.keys()):
        try:
            backfill_interval(SYMBOL, interval_min)
        except Exception as e:
            log(f"⚠️ [backfill] interval={interval_min}m 처리 중 예외: {e}")


if __name__ == "__main__":
    main()
