import { DEFAULT_SYMBOL, ZONE_TF_LIST } from "../constants";
import { safeJson } from "../chartUtils";
import {
  applyZoneStatePayload,
  clearZoneInFlightRequest,
  getZoneInFlightRequest,
  getZoneRangeKey,
  isZoneRangeCovered,
  isZoneRangeStale,
  applyStateToZones,
  markZoneRangeLoaded,
  mergeZoneStartupSnapshot,
  replaceZonesInRange,
  setZoneInFlightRequest,
  zoneStore,
  makeZoneKey,
} from "./store";
import { applyZoneOverlays } from "./overlay";
import { mark, measure } from "../../../utils/chartPerf";

// 타임프레임별 활성화 상태와 진입가 보정값을 먼저 불러오기 위함
export async function fetchZoneStateForTf(tfStr) {
  if (!ZONE_TF_LIST.includes(String(tfStr))) return;

  try {
    const url = `/api/zones/state?tf=${tfStr}&symbol=${DEFAULT_SYMBOL.ticker}`;
    const res = await fetch(url);
    if (!res.ok) {
      console.warn("[CandleChart] zone state 응답 코드:", res.status);
      return;
    }

    const rows = await safeJson(res, `boxes-state-${tfStr}`);
    applyZoneStatePayload(tfStr, rows);
    applyZoneOverlays();
  } catch (error) {
    console.error("[CandleChart] fetchZoneStateForTf 에러:", tfStr, error);
  }
}

// 차트에 그릴 Zone 본문 목록을 타임프레임 단위로 채우기 위함
export async function fetchZoneBoxesForTf(
  tfStr,
  {
    force = false,
    silent = false,
    fromMs = null,
    toMs = null,
    staleMs = 15_000,
    revalidateIfStale = false,
  } = {}
) {
  if (!ZONE_TF_LIST.includes(String(tfStr))) return [];

  const keyTf = String(tfStr);
  const requestKey = getZoneRangeKey(fromMs, toMs);
  const inFlightRequest = getZoneInFlightRequest(keyTf, requestKey);

  if (!force && inFlightRequest) {
    return inFlightRequest;
  }

  if (!force && isZoneRangeCovered(keyTf, fromMs, toMs)) {
    const cachedZones = zoneStore.zonesByTf[keyTf] || [];
    if (revalidateIfStale && isZoneRangeStale(keyTf, staleMs) && !inFlightRequest) {
      void fetchZoneBoxesForTf(keyTf, {
        force: true,
        silent: true,
        fromMs,
        toMs,
        staleMs,
      });
    }
    return cachedZones;
  }

  const requestPromise = (async () => {
    try {
      const symbol = DEFAULT_SYMBOL.ticker;
      let url =
        `/api/zones?symbol=${encodeURIComponent(symbol)}` +
        `&intervalMin=${encodeURIComponent(keyTf)}`;
      if (Number.isFinite(Number(fromMs))) {
        url += `&from=${encodeURIComponent(Number(fromMs))}`;
      }
      if (Number.isFinite(Number(toMs))) {
        url += `&to=${encodeURIComponent(Number(toMs))}`;
      }

      mark(`zone_boxes_tf_${keyTf}_start`, {
        tf: keyTf,
        url,
        request: true,
        fromMs: Number.isFinite(Number(fromMs)) ? Number(fromMs) : null,
        toMs: Number.isFinite(Number(toMs)) ? Number(toMs) : null,
      });
      const res = await fetch(url);
      mark(`zone_boxes_tf_${keyTf}_end`, {
        tf: keyTf,
        url,
        status: res.status,
        fromMs: Number.isFinite(Number(fromMs)) ? Number(fromMs) : null,
        toMs: Number.isFinite(Number(toMs)) ? Number(toMs) : null,
      });
      measure(
        `zone_boxes_tf_${keyTf}_start`,
        `zone_boxes_tf_${keyTf}_end`,
        `zone_boxes_tf_${keyTf}_ms`,
        {
          tf: keyTf,
          fromMs: Number.isFinite(Number(fromMs)) ? Number(fromMs) : null,
          toMs: Number.isFinite(Number(toMs)) ? Number(toMs) : null,
        }
      );
      if (!res.ok) {
        console.warn("[CandleChart] zone list 응답 코드:", res.status);
        return [];
      }

      const zones = await safeJson(res, `zones-${keyTf}`);
      if (!Array.isArray(zones)) {
        console.warn("[CandleChart] zone list 응답이 배열이 아님:", zones);
        return [];
      }

      for (const zone of zones) {
        const startTsRaw = zone?.startTs ?? zone?.start_ts;
        const startTs = startTsRaw != null ? Number(startTsRaw) : NaN;
        if (!Number.isFinite(startTs)) continue;

        const sideUp = String(zone?.side ?? "").toUpperCase() === "SHORT" ? "SHORT" : "LONG";
        const sign = sideUp === "SHORT" ? -1 : 1;

        zone.startTs = startTs;
        zone.side = sideUp;
        zone.id = `${keyTf}-${startTs}-${sign}`;
      }

      if (!zoneStore.boxStateCache[keyTf]) {
        zoneStore.boxStateCache[keyTf] = {};
      }

      for (const zone of zones) {
        const startTs = Number(zone?.startTs);
        if (!Number.isFinite(startTs)) continue;

        const sideUp = (zone?.side || "").toUpperCase() === "SHORT" ? "SHORT" : "LONG";
        const key = makeZoneKey(symbol, keyTf, startTs, sideUp);

        zoneStore.boxStateCache[keyTf][key] = {
          isActive: !!zone?.isActive,
          entryOverride: zone?.entryOverride != null ? Number(zone.entryOverride) : null,
        };
      }

      replaceZonesInRange(keyTf, zones, { fromMs, toMs });

      if (!silent) {
        applyZoneOverlays();
      }
      return zones;
    } catch (error) {
      mark(`zone_boxes_tf_${keyTf}_end`, {
        tf: keyTf,
        status: "error",
        error: error?.name || "unknown",
      });
      measure(
        `zone_boxes_tf_${keyTf}_start`,
        `zone_boxes_tf_${keyTf}_end`,
        `zone_boxes_tf_${keyTf}_ms`,
        {
          tf: keyTf,
          error: true,
          fromMs: Number.isFinite(Number(fromMs)) ? Number(fromMs) : null,
          toMs: Number.isFinite(Number(toMs)) ? Number(toMs) : null,
        }
      );
      console.error("[CandleChart] fetchZoneBoxesForTf 에러:", keyTf, error);
      return [];
    }
  })();

  setZoneInFlightRequest(keyTf, requestKey, requestPromise);
  try {
    return await requestPromise;
  } finally {
    clearZoneInFlightRequest(keyTf, requestKey, requestPromise);
  }
}

