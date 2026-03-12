## backend/core/utils/file_utils.py

import os, json, tempfile

# 동일 디렉터리에 임시파일을 만들고, flush/fsync 후 os.replace로 교체
# - os.replace는 같은 파일시스템 내에서 '원자적' 교체 보장 (Win/Unix 공통)
def write_json_atomic(path: str, data, *, indent: int = 2) -> None:
    dirpath = os.path.dirname(path or "")
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

    # 같은 디렉터리 내에 임시파일 생성
    fd, tmppath = tempfile.mkstemp(prefix=".tmp_", dir=(dirpath or "."))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        # 원자적 교체
        os.replace(tmppath, path)
    except Exception:
        # 실패 시 임시파일 제거 시도
        try:
            os.remove(tmppath)
        except Exception:
            pass
        raise
