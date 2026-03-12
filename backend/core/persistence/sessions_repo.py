# 봇 프로세스 실행 단위를 sessions 테이블에 기록하는 저장소

from core.persistence.mysql_conn import _conn

# 봇 프로세스가 시작될 때 sessions 행 1개 생성
#
# 반환값은 이후 종료 시각 업데이트에 쓰는 sessions.id
# mode는 'live' 또는 'simulation'
def start_session(account_id: int, mode: str, config_snapshot: dict|None) -> int:
    snap = None
    if config_snapshot is not None:
        import json as _json
        snap = _json.dumps(config_snapshot, ensure_ascii=False)
    sql = """
    INSERT INTO sessions (account_id, mode, config_snapshot, started_at)
    VALUES (%s, %s, %s, NOW(3))
    """
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (account_id, mode, snap))
            return cur.lastrowid

# 종료 직전에 같은 sessions 행의 ended_at만 기록
def end_session(session_id: int):
    sql = "UPDATE sessions SET ended_at = NOW(3) WHERE id=%s"
    with _conn() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, (session_id,))
