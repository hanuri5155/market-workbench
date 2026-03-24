CREATE_ZONE_PROJECTION_SQL = """
CREATE TABLE IF NOT EXISTS zone_projection (
  symbol VARCHAR(20) NOT NULL,
  interval_min INT NOT NULL,
  start_time DATETIME(3) NOT NULL,
  end_time DATETIME(3) NULL,
  side VARCHAR(8) NOT NULL,
  base_entry DECIMAL(18, 6) NOT NULL,
  base_sl DECIMAL(18, 6) NOT NULL,
  entry_override DECIMAL(18, 6) NULL,
  render_entry DECIMAL(18, 6) NOT NULL,
  render_sl DECIMAL(18, 6) NOT NULL,
  render_upper DECIMAL(18, 6) NOT NULL,
  render_lower DECIMAL(18, 6) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 0,
  is_broken TINYINT(1) NOT NULL DEFAULT 0,
  last_updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (symbol, interval_min, start_time, side),
  KEY ix_zone_projection_overlap_start (symbol, interval_min, start_time),
  KEY ix_zone_projection_overlap_end (symbol, interval_min, end_time),
  KEY ix_zone_projection_active (symbol, interval_min, is_active, is_broken)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

SEED_MISSING_ZONE_PROJECTION_SQL = """
INSERT INTO zone_projection (
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
SELECT
  s.symbol,
  s.interval_min,
  s.start_time,
  s.end_time,
  CASE
    WHEN UPPER(COALESCE(s.side, 'LONG')) = 'SHORT' THEN 'SHORT'
    ELSE 'LONG'
  END AS side,
  s.base_entry,
  s.base_sl,
  s.entry_override,
  CASE
    WHEN s.entry_override IS NULL THEN s.base_entry
    ELSE s.entry_override
  END AS render_entry,
  s.base_sl AS render_sl,
  CASE
    WHEN s.entry_override IS NULL THEN GREATEST(s.base_entry, s.base_sl)
    WHEN UPPER(COALESCE(s.side, 'LONG')) = 'SHORT' THEN s.base_sl
    ELSE s.entry_override
  END AS render_upper,
  CASE
    WHEN s.entry_override IS NULL THEN LEAST(s.base_entry, s.base_sl)
    WHEN UPPER(COALESCE(s.side, 'LONG')) = 'SHORT' THEN s.entry_override
    ELSE s.base_sl
  END AS render_lower,
  CASE
    WHEN s.end_time IS NOT NULL THEN 0
    ELSE s.is_active
  END AS is_active,
  CASE
    WHEN s.end_time IS NOT NULL THEN 1
    ELSE 0
  END AS is_broken,
  CURRENT_TIMESTAMP(3) AS last_updated_at
FROM zone_state s
LEFT JOIN zone_projection p
  ON p.symbol = s.symbol
 AND p.interval_min = s.interval_min
 AND p.start_time = s.start_time
 AND p.side = CASE
   WHEN UPPER(COALESCE(s.side, 'LONG')) = 'SHORT' THEN 'SHORT'
   ELSE 'LONG'
 END
WHERE p.symbol IS NULL
"""

def ensure_zone_projection_table(connection) -> None:
    connection.exec_driver_sql(CREATE_ZONE_PROJECTION_SQL)


def seed_missing_zone_projection_rows(connection) -> None:
    connection.exec_driver_sql(SEED_MISSING_ZONE_PROJECTION_SQL)
