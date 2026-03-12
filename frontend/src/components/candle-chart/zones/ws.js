import {
  DEFAULT_SYMBOL,
  ZONE_TF_LIST,
  TF_LIST,
} from "../constants";
import { applyZoneOverlays } from "./overlay";
import {
  applyZoneStatePayload,
  zoneStore,
} from "./store";

// Zone 상태 WS 메시지를 브라우저 상태로 연결하기 위함
function handleZoneStateWsMessage(event) {
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch (error) {
    console.error("[CandleChart][ZoneWS] JSON 파싱 실패:", error, event.data);
    return;
  }

  if (!msg || !msg.type) {
    console.warn("[CandleChart][ZoneWS] msg.type 없음:", msg);
    return;
  }

  switch (msg.type) {
    case "zone_state_sync": {
      const { symbol, tf, boxes } = msg;

      if (symbol !== DEFAULT_SYMBOL.ticker) {
        return;
      }

      const tfStr = String(tf);
      if (!ZONE_TF_LIST.includes(tfStr)) {
        return;
      }

      applyZoneStatePayload(tfStr, boxes);
      applyZoneOverlays();
      return;
    }

    case "zone_delta": {
      const { symbol, tf, delta, server_ts, seq } = msg;
      if (symbol !== DEFAULT_SYMBOL.ticker) return;

      const tfStr = String(tf);
      if (!ZONE_TF_LIST.includes(tfStr)) return;

      window.dispatchEvent(
        new CustomEvent("zone_delta", {
          detail: { symbol, tf: tfStr, delta, server_ts, seq },
        })
      );
      return;
    }

    case "candle_rest_confirmed": {
      if (typeof window === "undefined") return;

      const { symbol, tf, from, to, candle } = msg;

      if (symbol !== DEFAULT_SYMBOL.ticker) {
        return;
      }

      const tfStr = String(tf);
      if (!TF_LIST.includes(tfStr)) return;

      if (!candle) {
        console.warn(
          "[CandleChart][ZoneWS] candle_rest_confirmed 에 candle 필드가 없음:",
          msg
        );
        return;
      }

      try {
        const evt = new CustomEvent("candle_rest_confirmed", {
          detail: {
            symbol,
            tf: tfStr,
            from,
            to,
            candle,
          },
        });
        window.dispatchEvent(evt);
      } catch (error) {
        console.error(
          "[CandleChart] candle_rest_confirmed 이벤트 디스패치 실패:",
          error
        );
      }
      return;
    }

    case "mtf_ma_source_candle": {
      if (typeof window === "undefined") return;

      const { symbol, tf, candle } = msg;
      if (symbol !== DEFAULT_SYMBOL.ticker) return;

      const tfStr = String(tf);
      if (!TF_LIST.includes(tfStr)) return;
      if (!candle) return;

      try {
        window.dispatchEvent(
          new CustomEvent("mtf_ma_source_candle", {
            detail: {
              symbol,
              tf: tfStr,
              candle,
            },
          })
        );
      } catch (error) {
        console.error("[CandleChart] mtf_ma_source_candle 이벤트 디스패치 실패:", error);
      }
      return;
    }

    default:
      console.warn("[CandleChart][ZoneWS] 알 수 없는 메시지 type:", msg.type);
  }
}

// 브라우저와 백엔드가 같은 Zone 상태를 보도록 연결하기 위함
export function connectZoneStateWs() {
  if (zoneStore.ws) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  const url = `${protocol}//${host}/ws/zones`;

  try {
    const ws = new WebSocket(url);
    zoneStore.ws = ws;

    ws.onopen = () => {
      if (zoneStore.wsReconnectTimer) {
        clearTimeout(zoneStore.wsReconnectTimer);
        zoneStore.wsReconnectTimer = null;
      }

      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("zone_ws_connected", {
            detail: { connectedAt: Date.now() },
          })
        );
      }
    };

    ws.onmessage = (wsEvent) => {
      handleZoneStateWsMessage(wsEvent);
    };

    ws.onerror = (error) => {
      console.error("[CandleChart] zone WS error:", error);
      try {
        ws.close();
      } catch {
        // ignore
      }
    };

    ws.onclose = () => {
      console.warn("[CandleChart] zone WS closed");
      zoneStore.ws = null;

      if (!zoneStore.wsReconnectTimer) {
        zoneStore.wsReconnectTimer = setTimeout(() => {
          zoneStore.wsReconnectTimer = null;
          connectZoneStateWs();
        }, 3000);
      }
    };
  } catch (error) {
    console.error("[CandleChart] zone WS connect failed:", error);
  }
}
