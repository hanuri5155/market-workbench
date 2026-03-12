import { registerIndicator } from "klinecharts";

import {
  MTF_MA_INDICATOR_NAME,
  MTF_MA_INTERVAL_MS,
  MTF_MA_SOURCE_INIT_LIMIT,
  MTF_MA_SOURCE_MAX_CANDLES,
  MTF_MA_SOURCE_TF_LIST,
  MTF_MA_SPECS,
} from "../constants";
import { isFiniteNumber, safeJson } from "../chartUtils";

// 소스 캔들 캐시
function createMtfMaSourceCacheEntry() {
  return {
    list: [],
    byStart: new Map(),
    revision: 0,
    initLoaded: false,
  };
}

const mtfMaStore = {
  sourceCache: {
    "240": createMtfMaSourceCacheEntry(),
    "1440": createMtfMaSourceCacheEntry(),
  },
  smaCache: new Map(),
  indicatorRegistered: false,
  indicatorRevision: 0,
  indicatorId: null,
};

// 캔들 정규화 / 캐시 관리
function normalizeMtfMaSourceCandle(raw) {
  if (!raw || typeof raw !== "object") return null;

  const start = Number(raw.start);
  const end = Number(raw.end);
  const close = Number(raw.close);
  if (!isFiniteNumber(start) || !isFiniteNumber(close)) return null;

  return {
    start,
    end: isFiniteNumber(end) ? end : start,
    close,
  };
}

function trimMtfMaSourceCache(tfStr) {
  const cache = mtfMaStore.sourceCache[tfStr];
  if (!cache) return;
  if (cache.list.length <= MTF_MA_SOURCE_MAX_CANDLES) return;

  cache.list = cache.list.slice(-MTF_MA_SOURCE_MAX_CANDLES);
  cache.byStart = new Map(cache.list.map((candle) => [candle.start, candle]));
}

function invalidateMtfMaSmaCache(tfStr) {
  const prefix = `${tfStr}|`;
  for (const key of mtfMaStore.smaCache.keys()) {
    if (key.startsWith(prefix)) {
      mtfMaStore.smaCache.delete(key);
    }
  }
}

export function upsertMtfMaSourceCandles(tfStr, rawCandles) {
  const cache = mtfMaStore.sourceCache[tfStr];
  if (!cache || !Array.isArray(rawCandles) || rawCandles.length === 0) return false;

  let changed = false;
  for (const raw of rawCandles) {
    const candle = normalizeMtfMaSourceCandle(raw);
    if (!candle) continue;

    const prev = cache.byStart.get(candle.start);
    if (!prev || prev.close !== candle.close || prev.end !== candle.end) {
      cache.byStart.set(candle.start, candle);
      changed = true;
    }
  }

  if (!changed) return false;

  cache.list = Array.from(cache.byStart.values()).sort((a, b) => a.start - b.start);
  trimMtfMaSourceCache(tfStr);
  cache.revision += 1;
  invalidateMtfMaSmaCache(tfStr);
  return true;
}

// 소스 조회
export async function fetchMtfMaSourceCandles(
  tfStr,
  limit = MTF_MA_SOURCE_INIT_LIMIT
) {
  if (!MTF_MA_SOURCE_TF_LIST.includes(String(tfStr))) return false;

  try {
    const response = await fetch(`/api/candles/${tfStr}?limit=${limit}`);
    if (!response.ok) {
      console.warn(
        `[CandleChart][MTF_MA] source init fetch failed tf=${tfStr}:`,
        response.status
      );
      return false;
    }

    const candles = await safeJson(response, `mtf-ma-source-init-${tfStr}`);
    if (!Array.isArray(candles)) return false;

    const changed = upsertMtfMaSourceCandles(String(tfStr), candles);
    const cache = mtfMaStore.sourceCache[String(tfStr)];
    if (cache) cache.initLoaded = true;
    return changed;
  } catch (error) {
    console.warn(`[CandleChart][MTF_MA] source init fetch error tf=${tfStr}:`, error);
    return false;
  }
}

// 최신 캔들 조회
export async function fetchMtfMaSourceLatest(tfStr) {
  if (!MTF_MA_SOURCE_TF_LIST.includes(String(tfStr))) return false;

  try {
    const response = await fetch(`/api/candles/latest/${tfStr}`);
    if (!response.ok) {
      return false;
    }

    const candle = await safeJson(response, `mtf-ma-source-latest-${tfStr}`);
    if (!candle || candle.start == null) return false;

    return upsertMtfMaSourceCandles(String(tfStr), [candle]);
  } catch {
    return false;
  }
}

