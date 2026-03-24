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
import { counter, mark, measure } from "../../../utils/chartPerf";

const POSITION_OVERLAY_WS_SNAPSHOT_FALLBACK_MS = 1500;

function stopDashAnimationIfIdle() {
  if (!hasPositionOverlayDragHighlight()) {
    stopPositionOverlayDashAnimation();
  }
}

function handlePositionOverlayWsMessage(event) {
  let msg = null;
  try {
    msg = JSON.parse(event.data);
  } catch {
    return null;
  }

  if (msg?.type === "position_overlay_snapshot") {
    replacePositionOverlayState(msg.overlays);
    stopDashAnimationIfIdle();
    applyPositionOverlays();
    return "position_overlay_snapshot";
  }

  if (msg?.type === "position_overlay_update") {
    if (upsertPositionOverlay(msg.overlay)) {
      applyPositionOverlays();
    }
    return "position_overlay_update";
  }

  if (msg?.type === "position_overlay_clear") {
    if (removePositionOverlay(msg.id)) {
      stopDashAnimationIfIdle();
      applyPositionOverlays();
    }
    return "position_overlay_clear";
  }

  if (msg?.action === "update" && upsertPositionOverlay(msg.overlay)) {
    applyPositionOverlays();
    return "update";
  }

  if (msg?.action === "clear" && removePositionOverlay(msg.id)) {
    stopDashAnimationIfIdle();
    applyPositionOverlays();
    return "clear";
  }

  return msg?.type || msg?.action || null;
}

export async function fetchAndApplyPositionOverlaySnapshot({
  reason = "manual",
  silent = true,
} = {}) {
  try {
    counter("position_overlay_snapshot_count");
    mark("position_overlay_snapshot_start", {
      reason,
      request: true,
      url: "/api/position-overlays/snapshot",
    });
    const response = await fetch("/api/position-overlays/snapshot");
    mark("position_overlay_snapshot_end", {
      reason,
      status: response.status,
    });
    measure(
      "position_overlay_snapshot_start",
      "position_overlay_snapshot_end",
      "position_overlay_snapshot_ms",
      { reason }
    );
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
    mark("position_overlay_snapshot_end", {
      reason,
      status: "error",
      error: error?.name || "unknown",
    });
    measure(
      "position_overlay_snapshot_start",
      "position_overlay_snapshot_end",
      "position_overlay_snapshot_ms",
      { reason, error: true }
    );
    if (!silent) {
      console.error("[CandleChart] position overlay snapshot fetch 실패:", error);
    }
    return false;
  }
}

export function connectPositionOverlayWs() {
  if (positionOverlayStore.ws) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  const url = `${protocol}//${host}/ws/position-overlay`;

  try {
    let hasSeenFirstMessage = false;
    let hasReceivedSnapshot = false;
    let bootstrapFallbackTimer = null;
    const clearBootstrapFallbackTimer = () => {
      if (bootstrapFallbackTimer == null) return;
      clearTimeout(bootstrapFallbackTimer);
      bootstrapFallbackTimer = null;
    };

    mark("position_ws_connect_start", { url });
    const ws = new WebSocket(url);
    positionOverlayStore.ws = ws;

    ws.onopen = () => {
      mark("position_ws_open", { url });
      if (positionOverlayStore.wsReconnectTimer) {
        clearTimeout(positionOverlayStore.wsReconnectTimer);
        positionOverlayStore.wsReconnectTimer = null;
      }

      clearBootstrapFallbackTimer();
      bootstrapFallbackTimer = setTimeout(() => {
        bootstrapFallbackTimer = null;
        if (positionOverlayStore.ws !== ws || hasReceivedSnapshot) {
          return;
        }
        void fetchAndApplyPositionOverlaySnapshot({
          reason: "ws-bootstrap-fallback",
          silent: true,
        });
      }, POSITION_OVERLAY_WS_SNAPSHOT_FALLBACK_MS);
    };

    ws.onmessage = (wsEvent) => {
      if (!hasSeenFirstMessage) {
        hasSeenFirstMessage = true;
        mark("position_ws_first_message");
      }
      const messageType = handlePositionOverlayWsMessage(wsEvent);
      if (messageType === "position_overlay_snapshot") {
        hasReceivedSnapshot = true;
        clearBootstrapFallbackTimer();
      }
    };

    ws.onerror = (error) => {
      console.error("[CandleChart] position-overlay WS error:", error);
      clearBootstrapFallbackTimer();
      try {
        ws.close();
      } catch {
        // ignore
      }
    };

    ws.onclose = () => {
      positionOverlayStore.ws = null;
      clearBootstrapFallbackTimer();

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
