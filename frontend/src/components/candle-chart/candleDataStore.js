import { TF_LIST } from "./constants";
import { optionalFiniteNumber } from "./chartUtils";

function createCandleCacheEntry() {
  return {
    initLoaded: false,
    list: [],
    hasMorePast: true,
    earliestTs: null,
    lastFetchedAt: 0,
    initRequest: null,
  };
}

const candleDataStore = {
  cacheBySymbol: {},
  forceResyncBySymbol: {},
};

function ensureSymbolCache(symbol) {
  const keySymbol = String(symbol || "");
  if (!candleDataStore.cacheBySymbol[keySymbol]) {
    candleDataStore.cacheBySymbol[keySymbol] = {};
  }

  for (const tf of TF_LIST) {
    if (!candleDataStore.cacheBySymbol[keySymbol][tf]) {
      candleDataStore.cacheBySymbol[keySymbol][tf] = createCandleCacheEntry();
    }
  }

  return candleDataStore.cacheBySymbol[keySymbol];
}

function ensureSymbolResyncFlags(symbol) {
  const keySymbol = String(symbol || "");
  if (!candleDataStore.forceResyncBySymbol[keySymbol]) {
    candleDataStore.forceResyncBySymbol[keySymbol] = {};
  }

  for (const tf of TF_LIST) {
    if (candleDataStore.forceResyncBySymbol[keySymbol][tf] == null) {
      candleDataStore.forceResyncBySymbol[keySymbol][tf] = false;
    }
  }

  return candleDataStore.forceResyncBySymbol[keySymbol];
}

export function ensureCandleCacheEntry(symbol, tfStr) {
  return ensureSymbolCache(symbol)[String(tfStr)] || createCandleCacheEntry();
}

export function getCandleCacheEntry(symbol, tfStr) {
  return ensureCandleCacheEntry(symbol, tfStr);
}

export function hasWarmCandleCache(symbol, tfStr) {
  const cache = getCandleCacheEntry(symbol, tfStr);
  return cache.initLoaded && Array.isArray(cache.list) && cache.list.length > 0;
}

export function isCandleCacheFresh(symbol, tfStr, staleMs = 15_000) {
  const cache = getCandleCacheEntry(symbol, tfStr);
  if (!cache.initLoaded || !cache.lastFetchedAt) return false;
  return Date.now() - cache.lastFetchedAt <= staleMs;
}

export function replaceCandleCache(symbol, tfStr, candles, { hasMorePast = true } = {}) {
  const cache = ensureCandleCacheEntry(symbol, tfStr);
  const nextList = Array.isArray(candles) ? candles.slice() : [];

  cache.initLoaded = true;
  cache.list = nextList;
  cache.hasMorePast = Boolean(hasMorePast);
  cache.earliestTs = nextList[0]?.timestamp ?? null;
  cache.lastFetchedAt = Date.now();
  return cache;
}

export function prependCandleCache(symbol, tfStr, candles, { hasMorePast = true } = {}) {
  const cache = ensureCandleCacheEntry(symbol, tfStr);
  const incoming = Array.isArray(candles) ? candles : [];
  const mergedByTs = new Map();

  for (const candle of incoming) {
    const timestamp = Number(candle?.timestamp);
    if (!Number.isFinite(timestamp)) continue;
    mergedByTs.set(timestamp, candle);
  }

  for (const candle of cache.list) {
    const timestamp = Number(candle?.timestamp);
    if (!Number.isFinite(timestamp) || mergedByTs.has(timestamp)) continue;
    mergedByTs.set(timestamp, candle);
  }

  cache.initLoaded = true;
  cache.list = Array.from(mergedByTs.values()).sort((a, b) => a.timestamp - b.timestamp);
  cache.hasMorePast = Boolean(hasMorePast);
  cache.earliestTs = cache.list[0]?.timestamp ?? null;
  cache.lastFetchedAt = Date.now();
  return cache;
}

export function patchRestConfirmedCandle(symbol, tfStr, candle) {
  const cache = getCandleCacheEntry(symbol, tfStr);
  if (!Array.isArray(cache.list) || cache.list.length === 0) {
    return { updated: false, newBar: null };
  }

  const targetStart = Number(candle.start);
  if (!Number.isFinite(targetStart)) {
    return { updated: false, newBar: null };
  }

  const arr = cache.list;
  const idx = arr.findIndex((bar) => Number(bar.timestamp) === targetStart);
  const base = idx >= 0 ? arr[idx] : {};

  const newBar = {
    ...base,
    timestamp: targetStart,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  };
  const volume = optionalFiniteNumber(candle.volume);
  if (volume !== undefined) {
    newBar.volume = volume;
  }

  if (idx >= 0) {
    arr[idx] = newBar;
  } else {
    arr.push(newBar);
    arr.sort((a, b) => a.timestamp - b.timestamp);
  }

  cache.earliestTs = arr[0]?.timestamp ?? cache.earliestTs;
  cache.lastFetchedAt = Date.now();
  return { updated: true, newBar };
}

export function markForceCandleResyncOnNextInit(symbol, tfStr) {
  const flags = ensureSymbolResyncFlags(symbol);
  flags[String(tfStr)] = true;
}

export function consumeForceCandleResyncOnNextInit(symbol, tfStr) {
  const flags = ensureSymbolResyncFlags(symbol);
  const tfKey = String(tfStr);
  const shouldForce = flags[tfKey] === true;
  flags[tfKey] = false;
  return shouldForce;
}

export function getCandleInitRequest(symbol, tfStr) {
  return getCandleCacheEntry(symbol, tfStr).initRequest || null;
}

export function setCandleInitRequest(symbol, tfStr, requestPromise) {
  const cache = ensureCandleCacheEntry(symbol, tfStr);
  cache.initRequest = requestPromise || null;
  return cache.initRequest;
}

export function clearCandleInitRequest(symbol, tfStr, requestPromise = null) {
  const cache = ensureCandleCacheEntry(symbol, tfStr);
  if (requestPromise == null || cache.initRequest === requestPromise) {
    cache.initRequest = null;
  }
}