// SMA 계산
function floorTimestampToSourceBucketStart(timestamp, sourceTf) {
  const intervalMs = MTF_MA_INTERVAL_MS[String(sourceTf)];
  if (!isFiniteNumber(timestamp) || !isFiniteNumber(intervalMs) || intervalMs <= 0) {
    return null;
  }

  return Math.floor(timestamp / intervalMs) * intervalMs;
}

function getMtfMaSmaMap(sourceTf, period) {
  const tfKey = String(sourceTf);
  const normalizedPeriod = Number(period);
  const cache = mtfMaStore.sourceCache[tfKey];
  if (
    !cache ||
    !Array.isArray(cache.list) ||
    cache.list.length === 0 ||
    !Number.isFinite(normalizedPeriod) ||
    normalizedPeriod <= 0
  ) {
    return new Map();
  }

  const cacheKey = `${tfKey}|${normalizedPeriod}`;
  const cached = mtfMaStore.smaCache.get(cacheKey);
  if (cached && cached.sourceRevision === cache.revision) {
    return cached.map;
  }

  const map = new Map();
  let sum = 0;
  for (let index = 0; index < cache.list.length; index += 1) {
    const close = cache.list[index].close;
    sum += close;
    if (index >= normalizedPeriod - 1) {
      const value = sum / normalizedPeriod;
      const start = cache.list[index].start;
      map.set(start, value);
      sum -= cache.list[index - (normalizedPeriod - 1)].close;
    }
  }

  mtfMaStore.smaCache.set(cacheKey, {
    sourceRevision: cache.revision,
    map,
  });
  return map;
}

// 인디케이터 등록
export function ensureMtfMaIndicatorRegistered() {
  if (mtfMaStore.indicatorRegistered) return;

  registerIndicator({
    name: MTF_MA_INDICATOR_NAME,
    shortName: "MTF MA",
    series: "price",
    precision: 2,
    shouldOhlc: true,
    figures: MTF_MA_SPECS.map((spec) => ({
      key: spec.key,
      title: spec.title,
      type: "line",
    })),
    styles: {
      lines: MTF_MA_SPECS.map((spec) => ({
        style: "solid",
        smooth: false,
        size: spec.lineWidth,
        color: spec.color,
      })),
    },
    shouldUpdate: (prev, current) => {
      const prevRev = Number(prev?.extendData?.revision ?? 0);
      const currRev = Number(current?.extendData?.revision ?? 0);
      if (prevRev !== currRev) {
        return { calc: true, draw: true };
      }
      return { calc: false, draw: false };
    },
    calc: (dataList) => {
      const smaMaps = {};
      for (const spec of MTF_MA_SPECS) {
        smaMaps[spec.key] = getMtfMaSmaMap(spec.sourceTf, spec.period);
      }

      return dataList.map((bar) => {
        const row = {};
        const timestamp = Number(bar?.timestamp);
        if (!isFiniteNumber(timestamp)) return row;

        for (const spec of MTF_MA_SPECS) {
          const bucketStart = floorTimestampToSourceBucketStart(
            timestamp,
            spec.sourceTf
          );
          if (!isFiniteNumber(bucketStart)) continue;
          const value = smaMaps[spec.key]?.get(bucketStart);
          if (isFiniteNumber(value)) {
            row[spec.key] = value;
          }
        }

        return row;
      });
    },
  });

  mtfMaStore.indicatorRegistered = true;
}

// 인디케이터 생성
export function createMtfMaIndicator(chart, paneId) {
  if (!chart || typeof chart.createIndicator !== "function") return null;

  try {
    const indicatorId = chart.createIndicator(
      {
        name: MTF_MA_INDICATOR_NAME,
        visible: true,
        extendData: {
          revision: mtfMaStore.indicatorRevision,
          reason: "init",
        },
      },
      true,
      { id: paneId }
    );

    mtfMaStore.indicatorId =
      typeof indicatorId === "string" ? indicatorId : null;
    return mtfMaStore.indicatorId;
  } catch (error) {
    mtfMaStore.indicatorId = null;
    console.warn("[CandleChart][MTF_MA] indicator 생성 실패:", error);
    return null;
  }
}

// 인디케이터 갱신
export function refreshMtfMaIndicator(chart, reason = "unknown") {
  if (
    !chart ||
    !mtfMaStore.indicatorId ||
    typeof chart.overrideIndicator !== "function"
  ) {
    return;
  }

  try {
    mtfMaStore.indicatorRevision += 1;
    chart.overrideIndicator({
      id: mtfMaStore.indicatorId,
      extendData: {
        revision: mtfMaStore.indicatorRevision,
        reason,
      },
    });
  } catch (error) {
    console.warn("[CandleChart][MTF_MA] overrideIndicator 실패:", error);
  }
}

// 런타임 초기화
export function resetMtfMaIndicatorRuntimeState() {
  mtfMaStore.indicatorId = null;
}
