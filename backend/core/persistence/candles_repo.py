from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from core.persistence.mysql_conn import _conn


CANDLE_REST_VERIFY_EPSILON = Decimal("0.00000001")


@dataclass(frozen=True)
class RestVerifyCandleWriteResult:
    action: str
    existing_source: str | None = None
    changed_fields: tuple[str, ...] = ()


def _start_dt_from_ms(start_ms: int) -> datetime:
    # MySQL DATETIME은 tz 정보가 없으므로 UTC 기준 naive datetime으로 변환
    return datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _decimal_values_close(left, right, *, epsilon: Decimal = CANDLE_REST_VERIFY_EPSILON) -> bool:
    left_dec = _decimal_or_none(left)
    right_dec = _decimal_or_none(right)
    if left_dec is None or right_dec is None:
        return False
    return abs(left_dec - right_dec) <= epsilon


def _changed_rest_verify_ohlcv_fields(existing: dict, incoming: dict) -> tuple[str, ...]:
    changed: list[str] = []
    for field in ("open", "high", "low", "close", "volume"):
        incoming_value = incoming.get(field)
        if incoming_value is None:
            continue
        if not _decimal_values_close(existing.get(field), incoming_value):
            changed.append(field)
    return tuple(changed)


def _fetch_candle_for_key(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
) -> dict | None:
    sql = """
    SELECT
      `open`,
      `high`,
      `low`,
      `close`,
      volume,
      turnover,
      source
    FROM candles
    WHERE symbol = %s
      AND interval_min = %s
      AND start_time = %s
    LIMIT 1
    """
    start_dt = _start_dt_from_ms(start_ms)
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (symbol, int(interval_min), start_dt))
            return cur.fetchone()


def upsert_candle_from_rest_verify(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = None,
    turnover: float | None = None,
    source: str | None = "bybit_rest_fix",
) -> RestVerifyCandleWriteResult:
    """
    REST verify 전용 candles upsert guardrail.

    기존 row와 REST OHLCV가 동일한 경우 source만 bybit_rest_fix로 덮는
    metadata-only update를 skip한다. 실제 mismatch 또는 missing row는 기존
    upsert path를 유지한다.
    """
    existing = _fetch_candle_for_key(
        symbol=symbol,
        interval_min=interval_min,
        start_ms=start_ms,
    )

    if not existing:
        upsert_candle(
            symbol=symbol,
            interval_min=interval_min,
            start_ms=start_ms,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            turnover=turnover,
            source=source,
        )
        return RestVerifyCandleWriteResult(action="insert_bybit_rest_fix")

    incoming = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    changed_fields = _changed_rest_verify_ohlcv_fields(existing, incoming)
    existing_source = existing.get("source")

    if not changed_fields:
        return RestVerifyCandleWriteResult(
            action="skip_source_overwrite",
            existing_source=existing_source,
        )

    upsert_candle(
        symbol=symbol,
        interval_min=interval_min,
        start_ms=start_ms,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        turnover=turnover,
        source=source,
    )
    return RestVerifyCandleWriteResult(
        action="repair_source_bybit_rest_fix",
        existing_source=existing_source,
        changed_fields=changed_fields,
    )

def upsert_candle(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = None,
    turnover: float | None = None,
    source: str | None = "bybit_ws",
):
    """
    WS/REST에서 받은 마감 캔들을 candles 테이블에 upsert.
    - start_ms: 캔들 시작 시각 (epoch ms, UTC 기준)
    - interval_min: 15 / 30 / 60 / 240 ...
    """
    start_dt = _start_dt_from_ms(start_ms)

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
            cur.execute(
                sql,
                (
                    symbol,
                    int(interval_min),
                    start_dt,
                    float(open_),
                    float(high),
                    float(low),
                    float(close),
                    volume,
                    turnover,
                    source,
                ),
            )

def fetch_candles_for_chart(
    *,
    symbol: str,
    interval_min: int,
    limit: int | None = None,
    before_ms: int | None = None,   #  추가
    cx=None,
) -> list[dict]:
    """
    차트용 캔들 조회:
      - DB candles 테이블에서 symbol + interval_min 기준으로
        최신 캔들을 조회
      - limit가 None이면 전체,
        숫자면 해당 개수만 (최신부터) 가져온 뒤 시간 오름차순으로 정렬
      - before_ms가 주어지면, 그 timestamp 이전(start_time < before_dt)만 조회
    """
    interval_min = int(interval_min)

    base_sql = """
    SELECT
        start_time,
        `open`,
        `high`,
        `low`,
        `close`,
        volume
    FROM candles
    WHERE symbol = %s
      AND interval_min = %s
    """

    params: list = [symbol, interval_min]

    #  before_ms(= epoch ms) 이전의 캔들만
    if before_ms is not None:
        before_dt = datetime.fromtimestamp(
            before_ms / 1000.0,
            tz=timezone.utc
        ).replace(tzinfo=None)
        base_sql += " AND start_time < %s"
        params.append(before_dt)

    # 최신순으로 가져온 뒤
    base_sql += " ORDER BY start_time DESC"

    if limit is not None:
        base_sql += " LIMIT %s"
        params.append(int(limit))

    if cx is None:
        with _conn() as local_cx:
            with local_cx.cursor() as cur:
                cur.execute(base_sql, tuple(params))
                rows = cur.fetchall() or []
    else:
        with cx.cursor() as cur:
            cur.execute(base_sql, tuple(params))
            rows = cur.fetchall() or []

    # 최신순으로 뽑았으니, 차트용으로는 시간 오름차순(reverse)
    rows.reverse()

    interval_ms = interval_min * 60_000
    out: list[dict] = []

    for row in rows:
        dt = row.get("start_time")
        if not isinstance(dt, datetime):
            continue

        start_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = start_ms + interval_ms

        out.append(
            {
                "start": start_ms,
                "end": end_ms,
                "confirm": True,  # DB에는 확정 봉만 넣기 때문에 항상 True
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": _float_or_none(row.get("volume")),
            }
        )

    return out

