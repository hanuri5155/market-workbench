## backend/core/trading/funding_utils.py

import os, time, asyncio, requests, json 
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from core.utils.log_utils import funding_log, write_funding_snapshot
from core.state import shared_state
from core.utils.file_utils import write_json_atomic 

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
POLL_SEC = int(os.getenv("FUNDING_POLL_SEC", "60"))
FETCH_TAKER = os.getenv("FUNDING_FETCH_TAKER", "1") == "1"
TAKER_LIMIT = int(os.getenv("FUNDING_TAKER_LIMIT", "1000"))

#  비교/히스토리 파일 경로
PREV_PATH = os.getenv("FUNDING_PREV_PATH", "logs/funding_prev.json")
HISTORY_PATH = os.getenv("FUNDING_HISTORY_PATH", "logs/funding_history.jsonl")

#  집계(roll-up) 설정
ROLLUP_MINUTES = [int(x) for x in os.getenv("FUNDING_ROLLUP_MINUTES", "5,15,30,60").split(",") if x.strip()]
ROLLUP_PREFIX  = os.getenv("FUNDING_ROLLUP_PREFIX", "logs/funding_rollup")  # logs/funding_rollup_5m.jsonl ...
TICK_LOG       = os.getenv("FUNDING_TICK_LOG", "0") == "1"  # 초당 로그(요약+해석) 출력 여부(기본 끔)

# 의미있는 변화 판단 임계(집계 해석에도 활용)
FUNDING_MIN_BP = float(os.getenv("FUNDING_MIN_BP", "0.5"))   # 0.5bp
BASIS_MIN_PP   = float(os.getenv("BASIS_MIN_PP", "0.02"))    # 0.02%p
OI_MIN_USD     = float(os.getenv("OI_MIN_USD", "10000000"))  # 1천만 달러
TAKER_MIN_PP   = float(os.getenv("TAKER_MIN_PP", "1.0"))     # 1.0%p

# 내부 상태: 윈도우별 집계 버킷
_rollups = {}  # {window_min: state}

# 세션 + 리트라이 세팅
_RETRY = Retry(
    total=3, backoff_factor=0.3,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=_RETRY, pool_connections=4, pool_maxsize=8))
_session.headers.update({"User-Agent": "MarketWorkbench/1.0"})

def _who_pays(funding_rate: float) -> str:
    # > 0: Long pays Short / < 0: Short pays Long
    if funding_rate > 0:
        return "Long pays, Short receives"
    elif funding_rate < 0:
        return "Short pays, Long receives"
    else:
        return "Zero (no one pays)"

def _fetch_ticker():
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category":"linear", "symbol": SYMBOL}
    r = _session.get(url, params=params, timeout=(2, 3))  # (connect, read)
    r.raise_for_status()
    data = r.json()
    result = (data.get("result") or {}).get("list") or []
    return result[0] if result else None

# 최근 체결을 집계해 테이커 매수 비율(%)을 계산
# - Bybit v5 recent-trade: /v5/market/recent-trade  (선형: category=linear)
# - 필드명은 'side'('Buy'/'Sell') 또는 'isBuyerMaker'로 제공될 수 있음
#   * 일반적으로 'side'는 '테이커 방향'으로 해석됨
def _taker_buy_ratio(limit: int = 500) -> float | None:
    try:
        url = "https://api.bybit.com/v5/market/recent-trade"
        params = {"category":"linear", "symbol": SYMBOL, "limit": str(max(1, min(limit, 1000)))}
        r = _session.get(url, params=params, timeout=(2, 3))
        r.raise_for_status()
        data = r.json()
        trades = ((data.get("result") or {}).get("list")) or []
        if not trades:
            return None

        buy_qty = 0.0
        sell_qty = 0.0
        for t in trades:
            # 수량 키가 'qty' 또는 'size'로 오는 경우가 있으므로 둘 다 시도
            q = t.get("qty") or t.get("size") or t.get("v") or 0
            try:
                q = float(q)
            except Exception:
                q = 0.0

            side = (t.get("side") or "").strip().lower()  # 'buy' / 'sell' 가정
            if side == "buy":
                buy_qty += q
            elif side == "sell":
                sell_qty += q
            else:
                # side가 없으면 isBuyerMaker로 테이커 방향을 결정:
                # buyer가 maker면 seller가 taker → 'sell' 쪽 테이커
                ibm = t.get("isBuyerMaker")
                if ibm is not None:
                    is_buyer_maker = str(ibm).lower() in ("true", "1")
                    if is_buyer_maker:
                        sell_qty += q
                    else:
                        buy_qty += q

        tot = buy_qty + sell_qty
        return round((buy_qty / tot) * 100.0, 2) if tot > 0 else None
    except Exception:
        return None
    
