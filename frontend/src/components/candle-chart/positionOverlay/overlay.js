import { registerOverlay } from "klinecharts";

import {
  DEFAULT_SYMBOL,
  POSITION_DRAG_DASH_GAP_PX,
  POSITION_DRAG_DASH_INTERVAL_MS,
  POSITION_DRAG_DASH_SEGMENT_PX,
  POSITION_DRAG_DASH_STEP_PX,
  POSITION_LABEL_FALLBACK_HEIGHT_PX,
  POSITION_LABEL_FALLBACK_WIDTH_PX,
  POSITION_OVERLAY_GROUP_ID,
  POSITION_OVERLAY_NAME,
  POSITION_OVERLAY_YAXIS_DRAG_SL_KEY,
  POSITION_OVERLAY_YAXIS_DRAG_TP_KEY,
} from "../constants";
import { projectTsToChart, resolveMainPaneId } from "../chartUtils";
import {
  getPositionOverlayDragStartBridge,
  hasPositionOverlayDragHighlight,
  positionOverlayStore,
} from "./store";

// Y축 라벨 충돌 회피 상태
const yAxisLabelCollision = new Map();
const yAxisGapPx = 14;
const yAxisMaxShiftSteps = 25;

let positionOverlayRegistered = false;

// 드래그 필드 판별
function parsePositionOverlayDragFieldByFigure(figure) {
  const key = String(figure?.key || "");
  if (key === POSITION_OVERLAY_YAXIS_DRAG_TP_KEY) return "tp";
  if (key === POSITION_OVERLAY_YAXIS_DRAG_SL_KEY) return "sl";
  return null;
}

function yAxisFrameId() {
  const now =
    typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  return Math.floor(now / 16);
}

function getPositionLabelFrameBucket() {
  return positionOverlayStore.labelHitboxFrame;
}

// 라벨 너비 측정
export function measurePositionLabelWidthPx(text, fontSize = 12) {
  const label = String(text ?? "");
  if (!label) return 0;

  if (typeof document !== "undefined") {
    if (!positionOverlayStore.labelMeasureCtx) {
      try {
        const canvas = document.createElement("canvas");
        positionOverlayStore.labelMeasureCtx = canvas.getContext("2d");
      } catch {
        positionOverlayStore.labelMeasureCtx = null;
      }
    }

    const ctx = positionOverlayStore.labelMeasureCtx;
    if (ctx) {
      try {
        ctx.font = `${fontSize}px Helvetica Neue`;
        const measured = Number(ctx.measureText(label).width);
        if (Number.isFinite(measured) && measured > 0) {
          return measured;
        }
      } catch {
        // ignore
      }
    }
  }

  return label.length * Math.max(6, fontSize * 0.56);
}

function getPositionDragDashPeriodPx() {
  return POSITION_DRAG_DASH_SEGMENT_PX + POSITION_DRAG_DASH_GAP_PX;
}

// 드래그 가이드 라인
function buildMovingDashedLineFigures({
  left,
  right,
  y,
  color,
  size = 1,
  ignoreEvent = true,
}) {
  if (!Number.isFinite(left) || !Number.isFinite(right) || !Number.isFinite(y)) {
    return [];
  }

  const startX = Math.min(left, right);
  const endX = Math.max(left, right);
  if (endX - startX <= 0) return [];

  const period = getPositionDragDashPeriodPx();
  if (!Number.isFinite(period) || period <= 0) return [];

  const phaseRaw = Number(positionOverlayStore.dashPhase) || 0;
  const phase = ((phaseRaw % period) + period) % period;

  const figures = [];
  for (let x = startX - phase; x < endX; x += period) {
    const segStart = Math.max(startX, x);
    const segEnd = Math.min(endX, x + POSITION_DRAG_DASH_SEGMENT_PX);
    if (segEnd <= segStart) continue;
    figures.push({
      type: "line",
      attrs: { coordinates: [{ x: segStart, y }, { x: segEnd, y }] },
      styles: { style: "solid", color, size },
      ignoreEvent,
    });
  }

  return figures;
}

// 대시 애니메이션 정리
export function stopPositionOverlayDashAnimation() {
  if (positionOverlayStore.dashTimer != null) {
    clearInterval(positionOverlayStore.dashTimer);
    positionOverlayStore.dashTimer = null;
  }
  positionOverlayStore.dashPhase = 0;
}

