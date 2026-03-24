# Bybit 주문 실행 유틸
#
# 실거래 주문과 demo/no-op 주문을 같은 함수 시그니처로 다루기 위함

import datetime
import hashlib
import hmac
import inspect
import json
import os
import time
from decimal import Decimal, ROUND_UP, ROUND_DOWN
from typing import Optional

import requests
from dotenv import load_dotenv

from core.state import shared_state
from core.state.shared_state import latest_price_map, simulated_position
from core.utils.log_utils import log
from core.utils.qty_utils import floor_to_step

load_dotenv()

# 주문 실행에 필요한 환경 설정
BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_SECRET_KEY")
RECV_WINDOW = os.getenv("RECV_WINDOW")
QTY_STEP = Decimal(os.getenv("QTY_STEP", "0.001"))
HTTP_CONNECT_TIMEOUT_SEC = float(os.getenv("BYBIT_HTTP_CONNECT_TIMEOUT_SEC", "2"))
HTTP_READ_TIMEOUT_SEC = float(os.getenv("BYBIT_HTTP_READ_TIMEOUT_SEC", "8"))
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT_SEC, HTTP_READ_TIMEOUT_SEC)
PUBLIC_DEMO_MODE = str(os.getenv("DEMO_MODE", "0")).strip().lower() in ("1", "true", "yes", "on")
ENABLE_LIVE_ORDER_PLACEMENT = str(os.getenv("ENABLE_LIVE_ORDER_PLACEMENT", "0")).strip().lower() in ("1", "true", "yes", "on")

log(f"[BOOT] RECV_WINDOW in use = {RECV_WINDOW}")

def _floor_to_step(q: float) -> float:
    # 거래소 LOT step보다 작은 잔량이 생기지 않게 내림 처리
    return floor_to_step(q, step=QTY_STEP)

def _make_tp_order_link_id(base: str) -> str:
    # TP 시장가 주문도 추적 가능한 고유 orderLinkId를 만들기 위함
    h = hashlib.sha1((base or "").encode()).hexdigest()[:8]
    ts = str(int(time.time() * 1000))
    oid = f"__tp_mkt__{h}_{ts}"
    return oid

# Bybit V5 GET 요청 서명 생성
def generate_signature(timestamp: str, recv_window: str, params: dict, secret: str) -> str:
    sorted_params = sorted(params.items())
    query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
    origin = f"{timestamp}{BYBIT_API_KEY}{recv_window}{query_string}"
    return hmac.new(secret.encode(), origin.encode(), hashlib.sha256).hexdigest()

# Bybit V5 POST 요청 서명 생성
def generate_post_signature(timestamp: str, recv_window: str, body: dict, secret: str) -> str:
    body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    origin = f"{timestamp}{BYBIT_API_KEY}{recv_window}{body_str}"
    return hmac.new(secret.encode(), origin.encode(), hashlib.sha256).hexdigest()

# 수량을 특정 소수 자릿수 기준으로 올림할 때 사용
def round_up_qty(qty, precision=3):
    factor = Decimal(f"1e-{precision}")
    return float((Decimal(qty).quantize(factor, rounding=ROUND_UP)))

