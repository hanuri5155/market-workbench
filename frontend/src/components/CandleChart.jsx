//// frontend/src/components/CandleChart.jsx

// ICT 조건이 겹치는 진입 후보(zone)와 포지션 오버레이를 한 화면에서 확인하고 토글하는 메인 차트

import { useEffect, useRef, useState, useCallback } from "react";
import { init, dispose, registerOverlay } from "klinecharts";
import { saveZoneStateToServer } from "../api/zoneState";
import { updatePositionTpsl } from "../api/positionTpsl";
import { useZoneNotifications } from "../contexts/ZoneNotificationContext";
import {
  DEFAULT_SYMBOL,
  ENABLE_VISIBILITY_RESYNC,
  ZONE_OVERLAY_NAME,
  INDICATOR_TOOLTIP_TOGGLE_FEATURE_ID,
  INDICATOR_TOOLTIP_TOGGLE_NAME,
  INDICATOR_TOOLTIP_TOGGLE_PRESS_MAX_AGE_MS,
  LIVE_POLL_INTERVAL_MS,
  LIVE_POLL_TIMEOUT_MS,
  MTF_MA_SOURCE_INIT_LIMIT,
  MTF_MA_SOURCE_POLL_INTERVAL_MS,
  MTF_MA_SOURCE_TF_LIST,
  PAGE_LIMIT,
  POSITION_LABEL_AXIS_DOM_HIT_OVERFLOW_PX,
  POSITION_LABEL_FALLBACK_HEIGHT_PX,
  POSITION_LABEL_FALLBACK_WIDTH_PX,
  POSITION_LABEL_MOUSE_X_HIT_PADDING_PX,
  POSITION_LABEL_MOUSE_Y_HIT_PADDING_PX,
  POSITION_LABEL_STALE_MS,
  POSITION_LABEL_X_HIT_PADDING_PX,
  POSITION_LABEL_Y_HIT_PADDING_PX,
  POSITION_TOUCH_AXIS_OVERFLOW_PX,
  POSITION_TOUCH_LONG_PRESS_CANCEL_RADIUS_PX,
  POSITION_TOUCH_LONG_PRESS_MS,
  RESYNC_COOLDOWN_MS,
  RESYNC_DEBOUNCE_MS,
  RESYNC_LIMIT,
  TF_LIST,
} from "./candle-chart/constants";
import {
  formatTfButtonLabel,
  mapServerCandle,
  periodToTf,
  resolveMainPaneId,
  safeJson,
  tfToPeriod,
} from "./candle-chart/chartUtils";
import {
  ensureAllZoneBoxesLoadedOnce,
} from "./candle-chart/zones/api";
import {
  applyZoneOverlays,
  clearZoneHoverByBridge,
} from "./candle-chart/zones/overlay";
import {
  getHoveredZoneId,
  setZoneChart,
  toggleZoneActiveById,
} from "./candle-chart/zones/store";
import { connectZoneStateWs } from "./candle-chart/zones/ws";
import { useChartInstance } from "./candle-chart/hooks/useChartInstance";
import { useZoneNotificationSync } from "./candle-chart/hooks/useZoneNotifications";
import {
  applyPositionOverlays,
  clearPositionOverlayRetryTimer,
  ensurePositionOverlayDashAnimation,
  ensurePositionOverlayRegistered,
  getPositionOverlayAxisLabelHitboxes,
  measurePositionLabelWidthPx,
  stopPositionOverlayDashAnimation,
} from "./candle-chart/positionOverlay/overlay";
import {
  addPositionOverlayPending,
  clearPositionOverlayDragHighlight as clearPositionOverlayDragHighlightState,
  clearPositionOverlayPreview as clearPositionOverlayPreviewState,
  getPositionOverlay,
  getPositionOverlayDragStartBridge,
  getPositionOverlayLastPrice,
  hasPositionOverlayDragHighlight,
  hasPositionOverlayPending,
  positionOverlayStore,
  removePositionOverlayPending,
  resetPositionOverlayRuntimeState,
  setPositionOverlayChart,
  setPositionOverlayDragHighlight as setPositionOverlayDragHighlightState,
  setPositionOverlayDragStartBridge,
  setPositionOverlayLastPrice,
  setPositionOverlayPreview as setPositionOverlayPreviewState,
  upsertPositionOverlay,
} from "./candle-chart/positionOverlay/store";
import {
  connectPositionOverlayWs,
  fetchAndApplyPositionOverlaySnapshot,
} from "./candle-chart/positionOverlay/ws";
import {
  createMtfMaIndicator,
  ensureMtfMaIndicatorRegistered,
  fetchMtfMaSourceCandles,
  fetchMtfMaSourceLatest,
  refreshMtfMaIndicator as refreshMtfMaIndicatorByChart,
  resetMtfMaIndicatorRuntimeState,
} from "./candle-chart/indicators/mtfMa";
import {
  ensureIndicatorTooltipToggleRegistered,
  getIndicatorTooltipToggleHovered,
  markTooltipToggleClickHint,
  prioritizeMainTooltipFeatureEvents,
  recreateIndicatorTooltipToggle as recreateIndicatorTooltipToggleForChart,
  resetIndicatorTooltipToggleRuntimeState,
  setIndicatorTooltipToggleHovered,
  shouldSuppressZoneClick,
  syncIndicatorTooltipToggleHovered,
  toggleIndicatorTooltipCollapsed as toggleIndicatorTooltipCollapsedForChart,
} from "./candle-chart/indicators/tooltipToggle";
import "./CandleChart.css";

// 캔들 캐시
const CANDLE_CACHE = {};

// TF 재초기화 플래그
const FORCE_TF_RESYNC_ON_NEXT_INIT = {};

ensurePositionOverlayRegistered();

for (const tf of TF_LIST) {
  FORCE_TF_RESYNC_ON_NEXT_INIT[tf] = false;
}

// REST 확정 캔들 캐시 반영
function patchRestConfirmedCandleInCache(tfStr, candle) {
  const cache = CANDLE_CACHE[tfStr];
  if (!cache || !Array.isArray(cache.list) || cache.list.length === 0) {
    return { updated: false, newBar: null };
  }

  const arr = cache.list;

  const targetStart = Number(candle.start);
  let idx = arr.findIndex((bar) => Number(bar.timestamp) === targetStart);

  const base = idx >= 0 ? arr[idx] : {};

  const newBar = {
    ...base,
    timestamp: targetStart,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  };

  if (idx >= 0) {
    arr[idx] = newBar;
  } else {
    arr.push(newBar);
    // 정렬 유지
    arr.sort((a, b) => a.timestamp - b.timestamp);
  }

  cache.earliestTs = arr[0]?.timestamp ?? cache.earliestTs;

  return { updated: true, newBar };
}

// Structure Zone 오버레이 클릭과 hover 동작을 차트에 연결하기 위함
registerOverlay({
  name: ZONE_OVERLAY_NAME,
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: false,
  needDefaultYAxisFigure: false,
  lock: true,
  totalStep: 2,

  onRightClick: () => true,

  // 오버레이 클릭 시 활성화 상태를 토글하고 서버에도 저장하기 위함
  onClick: (event) => {
    if (shouldSuppressZoneClick(event)) {
      return true;
    }

    const { overlay } = event;
    if (!overlay) return true;

    const extend = overlay.extendData || {};
    const persistPayload = toggleZoneActiveById(extend.id);
    if (!persistPayload) return true;

    applyZoneOverlays();
    saveZoneStateToServer(persistPayload);

    return true;
  },

  // 커서가 Zone 위에 들어오면 알림 패널과 같은 hover id를 쓰기 위함
  onMouseEnter: (event) => {
    const toggleHovered =
      getIndicatorTooltipToggleHovered() ||
      syncIndicatorTooltipToggleHovered(event?.chart);
    if (toggleHovered) {
      clearZoneHoverByBridge();
      return false;
    }

    const overlay = event?.overlay;
    const id = overlay?.extendData?.id;
    if (!id) return false;

    if (
      typeof window !== "undefined" &&
      typeof window.__setZoneHoveredId === "function"
    ) {
      window.__setZoneHoveredId(id);
    }
    return true;
  },

  onMouseMove: (event) => {
    const toggleHovered =
      getIndicatorTooltipToggleHovered() ||
      syncIndicatorTooltipToggleHovered(event?.chart);
    if (toggleHovered) {
      clearZoneHoverByBridge();
      return false;
    }
    return true;
  },

  // 커서가 Zone 영역에서 벗어나면 hover 표시를 지우기 위함
  onMouseLeave: () => {
    clearZoneHoverByBridge();
    return true;
  },

  createPointFigures: ({ overlay, coordinates, bounding }) => {
    if (!coordinates || coordinates.length < 2) return [];

    const [p1, p2] = coordinates;
    const extend = overlay.extendData || {};
    const side = extend.side || "LONG";      // "LONG" | "SHORT"
    const isBroken = !!extend.isBroken;      // true면 깨진 5캔들
    const tf = extend.tf || "15";            // "15" | "30" | "60" | "240"
    const isActive = !!extend.isActive;      // 활성 여부(실매매 대상)
    const id = extend.id;                    //  박스 id
    const hoveredBoxId = extend.hoveredBoxId ?? null;

    const isHovered =
      !isBroken &&
      !getIndicatorTooltipToggleHovered() &&
      typeof id === "string" &&
      hoveredBoxId != null &&
      id === hoveredBoxId;

    // x 좌표 계산
    const xStart = p1.x;
    let xEnd;
    if (isBroken) {
      // 깨진 경우: 두 번째 포인트까지만
      xEnd = p2.x;
    } else {
      // 안 깨진 경우: 현재 차트 오른쪽 끝까지 익스텐드
      xEnd = bounding.left + bounding.width;
    }

    const left = Math.min(xStart, xEnd);
    const right = Math.max(xStart, xEnd);

    // y 좌표 (두 점의 위/아래)
    const yTop = Math.min(p1.y, p2.y);
    const yBottom = Math.max(p1.y, p2.y);

    const width = Math.max(1, right - left);
    const height = Math.max(1, yBottom - yTop);

    // 
    //  스타일 분기
    //   - 깨진 박스: 기존 그대로 (회색 박스, 테두리 없음)
    //   - 안 깨진 박스:
    //       · 롱: rgba(8, 153, 129, α)
    //       · 숏: rgba(242, 54, 69, α)
    //       · α = 0.10 (기본) / 0.50 (활성)
    // 
    let fillColor;
    let strokeColor;
    let borderSize;
    let style;        // rect 스타일: 'fill' | 'stroke' | 'stroke_fill'
    let borderStyle;  // 'solid' | 'dashed'

    if (isBroken) {
      // 깨진 박스: 회색 박스, 테두리 없음 (기존 유지)
      fillColor = "rgba(128, 128, 128, 0.10)";
      strokeColor = "transparent";
      borderSize = 0;
      style = "fill";
      borderStyle = "solid";
    } else {
      // 안 깨진 박스: 롱/숏 색 + 투명도 토글
      if (side === "LONG") {
        fillColor = isActive
          ? "rgba(8, 153, 129, 0.50)"
          : "rgba(8, 153, 129, 0.10)";
      } else {
        fillColor = isActive
          ? "rgba(242, 54, 69, 0.50)"
          : "rgba(242, 54, 69, 0.10)";
      }

      if (isHovered) {
        //  호버된 박스: 하얀 테두리 + 약간 더 두껍게
        strokeColor = "#FFFFFF";
        borderSize = 2;
        style = "stroke_fill";
      } else {
        // 기본: 테두리 없음
        strokeColor = "transparent";
        borderSize = 0;
        style = "fill";
      }

      borderStyle = "solid";
    }

    //  타임프레임 라벨 텍스트 (예: "15", "30")
    const tfLabel = `${tf}`;

    // 텍스트 위치: 기존 로직 그대로 사용
    let labelX;
    let labelY;
    if (isBroken) {
      // 박스 중앙
      labelX = left + width / 2 - 20;
      labelY = yTop;
    } else {
      // 우측 내부
      labelX = right - 20;
      labelY = yTop;
    }

    const labelColor = "#FFF";

    const figures = [
      // 1) 박스(Rect)
      {
        type: "rect",
        attrs: {
          x: left,
          y: yTop,
          width,
          height,
        },
        styles: {
          style,
          color: fillColor,
          borderStyle,
          borderColor: strokeColor,
          borderSize,
        },
        ignoreEvent: ["onRightClick"], // 우클릭(삭제) 무시
      },
    ];

    if (!isBroken) {
      figures.push({
        // 2) 타임프레임 텍스트
        type: "text",
        attrs: {
          x: labelX,
          y: labelY,
          text: tfLabel,
        },
        styles: {
          color: labelColor,
          size: 10,
          backgroundColor: "transparent",
          borderSize: 0,
          borderColor: "transparent",
          paddingLeft: 0,
          paddingRight: 0,
          paddingTop: 0,
          paddingBottom: 0,
        },
      });
    }

    return figures;
  },

  //    - 활성화된(선택된) & 안 깨진 5캔들 박스만
  //      가격축(Y축)에 상단/하단 가격 텍스트 표시
  createYAxisFigures: ({ overlay, coordinates, bounding, yAxis, precision }) => {
    // 좌표/포인트가 부족하면 아무 것도 그리지 않음
    if (!coordinates || coordinates.length < 2) return [];

    const extend = overlay.extendData || {};
    const isBroken = !!extend.isBroken;
    const isActive = !!extend.isActive;

    //  롱/숏 구분용 side
    const sideRaw = extend.side || "LONG";
    const side = String(sideRaw).toUpperCase(); // "LONG" | "SHORT"

    // 깨진 박스이거나 비활성 박스면 가격축에 표시하지 않음
    if (isBroken || !isActive) return [];

    // 가격축이 왼쪽/오른쪽 어느 쪽에 있는지에 따라 정렬/위치 결정
    const isFromZero = yAxis?.isFromZero?.() ?? false;
    const textAlign = isFromZero ? "left" : "right";
    const x = isFromZero ? 0 : bounding.width;

    const points = overlay.points || [];
    const topVal = points[0]?.value;
    const bottomVal = points[1]?.value;

    // klinecharts에서 넘겨주는 price 정밀도 사용 (없으면 2자리)
    const pricePrecision =
      precision && typeof precision.price === "number"
        ? precision.price
        : 2;

    const formatPrice = (v) =>
      typeof v === "number" && Number.isFinite(v)
        ? v.toFixed(pricePrecision)
        : "";

    //  5캔들 박스 롱 / 숏 라벨 색상 정의
    const labelBgColor =
      side === "SHORT"
        ? "rgba(242, 54, 69, 0.9)"   // 숏: 빨간 배경
        : "rgba(8, 153, 129, 0.9)";  // 롱: 초록 배경

    const baseStyles = {
      color: "#FFFFFF",
      backgroundColor: labelBgColor,
      borderColor: "transparent",
      borderSize: 0,
      paddingLeft: 4,
      paddingRight: 4,
      paddingTop: 2,
      paddingBottom: 2,
    };

    const figures = [];

    const pushLabel = (baseY, text) => {
      figures.push({
        type: "text",
        attrs: { x, y: baseY, text, align: textAlign, baseline: "middle" },
        styles: { ...baseStyles },
      });
    };

    // 상단 가격(label: upper)
    if (typeof topVal === "number" && Number.isFinite(topVal)) {
      pushLabel(coordinates[0].y, formatPrice(topVal));
    }

    // 하단 가격(label: lower)
    if (typeof bottomVal === "number" && Number.isFinite(bottomVal)) {
      pushLabel(coordinates[1].y, formatPrice(bottomVal));
    }

    return figures;
  },
});

