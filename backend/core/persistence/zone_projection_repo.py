from __future__ import annotations

from datetime import datetime

from core.persistence.mysql_conn import _conn
from core.persistence.zone_projection import (
    build_zone_projection_payload,
    normalize_zone_side,
)


UPSERT_PROJECTION_SQL = """
INSERT INTO zone_projection
  (
    symbol,
    interval_min,
    start_time,
    end_time,
    side,
    base_entry,
    base_sl,
    entry_override,
    render_entry,
    render_sl,
    render_upper,
    render_lower,
    is_active,
    is_broken,
    last_updated_at
  )
VALUES
  (
    %(symbol)s,
    %(interval_min)s,
    %(start_time)s,
    %(end_time)s,
    %(side)s,
    %(base_entry)s,
    %(base_sl)s,
    %(entry_override)s,
    %(render_entry)s,
    %(render_sl)s,
    %(render_upper)s,
    %(render_lower)s,
    %(is_active)s,
    %(is_broken)s,
    %(last_updated_at)s
  )
ON DUPLICATE KEY UPDATE
  end_time = VALUES(end_time),
  base_entry = VALUES(base_entry),
  base_sl = VALUES(base_sl),
  entry_override = VALUES(entry_override),
  render_entry = VALUES(render_entry),
  render_sl = VALUES(render_sl),
  render_upper = VALUES(render_upper),
  render_lower = VALUES(render_lower),
  is_active = VALUES(is_active),
  is_broken = VALUES(is_broken),
  last_updated_at = VALUES(last_updated_at)
"""

SELECT_CANONICAL_ZONE_SQL = """
SELECT
  symbol,
  interval_min,
  start_time,
  end_time,
  side,
  base_entry,
  base_sl,
  entry_override,
  is_active
FROM zone_state
WHERE symbol = %s
  AND interval_min = %s
  AND start_time = %s
  AND side = %s
LIMIT 1
"""


def sync_zone_projection_by_key(
    *,
    symbol: str,
    interval_min: int,
    start_dt: datetime,
    side: str,
    cx=None,
) -> None:
    side_up = normalize_zone_side(side)

    def _sync(cur) -> None:
        cur.execute(
            SELECT_CANONICAL_ZONE_SQL,
            (symbol, int(interval_min), start_dt, side_up),
        )
        row = cur.fetchone()
        if not row:
            return

        payload = build_zone_projection_payload(
            symbol=row["symbol"],
            interval_min=int(row["interval_min"]),
            start_time=row["start_time"],
            end_time=row.get("end_time"),
            side=row.get("side"),
            base_entry=float(row["base_entry"]),
            base_sl=float(row["base_sl"]),
            entry_override=row.get("entry_override"),
            is_active=bool(row.get("is_active")),
        )
        cur.execute(UPSERT_PROJECTION_SQL, payload)

    if cx is None:
        with _conn() as local_cx:
            with local_cx.cursor() as cur:
                _sync(cur)
    else:
        with cx.cursor() as cur:
            _sync(cur)


def sync_zone_projection_for_broken_rows(
    *,
    symbol: str,
    interval_min: int,
    rows: list[dict],
    cx=None,
) -> None:
    if not rows:
        return

    if cx is None:
        with _conn() as local_cx:
            for row in rows:
                start_dt = row.get("start_time")
                if not isinstance(start_dt, datetime):
                    continue
                sync_zone_projection_by_key(
                    symbol=symbol,
                    interval_min=interval_min,
                    start_dt=start_dt,
                    side=row.get("side"),
                    cx=local_cx,
                )
    else:
        for row in rows:
            start_dt = row.get("start_time")
            if not isinstance(start_dt, datetime):
                continue
            sync_zone_projection_by_key(
                symbol=symbol,
                interval_min=interval_min,
                start_dt=start_dt,
                side=row.get("side"),
                cx=cx,
            )
