## backend/app/db/crud.py

from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import datetime, timedelta, timezone
from . import models, schemas

# 타임존 포함된 UTC 현재 시각
def _now() -> datetime:
    return datetime.now(timezone.utc)

def get_summary(db: Session):
    cutoff = datetime.utcnow() - timedelta(hours=24)
    # 실현손익(닫힘) + 오픈포지션 부분실현/펀딩(누적)
    realized_closed = db.query(func.coalesce(func.sum(models.Position.pnl_net), 0)).filter(models.Position.closed == 1).scalar() or 0
    realized_open = db.query(func.coalesce(func.sum(models.Fill.pnl_gross - models.Fill.fee), 0))\
        .join(models.Position, models.Fill.position_id == models.Position.id)\
        .filter(models.Position.closed == 0).scalar() or 0
    equity = float(realized_closed) + float(realized_open)

    pnl24 = db.query(func.coalesce(func.sum(models.Fill.pnl_gross - models.Fill.fee), 0))\
        .filter(models.Fill.fill_time >= cutoff)\
        .filter(models.Fill.fill_type.in_(["TP","SL","EXIT","FUNDING"]))\
        .scalar() or 0.0

    positions_open = db.query(models.Position).filter(models.Position.closed == 0).count()

    return {
        "equity": float(equity),
        "pnl24h": float(pnl24),
        "positionsOpen": int(positions_open),
    }

def list_positions(db: Session):
    return db.query(models.Position).order_by(models.Position.entry_time.desc()).all()

def list_equity(db: Session, start: datetime | None, end: datetime | None):
    q = db.query(models.Fill.fill_time, (models.Fill.pnl_gross - models.Fill.fee).label("net"))\
        .filter(models.Fill.fill_type.in_(["TP","SL","EXIT","FUNDING"]))
    if start:
        q = q.filter(models.Fill.fill_time >= start)
    if end:
        q = q.filter(models.Fill.fill_time <= end)
    q = q.order_by(models.Fill.fill_time.asc())
    points = []
    cum = 0.0
    for t, net in q.all():
        cum += float(net or 0)
        points.append({"t": t, "equity": cum})
    return points

# 특정 심볼 + 타임프레임의 Structure Zone 상태 조회
#
# zone_state에 원본+상태가 함께 존재
# - row 수가 커질 수 있으므로 프론트/WS 동기화에서는
#   "상태가 있는 것"만 반환
#   (is_active=1 이거나 entry_override 가 있는 row)
def list_zone_state(db: Session, symbol: str, interval_min: int):
    return (
        db.query(models.ZoneState)
        .filter(
            models.ZoneState.symbol == symbol,
            models.ZoneState.interval_min == interval_min,
        )
        .filter(
            or_(
                models.ZoneState.is_active.is_(True),
                models.ZoneState.entry_override.isnot(None),
            )
        )
        .all()
    )
# 특정 심볼 + TF의 Structure Zone(원본+상태 통합 테이블) 조회
def list_zones(
    db: Session,
    symbol: str,
    interval_min: int,
    start_time_from: datetime | None = None,
    start_time_to: datetime | None = None,
):
    q = (
        db.query(models.ZoneState)
        .filter(
            models.ZoneState.symbol == symbol,
            models.ZoneState.interval_min == interval_min,
        )
    )

    if start_time_from is not None:
        q = q.filter(models.ZoneState.start_time >= start_time_from)
    if start_time_to is not None:
        q = q.filter(models.ZoneState.start_time <= start_time_to)

    # 차트/알림용으로는 시간 오름차순이 편해서 정렬까지 해 둠
    return q.order_by(models.ZoneState.start_time.asc()).all()
# 통합 테이블(zone_state)에서 프론트 zone 형식으로 반환
#
# - entry_override 가 있으면 upper/lower 재계산 규칙은 기존과 동일
# - isBroken 은 end_time != NULL 로 판정(기존 is_broken 컬럼 제거)
def list_zones_with_state(
    db: Session,
    symbol: str,
    interval_min: int,
    start_time_from: datetime | None = None,
    start_time_to: datetime | None = None,
):

    boxes = list_zones(
        db,
        symbol=symbol,
        interval_min=interval_min,
        start_time_from=start_time_from,
        start_time_to=start_time_to,
    )

    # datetime(naive UTC) -> epoch ms 변환
    from datetime import timezone as _timezone

    def _dt_to_ms(dt: datetime) -> int:
        return int(round(dt.replace(tzinfo=_timezone.utc).timestamp() * 1000))

    out = []
    tf_str = str(interval_min)

    for b in boxes:
        side_up = (b.side or "").upper()
        if side_up not in ("LONG", "SHORT"):
            side_up = "LONG"

        # base 값(원본 박스)
        base_entry = float(b.base_entry)
        base_sl = float(b.base_sl)
        base_upper = float(b.base_upper)
        base_lower = float(b.base_lower)

        # 상태(state)
        is_active = bool(b.is_active)
        entry_override = float(b.entry_override) if (b.entry_override is not None) else None

        #  프론트 applyStateToZones()와 동일한 규칙
        if entry_override is None:
            entry = base_entry
            sl = base_sl
            upper = base_upper
            lower = base_lower
        else:
            entry = entry_override
            sl = base_sl
            if side_up == "LONG":
                upper = entry
                lower = sl
            else:
                upper = sl
                lower = entry

        start_ms = _dt_to_ms(b.start_time)
        end_ms = _dt_to_ms(b.end_time) if b.end_time is not None else None

        #  기존 id 스타일 유지 (tf-startTs-sign)
        sign = 1 if side_up == "LONG" else -1
        box_id = f"{tf_str}-{start_ms}-{sign}"

        #  isBroken: end_time != NULL
        is_broken = b.end_time is not None

        out.append(
            {
                "id": box_id,
                "symbol": b.symbol,
                "intervalMin": int(b.interval_min),
                "tf": tf_str,
                "side": side_up,
                "startTs": start_ms,
                "endTs": end_ms,
                "entry": float(entry),
                "sl": float(sl),
                "upper": float(upper),
                "lower": float(lower),
                "isBroken": bool(is_broken),
                "isActive": bool(is_active),
                "baseEntry": base_entry,
                "baseSl": base_sl,
                "baseUpper": base_upper,
                "baseLower": base_lower,
                "entryOverride": entry_override,
            }
        )

    return out