function calcCandleOnlyAxisRange(chart, defaultRange) {
  if (
    !chart ||
    typeof chart.getDataList !== "function" ||
    typeof chart.getVisibleRange !== "function"
  ) {
    return defaultRange;
  }

  const dataList = chart.getDataList() || [];
  if (!Array.isArray(dataList) || dataList.length === 0) {
    return defaultRange;
  }

  const visible = chart.getVisibleRange?.();
  const rawFrom = Number.isFinite(visible?.realFrom) ? visible.realFrom : visible?.from;
  const rawTo = Number.isFinite(visible?.realTo) ? visible.realTo : visible?.to;
  if (!Number.isFinite(rawFrom) || !Number.isFinite(rawTo)) {
    return defaultRange;
  }

  const start = Math.max(0, Math.floor(rawFrom));
  const endExclusive = Math.min(dataList.length, Math.ceil(rawTo));
  if (endExclusive <= start) {
    return defaultRange;
  }

  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;

  for (let i = start; i < endExclusive; i += 1) {
    const bar = dataList[i];
    if (!bar) continue;

    const low = Number(bar.low);
    const high = Number(bar.high);
    if (Number.isFinite(low)) min = Math.min(min, low);
    if (Number.isFinite(high)) max = Math.max(max, high);
  }

  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return defaultRange;
  }

  if (max <= min) {
    const span = Math.max(Number(defaultRange?.realRange) || 1, 1);
    min -= span * 0.5;
    max += span * 0.5;
  }

  const range = max - min;
  return {
    from: min,
    to: max,
    range,
    realFrom: min,
    realTo: max,
    realRange: range,
    displayFrom: min,
    displayTo: max,
    displayRange: range,
  };
}

function applyCandleOnlyYAxisAutoRange(chart) {
  if (!chart || typeof chart.setPaneOptions !== "function") return;
  const mainPaneId = resolveMainPaneId(chart);

  try {
    chart.setPaneOptions({
      id: mainPaneId,
      axis: {
        createRange: ({ chart: rangeChart, paneId, defaultRange }) => {
          if (!defaultRange) return defaultRange;

          try {
            if (typeof rangeChart?.getPaneOptions === "function") {
              const paneOptions = rangeChart.getPaneOptions(paneId);
              if (paneOptions && !Array.isArray(paneOptions)) {
                const axisName = paneOptions.axis?.name;
                if (typeof axisName === "string" && axisName !== "normal") {
                  return defaultRange;
                }
              }
            }

            return calcCandleOnlyAxisRange(rangeChart, defaultRange);
          } catch (e) {
            console.warn("[CandleChart] candle-only createRange 실패:", e);
            return defaultRange;
          }
        },
      },
    });
  } catch (e) {
    console.warn("[CandleChart] candle-only Y축 range 적용 실패:", e);
  }
}

function cloneAxisRange(range) {
  if (!range || typeof range !== "object") return null;

  const snapshot = {
    from: Number(range.from),
    to: Number(range.to),
    range: Number(range.range),
    realFrom: Number(range.realFrom),
    realTo: Number(range.realTo),
    realRange: Number(range.realRange),
    displayFrom: Number(range.displayFrom),
    displayTo: Number(range.displayTo),
    displayRange: Number(range.displayRange),
  };

  return Number.isFinite(snapshot.from) &&
    Number.isFinite(snapshot.to) &&
    Number.isFinite(snapshot.range) &&
    Number.isFinite(snapshot.realFrom) &&
    Number.isFinite(snapshot.realTo) &&
    Number.isFinite(snapshot.realRange) &&
    Number.isFinite(snapshot.displayFrom) &&
    Number.isFinite(snapshot.displayTo) &&
    Number.isFinite(snapshot.displayRange)
    ? snapshot
    : null;
}

function captureManualMainYAxisRange(chart) {
  if (!chart || typeof chart.getDrawPaneById !== "function") return null;

  try {
    const mainPaneId = resolveMainPaneId(chart);
    const pane = chart.getDrawPaneById(mainPaneId);
    const yAxis = pane?.getAxisComponent?.();
    if (
      !yAxis ||
      typeof yAxis.getAutoCalcTickFlag !== "function" ||
      typeof yAxis.getRange !== "function"
    ) {
      return null;
    }

    // 자동 계산 상태면 복원할 수동 범위가 없으므로 패스
    if (yAxis.getAutoCalcTickFlag()) return null;

    const range = cloneAxisRange(yAxis.getRange());
    if (!range) return null;

    return { paneId: mainPaneId, range };
  } catch (e) {
    console.warn("[CandleChart] 수동 Y축 범위 캡처 실패:", e);
    return null;
  }
}

function restoreManualMainYAxisRange(chart, snapshot) {
  if (!chart || !snapshot || typeof chart.getDrawPaneById !== "function") return;

  try {
    const pane = chart.getDrawPaneById(snapshot.paneId);
    const yAxis = pane?.getAxisComponent?.();
    if (!yAxis || typeof yAxis.setRange !== "function") return;

    const range = cloneAxisRange(snapshot.range);
    if (!range) return;

    yAxis.setRange(range);

    // setRange는 내부 축 상태만 바꾸므로 즉시 다시 그리기
    if (typeof chart.layout === "function") {
      chart.layout({
        measureWidth: true,
        update: true,
        buildYAxisTick: true,
        forceBuildYAxisTick: true,
      });
    }
  } catch (e) {
    console.warn("[CandleChart] 수동 Y축 범위 복원 실패:", e);
  }
}