// 대시 애니메이션 시작
export function ensurePositionOverlayDashAnimation() {
  if (positionOverlayStore.dashTimer != null) return;
  if (typeof window === "undefined") return;

  positionOverlayStore.dashTimer = window.setInterval(() => {
    if (!hasPositionOverlayDragHighlight()) {
      stopPositionOverlayDashAnimation();
      return;
    }

    positionOverlayStore.dashPhase =
      (positionOverlayStore.dashPhase -
        POSITION_DRAG_DASH_STEP_PX +
        getPositionDragDashPeriodPx()) %
      getPositionDragDashPeriodPx();
    applyPositionOverlays();
  }, POSITION_DRAG_DASH_INTERVAL_MS);
}

// Y축 hitbox 등록
function registerPositionOverlayAxisLabelHitbox({
  overlayId,
  field,
  paneId,
  y,
  axisSide,
  text = "",
  fontSize = 12,
  paddingLeft = 6,
  paddingRight = 6,
  paddingTop = 3,
  paddingBottom = 3,
}) {
  if (!overlayId || (field !== "tp" && field !== "sl")) return;
  if (typeof y !== "number" || !Number.isFinite(y)) return;

  const bucket = getPositionLabelFrameBucket();
  const targetPaneId = paneId || "candle_pane";
  const side = axisSide === "left" ? "left" : "right";
  const measuredTextWidth = measurePositionLabelWidthPx(text, fontSize);
  const widthPx = Math.max(
    POSITION_LABEL_FALLBACK_WIDTH_PX,
    measuredTextWidth + Math.max(0, paddingLeft) + Math.max(0, paddingRight)
  );
  const heightPx = Math.max(
    POSITION_LABEL_FALLBACK_HEIGHT_PX,
    Math.max(0, fontSize) + Math.max(0, paddingTop) + Math.max(0, paddingBottom)
  );

  const key = `${overlayId}:${field}:${targetPaneId}:${side}`;
  const next = {
    key,
    overlayId: String(overlayId),
    field,
    paneId: targetPaneId,
    y,
    axisSide: side,
    widthPx,
    heightPx,
    at: Date.now(),
  };

  const prevIndex = bucket.labels.findIndex((label) => label.key === key);
  if (prevIndex >= 0) {
    bucket.labels[prevIndex] = next;
  } else {
    bucket.labels.push(next);
  }
}

// Y축 hitbox 조회
export function getPositionOverlayAxisLabelHitboxes() {
  return getPositionLabelFrameBucket().labels;
}

function yAxisKeyFromOverlay(overlay, yAxis) {
  const paneId = overlay?.paneId || "candle_pane";
  const isFromZero = yAxis?.isFromZero?.() ?? false;
  const sideKey = isFromZero ? "left" : "right";
  return `${paneId}:${sideKey}`;
}

function getYAxisBucket(key) {
  const frameId = yAxisFrameId();
  const prev = yAxisLabelCollision.get(key);
  if (!prev || prev.frameId !== frameId) {
    const next = {
      frameId,
      reservedYs: [],
      lastPriceReservedFrameId: -1,
    };
    yAxisLabelCollision.set(key, next);
    return next;
  }

  return prev;
}

function reserveY(key, y) {
  if (typeof y !== "number" || !Number.isFinite(y)) return;
  getYAxisBucket(key).reservedYs.push(y);
}

function allocY(key, baseY, bounding) {
  if (typeof baseY !== "number" || !Number.isFinite(baseY)) return null;

  const bucket = getYAxisBucket(key);
  const topLimit = (bounding?.top ?? 0) + 2;
  const bottomLimit =
    (typeof bounding?.height === "number"
      ? (bounding.top ?? 0) + bounding.height
      : Infinity) - 2;

  let y = baseY;
  let steps = 0;

  while (bucket.reservedYs.some((target) => Math.abs(target - y) < yAxisGapPx)) {
    y += yAxisGapPx;
    steps += 1;
    if (steps > yAxisMaxShiftSteps) return null;
    if (y > bottomLimit) return null;
  }

  if (y < topLimit) y = topLimit;
  if (y > bottomLimit) return null;
  return y;
}

