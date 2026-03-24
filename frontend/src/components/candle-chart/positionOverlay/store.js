// 포지션 오버레이 전역 저장소
export const positionOverlayStore = {
  ws: null,
  wsReconnectTimer: null,
  overlaysById: {},
  previewById: {},
  pendingIds: new Set(),
  dragHighlightById: {},
  labelMeasureCtx: null,
  labelHitboxFrame: {
    frameId: -1,
    labels: [],
  },
  dashPhase: 0,
  dashTimer: null,
  lastPrice: null,
  chart: null,
  retryTimer: null,
  dragStartBridge: null,
};

// 기본 참조
export function getPositionOverlay(overlayId) {
  if (!overlayId) return null;
  return positionOverlayStore.overlaysById[String(overlayId)] || null;
}

export function getPositionOverlayLastPrice() {
  return positionOverlayStore.lastPrice;
}

export function setPositionOverlayChart(chart) {
  positionOverlayStore.chart = chart || null;
}

export function setPositionOverlayLastPrice(price) {
  const normalized = Number(price);
  positionOverlayStore.lastPrice = Number.isFinite(normalized) ? normalized : null;
}

export function getPositionOverlayDragStartBridge() {
  return positionOverlayStore.dragStartBridge;
}

export function setPositionOverlayDragStartBridge(bridge) {
  positionOverlayStore.dragStartBridge =
    typeof bridge === "function" ? bridge : null;
}

// 프리뷰 / 드래그 상태
export function setPositionOverlayPreview({ overlayId, field, price }) {
  if (!overlayId || !Number.isFinite(price)) return false;

  const key = String(overlayId);
  const prev = positionOverlayStore.previewById[key] || {};
  positionOverlayStore.previewById[key] = {
    ...prev,
    ...(field === "tp"
      ? { tpPrice: price, tpAvailable: true }
      : { slPrice: price, slAvailable: true }),
  };
  return true;
}

export function clearPositionOverlayPreview(overlayId) {
  if (!overlayId) return false;

  const key = String(overlayId);
  if (!(key in positionOverlayStore.previewById)) return false;

  delete positionOverlayStore.previewById[key];
  return true;
}

export function setPositionOverlayDragHighlight({ overlayId, field }) {
  if (!overlayId || (field !== "tp" && field !== "sl")) return false;

  const key = String(overlayId);
  if (positionOverlayStore.dragHighlightById[key] === field) return false;

  positionOverlayStore.dragHighlightById[key] = field;
  return true;
}

export function clearPositionOverlayDragHighlight(overlayId) {
  if (!overlayId) return false;

  const key = String(overlayId);
  if (!(key in positionOverlayStore.dragHighlightById)) return false;

  delete positionOverlayStore.dragHighlightById[key];
  return true;
}

export function hasPositionOverlayDragHighlight() {
  return Object.keys(positionOverlayStore.dragHighlightById).length > 0;
}

export function addPositionOverlayPending(overlayId) {
  if (!overlayId) return;
  positionOverlayStore.pendingIds.add(String(overlayId));
}

export function removePositionOverlayPending(overlayId) {
  if (!overlayId) return;
  positionOverlayStore.pendingIds.delete(String(overlayId));
}

export function hasPositionOverlayPending(overlayId) {
  if (!overlayId) return false;
  return positionOverlayStore.pendingIds.has(String(overlayId));
}

// 스냅샷 반영
export function replacePositionOverlayState(overlays) {
  const arr = Array.isArray(overlays) ? overlays : [];
  const activeIds = new Set();

  for (const key of Object.keys(positionOverlayStore.overlaysById)) {
    delete positionOverlayStore.overlaysById[key];
  }

  for (const overlay of arr) {
    if (!overlay || !overlay.id) continue;
    const overlayId = String(overlay.id);
    positionOverlayStore.overlaysById[overlayId] = overlay;
    activeIds.add(overlayId);
  }

  for (const key of Object.keys(positionOverlayStore.previewById)) {
    if (!activeIds.has(key)) {
      delete positionOverlayStore.previewById[key];
    }
  }

  for (const overlayId of Array.from(positionOverlayStore.pendingIds)) {
    if (!activeIds.has(overlayId)) {
      positionOverlayStore.pendingIds.delete(overlayId);
    }
  }

  for (const key of Object.keys(positionOverlayStore.dragHighlightById)) {
    if (!activeIds.has(key)) {
      delete positionOverlayStore.dragHighlightById[key];
    }
  }
}

export function upsertPositionOverlay(overlay) {
  if (!overlay || !overlay.id) return false;

  positionOverlayStore.overlaysById[String(overlay.id)] = overlay;
  return true;
}

export function removePositionOverlay(overlayId) {
  if (overlayId == null) return false;

  const key = String(overlayId);
  delete positionOverlayStore.overlaysById[key];
  delete positionOverlayStore.previewById[key];
  positionOverlayStore.pendingIds.delete(key);
  delete positionOverlayStore.dragHighlightById[key];
  return true;
}

// 런타임 초기화
export function resetPositionOverlayRuntimeState() {
  positionOverlayStore.pendingIds.clear();
  positionOverlayStore.labelHitboxFrame.frameId = -1;
  positionOverlayStore.labelHitboxFrame.labels = [];

  for (const key of Object.keys(positionOverlayStore.previewById)) {
    delete positionOverlayStore.previewById[key];
  }

  for (const key of Object.keys(positionOverlayStore.dragHighlightById)) {
    delete positionOverlayStore.dragHighlightById[key];
  }

  setPositionOverlayDragStartBridge(null);
}
