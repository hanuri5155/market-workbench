from datetime import datetime, timezone

from core.persistence.mysql_conn import _conn
from core.utils.zone_ids import parse_zone_parent_order_link_id


def upsert_zone(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    end_ms: int | None,
    side: str,
    base_entry: float,
    base_sl: float,
    base_upper: float,
    base_lower: float,
    cx=None,
):
    # Structure Zone 원본 upsert용
    # 사용자 상태(entry_override, is_active) 보존용
    start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
    end_dt = (
        datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        if end_ms is not None
        else None
    )

    side_up = (side or "").upper()
    if side_up not in ("LONG", "SHORT"):
        side_up = "LONG"

    sql = """
    INSERT INTO zone_state
      (symbol, interval_min, start_time, end_time,
       side, base_entry, base_sl, base_upper, base_lower,
       entry_override, is_active)
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      end_time = VALUES(end_time),
      base_entry = VALUES(base_entry),
      base_sl = VALUES(base_sl),
      base_upper = VALUES(base_upper),
      base_lower = VALUES(base_lower),
      is_active = CASE WHEN VALUES(end_time) IS NOT NULL THEN 0 ELSE is_active END
    """
    params = (
        symbol,
        int(interval_min),
        start_dt,
        end_dt,
        side_up,
        float(base_entry),
        float(base_sl),
        float(base_upper),
        float(base_lower),
        None,
        0,
    )

    if cx is None:
        with _conn() as local_cx:
            with local_cx.cursor() as cur:
                cur.execute(sql, params)
    else:
        with cx.cursor() as cur:
            cur.execute(sql, params)


def mark_zones_broken_by_close(
    *,
    symbol: str,
    interval_min: int,
    break_ms: int,
    close_price: float,
    cx=None,
) -> list[dict]:
    # 확정봉 종가 기준으로 SL을 넘은 활성 zone 종료 처리용
    break_dt = datetime.fromtimestamp(break_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)

    select_sql = """
    SELECT start_time, side
    FROM zone_state
    WHERE symbol = %s
      AND interval_min = %s
      AND end_time IS NULL
      AND (
        (side = 'LONG' AND %s < base_sl) OR
        (side = 'SHORT' AND %s > base_sl)
      )
    """

    update_sql = """
    UPDATE zone_state
    SET end_time = %s,
        is_active = 0
    WHERE symbol = %s
      AND interval_min = %s
      AND end_time IS NULL
      AND (
        (side = 'LONG' AND %s < base_sl) OR
        (side = 'SHORT' AND %s > base_sl)
      )
    """

    def _rows_to_delta(rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            start_time = row.get("start_time")
            if not isinstance(start_time, datetime):
                continue
            start_ms = int(start_time.replace(tzinfo=timezone.utc).timestamp() * 1000)
            side_up = (row.get("side") or "").upper()
            side_up = "SHORT" if side_up == "SHORT" else "LONG"
            out.append({"startTs": start_ms, "side": side_up, "endTs": int(break_ms)})
        return out

    if cx is None:
        with _conn() as local_cx:
            with local_cx.cursor() as cur:
                cur.execute(select_sql, (symbol, int(interval_min), float(close_price), float(close_price)))
                rows = cur.fetchall() or []
                delta = _rows_to_delta(rows)
                if rows:
                    cur.execute(update_sql, (break_dt, symbol, int(interval_min), float(close_price), float(close_price)))
                return delta

    with cx.cursor() as cur:
        cur.execute(select_sql, (symbol, int(interval_min), float(close_price), float(close_price)))
        rows = cur.fetchall() or []
        delta = _rows_to_delta(rows)
        if rows:
            cur.execute(update_sql, (break_dt, symbol, int(interval_min), float(close_price), float(close_price)))
        return delta


def fetch_active_zone_levels(
    *,
    symbol: str,
    interval_mins: list[int] | None = None,
    cx=None,
) -> list[dict]:
    # 활성 zone 목록 조회용
    def _dt_to_ms(value: datetime) -> int:
        return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)

    sql = """
    SELECT
        symbol,
        interval_min,
        start_time,
        end_time,
        side,
        base_entry,
        base_sl,
        base_upper,
        base_lower,
        entry_override,
        is_active
    FROM zone_state
    WHERE symbol = %s
      AND is_active = 1
      AND end_time IS NULL
    """
    params: list = [symbol]

    if interval_mins:
        interval_mins = [int(value) for value in interval_mins]
        placeholders = ",".join(["%s"] * len(interval_mins))
        sql += f" AND interval_min IN ({placeholders})"
        params.extend(interval_mins)

    sql += " ORDER BY interval_min ASC, start_time DESC"

    if cx is None:
        with _conn() as local_cx:
            with local_cx.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall() or []
    else:
        with cx.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []

    out: list[dict] = []
    for row in rows:
        side_up = (row.get("side") or "").upper()
        side_up = "SHORT" if side_up == "SHORT" else "LONG"
        side_lc = "short" if side_up == "SHORT" else "long"

        base_entry = float(row["base_entry"])
        base_sl = float(row["base_sl"])
        base_upper = float(row["base_upper"])
        base_lower = float(row["base_lower"])
        entry_override = row.get("entry_override")
        entry_override = float(entry_override) if entry_override is not None else None

        if entry_override is None:
            entry = base_entry
            sl = base_sl
            upper = base_upper
            lower = base_lower
        elif side_up == "LONG":
            entry = entry_override
            sl = base_sl
            upper = entry
            lower = sl
        else:
            entry = entry_override
            sl = base_sl
            upper = sl
            lower = entry

        start_time = row.get("start_time")
        if not isinstance(start_time, datetime):
            continue
        end_time = row.get("end_time")

        out.append(
            {
                "side": side_lc,
                "price": float(entry),
                "sl_check_price": float(sl),
                "upper": float(upper),
                "lower": float(lower),
                "candle": int(row["interval_min"]),
                "box_key": {
                    "symbol": symbol,
                    "interval_min": int(row["interval_min"]),
                    "start_ts": _dt_to_ms(start_time),
                    "side": side_up,
                },
                "start_ts": _dt_to_ms(start_time),
                "end_ts": _dt_to_ms(end_time) if isinstance(end_time, datetime) else None,
                "entry_override": entry_override,
                "base_entry": base_entry,
                "base_sl": base_sl,
            }
        )

    return out


