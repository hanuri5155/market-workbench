import { useCallback, useEffect } from "react";

import { saveZoneStateToServer } from "../../../api/zoneState";
import {
  buildNotificationItemsFromZones,
  getVisibleZoneBoxes,
  normalizeZoneBoxes,
  selectTop15AroundMidPrice,
} from "../../zones/zoneNotificationUtils";
import {
  DEFAULT_SYMBOL,
  MTF_MA_SOURCE_TF_LIST,
  TF_LIST,
} from "../constants";
import { applyZoneOverlays } from "../zones/overlay";
import {
  applyZoneDelta,
  getZonesForTf,
  setHoveredZoneId,
  toggleZoneActiveById,
} from "../zones/store";
import { upsertMtfMaSourceCandles } from "../indicators/mtfMa";
import { setPositionOverlayLastPrice } from "../positionOverlay/store";

export function useZoneNotificationSync({
  chartRef,
  latestPriceRef,
  tf,
  hoveredBoxId,
  setHoveredBoxId,
  setNotificationItems,
  refreshMtfMaIndicator,
  patchRestConfirmedCandleInCache,
}) {
  // 현재 화면 범위 안에서 알림에 보여줄 Zone을 다시 계산하기 위함
  const refreshZoneNotifications = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const dataList = chart.getDataList?.() || [];
    if (!dataList.length) {
      setNotificationItems([]);
      return;
    }

    const lastBar = dataList[dataList.length - 1];
    const lastClose = lastBar?.close;

    let visibleHigh = null;
    let visibleLow = null;
    let mainPaneId = "candle_pane";

    if (typeof chart.getPaneOptions === "function") {
      try {
        const panes = chart.getPaneOptions();
        if (Array.isArray(panes) && panes[0]?.id) {
          mainPaneId = panes[0].id;
        }
      } catch (error) {
        console.warn("[CandleChart] getPaneOptions 실패 (notifications):", error);
      }
    }

    try {
      if (
        typeof chart.getSize === "function" &&
        typeof chart.convertFromPixel === "function"
      ) {
        const size =
          chart.getSize(mainPaneId, "main") || chart.getSize(mainPaneId);

        let paneTopLocal = 0;
        let paneBottomLocal = 0;

        if (size) {
          const bounding = size.bounding || size;
          const topValue = typeof bounding.top === "number" ? bounding.top : 0;
          const heightValue =
            typeof bounding.height === "number" ? bounding.height : 0;

          paneTopLocal = topValue;
          paneBottomLocal = topValue + heightValue;
        }

        const result = chart.convertFromPixel(
          [
            { y: paneTopLocal + 1 },
            { y: paneBottomLocal - 2 },
          ],
          { paneId: mainPaneId, absolute: false }
        );

        const topInfo = Array.isArray(result) ? result[0] : result;
        const bottomInfo = Array.isArray(result) ? result[1] : null;

        const topValue = topInfo?.value;
        const bottomValue = bottomInfo?.value;

        if (
          typeof topValue === "number" &&
          typeof bottomValue === "number" &&
          topValue !== bottomValue
        ) {
          visibleHigh = Math.max(topValue, bottomValue);
          visibleLow = Math.min(topValue, bottomValue);
        }
      }
    } catch (error) {
      console.warn("[CandleChart] convertFromPixel 기반 Y축 범위 계산 실패:", error);
    }

    if (visibleHigh == null || visibleLow == null) {
      const range = chart.getVisibleRange?.();
      if (!range) {
        setNotificationItems([]);
        return;
      }

      const from = Math.max(0, Math.floor(range.from));
      const to = Math.min(dataList.length - 1, Math.ceil(range.to));
      const visibleData = dataList.slice(from, to + 1);
      if (!visibleData.length) {
        setNotificationItems([]);
        return;
      }

      visibleHigh = Math.max(...visibleData.map((data) => data.high));
      visibleLow = Math.min(...visibleData.map((data) => data.low));
    }

    if (visibleHigh == null || visibleLow == null) {
      setNotificationItems([]);
      return;
    }

    const midPrice = (visibleHigh + visibleLow) / 2;
    const rawBoxes = getZonesForTf(tf);
    const normalizedBoxes = normalizeZoneBoxes(rawBoxes);
    const visibleBoxes = getVisibleZoneBoxes(normalizedBoxes, visibleHigh, visibleLow);

    if (!visibleBoxes.length) {
      setNotificationItems([]);
      return;
    }

    const selectedBoxes = selectTop15AroundMidPrice(visibleBoxes, midPrice);
    const currentPrice = latestPriceRef.current ?? lastClose;
    const items = buildNotificationItemsFromZones(selectedBoxes, currentPrice);
    setNotificationItems(items);
  }, [chartRef, latestPriceRef, setNotificationItems, tf]);

  // 차트 hover와 알림 hover가 같은 id를 바라보도록 맞추기 위함
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handler = (boxId) => {
      setHoveredZoneId(boxId);
      setHoveredBoxId(boxId);
      applyZoneOverlays();
    };

    window.__setZoneHoveredId = handler;

    return () => {
      if (window.__setZoneHoveredId === handler) {
        window.__setZoneHoveredId = null;
      }
    };
  }, [setHoveredBoxId]);

  // 알림 패널에서 선택한 hover를 차트 오버레이에도 반영하기 위함
  useEffect(() => {
    setHoveredZoneId(hoveredBoxId);
    applyZoneOverlays();
  }, [hoveredBoxId]);

  // REST 확정 캔들이 들어오면 캐시와 알림을 같은 기준으로 맞추기 위함
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handler = (event) => {
      const detail = event.detail || {};
      const { symbol, tf: tfStrRaw, candle } = detail;

      if (!symbol || symbol !== DEFAULT_SYMBOL.ticker || !candle) return;

      const tfStr = String(tfStrRaw);
      if (!TF_LIST.includes(tfStr)) return;

      if (MTF_MA_SOURCE_TF_LIST.includes(tfStr)) {
        const mtfChanged = upsertMtfMaSourceCandles(tfStr, [candle]);
        if (mtfChanged) {
          refreshMtfMaIndicator(`rest-confirmed-${tfStr}`);
        }
      }

      const { updated, newBar } = patchRestConfirmedCandleInCache(tfStr, candle);
      if (!updated) return;

      const chart = chartRef.current;
      if (tfStr !== tf || !chart) return;

      try {
        latestPriceRef.current = newBar.close;
        setPositionOverlayLastPrice(newBar.close);

        const close = Number(candle.close);
        if (Number.isFinite(close)) {
          latestPriceRef.current = close;
          setPositionOverlayLastPrice(close);
        }

        refreshZoneNotifications();
      } catch (error) {
        console.error("[CandleChart] candle_rest_confirmed 후 알림 갱신 중 오류:", error);
      }
    };

    window.addEventListener("candle_rest_confirmed", handler);
    return () => window.removeEventListener("candle_rest_confirmed", handler);
  }, [
    chartRef,
    latestPriceRef,
    patchRestConfirmedCandleInCache,
    refreshMtfMaIndicator,
    tf,
    refreshZoneNotifications,
  ]);

  // MTF MA 소스 실시간 캔들을 받아 차트 인디케이터를 즉시 갱신하기 위함
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handler = (event) => {
      const detail = event.detail || {};
      const { symbol, tf: tfStrRaw, candle } = detail;

      if (!symbol || symbol !== DEFAULT_SYMBOL.ticker || !candle) return;

      const tfStr = String(tfStrRaw);
      if (!MTF_MA_SOURCE_TF_LIST.includes(tfStr)) return;

      const mtfChanged = upsertMtfMaSourceCandles(tfStr, [candle]);
      if (mtfChanged) {
        refreshMtfMaIndicator(`ws-live-${tfStr}`);
      }
    };

    window.addEventListener("mtf_ma_source_candle", handler);
    return () => window.removeEventListener("mtf_ma_source_candle", handler);
  }, [refreshMtfMaIndicator]);

  // 새 Zone 생성/깨짐 이벤트를 메모리와 알림에 함께 반영하기 위함
  useEffect(() => {
    const onBoxDelta = (event) => {
      const detail = event?.detail || {};
      const symbol = detail.symbol;
      if (symbol && symbol !== DEFAULT_SYMBOL.ticker) return;

      const tfStr = String(detail.tf ?? "");
      const rawDelta = detail.delta;
      const delta =
        rawDelta &&
        typeof rawDelta === "object" &&
        rawDelta.delta &&
        typeof rawDelta.delta === "object"
          ? rawDelta.delta
          : rawDelta;

      if (!delta) return;

      applyZoneDelta(tfStr, delta);
      applyZoneOverlays();

      if (tfStr === tf) {
        refreshZoneNotifications();
      }
    };

    window.addEventListener("zone_delta", onBoxDelta);
    return () => window.removeEventListener("zone_delta", onBoxDelta);
  }, [tf, refreshZoneNotifications]);

  // 알림 패널 클릭이 차트 클릭과 같은 저장 경로를 타도록 맞추기 위함
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handler = (boxId) => {
      if (!boxId) return;

      const persistPayload = toggleZoneActiveById(boxId);
      if (!persistPayload) return;

      applyZoneOverlays();
      saveZoneStateToServer(persistPayload);
      refreshZoneNotifications();
    };

    window.__toggleZoneActiveById = handler;

    return () => {
      if (window.__toggleZoneActiveById === handler) {
        window.__toggleZoneActiveById = null;
      }
    };
  }, [refreshZoneNotifications]);

  return refreshZoneNotifications;
}