def _safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _append_jsonl(path: str, obj: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _fmt_money_delta(v: float) -> str:
    if v > 0:  return f"↑${abs(v):,.0f}"
    if v < 0:  return f"↓${abs(v):,.0f}"
    return f"→$0"

# prev 스냅샷과 curr 스냅샷을 비교해 한글 분석 1줄 생성
# - F변화: bp 단위
# - Basis변화: pp(percentage point) 단위
# - OI변화: 달러
# - TakerBuy변화: pp
# - 간단 분류: '신규 롱 유입', '숏 커버링', '신규 숏 유입', '롱 청산/약세 잔류', '중립'
def _make_analysis_ko(prev: dict | None, curr: dict) -> str:
    if not prev:
        return "직전 데이터 없음 — 비교 분석은 다음 스냅샷부터 제공됩니다."

    # 값 꺼내기
    fr_now = float(curr.get("fundingRate") or 0.0)
    fr_prev = float(prev.get("fundingRate") or 0.0)
    basis_now = curr.get("basisPct")
    basis_prev = prev.get("basisPct")
    oi_now = float(curr.get("openInterestValue") or 0.0)
    oi_prev = float(prev.get("openInterestValue") or 0.0)
    tk_now = curr.get("takerBuyRatio")
    tk_prev = prev.get("takerBuyRatio")

    # 변화 계산
    d_fr_bp = (fr_now - fr_prev) * 10000.0
    d_basis_pp = (basis_now - basis_prev) if (basis_now is not None and basis_prev is not None) else None
    d_oi = oi_now - oi_prev
    d_tk_pp = (tk_now - tk_prev) if (tk_now is not None and tk_prev is not None) else None

    # 변화 문자열
    parts = [
        f"F {d_fr_bp:+.1f}bp",
        (f"Basis {d_basis_pp:+.3f}pp" if d_basis_pp is not None else "Basis n/a"),
        f"OI {_fmt_money_delta(d_oi)}",
        (f"TakerBuy {d_tk_pp:+.2f}pp" if d_tk_pp is not None else "TakerBuy n/a"),
    ]
    change_txt = "직전 대비: " + ", ".join(parts)

    # 간단 분류 규칙
    classification = "중립"
    if tk_now is not None:
        # 임계치
        strong_buy = tk_now >= 55.0
        strong_sell = tk_now <= 45.0
        oi_up = d_oi > 0
        basis_up = (d_basis_pp is not None and d_basis_pp > 0)
        basis_down = (d_basis_pp is not None and d_basis_pp < 0)

        if strong_buy and oi_up and (basis_now is not None) and (basis_prev is None or basis_now >= basis_prev):
            classification = "매수 우위 강화(신규 롱 유입)"
        elif strong_buy and not oi_up:
            classification = "숏 커버링/완만 반등"
        elif strong_sell and oi_up and ((basis_now is not None) and (basis_prev is None or basis_now <= basis_prev)):
            classification = "매도 압력 강화(신규 숏 유입)"
        elif strong_sell and not oi_up:
            classification = "롱 청산/약세 잔류"

        # 신호 상충 보정(펀딩과 베이시스 방향이 자주 엇갈리면 중립으로 완화)
        if d_basis_pp is not None and abs(d_basis_pp) < 0.01 and abs(d_fr_bp) < 1.0:
            classification = "중립(미세 변화)"

    return f"{change_txt} → 평가: {classification}."


# ----------------------------
# 집계(roll-up) 유틸리티
# ----------------------------
def _window_ms(mins: int) -> int:
    return mins * 60 * 1000

def _bucket_start_ms(ts_ms: int, mins: int) -> int:
    wm = _window_ms(mins)
    return (ts_ms // wm) * wm  # 정시 정렬(예: 0,5,10,...분)

def _rollup_path(mins: int) -> str:
    return f"{ROLLUP_PREFIX}_{mins}m.jsonl"

def _rollup_state_init(mins: int, bucket_start: int, snap: dict) -> dict:
    basis = snap.get("basisPct")
    oi_val = float(snap.get("openInterestValue") or 0.0)
    tk = snap.get("takerBuyRatio")
    return {
        "mins": mins,
        "bucket_start": bucket_start,
        "count": 0,
        "sum_fr": 0.0,             # fundingRate(소수; 0.0001=0.01%)
        "sum_basis": 0.0, "cnt_basis": 0,
        "sum_tk": 0.0, "cnt_tk": 0,
        "basis_min": basis, "basis_max": basis,
        "first_oi": oi_val, "last_oi": oi_val,
        "first": None, "last": None
    }

def _rollup_accumulate(state: dict, snap: dict):
    state["count"] += 1
    state["sum_fr"] += float(snap.get("fundingRate") or 0.0)
    basis = snap.get("basisPct")
    if basis is not None:
        state["sum_basis"] += float(basis)
        state["cnt_basis"] += 1
        if state["basis_min"] is None or basis < state["basis_min"]:
            state["basis_min"] = basis
        if state["basis_max"] is None or basis > state["basis_max"]:
            state["basis_max"] = basis
    tk = snap.get("takerBuyRatio")
    if tk is not None:
        state["sum_tk"] += float(tk)
        state["cnt_tk"] += 1
    oi_val = float(snap.get("openInterestValue") or 0.0)
    if state["first"] is None:
        state["first"] = snap
        state["first_oi"] = oi_val
    state["last"] = snap
    state["last_oi"] = oi_val

def _fmt_money_delta(v: float) -> str:
    if v > 0:  return f"↑${abs(v):,.0f}"
    if v < 0:  return f"↓${abs(v):,.0f}"
    return f"→$0"

def _classify_rollup(avg_tk: float | None, oi_delta: float, avg_basis: float | None) -> str:
    # 간단 규칙: 테이커 평균 55/45 기준 + OIΔ 부호 + 평균 베이시스 방향
    if avg_tk is None:
        # 테이커 정보가 없으면 OI와 베이시스로만 약식
        if oi_delta > OI_MIN_USD and (avg_basis is not None and avg_basis >= 0):
            return "매수 유입 가능(테이커 정보 없음)"
        if oi_delta > OI_MIN_USD and (avg_basis is not None and avg_basis < 0):
            return "숏 유입 가능(테이커 정보 없음)"
        if oi_delta < -OI_MIN_USD:
            return "청산/규모 축소 지속"
        return "중립"
    strong_buy  = avg_tk >= 55.0
    strong_sell = avg_tk <= 45.0
    if strong_buy and oi_delta > 0 and (avg_basis is None or avg_basis >= 0):
        return "매수 우위 강화(신규 롱 유입)"
    if strong_buy and oi_delta <= 0:
        return "숏 커버링/완만 반등"
    if strong_sell and oi_delta > 0 and (avg_basis is None or avg_basis <= 0):
        return "매도 압력 강화(신규 숏 유입)"
    if strong_sell and oi_delta <= 0:
        return "롱 청산/약세 잔류"
    return "중립"

def _emit_rollup_and_log(state: dict):
    mins = state["mins"]
    bs = state["bucket_start"]
    be = bs + _window_ms(mins) - 1
    cnt = max(1, state["count"])
    avg_fr = state["sum_fr"] / cnt                      # 소수 (예: 0.0001)
    avg_fr_pct = avg_fr * 100.0
    avg_basis = (state["sum_basis"] / state["cnt_basis"]) if state["cnt_basis"] > 0 else None
    avg_tk = (state["sum_tk"] / state["cnt_tk"]) if state["cnt_tk"] > 0 else None
    oi_delta = (state["last_oi"] - state["first_oi"])

    basis_min = state["basis_min"]
    basis_max = state["basis_max"]

    # 요약 1줄
    basis_avg_txt = f"{avg_basis:+.3f}%" if avg_basis is not None else "n/a"
    basis_range_txt = (f"[{basis_min:+.3f}%,{basis_max:+.3f}%]" 
                       if (basis_min is not None and basis_max is not None) else "[n/a]")
    tk_avg_txt = f"{avg_tk:.2f}%" if avg_tk is not None else "n/a"
    summary = (f"[{mins}m] F_avg={avg_fr_pct:+.4f}% | Basis_avg={basis_avg_txt} {basis_range_txt} | "
               f"OIΔ={_fmt_money_delta(oi_delta)} | TakerBuy_avg={tk_avg_txt} | N={cnt}")

    # 해석 1줄
    verdict = _classify_rollup(avg_tk, oi_delta, avg_basis)
    analysis_ko = (f"[{mins}m] 평가: {verdict} — 평균 F {avg_fr_pct:+.4f}%, "
                   f"평균 Basis {basis_avg_txt}, OIΔ {_fmt_money_delta(oi_delta)}, "
                   f"TakerBuy_avg {tk_avg_txt}, 샘플 {cnt}개.")

    # JSONL로 저장
    item = {
        "windowMin": mins,
        "bucketStart": bs,
        "bucketEnd": be,
        "summary": summary,
        "analysis_ko": analysis_ko,
        "avgFundingRate": avg_fr,
        "avgBasisPct": avg_basis,
        "basisMin": basis_min,
        "basisMax": basis_max,
        "avgTakerBuy": avg_tk,
        "oiDelta": oi_delta,
        "count": cnt
    }
    _append_jsonl(_rollup_path(mins), item)

    # 로그 2줄 출력
    funding_log(f"⏱️ {summary}\n🧭 {analysis_ko}")

def _rollup_update_and_maybe_emit(snapshot: dict):
    ts = int(snapshot.get("serverTime") or time.time()*1000)
    for mins in ROLLUP_MINUTES:
        bs = _bucket_start_ms(ts, mins)
        st = _rollups.get(mins)
        if (st is None) or (bs != st["bucket_start"]):
            # 기존 버킷을 마감(있으면)
            if st is not None and st["count"] > 0:
                _emit_rollup_and_log(st)
            # 새 버킷 시작
            st = _rollup_state_init(mins, bs, snapshot)
            _rollups[mins] = st
        # 현재 스냅샷 누적
        _rollup_accumulate(st, snapshot)

async def start_funding_snapshot_poller(interval_sec: int = 60):
    while True:
        try:
            # 동기 I/O를 스레드로 넘겨 이벤트루프 차단 최소화
            t = await asyncio.to_thread(_fetch_ticker)
            if t:
                fr = float(t.get("fundingRate") or 0.0)
                next_ts = t.get("nextFundingTime")  # ms epoch str or None/""
                who = _who_pays(fr)

                #  베이시스/마크/인덱스/OI 추출 (tickers 응답) 
                idx_price = float(t.get("indexPrice") or 0.0)
                mark_price = float(t.get("markPrice") or 0.0)
                basis_pct = ((mark_price - idx_price) / idx_price * 100.0) if idx_price else None
                oi = float(t.get("openInterest") or 0.0)           # 계약수(코인 단위)
                oi_val = float(t.get("openInterestValue") or 0.0)  # 증거금/USDT 환산값

                #  FETCH_TAKER가 True면 최근 체결로 테이커 매수 비율(%) 계산
                taker_buy_ratio = (
                    await asyncio.to_thread(_taker_buy_ratio, TAKER_LIMIT)
                    if FETCH_TAKER
                    else None
                )

                server_ms = int(time.time() * 1000)
                try:
                    nxt_ms = int(str(next_ts)) if next_ts not in (None, "", "0") else 0
                except Exception:
                    nxt_ms = 0
                eta_sec = max(0, (nxt_ms - server_ms) // 1000) if nxt_ms else None

                #  '요약 한 줄' 문자열 생성
                arrow = "L→S" if fr > 0 else ("S→L" if fr < 0 else "-")
                fr_pct = f"{fr*100:+.4f}%"
                basis_txt = f"{basis_pct:+.3f}%" if basis_pct is not None else "n/a"
                oi_val_txt = f"${oi_val:,.0f}" if oi_val else "n/a"
                taker_txt = (f"{taker_buy_ratio:.2f}%" if taker_buy_ratio is not None else "n/a")
                eta_txt = f"{eta_sec}s" if eta_sec is not None else "n/a"
                summary = f"F={fr_pct} {arrow} | Basis={basis_txt} | OI={oi_val_txt} | TakerBuy={taker_txt} | ETA={eta_txt}"

                snapshot = {
                    "symbol": SYMBOL,
                    "fundingRate": fr,
                    "indexPrice": idx_price,
                    "markPrice": mark_price,
                    "basisPct": round(basis_pct, 4) if basis_pct is not None else None,
                    "openInterest": oi,
                    "openInterestValue": oi_val,
                    "takerBuyRatio": taker_buy_ratio,
                    "nextFundingTime": next_ts,
                    "nextFundingEtaSec": eta_sec,
                    "expected": who,
                    "summary": summary,
                    "serverTime": server_ms,
                    "updatedAt": server_ms
                }
                
                #  직전 스냅샷 로드 후 한글 분석 생성
                prev_snapshot = await asyncio.to_thread(_safe_load_json, PREV_PATH)
                analysis_ko = _make_analysis_ko(prev_snapshot, snapshot)
                snapshot["analysis_ko"] = analysis_ko

                # 메모리/디스크 기록
                shared_state.funding_snapshot = snapshot
                await asyncio.to_thread(write_funding_snapshot, snapshot)  # logs/funding_snapshot.json 덮어쓰기

                # 히스토리 누적(JSONL) 및 prev 교체
                hist_item = {
                    "ts": server_ms,
                    "summary": summary,
                    "analysis_ko": analysis_ko,
                    "fundingRate": fr,
                    "basisPct": snapshot["basisPct"],
                    "openInterestValue": oi_val,
                    "takerBuyRatio": taker_buy_ratio
                }
                await asyncio.to_thread(_append_jsonl, HISTORY_PATH, hist_item)
                os.makedirs(os.path.dirname(PREV_PATH), exist_ok=True)
                await asyncio.to_thread(write_json_atomic, PREV_PATH, snapshot)

                # (옵션) 초당 로그: 시끄러우면 끄기 (FUNDING_TICK_LOG=0)
                if TICK_LOG:
                    await asyncio.to_thread(funding_log, f"📊 {summary}\n📝 {analysis_ko}")

                #  5m/15m 집계 업데이트 및 마감 시 2줄 로그 + JSONL 기록
                await asyncio.to_thread(_rollup_update_and_maybe_emit, snapshot)
        except Exception as e:
            funding_log(f"⚠️ [Funding] snapshot update error: {e}")
        await asyncio.sleep(interval_sec or POLL_SEC)
