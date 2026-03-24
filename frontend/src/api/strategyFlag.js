//// frontend/src/api/strategyFlag.js

// 현재 전역 StrategyFlag 상태 조회
// (enable_trading / enable_zone_strategy)
export async function fetchStrategyFlags() {
  try {
    const res = await fetch("/api/strategy_flags");
    if (!res.ok) {
      console.error("[StrategyFlagAPI] /api/strategy_flags 응답 코드:", res.status);
      return null;
    }
    return await res.json();
  } catch (e) {
    console.error("[StrategyFlagAPI] /api/strategy_flags 호출 에러:", e);
    return null;
  }
}

// enable_trading 토글 저장
export async function updateEnableTrading(nextValue) {
  try {
    const res = await fetch("/api/strategy_flags/enable_trading", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: nextValue }),
    });
    if (!res.ok) {
      console.error(
        "[StrategyFlagAPI] /api/strategy_flags/enable_trading 응답 코드:",
        res.status,
      );
    }
  } catch (e) {
    console.error("[StrategyFlagAPI] /api/strategy_flags/enable_trading 호출 에러:", e);
  }
}

// Structure Zone 토글 저장
export async function updateEnableZoneStrategy(nextValue) {
  try {
    const res = await fetch("/api/strategy_flags/enable_zone_strategy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: nextValue }),
    });
    if (!res.ok) {
      console.error(
        "[StrategyFlagAPI] /api/strategy_flags/enable_zone_strategy 응답 코드:",
        res.status,
      );
    }
  } catch (e) {
    console.error(
      "[StrategyFlagAPI] /api/strategy_flags/enable_zone_strategy 호출 에러:",
      e,
    );
  }
}