# 주문 가능 잔고를 조회하기 위함
def get_wallet_balance(symbol: str = "USDT") -> float:
    url = f"{BYBIT_BASE_URL}/v5/account/wallet-balance"
    timestamp = str(int(time.time() * 1000))

    params = {
        "accountType": "UNIFIED",  
        "coin": symbol             
    }

    query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign = generate_signature(timestamp, RECV_WINDOW, params, BYBIT_API_SECRET)

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign
    }

    try:
        response = requests.get(f"{url}?{query_string}", headers=headers, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        #log(" 응답:", data)
        if data.get("retCode") == 0:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == symbol:
                    return float(c["walletBalance"])
    except Exception as e:
        log(f"❌ [Order Executor] 잔고 조회 오류: {e}")

    return 0.0

# 현재 열린 포지션 정보를 side까지 포함해 가져오기 위함
def get_open_position_info(symbol: str, side: str):
    url = f"{BYBIT_BASE_URL}/v5/position/list"
    timestamp = str(int(time.time() * 1000))
    
    params = {
        "category": "linear",
        "symbol": symbol
    }

    query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign = generate_signature(timestamp, RECV_WINDOW, params, BYBIT_API_SECRET)

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign
    }

    try:
        response = requests.get(f"{url}?{query_string}", headers=headers, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0:
            # Hedge mode에서는 positionIdx로 롱/숏 슬롯을 구분
            target_idx = 1 if side == "Buy" else 2
            for pos in data["result"]["list"]:
                if (
                    pos["symbol"] == symbol and 
                    int(pos["positionIdx"]) == target_idx and 
                    float(pos.get("size", 0)) > 0
                ):
                    return pos
    except Exception as e:
        log(f"❌ [Order Executor] 포지션 상세 정보 조회 실패: {e}")
    return None

# config에 맞는 레버리지를 주문 직전에 거래소에 맞추기 위함
def _ensure_leverage_from_config(symbol: str):
    cfg = shared_state.current_config or {}

    buy_lev = cfg.get("buy_leverage")
    sell_lev = cfg.get("sell_leverage")

    # 설정값이 없으면 주문 차단 사유로 보지 않음
    if buy_lev is None and sell_lev is None:
        return True

    try:
        url = f"{BYBIT_BASE_URL}/v5/position/set-leverage"
        timestamp = str(int(time.time() * 1000))

        body = {
            "category": "linear",
            "symbol": symbol,
        }
        # Hedge mode에서는 롱/숏 레버리지를 따로 보냄
        if buy_lev is not None:
            body["buyLeverage"] = str(int(float(buy_lev)))
        if sell_lev is not None:
            body["sellLeverage"] = str(int(float(sell_lev)))

        # 서명 문자열과 실제 전송 본문이 달라지지 않게 같은 직렬화를 사용
        body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        origin = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
        sign = hmac.new(BYBIT_API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()

        headers = {
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }

        resp = requests.post(url, headers=headers, data=body_str.encode('utf-8'), timeout=10)
        data = resp.json()
        ret = data.get("retCode")

        if ret == 0:
            log(f"🔧 [Order Executor] 레버리지 설정 완료: buy={body.get('buyLeverage')} sell={body.get('sellLeverage')}")
            return True
        elif ret == 110043:
            # 이미 같은 레버리지면 추가 변경 없이 성공 처리
            log(f"ℹ️ [Order Executor] 레버리지 변경 없음(동일값): {data}")
            return True
        else:
            log(f"⚠️ [Order Executor] set-leverage 실패: {data}")
            return False
    except Exception as e:
        log(f"⚠️ [Order Executor] 레버리지 설정 중 예외(무시): {e}")
        return False

# 실거래와 demo/no-op를 같은 호출 경로로 처리하기 위한 공용 주문 함수
def place_order(
        symbol: str, 
        side: str, 
        qty: float = None, 
        sl: float = None, 
        tp: float = None, 
        close_position: bool = False, 
        order_link_id: str = "",
        close_target_order_link_id: str = "", 
        order_type: str = "Market", 
        price: float = None, 
        reduce_only: bool = False, 
        is_tp_order: bool = False
        ):
    SIMULATION_MODE = (shared_state.current_config or {}).get("simulation_mode", False)
    if SIMULATION_MODE or PUBLIC_DEMO_MODE or not ENABLE_LIVE_ORDER_PLACEMENT:
        mode_label = "SIMULATION" if SIMULATION_MODE else "DEMO"
        log(f"[{mode_label}] live order placement skipped → {side} {symbol} qty={qty} sl={sl} tp={tp}")

        #  가상 포지션 열기
        simulated_position["is_open"] = True
        simulated_position["side"] = side
        simulated_position["entry_price"] = latest_price_map.get(symbol)

        return {
            "orderId": f"{mode_label}-{side}-{symbol}-{int(time.time())}"
        }

    if get_open_position_info(symbol, side) and not close_position:
        log(f"⚠️ [Order Executor] 이미 열린 {side} 포지션이 있어 주문을 생략합니다.")
        return "POSITION_OPEN"

    #  오픈주문 여부 판단 (청산/TP가 아닌 케이스만 '잔고/현재가' 체크)
    is_opening_order = not (close_position or reduce_only or is_tp_order)
    last_price = latest_price_map.get(symbol)
    entry_price = float(last_price) if last_price else 0.0

    if is_opening_order:
        usdt_balance = get_wallet_balance("USDT")
        if usdt_balance <= 0 or entry_price <= 0:
            log("❌ [Order Executor] 주문 중단: 잔고 또는 현재가 조회 실패")
            return None        
        
    #  종료 주문이라면 기존 포지션 크기로 수량 설정
    position_info = None
    if close_position and not is_tp_order:
        # 현재 포지션이 존재하는지 확인
        open_position = get_open_position_info(symbol, "Buy" if side == "Sell" else "Sell")
        if not open_position:
            log(f"⚠️ 종료 주문인데 반대 포지션이 없음 → side={side}")
        else:
            log(f"✅ 반대 포지션이 확인 완료")

        entry_info = shared_state.execution_data_store.get(close_target_order_link_id or "", {})

        if entry_info and not entry_info.get("closed", False):
            qty = float(entry_info.get("entry_size", 0))
            log(f"execution_data_store[{close_target_order_link_id}] qty: {qty}")
            log(f"🔚 [종료 주문] execution_data_store[{close_target_order_link_id}] 기준 수량 사용: {qty} BTC")
        else:
            log("⚠️ [종료 주문] execution_data_store에 유효한 포지션 정보 없음 → 실시간 조회 시도")
            opposite_side = "Sell" if side == "Buy" else "Buy"
            #  종료 수량 계산을 위한 반대 방향 포지션 조회
            opposite_position_info = get_open_position_info(symbol, opposite_side)

            if opposite_position_info:
                qty = float(opposite_position_info.get("size", 0))
                log(f"🔚 [종료 주문] 실시간 조회 기준 수량 사용: {qty} BTC")
            else:
                log("❌ [종료 주문] 포지션 정보를 어디서도 찾을 수 없습니다.")
                return None

        #  종료일 경우에도 positionIdx 설정을 위해 현재 방향 포지션 정보 조회
        position_info = get_open_position_info(symbol, side)

    else:
        #  진입 주문일 경우 position_info 필요 없음
        position_info = None
  
    #  수량 자동계산은 "오픈 주문"이고 qty를 안 넘긴 경우에만 수행
    if qty is None and is_opening_order:
        cfg = (shared_state.current_config or {})
        cfg_val = cfg.get("entry_usd_volume")

        # 값이 없거나 잘못된 타입이면 자동계산 건너뜀(에러 방지)
        try:
            entry_usd_target = float(cfg_val)
        except (TypeError, ValueError):
            entry_usd_target = 0.0
            log("⚠️ [Order Executor] entry_usd_volume 미설정/형식 오류 → 자동 수량 계산 생략")

        if entry_usd_target > 0 and entry_price > 0:
            raw_qty = entry_usd_target / entry_price
            qty = round_up_qty(raw_qty, precision=3)
            

    # 전략명-시간 기반 orderLinkId 생성용
    if not order_link_id:
        caller = inspect.stack()[1].filename

        if "zone_strategy" in caller or "demo_zone" in caller:
            strategy = "zone_strategy"
        else:
            strategy = "manual"

        timestamp_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        if is_tp_order:
            order_link_id = _make_tp_order_link_id(close_target_order_link_id)
        else:
            order_link_id = f"{strategy}-{timestamp_str}"

        # 항상 TP(Market)엔 태그 보장
        if is_tp_order and order_type == "Market" and reduce_only and close_position:
            if "__tp_mkt__" not in (order_link_id or ""):
                order_link_id = _make_tp_order_link_id(close_target_order_link_id)

    #  실매매 + 오픈 주문(청산/TP 아님)일 때 거래소 레버리지 보정
    if (not SIMULATION_MODE) and (not close_position) and (not is_tp_order):
        try:
            ok = _ensure_leverage_from_config(symbol)
            if ok is False:
                log("❌ [Order Executor] 레버리지 설정 실패 → 주문 중단")
                return None
        except Exception as e:
            log(f"⚠️ [Order Executor] 레버리지 보정 중 예외 → 주문 중단: {e}")
            return None

    url = f"{BYBIT_BASE_URL}/v5/order/create"
    timestamp = str(int(time.time() * 1000))
    if close_position:
    #  종료 주문이면 실제 포지션 방향은 반대니까 그걸 기준으로 positionIdx 설정
        actual_position_side = "Buy" if side == "Sell" else "Sell"
        position_info = get_open_position_info(symbol, actual_position_side)
        position_idx = int(position_info.get("positionIdx", 1)) if position_info else (1 if actual_position_side == "Buy" else 2)
    else:
        position_idx = 1 if side == "Buy" else 2

    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "positionIdx": position_idx,
        "orderLinkId": order_link_id,
        "orderType": order_type,
    }
    qty_to_send = None
    if qty is not None:
        q = _floor_to_step(qty)     # ← 안전하게 내림
        if q > 0.0:
            qty_to_send = q
    
    # 전량 종료(closePosition=True)이고 보정 후 q==0이면 qty 생략(잔량이 스텝 미만이어도 거래소가 포지션 전량 종료)
    if qty_to_send is not None:
        body["qty"] = str(Decimal(str(qty_to_send)).quantize(Decimal("1.000"), rounding=ROUND_DOWN))
    elif close_position:
        body["closePosition"] = True  # qty 없이 전량 종료(향후 사용 대비)

    if order_type == "Limit":
        body["timeInForce"] = "GTC"
    if sl is not None:
        body["stopLoss"] = str(sl)
    if tp is not None:
        body["takeProfit"] = str(tp)
    if order_link_id:
        body["orderLinkId"] = order_link_id
    if order_type == "Limit" and price is not None:
        body["price"] = str(price)    
    if reduce_only:
        body["reduceOnly"] = True

    # JSON 직렬화 후 POST 방식 서명
    body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    origin = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
    sign = hmac.new(BYBIT_API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            data=body_str.encode('utf-8'),
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        log("📦 [Order Executor] 주문 응답:", result)

        # 주문이 체결되었는지 실제 포지션을 통해 확인
        if close_position:
            #log(" 종료 주문 요청 완료 (체결 여부 확인 생략)")
            # 종료 주문은 orderId 반환 여부만 확인
            if result.get("retCode") == 0 and result.get("result", {}).get("orderId"):
                return result["result"]
            else:
                log(f"❌ [Order Executor] 종료 주문 응답 이상 → retCode: {result.get('retCode')}, result: {result.get('result')}")
                return None
        
        # 안전하게 응답 검사
        order_result = result.get("result", {})

        if result.get("retCode") == 0 and order_result.get("orderId"):
            return order_result
        else:
            ret_code = result.get("retCode")
            ret_msg = result.get("retMsg")
            ret_ext = result.get("retExtInfo", {})
            log(f"❌ [Order Executor] 주문 응답 이상 → retCode: {ret_code}, retMsg: {ret_msg}, retExtInfo: {ret_ext}, result: {order_result}")
            return None

    except Exception as e:
        log(f"❌ [Order Executor] 주문 요청 오류: {e}")
        return None

#  분할 TP 설정 함수 (Partial 모드)
# Bybit V5: 포지션에 분할 TP를 설정합니다 (tpslMode = Partial)
# - tp_trigger_price: 트리거 가격 (조건부 주문 발동 시점)
# - tp_limit_price: 체결 희망 가격 (지정가 TP용)
# - tp_size: 해당 TP로 청산할 수량
def set_partial_tp(symbol: str, side: str, tp_trigger_price: float, tp_limit_price: float, tp_size: float) -> Optional[str]:
    SIMULATION_MODE = (shared_state.current_config or {}).get("simulation_mode", False)
    if SIMULATION_MODE:
        log(f"[SIMULATION] TP 지정가 주문 생략됨 → {side} {tp_size} BTC @ {tp_trigger_price}")
        return f"SIMULATED_TP_{side}_{tp_trigger_price}"  # 가상 주문 ID 반환

    url = f"{BYBIT_BASE_URL}/v5/position/trading-stop"
    timestamp = str(int(time.time() * 1000))
    position_idx = 1 if side == "Buy" else 2

    #  현재가(LastPrice) 대비 유효성 보정
    last_price = latest_price_map.get(symbol)
    if last_price:
        # Bybit 요구 조건 회피: Sell은 takeProfit < LastPrice, Buy는 takeProfit > LastPrice
        if side == "Sell" and tp_trigger_price >= last_price:
            adj = round(last_price - 50, 1)
            log(f"🩹 [TP 트리거 보정] Sell: {tp_trigger_price} → {adj} (LastPrice={last_price})")
            tp_trigger_price = adj
        elif side == "Buy" and tp_trigger_price <= last_price:
            adj = round(last_price + 50, 1)
            log(f"🩹 [TP 트리거 보정] Buy:  {tp_trigger_price} → {adj} (LastPrice={last_price})")
            tp_trigger_price = adj

    body = {
        "category": "linear",
        "symbol": symbol,
        "tpslMode": "Partial",
        "positionIdx": position_idx,
        "takeProfit": str(tp_trigger_price),
        "tpOrderType": "Limit",             # tpOrderType은 Limit으로 전송
        "tpLimitPrice": str(tp_limit_price),
        "tpSize": str(tp_size),
        "tpTriggerBy": "LastPrice"
    }

    log(f"[DEBUG] TP 주문 요청 body: {json.dumps(body, ensure_ascii=False)}")

    body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    origin = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
    sign = hmac.new(BYBIT_API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        result = response.json()
        if result.get("retCode") == 0:
            log(f"📈 [Order Executor] Partial TP 설정 완료 → {tp_trigger_price} @ {tp_size}")
        else:
            log(f"❌ [Order Executor] Partial TP 설정 실패: {result}")
    except Exception as e:
        log(f"❌ [Order Executor] Partial TP 요청 오류: {e}")

# pos_side: "Buy"(롱 포지션), "Sell"(숏 포지션)
# - 롱: ref_price >= tp_price 이면 이미 TP 도달(또는 초과)
# - 숏: ref_price <= tp_price 이면 이미 TP 도달(또는 하회)
def _tp_already_reached(pos_side: str, tp_price: float, ref_price: Optional[float]) -> bool:
    if ref_price is None:
        return False
    if pos_side == "Buy":
        return ref_price >= tp_price
    return ref_price <= tp_price


#  전량 TP 설정(Full, tpOrderType=Market) + "이미 도달이면 즉시 시장가 전량 청산"
# - TP 설정 시점에 이미 tp_trigger_price에 도달(또는 초과/하회)했다면:
#     -> 즉시 전량 시장가 청산(=즉시 TP 처리)
# - 아직 미도달이면:
#     -> /v5/position/trading-stop 에 Full TP(Market) 설정
#
# 주의:
# - 시장가 청산이라 체결가는 트리거 가격과 정확히 같지 않을 수 있음(슬리피지)
# - "이미 도달" 판정은 latest_price_map[symbol] (보통 LastPrice)로 함
def set_full_tp_market_immediate(
    symbol: str,
    pos_side: str,                # "Buy"(롱 포지션) / "Sell"(숏 포지션)  ※ '포지션 방향'
    tp_trigger_price: float,
    *,
    tp_trigger_by: str = "LastPrice",
    close_target_order_link_id: str = "",
    order_link_id: str = "",
    retry_on_fail: bool = True,
) -> bool:

    SIMULATION_MODE = (shared_state.current_config or {}).get("simulation_mode", False)
    if SIMULATION_MODE:
        log(f"[SIMULATION] Full TP(Market) 생략 → {symbol} pos_side={pos_side} tp={tp_trigger_price}")
        return True

    # 1) 이미 도달이면 즉시 전량 시장가 청산
    last_price = latest_price_map.get(symbol)
    last_f = float(last_price) if last_price else None

    if _tp_already_reached(pos_side, tp_trigger_price, last_f):
        close_order_side = "Sell" if pos_side == "Buy" else "Buy"
        log(f"⚡ [Full TP] 이미 TP 도달 상태 → 즉시 전량 시장가 청산 실행: {symbol} pos_side={pos_side} tp={tp_trigger_price}, last={last_price}")

        res = place_order(
            symbol=symbol,
            side=close_order_side,
            close_position=True,
            reduce_only=True,
            is_tp_order=True,
            close_target_order_link_id=close_target_order_link_id,
            order_link_id=order_link_id,
            order_type="Market",
        )
        return bool(res)

    # 2) 미도달이면 Full TP(Market) 설정
    url = f"{BYBIT_BASE_URL}/v5/position/trading-stop"
    timestamp = str(int(time.time() * 1000))
    position_idx = 1 if pos_side == "Buy" else 2

    body = {
        "category": "linear",
        "symbol": symbol,
        "tpslMode": "Full",
        "positionIdx": position_idx,
        "takeProfit": str(tp_trigger_price),
        "tpTriggerBy": tp_trigger_by,
        "tpOrderType": "Market",
    }

    body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    origin = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
    sign = hmac.new(BYBIT_API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url,
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        result = response.json()

        if result.get("retCode") == 0:
            log(f"✅ [Order Executor] Full TP(Market) 설정 완료 → {symbol} pos_side={pos_side} TP={tp_trigger_price}")
            return True

        log(f"⚠️ [Order Executor] Full TP(Market) 설정 실패: {result}")

        # 3) 레이스 컨디션 대비: 실패했는데 그 사이 TP에 도달해버렸으면 즉시 청산
        if retry_on_fail:
            last_price2 = latest_price_map.get(symbol)
            last2_f = float(last_price2) if last_price2 else None

            if _tp_already_reached(pos_side, tp_trigger_price, last2_f):
                close_order_side = "Sell" if pos_side == "Buy" else "Buy"
                log(f"⚡ [Full TP] 설정 실패 후 재확인: 이미 도달 → 즉시 전량 시장가 청산 실행: {symbol} tp={tp_trigger_price}, last={last_price2}")

                res = place_order(
                    symbol=symbol,
                    side=close_order_side,
                    close_position=True,
                    reduce_only=True,
                    is_tp_order=True,
                    close_target_order_link_id=close_target_order_link_id,
                    order_link_id=order_link_id,
                    order_type="Market",
                )
                return bool(res)

        return False

    except Exception as e:
        log(f"❌ [Order Executor] Full TP(Market) 요청 오류: {e}")
        return False

#  기존 포지션에 SL만 설정 또는 변경
def set_stop_loss(symbol: str, side: str, sl_price: float) -> bool:
    url = f"{BYBIT_BASE_URL}/v5/position/trading-stop"
    timestamp = str(int(time.time() * 1000))
    position_idx = 1 if side == "Buy" else 2
    for attempt in range(3):
        timestamp = str(int(time.time() * 1000))

        # SL 중복 설정 방지
        position = get_open_position_info(symbol, side)
        try:
            current_sl = float(position.get("stopLoss") or 0) if position else 0
            if abs(current_sl - sl_price) < 1e-3:
                log(f"ℹ️ [Order Executor] 기존 SL과 동일 (기존 : {current_sl}, 설정하려는 SL : {sl_price}) → SL 설정 생략")
                return True
        except Exception as e:
            log(f"⚠️ [Order Executor] SL 비교 중 오류 발생: {e}")

        body = {
            "category": "linear",
            "symbol": symbol,
            "tpslMode":"Full",
            "positionIdx": position_idx,
            "stopLoss": str(sl_price),
            "slTriggerBy":"MarkPrice"
        }

        body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        origin = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
        sign = hmac.new(BYBIT_API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()

        headers = {
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                url,
                data=body_str.encode("utf-8"),
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            result = response.json()
            if result.get("retCode") in [0, 34040]:
                time.sleep(0.5)
                confirm = get_open_position_info(symbol, side)
                confirm_sl = float(confirm.get("stopLoss") or 0) if confirm else 0
                if abs(confirm_sl - sl_price) < 1e-3:
                    log(f"✅ SL 확인됨 → {symbol} {side} SL={confirm_sl}")
                    return True
                else:
                    log(f"⚠️ SL 설정 응답 성공 but 적용 안됨 → 재시도 {attempt + 1}/3")
                    time.sleep(0.5)
                    continue
            else:
                log(f"❌ [Order Executor] SL 설정 실패: {result}")
        except Exception as e:
            log(f"❌ [Order Executor] SL 설정 요청 오류: {e}")

    log("❌ [Order Executor] SL 설정 3회 재시도 후 실패")
    return False


def cancel_order(order_id: str, symbol: str) -> bool:
    url = f"{BYBIT_BASE_URL}/v5/order/cancel"
    timestamp = str(int(time.time() * 1000))

    body = {
        "category": "linear",
        "symbol": symbol,
        "orderId": order_id
    }

    body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    origin = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{body_str}"
    sign = hmac.new(BYBIT_API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        result = response.json()
        if result.get("retCode") == 0:
            log(f"🧹 주문 취소 완료 → orderId: {order_id}")
            return True
        else:
            log(f"⚠️ 주문 취소 실패 → orderId: {order_id}, 응답: {result}")
    except Exception as e:
        log(f"❌ 주문 취소 요청 오류 → orderId: {order_id}, 오류: {e}")
    return False
