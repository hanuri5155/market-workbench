import { registerIndicator } from "klinecharts";

import {
  ZONE_CLICK_SUPPRESS_RADIUS_PX,
  ZONE_CLICK_SUPPRESS_WINDOW_MS,
  INDICATOR_TOOLTIP_TOGGLE_BUTTON_PATH,
  INDICATOR_TOOLTIP_TOGGLE_COLLAPSED_FRAME_INDEX,
  INDICATOR_TOOLTIP_TOGGLE_EXPANDED_FRAME_INDEX,
  INDICATOR_TOOLTIP_TOGGLE_FEATURE_ID,
  INDICATOR_TOOLTIP_TOGGLE_FRAME_INTERVAL_MS,
  INDICATOR_TOOLTIP_TOGGLE_NAME,
  INDICATOR_TOOLTIP_TOGGLE_PATH_FRAMES,
  INDICATOR_TOOLTIP_TOGGLE_PRIMER_FEATURE_ID,
} from "../constants";
import { resolveMainPaneId } from "../chartUtils";
import {
  applyZoneOverlays,
  clearZoneHoverByBridge,
} from "../zones/overlay";

// 다른 인디케이터 툴팁을 접을 때 빈 데이터를 반환하기 위함
function createHiddenIndicatorTooltipDataSource() {
  return {
    name: "",
    calcParamsText: "",
    legends: [],
    features: [],
  };
}

// 클릭 직후 차트 오버레이 클릭을 잠시 막기 위한 좌표 기록
function createTooltipToggleClickHint() {
  return {
    at: 0,
    x: null,
    y: null,
    paneId: null,
  };
}

// 툴팁 토글 인디케이터의 임시 상태를 보관하기 위함
const tooltipToggleStore = {
  registered: false,
  hovered: false,
  clickHint: createTooltipToggleClickHint(),
  indicatorId: null,
  collapsed: false,
  frameIndex: INDICATOR_TOOLTIP_TOGGLE_EXPANDED_FRAME_INDEX,
  originalCreateTooltipMap: new Map(),
  animationTimer: null,
  animationToken: 0,
};

// 차트 우상단 토글 버튼 인디케이터를 한 번만 등록하기 위함
export function ensureIndicatorTooltipToggleRegistered() {
  if (tooltipToggleStore.registered) return;

  registerIndicator({
    name: INDICATOR_TOOLTIP_TOGGLE_NAME,
    shortName: "",
    series: "price",
    figures: [],
    calc: (dataList) => dataList.map(() => ({})),
    createTooltipDataSource: () => {
      const frameIndexRaw = Number(tooltipToggleStore.frameIndex);
      const frameIndex = Number.isInteger(frameIndexRaw)
        ? Math.max(
            INDICATOR_TOOLTIP_TOGGLE_COLLAPSED_FRAME_INDEX,
            Math.min(
              INDICATOR_TOOLTIP_TOGGLE_EXPANDED_FRAME_INDEX,
              frameIndexRaw
            )
          )
        : INDICATOR_TOOLTIP_TOGGLE_EXPANDED_FRAME_INDEX;
      const path = INDICATOR_TOOLTIP_TOGGLE_PATH_FRAMES[frameIndex];

      return {
        name: " ",
        calcParamsText: "",
        legends: [],
        features: [
          {
            id: INDICATOR_TOOLTIP_TOGGLE_FEATURE_ID,
            position: "left",
            marginLeft: 8,
            marginRight: 8,
            marginTop: 2,
            marginBottom: 2,
            paddingLeft: 0,
            paddingTop: 0,
            paddingRight: 0,
            paddingBottom: 0,
            size: 18,
            type: "path",
            borderRadius: 6,
            content: {
              style: "stroke",
              path: INDICATOR_TOOLTIP_TOGGLE_BUTTON_PATH,
              lineWidth: 0.85,
            },
            color: "rgba(255, 255, 255, 0.5)",
            activeColor: "rgba(255, 255, 255, 0.9)",
            backgroundColor: "transparent",
            activeBackgroundColor: "transparent",
          },
          {
            id: INDICATOR_TOOLTIP_TOGGLE_PRIMER_FEATURE_ID,
            position: "left",
            marginLeft: 0,
            marginRight: 0,
            marginTop: 0,
            marginBottom: 0,
            paddingLeft: 0,
            paddingTop: 0,
            paddingRight: 0,
            paddingBottom: 0,
            size: 0,
            type: "icon_font",
            borderRadius: 0,
            content: {
              family: "Helvetica Neue",
              code: "",
            },
            color: "#FFFFFF",
            activeColor: "#FFFFFF",
            backgroundColor: "transparent",
            activeBackgroundColor: "transparent",
          },
          {
            id: INDICATOR_TOOLTIP_TOGGLE_FEATURE_ID,
            position: "left",
            marginLeft: -22,
            marginRight: 12,
            marginTop: 7,
            marginBottom: 0,
            paddingLeft: 0,
            paddingTop: 0,
            paddingRight: 0,
            paddingBottom: 0,
            size: 10,
            type: "path",
            borderRadius: 0,
            content: {
              style: "fill",
              path,
              lineWidth: 1,
            },
            color: "#FFFFFF",
            activeColor: "#FFFFFF",
            backgroundColor: "transparent",
            activeBackgroundColor: "transparent",
          },
        ],
      };
    },
  });

  tooltipToggleStore.registered = true;
}

