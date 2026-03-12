from datetime import datetime, timezone
from core.persistence.mysql_conn import _conn

# entry 시 positions에 INSERT. order_link_id UNIQUE라 중복이면 무시
def upsert_position_on_entry(*, account_id: int, session_id: int|None,
                             symbol: str, strategy: str, side: str,
                             order_link_id: str, parent_order_link_id: str|None = None,
                             entry_price: float,entry_qty: float, leverage: float|None,
                             tp_partition: int|None, sl_price: float|None=None,
                             entry_time_utc: datetime|None=None):
    entry_time_utc = entry_time_utc or datetime.now(timezone.utc)
    entry_value = float(entry_price) * float(entry_qty)
    sql = """
    INSERT INTO positions
      (account_id, session_id, symbol, strategy, side,
       order_link_id, parent_order_link_id, entry_time, entry_price, entry_qty, entry_value,
       leverage, tp_partition, sl_price, closed)
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
    ON DUPLICATE KEY UPDATE
      parent_order_link_id = COALESCE(parent_order_link_id, VALUES(parent_order_link_id)),
      entry_time=LEAST(entry_time, VALUES(entry_time)),
      entry_price=VALUES(entry_price),
      entry_qty=VALUES(entry_qty),
      entry_value=VALUES(entry_value),
      leverage=VALUES(leverage),
      tp_partition=VALUES(tp_partition),
      sl_price=COALESCE(VALUES(sl_price), sl_price);
    """
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (
                account_id, session_id, symbol, strategy, side,
                order_link_id, parent_order_link_id,
                entry_time_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                entry_price, entry_qty, entry_value,
                leverage, tp_partition, sl_price
            ))
# order_link_id로 positions.id를 찾아 fills에 INSERT
# fill_type: 'ENTRY' | 'TP' | 'SL' | 'EXIT' | 'FUNDING' | 'OTHER'
# stage_code: 선택적 단계 코드. tp_stage로 들어온 값도 함께 수용
def insert_fill_by_order_link_id(order_link_id: str, *,
                                 fill_time_utc: datetime,
                                 price: float, qty: float,
                                 pnl_gross: float, fee: float,
                                 fill_type: str, stage_code: int|None=None, **kwargs):
    valid_types = {'ENTRY','TP','SL','EXIT','FUNDING','OTHER'}
    if fill_type not in valid_types:
        raise ValueError(f"Invalid fill_type: {fill_type}")
    
    # 이전 호출부에서 tp_stage를 넘겨도 같은 값으로 처리
    if stage_code is None and "tp_stage" in kwargs:
        try:
            stage_code = kwargs.pop("tp_stage")
        except Exception:
            stage_code = None

    get_pos = "SELECT id FROM positions WHERE order_link_id=%s"
    ins = """INSERT INTO fills(position_id, fill_time, price, qty, pnl_gross, fee, fill_type, stage_code)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s);"""

    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(get_pos, (order_link_id,))
            row = cur.fetchone()
            if not row:
                return
            pid = row["id"]
            cur.execute(ins, (
                pid,
                fill_time_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                price, qty, pnl_gross, fee, fill_type, stage_code
            ))