export default function CandleChart() {
  // 차트 DOM / 런타임 refs
  const hostRef = useRef(null);
  const chartRef = useRef(null);
  const latestPriceRef = useRef(null);
  const zoomAnchorRef = useRef(null);
  const zoomAnchorInputRef = useRef(null);
  const zoomAnchorEndRequestedRef = useRef(false);
  const wheelZoomEndTimerRef = useRef(null);
  const pendingForwardLoadCountRef = useRef(0);
  const xAxisDragZoomRef = useRef(null);
  const yAxisDragZoomRef = useRef(null);
  const lastCrosshairPayloadRef = useRef(null);

  // 차트와 알림 패널이 같은 Zone 목록과 hover 상태를 공유하기 위함
  const {
    setItems: setNotificationItems,
    hoveredBoxId,
    setHoveredBoxId,
  } = useZoneNotifications();

  // 화면 상태
  const [tf, setTf] = useState("15");
  const [showBbands, setShowBbands] = useState(true);
  const [chartEpoch, setChartEpoch] = useState(0);

  // 차트 보조 액션
  const recreateIndicatorTooltipToggle = useCallback((chart) => {
    recreateIndicatorTooltipToggleForChart(chart, lastCrosshairPayloadRef.current);
  }, []);

  const toggleIndicatorTooltipCollapsed = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;

    toggleIndicatorTooltipCollapsedForChart(
      chart,
      lastCrosshairPayloadRef.current
    );
  }, []);

  const refreshMtfMaIndicator = useCallback((reason = "unknown") => {
    refreshMtfMaIndicatorByChart(chartRef.current, reason);
  }, []);

  const markForceTfResyncOnNextInit = useCallback((tfStr) => {
    if (!TF_LIST.includes(tfStr)) return;
    FORCE_TF_RESYNC_ON_NEXT_INIT[tfStr] = true;
  }, []);

  // Zone 오버레이, 알림 패널, 실시간 캔들 이벤트를 한 흐름으로 묶기 위함
  const refreshZoneNotifications = useZoneNotificationSync({
    chartRef,
    latestPriceRef,
    tf,
    hoveredBoxId,
    setHoveredBoxId,
    setNotificationItems,
    refreshMtfMaIndicator,
    patchRestConfirmedCandleInCache,
  });

  const { handleScrollToLatest } = useChartInstance({
    chartRef,
    chartEpoch,
    tf,
    showBbands,
    refreshZoneNotifications,
    recreateIndicatorTooltipToggle,
    captureManualMainYAxisRange,
    restoreManualMainYAxisRange,
    markForceTfResyncOnNextInit,
  });

  // 차트 초기화 / 데이터 로더
  useEffect(() => {
    if (!hostRef.current) return;
    const el = hostRef.current;

    const chart = init(el, {
      zoomAnchor: {
        main: "cursor",
        xAxis: "cursor",
      },
    });
    chartRef.current = chart;
    setChartEpoch((prev) => prev + 1);
    setIndicatorTooltipToggleHovered(false);

    const resolveMainPaneIdForActions = () => {
      if (typeof chart.getPaneOptions !== "function") return "candle_pane";
      try {
        const panes = chart.getPaneOptions();
        if (Array.isArray(panes) && panes[0]?.id) {
          return panes[0].id;
        }
      } catch {
        // ignore
      }
      return "candle_pane";
    };

    const getViewportRightAnchor = () => {
      const dataList = chart.getDataList?.() || [];
      if (!dataList.length) return null;

      const paneId = resolveMainPaneIdForActions();
      const size =
        chart.getSize?.(paneId, "main") || chart.getSize?.(paneId);
      const bounding = size?.bounding || size;
      const paneWidth = Number(bounding?.width);

      if (Number.isFinite(paneWidth) && paneWidth > 1) {
        try {
          const converted =
            chart.convertFromPixel?.(
              [{ x: paneWidth - 1 }],
              { paneId, absolute: false }
            ) || [];
          const point = Array.isArray(converted) ? converted[0] : converted;

          const tsRaw = point?.timestamp;
          const diRaw = point?.dataIndex;
          const ts =
            typeof tsRaw === "number" && Number.isFinite(tsRaw) ? tsRaw : null;
          const di =
            typeof diRaw === "number" && Number.isFinite(diRaw) ? diRaw : null;

          if (ts != null || di != null) {
            return {
              timestamp: ts,
              dataIndex: di,
            };
          }
        } catch {
          // ignore
        }
      }

      const range = chart.getVisibleRange?.();
      const rawTo = Number(range?.to);
      const fallbackDataIndex = Number.isFinite(rawTo)
        ? Math.min(dataList.length - 1, Math.max(0, Math.ceil(rawTo) - 1))
        : dataList.length - 1;
      const fallbackTimestampRaw = dataList[fallbackDataIndex]?.timestamp;
      const fallbackTimestamp =
        typeof fallbackTimestampRaw === "number" &&
        Number.isFinite(fallbackTimestampRaw)
          ? fallbackTimestampRaw
          : null;

      return {
        timestamp: fallbackTimestamp,
        dataIndex: fallbackDataIndex,
      };
    };

    const RESYNC_RESTORE_MAX_RETRY = 24;

    const captureLatestCandleViewport = () => {
      const dataList = chart.getDataList?.() || [];
      if (!dataList.length) return null;

      const paneId = resolveMainPaneIdForActions();
      const size =
        chart.getSize?.(paneId, "main") || chart.getSize?.(paneId);
      const bounding = size?.bounding || size;
      const paneWidth = Number(bounding?.width);
      if (!Number.isFinite(paneWidth) || paneWidth <= 1) return null;

      const latestTimestampRaw = dataList[dataList.length - 1]?.timestamp;
      const latestTimestamp =
        typeof latestTimestampRaw === "number" && Number.isFinite(latestTimestampRaw)
          ? latestTimestampRaw
          : null;
      if (latestTimestamp == null) return null;

      const pixel =
        chart.convertToPixel?.(
          { timestamp: latestTimestamp },
          { paneId, absolute: false }
        ) || {};
      const latestX = Number(pixel?.x);
      if (!Number.isFinite(latestX)) return null;

      return {
        latestOffsetFromRight: (paneWidth - 1) - latestX,
      };
    };

    const restoreLatestCandleViewport = (snapshot) => {
      if (!snapshot) return true;

      const dataList = chart.getDataList?.() || [];
      if (!dataList.length) return false;

      const paneId = resolveMainPaneIdForActions();
      const size =
        chart.getSize?.(paneId, "main") || chart.getSize?.(paneId);
      const bounding = size?.bounding || size;
      const paneWidth = Number(bounding?.width);
      if (!Number.isFinite(paneWidth) || paneWidth <= 1) return false;

      const latestTimestampRaw = dataList[dataList.length - 1]?.timestamp;
      const latestTimestamp =
        typeof latestTimestampRaw === "number" && Number.isFinite(latestTimestampRaw)
          ? latestTimestampRaw
          : null;
      if (latestTimestamp == null) return false;

      const pixel =
        chart.convertToPixel?.(
          { timestamp: latestTimestamp },
          { paneId, absolute: false }
        ) || {};
      const currentX = Number(pixel?.x);
      if (!Number.isFinite(currentX)) return false;

      const targetX = (paneWidth - 1) - snapshot.latestOffsetFromRight;
      const distance = targetX - currentX;
      if (Number.isFinite(distance) && Math.abs(distance) >= 0.5) {
        chart.scrollByDistance?.(distance);
      }

      return true;
    };

    const scheduleLatestCandleViewportRestore = (snapshot) => {
      if (!snapshot) return;

      const raf =
        typeof requestAnimationFrame === "function"
          ? requestAnimationFrame
          : (fn) => setTimeout(fn, 0);

      let attempts = 0;
      const tryRestore = () => {
        if (destroyed) return;

        attempts += 1;
        const restored = restoreLatestCandleViewport(snapshot);
        if (!restored && attempts < RESYNC_RESTORE_MAX_RETRY) {
          raf(tryRestore);
        }
      };

      raf(tryRestore);
    };

    const clearWheelZoomEndTimer = () => {
      if (!wheelZoomEndTimerRef.current) return;
      clearTimeout(wheelZoomEndTimerRef.current);
      wheelZoomEndTimerRef.current = null;
    };

    const maybeFinalizeZoomAnchorGesture = () => {
      if (!zoomAnchorEndRequestedRef.current) return;
      if (pendingForwardLoadCountRef.current > 0) return;

      zoomAnchorRef.current = null;
      zoomAnchorInputRef.current = null;
      zoomAnchorEndRequestedRef.current = false;
      clearWheelZoomEndTimer();
    };

    const beginZoomAnchorGesture = (inputType) => {
      if (zoomAnchorRef.current == null) {
        zoomAnchorRef.current = getViewportRightAnchor();
      }
      zoomAnchorInputRef.current = inputType;
      zoomAnchorEndRequestedRef.current = false;
    };

    const requestZoomAnchorGestureEnd = () => {
      zoomAnchorEndRequestedRef.current = true;
      maybeFinalizeZoomAnchorGesture();
    };

    const scheduleWheelZoomEnd = () => {
      clearWheelZoomEndTimer();
      wheelZoomEndTimerRef.current = setTimeout(() => {
        requestZoomAnchorGestureEnd();
      }, 180);
    };

    const captureWheelZoomAnchor = () => {
      beginZoomAnchorGesture("wheel");
      scheduleWheelZoomEnd();
    };

    const capturePointerZoomAnchorStart = () => {
      beginZoomAnchorGesture("pointer");
    };

    const endPointerZoomAnchor = () => {
      if (zoomAnchorInputRef.current !== "pointer") return;
      requestZoomAnchorGestureEnd();
    };

    const X_AXIS_PANE_ID = "x_axis_pane";

    const getXAxisMainRect = () => {
      try {
        const xAxisMainDom = chart.getDom?.(X_AXIS_PANE_ID, "main");
        if (xAxisMainDom && typeof xAxisMainDom.getBoundingClientRect === "function") {
          return xAxisMainDom.getBoundingClientRect();
        }
      } catch {
        // ignore
      }

      const size =
        chart.getSize?.(X_AXIS_PANE_ID, "main") ||
        chart.getSize?.(X_AXIS_PANE_ID);
      const bounding = size?.bounding || size;
      const left = Number(bounding?.left);
      const top = Number(bounding?.top);
      const width = Number(bounding?.width);
      const height = Number(bounding?.height);
      if (
        Number.isFinite(left) &&
        Number.isFinite(top) &&
        Number.isFinite(width) &&
        Number.isFinite(height)
      ) {
        const hostRect = el.getBoundingClientRect();
        return {
          left: hostRect.left + left,
          top: hostRect.top + top,
          right: hostRect.left + left + width,
          bottom: hostRect.top + top + height,
          width,
          height,
        };
      }

      return null;
    };

    const getMainYAxisRect = () => {
      const paneId = resolveMainPaneIdForActions();

      try {
        const yAxisDom = chart.getDom?.(paneId, "yAxis");
        if (yAxisDom && typeof yAxisDom.getBoundingClientRect === "function") {
          return yAxisDom.getBoundingClientRect();
        }
      } catch {
        // ignore
      }

      const size = chart.getSize?.(paneId, "yAxis");
      const bounding = size?.bounding || size;
      const left = Number(bounding?.left);
      const top = Number(bounding?.top);
      const width = Number(bounding?.width);
      const height = Number(bounding?.height);
      if (
        Number.isFinite(left) &&
        Number.isFinite(top) &&
        Number.isFinite(width) &&
        Number.isFinite(height)
      ) {
        const hostRect = el.getBoundingClientRect();
        return {
          left: hostRect.left + left,
          top: hostRect.top + top,
          right: hostRect.left + left + width,
          bottom: hostRect.top + top + height,
          width,
          height,
        };
      }

      return null;
    };

    const getMainYAxisAxisContext = () => {
      if (typeof chart.getDrawPaneById !== "function") return null;

      try {
        const paneId = resolveMainPaneIdForActions();
        const pane = chart.getDrawPaneById(paneId);
        const yAxis = pane?.getAxisComponent?.();
        const mainWidget = pane?.getMainWidget?.();
        const mainBounding = mainWidget?.getBounding?.();
        const mainHeight = Number(mainBounding?.height);
        if (!yAxis || !Number.isFinite(mainHeight) || mainHeight <= 0) {
          return null;
        }
        return { yAxis, mainHeight };
      } catch {
        return null;
      }
    };

    const swallowNativeEvent = (event) => {
      event.preventDefault?.();
      event.stopPropagation?.();
      event.stopImmediatePropagation?.();
    };

    const detectMobileOrTabletTouchInput = () => {
      if (typeof navigator === "undefined") return false;

      const ua = navigator.userAgent || "";
      const platform = navigator.platform || "";
      const maxTouchPoints = Number(navigator.maxTouchPoints) || 0;

      const isIPadUA = /\biPad\b/i.test(ua);
      const isIPadOS = platform === "MacIntel" && maxTouchPoints > 1;
      if (isIPadUA || isIPadOS) return true;

      if (/\b(iPhone|iPod|Android|Mobile|Tablet)\b/i.test(ua)) {
        return true;
      }

      if (typeof window !== "undefined") {
        const coarsePointer =
          window.matchMedia?.("(pointer: coarse)")?.matches === true;
        if (coarsePointer && maxTouchPoints > 0) {
          return true;
        }
      }

      return false;
    };

    const shouldUsePositionTouchLongPress = detectMobileOrTabletTouchInput();

    const beginXAxisOnlyDragZoom = ({
      pageX,
      clientX,
      clientY,
      inputType,
      touchId = null,
    }) => {
      if (
        !Number.isFinite(pageX) ||
        !Number.isFinite(clientX) ||
        !Number.isFinite(clientY)
      ) {
        return false;
      }

      const xAxisRect = getXAxisMainRect();
      if (!xAxisRect) return false;

      const isInsideXAxis =
        clientX >= xAxisRect.left &&
        clientX <= xAxisRect.right &&
        clientY >= xAxisRect.top &&
        clientY <= xAxisRect.bottom;
      if (!isInsideXAxis) return false;

      const anchorX = clientX - xAxisRect.left;
      if (!Number.isFinite(anchorX)) return false;

      beginZoomAnchorGesture("pointer");
      xAxisDragZoomRef.current = {
        active: true,
        inputType,
        touchId,
        startPageX: pageX,
        prevScale: 1,
        anchorX,
      };
      return true;
    };

    const stepXAxisOnlyDragZoom = (pageX) => {
      const state = xAxisDragZoomRef.current;
      if (!state?.active) return;
      if (!Number.isFinite(pageX) || pageX === 0) return;

      const currentScale = state.startPageX / pageX;
      if (!Number.isFinite(currentScale) || currentScale <= 0) return;

      const scaleDelta = currentScale - state.prevScale;
      if (!Number.isFinite(scaleDelta) || Math.abs(scaleDelta) < 0.0001) return;
      state.prevScale = currentScale;

      const zoomScale = 1 + scaleDelta;
      if (!Number.isFinite(zoomScale) || zoomScale <= 0) return;

      chart.zoomAtCoordinate?.(zoomScale, { x: state.anchorX, y: 0 });
    };

    const beginYAxisOnlyDragZoom = ({
      pageY,
      clientX,
      clientY,
      inputType,
      touchId = null,
    }) => {
      if (
        !Number.isFinite(pageY) ||
        !Number.isFinite(clientX) ||
        !Number.isFinite(clientY)
      ) {
        return false;
      }

      const yAxisRect = getMainYAxisRect();
      if (!yAxisRect) return false;

      const isInsideYAxis =
        clientX >= yAxisRect.left &&
        clientX <= yAxisRect.right &&
        clientY >= yAxisRect.top &&
        clientY <= yAxisRect.bottom;
      if (!isInsideYAxis) return false;

      // TP/SL 라벨 드래그/클릭 시작점에서는 yAxis 줌 인터셉트 비활성
      const isTpslLabelTarget =
        resolvePositionLabelDragTarget(clientX, clientY, {
          labelCursorMode: inputType === "mouse",
          touchMode: inputType === "touch",
        }) != null;
      if (isTpslLabelTarget) return false;

      yAxisDragZoomRef.current = {
        active: false,
        inputType,
        touchId,
        startPageY: pageY,
        baseRange: null,
      };
      return true;
    };

    const stepYAxisOnlyDragZoom = ({ pageY, clientX, clientY }) => {
      const state = yAxisDragZoomRef.current;
      if (!Number.isFinite(pageY)) return;
      if (
        !Number.isFinite(clientX) ||
        !Number.isFinite(clientY) ||
        !state
      ) {
        return;
      }

      // TP/SL 라벨 드래그 중 yAxis 줌 개입 방지
      if (positionLabelDragState.active) {
        yAxisDragZoomRef.current = null;
        return;
      }

      // yAxis 영역 안에서는 기존 라이브러리 동작을 유지하고,
      // 영역 이탈 직후부터 y 기준 커스텀 줌으로 이어짐
      if (!state.active) {
        const yAxisRect = getMainYAxisRect();
        const stillInsideYAxis =
          yAxisRect &&
          clientX >= yAxisRect.left &&
          clientX <= yAxisRect.right &&
          clientY >= yAxisRect.top &&
          clientY <= yAxisRect.bottom;
        if (stillInsideYAxis) return;

        const axisContextForStart = getMainYAxisAxisContext();
        if (!axisContextForStart) {
          yAxisDragZoomRef.current = null;
          return;
        }
        const baseRangeForStart = cloneAxisRange(
          axisContextForStart.yAxis.getRange?.()
        );
        if (!baseRangeForStart) {
          yAxisDragZoomRef.current = null;
          return;
        }

        state.active = true;
        state.startPageY = pageY;
        state.baseRange = baseRangeForStart;
      }

      const startPageY = Number(state.startPageY);
      if (!Number.isFinite(startPageY) || startPageY === 0) return;

      const axisContext = getMainYAxisAxisContext();
      if (!axisContext) return;

      const yAxis = axisContext.yAxis;
      const baseRange = state.baseRange;
      if (!baseRange) return;
      const scale = pageY / startPageY;
      if (!Number.isFinite(scale) || scale <= 0) return;

      const newRange = baseRange.range * scale;
      const difRange = (newRange - baseRange.range) / 2;
      const newFrom = baseRange.from - difRange;
      const newTo = baseRange.to + difRange;
      const newRealFrom = yAxis.valueToRealValue?.(newFrom, { range: baseRange });
      const newRealTo = yAxis.valueToRealValue?.(newTo, { range: baseRange });
      const newDisplayFrom = yAxis.realValueToDisplayValue?.(newRealFrom, {
        range: baseRange,
      });
      const newDisplayTo = yAxis.realValueToDisplayValue?.(newRealTo, {
        range: baseRange,
      });
      if (
        !Number.isFinite(newRealFrom) ||
        !Number.isFinite(newRealTo) ||
        !Number.isFinite(newDisplayFrom) ||
        !Number.isFinite(newDisplayTo)
      ) {
        return;
      }

      yAxis.setRange?.({
        from: newFrom,
        to: newTo,
        range: newRange,
        realFrom: newRealFrom,
        realTo: newRealTo,
        realRange: newRealTo - newRealFrom,
        displayFrom: newDisplayFrom,
        displayTo: newDisplayTo,
        displayRange: newDisplayTo - newDisplayFrom,
      });

      chart.layout?.({
        measureWidth: true,
        update: true,
        buildYAxisTick: true,
      });
    };

    const endXAxisOnlyDragZoom = (inputType = null) => {
      const state = xAxisDragZoomRef.current;
      if (!state?.active) return;
      if (inputType && state.inputType !== inputType) return;

      xAxisDragZoomRef.current = null;
      requestZoomAnchorGestureEnd();
    };

    const endYAxisOnlyDragZoom = (inputType = null) => {
      const state = yAxisDragZoomRef.current;
      if (!state) return;
      if (inputType && state.inputType !== inputType) return;

      yAxisDragZoomRef.current = null;
    };

    const captureXAxisMouseDragStart = (event) => {
      if (event.button !== 0) return;

      const started = beginXAxisOnlyDragZoom({
        pageX: event.pageX,
        clientX: event.clientX,
        clientY: event.clientY,
        inputType: "mouse",
      });
      if (started) {
        swallowNativeEvent(event);
      }
    };

    const captureYAxisMouseDragStart = (event) => {
      if (event.button !== 0) return;

      beginYAxisOnlyDragZoom({
        pageY: event.pageY,
        clientX: event.clientX,
        clientY: event.clientY,
        inputType: "mouse",
      });
    };

    const captureXAxisTouchDragStart = (event) => {
      const touch = event.changedTouches?.[0];
      if (!touch) return;

      const started = beginXAxisOnlyDragZoom({
        pageX: touch.pageX,
        clientX: touch.clientX,
        clientY: touch.clientY,
        inputType: "touch",
        touchId: touch.identifier,
      });
      if (started) {
        swallowNativeEvent(event);
      }
    };

    const captureYAxisTouchDragStart = (event) => {
      const touch = event.changedTouches?.[0];
      if (!touch) return;

      beginYAxisOnlyDragZoom({
        pageY: touch.pageY,
        clientX: touch.clientX,
        clientY: touch.clientY,
        inputType: "touch",
        touchId: touch.identifier,
      });
    };

    const windowMouseMoveXAxisDragHandler = (event) => {
      const state = xAxisDragZoomRef.current;
      if (!state?.active || state.inputType !== "mouse") return;

      stepXAxisOnlyDragZoom(event.pageX);
      event.preventDefault?.();
    };

    const windowMouseMoveYAxisDragHandler = (event) => {
      const state = yAxisDragZoomRef.current;
      if (!state || state.inputType !== "mouse") return;

      stepYAxisOnlyDragZoom({
        pageY: event.pageY,
        clientX: event.clientX,
        clientY: event.clientY,
      });
      if (yAxisDragZoomRef.current?.active) {
        event.preventDefault?.();
      }
    };

    const windowMouseUpXAxisDragHandler = () => {
      endXAxisOnlyDragZoom("mouse");
    };

    const windowMouseUpYAxisDragHandler = () => {
      endYAxisOnlyDragZoom("mouse");
    };

    const windowTouchMoveXAxisDragHandler = (event) => {
      const state = xAxisDragZoomRef.current;
      if (!state?.active || state.inputType !== "touch") return;

      const changed = event.changedTouches;
      if (!changed || changed.length === 0) return;

      let activeTouch = null;
      for (let i = 0; i < changed.length; i += 1) {
        if (changed[i].identifier === state.touchId) {
          activeTouch = changed[i];
          break;
        }
      }
      if (!activeTouch) return;

      stepXAxisOnlyDragZoom(activeTouch.pageX);
      event.preventDefault?.();
    };

    const windowTouchMoveYAxisDragHandler = (event) => {
      const state = yAxisDragZoomRef.current;
      if (!state?.active || state.inputType !== "touch") return;

      const changed = event.changedTouches;
      if (!changed || changed.length === 0) return;

      let activeTouch = null;
      for (let i = 0; i < changed.length; i += 1) {
        if (changed[i].identifier === state.touchId) {
          activeTouch = changed[i];
          break;
        }
      }
      if (!activeTouch) return;

      stepYAxisOnlyDragZoom({
        pageY: activeTouch.pageY,
        clientX: activeTouch.clientX,
        clientY: activeTouch.clientY,
      });
      if (yAxisDragZoomRef.current?.active) {
        event.preventDefault?.();
      }
    };

    const windowTouchEndXAxisDragHandler = (event) => {
      const state = xAxisDragZoomRef.current;
      if (!state?.active || state.inputType !== "touch") return;

      const changed = event.changedTouches;
      if (!changed || changed.length === 0) return;

      for (let i = 0; i < changed.length; i += 1) {
        if (changed[i].identifier !== state.touchId) continue;
        endXAxisOnlyDragZoom("touch");
        event.preventDefault?.();
        break;
      }
    };

    const windowTouchEndYAxisDragHandler = (event) => {
      const state = yAxisDragZoomRef.current;
      if (!state?.active || state.inputType !== "touch") return;

      const changed = event.changedTouches;
      if (!changed || changed.length === 0) return;

      for (let i = 0; i < changed.length; i += 1) {
        if (changed[i].identifier !== state.touchId) continue;
        endYAxisOnlyDragZoom("touch");
        event.preventDefault?.();
        break;
      }
    };

    const formatPositionNoticePrice = (v) => {
      const numeric = Number(v);
      if (!Number.isFinite(numeric)) return null;
      const precision = Number(DEFAULT_SYMBOL.pricePrecision);
      if (Number.isFinite(precision) && precision >= 0) {
        return numeric.toFixed(precision);
      }
      return String(numeric);
    };

    const getHostLocalPoint = (clientX, clientY) => {
      if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
      const rect = el.getBoundingClientRect();
      return {
        localX: clientX - rect.left,
        localY: clientY - rect.top,
        width: rect.width,
        height: rect.height,
      };
    };

    const readComputedCursor = (node) => {
      if (!(node instanceof Element)) return "";
      try {
        const raw = window.getComputedStyle(node).cursor;
        return typeof raw === "string" ? raw.trim().toLowerCase() : "";
      } catch {
        return "";
      }
    };

    const getCurrentChartCursor = (eventTarget = null) => {
      const seen = new Set();
      const candidates = [];

      const pushCandidate = (value) => {
        const v = String(value || "").trim().toLowerCase();
        if (!v || seen.has(v)) return;
        seen.add(v);
        candidates.push(v);
      };

      pushCandidate(readComputedCursor(eventTarget));

      const paneId = resolveMainPaneIdForActions();
      try {
        const yAxisMainDom = chart.getDom?.(paneId, "y_axis");
        pushCandidate(readComputedCursor(yAxisMainDom));
      } catch {
        // ignore
      }
      pushCandidate(readComputedCursor(el));

      for (const c of candidates) {
        if (!c || c === "auto" || c === "default" || c === "inherit") continue;
        return c;
      }
      return "";
    };

    const isPositionLabelHoverCursor = (cursor) => {
      const c = String(cursor || "").trim().toLowerCase();
      return c === "pointer" || c === "grab" || c === "grabbing" || c === "move";
    };

    const isPointInsidePaneYAxisDom = (
      paneId,
      clientX,
      clientY,
      overflowPx = POSITION_LABEL_AXIS_DOM_HIT_OVERFLOW_PX
    ) => {
      if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
      if (typeof chart.getDom !== "function") return null;

      try {
        const yAxisDom = chart.getDom(paneId, "y_axis");
        if (!(yAxisDom instanceof Element)) return null;

        const rect = yAxisDom.getBoundingClientRect?.();
        if (!rect) return null;

        const overflow = Number.isFinite(Number(overflowPx))
          ? Math.max(0, Number(overflowPx))
          : POSITION_LABEL_AXIS_DOM_HIT_OVERFLOW_PX;
        const inX = clientX >= rect.left - overflow && clientX <= rect.right + overflow;
        const inY = clientY >= rect.top - overflow && clientY <= rect.bottom + overflow;
        return inX && inY;
      } catch {
        return null;
      }
    };

    const getPositionPriceFromLocalY = (paneId, localY) => {
      if (!Number.isFinite(localY)) return null;
      if (typeof chart.convertFromPixel !== "function") return null;

      let y = localY;
      const size =
        chart.getSize?.(paneId, "main") || chart.getSize?.(paneId);
      const bounding = size?.bounding || size;
      const top = Number(bounding?.top);
      const height = Number(bounding?.height);
      if (Number.isFinite(top) && Number.isFinite(height) && height > 2) {
        const minY = top + 1;
        const maxY = top + height - 1;
        y = Math.max(minY, Math.min(maxY, y));
      }

      try {
        const converted = chart.convertFromPixel(
          [{ y }],
          { paneId, absolute: false }
        );
        const point = Array.isArray(converted) ? converted[0] : converted;
        const price = Number(point?.value);
        return Number.isFinite(price) ? price : null;
      } catch {
        return null;
      }
    };

    const getCurrentMarketPriceForValidation = () => {
      const raw = latestPriceRef.current ?? getPositionOverlayLastPrice();
      const price = Number(raw);
      if (!Number.isFinite(price) || price <= 0) return null;
      return price;
    };

    const validateDraggedTpslPrice = ({ side, field, price }) => {
      if (!Number.isFinite(price) || price <= 0) {
        return "가격이 유효하지 않습니다.";
      }

      const market = getCurrentMarketPriceForValidation();
      if (!Number.isFinite(market)) {
        return null;
      }

      const marketText = formatPositionNoticePrice(market) || String(market);
      if (side === "LONG") {
        if (field === "tp" && price <= market) {
          return `롱 TP는 현재가(${marketText})보다 높아야 합니다.`;
        }
        if (field === "sl" && price >= market) {
          return `롱 SL은 현재가(${marketText})보다 낮아야 합니다.`;
        }
        return null;
      }

      if (field === "tp" && price >= market) {
        return `숏 TP는 현재가(${marketText})보다 낮아야 합니다.`;
      }
      if (field === "sl" && price <= market) {
        return `숏 SL은 현재가(${marketText})보다 높아야 합니다.`;
      }
      return null;
    };

    const setPositionOverlayPreviewPrice = ({ overlayId, field, price }) => {
      if (!setPositionOverlayPreviewState({ overlayId, field, price })) return;
      applyPositionOverlays(chart);
    };

    const clearPositionOverlayPreview = (overlayId) => {
      if (!clearPositionOverlayPreviewState(overlayId)) return;
      applyPositionOverlays(chart);
    };

    const setPositionOverlayDragHighlightField = ({ overlayId, field }) => {
      if (!setPositionOverlayDragHighlightState({ overlayId, field })) return;
      ensurePositionOverlayDashAnimation();
      applyPositionOverlays(chart);
    };

    const clearPositionOverlayDragHighlightField = (overlayId) => {
      if (!clearPositionOverlayDragHighlightState(overlayId)) return;
      if (!hasPositionOverlayDragHighlight()) {
        stopPositionOverlayDashAnimation();
      }
      applyPositionOverlays(chart);
    };

    const resolvePositionLabelDragTarget = (
      clientX,
      clientY,
      { labelCursorMode = false, touchMode = false } = {},
    ) => {
      const localPoint = getHostLocalPoint(clientX, clientY);
      if (!localPoint) return null;

      let labels = getPositionOverlayAxisLabelHitboxes();
      if (!Array.isArray(labels)) {
        labels = [];
      }

      // createYAxisFigures가 아직 실행되지 않았거나 라벨 캐시가 비어있으면
      // 현재 오버레이 값을 픽셀로 계산해 fallback hitbox 구성
      if (labels.length === 0 && typeof chart.convertToPixel === "function") {
        const fallbackPaneId = resolveMainPaneIdForActions();
        const fallback = [];
        const pricePrecision = Number(DEFAULT_SYMBOL.pricePrecision);
        const normalizedPrecision =
          Number.isFinite(pricePrecision) && pricePrecision >= 0
            ? pricePrecision
            : 2;
        const now = Date.now();
        for (const overlay of Object.values(positionOverlayStore.overlaysById)) {
          if (!overlay || !overlay.id || overlay.closed) continue;
          const overlayId = String(overlay.id);
          if (hasPositionOverlayPending(overlayId)) continue;

          const tpAvailable = overlay.tpAvailable === true || Number(overlay.tpPrice) > 0;
          const slAvailable = overlay.slAvailable === true || Number(overlay.slPrice) > 0;

          if (tpAvailable && Number.isFinite(Number(overlay.tpPrice))) {
            try {
              const p = chart.convertToPixel(
                { value: Number(overlay.tpPrice) },
                { paneId: fallbackPaneId, absolute: false }
              );
              const y = Array.isArray(p) ? Number(p[0]?.y) : Number(p?.y);
              if (Number.isFinite(y)) {
                const text = `TP ${Number(overlay.tpPrice).toFixed(normalizedPrecision)}`;
                fallback.push({
                  overlayId,
                  field: "tp",
                  paneId: fallbackPaneId,
                  axisSide: "right",
                  y,
                  widthPx: Math.max(
                    POSITION_LABEL_FALLBACK_WIDTH_PX,
                    measurePositionLabelWidthPx(text, 12) + 12
                  ),
                  heightPx: POSITION_LABEL_FALLBACK_HEIGHT_PX,
                  at: now,
                });
              }
            } catch {
              // ignore
            }
          }

          if (slAvailable && Number.isFinite(Number(overlay.slPrice))) {
            try {
              const p = chart.convertToPixel(
                { value: Number(overlay.slPrice) },
                { paneId: fallbackPaneId, absolute: false }
              );
              const y = Array.isArray(p) ? Number(p[0]?.y) : Number(p?.y);
              if (Number.isFinite(y)) {
                const text = `SL ${Number(overlay.slPrice).toFixed(normalizedPrecision)}`;
                fallback.push({
                  overlayId,
                  field: "sl",
                  paneId: fallbackPaneId,
                  axisSide: "right",
                  y,
                  widthPx: Math.max(
                    POSITION_LABEL_FALLBACK_WIDTH_PX,
                    measurePositionLabelWidthPx(text, 12) + 12
                  ),
                  heightPx: POSITION_LABEL_FALLBACK_HEIGHT_PX,
                  at: now,
                });
              }
            } catch {
              // ignore
            }
          }
        }
        labels = fallback;
      }

      if (labels.length === 0) return null;

      const now = Date.now();
      let best = null;
      for (const label of labels) {
        if (!label || !label.overlayId) continue;
        if (label.field !== "tp" && label.field !== "sl") continue;
        if (
          typeof label.at === "number" &&
          Number.isFinite(label.at) &&
          now - label.at > POSITION_LABEL_STALE_MS
        ) {
          continue;
        }

        const overlay = getPositionOverlay(label.overlayId);
        if (!overlay || overlay.closed) continue;
        if (hasPositionOverlayPending(String(label.overlayId))) continue;

        const labelWidth = Math.max(
          POSITION_LABEL_FALLBACK_WIDTH_PX,
          Number(label.widthPx) || 0
        );
        const labelHeight = Math.max(
          POSITION_LABEL_FALLBACK_HEIGHT_PX,
          Number(label.heightPx) || 0
        );
        const labelY = Number(label.y);
        if (!Number.isFinite(labelY)) continue;
        const dy = Math.abs(localPoint.localY - labelY);
        const paneId = label.paneId || "candle_pane";

        const axisDomOverflow = touchMode
          ? Math.max(POSITION_LABEL_AXIS_DOM_HIT_OVERFLOW_PX, POSITION_TOUCH_AXIS_OVERFLOW_PX)
          : POSITION_LABEL_AXIS_DOM_HIT_OVERFLOW_PX;
        const insideAxisDom = isPointInsidePaneYAxisDom(
          paneId,
          clientX,
          clientY,
          axisDomOverflow
        );
        if (insideAxisDom === false) continue;

        if (touchMode) {
          // 터치에서는 Y축 DOM hit를 우선 사용하고,
          // DOM을 못 찾은 경우에만 edge-zone 기반 fallback 적용
          if (insideAxisDom === true) {
            // pass
          } else {
            const rightMin = Math.max(
              0,
              localPoint.width - labelWidth - POSITION_LABEL_X_HIT_PADDING_PX
            );
            const rightMax = localPoint.width + POSITION_TOUCH_AXIS_OVERFLOW_PX;
            const leftMin = -POSITION_TOUCH_AXIS_OVERFLOW_PX;
            const leftMax = Math.min(
              localPoint.width,
              labelWidth + POSITION_LABEL_X_HIT_PADDING_PX
            );
            const inLeftEdge =
              localPoint.localX >= leftMin && localPoint.localX <= leftMax;
            const inRightEdge =
              localPoint.localX >= rightMin && localPoint.localX <= rightMax;
            if (!inLeftEdge && !inRightEdge) continue;
          }
        } else {
          // 마우스는 반드시 TP/SL 라벨 hitbox 영역 안에서만 드래그 시작 허용
          const xPadding = labelCursorMode
            ? POSITION_LABEL_MOUSE_X_HIT_PADDING_PX
            : POSITION_LABEL_X_HIT_PADDING_PX;
          const xMin = label.axisSide === "left"
            ? -xPadding
            : Math.max(0, localPoint.width - labelWidth - xPadding);
          const xMax = label.axisSide === "left"
            ? Math.min(localPoint.width + xPadding, labelWidth + xPadding)
            : localPoint.width + xPadding;
          if (localPoint.localX < xMin || localPoint.localX > xMax) continue;
        }

        const yHalf = touchMode
          ? labelHeight / 2 + POSITION_LABEL_Y_HIT_PADDING_PX
          : labelHeight / 2 + (labelCursorMode
              ? POSITION_LABEL_MOUSE_Y_HIT_PADDING_PX
              : POSITION_LABEL_Y_HIT_PADDING_PX);
        if (!Number.isFinite(dy) || dy > yHalf) continue;

        const previewPrice = getPositionPriceFromLocalY(paneId, localPoint.localY);
        if (!Number.isFinite(previewPrice)) continue;

        if (!best || dy < best.dy) {
          best = {
            overlayId: String(label.overlayId),
            field: label.field,
            paneId,
            previewPrice,
            localY: localPoint.localY,
            dy,
          };
        }
      }

      return best;
    };

    const positionLabelDragState = {
      active: false,
      inputType: null,
      touchId: null,
      overlayId: null,
      field: null,
      paneId: "candle_pane",
      side: "LONG",
      previewPrice: null,
    };

    const resetPositionLabelDragState = () => {
      positionLabelDragState.active = false;
      positionLabelDragState.inputType = null;
      positionLabelDragState.touchId = null;
      positionLabelDragState.overlayId = null;
      positionLabelDragState.field = null;
      positionLabelDragState.paneId = "candle_pane";
      positionLabelDragState.side = "LONG";
      positionLabelDragState.previewPrice = null;
    };

    const positionLabelTouchLongPressState = {
      active: false,
      touchId: null,
      startClientX: null,
      startClientY: null,
      lastClientX: null,
      lastClientY: null,
      target: null,
      timerId: null,
    };

    const resetPositionLabelTouchLongPressState = () => {
      const wasActive = positionLabelTouchLongPressState.active;
      if (positionLabelTouchLongPressState.timerId !== null) {
        clearTimeout(positionLabelTouchLongPressState.timerId);
      }
      positionLabelTouchLongPressState.active = false;
      positionLabelTouchLongPressState.touchId = null;
      positionLabelTouchLongPressState.startClientX = null;
      positionLabelTouchLongPressState.startClientY = null;
      positionLabelTouchLongPressState.lastClientX = null;
      positionLabelTouchLongPressState.lastClientY = null;
      positionLabelTouchLongPressState.target = null;
      positionLabelTouchLongPressState.timerId = null;
      return wasActive;
    };

    const activatePositionLabelDragFromTarget = ({
      event,
      inputType,
      touchId = null,
      target,
    }) => {
      if (!target) return false;
      if (positionLabelDragState.active) return false;

      const overlay = getPositionOverlay(target.overlayId);
      if (!overlay) return false;

      const side =
        String(overlay.side || "LONG").toUpperCase() === "SHORT"
          ? "SHORT"
          : "LONG";

      const price = Number(target.previewPrice);
      if (!Number.isFinite(price)) return false;

      positionLabelDragState.active = true;
      positionLabelDragState.inputType = inputType;
      positionLabelDragState.touchId = touchId;
      positionLabelDragState.overlayId = target.overlayId;
      positionLabelDragState.field = target.field;
      positionLabelDragState.paneId = target.paneId;
      positionLabelDragState.side = side;
      positionLabelDragState.previewPrice = price;

      setPositionOverlayPreviewPrice({
        overlayId: target.overlayId,
        field: target.field,
        price,
      });
      setPositionOverlayDragHighlightField({
        overlayId: target.overlayId,
        field: target.field,
      });
      if (event) {
        swallowNativeEvent(event);
      }
      return true;
    };

    const beginPositionLabelDrag = ({
      event,
      inputType,
      touchId = null,
      clientX,
      clientY,
      resolvedTarget = null,
    }) => {
      if (positionLabelDragState.active) return false;

      const useLabelCursorMode = inputType === "mouse";
      const useTouchMode = inputType === "touch";
      if (useLabelCursorMode) {
        const indicatorToggleHovered =
          getIndicatorTooltipToggleHovered() ||
          syncIndicatorTooltipToggleHovered(chart);
        if (indicatorToggleHovered) {
          return false;
        }

        const cursor = getCurrentChartCursor(event?.target ?? null);
        if (!isPositionLabelHoverCursor(cursor)) {
          return false;
        }
      }

      const target =
        resolvedTarget ||
        resolvePositionLabelDragTarget(clientX, clientY, {
          labelCursorMode: useLabelCursorMode,
          touchMode: useTouchMode,
        });
      return activatePositionLabelDragFromTarget({
        event,
        inputType,
        touchId,
        target,
      });
    };

    const stepPositionLabelDrag = ({
      event,
      inputType,
      touchId = null,
      clientX,
      clientY,
    }) => {
      if (!positionLabelDragState.active) return false;
      if (positionLabelDragState.inputType !== inputType) return false;
      if (inputType === "touch" && touchId !== positionLabelDragState.touchId) {
        return false;
      }

      const localPoint = getHostLocalPoint(clientX, clientY);
      if (!localPoint) return false;

      const nextPrice = getPositionPriceFromLocalY(
        positionLabelDragState.paneId,
        localPoint.localY
      );
      if (!Number.isFinite(nextPrice)) return false;

      positionLabelDragState.previewPrice = nextPrice;
      setPositionOverlayPreviewPrice({
        overlayId: positionLabelDragState.overlayId,
        field: positionLabelDragState.field,
        price: nextPrice,
      });
      event.preventDefault?.();
      event.stopPropagation?.();
      return true;
    };

    const cancelPositionLabelDrag = () => {
      const hadPendingLongPress = resetPositionLabelTouchLongPressState();
      if (!positionLabelDragState.active) return hadPendingLongPress;
      const overlayId = positionLabelDragState.overlayId;
      resetPositionLabelDragState();
      clearPositionOverlayDragHighlightField(overlayId);
      clearPositionOverlayPreview(overlayId);
      return true;
    };

    const commitPositionLabelDrag = async () => {
      if (!positionLabelDragState.active) return false;

      const snapshot = {
        overlayId: String(positionLabelDragState.overlayId || ""),
        field: positionLabelDragState.field,
        side: positionLabelDragState.side,
        previewPrice: Number(positionLabelDragState.previewPrice),
      };
      resetPositionLabelDragState();
      clearPositionOverlayDragHighlightField(snapshot.overlayId);

      if (!snapshot.overlayId || (snapshot.field !== "tp" && snapshot.field !== "sl")) {
        clearPositionOverlayPreview(snapshot.overlayId);
        return true;
      }

      const policyError = validateDraggedTpslPrice({
        side: snapshot.side,
        field: snapshot.field,
        price: snapshot.previewPrice,
      });
      if (policyError) {
        clearPositionOverlayPreview(snapshot.overlayId);
        return true;
      }

      addPositionOverlayPending(snapshot.overlayId);

      try {
        const result = await updatePositionTpsl({
          overlayId: snapshot.overlayId,
          field: snapshot.field,
          price: snapshot.previewPrice,
        });

        clearPositionOverlayPreviewState(snapshot.overlayId);

        const updatedOverlay = result?.overlay;
        upsertPositionOverlay(updatedOverlay);
        applyPositionOverlays(chart);
      } catch (e) {
        clearPositionOverlayPreviewState(snapshot.overlayId);
        applyPositionOverlays(chart);
      } finally {
        removePositionOverlayPending(snapshot.overlayId);
      }

      return true;
    };

    const capturePositionLabelTouchDragStart = (event) => {
      const indicatorToggleHovered =
        getIndicatorTooltipToggleHovered() ||
        syncIndicatorTooltipToggleHovered(chart);
      if (indicatorToggleHovered) return;

      const touch = event.changedTouches?.[0];
      if (!touch) return;

      const resolvedTpslTarget = resolvePositionLabelDragTarget(
        touch.clientX,
        touch.clientY,
        { labelCursorMode: false, touchMode: true },
      );
      if (!resolvedTpslTarget) return;

      if (!shouldUsePositionTouchLongPress) {
        beginPositionLabelDrag({
          event,
          inputType: "touch",
          touchId: touch.identifier,
          clientX: touch.clientX,
          clientY: touch.clientY,
          resolvedTarget: resolvedTpslTarget,
        });
        return;
      }

      if (positionLabelDragState.active) return;

      resetPositionLabelTouchLongPressState();
      positionLabelTouchLongPressState.active = true;
      positionLabelTouchLongPressState.touchId = touch.identifier;
      positionLabelTouchLongPressState.startClientX = touch.clientX;
      positionLabelTouchLongPressState.startClientY = touch.clientY;
      positionLabelTouchLongPressState.lastClientX = touch.clientX;
      positionLabelTouchLongPressState.lastClientY = touch.clientY;
      positionLabelTouchLongPressState.target = resolvedTpslTarget;
      positionLabelTouchLongPressState.timerId = window.setTimeout(() => {
        if (!positionLabelTouchLongPressState.active) return;
        const touchId = positionLabelTouchLongPressState.touchId;
        const target = positionLabelTouchLongPressState.target;
        const clientX = Number(positionLabelTouchLongPressState.lastClientX);
        const clientY = Number(positionLabelTouchLongPressState.lastClientY);
        const startClientX = Number(positionLabelTouchLongPressState.startClientX);
        const startClientY = Number(positionLabelTouchLongPressState.startClientY);

        const activated = beginPositionLabelDrag({
          inputType: "touch",
          touchId,
          clientX: Number.isFinite(clientX) ? clientX : startClientX,
          clientY: Number.isFinite(clientY) ? clientY : startClientY,
          resolvedTarget: target,
        });

        resetPositionLabelTouchLongPressState();
        if (!activated) return;
      }, POSITION_TOUCH_LONG_PRESS_MS);
    };

    const windowMouseMovePositionLabelDragHandler = (event) => {
      stepPositionLabelDrag({
        event,
        inputType: "mouse",
        clientX: event.clientX,
        clientY: event.clientY,
      });
    };

    const windowTouchMovePositionLabelDragHandler = (event) => {
      const changed = event.changedTouches;
      if (!changed || changed.length === 0) return;

      if (positionLabelTouchLongPressState.active) {
        for (let i = 0; i < changed.length; i += 1) {
          const touch = changed[i];
          if (touch.identifier !== positionLabelTouchLongPressState.touchId) continue;

          positionLabelTouchLongPressState.lastClientX = touch.clientX;
          positionLabelTouchLongPressState.lastClientY = touch.clientY;

          const startX = Number(positionLabelTouchLongPressState.startClientX);
          const startY = Number(positionLabelTouchLongPressState.startClientY);
          const dx = touch.clientX - startX;
          const dy = touch.clientY - startY;
          const distSq = dx * dx + dy * dy;
          if (
            Number.isFinite(distSq) &&
            distSq >
              POSITION_TOUCH_LONG_PRESS_CANCEL_RADIUS_PX *
                POSITION_TOUCH_LONG_PRESS_CANCEL_RADIUS_PX
          ) {
            resetPositionLabelTouchLongPressState();
            return;
          }
          return;
        }
      }

      if (!positionLabelDragState.active || positionLabelDragState.inputType !== "touch") {
        return;
      }

      for (let i = 0; i < changed.length; i += 1) {
        const touch = changed[i];
        if (touch.identifier !== positionLabelDragState.touchId) continue;
        stepPositionLabelDrag({
          event,
          inputType: "touch",
          touchId: touch.identifier,
          clientX: touch.clientX,
          clientY: touch.clientY,
        });
        break;
      }
    };

    const windowMouseUpPositionLabelDragHandler = () => {
      void commitPositionLabelDrag();
    };

    const windowTouchEndPositionLabelDragHandler = (event) => {
      const changed = event.changedTouches;
      if (!changed || changed.length === 0) return;

      if (positionLabelTouchLongPressState.active) {
        for (let i = 0; i < changed.length; i += 1) {
          if (changed[i].identifier !== positionLabelTouchLongPressState.touchId) continue;
          resetPositionLabelTouchLongPressState();
          return;
        }
      }

      if (!positionLabelDragState.active || positionLabelDragState.inputType !== "touch") {
        return;
      }

      for (let i = 0; i < changed.length; i += 1) {
        if (changed[i].identifier !== positionLabelDragState.touchId) continue;
        void commitPositionLabelDrag();
        event.preventDefault?.();
        break;
      }
    };

    const beginPositionOverlayDragFromOverlayEvent = (payload = {}) => {
      if (positionLabelDragState.active) return false;

      const overlayId = String(payload.overlayId || "");
      const field =
        payload.field === "tp" || payload.field === "sl" ? payload.field : null;
      if (!overlayId || !field) return false;

      const overlay = getPositionOverlay(overlayId);
      if (!overlay || overlay.closed) return false;
      if (hasPositionOverlayPending(overlayId)) return false;

      const paneId =
        typeof payload.paneId === "string" ? payload.paneId : "candle_pane";
      const localY = Number(payload.y);
      if (!Number.isFinite(localY)) return false;

      const previewPrice = getPositionPriceFromLocalY(paneId, localY);
      if (!Number.isFinite(previewPrice)) return false;

      const activated = activatePositionLabelDragFromTarget({
        event: payload.event ?? null,
        inputType: "mouse",
        target: {
          overlayId,
          field,
          paneId,
          previewPrice,
          localY,
          dy: 0,
        },
      });

      if (activated) {
        payload.event?.preventDefault?.();
        payload.event?.stopPropagation?.();
      }
      return activated;
    };
    setPositionOverlayDragStartBridge(beginPositionOverlayDragFromOverlayEvent);

    const alignZoomAnchorToViewportRight = (anchor) => {
      if (!anchor) return;

      const dataList = chart.getDataList?.() || [];
      if (!dataList.length) return;

      const paneId = resolveMainPaneIdForActions();

      const size =
        chart.getSize?.(paneId, "main") || chart.getSize?.(paneId);
      const bounding = size?.bounding || size;
      const paneWidth = Number(bounding?.width);
      if (!Number.isFinite(paneWidth) || paneWidth <= 1) return;

      const anchorTimestamp = anchor?.timestamp;
      const anchorDataIndex = anchor?.dataIndex;
      const pointForConvert =
        typeof anchorTimestamp === "number" && Number.isFinite(anchorTimestamp)
        ? { timestamp: anchorTimestamp }
        : typeof anchorDataIndex === "number" && Number.isFinite(anchorDataIndex)
          ? { dataIndex: anchorDataIndex }
          : null;
      if (!pointForConvert) return;

      const pixel =
        chart.convertToPixel?.(
          pointForConvert,
          { paneId, absolute: false }
        ) || {};

      const currentX = Number(pixel?.x);
      if (!Number.isFinite(currentX)) return;

      // 우측 끝(축 내부 마지막 픽셀)으로 anchor 캔들을 이동
      const targetX = paneWidth - 1;
      const distance = targetX - currentX;
      if (!Number.isFinite(distance) || Math.abs(distance) < 0.5) return;

      chart.scrollByDistance?.(distance);
    };

    const zoomHandler = () => {
      const anchor = zoomAnchorRef.current ?? getViewportRightAnchor();
      if (!anchor) return;

      alignZoomAnchorToViewportRight(anchor);

      // wheel 제스처는 onZoom 연속 호출마다 종료 타이머를 연장해
      // 한 제스처 동안 anchor 유지
      if (zoomAnchorInputRef.current === "wheel") {
        scheduleWheelZoomEnd();
      }

      maybeFinalizeZoomAnchorGesture();
    };

    // "십자 커서가 기본 커서로 바뀌는 순간" = 실제 포인터가 차트 영역 밖일 때로 간주
    const windowMouseMoveHandler = (event) => {
      if (
        getHoveredZoneId() != null ||
        getIndicatorTooltipToggleHovered()
      ) {
        syncIndicatorTooltipToggleHovered(chart);
      }
      if (getHoveredZoneId() == null) return;

      const { clientX, clientY } = event;
      if (
        typeof clientX !== "number" ||
        typeof clientY !== "number" ||
        Number.isNaN(clientX) ||
        Number.isNaN(clientY)
      ) {
        return;
      }

      const rect = el.getBoundingClientRect();
      const isInsideChart =
        clientX >= rect.left &&
        clientX <= rect.right &&
        clientY >= rect.top &&
        clientY <= rect.bottom;

      // 왼쪽 알림 패널 위에서는 hover 연동을 유지하기 위함
      const target = event.target;
      const isInsideNotifier =
        target instanceof Element &&
        target.closest(".zone-noti-root") !== null;

      // 차트와 알림 패널 바깥으로 완전히 벗어났을 때만 hover를 지우기 위함
      if (!isInsideChart && !isInsideNotifier) {
        clearZoneHoverByBridge();
      }
    };

    const hostMouseLeaveHandler = () => {
      setIndicatorTooltipToggleHovered(false);
      clearZoneHoverByBridge();
    };

    const windowBlurHandler = () => {
      setIndicatorTooltipToggleHovered(false);
      clearZoneHoverByBridge();
      cancelPositionLabelDrag();
      xAxisDragZoomRef.current = null;
      yAxisDragZoomRef.current = null;
      requestZoomAnchorGestureEnd();
    };

    const passiveOptions = { passive: true };
    const wheelCaptureOptions = { passive: true, capture: true };
    const mouseCaptureOptions = { capture: true };
    const touchCaptureOptions = { passive: false, capture: true };
    el.addEventListener("mouseleave", hostMouseLeaveHandler);
    el.addEventListener("pointerleave", hostMouseLeaveHandler);
    // wheel anchor 선캡처로 같은 휠 틱의 커서 기준 개입 차단
    el.addEventListener("wheel", captureWheelZoomAnchor, wheelCaptureOptions);
    el.addEventListener("pointerdown", capturePointerZoomAnchorStart, passiveOptions);
    el.addEventListener("touchstart", capturePositionLabelTouchDragStart, touchCaptureOptions);
    el.addEventListener("mousedown", captureXAxisMouseDragStart, mouseCaptureOptions);
    el.addEventListener("mousedown", captureYAxisMouseDragStart, mouseCaptureOptions);
    el.addEventListener("touchstart", captureXAxisTouchDragStart, touchCaptureOptions);
    el.addEventListener("touchstart", captureYAxisTouchDragStart, touchCaptureOptions);
    if (typeof window !== "undefined") {
      window.addEventListener("mousemove", windowMouseMovePositionLabelDragHandler, mouseCaptureOptions);
      window.addEventListener("mouseup", windowMouseUpPositionLabelDragHandler, mouseCaptureOptions);
      window.addEventListener("touchmove", windowTouchMovePositionLabelDragHandler, touchCaptureOptions);
      window.addEventListener("touchend", windowTouchEndPositionLabelDragHandler, touchCaptureOptions);
      window.addEventListener("touchcancel", windowTouchEndPositionLabelDragHandler, touchCaptureOptions);
      window.addEventListener("mousemove", windowMouseMoveXAxisDragHandler, mouseCaptureOptions);
      window.addEventListener("mousemove", windowMouseMoveYAxisDragHandler, mouseCaptureOptions);
      window.addEventListener("mousemove", windowMouseMoveHandler, passiveOptions);
      window.addEventListener("mouseup", windowMouseUpXAxisDragHandler, mouseCaptureOptions);
      window.addEventListener("mouseup", windowMouseUpYAxisDragHandler, mouseCaptureOptions);
      window.addEventListener("touchmove", windowTouchMoveXAxisDragHandler, touchCaptureOptions);
      window.addEventListener("touchmove", windowTouchMoveYAxisDragHandler, touchCaptureOptions);
      window.addEventListener("touchend", windowTouchEndXAxisDragHandler, touchCaptureOptions);
      window.addEventListener("touchend", windowTouchEndYAxisDragHandler, touchCaptureOptions);
      window.addEventListener("touchcancel", windowTouchEndXAxisDragHandler, touchCaptureOptions);
      window.addEventListener("touchcancel", windowTouchEndYAxisDragHandler, touchCaptureOptions);
      window.addEventListener("blur", windowBlurHandler);
      window.addEventListener("pointerup", endPointerZoomAnchor, passiveOptions);
      window.addEventListener("pointercancel", endPointerZoomAnchor, passiveOptions);
    }

    // Structure Zone 오버레이가 어느 차트에 그려져야 하는지 연결하기 위함
    setZoneChart(chart);

    //  전역 포지션 오버레이용 차트 인스턴스 등록
    setPositionOverlayChart(chart);

    // 스타일 설정 (기존 그대로)
    chart.setStyles({
      grid: {
        horizontal: { show: false, color: "rgba(255,255,255,0.08)" },
        vertical: { show: false, color: "rgba(255,255,255,0.08)" },
      },
      candle: {
        type: "candle_solid",
        bar: {
          upColor: "#FFFFFF",
          upBorderColor: "#FFFFFF",
          upWickColor: "#FFFFFF",
          downColor: "#F23645",
          downBorderColor: "#F23645",
          downWickColor: "#F23645",
          noChangeColor: "rgba(255,255,255,0.4)",
          noChangeBorderColor: "rgba(255,255,255,0.4)",
          noChangeWickColor: "rgba(255,255,255,0.4)",
        },
        priceMark: {
          show: true,
          last: {
            show: true,

            // 양봉(상승)일 때: 선 + 라벨 배경을 흰색으로
            upColor: "#FFFFFF",

            // 음봉(하락)일 때: 기존처럼 빨간색 유지
            downColor: "#F23645",

            // 보합일 때 색(적당한 연한 흰색)
            noChangeColor: "rgba(255,255,255,0.7)",

            // 가격 수평선 스타일
            line: {
              show: true,
              style: "dashed",
              size: 1,
              dashedValue: [4, 4],
            },

            // 오른쪽 숫자 라벨 스타일 (상승/하락 공통)
            text: {
              show: true,
              // 글자 색을 검정으로
              color: "#000000",
              size: 12,
              paddingLeft: 4,
              paddingRight: 4,
              paddingTop: 4,
              paddingBottom: 4,
              borderRadius: 2,
            },
          },
        },
      },
      // 내장 인디케이터(BOLL 포함) 선 색상 커스터마이징
      indicator: {
        lines: [
          {
            // 상단선 (Upper Band)
            style: "solid",
            smooth: false,
            size: 1,
            color: "#FFF",
          },
          {
            // 중단선 (Middle Band)
            style: "solid",
            smooth: false,
            size: 1,
            color: "#FFF",
          },
          {
            // 하단선 (Lower Band)
            style: "solid",
            smooth: false,
            size: 1,
            color: "#FFF",
          },
        ],
        tooltip: {
          showRule: "always",
        },
      },
    });

    // v10: 심볼 & 기본 기간 설정
    chart.setSymbol(DEFAULT_SYMBOL);
    applyCandleOnlyYAxisAutoRange(chart);

    ensureMtfMaIndicatorRegistered();
    ensureIndicatorTooltipToggleRegistered();
    prioritizeMainTooltipFeatureEvents(chart);
    const mainPaneIdForMtfMa = resolveMainPaneId(chart);
    createMtfMaIndicator(chart, mainPaneIdForMtfMa);

    recreateIndicatorTooltipToggle(chart);

    let liveTimer = null;
    let livePollInFlight = false;
    let mtfSourcePollTimer = null;
    let mtfSourcePollInFlight = false;
    let destroyed = false;
    let resyncInFlight = false;
    let resyncDebounceTimer = null;
    let lastResyncAt = 0;

    const isPageHidden = () =>
      typeof document !== "undefined" && document.hidden;

    const stopLivePolling = () => {
      if (!liveTimer) return;
      clearInterval(liveTimer);
      liveTimer = null;
      livePollInFlight = false;
    };

    const stopMtfSourcePolling = () => {
      if (!mtfSourcePollTimer) return;
      clearInterval(mtfSourcePollTimer);
      mtfSourcePollTimer = null;
      mtfSourcePollInFlight = false;
    };

    const refreshMtfSourceCaches = async ({ initial = false } = {}) => {
      if (destroyed || mtfSourcePollInFlight) return false;

      mtfSourcePollInFlight = true;
      try {
        let changed = false;
        if (initial) {
          const initResults = await Promise.all(
            MTF_MA_SOURCE_TF_LIST.map((sourceTf) =>
              fetchMtfMaSourceCandles(sourceTf, MTF_MA_SOURCE_INIT_LIMIT)
            )
          );
          changed = initResults.some(Boolean);
        } else {
          const latestResults = await Promise.all(
            MTF_MA_SOURCE_TF_LIST.map((sourceTf) => fetchMtfMaSourceLatest(sourceTf))
          );
          changed = latestResults.some(Boolean);
        }

        if (changed) {
          refreshMtfMaIndicator(initial ? "source-init" : "source-latest");
        }
        return changed;
      } finally {
        mtfSourcePollInFlight = false;
      }
    };

    const startMtfSourcePolling = () => {
      if (mtfSourcePollTimer) return;

      mtfSourcePollTimer = setInterval(() => {
        if (destroyed || isPageHidden()) return;
        void refreshMtfSourceCaches({ initial: false });
      }, MTF_MA_SOURCE_POLL_INTERVAL_MS);
    };

    const getActiveTf = () => {
      try {
        const period = chart.getPeriod?.();
        const tfStr = periodToTf(period);
        if (TF_LIST.includes(tfStr)) return tfStr;
      } catch {
        // ignore
      }
      return tf;
    };

    async function refreshTfCache(tfStr) {
      if (!TF_LIST.includes(tfStr)) return false;

      if (!CANDLE_CACHE[tfStr]) {
        CANDLE_CACHE[tfStr] = {
          initLoaded: false,
          list: [],
          hasMorePast: true,
          earliestTs: null,
        };
      }
      const cache = CANDLE_CACHE[tfStr];

      try {
        const res = await fetch(`/api/candles/${tfStr}?limit=${RESYNC_LIMIT}`);
        if (!res.ok) {
          console.error(
            `[CandleChart] Resync 캔들 조회 실패(tf=${tfStr}):`,
            res.status
          );
          return false;
        }

        const arr = await safeJson(res, `resync-${tfStr}`);
        if (!Array.isArray(arr)) {
          return false;
        }

        const mapped = arr.map(mapServerCandle);
        mapped.sort((a, b) => a.timestamp - b.timestamp);

        cache.initLoaded = true;
        cache.list = mapped;
        cache.hasMorePast = mapped.length === RESYNC_LIMIT;
        cache.earliestTs = mapped[0]?.timestamp ?? null;

        return true;
      } catch (e) {
        console.error(`[CandleChart] Resync fetch 에러(tf=${tfStr}):`, e);
        return false;
      }
    }

    async function runResync(reason) {
      if (!ENABLE_VISIBILITY_RESYNC || destroyed) return;
      if (resyncInFlight) return;
      if (isPageHidden()) return;

      const now = Date.now();
      if (now - lastResyncAt < RESYNC_COOLDOWN_MS) return;

      resyncInFlight = true;
      lastResyncAt = now;

      const tfStr = getActiveTf();
      const viewportSnapshot = captureLatestCandleViewport();

      try {
        stopLivePolling();

        const refreshed = await refreshTfCache(tfStr);
        if (!refreshed && CANDLE_CACHE[tfStr]) {
          // refresh 실패 시 resetData(init)의 서버 재조회 유도를 위한 캐시 재사용 차단
          CANDLE_CACHE[tfStr].initLoaded = false;
        }

        if (typeof chart.resetData === "function") {
          chart.resetData();
        } else {
          chart.setPeriod(tfToPeriod(tfStr));
        }

        scheduleLatestCandleViewportRestore(viewportSnapshot);

        const raf = typeof requestAnimationFrame === "function"
          ? requestAnimationFrame
          : (fn) => setTimeout(fn, 0);

        raf(() => {
          if (destroyed) return;
          applyZoneOverlays(chart);
          applyPositionOverlays(chart);
          refreshZoneNotifications();
        });
      } catch (e) {
        console.error(`[CandleChart] Resync 실패(reason=${reason}):`, e);
      } finally {
        resyncInFlight = false;
      }
    }

    function scheduleResync(reason) {
      if (!ENABLE_VISIBILITY_RESYNC || destroyed) return;
      if (isPageHidden()) return;
      if (resyncInFlight) return;

      if (resyncDebounceTimer) {
        clearTimeout(resyncDebounceTimer);
      }

      resyncDebounceTimer = setTimeout(() => {
        resyncDebounceTimer = null;
        void runResync(reason);
      }, RESYNC_DEBOUNCE_MS);
    }

    // 초기 진입 또는 타임프레임 전환 시 캔들 캐시와 Zone 상태를 함께 맞추기 위함
    async function loadInitCandlesForTf(tfStr) {
      if (destroyed) {
        return { data: [], more: false };
      }

      if (!TF_LIST.includes(tfStr)) {
        return { data: [], more: false };
      }

      // 타임프레임별 캔들 캐시가 없으면 먼저 생성
      if (!CANDLE_CACHE[tfStr]) {
        CANDLE_CACHE[tfStr] = {
          initLoaded: false,
          list: [],
          hasMorePast: true,
          earliestTs: null,
        };
      }
      const cache = CANDLE_CACHE[tfStr];
      const forceResync = FORCE_TF_RESYNC_ON_NEXT_INIT[tfStr] === true;
      const fallbackData = Array.isArray(cache.list) ? cache.list : [];
      const fallbackMore = Boolean(cache.hasMorePast);

      // 이미 불러온 타임프레임은 캔들 캐시를 재사용하고 Zone만 다시 맞춤
      if (!forceResync && cache.initLoaded && cache.list.length > 0) {
        await ensureAllZoneBoxesLoadedOnce();
        return { data: cache.list, more: cache.hasMorePast };
      }

      // 최초 로드 또는 TF 전환 강제 리싱크: 서버에서 PAGE_LIMIT 만큼 가져오기
      const url = `/api/candles/${tfStr}?limit=${PAGE_LIMIT}`;
      try {
        const res = await fetch(url);
        if (!res.ok) {
          console.error("[CandleChart] loadInitCandlesForTf 응답 코드:", res.status);
          if (forceResync && fallbackData.length > 0) {
            return { data: fallbackData, more: fallbackMore };
          }
          return { data: [], more: false };
        }

        const arr = await safeJson(res, `init-${tfStr}`);
        if (!Array.isArray(arr)) {
          if (forceResync && fallbackData.length > 0) {
            return { data: fallbackData, more: fallbackMore };
          }
          return { data: [], more: false };
        }

        if (arr.length === 0) {
          cache.initLoaded = true;
          cache.list = [];
          cache.hasMorePast = false;
          cache.earliestTs = null;
          return { data: [], more: false };
        }

        const mapped = arr.map(mapServerCandle);
        const more = mapped.length === PAGE_LIMIT;

        // 이후 forward 로딩과 최신봉 반영이 같은 배열을 쓰도록 오름차순으로 유지
        mapped.sort((a, b) => a.timestamp - b.timestamp);
        cache.initLoaded = true;
        cache.list = mapped;
        cache.hasMorePast = more;
        cache.earliestTs = mapped[0]?.timestamp ?? null;

        await ensureAllZoneBoxesLoadedOnce();

        return { data: mapped, more };
      } catch (e) {
        console.error("[CandleChart] loadInitCandlesForTf 에러:", e);
        if (forceResync && fallbackData.length > 0) {
          return { data: fallbackData, more: fallbackMore };
        }
        return { data: [], more: false };
      } finally {
        if (forceResync) {
          FORCE_TF_RESYNC_ON_NEXT_INIT[tfStr] = false;
        }
      }
    }

    // v10: 통합 데이터 로더
    chart.setDataLoader({
      // 초기 진입과 과거 구간 로딩을 같은 캐시 경로로 처리하기 위함
      getBars: async ({ type, period, callback }) => {
        if (destroyed) {
          callback([], false);
          return;
        }

        const tfStr = periodToTf(period);
        if (!TF_LIST.includes(tfStr)) {
          callback([], false);
          return;
        }

        // 타임프레임별 캔들 캐시 준비
        if (!CANDLE_CACHE[tfStr]) {
          CANDLE_CACHE[tfStr] = {
            initLoaded: false,
            list: [],
            hasMorePast: true,
            earliestTs: null,
          };
        }
        const cache = CANDLE_CACHE[tfStr];

        // 실시간 최신봉은 subscribeBar 경로에서만 처리
        if (type === "update") {
          callback([], false);
          return;
        }

        // 첫 진입 시 캔들 캐시와 Zone 오버레이 준비를 함께 끝내기 위함
        if (type === "init") {
          try {
            const { data, more } = await loadInitCandlesForTf(tfStr);

            callback(data, more);

            // 데이터 반영 직후 다음 프레임에서 오버레이를 다시 그림
            const raf = typeof requestAnimationFrame === "function"
              ? requestAnimationFrame
              : (fn) => setTimeout(fn, 0);

            raf(() => {
              if (destroyed) return;
              applyZoneOverlays(chart);
            });
          } catch (e) {
            console.error("[CandleChart] getBars(init) 에러:", e);
            callback([], false);
          }
          return;
        }

        // 더 과거 구간을 왼쪽으로 이어붙이기 위함
        if (type === "forward") {
          if (!cache.hasMorePast || cache.earliestTs == null) {
            callback([], false);
            return;
          }

          const boundary = cache.earliestTs;
          const url = `/api/candles/${tfStr}?limit=${PAGE_LIMIT}&before=${boundary}`;

          pendingForwardLoadCountRef.current += 1;
          try {
            const res = await fetch(url);
            if (!res.ok) {
              console.error("[CandleChart] getBars(forward) 응답 코드:", res.status);
              callback([], false);
              return;
            }

            const arr = await safeJson(res, type);
            if (!Array.isArray(arr) || arr.length === 0) {
              cache.hasMorePast = false;
              callback([], false);
              return;
            }

            const mapped = arr.map(mapServerCandle);
            const more = mapped.length === PAGE_LIMIT;

            // 차트와 캐시가 같은 시간순 배열을 보도록 오름차순 유지
            mapped.sort((a, b) => a.timestamp - b.timestamp);
            cache.list = [...mapped, ...cache.list];
            cache.earliestTs = cache.list[0]?.timestamp ?? cache.earliestTs;
            cache.hasMorePast = more;

            // anchor timestamp가 없을 때는 데이터 인덱스로 현재 보는 위치를 보정
            if (zoomAnchorRef.current) {
              const currentAnchorTimestamp = zoomAnchorRef.current.timestamp;
              const currentAnchorDataIndex = zoomAnchorRef.current.dataIndex;
              if (
                !(
                  typeof currentAnchorTimestamp === "number" &&
                  Number.isFinite(currentAnchorTimestamp)
                ) &&
                typeof currentAnchorDataIndex === "number" &&
                Number.isFinite(currentAnchorDataIndex)
              ) {
                zoomAnchorRef.current = {
                  ...zoomAnchorRef.current,
                  dataIndex: currentAnchorDataIndex + mapped.length,
                };
              }
            }

            callback(mapped, more);
            const raf = typeof requestAnimationFrame === "function"
              ? requestAnimationFrame
              : (fn) => setTimeout(fn, 0);

            raf(() => {
              if (destroyed) return;
              applyZoneOverlays(chart);
            });
          } catch (e) {
            console.error("[CandleChart] getBars(forward) 에러:", e);
            callback([], false);
          } finally {
            pendingForwardLoadCountRef.current = Math.max(
              0,
              pendingForwardLoadCountRef.current - 1
            );
            maybeFinalizeZoomAnchorGesture();
          }
          return;
        }

        if (type === "backward") {
          callback([], false);
          return;
        }

        callback([], false);
      },

      // 최신봉은 짧은 주기로 polling해서 차트와 알림에 동시에 반영
      subscribeBar: ({ period, callback }) => {
        const tfStr = periodToTf(period);
        if (!TF_LIST.includes(tfStr)) return;

        stopLivePolling();

        liveTimer = setInterval(async () => {
          if (destroyed) return;
          if (isPageHidden()) return;
          if (livePollInFlight) return;

          livePollInFlight = true;
          let timeoutId = null;
          try {
            let fetchOptions = undefined;
            if (typeof AbortController === "function") {
              const controller = new AbortController();
              timeoutId = setTimeout(() => {
                controller.abort();
              }, LIVE_POLL_TIMEOUT_MS);
              fetchOptions = { signal: controller.signal };
            }

            const res = await fetch(`/api/candles/latest/${tfStr}`, fetchOptions);
            if (!res.ok) {
              console.error(
                "[CandleChart] /api/candles/latest 응답 코드:",
                res.status
              );
              return;
            }

            const c = await safeJson(res, "latest");
            if (!c || !c.start) return;

            const bar = mapServerCandle(c);

            // 새 봉 등장 여부 판단을 위해 차트의 마지막 봉을 기억
            let prevLastBar = null;
            if (typeof chart.getDataList === "function") {
              const all = chart.getDataList() || [];
              if (all.length > 0) {
                prevLastBar = all[all.length - 1];
              }
            }

            // 새 봉 판단은 아래 최신 가격/알림 갱신 흐름에서 함께 소비
            void prevLastBar;

            latestPriceRef.current = bar.close;
            setPositionOverlayLastPrice(bar.close);

            callback(bar);

            refreshZoneNotifications();
          } catch (e) {
            if (e?.name !== "AbortError") {
              // 일시적인 네트워크 오류는 무시
            }
          } finally {
            if (timeoutId) {
              clearTimeout(timeoutId);
            }
            livePollInFlight = false;
          }
        }, LIVE_POLL_INTERVAL_MS);
      },

      // 실시간 구독 해제
      unsubscribeBar: () => {
        stopLivePolling();
      },
    });

    void refreshMtfSourceCaches({ initial: true }).finally(() => {
      if (!destroyed) {
        startMtfSourcePolling();
      }
    });

    const visibilityChangeHandler = () => {
      if (!ENABLE_VISIBILITY_RESYNC || destroyed) return;

      if (isPageHidden()) {
        stopLivePolling();
        stopMtfSourcePolling();
        return;
      }

      startMtfSourcePolling();
      void refreshMtfSourceCaches({ initial: false });
      scheduleResync("visibilitychange");
    };

    const windowFocusHandler = () => {
      if (!ENABLE_VISIBILITY_RESYNC || destroyed) return;
      startMtfSourcePolling();
      void refreshMtfSourceCaches({ initial: false });
      scheduleResync("focus");
    };

    const windowOnlineHandler = () => {
      if (!ENABLE_VISIBILITY_RESYNC || destroyed) return;
      void refreshMtfSourceCaches({ initial: false });
      scheduleResync("online");
    };

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", visibilityChangeHandler);
    }
    if (typeof window !== "undefined") {
      window.addEventListener("focus", windowFocusHandler);
      window.addEventListener("online", windowOnlineHandler);
    }

    // Zone 상태 WebSocket 연결 시작
    connectZoneStateWs();

    // API 재시작 직후 차트 탭 진입 시 기존 포지션 오버레이를 즉시 복구
    void fetchAndApplyPositionOverlaySnapshot({ reason: "chart-enter", silent: true });

    // 포지션 오버레이 WebSocket 연결 시작
    connectPositionOverlayWs();

    // 좌/우 최소 바 설정 (왼쪽 끝까지 가야 로딩되도록)
    if (typeof chart.setLeftMinVisibleBarCount === "function") {
      chart.setLeftMinVisibleBarCount(0);
    }
    if (typeof chart.setRightMinVisibleBarCount === "function") {
      chart.setRightMinVisibleBarCount(0);
    }

    const fit = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        chart.resize();
      }
    };
    fit();

    const ro = new ResizeObserver(() => fit());
    ro.observe(el);

    let pendingIndicatorTooltipTogglePress = null;

    const clearPendingIndicatorTooltipTogglePress = () => {
      pendingIndicatorTooltipTogglePress = null;
    };

    const toChartLocalPoint = (clientX, clientY) => {
      if (
        typeof clientX !== "number" ||
        typeof clientY !== "number" ||
        !Number.isFinite(clientX) ||
        !Number.isFinite(clientY)
      ) {
        return { x: null, y: null };
      }

      const rect = el.getBoundingClientRect();
      return {
        x: clientX - rect.left,
        y: clientY - rect.top,
      };
    };

    const getReleasePointFromEvent = (nativeEvent) => {
      if (!nativeEvent) return { x: null, y: null };

      if (
        typeof nativeEvent.clientX === "number" &&
        typeof nativeEvent.clientY === "number"
      ) {
        return toChartLocalPoint(nativeEvent.clientX, nativeEvent.clientY);
      }

      const changedTouches = nativeEvent.changedTouches;
      if (changedTouches && changedTouches.length > 0) {
        const touch = changedTouches[0];
        return toChartLocalPoint(touch.clientX, touch.clientY);
      }

      return { x: null, y: null };
    };

    const finalizePendingIndicatorTooltipTogglePress = (nativeEvent = null) => {
      const pending = pendingIndicatorTooltipTogglePress;
      if (!pending) return;
      pendingIndicatorTooltipTogglePress = null;

      const elapsed = Date.now() - pending.at;
      if (elapsed < 0 || elapsed > INDICATOR_TOOLTIP_TOGGLE_PRESS_MAX_AGE_MS) {
        return;
      }

      const releasePoint = getReleasePointFromEvent(nativeEvent);
      const releaseX =
        typeof releasePoint.x === "number" && Number.isFinite(releasePoint.x)
          ? releasePoint.x
          : null;
      const releaseY =
        typeof releasePoint.y === "number" && Number.isFinite(releasePoint.y)
          ? releasePoint.y
          : null;

      markTooltipToggleClickHint({
        x: releaseX ?? pending.x,
        y: releaseY ?? pending.y,
        paneId: pending.paneId,
      });
      toggleIndicatorTooltipCollapsed();
    };

    const visibleRangeHandler = () => {
      refreshZoneNotifications();
    };
    const crosshairCacheHandler = (payload) => {
      lastCrosshairPayloadRef.current = payload ?? null;
      const toggleHovered = syncIndicatorTooltipToggleHovered(chart);
      if (toggleHovered) {
        clearZoneHoverByBridge();
      }
    };
    const indicatorTooltipFeatureClickHandler = (payload) => {
      const indicatorName = payload?.indicator?.name;
      const featureId = payload?.feature?.id;
      if (indicatorName !== INDICATOR_TOOLTIP_TOGGLE_NAME) return;
      if (featureId !== INDICATOR_TOOLTIP_TOGGLE_FEATURE_ID) return;

      const hintXRaw = lastCrosshairPayloadRef.current?.x;
      const hintYRaw = lastCrosshairPayloadRef.current?.y;
      const hintX =
        typeof hintXRaw === "number" && Number.isFinite(hintXRaw)
          ? hintXRaw
          : null;
      const hintY =
        typeof hintYRaw === "number" && Number.isFinite(hintYRaw)
          ? hintYRaw
          : null;
      const paneIdRaw =
        lastCrosshairPayloadRef.current?.paneId ??
        payload?.paneId ??
        resolveMainPaneId(chart);
      const paneId = typeof paneIdRaw === "string" ? paneIdRaw : null;

      pendingIndicatorTooltipTogglePress = {
        at: Date.now(),
        x: hintX,
        y: hintY,
        paneId,
      };

      markTooltipToggleClickHint({ x: hintX, y: hintY, paneId });
    };
    const windowMouseUpIndicatorTooltipToggleHandler = (event) => {
      finalizePendingIndicatorTooltipTogglePress(event);
    };
    const windowTouchEndIndicatorTooltipToggleHandler = (event) => {
      finalizePendingIndicatorTooltipTogglePress(event);
    };
    const windowPointerUpIndicatorTooltipToggleHandler = (event) => {
      finalizePendingIndicatorTooltipTogglePress(event);
    };
    const windowPointerCancelIndicatorTooltipToggleHandler = () => {
      clearPendingIndicatorTooltipTogglePress();
    };

    if (typeof window !== "undefined") {
      window.addEventListener(
        "mouseup",
        windowMouseUpIndicatorTooltipToggleHandler,
        mouseCaptureOptions
      );
      window.addEventListener(
        "touchend",
        windowTouchEndIndicatorTooltipToggleHandler,
        touchCaptureOptions
      );
      window.addEventListener(
        "pointerup",
        windowPointerUpIndicatorTooltipToggleHandler,
        passiveOptions
      );
      window.addEventListener(
        "touchcancel",
        windowPointerCancelIndicatorTooltipToggleHandler,
        touchCaptureOptions
      );
      window.addEventListener(
        "pointercancel",
        windowPointerCancelIndicatorTooltipToggleHandler,
        passiveOptions
      );
    }

    chart.subscribeAction?.("onVisibleRangeChange", visibleRangeHandler);
    chart.subscribeAction?.("onZoom", zoomHandler);
    chart.subscribeAction?.("onCrosshairChange", crosshairCacheHandler);
    chart.subscribeAction?.(
      "onIndicatorTooltipFeatureClick",
      indicatorTooltipFeatureClickHandler
    );

    return () => {
      chart.unsubscribeAction?.("onVisibleRangeChange", visibleRangeHandler);
      chart.unsubscribeAction?.("onZoom", zoomHandler);
      chart.unsubscribeAction?.("onCrosshairChange", crosshairCacheHandler);
      chart.unsubscribeAction?.(
        "onIndicatorTooltipFeatureClick",
        indicatorTooltipFeatureClickHandler
      );
      el.removeEventListener("mouseleave", hostMouseLeaveHandler);
      el.removeEventListener("pointerleave", hostMouseLeaveHandler);
      el.removeEventListener("wheel", captureWheelZoomAnchor, wheelCaptureOptions);
      el.removeEventListener("pointerdown", capturePointerZoomAnchorStart, passiveOptions);
      el.removeEventListener("touchstart", capturePositionLabelTouchDragStart, touchCaptureOptions);
      el.removeEventListener("mousedown", captureXAxisMouseDragStart, mouseCaptureOptions);
      el.removeEventListener("mousedown", captureYAxisMouseDragStart, mouseCaptureOptions);
      el.removeEventListener("touchstart", captureXAxisTouchDragStart, touchCaptureOptions);
      el.removeEventListener("touchstart", captureYAxisTouchDragStart, touchCaptureOptions);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", visibilityChangeHandler);
      }
      if (typeof window !== "undefined") {
        window.removeEventListener("mousemove", windowMouseMovePositionLabelDragHandler, mouseCaptureOptions);
        window.removeEventListener("mouseup", windowMouseUpPositionLabelDragHandler, mouseCaptureOptions);
        window.removeEventListener("touchmove", windowTouchMovePositionLabelDragHandler, touchCaptureOptions);
        window.removeEventListener("touchend", windowTouchEndPositionLabelDragHandler, touchCaptureOptions);
        window.removeEventListener("touchcancel", windowTouchEndPositionLabelDragHandler, touchCaptureOptions);
        window.removeEventListener("mousemove", windowMouseMoveXAxisDragHandler, mouseCaptureOptions);
        window.removeEventListener("mousemove", windowMouseMoveYAxisDragHandler, mouseCaptureOptions);
        window.removeEventListener("mousemove", windowMouseMoveHandler);
        window.removeEventListener("mouseup", windowMouseUpXAxisDragHandler, mouseCaptureOptions);
        window.removeEventListener("mouseup", windowMouseUpYAxisDragHandler, mouseCaptureOptions);
        window.removeEventListener("mouseup", windowMouseUpIndicatorTooltipToggleHandler, mouseCaptureOptions);
        window.removeEventListener("touchmove", windowTouchMoveXAxisDragHandler, touchCaptureOptions);
        window.removeEventListener("touchmove", windowTouchMoveYAxisDragHandler, touchCaptureOptions);
        window.removeEventListener("touchend", windowTouchEndXAxisDragHandler, touchCaptureOptions);
        window.removeEventListener("touchend", windowTouchEndYAxisDragHandler, touchCaptureOptions);
        window.removeEventListener("touchend", windowTouchEndIndicatorTooltipToggleHandler, touchCaptureOptions);
        window.removeEventListener("touchcancel", windowTouchEndXAxisDragHandler, touchCaptureOptions);
        window.removeEventListener("touchcancel", windowTouchEndYAxisDragHandler, touchCaptureOptions);
        window.removeEventListener("touchcancel", windowPointerCancelIndicatorTooltipToggleHandler, touchCaptureOptions);
        window.removeEventListener("blur", windowBlurHandler);
        window.removeEventListener("pointerup", endPointerZoomAnchor);
        window.removeEventListener("pointerup", windowPointerUpIndicatorTooltipToggleHandler);
        window.removeEventListener("pointercancel", endPointerZoomAnchor);
        window.removeEventListener("pointercancel", windowPointerCancelIndicatorTooltipToggleHandler);
        window.removeEventListener("focus", windowFocusHandler);
        window.removeEventListener("online", windowOnlineHandler);
      }
      clearWheelZoomEndTimer();
      zoomAnchorRef.current = null;
      zoomAnchorInputRef.current = null;
      zoomAnchorEndRequestedRef.current = false;
      xAxisDragZoomRef.current = null;
      yAxisDragZoomRef.current = null;
      pendingIndicatorTooltipTogglePress = null;
      cancelPositionLabelDrag();
      if (
        getPositionOverlayDragStartBridge() === beginPositionOverlayDragFromOverlayEvent
      ) {
        setPositionOverlayDragStartBridge(null);
      }
      pendingForwardLoadCountRef.current = 0;
      destroyed = true;
      if (resyncDebounceTimer) {
        clearTimeout(resyncDebounceTimer);
        resyncDebounceTimer = null;
      }
      ro.disconnect();
      stopLivePolling();
      stopMtfSourcePolling();
      try {
        dispose(el);
      } catch {
        // ignore
      }
      resetIndicatorTooltipToggleRuntimeState();
      resetMtfMaIndicatorRuntimeState();
      chartRef.current = null;
      lastCrosshairPayloadRef.current = null;

      // 다른 화면으로 나갈 때 전역 차트 참조를 함께 정리
      setZoneChart(null);
      setPositionOverlayChart(null);
      setPositionOverlayLastPrice(null);
      resetPositionOverlayRuntimeState();
      clearPositionOverlayRetryTimer();
      stopPositionOverlayDashAnimation();
    };
  }, [
    recreateIndicatorTooltipToggle,
    refreshMtfMaIndicator,
    toggleIndicatorTooltipCollapsed,
    refreshZoneNotifications,
  ]);

  return (
    <div className="cchart-layout">
      {/* 헤더 */}
      <div className="cchart-header">
        {/* 타임프레임 버튼 */}
        <div className="cchart-controls">
          {TF_LIST.map((k) => (
            <button
              key={k}
              onClick={() => setTf(k)}
              className={
                "cchart-btn cchart-btn--time" + (tf === k ? " is-active" : "")
              }
            >
              {formatTfButtonLabel(k)}
            </button>
          ))}

          {/* 최신 캔들 버튼 */}
          <button
            onClick={handleScrollToLatest}
            className="cchart-btn cchart-btn--latest"
          >
            ▶
          </button>
        </div>

        {/* BBand 토글 버튼 */}
        <button
          onClick={() => setShowBbands((prev) => !prev)}
          className={
            "cchart-btn cchart-btn--toggle" + (showBbands ? " is-active" : "")
          }
        >
          BBand
        </button>
      </div>

      {/* 차트 캔버스 컨테이너 */}
      <div className="cchart-canvas-wrap">
        <div ref={hostRef} className="cchart-host" />
      </div>
    </div>
  );
}