// 토글 버튼을 누른 직후 같은 좌표의 오버레이 클릭을 무시하기 위함
export function markTooltipToggleClickHint(hint = {}) {
  tooltipToggleStore.clickHint = {
    at: Date.now(),
    x: Number.isFinite(Number(hint.x)) ? Number(hint.x) : null,
    y: Number.isFinite(Number(hint.y)) ? Number(hint.y) : null,
    paneId: typeof hint.paneId === "string" ? hint.paneId : null,
  };
}

// 툴팁 토글 클릭이 Zone 클릭으로 잘못 이어지는 것을 막기 위함
export function shouldSuppressZoneClick(event) {
  const hint = tooltipToggleStore.clickHint;
  if (!hint?.at) return false;

  const elapsed = Date.now() - hint.at;
  if (elapsed < 0 || elapsed > ZONE_CLICK_SUPPRESS_WINDOW_MS) {
    return false;
  }

  const eventX = Number(event?.x);
  const eventY = Number(event?.y);
  const samePane =
    !hint.paneId ||
    !event?.paneId ||
    String(hint.paneId) === String(event.paneId);

  if (
    samePane &&
    Number.isFinite(eventX) &&
    Number.isFinite(eventY) &&
    Number.isFinite(hint.x) &&
    Number.isFinite(hint.y)
  ) {
    const dx = eventX - hint.x;
    const dy = eventY - hint.y;
    return (
      dx * dx + dy * dy <=
      ZONE_CLICK_SUPPRESS_RADIUS_PX * ZONE_CLICK_SUPPRESS_RADIUS_PX
    );
  }

  return true;
}

// 메인 툴팁의 클릭 영역이 다른 레이어보다 앞에 오게 맞추기 위함
export function prioritizeMainTooltipFeatureEvents(chart) {
  if (!chart || typeof chart.getDrawPaneById !== "function") return;

  try {
    const mainPane = chart.getDrawPaneById(resolveMainPaneId(chart));
    const mainWidget = mainPane?.getMainWidget?.();
    if (!mainWidget) return;

    const children = mainWidget._children;
    const tooltipView = mainWidget._tooltipView;
    if (!Array.isArray(children) || !tooltipView) return;

    const currentIndex = children.indexOf(tooltipView);
    if (currentIndex < 0 || currentIndex === children.length - 1) return;

    children.splice(currentIndex, 1);
    children.push(tooltipView);
  } catch (error) {
    console.warn("[CandleChart] tooltip event 우선순위 보정 실패:", error);
  }
}