def fetch_latest_candle_for_chart(
    *,
    symbol: str,
    interval_min: int,
    latest_partial_override: dict | None = None,
) -> dict | None:
    """
    차트용 최신 1개 캔들 조회.
    - 기본: DB(candles 테이블)의 마지막 확정 봉
    - 추가: candle_detector 가 공유한 실시간 캔들이 있으면
            그 봉과 비교해서 더 최근(end 기준) 것을 반환
    """
    interval_min = int(interval_min)
    has_partial_override = isinstance(latest_partial_override, dict)

    # 1) DB 에서 마지막 확정 봉 조회
    latest_db: dict | None = None

    sql = """
    SELECT
        start_time,
        `open`,
        `high`,
        `low`,
        `close`,
        volume
    FROM candles
    WHERE symbol = %s
      AND interval_min = %s
    ORDER BY start_time DESC
    LIMIT 1
    """
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (symbol, interval_min))
            row = cur.fetchone()

    if row:
        dt = row.get("start_time")
        if isinstance(dt, datetime):
            interval_ms = interval_min * 60_000
            start_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            end_ms = start_ms + interval_ms

            latest_db = {
                "start": start_ms,
                "end": end_ms,
                "confirm": True,  # DB 에는 확정 봉만 들어감
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": _float_or_none(row.get("volume")),
            }

    # 2) shared_state 에 저장된 실시간 캔들 후보 가져오기
    latest_partial: dict | None = None
    try:
        raw = latest_partial_override
        if raw is None:
            from core.state.shared_state import get_latest as _get_latest_partial

            raw = _get_latest_partial(str(interval_min))
        if isinstance(raw, dict):
            # 필요한 필드만 안전하게 캐스팅
            start_ms = int(raw.get("start"))
            end_ms = int(raw.get("end"))
            open_ = float(raw.get("open"))
            high = float(raw.get("high"))
            low = float(raw.get("low"))
            close = float(raw.get("close"))
            volume = _float_or_none(raw.get("volume"))

            latest_partial = {
                "start": start_ms,
                "end": end_ms,
                "confirm": bool(raw.get("confirm", False)),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
    except Exception:
        # shared_state 쪽에서 문제가 나더라도 차트가 죽지 않도록 조용히 무시
        latest_partial = None

    # 3) 최종 선택 로직 + source 플래그
    selected: dict | None = None
    source: str | None = None

    # 3-1) partial/DB 둘 다 없는 경우
    if not latest_db and not latest_partial:
        return None

    # 3-2) DB만 있는 경우
    if latest_db and not latest_partial:
        selected = latest_db
        source = "db"

    # 3-3) partial만 있는 경우
    elif latest_partial and not latest_db:
        selected = latest_partial
        source = "partial"

    # 3-4) 둘 다 있는 경우: 기존 로직 그대로 유지
    else:
        assert latest_db is not None and latest_partial is not None
        interval_ms = interval_min * 60_000
        end_diff_ms = latest_partial["end"] - latest_db["end"]

        # 1) partial 이 너무 과거/미래인 경우 DB 우선
        if end_diff_ms < -interval_ms * 1.1:
            selected = latest_db
            source = "db"
        elif end_diff_ms > interval_ms * 1.1:
            selected = latest_db
            source = "db"
        else:
            # 2) end 시각이 같거나 partial 이 과거면 DB 사용
            if latest_partial["end"] <= latest_db["end"]:
                selected = latest_db
                source = "db"
            else:
                # 3) partial 이 더 최신이면 partial 사용
                #    (이때 confirm 정보도 함께 내려줌)
                if has_partial_override:
                    is_confirmed = bool(latest_partial.get("confirm", False))
                else:
                    is_confirmed = False
                    try:
                        from core.state.shared_state import get_confirm_flag as _get_confirm_flag

                        is_confirmed = bool(
                            _get_confirm_flag(str(interval_min), latest_partial["start"])
                        )
                    except Exception:
                        is_confirmed = False

                latest_partial["confirm"] = bool(is_confirmed)
                selected = latest_partial
                source = "partial"

    if selected is None:
        return None

    #  최종적으로 source 플래그를 붙여서 반환
    return {
        **selected,
        "source": source or "unknown",
    }