# 지정된 order_link_id의 포지션을 닫고, fills 집계를 positions에 반영:
# ※ 생성 칼럼(pnl_per_usd, fee_per_usd, is_win, initial_margin_usd)은 절대 SET하지 않음
def finalize_position_close_by_order_link_id(order_link_id: str, *, exit_time_utc: datetime):
    with _conn() as cx:
        with cx.cursor() as cur:
            # 1) 포지션 ID + fee_open 조회
            cur.execute("SELECT id, fee_open FROM positions WHERE order_link_id=%s", (order_link_id,))
            row = cur.fetchone()
            if not row:
                return
            pid = row["id"]
            fee_open_from_db = float(row.get("fee_open") or 0.0)

            # 2) 종료 체결 집계 (TP/SL/EXIT) + VWAP 종료가 계산
            cur.execute("""
                SELECT
                    -- VWAP: Bybit exec_price들이 fills.price로 들어오므로 그대로 사용
                    SUM(CASE WHEN fill_type IN ('TP','SL','EXIT') THEN price * qty ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN fill_type IN ('TP','SL','EXIT') THEN qty ELSE 0 END), 0) AS exit_avg,
                    COALESCE(SUM(CASE WHEN fill_type IN ('TP','SL','EXIT','FUNDING') THEN pnl_gross ELSE 0 END), 0) AS sum_pnl_gross,
                    COALESCE(SUM(CASE WHEN fill_type IN ('TP','SL','EXIT')          THEN fee       ELSE 0 END), 0) AS sum_fee_close
                FROM fills
                WHERE position_id=%s
            """, (pid,))
            agg = cur.fetchone() or {}
            exit_avg = agg.get("exit_avg")
            exit_avg = float(exit_avg) if exit_avg is not None else None

            # 2-1) 마지막 체결가(Last) 조회: 동일 시각 다중 체결 대비를 위해 id도 보조 정렬
            cur.execute("""
                SELECT price
                FROM fills
                WHERE position_id=%s AND fill_type IN ('TP','SL','EXIT')
                ORDER BY fill_time DESC, id DESC
                LIMIT 1
            """, (pid,))
            last_row = cur.fetchone()
            exit_last = float(last_row["price"]) if last_row and last_row.get("price") is not None else None

            pnl_gross = float(agg.get("sum_pnl_gross") or 0.0)
            fee_close = float(agg.get("sum_fee_close") or 0.0)

            # 3) 진입 수수료는 positions.fee_open을 그대로 사용
            fee_open = fee_open_from_db
            fee_total = fee_open + fee_close
            pnl_net   = pnl_gross - fee_total

            # 4) 종료 업데이트 (VWAP: exit_price, Last: exit_price_last 함께 기록)
            cur.execute("""
                UPDATE positions
                SET closed=1,
                    exit_time=%s,
                    exit_price=%s,
                    exit_price_last=%s,
                    fee_open=%s,
                    fee_close=%s,
                    fee_total=%s,
                    pnl_gross=%s,
                    pnl_net=%s,
                    duration_sec=TIMESTAMPDIFF(SECOND, entry_time, %s)
                WHERE id=%s
            """, (
                exit_time_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                (None if exit_avg  is None else round(exit_avg,  6)),
                (None if exit_last is None else round(exit_last, 6)),
                fee_open, fee_close, fee_total,
                pnl_gross, pnl_net,
                exit_time_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                pid,
            ))

# 이번 체결분(delta)을 positions에 누적 업서트:
#   - entry_qty += delta_qty
#   - entry_value += delta_value
#   - entry_price = 가중평균
#   - fee_open += delta_fee_open
def upsert_entry_and_add_fee(*,
    account_id: int, session_id: int|None,
    symbol: str, strategy: str, side: str,
    order_link_id: str, parent_order_link_id: str|None = None,
    entry_price: float, entry_qty: float,
    leverage: float|None,
    tp_partition: int|None, sl_price: float|None,
    delta_fee_open: float,
    entry_time_utc: datetime
):
    delta_value = float(entry_price) * float(entry_qty)
    sql = """
    INSERT INTO positions
      (account_id, session_id, symbol, strategy, side,
       order_link_id, parent_order_link_id, entry_time, entry_price, entry_qty, entry_value,
       leverage, tp_partition, sl_price, fee_open, closed)
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
    ON DUPLICATE KEY UPDATE
      parent_order_link_id = COALESCE(parent_order_link_id, VALUES(parent_order_link_id)),
      entry_time = LEAST(entry_time, VALUES(entry_time)),
      -- 1) 누적 먼저
      entry_qty   = entry_qty   + VALUES(entry_qty),
      entry_value = entry_value + VALUES(entry_value),
      fee_open = COALESCE(fee_open, 0) + VALUES(fee_open),
      -- 2) 평균가는 누적된 값으로 계산(좌→우 안전)
      entry_price = CASE
          WHEN entry_qty > 0 THEN entry_value / entry_qty
          ELSE entry_price
      END,
      -- 3) 보조 필드
      leverage     = COALESCE(VALUES(leverage), leverage),
      tp_partition = COALESCE(VALUES(tp_partition), tp_partition),
      sl_price     = COALESCE(VALUES(sl_price), sl_price);
    """
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (
                account_id, session_id, symbol, strategy, side,
                order_link_id, parent_order_link_id,
                entry_time_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                entry_price, entry_qty, delta_value,
                leverage, tp_partition, sl_price, float(delta_fee_open)
            ))
