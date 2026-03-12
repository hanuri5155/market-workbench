import { DEFAULT_SYMBOL, ZONE_TF_LIST } from "../constants";
import { safeJson } from "../chartUtils";
import {
  applyZoneStatePayload,
  applyStateToZones,
  zoneStore,
  makeZoneKey,
} from "./store";
import { applyZoneOverlays } from "./overlay";

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
  { force = false, silent = false } = {}
) {
  if (!ZONE_TF_LIST.includes(String(tfStr))) return [];

  const keyTf = String(tfStr);
  if (!force && zoneStore.zonesLoaded[keyTf]) {
    return zoneStore.zonesByTf[keyTf] || [];
  }

  try {
    const symbol = DEFAULT_SYMBOL.ticker;
    const url =
      `/api/zones?symbol=${encodeURIComponent(symbol)}` +
      `&intervalMin=${encodeURIComponent(keyTf)}`;

    const res = await fetch(url);
    if (!res.ok) {
      console.warn("[CandleChart] zone list 응답 코드:", res.status);
      zoneStore.zonesByTf[keyTf] = [];
      zoneStore.zonesLoaded[keyTf] = true;
      return [];
    }

    const zones = await safeJson(res, `zones-${keyTf}`);
    if (!Array.isArray(zones)) {
      console.warn("[CandleChart] zone list 응답이 배열이 아님:", zones);
      zoneStore.zonesByTf[keyTf] = [];
      zoneStore.zonesLoaded[keyTf] = true;
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

    zoneStore.zonesByTf[keyTf] = zones;
    zoneStore.zonesLoaded[keyTf] = true;

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

    applyStateToZones(keyTf);

    if (!silent) {
      applyZoneOverlays();
    }

    return zones;
  } catch (error) {
    console.error("[CandleChart] fetchZoneBoxesForTf 에러:", keyTf, error);
    return [];
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
      await Promise.all(
        ZONE_TF_LIST.map((tfKey) =>
          fetchZoneBoxesForTf(tfKey, { force: true, silent: true })
        )
      );
      zoneStore.allLoaded = true;
    })();
  }

  await zoneStore.allLoading;
}