function reserveLastPriceLabelY(key, paneId) {
  const bucket = getYAxisBucket(key);
  if (bucket.lastPriceReservedFrameId === bucket.frameId) return;

  bucket.lastPriceReservedFrameId = bucket.frameId;

  const price = positionOverlayStore.lastPrice;
  if (typeof price !== "number" || !Number.isFinite(price)) return;

  const chart = positionOverlayStore.chart;
  if (!chart || typeof chart.convertToPixel !== "function") return;

  try {
    const point = chart.convertToPixel({ value: price }, { paneId, absolute: false });
    const y = Array.isArray(point) ? point[0]?.y : point?.y;
    if (typeof y === "number" && Number.isFinite(y)) {
      reserveY(key, y);
    }
  } catch {
    // ignore
  }
}

// 오버레이 재시도 타이머 정리
export function clearPositionOverlayRetryTimer() {
  if (positionOverlayStore.retryTimer != null) {
    clearTimeout(positionOverlayStore.retryTimer);
    positionOverlayStore.retryTimer = null;
  }
}

// 차트 오버레이 투영
export function applyPositionOverlays(chartArg) {
  const chart = chartArg || positionOverlayStore.chart;

  if (
    !chart ||
    typeof chart.createOverlay !== "function" ||
    typeof chart.removeOverlay !== "function"
  ) {
    return;
  }

  try {
    chart.removeOverlay({ name: POSITION_OVERLAY_NAME });
  } catch {
    // ignore
  }

  const overlays = Object.values(positionOverlayStore.overlaysById);
  if (!overlays.length) return;

  const dataList = chart.getDataList?.() || [];
  if (!dataList.length) {
    if (!positionOverlayStore.retryTimer) {
      positionOverlayStore.retryTimer = setTimeout(() => {
        positionOverlayStore.retryTimer = null;
        applyPositionOverlays(chart);
      }, 50);
    }
    return;
  }

  const mainPaneId = resolveMainPaneId(chart);

  const overlayConfigs = overlays
    .map((overlay) => {
      if (!overlay || !overlay.id) return null;
      if (overlay.symbol && overlay.symbol !== DEFAULT_SYMBOL.ticker) return null;

      const overlayId = String(overlay.id);
      const preview = positionOverlayStore.previewById[overlayId];
      const entryTs = Number(overlay.entryTs);
      const entryPrice = Number(overlay.entryPrice);
      if (!Number.isFinite(entryPrice)) return null;

      const tpAvailableBase =
        overlay.tpAvailable === true ||
        (overlay.tpPrice != null && Number(overlay.tpPrice) > 0);
      const tpAvailable =
        preview?.tpAvailable != null ? !!preview.tpAvailable : tpAvailableBase;
      const tpPriceRaw =
        preview?.tpPrice != null ? Number(preview.tpPrice) : Number(overlay.tpPrice);
      const tpPriceSafe = Number.isFinite(tpPriceRaw) ? tpPriceRaw : entryPrice;
      const tpPrice = tpAvailable ? tpPriceSafe : entryPrice;

      const entryAnchor = Number.isFinite(entryTs)
        ? projectTsToChart(dataList, entryTs, "floor")
        : null;
      const anchorTs = entryAnchor != null ? entryAnchor : Number(dataList[0]?.timestamp);
      if (!Number.isFinite(anchorTs)) return null;

      const slAvailableBase = !!overlay.slAvailable;
      const slAvailable =
        preview?.slAvailable != null ? !!preview.slAvailable : slAvailableBase;
      const slPriceRaw =
        preview?.slPrice != null ? Number(preview.slPrice) : Number(overlay.slPrice);
      const slPriceSafe = Number.isFinite(slPriceRaw) ? slPriceRaw : entryPrice;
      const slPrice = slAvailable ? slPriceSafe : entryPrice;

      return {
        name: POSITION_OVERLAY_NAME,
        groupId: POSITION_OVERLAY_GROUP_ID,
        paneId: mainPaneId,
        lock: true,
        points: [
          { timestamp: anchorTs, value: entryPrice },
          { timestamp: anchorTs, value: tpPrice },
          { timestamp: anchorTs, value: Number.isFinite(slPrice) ? slPrice : entryPrice },
        ],
        extendData: {
          id: overlayId,
          symbol: String(overlay.symbol || DEFAULT_SYMBOL.ticker),
          side: String(overlay.side || "LONG").toUpperCase(),
          tpAvailable,
          slAvailable,
          dragHighlightField:
            positionOverlayStore.dragHighlightById[overlayId] || null,
          closed: !!overlay.closed,
        },
      };
    })
    .filter(Boolean);

  if (!overlayConfigs.length) return;
  chart.createOverlay(overlayConfigs);
}