// 현재 커서가 토글 버튼 위에 있는지 확인하기 위함
export function isIndicatorTooltipToggleHovered(chart) {
  if (!chart || typeof chart.getDrawPaneById !== "function") return false;

  try {
    const mainPane = chart.getDrawPaneById(resolveMainPaneId(chart));
    const mainWidget = mainPane?.getMainWidget?.();
    const activeFeatureInfo = mainWidget?._tooltipView?._activeFeatureInfo;
    if (!activeFeatureInfo) return false;

    const featureId = activeFeatureInfo?.feature?.id;
    const indicatorName = activeFeatureInfo?.indicator?.name;
    return (
      featureId === INDICATOR_TOOLTIP_TOGGLE_FEATURE_ID &&
      indicatorName === INDICATOR_TOOLTIP_TOGGLE_NAME
    );
  } catch {
    return false;
  }
}

// 토글 hover 여부는 차트 오버레이 표시 규칙과도 연결되기 때문에 별도 저장함
export function getIndicatorTooltipToggleHovered() {
  return tooltipToggleStore.hovered;
}

export function setIndicatorTooltipToggleHovered(nextHovered) {
  const normalized = !!nextHovered;
  if (tooltipToggleStore.hovered === normalized) return normalized;

  tooltipToggleStore.hovered = normalized;
  if (normalized) {
    clearZoneHoverByBridge();
  } else {
    applyZoneOverlays();
  }
  return normalized;
}

// 크로스헤어 이동 중 hover 상태를 런타임 저장소와 동기화하기 위함
export function syncIndicatorTooltipToggleHovered(chart) {
  return setIndicatorTooltipToggleHovered(
    isIndicatorTooltipToggleHovered(chart)
  );
}

// 인디케이터 설정 변경 직후 즉시 화면을 다시 그리기 위함
function forceIndicatorTooltipRedraw(chart, crosshairPayload) {
  if (!chart || typeof chart.executeAction !== "function") return;
  if (!crosshairPayload) return;

  chart.executeAction("onCrosshairChange", crosshairPayload);
}

// 접힘/펼침 애니메이션 프레임을 교체하기 위함
function updateIndicatorTooltipToggleFrame(chart, frameIndex, crosshairPayload) {
  if (
    !chart ||
    !tooltipToggleStore.indicatorId ||
    typeof chart.overrideIndicator !== "function"
  ) {
    return;
  }

  try {
    chart.overrideIndicator({
      id: tooltipToggleStore.indicatorId,
      extendData: { frameIndex },
    });
    forceIndicatorTooltipRedraw(chart, crosshairPayload);
  } catch (error) {
    console.warn("[CandleChart] 토글 인디케이터 frame 업데이트 실패:", error);
  }
}

// 새 애니메이션 시작 전에 이전 타이머를 정리하기 위함
export function clearIndicatorTooltipArrowAnimation() {
  tooltipToggleStore.animationToken += 1;
  if (tooltipToggleStore.animationTimer != null) {
    clearTimeout(tooltipToggleStore.animationTimer);
    tooltipToggleStore.animationTimer = null;
  }
}

// 다른 인디케이터 툴팁 본문을 숨기고 토글 버튼만 남기기 위함
function setIndicatorsTooltipHidden(chart, hidden) {
  if (
    !chart ||
    typeof chart.getIndicators !== "function" ||
    typeof chart.overrideIndicator !== "function"
  ) {
    return;
  }

  const indicators = chart.getIndicators();
  const activeIndicatorIds = new Set();

  indicators.forEach((indicator) => {
    if (!indicator || indicator.name === INDICATOR_TOOLTIP_TOGGLE_NAME) return;

    activeIndicatorIds.add(indicator.id);
    if (!tooltipToggleStore.originalCreateTooltipMap.has(indicator.id)) {
      tooltipToggleStore.originalCreateTooltipMap.set(
        indicator.id,
        indicator.createTooltipDataSource ?? null
      );
    }

    try {
      if (hidden) {
        chart.overrideIndicator({
          id: indicator.id,
          createTooltipDataSource: createHiddenIndicatorTooltipDataSource,
        });
      } else {
        const originalCreateTooltipDataSource =
          tooltipToggleStore.originalCreateTooltipMap.get(indicator.id);
        chart.overrideIndicator({
          id: indicator.id,
          createTooltipDataSource:
            typeof originalCreateTooltipDataSource === "function"
              ? originalCreateTooltipDataSource
              : null,
        });
      }
    } catch (error) {
      console.warn("[CandleChart] indicator tooltip override 실패:", error);
    }
  });

  for (const indicatorId of Array.from(tooltipToggleStore.originalCreateTooltipMap.keys())) {
    if (!activeIndicatorIds.has(indicatorId)) {
      tooltipToggleStore.originalCreateTooltipMap.delete(indicatorId);
    }
  }
}