# (symbol, interval_min, start_time, side) 를 키로 상태 업데이트
#     
def upsert_zone_state(
    db: Session,
    payload: "schemas.ZoneStateBase",
):

    side_up = (payload.side or "").strip().upper()
    if side_up not in ("LONG", "SHORT"):
        side_up = "LONG"

    #  start_time: UTC naive 로 정규화 (DB 저장 규칙과 통일)
    start_time = payload.startTime
    if isinstance(start_time, datetime) and start_time.tzinfo is not None:
        start_time = start_time.astimezone(timezone.utc).replace(tzinfo=None)

    q = (
        db.query(models.ZoneState)
        .filter(
            models.ZoneState.symbol == payload.symbol,
            models.ZoneState.interval_min == payload.intervalMin,
            models.ZoneState.start_time == start_time,
            models.ZoneState.side == side_up,
        )
    )
    row = q.one_or_none()

    if row is None:
        raise ValueError(
            f"zone_state row not found: symbol={payload.symbol} interval={payload.intervalMin} start={start_time} side={side_up}"
        )

    #  혹시 기존 row가 'Long'/'Short'로 남아있으면 여기서 대문자로 정규화
    row.side = side_up

    # 항상 is_active 는 토글 값으로 갱신
    row.is_active = payload.isActive

    # entryOverride 부분 업데이트 로직은 기존 그대로 유지
    field_set = getattr(payload, "__fields_set__", None)

    if field_set is not None:
        if "entryOverride" in field_set:
            row.entry_override = payload.entryOverride
    else:
        row.entry_override = payload.entryOverride

    db.commit()
    db.refresh(row)
    return row
# (참고/유지용) Structure Zone 원본 upsert
def bulk_upsert_zones(
    db: Session,
    boxes: list["schemas.ZoneBase"],
) -> int:
    if not boxes:
        return 0

    count = 0
    for b in boxes:
        row = (
            db.query(models.ZoneState)
            .filter(
                models.ZoneState.symbol == b.symbol,
                models.ZoneState.interval_min == b.intervalMin,
                models.ZoneState.start_time == b.startTime,
                models.ZoneState.side == b.side,
            )
            .one_or_none()
        )

        if row is None:
            row = models.ZoneState(
                symbol=b.symbol,
                interval_min=b.intervalMin,
                start_time=b.startTime,
                end_time=b.endTime,
                side=b.side,
                base_entry=b.baseEntry,
                base_sl=b.baseSl,
                base_upper=b.baseUpper,
                base_lower=b.baseLower,
                is_active=False,
                entry_override=None,
            )
            db.add(row)
        else:
            row.end_time = b.endTime
            row.base_entry = b.baseEntry
            row.base_sl = b.baseSl
            row.base_upper = b.baseUpper
            row.base_lower = b.baseLower

        count += 1

    db.commit()
    return count


#  Strategy Flags (strategy_flags) 

# strategy_flags에서 주어진 key 목록을 조회해서 {key: bool_value} 형태로 반환
# DB에 row가 없으면 map 제외
def get_strategy_flags_map(db: Session, keys: list[str]) -> dict[str, bool | None]:
    if not keys:
        return {}

    rows = (
        db.query(models.StrategyFlag)
        .filter(models.StrategyFlag.key.in_(keys))
        .all()
    )
    return {row.key: row.bool_value for row in rows}


# StrategyFlag.bool_value upsert (존재하면 업데이트, 없으면 insert)
def set_strategy_flag_bool(db: Session, key: str, value: bool) -> None:
    now = _now()
    row = (
        db.query(models.StrategyFlag)
        .filter(models.StrategyFlag.key == key)
        .one_or_none()
    )

    if row is None:
        row = models.StrategyFlag(
            key=key,
            bool_value=value,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.bool_value = value
        row.updated_at = now

    db.commit()
