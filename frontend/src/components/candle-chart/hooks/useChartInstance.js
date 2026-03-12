import { useCallback, useEffect } from "react";

import { TF_LIST } from "../constants";
import { resolveMainPaneId, tfToPeriod } from "../chartUtils";
import { applyPositionOverlays } from "../positionOverlay/overlay";

export function useChartInstance({
  chartRef,
  chartEpoch,
  tf,
  showBbands,
  refreshZoneNotifications,
  recreateIndicatorTooltipToggle,
  captureManualMainYAxisRange,
  restoreManualMainYAxisRange,
  markForceTfResyncOnNextInit,
}) {
  // 사용자가 확대/축소를 많이 건드린 뒤에도 메인 축을 자동 범위로 돌리기 위함
  const resetYAxis = useCallback(() => {
    const chart = chartRef.current;
    if (
      !chart ||
      typeof chart.getPaneOptions !== "function" ||
      typeof chart.setPaneOptions !== "function"
    ) {
      return;
    }

    try {
      const panes = chart.getPaneOptions();
      if (!Array.isArray(panes)) return;

      panes.forEach((pane) => {
        if (!pane || !pane.id) return;

        chart.setPaneOptions({
          id: pane.id,
          axis: {
            ...(pane.axis || {}),
            price: {
              ...(pane.axis?.price || {}),
              autoScale: true,
            },
          },
        });
      });
    } catch (error) {
      console.warn("[CandleChart] resetYAxis 실패:", error);
    }
  }, [chartRef]);

  // 현재 시점으로 바로 되돌아가는 버튼 동작을 묶기 위함
  const handleScrollToLatest = useCallback(() => {
    const chart = chartRef.current;
    if (!chart || typeof chart.scrollToRealTime !== "function") return;

    resetYAxis();
    chart.scrollToRealTime(200);
  }, [chartRef, resetYAxis]);

  // 차트 재생성 시 BOLL과 툴팁 토글을 현재 설정대로 다시 붙이기 위함
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const mainPaneId = resolveMainPaneId(chart);
    const manualYAxisSnapshot = captureManualMainYAxisRange(chart);

    if (showBbands) {
      if (typeof chart.removeIndicator === "function") {
        try {
          chart.removeIndicator({ paneId: mainPaneId, name: "BOLL" });
        } catch {
          // ignore
        }
      }

      if (typeof chart.createIndicator === "function") {
        try {
          chart.createIndicator(
            {
              name: "BOLL",
              calcParams: [20, 2],
            },
            true,
            { id: mainPaneId }
          );
        } catch (error) {
          console.warn("[CandleChart] BOLL indicator 생성 실패:", error);
        }
      }
    } else if (typeof chart.removeIndicator === "function") {
      try {
        chart.removeIndicator({ paneId: mainPaneId, name: "BOLL" });
      } catch (error) {
        console.warn("[CandleChart] BOLL indicator 제거 실패:", error);
      }
    }

    recreateIndicatorTooltipToggle(chart);

    if (manualYAxisSnapshot) {
      restoreManualMainYAxisRange(chart, manualYAxisSnapshot);
    }
  }, [
    captureManualMainYAxisRange,
    chartRef,
    chartEpoch,
    recreateIndicatorTooltipToggle,
    restoreManualMainYAxisRange,
    showBbands,
    tf,
  ]);

  // 타임프레임 변경 직후 차트, 알림, 오버레이가 같은 기준을 쓰게 맞추기 위함
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const tfStr = String(tf);
    if (TF_LIST.includes(tfStr)) {
      markForceTfResyncOnNextInit(tfStr);
      chart.setPeriod(tfToPeriod(tfStr));
      if (typeof chart.resetData === "function") {
        chart.resetData();
      }
    }

    refreshZoneNotifications();
    applyPositionOverlays();
  }, [
    chartRef,
    chartEpoch,
    markForceTfResyncOnNextInit,
    tf,
    refreshZoneNotifications,
  ]);

  return {
    handleScrollToLatest,
    resetYAxis,
  };
}
