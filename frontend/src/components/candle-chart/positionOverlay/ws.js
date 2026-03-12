import { safeJson } from "../chartUtils";
import {
  hasPositionOverlayDragHighlight,
  positionOverlayStore,
  removePositionOverlay,
  replacePositionOverlayState,
  upsertPositionOverlay,
} from "./store";
import {
  applyPositionOverlays,
  stopPositionOverlayDashAnimation,
} from "./overlay";

// 드래그 강조선이 없는 상태에서는 점선 애니메이션을 멈추기 위함
function stopDashAnimationIfIdle() {
  if (!hasPositionOverlayDragHighlight()) {
    stopPositionOverlayDashAnimation();
  }
}

// 서버에서 온 포지션 오버레이 이벤트를 메모리 상태에 반영하기 위함
function handlePositionOverlayWsMessage(event) {
  let msg = null;
  try {
    msg = JSON.parse(event.data);
  } catch {
    return;
  }

  if (msg?.type === "position_overlay_snapshot") {
    replacePositionOverlayState(msg.overlays);
    stopDashAnimationIfIdle();
    applyPositionOverlays();
    return;
  }

  if (msg?.type === "position_overlay_update") {
    if (upsertPositionOverlay(msg.overlay)) {
      applyPositionOverlays();
    }
    return;
  }

  if (msg?.type === "position_overlay_clear") {
    if (removePositionOverlay(msg.id)) {
      stopDashAnimationIfIdle();
      applyPositionOverlays();
    }
    return;
  }

  if (msg?.action === "update" && upsertPositionOverlay(msg.overlay)) {
    applyPositionOverlays();
    return;
  }

  if (msg?.action === "clear" && removePositionOverlay(msg.id)) {
    stopDashAnimationIfIdle();
    applyPositionOverlays();
  }
}

// 재연결 직후 서버 기준 최신 오버레이 스냅샷을 다시 맞추기 위함
export async function fetchAndApplyPositionOverlaySnapshot({
  reason = "manual",
  silent = true,
} = {}) {
  try {
    const response = await fetch("/api/position-overlays/snapshot");
    if (!response.ok) {
      if (!silent) {
        console.warn("[CandleChart] position overlay snapshot 응답 코드:", response.status);
      }
      return false;
    }

    const overlays = await safeJson(response, `position-overlay-snapshot-${reason}`);
    if (!Array.isArray(overlays)) {
      if (!silent) {
        console.warn("[CandleChart] position overlay snapshot 형식 오류:", overlays);
      }
      return false;
    }

    replacePositionOverlayState(overlays);
    stopDashAnimationIfIdle();
    applyPositionOverlays();
    return true;
  } catch (error) {
    if (!silent) {
      console.error("[CandleChart] position overlay snapshot fetch 실패:", error);
    }
    return false;
  }
}

// 차트와 서버가 같은 포지션 오버레이 상태를 보도록 연결하기 위함
export function connectPositionOverlayWs() {
  if (positionOverlayStore.ws) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  const url = `${protocol}//${host}/ws/position-overlay`;

  try {
    const ws = new WebSocket(url);
    positionOverlayStore.ws = ws;

    ws.onopen = () => {
      if (positionOverlayStore.wsReconnectTimer) {
        clearTimeout(positionOverlayStore.wsReconnectTimer);
        positionOverlayStore.wsReconnectTimer = null;
      }
      void fetchAndApplyPositionOverlaySnapshot({ reason: "ws-open", silent: true });
    };

    ws.onmessage = (wsEvent) => {
      handlePositionOverlayWsMessage(wsEvent);
    };

    ws.onerror = (error) => {
      console.error("[CandleChart] position-overlay WS error:", error);
      try {
        ws.close();
      } catch {
        // ignore
      }
    };

    ws.onclose = () => {
      console.warn("[CandleChart] position-overlay WS closed");
      positionOverlayStore.ws = null;

      if (!positionOverlayStore.wsReconnectTimer) {
        positionOverlayStore.wsReconnectTimer = setTimeout(() => {
          positionOverlayStore.wsReconnectTimer = null;
          connectPositionOverlayWs();
        }, 3000);
      }
    };
  } catch (error) {
    console.error("[CandleChart] position-overlay WS connect failed:", error);
  }
}