def fetch_zone_base_sl_by_key(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    side: str,
    cx=None,
) -> float | None:
    # zone 메타 기준 기본 SL 조회용
    try:
        start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        side_up = (side or "").upper()
        sql = """
        SELECT base_sl
        FROM zone_state
        WHERE symbol=%s AND interval_min=%s AND start_time=%s AND side=%s
        LIMIT 1
        """
        if cx is None:
            with _conn() as local_cx:
                with local_cx.cursor() as cur:
                    cur.execute(sql, (symbol, int(interval_min), start_dt, side_up))
                    row = cur.fetchone()
        else:
            with cx.cursor() as cur:
                cur.execute(sql, (symbol, int(interval_min), start_dt, side_up))
                row = cur.fetchone()

        if not row:
            return None
        value = row.get("base_sl")
        return float(value) if value is not None else None
    except Exception:
        return None


def deactivate_zone_state_by_key(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    side: str,
    cx=None,
) -> bool | None:
    # 특정 zone 비활성화 처리용
    try:
        start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        side_up = (side or "").upper()
        sql = """
        UPDATE zone_state
        SET is_active = 0
        WHERE symbol=%s AND interval_min=%s AND start_time=%s AND side=%s
          AND end_time IS NULL
          AND is_active = 1
        """
        params = (symbol, int(interval_min), start_dt, side_up)

        def _run(cur):
            cur.execute(sql, params)
            affected = getattr(cur, "rowcount", 0) or 0
            return affected > 0

        if cx is None:
            with _conn() as local_cx:
                with local_cx.cursor() as cur:
                    return _run(cur)
        with cx.cursor() as cur:
            return _run(cur)
    except Exception:
        return None


def is_zone_active_by_key(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    side: str,
    cx=None,
) -> bool | None:
    # 특정 zone 활성 여부 확인용
    try:
        start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        side_up = (side or "").upper()
        sql = """
        SELECT 1
        FROM zone_state
        WHERE symbol=%s AND interval_min=%s AND start_time=%s AND side=%s
          AND end_time IS NULL
          AND is_active = 1
        LIMIT 1
        """
        params = (symbol, int(interval_min), start_dt, side_up)

        def _run(cur):
            cur.execute(sql, params)
            return cur.fetchone() is not None

        if cx is None:
            with _conn() as local_cx:
                with local_cx.cursor() as cur:
                    return _run(cur)
        with cx.cursor() as cur:
            return _run(cur)
    except Exception:
        return None


def mark_zone_broken_by_key(
    *,
    symbol: str,
    interval_min: int,
    start_ms: int,
    side: str,
    break_ms: int,
    deactivate_state: bool = True,
    cx=None,
) -> bool:
    # 특정 zone broken 처리용
    try:
        start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        break_dt = datetime.fromtimestamp(break_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        side_up = (side or "").upper()

        if deactivate_state:
            sql = """
            UPDATE zone_state
            SET end_time=%s,
                is_active=0
            WHERE symbol=%s AND interval_min=%s AND start_time=%s AND side=%s
              AND end_time IS NULL
            """
        else:
            sql = """
            UPDATE zone_state
            SET end_time=%s
            WHERE symbol=%s AND interval_min=%s AND start_time=%s AND side=%s
              AND end_time IS NULL
            """

        params = (break_dt, symbol, int(interval_min), start_dt, side_up)

        def _run(cur):
            cur.execute(sql, params)
            affected = getattr(cur, "rowcount", 0) or 0
            return affected > 0

        if cx is None:
            with _conn() as local_cx:
                with local_cx.cursor() as cur:
                    return _run(cur)
        with cx.cursor() as cur:
            return _run(cur)
    except Exception:
        return False


def mark_zone_broken_by_parent_order_link_id(
    *,
    parent_olid: str,
    break_ms: int,
    deactivate_state: bool = True,
    cx=None,
) -> bool:
    # parent_order_link_id 기준 zone broken 처리용
    meta = parse_zone_parent_order_link_id(parent_olid)
    if not meta:
        return False
    return mark_zone_broken_by_key(
        symbol=meta["symbol"],
        interval_min=meta["interval_min"],
        start_ms=meta["start_ts"],
        side=meta["side"],
        break_ms=int(break_ms),
        deactivate_state=deactivate_state,
        cx=cx,
    )