export async function fetchZoneStartupSnapshot(
  {
    symbol = DEFAULT_SYMBOL.ticker,
    intervals = ZONE_TF_LIST,
    viewportFromMs,
    viewportToMs,
    silent = false,
  } = {}
) {
  const normalizedIntervals = Array.isArray(intervals)
    ? intervals
        .map((value) => String(value))
        .filter((value, index, arr) => ZONE_TF_LIST.includes(value) && arr.indexOf(value) === index)
    : [];

  const fromMs = Number(viewportFromMs);
  const toMs = Number(viewportToMs);
  if (!normalizedIntervals.length || !Number.isFinite(fromMs) || !Number.isFinite(toMs)) {
    return null;
  }

  const viewportFrom = Math.min(fromMs, toMs);
  const viewportTo = Math.max(fromMs, toMs);
  const intervalParam = normalizedIntervals.join(",");
  const url =
    `/api/zones/startup-snapshot?symbol=${encodeURIComponent(symbol)}` +
    `&intervals=${encodeURIComponent(intervalParam)}` +
    `&viewport_from=${encodeURIComponent(viewportFrom)}` +
    `&viewport_to=${encodeURIComponent(viewportTo)}`;

  mark("zone_boxes_startup_snapshot_start", {
    intervals: normalizedIntervals,
    url,
    request: true,
    viewportFrom,
    viewportTo,
  });

  try {
    const res = await fetch(url);
    mark("zone_boxes_startup_snapshot_end", {
      intervals: normalizedIntervals,
      url,
      status: res.status,
      viewportFrom,
      viewportTo,
    });
    measure(
      "zone_boxes_startup_snapshot_start",
      "zone_boxes_startup_snapshot_end",
      "zone_boxes_startup_snapshot_ms",
      {
        intervals: normalizedIntervals,
        viewportFrom,
        viewportTo,
      }
    );

    if (!res.ok) {
      console.warn("[CandleChart] zone startup snapshot 응답 코드:", res.status);
      return null;
    }

    const payload = await safeJson(res, "zone-startup-snapshot");
    if (!payload || typeof payload !== "object") {
      return null;
    }

    mergeZoneStartupSnapshot(payload.boxesByTf, {
      symbol,
      fromMs: viewportFrom,
      toMs: viewportTo,
    });

    if (!silent) {
      applyZoneOverlays();
    }

    return payload;
  } catch (error) {
    mark("zone_boxes_startup_snapshot_end", {
      intervals: normalizedIntervals,
      url,
      status: "error",
      error: error?.name || "unknown",
      viewportFrom,
      viewportTo,
    });
    measure(
      "zone_boxes_startup_snapshot_start",
      "zone_boxes_startup_snapshot_end",
      "zone_boxes_startup_snapshot_ms",
      {
        intervals: normalizedIntervals,
        error: true,
        viewportFrom,
        viewportTo,
      }
    );
    console.error("[CandleChart] fetchZoneStartupSnapshot 에러:", error);
    return null;
  }
}

// 첫 진입 시 알림과 차트가 같은 기준을 보도록 상태를 선로딩하기 위함
export async function preloadZoneStateForAllTf() {
  try {
    await Promise.all(
      ZONE_TF_LIST.map((tfKey) => fetchZoneStateForTf(tfKey))
    );
  } catch (error) {
    console.error("[CandleChart] preloadZoneStateForAllTf 실패:", error);
  }
}

// 한 번만 전체 Zone 목록을 채워 두고 타임프레임 전환 비용을 줄이기 위함
export async function ensureAllZoneBoxesLoadedOnce() {
  if (zoneStore.allLoaded) return;
  if (!zoneStore.allLoading) {
    zoneStore.allLoading = (async () => {
      mark("zone_boxes_preload_start");
      await Promise.all(
        ZONE_TF_LIST.map((tfKey) =>
          fetchZoneBoxesForTf(tfKey, { force: true, silent: true })
        )
      );
      zoneStore.allLoaded = true;
      mark("zone_boxes_preload_end");
      measure("zone_boxes_preload_start", "zone_boxes_preload_end", "zone_boxes_preload_ms");
    })();
  }

  await zoneStore.allLoading;
}
