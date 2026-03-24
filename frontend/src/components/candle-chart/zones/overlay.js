import {
  ZONE_OVERLAY_GROUP_ID,
  ZONE_OVERLAY_NAME,
} from "../constants";
import { projectTsToChart, resolveMainPaneId } from "../chartUtils";
import {
  zoneStore,
  getHoveredZoneId,
  setHoveredZoneId,
} from "./store";

// 차트 밖으로 커서가 나갔을 때 hover 표시를 비우기 위함
export function clearZoneHoverByBridge() {
  if (getHoveredZoneId() == null) return;

  if (
    typeof window !== "undefined" &&
    typeof window.__setZoneHoveredId === "function"
  ) {
    window.__setZoneHoveredId(null);
    return;
  }

  setHoveredZoneId(null);
  applyZoneOverlays();
}

// 메모리 상태에 있는 Zone 목록을 현재 차트 오버레이로 다시 그리기 위함
export function applyZoneOverlays(chartArg) {
  const chart = chartArg || zoneStore.chart;

  if (
    !chart ||
    typeof chart.createOverlay !== "function" ||
    typeof chart.removeOverlay !== "function"
  ) {
    return;
  }

  try {
    chart.removeOverlay({ name: ZONE_OVERLAY_NAME });
  } catch {
    // ignore
  }

  const zones = Object.values(zoneStore.zonesByTf).flat();
  if (!zones.length) return;

  const dataList = chart.getDataList?.() || [];
  if (!dataList.length) return;

  const chartEarliest = Number(dataList[0]?.timestamp);
  const chartLatest = Number(dataList[dataList.length - 1]?.timestamp);
  const mainPaneId = resolveMainPaneId(chart);

  const overlayConfigs = zones
    .map((zone) => {
      const startTs = Number(zone.startTs);
      if (!Number.isFinite(startTs)) return null;

      const isBroken = !!zone.isBroken;
      const endTs = zone.endTs != null ? Number(zone.endTs) : null;

      if (Number.isFinite(chartLatest) && startTs > chartLatest) return null;
      if (
        isBroken &&
        endTs != null &&
        Number.isFinite(chartEarliest) &&
        endTs < chartEarliest
      ) {
        return null;
      }

      const startAnchor = projectTsToChart(dataList, startTs, "floor");
      if (startAnchor == null) return null;

      let endAnchor = startAnchor;
      if (isBroken) {
        const targetEnd = endTs != null ? endTs : startTs;
        endAnchor = projectTsToChart(dataList, targetEnd, "ceil") ?? startAnchor;
        if (endAnchor < startAnchor) endAnchor = startAnchor;
      }

      return {
        name: ZONE_OVERLAY_NAME,
        groupId: ZONE_OVERLAY_GROUP_ID,
        paneId: mainPaneId,
        lock: true,
        points: [
          { timestamp: startAnchor, value: zone.upper },
          { timestamp: isBroken ? endAnchor : startAnchor, value: zone.lower },
        ],
        extendData: {
          id: zone.id,
          side: zone.side,
          isBroken,
          tf: String(zone.tf || zone.intervalMin || ""),
          isActive: !!zone.isActive,
          hoveredBoxId: zoneStore.hoveredBoxId,
          startTs,
        },
      };
    })
    .filter(Boolean);

  chart.createOverlay(overlayConfigs);
}