// 포지션 오버레이 등록
export function ensurePositionOverlayRegistered() {
  if (positionOverlayRegistered) return;

  registerOverlay({
    name: POSITION_OVERLAY_NAME,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    lock: true,
    totalStep: 3,

    onPressedMoveStart: (event) => {
      if (event?.isTouch) return;

      const bridge = getPositionOverlayDragStartBridge();
      if (typeof bridge !== "function") return;

      const field = parsePositionOverlayDragFieldByFigure(event?.figure);
      if (field !== "tp" && field !== "sl") return;

      const overlayId = String(event?.overlay?.extendData?.id || "");
      if (!overlayId) return;

      bridge({
        overlayId,
        field,
        paneId: event?.overlay?.paneId || "candle_pane",
        y: Number(event?.y),
        event,
      });
    },

    createPointFigures: ({ overlay, coordinates, bounding }) => {
      if (!coordinates || coordinates.length < 2) return [];

      const extend = overlay.extendData || {};
      const slAvailable = !!extend.slAvailable;
      const tpAvailable = !!extend.tpAvailable;
      const dragHighlightField =
        extend.dragHighlightField === "tp" || extend.dragHighlightField === "sl"
          ? extend.dragHighlightField
          : null;

      const entryPoint = coordinates[0];
      const tpPoint = coordinates[1];
      const slPoint = coordinates[2] || coordinates[0];

      const xStart = entryPoint.x;
      const xEnd = bounding.left + bounding.width;

      const left = Math.min(xStart, xEnd);
      const right = Math.max(xStart, xEnd);
      const width = Math.max(1, right - left);

      const yEntry = entryPoint.y;
      const yTp = tpPoint.y;
      const ySl = slPoint.y;

      const rewardFill = "rgba(105, 200, 108, 0.12)";
      const riskFill = "rgba(242, 54, 69, 0.12)";

      const entryLineColor = "rgba(105, 200, 108, 0.9)";
      const tpLineColor =
        dragHighlightField === "tp" ? "#FFFFFF" : "rgba(105, 200, 108, 0.9)";
      const slLineColor =
        dragHighlightField === "sl" ? "#FFFFFF" : "rgba(242, 54, 69, 0.9)";

      const ignoreAll = true;
      const figures = [];

      if (tpAvailable) {
        const rewardTop = Math.min(yEntry, yTp);
        const rewardHeight = Math.max(1, Math.abs(yEntry - yTp));
        figures.push({
          type: "rect",
          attrs: { x: left, y: rewardTop, width, height: rewardHeight },
          styles: { style: "fill", color: rewardFill },
          ignoreEvent: ignoreAll,
        });
      }

      if (slAvailable) {
        const riskTop = Math.min(yEntry, ySl);
        const riskHeight = Math.max(1, Math.abs(yEntry - ySl));
        figures.push({
          type: "rect",
          attrs: { x: left, y: riskTop, width, height: riskHeight },
          styles: { style: "fill", color: riskFill },
          ignoreEvent: ignoreAll,
        });
      }

      figures.push({
        type: "line",
        attrs: { coordinates: [{ x: left, y: yEntry }, { x: right, y: yEntry }] },
        styles: { style: "solid", color: entryLineColor, size: 1 },
        ignoreEvent: ignoreAll,
      });

      if (tpAvailable) {
        if (dragHighlightField === "tp") {
          figures.push(
            ...buildMovingDashedLineFigures({
              left,
              right,
              y: yTp,
              color: tpLineColor,
              size: 1,
              ignoreEvent: ignoreAll,
            })
          );
        } else {
          figures.push({
            type: "line",
            attrs: { coordinates: [{ x: left, y: yTp }, { x: right, y: yTp }] },
            styles: { style: "dashed", color: tpLineColor, size: 1, dashedValue: [8, 8] },
            ignoreEvent: ignoreAll,
          });
        }
      }

      if (slAvailable) {
        if (dragHighlightField === "sl") {
          figures.push(
            ...buildMovingDashedLineFigures({
              left,
              right,
              y: ySl,
              color: slLineColor,
              size: 1,
              ignoreEvent: ignoreAll,
            })
          );
        } else {
          figures.push({
            type: "line",
            attrs: { coordinates: [{ x: left, y: ySl }, { x: right, y: ySl }] },
            styles: { style: "dashed", color: slLineColor, size: 1, dashedValue: [8, 8] },
            ignoreEvent: ignoreAll,
          });
        }
      }

      return figures;
    },

    createYAxisFigures: ({ overlay, coordinates, bounding, yAxis, precision }) => {
      if (!coordinates || coordinates.length < 1) return [];

      const extend = overlay.extendData || {};
      const overlayId = String(extend.id || "");
      const tpAvailable = !!extend.tpAvailable;
      const slAvailable = !!extend.slAvailable;
      const isClosed = !!extend.closed;

      const points = overlay.points || [];
      const entryVal = points[0]?.value;
      const tpVal = points[1]?.value;
      const slVal = points[2]?.value;

      const pricePrecision =
        precision && typeof precision.price === "number" ? precision.price : 2;

      const formatPrice = (value) =>
        typeof value === "number" && Number.isFinite(value)
          ? value.toFixed(pricePrecision)
          : "";

      const isFromZero = yAxis?.isFromZero?.() ?? false;
      const textAlign = isFromZero ? "left" : "right";
      const axisSide = isFromZero ? "left" : "right";
      const x = isFromZero ? 0 : bounding.width;

      const entryBg = "rgba(105, 200, 108, 0.95)";
      const tpBg = "rgba(105, 200, 108, 0.95)";
      const slBg = "rgba(242, 54, 69, 0.95)";

      const baseStyles = {
        color: "#FFFFFF",
        backgroundColor: "transparent",
        borderColor: "transparent",
        borderSize: 0,
        paddingLeft: 6,
        paddingRight: 6,
        paddingTop: 3,
        paddingBottom: 3,
      };

      const paneId = overlay?.paneId || "candle_pane";
      const key = yAxisKeyFromOverlay(overlay, yAxis);

      reserveLastPriceLabelY(key, paneId);

      const makeLabel = (baseY, text, backgroundColor, field = null) => {
        const y = allocY(key, baseY, bounding);
        if (y == null) return null;

        if (!isClosed && overlayId && (field === "tp" || field === "sl")) {
          registerPositionOverlayAxisLabelHitbox({
            overlayId,
            field,
            paneId,
            y,
            axisSide,
            text,
            fontSize: 12,
            paddingLeft: 6,
            paddingRight: 6,
            paddingTop: 3,
            paddingBottom: 3,
          });
        }

        const figureKey =
          field === "tp"
            ? POSITION_OVERLAY_YAXIS_DRAG_TP_KEY
            : field === "sl"
              ? POSITION_OVERLAY_YAXIS_DRAG_SL_KEY
              : null;

        return {
          ...(figureKey ? { key: figureKey } : {}),
          type: "text",
          attrs: { x, y, text, align: textAlign, baseline: "middle" },
          styles: { ...baseStyles, backgroundColor },
        };
      };

      const figures = [];

      if (typeof entryVal === "number" && Number.isFinite(entryVal) && coordinates[0]) {
        const figure = makeLabel(coordinates[0].y, `${formatPrice(entryVal)}`, entryBg);
        if (figure) figures.push(figure);
      }

      if (tpAvailable && typeof tpVal === "number" && Number.isFinite(tpVal) && coordinates[1]) {
        const figure = makeLabel(coordinates[1].y, `TP ${formatPrice(tpVal)}`, tpBg, "tp");
        if (figure) figures.push(figure);
      }

      if (slAvailable && typeof slVal === "number" && Number.isFinite(slVal) && coordinates[2]) {
        const figure = makeLabel(coordinates[2].y, `SL ${formatPrice(slVal)}`, slBg, "sl");
        if (figure) figures.push(figure);
      }

      return figures;
    },
  });

  positionOverlayRegistered = true;
}