// 토글 인디케이터 재생성
export function recreateIndicatorTooltipToggle(chart, crosshairPayload = null) {
  if (!chart) return;

  if (
    tooltipToggleStore.indicatorId &&
    typeof chart.removeIndicator === "function"
  ) {
    try {
      chart.removeIndicator({ id: tooltipToggleStore.indicatorId });
    } catch (error) {
      console.warn("[CandleChart] 기존 토글 인디케이터 제거 실패:", error);
    } finally {
      tooltipToggleStore.indicatorId = null;
    }
  }

  if (typeof chart.createIndicator !== "function") return;

  try {
    const indicatorId = chart.createIndicator(
      {
        name: INDICATOR_TOOLTIP_TOGGLE_NAME,
        visible: true,
        extendData: { frameIndex: tooltipToggleStore.frameIndex },
      },
      true,
      { id: resolveMainPaneId(chart) }
    );

    tooltipToggleStore.indicatorId =
      typeof indicatorId === "string" ? indicatorId : null;
    setIndicatorsTooltipHidden(chart, tooltipToggleStore.collapsed);
    forceIndicatorTooltipRedraw(chart, crosshairPayload);
  } catch (error) {
    tooltipToggleStore.indicatorId = null;
    console.warn("[CandleChart] 토글 인디케이터 생성 실패:", error);
  }
}

// 화살표 애니메이션
function animateIndicatorTooltipArrow(chart, crosshairPayload, toCollapsed) {
  clearIndicatorTooltipArrowAnimation();

  const token = tooltipToggleStore.animationToken;
  const frames = toCollapsed ? [4, 3, 2, 1, 0] : [0, 1, 2, 3, 4];
  let index = 0;

  const tick = () => {
    if (tooltipToggleStore.animationToken !== token) return;

    const nextFrame = frames[index];
    tooltipToggleStore.frameIndex = nextFrame;
    updateIndicatorTooltipToggleFrame(chart, nextFrame, crosshairPayload);

    index += 1;
    if (index < frames.length) {
      tooltipToggleStore.animationTimer = setTimeout(
        tick,
        INDICATOR_TOOLTIP_TOGGLE_FRAME_INTERVAL_MS
      );
    } else {
      tooltipToggleStore.animationTimer = null;
    }
  };

  tick();
}

// 접기 / 펼치기 토글
export function toggleIndicatorTooltipCollapsed(chart, crosshairPayload = null) {
  if (!chart) return;

  const nextCollapsed = !tooltipToggleStore.collapsed;
  tooltipToggleStore.collapsed = nextCollapsed;

  setIndicatorsTooltipHidden(chart, nextCollapsed);
  animateIndicatorTooltipArrow(chart, crosshairPayload, nextCollapsed);
  forceIndicatorTooltipRedraw(chart, crosshairPayload);
}

// 런타임 초기화
export function resetIndicatorTooltipToggleRuntimeState() {
  clearIndicatorTooltipArrowAnimation();
  tooltipToggleStore.originalCreateTooltipMap.clear();
  tooltipToggleStore.clickHint = createTooltipToggleClickHint();
  tooltipToggleStore.hovered = false;
  tooltipToggleStore.indicatorId = null;
  tooltipToggleStore.collapsed = false;
  tooltipToggleStore.frameIndex = INDICATOR_TOOLTIP_TOGGLE_EXPANDED_FRAME_INDEX;
}
